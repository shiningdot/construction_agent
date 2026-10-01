"""`NewsAggregator` 的两条断路，以及它们各自的计数器。

**不联网、不真调百度**：`BaiduSearchEngine` 换成假的，`_download_article`
按需替换。

2026-10-01 第三十九轮实测：这个源在健康检查表上长期是「空 / 0 条 / 列表 —
/ 闭环 —」，**内部状态一格都看不见**。查下去发现是两条**各自独立**的断路，
修前在整条链路上与「这个源今天真的没内容」完全同形：

1. **百度风控。** 同一 IP 连打 17 个查询，只有第 1 个拿到真结果页，其余全是
   1,438 字节的 `百度安全验证` 拦截页（HTTP 200、无 `content_left`）。修前
   `search()` 返回空列表、调用方 `if not search_results: continue`，**日志里
   只有一条 INFO `获到 0 条结果`**——与「17 个查询真的都没结果」一字不差。
2. **域名白名单。** 即便风控放行，搜索结果 href 也全是
   `http://www.baidu.com/link?url=…`，域名 `www.baidu.com` 不在 19 个白名单
   域名里（`_is_allowed_domain` 全部拒掉）。修前这一层**一条日志都没有**，
   只是一个光秃秃的 `continue`。实测 5 个样本：2 个百度跳转链接全拦、
   3 个白名单域名全放。

本轮**只让这两件事看得见，不修它们**——第 1 条是百度对这个 IP 的策略，
不是代码能绕的；第 2 条要跟着重定向才能拿到真实域名，而重定向在风控之后
才谈得上。两条都交作者裁决。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.base import RawAccidentText  # noqa: E402
from construction_agent.collector.sources import news_aggregator  # noqa: E402
from construction_agent.collector.sources.news_aggregator import (  # noqa: E402
    NewsAggregator)
from construction_agent.search.baidu_search import SearchResult  # noqa: E402


class _FakeSearcher:
    """假的 `BaiduSearchEngine`：既给结果，也模拟风控计数。

    `blocked_per_query > 0` 时，每次 `search()` 都当成「这次撞上了拦截页」：
    返回空列表、`blocked_pages` 加一——**这正是真引擎的行为**（见
    `baidu_search.is_block_page`）。
    """

    def __init__(self, results: list[SearchResult] | None = None,
                 blocked_per_query: int = 0):
        self._results = results if results is not None else []
        self._blocked_per_query = blocked_per_query
        self.blocked_pages = 0
        self.calls: list[str] = []

    def search(self, query: str, count: int = 10) -> list[SearchResult]:
        self.calls.append(query)
        self.blocked_pages += self._blocked_per_query
        if self._blocked_per_query:
            return []
        return list(self._results)


def _make(tmp_path, searcher: _FakeSearcher, n_queries: int = 2) -> NewsAggregator:
    """造一个只跑 `n_queries` 个查询、间隔为 0 的聚合器。"""
    queries = [(f"查询{i}", f"cat{i}") for i in range(n_queries)]
    agg = NewsAggregator(queries=queries, delay=0.0, data_dir=tmp_path)
    agg._searcher = searcher
    return agg


# --------------------------------------------------------------------------
# 断路①：百度风控
# --------------------------------------------------------------------------


def test_全部查询被拦时计数并与没结果区分开(tmp_path, caplog):
    """17 个查询全被挡，必须有一条说得出话的日志。"""
    searcher = _FakeSearcher(blocked_per_query=1)
    agg = _make(tmp_path, searcher, n_queries=3)

    with caplog.at_level(logging.INFO,
                         logger="construction_agent.collector.sources.news_aggregator"):
        got = agg.collect()

    assert got == []
    assert agg._fetch_failed == 3, "风控次数要按查询数累加"
    assert agg._total_listed == 0
    assert "全部" in caplog.text and "拦截页" in caplog.text
    assert "换关键词" in caplog.text, "要说清楚「这不是换关键词能解决的」"


def test_部分查询被拦时给出比例(tmp_path, caplog):
    """被挡一半时不能报「全部」，否则日志会骗人。"""
    searcher = _FakeSearcher()
    agg = _make(tmp_path, searcher, n_queries=2)
    # 第 1 个查询被挡，第 2 个放行但无结果
    agg._searcher = searcher
    agg.collect()

    searcher2 = _FakeSearcher(blocked_per_query=1)
    searcher2.blocked_pages = 0
    agg2 = _make(tmp_path, searcher2, n_queries=4)
    # 只让前两个查询被挡：用一个会在第 3 次调用后停手的包装
    orig = searcher2.search

    def _half(query, count=10):
        if len(searcher2.calls) >= 2:
            searcher2.calls.append(query)
            return []
        return orig(query, count)

    searcher2.search = _half
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.collector.sources.news_aggregator"):
        agg2.collect()

    assert agg2._fetch_failed == 2
    assert "全部" not in caplog.text, "2/4 不是全部，不能说成全部"


def test_一次都没被拦时不报风控(tmp_path, caplog):
    """风控计数为 0 时，日志里不该出现「拦截页」——否则告警变噪音。"""
    searcher = _FakeSearcher(blocked_per_query=0)
    agg = _make(tmp_path, searcher, n_queries=2)

    with caplog.at_level(logging.INFO,
                         logger="construction_agent.collector.sources.news_aggregator"):
        agg.collect()

    assert agg._fetch_failed == 0
    assert "拦截页" not in caplog.text


# --------------------------------------------------------------------------
# 断路②：域名白名单与百度跳转链接
# --------------------------------------------------------------------------


def test_搜到结果却全被白名单拦掉时说得出来(tmp_path, caplog):
    """搜索通了、一条都没产出——修前这里一条日志都没有。"""
    results = [
        SearchResult(title="某地工地事故", url="http://www.baidu.com/link?url=aaa",
                     snippet=""),
        SearchResult(title="另一地工地事故", url="http://www.baidu.com/link?url=bbb",
                     snippet=""),
    ]
    searcher = _FakeSearcher(results=results)
    agg = _make(tmp_path, searcher, n_queries=1)

    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.collector.sources.news_aggregator"):
        got = agg.collect()

    assert got == []
    assert agg._total_listed == 2
    assert agg._filtered_domain == 2
    assert "重定向" in caplog.text, "要把根因（跳转链接）写出来"
    assert "www.baidu.com" in caplog.text


def test_白名单域名会被下载(tmp_path):
    """正例：白名单里的域名要真的走到下载那一步。"""
    results = [
        SearchResult(title="人民网报道工地事故",
                     url="https://www.people.com.cn/n1/2026/0101/c1001-123.html",
                     snippet=""),
    ]
    searcher = _FakeSearcher(results=results)
    agg = _make(tmp_path, searcher, n_queries=1)
    agg._download_article = lambda url, title, cat: RawAccidentText(
        source=f"news_{cat}", title=title, raw_text="正文" * 100, source_url=url)

    got = agg.collect()

    assert len(got) == 1
    assert agg._total_listed == 1
    assert agg._filtered_domain == 0


def test_gov_cn域名虽不在白名单也放行(tmp_path):
    """`_is_allowed_domain` 对任意 `.gov.cn` 放行——这条别被后面的改动弄丢。"""
    searcher = _FakeSearcher(results=[
        SearchResult(title="x", url="https://www.mem.gov.cn/xw/2026/1.html", snippet=""),
    ])
    agg = _make(tmp_path, searcher, n_queries=1)
    assert agg._is_allowed_domain("https://www.mem.gov.cn/xw/2026/1.html") is True
    assert agg._is_allowed_domain("http://www.baidu.com/link?url=a") is False


# --------------------------------------------------------------------------
# 断路③：两条 `continue` 把请求间隔也跳过了
# --------------------------------------------------------------------------


def test_查询没结果时也要等间隔(tmp_path, monkeypatch):
    """**被风控时零间隔连打，只会招来更强的风控。**

    修前 `if not search_results: continue` 会跳过循环末尾的
    `time.sleep(self._delay * 2)`。2026-10-01 实测：全被拦那一轮 17 个查询
    共 2.7 秒（≈0.15 秒/次），而正常有结果、间隔生效时同样 17 个查询要
    100 秒出头——**两轮耗时差 40 倍，差的就是这个**。
    """
    slept: list[float] = []
    monkeypatch.setattr(news_aggregator.time, "sleep", slept.append)
    agg = _make(tmp_path, _FakeSearcher(blocked_per_query=1), n_queries=3)
    agg._delay = 3.0

    agg.collect()

    assert slept == [6.0, 6.0, 6.0], "每个查询之间都要等，哪怕它一条都没返回"


def test_查询抛异常时也要等间隔(tmp_path, monkeypatch):
    """另一条 `continue` 在 `except` 里——异常时连打得更快，更糟。"""

    class _Boom(_FakeSearcher):
        def search(self, query, count=10):
            raise RuntimeError("connection reset")

    slept: list[float] = []
    monkeypatch.setattr(news_aggregator.time, "sleep", slept.append)
    agg = _make(tmp_path, _Boom(), n_queries=2)
    agg._delay = 3.0

    agg.collect()

    assert slept == [6.0, 6.0]


def test_有结果时下载之间也照常等(tmp_path, monkeypatch):
    """原行为不能被改坏：下载每篇文章之间的间隔照旧。"""
    slept: list[float] = []
    monkeypatch.setattr(news_aggregator.time, "sleep", slept.append)
    searcher = _FakeSearcher(results=[
        SearchResult(title="a", url="https://www.people.com.cn/a.html", snippet=""),
        SearchResult(title="b", url="https://www.people.com.cn/b.html", snippet=""),
    ])
    agg = _make(tmp_path, searcher, n_queries=1)
    agg._delay = 3.0
    agg._download_article = lambda url, title, cat: RawAccidentText(
        source=f"news_{cat}", title=title, raw_text="正文" * 100, source_url=url)

    agg.collect()

    # 2 篇之间各睡 3.0（下载间隔）+ 收尾的 6.0（查询间隔）
    assert slept == [3.0, 3.0, 6.0]


# 「把间隔挪回循环末尾会不会红」不写成测试：测试能看到的只有 `slept` 这个
# 列表，看不到「间隔写在哪一行」，挪位置这种反改只能在源码上做。
# 交给 `revert39.py` 的 F 组。


# --------------------------------------------------------------------------
# 计数器与健康检查的契约
# --------------------------------------------------------------------------


@pytest.mark.parametrize("attr", ["_total_listed", "_fetch_failed", "_filtered_domain"])
def test_计数器名字就是健康检查读的名字(tmp_path, attr):
    """`check_collector_health.py` 的 `_probe_child` 按名字读。

    `_total_listed` 是三个候选名之一（`_total_listed` / `_total_list` /
    `_total_records`）——**名字写错不会报错，只会让「列表」那栏一直显示 `—`**，
    正是这个源修前的样子。所以把名字钉在测试里。
    """
    agg = _make(tmp_path, _FakeSearcher(), n_queries=1)
    assert hasattr(agg, attr)
    assert isinstance(getattr(agg, attr), int)


def test_判据自检_不看拦截计数这一条就红(tmp_path, caplog):
    """反改验证：把「读 blocked_pages」改回恒 0（等于修前不计数）。

    修前 `_fetch_failed` 这个属性压根不存在，`_probe_child` 读到 None，
    表上「闭环」那栏是 `—`——**没有任何一栏说得出「整轮都被风控挡了」**。
    """
    searcher = _FakeSearcher(blocked_per_query=1)
    agg = _make(tmp_path, searcher, n_queries=3)
    agg._searcher = searcher

    # 模拟修前：不知道 blocked_pages 这回事
    class _NoCounterSearcher(_FakeSearcher):
        def search(self, query, count=10):
            self.calls.append(query)
            return []

    agg._searcher = _NoCounterSearcher(blocked_per_query=1)
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.collector.sources.news_aggregator"):
        agg.collect()

    assert agg._fetch_failed == 0
    assert "拦截页" not in caplog.text
