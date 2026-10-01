"""易安网 (esafety.cn) 建筑施工事故报告采集器。

目标: https://www.esafety.cn/law/accident-reports/search?industry=建筑施工

架构:
- Next.js 14 纯客户端渲染, 数据通过 API 获取
- API 需要 JWT Token 认证（从浏览器 localStorage 获取）
- 列表: POST /api/dmplp/open/pc/accident-report/page
- 详情: GET /api/dmplp/open/pc/accident-report/{id}
- 报告内容以图片形式存储(CDN), 文字 content 为空
- 421条建筑施工事故, pageSize=100 仅需5页

认证方式: Authorization header 直接传 token, 不需要 Bearer 前缀
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── URL 常量 ──
ESAFETY_BASE = "https://www.esafety.cn"
ESAFETY_PAGE_API = "/api/dmplp/open/pc/accident-report/page"
ESAFETY_DETAIL_API = "/api/dmplp/open/pc/accident-report"
# 建筑施工行业 ID (POST body 使用中文原文)
INDUSTRY_CONSTRUCTION = "建筑施工"


class EsafetyCollector(BaseCollector):
    """易安网建筑施工事故报告采集器。

    数据流:
        POST 分页API → 解析 JSON → GET 详情API → RawAccidentStore

    用法:
        collector = EsafetyCollector(token="eyJ...")
        stats = collector.collect()
    """

    def __init__(
        self,
        token: str = "",
        delay: float = 0.5,
        page_size: int = 100,
        fetch_details: bool = True,  # 是否获取详情页(含图片URL)
    ):
        self._token = token
        self._delay = delay
        self._page_size = min(page_size, 100)
        self._fetch_details = fetch_details

        # HTTP 客户端
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.esafety.cn",
            "Referer": "https://www.esafety.cn/law/accident-reports/search",
        }
        if token:
            headers["Authorization"] = token  # 不带 Bearer 前缀!

        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers=headers,
        )

        # 原始数据缓存
        self.raw_store = RawAccidentStore()

        # 状态统计
        self._total_api: int = 0
        self._collected: int = 0
        self._errors: int = 0

    # ── 兼容 Web GUI ──
    @property
    def start_page(self) -> int:
        return 1

    @start_page.setter
    def start_page(self, value: int):
        pass

    @property
    def end_page(self) -> int:
        return 1

    @end_page.setter
    def end_page(self, value: int):
        pass

    def source_name(self) -> str:
        return "esafety"

    @property
    def collected_count(self) -> int:
        return self._collected

    def collect(self) -> list[RawAccidentText]:
        """执行采集，返回统计字典。"""
        t_start = time.time()

        if not self._token:
            logger.error("未配置认证 Token, 无法采集")
            return self._make_stats(0)

        logger.info("开始采集易安网建筑施工事故报告...")

        # Step 1: 获取第一页确定总页数
        page1 = self._fetch_page(1)
        if not page1 or "data" not in page1:
            logger.error("API 请求失败")
            return self._make_stats(0)

        data = page1["data"]
        total = data.get("total", 0)
        total_pages = data.get("pages", 0)
        self._total_api = total
        logger.info(f"API 返回: total={total}, pages={total_pages}")

        # Step 2: 逐页采集
        all_items = data.get("list", [])
        for page in range(2, total_pages + 1):
            time.sleep(self._delay)
            page_data = self._fetch_page(page)
            if page_data and "data" in page_data:
                items = page_data["data"].get("list", [])
                all_items.extend(items)
                logger.info(f"  第{page}/{total_pages}页: +{len(items)}条")

        logger.info(f"列表采集完成: {len(all_items)} 条")

        # Step 3: 逐条获取详情并存储
        batch_items = []
        for item in all_items:
            report_id = item.get("id", "")
            title = item.get("title", "")
            exec_date = item.get("executionDate", "")

            # 可选项: 获取详情(含图片URL)
            detail = None
            image_urls = []
            if self._fetch_details and report_id:
                time.sleep(self._delay * 0.5)
                detail = self._fetch_detail(report_id)
                if detail and "data" in detail:
                    pages = detail["data"].get("regulationPageInfos") or []
                    # 去掉空串（2026-10-01 第二十八轮）：接口里某一页没有
                    # `imageUrl` 时会交出 `''`，留着会让下面每一个判据都失真
                    # ——`needs_ocr` 因为一个空串标成「要 OCR」、`content` 里
                    # 写「[图片报告 1页]」却列不出地址、登记时还得靠 asset 层
                    # 再滤一次（`_assets_from` 确实滤了，但那是别人的防御）。
                    image_urls = [(p.get("imageUrl") or "").strip() for p in pages]
                    image_urls = [u for u in image_urls if u]

            # 构建记录
            content_text = title
            if image_urls:
                content_text = (
                    f"[图片报告 {len(image_urls)}页] {title}\n"
                    + "\n".join(image_urls[:5])
                )
                if len(image_urls) > 5:
                    content_text += f"\n... 共 {len(image_urls)} 页"

            record = {
                "title": title,
                "content": content_text[:50000],
                "source_url": f"{ESAFETY_BASE}/law/accident-reports/detail/{report_id}",
                "source_name": "esafety",
                "publish_date": exec_date,
                "status": "new",
                "needs_ocr": len(image_urls) > 0,
                # ── 图片 URL **不能放在 `pdf_urls` 里**（2026-10-01 第二十八轮改）
                #
                # `executor._ASSET_KEYS` 是**按键名**认类型的——放在 `pdf_urls`
                # 里就登记成 `asset_type='pdf'`，阶段② 会拿这批「PDF」去下载并
                # 解析，一份也解不出来。§2.5.15 第十四轮把这条登记为「潜在缺陷」
                # （该源当时无 token、0 条产出，路上没人），到这里才轮上。
                #
                # `pdf_urls` 现在是**真的空**，不是漏了：本源走 API，没有任何
                # 附件文件，正文就是 `content` 里那几行标题与图片地址。
                "pdf_urls": [],
                "image_urls": image_urls,
                "report_id": report_id,
                "accident_type_id": item.get("accidentTypeId", ""),
                "accident_level_id": item.get("accidentLevelId", ""),
                "view_count": item.get("viewCount", 0),
            }

            batch_items.append(record)
            self._collected += 1

            if self._collected % 50 == 0:
                logger.info(f"  已处理 {self._collected}/{len(all_items)} 条...")
                self.raw_store.add_batch(batch_items)
                batch_items = []

        # 保存剩余
        if batch_items:
            self.raw_store.add_batch(batch_items)
        self.raw_store._save()

        logger.info(f"采集完成: {self._collected} 条")

        return self._make_stats(time.time() - t_start)

    def _fetch_page(self, page: int) -> Optional[dict]:
        """获取一页列表数据。"""
        body = {
            "pageNum": page,
            "pageSize": self._page_size,
            "industry": INDUSTRY_CONSTRUCTION,
        }
        try:
            resp = self._client.post(
                f"{ESAFETY_BASE}{ESAFETY_PAGE_API}",
                json=body,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("errcode") == 0:
                    return data
                else:
                    logger.warning(f"API 错误: {data.get('errmsg')}")
            else:
                logger.warning(f"API HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"API 请求失败: {e}")
            self._errors += 1
        return None

    def _fetch_detail(self, report_id: str) -> Optional[dict]:
        """获取单条报告详情。"""
        try:
            resp = self._client.get(
                f"{ESAFETY_BASE}{ESAFETY_DETAIL_API}/{report_id}"
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("errcode") == 0:
                    return data
        except Exception as e:
            logger.debug(f"详情请求失败 {report_id}: {e}")
        return None

    def _make_stats(self, duration: float) -> dict:
        cache_stats = self.raw_store.stats()
        return {
            "total_api": self._total_api,
            "total_crawled": self._collected,
            "errors": self._errors,
            "cached_total": cache_stats["total"],
            "cached_new": cache_stats["new"],
            "duration_seconds": round(duration, 1),
        }

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════

def crawl_esafety(token: str = "", delay: float = 0.5) -> dict:
    """同步入口。"""
    c = EsafetyCollector(token=token, delay=delay)
    return c.collect()
