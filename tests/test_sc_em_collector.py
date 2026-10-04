"""`sc_em` 列表项**两种形态**的单元测试。**不联网。**

本源的老毛病不是"连不上"也不是"详情页解析错"，而是**入口只开了一半**：
`list_link_pattern` 只认 `.shtml`，而 `dcbg` 目录下的条目有一半是 **PDF
直链**（`…/<32 位 hex>/files/<名>.pdf`）——它们**不需要详情解析**，地址本身
就是正文。写那条正则时的理由是"PDF 直链没有配套的详情解析，收进来只会多出
一条有标题、无正文的记录"，**前半句错了**：把"没有详情页"当成了"没有正文"，
于是唯一带建筑施工内容的几条恰好全被挡在门外。

健康检查表上它一直是「空 / 列表 4 / 闭环 ✓」——**闭环成立**说明那 4 条
`.shtml` 全进了初筛过滤，账目没错，是**入口少了**。

所以这里三组判据各钉一件事：

1. 正则认**两种**形态（少认一种就退回原状）；
2. `_crawl_detail` 对 PDF 直链**短路**，不抓页面、把地址原样交回当附件；
3. `_parse_list` 在**真实清单**上得 10 条 / 过初筛 3 条。

另有 collect() 级一条：`jx_em` 那轮的教训——**脚本级直调全绿 ≠ `collect()`
绿**，"到底采到几条"只有 `collect()` 答得出（见 `test_端到端_…`）。
"""
from __future__ import annotations

import re

import pytest

from construction_agent.collector.classifier import is_construction_candidate
from construction_agent.collector.sources.sc_em import (
    ScEMCollector,
    _is_direct_pdf,
)

# --------------------------------------------------------------------------
# 真实清单（2026-10-05 从栏目页解析出来的 10 条，URL 截去了中文文件名的
# URL 编码尾巴——正则只看到 `[^"']+\.pdf`，缩短不影响判据）。
# **形态是实测的**：6 条 PDF 直链 + 4 条 `.shtml`。
# --------------------------------------------------------------------------
_LINKS: list[tuple[str, str, str]] = [
    # 1 阿坝马尔康红旗大桥 **垮塌**——PDF，但过不了初筛（关键词表里没有"垮塌"）
    ("阿坝马尔康“11·11”红旗大桥垮塌灾害调查报告", "2026/7/31",
     "e8b588199ee246b38da34fb93b0d23af", "pdf"),
    ("自贡九鼎大楼“7·17”重大火灾事故防范和整改措施落实情况评估报告", "2026/4/15",
     "97f10741986c4e59ac42a5e9f738fb7c", "pdf"),
    ("成都成华臻礼著项目“9·1”较大起重伤害事故调查报告", "2026/1/5",
     "63d25391a5864ef6b1b28b09d2b91441", "pdf"),
    ("四川自贡九鼎大楼“7·17”重大火灾事故调查报告", "2025/4/9",
     "be18bd46eaa84da89b1995ee04171cd8", "pdf"),
    ("甘孜州丹巴县关州水电站“1•12”较大透水事故防范和整改措施落实情况评估报告",
     "2023/8/14", "0ef92c226d294456896c975bbb90ff4a", "shtml"),
    ("绵阳江油市“1•2”较大道路交通安全事故防范和整改措施落实情况评估报告",
     "2023/8/14", "cb1a272192544824b623a3bb04a24707", "shtml"),
    # 7 文件名是**纯 hex**（不是 URL 编码的中文），也有源站这么发
    ("长宁县福荣笋类食品厂“5·24”较大中毒窒息事故防范和整改措施落实情况评估报告",
     "2022/6/27", "1dbb8ca08e6841d68b553ccbf0428023", "pdf_hexname"),
    ("甘孜州丹巴县关州水电站“1·12”较大透水事故调查报告", "2022/5/20",
     "c7cfa3220fab45f6b17c7eda8a347eca", "pdf_hexname"),
    ("绵阳江油市“1·2”较大道路交通安全事故调查报告", "2022/4/8",
     "999dab979c9746c48c7f9a4d087cb906", "shtml"),
    ("长宁县福荣笋类食品厂“5·24”较大中毒窒息事故调查报告", "2021/7/5",
     "b3eebb4076634c3482006b829e5fb467", "shtml"),
]


def _href(title: str, date: str, hexid: str, kind: str) -> str:
    base = f"https://yjt.sc.gov.cn/scyjt/dcbg/{date}/{hexid}"
    if kind == "shtml":
        return f"{base}.shtml"
    if kind == "pdf_hexname":
        return f"{base}/files/a5358736217c42bab1e070f7f94fc16c.pdf"
    return f"{base}/files/{title[:6]}.pdf"


def _list_html() -> str:
    items = "".join(
        f'<li><a href="{_href(t, d, h, k)}">{t}</a></li>'
        for t, d, h, k in _LINKS)
    return f"<html><body><ul>{items}</ul></body></html>"


@pytest.fixture
def collector(tmp_path):
    # `delay=0`：不联网，取数只是为了拿到 `cfg` 与纯函数。
    return ScEMCollector(delay=0, data_dir=tmp_path)


# --------------------------------------------------------------------------
# A 组：正则认两种形态
# --------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://yjt.sc.gov.cn/scyjt/dcbg/2023/8/14/0ef92c226d294456896c975bbb90ff4a.shtml",
    "https://yjt.sc.gov.cn/scyjt/dcbg/2026/1/5/63d25391a5864ef6b1b28b09d2b91441/files/成华.pdf",
    "https://yjt.sc.gov.cn/scyjt/dcbg/2022/6/27/1dbb8ca08e6841d68b553ccbf0428023/files/a5358736217c42bab1e070f7f94fc16c.pdf",
])
def test_列表项正则收两种形态(collector, url):
    assert re.search(collector.cfg.list_link_pattern, url), url


@pytest.mark.parametrize("url, why", [
    ("https://yjt.sc.gov.cn/scyjt/dcbg/2023/8/14/xxx.html", "非 16 位 hex"),
    ("https://yjt.sc.gov.cn/scyjt/dcbg/2023/8/14/0ef92c226d294456896c975bbb90ff4a.html",
     "尾段必须是 .shtml 或 /files/*.pdf"),
    ("https://yjt.sc.gov.cn/scyjt/news/2026/1/5/63d25391a5864ef6b1b28b09d2b91441/files/x.pdf",
     "不是 dcbg 栏目"),
    ("https://yjt.sc.gov.cn/scyjt/dcbg/GovInfoPublic.shtml", "栏目页自己"),
])
def test_列表项正则不收别的(collector, url, why):
    assert not re.search(collector.cfg.list_link_pattern, url), why


@pytest.mark.parametrize("url", [
    "https://a.gov.cn/x/files/报告.pdf",
    "https://a.gov.cn/x/files/报告.PDF",              # 后缀大小写
    "https://a.gov.cn/x/files/报告.pdf?download=1",   # 带 query
])
def test_is_direct_pdf认尾段(url):
    assert _is_direct_pdf(url), url


@pytest.mark.parametrize("url", [
    "https://a.gov.cn/x/t20260105_8639621.html",
    "https://a.gov.cn/x/t20260105_8639621.shtml",
    "https://a.gov.cn/x/说明pdf.shtml",        # 中间带 pdf 不算
    "https://a.gov.cn/x/报告.pdf.shtml",
])
def test_is_direct_pdf不认非PDF(url):
    assert not _is_direct_pdf(url), url


def test_正则里的两个分支确实各自存在():
    """少一个分支 = 退回修之前的状态，而**那个状态也是绿的**（列表 4 条、
    闭环成立）。所以不能只看 `_parse_list` 有没有返回东西。"""
    import construction_agent.collector.sources.sc_em as m
    assert r"\.shtml" in m._LIST_ITEM
    assert r"\.pdf" in m._LIST_ITEM


# --------------------------------------------------------------------------
# B 组：`_crawl_detail` 对 PDF 直链短路
# --------------------------------------------------------------------------
def test_PDF直链不抓页面(collector, monkeypatch):
    """**这条是"少一条有标题、无正文的空壳"的判据本身。** 短路生效时页面
    一次都不抓；如果它退回去走基类，`_fetch` 会被调用，这里当场报红。"""
    def boom(url):
        raise AssertionError(f"PDF 直链不该抓页面，却抓了 {url}")
    monkeypatch.setattr(collector, "_fetch", boom)

    d = collector._crawl_detail("https://a.gov.cn/x/files/报告.pdf")
    assert d is not None
    assert d["pdf_urls"] == ["https://a.gov.cn/x/files/报告.pdf"]


def test_PDF直链返回的结构(collector):
    url = "https://a.gov.cn/x/files/报告.pdf"
    d = collector._crawl_detail(url)
    assert d == {"title": "", "date": "", "text": "", "text_lines": [],
                 "pdf_urls": [url], "office_urls": [], "image_urls": []}


def test_PDF直链的标题日期留空是有意的(collector):
    """主循环里 `detail.get("title") or title` / `… or list_date` 会退回
    **列表项自己的**标题与日期（`_parse_list` 已从 `<a>` 文本和 URL 取到），
    比在短路分支里重算一遍可靠。这里钉住"留空"这个约定。"""
    d = collector._crawl_detail("https://a.gov.cn/x/files/报告.pdf")
    assert not d["title"] and not d["date"]


def test_详情页仍走基类(collector):
    """短路只对 PDF 生效，`.shtml` 必须原样走基类的详情解析。"""
    html = ("<html><head><title>某地事故调查报告</title></head><body>"
            "<div class='con'>" + "这是一段够长的正文。" * 30 + "</div>"
            "</body></html>")
    d = collector._crawl_detail(
        "https://a.gov.cn/scyjt/dcbg/2023/8/14/0ef92c226d294456896c975bbb90ff4a.shtml",
        html)
    assert d is not None
    assert d["text"], "详情页没解析出正文——短路把 .shtml 也吃掉了？"
    assert d["pdf_urls"] == []


# --------------------------------------------------------------------------
# C 组：`_parse_list` 在真实清单上的产出
# --------------------------------------------------------------------------
def test_列表解析出10条(collector):
    got = collector._parse_list(_list_html(), collector.cfg.list_url)
    assert len(got) == 10
    assert [g["title"] for g in got] == [t for t, _, _, _ in _LINKS]


def test_十条里六条PDF直链四条详情页(collector):
    got = collector._parse_list(_list_html(), collector.cfg.list_url)
    pdfs = [g for g in got if _is_direct_pdf(g["url"])]
    # **6 + 4 = 10**。原先正则只收 4 条 `.shtml`，正是把 6 条 PDF 全丢了。
    assert len(pdfs) == 6, [g["url"] for g in pdfs]
    assert len(got) - len(pdfs) == 4


def test_日期从href的日期段推出(collector):
    got = collector._parse_list(_list_html(), collector.cfg.list_url)
    assert got[0]["date"] == "2026-07-31"
    assert got[2]["date"] == "2026-01-05"
    assert got[-1]["date"] == "2021-07-05"


def test_过初筛的正好3条(collector):
    """初筛这一道从来没错——错的是**入口只进来 4 条、且一条都不是建筑**。"""
    got = collector._parse_list(_list_html(), collector.cfg.list_url)
    kw = collector.cfg.construction_keywords
    passed = [g["title"] for g in got
              if is_construction_candidate(g["title"], keywords=kw)[0]]
    assert len(passed) == 3, passed
    assert any("起重伤害" in t for t in passed)
    assert all("九鼎" in t or "起重" in t for t in passed)


def test_垮塌不过初筛_缺口记录(collector):
    """阿坝马尔康红旗大桥 **垮塌** 是 PDF 直链、也是真事故，但关键词表里只有
    「坍塌」「倒塌」，没有「垮塌」——它判否。

    **这条钉的是现状、不是"该判否"**：若将来给关键词表补上「垮塌」，本测试
    应当跟着改（它会红，红的正是地方）。
    """
    title = "阿坝马尔康“11·11”红旗大桥垮塌灾害调查报告"
    ok, why = is_construction_candidate(
        title, keywords=collector.cfg.construction_keywords)
    assert ok is False
    assert "垮塌" not in " ".join(collector.cfg.construction_keywords)
    assert why  # 判据给了理由，不是静默丢弃


# --------------------------------------------------------------------------
# D 组：配置
# --------------------------------------------------------------------------
def test_配置指向真实栏目页(collector):
    assert collector.cfg.base_url == "https://yjt.sc.gov.cn"
    assert collector.cfg.list_url.endswith("/scyjt/dcbg/GovInfoPublic.shtml")


def test_分页留空因为确实只有一页(collector):
    """页面源码里 `createPageHTML('page_div', 1, 1, …)`——第 2、3 个参数
    都是 1，**总页数就是 1**。留空不是"没查过"，是查过。
    （留空就让 `_discover_pages` 只抓列表页本身。）"""
    assert collector.cfg.list_page_pattern == ""


def test_registry里的URL与采集器同域(collector):
    from construction_agent.collector.registry import SOURCES
    ent = SOURCES["sc_em"].entry_url
    assert "yjt.sc.gov.cn" in ent
    assert ent == collector.cfg.list_url


# --------------------------------------------------------------------------
# E 组：`collect()` 级——直调全绿 ≠ collect 绿
# --------------------------------------------------------------------------
_BODY = ("成都成华臻礼著项目“9·1”较大起重伤害事故调查报告\n"
         "事故调查组\n2025 年11 月\n") + "正文段落。" * 400


def _stub(tmp_path):
    """不联网的 sc_em 采集器：列表页给内联清单，PDF 一律回一段长正文。"""
    c = ScEMCollector(delay=0, data_dir=tmp_path / "src")
    from construction_agent.collector.stores import RawAccidentStore
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    c.cfg.enable_classify_v2 = False          # 默认走审核队列，不调分类
    c._fetch = lambda url: (_list_html() if url == c.cfg.list_url else "")
    c._download_and_extract_pdf = lambda u, t, d: (_BODY, "")
    return c


def _close(c: ScEMCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


def test_端到端_PDF直链能采到正文(tmp_path):
    """修之前这里恒为 0 条。**PDF 直链下载→提正文→进队列**整条通。"""
    c = _stub(tmp_path)
    try:
        c.collect()
        items = c.raw_store.get_all()
        assert len(items) == 3, [i["title"] for i in items]
        for it in items:
            assert len(it["content"]) > 1000, it["title"]
            assert "正文段落" in it["content"]
        assert c._total_listed == 10
        assert c._filtered_title == 7
    finally:
        _close(c)


def test_端到端_列表少认一种就退回原状(tmp_path):
    """把正则退回"只收 .shtml"，采集结果必须**当场塌回 0 条**——这是"两种
    形态缺一不可"的负向判据（原先那个状态在健康检查表上也是"闭环 ✓"）。"""
    c = _stub(tmp_path)
    try:
        c.cfg.list_link_pattern = r"/scyjt/dcbg/\d{4}/\d{1,2}/\d{1,2}/[0-9a-f]{16,}\.shtml"
        c.collect()
        assert c._total_listed == 4
        assert c.raw_store.get_all() == []
    finally:
        _close(c)


def test_端到端_分类闸门把非施工挡在外面(tmp_path):
    """三条候选里两条是自贡九鼎大楼火灾（评估报告 + 正本），`classify_v2`
    判高置信非施工 → 跳过；起重伤害那条置信度不够 → 进审核。"""
    c = _stub(tmp_path)
    import construction_agent.collector.sources.trs_cms_base as m
    truth = {
        "成都成华": {"is_construction": None, "confidence": "low",
                     "reason": "含施工要素但证据不足"},
        "自贡九鼎": {"is_construction": False, "confidence": "high",
                     "reason": "火灾，非建筑施工"},
    }

    def fake(title, text):
        for k, v in truth.items():
            if k in title:
                return v
        return {"is_construction": True, "confidence": "high", "reason": "施工"}
    c.cfg.enable_classify_v2 = True
    try:
        orig = m.classify_v2
        m.classify_v2 = fake
        try:
            c.collect()
        finally:
            m.classify_v2 = orig
        titles = [i["title"] for i in c.raw_store.get_all()]
        assert len(titles) == 1 and "起重" in titles[0]
        assert c._filtered_classify == 2
        assert c._collected == 0 and c._review == 1
    finally:
        _close(c)
