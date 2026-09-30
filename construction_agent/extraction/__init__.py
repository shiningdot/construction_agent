"""特征提取模块。

## 新链路在这个包里

`caics_pipeline.py`（S1–S4 编排，生产链路）、`step1_basic_extractor.py`、
`step2_caics_encoder.py`、`staging_executor.py`、`evidence_quality.py`。
生产代码直接从这些子模块导入：

    from construction_agent.extraction.caics_pipeline import CaicsPipeline

## 旧 `ExtractionPipeline` 为什么不在这里直接导出

`claude_extractor.py` 是 V1 四维度 + HFACS-CS 的实现，产出与主库不同构
（`output/csv_formatter.py`、`scripts/batch_process.py` 还在用它做历史复核）。

原先这里是模块级 `from .claude_extractor import ...`，结果是：**本包内任何
一个模块被导入，都会连带加载整个旧提取器**。`staging_executor` 就在这个包里，
所以在线管道每起一次提取，都先把旧理论拉进内存一遍。

改成 PEP 562 惰性导出：`from construction_agent.extraction import
ExtractionPipeline` 仍可用，但不主动加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from construction_agent.extraction.claude_extractor import (
        ClaudeExtractor,
        ExtractionPipeline,
    )

__all__ = ["ClaudeExtractor", "ExtractionPipeline"]

_LAZY = {
    "ClaudeExtractor": ("construction_agent.extraction.claude_extractor",
                        "ClaudeExtractor"),
    "ExtractionPipeline": ("construction_agent.extraction.claude_extractor",
                           "ExtractionPipeline"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
