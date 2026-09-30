"""山西省应急管理厅 (yjt.shanxi.gov.cn) 事故调查报告采集器。

目标: https://yjt.shanxi.gov.cn/zwgk_0/zfxxgk_78327/fdzdgknr_78331/gszl/sgdc_78341/
TRS CMS, 单页15条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings

# 山西省建筑施工关键词（含煤矿特色）
SX_CONSTRUCTION_KW = [
    "建筑", "建设", "施工", "坍塌", "倒塌", "工地", "坠落", "工程",
    "煤矿", "矿山", "矿井", "隧道", "桥梁", "拆除", "基坑", "模板", "脚手架",
]


class SxEMCollector(TrsCmsCollector):
    """山西省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="sx_em",
            authority_name="山西省应急管理厅",
            base_url="https://yjt.shanxi.gov.cn",
            list_url="https://yjt.shanxi.gov.cn/zwgk_0/zfxxgk_78327/fdzdgknr_78331/gszl/sgdc_78341/",
            list_page_pattern="",  # 单页，无分页
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.shtml",
            construction_keywords=SX_CONSTRUCTION_KW,
            delay=delay,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "sx_em"))


def crawl_sx_em(delay: float = 1.0) -> list:
    """旧版兼容接口。"""
    return SxEMCollector(delay=delay).collect()
