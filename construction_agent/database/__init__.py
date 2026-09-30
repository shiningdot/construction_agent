"""数据集管理模块。

## 这个包已经不在生产路径上

`DatasetManager` 管的是 JSON 快照（`data/accidents.json` 一类）；主库
`data/caiems_v2.db` 的读写走 `construction_agent.review.promote`（并入）、
`construction_agent.web.staging_db`（暂存区）与各批处理脚本里的裸 sqlite3。

`sqlite_repository.py` 与 `models.py` 是 SQLAlchemy ORM 时代的产物，**已删除**
（2026-10-01 核对：本目录下只剩本文件与 `dataset_manager.py`）。删除时漏改了
`tests/test_legacy_isolation.py` 的 LEGACY 名单，于是那两条"不该被生产加载"的
检查变成恒真的空转——名单本身不可信这件事，现在由
`test_legacy_list_has_no_ghosts` 兜住。

## 为什么改成惰性导出

原先这里是模块级 `from .dataset_manager import DatasetManager`，任何一次
`import construction_agent.database` 都会把它加载进来。而 `DatasetManager`
又会拉起 `construction_agent.models.accident` 那批 V1 四维度 dataclass。
惰性化之后，取才加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查器
    from construction_agent.database.dataset_manager import DatasetManager

__all__ = ["DatasetManager"]

_LAZY = {
    "DatasetManager": ("construction_agent.database.dataset_manager",
                       "DatasetManager"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
