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
            list_page_pattern="",
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "cq_em"))


def crawl_cq_em(delay: float = 1.0) -> list:
    return CqEMCollector(delay=delay).collect()
