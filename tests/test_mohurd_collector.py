"""`MohurdCollector` 里那几个判据的单元测试。

**不联网。** 测的是纯函数与几处「拿不到就返回 None」的分支，
源活没活着只有真发请求才知道。

这一组测试盯的是 2026-10-01 查出的一串毛病——它们的共同点是
**HTTP 200、看着正常、产出是 0**：

1. 列表页是 JS 壳，整页 `<a>` 一个没有 → 改走 JPaaS 接口拿 JSON。
2. 只按「房屋市政工程」筛标题，捞进来的大半是标准、指南、大纲。
3. 正文抽取的写法是「找到就用、找不到才回退」，`.text` 命中一个
   24 字的元素就把回退分支堵死，于是列表抓得到、正文永远抽不出。
4. `_handle_pdf` 里那句 `if s.MINERU_API_TOKEN` 写在 `try` 外面，
   属性不存在时直接 AttributeError，整条 PDF 路径一次都没跑通。
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.sources.mohurd import (  # noqa: E402
    MEM_SGCC_URL, MohurdCollector)


@pytest.fixture
def collector(tmp_path):
    c = MohurdCollector(data_dir=tmp_path)
    yield c
    # 测接口那几个用例会把 `_client` 换成假对象，它们没有 `close`——
    # 收尾时探一下再关，否则 28 个通过、5 个报 error 全在 teardown 上。
    close = getattr(c._client, "close", None)
    if close is not None:
        close()


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# --------------------------------------------------------------------------
# 标题过滤：三段式，实测调出来的
# --------------------------------------------------------------------------


def test_督办通知书要收():
    """主力就是它——实测 30 页里出现 19 次，每份对应一起不同事故。"""
    assert MohurdCollector._is_accident_title(
        "房屋市政工程生产安全较大及以上事故查处督办通知书")


def test_年度通报要收():
    """模块文档里点名要的就是这个。2018/2019/2020 三年各一份。"""
    for t in (
        "住房和城乡建设部办公厅关于2020年房屋市政工程生产安全事故情况的通报",
        "2018年11月房屋市政工程生产安全事故情况通报",
        "住房和城乡建设部办公厅关于2018年房屋市政工程生产安全事故和"
        "建筑施工安全专项治理行动情况的通报",
    ):
        assert MohurdCollector._is_accident_title(t), t


def test_事故分析与案例要收():
    """主库里 `建筑施工安全事故案例分析(住建部)` 那 192 条的源头。

    它们不含「通报/督办/调查」，只看前三个词会漏掉——所以
    `MOHURD_TITLE_KINDS` 里加了「分析」。
    """
    assert MohurdCollector._is_accident_title(
        "住房城乡建设部工程质量安全监管司关于印送《建筑施工安全事故案例分析》的函")
    assert MohurdCollector._is_accident_title(
        "住房城乡建设部工程质量安全监管司关于印送"
        "《房屋市政工程施工安全较大及以上事故分析（2018年）》的函")


def test_单起事故的通报要收():
    assert MohurdCollector._is_accident_title(
        "住房和城乡建设部办公厅关于河北省衡水市“4·25”施工升降机坠落事故的通报")


@pytest.mark.parametrize("title", [
    "住房城乡建设部办公厅关于印发《房屋市政工程有限空间识别及施工安全作业指南（试行）》的通知",
    "住房城乡建设部关于印发《房屋市政工程生产安全重大事故隐患判定标准（2024版）》的通知",
    "住房城乡建设部办公厅关于印发房屋市政工程施工安全监督人员培训考核大纲（试行）的通知",
    "住房城乡建设部关于开展房屋市政工程安全生产治本攻坚三年行动的通知",
    "住房和城乡建设部办公厅关于全面加强房屋市政工程施工工地新冠肺炎疫情防控工作的通知",
    "住房和城乡建设部关于开展房屋市政工程安全生产治理行动的通知",
])
def test_政策文件要挡住(title):
    """这一组是只按「房屋市政工程」筛时会漏进来的 16 条。

    它们是标准、指南、大纲、行动通知——标题里恰好有「房屋市政工程」，
    可拿去抽「死亡人数」「事故类型」抽不出来。**放进下游是污染**，
    比漏掉更麻烦，因为它在计数上看起来是成功的。
    """
    assert not MohurdCollector._is_accident_title(title), title


def test_非建筑行业的事故不收():
    """三段式里的第三段（建筑行业词）拦的就是这种。

    应急管理部的督办通知书标题长这样，事故类型全在正文里——
    这也是 `_collect_mem` 的入口选「调查报告」而不是「挂牌督办」的原因。
    """
    assert not MohurdCollector._is_accident_title(
        "重大生产安全事故查处挂牌督办通知书（安委督〔2026〕2号）")
    assert not MohurdCollector._is_accident_title(
        "国务院安委会关于福建泉州“7·9”重大火灾查处挂牌督办的函")


def test_含事故但缺文书类型词也不收():
    """判定是「同时满足」不是「满足其一」。"""
    assert not MohurdCollector._is_accident_title(
        "住房和城乡建设部 应急管理部关于加强建筑施工安全事故责任企业人员处罚的意见")
    assert not MohurdCollector._is_accident_title(
        "住房和城乡建设部工程质量安全监管司关于征集提升建筑施工安全生产"
        "事故防治关键技术攻坚难题的通知")


# --------------------------------------------------------------------------
# 附件挑选
# --------------------------------------------------------------------------


def test_优先挑正文附件而不是下载按钮(collector):
    """页面上挂两个 PDF，按钮那个以【下 载】为锚文本。

    **锚文本里的空格要去掉再比**——真实页面上就是「【下 载】」这样
    带一个全角空格的写法，不处理会认不出它是按钮。
    """
    html = """
    <a href="./W020240204579335303864.pdf">内蒙古阿拉善新井煤业有限公司露天煤矿“2·22”特别重大坍塌事故调查报告</a>
    <a href="./P020230829535806813294.pdf">【下 载】</a>
    """
    got = collector._find_attachment_pdf(
        _soup(html), "https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202308/t1.shtml")
    assert got == ("https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202308/"
                   "W020240204579335303864.pdf")


def test_老页面没有按钮也要挑得出来(collector):
    """2020 年的模板只有正文附件、没有「相关链接」那一栏。"""
    html = '<a href="./W020240229374240397495.pdf">福建省泉州市欣佳酒店“3·7”坍塌事故调查报告</a>'
    got = collector._find_attachment_pdf(
        _soup(html), "https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/2020/202007/t1.shtml")
    assert got.endswith("/2020/202007/W020240229374240397495.pdf")


def test_只剩按钮时退回用按钮(collector):
    """页面只挂按钮时，它就是这篇文档唯一的附件，取它比空手而归强。"""
    html = '<a href="./P020230829535806813294.pdf">【下 载】</a>'
    got = collector._find_attachment_pdf(
        _soup(html), "https://www.mem.gov.cn/gk/zfxxgkpt/fdzdgknr/202308/t1.shtml")
    assert got.endswith("P020230829535806813294.pdf")


def test_没有附件就返回None(collector):
    """返回 None 是有意的——不要拿详情页那 639 字导航菜单冒充正文。"""
    assert collector._find_attachment_pdf(
        _soup("<p>这一页没有附件</p>"), "https://www.mem.gov.cn/x/") is None


def test_区分大小写的pdf后缀也认(collector):
    html = '<a href="./W1.PDF">报告</a>'
    assert collector._find_attachment_pdf(
        _soup(html), "https://www.mem.gov.cn/x/") == "https://www.mem.gov.cn/x/W1.PDF"


# --------------------------------------------------------------------------
# 正文抽取：那个「有无 vs 长短」的坑
# --------------------------------------------------------------------------


def test_泛选择器命中短元素时仍要回退到body(collector):
    """回归钉子。

    原写法逐个试选择器，一旦 `select_one` 命中就赋给 `content`，然后
    「如果有内容就不再回退」——`.text` 在住建部详情页上命中一个 **24 字**
    的元素，`content` 被填满，回退分支从此不执行，最后卡在
    `len(content) < 100` 上返回 None。

    后果是：列表抓得到、正文永远抽不出，两个环节各修一半也还是 0 条。
    **判断条件得看长短，不能看有无。**
    """
    html = ("<html><body><div class='text'>短</div>"
            "<div id='main'>" + "这是正文内容。" * 40 + "</div></body></html>")
    text = collector._extract_article_text(_soup(html))
    assert "这是正文内容" in text
    assert len(text) > 100


def test_具体选择器命中长文本就优先用它(collector):
    """`.editorContent-box` 是住建部现行模板的正文容器（实测 685 字）。

    它排在 `_CONTENT_SELECTORS` 第一位，命中够长就直接返回，
    不会被后面 `.text` 那类泛选择器盖掉。
    """
    body = "房屋市政工程生产安全较大及以上事故查处督办通知书。" * 20
    html = (f"<html><body><div class='editorContent-box'>{body}</div>"
            "<div class='text'>导航</div></body></html>")
    text = collector._extract_article_text(_soup(html))
    assert text == body


def test_都命不中就用body去掉噪声(collector):
    html = ("<html><body><nav>导航导航</nav>"
            "<div class='whatever'>" + "正文。" * 60 + "</div>"
            "<script>var x=1;</script></body></html>")
    text = collector._extract_article_text(_soup(html))
    assert "正文。" in text
    assert "导航导航" not in text
    assert "var x" not in text


# --------------------------------------------------------------------------
# JPaaS 接口：四种「取不到」都要返回 None
# --------------------------------------------------------------------------


def _client_returning(collector, response):
    collector._client = type("C", (), {"get": lambda self, *a, **k: response})()


def test_接口正常时取出data_html(collector):
    _client_returning(collector, httpx.Response(
        200, json={"data": {"html": "<ul><li><a href='/x'>标题</a></li></ul>"}}))
    assert collector._fetch_mohurd_page(1) == "<ul><li><a href='/x'>标题</a></li></ul>"


def test_非200就是取不到(collector):
    _client_returning(collector, httpx.Response(503))
    assert collector._fetch_mohurd_page(1) is None


def test_响应不是JSON就是取不到(collector):
    """页面上是 JS 壳时，直接抓 URL 拿到的是 HTML 不是 JSON——
    这正是改接口之前的状态。"""
    _client_returning(collector, httpx.Response(200, text="<!DOCTYPE html><html>"))
    assert collector._fetch_mohurd_page(1) is None


def test_JSON里没有html就是取不到(collector):
    """服务端换结构了要在这里显形，不能默默返回空串。"""
    _client_returning(collector, httpx.Response(200, json={"data": {"total": 0}}))
    assert collector._fetch_mohurd_page(1) is None


def test_接口抛异常就是取不到(collector):
    def boom(*a, **k):
        raise httpx.ConnectError("Name or service not known")

    collector._client = type("C", (), {"get": staticmethod(boom)})()
    assert collector._fetch_mohurd_page(1) is None


# --------------------------------------------------------------------------
# PDF：不崩，且落一条待解析记录
# --------------------------------------------------------------------------


def test_存PDF并落待解析记录(collector, tmp_path):
    """`_handle_pdf` 里那句 `if s.MINERU_API_TOKEN` 原先写在 `try` 外面。

    `Settings` 上根本没这个字段，于是 AttributeError 直接把函数掀翻——
    连上面那句「存 PDF」都到不了，住建部的年度通报（PDF）因此
    一次也没进过库。
    """
    raw = collector._handle_pdf("https://x/W1.pdf", "某事故调查报告",
                                "2024-11-22", b"%PDF-1.4 fake")
    assert raw is not None
    assert raw.source == "mohurd_pdf_unparsed"
    assert raw.extra["needs_parsing"] is True
    assert Path(raw.extra["pdf_path"]).exists()
    assert "待解析" in raw.raw_text


def test_应急部的PDF来源前缀是mem(collector):
    """两个来源在落库时要分得开。"""
    raw = collector._handle_pdf("https://x/W1.pdf", "某事故调查报告", "",
                                b"%PDF-1.4 fake", source_base="mem")
    assert raw.source == "mem_pdf_unparsed"


def test_文字版PDF要就地提出正文(collector):
    """`source` 用源的 key（`mem` / `mohurd`），与主库既有取值同构。

    改之前每条 PDF 都落成 32 字的 `[PDF待解析] 标题`，因为「解析」那一段
    是够不着的死代码（`settings.MINERU_API_TOKEN`）。实测应急部的调查
    报告全是**文字版**：欣佳酒店 51 页提出 37,618 字、商洛桥梁 39 页提出
    28,315 字——本地 `extract_pdf_text` 一次到位。

    现场造一份文字版 PDF，不塞真附件：测试不该依赖网络，也不该把
    1 MB 的二进制带进仓库。
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "\n".join(
        f"Line {i}: accident investigation report body text." for i in range(12)))
    pdf_path = tmp_pdf = collector.data_dir / "src.pdf"
    doc.save(str(pdf_path))
    doc.close()

    raw = collector._handle_pdf("https://x/W1.pdf", "某事故调查报告",
                                "2024-11-22", tmp_pdf.read_bytes())

    assert raw.source == "mohurd"
    assert "accident investigation report" in raw.raw_text
    assert len(raw.raw_text) > 50
    assert raw.extracted_date is not None
    # 成功的这条不带待解析标记——否则下游还会再排一次队
    assert not raw.extra.get("needs_parsing")
    assert raw.extra["parsed_from"] == "pdf_text"
    assert Path(raw.extra["pdf_path"]).exists()


def test_扫描件才落待解析(collector):
    """提不出正文是**扫描件**的信号，不是错误。

    政府站的 PDF 常常是扫描件，PyMuPDF 只取得到零星页眉页码——那种
    「提取到了」比提取不到更有害（下游会以为正文已经拿到）。判据
    「短于 50 字算失败」由 `extract_pdf_text` 自己下。

    造一份**空白页**的 PDF 来模拟：能打开、有页数、但一个字都没有。
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()
    blank = collector.data_dir / "blank.pdf"
    doc.save(str(blank))
    doc.close()

    raw = collector._handle_pdf("https://x/W2.pdf", "某扫描件", "",
                                blank.read_bytes())

    assert raw.source == "mohurd_pdf_unparsed"
    assert raw.extra["needs_parsing"] is True
    assert "待解析" in raw.raw_text
    # PDF 仍留在盘上——下游可据此走 OCR
    assert Path(raw.extra["pdf_path"]).exists()


def test_列表页入口是调查报告而不是跳转页():
    """`/gk/sgcc/` 现在只有一行 `location.href="./sggpdbqk/"`，
    httpx 不跑 JS，抓到的就是那 484 字节空壳。主库里 3 条 mem 记录的
    URL 全在 `tbzdsgdcbg/` 下。"""
    assert MEM_SGCC_URL.endswith("/gk/sgcc/tbzdsgdcbg/")
