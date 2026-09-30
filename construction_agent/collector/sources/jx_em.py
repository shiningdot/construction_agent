"""江西省应急管理厅 (yjglt.jiangxi.gov.cn) 事故调查报告采集器。

目标: https://yjglt.jiangxi.gov.cn/jxsyjglt/col/251210dxsgal/
TRS CMS, 单页25条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class JxEMCollector(TrsCmsCollector):
    """江西省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="jx_em",
            authority_name="江西省应急管理厅",
            base_url="https://yjglt.jiangxi.gov.cn",
            list_url="https://yjglt.jiangxi.gov.cn/jxsyjglt/col/251210dxsgal/",
            list_page_pattern="",
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "jx_em"))


def crawl_jx_em(delay: float = 1.0) -> list:
    return JxEMCollector(delay=delay).collect()
