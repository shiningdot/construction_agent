"""HFACS-CS 编码器：将 V2 提取的四维度致因映射到 HFACS-CS 标准编码。

策略：
1. 主要：LLM 编码 — 一条事故的全部致因一次 API 调用
2. 备选：关键词编码 — 使用 HfacsTaxonomy.classify() 关键词匹配
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from anthropic import Anthropic

from construction_agent.config import settings
from construction_agent.hfacs import HfacsTaxonomy
from construction_agent.models.accident import HfacsCode

logger = logging.getLogger(__name__)

# ── HFACS 层级信息 ──
CODE_PREFIX_MAP = {
    "O": (1, "组织因素"),
    "S": (2, "不安全监督"),
    "P": (3, "不安全行为的前提条件"),
    "A": (4, "不安全行为"),
    "W": (5, "施工现场条件"),
    "E": (6, "物理环境"),
}

# ── 完整系统提示词 ──
HFACS_ENCODING_SYSTEM_PROMPT = """你是一名建筑施工事故 HFACS-CS 编码专家。你的任务是将已提取的事故致因信息映射到 HFACS-CS 分类编码体系。

## HFACS-CS 完整分类体系（6层29类）

### Level 1 — 组织因素 (Organizational Factors) — 编码前缀 O
- O1 安全文化缺失: 安全主体责任不落实, 安全管理混乱, 安全投入不足
- O2 分包/承包商管理缺陷: 违法分包, 转包, 使用无资质承包商
- O3 工期/成本压力: 不合理压缩工期, 削减安全投入, 赶工期, 抢进度
- O4 培训体系不健全: 缺乏系统化安全培训, 培训体系缺失, 组织层面忽视安全教育

### Level 2 — 不安全监督 (Unsafe Supervision) — 编码前缀 S
- S1 现场监管缺失: 监管不到位, 无人监管, 现场缺乏有效监督, 监理形同虚设
- S2 隐患排查不充分: 未进行风险评估, 未发现隐患, 排查不到位, 检查走过场
- S3 安全制度/规程不健全: 应急预案缺失, 安全制度不健全, 操作规程缺失
- S4 违规组织施工: 违法建设, 未办理报建审批, 违规组织施工
- S5 安全培训/交底不足: 安全交底不足, 岗前培训未落实, 未进行技术交底

### Level 3 — 不安全行为的前提条件 (Preconditions) — 编码前缀 P
- P1 操作技能/经验不足: 技能不足, 经验不足, 不熟悉操作, 无证上岗
- P2 安全意识薄弱: 未意识到风险, 侥幸心理, 安全意识不强, 麻痹大意
- P3 生理/心理状态不佳: 疲劳作业, 酒后作业, 注意力下降, 带病作业
- P4 沟通/协调不足: 沟通不到位, 信号不清, 协调失误, 信息传递不畅

### Level 4 — 不安全行为 (Unsafe Acts) — 编码前缀 A
- A1 违章操作: 违规操作, 擅自操作, 违章作业, 违反规程
- A2 违规指挥: 强令冒险作业, 违规指挥, 违章指挥
- A3 冒险作业: 明知危险继续作业, 铤而走险, 侥幸涉险
- A4 未使用PPE: 未佩戴安全帽, 未系安全绳, 未穿防护装备
- A5 操作失误: 误操作, 不当操作, 操作错误, 判断失误

### Level 5 — 施工现场条件 (Site Conditions) — 编码前缀 W
- W1 脚手架/模板/支撑体系缺陷: 脚手架坍塌, 支撑失稳, 模板倒塌, 支撑承载力不足
- W2 临时设施/防护缺陷: 防护缺失, 安全网缺失, 临边防护缺失, 洞口防护不到位
- W3 设备/机具故障: 设备故障, 断裂, 失效, 倾覆, 安全装置失效
- W4 建/构筑物结构缺陷: 结构强度不足, 结构失稳, 建筑质量缺陷
- W5 材料/构件质量问题: 材料不合格, 质量缺陷, 连接不牢固, 构件强度不足
- W6 地基/基坑条件不良: 地基下陷, 基坑失稳, 边坡滑移, 围护结构变形

### Level 6 — 物理环境 (Physical Environment) — 编码前缀 E
- E1 不良地质条件: 地质条件差, 围岩破碎, 地下水丰富, 溶洞
- E2 恶劣天气: 降雨, 暴雨, 大风, 高温, 雨雪, 台风
- E3 作业空间/环境复杂: 受限空间, 高空作业, 交叉作业, 临水临边
- E4 照明/视线不足: 夜间施工, 照明不足, 光线不足, 视线遮挡
- E5 外部干扰: 施工震动, 货车经过震动, 相邻施工影响

## 编码规则

1. **每个致因项可以映射到 0 个、1 个或多个 HFACS-CS 编码**
2. **来源维度提示**（软约束，不是硬限制）：
   - human_factors → 通常映射到 Level 3 (P) 或 Level 4 (A)
   - machine_factors → 通常映射到 Level 5 (W)
   - management_factors → 通常映射到 Level 1 (O) 或 Level 2 (S)
   - environmental_factors → 通常映射到 Level 6 (E)
3. **区分层级的关键**：
   - O vs S：组织性/系统性缺陷→O，现场执行层面的缺失→S
   - P vs A：状态/能力不足→P，具体行为→A
   - W vs E：物的不安全状态→W，外部环境→E
4. **同一个编码不可重复分配给同一个致因项**
5. **所有编码必须来自以上 29 个编码之一**，不要编造新编码
6. 如果致因项的描述过于笼统无法判断，尽量根据 category 关键字猜测最可能的编码

## 输出格式

输出一个 JSON 对象，key 为致因项序号（从0开始），value 为编码列表：
{"0": ["O1", "S1"], "1": ["A1"], "2": ["E2"], "3": []}

对于每个致因项，如果没有任何匹配的编码，返回空列表 []。
"""


def _get_prefix(code: str) -> str:
    return code[0] if code and len(code) > 0 else ""


def _get_level_info(code: str) -> tuple[int, str]:
    prefix = _get_prefix(code)
    return CODE_PREFIX_MAP.get(prefix, (0, ""))


class HfacsEncoder:
    """HFACS-CS 编码器。

    将 V2 提取的四维度致因（{category, description}）映射到 HFACS-CS 标准编码。
    支持 LLM 编码（主要）和关键词编码（备选回退）。
    """

    def __init__(self, api_key: Optional[str] = None):
        self.client: Optional[Anthropic] = None
        api_key = api_key or settings.ANTHROPIC_API_KEY
        if api_key:
            self.client = Anthropic(api_key=api_key)
        self.taxonomy = HfacsTaxonomy()
        self.taxonomy.load()

        # 预构建编码→中文名映射
        self._code_to_name: dict[str, str] = {}
        for level in self.taxonomy.levels:
            for cat in level.categories.values():
                self._code_to_name[cat.code] = cat.name_cn

    def encode(
        self,
        title: str,
        summary: str,
        human_factors: list[dict],
        machine_factors: list[dict],
        management_factors: list[dict],
        environmental_factors: list[dict],
        method: str = "llm",
    ) -> tuple[list[str], list[HfacsCode]]:
        """对单个事故的全部致因进行 HFACS-CS 编码。

        Args:
            title: 事故标题
            summary: 事故摘要
            human_factors: V2 提取的 [{"category":..., "description":...}, ...]
            machine_factors, management_factors, environmental_factors: 同上
            method: "llm" 或 "keyword"

        Returns:
            (hfacs_codes, hfacs_details)
            - hfacs_codes: 去重排序的编码列表，如 ["A1", "O3", "S2"]
            - hfacs_details: 详细映射信息列表
        """
        if method == "llm" and self.client:
            try:
                return self._encode_with_llm(
                    title, summary,
                    human_factors, machine_factors,
                    management_factors, environmental_factors,
                )
            except Exception as e:
                logger.warning(f"LLM 编码失败，回退关键词编码: {e}")

        # 备选：关键词编码
        return self._encode_with_keyword(
            human_factors, machine_factors,
            management_factors, environmental_factors,
        )

    def _build_cause_items(
        self,
        human_factors: list[dict],
        machine_factors: list[dict],
        management_factors: list[dict],
        environmental_factors: list[dict],
    ) -> list[tuple[str, dict]]:
        """构建有序致因项列表，每项为 (dimension_name, {category, description})。"""
        items: list[tuple[str, dict]] = []
        for dim_name, causes in [
            ("human", human_factors),
            ("machine", machine_factors),
            ("management", management_factors),
            ("environmental", environmental_factors),
        ]:
            for c in causes:
                if isinstance(c, dict) and c.get("category", "").strip():
                    items.append((dim_name, c))
        return items

    def _encode_with_llm(
        self,
        title: str,
        summary: str,
        human_factors: list[dict],
        machine_factors: list[dict],
        management_factors: list[dict],
        environmental_factors: list[dict],
    ) -> tuple[list[str], list[HfacsCode]]:
        """使用 LLM 进行编码映射。"""
        cause_items = self._build_cause_items(
            human_factors, machine_factors, management_factors, environmental_factors
        )
        if not cause_items:
            return [], []

        # 构建用户提示
        causes_text = "\n".join(
            f"[{i}] (来源:{dim}) 分类:{c.get('category','')} | 描述:{c.get('description','')}"
            for i, (dim, c) in enumerate(cause_items)
        )

        user_prompt = (
            f"请为以下事故的致因进行 HFACS-CS 编码。\n\n"
            f"事故标题: {title}\n"
            f"事故摘要: {(summary or '')[:400]}\n\n"
            f"待编码的致因列表（共{len(cause_items)}项）:\n{causes_text}\n\n"
            f"请输出每个致因项对应的 HFACS-CS 编码。"
        )

        response = self.client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=HFACS_ENCODING_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # 解析 LLM 响应
        result = self._parse_llm_json(response)
        if result is None:
            logger.warning("LLM 响应解析失败，回退关键词编码")
            return self._encode_with_keyword(
                human_factors, machine_factors,
                management_factors, environmental_factors,
            )

        # 构建 HfacsCode 列表
        codes_set: set[str] = set()
        details: list[HfacsCode] = []

        for idx, (dim_name, cause) in enumerate(cause_items):
            assigned = result.get(str(idx), [])
            if not isinstance(assigned, list):
                assigned = [assigned] if isinstance(assigned, str) else []

            for code in assigned:
                if not code or not isinstance(code, str):
                    continue
                code = code.strip()
                if code not in codes_set:
                    codes_set.add(code)
                level_info = _get_level_info(code)
                cat_name = self._code_to_name.get(code, code)
                details.append(HfacsCode(
                    code=code,
                    level=level_info[0],
                    level_name_cn=level_info[1],
                    category_name_cn=cat_name,
                    source_dimension=dim_name,
                    source_category=cause.get("category", ""),
                    source_description=cause.get("description", ""),
                    encoding_method="llm",
                ))

        return sorted(codes_set), details

    def _encode_with_keyword(
        self,
        human_factors: list[dict],
        machine_factors: list[dict],
        management_factors: list[dict],
        environmental_factors: list[dict],
    ) -> tuple[list[str], list[HfacsCode]]:
        """使用关键词匹配进行编码（备选方案）。"""
        codes_set: set[str] = set()
        details: list[HfacsCode] = []

        for dim_name, causes in [
            ("human", human_factors),
            ("machine", machine_factors),
            ("management", management_factors),
            ("environmental", environmental_factors),
        ]:
            for c in causes:
                if not isinstance(c, dict):
                    continue
                cat = c.get("category", "") or ""
                desc = c.get("description", "") or ""
                if not cat.strip():
                    continue

                matched = self.taxonomy.classify(cat, desc)
                for code, lname, cname in matched:
                    if code not in codes_set:
                        codes_set.add(code)
                    level_info = _get_level_info(code)
                    details.append(HfacsCode(
                        code=code,
                        level=level_info[0],
                        level_name_cn=lname,
                        category_name_cn=cname,
                        source_dimension=dim_name,
                        source_category=cat,
                        source_description=desc,
                        encoding_method="keyword",
                    ))

        return sorted(codes_set), details

    def _parse_llm_json(self, response) -> Optional[dict]:
        """从 LLM 响应中解析 JSON。"""
        import re

        for block in response.content:
            # tool_use 格式
            if block.type == "tool_use":
                return dict(block.input)

            # 文本格式
            if block.type == "text":
                text = block.text.strip()
                # 尝试 json 代码块
                m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
                # 尝试直接解析最外层 {}
                m = re.search(r'\{[\s\S]*\}', text)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except json.JSONDecodeError:
                        pass
        return None
