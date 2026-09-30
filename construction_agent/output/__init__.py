"""输出格式化模块。

## 服务的是旧链路

`JSONFormatter` / `CSVFormatter` 把 `Accident`（V1 四维度模型）序列化成本地
文件，供 `main.py` 的旧命令与旧批处理脚本输出中间产物。主库的对外形态只有
两种：`accidents` + `caics_causes` 两张表，以及 web 的 JSON 端点（
`web/accidents_query.py`）。没有 CSV/JSON 导出链路。

## 为什么改成惰性导出

原先模块级导入会让 `import construction_agent.output` 连带拉起两个格式化器，
而 `csv_formatter` 又依赖 V1 模型层。惰性化之后，取才加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from construction_agent.output.csv_formatter import CSVFormatter
    from construction_agent.output.json_formatter import JSONFormatter

__all__ = ["JSONFormatter", "CSVFormatter"]

_LAZY = {
    "JSONFormatter": ("construction_agent.output.json_formatter", "JSONFormatter"),
    "CSVFormatter": ("construction_agent.output.csv_formatter", "CSVFormatter"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
