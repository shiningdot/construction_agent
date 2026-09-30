"""建筑施工事故数据采集模块。

## 导入约定

新代码从子模块导入：

    from construction_agent.collector import registry, executor
    from construction_agent.collector.base import BaseCollector, RawAccidentText

## 旧 `CollectorPipeline` 为什么不在这里直接导出

它编排的是旧链路：`TextCleaner` → `RuleBasedFieldExtractor` → `DatasetManager`
（写 JSON 快照），需要 LLM 增强时调 `ExtractionPipeline`（V1 四维度 + HFACS-CS）。
新 web 的采集走 `collector.executor` + `/api/collect/*`，落 `raw_accidents` 表。

问题不在"它旧"，在**加载时机**。原先这里是模块级
`from construction_agent.collector.pipeline import CollectorPipeline`，而
`web/views.py` 有 `from ..collector import executor`——于是新 web 的**每一个
页面**都顺带把旧编排器、`DatasetManager`、`ExtractionPipeline` 拉进内存。
用不用得上另说，先加载了。

改成 PEP 562 惰性导出后，`from construction_agent.collector import
CollectorPipeline` 仍然可用（`collector/scripts/run_collect.py` 没断），
但**不再自动加载**。用不用得到，由谁真去取它决定。

`base` 不惰性——`RawAccidentText` / `BaseCollector` 是新链路的核心契约，
`executor.py`、`caics_pipeline.py` 与全部 sources 都要它。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from construction_agent.collector.base import BaseCollector, RawAccidentText

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from construction_agent.collector.pipeline import CollectorPipeline

__all__ = ["BaseCollector", "RawAccidentText", "CollectorPipeline"]

_LAZY = {
    "CollectorPipeline": ("construction_agent.collector.pipeline",
                          "CollectorPipeline"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
