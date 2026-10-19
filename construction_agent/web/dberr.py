"""数据库异常 → 结构化错误体。**全项目唯一出口。**

本项目有两个 SQLite 库：

| | 路径 | 谁在读 | 建库脚本 |
|---|---|---|---|
| 最终库 | `data/caiems_v2.db` | 展示层（只读） | `scripts/create_db_v2.py` |
| 工作库 | `data/caiems_staging.db` | 采集/提取/审核（读写） | `scripts/create_staging_db.py` |

两个库的路径不同、建库脚本不同、读写权限不同，但**「异常说的是哪一件事」是同一件事**。
所以分类只有一份（`classify`），只有 `hint` 按 `kind` 分岔——因为 hint 要指向
**下一步动作**，而两个库的下一步动作不同（跑哪个建库脚本）。

这里的目标是「同一件事写一遍」。2026-10-01 第四十一轮修最终库那一层时，
工作库侧已经有三份逐字相同的 `_unavailable()` 与三份逐字相同的 `_has_table()`
分散在 `collect.py` / `extract.py` / `review.py` 里——本轮把它们收敛到
`staging_db`，而**分类这件事**收敛到这里，两个库共用。

分类原则（第四十一轮确立，第四十二轮扩到工作库）：

> **按下一步动作分档，不按异常类型分档，也不按 SQLite 的报错文案分档。**

「缺表」与「缺列」合成同一个 `schema_mismatch` 是**故意的**：SQLite 说的是两句
不同的话（`no such table: x` / `no such column: x`），但下一步动作相同（跑建库
脚本）。而 `not_a_database` 必须单独一档——它是**路径指错了**，跑建库脚本会把错误
固化下来（在一个装错的文件上建我们的表）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

__all__ = [
    "ERROR_CODES",
    "DBUnavailable",
    "NotFound",
    "classify",
    "diagnose",
]

#: 四个码，按「下一步动作」分。顺序即严重度：越靠前越可能是配置问题。
ERROR_CODES = (
    "database_not_found",   # 文件不在          → 指对路径
    "not_a_database",       # 路径指到别的文件   → 指对路径
    "schema_mismatch",      # 表缺 / 列缺 / 空壳 → 跑建库脚本
    "database_error",       # 兜底（文件损坏等） → 备份 + integrity_check
)


class DBUnavailable(RuntimeError):
    """两个库共用的「库不可用」基类。

    定义在这里而不是各自的模块里，是为了让 `classify()` 能**只认一个类型**
    就覆盖两个库——`db.DatabaseNotFoundError` 与
    `staging_db.StagingDBNotInitialized` 都继承它。

    **不能靠 `isinstance(exc, RuntimeError)` 来判**：那会把程序里任何一个
    笔误导致的 `RuntimeError` 都说成「库文件不存在」。
    """


class NotFound(DBUnavailable):
    """库文件不存在。两个库各自再包一层（附上各自的提示）。"""


#: `sqlite3.Error` 的消息里，「这个文件不是 SQLite 库」的说法（版本不同措辞不同）。
_NOT_A_DB = ("file is not a database", "file is encrypted")

_NO_SUCH_TABLE = re.compile(r"no such table:\s*([A-Za-z_][A-Za-z0-9_]*)")
#: 表在、列不在。**与缺表同一档**——下一步动作相同（跑建库脚本，或换个库）。
#: 实测（第四十一轮）它不罕见：「建了一张表、列只建了一半」的库查 `deaths`
#: 就报这个，而 SQLite 说的是 `no such column: deaths`，与「缺表」是两句不同的话。
_NO_SUCH_COLUMN = re.compile(r"no such column:\s*([A-Za-z_][A-Za-z0-9_.]*)")

#: `sqlite3.connect(uri=True, mode=ro)` 在文件不存在时报的话。SQLite 是惰性的，
#: 这一句只在**打开时**出现，所以它确实是「文件不在」，不是「表不在」。
_UNABLE_TO_OPEN = "unable to open database file"


def classify(exc: BaseException) -> str:
    """异常 → 四个码之一。**纯分类**：不看路径、不管下一步动作。

    分成两层（分类 / 提示）的理由：分类是**客观的**（SQLite 说了什么就是什么），
    提示是**主观的**（取决于这是最终库还是工作库）。混在一起就会出现「同一句
    报错在两个库里给出同一条指错方向的建议」——那正是第四十一轮 health 报
    「缺 3 个索引」的成因。
    """
    if isinstance(exc, DBUnavailable):
        return "database_not_found"

    msg = str(exc)
    low = msg.lower()

    if any(k in low for k in _NOT_A_DB):
        return "not_a_database"

    if _UNABLE_TO_OPEN in low:
        # 只读连接的打开阶段。走到这里说明文件确实不在（`connect_readonly`
        # 的前置 `path.exists()` 已经挡了一层，但工作库侧没有前置检查）。
        return "database_not_found"

    if _NO_SUCH_TABLE.search(msg) or _NO_SUCH_COLUMN.search(msg):
        return "schema_mismatch"

    return "database_error"


def _detail(exc: BaseException) -> str:
    """从异常里取出「缺的是哪张表 / 哪一列」，取不到返回空串。

    在**原大小写**的消息上搜，不用 `low`——在小写串上搜会把表名也降级成
    小写，报出来的名字与库里的对不上。
    """
    m = _NO_SUCH_TABLE.search(str(exc))
    if m:
        return m.group(1)
    m = _NO_SUCH_COLUMN.search(str(exc))
    return m.group(1) if m else ""


#: `kind` → 下一步动作。两个库各自该跑哪个脚本。
_BUILD_SCRIPT = {
    "final": "scripts/create_db_v2.py",
    "staging": "scripts/create_staging_db.py",
}

#: `kind` → 该库的中文名（消息里用）
_LABEL = {"final": "最终库", "staging": "工作库"}

#: `kind` → 指定库路径的环境变量名
_ENV = {"final": "CAIEMS_DB", "staging": "CAIEMS_STAGING_DB"}

#: `kind` → 默认库文件名（hint 里给一个能照抄的例子）
_FILENAME = {"final": "caiems_v2.db", "staging": "caiems_staging.db"}


def _hint(code: str, kind: str, path: Path, what: str) -> str:
    """`hint` 要指向**下一步动作**——所以它必须区分「缺表」与「缺列」。

    两者的下一步动作**不同**，尽管它们同属 `schema_mismatch` 一档：

    - **缺表** → 跑建库脚本能修。两个脚本都是 `CREATE TABLE IF NOT EXISTS`，
      只会跳过已存在的表、把缺的补上
    - **缺列** → 跑建库脚本**修不了**。表名已经在 `sqlite_master` 里，脚本一句
      `IF NOT EXISTS` 就跳过去了，跑完还是 `no such column`（第四十二轮实测
      确认）。必须先把这个文件移走或换个路径

    第四十一轮的 final 侧漏了这一层：缺列的 hint 写的是「跑
    `create_db_v2.py` 重建」，而那个脚本对这个文件什么都不做——**说错方向的
    提示比没有提示更费事**，同一轮在 `health` 上刚犯过一次。
    """
    env = _ENV[kind]
    script = _BUILD_SCRIPT[kind]
    label = _LABEL[kind]

    if code == "database_not_found":
        # 同一档在两种库上的**下一步动作不同**，所以按 kind 分岔。
        #
        # 工作库本来就是空的，「文件不在」跑建库脚本是对的（脚本是
        # `CREATE TABLE IF NOT EXISTS`，幂等、只补缺的表）。这一格 2026-10-05
        # 之前漏了——hint 只说「用环境变量指定库文件」，把第一次打开页面的
        # 用户引去设一个他并不需要设的环境变量，**而真正的下一步是建库**。
        # 实现漏了这条，本文件开头对它推理的注释却写着「『文件不在』与
        # 『零张表』都能靠 `create_staging_db.py` 修」——注释说了、代码没做。
        #
        # 主库（final）**不能**这么说：它是数据资产，不存在说明环境指错了，
        # 引用户去 `create_db_v2.py` 只会建出一个空的主库，比不提示更费事。
        if kind == "staging":
            return (f"工作库还没建：跑 {script} 建表（幂等，可反复跑）。"
                    f"若库文件在别处，用环境变量 {env} 指定")
        return (f"用环境变量 {env} 指定库文件，例如 "
                f"{env}=/home/suntianhao/construction_agent/data/{_FILENAME[kind]}")

    if code == "not_a_database":
        return (f"路径多半指错了（指向了别的文件或半截下载）。"
                f"用 {env} 重新指定")

    if code == "schema_mismatch":
        if what == "column":
            return (f"这个文件有表名但没有完整列，多半是别的程序建的同名表，"
                    f"或建库脚本跑了一半。跑 {script} **修不了已存在的表**"
                    f"（它用的是 CREATE TABLE IF NOT EXISTS）——"
                    f"先把它移走或换个路径，再跑 {script} 重建")
        return (f"这是一个还没有建表的空库（0 字节文件也走到这里）。"
                f"跑 {script} 建表；若路径本就是空的，改用正确的库文件")

    return (f"{label}文件可能存在但已损坏。先备份，再用 sqlite3 的 "
            f"integrity_check 查")


def describe(
    code: str,
    *,
    kind: str,
    path: str | Path,
    what: str = "",
    detail: str = "",
    origin: str = "",
) -> dict[str, str]:
    """从**已经分好的类**构造 `{error, message, hint}`。

    与 `diagnose()` 分开的理由：`diagnose()` 的输入是一个异常，而这个世上
    还有「不是异常、但同样是不可用」的情况——**主动探测**就是。工作库那边
    要问「现在是哪种不可用」，问的时候手里没有异常（因为还没跑查询），只有
    一个文件路径。硬要它去伪造一个异常再交给 `diagnose()`，等于承认「只有
    异常能表达不可用」，那是错的。

    `what` 取 `"table"` / `"column"` / `""`；`detail` 是表名或列名；`origin`
    只在 `code == "database_error"` 时用（原始异常的类型名与消息）。
    """
    if kind not in _BUILD_SCRIPT:
        raise ValueError(f"kind 只能是 'final' 或 'staging'，收到 {kind!r}")
    if code not in ERROR_CODES:
        raise ValueError(f"未知的 error 码：{code!r}")

    p = Path(path).expanduser().resolve() if path else Path("<未知>")
    label = _LABEL[kind]

    if code == "database_not_found":
        message = f"{label}不存在：{p}"
    elif code == "not_a_database":
        message = f"这不是一个 SQLite 数据库文件：{p}"
    elif what == "table":
        message = f"{label}里没有 `{detail}` 表：{p}" if detail else \
                  f"{label}的表名不全：{p}"
    elif what == "column":
        message = f"{label}的表结构不完整：缺 `{detail}` 列（{p}）" if detail else \
                  f"{label}的表结构不完整：{p}"
    elif code == "schema_mismatch":
        message = f"{label}的表结构不完整：{p}"
    else:
        message = f"读取{label}失败{origin and f'（{origin}）'}：{p}"

    return {"error": code, "message": message, "hint": _hint(code, kind, p, what)}


def diagnose(
    exc: BaseException,
    db_path: str | Path | None = None,
    *,
    kind: str = "final",
) -> dict[str, str]:
    """异常 → `{error, message, hint}`。`hint` 指向**下一步动作**。

    `kind` 取 `"final"`（最终库）或 `"staging"`（工作库），只影响 `hint` 与
    message 里的库名——**分类不受它影响**。

    第四十一轮实测（最终库五种形态 × 28 端点）：`hint` 说错方向比没有 hint
    更费事——用户照做会多跑一步才知道真相（空壳库报「缺 3 个索引」，把人引去
    跑 `add_indexes.py`）。第四十二轮在工作库侧撞见同一件事：非 SQLite 文件与
    「文件不存在」在对外表现上逐字相同，都说「工作库未建，请先运行
    `create_staging_db.py`」。
    """
    path = Path(db_path).expanduser().resolve() if db_path else Path("<未知>")
    code = classify(exc)
    msg = str(exc)

    # 「缺表」与「缺列」在**码**上合成一个（动的是同一个文件），但在 message
    # 与 hint 里必须分开说——两者的下一步动作其实不同（见 `_hint`）。
    what = ""
    if code == "schema_mismatch":
        what = ("table" if _NO_SUCH_TABLE.search(msg)
                else "column" if _NO_SUCH_COLUMN.search(msg)
                else "")

    return describe(code, kind=kind, path=path, what=what,
                    detail=_detail(exc),
                    origin=f"{type(exc).__name__}: {exc}" if code == "database_error" else "")
