"""`jx_em` 三层通路的单元测试。**不联网。**

本源是唯一一家「JSON 列表 + JS 数据岛正文 + PDF 附件」的源（形态普查见
`docs/web/01_在线采集.md` §2.5.46），三层各有一组判据，都不是从别的源推得的：

1. 列表不是 HTML，是 `-articleList.json` 的响应体——`_parse_list` 的入参名叫
   `html`，实际拿到的却是 JSON。**参数名不变、语义变了**，所以要有测试钉住。
2. `urls` 字段是**双重编码的 JSON 字符串**。当成 dict 去下标，会拼出一个带引号
   的 URL，`httpx` 抛 `InvalidURL: Invalid port: '"'`——错误发生在离病灶很远的地方。
3. 详情页正文在无 `type` 属性的内联 `<script>` 里（基类 `_unwrap_data_islands`
   只认 `text/xml`，不认这一种），且**正文实体是一串 PDF 链接**。

第 3 条有个容易写错的地方：提附件时**只能提 `<a>` 开标签、不能带标签文本**，
否则拼进页面会污染基类用 `get_text()` 取的正文。这里用 `get_text()` 前后对比来
守它，而不是用字符串 contains——`download="评估报告.pdf"` 里的那几个字是**属性值**，
本来就在、也应该在。
"""
from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup

from construction_agent.collector.sources.jx_em import (
    _LIST_API,
    JxEMCollector,
    _island_attachment_tags,
)


@pytest.fixture
def collector(tmp_path):
    # `delay=0`：不联网，取数只是为了拿到 `cfg`。
    return JxEMCollector(delay=0, data_dir=tmp_path)


# --------------------------------------------------------------------------
# 第三层：数据岛 → 附件标签
# --------------------------------------------------------------------------

# 与真实页面同形：`r'''…'''` 里的 `\"` 就是页面上的 JSON 转义，不是笔误。
_ISLAND = r'''<html><script>
// 1 519199 文章正文 col0
const col0_519199 = [{ content:{"content":"<p><a class=\"tinymce-annex\" href=\"/jxsyjglt/251210dxsgal/2074388022702022656/ZD7IpYi6.pdf\" download=\"评估报告.pdf\">评估报告．pdf</a>&nbsp;</p>"}, images:[{}], title:'x'}]
</script></html>'''

_PLAIN = "<html><body><p>很普通的页面，没有任何数据岛</p></body></html>"


def test_数据岛里的附件被提成开标签():
    got = _island_attachment_tags(_ISLAND)
    assert got.startswith("<a class=")
    assert "ZD7IpYi6.pdf" in got
    assert "</a>" not in got, "只提开标签"


def test_提出来的标签不会给正文增加一个字():
    """这条才是「只提开标签」的**真判据**。

    前一条检查字符串里有没有 `</a>`，只能挡住最粗的一种写法；真正要保的性质是
    「拼进页面后 `get_text()` 一字不增」。拿属性值里的字去 contains 会把
    `download="评估报告.pdf"` 误判成污染——那是属性，不进正文。
    """
    before = BeautifulSoup(_PLAIN, "html.parser").get_text()
    after = BeautifulSoup(_PLAIN + _island_attachment_tags(_ISLAND),
                          "html.parser").get_text()
    assert before == after


def test_没有数据岛的页面是无操作():
    """9 家有数据的 TRS 源全是纯 HTML——这个函数对它们必须什么都不做。"""
    assert _island_attachment_tags(_PLAIN) == ""
    assert _island_attachment_tags("") == ""


def test_多个数据岛里的附件都要提出来():
    two = _ISLAND.replace("col0_519199", "col0_1", 1) + _ISLAND
    got = _island_attachment_tags(two)
    assert got.count("<a ") == 2, "一页挂多个岛时不能只取第一个"


def test_反解不了的岛记一声但不当成错误(caplog):
    """静默跳过与「岛上没有附件」长得一样，而这两种处理完全不同。

    构造要点：**要坏在 `json.loads` 那一步，而不是坏在正则那一步**。
    第一版把某个 `\\\\"` 改成 `XX`——正则照样抠得出值、`json.loads` 照样成功，
    于是测试红在一个假病灶上。`\\q` 才是真的非法转义：正则抠得出来，反解不了。
    """
    broken = r'''<script>const col0_1 = [{ content:{"content":"abc\qdef"}}]</script>'''
    with caplog.at_level("WARNING"):
        assert _island_attachment_tags(broken) == ""
    assert any("数据岛" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# 第二层：JSON 列表接口
# --------------------------------------------------------------------------

_REAL_ITEM = {
    "id": "2074388022702022656",
    "title": "吉安峡江县戈坪铁矿有限公司“7·27”较大冒顶片帮瞒报事故整改和防范措施落实情况评估报告",
    "pubDate": "2026-07-07 14:59",
    "contentSource": "调查统计处",
    # **双重编码**：这个值是一个 JSON 字符串，不是 dict。
    "urls": '{"pc":"/jxsyjglt/col/251210dxsgal/content/content_2074388022702022656.html"}',
}


def test_列表接口的条目解析成标题与绝对URL(collector):
    links = collector._parse_list(json.dumps([_REAL_ITEM], ensure_ascii=False), _LIST_API)
    assert len(links) == 1
    one = links[0]
    assert one["url"] == ("https://yjglt.jiangxi.gov.cn/jxsyjglt/col/"
                          "251210dxsgal/content/content_2074388022702022656.html")
    assert '"' not in one["url"], "双重编码的引号漏进来了"
    assert one["date"] == "2026-07-07", "pubDate 只取日期那半截"
    assert "吉安峡江县戈坪铁矿" in one["title"]


def test_urls字段已经是dict时也要认(collector):
    """两种编码层数在新旧接口里并存，不能只认一种。"""
    item = dict(_REAL_ITEM, urls={"pc": "/jxsyjglt/col/x/content/content_1.html"})
    links = collector._parse_list(json.dumps([item], ensure_ascii=False), _LIST_API)
    assert len(links) == 1
    assert links[0]["url"].endswith("content_1.html")


def test_列表接口没吐JSON时返回空表而不是抛(collector, caplog):
    with caplog.at_level("WARNING"):
        assert collector._parse_list("<html>被 WAF 换成了拦截页</html>", _LIST_API) == []
    assert any("没返回 JSON" in r.message for r in caplog.records)


def test_缺少标题或地址的条目跳过(collector):
    items = [
        dict(_REAL_ITEM, title=""),
        dict(_REAL_ITEM, urls="{}"),
        dict(_REAL_ITEM, urls=""),          # 空串也是 str，走 json.loads 会抛
        _REAL_ITEM,
    ]
    links = collector._parse_list(json.dumps(items, ensure_ascii=False), _LIST_API)
    assert len(links) == 1, "三条残件都不该进结果"


def test_列表不探测分页只返回接口地址(collector):
    """接口自己不给翻页（六种 GET 参数 + POST + 路径后缀全无效），探也是白探。"""
    assert collector._discover_pages() == [_LIST_API]
