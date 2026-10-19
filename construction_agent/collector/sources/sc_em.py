"""四川省应急管理厅 (yjt.sc.gov.cn) 事故调查报告采集器。

目标: https://yjt.sc.gov.cn/scyjt/dcbg/GovInfoPublic.shtml
TRS CMS, 单页25条, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings


class ScEMCollector(TrsCmsCollector):
    """四川省应急管理厅事故调查报告采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="sc_em",
            authority_name="四川省应急管理厅",
            base_url="https://yjt.sc.gov.cn",
            list_url="https://yjt.sc.gov.cn/scyjt/dcbg/GovInfoPublic.shtml",
            list_page_pattern="",
            # 原值 r"/\d{6}/t\d{8}_\d+\.html" 是另一种 CMS 的格式，本页一条都不匹配
            # ——列表页 HTTP 200、54 KB、79 个 <a>，正则命中 0，于是整个源恒返回空。
            # 实测该站详情页形如 /scyjt/dcbg/2023/8/14/<32 位 hex>.shtml。
            # 同构的 PDF 直链（…/<hex>/files/xxx.pdf）本正则不收：它没有配套的
            # 详情解析，收进来只会多出一条有标题、无正文的记录。
            list_link_pattern=r"/scyjt/dcbg/\d{4}/\d{1,2}/\d{1,2}/[0-9a-f]{16,}\.shtml",
            delay=delay,
            timeout=30,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "sc_em"))


def crawl_sc_em(delay: float = 1.0) -> list:
    return ScEMCollector(delay=delay).collect()
