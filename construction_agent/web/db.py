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
from . import dberr

DB_FILENAME = "caiems_v2.db"
ENV_DB_PATH = "CAIEMS_DB"

#: 展示层赖以工作的表。缺任何一张，页面就不是「少点数据」而是直接崩。
#:
#: 只列**内容表**：`pipeline_metadata` 缺了不崩（`extract.py` 读它时有 try
#: 兜底），`source_urls` / `search_logs` / `processing_log` / `image_files` /
#: `image_analyses` 这些展示层一次都没查过（`image_files` 被 `images.py`
#: 显式弃用，改读工作库）。判据要回答的是「这个文件是不是本项目那个最终库」，
#: **不是「它完不完整」**——把九张全列上，一座刚 `create_db_v2.py` 建好、
#: 还没导入数据的库会被判成坏库。
REQUIRED_TABLES: frozenset[str] = frozenset(
    {"accidents", "caics_causes", "enum_definitions"}
)

def missing_tables(db_path: str | Path | None = None) -> set[str]:
    """必需表里缺了哪几张。

    库**读不动**时让异常照常抛出，不吞——调用方要能分开处理「缺表」（可修，
    下一步是建表）与「读不动」（路径指错了）。这两件事的下一步动作不同，
    合成一个返回值就回到了「一个比特要说两件事」。
    """
    return set(REQUIRED_TABLES) - set(list_tables(db_path))


def diagnose(exc: BaseException, db_path: str | Path | None = None) -> dict[str, str]:
    """把一个数据库异常翻译成「哪一种不可用 + 下一步做什么」。

    分类与提示的**唯一出口**在 `dberr.py`——工作库那边要的是同一套分类、
    不同的下一步动作，所以分类只有一份，这里只负责补上最终库的路径与其
    `hint` 方向（`kind="final"`）。**本函数是薄转发，不要在这里加分支**：
    加在这里的分支，工作库那份就落下。

    2026-10-01 第四十一轮实测：造七种最终库、各起干净子进程打 28 个端点，
    「库不可用」这一个状态在改前给出了这几种对外表现：

      最终库文件                 改前                  现在           下一步
      不存在                     503 ✓                 not_found      指对路径
      0 字节空壳                 10 个端点 500 崩      schema_mismatch 建表
      合法 SQLite、0 张表        10 个端点 500 崩      schema_mismatch 建表
      非 SQLite 内容的文件        10 个端点 500 崩      not_a_database  指对路径
      只建了一张表               10 个端点 500 崩      schema_mismatch 建表
      表在、列不全               10 个端点 500 崩      schema_mismatch 移走重建
      表齐全、0 行               200「0 条」✓          （不归这里）     正常态
      正常库                     200 ✓                 （不归这里）     正常态

    崩的那五种来路共一个成因：`connect_readonly` 的前置检查是 `path.exists()`
    ——**文件存在不等于库可用**（0 字节文件是一个合法存在的文件），而空壳库
    要到第一条 SQL 才报 `no such table`，那时已经没有 handler 接了。这与
    第四十轮工作库那处的 `available()` 是**同一个来路**，只是换了一层。

    「表齐全、0 行」**不是故障**：那是刚建完库还没导入数据的正常状态，与第四十轮
    的 `empty` 同档，照常返回 0 是对的。

    **第四十二轮修正一处**：这张表里「表在、列不全」那一行的下一步，第四十一轮
    写的是「建表」，与另两行并列——**错了**。`create_db_v2.py` 用的是
    `CREATE TABLE IF NOT EXISTS`，对这个文件什么都不做，跑完还是 `no such
    column`。缺列与缺表同属一个**码**（动的是同一个文件），但**下一步动作不同**，
    已改成「移走重建」。
    """
    path = Path(db_path).expanduser().resolve() if db_path else resolve_db_path()
    return dberr.diagnose(exc, path, kind="final")


class DatabaseNotFoundError(dberr.NotFound):
    """最终库文件不存在。

    继承 `dberr.NotFound`（→ `dberr.DBUnavailable`）而不是直接 `RuntimeError`：
    `dberr.classify()` 靠这个基类认「文件不在」这一档，**不能靠
    `isinstance(exc, RuntimeError)`**——那会把程序里任何一个笔误导致的
    `RuntimeError` 都说成「库文件不存在」。工作库的
    `staging_db.StagingDBNotInitialized` 继承同一个基类。
    """


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


def db_fingerprint(db_path: str | Path | None = None) -> tuple[str, int, int]:
    """库文件指纹 ``(路径, mtime_ns, 大小)``。

    聚合层（province / overview）用它做缓存失效键：库没变就复用上次结果，
    库一更新（补跑采集、并入暂存数据）立即失效。文件不存在时返回零值指纹，
    让调用方照常走一次查询并在那里抛出 `DatabaseNotFoundError`。
    """
    path = Path(db_path).expanduser().resolve() if db_path else resolve_db_path()
    try:
        st = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), st.st_mtime_ns, st.st_size)


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
