"""数据访问抽象接口 —— 业务代码只依赖此接口，不依赖具体数据库。

本质与实现分离：
    - Pydantic Accident 领域模型 → 业务代码使用
    - AccidentRepository 抽象接口 → 业务代码注入
    - SqliteAccidentRepository / JsonAccidentRepository → 可替换实现
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from construction_agent.models.accident import Accident


@dataclass
class AddResult:
    """add() 操作的结果。"""
    action: str  # "added" / "updated" / "skipped"
    accident_id: str = ""
    previous_id: str = ""


@dataclass
class DatasetStats:
    """数据集统计。"""
    total: int = 0
    total_deaths: int = 0
    total_injuries: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)


@dataclass
class ProcessingRecord:
    """处理历史记录 —— 中间数据的入口。"""
    accident_id: Optional[str] = None
    pipeline_version: str = ""
    status: str = ""  # success / failed / partial
    retry_count: int = 0
    input_summary: str = ""
    error_message: str = ""
    error_category: str = ""
    duration_ms: int = 0
    token_usage_prompt: int = 0
    token_usage_completion: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class LlmApiRecord:
    """LLM API 调用记录。"""
    processing_id: int = 0
    model: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    raw_response: str = ""
    thinking_block: str = ""
    json_extracted: str = ""
    parse_success: bool = False
    parse_error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SearchLogRecord:
    """搜索日志记录。"""
    processing_id: int = 0
    engine: str = ""
    query: str = ""
    raw_html: str = ""
    raw_api_response: str = ""
    result_count: int = 0
    result_positions: str = ""
    success: bool = True
    error_message: str = ""
    duration_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class PdfProcessingRecord:
    """PDF 处理日志记录。"""
    processing_id: int = 0
    pdf_filename: str = ""
    mode: str = ""
    mineru_raw_output: str = ""
    mineru_zip_path: str = ""
    cleaned_text: str = ""
    segment_count: int = 0
    segments_discarded: int = 0
    discarded_text_sample: str = ""
    split_strategy_used: str = ""
    success: bool = True
    error_message: str = ""
    duration_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class AccidentRepository(ABC):
    """事故数据仓库抽象接口。

    所有业务代码通过此接口操作数据，不感知底层是 SQLite/PostgreSQL/JSON。
    """

    # ── 核心 CRUD ──

    @abstractmethod
    def add(self, accident: Accident) -> AddResult:
        """添加或更新一条事故记录（去重）。"""
        ...

    @abstractmethod
    def add_batch(self, accidents: list[Accident]) -> list[AddResult]:
        """批量添加。"""
        ...

    @abstractmethod
    def get_by_id(self, accident_id: str) -> Optional[dict]:
        """按 ID 获取单条记录。"""
        ...

    @abstractmethod
    def get_all(self) -> list[dict]:
        """获取全部记录。"""
        ...

    @abstractmethod
    def search(self, keyword: str) -> list[dict]:
        """按关键词搜索（全文检索）。"""
        ...

    @abstractmethod
    def stats(self) -> DatasetStats:
        """数据集统计（总数/按类型/按等级/按年份）。"""
        ...

    @abstractmethod
    def export(self, filepath: str, format: str = "json") -> bool:
        """导出数据集。"""
        ...

    @abstractmethod
    def clear(self):
        """清空全部数据。"""
        ...

    # ── 中间数据写入 ──

    @abstractmethod
    def log_processing(self, record: ProcessingRecord) -> int:
        """写入处理历史，返回 processing_id。"""
        ...

    @abstractmethod
    def log_llm_api(self, record: LlmApiRecord) -> int:
        """写入 LLM API 调用日志。"""
        ...

    @abstractmethod
    def log_search(self, record: SearchLogRecord) -> int:
        """写入搜索日志。"""
        ...

    @abstractmethod
    def log_pdf_processing(self, record: PdfProcessingRecord) -> int:
        """写入 PDF 处理日志。"""
        ...
