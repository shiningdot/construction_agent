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
from . import dberr
from .staging_schema import SCHEMA_SQL, SCHEMA_VERSION, TABLES

STAGING_DB_FILENAME = "caiems_staging.db"
ENV_STAGING_DB = "CAIEMS_STAGING_DB"


class StagingDBNotInitialized(dberr.NotFound):
    """工作库文件不存在（尚未建库）。

    继承 `dberr.NotFound`（→ `dberr.DBUnavailable` → `RuntimeError`）而不是直接
    `RuntimeError`：`dberr.classify()` 靠这个基类认「文件不在」这一档，
    **不能靠 `isinstance(exc, RuntimeError)`**——那会把程序里任何一个笔误导致的
    `RuntimeError` 都说成「库文件不存在」。最终库侧的
    `db.DatabaseNotFoundError` 继承同一个基类。

    仍然是 `RuntimeError` 的子类，所以既有的 `except RuntimeError` 不受影响。
    """


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


#: 判断「这个文件是不是本项目的工作库」用的**最小表集**。
#:
#: 同最终库侧 `db.REQUIRED_TABLES` 的理由：判据要回答的是「这是不是我们的库」，
#: **不是「它完不完整」**。工作库有 11 张表，把 11 张全列上，一座刚
#: `create_staging_db.py` 建好、还没跑过采集的库会被判成坏库。
#:
#: 三张的选法：`raw_accidents` 是采集的产出（一切从这里开始），
#: `staging_accidents` 是提取的产出，`collect_sources` 是源登记。
#: 其余（资产、任务、promote 日志、图片清单）缺了只会让对应端点降级。
CORE_TABLES: frozenset[str] = frozenset(
    {"raw_accidents", "staging_accidents", "collect_sources"}
)


def list_tables(db_path: str | Path | None = None) -> list[str]:
    """列出工作库里所有用户表（不含 sqlite 内部表）。

    库读不动时**让异常抛出**，不吞——理由同 `db.list_tables`：调用方要能分开
    处理「表不在」与「读不动」。
    """
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        con.close()


def has_table(name: str, db_path: str | Path | None = None, *,
              strict: bool = True) -> bool:
    """表名是否登记在 `sqlite_master` 里。

    **判据是「表名在不在」，不是「这张表能不能用」。** 表在、列不全的库它照样
    返回 True，查询才撞 `no such column`——那一路由 blueprint 的 errorhandler
    接住并分档（第四十二轮实测：`partial` 形态下 4 个端点曾直接崩成 500）。

    `strict=True`（默认）：**文件在、但读不动**（不是 SQLite 库、头损坏）时让
    `sqlite3.Error` 抛出，由 blueprint 的 errorhandler 分档成 503。三处原有实现
    都是 `except Exception: return False`，于是「非 SQLite 文件」与「工作库没建」
    在对外表现上**逐字相同**——都说「工作库未建，请先运行 create_staging_db.py」，
    而前者照做没有用（那个文件压根不是我们的库，跑脚本只会把我们的表建到
    一个装错的文件上）。

    **文件不存在一律返回 False，不抛**——它不是「读不动」，是闸门本来就要正常
    处理的一种情况：返回 `available: false` + 建库提示（200），前端据此提示用户
    去建库。这两个分支必须分开：把它们合起来，要么「库没建」被错报成 503，
    要么「库指错了」被错报成「没建」。

    `strict=False`：读不动也当没有。**只给「附带的、可选的信息」用**（例如源清单
    里那一栏健康度）——那类信息缺了不致命，不该把整个端点变成 503。
    """
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()
    if not path.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            hit = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                (name,)).fetchone()
            return hit is not None
        finally:
            con.close()
    except sqlite3.Error:
        if strict:
            raise
        return False


def probe(db_path: str | Path | None = None, *, missing: str = "") -> dict[str, str]:
    """工作库现在处于**哪一种**不可用？返回 `{error, message, hint}`。

    `missing` 是调用方**已经判出来**的那张缺失的表名（闸门查的是自己那张表，
    不是 `CORE_TABLES`）。传进来有两个好处：一是不必为了探测再列一遍全部表名，
    二是 message 能点名是哪张表——`CORE_TABLES` 齐全但缺 `collect_tasks` 时，
    不传 `missing` 会让探测以为「表名都齐、只可能是列不全」，于是给出「先移走
    再重建」的建议，而其实跑一次建库脚本就能把这张表补上。**指错方向比不指
    更费事**——第四十一轮已经在 `health` 上犯过一次。

    **只在出错路径上调用，正常路径零成本**——与第四十一轮对最终库的裁决同一条：
    探测放在出错时判别，不放在每次连接（实测连接 + 查三张表名 140.5 µs，是仅
    连接的 6.7 倍，比列全部表名还贵）。

    分档依据是**下一步动作**：「文件不在」与「零张表」都能靠
    `create_staging_db.py` 修（脚本是 `CREATE TABLE IF NOT EXISTS`，只跳过已有
    的表），而同属 `schema_mismatch` 的「表在、列不全」**修不了**——表名已经在
    `sqlite_master` 里，脚本一句 `IF NOT EXISTS` 就过去了。所以后者必须说
    「先移走再重建」，前者不能这么说。
    """
    path = Path(db_path).expanduser().resolve() if db_path else resolve_staging_path()

    if not path.exists():
        return dberr.describe("database_not_found", kind="staging", path=path)

    if missing:
        # 调用方手里的判据比这里准（它查的是自己那张表）。既然已经知道缺哪张，
        # 就不必再列一遍全部表名——那一次查询纯粹是浪费。
        return dberr.describe("schema_mismatch", kind="staging", path=path,
                              what="table", detail=missing)

    try:
        present = set(list_tables(path))
    except sqlite3.Error as exc:
        # 文件在、但读不动：不是 SQLite 库，或头损坏。SQLite 是惰性的，
        # 连接时它不报错，要等第一条 SQL。
        return dberr.diagnose(exc, path, kind="staging")

    missing = CORE_TABLES - present
    if missing:
        return dberr.describe("schema_mismatch", kind="staging", path=path,
                              what="table", detail=sorted(missing)[0])

    # 表名齐全、却仍然走到探测这一步 —— 只可能是列不全（表名在、列不在）。
    # 不再多查一次列：调用方正是因为撞了 `no such column` 才来问的。
    return dberr.describe("schema_mismatch", kind="staging", path=path,
                          what="column")


def unavailable_payload(data: Any = None, *, missing: str = "") -> dict[str, Any]:
    """工作库不可用时的**统一响应体**。

    收敛前这三份逐字相同、各写在 `collect.py` / `extract.py` / `review.py` 里：

        return {"ok": True, "available": False, "hint": BUILD_HINT, "data": []}

    改后多带 `error` / `message` 两键，`hint` 由 `probe()` 按来路给——**不再是
    一句写死的「工作库未建」**。多出来的键对前端是兼容的：它只看
    `available` 与 `hint`，多两个键不会认不出。

    `missing` 透传给 `probe()`，见那里对「指错方向」的说明。
    """
    return {
        "ok": True,
        "available": False,
        "data": [] if data is None else data,
        **probe(missing=missing),
    }


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
