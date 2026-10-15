"""补全重试：模型漏写字段时，用它的残缺输出做上下文，要求补齐后重发一次。

## 为什么需要

要求「先写长文推理、再输出 JSON」的策略（S3′ 的 Plan-and-Solve、S4′ 的两阶段问题链、
S2′ 的逐层推理）里，模型写完整段分析后，偶尔会在最终 JSON 里**整块跳过字段**。

实测数据（同一 300 条、temperature=0）：

| 格 | 漏写 `accident_type`/`severity` 的条数 |
|---|---:|
| S0′ × glm-5.2（零样本，无长文推理） | 0 / 300 |
| S2′ × glm-5.2 | 2 / 300 |
| S2′ × flash | 18 / 300（6.0%） |
| S3′ × glm-5.2 | 9 / 119（7.6%） |

**症状**：JSON 里缺的是一整块**连续**字段（实测为 `accident_type`、`severity`、
`deaths`、`injuries`），其余 28 个键齐全。
**已排除的原因**（逐条验证过）：
- 不是截断——失败样本输出仅 ~950 tokens，远低于 `max_tokens=16384`，`hit_cap` 为假；
- 不是解析错误——原始文本里只有一个 `{`，`extract_json` 取的就是那个对象；
- 不是输入决定的——同一条事故重复 8 次，第 2 次复现、其余 7 次正常，失败 id 跨格几乎不重合；
- 不是提示词里字段没写全——`accident_type` 的枚举取值就在字段清单下一行。

**判断**：模型在正文里已经用文字交代了「3 人高处坠落死亡」，于是认为 JSON 里不必再重复
这一组字段。长文推理是这个行为的触发条件（零样本的 S0′ 完全不出现）。

## 处理方式

解析成功但字段不全时，把模型的原始输出**回灌给它**，点名缺失的字段，要求重发一份完整
JSON（其余字段沿用原值）。这与「下一轮全量重跑补漏」的最终效果相同——每个 id 最终都有
一份完整 JSON——但省掉一整轮重跑，且不改动任何提示词（各策略的 prompt 保持逐字节不变，
矩阵的策略间可比性不受影响）。

只补一次；补完仍不全就如实记录，交给下一轮续跑。
"""
from __future__ import annotations

import json

_CACHE: dict = {}


def expected_fields() -> list[str]:
    """31 个基础特征字段名（取自主管道 STEP1_TOOL，与提示词里的字段清单同源）。"""
    if "fields" not in _CACHE:
        import prompt_build
        props = prompt_build._load_step1_tool()["input_schema"]["properties"]
        _CACHE["fields"] = list(props.keys())
    return _CACHE["fields"]


def missing_fields(parsed: dict) -> list[str]:
    """返回 parsed 里**键不存在**的必需字段。

    注意判的是「键是否存在」而不是「值是否为真」——原文未提及的字段本来就该是 null，
    值可以空，键不能少。
    """
    if not isinstance(parsed, dict) or "_error" in parsed:
        return []
    return [k for k in expected_fields() if k not in parsed]


def repair_prompt(missing: list[str]) -> str:
    return (
        f"你上面的 JSON 缺少以下必需字段：{'、'.join(missing)}。\n"
        f"请重新输出**完整的** JSON 对象：包含 codes 与全部 31 个基础特征字段（共 32 个键），"
        f"一个都不能少；其余字段沿用你上面的取值（原文未提及的字段按规则填 null / \"未提及\" / 0）。\n"
        f"只输出 JSON，不要输出任何其它文字。"
    )


def try_repair(call, user: str, raw: str, parsed: dict) -> tuple[dict, int]:
    """执行一次补全重试。

    `call` 是一个可调用对象，签名 `call(messages: list) -> (raw_text, usage)`，
    由调用方注入（单调用策略与 S5′ 汇总调用的消息构造方式不同）。

    返回 (新的 parsed, 补齐的字段数)。只有在确实补上字段时才采纳新结果。
    """
    missing = missing_fields(parsed)
    if not missing:
        return parsed, 0

    messages = [
        {"role": "user", "content": user},
        {"role": "assistant", "content": raw},
        {"role": "user", "content": repair_prompt(missing)},
    ]
    try:
        raw2, _ = call(messages)
    except Exception:  # noqa: BLE001
        return parsed, 0
    if not raw2:
        return parsed, 0

    from run_S1_golden import extract_json
    fixed = extract_json(raw2)
    if "_error" in fixed:
        return parsed, 0

    before, after = len(missing_fields(parsed)), len(missing_fields(fixed))
    if after < before:                      # 确实补上了才采纳
        # 补全调用可能只回部分字段——用原值兜底，避免把已对的字段改丢
        for k, v in parsed.items():
            fixed.setdefault(k, v)
        return fixed, before - after
    return parsed, 0
