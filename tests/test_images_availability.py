"""`images.availability()` 的三态，以及它修掉的那个「一句话指错方向」的毛病。

2026-10-01 第四十轮实测：造五种工作库、各起一个干净子进程打真实请求，得到

  工作库文件                available()   真实处置
  不存在                    false         跑 create_staging_db.py
  0 字节（空壳）            false         同上——空壳文件骗过了 exists()
  12,288 B、0 张表          false         同上
  16,384 B、表在、0 行      false         **跑 build_image_manifest.py**
  16,384 B、表在、有行      true          正常

**四条来路报同一个 `false`**，而详情页只有两句文案（`accident.js`），于是
「工作库压根没建」被说成「图片索引未重建，需要先跑
`scripts/build_image_manifest.py`」。照做，脚本自己会退 3 说「工作库未初始化，
先跑 `create_staging_db.py`」（`build_image_manifest.py:451`）——用户要多跑
一步才知道真相，而真相本该写在那句话里。

概览页没这个毛病：`overview.js:642` 判 `if (img && img.available)`，为假时
整张卡不出现。它有退路（不渲染），详情页没有（那块面板的位置已经占了）。

与第三十九轮 `baidu_search.classify_response` 同一手法：**判据本身改成三态，
另留一个布尔便捷版给已写进契约的调用方**。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.web import images  # noqa: E402

ENV = "CAIEMS_STAGING_DB"

# 与工作库真实 schema 一致（`staging_schema.py:258`）。少一列 `stats()` 就会
# 在 `full` 场景上抛 sqlite3.Error，而被当成 unavailable——那样这条测试反而
# 会「通过」，测的却是别的东西。
_MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS image_manifest (
    accident_id TEXT, rel_path TEXT, filename TEXT, ext TEXT, bytes INTEGER,
    sha1 TEXT, source_url TEXT, source_page_url TEXT, width INTEGER,
    height INTEGER, version TEXT, analyzed INTEGER, manifest_at TEXT)
"""


def _staging(root: Path, kind: str) -> Path:
    """按场景造一个工作库文件。五种，见模块 docstring 的表。"""
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"staging_{kind}.db"
    if kind == "missing":
        return p                                  # 故意不创建
    if kind == "emptyfile":
        p.write_bytes(b"")                        # 0 字节：文件名骗过 exists()
        return p
    con = sqlite3.connect(p)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS raw_accidents (id TEXT PRIMARY KEY)")
        if kind in ("emptytable", "full"):
            con.execute(_MANIFEST_DDL)
        if kind == "full":
            con.execute(
                "INSERT INTO image_manifest "
                "(accident_id, rel_path, filename, ext, bytes, sha1, version, "
                " analyzed, manifest_at) "
                "VALUES ('a1','imageV3/a1/x.jpg','x.jpg','jpg',10,'deadbeef',"
                "'v3',0,'2026-10-01 00:00:00')")
        con.commit()
    finally:
        con.close()
    return p


def _measure(tmp_path, monkeypatch, kind: str) -> str:
    p = _staging(tmp_path, kind)
    monkeypatch.setenv(ENV, str(p))
    return images.availability()


# --------------------------------------------------------------------------
# A 组：三态判据本身
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind,expected", [
    ("missing", "unavailable"),      # 库文件不在
    ("emptyfile", "unavailable"),    # 0 字节空壳
    ("notables", "unavailable"),     # 库在、一张表都没有
    ("emptytable", "empty"),         # 库在、表在、一行没有 ← 全新环境的正常态
    ("full", "ready"),               # 正常
])
def test_五种工作库报三种状态(tmp_path, monkeypatch, kind, expected):
    assert _measure(tmp_path, monkeypatch, kind) == expected


def test_全新环境与工作库没建必须可区分(tmp_path, monkeypatch):
    """**这一条就是本轮修的东西。**

    `emptytable`（配好了，只是还没跑过清单）与 `notables`（工作库根本没建）
    在修前都是 `False`，详情页因此说同一句话。两者的下一步动作不同：
    前者跑 `build_image_manifest.py`，后者得先跑 `create_staging_db.py`。
    """
    fresh = _measure(tmp_path / "a", monkeypatch, "emptytable")
    broken = _measure(tmp_path / "b", monkeypatch, "notables")

    assert fresh == "empty"
    assert broken == "unavailable"
    assert fresh != broken, "「还没跑过清单」与「工作库没建」不能同形"


def test_零字节空壳不算empty(tmp_path, monkeypatch):
    """`exists()` 为真的空文件是最容易骗过判据的一种——它的名字证明不了任何事。

    这与 `staging_ready` / `staging_db.is_initialized` 的注释说的是同一件事
    （「文件名骗人，不拿文件在不在当判据」）。这里更细一层：连「有没有表」
    都不够，`empty` 这一档专指**表建好了、只是没数据**。
    """
    assert _measure(tmp_path, monkeypatch, "emptyfile") == "unavailable"


# --------------------------------------------------------------------------
# B 组：布尔版与三态版的关系
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["missing", "emptyfile", "notables",
                                  "emptytable", "full"])
def test_布尔版恰等于三态版取ready(tmp_path, monkeypatch, kind):
    p = _staging(tmp_path, kind)
    monkeypatch.setenv(ENV, str(p))
    assert images.available() is (images.availability() == "ready")


def test_布尔版把后两态压成同一个False(tmp_path, monkeypatch):
    """**这是已知的、故意的压缩，不是待修的 bug。**

    `images_available` 是蓝图 05/06/09 三份文档写死的对外契约，
    `check_api_contract` 还按 `v is False` 判它。改名字的代价大于收益，
    所以布尔版留下，需要区分的调用方走 `availability()`。
    """
    a = _staging(tmp_path / "a", "emptytable")
    monkeypatch.setenv(ENV, str(a))
    assert images.available() is False

    b = _staging(tmp_path / "b", "notables")
    monkeypatch.setenv(ENV, str(b))
    assert images.available() is False        # 与上一句同一个值，但来路不同


# --------------------------------------------------------------------------
# C 组：`stats()` 的 state 字段
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind,expected", [
    ("missing", "unavailable"),
    ("emptyfile", "unavailable"),
    ("notables", "unavailable"),
    ("emptytable", "empty"),
    ("full", "ready"),
])
def test_stats的state与availability同源(tmp_path, monkeypatch, kind, expected):
    p = _staging(tmp_path, kind)
    monkeypatch.setenv(ENV, str(p))
    assert images.stats()["state"] == expected


def test_stats的available契约没被改坏(tmp_path, monkeypatch):
    """`overview.js:642` 判 `if (img && img.available)` 决定整张卡出不出现。

    这条契约改语义会把概览页弄坏——所以加了 `state` 也不动 `available`。
    """
    for kind, want in [("missing", False), ("emptyfile", False),
                       ("notables", False), ("emptytable", False),
                       ("full", True)]:
        p = _staging(tmp_path / kind, kind)
        monkeypatch.setenv(ENV, str(p))
        s = images.stats()
        assert s["available"] is want, f"{kind} 场景的 available 变成了 {s['available']}"
        assert s["state"] == ("ready" if want else s["state"])


def test_stats在空态上仍然给零值而不是抛异常(tmp_path, monkeypatch):
    """零值本身是旧的对外形状，保留——区别只在 `state` 说得清是哪种空。"""
    p = _staging(tmp_path, "emptytable")
    monkeypatch.setenv(ENV, str(p))
    s = images.stats()

    assert s["files"] == 0 and s["coverage"] == 0.0
    assert s["generated_at"] is None
    assert s["state"] == "empty"
