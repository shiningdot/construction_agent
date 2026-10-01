"""`HebeiEMCollector` 的附件通路测试。

**这个文件此前不存在**——2026-10-01 第三十轮改完 `hebei_em` 先跑全量，
**653 passed 一条都没动**，说明下面的每一条通路原先都无人看管。

改前这个源有四处毛病，都在「附件」这一件事上：

1. **附件类型全当 PDF。** 它的附件 URL 是
   `/portal/upload/downloadFile?id={uuid}`——**没有扩展名**，正则也只认这个
   路径前缀。实测全站 110 页、18 个去重附件：`.pdf` 15、`.doc` 1、`.png` 1、
   另有 1 个 404 死链。那 1 个 Word 和 1 张 PNG 都写进了
   `"pdf_urls": [pdf_url]`，经 `executor._ASSET_KEYS` 登记成
   `asset_type='pdf'`，阶段② 会拿 PyMuPDF 去解它们。
2. **一页只收第一个附件。** `_crawl_detail` 里是 `re.search`，不是 `findall`。
   沧州鼎睿石化"5·31"那页有 3 个链接，只收第一个——而第一个恰好是那张 PNG，
   唯一能提出正文的 PDF 在第三个。
3. **落盘前缀写死 `.pdf`**，Word 附件顶着 PDF 的名字躺在缓存目录里。
4. 小：`content` 里一律写「PDF:」，`needs_ocr` 的注释见 `collect()` 那处。

**不联网。** `_session` 换成只认路由的假对象；`_fetch_all_pages` 换掉（它走的
是 POST，假对象没有这个方法）；`raw_store` 必须改道（无参构造落
`settings.DATA_DIR/raw_accidents.json`——仓库里的共享文件）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.executor import (  # noqa: E402
    _row_from_sink_record)
from construction_agent.collector.sources.hebei_em import (  # noqa: E402
    HBEM_BASE, HebeiEMCollector, _kind_from_disposition)
from construction_agent.collector.stores import RawAccidentStore  # noqa: E402

# 真实列表里的一条，能过标题初筛（跑挂了就说明初筛规则变了）
_TITLE = "保定满城河北泽优电气设备科技有限公司围墙建设工程“7·10”较大坍塌事故调查报告"


# --------------------------------------------------------------------------
# 假的网络层
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, content: bytes = b"",
                 headers: dict | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    @property
    def text(self) -> str:
        """`_crawl_detail` 读的是 `.text`（详情页是 HTML）。"""
        return self.content.decode("utf-8", "replace")


class _Session:
    """只实现 `collect()` 用到的那几样：`.get()` / `.headers` / `.close()`。

    路由按「子串命中」匹配，字典顺序即优先级——所以 `downloadFile?id=a` 这种
    要排在更宽松的键前面。
    """

    def __init__(self, routes: dict[str, _Resp]):
        self.routes = routes
        self.headers: dict[str, str] = {}

    def get(self, url: str, **_kw) -> _Resp:
        for key, resp in self.routes.items():
            if key in str(url):
                return resp
        return _Resp(status_code=404)

    def close(self) -> None:
        pass


def _attach(filename: str, status: int = 200, size: int = 200) -> _Resp:
    """一个附件响应。`size` 要 ≥100——`_process_pdf` 拿它当「这个文件是空的」的线。"""
    return _Resp(status, b"%PDF-1.7\n" + b"0" * size,
                 {"content-disposition": f"attachment; filename={filename}"})


def _detail_html(links: list[str]) -> str:
    """详情页。meta 的 `content` **必须是单引号**——`_crawl_detail` 的正则钉死了。"""
    body = "".join(f'<p>附件：<a href="{u}">报告全文</a></p>' for u in links)
    return (
        "<html><head>"
        f"<meta name=\"ArticleTitle\" content='{_TITLE}'>"
        "<meta name=\"PubDate\" content='2025-07-10'>"
        "<meta name=\"ContentSource\" content='河北省应急管理厅'>"
        f"</head><body><div>{body}</div></body></html>")


def _collector(tmp_path: Path, routes: dict[str, _Resp],
               **kwargs) -> HebeiEMCollector:
    c = HebeiEMCollector(delay=0.0, data_dir=tmp_path / "src", **kwargs)
    c._session = _Session(routes)
    c.raw_store = RawAccidentStore(filepath=tmp_path / "raw.json")
    return c


def _records(n: int = 1) -> list[dict]:
    return [{"title": _TITLE, "article_id": f"a{i}", "date": "2025-07-10",
             "category_id": "3a9d0375", "category_name": "事故调查报告"}
            for i in range(n)]


def _run(c: HebeiEMCollector, n: int = 1) -> list[dict]:
    c._fetch_all_pages = lambda: _records(n)
    c.collect()
    return c.raw_store.get_all()


# --------------------------------------------------------------------------
# 判据本身
# --------------------------------------------------------------------------


@pytest.mark.parametrize("filename,kind,suffix", [
    ("报告.pdf", "pdf", ".pdf"),
    ("报告.doc", "office", ".doc"),
    ("报告.docx", "office", ".docx"),
    ("报告.PDF", "pdf", ".pdf"),
    # 实测那一张：612×374、19,830 字节。**认不出**——不收，但后缀要留住
    ("]GSZE$FOVYWCF59K6T9UD24.png", "", ".png"),
    # 汉字坏掉的那一段是 GBK 被当 latin-1 读的结果，判据只用 ASCII 后缀
    ("¡°3?13¡±½Ï´óÈ¼Æø±¬È¼ÊÂ¹Êµ÷²é±¨¸æ.pdf", "pdf", ".pdf"),
    ("没有后缀", "", ""),
])
def test_判据从Content_Disposition取类型(filename, kind, suffix):
    """**类型信号在响应头，不在 URL。**

    URL 是 `/portal/upload/downloadFile?id={uuid}`，没有扩展名；
    `Content-Type` 实测 18/18 都是 `application/octet-stream`。两条路都判不了，
    只有 `Content-Disposition` 的 `filename` 能。
    """
    resp = _Resp(headers={"content-disposition": f"attachment; filename={filename}"})
    assert _kind_from_disposition(resp) == (kind, suffix)


def test_没有Content_Disposition时不猜():
    """**从没见过**这条头缺失（17/18 带，缺的那条是 404）。真缺了就不报类型。"""
    assert _kind_from_disposition(_Resp()) == ("", "")


# --------------------------------------------------------------------------
# 分拣（端到端 collect）
# --------------------------------------------------------------------------


def test_Word附件登记到office_urls(tmp_path):
    """改前这一条会走进 `pdf_urls`。"""
    url = f"{HBEM_BASE}/portal/upload/downloadFile?id=doc1"
    c = _collector(tmp_path, {
        "downloadFile?id=doc1": _attach("事故调查报告.doc"),
    }, enable_classify=False)
    c._crawl_detail = lambda _u: {
        "title": _TITLE, "date": "2025-07-10", "source": "河北省应急管理厅",
        "text": "页面正文", "attach_urls": [url]}

    item = _run(c)[0]
    assert item["office_urls"] == [url]
    assert item["pdf_urls"] == []
    # 标签也跟着对（改前一律写「PDF:」）
    assert "Office 文档: " in item["content"]


def test_通路A把office_urls登记成office(tmp_path):
    """登记端：`executor._ASSET_KEYS` 里那条 `("office_urls", "office")`。

    `hebei_em` 的 record 是 dict，走 `raw_store.add_batch()` → 通路 A。
    """
    url = f"{HBEM_BASE}/portal/upload/downloadFile?id=doc1"
    c = _collector(tmp_path, {
        "downloadFile?id=doc1": _attach("事故调查报告.doc"),
    }, enable_classify=False)
    c._crawl_detail = lambda _u: {
        "title": _TITLE, "date": "2025-07-10", "source": "河北省应急管理厅",
        "text": "页面正文", "attach_urls": [url]}

    row = _row_from_sink_record(_run(c)[0], "hebei_em", "t")
    assert [a["asset_type"] for a in row["_assets"]] == ["office"]


def test_PNG附件两个键都不收但留warning(tmp_path, caplog):
    """实测那张 612×374 的 PNG。**宁可漏登记一条，也不要把图片当 PDF 交下去。**

    与 `beijing_em` 对 `.xlsx` 的处置同一档：认不出 → 不收 → 但记一条 warning，
    站点哪天多出一种附件类型，得有人知道。
    """
    url = f"{HBEM_BASE}/portal/upload/downloadFile?id=png1"
    c = _collector(tmp_path, {
        "downloadFile?id=png1": _attach("]GSZE$FOVYWCF59K6T9UD24.png"),
    }, enable_classify=False)
    c._crawl_detail = lambda _u: {
        "title": _TITLE, "date": "2025-07-10", "source": "河北省应急管理厅",
        "text": "页面正文", "attach_urls": [url]}

    with caplog.at_level("WARNING"):
        item = _run(c)[0]

    assert item["pdf_urls"] == []
    assert item["office_urls"] == []
    assert "附件类型认不出" in caplog.text


def test_一页多附件全部收下(tmp_path):
    """**判据自检**：把 `findall` 换回 `re.search`，这条就红。

    实测形态取自沧州鼎睿石化"5·31"那页——3 个链接，其中两个指向同一个 id。
    改前 `re.search` 只拿到第一个，而后面的 PDF 才是能提出正文的那个。
    """
    # 页面里的 href 是**相对路径**（实测如此，`_crawl_detail` 才要 `urljoin`）
    dup_rel = "/portal/upload/downloadFile?id=png1"
    pdf_rel = "/portal/upload/downloadFile?id=pdf9"
    dup = f"{HBEM_BASE}{dup_rel}"
    pdf = f"{HBEM_BASE}{pdf_rel}"
    c = _collector(tmp_path, {
        # 走真的 `_crawl_detail`（喂 HTML），这样才测得到那条正则
        "getPortalNewsDetails": _Resp(
            content=_detail_html([dup_rel, dup_rel, pdf_rel]).encode()),
        "downloadFile?id=png1": _attach("图.png"),
        "downloadFile?id=pdf9": _attach("报告.pdf"),
    }, enable_classify=False)

    item = _run(c)[0]
    assert item["pdf_urls"] == [pdf], "第一个是 PNG，改回 re.search 就只剩它、一个也收不到"
    assert len(c._crawl_detail(
        f"{HBEM_BASE}/portal/index/getPortalNewsDetails?id=1")["attach_urls"]) == 2, \
        "重复的 id 要去重，但后面的附件不能一起丢"


def test_404死链不登记但needs_ocr仍为真(tmp_path, caplog):
    """实测有这种：附件已从站点删掉，链接还挂着，`downloadFile?id=` 返回 404 空响应。

    不登记进 `pdf_urls`（没有可取的东西），但 `needs_ocr` 仍标 —— 「有附件」
    取的是页面上的链接而不是「下载成功」，资料确实存在过，只是这次没拿到。
    """
    dead = f"{HBEM_BASE}/portal/upload/downloadFile?id=dead1"
    c = _collector(tmp_path, {
        "downloadFile?id=dead1": _Resp(status_code=404),
    }, enable_classify=False)
    c._crawl_detail = lambda _u: {
        "title": _TITLE, "date": "2025-07-10", "source": "河北省应急管理厅",
        "text": "", "attach_urls": [dead]}

    with caplog.at_level("WARNING"):
        item = _run(c)[0]

    assert item["pdf_urls"] == []
    assert item["needs_ocr"] is True
    assert "附件取不到" in caplog.text


def test_落盘后缀跟着真实类型(tmp_path):
    """改前写死 `.pdf`，Word 附件顶着 PDF 的名字躺在缓存里。

    顺带钉住缓存通路：同一个附件跑第二遍时不重新下载，类型仍要从**文件名**
    读出来（缓存分支没有响应头可看）。
    """
    url = f"{HBEM_BASE}/portal/upload/downloadFile?id=doc1"
    downloads = []

    def fake_get(u, **_kw):
        # 只数附件那几次：`collect()` 开头还有一次列表页预热
        if "downloadFile" in u:
            downloads.append(u)
            return _attach("事故调查报告.doc")
        return _Resp()

    c = _collector(tmp_path, {}, enable_classify=False)
    c._session.get = fake_get
    c._crawl_detail = lambda _u: {
        "title": _TITLE, "date": "2025-07-10", "source": "河北省应急管理厅",
        "text": "", "attach_urls": [url]}

    _run(c)
    files = sorted(p.name for p in (tmp_path / "src").iterdir())
    assert len(files) == 1, f"该只落一个文件，实际 {files}"
    assert files[0].endswith(".doc"), f"落盘名没跟着真实类型：{files[0]}"
    assert len(downloads) == 1

    # 第二遍：缓存命中，不再下载，类型仍要对——缓存分支没有响应头可看，
    # 只能从**文件名**读出来，所以落盘名带真实后缀是这条通路的前提
    c2 = _collector(tmp_path, {}, enable_classify=False)
    c2._session.get = fake_get
    c2._crawl_detail = lambda _u: {
        "title": _TITLE, "date": "2025-07-10", "source": "河北省应急管理厅",
        "text": "", "attach_urls": [url]}
    item = _run(c2)[0]
    assert len(downloads) == 1, "缓存没命中，又下载了一次"
    assert item["office_urls"] == [url]
