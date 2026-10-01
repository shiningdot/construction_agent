"""内容图抽取（`construction_agent.collector.images`）的回归测试。

**不联网。** `_fetch` 换成假的。

判据是 2026-10-01 第十八/十九轮定的：**图片 URL 与详情页 URL 同目录**
（或以页面路径为前缀）。它的前身「同目录 + 编号/时间戳连续」被 `tj_em`
当场推翻——那个源有 10 张同目录、时间戳连续递增的图，却是模板图（四个不同
页面每页都挂同一批）。所以这里的反例测试比正例更要紧。

第二十一轮加了第三条：**图片名主干 == 页面 ID**（`gd_em` 形态，它的图不在
页面同目录下，「同目录」在它身上 152 张里漏 152 张）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from construction_agent.collector.images import (  # noqa: E402
    extract_page_images, is_attachment_icon, is_page_image, page_article_id)
from construction_agent.collector.sources.nmg_em import (  # noqa: E402
    NMGEM_LIST_URL, NmgEMCollector)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

_PAGE = "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/t20251121_123.html"
_DIR = "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/"


# --------------------------------------------------------------------------
# A 组：判据本身
# --------------------------------------------------------------------------


def test_同目录的图算内容图():
    assert is_page_image(_PAGE, _DIR + "W020251121650679042482_ORIGIN.jpg")


def test_以页面路径为前缀的图算内容图():
    """`sc_em` 形态：`…/b3eebb40….html` 配 `…/b3eebb40…/images/0001.jpg`
    ——**不同目录**，但图片在页面路径之下。"""
    page = "https://yjt.sc.gov.cn/scyjt/dcbg/2021/7/5/b3eebb4076634c3482006b829e5fb467.html"
    img = ("https://yjt.sc.gov.cn/scyjt/dcbg/2021/7/5/"
           "b3eebb4076634c3482006b829e5fb467/images/0001.jpg")
    assert is_page_image(page, img)


def test_跨目录的图不算内容图():
    """`tj_em` 的模板图形态：页面在 `/ZWGK6939/SGDCBG354/202608/`，
    图在 `/qdfy/tytb/202008/`。**命名与内容图一模一样**（同目录、时间戳连续
    递增的 TRS 资源），唯一能分开的就是目录对不对。"""
    page = "https://yjgl.tj.gov.cn/ZWGK6939/SGDCBG354/202608/t20260821_7356773.html"
    img = "https://yjgl.tj.gov.cn/qdfy/tytb/202008/W020200824411649201603.png"
    assert not is_page_image(page, img)


def test_换域名的图不算内容图():
    assert not is_page_image(_PAGE, "https://cdn.example.com/202511/a.jpg")


def test_同目录但不同站也不算():
    assert not is_page_image(_PAGE, "https://other.gov.cn/zwgk/zdxx/sgxxccpg/202511/a.jpg")


# ── A2 组：第三条判据「图片名主干 == 页面 ID」（第二十一轮，gd_em 逼出来的）──
#
# `gd_em` 的内容图集中放在 `/img/0/{a}/{b}/` 下，与页面**不同目录**，「同目录」
# 在它身上 152 张内容图漏 152 张。而图名主干恰好就是页面 ID。

_GD_PAGE = "https://yjgl.gd.gov.cn/gk/zdlyxxgk/sgdcbg/content/post_3554784.html"


def test_图名等于页面ID时算内容图():
    """`gd_em` 形态：跨目录，但 `/img/0/670/670670/3554784.jpg` 的主干
    就是页面的 `post_3554784` 里的 ID。"""
    assert is_page_image(_GD_PAGE, "https://yjgl.gd.gov.cn/img/0/670/670670/3554784.jpg")


def test_页面末段带前缀时剥掉前缀再比():
    """`post_3554784` 里的 `post_` 不是 ID 的一部分，得剥。TRS 的
    `t20260821_7356773` 同理——取最后一个下划线之后。"""
    assert page_article_id(_GD_PAGE) == "3554784"
    assert page_article_id("https://x.gov.cn/202608/t20260821_7356773.html") == "7356773"
    assert page_article_id("https://x.gov.cn/dw/1234.html") == "1234"


def test_TRS的资源图名不等于页面ID():
    """第三条判据在 TRS 系上**不该命中**——那边靠「同目录」。这条钉的是
    「新判据不会把 TRS 的资源图误认成页面自己的图」的判据侧。"""
    page = "https://yjgl.tj.gov.cn/ZWGK6939/SGDCBG354/202608/t20260821_7356773.html"
    assert not is_page_image(page, "https://yjgl.tj.gov.cn/qdfy/tytb/202008/"
                                   "W020200824411649201603.png")


def test_图名只是以页面ID结尾不算():
    """**反例，比正例更要紧。** `logo_3554784.png` 的主干以 `3554784` 结尾，
    但如果图片那侧也剥前缀，它就会被误纳成内容图。所以图片名**只做全等**。"""
    assert not is_page_image(_GD_PAGE, "https://yjgl.gd.gov.cn/img/0/1/2/logo_3554784.png")
    assert not is_page_image(_GD_PAGE, "https://yjgl.gd.gov.cn/img/0/1/2/x3554784.png")


def test_换域名时图名相同也不算():
    assert not is_page_image(_GD_PAGE, "https://cdn.example.com/img/3554784.jpg")


def test_图名带后缀时的已知漏判():
    """明知会漏、且选择不修：`3554784_ORIGIN.jpg` 的主干不等于页面 ID。

    修它就得让图片那侧也剥后缀，而那正是上一条反例会失守的地方。
    方向是**宁可漏**——漏一张图不等于整条记录丢失；误纳一张模板图则是
    给下游塞噪音。这条测试是留给将来遇到「某源图片全带后缀」时的路标：
    到时候改这里，别改判据的方向。
    """
    assert not is_page_image(_GD_PAGE, "https://yjgl.gd.gov.cn/img/0/1/2/3554784_ORIGIN.jpg")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ── A3 组：DOM 层判据「附件行里的图标」（第二十六轮，sc_em 逼出来的）──
#
# `sc_em` 的附件图标**就放在文章自己的资源目录里**——和它 24 张扫描页同一个
# `images/` 目录。URL 层判据对两者一视同仁，接不住。实测原文（2026-10-01）：
#
#     <p>附件：<img border="0" src="999dab…/images/pdf.gif">
#             <a href="999dab…/files/…报告.pdf" target="_blank">报告名称</a></p>
#
# 这张图标会一路带下去：`image_urls` → asset 表一行 → 阶段②的 OCR 队列。

_SC_PAGE = ("https://yjt.sc.gov.cn/scyjt/dcbg/2022/4/8/"
            "999dab979c9746c48c7f9a4d087cb906.shtml")
_SC_ICON = ("https://yjt.sc.gov.cn/scyjt/dcbg/2022/4/8/"
            "999dab979c9746c48c7f9a4d087cb906/images/pdf.gif")


def test_附件行的图标不算内容图():
    """**正例。** 图标本身完全满足 URL 层判据（以页面路径为前缀），
    所以先钉住这一点——否则这条测试可能只是「URL 判据碰巧没放行」。"""
    assert is_page_image(_SC_PAGE, _SC_ICON)
    s = _soup('<p>附件：<img border="0" src="./999dab979c9746c48c7f9a4d087cb906/'
              'images/pdf.gif"><a href="./999dab979c9746c48c7f9a4d087cb906/'
              'files/报告.pdf" target="_blank">报告名称</a></p>')
    assert extract_page_images(_SC_PAGE, s) == []


def test_判据自检_拿掉那个兄弟链接图标就回来了():
    """**这条测试比正例更要紧。**

    正例单独看有歧义：图标没被抽出来，可能是新判据挡的，也可能是 URL 判据
    本来就没放行。把兄弟 `<a>` 一拿掉，同一张图立刻回来——这才是
    「是 `is_attachment_icon` 在起作用」的证明。"""
    ico = './999dab979c9746c48c7f9a4d087cb906/images/pdf.gif'
    assert extract_page_images(_SC_PAGE, _soup(f'<p>附件：<img src="{ico}"></p>')) \
        == [_SC_ICON]


def test_内容图与附件链接同页但不同段落不误伤():
    """**反例，比正例更要紧。** 页面上有附件链接不代表内容图是图标——
    两者得结构上挨着才算。"""
    s = _soup('<p><img src="./999dab979c9746c48c7f9a4d087cb906/images/0001.jpg"></p>'
              '<p><a href="./999dab979c9746c48c7f9a4d087cb906/files/a.pdf">'
              '报告</a></p>')
    assert extract_page_images(_SC_PAGE, s) == [_SC_ICON.replace("pdf.gif", "0001.jpg")]


def test_被附件链接包着的图也算图标():
    """第二种形态：`<a href="…pdf"><img …></a>`。"""
    s = _soup('<a href="./files/a.pdf"><img src="./999dab979c9746c48c7f9a4d087cb906/'
              'images/pdf.gif"></a>')
    assert extract_page_images(_SC_PAGE, s) == []


def test_紧挨着的是普通链接就不算图标():
    """判据认的是**附件**链接，不是「图片旁边有个 `<a>`」。"""
    s = _soup('<p><img src="./999dab979c9746c48c7f9a4d087cb906/images/0001.jpg">'
              '<a href="./other.html">相关新闻</a></p>')
    assert extract_page_images(_SC_PAGE, s) == [_SC_ICON.replace("pdf.gif", "0001.jpg")]


def test_判据本身对图标与内容图分得开():
    """直接调 `is_attachment_icon`——上面几条测的是 `extract_page_images`
    的行为，这条把判据函数本身也钉一下。"""
    icon = _soup('<p><img src="./a/pdf.gif"><a href="./b/a.pdf">报告</a></p>'
                 ).find("img")
    plain = _soup('<p><img src="./a/0001.jpg"></p>').find("img")
    assert is_attachment_icon(icon) is True
    assert is_attachment_icon(plain) is False


def test_已知代价_内容图紧挨着附件链接会被一起跳过():
    """**这条钉的是一个代价，不是一条特性。**

    `sc_em` 的图标与附件链接就是紧挨着排的，判据只能靠「紧邻」来认它，
    于是「内容图恰好也紧挨着附件链接」这种排布会被一并跳过。

    没有更好的办法：图标与内容图在 URL 上**完全同类**（同在文章资源目录），
    只能看结构；而任何比「紧邻」更松的口径都会误伤——试过「直接父元素内有
    附件链接」，`nmg_em` 把内容图直接放在 `<body>` 下，于是全页的图都被
    判成图标了。

    方向仍是**宁可漏**：漏一张图标只是多 OCR 一个 200 字节的 GIF，
    误伤一张内容图是丢掉一整页报告。实测 8 个 TRS 源 × 3 个详情页，
    判据放行的 14 张图里只有 `sc_em` 那 1 张命中，真实样本里没有出现过
    「内容图紧挨附件链接」。留着这条是为了将来真遇到时，改这里，别改方向。
    """
    s = _soup('<p><img src="./999dab979c9746c48c7f9a4d087cb906/images/0001.jpg">'
              '<a href="./999dab979c9746c48c7f9a4d087cb906/files/a.pdf">报告</a></p>')
    assert extract_page_images(_SC_PAGE, s) == []


def test_图标不占上限额度():
    """判据放在 `is_page_image` **之前**，图标连判据都不进——所以它不挤
    `max_n`。挡 3 张图标时，`max_n=2` 仍该拿到 2 张内容图。"""
    s = _soup("".join('<p><img src="./a/pdf.gif"><a href="./b/x.pdf">x</a></p>'
                      for _ in range(3))
              + '<img src="./999dab979c9746c48c7f9a4d087cb906/images/0001.jpg">'
              + '<img src="./999dab979c9746c48c7f9a4d087cb906/images/0002.jpg">')
    got = extract_page_images(_SC_PAGE, s, max_n=2)
    assert got == [_SC_ICON.replace("pdf.gif", "0001.jpg"),
                   _SC_ICON.replace("pdf.gif", "0002.jpg")]


def test_图片与附件链接隔着大段正文时不算图标():
    """**这条钉的是判据实现里踩过的坑，比上面所有正例都值钱。**

    最初的实现用 `tag.find_next_sibling()`——它只找 Tag、自动跳过文本节点，
    看起来正合适。跑全量测试时 `gd_em` 的两条挂了，才发现它会**跨过整段正文**
    找到文末的附件链接：

        <div class="article_con">
          <img class="nfw-cms-img" src="…/1.jpg">   ← 内容图
          ……正文正文正文（几千字）……
          <a href="…/3.pdf">附件：评估报告</a>
        </div>

    于是整页的内容图被判成图标。改成只看 `next_sibling` 且只允许中间隔**纯空白**。
    将来谁想把这里「简化」回 `find_next_sibling()`，这条会拦住。
    """
    body = "正文正文正文。" * 400
    s = _soup('<div class="article_con">'
              '<img class="nfw-cms-img" '
              'src="./999dab979c9746c48c7f9a4d087cb906/images/0001.jpg">'
              f'{body}'
              '<a href="./999dab979c9746c48c7f9a4d087cb906/files/a.pdf">'
              '附件：评估报告</a></div>')
    assert extract_page_images(_SC_PAGE, s) == [
        _SC_ICON.replace("pdf.gif", "0001.jpg")]


def test_中间只有一个空格仍算紧邻():
    """留纯空白的中断：站点把 `<img …><a …>` 写成 `<img …> <a …>` 只是排版
    差异，不该因此换结论。"""
    s = _soup('<p>附件：<img src="./999dab979c9746c48c7f9a4d087cb906/images/pdf.gif">'
              ' <a href="./999dab979c9746c48c7f9a4d087cb906/files/a.pdf">报告</a></p>')
    assert extract_page_images(_SC_PAGE, s) == []


# --------------------------------------------------------------------------
# B 组：从 DOM 里抽
# --------------------------------------------------------------------------


def test_抽出同目录图并还原成绝对URL():
    s = _soup('<img src="./W020251121650679042482_ORIGIN.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W020251121650679042482_ORIGIN.jpg"]


def test_懒加载的data_src也认():
    """只认 `src` 会拿到占位图。"""
    s = _soup('<img src="data:image/gif;base64,R0lGOD" data-src="./W0202511211.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W0202511211.jpg"]


def test_source标签也收():
    s = _soup('<source src="./W0202511212.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W0202511212.jpg"]


def test_模板图混在里面时只取内容图():
    s = _soup('<img src="/material/2021_static/logo.png">'
              '<img src="./W0202511213.jpg">'
              '<img src="https://yjglt.nmg.gov.cn/images/top/banner.png">')
    assert extract_page_images(_PAGE, s) == [_DIR + "W0202511213.jpg"]


def test_非图片扩展名跳过():
    s = _soup('<img src="./a.pdf"><img src="./b.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "b.jpg"]


def test_内联base64跳过():
    s = _soup('<img src="data:image/png;base64,iVBORw0KGgo">')
    assert extract_page_images(_PAGE, s) == []


def test_同一张图去重():
    s = _soup('<img src="./a.jpg"><img src="./a.jpg"><img src="a.jpg">')
    assert extract_page_images(_PAGE, s) == [_DIR + "a.jpg"]


def test_上限为0时不抽取():
    s = _soup('<img src="./a.jpg">')
    assert extract_page_images(_PAGE, s, max_n=0) == []


def test_超过上限时截断并保序():
    s = _soup("".join(f'<img src="./{i:03d}.jpg">' for i in range(8)))
    got = extract_page_images(_PAGE, s, max_n=3)
    assert got == [_DIR + f"{i:03d}.jpg" for i in range(3)]


def test_默认上限容得下扫描件报告():
    """默认上限是 200，不是 50。

    原为 50。端到端跑 `gd_em` 时有一条记录**正好落 50 张**——正好等于上限，
    那不是「刚好 50 张」，是被截断的。单抓那一页数出来是 **59 张**，而且
    正文 0 字（纯图片型报告，缺页就是缺内容）。

    59 是实测形态里最长的一页，所以这条钉住的是「默认值得容得下它」，
    而不是「59 这个数有什么特别」。
    """
    s = _soup("".join(f'<img src="./{i:04d}.jpg">' for i in range(59)))
    assert len(extract_page_images(_PAGE, s)) == 59


def test_带查询串的图片URL归一后不重复():
    s = _soup('<img src="./a.jpg?x=1"><img src="./a.jpg?x=2">')
    assert extract_page_images(_PAGE, s) == [_DIR + "a.jpg"]


# --------------------------------------------------------------------------
# C 组：nmg_em 的接入（此前它的图片一张都不登记）
# --------------------------------------------------------------------------
#
# `nmg_em` 实测 121 张内容图，是全项目最多的一块。它不继承 TRS 基类，所以
# 第十九轮在基类里做的落地**碰不到它**——这一组就是盯这件事。

_LINK = "./202511/t20251121_123.html"
_DETAIL_URL = NMGEM_LIST_URL + "202511/t20251121_123.html"


def _list_html() -> str:
    return ('<html><body>'
            f'<div class="sn-item"><a href="{_LINK}" '
            'title="某地高处坠落事故调查报告">某地高处坠落事故调查报告</a>'
            '<span class="sn-date">2025-11-21</span></div>'
            '</body></html>')


def _detail_html(pare: str | None = None, pdf: bool = False,
                 script_attach: str = "") -> str:
    """`pare=None` → 造一个**没有正文容器**的页面（走退化到 `body` 那条路）。

    `div.article_content` 是**故意放进去的**：实测那个站上有它，也带 `content`
    类，且在 `div#pare` 之前出现——`soup.find("div", class_="content")` 拿到
    的是它（9 字），不是正文区。只有 `id="pare"` 是对的，这一条钉住这件事。

    `script_attach` 给扩展名（如 `"pdf"`）→ 造「附件链接藏在 `<script>` 里」
    的那个形态，`<a href>` 一个都不给。
    """
    box = f'<div id="pare" class="content">{pare}</div>' if pare is not None else ""
    att = '<a href="./W020251121650679042482.pdf">附件</a>' if pdf else ""
    sc = (f'<script>var fujian = "./W020251121650679042482.{script_attach}";'
          f'var file = \'<a href="./W020251121650679042482.{script_attach}">报告</a>\';'
          '</script>' if script_attach else "")
    return ('<html><head>'
            '<meta name="ArticleTitle" content=\'某地高处坠落事故调查报告\'/>'
            '<meta name="PubDate" content=\'2025-11-21 10:00:00\'/>'
            '</head><body>'
            '<div class="article_content">站点导航</div>'
            '<div class="con">正文正文正文</div>'
            f'{box}'
            f'{att}'
            f'{sc}'
            '<img src="/material/2021_static/logo.png">'
            '<img src="./W020251121650679042482_ORIGIN.jpg">'
            '<img src="./W020251121650679093210_ORIGIN.jpg">'
            '</body></html>')


def _nmg(tmp_path: Path, image_max: int = 50, pare: str | None = None,
         pdf: bool = False, script_attach: str = "") -> NmgEMCollector:
    c = NmgEMCollector(delay=0, enable_classify=False,
                       data_dir=tmp_path / "nmg", image_max=image_max)
    c._fetch = lambda url: (_list_html() if url == NMGEM_LIST_URL
                            else _detail_html(pare, pdf, script_attach))
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    return c


def _close(c: NmgEMCollector) -> None:
    client = getattr(c, "_client", None)
    if client is not None:
        client.close()


def test_nmg_em_详情页抽出内容图(tmp_path):
    c = _nmg(tmp_path)
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["image_urls"] == [
            NMGEM_LIST_URL + "202511/W020251121650679042482_ORIGIN.jpg",
            NMGEM_LIST_URL + "202511/W020251121650679093210_ORIGIN.jpg",
        ]
    finally:
        _close(c)


def test_nmg_em_产出里带上image_urls(tmp_path):
    """这个键此前根本不存在，`raw_accident_assets` 里 `asset_type='image'`
    因此一直是 0 行。"""
    c = _nmg(tmp_path)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert len(item["image_urls"]) == 2
        assert all("_ORIGIN.jpg" in u for u in item["image_urls"])
    finally:
        _close(c)


def test_nmg_em_模板图不登记(tmp_path):
    c = _nmg(tmp_path)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert not any("/material/" in u for u in item["image_urls"])
    finally:
        _close(c)


def test_nmg_em_上限为0时不出图(tmp_path):
    c = _nmg(tmp_path, image_max=0)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["image_urls"] == []
    finally:
        _close(c)


def test_nmg_em_的图能落成image类型asset(tmp_path):
    from construction_agent.collector.executor import _row_from_raw_item
    c = _nmg(tmp_path)
    try:
        c.collect()
        raw = c.raw_store.get_all()[0]
        row = _row_from_raw_item(_as_raw_item(raw), "nmg_em", "task1")
        assert [a["asset_type"] for a in row["_assets"]] == ["image", "image"]
    finally:
        _close(c)


# ── C2 组：正文口径与 `needs_ocr`（第二十二轮）──
#
# 这个站的模板文字实测**恒定 1,225 字**：图片型页 `body` 全文 1,313–1,359 字，
# 去掉模板只剩 **88–134 字**。取 `body` 的后果不是「多读了几个字」，而是
# 「一条报告本体全在图里的记录，从外观看是条有 1,300 字正文的普通记录」——
# 缺陷 G 正是这个。正文区是 `div#pare`（图片型 3 字 / 文字型 18,724 字）。

_PAREN = ("某地高处坠落事故调查报告\n一、事故经过\n"
          "2025年11月21日，某工地发生高处坠落事故，造成一人死亡。")


def test_正文取自pare容器而不是body(tmp_path):
    c = _nmg(tmp_path, pare=_PAREN)
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["text"] == _PAREN
        assert d["text_source"] == "pare"
    finally:
        _close(c)


def test_正文不含body里的导航与页脚(tmp_path):
    """钉「**不能**取 `body`」。fixture 里 `div.article_content`（站点导航）
    与 `div.con`（正文正文正文）都在 `div#pare` 之外，取 pare 就看不到它们。"""
    c = _nmg(tmp_path, pare=_PAREN)
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert "站点导航" not in d["text"]
        assert "正文正文正文" not in d["text"]
    finally:
        _close(c)


def test_没有pare容器时退化到body并标记来源(tmp_path):
    """站点改版时 `div#pare` 消失，退化到旧口径。退化**必须可见**——
    `text_source` 一路传到产出里，否则「正文突然多了 1,200 字」无从解释。"""
    c = _nmg(tmp_path)
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["text_source"] == "body"
        assert "站点导航" in d["text"]
    finally:
        _close(c)


def test_图片型条目标needs_ocr(tmp_path):
    """正文区空 + 挂了内容图 = 报告本体在图里。

    改前这个源 `needs_ocr` 恒为 False（判据是 `bool(pdf_url) and not
    detail_text`，而 detail_text 是 body 全文，模板文字永远把它填满）。
    `classify_v2` 拿 3 字的正文只会说「置信度低」，说不出「正文在图片里」，
    所以进审核队列时把理由也一并写准。"""
    c = _nmg(tmp_path, pare="")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is True
        assert item["text_source"] == "pare"
        assert "[图片格式报告，需 OCR 提取文字]" in item["content"]
        assert item["classification_reason"] == "图片格式报告，需 OCR 提取文字"
    finally:
        _close(c)


def test_文字型条目不标needs_ocr(tmp_path):
    """有正文的页不该被误标——`needs_ocr` 不是「有图就标」。"""
    c = _nmg(tmp_path, pare=_PAREN * 20)
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is False
        assert "[图片格式报告，需 OCR 提取文字]" not in item["content"]
    finally:
        _close(c)


def test_只有附件没有正文时标needs_ocr(tmp_path):
    """PDF 分支。`_process_pdf` 换掉——不换就要联网下真 PDF。省级站的常态是
    页面上只有一行「点击链接查看报告详情：」，报告本体在附件里。"""
    c = _nmg(tmp_path, pare="", pdf=True)
    c._process_pdf = lambda url, title, pub: ("", "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["needs_ocr"] is True
        assert item["classification_reason"] in (
            "附件未提到正文，待下载处理", "图片格式报告，需 OCR 提取文字")
    finally:
        _close(c)


# ── C3 组：附件链接藏在 `<script>` 里（第二十二轮实测撞出来的）──
#
# 端到端跑完那一轮，3 条落库记录里有 1 条 `needs_ocr=False`——查下去发现
# 它的附件链接根本不在 DOM 里：页面把附件写成 JS 变量，`<a>` 是运行时拼的。
# 8 页实测：`<a href>` 带 `.pdf` 的 **0 条**，这个形态 1 条。

_SCRIPT_PDF = NMGEM_LIST_URL + "202511/W020251121650679042482.pdf"


def test_附件链接藏在script里也要抽到(tmp_path):
    c = _nmg(tmp_path, pare="附件：", script_attach="pdf")
    c._process_pdf = lambda url, title, pub: ("", "")
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["pdf_url"] == _SCRIPT_PDF
    finally:
        _close(c)


def test_script附件也能把正文提到(tmp_path):
    """抽到 `pdf_url` 之后，`_process_pdf` 提出的正文才算真正文——
    这条是端到端那一步的单测替身，`_process_pdf` 换成假的不去联网，
    但走的是 `collect()` 的完整分支。"""
    c = _nmg(tmp_path, pare="附件：", script_attach="pdf")
    c._process_pdf = lambda url, title, pub: ("某地高处坠落事故调查报告全文" * 20, "")
    try:
        c.collect()
        item = c.raw_store.get_all()[0]
        assert item["pdf_urls"] == [_SCRIPT_PDF]
        assert item["needs_ocr"] is False
        assert "某地高处坠落事故调查报告全文" in item["content"]
    finally:
        _close(c)


def test_非PDF的script附件不当成pdf_url(tmp_path):
    """模板的扩展名白名单还有 `.doc/.docx/.xls/.rar/.zip/.txt`，但
    `_process_pdf` 只解得了 PDF。**不静默吞掉**——它会被记 warning，
    只是不落 `pdf_url`。"""
    c = _nmg(tmp_path, pare="附件：", script_attach="docx")
    c._process_pdf = lambda url, title, pub: ("", "")
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["pdf_url"] == ""
    finally:
        _close(c)


def test_真a标签优先于script变量(tmp_path):
    """两者同时存在时取 `<a>`。顺序反过来的话，某天页面里的 JS 变量是旧版本
    就会取错地址。"""
    c = _nmg(tmp_path, pare="附件：", pdf=True, script_attach="docx")
    c._process_pdf = lambda url, title, pub: ("", "")
    try:
        d = c._crawl_detail(_DETAIL_URL)
        assert d["pdf_url"] == NMGEM_LIST_URL + "202511/W020251121650679042482.pdf"
    finally:
        _close(c)


def _as_raw_item(d: dict):
    """`RawAccidentStore` 的 dict → `RawAccidentText`。

    通路 A 与通路 B 的字段名不同（正文一个叫 `content` 一个叫 `raw_text`），
    `nmg_em` 走的是 store 通路，所以测试要自己转这道手。
    """
    from construction_agent.collector.base import RawAccidentText
    return RawAccidentText(
        source=d.get("source_name", "nmg_em"),
        title=d.get("title", ""),
        raw_text=d.get("content", ""),
        source_url=d.get("source_url", ""),
        fetch_time="",
        extracted_date=None,
        extra={"image_urls": d.get("image_urls") or [],
               "pdf_urls": d.get("pdf_urls") or []},
    )
