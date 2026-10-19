"""Step2 证据链 v2：把「单条引文」改成「原文事实 → 推断规则 → 编码」的可核查链。

与生产版的关系：
  - **只读**生产文件 `step2_caics_encoder.py`，复用其中的 CAICS 5 层 19 类定义
    （保证分类体系与生产完全一致，可比），其余全部重写。
  - 不修改、不导入生产模块（按文件路径加载，避免 worktree 包遮蔽问题）。
  - 不触碰 `evidence_quality.py`（论文三级证据数字的来源）。

三个新字段（替换原来的 evidence / inference）：
  source_facts[]  逐字原文片段列表，每项 {quote, note}；quote 必须能在原文中逐字找到
  inference_rule  从这些事实推出该编码的规则，并注明依据类型
  evidence_type   direct_quote（原文直述该编码）/ composite（多条事实组合推断）/
                  external（依赖规范或常识，原文无对应事实）
"""
from __future__ import annotations

import ast
import copy
import os
from pathlib import Path

PROD_STEP2 = Path(os.getenv(
    "STEP2_SOURCE",
    "/home/suntianhao/construction_agent/construction_agent/extraction/step2_caics_encoder.py"))

EVIDENCE_TYPES = ["direct_quote", "composite", "external"]
BASIS_TYPES = ["规范条文", "行业惯例", "常识"]

_a = "## CAICS 框架（5层19类"
_b = "## 两步判断法"

_WANTED = ("STEP2_SYSTEM_PROMPT", "STEP2_TOOL")


def _load_prod_literals() -> dict:
    """用 AST 提取生产文件里的两个字面量常量，**不执行该文件**。

    直接 exec 会触发 `from construction_agent.config import settings` 等导入副作用。
    """
    if not PROD_STEP2.exists():
        raise FileNotFoundError(
            f"找不到生产文件 {PROD_STEP2}；可用环境变量 STEP2_SOURCE 指定路径")
    tree = ast.parse(PROD_STEP2.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in _WANTED:
                    out[tgt.id] = ast.literal_eval(node.value)
    missing = [k for k in _WANTED if k not in out]
    if missing:
        raise RuntimeError(f"{PROD_STEP2} 里未找到常量: {missing}")
    return out


def taxonomy_block() -> str:
    """从生产提示词里切出 CAICS 分类体系段落，保证 19 类定义不改一字。"""
    src = _load_prod_literals()["STEP2_SYSTEM_PROMPT"]
    i, j = src.find(_a), src.find(_b)
    if i < 0 or j < 0:
        raise RuntimeError(f"无法从 {PROD_STEP2} 定位分类体系段落")
    return src[i:j].rstrip()


V2_SYSTEM_PROMPT = """你是一名建筑施工事故致因分析专家。使用 CAICS（建筑施工事故综合致因系统）框架对事故进行编码。

__TAXONOMY__

## 两步判断法

**第一步: 理解事故因果结构**
阅读事故文本，回答:
1. 事故的直接触发因素是什么？（T层）
2. 哪些物理防护/屏障失败了？（F层）
3. 现场的人做了什么或没做什么？（H层）
4. 哪些管理缺陷为事故创造了条件？（M层）
5. 后果为什么这么严重？（C层，如有）

**第二步: 匹配编码 + 建立可核查的证据链**

对每个识别到的致因，必须给出三段式证据，**不得只给一条引文了事**：

1. `source_facts`（数组）—— 支撑该编码的**原文事实**，逐条列出。
   - `quote`：**逐字**摘自原文的片段，不得改写、不得概括、不得自己组织语言。
     系统会用程序把 quote 回查原文，找不到的会被标为「无依据」。
   - `note`：这条事实说明了什么（一句话）。
   - 原文确实没有任何相关表述时，`source_facts` 留空数组 `[]`。

2. `inference_rule` —— 从上述事实推出该编码的**规则**，并注明依据类型：
   - 依据是法规/标准条文的，写「依据：规范条文——<哪条要求>」
   - 依据是行业通行做法的，写「依据：行业惯例——<什么惯例>」
   - 依据是生活常识/物理常识的，写「依据：常识——<什么常识>」
   例：「依据：规范条文——《建筑施工高处作业安全技术规范》要求临边必须设防护栏杆；
   本处防护栏杆缺失，故属 F1」

3. `evidence_type` —— 三选一，**必须如实选择，不得虚报**：
   - `direct_quote`：原文**直接**表述了该编码所指的问题（如原文写"未对工人进行安全培训"→ M3）
   - `composite`：原文没有直述该编码，但**多条原文事实组合**后可合理推出
     （如原文写"工龄3个月""未参加培训"→ 组合推出 M3 安全培训不足）
   - `external`：原文**没有**可用事实，编码依赖外部规范或常识（如原文只写坍塌结果，
     依据规范推出"未编制专项施工方案"→ M4）

## 判定纪律（重要）

- **`source_facts` 为空时必须选 `external`**；`evidence_type=direct_quote` 时
  `source_facts` 必须包含直接表述该问题的原文片段。
- **宁可标成 `external`，也不要把推断包装成原文直述。** 虚报类型会让整条证据链失去价值。
- 判定 `composite` 与 `external` 的区别：前者**有原文事实**（只是需要组合），
  后者**没有原文事实**（全靠规范或常识）。
- 不要因为「后果严重」就反推管理类编码。伤亡人数本身不是管理缺陷的证据——
  必须有原文事实（如"未设置安全员""施工方案缺失"）或明确的规范依据。

## 编码规则
1. 每层可多选，无相关致因则不选该层编码
2. 不能全空，至少1个编码
3. 典型事故通常呈现 M→F→T→H 的部分因果链
4. 编码名称(code name)使用中文全称

## Few-Shot 示例

### 示例1: 齐齐哈尔体育馆坍塌（原文事实充分）

事故: 施工方将珍珠岩违规堆放在体育馆屋顶，降雨后珍珠岩增重，屋顶结构超载坍塌，11死4伤。
施工单位无建筑工程施工资质。监理单位未对违规堆放物料进行制止。

编码:
- primary_cause: "珍珠岩违规堆放屋顶，浸水增重导致结构超载坍塌"
- cause_chain: "M5(无资质)→M1(监管缺失)→H1(违规堆放)→T2(物料超载)→F2(结构坍塌)"
- causes:
  [{"code":"M5","name":"市场行为违规",
    "source_facts":[{"quote":"施工单位无建筑工程施工资质","note":"无资质承揽工程"}],
    "inference_rule":"依据：规范条文——《建筑法》要求承揽工程须具备相应资质；无资质承揽属市场行为违规",
    "evidence_type":"direct_quote"},
   {"code":"M1","name":"现场监管缺失",
    "source_facts":[{"quote":"监理单位未对违规堆放物料进行制止","note":"监理明知违规堆料而未制止"}],
    "inference_rule":"依据：规范条文——监理应履行现场安全监督职责并制止违规行为；本案未制止，故属监管缺失",
    "evidence_type":"direct_quote"},
   {"code":"H1","name":"违反操作规程",
    "source_facts":[{"quote":"将珍珠岩违规堆放在体育馆屋顶","note":"违反物料堆放规定"}],
    "inference_rule":"依据：规范条文——屋面堆载不得超过设计荷载；违规堆放即违规操作",
    "evidence_type":"direct_quote"},
   {"code":"T2","name":"物料状态异常",
    "source_facts":[{"quote":"降雨后珍珠岩增重","note":"材料因浸水而性质改变"},
                    {"quote":"屋顶结构超载坍塌","note":"荷载超出承载能力导致失稳"}],
    "inference_rule":"依据：常识——珍珠岩吸水后重量剧增，从轻质材料变为超载荷载，属物料状态异常",
    "evidence_type":"composite"},
   {"code":"F2","name":"支撑体系失稳",
    "source_facts":[{"quote":"屋顶结构超载坍塌","note":"承重结构失效"}],
    "inference_rule":"依据：规范条文——承重结构须具备足够承载力；本案因超载失去承载力，属支撑体系失稳",
    "evidence_type":"direct_quote"}]

### 示例2: 高处坠落（原文对管理类编码无直接表述）

事故: 杭州滨江一在建大楼，一名工人在12层电梯井作业时防护栏杆缺失，坠落36米死亡。

编码:
- primary_cause: "电梯井临边防护栏杆缺失导致工人坠落"
- cause_chain: "F1(防护缺失)→H1(无防护作业)→M2(排查失效)"
- causes:
  [{"code":"F1","name":"临边洞口防护缺失",
    "source_facts":[{"quote":"一名工人在12层电梯井作业时防护栏杆缺失","note":"电梯井口未设防护栏杆"}],
    "inference_rule":"依据：规范条文——电梯井口属临边洞口，规范要求必须设置防护栏杆；其缺失即防护防线失效",
    "evidence_type":"direct_quote"},
   {"code":"H1","name":"违反操作规程",
    "source_facts":[{"quote":"一名工人在12层电梯井作业时防护栏杆缺失","note":"在无防护的井口边作业"}],
    "inference_rule":"依据：规范条文——高处作业在无防护条件下须系挂安全带；原文未提及系挂，且作业发生于无防护井口，属违反操作规程",
    "evidence_type":"composite"},
   {"code":"M2","name":"隐患排查失效",
    "source_facts":[],
    "inference_rule":"依据：行业惯例——临边洞口防护属施工单位日常安全巡查与隐患整改的必查项；长期缺失未见整改记录，可推其排查失效。但原文未提及任何排查或整改情况，故为依赖惯例的推断",
    "evidence_type":"external"}]"""


def build_tool():
    """在生产的 tool schema 上替换证据字段。"""
    tool = copy.deepcopy(_load_prod_literals()["STEP2_TOOL"])
    item = tool["input_schema"]["properties"]["causes"]["items"]
    item["properties"].pop("evidence", None)
    item["properties"].pop("inference", None)
    item["properties"]["source_facts"] = {
        "type": "array",
        "description": "支撑该编码的原文事实，逐条列出。quote 必须逐字摘自原文；"
                       "原文确无相关表述时留空数组",
        "items": {
            "type": "object",
            "properties": {
                "quote": {"type": "string", "description": "逐字摘自原文的片段，不得改写或概括"},
                "note": {"type": "string", "description": "这条事实说明了什么（一句话）"},
            },
            "required": ["quote", "note"],
        },
    }
    item["properties"]["inference_rule"] = {
        "type": "string",
        "description": "从上述原文事实推出该编码的规则，并注明依据类型"
                       "（规范条文 / 行业惯例 / 常识）",
    }
    item["properties"]["evidence_type"] = {
        "type": "string",
        "enum": EVIDENCE_TYPES,
        "description": "direct_quote=原文直述该编码；composite=多条原文事实组合推断；"
                       "external=原文无事实，依赖规范或常识",
    }
    item["required"] = ["code", "name", "source_facts", "inference_rule", "evidence_type"]
    ad = tool["input_schema"]["properties"]["causes"]
    ad["description"] = ("CAICS 致因编码列表。每个编码必须给出三段式可核查证据："
                         "source_facts（逐字原文事实）+ inference_rule（推断规则与依据类型）"
                         "+ evidence_type（如实标注证据类型）")
    return tool


def build_system_prompt() -> str:
    return V2_SYSTEM_PROMPT.replace("__TAXONOMY__", taxonomy_block())


if __name__ == "__main__":
    p = build_system_prompt()
    t = build_tool()
    print(f"提示词 {len(p)} 字符 | tool 名 {t['name']}")
    print("causes 字段:", list(t["input_schema"]["properties"]["causes"]["items"]["properties"]))
    print("required:", t["input_schema"]["properties"]["causes"]["items"]["required"])
    print("分类体系段落是否含 19 类:", all(f"{c} " in p for c in
          ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "T1", "T2", "T3", "T4",
           "H1", "H2", "H3", "H4", "C1", "C2"]))
