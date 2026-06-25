"""V4Pipeline: Agent 驱动的提取 Pipeline。"""

import time
from typing import Optional

from construction_agent.versions.base import BasePipeline, PipelineResult, BatchResult
from construction_agent.versions.registry import register
from construction_agent.versions.v4.agent.core import AgentCore


@register
class V4Pipeline(BasePipeline):
    """V4 Agent Pipeline: 自主决策的 ReAct 循环 Agent。"""
    version = "v4"

    def __init__(self):
        self.agent = AgentCore()

    def process_single(self, text: str, **kwargs) -> PipelineResult:
        t0 = time.time()
        try:
            result = self.agent.extract_structured(text)
            elapsed = time.time() - t0
            if result.get("success"):
                from construction_agent.models.accident import Accident
                accident = Accident(**result["accident"])
                return PipelineResult(success=True, accident=accident, processing_time=elapsed, version=self.version)
            return PipelineResult(success=False, error_message=result.get("error","提取失败"), processing_time=elapsed, version=self.version)
        except Exception as e:
            return PipelineResult(success=False, error_message=str(e), processing_time=time.time()-t0, version=self.version)

    def get_info(self) -> dict:
        return {
            "version": "v4",
            "description": "Agent-driven extraction (ReAct loop + tools)",
            "model": "claude-sonnet-4-6 (via V2 extraction)",
        }
