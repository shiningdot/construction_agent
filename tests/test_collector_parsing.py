"""采集器「解析之前」的两处沉默失效，加上两类只能静态查的契约。

## 为什么有这个文件

2026-10-01 的 24 源动态试跑查出四个缺陷，共同点是**不跑就看不出来**，
而跑起来又都显示成「这个源没数据」——与「该站确实没有事故通报」长得一模一样。

| 缺陷 | 表面现象 | 本文件哪一组 |
|---|---|---|
| `js_em`：列表条目藏在 `<script type="text/xml">` 数据岛里 | 0 条 | 第一组 |
| `sc_em`：链接正则抄的是另一种 CMS 的格式 | 0 条 | 第二组 |
| `RawAccidentText` 收到未声明的 `classification` 关键字 | 当场崩（还算好的） | 第三组 |
| `_extract_pdf_text` 三份副本、漏了三个调它的模块 | `NameError` 被 `except Exception` 吞掉 | 第四组 |

后两个属于「schema 有没有对齐」级别的错误，**静态就能查**。写在这里是为了
让它们不必再靠「碰巧有人跑了一次」来发现。

## 不测什么

**不发任何网络请求。** 站点是否还活着、选择器是否还匹配实际页面，只有
`scripts/check_collector_health.py` 能回答（它真发请求、真读响应）。这里测的是
「给定一份 HTML / 一段源码，解析与契约对不对」。
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import re
from pathlib import Path
from urllib.parse import urljoin

import pytest
from bs4 import BeautifulSoup

from construction_agent.collector.base import RawAccidentText
from construction_agent.collector.sources.cq_em import CqEMCollector
from construction_agent.collector.sources.js_em import JsEMCollector
from construction_agent.collector.sources.sc_em import ScEMCollector
from construction_agent.collector.sources.trs_cms_base import (
    _absolute,
    _date_from_url,
    _unwrap_data_islands,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES_DIR = ROOT / "construction_agent" / "collector" / "sources"

# 数据岛：`js_em` 列表页的真实结构（截自 2026-10-01 的响应）
ISLAND_HTML = (
    '<div id="397082"><script type="text/xml"><datastore><recordset><record><![CDATA['
    '<li class="cf"><a title="x" href="/art/2019/4/18/art_81395_8732508.html">'
    '省安全生产委员会办公室关于近期两起建筑施工较大事故情况的通报</a>'
    "<span>2019-04-18</span></li>"
    "]]></record></recordset></script></div>"
)


def _links(html: str) -> list[str]:
    return [a["href"] for a in BeautifulSoup(html, "html.parser").find_all("a", href=True)]


# ═══════════════════════════════════════════════════════════════════
# 第一组：数据岛
# ═══════════════════════════════════════════════════════════════════

def test_数据岛里的链接_剥离前一条都看不见():
    """这是缺陷本身，不是断言写得奇怪：`html.parser` 对 `<script>` 的内容一律当文本。"""
    assert _links(ISLAND_HTML) == []


@pytest.mark.parametrize(
    "opener",
    [
        '<script type="text/xml">',          # 实测站点的写法
        "<script  type='text/xml'>",          # 单引号
        '<SCRIPT TYPE="TEXT/XML">',           # 大写
        '<script type="text/xml; charset=utf-8">',  # 带参数后缀
    ],
    ids=["实测写法", "单引号", "大写", "带charset"],
)
def test_数据岛剥离_几种写法都要认(opener):
    """后三种没有实测样本，是**防御性**放宽。

    理由：这几种写法一旦认不出，表现与「这个源没数据」完全一样。宁可多认几种，
    也不要再出现一次「沉默失效」。
    """
    html = ISLAND_HTML.replace('<script type="text/xml">', opener)
    assert _links(_unwrap_data_islands(html)) == ["/art/2019/4/18/art_81395_8732508.html"]


def test_没有数据岛的页面_逐字不变():
    """其余 11 个 TRS 源走的就是这条路，不许被顺手改动。"""
    plain = '<div><ul><li><a href="/art/2020/1/1/art_1_2.html">普通列表条目</a></li></ul></div>'
    assert _unwrap_data_islands(plain) == plain


def test_普通script不动():
    """只认 `type="text/xml"`。JS 字符串里的 `<a>` 仍在 DOM 之外，不该被拽进来。"""
    html = (
        "<div><script type=\"text/javascript\">var a = \"<a href='/art/9/9/9/art_9_9.html'>x</a>\";"
        '</script><a href="/art/2/2/2/art_2_2.html">真的链接</a></div>'
    )
    assert _unwrap_data_islands(html) == html


def test_js_em_能解析出列表条目(tmp_path):
    """端到端一点：走真的 `_parse_list`，而不是只测那个辅助函数。"""
    collector = JsEMCollector(data_dir=tmp_path)
    records = collector._parse_list(ISLAND_HTML)
    assert [r["url"] for r in records] == [
        "https://yjglt.jiangsu.gov.cn/art/2019/4/18/art_81395_8732508.html"
    ]
    assert records[0]["date"] == "2019-04-18"


@pytest.mark.parametrize(
    "href, expected",
    [
        ("/art/2019/4/18/art_81395_8732508.html", "2019-04-18"),
        ("/scyjt/dcbg/2023/8/14/0123456789abcdef.shtml", "2023-08-14"),
        ("/art/2020/12/31/art_1_2.html", "2020-12-31"),
        ("/20230101/index.html", "2023-01-01"),
        ("/art/x/art_81395_8732508.html", ""),   # 没有日期
        ("/art/1234/56/78/art_1.html", ""),      # 抠出了三组数，但范围不对
    ],
    ids=["月日不补零", "另一站的不补零", "补零", "连续八位", "无日期", "范围外"],
)
def test_从URL抠日期(href, expected):
    """原先只写了要求补零的正则，`/art/2019/4/18/…` 一条都匹配不上，`date` 恒空。

    空值不报错，看起来就像「这个站列表页不写日期」——恒空的判据。
    """
    assert _date_from_url(href) == expected


# ═══════════════════════════════════════════════════════════════════
# 第二组：`sc_em` 的链接正则
# ═══════════════════════════════════════════════════════════════════

def test_sc_em_正则认得本站详情页():
    pattern = ScEMCollector(data_dir=Path("/tmp")).cfg.list_link_pattern
    assert re.search(pattern, "/scyjt/dcbg/2023/8/14/0123456789abcdef0123456789abcdef.shtml")
    assert re.search(pattern, "/scyjt/dcbg/2024/12/3/abcdef0123456789.shtml")  # 月/日不补零


@pytest.mark.parametrize(
    "href",
    [
        "/scyjt/dcbg/2023/8/14/0123456789abcdef0123456789abcdef/files/abc.pdf",  # 无详情解析
        "/scyjt/dcbg/other.shtml",                                               # 无日期目录
        "/art/2019/4/18/art_81395_8732508.html",                                 # 别的 CMS
    ],
)
def test_sc_em_正则不收别的形状(href):
    """PDF 直链故意不收：它没有配套的详情解析，收进来只会多一条「有标题、无正文」的记录。"""
    pattern = ScEMCollector(data_dir=Path("/tmp")).cfg.list_link_pattern
    assert not re.search(pattern, href)


# ═══════════════════════════════════════════════════════════════════
# 第三组：列表页里的相对链接（四个源「正文 0 字」的根因）
#
# `cq_em` / `shaanxi_em` / `sx_em` / `tj_em` 的详情页**全是 404**。列表页正常、
# 条目正常解析出十几条、标题都齐全，只有正文一个字都没有——健康检查里它们
# 显示的是「有数据」，是四个源里最容易被放过的那一类。
#
# 根因：TRS 把详情链接写成相对当前目录的 `./202609/t20260918_…html`，而旧拼法
# 是 `base_url.rstrip("/") + "/" + href.lstrip("/")`——`lstrip("/")` **不去掉
# 开头的 `./`**，拼出 `https://yjj.cq.gov.cn/./202609/…`，服务器对 `/./` 一律 404。
# 三种拼法的实测（2026-10-01，`cq_em` 第一条）见 `_absolute()` 的 docstring。
# ═══════════════════════════════════════════════════════════════════

CQ_LIST_URL = "https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/"

REL_LIST_HTML = (
    "<ul>"
    '<li><a href="./202609/t20260918_16096386.html">重庆南川“3·12”一般森林火灾事故调查报告</a></li>'
    '<li><a href="./202609/t20260918_16096374.html">重庆涪陵“2·10”一般坍塌事故调查报告</a></li>'
    "</ul>"
)


@pytest.fixture(scope="module")
def cq_collector(tmp_path_factory):
    """真构造一个采集器，但不发任何请求——`_parse_list` 只吃 HTML 字符串。"""
    return CqEMCollector(data_dir=tmp_path_factory.mktemp("cq_em"))


def test_相对链接要还原成绝对URL(cq_collector):
    records = cq_collector._parse_list(REL_LIST_HTML, CQ_LIST_URL)
    assert [r["url"] for r in records] == [
        "https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/"
        "202609/t20260918_16096386.html",
        "https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/"
        "202609/t20260918_16096374.html",
    ]


def test_拼出来的URL里不许出现_斜杠点_或_斜杠点点(cq_collector):
    """`/./` 是旧拼法的指纹，服务器对它的应答一律 404。

    这条比上一条更值得留：它不绑定具体站点地址，**任何一个源**拼出 `/./`、
    `/../` 都说明没走 `urljoin`。
    """
    for r in cq_collector._parse_list(REL_LIST_HTML, CQ_LIST_URL):
        assert "/./" not in r["url"], r["url"]
        assert "/../" not in r["url"], r["url"]


@pytest.mark.parametrize(
    "href, expected",
    [
        ("./202609/t1.html", "https://yjj.cq.gov.cn/a/b/c/202609/t1.html"),
        ("../202609/t1.html", "https://yjj.cq.gov.cn/a/b/202609/t1.html"),
        ("/zwgk_230/x.html", "https://yjj.cq.gov.cn/zwgk_230/x.html"),
        ("//other.example.com/x.html", "https://other.example.com/x.html"),
        ("https://other.example.com/x.html", "https://other.example.com/x.html"),
    ],
    ids=["相对当前目录", "相对上一级", "站内绝对路径", "协议相对", "已是绝对URL"],
)
def test_五种href写法都还原得对(href, expected):
    """后三种是旧拼法处理不了的：`//host/x` 会被 `lstrip("/")` 啃掉两个斜杠。"""
    assert _absolute("https://yjj.cq.gov.cn/a/b/c/index.html", href) == expected


def test_详情页里的PDF附件也要还原成绝对URL(cq_collector, monkeypatch):
    """同一段错误逻辑的**第二个现场**：附件链接也写作 `./P0202…pdf`。

    旧拼法把它接到站点根上，拼出 `https://yjt.shanxi.gov.cn/P0202…pdf`，
    **整条路径都丢了**，下载必然 404。而省级源的正文往往只存在于附件里：
    `sx_em` 的详情页 HTML 一个字正文都没有，全在 PDF 中——PDF 取不到，
    `body.get_text()` 就只剩页脚导航，分类器再从页脚里读到
    「国家矿山安全监察局」，**高置信度判成非建筑施工事故**。
    一个 URL 拼接错误，最后长成了「这个源没有建筑事故」。

    这里 monkeypatch 掉 `_fetch`，不发请求。
    """
    detail_html = (
        "<html><head><title>某坍塌事故调查报告</title></head><body>"
        '<div class="box">运城永济市…较大坍塌事故防范和整改措施落实情况评估报告</div>'
        '<a href="./P020240905567173081196.pdf">…评估报告.pdf</a>'
        "</body></html>"
    )
    monkeypatch.setattr(cq_collector, "_fetch", lambda url: detail_html)
    detail = cq_collector._crawl_detail(
        "https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/"
        "202409/t20240905_9648052.html"
    )
    assert detail["pdf_url"] == (
        "https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/202409/"
        "P020240905567173081196.pdf"
    )


# ═══════════════════════════════════════════════════════════════════
# 第六组：分页
#
# 2026-10-01 之前，两个配了分页的源**从头到尾只抓了第一页**：
# `shaanxi_em` 1 页 20 条（真实 20 页 392 条）、`sh_em` 1 页 15 条（真实 7 页 94 条）。
# 两处叠加的缺陷，每一处单独看都「正常」：
#
# 1. `_build_page_url` 对 `index_{n}.html` 返回**裸文件名** `index_2.html`
#    （既含 `{n}` 又含 `index_`，命中前面那个分支）——没有协议头的相对路径。
# 2. `_discover_pages` 用 HEAD 判存在性，而 `shaanxi_em` **整站拒 HEAD**：
#    每页 HEAD 403、GET 200 且内容完整。这是「恒真的判据」——
#    403 被读成「没有下一页」，与「这个站只有一页」表现完全一致。
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def paging_collectors(tmp_path_factory):
    from construction_agent.collector.sources.sh_em import ShEMCollector
    from construction_agent.collector.sources.shaanxi_em import ShaanxiEMCollector

    return [
        cls(data_dir=tmp_path_factory.mktemp(cls.__name__))
        for cls in (ShEMCollector, ShaanxiEMCollector)
    ]


def test_分页URL必须是带协议的绝对地址(paging_collectors):
    """缺陷 1 的直接断言：旧写法返回 `index_2.html`，连协议头都没有。

    没协议头时 httpx 抛 `UnsupportedProtocol`，然后被 `_discover_pages` 的
    `except Exception` 吃掉——**异常被吞掉的那次，看起来和「没有下一页」一样**。
    """
    for c in paging_collectors:
        for n in (2, 3, 20):
            url = c._build_page_url(n)
            assert url.startswith(("http://", "https://")), f"{c.source_name()} 第{n}页: {url!r}"


def test_分页URL对文件型和目录型list_url都拼得对():
    """`list_url` 有两种形状，拼接只对其中一种成立，`urljoin` 两种都对。

    这条不绑具体站点：任何一个源把分页 URL 拼成 `…/index_1.html/index_2.html`
    都会红。
    """
    cases = [
        # 目录型：拼在目录下
        ("https://yjglj.sh.gov.cn/xxgk/zfxxgk/zcwj/sgdcbg/", "index_{n}.html",
         "https://yjglj.sh.gov.cn/xxgk/zfxxgk/zcwj/sgdcbg/index_2.html"),
        # 文件型：替换文件名，不能把 index_1.html 当成目录
        ("https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/index_1.html", "index_{n}.html",
         "https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/index_2.html"),
    ]
    for list_url, pattern, expected in cases:
        got = urljoin(list_url, pattern.format(n=2))
        assert got == expected
        assert "index_1.html/" not in got


def test_所有配了分页模式的源_拼出来都是绝对URL():
    """扫全部源文件，不绑站点。分页模式写在 `TrsSiteConfig(...)` 的关键字里，静态可读。"""
    problems = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "TrsSiteConfig"):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            pattern = getattr(kw.get("list_page_pattern"), "value", "")
            list_url = getattr(kw.get("list_url"), "value", "")
            if not pattern or not list_url:
                continue
            built = urljoin(list_url, pattern.format(n=2))
            if not built.startswith(("http://", "https://")):
                problems.append(f"{path.name}: list_url={list_url!r} pattern={pattern!r} → {built!r}")
    assert not problems, "分页 URL 不是绝对地址：\n" + "\n".join(problems)


def test_分页探测不许用HEAD():
    """缺陷 2 的静态护栏：`shaanxi_em` 每页 HEAD 都 403，GET 都 200。

    403 不是「页面不存在」的证据，只是「不给你 HEAD」。拿 HEAD 判存在性，
    这个源永远只有 1 页。这条测试盯住 `_discover_pages` 里不许出现 `.head(`。
    """
    tree = ast.parse((SOURCES_DIR / "trs_cms_base.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_discover_pages":
            heads = [n for n in ast.walk(node)
                     if isinstance(n, ast.Attribute) and n.attr == "head"]
            assert not heads, "`_discover_pages` 用了 HEAD 判分页存在性，遇拒 HEAD 的站会静默只剩一页"
            break
    else:
        pytest.fail("找不到 `_discover_pages`——测试失效了")


def test_探测过的列表页不再重复请求(paging_collectors):
    """`_discover_pages` 已经 GET 到正文，`collect()` 里的 `_fetch` 该直接取用。

    否则每个列表页发两遍请求——不算错，但没有理由。
    """
    c = paging_collectors[0]
    requested = []

    class _Resp:
        status_code = 200
        text = "<ul></ul>"

    class _Client:
        def get(self, url, timeout=None):
            requested.append(url)
            return _Resp()

    c._client = _Client()
    c.cfg = dataclasses.replace(c.cfg, list_page_max_probe=3)  # 只探 2 页，别等 20 次
    c._page_cache.clear()

    pages = c._discover_pages()
    assert len(pages) == 3
    before = len(requested)

    assert c._fetch(pages[1]) == "<ul></ul>"   # 第 2 页：走缓存
    assert len(requested) == before, "探测过的列表页又发了一次请求"
    assert c._fetch("https://example.com/never.json") is None or len(requested) > before


# ═══════════════════════════════════════════════════════════════════
# 第四、五组：只能静态查的契约
#
# 两组的共同点：错误发生在方法体内部，`import` 得动、契约也 "符合"，
# 只有真正执行到那一行才炸。第一组那类「跑一次就知道」的缺陷上一轮已经
# 由健康检查覆盖；这两类是它查不到的（真跑要等到 PDF 分支、要等到某条
# 记录走到构造那一步），所以用 AST 补上。
# ═══════════════════════════════════════════════════════════════════

def _source_files() -> list[Path]:
    files = sorted(SOURCES_DIR.glob("*.py"))
    assert files, f"没找到任何源文件：{SOURCES_DIR}"
    return files


def _trees() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p))) for p in _source_files()]


def _call_keywords(tree: ast.Module, func_name: str) -> list[tuple[int, list[str]]]:
    """收集 `func_name(...)` 每次调用的关键字参数名与行号。星号展开（`**kw`）跳过。"""
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            out.append((node.lineno, [k.arg for k in node.keywords if k.arg]))
    return out


def test_RawAccidentText_的关键字都在字段表里():
    """守的是什么：`RawAccidentText` 是 dataclass，`__init__` 不收未声明的关键字。

    2026-10-01 之前，`trs_cms_base.py` 把 `classification` /
    `classification_reason` / `classification_confidence` 平铺成关键字参数，
    12 个 TRS 源里凡是走到这一行的**全部当场 `TypeError`**。那三个键本来就该
    走 `extra`（`stores/__init__.py` 是按 extra 里的这几个键名读的）。

    静态可查的理由：字段表在 `dataclasses.fields()` 里摆着，调用点在 AST 里
    摆着，两边一比就出来——不必等某个源真的抓到一条。
    """
    field_names = {f.name for f in dataclasses.fields(RawAccidentText)}
    allowed = field_names | {"extra"}
    offenders = [
        (p.name, lineno, kw)
        for p, tree in _trees()
        for lineno, kws in _call_keywords(tree, "RawAccidentText")
        for kw in kws
        if kw not in allowed
    ]
    assert not offenders, (
        "以下调用点给 RawAccidentText 传了未声明的关键字，运行到该行必 TypeError。"
        f"扩展数据请放进 extra={{...}}：{offenders}"
    )


def test_PDF文本提取函数在每个调用它的模块里都解析得到():
    """守的是什么：`_extract_pdf_text` 被复制过三份，同时有三个模块在调它却没定义。

    漏掉的 `beijing_em` 冒到顶层报了 `NameError`，`ln_em` 和 `henan_em`
    则被自己方法里的 `except Exception` 吞进日志——**两种表现，同一个缺陷**。

    这条测试只做一件事：调用名在该模块里必须绑得上（import 或 def）。
    它查不到函数体对不对，但「名字根本不存在」这一类跑不掉。
    """
    builtin_names = set(dir(builtins))
    problems = []
    for path, tree in _trees():
        bound = set(builtin_names)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Import):
                bound.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                bound.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Assign):
                bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.endswith("extract_pdf_text")
                and node.func.id not in bound
            ):
                problems.append(f"{path.name}:{node.lineno} 调了 {node.func.id}()，但本模块没有绑定它")
    assert not problems, "PDF 文本提取的调用点与定义对不上：\n" + "\n".join(problems)
