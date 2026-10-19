"""重庆市应急管理局 (yjj.cq.gov.cn) 事故调查报告采集器。

目标: https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/
TRS CMS, 单页25条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class CqEMCollector(TrsCmsCollector):
    """重庆市应急管理局事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="cq_em",
            authority_name="重庆市应急管理局",
            base_url="https://yjj.cq.gov.cn",
            list_url="https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/",
            # **零基分页**：`list_url` 是第 1 页，第 n 页是 `index_{n-1}.html`。
            # 原写 `""`（当单页），实际共 3 页 42 条（实测 15 + 15 + 12）。
            # 证据：`index_1.html`（15 条，2024-06…2025-08）接在第 1 页
            # （15 条，2025-08-07…2026-09-18）后面，最旧 2025-08-07 → 最新
            # 2025-08-05，正好接上；`index_2.html` 12 条 2021-05…2023-12。
            # 所以 `list_page_start=0`——从 n=1 起探，n=1 即第 2 页。
            list_page_pattern="index_{n}.html",
            list_page_start=0,
            list_page_max_probe=30,
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "cq_em"))


def crawl_cq_em(delay: float = 1.0) -> list:
    return CqEMCollector(delay=delay).collect()
