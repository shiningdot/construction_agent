"""版本化 Pipeline 系统。

支持多版本演进（V1→V2→V3→V4），共享评测框架。
每个版本实现 BasePipeline 接口，可在相同测试集上对比评估。
"""

from construction_agent.versions.base import BasePipeline, PipelineResult
from construction_agent.versions.registry import get_pipeline, list_versions, register

__all__ = ["BasePipeline", "PipelineResult", "get_pipeline", "list_versions", "register"]
