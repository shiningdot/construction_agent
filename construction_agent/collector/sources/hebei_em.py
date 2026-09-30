"""河北省应急管理厅 (yjgl.hebei.gov.cn) 事故调查爬虫。

目标: https://yjgl.hebei.gov.cn/portal/infopublic/toInfoPublicNews?type=sgdc

架构:
- Java/JSP 站点，列表通过 AJAX POST API 加载
- API: POST /portal/securitycontrol/getParentAndChildNews
  参数: id=sgdc, type=2, limit=15, pageNo=N
  返回: {data: {count, resultList: [{articletitle, articleid, createdate, ...}]}}
- 详情页 URL: /portal/index/getPortalNewsDetails?id={articleid}&categoryid={categoryid}
- PDF 附件: /portal/upload/downloadFile?id={uuid}
- 详情页正文极少，事故报告全文在 PDF 中

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
from construction_agent.collector.classifier import classify_v2, is_construction_candidate
from construction_agent.collector.pdf_text import extract_pdf_text

from construction_agent.collector.base import BaseCollector, RawAccidentText
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

# ── PDF 文本提取 ──
# 本地那份已收敛到 `construction_agent.collector.pdf_text`（2026-10-01）。

# (classify_v2 已迁移至 construction_agent.collector.classifier)

# (is_construction_candidate 已迁移至 classifier 模块)


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

    def collect(self) -> list[RawAccidentText]:
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
            pdf_url = ""
            local_path = ""

            if detail:
                detail_title = detail.get("title") or title
                detail_date = detail.get("date") or date_str
                detail_source = detail.get("source") or detail_source
                detail_text = detail.get("text") or ""
                pdf_url = detail.get("pdf_url") or ""

            # ── 下载 PDF 并提取文本 ──
            pdf_text = ""
            if pdf_url:
                pdf_text, local_path = self._process_pdf(pdf_url, title, detail_date)
                if pdf_text:
                    detail_text = pdf_text

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
            elif not detail_text and pdf_url:
                classification, reason_c, conf, status = "pending_review", "PDF文本提取失败", "low", "review"
            else:
                classification, reason_c, conf, status = "pending_review", "无正文内容", "low", "review"

            # ── 构建内容 ──
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
                "title": detail_title, "content": "\n".join(parts)[:50000],
                "source_url": detail_url, "source_name": "hebei_em",
                "publish_date": detail_date, "status": status,
                "pdf_urls": [pdf_url] if pdf_url else [],
                "needs_ocr": bool(pdf_url) and not detail_text,
                "issuing_authority": detail_source,
                "classification": classification,
                "classification_reason": reason_c,
                "classification_confidence": conf,
                "review_note": "", "local_file_path": local_path,
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

        # PDF 附件: /portal/upload/downloadFile?id={uuid}
        pdf_url = ""
        pdf_match = re.search(r'["\'](/portal/upload/downloadFile\?id=[^"\']+)["\']', html)
        if pdf_match:
            pdf_url = urljoin(HBEM_BASE, pdf_match.group(1))

        return {"title": title, "date": date, "source": source, "text": text, "pdf_url": pdf_url}

    def _process_pdf(self, pdf_url: str, title: str, pub_date: str) -> tuple[str, str]:
        url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        local_path = self.data_dir / f"{pub_date}_{safe_title}_{url_hash}.pdf"
        if local_path.exists():
            return extract_pdf_text(local_path), str(local_path)
        try:
            resp = self._session.get(pdf_url)
            if resp.status_code == 200 and len(resp.content) >= 100:
                local_path.write_bytes(resp.content)
                logger.info(f"    PDF: {local_path.name} ({len(resp.content)} bytes)")
                return extract_pdf_text(local_path), str(local_path)
        except Exception as e:
            logger.error(f"    PDF异常: {e}")
        return "", ""

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
