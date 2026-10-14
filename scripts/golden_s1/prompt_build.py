"""构建任意策略的提示词：策略正文（按来源取，不手抄）+ CAICS 类 + 基础特征 31 字段。

对外只有一个入口 `build_system_prompt(strategy)`：把指定策略的 system 正文
（S0–S4）拼上统一的 31 字段输出要求，使六个策略输出口径与 S1′ 一致。

策略正文的来源（**唯一权威源，不手工转录**）：
- S0/S1：experiment3/runner.py 的 `STRATEGIES[...]["system"]`（按文件路径加载，绕开包遮蔽）
- S2/S3/S4：各自 `run_S*.py` 的提示词定义段（执行定义段取常量，照抄 gen_6strategy_docs.py 的做法）
- S5：分层多调用，待重新设计后单独接入（本次先明确抛未实现）

基础特征字段定义：渲染 construction_agent/extraction/step1_basic_extractor.py 的 STEP1_TOOL，
保证与主管道 Step1 的枚举/口径完全一致。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MAIN = "/home/suntianhao/construction_agent"
CLASSIFIER_MOD = f"{MAIN}/.claude/worktrees/classifier-mod"
SIX = f"{CLASSIFIER_MOD}/experiments/experiment_6strategies"

# 策略正文在 system 文本中的截断标记：其后是「输出字段要求」，由本模块统一重写
_OUTPUT_MARK = "输出最终JSON。字段要求:"
_RUNNER_HEAD = "你是施工安全事故分析专家。从事故文本中提取致因编码。"

# 本地重写覆盖目录：`prompts/{策略}.md` 存在时优先于上游源码。
# 重设计的 S3′/S4′/S5′ 放这里——上游 run_S*.py 属于另一个 worktree，不可改动。
# 文件内可用一行 `<!--TAIL-->` 把内容切成两段：前段进正文位置，后段拼在整个 system 的最末尾
# （用于「必须输出 codes 数组」这类需要在字段清单之后才出现的指令）。
OVERRIDE_DIR = Path(__file__).resolve().parent / "prompts"
_TAIL_MARK = "<!--TAIL-->"

# 各策略正文在 run_S*.py 里的常量名
_RUN_CONST = {"S2": "S2_SYSTEM", "S3": "S3_SYSTEM", "S4": "S4_SYSTEM"}


def _load_runner():
    """按文件路径加载 runner.py。

    不能走 `import construction_agent.evaluation.experiment3.runner`：主仓库已经
    缓存了 `construction_agent` 包，classifier-mod 下的同名子包会被遮蔽
    （见记忆「双目录路径陷阱」）。runner.py 只依赖 json/re，按路径加载无副作用。
    """
    import importlib.util

    path = f"{CLASSIFIER_MOD}/construction_agent/evaluation/experiment3/runner.py"
    spec = importlib.util.spec_from_file_location("_gm_runner", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exec_prompt_defs(script: str) -> dict:
    """执行 run_S*.py 的「提示词定义段」取出常量。

    定义段 = 文件开头到样本加载（`SAMPLES_PATH`）之前。该段里的 import 语句会被剥掉
    （它们指向主仓库的 construction_agent 包，会因包遮蔽而失败），改由调用方预置好
    CAICS_TAXONOMY 等依赖。
    """
    src = Path(f"{SIX}/{script}").read_text(encoding="utf-8")
    cut = src.find("SAMPLES_PATH")
    seg = src[:cut] if cut > 0 else src
    seg = "\n".join(l for l in seg.splitlines()
                    if not l.lstrip().startswith(("import ", "from ")))

    runner = _load_runner()
    import json as _json
    import os as _os
    import re as _re
    import time as _time

    class _StubAnthropic:                      # 定义段里会构造 client，给个空壳即可
        def __init__(self, *a, **k):
            self.messages = None

    ns = {
        "CAICS_TAXONOMY": runner.CAICS_TAXONOMY,
        # 以下为定义段所需的依赖桩：只为取常量，不做任何真实调用
        "__name__": f"_promptdefs_{script}",
        "os": _os, "json": _json, "re": _re, "time": _time, "sys": sys,
        "Path": Path, "Anthropic": _StubAnthropic,
        "load_dotenv": lambda *a, **k: False,
        "PROJ": Path(MAIN),
    }
    for name in ("OUTPUT_JSON", "OUTPUT_JSON_EMPTY", "FEWSHOT_EXAMPLES"):
        if hasattr(runner, name):
            ns[name] = getattr(runner, name)
    exec(compile(seg, f"<{script}>", "exec"), ns)  # noqa: S102 - 取自家仓库源码
    return ns


def _strip_field_block(sys_text: str) -> str:
    """把 system 里的「字段要求」清单块换成空，但**保留其后的指令**。

    策略文本的结构是：正文 → `输出最终JSON。字段要求:` → 一串 `- 字段: ...` → 可能的尾部指令。
    S2 的「注意：必须包含 codes 数组」、S3 的「先制定计划再执行」都在尾部，直接截断会丢。

    ⚠️ **S1 例外**：S1 已跑完 300 条且数据冻结，必须逐字节复现当时的提示词；当时的实现是把
    字段块之后的「示例: {...}」一并丢掉。故 S1 走旧行为（直接截断），其余策略保留尾部。
    """
    i = sys_text.find(_OUTPUT_MARK)
    if i < 0:
        return sys_text.rstrip()
    head = sys_text[:i].rstrip()
    rest = sys_text[i + len(_OUTPUT_MARK):]
    lines = rest.splitlines()
    j = 0
    while j < len(lines) and not lines[j].strip():
        j += 1
    while j < len(lines) and lines[j].lstrip().startswith("-"):
        j += 1
    # 尾部里的 `示例: {...}` 是旧的 6 字段形状样例，与本模块的 31 字段样例冲突 → 剔除；
    # 其余尾部指令（S2 的「必须包含 codes 数组」、S3 的「先制定计划」）保留
    tail_lines = [l for l in lines[j:] if not l.lstrip().startswith("示例:")]
    tail = "\n".join(tail_lines).strip()
    return head + ("\n\n" + tail if tail else "")


def _upstream_body(strategy: str) -> str:
    """只从上游源码取正文（runner.py / run_S*.py），不看本地重写。"""
    if strategy in ("S0", "S1"):
        key = {"S0": "S0_ZeroShot", "S1": "S1_FewShot"}[strategy]
        sys_text = _load_runner().STRATEGIES[key]["system"]
    elif strategy in _RUN_CONST:
        ns = _exec_prompt_defs(f"run_{strategy}.py")
        sys_text = ns[_RUN_CONST[strategy]]
    elif strategy == "S5":
        raise NotImplementedError(
            "S5 是分层多调用策略（5 层 + 汇总），待重新设计后单独接入 run_strategy.py 的多层适配器")
    else:
        raise ValueError(f"未知策略: {strategy}")

    if strategy == "S1":
        # 冻结口径：S1 的数据已产出，提示词不可变（见 _strip_field_block 的说明）
        body = sys_text.split(_OUTPUT_MARK)[0].rstrip()
    else:
        body = _strip_field_block(sys_text)
    if body.startswith(_RUNNER_HEAD):          # 去掉自带抬头，避免与本模块抬头重复
        body = body[len(_RUNNER_HEAD):].lstrip()
    return body


def load_strategy_parts(strategy: str) -> tuple[str, str]:
    """返回 (正文, 末尾追加段)。

    优先级：本地重写 `prompts/{策略}.md` > 上游源码（runner.py / run_S*.py）。
    重写文件首行可用 `<!--BASE:Sx-->` 复用上游某策略的正文（避免抄写分类体系时漂移），
    其后是要追加的内容；`<!--TAIL-->` 之后的内容则拼在整个 system 的最末尾。
    """
    ov = OVERRIDE_DIR / f"{strategy}.md"
    if ov.exists():
        text = ov.read_text(encoding="utf-8")
        base = ""
        m = re.match(r"^<!--BASE:(\w+)-->\s*", text)
        if m:
            base = _upstream_body(m.group(1))
            text = text[m.end():]
        if _TAIL_MARK in text:
            body, tail = text.split(_TAIL_MARK, 1)
            body, tail = body.strip(), tail.strip()
        else:
            body, tail = text.strip(), ""
        if base:
            body = base + ("\n\n" + body if body else "")
        return body, tail
    return _upstream_body(strategy), ""


def load_strategy_body(strategy: str) -> str:
    """返回策略的 system 正文（不含输出字段要求那一段，但保留尾部指令）。"""
    return load_strategy_parts(strategy)[0]


def _load_s1():            # 兼容旧调用点
    return _load_runner().STRATEGIES["S1_FewShot"]


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


def build_output_block() -> str:
    """「31 字段规格 + 输出要求」这一段——所有策略共用，保证输出口径一致。

    单独抽出来，是为了让 S5′ 的汇总调用（只产出基础特征、codes 由管道给出）
    复用同一段文本，避免两处口径漂移。
    """
    global FIELDS
    FIELDS = render_field_spec()
    props = _load_step1_tool()["input_schema"]["properties"]

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

    return f"""## 基础特征字段（31 个，与主管道 Step1 口径一致）

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


def build_system_prompt(strategy: str = "S1") -> str:
    """把指定策略的正文拼上统一的 31 字段输出要求。默认 S1（与既有产物兼容）。"""
    tax_and_examples, tail = load_strategy_parts(strategy)
    block = build_output_block()
    return f"""你是施工安全事故分析专家。从事故文本中同时提取【基础特征】与【CAICS 致因编码】。

{tax_and_examples}

{block}{("\n\n" + tail) if tail else ""}"""


if __name__ == "__main__":
    p = build_system_prompt()
    print(f"字符数 {len(p)}")
    print(p[:1200])
    print("...")
    print(p[-1500:])
