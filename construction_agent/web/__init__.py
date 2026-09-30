"""Web 模块。

## 导入约定

新代码请从具体子模块导入：

    from construction_agent.web import db, province, overview, accidents_query
    from construction_agent.web.factory import create_app

`app` 与 `create_app` 也可从本包直接取，但走的是下面的惰性入口。

## 为什么是惰性的

旧 `app.py` 在**导入时**就执行 `mkdir`、建 `ExtractionPipeline` 单例、
加载 HFACS 分类文件。本包原先无条件 `from .app import app`，导致任何一个
`from construction_agent.web import db` 都会触发上述副作用——
`scripts/add_indexes.py` 只是想解析一个库路径，却要把整个旧应用连根拔起。

PEP 562 的模块级 `__getattr__` 让 `from construction_agent.web import app`
仍然可用（`main.py:295` 的旧入口没断），但不再有导入即副作用。
M1–M5 逐个模块落地后，这个兼容入口连同 `app.py` 一并移除。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from flask import Flask

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from .app import app as legacy_app

        return legacy_app
    if name == "create_app":
        from .factory import create_app as factory

        return factory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
