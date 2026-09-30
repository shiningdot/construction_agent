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
            # `list_page_start` 是「`list_url` 对应的页码」——上面是 `index_1.html`，
            # 即第 1 页。原先写 2，`_discover_pages` 从 `start + 1` 起探，
            # 于是**第 2 页从未被抓过**（且当时整段分页都没跑起来，见 `_build_page_url`）。
            list_page_start=1,
            # 实测末页在第 24 页（第 25 页 404）。原值 20 会把它截掉 4 页约 80 条——
            # `_discover_pages` 靠「非 200 就停」自然收敛，这个值只是保险上限。
            list_page_max_probe=30,
            list_link_pattern=r"/\d{6}/t\d{8}_\d+\.html",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "shaanxi_em"))


def crawl_shaanxi_em(delay: float = 1.0) -> list:
    return ShaanxiEMCollector(delay=delay).collect()
