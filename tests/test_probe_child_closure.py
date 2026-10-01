"""`_probe_child` 往盘上写的「闭环」这一格。

**不联网、不起子进程**：`registry.build` 换成假的，直接把假采集器交给
`_probe_child` 跑一遍，看它写出的 `<key>.closure` 是什么。

这一组守的是 **2026-10-01 第三十九轮**撞出来的第三例同族问题：`_closure_of`
返回 `None` 本来只说「列表 == 收录 + 审核 + 排除」这个等式凑不齐（这个源没记
那三个计数器），却被当成了「这个源什么都没有」——于是同一段代码里的
`_fetch_failed` 与 `_list_pages_ok` **也一起传不出去**。

后果很具体：`news_aggregator` 与 `open_gov` 这一轮都加了 `_fetch_failed`，
可它们都没有闭环那三个数，`_closure_of` 返回 `None`，`net_failed` 到不了父
进程，**表上还是「空」**——新加的计数器加了等于没加。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "check_collector_health", ROOT / "scripts" / "check_collector_health.py")
health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health)


class _NoClosureCollector:
    """只有网络层计数、没有闭环三计数器的采集器。

    `news_aggregator` 与 `open_gov` 都是这个形状：`_fetch_failed` 有，
    `_collected` / `_review` / `_filtered_out` 没有。
    """

    def __init__(self, fetch_failed: int = 3, pages_ok: int = 0):
        self._fetch_failed = fetch_failed
        self._list_pages_ok = pages_ok
        self._total_listed = 0

    def collect(self):
        return []


class _FullClosureCollector(_NoClosureCollector):
    """闭环三计数器齐全的采集器（TRS 族形状）。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._collected = 1
        self._review = 1
        self._filtered_title = 2
        self._filtered_classify = 0


def _run(monkeypatch, tmp_path, collector, key: str = "fake_src") -> dict | None:
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(health.registry, "build",
                        lambda k, data_dir=None: (collector, None))
    health._probe_child(key, work, 5, tmp_path / "tmp_root")
    closure_file = work / f"{key}.closure"
    if not closure_file.exists():
        return None
    return json.loads(closure_file.read_text(encoding="utf-8"))


def test_没有闭环计数器时网络失败数照样写出去(monkeypatch, tmp_path):
    """**这就是第三十九轮修的那一处。** 修前 `closure is None` 直接跳过写盘，
    `net_failed` 到不了父进程，表上那一栏显示「空」。"""
    got = _run(monkeypatch, tmp_path, _NoClosureCollector(fetch_failed=3))

    assert got is not None, "闭环算不出来，不代表这两个数不存在"
    assert got["net_failed"] == 3, "修前这里传不出去，表上是「空」"
    assert "listed" not in got, "不要假装闭环算得出来"


def test_没有闭环计数器时列表页数也照样写出去(monkeypatch, tmp_path):
    """`listed == 0` 时靠它把「解析空」与「压根没取到页面」分开。"""
    got = _run(monkeypatch, tmp_path, _NoClosureCollector(fetch_failed=0,
                                                          pages_ok=7))
    assert got["list_pages_ok"] == 7
    assert got["net_failed"] == 0


def test_闭环齐全时两个数一起带在闭环里(monkeypatch, tmp_path):
    """正例：闭环算得出来时，`net_failed` / `list_pages_ok` 搭在闭环里一起走。"""
    got = _run(monkeypatch, tmp_path, _FullClosureCollector(fetch_failed=0,
                                                            pages_ok=3))
    assert got["listed"] == 0
    assert got["collected"] == 1
    assert got["review"] == 1
    assert got["filtered"] == 2
    assert got["net_failed"] == 0
    assert got["list_pages_ok"] == 3
    assert got["early_stop"] is False


def test_两个数一个都没有时不写闭包文件(monkeypatch, tmp_path):
    """反例：连 `_fetch_failed` 都没有的源，不该凭空冒出一个 closure 文件——
    那会让父进程以为它记了网络层的事。"""
    class _Nothing:
        def collect(self):
            return []

    got = _run(monkeypatch, tmp_path, _Nothing())
    assert got is None


def test_计数器不是整数时当成没有(monkeypatch, tmp_path):
    """有的源把 `_fetch_failed` 写成别的东西（字符串、None）。

    `isinstance(v, int)` 这个判断是**故意的**：放过去的话，父进程 `if
    c.get("net_failed")` 会拿一个非数值去判真假，`"0"` 这种字符串恒真，
    判据就反了。
    """
    c = _NoClosureCollector()
    c._fetch_failed = "3"          # 字符串，不是计数
    c._list_pages_ok = None
    got = _run(monkeypatch, tmp_path, c)
    assert got is None
