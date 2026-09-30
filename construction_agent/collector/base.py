"""采集器抽象基类和通用数据结构。"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class RawAccidentText:
    """从数据源采集到的原始事故文本（尚未经过 LLM 提取）。

    设计意图：爬虫只负责填充此结构体，不调用任何 LLM。
    后续由清洗层和提取层统一处理。
    """

    # ── 唯一标识 ──
    # 2026-09-30 补：`id` 此前不是本类的字段，却被两处直接读取——
    #   llm_classifier.py:413/:422/:432 的 `item.id`
    #   （同文件对 source_name 用的是 getattr(item,'source_name','') 兜底，
    #     唯独 id 没有兜底）
    # 于是任何未在调用点手工补 `item.id = ...` 的实例都会在 classify() 里抛
    # AttributeError。run_batch_pipeline.py:179 那两行动态赋值实际是这个
    # 缺陷的补丁，只打在调用点。今把两个字段正式收进本类。
    id: str = ""  # 采集记录唯一标识（指纹或外部 id）
    source: str = ""  # 数据来源标注（如 safehoo / open_gov_shenyang / mohurd）

    # ── 原始内容 ──
    title: str = ""  # 事故标题（从页面提取，可能为空）
    raw_text: str = ""  # 原始全文（HTML已清洗为纯文本）
    raw_html: str = ""  # 原始 HTML（保留用于后续二次解析，不超过 50KB）

    # ── 来源追踪 ──
    source_name: str = ""  # 来源中文名（与 source 的英文 key 相对，用于展示）
    source_url: str = ""  # 原始页面 URL
    fetch_time: str = ""  # 采集时间 ISO 格式

    # ── 规则预提取字段（纯代码提取，用于去重指纹和预筛选）──
    # 这些字段由 RuleBasedFieldExtractor 在清洗阶段填充，
    # 准确率约 80-95%，用于决定是否需要 LLM 增强
    extracted_date: Optional[date] = None  # 预提取的事故日期
    extracted_city: str = ""  # 预提取的城市
    extracted_province: str = ""  # 预提取的省份
    extracted_deaths: int = 0  # 预提取的死亡人数
    extracted_injuries: int = 0  # 预提取的受伤人数
    extracted_accident_type: str = ""  # 预提取的事故类型
    extracted_severity: str = ""  # 预提取的事故等级

    # ── 字段置信度（0.0-1.0，由规则提取器评估）──
    confidence_date: float = 0.0
    confidence_city: float = 0.0
    confidence_deaths: float = 0.0
    confidence_accident_type: float = 0.0
    confidence_overall: float = 0.0  # 综合置信度

    # ── 元数据 ──
    extra: dict = field(default_factory=dict)  # 采集器自定义扩展数据

    def generate_url_hash(self) -> str:
        """生成 URL 哈希（用于 L1 去重）。"""
        if not self.source_url:
            return ""
        return hashlib.md5(self.source_url.strip().encode("utf-8")).hexdigest()[:12]

    def generate_fingerprint(self) -> str:
        """生成事故指纹（用于 L2 去重）。

        基于客观事实：日期+城市+伤亡+事故类型。
        同一事故无论被哪个来源报道，这些数据不变。
        """
        parts = []
        if self.extracted_date:
            # 只用年月，容忍日期 ±1-2 天的偏差
            parts.append(self.extracted_date.strftime("%Y-%m"))
        if self.extracted_city:
            parts.append(self.extracted_city)
        if self.extracted_deaths > 0:
            parts.append(f"d{self.extracted_deaths}")
        if self.extracted_injuries > 0:
            parts.append(f"i{self.extracted_injuries}")
        if self.extracted_accident_type:
            parts.append(self.extracted_accident_type)

        if not parts:
            # 无可用事实时退化为标题哈希
            return hashlib.md5(self.title.strip().encode("utf-8")).hexdigest()[:12]

        key = "|".join(parts)
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]

    def generate_title_hash(self) -> str:
        """生成标准化标题哈希（用于 L3 语义去重的快速索引）。"""
        # 去空白、去标点、转小写
        normalized = re.sub(r'\s+', '', self.title)
        normalized = re.sub(r'[，。！？、；：""''【】《》（）….,!?;:(){}[]—-]', '', normalized)
        normalized = normalized.lower().strip()
        if len(normalized) < 4:
            return ""
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]

    def needs_llm_enhancement(self) -> bool:
        """判断是否需要 LLM 增强提取。

        当综合置信度低于阈值时，说明规则提取质量不够，
        需要提交 LLM 进行更精确的结构化提取。
        """
        # 阈值：综合置信度 < 0.7 或没有提取到足够字段
        if self.confidence_overall < 0.7:
            return True
        # 核心字段缺失太多也需要 LLM
        critical_missing = sum([
            self.extracted_date is None,
            not self.extracted_city,
            self.extracted_deaths == 0,
            not self.extracted_accident_type,
        ])
        if critical_missing >= 3:
            return True
        return False


class BaseCollector(ABC):
    """所有数据源采集器的抽象基类。

    子类只需实现：
    1. source_name() — 返回数据源唯一标识
    2. collect() — 从数据源获取原始文本列表，返回 RawAccidentText 列表

    注意：
    - collect() 不应调用任何 LLM
    - collect() 可以使用 httpx、BeautifulSoup、API 调用等纯代码手段
    - 采集失败应 log warning 并跳过，不中断整体流程
    """

    @abstractmethod
    def source_name(self) -> str:
        """返回数据源唯一标识。

        约定格式：类型_名称，如 'open_gov_shenyang', 'safehoo_cases', 'mohurd_annual'
        """
        ...

    @abstractmethod
    def collect(self) -> list[RawAccidentText]:
        """从数据源采集原始事故文本。

        Returns:
            RawAccidentText 列表，采集失败时返回空列表
        """
        ...

    def get_crawl_delay(self) -> float:
        """返回请求间隔（秒），子类可覆写以遵守目标网站的速率限制。

        默认 2 秒，对政府网站应设置更长间隔（5-10 秒）。
        """
        return 2.0

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.source_name()})>"
