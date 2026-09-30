"""数据库一致性备份。

## 为什么不能用 `shutil.copy2`

WAL 模式下新写入先落在 `-wal` 旁文件，主 `.db` 在 checkpoint 之前是**滞后**的。
`shutil.copy2` 只复制主文件，于是副本停在最后一次 checkpoint 的状态——静默丢数据。

实测（SQLite 3.46.1，写 51 行、连接仍开着、`-wal` 未 checkpoint）：

    journal_mode     backup API     shutil.copy2
    delete               51              51
    wal                  51               1      ← 丢 50 行

`delete` 模式下 `copy2` 恰好是对的，这正是它一直显得没问题的原因。而本项目
**建库时就开 WAL**（`scripts/create_db_v2.py:309`、`web/staging_db.py`），
`review/promote.py` 并入最终库时也会把最终库切成 WAL（WAL 是写进文件头的
持久属性）。所以这条路迟早会踩到：主库当前是 `delete`，跑一次并入就变 `wal`，
此后 `add_indexes.py` 那句 `copy2` 备份就不完整了。

## 为什么是 backup API 而不是 `VACUUM INTO`

`VACUUM INTO '<路径>'` 把路径当 SQL 字符串拼，路径含单引号就语法错误
（实测：`OperationalError: near "s": syntax error`）。备份路径由项目目录名和
用户名拼出来，不该赌它没有引号。backup API 走 Python 参数，没有这个问题，
也不要求目标文件不存在。

## 用法

    from construction_agent.database.backup import safe_backup
    safe_backup(db_path, db_path.parent / f"{db_path.name}.bak.{ts}")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class BackupError(RuntimeError):
    """备份未能完成（源缺失、目标已存在、或复制中途失败）。"""


def safe_backup(src: str | Path, dest: str | Path) -> Path:
    """把 `src` 备份到 `dest`，返回 `dest`。

    用 backup API 抓一致快照，所以 `src` 正被写入、或处于 WAL 未 checkpoint
    状态时也安全——两种情况 `shutil.copy2` 都会给出不完整副本。

    `dest` 必须不存在：宁可报错，也不静默覆盖一份已有的备份。中途失败会把
    半成品删掉——一份看起来像备份、实际不能用的东西，比没有备份更危险。
    """
    src, dest = Path(src), Path(dest)

    if not src.exists():
        raise BackupError(f"源库不存在：{src}")
    if dest.exists():
        raise BackupError(f"备份目标已存在，拒绝覆盖：{dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    # 用普通连接打开源：只读连接（`mode=ro`）在 WAL 库上需要能创建 `-shm`，
    # 某些情况下会直接失败。这里不执行任何 PRAGMA，所以不会改动源的 journal_mode。
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    return dest
