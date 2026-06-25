"""V1 Pipeline：固定流程的 LLM 提取管道。

流程：文本输入 → Claude API 结构化提取 → 自动派生字段 → 保存
支持单条和 CSV 批量处理，含重试和断点续传。
"""

from construction_agent.versions.v1.pipeline import V1Pipeline

__all__ = ["V1Pipeline"]
