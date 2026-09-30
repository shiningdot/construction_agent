"""只读数据访问层：展示层访问最终库的唯一入口。

设计要点（详见 docs/web/00_总体架构与数据层.md 第 3.2 节）：

1. 连接串用 `mode=ro`，由 SQLite 自身从物理层拒绝写操作
2. 再叠一层 `PRAGMA query_only = ON` 作双保险
3. 每请求短连接——SQLite 打开成本极低，换来无状态、无并发竞态

与之相对，**写操作一律不走本模块**：

- 工作库（采集/提取/审核的中间态）走 `staging_db.py`
- 最终库的并入走后续的 promote 模块（带 dry_run 与备份）

这条"展示层只有只读句柄"的边界，是蓝图 07 中 M0 验收第 3、5 条要专门测的。

库路径解析优先级：

1. 环境变量 `CAIEMS_DB` —— 显式指定，测试与部署用
2. `settings.DATABASE_URL` —— 兼容既有配置（config.py 目前未定义此属性，
   用 getattr 兜底；将来若真加了此配置也不会失效）
3. `settings.DATA_DIR / "caiems_v2.db"` —— 默认

注意：`config.py` 的 `load_dotenv(override=True)` 会覆盖 shell 环境变量，
但项目 `.env` 中不含 `CAIEMS_DB` 这个键，故第 1 条不受影响。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..config import settings

DB_FILENAME = "caiems_v2.db"
ENV_DB_PATH = "CAIEMS_DB"


class DatabaseNotFoundError(RuntimeError):
    """最终库文件不存在。附带已尝试的路径，便于定位配置问题。"""


def resolve_db_path() -> Path:
    """按优先级解析最终库路径。"""
    raw = os.environ.get(ENV_DB_PATH)
    if raw:
        return Path(raw).expanduser().resolve()

    url = getattr(settings, "DATABASE_URL", None)
    if url:
        # 兼容 "sqlite:///abs/path" 与裸路径两种写法
        cleaned = url.split("///", 1)[-1] if "///" in url else url
        if cleaned:
            return Path(cleaned).expanduser().resolve()

    return (settings.DATA_DIR / DB_FILENAME).resolve()


def connect_readonly(db_path: str | Path | None = None) -> sqlite3.Connection:
    """建立只读连接。任何写操作都会抛 sqlite3.OperationalError。"""
    path = Path(db_path).expanduser().resolve() if db_path else resolve_db_path()
    if not path.exists():
        raise DatabaseNotFoundError(
            f"最终库不存在：{path}\n"
            f"   可用环境变量 {ENV_DB_PATH} 指定，例如：\n"
            f"   {ENV_DB_PATH}=/home/suntianhao/construction_agent/data/{DB_FILENAME}"
        )
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


@contextmanager
def read_conn(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """只读连接的上下文管理器。展示层每请求调用一次。"""
    con = connect_readonly(db_path)
    try:
        yield con
    finally:
        con.close()


# --------------------------------------------------------------------------
# 查询辅助
# --------------------------------------------------------------------------


def query(
    sql: str, params: Sequence[Any] = (), db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """执行查询，返回字典列表。"""
    with read_conn(db_path) as con:
        return [dict(row) for row in con.execute(sql, params)]


def query_one(
    sql: str, params: Sequence[Any] = (), db_path: str | Path | None = None
) -> dict[str, Any] | None:
    """执行查询，返回首行或 None。"""
    with read_conn(db_path) as con:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row is not None else None


def scalar(
    sql: str,
    params: Sequence[Any] = (),
    default: Any = None,
    db_path: str | Path | None = None,
) -> Any:
    """执行查询，返回首行首列。用于 COUNT / SUM 这类聚合。"""
    with read_conn(db_path) as con:
        row = con.execute(sql, params).fetchone()
        if row is None or row[0] is None:
            return default
        return row[0]


# --------------------------------------------------------------------------
# schema 元信息
# --------------------------------------------------------------------------


def list_tables(db_path: str | Path | None = None) -> list[str]:
    """列出所有用户表（不含 sqlite 内部表）。"""
    return [
        r["name"]
        for r in query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name",
            db_path=db_path,
        )
    ]


def table_exists(table: str, db_path: str | Path | None = None) -> bool:
    return (
        query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
            db_path=db_path,
        )
        is not None
    )


def table_columns(table: str, db_path: str | Path | None = None) -> list[str]:
    """返回表的列名清单。

    这是 schema 的**真相来源**。注意 `scripts/create_db_v2.py` 的 SCHEMA 常量
    与真实库存在漂移（脚本注释 55 列 / SCHEMA 52 列 / 真实库 48 列），
    凡涉及列名与类型，一律以本函数返回的实际结构为准。
    """
    with read_conn(db_path) as con:
        return [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]


def list_indexes(table: str | None = None, db_path: str | Path | None = None) -> list[dict]:
    """列出索引。table 为 None 时返回全部。"""
    if table:
        rows = query(f"PRAGMA index_list({table})", db_path=db_path)
    else:
        rows = query(
            "SELECT name, tbl_name, sql FROM sqlite_master "
            "WHERE type='index' AND sql IS NOT NULL ORDER BY tbl_name, name",
            db_path=db_path,
        )
    return [dict(r) for r in rows]


def db_summary(db_path: str | Path | None = None) -> dict[str, Any]:
    """库级概览：路径、大小、表与行数。用于启动自检与「关于数据」页。"""
    path = Path(db_path).expanduser().resolve() if db_path else resolve_db_path()
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "tables": {},
    }
    if not path.exists():
        return info
    for t in list_tables(db_path=path):
        info["tables"][t] = scalar(f"SELECT COUNT(*) FROM {t}", db_path=path, default=0)
    return info
