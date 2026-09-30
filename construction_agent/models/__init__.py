"""数据模型模块（V1 四维度时代）。

## 这批模型不对应主库

`accident.py` 里的 `HumanFactor` / `MachineFactor` / `ManagementFactor` /
`EnvironmentalFactor` 是 V1 四维度体系的 dataclass，主库 `accidents` 表里
对应的四列（`human_factors` 等）**实测非空数为 0**。主库数据的结构由
`accidents`（48 列）+ `caics_causes`（编码与证据链）表达，没有对应的
dataclass——查询侧用 `web/accidents_query.py` 的字典行，不是这些模型。

还留在生产路径上的只有 `Accident` 本身（`main.py` 的旧命令展示用）与
`AccidentType` / `SeverityLevel` 两个枚举。

## 为什么改成惰性导出

原先模块级导入会让 `import construction_agent.models` 直接拉起整个 V1 模型层。
惰性化之后，取才加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from construction_agent.models.accident import (
        Accident,
        AccidentCreateRequest,
        AccidentExtractionResult,
        AccidentType,
        EnvironmentalFactor,
        HumanFactor,
        MachineFactor,
        ManagementFactor,
        SeverityLevel,
    )

__all__ = [
    "Accident",
    "AccidentType",
    "SeverityLevel",
    "HumanFactor",
    "MachineFactor",
    "ManagementFactor",
    "EnvironmentalFactor",
    "AccidentCreateRequest",
    "AccidentExtractionResult",
]

_LAZY = {
    name: ("construction_agent.models.accident", name) for name in __all__
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
