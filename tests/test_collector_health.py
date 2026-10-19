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


def test_正文中位没有样本时显示破折号不是零():
    """「一个样本都没量到」与「量到 0 字」必须分开——这是同一个 `0`。

    2026-10-05 全量重跑的表里，三个源的「正文中位」都是 `0`，而它们**各自的
    情况完全不同**：

    - `hub_em`  压根没连上（HTTP 412）——从没走到详情页
    - `jx_em`   列表页取到 1 页、解析出 0 条——没有条目可抓
    - `sc_em`   列表 4 条、闭环 ✓——4 条**全被初筛排除**，也没抓过详情页

    三家的 `rows` 都是空的，而 `_median([])` 返回 `0`，于是表格把它们印成了
    「量到 0 字」。三种情况要修的东西南辕北辙，读者却看到同一个数。

    真值 `0`（抓到了详情页、正文确实 0 字）**仍然要显示 `0`**——这一半与上面
    成对，没有它，修法会矫枉过正成「所有 0 都藏起来」，那是把真值也一起丢了。
    """
    assert health._median([]) is None
    assert health._median_cell([]) == "—"

    # 真值一侧：抓到了，正文就是 0 字 / 很短
    assert health._median_cell([0]) == "0"
    assert health._median_cell([0, 0, 120]) == "0"
    # 常规：中位取排序后 len//2，不是平均
    assert health._median_cell([700, 900]) == "900"
    assert health._median_cell([6335]) == "6335"


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


def test_列表页拿到了却解析不出要判成解析空():
    """**第三十八轮的活样本 `jx_em`。**

    TLS 修好之后它的 `net_failed = 0`、`listed = 0`，与「站点通了、栏目
    确实没内容」逐格相同；真相是列表页 200 / 271,906 字节、49 条全在
    JSON 接口里。修好一层只是把它从一种「看不出区别」挪到另一种。
    """
    jx = {"count": 0, "listed": 0,
          "closure": {"net_failed": 0, "list_pages_ok": 1}}
    assert health._status_of(jx) == "解析空"


def test_页面没回来时还是空():
    """页数为 0 与「真·空栏目」仍不可分——这一格没解决它，只缩小了它。"""
    res = {"count": 0, "listed": 0,
           "closure": {"net_failed": 0, "list_pages_ok": 0}}
    assert health._status_of(res) == "空"


def test_listed为None时不能落进解析空():
    """**反例**：判据若写成 `not listed`，`None` 也是真。

    `esafety` / `news_aggregator` / `open_gov` 这类源没有列表通路，`listed`
    是 `None`；有 `list_pages_ok` 的几个源里也保不齐某个是 `None`。写成
    `not listed` 会把它们一并报成「解析空」——凭空多出几个故障。
    """
    res = {"count": 0, "listed": None, "closure": {"list_pages_ok": 3}}
    assert health._status_of(res) == "空"


def test_没连上优先于解析空():
    """两个计数器同时有值时，网络层的问题更根本，先报 `没连上`。"""
    res = {"count": 0, "listed": 0,
           "closure": {"net_failed": 2, "list_pages_ok": 1}}
    assert health._status_of(res) == "没连上"


def test_有数据时新增的分支不干扰():
    res = {"count": 4, "listed": 40,
           "closure": {"net_failed": 0, "list_pages_ok": 2}}
    assert health._status_of(res) == "有数据"


def test_有源抓到数据就退0():
    assert health._exit_code([{"count": 0}, {"count": 3}], only=None) == 0


def test_全量探测全灭才退2():
    """这是为「跑全量却一个都没抓到」设计的：探测本身坏了的信号。"""
    assert health._exit_code([{"count": 0}, {"count": 0}], only=None) == 2


def test_限定探测全灭不退2():
    """**2026-10-01 实测撞出来的。**

    `--only jx_em,hub_em,jl_em,sc_em,esafety` 五个全灭 → 退 2 并在 stderr
    打印「探测本身出了问题」。而它其实是这次探测最成功的一次——五个源各自
    的三格判据全对。排查时挑的本来就是已知有问题的源，「0 个抓到」是预期结果。
    判据的取值域里混进了「不适用」，和「闭环」那一栏的老毛病同族。
    """
    assert health._exit_code([{"count": 0}], only="jx_em") == 0


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


# --------------------------------------------------------------------------
# _Sink：两条投递通路要合计数，不能各记一次
# --------------------------------------------------------------------------


def _feed_record(sink, url: str, text: str = "x" * 10) -> None:
    """走通路 A（`store.attach(sink)` → `RawAccidentStore.add()` 回调）。"""
    sink({"title": "t", "source_url": url, "content": text})


def _feed_item(sink, url: str, text: str = "x" * 10) -> None:
    """走通路 B（`for item in collector.collect(): sink.feed(item)`）。"""
    sink.feed(SimpleNamespace(title="t", source_url=url, raw_text=text))


def test_同一URL经两条通路只记一次(tmp_path):
    """`run_source` 对**同一个** sink 接了两条投递路径：

        通路 A：`store.attach(sink)` → `RawAccidentStore.add()` 里回调
        通路 B：`for item in collector.collect(): sink.feed(item)`

    而 TRS 族的 `collect()` **两条都走**（`raw_store.add_batch(...)` 与
    `return results`）。不去重就把同一批数据记两遍——实测 `jx_em`：
    `collect()` 返回 3 条，表上印 **6** 条（2026-10-05 §2.5.48）。

    去重口径与生产侧一致：`raw_accidents` 表有 `UNIQUE(url_hash)`，真写库
    那边本来就幂等，**只有这张纯内存的表会翻倍**。
    """
    sink = health._Sink(tmp_path / "sink.jsonl", limit=99)
    for u in ("https://x/1", "https://x/2"):
        _feed_record(sink, u)
    for u in ("https://x/1", "https://x/2"):
        _feed_item(sink, u)
    sink.close()

    assert sink.count == 2, f"同一批 URL 被记了 {sink.count} 次"
    lines = [ln for ln in (tmp_path / "sink.jsonl").read_text(
        encoding="utf-8").splitlines() if ln]
    assert len(lines) == 2, "落盘也要只有两行，否则「正文中位」也会被同一批数据加倍"


def test_两条通路来的正文不一样时保留先到的(tmp_path):
    """通路 A 的键是 `content`、通路 B 的是 `raw_text`，且**不保证一样**：
    `collect()` 返回的是 `RawAccidentText`，store 里那条可能已剥过模板行。
    去重保留先到的（通路 A），不合并、不覆盖——**合并是另一件事，没做**。
    """
    sink = health._Sink(tmp_path / "sink.jsonl", limit=99)
    _feed_record(sink, "https://x/1", text="A" * 30)
    _feed_item(sink, "https://x/1", text="B" * 5)
    sink.close()

    assert sink.count == 1
    only = (tmp_path / "sink.jsonl").read_text(encoding="utf-8").strip()
    assert '"chars": 30' in only, "保留的应当是通路 A 那条"


def test_URL为空时不去重(tmp_path):
    """没有键可用就退回原行为——**不猜**。两条空 URL 就是两条。"""
    sink = health._Sink(tmp_path / "sink.jsonl", limit=99)
    _feed_record(sink, "", text="x")
    _feed_record(sink, "", text="y")
    sink.close()

    assert sink.count == 2


def test_去重后条数不因通路数翻倍(tmp_path):
    """3 条走两条通路仍是 3——这是表上「条数」那一栏的字面判据。"""
    sink = health._Sink(tmp_path / "sink.jsonl", limit=99)
    urls = [f"https://x/{i}" for i in range(3)]
    for u in urls:
        _feed_record(sink, u)
    for u in urls:
        _feed_item(sink, u)
    sink.close()

    assert sink.count == 3

