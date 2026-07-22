"""原始数据缓存存储层。

与 DatasetManager 分离，专门存储爬虫刚采集的原始事故数据。
这些数据尚未经过 LLM 提取和去重处理，保存在独立的 JSON 文件中。

设计原则：
- 爬虫只管"找到并存储原始文本"，不涉及 LLM 或结构化提取
- 原始数据缓存与已处理数据集完全隔离
- 用户可以在 GUI 中审阅原始数据，选择哪些需要进一步处理
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from construction_agent.config import settings

logger = logging.getLogger(__name__)


class RawAccidentStore:
    """原始事故数据缓存。

    文件位置: data/raw_accidents.json

    每条记录格式:
    {
        "id": "md5(url)前12位",
        "title": "事故标题",
        "content": "正文全文",
        "source_url": "原始URL",
        "source_name": "数据源名称（如 safehoo）",
        "crawl_date": "采集日期 ISO",
        "publish_date": "发布日期（从页面提取）",
        "status": "new" | "processed" | "filtered_out" | "review",
        "filter_reason": "被过滤的原因（如有）",
        "classification": "pending_review" | "confirmed_construction" | "confirmed_non_construction",
        "classification_reason": "自动分类的判定理由",
        "classification_confidence": "high" | "low",
        "review_note": "人工审核备注",
    }
    """

    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or (settings.DATA_DIR / "raw_accidents.json")
        self._data: list[dict] = []
        self._url_index: set[str] = set()  # 已缓存的 URL 哈希
        self._load()

    def _load(self):
        """从文件加载已有缓存。"""
        if self.filepath.exists():
            try:
                content = self.filepath.read_text(encoding="utf-8")
                if content.strip():
                    self._data = json.loads(content)
                else:
                    self._data = []
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"原始缓存文件解析失败: {e}")
                self._data = []
        else:
            self._data = []
            self.filepath.parent.mkdir(parents=True, exist_ok=True)

        # 重建 URL 索引
        self._url_index = set()
        for record in self._data:
            rid = record.get("id", "")
            if rid:
                self._url_index.add(rid)

        logger.info(f"原始缓存已加载: {len(self._data)} 条")

    def _save(self):
        """保存到文件。"""
        self.filepath.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _hash_url(url: str) -> str:
        """URL → MD5 前12位作为唯一 ID。"""
        return hashlib.md5(url.strip().encode("utf-8")).hexdigest()[:12]

    def exists(self, url: str) -> bool:
        """检查 URL 是否已被缓存。"""
        return self._hash_url(url) in self._url_index

    def add(self, title: str, content: str, source_url: str,
            source_name: str = "", publish_date: str = "",
            status: str = "new", extra: dict | None = None) -> Optional[str]:
        """添加一条原始记录。

        Args:
            title: 事故标题
            content: 正文内容
            source_url: 来源 URL
            source_name: 数据源名称
            publish_date: 发布日期
            status: 初始状态 (new / filtered_out)
            extra: 额外字段（如 pdf_urls, needs_ocr 等）

        Returns:
            记录 ID，如果已存在则返回 None
        """
        rid = self._hash_url(source_url)
        if rid in self._url_index:
            return None  # 已存在，跳过

        record = {
            "id": rid,
            "title": title,
            "content": content,
            "source_url": source_url,
            "source_name": source_name,
            "crawl_date": datetime.now(timezone.utc).isoformat(),
            "publish_date": publish_date,
            "status": status,
            "filter_reason": "",
        }

        # 合并额外字段（如 pdf_urls, needs_ocr）
        if extra:
            record.update(extra)

        self._data.append(record)
        self._url_index.add(rid)
        return rid

    def add_batch(self, items: list[dict]) -> int:
        """批量添加，返回新增数量。

        每项标准字段: title, content, source_url, source_name, publish_date, status
        额外字段（如 pdf_urls, needs_ocr）会自动保留。
        """
        # 标准字段名
        standard_fields = {
            "title", "content", "source_url", "source_name",
            "publish_date", "status",
        }
        added = 0
        for item in items:
            # 分离标准字段和额外字段
            extra = {k: v for k, v in item.items() if k not in standard_fields
                     and k not in ("id", "crawl_date", "filter_reason")}
            rid = self.add(
                title=item.get("title", ""),
                content=item.get("content", ""),
                source_url=item.get("source_url", ""),
                source_name=item.get("source_name", ""),
                publish_date=item.get("publish_date", ""),
                status=item.get("status", "new"),
                extra=extra if extra else None,
            )
            if rid:
                added += 1

        if added > 0:
            self._save()
            logger.info(f"原始缓存: 新增 {added} 条 (总计 {len(self._data)} 条)")

        return added

    def get_new(self) -> list[dict]:
        """获取所有状态为 'new' 的未处理记录。"""
        return [r for r in self._data if r.get("status") == "new"]

    def get_all(self) -> list[dict]:
        """获取所有记录。"""
        return list(self._data)

    def mark_processed(self, record_ids: list[str]):
        """将指定记录标记为已处理。"""
        for r in self._data:
            if r.get("id") in record_ids:
                r["status"] = "processed"
        self._save()

    def mark_filtered(self, record_id: str, reason: str = ""):
        """将记录标记为被过滤。"""
        for r in self._data:
            if r.get("id") == record_id:
                r["status"] = "filtered_out"
                r["filter_reason"] = reason
                break
        self._save()

    def stats(self) -> dict:
        """缓存统计信息。"""
        new_count = sum(1 for r in self._data if r.get("status") == "new")
        processed_count = sum(1 for r in self._data if r.get("status") == "processed")
        filtered_count = sum(1 for r in self._data if r.get("status") == "filtered_out")
        review_count = sum(1 for r in self._data if r.get("status") == "review")
        confirmed_count = sum(1 for r in self._data
                            if r.get("classification") == "confirmed_construction")
        rejected_count = sum(1 for r in self._data
                            if r.get("classification") == "confirmed_non_construction")
        return {
            "total": len(self._data),
            "new": new_count,
            "processed": processed_count,
            "filtered_out": filtered_count,
            "review": review_count,
            "confirmed_construction": confirmed_count,
            "confirmed_non_construction": rejected_count,
        }

    # ── 分类/审核相关方法 ──

    def get_review(self) -> list[dict]:
        """获取所有待人工审核的记录（status: review）。"""
        return [r for r in self._data if r.get("status") == "review"]

    def get_classified(self) -> list[dict]:
        """获取所有已完成分类的记录。"""
        return [r for r in self._data
                if r.get("classification") in
                ("confirmed_construction", "confirmed_non_construction")]

    def classify(self, record_id: str, classification: str,
                 note: str = "") -> bool:
        """单条记录分类。

        Args:
            record_id: 记录 ID
            classification: "confirmed_construction" 或 "confirmed_non_construction"
            note: 审核备注

        Returns:
            是否找到并更新
        """
        for r in self._data:
            if r.get("id") == record_id:
                r["classification"] = classification
                r["review_note"] = note
                if classification == "confirmed_construction":
                    r["status"] = "new"  # 确认后转为可导入状态
                elif classification == "confirmed_non_construction":
                    r["status"] = "filtered_out"
                self._save()
                return True
        return False

    def batch_classify(self, record_ids: list[str], classification: str) -> int:
        """批量分类。

        Returns:
            成功更新的记录数
        """
        count = 0
        id_set = set(record_ids)
        for r in self._data:
            if r.get("id") in id_set:
                r["classification"] = classification
                if classification == "confirmed_construction":
                    r["status"] = "new"
                elif classification == "confirmed_non_construction":
                    r["status"] = "filtered_out"
                count += 1
        if count > 0:
            self._save()
        return count

    def clear(self):
        """清空缓存。"""
        self._data = []
        self._url_index = set()
        self._save()
        logger.info("原始缓存已清空")
