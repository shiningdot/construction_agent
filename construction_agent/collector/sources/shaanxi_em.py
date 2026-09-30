"""陕西省应急管理厅 (yjt.shaanxi.gov.cn) 事故调查报告采集器。

目标: https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/
TRS CMS, 分页(index_1~index_N), 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class ShaanxiEMCollector(TrsCmsCollector):
    """陕西省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="shaanxi_em",
            authority_name="陕西省应急管理厅",
            base_url="https://yjt.shaanxi.gov.cn",
            list_url="https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/index_1.html",
            list_page_pattern="index_{n}.html",
            list_page_start=2,
            list_page_max_probe=20,
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "shaanxi_em"))


def crawl_shaanxi_em(delay: float = 1.0) -> list:
    return ShaanxiEMCollector(delay=delay).collect()
