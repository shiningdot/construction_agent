"""`scripts/check_collector_health.py` 里那几个判据的单元测试。

**不联网。** 测的是纯函数与 `_fetch` 的计数逻辑，不是一个源活没活着——
后者只有真发请求才知道，见 `tests/test_collector_parsing.py` 开头那段。

这一组测试存在的理由，是这一栏（「闭环」）刚加出来时踩过的两个坑：

1. `0 == 0 + 0 + 0`。任何完全抓不到的源都会得到 `✓`，恒真判据。
2. TRS 族的「排除」是 `_filtered_title` **加上** `_filtered_classify` 两段。
   只读前者，12 个源的差额会恰好等于初筛排除数，看起来像「丢了这么多条」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# `scripts/` 不是包，按路径加载。
_spec = importlib.util.spec_from_file_location(
    "check_collector_health", ROOT / "scripts" / "check_collector_health.py")
health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health)


# --------------------------------------------------------------------------
# _closure_of：两族命名，语义对齐后才能相加
# --------------------------------------------------------------------------


def test_TRS族的排除要把初筛和分类两段加起来():
    """只读 `_filtered_title` 会漏掉分类排除那一段。

    实测 `tj_em`：列表 15、初筛排除 12、分类排除 0、收录 3——
    这一次 `_filtered_classify` 恰好是 0，差额看不出来。换成
    `sx_em` 那种两段都有值的源，少的就正好是分类排除数。
    """
    c = SimpleNamespace(_total_listed=15, _collected=3, _review=1,
                        _filtered_title=8, _filtered_classify=3)
    got = health._closure_of(c)
    assert got == {"listed": 15, "collected": 3, "review": 1,
                   "filtered": 11, "sum": 15}


def test_独立族的排除就是一个_filtered_out():
    c = SimpleNamespace(_total_list=20, _collected=0, _review_count=2,
                        _filtered_out=18)
    assert health._closure_of(c) == {"listed": 20, "collected": 0, "review": 2,
                                     "filtered": 18, "sum": 20}


def test_beijing_em那种_total_records也认():
    c = SimpleNamespace(_total_records=84, _collected=30, _review_count=4,
                        _filtered_out=50)
    assert health._closure_of(c)["listed"] == 84


def test_读不齐就返回None而不是拿0凑数():
    """缺一样就返回 None。

    用 0 顶上，这一栏会显示一个看起来很正常的 `✓`——
    而真相是「这个源根本没记这个数」。
    """
    assert health._closure_of(None) is None
    # 有收录有审核，但没有列表长度（`mem` / `safehoo` 就是这样）
    assert health._closure_of(
        SimpleNamespace(_collected=3, _review_count=1, _filtered_out=2)) is None
    # 有列表有收录，但没有排除
    assert health._closure_of(
        SimpleNamespace(_total_list=5, _collected=1)) is None


# --------------------------------------------------------------------------
# _closure_cell：三种「不适用」不能长得像结论
# --------------------------------------------------------------------------


def test_列表为0时不给打勾():
    """`0 == 0 + 0 + 0` 是恒真的，恒真判据和恒假判据一样没用。"""
    res = {"closure": {"listed": 0, "collected": 0, "review": 0,
                       "filtered": 0, "sum": 0, "early_stop": False}}
    assert health._closure_cell(res) == "空表"


def test_早停不算判定():
    res = {"closure": {"listed": 94, "collected": 3, "review": 0,
                       "filtered": 0, "sum": 3, "early_stop": True}}
    assert health._closure_cell(res) == "早停"


def test_跑到底且对得上就是勾():
    res = {"closure": {"listed": 20, "collected": 0, "review": 2,
                       "filtered": 18, "sum": 20, "early_stop": False}}
    assert health._closure_cell(res) == "✓"


def test_跑到底对不上要报出差额():
    """这是这一栏存在的理由：进了循环却哪个去向都没落到。"""
    res = {"closure": {"listed": 20, "collected": 0, "review": 2,
                       "filtered": 15, "sum": 17, "early_stop": False}}
    assert health._closure_cell(res) == "差 3"


def test_没有这个判据显示破折号():
    assert health._closure_cell({"closure": None}) == "—"
    # 从旧版 JSON 报告里读出来的、只有 early_stop 没有 sum 的残件
    assert health._closure_cell({"closure": {"early_stop": True}}) == "—"


# --------------------------------------------------------------------------
# _status_of / _count_cell
# --------------------------------------------------------------------------


def test_没连上和真的空要分开():
    """`jl_em`(DNS) / `jx_em`(SSL) / `hub_em`(412) 三种故障在旧表上逐格相同。

    它们和「站点通了、栏目确实没内容」在 `_total_listed` 上都是 0。
    """
    empty = {"count": 0, "closure": {"net_failed": 0}}
    down = {"count": 0, "closure": {"net_failed": 3}}
    assert health._status_of(empty) == "空"
    assert health._status_of(down) == "没连上"
    # 没有 closure（`esafety` 那类）时退回「空」，不猜
    assert health._status_of({"count": 0, "closure": None}) == "空"
    # 有数据时 net_failed 不影响结论
    assert health._status_of({"count": 5, "closure": {"net_failed": 1}}) == "有数据"


def test_条数带星号表示被早停截断():
    """`cq_em` 的 13 和 `sh_em` 的 3 直接比是错的——后者列表 94 条，只是停在了 3。"""
    assert health._count_cell({"count": 13, "note": "通路A+通路B"}) == "13"
    assert health._count_cell({"count": 3, "note": "通路A+早停"}) == "3*"


# --------------------------------------------------------------------------
# TRS 基类的 _fetch_failed
# --------------------------------------------------------------------------


def _bare_trs(max_retries: int = 2):
    """绕开 `__init__` 造一个只够跑 `_fetch` 的 TRS 采集器。

    真构造要配置文件（`self.cfg` 从 JSON 读），与这个测试要验的东西无关——
    这里只关心「重试耗尽」与「404」这两条返回 `None` 的路径分不分得开。
    """
    from construction_agent.collector.sources.trs_cms_base import TrsCmsCollector
    c = object.__new__(TrsCmsCollector)
    c._page_cache = {}
    c._fetch_failed = 0
    c.cfg = SimpleNamespace(max_retries=max_retries)
    return c


def test_重试耗尽要计数(monkeypatch):
    c = _bare_trs()

    def boom(url):
        raise RuntimeError("Name or service not known")

    c._client = SimpleNamespace(get=boom)
    monkeypatch.setattr(health.time, "sleep", lambda *_: None)

    assert c._fetch("https://example.invalid/list") is None
    assert c._fetch_failed == 1


def test_404不算网络失败():
    """`404` 与「没连上」都会返回 `None`，但一个是正常结果、一个是故障。

    混在一起数，判据两头都真：一个把旧文章删干净了的站点会被报成「不通」。
    """
    c = _bare_trs()
    c._client = SimpleNamespace(get=lambda url: httpx.Response(404))

    assert c._fetch("https://example.invalid/gone") is None
    assert c._fetch_failed == 0


def test_缓存命中不计数也不发请求():
    c = _bare_trs()
    c._client = SimpleNamespace(
        get=lambda url: pytest.fail("缓存命中时不该发请求"))
    c._page_cache["https://example.invalid/list"] = "<html>列表</html>"

    assert c._fetch("https://example.invalid/list") == "<html>列表</html>"
    assert c._fetch_failed == 0
