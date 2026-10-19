"""`create_staging_db.py --info` 对「文件在、表不在」不能崩（2026-10-01 第二十五轮）。

**不联网，也不碰真库**：临时目录里造三种状态各跑一遍。

查出的问题：`print_info()` 只判了「文件存不存在」，然后就查
`SELECT value FROM staging_metadata`。对**文件存在但没建表**的库，这句抛
`sqlite3.OperationalError: no such table: staging_metadata`（实测复现，见
`test_空壳库上直接查元数据表会抛OperationalError`）——

    最需要看现状的时候，它给的是一段 traceback。

而这种状态**不罕见**，成因就在 `staging_db.connect()` 自己身上：写连接第一句是
`PRAGMA journal_mode = WAL`，**光打开就把库文件连同 `-wal`/`-shm` 建出来**，建表要
等 `init_db()`。任何「打开过、但没跑到建表那一步」的进程都会留下一具空壳——
实测主仓库 `data/caiems_staging.db` 现在就是这个状态（4,096 字节、表数 0）。

判据不在这里另写一套：复用 `staging_db.is_initialized()`（查 `sqlite_master` 里有没有
`raw_accidents`，对空壳返回 False）。全项目 `staging_metadata` 的读取点只有这一处，
另外两处使用点（`scripts/verify_m0.py`、`scripts/build_image_manifest.py`）本来就先判过。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "create_staging_db.py"

from construction_agent.web import staging_db as sdb  # noqa: E402


@pytest.fixture(scope="module")
def script():
    """按项目既有范式从路径加载脚本模块（它自己会往上插 `sys.path`）。"""
    spec = importlib.util.spec_from_file_location(
        "create_staging_db_under_test", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _hollow(path: Path) -> Path:
    """造一具空壳：**用写连接打开，但不建表**——就是真实成因那一步。"""
    con = sdb.connect(db_path=path)
    con.close()
    return path


# --------------------------------------------------------------------------
# A 组：成因证明——空壳不是凭空来的，`connect()` 光打开就会造出来
# --------------------------------------------------------------------------


def test_写连接光打开就会建出文件_而表还是没有(tmp_path):
    """这一条是整个 bug 的前提，所以要正面钉住，不能靠注释说。

    实测的因果链（三步，缺一不可）：

        1. `sqlite3.connect(path)`      → 创建一个 **0 字节**文件
        2. `PRAGMA journal_mode = WAL`  → 写入库头，文件变 **4,096 字节**
        3. `init_db()` 没跑             → 表永远没有

    主仓库 `data/caiems_staging.db` 那具空壳**正好 4,096 字节**，停在第 2 步。
    （旁文件 `-wal`/`-shm` 要等第一次**写入**才出现，正常关闭时又会被收掉；
    主仓库那个残留的 `-shm` 说明当时那个进程没走到关闭。）
    """
    p = tmp_path / "hollow.db"
    assert not p.exists()

    con = sdb.connect(db_path=p)   # 写连接：`connect()` 会 PRAGMA journal_mode=WAL
    try:
        assert p.exists(), "写连接打开不存在的工作库，SQLite 会创建文件"
        assert p.stat().st_size > 0, \
            "WAL 那句把库头写进去了——文件不再是 0 字节（实测 4096）"
    finally:
        con.close()

    assert sdb.is_initialized(p) is False, \
        "光打开不建表——所以「文件存在」绝不能当成「库可用」"


def test_空壳库上直接查元数据表会抛OperationalError(tmp_path):
    """改前 `print_info()` 干的就是这件事，把崩溃本身钉下来当回归依据。"""
    p = _hollow(tmp_path / "hollow.db")
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="staging_metadata"):
            con.execute(
                "SELECT value FROM staging_metadata WHERE key='schema_version'")
    finally:
        con.close()


# --------------------------------------------------------------------------
# B 组：三种状态各跑一遍 print_info
# --------------------------------------------------------------------------


def test_空壳库不再崩_而是说清楚它是什么(script, tmp_path, capsys):
    p = _hollow(tmp_path / "hollow.db")

    script.print_info(p)          # ← 改前这里抛 OperationalError

    out = capsys.readouterr().out
    assert "不是工作库" in out
    assert "raw_accidents" in out
    # 光说「有问题」不够，得给出可执行的处理办法
    assert "--drop" in out
    assert "Traceback" not in out


def test_不存在的库报尚未创建(script, tmp_path, capsys):
    script.print_info(tmp_path / "nope.db")
    out = capsys.readouterr().out
    assert "尚未创建" in out
    assert "不是工作库" not in out      # 两种状态要分得开，不能用一句话糊过去


def test_正常的库照常列出全部表与版本(script, tmp_path, capsys):
    p = tmp_path / "ok.db"
    sdb.init_db(p)

    script.print_info(p)

    out = capsys.readouterr().out
    assert "schema 版本" in out
    assert "staging_v" in out            # 版本号真的读出来了，不是「(未记录)」
    assert "(未记录)" not in out
    for t in ("raw_accidents", "raw_accident_assets", "staging_causes"):
        assert t in out
    assert "不是工作库" not in out


# --------------------------------------------------------------------------
# C 组：给的处理办法真的管用——照着做一遍，空壳变成正常库
# --------------------------------------------------------------------------


def test_空壳上跑一次建表就正常了(script, tmp_path, capsys):
    """提示里写的是「再跑一次本脚本即可建表，幂等」。这句得是真的。"""
    p = _hollow(tmp_path / "hollow.db")
    assert sdb.is_initialized(p) is False

    sdb.init_db(p)                        # 就是「不带参数再跑一次」做的事

    assert sdb.is_initialized(p) is True
    script.print_info(p)
    out = capsys.readouterr().out
    assert "不是工作库" not in out
    assert "schema 版本" in out


def test_空壳上建表不会动已有的表(tmp_path):
    """「幂等，不会动已有数据」也不能只是提示里的一句话。

    造一个「建过表、又被人补了张自定义表」的库，再 init_db 一次，那张表要还在。
    """
    p = tmp_path / "half.db"
    sdb.init_db(p)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE my_extra (x INTEGER)")
    con.execute("INSERT INTO my_extra VALUES (7)")
    con.commit()
    con.close()

    sdb.init_db(p)                        # 再建一次

    con = sqlite3.connect(p)
    try:
        assert con.execute("SELECT x FROM my_extra").fetchone()[0] == 7
    finally:
        con.close()


# --------------------------------------------------------------------------
# D 组：--info 这条命令行分支，端到端走一遍
# --------------------------------------------------------------------------


def test_main的info分支对空壳返回0而不是异常(script, tmp_path, monkeypatch, capsys):
    p = _hollow(tmp_path / "hollow.db")
    monkeypatch.setattr(sys, "argv", ["create_staging_db.py", "--db", str(p),
                                      "--info"])

    assert script.main() == 0             # 改前是未捕获的 OperationalError

    out = capsys.readouterr().out
    assert "不是工作库" in out
    assert not p.with_name(p.name + "-orig").exists()   # 只读，没动文件
