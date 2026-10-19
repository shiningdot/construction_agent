"""V3 Pipeline：多技能协同提取（HFACS-CS Skills）。

流程：
1. Coordinator 接收事故文本
2. 依次调用 6 个 HFACS-CS 技能（或并行调用）
3. 每个技能独立提取其层级的致因
4. 合并所有技能结果 → Accident 对象
5. 自动派生字段（星期/季节/时段/光照）

与 V1 的关键区别：
  V1: 1 次 API 调用 + 1 个大提示词
  V3: N 次 API 调用 + N 个专注提示词（每个技能独立）
"""

from __future__ import annotations

import time
from typing import Optional

from construction_agent.models.accident import (
    Accident,
    HumanFactor,
    MachineFactor,
    ManagementFactor,
    EnvironmentalFactor,
)
from construction_agent.versions.base import BasePipeline, PipelineResult, BatchResult
from construction_agent.versions.registry import register
from construction_agent.versions.v3.skills import ALL_SKILLS
from construction_agent.llm_client import create_llm_client, resolve_model


# ── HFACS 编码 → V1 四维度映射 ──
# 用于将 HFACS-CS 结果存储到现有的 Accident 模型

CODE_TO_DIMENSION = {
    # Level 1 组织因素 → 管理因素
    "O1": "management", "O2": "management", "O3": "management", "O4": "management",
    # Level 2 不安全监督 → 管理因素
    "S1": "management", "S2": "management", "S3": "management", "S4": "management", "S5": "management",
    # Level 3 前提条件 → 人的因素
    "P1": "human", "P2": "human", "P3": "human", "P4": "human",
    # Level 4 不安全行为 → 人的因素
    "A1": "human", "A2": "human", "A3": "human", "A4": "human", "A5": "human",
    # Level 5 现场条件 → 设备因素
    "W1": "machine", "W2": "machine", "W3": "machine", "W4": "machine", "W5": "machine", "W6": "machine",
    # Level 6 物理环境 → 环境因素
    "E1": "environmental", "E2": "environmental", "E3": "environmental", "E4": "environmental", "E5": "environmental",
}

DIMENSION_CLASSES = {
    "human": ("human_factors", HumanFactor),
    "machine": ("machine_factors", MachineFactor),
    "management": ("management_factors", ManagementFactor),
    "environmental": ("environmental_factors", EnvironmentalFactor),
}

# 编码 → 类别名称映射
CODE_TO_NAME = {}
for skill_cls in ALL_SKILLS:
    for code, name_cn, _ in skill_cls.subcategories:
        CODE_TO_NAME[code] = name_cn


@register
class V3Pipeline(BasePipeline):
    """V3 Skills Pipeline：多技能协同提取（HFACS-CS 六层模型）。

    特点：
    - 6 个独立技能，每个专注一个 HFACS-CS 层级
    - 提示词更短（聚焦单个维度），提取精度更高
    - 支持消融实验（移除某技能观察效果变化）
    - 结果自动映射到 V1 四维度模型兼容格式
    """

    version = "v3"

    def __init__(self, api_key: Optional[str] = None):
        self.client = create_llm_client(api_key=api_key)
        if self.client is None:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("LLM API Key 未配置")

    def process_single(self, text: str, **kwargs) -> PipelineResult:
        """处理单条事故文本。

        依次调用 6 个技能，合并结果。
        """
        if not self.client:
            return PipelineResult(
                success=False,
                error_message="API Key 未配置",
                version=self.version,
            )

        t0 = time.time()

        # 调用所有技能
        all_results = {}
        skill_errors = []

        for skill_cls in ALL_SKILLS:
            skill = skill_cls()
            try:
                found = skill.extract(text, self.client)
                if found is not None:
                    all_results[skill.level] = found
                else:
                    skill_errors.append(f"{skill.name}: 返回None")
            except Exception as e:
                skill_errors.append(f"{skill.name}: {e}")

        # 合并到 V1 四维度格式
        factors = {
            "human": [],
            "machine": [],
            "management": [],
            "environmental": [],
        }

        hfacs_codes = []  # 保存原始 HFACS 编码

        for level, causes in all_results.items():
            for cause in causes:
                code = cause.get("code", "")
                desc = cause.get("description", "")
                if not code:
                    continue
                hfacs_codes.append(code)
                dim = CODE_TO_DIMENSION.get(code, "management")
                cat_name = CODE_TO_NAME.get(code, code)
                factors[dim].append({
                    "category": cat_name,
                    "description": desc,
                })

        # 构造 Accident（沿用 V1 模型，填充致因字段）
        accident = Accident(
            title="(V3多技能提取)",
            accident_type="",
            severity="",
            deaths=0,
            injuries=0,
            summary="",
            human_factors=[HumanFactor(**f) for f in factors["human"]],
            machine_factors=[MachineFactor(**f) for f in factors["machine"]],
            management_factors=[ManagementFactor(**f) for f in factors["management"]],
            environmental_factors=[EnvironmentalFactor(**f) for f in factors["environmental"]],
            raw_input=text,
        )
        # 致因来源信息（V3 专用）
        accident.id = f"v3_{hash(text) % 10**8:08x}"
        accident.auto_derive_fields()

        elapsed = time.time() - t0

        result = PipelineResult(
            success=True,
            accident=accident,
            processing_time=elapsed,
            version=self.version,
        )

        # 附加 HFACS 编码（用于调试和分析）
        result._hfacs_codes = hfacs_codes
        result._skill_errors = skill_errors

        return result

    def get_info(self) -> dict:
        """版本信息。"""
        return {
            "version": "v3",
            "description": "多技能协同提取（HFACS-CS 六层 Skills）",
            "extraction_method": "6 × Claude API (tool_use) + Coordinator",
            "skills": [
                f"L{s.level} {s.name} ({len(s.subcategories)} subcategories)"
                for s in [cls() for cls in ALL_SKILLS]
            ],
            "model": resolve_model(),
        }
