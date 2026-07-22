"""四层递进式查重器。

查重策略（按成本和准确率从低到高排列）：

L1 — URL 精确去重：已爬过的 URL 直接跳过。零成本，100% 准确。
L2 — 结构化指纹去重：(日期+城市+伤亡+事故类型) 生成指纹。极低成本，~95% 准确。
L3 — 标题语义相似度去重：用 text2vec-base-chinese 计算标题余弦相似度。
     仅在 L2 未能判定时触发，且候选集限制在同月+同省范围内。
L4 — 关键事实交叉验证：纯规则，对比(日期差/城市/死亡/类型) 4 项。零成本。

整体设计目标：让 90% 以上的重复在 L1+L2 就被拦截，
只留下 ~10% 的边界案例进入 L3 语义判断。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from typing import Optional

from construction_agent.collector.base import RawAccidentText

logger = logging.getLogger(__name__)


class Deduplicator:
    """四层递进式查重器。

    用法：
        dedup = Deduplicator()
        dedup.load_existing_dataset(dataset_records)  # 加载已有数据

        for item in new_items:
            result = dedup.check(item)
            if result["is_duplicate"]:
                dedup.merge(item, result["matched_id"])
            else:
                dedup.add(item)
    """

    # ── 内部索引结构 ──
    # L1: url_hash → accident_id
    _url_index: dict[str, str]
    # L2: fingerprint → accident_id
    _fingerprint_index: dict[str, str]
    # L2: (year_month, province) → [accident_ids] 用于缩小 L3 候选集
    _spatial_temporal_index: dict[str, list[str]]
    # 完整记录缓存: accident_id → record_dict（用于合并和事实对比）
    _records: dict[str, dict]
    # L3 语义模型（延迟加载）
    _embed_model = None  # SentenceTransformer

    # ── 配置阈值 ──
    FINGERPRINT_EXACT_MATCH = True  # L2 指纹完全匹配即判重
    SEMANTIC_SIMILARITY_THRESHOLD = 0.85  # L3 余弦相似度阈值
    TITLE_MIN_LENGTH_FOR_SEMANTIC = 6  # 标题至少6个字符才做语义去重

    def __init__(self):
        self._url_index = {}
        self._fingerprint_index = {}
        self._spatial_temporal_index = defaultdict(list)
        self._records = {}

    # ═══════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════

    def load_existing_dataset(self, records: list[dict]):
        """从现有数据集中加载已有记录并构建索引。

        Args:
            records: 数据集记录列表（来自 DatasetManager.get_all() 或直接从 JSON 读取）
        """
        count = 0
        for record in records:
            accident_id = record.get("id", "")
            if not accident_id:
                continue

            self._records[accident_id] = record
            count += 1

            # L1: URL 索引
            source_urls = record.get("source_urls", []) or []
            for url in source_urls:
                url_hash = self._hash_url(url)
                if url_hash:
                    self._url_index[url_hash] = accident_id

            # L2: 指纹索引
            fp = self._build_record_fingerprint(record)
            if fp:
                self._fingerprint_index[fp] = accident_id

            # 时空索引 (L3 候选集优化)
            st_key = self._build_spatial_temporal_key(record)
            if st_key:
                self._spatial_temporal_index[st_key].append(accident_id)

        logger.info(f"Deduplicator: 已加载 {count} 条现有记录，"
                    f"URL索引 {len(self._url_index)} 条，"
                    f"指纹索引 {len(self._fingerprint_index)} 条")

    def check(self, item: RawAccidentText) -> dict:
        """检查一条新采集的文本是否与已有数据重复。

        Args:
            item: 经过规则提取的原始事故文本

        Returns:
            {
                "is_duplicate": bool,
                "matched_id": str | None,   # 匹配到的已有事故ID
                "layer": str,                # 在哪一层被判定为重复 (L1/L2/L3/L4)
                "confidence": float,         # 判定置信度
                "matched_title": str | None, # 匹配到的已有记录标题（用于人工核查）
            }
        """
        # ── L1: URL 去重 ──
        if item.source_url:
            url_hash = self._hash_url(item.source_url)
            if url_hash and url_hash in self._url_index:
                matched_id = self._url_index[url_hash]
                matched_title = self._records.get(matched_id, {}).get("title", "")
                logger.debug(f"L1 URL去重: {item.source_url[:80]} → {matched_title[:40]}")
                return {
                    "is_duplicate": True,
                    "matched_id": matched_id,
                    "layer": "L1",
                    "confidence": 1.0,
                    "matched_title": matched_title,
                }

        # ── L2: 指纹去重 ──
        fp = item.generate_fingerprint()
        if fp and fp in self._fingerprint_index:
            matched_id = self._fingerprint_index[fp]
            matched_title = self._records.get(matched_id, {}).get("title", "")
            logger.debug(f"L2 指纹去重: {fp} → {matched_title[:40]}")
            return {
                "is_duplicate": True,
                "matched_id": matched_id,
                "layer": "L2",
                "confidence": 0.95,
                "matched_title": matched_title,
            }

        # ── L3: 语义相似度去重 ──
        if (item.title and len(item.title) >= self.TITLE_MIN_LENGTH_FOR_SEMANTIC):
            result = self._check_semantic_similarity(item)
            if result["is_duplicate"]:
                return result

        # ── L4: 关键事实交叉验证 ──
        result = self._check_fact_cross_validation(item)
        if result["is_duplicate"]:
            return result

        # ── 不重复 ──
        return {
            "is_duplicate": False,
            "matched_id": None,
            "layer": None,
            "confidence": 0.0,
            "matched_title": None,
        }

    def add(self, item: RawAccidentText, accident_id: str, record: dict | None = None):
        """将新记录加入索引（在确认不重复后调用）。

        Args:
            item: 已确认不重复的原始文本
            accident_id: 分配的事故ID
            record: 已转换为完整记录 dict（可选，用于后续 L4 对比）
        """
        # L1 索引
        if item.source_url:
            url_hash = self._hash_url(item.source_url)
            if url_hash:
                self._url_index[url_hash] = accident_id

        # L2 索引
        fp = item.generate_fingerprint()
        if fp:
            self._fingerprint_index[fp] = accident_id

        # 时空索引
        st_key = self._build_item_spatial_temporal_key(item)
        if st_key:
            self._spatial_temporal_index[st_key].append(accident_id)

        # 记录缓存
        if record:
            self._records[accident_id] = record

    def merge(self, item: RawAccidentText, matched_id: str):
        """将新来源的信息合并到已有记录。

        Args:
            item: 被判定为重复的新采集文本
            matched_id: 匹配到的已有事故ID
        """
        record = self._records.get(matched_id)
        if not record:
            return

        # 合并 URL
        if item.source_url:
            existing_urls = set(record.get("source_urls", []) or [])
            if item.source_url not in existing_urls:
                existing_urls.add(item.source_url)
                record["source_urls"] = list(existing_urls)
                # 同步更新 L1 索引
                url_hash = self._hash_url(item.source_url)
                if url_hash and url_hash not in self._url_index:
                    self._url_index[url_hash] = matched_id

        # 记录来源数
        record["_source_count"] = record.get("_source_count", 1) + 1

        # 补充缺失字段
        for field in ["summary", "raw_text"]:
            old_val = record.get(field, "")
            if (not old_val or old_val == ""):
                if hasattr(item, field) and getattr(item, field, ""):
                    record[field] = getattr(item, field)

        self._records[matched_id] = record

    # ═══════════════════════════════════════════
    # L3 语义相似度
    # ═══════════════════════════════════════════

    def _check_semantic_similarity(self, item: RawAccidentText) -> dict:
        """L3: 基于标题的语义相似度去重。

        仅在时空范围受限的候选集内计算，避免全量 O(N) 计算。
        """
        st_key = self._build_item_spatial_temporal_key(item)
        candidate_ids = self._spatial_temporal_index.get(st_key, [])

        if not candidate_ids:
            return {"is_duplicate": False, "matched_id": None,
                    "layer": None, "confidence": 0.0, "matched_title": None}

        # 加载语义模型（延迟加载，首次使用才初始化）
        model = self._get_embed_model()
        if model is None:
            # 无模型时回退到编辑距离相似度
            return self._fallback_title_similarity(item, candidate_ids)

        try:
            item_embedding = model.encode(item.title, convert_to_numpy=True)
            import numpy as np

            best_sim = 0.0
            best_id = None
            best_title = None

            for cid in candidate_ids:
                rec = self._records.get(cid)
                if not rec:
                    continue
                rec_title = rec.get("title", "")
                if not rec_title or len(rec_title) < self.TITLE_MIN_LENGTH_FOR_SEMANTIC:
                    continue

                rec_embedding = model.encode(rec_title, convert_to_numpy=True)
                sim = float(np.dot(item_embedding, rec_embedding) /
                           (np.linalg.norm(item_embedding) * np.linalg.norm(rec_embedding) + 1e-8))

                if sim > best_sim:
                    best_sim = sim
                    best_id = cid
                    best_title = rec_title

            if best_sim >= self.SEMANTIC_SIMILARITY_THRESHOLD:
                logger.debug(f"L3 语义去重: '{item.title[:40]}' ≈ '{best_title[:40]}' (sim={best_sim:.3f})")
                return {
                    "is_duplicate": True,
                    "matched_id": best_id,
                    "layer": "L3",
                    "confidence": best_sim,
                    "matched_title": best_title,
                }

        except Exception as e:
            logger.warning(f"语义相似度计算异常: {e}，回退到字符相似度")
            return self._fallback_title_similarity(item, candidate_ids)

        return {"is_duplicate": False, "matched_id": None,
                "layer": None, "confidence": 0.0, "matched_title": None}

    def _fallback_title_similarity(self, item: RawAccidentText, candidate_ids: list[str]) -> dict:
        """回退方案：基于编辑距离的标题相似度（无需模型）。"""
        from difflib import SequenceMatcher

        best_sim = 0.0
        best_id = None
        best_title = None

        normalized_title = self._normalize_for_comparison(item.title)

        for cid in candidate_ids:
            rec = self._records.get(cid)
            if not rec:
                continue
            rec_title = self._normalize_for_comparison(rec.get("title", ""))
            if not rec_title:
                continue

            sim = SequenceMatcher(None, normalized_title, rec_title).ratio()
            if sim > best_sim:
                best_sim = sim
                best_id = cid
                best_title = rec_title

        if best_sim >= 0.80:
            return {
                "is_duplicate": True,
                "matched_id": best_id,
                "layer": "L3_fallback",
                "confidence": best_sim,
                "matched_title": best_title,
            }

        return {"is_duplicate": False, "matched_id": None,
                "layer": None, "confidence": 0.0, "matched_title": None}

    # ═══════════════════════════════════════════
    # L4 关键事实交叉验证
    # ═══════════════════════════════════════════

    def _check_fact_cross_validation(self, item: RawAccidentText) -> dict:
        """L4: 当 L1-L3 未能判定时，对比关键事实字段。

        4 项检查中 ≥3 项匹配 → 视为重复。
        """
        # 需要至少有一些关键字段
        if not item.extracted_date and not item.extracted_city and item.extracted_deaths == 0:
            return {"is_duplicate": False, "matched_id": None,
                    "layer": None, "confidence": 0.0, "matched_title": None}

        # 在时空候选集中查找
        st_key = self._build_item_spatial_temporal_key(item)
        candidate_ids = self._spatial_temporal_index.get(st_key, [])

        best_score = 0
        best_id = None
        best_title = None

        for cid in candidate_ids:
            rec = self._records.get(cid)
            if not rec:
                continue

            score = 0
            checks = 0

            # 检查1: 日期差 ≤ 3天
            if item.extracted_date and rec.get("date"):
                try:
                    from datetime import date as dt_date, timedelta
                    rec_date = dt_date.fromisoformat(rec["date"])
                    if rec_date.month == item.extracted_date.month:
                        day_diff = abs((rec_date - item.extracted_date).days)
                        if day_diff <= 3:
                            score += 1
                        elif day_diff <= 7:
                            score += 0.5
                    elif abs(rec_date.month - item.extracted_date.month) <= 1:
                        score += 0.5
                except (ValueError, TypeError):
                    pass
                checks += 1

            # 检查2: 城市相同
            if item.extracted_city and rec.get("city"):
                if item.extracted_city == rec["city"]:
                    score += 1
                checks += 1

            # 检查3: 死亡人数差 ≤ 1
            if item.extracted_deaths > 0 and rec.get("deaths", 0) > 0:
                death_diff = abs(item.extracted_deaths - rec["deaths"])
                if death_diff == 0:
                    score += 1
                elif death_diff == 1:
                    score += 0.5
                checks += 1

            # 检查4: 事故类型相同
            if item.extracted_accident_type and rec.get("accident_type"):
                if item.extracted_accident_type == rec["accident_type"]:
                    score += 1
                checks += 1

            if checks >= 2 and score / checks >= 0.75:
                if score > best_score:
                    best_score = score
                    best_id = cid
                    best_title = rec.get("title", "")

        if best_score >= 2 and best_id:
            logger.debug(f"L4 事实验证去重: score={best_score}")
            return {
                "is_duplicate": True,
                "matched_id": best_id,
                "layer": "L4",
                "confidence": min(0.9, best_score / 4),
                "matched_title": best_title,
            }

        return {"is_duplicate": False, "matched_id": None,
                "layer": None, "confidence": 0.0, "matched_title": None}

    # ═══════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════

    @staticmethod
    def _hash_url(url: str) -> str:
        """对 URL 做标准化哈希。"""
        if not url:
            return ""
        # 去除尾部斜杠和 www 前缀使 URL 规范化
        normalized = url.strip().rstrip('/')
        normalized = re.sub(r'^https?://(?:www\.)?', '', normalized)
        # 去除 URL fragment
        normalized = normalized.split('#')[0]
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _build_record_fingerprint(record: dict) -> str:
        """从已有记录的 dict 构建指纹。"""
        parts = []
        date_str = record.get("date", "")
        if date_str and len(date_str) >= 7:
            parts.append(date_str[:7])
        city = record.get("city", "")
        if city:
            parts.append(city)
        deaths = record.get("deaths", 0) or 0
        if deaths > 0:
            parts.append(f"d{deaths}")
        injuries = record.get("injuries", 0) or 0
        if injuries > 0:
            parts.append(f"i{injuries}")
        atype = record.get("accident_type", "")
        if atype:
            parts.append(atype)

        if not parts:
            return ""
        key = "|".join(parts)
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _build_spatial_temporal_key(record: dict) -> str:
        """构建时空索引键：(年月, 省份)。"""
        date_str = record.get("date", "") or ""
        ym = date_str[:7] if len(date_str) >= 7 else ""
        province = record.get("location", "") or ""
        if province:
            province = province.rstrip("省市自治区县")
        if not ym and not province:
            return ""
        return f"{ym}|{province}"

    @staticmethod
    def _build_item_spatial_temporal_key(item: RawAccidentText) -> str:
        """为 RawAccidentText 构建时空索引键。"""
        ym = ""
        if item.extracted_date:
            ym = item.extracted_date.strftime("%Y-%m")
        province = item.extracted_province or ""
        if province:
            province = province.rstrip("省市自治区县")
        if not ym and not province:
            return ""
        return f"{ym}|{province}"

    @staticmethod
    def _normalize_for_comparison(text: str) -> str:
        """标准化文本用于比较。"""
        t = re.sub(r'\s+', '', text)
        t = re.sub(r'[，。！？、；：""''【】《》（）….,!?;:(){}[]【】「」『』—-]', '', t)
        return t.lower()

    def _get_embed_model(self):
        """延迟加载 text2vec 语义模型。"""
        if self._embed_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                # 使用轻量中文模型
                self._embed_model = SentenceTransformer(
                    'shibing624/text2vec-base-chinese',
                    device='cpu',
                )
                logger.info("已加载语义去重模型: text2vec-base-chinese")
            except ImportError:
                logger.warning(
                    "sentence-transformers 未安装，L3 语义去重将使用编辑距离回退方案。"
                    "安装方法: pip install sentence-transformers"
                )
                self._embed_model = False  # 标记为已尝试
            except Exception as e:
                logger.warning(f"语义模型加载失败: {e}")
                self._embed_model = False
        return self._embed_model if self._embed_model is not False else None

    def stats(self) -> dict:
        """返回索引统计信息。"""
        return {
            "url_index_size": len(self._url_index),
            "fingerprint_index_size": len(self._fingerprint_index),
            "spatial_temporal_groups": len(self._spatial_temporal_index),
            "records_cached": len(self._records),
            "model_loaded": self._embed_model is not None and self._embed_model is not False,
        }
