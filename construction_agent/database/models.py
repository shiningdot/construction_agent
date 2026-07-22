"""SQLAlchemy 2.0 ORM 模型 —— 对应 CAIEMS 数据库全部 8 张表。

表清单：
    accidents            主事故表（精确 SQL 类型）
    factors              统一致因表（human/machine/management/environmental）
    hfacs_details        HFACS-CS 编码明细
    source_urls          来源 URL
    search_queries       搜索查询
    search_snippets      搜索摘要
    images               图片路径引用
    processing_history   处理历史
    llm_api_logs         LLM API 调用日志
    search_logs          搜索日志
    pdf_processing_logs  PDF 处理日志
"""

from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════
# 核心域表
# ═══════════════════════════════════════════════════════════════════

class Accident(Base):
    """事故主表 —— 精确 SQL 类型。"""

    __tablename__ = "accidents"

    # ── 唯一标识 ──
    id: Mapped[str] = mapped_column(Text, primary_key=True)

    # ── 基础信息 ──
    title: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ISO date YYYY-MM-DD
    accident_time: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # HH:MM
    location: Mapped[str] = mapped_column(Text, nullable=False, default="")
    city: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accident_location_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    project_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    project_type: Mapped[str] = mapped_column(Text, nullable=False, default="")
    building_structure: Mapped[str] = mapped_column(Text, nullable=False, default="")
    building_completion_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    construction_phase: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weather: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 事故属性 ──
    accident_type: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 伤亡经济 ──
    deaths: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    injuries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trapped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    direct_economic_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── 时间派生 ──
    day_of_week: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    season: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    time_period: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lighting: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── 致因文本 ──
    direct_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    contractor: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 来源 ──
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_input: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── HFACS ──
    hfacs_codes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    hfacs_encoding_version: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 元数据 ──
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    # ── 关系 ──
    factors: Mapped[list["Factor"]] = relationship(
        back_populates="accident", cascade="all, delete-orphan"
    )
    hfacs_details: Mapped[list["HfacsDetail"]] = relationship(
        back_populates="accident", cascade="all, delete-orphan"
    )
    source_urls: Mapped[list["SourceUrl"]] = relationship(
        back_populates="accident", cascade="all, delete-orphan"
    )
    search_snippets: Mapped[list["SearchSnippet"]] = relationship(
        back_populates="accident", cascade="all, delete-orphan"
    )
    search_queries: Mapped[list["SearchQuery"]] = relationship(
        back_populates="accident", cascade="all, delete-orphan"
    )
    images: Mapped[list["Image"]] = relationship(
        back_populates="accident", cascade="all, delete-orphan"
    )
    processing_history: Mapped[list["ProcessingHistory"]] = relationship(
        back_populates="accident",
    )

    __table_args__ = (
        Index("idx_acc_type", "accident_type"),
        Index("idx_acc_severity", "severity"),
        Index("idx_acc_location", "location"),
        Index("idx_acc_date", "date"),
        Index("idx_acc_deaths", "deaths"),
        Index("idx_acc_type_sev_date", "accident_type", "severity", "date"),
    )


class Factor(Base):
    """统一致因表 —— 替代 human/machine/management/environmental 四个 list。"""

    __tablename__ = "factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="CASCADE"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    accident: Mapped["Accident"] = relationship(back_populates="factors")

    __table_args__ = (
        CheckConstraint(
            "dimension IN ('human','machine','management','environmental')",
            name="ck_factor_dimension",
        ),
        Index("idx_factors_accident", "accident_id"),
        Index("idx_factors_dim", "dimension"),
    )


class HfacsDetail(Base):
    """HFACS-CS 编码明细。"""

    __tablename__ = "hfacs_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    level_name_cn: Mapped[str] = mapped_column(Text, nullable=False)
    category_name_cn: Mapped[str] = mapped_column(Text, nullable=False)
    source_dimension: Mapped[str] = mapped_column(Text, nullable=False)
    source_category: Mapped[str] = mapped_column(Text, nullable=False)
    source_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    encoding_method: Mapped[str] = mapped_column(Text, nullable=False, default="llm")

    accident: Mapped["Accident"] = relationship(back_populates="hfacs_details")

    __table_args__ = (
        Index("idx_hfacs_accident", "accident_id"),
        Index("idx_hfacs_code", "code"),
    )


class SourceUrl(Base):
    __tablename__ = "source_urls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    accident: Mapped["Accident"] = relationship(back_populates="source_urls")

    __table_args__ = (UniqueConstraint("accident_id", "url"),)


class SearchSnippet(Base):
    __tablename__ = "search_snippets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    accident: Mapped["Accident"] = relationship(back_populates="search_snippets")

    __table_args__ = (Index("idx_snippets_accident", "accident_id"),)


class SearchQuery(Base):
    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="CASCADE"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    accident: Mapped["Accident"] = relationship(back_populates="search_queries")


class Image(Base):
    """图片路径引用 —— 实际文件保留在 data/images/ 文件系统。"""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[str] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="CASCADE"), nullable=False
    )
    local_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    thumb_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    accident: Mapped["Accident"] = relationship(back_populates="images")


# ═══════════════════════════════════════════════════════════════════
# 中间数据表（新增）
# ═══════════════════════════════════════════════════════════════════

class ProcessingHistory(Base):
    """处理历史 —— 每次处理一条事故记录一次。"""

    __tablename__ = "processing_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accident_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("accidents.id", ondelete="SET NULL"), nullable=True
    )
    pipeline_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_usage_prompt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_usage_completion: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    accident: Mapped["Accident"] = relationship(back_populates="processing_history")
    llm_logs: Mapped[list["LlmApiLog"]] = relationship(
        back_populates="processing", cascade="all, delete-orphan"
    )
    search_logs: Mapped[list["SearchLog"]] = relationship(
        back_populates="processing", cascade="all, delete-orphan"
    )
    pdf_logs: Mapped[list["PdfProcessingLog"]] = relationship(
        back_populates="processing", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('success','failed','partial')", name="ck_ph_status"
        ),
        Index("idx_ph_accident", "accident_id"),
        Index("idx_ph_version", "pipeline_version"),
        Index("idx_ph_created", "created_at"),
    )


class LlmApiLog(Base):
    """LLM API 调用日志 —— 每次 API 调用一条。"""

    __tablename__ = "llm_api_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("processing_history.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thinking_block: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    json_extracted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parse_success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    processing: Mapped["ProcessingHistory"] = relationship(back_populates="llm_logs")

    __table_args__ = (
        Index("idx_llm_processing", "processing_id"),
        Index("idx_llm_parse", "parse_success"),
    )


class SearchLog(Base):
    """搜索日志 —— 每次搜索查询一条。"""

    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("processing_history.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    raw_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_api_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result_positions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    success: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    processing: Mapped["ProcessingHistory"] = relationship(back_populates="search_logs")


class PdfProcessingLog(Base):
    """PDF 处理日志 —— 每次 PDF 解析一条。"""

    __tablename__ = "pdf_processing_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processing_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("processing_history.id", ondelete="SET NULL"), nullable=True
    )
    pdf_filename: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    mineru_raw_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mineru_zip_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cleaned_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    segment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    segments_discarded: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discarded_text_sample: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    split_strategy_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    success: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    processing: Mapped["ProcessingHistory"] = relationship(back_populates="pdf_logs")
