"""事故数据集管理器。"""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from construction_agent.config import settings
from construction_agent.models.accident import Accident, AccidentType, generate_accident_id

logger = logging.getLogger(__name__)


class DatasetManager:
    """管理事故数据集的增删改查。"""

    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or settings.DATASET_FILE
        self._data: list[dict] = []
        self._id_index: dict[str, int] = {}  # id -> index in _data
        self._load()

    def _load(self):
        """从文件加载数据集。"""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    if content.strip():
                        self._data = json.loads(content)
                    else:
                        self._data = []
                logger.info(f"加载数据集: {self.filepath} ({len(self._data)} 条)")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"数据集文件损坏或无法解析，将重新创建: {e}")
                self._data = []
        else:
            self._data = []
            self.filepath.parent.mkdir(parents=True, exist_ok=True)

        # 重建 ID 索引，并为缺少 ID 的老记录回填
        self._rebuild_id_index()

    def _rebuild_id_index(self):
        """重建 ID 索引，为缺少 ID 的记录自动生成。"""
        self._id_index = {}
        changed = False
        for i, record in enumerate(self._data):
            rid = (record.get("id") or "").strip()
            if not rid:
                # 回填 ID
                title = record.get("title", "")
                date_str = record.get("date", "")
                city = record.get("city", "")
                rid = generate_accident_id(title, date_str, city)
                record["id"] = rid
                changed = True
            self._id_index[rid] = i
        if changed:
            self._save()
            logger.info(f"已为 {len(self._data)} 条记录建立/回填 ID")

    def _save(self):
        """保存数据集到文件。"""
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False, default=str)

    def add(self, accident: Accident) -> dict:
        """添加一条事故记录，自动去重（按 ID 判断）。

        Args:
            accident: 事故数据

        Returns:
            {"action": "added"|"skipped"|"updated", "id": str, "total": int}
        """
        accident.ensure_id()
        record = accident.to_dict()
        rid = record["id"]

        if rid in self._id_index:
            idx = self._id_index[rid]
            existing = self._data[idx]

            # 检查是否有新信息需要合并（如来源URL）
            merged = False
            existing_urls = set(existing.get("source_urls") or [])
            new_urls = set(record.get("source_urls") or [])
            if new_urls - existing_urls:
                existing["source_urls"] = list(existing_urls | new_urls)
                merged = True

            # 如果原记录缺少某些字段，用新值补充
            for field in ["direct_cause", "contractor", "summary",
                          "accident_location_detail", "project_name",
                          "human_factors", "machine_factors",
                          "management_factors", "environmental_factors"]:
                old_val = existing.get(field)
                new_val = record.get(field)
                if (not old_val or old_val == "" or old_val == [] or old_val == []) and new_val:
                    existing[field] = new_val
                    merged = True

            if merged:
                self._data[idx] = existing
                self._save()
                logger.info(f"🔄 更新已有事故(补充信息): {accident.title}")
                return {"action": "updated", "id": rid, "total": len(self._data)}
            else:
                logger.info(f"⏭️ 跳过重复事故: {accident.title}")
                return {"action": "skipped", "id": rid, "total": len(self._data)}

        # 标题模糊匹配查重（应对同一事故不同标题）
        new_title = record.get("title", "")
        new_norm = re.sub(r'\s+', '', (new_title or "")).lower()
        for existing_idx, existing_rec in enumerate(self._data):
            existing_title = existing_rec.get("title", "")
            if not existing_title:
                continue
            exist_norm = re.sub(r'\s+', '', (existing_title or "")).lower()
            # 标题相似度 ≥ 0.65 且日期相近 → 视为同一事故
            if new_norm and exist_norm:
                sim = SequenceMatcher(None, new_norm, exist_norm).ratio()
                if sim >= 0.65:
                    # 检查日期是否相近
                    new_date = str(record.get("date", ""))
                    exist_date = str(existing_rec.get("date", ""))
                    date_ok = (not new_date) or (not exist_date) or new_date[:7] == exist_date[:7]
                    if date_ok:
                        # 合并到现有记录
                        merged = False
                        eu = set(existing_rec.get("source_urls") or [])
                        nu = set(record.get("source_urls") or [])
                        if nu - eu:
                            existing_rec["source_urls"] = list(eu | nu)
                            merged = True
                        ei = set(existing_rec.get("images") or [])
                        ni = set(record.get("images") or [])
                        if ni - ei:
                            existing_rec["images"] = list(ei | ni)
                            merged = True
                        for field in ["severity","deaths","injuries","direct_cause","contractor",
                                       "summary","weather","city","accident_type"]:
                            ov = existing_rec.get(field)
                            nv = record.get(field)
                            if (not ov or ov in ("",[],0,"0","无法判断")) and nv:
                                existing_rec[field] = nv
                                merged = True
                        if merged:
                            self._data[existing_idx] = existing_rec
                            self._save()
                            logger.info(f"🔄 模糊匹配合并: {new_title[:40]} → {existing_title[:40]}")
                            return {"action": "updated", "id": existing_rec.get("id",""), "total": len(self._data)}
                        else:
                            logger.info(f"⏭️ 模糊匹配跳过: {new_title[:40]} ≈ {existing_title[:40]}")
                            return {"action": "skipped", "id": existing_rec.get("id",""), "total": len(self._data)}
                    break

        self._data.append(record)
        self._id_index[rid] = len(self._data) - 1
        self._save()
        logger.info(f"✅ 添加事故: {accident.title} (ID:{rid})")
        return {"action": "added", "id": rid, "total": len(self._data)}

    def add_batch(self, accidents: list[Accident]) -> dict:
        """批量添加事故记录（逐条去重）。"""
        added = 0
        skipped = 0
        updated = 0
        for a in accidents:
            result = self.add(a)
            if result["action"] == "added":
                added += 1
            elif result["action"] == "updated":
                updated += 1
            else:
                skipped += 1
        logger.info(f"批量处理完成: 新增{added} 更新{updated} 跳过{skipped}")
        return {"added": added, "updated": updated, "skipped": skipped, "total": len(self._data)}

    def get_all(self) -> list[dict]:
        """获取所有事故记录。"""
        return list(self._data)

    def search(self, keyword: str) -> list[dict]:
        """搜索事故记录。

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的事故记录列表
        """
        keyword = keyword.lower()
        results = []
        for record in self._data:
            searchable = json.dumps(record, ensure_ascii=False).lower()
            if keyword in searchable:
                results.append(record)
        return results

    def filter_by_type(self, accident_type: str) -> list[dict]:
        """按事故类型筛选。"""
        return [r for r in self._data if r.get("accident_type") == accident_type]

    def filter_by_severity(self, severity: str) -> list[dict]:
        """按事故等级筛选。"""
        return [r for r in self._data if r.get("severity") == severity]

    def stats(self) -> dict:
        """数据集统计信息。"""
        if not self._data:
            return {"total": 0, "by_type": {}, "by_severity": {}}

        type_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        total_deaths = 0
        total_injuries = 0

        for r in self._data:
            t = r.get("accident_type", "未知")
            type_counts[t] = type_counts.get(t, 0) + 1

            s = r.get("severity", "未知")
            severity_counts[s] = severity_counts.get(s, 0) + 1

            total_deaths += r.get("deaths", 0) or 0
            total_injuries += r.get("injuries", 0) or 0

        return {
            "total": len(self._data),
            "total_deaths": total_deaths,
            "total_injuries": total_injuries,
            "by_type": dict(sorted(type_counts.items(), key=lambda x: -x[1])),
            "by_severity": dict(sorted(severity_counts.items(), key=lambda x: -x[1])),
        }

    def export(self, output_path: Path, fmt: str = "json") -> bool:
        """导出数据集到文件。

        Args:
            output_path: 输出路径
            fmt: 格式 (json/csv)

        Returns:
            是否成功
        """
        from construction_agent.output.json_formatter import JSONFormatter
        from construction_agent.output.csv_formatter import CSVFormatter

        accidents = [Accident(**r) for r in self._data]

        if fmt == "csv":
            return CSVFormatter.save_batch(accidents, output_path)
        else:
            return JSONFormatter.save_batch(accidents, output_path)

    def clear(self) -> bool:
        """清空数据集。"""
        self._data = []
        self._save()
        logger.info("数据集已清空")
        return True
