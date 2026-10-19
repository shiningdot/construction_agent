"""从 LLM 响应里取 token 用量。

**为什么单独建一个零依赖模块**：S1–S4 四个组件都要读 `response.usage`，
而它们分处两个包（`collector/` 与 `extraction/`），彼此已有反向依赖
（`extraction.caics_pipeline` → `collector.llm_classifier`）。把这段逻辑
放进任何一边都会给另一边的 import 加一条边，绕出循环。本模块只认
`getattr`，不 import 项目内任何东西，因此谁引用都不会成环。

**为什么需要它**：`Step1Result` / `Step2Result` 此前把 `response.usage`
**直接丢弃**，于是历史批次的实际 token 消耗无法回溯——只能看到「跑了
2,623 条」，看不到花了多少钱。蓝图 02 §3.4 的用量记账依赖这里。
"""

from __future__ import annotations

from typing import Any, Optional


def usage_of(response: Any) -> tuple[int, int]:
    """取 `(input_tokens, output_tokens)`。

    兼容三种形态，按可靠性排序：
    1. Anthropic SDK 的 `response.usage.input_tokens / output_tokens`
       （这是实际走的那条）
    2. OpenAI 风格的 `usage.prompt_tokens / completion_tokens`
    3. 取不到 → `(0, 0)`

    **不抛异常**。用量的价值在于统计，不在于让主流程失败——一个字段名
    对不上就把整条提取判失败，代价远大于少记一次 token。
    """
    u = getattr(response, "usage", None)
    if u is None and isinstance(response, dict):
        u = response.get("usage")
    if u is None:
        return (0, 0)

    def pick(*names: str) -> Optional[int]:
        for n in names:
            v = getattr(u, n, None)
            if v is None and isinstance(u, dict):
                v = u.get(n)
            if isinstance(v, int):
                return v
        return None

    tin = pick("input_tokens", "prompt_tokens")
    tout = pick("output_tokens", "completion_tokens")
    # 两个都没有才算取不到；只缺一个时另一个照记（0 与「没这个字段」都归 0）
    if tin is None and tout is None:
        return (0, 0)
    return (tin or 0, tout or 0)
