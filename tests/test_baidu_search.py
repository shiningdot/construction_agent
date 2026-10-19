"""百度搜索的「拦截页 vs 真的没有结果」判据。

**不联网。** `httpx.Client` 换成假的，HTML 用实测抓下来的形态。

2026-10-01 第三十九轮实测（同一 IP 连打同一个查询）：

    第 1 次  HTTP 200  889,697 字节  解析 7 条      ← 真结果页
    第 2 次  HTTP 200    1,438 字节  解析 0 条      ← `<title>百度安全验证</title>`
    第 3–5 次 同上

三件事让这个故障在被发现之前完全隐形：**都是 HTTP 200**（`raise_for_status`
放行）、**都不抛异常**、**`_parse_results` 都返回空列表**。调用方
`if not search_results: continue` 于是把「被风控挡了」记成「这个查询没有结果」，
日志里只有一条 INFO——**换关键词、加重试都不会有用，但没有任何地方说得出
这句话**。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.search import baidu_search  # noqa: E402
from construction_agent.search.baidu_search import (  # noqa: E402
    BaiduSearchEngine, classify_response, is_block_page)

# 实测形状：1,438 字节、title 是「百度安全验证」、没有 content_left
_BLOCK_HTML = ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
               "    <meta charset=\"utf-8\">\n"
               "    <title>百度安全验证</title>\n"
               "    <meta http-equiv=\"Content-Type\" "
               "content=\"text/html; charset=utf-8\">\n"
               "</head>\n<body>\n"
               "    <p>请完成安全验证后继续访问</p>\n"
               "</body>\n</html>\n")

# 真结果页：有 content_left 容器
_RESULT_HTML = (
    '<html><body><div id="content_left">'
    '<div class="result c-container"><h3>'
    '<a href="http://www.baidu.com/link?url=Sy7kCqtQeULZbwXfPwPzX5">'
    '一建筑工地塔吊施工现场发生一起安全生产事故</a></h3>'
    '<div class="c-abstract">摘要文本</div></div>'
    '</div></body></html>')

# **真结果页，但一个条目都没有**——这才是「这个查询真的没搜到」
_EMPTY_RESULT_HTML = '<html><body><div id="content_left"></div></body></html>'

# 没有结果容器、也不命中任何风控标记词。真世界里这一格有来路：百度改版
# 换了容器名、或者回了一个别样的错误页。
_UNKNOWN_HTML = ("<html><head><title>出错了</title></head>"
                 "<body><p>页面暂时无法访问</p></body></html>")


class _FakeClient:
    """只够 `search()` 用的假 client。"""

    def __init__(self, html: str, status: int = 200):
        self._html = html
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None, params=None):
        def _raise():
            if self._status >= 400:
                raise RuntimeError(f"HTTP {self._status}")

        return SimpleNamespace(text=self._html, status_code=self._status,
                               raise_for_status=_raise)


def _stub_client(monkeypatch, html: str, status: int = 200) -> None:
    monkeypatch.setattr(baidu_search.httpx, "Client",
                        lambda **kw: _FakeClient(html, status))


# --------------------------------------------------------------------------
# `is_block_page`：判据本身
# --------------------------------------------------------------------------


def test_拦截页判成拦截页():
    assert is_block_page(_BLOCK_HTML) is True


def test_真结果页不是拦截页():
    assert is_block_page(_RESULT_HTML) is False


def test_结果页里一个条目都没有也不是拦截页():
    """**反例，也是这条判据的全部要点。**

    「没有结果」有两种：结果页里确实一条都没有，和压根没拿到结果页。
    判据必须只看「有没有结果容器 `content_left`」，不能只看「解析出几条」——
    后者对两种情况都是 0。
    """
    assert is_block_page(_EMPTY_RESULT_HTML) is False


def test_不认识的无容器页面不说是风控():
    """没有结果容器、又不认识——**判成 `unknown`，不是 `blocked`**。

    起初我把这条写成「把标记词去掉仍要判成拦截页」，跑出来是红的，红得对：
    那样等于宣称「只要没有 `content_left` 就是风控」，于是**百度改版换了容器
    名也会被报成「你被风控了」**——把人引去换 IP、换搜索源，而真正该做的是
    修解析器。方向错了。误报风控比漏报贵，所以 `blocked` 还要求命中标记词。
    """
    assert is_block_page(_UNKNOWN_HTML) is False, "不认识 ≠ 风控"
    assert classify_response(_UNKNOWN_HTML) == "unknown"


def test_百度换措辞时落到unknown而不是风控():
    """标记词是「确认风控」的**必要条件**——百度只要换个措辞，就落 `unknown`。

    这不是漏，是设计：`unknown` 说的是实话（「我不认识这个页面」），
    而误报 `blocked` 会把人引去换 IP。宁可说「我不知道」。

    拿实测那个拦截页把标记词全抹掉，就是这个情形。
    """
    no_marker = (_BLOCK_HTML
                 .replace("百度安全验证", "访问受限")
                 .replace("请完成安全验证", "请您稍后再试"))
    assert classify_response(no_marker) == "unknown", (
        "抹掉标记词后仍判成 blocked，说明判据其实是标记词而非容器")


def test_三态分得开():
    """`results` / `blocked` / `unknown` 三格各自有各自的意思。"""
    assert classify_response(_RESULT_HTML) == "results"
    assert classify_response(_EMPTY_RESULT_HTML) == "results", "空的结果页仍是结果页"
    assert classify_response(_BLOCK_HTML) == "blocked"
    assert classify_response("<html><body>什么都没有</body></html>") == "unknown"
    assert classify_response("") == "unknown", "空响应也不能被当成结果页"


# --------------------------------------------------------------------------
# `search()`：拦截页要计数、要告警，且不能与「没结果」同形
# --------------------------------------------------------------------------


def test_被拦截时计数并留warning(monkeypatch, caplog):
    _stub_client(monkeypatch, _BLOCK_HTML)
    eng = BaiduSearchEngine()
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.search.baidu_search"):
        got = eng.search("建筑施工 安全事故 死亡", count=8)

    assert got == []
    assert eng.blocked_pages == 1
    assert "拦截页" in caplog.text
    assert "百度安全验证" in caplog.text, "告警要把 title 带上，人才知道被谁挡了"


def test_真结果页不计数(monkeypatch, caplog):
    _stub_client(monkeypatch, _RESULT_HTML)
    eng = BaiduSearchEngine()
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.search.baidu_search"):
        got = eng.search("建筑施工 安全事故 死亡", count=8)

    assert len(got) == 1
    assert eng.blocked_pages == 0
    assert "拦截页" not in caplog.text


def test_真没结果时不计数也不告警(monkeypatch, caplog):
    """拿到的是结果页、只是里面空的——这是「没搜到」，不是被拦。"""
    _stub_client(monkeypatch, _EMPTY_RESULT_HTML)
    eng = BaiduSearchEngine()
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.search.baidu_search"):
        got = eng.search("一个查不到东西的词", count=8)

    assert got == []
    assert eng.blocked_pages == 0, "真没结果被计成风控，告警就变成了噪音"
    assert "拦截页" not in caplog.text


def test_连续被拦会累加(monkeypatch):
    """`blocked_pages` 要能累加——调用方靠它判断「整轮全被挡了」。"""
    _stub_client(monkeypatch, _BLOCK_HTML)
    eng = BaiduSearchEngine()
    for i in range(3):
        eng.search(f"查询{i}", count=8)
    assert eng.blocked_pages == 3


def test_不认识的页面在search里也要说话(monkeypatch, caplog):
    """`unknown` 不能沉默——沉默就等于又把它记成「没结果」。"""
    _stub_client(monkeypatch, _UNKNOWN_HTML)
    eng = BaiduSearchEngine()
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.search.baidu_search"):
        got = eng.search("建筑施工 安全事故 死亡", count=8)

    assert got == []
    assert eng.blocked_pages == 0, "不认识 ≠ 风控，不能计进风控数"
    assert "不认识" in caplog.text
    assert "解析器" in caplog.text, "要说清楚该往哪个方向查"
    assert "返回的是拦截页" not in caplog.text, "不能把不认识说成风控"


def test_判据自检_不分三态这一条就红(monkeypatch, caplog):
    """反改验证：把 `classify_response` 改回「恒 results」（等于修前）。

    改前没有这个函数，`search()` 直接把空列表当结果返回，`blocked_pages`
    永远是 0、日志里永远没有 warning——正是这个源长期 0 产出而没人知道
    原因的样子。
    """
    monkeypatch.setattr(baidu_search, "classify_response", lambda _html: "results")
    _stub_client(monkeypatch, _BLOCK_HTML)
    eng = BaiduSearchEngine()
    with caplog.at_level(logging.WARNING,
                         logger="construction_agent.search.baidu_search"):
        got = eng.search("建筑施工 安全事故 死亡", count=8)

    assert got == []
    assert eng.blocked_pages == 0
    assert "拦截页" not in caplog.text
