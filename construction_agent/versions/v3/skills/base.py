"""HFACS-CS 技能基类。

每个技能 = 一个独立的 HFACS 层级提取器，
拥有自己的 system prompt + tool_use schema。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from anthropic import Anthropic

from construction_agent.config import settings

logger = logging.getLogger(__name__)


class HFACSSkill:
    """HFACS-CS 技能基类。

    子类只需定义:
    - level: 层级编号 (1-6)
    - name: 技能名称
    - subcategories: [(code, name_cn, description), ...]
    - system_prompt_extra: 额外的提示词内容
    """

    level: int = 0
    name: str = ""
    subcategories: list[tuple[str, str, str]] = []

    @property
    def code_prefix(self) -> str:
        """层级编码前缀: O, S, P, A, W, E"""
        mapping = {1: "O", 2: "S", 3: "P", 4: "A", 5: "W", 6: "E"}
        return mapping.get(self.level, "X")

    @property
    def system_prompt(self) -> str:
        """生成技能的系统提示词。"""
        cats_str = "\n".join(
            f"  {code} - {name_cn}: {desc}"
            for code, name_cn, desc in self.subcategories
        )
        return f"""你是一名建筑施工安全事故分析专家，专注于【{self.name}】维度的致因分析。

## 分析维度
你只负责识别以下 {len(self.subcategories)} 个类别的致因：

{cats_str}

## 分析要求
1. 仔细阅读事故文本，找出与【{self.name}】相关的安全缺陷
2. 如果文本中完全没有该维度的信息，返回空的 found_causes 列表
3. 不要编造信息，没有就返回空
4. 每条致因需提供：编码、类别名称、具体描述（从文本中提取原文或概括）
5. 同一类别可能出现多次（如多处监管缺失），分别列出

{self.system_prompt_extra}"""

    @property
    def system_prompt_extra(self) -> str:
        return ""

    @property
    def output_schema(self) -> dict:
        """生成 tool_use 输出 schema。"""
        codes = [code for code, _, _ in self.subcategories]
        return {
            "name": f"extract_{self.name.lower().replace(' ','_')}",
            "description": f"提取事故文本中与{self.name}相关的致因因素",
            "input_schema": {
                "type": "object",
                "properties": {
                    "found_causes": {
                        "type": "array",
                        "description": f"识别到的{self.name}致因列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {
                                    "type": "string",
                                    "enum": codes,
                                    "description": "致因编码",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "具体描述，从文本中提取或概括",
                                },
                            },
                            "required": ["code", "description"],
                        },
                    },
                    "not_applicable": {
                        "type": "boolean",
                        "description": "文本中完全没有该维度的相关信息时设为 true",
                    },
                },
                "required": ["found_causes", "not_applicable"],
            },
        }

    def extract(self, text: str, client: Anthropic) -> Optional[list[dict]]:
        """调用 LLM 提取本技能的致因。"""
        import re

        try:
            response = client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                system=self.system_prompt,
                messages=[{
                    "role": "user",
                    "content": f"请分析以下建筑施工事故的【{self.name}】致因：\n\n{text}",
                }],
            )

            for block in response.content:
                # 模式1: tool_use
                if block.type == "tool_use" and block.name == self.output_schema["name"]:
                    data = block.input
                    if data.get("not_applicable"):
                        return []
                    return data.get("found_causes", [])

                # 模式2: 文本中提取 JSON
                if block.type == "text":
                    content = block.text
                    # 找 ```json ... ``` 块
                    for match in re.finditer(r'```(?:json)?\s*([\s\S]*?)```', content):
                        try:
                            data = json.loads(match.group(1))
                            if data.get("not_applicable"):
                                return []
                            found = data.get("found_causes", [])
                            if found:
                                return found
                        except (json.JSONDecodeError, Exception):
                            continue
                    # 找最外层 {...}
                    brace_match = re.search(r'\{[\s\S]*\}', content)
                    if brace_match:
                        try:
                            data = json.loads(brace_match.group(0))
                            if data.get("not_applicable"):
                                return []
                            found = data.get("found_causes", [])
                            if found:
                                return found
                        except (json.JSONDecodeError, Exception):
                            pass

            logger.info(f"[{self.name}] 无符合条件的致因（返回为空）")
            return []

        except Exception as e:
            logger.error(f"[{self.name}] 提取异常: {e}")
            return None
