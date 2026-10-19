"""海南省应急管理厅 (yjglt.hainan.gov.cn) 事故调查报告采集器。

目标: https://yjglt.hainan.gov.cn/xxgk/sgdcbg/
TRS CMS, 单页25条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class HainEMCollector(TrsCmsCollector):
    """海南省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="hain_em",
            authority_name="海南省应急管理厅",
            base_url="https://yjglt.hainan.gov.cn",
            list_url="https://yjglt.hainan.gov.cn/xxgk/sgdcbg/",
            # **零基分页**。原注释写「单页」，而页面自己的脚本写着：
            #     createPageHTML(38, 2, 0, "index", "html")
            # 即 (总数 38, 总页数 2, 当前页索引 0, 文件名, 扩展名)——
            # 索引从 0 起、共 2 页。实测 19 条（2021-04…2026-09）+
            # `index_1.html` 18 条（2019-01…2020-09），日期正好接上。
            list_page_pattern="index_{n}.html",
            list_page_start=0,
            list_page_max_probe=30,
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "hain_em"))


def crawl_hain_em(delay: float = 1.0) -> list:
    """旧版兼容接口。"""
    return HainEMCollector(delay=delay).collect()
