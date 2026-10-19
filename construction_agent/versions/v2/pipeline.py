"""V2 Pipeline: Few-shot CoT 增强提取。

相对于 V1 的核心改进：
1. Chain-of-Thought 推理链 — 引导 LLM 逐步推理再输出
2. Few-shot 示例 — 5 个高质量示例嵌入系统提示词
3. 更精细的字段描述和分类引导
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

from construction_agent.llm_client import (
    create_llm_client, resolve_max_tokens, resolve_model,
)
from construction_agent.extraction.claude_extractor import EXTRACTION_TOOL
from construction_agent.models.accident import (
    Accident, AccidentExtractionResult,
)
from construction_agent.versions.base import BasePipeline, PipelineResult
from construction_agent.versions.registry import register

logger = __import__("logging").getLogger(__name__)


# ── V2 CoT System Prompt（含 Few-shot 示例） ──

V2_SYSTEM_PROMPT = """你是一名建筑施工安全事故分析专家。请按以下步骤推理，然后提取结构化数据。

## 分析步骤（Chain-of-Thought）

**步骤1 - 事故基本信息识别**
阅读文本，找出：发生时间（日期、时刻）、地点（省级、城市）、项目名称、工程类型（房屋建筑/市政/交通运输/水利/工业厂房/装饰装修/拆除/其他）、事故类型（坍塌/高处坠落/触电/机械伤害/起重伤害/物体打击/土方坍塌/火灾爆炸/中毒窒息/淹溺）

**步骤2 - 伤亡与等级判断**
死亡/受伤/被困人数。等级：一般(1-2死)、较大(3-9死)、重大(10-29死)、特别重大(30+死)

**步骤3 - 直接原因分析**
文本中导致事故的直接原因是什么？注意区分直接原因（脚手架坍塌）和深层原因（监管缺失）。

**步骤4 - 四维度致因分析**
1. 人的因素 — 违章操作、安全意识不足、技能不足、未佩戴PPE、违规指挥、疲劳作业
2. 设备/物因素 — 防护缺陷、脚手架/模板问题、临时设施问题、设备故障、安全装置失效
3. 管理因素 — 监管缺失、隐患排查不到位、培训不足、安全制度不健全、违法分包
4. 环境因素 — 地质条件不良、恶劣天气、作业环境复杂、照明不足
无相关信息就返回空数组。

**步骤5 - 其他信息**
天气（未提及填无法判断）、施工单位、施工阶段、建筑结构

## 参考示例

注意每个示例中的推理→输出过程：

示例1（坍塌）：输入"云南昆武高速桥墩倒塌事故。2022年3月15日14时30分许，云南省昆明市昆武高速公路施工段发生桥墩倒塌事故，造成4人死亡、3人受伤。"→推理：交通运输工程坍塌，较大事故。致因涉及技能不足、防护缺陷、监管缺失、排查不到位、地质不良。→输出对应JSON。

示例2（土方坍塌）：输入"南京一工地塌方1死4伤。2021年8月12日上午9时许，江苏省南京市一市政排水管网施工现场发生土方坍塌，造成1人死亡、4人受伤。工人在未按规定放坡的沟槽内作业时，两侧土体突然坍塌被埋。"→推理：市政工程沟槽坍塌，一般事故。致因涉及违章冒险作业、防护缺失、监管缺失、交底不足。→输出对应JSON。

示例3（高处坠落）：输入"杭州滨江一在建大楼突发坠井事故。2019年6月18日下午15时许，浙江省杭州市滨江区一在建商业大楼施工现场，一名工人在电梯井道作业时防护栏杆缺失，从12层坠落至底层，当场死亡。"→推理：房屋建筑高处坠落，一般事故。致因涉及防护缺陷、排查不到位。→输出对应JSON。

示例4（起重伤害）：输入"上海市质监局通报8.19起重机械倾覆事故。2019年8月19日上午10时许，上海市闵行区一建筑工地，塔吊在吊运钢筋过程中发生倾覆，造成2死1伤。原因为塔吊基础节螺栓松动，检查维护不到位。"→推理：房屋建筑塔吊倾覆，较大事故。致因涉及设备故障、排查不到位、制度不健全。→输出对应JSON。

示例5（中毒窒息）：输入"泰兴下水道施工事故1死2失踪。2020年7月3日上午11时许，江苏省泰兴市一污水管网工地，3名工人在井下因硫化氢中毒晕倒。井下通风不良，未进行气体检测，未佩戴防毒面具。未制定有限空间作业方案。"→推理：市政工程中毒窒息，较大事故。致因涉及未使用PPE、防护缺失、培训和预案缺失、环境复杂。→输出对应JSON。

## 输出要求
使用 extract_accident_info 工具输出。日期用ISO格式(YYYY-MM-DD)。无信息返回空数组，不编造。施工单位名称提取完整名称。直接原因要具体。"""


@register
class V2Pipeline(BasePipeline):
    """V2 Pipeline: Few-shot CoT 增强提取。

    改进：
    - CoT 五步推理链引导 LLM 逐步思考
    - 5 个高质量 few-shot 示例（覆盖主要事故类型）
    - 更精细的致因分类引导
    """

    version = "v2"

    def __init__(self, api_key: Optional[str] = None):
        self.client = create_llm_client(api_key=api_key)
        if self.client is None:
            logger.warning("API Key 未配置")

    def process_single(self, text: str, **kwargs) -> PipelineResult:
        if not self.client:
            return PipelineResult(success=False, error_message="API Key 未配置", version=self.version)

        t0 = time.time()

        try:
            response = self.client.messages.create(
                model=resolve_model(),
                max_tokens=resolve_max_tokens(),
                system=V2_SYSTEM_PROMPT,
                tools=[EXTRACTION_TOOL],
                messages=[{
                    "role": "user",
                    "content": f"请分析以下建筑施工事故：\n\n{text}",
                }],
            )

            result = self._parse_response(response)
            if result is None:
                return PipelineResult(
                    success=False, error_message="解析返回结果失败",
                    processing_time=time.time() - t0, version=self.version,
                )

            accident = result.to_accident(raw_input=text)
            elapsed = time.time() - t0

            return PipelineResult(
                success=True, accident=accident,
                processing_time=elapsed, version=self.version,
            )

        except Exception as e:
            logger.error(f"V2 提取异常: {e}", exc_info=True)
            return PipelineResult(
                success=False, error_message=f"{type(e).__name__}: {e}",
                processing_time=time.time() - t0, version=self.version,
            )

    def _parse_response(self, response) -> Optional[AccidentExtractionResult]:
        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_accident_info":
                try:
                    return AccidentExtractionResult(**block.input)
                except Exception as e:
                    logger.warning(f"tool_use 解析失败: {e}")
                    continue

            if block.type == "text":
                content = block.text
                for match in re.finditer(r'```(?:json)?\s*([\s\S]*?)```', content):
                    try:
                        data = json.loads(match.group(1))
                        return AccidentExtractionResult(**data)
                    except Exception:
                        continue
                brace = re.search(r'\{[\s\S]*\}', content)
                if brace:
                    try:
                        data = json.loads(brace.group(0))
                        return AccidentExtractionResult(**data)
                    except Exception:
                        continue

        return None

    def get_info(self) -> dict:
        return {
            "version": "v2",
            "description": "Few-shot CoT 增强提取（5个shots + 五步推理链）",
            "enhancements": [
                "CoT 五步推理链（基本信息→伤亡等级→直接原因→致因分析→输出）",
                "5 个高质量 few-shot 示例（坍塌/土方坍塌/高处坠落/起重伤害/中毒窒息）",
                "更精细的致因分类引导",
            ],
            "model": resolve_model(),
        }
