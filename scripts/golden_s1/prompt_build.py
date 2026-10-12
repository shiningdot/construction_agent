"""构建 S1'(FewShot) 提示词：CAICS 5 层 19 类 + 基础特征 31 字段。

- CAICS 分类体系与少样本示例：直接取自 experiment3/runner.py 的 STRATEGIES["S1_FewShot"]
  （提示词的唯一权威源，不手工转录）。
- 基础特征字段定义：直接渲染 construction_agent/extraction/step1_basic_extractor.py 的
  STEP1_TOOL，保证与主管道 Step1 的枚举/口径完全一致。
"""
from __future__ import annotations

import sys

MAIN = "/home/suntianhao/construction_agent"
CLASSIFIER_MOD = f"{MAIN}/.claude/worktrees/classifier-mod"


def _load_s1():
    """按文件路径加载 runner.py 的 STRATEGIES。

    不能走 `import construction_agent.evaluation.experiment3.runner`：主仓库已经
    缓存了 `construction_agent` 包，classifier-mod 下的同名子包会被遮蔽
    （见记忆「双目录路径陷阱」）。runner.py 只依赖 json/re，按路径加载无副作用。
    """
    import importlib.util

    path = f"{CLASSIFIER_MOD}/construction_agent/evaluation/experiment3/runner.py"
    spec = importlib.util.spec_from_file_location("_gm_runner_s1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.STRATEGIES["S1_FewShot"]


def _load_step1_tool():
    if MAIN not in sys.path:
        sys.path.insert(0, MAIN)
    from construction_agent.extraction.step1_basic_extractor import STEP1_TOOL
    return STEP1_TOOL


def render_field_spec() -> str:
    """把 Step1 的 tool schema 渲染成提示词里的字段清单。"""
    props = _load_step1_tool()["input_schema"]["properties"]
    out = []
    for name, spec in props.items():
        line = f"- {name}: {spec.get('description', '')}"
        if "enum" in spec:
            line += f"\n    取值必须是: {'|'.join(spec['enum'])}"
        out.append(line)
    return "\n".join(out)


FIELDS = None  # 运行时填充


def build_system_prompt() -> str:
    s1 = _load_s1()
    tax_and_examples = s1["system"].split("输出最终JSON。字段要求:")[0].rstrip()
    # 去掉 S1 自带的抬头行，避免与本提示词的抬头重复
    head = "你是施工安全事故分析专家。从事故文本中提取致因编码。"
    if tax_and_examples.startswith(head):
        tax_and_examples = tax_and_examples[len(head):].lstrip()

    global FIELDS
    FIELDS = render_field_spec()
    props = _load_step1_tool()["input_schema"]["properties"]

    code_keys = '", "'.join([f'"{k}"' for k in []])  # noqa: F841
    example_keys = ", ".join(f'"{k}"' for k in props)
    sample = {
        "codes": ["M1", "F2", "H1"],
        "title": "某工地模板支撑体系坍塌事故",
        "date": "2023-05-12", "location": "江苏省", "city": "南京市",
        "district": None, "accident_time": "09:10",
        "accident_location_detail": "3号楼2层顶板", "weather": "未提及",
        "project_name": "某住宅项目", "project_type": "房屋建筑",
        "building_structure": "BLD04", "construction_phase": "主体结构",
        "work_task": "WT03", "developer": None, "contractor": "某建筑公司",
        "contractor_type": "总包", "supervision_company": None,
        "accident_type": "坍塌", "severity": "较大事故", "deaths": 3, "injuries": 2,
        "trapped_count": 0, "direct_economic_loss": 500.0, "collapse_subtype": "模板坍塌",
        "fall_height": None, "equipment_involved": "EQP06", "material_involved": "MAT05",
        "worker_type": "WKR02", "report_type": "事故调查报告", "summary": "一句话概述",
        "direct_cause": "模板支撑体系失稳导致浇筑时坍塌",
    }
    import json as _json
    sample_txt = _json.dumps(sample, ensure_ascii=False)

    return f"""你是施工安全事故分析专家。从事故文本中同时提取【基础特征】与【CAICS 致因编码】。

{tax_and_examples}

## 基础特征字段（31 个，与主管道 Step1 口径一致）

{FIELDS}

## 输出要求

只输出一个 JSON 对象，同时包含 `codes`（CAICS 编码）与全部 31 个基础特征字段。
- `codes`: CAICS 编码列表，如 ["M1","F2","H1"]；编码必须来自上面的 19 类，不得杜撰
- 枚举字段必须严格取自上面列出的取值，不得自造
- 原文未提及的字段：字符串填 null（`weather` 填 "未提及"），数值填 0 或 null（按字段说明）
- `severity` 按死亡人数判定: 0-2 一般事故 / 3-9 较大事故 / 10-29 重大事故 / 30+ 特别重大事故

输出示例（仅示字段形状，字段值以实际文本为准）：
{sample_txt}

字段清单：{example_keys}
只输出 JSON，不要输出其它文字。"""


if __name__ == "__main__":
    p = build_system_prompt()
    print(f"字符数 {len(p)}")
    print(p[:1200])
    print("...")
    print(p[-1500:])
