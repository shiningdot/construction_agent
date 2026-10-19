"""湖北省应急管理厅 (yjt.hubei.gov.cn) 事故调查报告采集器。

目标: https://yjt.hubei.gov.cn/yjgl/aqsc/sgdc/
TRS CMS, 单页25条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class HubEMCollector(TrsCmsCollector):
    """湖北省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="hub_em",
            authority_name="湖北省应急管理厅",
            base_url="https://yjt.hubei.gov.cn",
            list_url="https://yjt.hubei.gov.cn/yjgl/aqsc/sgdc/",
            list_page_pattern="",
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "hub_em"))


def crawl_hub_em(delay: float = 1.0) -> list:
    return HubEMCollector(delay=delay).collect()
