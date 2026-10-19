"""Step 2: CAICS 致因编码器。

接收事故文本 + Step 1 基础信息，输出 CAICS 5层19类致因编码，
每个编码附带原文证据和推理链。

策略: CAICS-CoT+ (两步判断法)，修复词汇门控效应。
- 第一步: 理解事故因果结构，识别致因模式
- 第二步: 匹配CAICS编码，引用原文证据

使用方式:
    encoder = Step2CaicsEncoder()
    result = encoder.encode(text, basic_info=step1_result)
    # result.caics_codes = ["M1","F2","T2","H1","C1"]
    # result.reasoning = [{code, evidence, inference}, ...]
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from construction_agent.config import settings
from construction_agent.llm_usage import usage_of
from construction_agent.rag.context import RagContext, build_coding_context

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Tool Schema
# ══════════════════════════════════════════════════════════════════

STEP2_TOOL = {
    "name": "encode_caics_causes",
    "description": "使用CAICS框架对建筑施工事故进行致因编码，每个编码附带原文证据和推理链",
    "input_schema": {
        "type": "object",
        "properties": {
            "primary_cause": {
                "type": "string",
                "description": "事故最直接的原因描述（一句话，≤50字）"
            },
            "cause_chain": {
                "type": "string",
                "description": "致因链，按因果顺序。如'M5(无资质)→M1(监管缺失)→H1(违规堆放)→T2(物料超载)→F2(结构坍塌)'"
            },
            "causes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "enum": [
                                "M1","M2","M3","M4","M5",
                                "F1","F2","F3","F4",
                                "T1","T2","T3","T4",
                                "H1","H2","H3","H4",
                                "C1","C2"
                            ]
                        },
                        "name": {"type": "string"},
                        "evidence": {
                            "type": "string",
                            "description": "从原文中引用支持此编码的具体语句（中文原文，用引号标注）"
                        },
                        "inference": {
                            "type": "string",
                            "description": "推理链: 从原文证据到编码结论的逻辑过程"
                        },
                        "basis": {
                            "type": "string",
                            "description": "支撑该编码的规范依据编号（如 \"R1\"），多条用逗号分隔；无依据留空字符串"
                        }
                    },
                    "required": ["code", "name", "evidence", "inference", "basis"]
                },
                "minItems": 1,
                "maxItems": 15
            }
        },
        "required": ["primary_cause", "cause_chain", "causes"]
    }
}


# ══════════════════════════════════════════════════════════════════
# System Prompt (CAICS-CoT+)
# ══════════════════════════════════════════════════════════════════

STEP2_SYSTEM_PROMPT = """你是一名建筑施工事故致因分析专家。使用 CAICS（建筑施工事故综合致因系统）框架对事故进行编码。

## CAICS 框架（5层19类，按因果泄漏模型 M→F→T→H→C）

### M层: 管理根基 (Management Root) — 为什么这个工地容易出事？
- M1 现场监管缺失: 无人监督/监理失职/未及时发现制止不安全状态
- M2 隐患排查失效: 未排查/未整改/隐患长期存在/检查走过场
- M3 安全培训不足: 未培训/安全交底不到位/缺乏安全知识/三级教育不足
- M4 制度规程漏洞: 无安全方案/应急预案缺失/制度不健全/无专项施工方案
- M5 市场行为违规: 违法分包/无资质/转包/挂靠/违规压缩工期

### F层: 防线失效 (Protection Failure) — 为什么防护没挡住？
- F1 临边洞口防护缺失: 无防护栏杆/安全网缺失/洞口未盖/临边未设防
- F2 支撑体系失稳: 模板坍塌/脚手架失稳/支撑不足/临时结构失效
- F3 设备安全装置失效: 限位器被屏蔽/防坠器失效/安全装置损坏或未安装
- F4 个体防护未使用: 未系安全带/未戴安全帽/PPE未佩戴

### T层: 触发条件 (Trigger) — 什么直接触发了事故？
- T1 设备机具故障: 设备故障/钢丝绳断裂/机械失灵/制动失效
- T2 物料状态异常: 堆载超限/材料缺陷/物料失稳/珍珠岩浸水增重
- T3 地质环境突变: 涌水/边坡失稳/地基沉降/流沙/基坑失稳
- T4 气象水文触发: 暴雨/大风/冰雪/雷电/连日降雨

### H层: 人的行为 (Human Acts) — 人做了或没做什么构成最后防线？
- H1 违反操作规程: 未按方案施工/违规操作/擅自变更方案/未按规范搭设
- H2 违规指挥决策: 强令冒险作业/明知风险仍施工/违章指挥
- H3 风险辨识失败: 未意识到危险/安全意识薄弱/疏忽大意/判断失误
- H4 应急行为失当: 盲目施救/未及时撤离/救援方法错误导致伤亡扩大

### C层: 后果放大 (Consequence Amplification) — 为什么伤亡比预期更严重？
- C1 救援响应迟缓: 救援不及时/无预案/二次坍塌/延误抢救
- C2 信息迟报瞒报: 未及时上报/瞒报/迟报/漏报

## 两步判断法

**第一步: 理解事故因果结构**
阅读事故文本，回答:
1. 事故的直接触发因素是什么？（T层）
2. 哪些物理防护/屏障失败了？（F层）
3. 现场的人做了什么或没做什么？（H层）
4. 哪些管理缺陷为事故创造了条件？（M层）
5. 后果为什么这么严重？（C层，如有）

**第二步: 匹配编码 + 引用证据**
- 对每个识别到的致因，选择最匹配的CAICS编码
- evidence: 必须从原文中引用具体的语句作为证据（用中文引号标注）
- inference: 说明从证据到编码的推理过程
- M层通常是最深层的原因，从文本中管理相关的描述推断
- C层仅在文本明确提到救援迟缓或迟报瞒报时编码

## 编码规则
1. 每层可多选，无相关致因则不选该层编码
2. 不能全空，至少1个编码
3. 典型事故通常呈现 M→F→T→H 的部分因果链
4. evidence必须来自原文，不能编造
5. 编码名称(code name)使用中文全称

## Few-Shot 示例

示例1: 齐齐哈尔体育馆坍塌
事故: 施工方将珍珠岩违规堆放在体育馆屋顶，降雨后珍珠岩增重，屋顶结构超载坍塌，11死4伤。施工单位无资质。监理未制止。
编码:
- primary_cause: "珍珠岩违规堆放屋顶，浸水增重导致结构超载坍塌"
- cause_chain: "M5(无资质)→M1(监管缺失)→H1(违规堆放)→T2(物料超载)→F2(结构坍塌)"
- causes:
  [{"code":"M5","name":"市场行为违规","evidence":"施工单位无建筑工程施工资质，违法承揽工程","inference":"无资质承揽是系统性管理缺陷的根源，直接导致后续所有管理环节缺失"},
   {"code":"M1","name":"现场监管缺失","evidence":"监理单位未对违规堆放物料进行制止","inference":"监理未履行监督职责，明知屋顶堆料有风险却未制止"},
   {"code":"H1","name":"违反操作规程","evidence":"将大量珍珠岩违规堆放在体育馆屋顶","inference":"施工方违反物料堆放规范，将大量珍珠岩堆放在承重有限的屋顶"},
   {"code":"T2","name":"物料状态异常","evidence":"降雨导致珍珠岩浸水增重，屋顶荷载远超设计承载能力","inference":"珍珠岩吸水后重量剧增，从轻质保温材料变为超载荷载"},
   {"code":"F2","name":"支撑体系失稳","evidence":"屋顶荷载远超设计承载能力，引发整体坍塌","inference":"屋顶结构因超载失去承载力，物理防线彻底崩溃"}]

示例2: 高处坠落（短文本）
事故: 杭州滨江一在建大楼，一名工人在12层电梯井作业时防护栏杆缺失，坠落36米死亡。
编码:
- primary_cause: "电梯井临边防护栏杆缺失导致工人坠落"
- cause_chain: "M2(排查失效)→F1(防护缺失)→H3(未辨识风险)→H1(未做防护)"
- causes:
  [{"code":"M2","name":"隐患排查失效","evidence":"原文未直接提及，但从防护栏杆缺失可推断","inference":"防护栏杆缺失是显性隐患，长期未被排查发现并修复"},
   {"code":"F1","name":"临边洞口防护缺失","evidence":"防护栏杆缺失","inference":"电梯井口是规范要求必须设置防护栏杆的部位，其缺失直接导致坠落可能"},
   {"code":"H3","name":"风险辨识失败","evidence":"原文未直接描述","inference":"工人在无防护栏杆的井口边作业，未意识到坠落风险或虽有意识但判断'应该没事'"},
   {"code":"H1","name":"违反操作规程","evidence":"原文未直接描述","inference":"高处作业规范要求工人应在有防护条件下作业，或在无防护时必须系挂安全带"}]"""


RAG_USAGE_RULES = """
## 依据使用规则（重要）
1. **不漏**：事故文本明确描述到的致因，无论依据里是否提到，都必须编码。
2. **不多**：依据里出现、但事故文本没有对应事实的类目，不要编码。
3. 判定要点用于判断证据归属，**不是二选一**：同一事实可以同时支撑多个编码（堆载压垮支架 → T2 记载荷、F2 记结构失效；无防护且未系安全带 → F1 与 F4 并存）。
   只有当文本中确实没有对应事实时，才不编该码。
4. **每条编码都要填 basis**：判定依赖某条依据就写编号（如 "R1"，多条用逗号），纯靠原文证据、不依赖依据的填空字符串。依据里没有的条号不要编造。
5. 依据与事故原文冲突时，以事故原文描述的事实为准。
"""


# ══════════════════════════════════════════════════════════════════
# 输出数据类
# ══════════════════════════════════════════════════════════════════

@dataclass
class CauseItem:
    """单个 CAICS 致因项。"""
    code: str = ""
    name: str = ""
    evidence: str = ""
    inference: str = ""
    # 支撑该编码的依据编号（R1/R2…）。不进 to_dict()：该方法被既有脚本当作
    # 固定 4 键结构使用，加键会破坏其统计口径。
    basis: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name,
            "evidence": self.evidence, "inference": self.inference,
        }


@dataclass
class Step2Result:
    """Step 2 致因编码结果。"""
    primary_cause: str = ""
    cause_chain: str = ""
    causes: list[CauseItem] = field(default_factory=list)
    # 用量记账（蓝图 02 §3.4）。不进 `to_dict()`，理由同 `Step1Result`：
    # 该 dict 的键数被既有脚本当作统计口径用过。
    tokens_in: int = 0
    tokens_out: int = 0
    _processing_time: float = 0.0
    # RAG 溯源（不进 to_dict()，同上）。citations 每条含 chunk_id/条号/出处，
    # 供审计「为什么记这个码」；rag_meta 记录注入了哪些卡、占多少字符。
    citations: list = field(default_factory=list)
    rag_meta: dict = field(default_factory=dict)

    @property
    def caics_codes(self) -> list[str]:
        return sorted(set(c.code for c in self.causes))

    @property
    def cause_count(self) -> int:
        return len(self.causes)

    def to_dict(self) -> dict:
        return {
            "primary_cause": self.primary_cause,
            "cause_chain": self.cause_chain,
            "caics_codes": self.caics_codes,
            "causes": [c.to_dict() for c in self.causes],
            "cause_count": self.cause_count,
            "_processing_time": self._processing_time,
        }

    def to_dict_with_citations(self) -> dict:
        """含 basis 与 RAG 引用的完整序列化。论文评测与审计用这个。"""
        d = self.to_dict()
        d["causes"] = [
            {**c.to_dict(), "basis": c.basis} for c in self.causes
        ]
        d["citations"] = self.citations
        d["rag"] = self.rag_meta
        return d


# ══════════════════════════════════════════════════════════════════
# 编码器
# ══════════════════════════════════════════════════════════════════

class Step2CaicsEncoder:
    """Step 2 CAICS 致因编码器。

    用法:
        encoder = Step2CaicsEncoder()
        result = encoder.encode(text, basic_info=step1_dict)
        print(result.caics_codes)  # ["M1","F2","T2","H1"]
    """

    def __init__(self, api_key: Optional[str] = None, *, rag: Optional[bool] = None,
                 retriever=None):
        from construction_agent.llm_client import (
            create_llm_client, resolve_max_tokens, resolve_model,
        )
        self.client = create_llm_client(api_key=api_key)
        self._model = resolve_model()
        self._max_tokens = resolve_max_tokens()
        # rag=None 时取配置；配置默认关闭，保证既有实验可复现
        self._rag = settings.RAG_ENABLED if rag is None else rag
        self._retriever = retriever

    def _get_retriever(self):
        """懒加载检索器。构造约 0.2s，只在真正用到 RAG 时付出。"""
        if self._retriever is None:
            from construction_agent.rag.retriever import Retriever
            corpus = settings.RAG_CORPUS or None
            self._retriever = Retriever(corpus) if corpus else Retriever.from_default()
        return self._retriever

    def _rag_context(self, text: str, basic_info: Optional[dict],
                     codes: Optional[list[str]], top_k: Optional[int]) -> Optional[RagContext]:
        try:
            return build_coding_context(
                text, basic_info,
                retriever=self._get_retriever(),
                codes=codes,
                top_k=top_k or settings.RAG_TOP_K,
                budget_chars=settings.RAG_BUDGET_CHARS,
            )
        except Exception as e:  # 检索失败不能让编码整体失败
            logger.warning(f"RAG 检索失败，退化为无依据编码: {type(e).__name__}: {e}")
            return None

    def encode(
        self, text: str, basic_info: Optional[dict] = None,
        *, use_rag: Optional[bool] = None,
        rag_codes: Optional[list[str]] = None,
        rag_top_k: Optional[int] = None,
    ) -> Optional[Step2Result]:
        """对事故文本进行 CAICS 致因编码。

        Args:
            text: 事故原文
            basic_info: Step 1 提取的基础信息（可选，用于提供上下文）
            use_rag: 本次是否启用检索增强；None 取构造参数/配置
            rag_codes: 指定要注入的概念卡（边界复核场景传待比对的码）
            rag_top_k: 注入的条文数，默认取配置
        """
        if not self.client:
            logger.error("LLM 客户端未初始化")
            return None

        t0 = time.time()

        # 构建上下文
        rag_ctx: Optional[RagContext] = None
        if use_rag if use_rag is not None else self._rag:
            rag_ctx = self._rag_context(text, basic_info, rag_codes, rag_top_k)

        head = "请分析以下建筑施工事故的致因。"
        if basic_info:
            head = (
                f"【已知事实】\n"
                f"事故类型: {basic_info.get('accident_type','未知')}\n"
                f"严重等级: {basic_info.get('severity','未知')}\n"
                f"死亡{basic_info.get('deaths',0)}人 受伤{basic_info.get('injuries',0)}人\n"
            )
            if basic_info.get('work_task'):
                head += f"事发作业: {basic_info['work_task']}\n"
            if basic_info.get('project_type'):
                head += f"工程类型: {basic_info['project_type']}\n"

        # 顺序刻意如此：事实 → 依据 → 原文。原文放最后（近因效应），
        # 避免模型把依据里的条文当成待编码内容。
        if rag_ctx is not None and not rag_ctx.empty:
            user_msg = f"{head}\n\n{rag_ctx.text}\n{RAG_USAGE_RULES}\n\n【事故原文】\n{text}"
        elif basic_info:
            user_msg = f"{head}\n【事故原文】\n{text}"
        else:
            user_msg = f"请分析以下建筑施工事故的致因：\n\n{text}"

        try:
            response = self.client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0.3,  # 适度温度允许推理多样性
                system=STEP2_SYSTEM_PROMPT,
                tools=[STEP2_TOOL],
                messages=[{"role": "user", "content": user_msg}],
            )
            parsed = self._parse_response(response)
            if parsed:
                result = self._build_result(parsed)
                self._attach_rag(result, rag_ctx)
                result._processing_time = time.time() - t0
                result.tokens_in, result.tokens_out = usage_of(response)
                return result

            # 重试
            logger.warning("tool_use 解析失败，重试")
            retry = self.client.messages.create(
                model=self._model, max_tokens=self._max_tokens, temperature=0.3,
                system=STEP2_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            parsed = self._parse_response(retry)
            if parsed:
                result = self._build_result(parsed)
                self._attach_rag(result, rag_ctx)
                result._processing_time = time.time() - t0
                # 与 S3 一致：两次调用的 token 都算，失败的调用也是真花钱
                f_in, f_out = usage_of(response)
                r_in, r_out = usage_of(retry)
                result.tokens_in, result.tokens_out = f_in + r_in, f_out + r_out
                return result

        except Exception as e:
            logger.error(f"编码失败: {type(e).__name__}: {e}")

        return None

    @staticmethod
    def _parse_response(response) -> Optional[dict]:
        for block in response.content:
            if hasattr(block, 'type') and block.type == 'thinking':
                continue
            if hasattr(block, 'type') and block.type == 'tool_use':
                return dict(block.input) if hasattr(block, 'input') else None
            if hasattr(block, 'text'):
                text = block.text.strip()
                try:
                    d = json.loads(text)
                    if isinstance(d, dict) and 'causes' in d:
                        return d
                except (json.JSONDecodeError, TypeError):
                    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                    if m:
                        try:
                            d = json.loads(m.group(1))
                            if isinstance(d, dict):
                                return d
                        except (json.JSONDecodeError, TypeError):
                            pass
        return None

    @staticmethod
    def _attach_rag(result: Step2Result, ctx: Optional[RagContext]) -> None:
        """把引用写回结果，并统计「有依据支撑的编码占比」。"""
        if ctx is None:
            return
        result.citations = ctx.citations
        supported = sum(1 for c in result.causes if c.basis)
        result.rag_meta = {
            "codes": ctx.codes,
            "chars": ctx.chars,
            "n_citations": len(ctx.citations),
            "n_causes": len(result.causes),
            "n_with_basis": supported,
        }

    @staticmethod
    def _build_result(parsed: dict) -> Step2Result:
        causes = []
        for c in parsed.get("causes", []) or []:
            causes.append(CauseItem(
                code=c.get("code", ""),
                name=c.get("name", ""),
                evidence=c.get("evidence", ""),
                inference=c.get("inference", ""),
                basis=str(c.get("basis", "") or "").strip(),
            ))
        return Step2Result(
            primary_cause=parsed.get("primary_cause", ""),
            cause_chain=parsed.get("cause_chain", ""),
            causes=causes,
        )
