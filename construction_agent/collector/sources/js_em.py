"""江苏省应急管理厅 (yjglt.jiangsu.gov.cn) 事故调查处理采集器。

目标: https://yjglt.jiangsu.gov.cn/col/col81395/index.html
TRS CMS, 45条/页, 基于 TrsCmsCollector 通用基类。
"""

from __future__ import annotations

from pathlib import Path

from construction_agent.collector.sources.trs_cms_base import (
    TrsCmsCollector,
    TrsSiteConfig,
)
from construction_agent.config import settings

# 江苏省建筑施工关键词（含本省特色关键词）
JS_CONSTRUCTION_KW = [
    "建筑", "建设", "施工", "坍塌", "倒塌", "工地", "坠落", "工程",
    "地铁", "电梯", "拆除", "基坑", "模板", "脚手架", "项目",
]


class JsEMCollector(TrsCmsCollector):
    """江苏省应急管理厅事故调查处理采集器。"""

    def __init__(self, delay: float = 1.0, data_dir: Path | None = None):
        config = TrsSiteConfig(
            source_name="js_em",
            authority_name="江苏省应急管理厅",
            base_url="https://yjglt.jiangsu.gov.cn",
            list_url="https://yjglt.jiangsu.gov.cn/col/col81395/index.html",
            list_page_pattern="",   # 无分页模式，使用 URL 参数
            list_link_pattern=r"/art/\d{4}/\d{1,2}/\d{1,2}/art_\d+_\d+\.html",
            construction_keywords=JS_CONSTRUCTION_KW,
            delay=delay,
        )
        super().__init__(config, data_dir=data_dir or (settings.DATA_DIR / "js_em"))


def crawl_js_em(delay: float = 1.0) -> list:
    """旧版兼容接口。"""
    return JsEMCollector(delay=delay).collect()
