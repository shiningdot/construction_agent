"""贵州省应急管理厅 (yjgl.guizhou.gov.cn) 事故调查报告采集器。

目标: https://yjgl.guizhou.gov.cn/gk/zfxxgk/fdzdgk/dcbg_5888759/
TRS CMS, 单页25条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class GzEMCollector(TrsCmsCollector):
    """贵州省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="gz_em",
            authority_name="贵州省应急管理厅",
            base_url="https://yjgl.guizhou.gov.cn",
            list_url="https://yjgl.guizhou.gov.cn/gk/zfxxgk/fdzdgk/dcbg_5888759/",
            # **零基分页**，与 `cq_em` 同：`list_url` 第 1 页，`index_{n-1}.html` 第 n 页。
            # 原写 `""`（当单页），实际共 3 页 33 条（实测 15 + 15 + 3）。
            # 页面自己的证据最直接：「下一页」按钮 href 指向 `index_1.html`，
            # 分页条上有 `pagenav_0` / `pagenav_1` / `pagenav_2` 三个页码。
            list_page_pattern="index_{n}.html",
            list_page_start=0,
            list_page_max_probe=30,
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "gz_em"))


def crawl_gz_em(delay: float = 1.0) -> list:
    return GzEMCollector(delay=delay).collect()
