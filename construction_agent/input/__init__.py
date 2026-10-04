"""输入层模块 —— 多样化输入源统一处理。

支持 PDF（MinerU 解析）、文本、CSV/XLSX、RSS 等多种输入源，
输出标准化的 AccidentInput 供提取模块消费。
"""

from construction_agent.input.models import AccidentInput
from construction_agent.input.pdf_processor import PDFProcessor

__all__ = [
    "AccidentInput",
    "PDFProcessor",
]
