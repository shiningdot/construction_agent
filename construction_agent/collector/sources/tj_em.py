"""天津市应急管理局 (yjgl.tj.gov.cn) 事故调查报告采集器。

目标: https://yjgl.tj.gov.cn/ZWGK6939/SGDCBG354/
政府CMS, 单页10条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings

# 天津市建筑施工关键词
TJ_CONSTRUCTION_KW = [
    "建筑", "建设", "施工", "坍塌", "倒塌", "工地", "坠落", "工程",
    "地铁", "电梯", "拆除", "基坑", "模板", "脚手架", "二期", "项目",
]


class TjEMCollector(TrsCmsCollector):
    """天津市应急管理局事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="tj_em",
            authority_name="天津市应急管理局",
            base_url="https://yjgl.tj.gov.cn",
            list_url="https://yjgl.tj.gov.cn/ZWGK6939/SGDCBG354/",
            # **零基分页**。原注释写「单页」，实际 2 页 15 条（实测 8 + 7）。
            # 证据是接缝：第 1 页最旧 2021-12-24，`index_1.html` 最新 2021-12-17，
            # 七天之隔正好接上，且两个集合无一条重复。
            list_page_pattern="index_{n}.html",
            list_page_start=0,
            list_page_max_probe=30,
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            construction_keywords=TJ_CONSTRUCTION_KW,
            delay=delay,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "tj_em"))


def crawl_tj_em(delay: float = 1.0) -> list:
    """旧版兼容接口。"""
    return TjEMCollector(delay=delay).collect()
