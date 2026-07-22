"""SQLite 数据仓库实现 —— 基于 SQLAlchemy 2.0。

实现 AccidentRepository 全部接口，自动管理 Session 生命周期。
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker

from construction_agent.database.models import (
    Base,
    Accident as OrmAccident,
    Factor,
    HfacsDetail,
    SourceUrl,
    SearchSnippet,
    SearchQuery,
    Image,
    ProcessingHistory,
    LlmApiLog,
    SearchLog,
    PdfProcessingLog,
)
from construction_agent.database.repository import (
    AccidentRepository,
    AddResult,
    DatasetStats,
    ProcessingRecord,
    LlmApiRecord,
    SearchLogRecord,
    PdfProcessingRecord,
)
from construction_agent.models.accident import (
    Accident as PydanticAccident,
    HumanFactor,
    MachineFactor,
    ManagementFactor,
    EnvironmentalFactor,
    HfacsCode,
    generate_accident_id,
)

logger = logging.getLogger(__name__)


def _safe_int(val) -> Optional[int]:
    """安全转换为 int，非数字返回 None。"""
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


class SqliteAccidentRepository(AccidentRepository):
    """SQLite + SQLAlchemy 实现。

    Usage:
        repo = SqliteAccidentRepository("sqlite:///data/caiems.db")
        repo.initialize()  # 首次运行创建表
        repo.add(accident)
        stats = repo.stats()
    """

    def __init__(self, database_url: str, echo: bool = False):
        self._engine = create_engine(database_url, echo=echo)
        self._session_factory = sessionmaker(self._engine)

    def initialize(self):
        """创建所有表（幂等）。"""
        Base.metadata.create_all(self._engine)
        logger.info("数据库表已就绪")

    def _session(self) -> Session:
        return self._session_factory()

    # ═══════════════════════════════════════════════════════════════
    # 核心 CRUD
    # ═══════════════════════════════════════════════════════════════

    def add(self, accident: PydanticAccident) -> AddResult:
        """添加或更新一条事故记录（按 ID 去重）。"""
        accident.ensure_id()
        aid = accident.id

        with self._session() as s:
            existing = s.get(OrmAccident, aid)
            if existing:
                # 更新现有记录
                return self._update_existing(s, existing, accident)

            # 新记录
            orm_acc = self._pydantic_to_orm(accident)
            s.add(orm_acc)
            s.commit()
            logger.info(f"已添加事故: {accident.title[:40]} (id={aid})")
            return AddResult(action="added", accident_id=aid)

    def add_batch(self, accidents: list[PydanticAccident]) -> list[AddResult]:
        results = []
        for acc in accidents:
            results.append(self.add(acc))
        return results

    def get_by_id(self, accident_id: str) -> Optional[dict]:
        with self._session() as s:
            orm_acc = s.get(OrmAccident, accident_id)
            if orm_acc is None:
                return None
            return self._orm_to_dict(orm_acc)

    def get_all(self) -> list[dict]:
        with self._session() as s:
            orm_accs = s.query(OrmAccident).order_by(OrmAccident.created_at.desc()).all()
            return [self._orm_to_dict(a) for a in orm_accs]

    def search(self, keyword: str) -> list[dict]:
        with self._session() as s:
            orm_accs = (
                s.query(OrmAccident)
                .filter(
                    (OrmAccident.title.contains(keyword))
                    | (OrmAccident.summary.contains(keyword))
                    | (OrmAccident.direct_cause.contains(keyword))
                )
                .all()
            )
            return [self._orm_to_dict(a) for a in orm_accs]

    def stats(self) -> DatasetStats:
        with self._session() as s:
            total = s.query(OrmAccident).count()
            total_deaths = s.query(func.coalesce(func.sum(OrmAccident.deaths), 0)).scalar() or 0
            total_injuries = s.query(func.coalesce(func.sum(OrmAccident.injuries), 0)).scalar() or 0

            # 按类型统计
            rows = s.query(OrmAccident.accident_type, func.count()).group_by(
                OrmAccident.accident_type
            ).all()
            by_type = {r[0]: r[1] for r in rows if r[0]}

            # 按等级统计
            rows = s.query(OrmAccident.severity, func.count()).group_by(
                OrmAccident.severity
            ).all()
            by_severity = {r[0]: r[1] for r in rows if r[0]}

            return DatasetStats(
                total=total or 0,
                total_deaths=total_deaths or 0,
                total_injuries=total_injuries or 0,
                by_type=by_type,
                by_severity=by_severity,
            )

    def export(self, filepath: str, format: str = "json") -> bool:
        path = Path(filepath)
        records = self.get_all()
        if not records:
            return False
        if format == "csv":
            import csv as csv_module
            from construction_agent.models.accident import Accident as PA
            flat_records = [PA(**r).to_flat_dict() for r in records]
            if not flat_records:
                return False
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv_module.DictWriter(f, fieldnames=flat_records[0].keys())
                writer.writeheader()
                writer.writerows(flat_records)
        else:
            path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def clear(self):
        with self._session() as s:
            s.query(OrmAccident).delete()
            s.commit()
        logger.warning("数据集已清空")

    # ═══════════════════════════════════════════════════════════════
    # 中间数据写入
    # ═══════════════════════════════════════════════════════════════

    def log_processing(self, record: ProcessingRecord) -> int:
        with self._session() as s:
            ph = ProcessingHistory(
                accident_id=record.accident_id,
                pipeline_version=record.pipeline_version,
                status=record.status,
                retry_count=record.retry_count,
                input_summary=record.input_summary,
                error_message=record.error_message,
                error_category=record.error_category,
                duration_ms=record.duration_ms,
                token_usage_prompt=record.token_usage_prompt,
                token_usage_completion=record.token_usage_completion,
                created_at=record.created_at,
            )
            s.add(ph)
            s.commit()
            return ph.id

    def log_llm_api(self, record: LlmApiRecord) -> int:
        with self._session() as s:
            log = LlmApiLog(
                processing_id=record.processing_id,
                model=record.model,
                system_prompt=record.system_prompt,
                user_prompt=record.user_prompt,
                raw_response=record.raw_response,
                thinking_block=record.thinking_block,
                json_extracted=record.json_extracted,
                parse_success=1 if record.parse_success else 0,
                parse_error=record.parse_error,
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                duration_ms=record.duration_ms,
                created_at=record.created_at,
            )
            s.add(log)
            s.commit()
            return log.id

    def log_search(self, record: SearchLogRecord) -> int:
        with self._session() as s:
            log = SearchLog(
                processing_id=record.processing_id,
                engine=record.engine,
                query=record.query,
                raw_html=record.raw_html,
                raw_api_response=record.raw_api_response,
                result_count=record.result_count,
                result_positions=record.result_positions,
                success=1 if record.success else 0,
                error_message=record.error_message,
                duration_ms=record.duration_ms,
                created_at=record.created_at,
            )
            s.add(log)
            s.commit()
            return log.id

    def log_pdf_processing(self, record: PdfProcessingRecord) -> int:
        with self._session() as s:
            log = PdfProcessingLog(
                processing_id=record.processing_id if record.processing_id else None,
                pdf_filename=record.pdf_filename,
                mode=record.mode,
                mineru_raw_output=record.mineru_raw_output,
                mineru_zip_path=record.mineru_zip_path,
                cleaned_text=record.cleaned_text,
                segment_count=record.segment_count,
                segments_discarded=record.segments_discarded,
                discarded_text_sample=record.discarded_text_sample,
                split_strategy_used=record.split_strategy_used,
                success=1 if record.success else 0,
                error_message=record.error_message,
                duration_ms=record.duration_ms,
                created_at=record.created_at,
            )
            s.add(log)
            s.commit()
            return log.id

    # ═══════════════════════════════════════════════════════════════
    # 内部辅助
    # ═══════════════════════════════════════════════════════════════

    def _update_existing(self, s: Session, existing: OrmAccident, accident: PydanticAccident) -> AddResult:
        """更新已存在的记录。"""
        accident.ensure_id()
        # 仅更新基础字段
        for field_name in [
            "title", "date", "accident_time", "location", "city",
            "accident_location_detail", "project_name", "project_type",
            "building_structure", "building_completion_year", "construction_phase",
            "weather", "accident_type", "severity", "deaths", "injuries",
            "trapped_count", "direct_economic_loss",
            "day_of_week", "season", "time_period", "lighting",
            "direct_cause", "contractor", "summary", "source", "raw_input",
            "hfacs_codes", "hfacs_encoding_version",
        ]:
            val = getattr(accident, field_name, None)
            if val is not None or (
                isinstance(val, (int, float)) and field_name in ("deaths", "injuries", "trapped_count")
            ):
                if field_name == "hfacs_codes" and isinstance(val, list):
                    val = json.dumps(val, ensure_ascii=False)
                if field_name == "date" and hasattr(val, "isoformat"):
                    val = val.isoformat()
                setattr(existing, field_name, val)

        s.commit()
        return AddResult(action="updated", accident_id=accident.id, previous_id=accident.id)

    @staticmethod
    def _pydantic_to_orm(acc: PydanticAccident) -> OrmAccident:
        """Pydantic Accident → SQLAlchemy ORM 对象（含所有关联）。"""
        acc.ensure_id()

        now = acc.created_at or time.strftime("%Y-%m-%dT%H:%M:%S")

        orm = OrmAccident(
            id=acc.id,
            title=acc.title,
            date=acc.date.isoformat() if acc.date else None,
            accident_time=acc.accident_time or None,
            location=acc.location,
            city=acc.city,
            accident_location_detail=acc.accident_location_detail,
            project_name=acc.project_name,
            project_type=acc.project_type,
            building_structure=acc.building_structure,
            building_completion_year=(
                _safe_int(acc.building_completion_year)
            ),
            construction_phase=acc.construction_phase,
            weather=acc.weather,
            accident_type=acc.accident_type,
            severity=acc.severity,
            deaths=acc.deaths,
            injuries=acc.injuries,
            trapped_count=acc.trapped_count,
            direct_economic_loss=acc.direct_economic_loss,
            day_of_week=acc.day_of_week,
            season=acc.season,
            time_period=acc.time_period,
            lighting=acc.lighting,
            direct_cause=acc.direct_cause,
            contractor=acc.contractor,
            summary=acc.summary,
            source=acc.source,
            raw_input=acc.raw_input,
            hfacs_codes=json.dumps(acc.hfacs_codes, ensure_ascii=False) if acc.hfacs_codes else "[]",
            hfacs_encoding_version=acc.hfacs_encoding_version,
            created_at=now,
        )

        # ── 辅助：兼容 dict 和对象 ──
        def _g(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # ── 致因 factors ──
        for i, f in enumerate(acc.human_factors or []):
            orm.factors.append(Factor(
                dimension="human", category=_g(f, "category", ""), description=_g(f, "description", ""),
                sort_order=i, created_at=now,
            ))
        for i, f in enumerate(acc.machine_factors or []):
            orm.factors.append(Factor(
                dimension="machine", category=_g(f, "category", ""), description=_g(f, "description", ""),
                sort_order=i + 1000, created_at=now,
            ))
        for i, f in enumerate(acc.management_factors or []):
            orm.factors.append(Factor(
                dimension="management", category=_g(f, "category", ""), description=_g(f, "description", ""),
                sort_order=i + 2000, created_at=now,
            ))
        for i, f in enumerate(acc.environmental_factors or []):
            orm.factors.append(Factor(
                dimension="environmental", category=_g(f, "category", ""), description=_g(f, "description", ""),
                sort_order=i + 3000, created_at=now,
            ))

        # ── HFACS details ──
        for d in acc.hfacs_details or []:
            orm.hfacs_details.append(HfacsDetail(
                code=_g(d, "code", ""), level=_g(d, "level", 0),
                level_name_cn=_g(d, "level_name_cn", ""),
                category_name_cn=_g(d, "category_name_cn", ""),
                source_dimension=_g(d, "source_dimension", ""),
                source_category=_g(d, "source_category", ""),
                source_description=_g(d, "source_description", ""),
                encoding_method=_g(d, "encoding_method", "llm"),
            ))

        # ── URLs / snippets / queries ──
        for url in acc.source_urls:
            if url:
                orm.source_urls.append(SourceUrl(url=url, created_at=now))
        for sn in acc.search_snippets:
            if isinstance(sn, dict):
                orm.search_snippets.append(SearchSnippet(
                    url=sn.get("url", ""), title=sn.get("title", ""),
                    snippet=sn.get("snippet", ""), query=sn.get("query", ""),
                    created_at=now,
                ))
        for q in acc.search_queries:
            if q:
                orm.search_queries.append(SearchQuery(query=q, created_at=now))

        # ── Images ──
        for i, img in enumerate(acc.images):
            img_info = {}
            if i < len(acc.image_urls):
                img_info = acc.image_urls[i] if isinstance(acc.image_urls[i], dict) else {}
            local_path = img if isinstance(img, str) else ""
            orm.images.append(Image(
                local_path=local_path,
                source_url=img_info.get("source_url", ""),
                thumb_url=img_info.get("thumb_url", ""),
                sort_order=i,
                created_at=now,
            ))

        return orm

    @staticmethod
    def _orm_to_dict(orm: OrmAccident) -> dict:
        """SQLAlchemy ORM → dict（与当前 JSON 格式兼容）。"""
        def _fmt_date(d):
            return d if d else ""

        # 按维度分组 factors
        human_factors = []
        machine_factors = []
        management_factors = []
        environmental_factors = []
        for f in orm.factors:
            item = {"category": f.category, "description": f.description}
            if f.dimension == "human":
                human_factors.append(item)
            elif f.dimension == "machine":
                machine_factors.append(item)
            elif f.dimension == "management":
                management_factors.append(item)
            elif f.dimension == "environmental":
                environmental_factors.append(item)

        return {
            "id": orm.id,
            "title": orm.title,
            "date": _fmt_date(orm.date),
            "accident_time": orm.accident_time or "",
            "location": orm.location or "",
            "city": orm.city or "",
            "accident_location_detail": orm.accident_location_detail or "",
            "project_name": orm.project_name or "",
            "project_type": orm.project_type or "",
            "building_structure": orm.building_structure or "",
            "building_completion_year": str(orm.building_completion_year) if orm.building_completion_year else "",
            "construction_phase": orm.construction_phase or "",
            "weather": orm.weather or "",
            "accident_type": orm.accident_type or "",
            "severity": orm.severity or "",
            "deaths": orm.deaths,
            "injuries": orm.injuries,
            "trapped_count": orm.trapped_count or 0,
            "direct_economic_loss": orm.direct_economic_loss,
            "day_of_week": orm.day_of_week,
            "season": orm.season,
            "time_period": orm.time_period,
            "lighting": orm.lighting,
            "direct_cause": orm.direct_cause or "",
            "contractor": orm.contractor or "",
            "summary": orm.summary or "",
            "source": orm.source or "",
            "raw_input": orm.raw_input or "",
            "source_urls": [u.url for u in orm.source_urls],
            "search_snippets": [
                {"url": s.url, "title": s.title, "snippet": s.snippet, "query": s.query}
                for s in orm.search_snippets
            ],
            "search_queries": [q.query for q in orm.search_queries],
            "images": [img.local_path for img in orm.images],
            "image_urls": [
                {"source_url": img.source_url, "thumb_url": img.thumb_url, "local_path": img.local_path}
                for img in orm.images
            ],
            "human_factors": human_factors,
            "machine_factors": machine_factors,
            "management_factors": management_factors,
            "environmental_factors": environmental_factors,
            "hfacs_codes": json.loads(orm.hfacs_codes) if orm.hfacs_codes else [],
            "hfacs_details": [
                {
                    "code": d.code, "level": d.level, "level_name_cn": d.level_name_cn,
                    "category_name_cn": d.category_name_cn,
                    "source_dimension": d.source_dimension,
                    "source_category": d.source_category,
                    "source_description": d.source_description,
                    "encoding_method": d.encoding_method,
                }
                for d in orm.hfacs_details
            ],
            "hfacs_encoding_version": orm.hfacs_encoding_version or "",
            "created_at": orm.created_at or "",
        }
