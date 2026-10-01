"""河北省应急管理厅 (yjgl.hebei.gov.cn) 事故调查爬虫。

目标: https://yjgl.hebei.gov.cn/portal/infopublic/toInfoPublicNews?type=sgdc

架构:
- Java/JSP 站点，列表通过 AJAX POST API 加载
- API: POST /portal/securitycontrol/getParentAndChildNews
  参数: id=sgdc, type=2, limit=15, pageNo=N
  返回: {data: {count, resultList: [{articletitle, articleid, createdate, ...}]}}
- 详情页 URL: /portal/index/getPortalNewsDetails?id={articleid}&categoryid={categoryid}
- 附件: /portal/upload/downloadFile?id={uuid}
- 详情页正文极少，事故报告全文在附件中

**附件不全是 PDF，而且 URL 上看不出来**（2026-10-01 第三十轮实测全站 110 页、
18 个去重附件）：`.pdf` 15、`.doc` 1、`.png` 1、另有 1 个 id 已被删（404）。

这个 URL 形态（`downloadFile?id={uuid}`）**没有扩展名**，所以类型判不了在下载
之前——而 `Content-Type` 一律是 `application/octet-stream`，也没用。**可靠的信号
是响应头 `Content-Disposition` 的 `filename`**（17/18 个带，后缀是 ASCII 没坏；
汉字部分是坏的，GBK 被当 latin-1 读，不影响取后缀）。判据因此挪到下载之后，
见 `_process_pdf`。

> 上一轮（§2.5.29）把本源算作「14 个有后缀判据的源之一」，那是错的：正则只
> 认路径前缀 `/portal/upload/downloadFile?`，它**不是类型判据**。本源的附件
> 与 `esafety`（图片塞进 `pdf_urls`，§2.5.28）、`beijing_em`（Word 塞进
> `pdf_urls`，§2.5.29）是同一族，只是更靠后一轮才发现。

**每页附件个数也不止一个**：110 页里 93 页无附件、16 页 1 个、1 页 3 个
（沧州鼎睿石化"5·31"，其中两个是同一个 id）。`_crawl_detail` 原先用
`re.search` 只取第一个，那一页会漏掉后面的 PDF——而漏掉的正巧是唯一能提出
正文的那个（第一个是 PNG）。

**这个接口有两条响应路径，cookie 决定走哪一条：**

| 请求方式 | content-type | 响应体 | 标题 |
|---|---|---|---|
| 带 `JSESSIONID`（`collect()` 先 GET 列表页预热） | `application/json;charset=UTF-8` | 6,499 字节 | 正常中文 |
| 不带 cookie（直接 POST） | `text/plain;charset=ISO-8859-1` | 5,460 字节 | 汉字被逐字替换成 `?` |

匿名那条路径的响应体还含非法 UTF-8 字节（`0xb7`），`resp.json()` 抛的
`UnicodeDecodeError` 指不到「cookie 没了」这个真正的原因。

所以两件事：**不要绕过 `collect()` 的 cookie 预热直接调
`_fetch_all_pages()`**（那样测出来的是一个不存在于生产路径的故障）；
**不要用 `resp.json()`**，走 `_decode_json()`——它把这种情况翻译成一句说清
原因的错误，而不是静默降级成一份汉字全是 `?` 的响应。

原本这里写着「110 条记录, 8 页, 96 篇事故调查报告 + 14 篇挂牌督办」——
`count=110` 与接口自报一致，但「96 + 14」这两个数没有核算过，已删去。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from construction_agent.collector.attachments import kind_of_suffix, sort_into
from construction_agent.collector.classifier import classify_v2, is_construction_candidate
from construction_agent.collector.pdf_text import extract_pdf_text

from construction_agent.collector.base import (PREVIEW_CHARS, BaseCollector,
                                               CollectResult, RawAccidentText)
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
HBEM_BASE = "https://yjgl.hebei.gov.cn"
HBEM_LIST_PAGE = f"{HBEM_BASE}/portal/infopublic/toInfoPublicNews?type=sgdc"
HBEM_LIST_API = f"{HBEM_BASE}/portal/securitycontrol/getParentAndChildNews"
HBEM_DETAIL_URL = f"{HBEM_BASE}/portal/index/getPortalNewsDetails"
# 事故调查报告的分类 ID
HBEM_CATEGORY_ID = "3a9d0375-6937-4730-bf52-febb997d8b48"

# 附件链接。**没有扩展名**——类型要等下载后看 `Content-Disposition`（见 `_process_pdf`）。
_ATTACH_RE = re.compile(r'["\'](/portal/upload/downloadFile\?id=[^"\']+)["\']')

# `Content-Disposition` 里的 filename。汉字部分是坏的（GBK 被当 latin-1 读），
# 但**后缀是 ASCII、没坏**——要取的正是它。
_CD_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)')

# ── PDF 文本提取 ──
# 本地那份已收敛到 `construction_agent.collector.pdf_text`（2026-10-01）。

# (classify_v2 已迁移至 construction_agent.collector.classifier)

# (is_construction_candidate 已迁移至 classifier 模块)


def _kind_from_disposition(resp: httpx.Response) -> tuple[str, str]:
    """从响应头 `Content-Disposition` 里取 `(类型, 后缀)`。

    实测这条头长这样（汉字是坏的，GBK 被当 latin-1 读）：

        attachment; filename=¡°3?13¡±½Ï´óÈ¼Æø±¬È¼ÊÂ¹Êµ÷²é±¨¸æ.pdf

    **判据只用后缀那一段 ASCII**，所以汉字坏掉不影响它——这一点是有意利用的，
    不是侥幸：`.pdf` / `.doc` / `.png` 都是 ASCII。

    说不清是哪种的时候返回 `("", 后缀)`：后缀照用（落盘文件名诚实），类型空
    字符串让调用方不收。
    """
    m = _CD_FILENAME_RE.search(resp.headers.get("content-disposition", "") or "")
    if not m:
        return "", ""
    name = m.group(1).strip()
    suffix = Path(name).suffix.lower()
    # 用 `kind_of_suffix` 而不是 `attachment_kind(name)`：后者按 URL 解析，
    # 会把 filename 里那个乱码产生的 `?` 之后的整段（含真后缀）当查询串丢掉。
    return kind_of_suffix(suffix), suffix


def _decode_json(resp: httpx.Response) -> dict:
    """解析响应体，失败时给一句能定位原因的话。

    **这个接口有两条响应路径，内容完全不同：**

    | 请求方式 | content-type | 响应体 | 标题 |
    |---|---|---|---|
    | 带 `JSESSIONID`（`collect()` 先 GET 列表页预热） | `application/json;charset=UTF-8` | 6,499 字节 | 正常中文 |
    | 不带 cookie（直接 POST） | `text/plain;charset=ISO-8859-1` | 5,460 字节 | 汉字被逐字替换成 `?` |

    **cookie 决定了服务器返回哪一份。** 第二条路径下响应体还含非法 UTF-8
    字节（`0xb7`），`resp.json()` 抛的是
    `UnicodeDecodeError: byte 0xb7 in position 388`——这句话指不到
    「cookie 没了」这个真正的原因。

    这里**不做静默降级**。用 latin-1 硬解确实能出 JSON，但拿到的是那份汉字
    全被替换成 `?` 的降级响应，接着关键词初筛会把 110 条全部判为无关，
    表面上与「这个栏目本来就没有建筑事故」一模一样。宁可当场抛一句说清
    原因的错误。
    """
    try:
        return json.loads(resp.content.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise RuntimeError(
            f"列表接口返回的不是 UTF-8（{e}）。这通常意味着 cookie 预热失败："
            f"服务器对匿名请求返回的降级响应 content-type 为 "
            f"`text/plain;charset=ISO-8859-1`，其中的汉字已被替换成 `?`，"
            f"解析出来只会得到一批无法匹配的标题。请检查 `collect()` 开头的 "
            f"`self._session.get(HBEM_LIST_PAGE)` 是否成功。"
        ) from e

# ── 爬虫 ──
class HebeiEMCollector(BaseCollector):
    """河北省应急管理厅事故调查采集器。

    API 驱动采集:
        POST list API → 解析 JSON → 详情页 → 下载 PDF → classify_v2 → RawAccidentStore

    用法:
        collector = HebeiEMCollector()
        stats = collector.collect()
    """

    def __init__(self, delay: float = 1.0, enable_classify: bool = True,
                 data_dir: Optional[Path] = None):
        self._delay = delay
        self._enable_classify = enable_classify
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "hebei_em")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._session = httpx.Client(
            timeout=30.0, follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        self.raw_store = RawAccidentStore()
        self._total_list: int = 0
        self._filtered_out: int = 0
        self._collected: int = 0
        self._review_count: int = 0

    @property
    def start_page(self) -> int: return 1
    @start_page.setter
    def start_page(self, value: int): pass
    @property
    def end_page(self) -> int: return 1
    @end_page.setter
    def end_page(self, value: int): pass
    def source_name(self) -> str: return "hebei_em"
    @property
    def collected_count(self) -> int: return self._collected

    def collect(self) -> CollectResult:
        t_start = time.time()
        logger.info("开始采集河北省应急管理厅事故调查...")

        # Step 1: 初始化 session（获取 cookies）
        self._session.get(HBEM_LIST_PAGE)
        # 设置 AJAX headers
        self._session.headers.update({
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": HBEM_BASE,
        })

        # Step 2: API 分页获取所有记录
        all_records = self._fetch_all_pages()
        self._total_list = len(all_records)
        logger.info(f"API 采集完成: {self._total_list} 条")

        # Step 3: 逐条处理
        batch_items = []
        for i, rec in enumerate(all_records):
            title = rec["title"]
            article_id = rec["article_id"]
            date_str = rec["date"]
            category_id = rec.get("category_id", HBEM_CATEGORY_ID)

            # 一级筛选
            is_ok, reason = is_construction_candidate(title)
            if not is_ok:
                self._filtered_out += 1
                logger.debug(f"  过滤: [{title[:60]}] - {reason}")
                continue

            logger.info(f"  候选({i+1}/{self._total_list}): [{title[:60]}] - {reason}")
            time.sleep(self._delay)

            # ── 获取详情页 ──
            detail_url = f"{HBEM_DETAIL_URL}?id={article_id}&categoryid={category_id}"
            detail = self._crawl_detail(detail_url)
            detail_title = title
            detail_date = date_str
            detail_source = "河北省应急管理厅"
            detail_text = ""
            attach_urls: list[str] = []

            if detail:
                detail_title = detail.get("title") or title
                detail_date = detail.get("date") or date_str
                detail_source = detail.get("source") or detail_source
                detail_text = detail.get("text") or ""
                attach_urls = detail.get("attach_urls") or []

            # ── 下载附件、判类型、提取文本 ──
            #
            # 落盘路径**不往外传**（2026-10-01 第二十四轮）。`_process_pdf` 仍要
            # 落盘（`extract_pdf_text` 读的是文件），但那个路径只在内部有意义：
            # 经在线采集进来的 `data_dir` 是临时目录，任务结束即被
            # `shutil.rmtree` 删掉——写进产出等于记录一个必然消失的位置。
            # 裁决的出处与实测见 `sources/nmg_em.py` 同一处注释。
            #
            # **类型只能在下载之后判**（URL 没有扩展名，见 `_process_pdf`）。
            # 认不出的不入产出——`_process_pdf` 已经记了警告，这里不重复。
            pdf_urls: list[str] = []
            office_urls: list[str] = []
            # 图片附件（2026-10-01 第三十三轮补）。实测全站 110 页里唯一一张
            # `.png` 是「鼎睿石化"5·31"火灾事故调查报告」的**罐区总平面布置图**
            # ——内容是图，不是装饰。改前它走「认不出」分支被丢掉，而
            # `executor._ASSET_KEYS` 里 `("image_urls", "image")` 这一项
            # 早就等着被填。
            image_urls: list[str] = []
            attach_texts: list[str] = []
            for attach_url in attach_urls:
                a_text, _local, kind = self._process_pdf(
                    attach_url, title, detail_date)
                # 分拣不在这儿写三元表达式——加一种类型要改 n 处的教训见
                # `attachments.sort_into`。
                sort_into(kind, attach_url,
                          pdf=pdf_urls, office=office_urls, image=image_urls)
                if a_text:
                    attach_texts.append(a_text)
            if attach_texts:
                # 多个附件时全拼上（改前只可能有一个，见 `_crawl_detail`）
                detail_text = "\n\n".join(attach_texts)

            # ── classify_v2 ──
            if self._enable_classify and detail_text:
                result = classify_v2(detail_title, detail_text)
                logger.info(f"    分类: is_const={result['is_construction']}, "
                           f"conf={result['confidence']}, reason={result['reason']}")
                if result["is_construction"] and result["confidence"] == "high":
                    classification, reason_c, conf, status = "auto_construction", result["reason"], "high", "new"
                elif not result["is_construction"] and result["confidence"] == "high":
                    self._filtered_out += 1
                    logger.info(f"    排除: {result['reason']}")
                    continue
                else:
                    classification, reason_c, conf, status = "pending_review", result["reason"], "low", "review"
            elif not detail_text and attach_urls:
                classification, reason_c, conf, status = "pending_review", "附件文本提取失败", "low", "review"
            else:
                classification, reason_c, conf, status = "pending_review", "无正文内容", "low", "review"

            # ── 构建内容 ──
            # 不拼 `[本地文件: …]`（2026-10-01 第二十四轮）——理由见上方注释。
            parts = [detail_title, f"信息来源: {detail_source}"]
            # 分行按类型写（改前一律写「PDF:」，Word 附件也顶着这个标签）
            if pdf_urls:
                parts.append("PDF: " + " / ".join(pdf_urls))
            if office_urls:
                parts.append("Office 文档: " + " / ".join(office_urls))
            if image_urls:
                parts.append("图片附件: " + " / ".join(image_urls))
            if detail_text:
                # 截断长度收进共享常量（2026-10-01 第三十四轮）。这里原是
                # `[:3000]`——实测本源这条通路上的报告有 14,275 字，切到
                # 3000 只到第 4 页，**七个一级章节切掉六个**（含「事故原因
                # 分析」与「责任单位存在的主要问题」，前者是 CAICS 编码的
                # 原始依据）。理由与实测表见 `base.PREVIEW_CHARS`。
                preview = detail_text[:PREVIEW_CHARS]
                if len(detail_text) > PREVIEW_CHARS:
                    preview += f"\n... (共 {len(detail_text)} 字)"
                parts.append(f"\n正文:\n{preview}")

            batch_items.append({
                "title": detail_title, "content": "\n".join(parts)[:50000],
                "source_url": detail_url, "source_name": "hebei_em",
                "publish_date": detail_date, "status": status,
                "pdf_urls": pdf_urls,
                "office_urls": office_urls,
                "image_urls": image_urls,
                # 全项目约定（§2.5.13）：有附件 且 正文没提出来。
                # 「有附件」取的是**页面上的链接**，不是「下载成功的附件」——
                # 实测有 404 的死链（`downloadFile?id=` 返回 404 空响应），
                # 那种更要标：资料确实存在过，只是这次没拿到。
                # 本轮这一项的行为没变（`search` 与 `findall` 对「非空」等价），
                # 变的是第二项：`detail_text` 现在可能是多个附件正文的拼接。
                "needs_ocr": bool(attach_urls) and not detail_text,
                "issuing_authority": detail_source,
                "classification": classification,
                "classification_reason": reason_c,
                "classification_confidence": conf,
                "review_note": "",
            })

            if status == "review":
                self._review_count += 1
            else:
                self._collected += 1

            if len(batch_items) >= 5:
                self.raw_store.add_batch(batch_items)
                batch_items = []

        if batch_items:
            self.raw_store.add_batch(batch_items)

        logger.info(f"完成: {self._collected}入库, {self._review_count}待审, {self._filtered_out}过滤")
        return self._make_stats(time.time() - t_start)

    def _fetch_all_pages(self) -> list[dict]:
        """通过 POST API 获取全部页面的记录列表。"""
        # 先获取总数
        resp = self._session.post(HBEM_LIST_API,
            data={"id": "sgdc", "type": "2", "limit": "15", "pageNo": "1", "title": ""})
        if resp.status_code != 200:
            logger.error("API 请求失败")
            return []
        data = _decode_json(resp).get("data", {})
        count = data.get("count", 0)
        total_pages = (count + 14) // 15
        logger.info(f"API: count={count}, pages={total_pages}")

        all_records = []
        for page in range(1, total_pages + 1):
            resp = self._session.post(HBEM_LIST_API,
                data={"id": "sgdc", "type": "2", "limit": "15", "pageNo": str(page), "title": ""})
            if resp.status_code != 200:
                continue
            items = _decode_json(resp).get("data", {}).get("resultList", [])
            for item in items:
                title = item.get("articletitle", "")
                article_id = item.get("articleid", "")
                date_str = item.get("createdate", "")
                # 日期格式: "Jul 7, 202" (不完整) → 从详情页获取更准确
                all_records.append({
                    "title": title,
                    "article_id": article_id,
                    "date": date_str,
                    "category_id": item.get("categoryid", HBEM_CATEGORY_ID),
                    "category_name": item.get("categoryname", ""),
                })
            time.sleep(0.2)

        return all_records

    def _crawl_detail(self, url: str) -> Optional[dict]:
        """获取详情页，提取 meta 数据和 PDF 附件 URL。"""
        try:
            resp = self._session.get(url)
            if resp.status_code != 200:
                return None
        except Exception:
            return None

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # Meta 标签提取
        title = ""
        date = ""
        source = ""
        for match in re.finditer(r'<meta\s+name="([^"]+)"\s+content=\'([^\']*)\'', html):
            name, content = match.group(1), match.group(2)
            if name == "ArticleTitle": title = content
            elif name == "PubDate":
                m = re.match(r'(\d{4}-\d{2}-\d{2})', content)
                if m: date = m.group(1)
            elif name == "ContentSource": source = content

        # 回退: 从 <title> 和页面文本提取
        if not title:
            t_tag = soup.find("title")
            if t_tag:
                title_text = t_tag.get_text(strip=True)
                if "河北省应急管理厅" in title_text:
                    title = title_text.replace("河北省应急管理厅", "").strip()

        # 发布时间
        if not date:
            m = re.search(r'发布时间[：:]\s*(\d{4}-\d{2}-\d{2})', html)
            if m: date = m.group(1)

        # 正文
        text = ""
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)

        # 附件链接。**`findall` 不是 `search`** —— 2026-10-01 第三十轮实测
        # 沧州鼎睿石化"5·31"那页有 3 个链接（其中两个指向同一个 id），
        # `search` 只收第一个；而那一页第一个恰好是张 PNG（提不出正文），
        # 唯一能用的 PDF 在第三个。
        #
        # 按出现顺序去重：同一个 id 在一页里出现多次是常态（正文里引一次、
        # 附件区再列一次）。
        attach_urls: list[str] = []
        for hit in _ATTACH_RE.findall(html):
            full = urljoin(HBEM_BASE, hit)
            if full not in attach_urls:
                attach_urls.append(full)

        return {"title": title, "date": date, "source": source,
                "text": text, "attach_urls": attach_urls}

    def _process_pdf(self, url: str, title: str, pub_date: str) -> tuple[str, str, str]:
        """下载一个附件，返回 `(正文, 落盘路径, 类型)`。

        **类型只能在这里判。** 本源的附件 URL 是
        `/portal/upload/downloadFile?id={uuid}`——**没有扩展名**；响应头
        `Content-Type` 一律是 `application/octet-stream`（实测 18/18），也没用。
        可靠的信号只有 `Content-Disposition` 的 `filename`（17/18 个带，
        后缀是 ASCII 没坏）。所以判据在**下载之后**，这不是绕远，是这里只有
        下载之后才有信号。

        认不出的**照旧落盘、但不报类型**——落盘是为了文件名诚实、便于人工去看
        它到底是什么；类型给空字符串，由调用方决定不收。

        **图片不试文本提取**（2026-10-01 第三十三轮）：改前那张 `.png` 走的是
        「认不出」分支，本轮判据认了图片之后它会带着 `kind="image"` 一路走到
        `extract_pdf_text`——而 `fitz` 打不开 PNG，白跑一次还得把异常吞掉。
        图片的正文在阶段②的 OCR 里，不在采集层。

        函数名里的 `pdf` 是历史包袱：它从 2026-10-01 第三十轮起处理的是「附件」。
        """

        def _text_of(path: Path, kind: str) -> str:
            """PDF / Office 提出正文；图片返回空串（见上面那段）。"""
            return "" if kind == "image" else extract_pdf_text(path)

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        stem = f"{pub_date}_{safe_title}_{url_hash}"

        # 缓存：后缀下载前不知道（也就没法拼出确切文件名），按 stem 找已落盘的。
        # 用 `iterdir` 不用 `glob`——标题里可能有 `[`，那在 glob 里是字符集语法。
        for cached in self.data_dir.iterdir():
            if cached.name.startswith(stem + "."):
                k = kind_of_suffix(cached.suffix)
                return _text_of(cached, k), str(cached), k

        try:
            resp = self._session.get(url)
            if resp.status_code != 200 or len(resp.content) < 100:
                # 实测有 404 的死链（附件已从站点删掉，链接还挂着）
                logger.warning(f"    附件取不到（HTTP {resp.status_code}，"
                               f"{len(resp.content)} bytes）: {url}")
                return "", "", ""
            kind, suffix = _kind_from_disposition(resp)
            local_path = self.data_dir / f"{stem}{suffix}"
            local_path.write_bytes(resp.content)
            logger.info(f"    附件[{kind or '认不出'}]: {local_path.name} "
                        f"({len(resp.content)} bytes)")
            if not kind:
                logger.warning(f"    附件类型认不出，不入产出: {url}（后缀 "
                               f"{suffix or '无'}）")
            return _text_of(local_path, kind), str(local_path), kind
        except Exception as e:
            logger.error(f"    附件异常: {e}")
        return "", "", ""

    def _make_stats(self, duration: float) -> dict:
        cs = self.raw_store.stats()
        return {"total_records": self._total_list, "total_crawled": self._collected,
                "pending_review": self._review_count, "filtered_out": self._filtered_out,
                "cached_total": cs["total"], "cached_new": cs["new"],
                "cached_review": cs["review"], "duration_seconds": round(duration, 1)}

    def __del__(self):
        try: self._session.close()
        except Exception: pass

def crawl_hebei_em(delay: float = 1.0) -> dict:
    return HebeiEMCollector(delay=delay).collect()
