"""
实验二：提示策略定义

6 种策略，统一接口:
- Zero-shot 基础提取
- Few-shot (3 示例)
- CoT 五步推理链
- Self-Correction (自修正)
- Multi-Agent Debate (3 投票)
- dspy 优化提示词
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from construction_agent.evaluation.experiment1.model_clients import create_client
from construction_agent.evaluation.experiment1.config import CORE_MODELS, INFERENCE_PARAMS


def _clean_json(text: str) -> str:
    """从模型回复中提取 JSON 字符串"""
    if not text:
        return "{}"
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else "{}"


# ============================================================
# 统一系统提示词（所有策略共享 Schema 定义）
# ============================================================

SCHEMA_DEFINITION = """
## 输出 JSON Schema
{
  "title": "事故标题（必填）",
  "date": "日期 YYYY-MM-DD",
  "location": "省级行政区",
  "city": "地级市",
  "project_type": "工程类型",
  "construction_phase": "施工阶段",
  "accident_type": "事故类型枚举",
  "severity": "事故等级枚举",
  "deaths": 死亡人数,
  "injuries": 受伤人数,
  "direct_cause": "直接原因",
  "direct_economic_loss": 直接经济损失(万元),
  "contractor": "施工单位",
  "weather": "天气",
  "human_factors": [{"category": "类别", "description": "描述"}],
  "machine_factors": [],
  "management_factors": [],
  "environmental_factors": [],
  "summary": "事故摘要（必填）"
}

事故类型: 物体打击, 车辆伤害, 机械伤害, 起重伤害, 触电, 淹溺, 高处坠落, 坍塌, 放炮/爆破, 火灾爆炸, 其他伤害
事故等级: 一般事故, 较大事故, 重大事故, 特别重大事故

不要编造信息，不确定的字段填 null。"""


class Strategy(ABC):
    """策略基类"""

    def __init__(self, name: str, model_key: str = "deepseek_v4"):
        self.name = name
        config = [m for m in CORE_MODELS if m.key == model_key][0]
        self.client = create_client(config)

    @abstractmethod
    def extract(self, text: str) -> Dict[str, Any]:
        ...

    def _call(self, system: str, user: str) -> Dict[str, Any]:
        result = self.client.call(system_prompt=system, user_message=user)
        if not result["success"]:
            return {"error": result["error"], "raw": ""}
        try:
            parsed = json.loads(_clean_json(result["text"]))
            return parsed
        except json.JSONDecodeError:
            return {"error": "JSON 解析失败", "raw": result["text"]}


# ============================================================
# 策略 1: Zero-shot
# ============================================================

SYSTEM_ZERO = f"""你是一个建筑施工事故信息提取专家。从事故描述中提取结构化信息并输出 JSON。{SCHEMA_DEFINITION}"""


class ZeroShot(Strategy):
    def __init__(self, model_key: str = "deepseek_v4"):
        super().__init__("zero_shot", model_key)

    def extract(self, text: str) -> Dict[str, Any]:
        return self._call(SYSTEM_ZERO, f"提取以下事故信息：\n\n{text}")


# ============================================================
# 策略 2: Few-shot (3示例)
# ============================================================

EXAMPLES = [
    ("2024年3月13日，河北三河市一工地基坑坍塌，2死3伤。原因为基坑支护不到位。",
     {"title": "河北三河基坑坍塌事故", "accident_type": "坍塌", "severity": "一般事故",
      "deaths": 2, "injuries": 3, "location": "河北省", "city": "三河市",
      "direct_cause": "基坑支护不到位",
      "human_factors": [{"category": "违章操作", "description": "未按方案支护"}],
      "management_factors": [{"category": "监管缺失", "description": "现场安全管理不到位"}]}),
    ("2023年8月，广州一工地工人18层坠落身亡，安全带未挂设。",
     {"title": "广州工地高处坠落事故", "accident_type": "高处坠落", "severity": "一般事故",
      "deaths": 1, "injuries": 0, "location": "广东省", "city": "广州市",
      "direct_cause": "安全带未挂设",
      "human_factors": [{"category": "未使用PPE", "description": "高处作业未系安全带"}]}),
    ("2024年6月，上海地铁工地起重机倾覆，3死4伤。支腿下方地基松软。",
     {"title": "上海地铁工地起重机倾覆", "accident_type": "起重伤害", "severity": "较大事故",
      "deaths": 3, "injuries": 4, "location": "上海市",
      "direct_cause": "起重机支腿地基松软",
      "machine_factors": [{"category": "基础不牢", "description": "地基承载力不足"}],
      "management_factors": [{"category": "方案缺陷", "description": "未验算地基承载力"}]}),
]

FEW_SHOT_EXAMPLES_TEXT = "\n\n".join(
    f"示例{i+1}:\n输入:{ex[0]}\n输出:{json.dumps(ex[1], ensure_ascii=False)}"
    for i, ex in enumerate(EXAMPLES)
)

SYSTEM_FEW = f"""你是一个建筑施工事故信息提取专家。参考以下示例格式输出 JSON。{SCHEMA_DEFINITION}"""


class FewShot(Strategy):
    def __init__(self, model_key: str = "deepseek_v4"):
        super().__init__("few_shot", model_key)

    def extract(self, text: str) -> Dict[str, Any]:
        return self._call(SYSTEM_FEW, f"{FEW_SHOT_EXAMPLES_TEXT}\n\n请提取以下事故（严格按示例格式）：\n{text}")


# ============================================================
# 策略 3: CoT 五步推理链
# ============================================================

SYSTEM_COT = f"""你是一个建筑施工事故信息提取专家。请按以下五步推理链分析：

步骤1 — 事故识别：判断事故类型、等级、伤亡
步骤2 — 时空定位：提取时间、地点、项目、施工阶段
步骤3 — 直接原因：分析直接引发因素
步骤4 — 多维归因：从人/设备/管理/环境四维度分析致因
步骤5 — 结构化输出：整合为 JSON

先写推理过程（以"推理："开头），然后输出 JSON（以"输出："开头）。{SCHEMA_DEFINITION}"""


class CoT(Strategy):
    def __init__(self, model_key: str = "deepseek_v4"):
        super().__init__("cot", model_key)

    def extract(self, text: str) -> Dict[str, Any]:
        result = self.client.call(SYSTEM_COT, f"请分析以下施工事故：\n\n{text}")
        if not result["success"]:
            return {"error": result["error"]}
        raw = result["text"]
        # 从输出中提取 JSON
        if "输出：" in raw:
            json_str = raw.split("输出：")[-1].strip()
        else:
            json_str = _clean_json(raw)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"error": "JSON 解析失败", "raw": raw}


# ============================================================
# 策略 4: Self-Correction
# ============================================================


class SelfCorrect(Strategy):
    def __init__(self, model_key: str = "deepseek_v4"):
        super().__init__("self_correct", model_key)
        self.base = FewShot(model_key)

    def extract(self, text: str) -> Dict[str, Any]:
        first = self.base.extract(text)
        if "error" in first:
            return first

        correct_msg = (
            f"原始文本:\n{text}\n\n"
            f"初次提取结果:\n{json.dumps(first, ensure_ascii=False, indent=2)}\n\n"
            "请审查以上结果，检查：\n"
            "1. 事故类型和等级是否正确？\n"
            "2. 致因分类是否到了正确维度？\n"
            "3. 是否有遗漏字段？\n"
            "输出修正后的完整 JSON。"
        )
        result = self.client.call(
            "你是一个事故提取质量审查专家。检查并修正提取结果。",
            correct_msg,
        )
        if not result["success"]:
            return first
        try:
            return json.loads(_clean_json(result["text"]))
        except json.JSONDecodeError:
            return first


# ============================================================
# 策略 5: Multi-Agent Debate
# ============================================================


class Debate(Strategy):
    def __init__(self, model_key: str = "deepseek_v4"):
        super().__init__("debate", model_key)
        self.agents = [ZeroShot(model_key), FewShot(model_key), CoT(model_key)]

    def extract(self, text: str) -> Dict[str, Any]:
        results = [a.extract(text) for a in self.agents]
        judge_prompt = (
            f"三个 Agent 分别提取了事故信息，结果如下：\n\n"
            f"Agent1:\n{json.dumps(results[0], ensure_ascii=False, indent=2)}\n\n"
            f"Agent2:\n{json.dumps(results[1], ensure_ascii=False, indent=2)}\n\n"
            f"Agent3:\n{json.dumps(results[2], ensure_ascii=False, indent=2)}\n\n"
            f"请分析一致性。一致字段直接采用；不一致字段根据原始文本裁决。\n"
            f"原始文本:\n{text}\n\n输出最终 JSON。"
        )
        result = self.client.call("你是事故提取裁决专家。", judge_prompt)
        if not result["success"]:
            return results[0]
        try:
            return json.loads(_clean_json(result["text"]))
        except json.JSONDecodeError:
            return results[0]


# ============================================================
# 策略 6: dspy 优化提示词
# ============================================================

SYSTEM_DSPY = f"""你是一个建筑施工事故信息提取专家。严格按以下优化指令执行：

[强制规则]
1. 事故类型：从描述关键词判定（坍塌/坠落/触电等）
2. 事故等级：根据死亡人数自动推算
3. 致因分析：必须从人/设备/管理/环境四个维度分析，每维至少考虑是否有关联
4. direct_cause 不得为空
5. 城市信息必须提取（如文中提及）

{SCHEMA_DEFINITION}"""


class DspyOpt(Strategy):
    def __init__(self, model_key: str = "deepseek_v4"):
        super().__init__("dspy", model_key)

    def extract(self, text: str) -> Dict[str, Any]:
        return self._call(SYSTEM_DSPY, f"应用优化规则，提取以下事故：\n\n{text}")


# ============================================================
# 工厂函数
# ============================================================

STRATEGY_MAP = {
    "zero_shot": ZeroShot,
    "few_shot": FewShot,
    "cot": CoT,
    "self_correct": SelfCorrect,
    "debate": Debate,
    "dspy": DspyOpt,
}

STRATEGY_NAMES = {
    "zero_shot": "Zero-shot 零样本",
    "few_shot": "Few-shot 3示例",
    "cot": "CoT 五步推理",
    "self_correct": "Self-Correction 自修正",
    "debate": "Multi-Agent 辩论",
    "dspy": "dspy 优化提示词",
}


def get_strategy(name: str, model_key: str = "deepseek_v4") -> Strategy:
    if name not in STRATEGY_MAP:
        raise ValueError(f"未知策略: {name}，可选: {list(STRATEGY_MAP.keys())}")
    return STRATEGY_MAP[name](model_key)


def list_strategies() -> List[str]:
    return list(STRATEGY_MAP.keys())
