"""山西省应急管理厅 (yjt.shanxi.gov.cn) 事故调查报告爬虫。

目标: https://yjt.shanxi.gov.cn/zwgk_0/zfxxgk_78327/fdzdgknr_78331/gszl/sgdc_78341/
- TRS CMS, 纯HTML, 单页15条, 无分页
- URL: ./{YYYYMM}/t{YYYYMMDD}_{id}.shtml
- 详情: PDF附件为主
"""

from __future__ import annotations
import hashlib, logging, re, time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from construction_agent.collector.classifier import classify_v2
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

SX_BASE = "https://yjt.shanxi.gov.cn"
SX_LIST_URL = "https://yjt.shanxi.gov.cn/zwgk_0/zfxxgk_78327/fdzdgknr_78331/gszl/sgdc_78341/"

CONSTRUCTION_TITLE_KW = ["建筑","建设","施工","坍塌","倒塌","工地","坠落","工程",
                          "煤矿","矿山","矿井","隧道","桥梁","拆除","基坑","模板","脚手架"]

def _extract_pdf_text(fp: Path) -> str:
    try:
        import fitz; doc = fitz.open(str(fp))
        t = "".join(p.get_text() for p in doc); doc.close()
        return t if len(t) > 50 else ""
    except: return ""

def is_candidate(title: str) -> tuple[bool, str]:
    if "挂牌督办" in title: return False, "挂牌督办"
    m = [kw for kw in CONSTRUCTION_TITLE_KW if kw in title]
    return (True, f"关键词({len(m)}):{','.join(m[:3])}") if m else (False, "无")

class SxEMCollector:
    def __init__(self, delay: float = 1.0, enable_classify: bool = True):
        self._delay = delay; self._enable_classify = enable_classify
        self.data_dir = settings.DATA_DIR / "sx_em"; self.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=30, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        self.raw_store = RawAccidentStore()
        self._total = self._filtered = self._collected = self._review = 0

    @property
    def start_page(self) -> int: return 1
    @start_page.setter
    def start_page(self, v): pass
    @property
    def end_page(self) -> int: return 1
    @end_page.setter
    def end_page(self, v): pass
    def source_name(self) -> str: return "sx_em"
    @property
    def collected_count(self) -> int: return self._collected

    def collect(self) -> dict:
        t0 = time.time()
        logger.info("开始采集山西省应急管理厅...")
        html = self._fetch(SX_LIST_URL)
        if not html: return self._stats(0)
        records = self._parse_list(html)
        self._total = len(records)
        logger.info(f"列表: {self._total} 条")
        batch = []
        for i, rec in enumerate(records):
            title, url, date = rec["title"], rec["url"], rec["date"]
            ok, reason = is_candidate(title)
            if not ok: self._filtered += 1; continue
            logger.info(f"  候选({i+1}): [{title[:60]}] - {reason}")
            time.sleep(self._delay)
            detail = self._crawl_detail(url)
            d_title = (detail or {}).get("title") or title
            d_date = (detail or {}).get("date") or date
            d_text = (detail or {}).get("text") or ""
            pdf_url = (detail or {}).get("pdf_url") or ""
            pdf_text = ""; local = ""
            if pdf_url: pdf_text, local = self._dl_pdf(pdf_url, title, d_date)
            combined = pdf_text or d_text
            if self._enable_classify and combined:
                r = classify_v2(d_title, combined)
                if r["is_construction"] and r["confidence"] == "high":
                    cls, cr, cf, st = "auto_construction", r["reason"], "high", "new"
                elif not r["is_construction"] and r["confidence"] == "high":
                    self._filtered += 1; continue
                else: cls, cr, cf, st = "pending_review", r["reason"], "low", "review"
            else: cls, cr, cf, st = "pending_review", "无正文", "low", "review"
            parts = [d_title]
            if local: parts.append(f"[本地:{local}]")
            if pdf_url: parts.append(f"PDF:{pdf_url}")
            if combined: parts.append(f"\n正文:\n{combined[:3000]}")
            batch.append({"title":d_title,"content":"\n".join(parts)[:50000],
                "source_url":url,"source_name":"sx_em","publish_date":d_date,"status":st,
                "pdf_urls":[pdf_url] if pdf_url else [],
                "issuing_authority":"山西省应急管理厅",
                "classification":cls,"classification_reason":cr,"classification_confidence":cf})
            if st == "review": self._review += 1
            else: self._collected += 1
            if len(batch) >= 5: self.raw_store.add_batch(batch); batch = []
        if batch: self.raw_store.add_batch(batch)
        self.raw_store._save()
        return self._stats(time.time() - t0)

    def _parse_list(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        records = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.search(r'/t\d{8,10}_\d+\.shtml', href): continue
            title = a.get_text(strip=True)
            full = urljoin(SX_LIST_URL, href) if not href.startswith("http") else href
            m = re.search(r'/(\d{4})(\d{2})/t(\d{8})_', full)
            date = f"{m.group(1)}-{m.group(2)}-{m.group(3)[6:8]}" if m else ""
            if title: records.append({"title": title, "url": full, "date": date})
        return records

    def _crawl_detail(self, url: str) -> Optional[dict]:
        html = self._fetch(url)
        if not html: return None
        soup = BeautifulSoup(html, "html.parser")
        title = ""; t_tag = soup.find("title")
        if t_tag: title = t_tag.get_text(strip=True)
        m = re.search(r'(\d{4}-\d{2}-\d{2})', html)
        date = m.group(1) if m else ""
        pdf_url = ""
        for a in soup.find_all("a", href=True):
            if ".pdf" in a["href"].lower():
                pdf_url = urljoin(url, a["href"]); break
        return {"title": title, "date": date, "text": soup.get_text() if soup.find("body") else "", "pdf_url": pdf_url}

    def _dl_pdf(self, pdf_url: str, title: str, date: str) -> tuple[str, str]:
        h = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
        fn = re.sub(r'[\\/*?:"<>|]',"",title)[:50]
        lp = self.data_dir / f"{date}_{fn}_{h}.pdf"
        if lp.exists(): return _extract_pdf_text(lp), str(lp)
        try:
            r = self._client.get(pdf_url)
            if r.status_code == 200 and len(r.content) >= 100:
                lp.write_bytes(r.content); return _extract_pdf_text(lp), str(lp)
        except: pass
        return "", ""

    def _fetch(self, url: str) -> Optional[str]:
        for _ in range(3):
            try:
                r = self._client.get(url)
                if r.status_code == 200: return r.text
                elif r.status_code == 404: return None
            except: pass
            time.sleep(2)
        return None

    def _stats(self, d: float) -> dict:
        cs = self.raw_store.stats()
        return {"total_records":self._total,"total_crawled":self._collected,
                "pending_review":self._review,"filtered_out":self._filtered,
                "cached_total":cs["total"],"duration_seconds":round(d,1)}

    def __del__(self):
        try: self._client.close()
        except: pass

def crawl_sx_em(delay: float = 1.0) -> dict:
    return SxEMCollector(delay=delay).collect()
