"""住建部/应急管理部事故通报采集器。

采集住建部 (MOHURD) 和应急管理部 (MEM) 发布的建筑安全事故通报。

**2026-10-01 实修。** 这个采集器此前一条也产出不了，三个入口各有一种
「静默失败」，共同点是 HTTP 200、页面里 `<a>` 一个都没有、`except`
只记一条 debug——健康检查表上则统一显示成「空 / 0 条 / 0.8 秒」：

    /gongkai/zc/wjk/      JS 异步渲染。静态 HTML 5 KB、`<title>` 正确、
                          `<a>` 0 个；列表在 JPaaS 接口的 JSON 里。
    zlaq.mohurd.gov.cn    HTTP 521 + JS 反爬挑战页。**未修**，见下面注释。
    /gk/sgcc/             JS 跳转页（`location.href="./sggpdbqk/"`）。
                          httpx 不执行 JS，所以停在那一行。

住建部年度通报与督办通知书：
- 栏目: https://www.mohurd.gov.cn/gongkai/zc/wjk/（政策文件库）
- 逐年发布"房屋市政工程生产安全较大及以上事故查处督办通知书"
- 站方已改成 JS 异步渲染，列表改从 JPaaS 接口取，见 `MOHURD_JPaaS_API`

应急管理部事故调查报告：
- 栏目: https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/
- 重大及以上事故调查报告，HTML 页面 + 少量 PDF
- 原先指的上一级 `/gk/sgcc/` 现在只剩一行 JS 跳转；主库里 3 条
  `mem` 源记录的 URL 全在 `tbzdsgdcbg/` 下，所以入口换成它

采集策略：
1. 列表页 → 按标题关键词筛出事故通报（住建部走 JPaaS 接口并翻页）
2. 正文 —— 两条路，取决于详情页把正文放在哪：
     HTML 页面 → `_extract_article_text` 直接抽
     PDF 附件 → 存盘后由 `extract_pdf_text` 就地解析（PyMuPDF，本地、
                无 token、无网络）。提出文本就用它，提出空才落
                `needs_parsing` 占位——那是扫描件，要 OCR 的另走一路。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.pdf_text import extract_pdf_text
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── 住建部页面结构 ──
MOHURD_BASE = "https://www.mohurd.gov.cn"
MOHURD_SEARCH_URL = "https://www.mohurd.gov.cn/gongkai/zc/wjk/"

# 住建部政策文件库的列表接口（JPaaS 门户发布平台）。
#
# 官网改版后 `/gongkai/zc/wjk/` 只剩一个 JS 壳：5 KB、`<title>文件库</title>`、
# `<a>` **0 个**。列表由页面里那个 `<script id="...">` 标签上的属性驱动，
# 由 `unitbuild.js` 发这个 GET 请求，真正的列表 HTML 在响应的 `data.html` 里。
# 参数逐字抄自页面属性——`parseType` 的拼写（bulidstatic）是站方原样。
MOHURD_JPaaS_API = ("https://www.mohurd.gov.cn/api-gateway/"
                    "jpaas-publish-server/front/page/build/unit")
MOHURD_JPaaS_QUERY = {
    "parseType": "bulidstatic",
    "webId": "86ca573ec4df405db627fdc2493677f3",
    "tplSetId": "fc259c381af3496d85e61997ea7771cb",
    "pageType": "column",
    "tagId": "内容1",
    "editType": "null",
    "pageId": "vhiC3JxmPC8o7Lqg4Jw0E",  # 同页面 <meta name="ColId">
}

# 服务端每页硬上限：给 200 也只回 99 条，给 50 就回 50。所以「一次拉完」
# 做不到，只能翻页。`data` 里也**没有 total 字段**，翻到底只能靠
# 「这一页没有链接了」判断。
MOHURD_PAGE_SIZE = 99

# 默认翻几页。`wjk` 是政策文件库总栏目，**一页 99 条、一年约 400~500 篇**，
# 事故通报混在资质公告与标准文件里。实测（见下）翻 60 页有 5,940 条标题、
# 覆盖 2018 年至今，要 93 秒；32 页左右够到 2022 年。
MOHURD_MAX_PAGES = 30

# 标题过滤三段式，**必须同时满足**：
#   ① 含「事故」  ② 含文书类型词  ③ 含建筑行业词
#
# 只按「房屋市政工程」筛会怎样：实测 60 页里 23 条命中，其中 16 条是
# 《有限空间识别及施工安全作业指南》《生产安全重大事故隐患判定标准》
# 《施工安全监督人员培训考核大纲》《安全生产治本攻坚三年行动》这类
# **政策文件**。它们标题里恰好有「房屋市政工程」，可拿去抽「死亡人数」
# 「事故类型」是抽不出来的——要么空、要么错。
#
# 加 ② 之后剩 30 条，逐条看过，全部是事故通报、督办通知书、事故分析与案例。
MOHURD_TITLE_MUST = "事故"
MOHURD_TITLE_KINDS = ("通报", "督办", "调查", "分析")
MOHURD_TITLE_BUILD = ("房屋", "市政", "施工", "建筑", "工程",
                      "工地", "塔吊", "基坑", "坍塌", "坠落")

# 详情页正文容器的候选选择器，住建部与应急部共用。
# **顺序有讲究**：具体的在前、泛化的在后。`.content` / `.text` 这种词在
# 政府站上几乎必然命中点什么，放前面会盖掉真正的正文容器。
_CONTENT_SELECTORS = (
    ".editorContent-box",      # 住建部现行模板，实测 2026-10-01
    ".centralFile-wrapper",
    ".article-content", "#content", ".content",
    ".TRS_Editor", ".pages_content", ".text", ".con",
)

# 应急部详情页「相关链接」里那些按钮的锚文本。它们指向的 PDF 和正文附件
# 常常是同一份（`【下 载】` 就指向正文），但 2020 年的老页面只有正文附件、
# 没有按钮——所以按「排除按钮」选比按「找按钮」稳。
_ATTACHMENT_BUTTON_WORDS = ("下载", "打印", "关闭")

# ⚠️ **未接入**：住建部工程质量安全监管平台。
# 2026-10-01 实测 HTTP 521，并跳 `https://zlaq.mohurd.gov.cn/fwmh`，正文是
# 一段混淆过的 JS 反爬挑战（`function er(OM){var qo,mo="",no="",oo=[0xb4,...]}`）。
# 要过它得执行 JS 换 cookie，属于需要浏览器渲染的一档，本项目不做——
# 这里保留常量是为了让「为什么没有这一路」有处可查，不要再往 `collect()`
# 里挂，挂了也只会是又一个 0 条。
MOHURD_SAFETY_URL = "https://zlaq.mohurd.gov.cn"

# 应急管理部
MEM_BASE = "https://www.mem.gov.cn"
# 事故及灾害查处 → 调查报告。**不是**上一级的 `/gk/sgcc/`：那一级现在是
# 一个只有 `window.location.href="./sggpdbqk/"` 一行的跳转页，httpx 不跑
# JS，抓到的就是那 484 字节的空壳。主库里 3 条 `mem` 源记录的 URL 全在
# `tbzdsgdcbg/` 下，入口选它。
MEM_SGCC_URL = "https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/"


class MohurdCollector(BaseCollector):
    """住建部事故通报采集器。

    采集住建部官网的房屋市政工程事故年度通报和个案通报。
    支持两种模式：
    1. list 模式：从通报列表页提取链接 → 下载 HTML
    2. pdf 模式：下载 PDF 文件 → 调用 MinerU 解析
    """

    def __init__(
        self,
        collect_mem: bool = True,
        delay: float | None = None,
        data_dir: Optional[Path] = None,
    ):
        """
        Args:
            collect_mem: 是否同时采集应急管理部数据
            delay: 请求间隔（秒）
            data_dir: 数据缓存目录
        """
        self.collect_mem = collect_mem
        self._delay = delay if delay is not None else 3.0
        self.data_dir = data_dir or (settings.DATA_DIR / "mohurd")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    def source_name(self) -> str:
        return "mohurd"

    def get_crawl_delay(self) -> float:
        return self._delay

    def collect(self) -> list[RawAccidentText]:
        """采集住建部和应急管理部事故通报。"""
        results: list[RawAccidentText] = []

        # ── 住建部通报 ──
        try:
            mohurd_results = self._collect_mohurd()
            results.extend(mohurd_results)
            logger.info(f"住建部: 采集 {len(mohurd_results)} 条")
        except Exception as e:
            logger.warning(f"住建部采集失败: {e}")

        # ── 应急管理部 ──
        if self.collect_mem:
            try:
                mem_results = self._collect_mem()
                results.extend(mem_results)
                logger.info(f"应急管理部: 采集 {len(mem_results)} 条")
            except Exception as e:
                logger.warning(f"应急管理部采集失败: {e}")

        return results

    def _collect_mohurd(self) -> list[RawAccidentText]:
        """采集住建部通报。"""
        results: list[RawAccidentText] = []

        # Step 1: 从政策文件列表页搜索事故相关通报
        article_links = self._search_mohurd_articles()
        if not article_links:
            logger.warning("住建部: 未找到事故相关通报")
            return results

        logger.info(f"住建部: 找到 {len(article_links)} 个相关通报")

        # Step 2: 逐篇下载解析
        for i, (url, title, date_str) in enumerate(article_links, 1):
            try:
                raw = self._download_mohurd_article(url, title, date_str)
                if raw:
                    results.append(raw)
            except Exception as e:
                logger.debug(f"住建部文章 {url} 失败: {e}")
                continue

            time.sleep(self._delay)

        return results

    def _fetch_mohurd_page(self, page_no: int) -> Optional[str]:
        """取政策文件库第 `page_no` 页的列表 HTML。

        返回 `None` 表示**这一页没取到**（网络失败 / 非 200 / 不是 JSON /
        响应里没有 `html`）。调用方据此停止翻页。

        ⚠️ 和 `TrsCmsCollector._fetch` 同一个教训：`None` 有两种含义，
        「取不到」和「本来就空」。这里只表示前者；「翻到底了」的信号是
        **取到了 HTML 但里面一个 `<a>` 都没有**，由调用方判断。
        """
        params = dict(MOHURD_JPaaS_QUERY)
        params["paramJson"] = json.dumps(
            {"pageNo": page_no, "pageSize": MOHURD_PAGE_SIZE})
        try:
            resp = self._client.get(
                MOHURD_JPaaS_API,
                params=params,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": MOHURD_SEARCH_URL,
                },
            )
        except Exception as e:
            logger.warning(f"住建部列表接口第 {page_no} 页请求失败: {e}")
            return None

        if resp.status_code != 200:
            logger.warning(
                f"住建部列表接口第 {page_no} 页 HTTP {resp.status_code}")
            return None

        try:
            data = resp.json().get("data") or {}
        except Exception as e:
            logger.warning(f"住建部列表接口第 {page_no} 页响应不是 JSON: {e}")
            return None

        html = data.get("html")
        if not isinstance(html, str):
            logger.warning(
                f"住建部列表接口第 {page_no} 页响应里没有 data.html"
                f"（data 的键: {list(data.keys())}）")
            return None
        return html

    @staticmethod
    def _is_accident_title(title: str) -> bool:
        """住建部列表里的这个标题，是不是一份事故文书。

        三段式，**必须同时满足**，理由见 `MOHURD_TITLE_KINDS` 上方那段
        实测记录：只按「房屋市政工程」一个词筛，捞进来的大半是标准、
        指南、大纲这类政策文件。

        它**分不出具体是哪起事故**：「房屋市政工程生产安全较大及以上
        事故查处督办通知书」这个标题实测出现 22 次，每一份对应一起不同
        的事故，只有正文里才有。所以同名不等于重复——去重要按 URL 做。
        """
        if MOHURD_TITLE_MUST not in title:
            return False
        if not any(k in title for k in MOHURD_TITLE_KINDS):
            return False
        return any(k in title for k in MOHURD_TITLE_BUILD)

    def _search_mohurd_articles(
        self, max_pages: int = MOHURD_MAX_PAGES
    ) -> list[tuple[str, str, str]]:
        """搜索住建部事故通报列表，返回 (url, title, date) 列表。

        翻 `wjk` 栏目的页，逐条过 `_is_accident_title`。翻页的两个停止
        条件**都不看命中数**——连着几页没命中很正常（政策文件扎堆的时段），
        按命中数早停会把后面的真通报一起截掉：

          1. 某页取不到（`_fetch_mohurd_page` 返回 `None`）
          2. 某页 HTML 里一个 `<a>` 都没有 → 翻到底了
        """
        articles: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        for page_no in range(1, max_pages + 1):
            html = self._fetch_mohurd_page(page_no)
            if html is None:
                break

            soup = BeautifulSoup(html, "lxml")
            links = soup.find_all("a", href=True)
            if not links:
                logger.debug(f"住建部列表第 {page_no} 页没有链接，翻到底")
                break

            for a_tag in links:
                title = a_tag.get_text(strip=True)
                if not title or not self._is_accident_title(title):
                    continue
                full_url = urljoin(MOHURD_BASE, a_tag["href"])
                if full_url in seen:
                    continue
                seen.add(full_url)

                li = a_tag.find_parent("li")
                date_str = self._extract_date_from_text(
                    li.get_text() if li else title) or ""
                articles.append((full_url, title, date_str))

        return articles

    def _extract_article_text(self, soup: BeautifulSoup) -> str:
        """从详情页里抽正文。

        逐个试选择器，**取第一个超过 100 字的**；一个都没过，就退回到
        `<body>` 去掉导航/脚本后的全文。返回可能仍然很短，阈值由调用方判。

        ⚠️ 这里踩过一个坑，值得写下来。原来的写法是「找到就用，找不到
        才回退」，看着等价，其实不是——

            for sel in [...]:
                tag = soup.select_one(sel)
                if tag:
                    content = tag.get_text(...)   # `.content` 命中一个摘要框
                    if len(content) > 100: break
            if not content:                       # 非空 → 回退分支不执行
                content = body.get_text(...)
            if len(content) < 100: return None    # 到这儿返回 None

        `.content` / `.text` 这类泛选择器在政府站上总会命中点什么（一个
        摘要框、一行提示），于是 `content` 被填成短文本、回退再也不跑，
        最后卡在长度判断上。**判断条件得看长短，不能看有无。**
        2026-10-01 实测：住建部详情页正文在 `.editorContent-box` 里，
        上面那串选择器一个都不匹配，所以这一路从来没抽出过正文——
        列表修好了，正文层照样是 0 条。
        """
        for selector in _CONTENT_SELECTORS:
            tag = soup.select_one(selector)
            if tag is None:
                continue
            text = tag.get_text(separator="\n", strip=True)
            if len(text) > 100:
                return text

        body = soup.find("body")
        if body is None:
            return ""
        for noise in body.find_all(["nav", "footer", "script", "style"]):
            noise.decompose()
        return body.get_text(separator="\n", strip=True)

    def _find_attachment_pdf(self, soup: BeautifulSoup,
                             base_url: str) -> Optional[str]:
        """在应急部详情页里找正文 PDF 附件的地址，找不到返回 `None`。

        这一类的正文**一律在附件里**——2020 / 2023 / 2024 三个年份的详情页
        都验过：HTML 里只有标题和元数据，`body` 的文本是整站导航菜单。

        页面上通常挂着两个 PDF：正文附件（URL 以 `W` 开头，锚文本是报告
        标题）和「相关链接」里的【下载】按钮（`P` 开头）。**优先取正文
        附件**——两者的文件名实测不同，不保证是同一份。

        一个都没找到「非按钮」的，就退回去用按钮那个：页面只挂按钮时，
        它就是这篇文档唯一的附件，取它总比空手而归强。
        """
        pat = re.compile(r"\.pdf(\?|$)", re.I)
        fallback: Optional[str] = None
        for a_tag in soup.find_all("a", href=True):
            if not pat.search(a_tag["href"]):
                continue
            text = a_tag.get_text(strip=True).replace(" ", "")
            if any(w in text for w in _ATTACHMENT_BUTTON_WORDS):
                if fallback is None:
                    fallback = urljoin(base_url, a_tag["href"])
                continue
            return urljoin(base_url, a_tag["href"])
        return fallback

    def _download_mohurd_article(
        self, url: str, title: str, date_str: str
    ) -> Optional[RawAccidentText]:
        """下载单篇住建部通报。"""
        resp = self._client.get(url)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "")

        # PDF 文件
        if url.endswith(".pdf") or "application/pdf" in content_type:
            return self._handle_pdf(url, title, date_str, resp.content)

        # HTML 页面
        soup = BeautifulSoup(resp.text, "lxml")
        content = self._extract_article_text(soup)

        if len(content) < 100:
            logger.debug(f"住建部详情页正文过短（{len(content)} 字）: {url}")
            return None

        # 日期
        from construction_agent.utils.date_utils import parse_date_to_dateobj
        date_obj = parse_date_to_dateobj(date_str) if date_str else None
        if not date_obj:
            date_obj = self._extract_date_from_content(soup)

        return RawAccidentText(
            source="mohurd",
            title=title,
            raw_text=content,
            raw_html=resp.text[:50000],
            source_url=url,
            extracted_date=date_obj,
            confidence_date=0.9 if date_obj else 0.0,
        )

    def _handle_pdf(
        self, url: str, title: str, date_str: str, pdf_data: bytes,
        source_base: str = "mohurd",
    ) -> Optional[RawAccidentText]:
        """把 PDF 存到本地，**就地解析出正文**；解析不出才落待解析记录。

        `source_base` 决定 `source` 字段：住建部传默认值，应急部传
        `"mem"`，落库时两个来源分得开——取值与主库里既有的 `mem`、
        `sh_em` 一族同构，都是源的 key。

        两条历史记录解释了为什么这里必须解析而不是只排队：

        1. 原先有一段「调 MinerU 直接解析」的代码**是死代码**，
           2026-10-01 全库 grep 确认：

               settings.MINERU_API_TOKEN                —— Settings 上没这个字段
               construction_agent.input.pdf_processor   —— `input/` 目录是空的

           而且那行 `if s.MINERU_API_TOKEN:` 写在 `try` **外面**，属性不
           存在时直接抛 `AttributeError`，整个函数崩掉——连上面那句
           「存 PDF」都执行不到。调用方没有 try，异常冒泡到采集循环里
           被 `logger.debug` 吞掉。所以**住建部的通报（PDF）从来没进过库**。

        2. 取掉死代码、只存不解析之后，应急部那条链路（列表 → 详情 →
           附件）是通的，但每条都落成 32 字的 `[PDF待解析] 标题`。实测
           同一个详情页的附件——欣佳酒店调查报告 1.1 MB / 51 页——本地
           `extract_pdf_text` 一次提出 **37,618 字**，商洛桥梁 39 页提出
           **28,315 字**。两份都是文字版，不需要 OCR、不需要 token、不
           需要联网。

        所以「排队等批量解析」这条理由站不住了：解析器就在同一个包里的
        `pdf_text.py`，其他 PDF 分支的采集器（`ln_em` / `henan_em` /
        `beijing_em`）一直在用它，只有这里没接。

        提出空（短于 50 字）才落 `needs_parsing` 占位。那个判据由
        `extract_pdf_text` 自己下：政府站的 PDF 常常是扫描件，PyMuPDF
        只取得到零星页眉页码，那种「提取到了」比提取不到更有害。
        """
        pdf_name = re.sub(r'[^\w\-.]', '_', title)[:50] + ".pdf"
        pdf_path = self.data_dir / pdf_name
        pdf_path.write_bytes(pdf_data)
        logger.info(f"PDF 已保存: {pdf_path}")

        from construction_agent.utils.date_utils import parse_date_to_dateobj
        date_obj = parse_date_to_dateobj(date_str) if date_str else None

        text = extract_pdf_text(pdf_path)
        if text:
            logger.info(f"PDF 已解析出正文 {len(text):,} 字: {title[:40]}")
            return RawAccidentText(
                source=source_base,
                title=title,
                raw_text=text,
                source_url=url,
                extracted_date=date_obj,
                confidence_date=0.9 if date_obj else 0.0,
                extra={"pdf_path": str(pdf_path), "parsed_from": "pdf_text"},
            )

        # 提不出正文 = 扫描件或损坏。PDF 仍留在盘上，下游可据此走 OCR。
        logger.info(f"PDF 提不出正文（扫描件？），落待解析记录: {title[:40]}")
        return RawAccidentText(
            source=f"{source_base}_pdf_unparsed",
            title=title,
            raw_text=f"[PDF待解析] {title}",
            source_url=url,
            extracted_date=date_obj,
            confidence_date=0.9 if date_obj else 0.0,
            extra={"pdf_path": str(pdf_path), "needs_parsing": True},
        )

    def _collect_mem(self) -> list[RawAccidentText]:
        """采集应急管理部事故通报。"""
        results: list[RawAccidentText] = []

        # 只关注建筑相关事故。**关键词只看标题**——调查报告的标题里带着
        # 事故本身（「…坍塌事故调查报告」），所以这一层筛得动。
        #
        # ⚠️ 挂牌督办那一类筛不动：标题只有文号，例如「重大生产安全事故
        # 查处挂牌督办通知书（安委督〔2026〕2号）」，事故类型全在正文里。
        # 入口因此选「调查报告」而不是「挂牌督办」——后者要全下再筛。
        construction_kw = ["建筑", "施工", "坍塌", "坠落", "工地", "工程",
                           "房屋", "市政", "塔吊", "基坑"]

        try:
            resp = self._client.get(MEM_SGCC_URL)
            if resp.status_code != 200:
                logger.warning(f"应急管理部列表页 HTTP {resp.status_code}")
                return results

            soup = BeautifulSoup(resp.text, "lxml")

            for a_tag in soup.find_all("a", href=True):
                title = a_tag.get_text(strip=True)
                if not title or not any(kw in title for kw in construction_kw):
                    continue

                # 基准用列表页自己的 URL，**不是** `MEM_BASE`。
                # 列表里的 href 长这样：`../../zfxxgkpt/fdzdgknr/202411/t2024....shtm`
                # ——相对路径。拿站点根（无路径）当基准，`../..` 无处可上、
                # 被吃掉，拼出来会少掉中间两级目录，直接 404。
                full_url = urljoin(MEM_SGCC_URL, a_tag["href"])

                raw = self._download_mem_article(full_url, title)
                if raw:
                    results.append(raw)
                time.sleep(self._delay)

        except Exception as e:
            logger.warning(f"应急管理部采集失败: {e}")

        return results

    def _download_mem_article(self, url: str, title: str) -> Optional[RawAccidentText]:
        """下载应急管理部单篇事故调查报告。

        正文一律在 PDF 附件里，所以这里不猜正文、只找附件。

        ⚠️ 原来走的是 `_extract_article_text` + 50 字阈值。问题是详情页
        `body` 的文本是**整站导航菜单**（639 字，含「首页 机构 新闻
        时政要闻…」），轻松越过阈值——于是返回一条「正文」全是导航菜单的
        记录进了管道。抽不出正文是失败，抽出一堆菜单是**污染**，后者更难
        发现，因为它在计数上看起来是成功的。
        """
        try:
            resp = self._client.get(url)
            if resp.status_code != 200:
                logger.warning(f"应急部详情页 HTTP {resp.status_code}: {url}")
                return None

            soup = BeautifulSoup(resp.text, "lxml")
            pdf_url = self._find_attachment_pdf(soup, url)
            if pdf_url is None:
                logger.debug(f"应急部详情页没找到 PDF 附件: {url}")
                return None

            pdf_resp = self._client.get(pdf_url)
            if pdf_resp.status_code != 200:
                logger.warning(
                    f"应急部附件 HTTP {pdf_resp.status_code}: {pdf_url}")
                return None

            return self._handle_pdf(pdf_url, title, "", pdf_resp.content,
                                    source_base="mem")

        except Exception as e:
            logger.debug(f"应急管理部页面 {url} 失败: {e}")
            return None

    @staticmethod
    def _extract_date_from_text(text: str) -> str:
        """从文本中提取日期字符串。"""
        m = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})?[日]?', text)
        if m:
            year, month = m.group(1), m.group(2)
            day = m.group(3) or "01"
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return ""

    @staticmethod
    def _extract_date_from_content(soup: BeautifulSoup):
        """从页面内容中提取日期。"""
        date_str = ""
        for selector in [".article-info", ".info", ".time", ".date", ".source"]:
            tag = soup.select_one(selector)
            if tag:
                date_str = tag.get_text(strip=True)
                break

        if date_str:
            from construction_agent.utils.date_utils import parse_date_to_dateobj
            return parse_date_to_dateobj(date_str)
        return None

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass
