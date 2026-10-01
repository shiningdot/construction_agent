"""内蒙古应急管理厅 (yjglt.nmg.gov.cn) 事故信息和查处评估爬虫。

目标: https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/

架构:
- TRS CMS，纯 HTML 渲染，无 JS，单页 7 条
- 列表: <div class="sr-news sr-law"> → <div class="sn-item"> → <span class="sn-date"> + <a>
- URL 模式: ./{YYYYMM}/t{YYYYMMDD}_{id}.html
- 详情页主要内容在 <meta> 标签中:
  - <meta name="ArticleTitle"> — 标题
  - <meta name="PubDate"> — 日期
  - <meta name="ContentSource"> — 来源
- 详情页几乎没有正文文本，事故报告可能以 PDF 附件形式存在
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from construction_agent.collector.classifier import classify_v2, is_construction_candidate
from construction_agent.collector.images import extract_page_images
from construction_agent.collector.pdf_text import extract_pdf_text

from construction_agent.collector.base import (BODY_MIN_CHARS, BaseCollector,
                                               CollectResult, RawAccidentText)
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
NMGEM_BASE = "https://yjglt.nmg.gov.cn"
NMGEM_LIST_URL = "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/"

# 附件链接藏在 `<script>` 里（2026-10-01 第二十二轮补）。
#
# 这个站的模板把附件写成 JS 变量，`<a>` 是**运行时**由 JS 拼出来的：
#
#     <div class="article_content" style="display:none">附件：
#       <script>
#         var fujian = './P020251024611236404050.pdf';
#         var file = '<a href="./P020251024611236404050.pdf" …>…</a>';
#         … $(".article_content").append(file[i]) …
#       </script>
#     </div>
#
# 于是 `soup.find_all("a", href=True)` 在这页上 **0 次命中**——`div#pare` 里
# 一个 `<a>` 都没有，整页也没有任何 `<a>` 指向那个 PDF。
#
# 8 页实测的命中分布：`<a href>` 带 `.pdf` 的 **0 条**，这个正则在 1 条上命中
# （就是真有附件的那一页），其余 7 条都没有——不是误纳风险高的形态。
# 只匹配 `var fujian` 这一个变量名，不扫 `script` 里所有 `.pdf` 引号串：
# 后者在这 8 页上结果相同，但边界更松，将来页面底部挂个统计脚本就可能误吞。
_RE_SCRIPT_ATTACH = re.compile(
    r"""var\s+fujian\s*=\s*['"]([^'"]+)['"]""", re.I)

# ── PDF 文本提取 ──
# 本地那份已收敛到 `construction_agent.collector.pdf_text`（2026-10-01）。

# (classify_v2 已迁移至 construction_agent.collector.classifier)

# ── 爬虫 ──
class NmgEMCollector(BaseCollector):
    """内蒙古应急管理厅事故信息和查处评估采集器。

    用法:
        collector = NmgEMCollector()
        stats = collector.collect()
    """

    def __init__(self, delay: float = 1.0, enable_classify: bool = True,
                 data_dir: Optional[Path] = None, image_max: int = 200):
        self._delay = delay
        self._enable_classify = enable_classify
        # 一条详情页最多登记几张内容图（0 = 不抽取）。**只登记 URL、不下载**
        # ——下载是阶段② OCR 的事，这个源实测 121 张内容图，全下等于把采集
        # 任务变成图片搬运工。上限是防病态页面，不是常态截断：原为 50，
        # `gd_em` 实测有 59 张的扫描件报告被截，故放到 200。
        self._image_max = image_max
        # `data_dir=None` 时落 `settings.DATA_DIR/<源名>`（旧行为）。在线采集
        # 传一个临时目录进来，`data/<源名>/` 就不再产生——采集产出自 M2 起
        # 只落工作库的 `raw_accidents` 表（蓝图 01 §3.4）。
        self.data_dir = data_dir or (settings.DATA_DIR / "nmg_em")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._client = httpx.Client(
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
    def source_name(self) -> str: return "nmg_em"
    @property
    def collected_count(self) -> int: return self._collected

    def collect(self) -> CollectResult:
        t_start = time.time()
        logger.info("开始采集内蒙古应急管理厅事故信息和查处评估...")

        html = self._fetch(NMGEM_LIST_URL)
        if not html:
            return self._make_stats(0)

        records = self._parse_list_page(html)
        self._total_list = len(records)
        logger.info(f"列表: {self._total_list} 条")

        batch_items = []
        for i, rec in enumerate(records):
            title, rec_url, pub_date = rec["title"], rec["url"], rec["pub_date"]

            is_ok, reason = is_construction_candidate(title)
            if not is_ok:
                self._filtered_out += 1
                logger.debug(f"  过滤: [{title[:60]}] - {reason}")
                continue

            logger.info(f"  候选({i+1}): [{title[:60]}] - {reason}")
            time.sleep(self._delay)

            # 获取详情页
            detail = self._crawl_detail(rec_url)
            detail_title = title
            detail_date = pub_date
            detail_source = "内蒙古自治区应急管理厅"
            detail_text = ""
            text_source = ""
            pdf_url = ""
            image_urls: list[str] = []

            if detail:
                detail_title = detail.get("title") or title
                detail_date = detail.get("date") or pub_date
                detail_source = detail.get("source") or detail_source
                detail_text = detail.get("text") or ""
                text_source = detail.get("text_source") or ""
                pdf_url = detail.get("pdf_url") or ""
                image_urls = detail.get("image_urls") or []
                if pdf_url:
                    # 落盘路径**不往外传**（2026-10-01 第二十四轮）。`_process_pdf`
                    # 仍要落盘（`extract_pdf_text` 读的是文件），但那个路径只在
                    # 这一步内部有意义：经在线采集进来的 `data_dir` 是
                    # `_run_source` 建的**临时目录**，任务一结束就被
                    # `shutil.rmtree` 删掉——写进产出等于**记录一个必然消失的
                    # 位置**。空着只是缺信息，填错值是在撒谎。
                    #
                    # 这条裁决 `executor._assets_from` 的 docstring 里早就写着
                    # （第十七轮定的），只是这四个源各自拼 content 时绕过了它，
                    # 于是正文里混进一行 `/tmp/collect_d474f0794894_yaro70_1/…`
                    # ——而且那串目录名是 `urandom` 生成的，每次采集都不同。
                    pdf_text, _ = self._process_pdf(pdf_url, title, detail_date)
                    if pdf_text:
                        detail_text = pdf_text

            # classify_v2
            if self._enable_classify:
                result = classify_v2(detail_title, detail_text)
                logger.info(f"    分类: is_const={result['is_construction']}, "
                           f"conf={result['confidence']}, reason={result['reason']}")
                if result["is_construction"] and result["confidence"] == "high":
                    classification, reason, conf, status = "auto_construction", result["reason"], "high", "new"
                elif not result["is_construction"] and result["confidence"] == "high":
                    self._filtered_out += 1
                    logger.info(f"    排除: {result['reason']}")
                    continue
                else:
                    classification, reason, conf, status = "pending_review", result["reason"], "low", "review"
            else:
                classification, reason, conf, status = "pending_review", "未启用分类", "low", "review"

            # 真正文没提到：页面挂了附件或内容图，而正文区是空的
            needs_ocr = ((bool(pdf_url) or bool(image_urls))
                         and len(detail_text) < BODY_MIN_CHARS)

            # 进审核队列时把理由写准。`classify_v2` 拿 3 字的正文只能给出
            # 「置信度低」，说不出「这页的正文在图片里」。
            if needs_ocr and status == "review":
                reason = ("图片格式报告，需 OCR 提取文字" if image_urls
                          else "附件未提到正文，待下载处理")

            # 构建内容
            parts = [detail_title, f"信息来源: {detail_source}"]
            if pdf_url:
                parts.append(f"PDF: {pdf_url}")
            if image_urls:
                parts.append(f"内容图 {len(image_urls)} 张")
            # 页面挂了内容图、正文区却是空的 → 报告本体就在图里。此前这条
            # 页面交出的是 `body` 全文（导航+页脚 1,225 字），从外观看是
            # 「一条有正文的普通记录」，图片一张也不标——正是缺陷 C 的形态。
            if image_urls and needs_ocr:
                parts.append("[图片格式报告，需 OCR 提取文字]")
            if detail_text:
                preview = detail_text[:3000]
                if len(detail_text) > 3000:
                    preview += f"\n... (共 {len(detail_text)} 字)"
                parts.append(f"\n正文:\n{preview}")

            batch_items.append({
                "title": detail_title,
                "content": "\n".join(parts)[:50000],
                "source_url": rec_url, "source_name": "nmg_em",
                "publish_date": detail_date, "status": status,
                "pdf_urls": [pdf_url] if pdf_url else [],
                # 内容图（2026-10-01 第二十轮补）。`nmg_em` 实测有 **121 张
                # 内容图**（`/zwgk/zdxx/sgxxccpg/202511/W020251121….jpg`），是
                # 全项目最多的一块，此前一张都不登记——`executor._ASSET_KEYS`
                # 里那项 `("image_urls", "image")` 声明着，却没有源往外传。
                # 判据与抽取逻辑共用 `construction_agent.collector.images`。
                "image_urls": image_urls,
                # 判据见上方 `needs_ocr` 那行。改前是 `bool(pdf_url) and
                # not detail_text`——只看 PDF、且把 `body` 全文当正文，
                # 于是图片型页（正文区 3 字、body 1,225 字模板）恒为 False。
                "needs_ocr": needs_ocr,
                # 正文是从哪个容器取的（`pare` = 正文区 / `body` = 站点改版后
                # 的退化路径）。落进 extra 便于事后排查，不参与业务判断。
                "text_source": text_source,
                "issuing_authority": detail_source,
                "classification": classification,
                "classification_reason": reason,
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

        logger.info(f"完成: {self._collected}条入库, {self._review_count}条待审, {self._filtered_out}条过滤")
        return self._make_stats(time.time() - t_start)

    def _parse_list_page(self, html: str) -> list[dict]:
        """解析列表页: <div class="sn-item"> → <span class="sn-date"> + <a>"""
        soup = BeautifulSoup(html, "html.parser")
        records = []
        for item in soup.find_all("div", class_="sn-item"):
            a = item.find("a", href=True)
            if not a:
                continue
            title = a.get("title", "").strip() or a.get_text(strip=True)
            href = a["href"]
            # URL: ./{YYYYMM}/t{YYYYMMDD}_{id}.html
            full_url = urljoin(NMGEM_LIST_URL, href) if not href.startswith("http") else href
            span = item.find("span", class_="sn-date")
            pub_date = span.get_text(strip=True) if span else ""
            if not pub_date:
                m = re.search(r'/(\d{4})(\d{2})/t(\d{8})_', full_url)
                if m:
                    pub_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)[6:8]}"
            if title:
                records.append({"title": title, "url": full_url, "pub_date": pub_date})
        return records

    def _crawl_detail(self, url: str) -> Optional[dict]:
        """提取详情页 meta 标签中的数据和 PDF 附件。"""
        html = self._fetch(url)
        if not html:
            return None

        # 从 meta 标签提取
        title = ""
        date = ""
        source = ""
        for match in re.finditer(r'<meta\s+name="([^"]+)"\s+content=\'([^\']*)\'', html):
            name, content = match.group(1), match.group(2)
            if name == "ArticleTitle":
                title = content
            elif name == "PubDate":
                m = re.match(r'(\d{4}-\d{2}-\d{2})', content)
                if m:
                    date = m.group(1)
            elif name == "ContentSource":
                source = content

        # 正文 —— 取正文容器，**不是 `body`**（2026-10-01 第二十二轮改）
        #
        # 改之前取的是 `body.get_text()`，于是正文里混着导航与页脚。实测这个
        # 站的模板文字恒定 **1,225 字**：图片型页 `body` 全文 1,313–1,359 字，
        # **去掉模板只剩 88–134 字**——这些条目靠模板文字混过了正文长度这道
        # 防线（缺陷 G）。
        #
        # 正文容器是 `div#pare`，实测 8 页全部命中：
        #     图片型页  `div#pare` **3 字**（含 69 张内容图）
        #     文字型页  `div#pare` **18,724 字**
        # 口径分得很干净。注意**不能写成 `class_="content"`**——该站还有一个
        # `div.article_content` 也带 `content` 类，`find(class_="content")` 拿到
        # 的是它（9 字），不是正文区。只有 `id="pare"` 是对的。
        soup = BeautifulSoup(html, "html.parser")
        text = ""
        text_source = ""
        box = soup.find("div", id="pare")
        if box is not None:
            text = box.get_text(separator="\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            text_source = "pare"
        else:
            # 站点改版时退化到旧口径。**退化不是无声的**：`text_source`
            # 会一路传到产出里，`needs_ocr` 的判据也会因此偏保守（宁可标）。
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n").strip()
                text = re.sub(r"\n{3,}", "\n\n", text)
                text_source = "body"

        # PDF 附件
        pdf_url = ""
        for a in soup.find_all("a", href=True):
            if ".pdf" in a["href"].lower():
                pdf_url = a["href"] if a["href"].startswith("http") else urljoin(url, a["href"])
                break
        # DOM 里找不到时，去 `<script>` 里找（来由见 `_RE_SCRIPT_ATTACH`）。
        # 这是**退化路径的顺序**：先认真正的 `<a>`，再认 JS 变量。反过来的话，
        # 某天页面同时有两者、且 JS 那个是旧版本时会取错。
        if not pdf_url:
            for sc in soup.find_all("script"):
                m = _RE_SCRIPT_ATTACH.search(sc.get_text())
                if not m:
                    continue
                href = m.group(1)
                if href.lower().endswith(".pdf"):
                    pdf_url = urljoin(url, href)
                    break
                # 模板的扩展名白名单还有 .doc/.docx/.xls/.xlsx/.rar/.zip/.txt，
                # 但 `_process_pdf` 只解得了 PDF。**不静默跳过**——这个源实测
                # 只有 `.pdf`，出现别的说明站点改版了，得有人知道。
                logger.warning(f"    附件非 PDF，暂不处理: {href[:80]}")

        image_urls = extract_page_images(url, soup, max_n=self._image_max)

        return {"title": title, "date": date, "source": source, "text": text,
                "text_source": text_source,
                "pdf_url": pdf_url, "image_urls": image_urls}

    def _process_pdf(self, pdf_url: str, title: str, pub_date: str) -> tuple[str, str]:
        url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        local_path = self.data_dir / f"{pub_date}_{safe_title}_{url_hash}.pdf"
        if local_path.exists():
            return extract_pdf_text(local_path), str(local_path)
        try:
            resp = self._client.get(pdf_url)
            if resp.status_code == 200 and len(resp.content) >= 100:
                local_path.write_bytes(resp.content)
                logger.info(f"    PDF: {local_path.name} ({len(resp.content)} bytes)")
                return extract_pdf_text(local_path), str(local_path)
        except Exception as e:
            logger.error(f"    PDF异常: {e}")
        return "", ""

    def _fetch(self, url: str) -> Optional[str]:
        for attempt in range(3):
            try:
                resp = self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code == 404:
                    return None
            except Exception as e:
                logger.debug(f"请求失败(尝试{attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
        return None

    def _make_stats(self, duration: float) -> dict:
        cs = self.raw_store.stats()
        return {"total_records": self._total_list, "total_crawled": self._collected,
                "pending_review": self._review_count, "filtered_out": self._filtered_out,
                "cached_total": cs["total"], "cached_new": cs["new"],
                "cached_review": cs["review"], "duration_seconds": round(duration, 1)}

    def __del__(self):
        try: self._client.close()
        except Exception: pass

def crawl_nmg_em(delay: float = 1.0) -> dict:
    return NmgEMCollector(delay=delay).collect()
