"""工作库 `caiems_staging.db` 的读写连接层。

与 `db.py`（最终库只读）严格分工：

- `db.py`          → 最终库，**只读**，展示层专用
- `staging_db.py`  → 工作库，**读写**，采集/提取/审核专用

三条不可协商的规则（蓝图 03 第 5.3 节）：

1. 主表与子表写在**同一事务**——避免"主表已更新、子表还是旧的"中间态
2. 子表**先 DELETE 再 INSERT**——消除重复行的直接手段
3. 写连接**显式 `PRAGMA foreign_keys = ON`**——SQLite 的连接级开关默认 OFF，
   2026-09-23 清理掉的 1,041 行孤儿外键，根因就是"表声明了外键 + 连接没开 PRAGMA"

本模块的 `write_tx()` 把这三条固化成模板，调用方不需要自己记。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..config import settings
from .staging_schema import SCHEMA_SQL, SCHEMA_VERSION, TABLES

STAGING_DB_FILENAME = "caiems_staging.db"
ENV_STAGING_DB = "CAIEMS_STAGING_DB"


class StagingDBNotInitialized(RuntimeError):
    """工作库尚未建表。运行 scripts/create_staging_db.py 即可。"""


def resolve_staging_path() -> Path:
    """按优先级解析工作库路径。

    1. 环境变量 `CAIEMS_STAGING_DB`
    2. `settings.DATA_DIR / "caiems_staging.db"`
    """
    raw = os.environ.get(ENV_STAGING_DB)
    if raw:
        return Path(raw).expanduser().resolve()
    return (settings.DATA_DIR / STAGING_DB_FILENAME).resolve()


def connect(
    readonly: bool = False, db_path: str | Path | None = None
) -> sqlite3.Connection:
    """打开工作库连接。

    写连接会自动开启 `foreign_keys` 与 WAL；只读连接则加 `query_only` 双保险。
    """
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()

    if readonly:
        if not path.exists():
            raise StagingDBNotInitialized(f"工作库不存在：{path}")
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
    else:
        # isolation_level=None → 关闭 sqlite3 的隐式事务，由 write_tx 显式控制
        con = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA foreign_keys = ON")   # ← 规则 3
        con.execute("PRAGMA busy_timeout = 5000")

    con.row_factory = sqlite3.Row
    return con


@contextmanager
def write_tx(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """写事务上下文：`BEGIN IMMEDIATE` → 业务 → `COMMIT`，异常则 `ROLLBACK`。

    用法：

        with write_tx() as con:
            con.execute("INSERT OR REPLACE INTO accidents (...) VALUES (...)", values)
            con.execute("DELETE FROM caics_causes WHERE accident_id = ?", (aid,))
            con.executemany("INSERT INTO caics_causes (...) VALUES (...)", rows)

    `BEGIN IMMEDIATE` 立即取写锁，避免两个并发并入互相等待到超时。
    """
    con = connect(readonly=False, db_path=db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except Exception:
            # 某些错误（如外键违反）SQLite 已自动回滚，此时再 ROLLBACK 会报
            # "cannot rollback - no transaction is active"，故吞掉二次异常
            try:
                con.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        else:
            con.execute("COMMIT")
    finally:
        con.close()


# --------------------------------------------------------------------------
# 初始化
# --------------------------------------------------------------------------


def is_initialized(db_path: str | Path | None = None) -> bool:
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()
    if not path.exists():
        return False
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        hit = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_accidents'"
        ).fetchone()
        return hit is not None
    finally:
        con.close()


def init_db(db_path: str | Path | None = None) -> dict[str, Any]:
    """建表（幂等）。已存在的表不会被改动。"""
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA_SQL)
        con.execute("PRAGMA journal_mode = WAL")
        con.execute(
            "INSERT OR REPLACE INTO staging_metadata (key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        con.execute(
            "INSERT OR REPLACE INTO staging_metadata (key, value) VALUES (?, ?)",
            ("created_at", datetime.now().isoformat(timespec="seconds")),
        )
        counts = {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES
        }
    finally:
        con.close()

    return {"path": str(path), "schema_version": SCHEMA_VERSION, "tables": counts}


def assert_initialized(db_path: str | Path | None = None) -> None:
    if not is_initialized(db_path):
        raise StagingDBNotInitialized(
            f"工作库未初始化：{resolve_staging_path()}\n"
            f"   请先运行：python3 scripts/create_staging_db.py"
        )


# --------------------------------------------------------------------------
# 查询辅助（Web 层读工作库状态用）
# --------------------------------------------------------------------------


def query(
    sql: str, params: Sequence[Any] = (), db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    con = connect(readonly=True, db_path=db_path)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def query_one(
    sql: str, params: Sequence[Any] = (), db_path: str | Path | None = None
) -> dict[str, Any] | None:
    con = connect(readonly=True, db_path=db_path)
    try:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def scalar(
    sql: str,
    params: Sequence[Any] = (),
    default: Any = None,
    db_path: str | Path | None = None,
) -> Any:
    con = connect(readonly=True, db_path=db_path)
    try:
        row = con.execute(sql, params).fetchone()
        if row is None or row[0] is None:
            return default
        return row[0]
    finally:
        con.close()


def stats(db_path: str | Path | None = None) -> dict[str, Any]:
    """工作库各表行数，用于 Web 首页的状态条。"""
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()
    out: dict[str, Any] = {"path": str(path), "exists": path.exists(), "tables": {}}
    if not path.exists():
        return out
    for t in TABLES:
        try:
            out["tables"][t] = scalar(f"SELECT COUNT(*) FROM {t}", db_path=path, default=0)
        except sqlite3.OperationalError:
            out["tables"][t] = None  # 表缺失（库是旧版建的）
    return out
