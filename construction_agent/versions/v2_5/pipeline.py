"""V2.5 Pipeline: V2 提取 + HFACS-CS 编码。

流程：
1. V2 Pipeline（CoT + Few-shot）提取结构化和四维度致因
2. HfacsEncoder 对所有致因进行 HFACS-CS 编码
3. 编码结果存入 Accident 模型的 hfacs_* 字段
"""

from __future__ import annotations

import time
from typing import Optional

from construction_agent.models.accident import Accident
from construction_agent.versions.base import BasePipeline, PipelineResult
from construction_agent.versions.registry import register
from construction_agent.versions.v2.pipeline import V2Pipeline
from construction_agent.versions.v2_5.encoder import HfacsEncoder

logger = __import__("logging").getLogger(__name__)


@register
class V2_5Pipeline(BasePipeline):
    """V2.5 Pipeline: V2 提取 + HFACS-CS 编码。"""

    version = "v2.5"

    def __init__(
        self,
        api_key: Optional[str] = None,
        encoding_method: str = "llm",
    ):
        self.v2 = V2Pipeline(api_key=api_key)
        self.encoder = HfacsEncoder(api_key=api_key)
        self.encoding_method = encoding_method

    def process_single(self, text: str, **kwargs) -> PipelineResult:
        t0 = time.time()

        # Step 1: V2 提取
        v2_result = self.v2.process_single(text, **kwargs)
        if not v2_result.success or not v2_result.accident:
            elapsed = time.time() - t0
            v2_result.processing_time = elapsed
            v2_result.version = self.version
            return v2_result

        accident = v2_result.accident

        # Step 2: HFACS 编码
        try:
            hfacs_codes, hfacs_details = self.encoder.encode(
                title=accident.title,
                summary=accident.summary,
                human_factors=[f.model_dump() for f in accident.human_factors],
                machine_factors=[f.model_dump() for f in accident.machine_factors],
                management_factors=[f.model_dump() for f in accident.management_factors],
                environmental_factors=[f.model_dump() for f in accident.environmental_factors],
                method=self.encoding_method,
            )
            accident.hfacs_codes = hfacs_codes
            accident.hfacs_details = hfacs_details
            accident.hfacs_encoding_version = f"v2.5_{self.encoding_method}_v1"
        except Exception as e:
            logger.error(f"HFACS 编码异常: {e}")

        elapsed = time.time() - t0
        logger.info(
            f"V2.5: {accident.title[:40]} | "
            f"致因{len(accident.human_factors)+len(accident.machine_factors)+len(accident.management_factors)+len(accident.environmental_factors)}项 "
            f"→ {len(hfacs_codes)}个HFACS编码 "
            f"({elapsed:.1f}s)"
        )
        return PipelineResult(
            success=True,
            accident=accident,
            processing_time=elapsed,
            version=self.version,
        )

    def get_info(self) -> dict:
        base = self.v2.get_info()
        return {
            "version": "v2.5",
            "description": "V2 提取 + HFACS-CS 编码（6层29类）",
            "base_version": "v2",
            "encoding_method": self.encoding_method,
            "enhancements": base.get("enhancements", []) + [
                f"HFACS-CS 编码（{self.encoding_method}）",
            ],
        }
