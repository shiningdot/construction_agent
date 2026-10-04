"""输入层数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AccidentInput(BaseModel):
    """PDF / 文本处理后标准化输入片段。

    从 PDF 提取的单个事故文本，作为后续提取 Pipeline 的输入。
    """

    text: str = Field(description="事故文本内容")
    source: str = Field(default="", description="源文件名，如 '事故通报_2024.pdf'")
    page_range: str = Field(default="", description="页码范围，如 '1-3'")
    metadata: dict = Field(default_factory=dict, description="扩展元数据（MinerU 解析信息等）")
