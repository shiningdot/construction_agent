"""上海市应急管理局 (yjglj.sh.gov.cn) 事故调查报告采集器。

目标: https://yjglj.sh.gov.cn/xxgk/zfxxgk/zcwj/sgdcbg/

7页, 15条/页, 纯HTML. 详情含PDF附件.
基于 TrsCmsCollector 通用基类，仅需站点配置。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class ShEMCollector(TrsCmsCollector):
    """上海市应急管理局事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="sh_em",
            authority_name="上海市应急管理局",
            base_url="https://yjglj.sh.gov.cn",
            list_url="https://yjglj.sh.gov.cn/xxgk/zfxxgk/zcwj/sgdcbg/",
            list_page_pattern="index_{n}.html",
            # `list_page_start` 是「`list_url` 对应的页码」，而上面这个 list_url
            # 是目录（`…/sgdcbg/`），即第 1 页。原先写 2，`_discover_pages` 从
            # `start + 1` 起探，于是**第 2 页从未被抓过**。
            list_page_start=1,
            # 实测末页在第 7 页（第 8 页 404）。和上面同理，这只是保险上限。
            list_page_max_probe=30,
            list_link_pattern=r"/sgdcbg/\d{8}/",
            delay=delay,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "sh_em"))


def crawl_sh_em(delay: float = 1.0) -> list:
    """旧版兼容接口。"""
    return ShEMCollector(delay=delay).collect()
