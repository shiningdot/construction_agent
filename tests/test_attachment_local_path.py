"""附件落盘路径不再进正文、不再进产出（2026-10-01 第二十四轮）。

**不联网。** 列表页 / 详情页 HTML 现场造，PDF 用 PyMuPDF 现场造，HTTP
客户端换成假的。

盯的问题：四个源（`nmg_em` / `henan_em` / `ln_em` / `hebei_em`）各自把
`_process_pdf` 返回的落盘路径拼进 content（`[本地文件: {local_path}]`），
并写进产出的 `local_file_path` 键。经在线采集进来时那个路径是
`_run_source` 建的**临时目录**（`/tmp/collect_<urandom>_<n>/…`），任务一
结束就被 `shutil.rmtree` 删掉——正文里于是留下一行「记录一个必然消失的
位置」，而且那串目录名每次采集都不同。`executor._assets_from` 的
docstring（第十七轮定的）早就写明「不填 local_path，填错值比空着更糟」，
这四个源各自拼 content 时绕过了它。

判据放在**产出**上（content 文本 + item 的键），不放在「有没有调用
`_process_pdf`」——后者在 PDF 没提到文本时照样绿，而那时本就不该有路径。
所以每条行为测试都先证明正文是真的提出来了。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.sources.henan_em import (  # noqa: E402
    HNEM_LIST_URL, HenanEMCollector)
from construction_agent.collector.sources.nmg_em import (  # noqa: E402
    NMGEM_LIST_URL, NmgEMCollector)

SOURCES_DIR = ROOT / "construction_agent" / "collector" / "sources"

# 一条真实标题：含「事故」「调查报告」，过得了各源自己的标题初筛
TITLE = "福建省泉州市欣佳酒店“3·7”坍塌事故调查报告"
PDF_TEXT = "This is the full investigation report body extracted from the PDF attachment. " * 4


def _make_pdf(path: Path) -> bytes:
    """造一份**有字**的 PDF。空白页会让提取返回空串，那条路径就不该有路径。"""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    # 按每行一句写进去——`extract_pdf_text` 短于 50 字返回空串，得给够
    for i in range(12):
        page.insert_text((72, 72 + i * 14),
                         f"Line {i}: accident investigation report full text.",
                         fontsize=9)
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


def _client_serving(files: dict[str, object]):
    """假客户端：**URL 全等**才返回；没命中的一律 404。

    全等而非前缀，是因为列表页 URL 天然是详情页 URL 的前缀
    （`.../sgxx/` ⊂ `.../sgxx/2025/11-21/12345.html`）。用前缀匹配的话，
    详情页请求会吃到列表页 HTML，`_discover_pages` 也会把
    `index_1.html`、`index_2.html`… 全部读成「有内容」而探到 30 页——
    测试于是测的是一条错路径，而且看不出来。

    `henan_em._discover_pages` 靠 404 判断末页，所以 404 是**功能性的**，
    不是兜底。
    """
    def _get(url, *a, **k):
        data = files.get(url)
        if data is None:
            return httpx.Response(404, request=httpx.Request("GET", url))
        if isinstance(data, int):  # 显式状态码
            return httpx.Response(data, request=httpx.Request("GET", url))
        return httpx.Response(
            200, content=data, request=httpx.Request("GET", url))

    return types.SimpleNamespace(get=_get)


def _fake_store() -> tuple[types.SimpleNamespace, list[dict]]:
    """别碰 `data/raw_accidents.json`：真实的 store 会写盘。

    返回 `(store, 产出)`。**产出要从这里接**，不走 `collect()` 的返回值：
    `collect()` 返回的是 `_make_stats()` 的统计字典（注解已由第三十一轮
    改为 `base.CollectResult`，不再是那个说假话的 `list[RawAccidentText]`），
    条目是在 `add_batch` 那一步交出去的。
    """
    captured: list[dict] = []
    store = types.SimpleNamespace(
        mark_filtered=lambda *a, **k: None,
        add_batch=lambda batch: captured.extend(batch),
        _save=lambda *a, **k: None,
        stats=lambda *a, **k: {"total": len(captured), "new": 0, "review": 0},
    )
    return store, captured


def _assert_clean(item: dict) -> None:
    """三件事一起断言——少一件都可能是「碰巧没走到那条路径」。"""
    # 1. 产出里没有那个键（填错值比空着更糟，所以是「没有」不是「为空」）
    assert "local_file_path" not in item, (
        f"产出里又出现了 local_file_path={item.get('local_file_path')!r}")
    # 2. 正文里没有那行标记
    assert "[本地文件" not in item["content"], item["content"][:300]
    # 3. 也没有任何临时目录痕迹（`_run_source` 的 tmp 前缀 / 通用 /tmp）
    assert "/tmp/collect_" not in item["content"]
    assert "collect_" not in item["content"]


# --------------------------------------------------------------------------
# A 组：行为——真的跑一遍 collect()，PDF 真的提出文本，而路径没跟着出来
# --------------------------------------------------------------------------


def _nmg_html(pdf_href: str) -> str:
    # meta 的 `content` 必须是**单引号**——`_crawl_detail` 的正则钉死了
    return (
        "<html><head>"
        f"<meta name='ArticleTitle' content='{TITLE}'/>"
        "<meta name='PubDate' content='2025-11-21 10:00:00'/>"
        "<meta name='ContentSource' content='内蒙古自治区应急管理厅'/>"
        "</head><body>"
        f"<div id='pare'>事故经过与原因分析。<br/>（正文区）</div>"
        f"<div class='article_content'><a href='{pdf_href}'>附件下载</a></div>"
        "</body></html>"
    )


def test_nmg_em_提出PDF正文后产出里没有落盘路径(tmp_path):
    """`nmg_em` 这条走的是 `<a href>` 挂附件的常规路径。"""
    pdf = _make_pdf(tmp_path / "attach.pdf")
    c = NmgEMCollector(delay=0, enable_classify=False,
                       data_dir=tmp_path / "nmg_em")
    c.raw_store, items = _fake_store()
    list_html = (
        "<div class='sn-item'>"
        f"<a href='./202511/t20251121_12345.html' title='{TITLE}'>{TITLE}</a>"
        "<span class='sn-date'>2025-11-21</span></div>"
    )
    c._client = _client_serving({
        NMGEM_LIST_URL: list_html.encode(),
        "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/t20251121_12345.html":
            _nmg_html("P0202511210001.pdf").encode(),
        "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/P0202511210001.pdf":
            pdf,
    })

    c.collect()

    assert len(items) == 1, items
    item = items[0]
    # 先证「PDF 真的走通了」——否则下面的断言只是在断言一条没走到的路径
    assert "accident investigation report full text" in item["content"]
    assert item["pdf_urls"] == [
        "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/202511/P0202511210001.pdf"]
    _assert_clean(item)
    # 落盘本身**不能**因此被取消：`extract_pdf_text` 读的是文件
    assert list((tmp_path / "nmg_em").glob("*.pdf")), \
        "PDF 仍应落在 data_dir 里，只是路径不往外传"


def _henan_list_html() -> str:
    return (
        "<div class='zfxxgk_zdgkc'><ul><li>"
        f"<a href='/zwgk/xxgkml/sgxx/2025/11-21/12345.html' title='{TITLE}'>{TITLE}</a>"
        "<b>2025-11-21</b></li></ul></div>"
    )


def _henan_detail_html(pdf_url: str) -> str:
    return (
        f"<html><head><title>{TITLE}_事故信息_河南省应急管理厅</title></head>"
        "<body><div class='content'>"
        "时间：2025-11-21　来源：河南省应急管理厅"
        f"<a href='{pdf_url}'>报告全文</a>"
        "</div></body></html>"
    )


def test_henan_em_分页源里提出PDF正文后同样干净(tmp_path):
    """`henan_em` 另有分页探测（`_discover_pages`），一并覆盖。"""
    pdf = _make_pdf(tmp_path / "attach.pdf")
    pdf_url = "https://oss.henan.gov.cn/attach/2025/11/report.pdf"
    c = HenanEMCollector(delay=0, enable_classify=False,
                         data_dir=tmp_path / "henan_em")
    c.raw_store, items = _fake_store()
    c._client = _client_serving({
        HNEM_LIST_URL: _henan_list_html().encode(),
        "https://yjglt.henan.gov.cn/zwgk/xxgkml/sgxx/2025/11-21/12345.html":
            _henan_detail_html(pdf_url).encode(),
        pdf_url: pdf,
    })

    c.collect()

    assert len(items) == 1, items
    item = items[0]
    assert "accident investigation report full text" in item["content"]
    assert item["pdf_urls"] == [pdf_url]
    _assert_clean(item)
    assert list((tmp_path / "henan_em" / "pdfs").glob("*.pdf"))


def test_判据本身抓得住旧行为():
    """否则上面两条可能只是在断言一个恒真的东西。

    造的就是改前产出的形状（`nmg23.db` 里那条实测记录）。
    """
    old = {
        "content": ("标题\n信息来源: 内蒙古自治区应急管理厅\n"
                    "[本地文件: /tmp/collect_d474f0794894_yaro70_1/nmg_em/x.pdf]\n"
                    "正文:\n…"),
        "local_file_path": "/tmp/collect_d474f0794894_yaro70_1/nmg_em/x.pdf",
    }
    with pytest.raises(AssertionError):
        _assert_clean(old)


# --------------------------------------------------------------------------
# B 组：回归网——将来有人把这行加回来，静态就能拦住
# --------------------------------------------------------------------------

# 断言的是**产出字典的键**与**拼正文的那行**，不是「local_path」这个名字
# ——`local_path` 作为 `_process_pdf` 的**局部变量**是对的（它要落盘），
# 错的只是把它往外交。
_BANNED = ('"local_file_path"', "local_file_path:", "'local_file_path'",
           "[本地文件: {", "f\"[本地文件: {")


@pytest.mark.parametrize("name", ["nmg_em", "henan_em", "ln_em", "hebei_em"])
def test_四个源里不再出现落盘路径的外传(name):
    src = (SOURCES_DIR / f"{name}.py").read_text(encoding="utf-8")
    hits = [p for p in _BANNED if p in src]
    assert not hits, f"{name}.py 里又出现了外传落盘路径的写法: {hits}"


def test_扫描全部源不许再有源外传落盘路径():
    """新增源也拦得住——不是只钉这四个文件名。"""
    offenders: dict[str, list[str]] = {}
    for path in sorted(SOURCES_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        hits = [p for p in _BANNED if p in src]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"这些源在往外传落盘路径: {offenders}"
