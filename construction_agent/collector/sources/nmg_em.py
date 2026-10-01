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

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
NMGEM_BASE = "https://yjglt.nmg.gov.cn"
NMGEM_LIST_URL = "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/"

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

    def collect(self) -> list[RawAccidentText]:
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
            pdf_url = ""
            image_urls: list[str] = []
            local_path = ""

            if detail:
                detail_title = detail.get("title") or title
                detail_date = detail.get("date") or pub_date
                detail_source = detail.get("source") or detail_source
                detail_text = detail.get("text") or ""
                pdf_url = detail.get("pdf_url") or ""
                image_urls = detail.get("image_urls") or []
                if pdf_url:
                    pdf_text, local_path = self._process_pdf(pdf_url, title, detail_date)
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

            # 构建内容
            parts = [detail_title, f"信息来源: {detail_source}"]
            if local_path:
                parts.append(f"[本地文件: {local_path}]")
            if pdf_url:
                parts.append(f"PDF: {pdf_url}")
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
                "needs_ocr": bool(pdf_url) and not detail_text,
                "issuing_authority": detail_source,
                "classification": classification,
                "classification_reason": reason,
                "classification_confidence": conf,
                "review_note": "",
                "local_file_path": local_path,
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
        self.raw_store._save()

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

        # 正文
        soup = BeautifulSoup(html, "html.parser")
        text = ""
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)

        # PDF 附件
        pdf_url = ""
        for a in soup.find_all("a", href=True):
            if ".pdf" in a["href"].lower():
                pdf_url = a["href"] if a["href"].startswith("http") else urljoin(url, a["href"])
                break

        image_urls = extract_page_images(url, soup, max_n=self._image_max)

        return {"title": title, "date": date, "source": source, "text": text,
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
