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

import pytest
from bs4 import BeautifulSoup

from construction_agent.collector.base import RawAccidentText
from construction_agent.collector.sources.js_em import JsEMCollector
from construction_agent.collector.sources.sc_em import ScEMCollector
from construction_agent.collector.sources.trs_cms_base import (
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
# 第三、四组：只能静态查的契约
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
