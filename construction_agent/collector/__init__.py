"""建筑施工事故数据采集模块。

主动从多源采集建筑施工事故原始数据，经清洗/去重/规则提取后，
将低置信度字段提交 LLM 增强，最终存入统一数据集。

架构：
    采集层（各 Source Collector）→ 清洗层（Cleaners）→ 提取层（LLM）→ 存储层（DatasetManager）
"""

from construction_agent.collector.pipeline import CollectorPipeline
from construction_agent.collector.base import BaseCollector, RawAccidentText

__all__ = [
    "CollectorPipeline",
    "BaseCollector",
    "RawAccidentText",
]
