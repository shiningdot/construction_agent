"""`jx_em` 三层通路的单元测试。**不联网。**

本源是唯一一家「JSON 列表 + JS 数据岛正文 + PDF 附件」的源（形态普查见
`docs/web/01_在线采集.md` §2.5.46），三层各有一组判据，都不是从别的源推得的：

1. 列表不是 HTML，是 `-articleList.json` 的响应体——`_parse_list` 的入参名叫
   `html`，实际拿到的却是 JSON。**参数名不变、语义变了**，所以要有测试钉住。
2. `urls` 字段是**双重编码的 JSON 字符串**。当成 dict 去下标，会拼出一个带引号
   的 URL，`httpx` 抛 `InvalidURL: Invalid port: '"'`——错误发生在离病灶很远的地方。
3. 详情页正文在无 `type` 属性的内联 `<script>` 里（基类 `_unwrap_data_islands`
   只认 `text/xml`，不认这一种），而**数据岛的 content 有两种形态**：附件链接
   与 HTML 正文。**两种都要接**（§2.5.55）。

第 3 条**本轮翻转过一次判断**。原判据是「只提 `<a>` 开标签、不能带标签文本」，
理由是那三篇样本剥掉标签后是一串「…报告．pdf」，文字进正文算污染；守它的两条
测试，一条查字符串里有没有 `</a>`，一条比 `get_text()` 前后是否**一字不增**。
2026-10-05 复测另外 3 条时拿到反例：那 3 页的 content 就是 784 / 2,842 / 276 字
的正文 HTML，页面上 `<a href>` **0 个**——只补 `<a>` 是空操作，三条正文全为 0 字。
**「`get_text()` 一字不增」正是缺陷本身，不是要保的性质。**
"""
from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup

from construction_agent.collector.sources.jx_em import (
    _LIST_API,
    JxEMCollector,
    _inject_into_body,
    _island_content_html,
)


@pytest.fixture
def collector(tmp_path):
    # `delay=0`：不联网，取数只是为了拿到 `cfg`。
    return JxEMCollector(delay=0, data_dir=tmp_path)


# --------------------------------------------------------------------------
# 第三层：数据岛 → 正文 HTML
# --------------------------------------------------------------------------

# 形态一：content 是一串附件链接（与真实页面 `content_2074388022702022656` 同形）。
# `r'''…'''` 里的 `\"` 就是页面上的 JSON 转义，不是笔误。
_ISLAND_ATTACH = r'''<html><script>
// 1 519199 文章正文 col0
const col0_519199 = [{ content:{"content":"<p><a class=\"tinymce-annex\" href=\"/jxsyjglt/251210dxsgal/2074388022702022656/ZD7IpYi6.pdf\" download=\"评估报告.pdf\">评估报告．pdf</a>&nbsp;</p>"}, images:[{}], title:'x'}]
</script></html>'''

# 形态二：content **就是正文本身**（与真实页面「揭阳市“3·26”重大火灾事故调查
# 报告」同形——那一页实测 `<a href>` 0 个、数据岛正文 2,842 字）。
_ISLAND_TEXT = r'''<html><script>
const col0_2 = [{ content:{"content":"<p>2014年3月26日13时20分，位于揭阳普宁市军埠镇莲坛村水浮沟下第二街泉发楼郑晓生等人经营的内衣作坊发生重大火灾事故，造成12人死亡，5人受伤。</p><p>事故发生经过：……</p>"}, images:[{}], title:'y'}]
</script></html>'''

_PLAIN = "<html><body><p>很普通的页面，没有任何数据岛</p></body></html>"


def test_附件型数据岛的链接照旧交得回去():
    got = _island_content_html(_ISLAND_ATTACH)
    assert "ZD7IpYi6.pdf" in got


def test_正文型数据岛的文字这次进得来():
    """**这条是翻转过来的判据。**

    改前它叫 `test_提出来的标签不会给正文增加一个字`，比的是 `get_text()` 前后
    **一字不增**——而现在那正好是要消灭的现象：整页正文都在数据岛里时，「一字
    不增」就是「正文 0 字」。判据反过来了：**该增的必须增，且增的是正文**。

    **取的是 `soup.body`，不是 `soup`**——这不是风格问题。第一版修复（尾拼接）
    下 `soup.get_text()` 有正文、`soup.body.get_text()` 没有，而基类用的是后者：
    测试用整棵树、生产只用 body，**等于没测**。判据必须与产出函数同口径。
    """
    before = BeautifulSoup(_PLAIN, "html.parser").body.get_text()
    after = BeautifulSoup(
        _inject_into_body(_PLAIN, _island_content_html(_ISLAND_TEXT)),
        "html.parser").body.get_text()
    assert "2014年3月26日13时20分" in after
    assert "造成12人死亡" in after
    assert len(after) > len(before) + 40, "正文没进 body"


def test_尾拼接会把正文丢在body外面():
    """把上面那条判据的**失败路径**钉住——它是本轮最贵的一个坑。

    同样的内容，尾拼接时整棵树拿得到、`body` 拿不到；插进 `</body>` 前才两者
    都有。这条用例直接断言这个差别，防止将来有人「简化」回 `html + extra`。
    """
    extra = _island_content_html(_ISLAND_TEXT)
    tails = BeautifulSoup(_PLAIN + extra, "html.parser")
    assert "2014年3月26日" in tails.get_text()          # 整棵树看得见
    assert "2014年3月26日" not in tails.body.get_text()  # body 里没有 ← 病灶
    fixed = BeautifulSoup(_inject_into_body(_PLAIN, extra), "html.parser")
    assert "2014年3月26日" in fixed.body.get_text()


def test_没有body标签时退回尾拼接():
    """真实 TRS 页面都有 `</body>`；没有时不能抛，行为与旧版一致。"""
    assert _inject_into_body("<p>只有片段</p>", "<p>x</p>") == "<p>只有片段</p><p>x</p>"


def test_空正文时不改动原串():
    """无数据岛的页面必须逐字返回原串——13 个 TRS 源都走这条路。"""
    assert _inject_into_body(_PLAIN, "") == _PLAIN


def test_没有数据岛的页面是无操作():
    """9 家有数据的 TRS 源全是纯 HTML——这个函数对它们必须什么都不做。"""
    assert _island_content_html(_PLAIN) == ""
    assert _island_content_html("") == ""


def test_多个数据岛里的正文都要取出来():
    two = _ISLAND_TEXT.replace("col0_2", "col0_1", 1) + _ISLAND_TEXT
    got = _island_content_html(two)
    assert got.count("2014年3月26日") == 2, "一页挂多个岛时不能只取第一个"


def test_没有数据岛形态的页面不动它的content():
    """守卫 `_ISLAND_ASSIGN` 的价值，**测试的是误伤面**。

    反改验证时把守卫去掉，14 条测试一条没红——因为 `_PLAIN` 里压根没有
    `"content": "…"`，去掉守卫也不改变它。守卫真正挡的是**另一种页面**：
    页面里有别处的 `"content": "…"`（JSON-LD、别的 JS 变量），但**没有**
    `const colN_N = [` 那种数据岛声明。去掉它，那些页面的元数据会被当成正文
    拼进来——那才是这个 `if` 存在的理由。
    """
    page = ('<html><body><p>正文</p>'
            '<script>var meta = {"content": "这是页面元数据，不是正文"}</script>'
            '</body></html>')
    assert _island_content_html(page) == ""


def test_反解不了的岛记一声但不当成错误(caplog):
    """静默跳过与「岛上没有正文」长得一样，而这两种处理完全不同。

    构造要点：**要坏在 `json.loads` 那一步，而不是坏在正则那一步**。
    第一版把某个 `\\\\"` 改成 `XX`——正则照样抠得出值、`json.loads` 照样成功，
    于是测试红在一个假病灶上。`\\q` 才是真的非法转义：正则抠得出来，反解不了。
    """
    broken = r'''<script>const col0_1 = [{ content:{"content":"abc\qdef"}}]</script>'''
    with caplog.at_level("WARNING"):
        assert _island_content_html(broken) == ""
    assert any("数据岛" in r.message for r in caplog.records)


# 页面形态：数据岛**长在页面自己的 `<body>` 里**（真实页面如此）。
_PAGE_WITH_ISLAND = (
    '<html><body><p>模板壳 {{#each data}}</p>'
    r'<script>const col0_1 = [{ content:{"content":"'
    '<p>2014年3月26日13时20分，作坊发生重大火灾事故，造成12人死亡。</p>'
    r'"}}]</script>'
    '</body></html>')


def test_crawl_detail_把数据岛正文交进正文(collector, monkeypatch):
    """**这条才覆盖 `_crawl_detail` 的调用点**——上面那组只测到函数本身。

    补它的原因很具体：反改验证时把 `_crawl_detail` 里的 `_inject_into_body`
    改回尾拼接（`raw + extra`，即第一版那个「改完还是 0 字」的版本），
    **上面 13 条测试一条都没红**——它们全都绕过了调用点，直接调那两个纯函数。
    「测试组有几条」与「它守住了什么」是两件事；这条补的是**位置**。
    """
    monkeypatch.setattr(collector, "_fetch", lambda url: _PAGE_WITH_ISLAND)
    got = collector._crawl_detail("https://x/y.html")
    assert got is not None
    assert "2014年3月26日13时20分" in got["text"], "正文没被交到基类的正文口径里"
    assert "模板壳" in got["text"], "页面原有的内容不该被丢掉"
    assert got["text_lines"], "text_lines 也要有——模板统计走的正是它"


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
