"""建筑施工事故两级分类器：规则优先 + LLM 仲裁。

流水线:
    Step 0: 文本类型判断 → 单事故/多事故/公文/统计/培训
        非单事故(除multi) → 归档删除
        MULTI → LLM拆分 → 逐条进入Step 1
    Step 1: 事故分类(规则+LLM) → 施工/非施工
        非施工 → 归档删除

使用方式:
    classifier = AccidentClassifier()
    result = classifier.classify(item)           # 完整流水线
    results = classifier.classify_batch(items)   # 批量（并发+断点续传）
    report = classifier.generate_report(results) # 统计报告
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from construction_agent.collector.base import RawAccidentText
from construction_agent.collector.classifier import classify_v2
from construction_agent.llm_usage import usage_of

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 文本类型枚举 (Step 0)
# ══════════════════════════════════════════════════════════════════

class TextType(str, Enum):
    """文本类型。非 SINGLE 的文本除 MULTI 外均归档删除。"""
    SINGLE = "single"               # 单事故报道 → 进入事故分类
    MULTI = "multi"                 # 多事故文本 → 拆分后逐条进入事故分类
    REGULATORY = "regulatory"       # 政策/督察/批复/标准 → 归档删除
    AGGREGATE = "aggregate"         # 统计报告/趋势分析 → 归档删除
    TRAINING = "training"           # 培训教材/案例教学 → 归档删除
    OTHER = "other"                 # 无法归类 → 归档删除

    def should_purge(self) -> bool:
        """非 SINGLE 且非 MULTI 的文本应归档删除。"""
        return self not in (TextType.SINGLE, TextType.MULTI)

    def should_split(self) -> bool:
        """MULTI 文本需要 LLM 拆分。"""
        return self == TextType.MULTI


@dataclass
class TextTypeResult:
    """文本类型判断结果。"""
    text_type: TextType = TextType.SINGLE
    confidence: str = "low"
    reason: str = ""
    # 仅 MULTI 类型时有值
    accidents: list[dict] = field(default_factory=list)
    # LLM 原始响应（用于审计）
    llm_raw: Optional[dict] = None
    # 用量记账（蓝图 02 §3.4）。规则直接判过、没调 LLM 时保持 0。
    tokens_in: int = 0
    tokens_out: int = 0

    def to_dict(self) -> dict:
        return {
            "text_type": self.text_type.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "accident_count": len(self.accidents),
            "accidents": self.accidents,
        }


# LLM Prompt: 文本类型判断 + 多事故拆分
TEXT_TYPE_PROMPT = """你是建筑施工事故文本分析专家。判断文本类型，如果是多事故文本则拆分。

【文本类型定义 + 典型示例】

1. single — 单起事故报道
   围绕单一起建筑施工事故的报道或通告。允许附带简短安全提示或领导批示（但主体必须是单起事故）。
   ✅ 例: "3月15日,XX工地发生坍塌,造成3人死亡。施工单位为XX公司。"
   ✅ 例: "XX市一在建楼房昨日发生高处坠落事故,1名工人死亡。市安监局已介入调查。"
   ❌ 非single: "近期发生多起事故" "去年发生X起" "开展安全隐患排查" "通报X起事故"

2. multi — 多起独立事故
   包含多起独立建筑施工事故，每起至少能提取时间+地点+伤亡中的两项。
   ✅ 例: "国务院安委会通报近期两起重大事故: 8月13日内蒙古...; 8月15日山西..."
   ✅ 例: "一周内三工地连发事故..."

3. regulatory — 政策/督察/管理文件
   安全生产政策文件、督察通知、挂牌督办、管理办法、安全整治通知、隐患排查部署。
   ✅ 例: "山西：建筑领域安全整治重点排查脚手架" → regulatory（安全整治部署，非事故报道）
   ✅ 例: "河北实施建筑工程安全生产动态管理办法" → regulatory（管理办法文件）
   ✅ 例: "XX市开展建筑施工安全大检查" → regulatory

4. aggregate — 统计/趋势分析/综述
   安全生产统计数据、安全形势分析、事故趋势报告、多年/多起事故汇总。
   ✅ 例: "广州过去5年高空坠落事故统计数据综述" → aggregate（多年统计数据）
   ✅ 例: "去年全国发生建筑施工安全事故XX起，死亡XX人" → aggregate
   ✅ 例: "蒙古国首都工地安全事故频发致逾10名中国民工死亡" → aggregate（多起事故汇总统计）
   ✅ 例: "入秋以来，多处建筑工地安全事故频发" → aggregate

5. training — 培训教材/案例教学
   事故案例分析教材、安全培训材料、警示教育文本。
   ✅ 例: "某公司开展安全培训分析2019年事故" → training
   ✅ 例: "建筑施工典型事故案例分析PPT" → training

6. other — 以上均不匹配
   非建筑施工相关、无法归类。

【关键判定规则】
- 标题含"XX整治""XX排查""XX大检查""XX管理办法""XX通知(部署类)"→ regulatory
- 标题含"X年事故统计""X年X起""过去X年""事故频发""X起事故致X人死亡(汇总)"→ aggregate
- 标题含"通报X起事故""近期X起事故"→ multi
- 正文连续出现多个不同日期+不同地点的事故 → multi
- 安全隐患/违法施工但未发生实际事故 → other（不是事故报道）
- 文本类型是安全部署/政策文件，即使提及某起具体事故作为背景 → 仍为regulatory

【多事故拆分规则】（仅 type=multi 时执行）
1. 为每起独立事故提取完整的 title 和 text_segment
2. 标题从正文第一句提炼（不超过30字）。如原文无标题则概括
3. 事故边界判定：注意序号结构(一/二/三、1.2.3.)、日期变化、地点变化
4. 同一工地不同日期的独立事故 → 拆分
5. 同一事故的不同角度描述 → 不拆分，合并为一条
6. 提取的 text_segment 应包含该事故的所有相关信息

输出严格 JSON（不要 Markdown 代码块、不要额外文字）：
{"type":"single|multi|regulatory|aggregate|training|other","confidence":"high|medium|low","reason":"一句话(中文,引用原文关键证据)","accidents":[{"title":"事故标题","text_segment":"事故原文片段"}]}

【Few-Shot 示例 — 真实标注案例】

示例1: regulatory（安全整治通知）
标题: 山西：建筑领域安全整治重点排查脚手架
正文: 山西：建筑领域安全整治重点排查脚手架...4月5日，记者从山西省政府办公厅获悉，今年我省将强化建筑市场安全责任，重点加强对脚手架、建筑起重机械设备等的隐患排查治理...
输出: {"type":"regulatory","confidence":"high","reason":"安全整治排查部署文件，非事故报道","accidents":[]}

示例2: aggregate（多起事故汇总统计）
标题: 蒙古国首都工地安全事故频发致逾10名中国民工死亡
正文: 蒙古国首都工地安全事故频发 致逾10名中国民工死亡...入秋以来，蒙古国首都乌兰巴托多处建筑工地安全事故频发，致使十几名中国民工死亡...
输出: {"type":"aggregate","confidence":"high","reason":"多起事故汇总统计，入秋以来多处工地频发事故，非单起事故报道","accidents":[]}

示例3: aggregate（年度事故统计）
标题: 广州过去5年高空坠落事故致死人数最多
正文: "过去5年广州所有安全生产事故中，高空坠落导致的死亡重伤人数最多。"昨日广州市安全生产监管局副局长方少华接受采访时表示，过去5年广州出现的死亡或重伤生产事故，有77.7%属于高空坠落...
输出: {"type":"aggregate","confidence":"high","reason":"过去5年事故统计数据综述，非单起事故报道","accidents":[]}

示例4: regulatory（管理办法文件）
标题: 河北实施建筑工程安全生产动态管理办法
正文: 河北实施建筑工程安全生产动态管理办法...近日，河北省出台《建筑工程安全生产动态管理办法》，对建筑施工企业实施动态监管...
输出: {"type":"regulatory","confidence":"high","reason":"出台管理办法的政策文件，非事故报道","accidents":[]}

示例5: multi（多起事故通报）
标题: 国务院安委会通报近期两起重大建筑施工事故
正文: 国务院安委会通报近期两起重大建筑施工事故...2010年8月13日，内蒙古自治区包头市达茂旗境内包满铁路新建线路段发生工程车辆溜逸事故，造成11人死亡...8月15日，山西省...
输出: {"type":"multi","confidence":"high","reason":"通报两起独立事故，每起有时间地点伤亡信息","accidents":[{"title":"包头达茂旗包满铁路工程车辆溜逸事故","text_segment":"2010年8月13日，内蒙古自治区包头市达茂旗境内包满铁路新建线路段发生工程车辆溜逸事故，造成11人死亡..."},{"title":"山西XX事故","text_segment":"8月15日，山西省..."}]}"""


# ══════════════════════════════════════════════════════════════════
# 事故分类枚举 (Step 1)
# ══════════════════════════════════════════════════════════════════

class ClassificationVerdict(str, Enum):
    """分类判定结果类型。"""
    RULE_HIGH_CONSTRUCTION = "rule_high_construction"
    CONFIRMED_CONSTRUCTION = "confirmed_construction"
    CONFIRMED_NON_CONSTRUCTION = "confirmed_non_construction"
    LLM_OVERRIDE_CONSTRUCTION = "llm_override_construction"
    LLM_OVERRIDE_NON_CONSTRUCTION = "llm_override_non_construction"
    INFO_INSUFFICIENT = "info_insufficient"
    LLM_FAILED_FALLBACK = "llm_failed_fallback"


@dataclass
class ClassificationResult:
    """单条文本的分类结果。"""
    item_id: str
    item_title: str
    item_text: str = ""
    item_source: str = ""
    is_construction: bool = False
    verdict: ClassificationVerdict = ClassificationVerdict.INFO_INSUFFICIENT
    confidence: str = "low"
    rule_result: dict = field(default_factory=dict)
    llm_result: Optional[dict] = None
    reason: str = ""
    # 用量记账。规则直接判过（`RULE_HIGH_CONSTRUCTION`）时没有 LLM 调用，保持 0。
    tokens_in: int = 0
    tokens_out: int = 0

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "item_title": self.item_title,
            "item_source": self.item_source,
            "is_construction": self.is_construction,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "rule_result": self.rule_result,
            "llm_result": self.llm_result,
            "reason": self.reason,
        }


@dataclass
class ClassificationReport:
    """批量分类完成后生成的统计报告。"""
    total: int = 0
    rule_high_construction: int = 0
    confirmed_construction: int = 0
    confirmed_non_construction: int = 0
    llm_override_construction: int = 0
    llm_override_non_construction: int = 0
    info_insufficient: int = 0
    llm_failed_fallback: int = 0
    non_chinese_skip: int = 0
    by_source: dict = field(default_factory=dict)

    @property
    def construction_kept(self) -> int:
        return (self.rule_high_construction + self.confirmed_construction
                + self.llm_override_construction)

    @property
    def non_construction_purged(self) -> int:
        return (self.confirmed_non_construction
                + self.llm_override_non_construction)

    @property
    def llm_disagreement_rate(self) -> float:
        override = self.llm_override_construction + self.llm_override_non_construction
        total_llm = self.total - self.rule_high_construction - self.info_insufficient - self.non_chinese_skip
        return round(override / max(total_llm, 1) * 100, 1)

    def summary(self) -> str:
        lines = [
            f"总记录: {self.total}",
            f"  rule_high_construction:        {self.rule_high_construction}",
            f"  confirmed_construction:        {self.confirmed_construction}",
            f"  confirmed_non_construction:    {self.confirmed_non_construction}  <- 删除",
            f"  llm_override_construction:     {self.llm_override_construction}",
            f"  llm_override_non_construction: {self.llm_override_non_construction}  <- 删除",
            f"  info_insufficient:             {self.info_insufficient}  <- 人工判断",
            f"  llm_failed_fallback:           {self.llm_failed_fallback}",
            f"  --------",
            f"  保留施工事故:                 {self.construction_kept}",
            f"  删除非施工:                   {self.non_construction_purged}",
            f"  LLM 推翻规则比例:             {self.llm_disagreement_rate}%",
        ]
        if self.by_source:
            lines.append("")
            lines.append("按采集源:")
            for source, stats in sorted(self.by_source.items()):
                lines.append(
                    f"  {source}: {stats['total']}->{stats['construction']}施工 "
                    f"({stats.get('yield_pct', 0)}%)"
                )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# LLM Prompt
# ══════════════════════════════════════════════════════════════════

CLASSIFIER_SYSTEM_PROMPT = """你是一个建筑施工事故文本分类器。判断文本是否描述了一起建筑施工事故。

【建筑施工事故的判定标准】
属于建筑施工事故：
- 事故发生在工程承包合同范围内的施工活动中
- 包括房屋建筑、市政工程、桥梁隧道、工业厂房的新建/改建/扩建/拆除/装修
- 包括但不限于：坍塌、高处坠落、起重伤害、物体打击、触电、机械伤害、火灾爆炸、中毒窒息、淹溺

不属于建筑施工事故：
- 煤矿/矿山事故、化工厂生产过程事故、非施工区域的道路交通事故
- 工业生产设备的日常维修保养、行政执法（如城管拆违）
- 安全培训材料、事故案例分析教材（不是报道新发生的事故）
- 纯粹的自然灾害（在建工地受台风/暴雨触发则属于施工事故）

【边界案例参考】
- "某拆除公司在拆楼时墙体倒塌" → 是（拆除属于施工阶段）
- "某化工厂维修管道时爆炸" → 否（工业生产设备维修）
- "城管拆除违章建筑时墙体倒塌" → 否（行政执法）
- "台风导致在建工地塔吊倒塌" → 是（施工设备受自然灾害触发）
- "某公司开展安全培训分析2019年事故" → 否（培训材料）

严格回复 JSON，不要 Markdown 包裹、不要其他文字：
{"is_construction": true|false, "confidence": "high"|"medium"|"low", "reason": "一句话（中文），引用原文关键证据"}"""


# ══════════════════════════════════════════════════════════════════
# 主类
# ══════════════════════════════════════════════════════════════════

class AccidentClassifier:
    """两级施工事故分类器：规则优先 + LLM 仲裁。

    用法:
        classifier = AccidentClassifier()
        result = classifier.classify(item)
        results = classifier.classify_batch(items, checkpoint_path="data/audit/checkpoint.json")
        report = classifier.generate_report(results)
        samples = classifier.sample_overrides(results, n=20)
    """

    MIN_TEXT_LENGTH = 30

    def __init__(self, llm_client=None):
        from construction_agent.llm_client import (
            create_llm_client, resolve_model,
        )
        if llm_client is None:
            self.client = create_llm_client()
            if self.client is not None:
                self._model = resolve_model()
            else:
                self._model = None
                logger.warning(
                    "未配置 LLM API Key（ANTHROPIC_API_KEY / SILICONFLOW_API_KEY），"
                    "LLM 分类将不可用")
        else:
            self.client = llm_client
            self._model = getattr(llm_client, 'model', None) or resolve_model()

        self._checkpoint: dict[str, dict] = {}

        # 本次判定累计消耗的 token（蓝图 02 §3.4）。语义是「自上一次
        # classify*/classify 入口以来」——两个入口都会先把它清零再调 LLM，
        # 所以读的人拿到的永远是**当前这条**的用量，不会串到上一条。
        # ⚠️ 这是实例状态，**同一实例不可跨线程共用**（M3 执行器因此让每个
        # 工作线程各持一个 pipeline 实例，而不是共享一个）。
        self.last_usage: tuple[int, int] = (0, 0)

    # ═══════════════════════════════════════════════
    # Step 0: 文本类型判断 + MULTI 拆分
    # ═══════════════════════════════════════════════

    def classify_text_type(self, title: str, text: str) -> TextTypeResult:
        """判断文本是否为单事故报道。非单事故(除MULTI)需归档删除。

        每条文本均通过 LLM + Few-Shot Prompt 判断类型，
        不做规则预判（避免1.7%误杀率）。
        """
        self.last_usage = (0, 0)
        llm_result = self._call_text_type_llm(title, text)
        result = self._parse_text_type_result(llm_result, title)
        result.tokens_in, result.tokens_out = self.last_usage
        return result

    def _parse_text_type_result(self, llm_result: dict, title: str) -> TextTypeResult:
        """解析 LLM 返回的文本类型结果。"""
        if llm_result.get("parse_error"):
            # LLM 失败 → 标记为 other 而非 single（避免噪音污染DB）
            return TextTypeResult(
                text_type=TextType.OTHER,
                confidence="low",
                reason=f"LLM失败标记为other: {llm_result.get('reason','')}",
                llm_raw=llm_result,
            )

        type_str = llm_result.get("type", "single")
        try:
            text_type = TextType(type_str)
        except ValueError:
            text_type = TextType.OTHER

        accidents = llm_result.get("accidents", []) or []
        # 验证 accidents 格式
        valid_accidents = []
        for acc in accidents:
            if isinstance(acc, dict) and acc.get("title") and acc.get("text_segment"):
                valid_accidents.append(acc)

        return TextTypeResult(
            text_type=text_type,
            confidence=llm_result.get("confidence", "low"),
            reason=llm_result.get("reason", ""),
            accidents=valid_accidents,
            llm_raw=llm_result,
        )

    def _call_text_type_llm(self, title: str, text: str) -> dict:
        """调用 LLM 判断文本类型并拆分多事故。"""
        if self.client is None:
            return self._fallback_result("LLM客户端未初始化")

        user_message = f"【标题】{title}\n【正文】{text[:4000]}"

        for attempt in range(3):
            try:
                response = self.client.messages.create(
                    model=self._model,
                    max_tokens=2000,  # multi 拆分需要更多 token
                    temperature=0.0,
                    timeout=30,
                    system=TEXT_TYPE_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                self._add_usage(response)
                return self._parse_llm_response(response)
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    return self._fallback_result(f"文本类型LLM失败: {type(e).__name__}")

        return self._fallback_result("文本类型LLM失败")

    # ═══════════════════════════════════════════════
    # Step 1: 事故分类（已有代码的包装）
    # ═══════════════════════════════════════════════

    def classify(self, item: RawAccidentText) -> ClassificationResult:
        """对单条原始采集文本进行分类。"""
        self.last_usage = (0, 0)
        text = self._sanitize_text(item.raw_text or "")
        title = item.title or ""

        if not self._is_primarily_chinese(title + text[:100]):
            return self._stamp_usage(ClassificationResult(
                item_id=item.id, item_title=title,
                item_text=text[:200], item_source=getattr(item, 'source_name', ''),
                verdict=ClassificationVerdict.INFO_INSUFFICIENT,
                reason="文本非中文为主",
            ))

        if len(text) < self.MIN_TEXT_LENGTH and len(title) < 10:
            return self._stamp_usage(ClassificationResult(
                item_id=item.id, item_title=title,
                item_text=text[:200], item_source=getattr(item, 'source_name', ''),
                verdict=ClassificationVerdict.INFO_INSUFFICIENT,
                reason=f"信息不足（正文{len(text)}字）",
            ))

        rule_result = classify_v2(title, text)

        # 规则 HIGH + 施工 → 直接通过
        if rule_result.get("confidence") == "high" and rule_result.get("is_construction"):
            return self._stamp_usage(ClassificationResult(
                item_id=item.id, item_title=title,
                item_text=text[:200], item_source=getattr(item, 'source_name', ''),
                is_construction=True,
                verdict=ClassificationVerdict.RULE_HIGH_CONSTRUCTION,
                confidence="high",
                rule_result=rule_result,
                llm_result=None,
                reason=rule_result.get("reason", ""),
            ))

        llm_result = self._call_llm_with_retry(title, text)
        return self._stamp_usage(
            self._merge_results(item, text, rule_result, llm_result))

    def classify_batch(
        self, items: list[RawAccidentText],
        max_concurrency: int = 5,
        checkpoint_path: Optional[str] = None,
    ) -> list[ClassificationResult]:
        """批量分类，支持并发调用和断点续传。"""
        n = len(items)
        results: list[Optional[ClassificationResult]] = [None] * n

        if checkpoint_path and os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    self._checkpoint = json.load(f)
                logger.info(f"已加载断点: {len(self._checkpoint)} 条")
            except Exception as e:
                logger.warning(f"断点文件读取失败: {e}")

        to_llm: list[tuple[int, RawAccidentText, dict]] = []
        for i, item in enumerate(items):
            if checkpoint_path and item.id in self._checkpoint:
                cached = self._checkpoint[item.id]
                results[i] = ClassificationResult(**cached)
                continue

            text = self._sanitize_text(item.raw_text or "")
            title = item.title or ""

            if not self._is_primarily_chinese(title + text[:100]):
                results[i] = ClassificationResult(
                    item_id=item.id, item_title=title,
                    item_text=text[:200], item_source=getattr(item, 'source_name', ''),
                    verdict=ClassificationVerdict.INFO_INSUFFICIENT, reason="非中文",
                )
                self._save_checkpoint_item(results[i], checkpoint_path)
                continue

            if len(text) < self.MIN_TEXT_LENGTH and len(title) < 10:
                results[i] = ClassificationResult(
                    item_id=item.id, item_title=title,
                    item_text=text[:200], item_source=getattr(item, 'source_name', ''),
                    verdict=ClassificationVerdict.INFO_INSUFFICIENT,
                    reason=f"信息不足（正文{len(text)}字）",
                )
                self._save_checkpoint_item(results[i], checkpoint_path)
                continue

            rule = classify_v2(title, text)

            if rule.get("confidence") == "high" and rule.get("is_construction"):
                results[i] = ClassificationResult(
                    item_id=item.id, item_title=title,
                    item_text=text[:200], item_source=getattr(item, 'source_name', ''),
                    is_construction=True,
                    verdict=ClassificationVerdict.RULE_HIGH_CONSTRUCTION,
                    confidence="high", rule_result=rule, llm_result=None,
                    reason=rule.get("reason", ""),
                )
                self._save_checkpoint_item(results[i], checkpoint_path)
                continue

            to_llm.append((i, item, rule))

        logger.info(f"批量分类: {n} 条, 规则直接通过 {n - len(to_llm)} 条, 需 LLM {len(to_llm)} 条")

        if not to_llm:
            return [r for r in results if r is not None]

        completed = 0
        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            futures = {}
            for i, item, rule in to_llm:
                text = self._sanitize_text(item.raw_text or "")
                title = item.title or ""
                futures[executor.submit(self._call_llm_with_retry, title, text)] = (i, item, rule, text)

            for future in as_completed(futures):
                i, item, rule, text = futures[future]
                try:
                    llm_result = future.result()
                except Exception as e:
                    llm_result = self._fallback_result(f"并发异常: {e}")
                    logger.error(f"LLM 异常 [{item.id}]: {e}")

                results[i] = self._merge_results(item, text, rule, llm_result)
                self._save_checkpoint_item(results[i], checkpoint_path)
                completed += 1
                if completed % 10 == 0:
                    logger.info(f"LLM 进度: {completed}/{len(to_llm)}")

        return [r for r in results if r is not None]

    def generate_report(self, results: list[ClassificationResult]) -> ClassificationReport:
        """生成分类统计报告。"""
        report = ClassificationReport(total=len(results))
        by_source: dict = defaultdict(lambda: {"total": 0, "construction": 0, "non": 0})

        for r in results:
            key = r.verdict.value
            setattr(report, key, getattr(report, key, 0) + 1)
            source = r.item_source or "unknown"
            by_source[source]["total"] += 1
            if r.is_construction:
                by_source[source]["construction"] += 1
            else:
                by_source[source]["non"] += 1

        for source, stats in by_source.items():
            stats["yield_pct"] = round(stats["construction"] / max(stats["total"], 1) * 100, 1)

        report.by_source = dict(sorted(by_source.items()))
        return report

    def sample_overrides(self, results: list[ClassificationResult], n: int = 20) -> list[dict]:
        """从 LLM 推翻规则的案例中随机抽样。"""
        overrides = [r for r in results if r.verdict.value.startswith("llm_override")]
        if not overrides:
            return []
        sample = random.sample(overrides, min(n, len(overrides)))
        return [{
            "id": r.item_id, "title": r.item_title,
            "rule_said": "施工" if r.rule_result.get("is_construction") else "非施工",
            "llm_said": "施工" if (r.llm_result or {}).get("is_construction") else "非施工",
            "rule_reason": r.rule_result.get("reason", ""),
            "llm_reason": (r.llm_result or {}).get("reason", ""),
            "text_preview": r.item_text[:200],
        } for r in sample]

    # ═══════════════════════════════════════════════
    # 文本处理
    # ═══════════════════════════════════════════════

    @staticmethod
    def _sanitize_text(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(
            r'[^一-鿿　-〿＀-￯'
            r'a-zA-Z0-9\s.,;:!?，。；：！？、""''【】《》（）…—\\-\n]',
            ' ', text,
        )
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    @staticmethod
    def _is_primarily_chinese(text: str, threshold: float = 0.3) -> bool:
        if len(text) < 10:
            return True
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        return chinese_chars / len(text) >= threshold

    # ═══════════════════════════════════════════════
    # LLM 调用
    # ═══════════════════════════════════════════════

    def _call_llm_with_retry(self, title: str, text: str, max_retries: int = 3) -> dict:
        if self.client is None:
            return self._fallback_result("LLM 客户端未初始化")

        user_message = f"【标题】{title}\n【正文】{text[:1500]}"

        for attempt in range(max_retries):
            try:
                response = self.client.messages.create(
                    model=self._model,
                    max_tokens=300,
                    temperature=0.0,
                    timeout=15,
                    system=CLASSIFIER_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                self._add_usage(response)
                return self._parse_llm_response(response)
            except Exception as e:
                error_type = type(e).__name__
                if attempt < max_retries - 1:
                    delay = 2 ** attempt
                    logger.warning(f"LLM 失败 (尝试{attempt+1}/{max_retries}): {error_type}, {delay}s后重试")
                    time.sleep(delay)
                else:
                    logger.error(f"LLM 全部失败: {error_type}: {e}")
                    return self._fallback_result(f"{error_type}: 全部重试失败")

        return self._fallback_result("LLM调用失败")

    @staticmethod
    def _parse_llm_response(response) -> dict:
        text = ""
        if hasattr(response, 'content') and response.content:
            for block in response.content:
                # 跳过 thinking 块（DeepSeek 推理）
                if hasattr(block, 'type') and block.type == 'thinking':
                    continue
                if hasattr(block, 'text'):
                    text += block.text
        else:
            text = str(response)
        text = text.strip()

        # 1. 直接解析（任何有效 JSON）
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

        # 2. Markdown 代码块
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, TypeError):
                pass

        # 3. 正则回退：提取 is_construction（事故分类专用）
        is_match = re.search(r'"is_construction"\s*:\s*(true|false)', text)
        if is_match:
            is_const = is_match.group(1) == "true"
            reason_m = re.search(r'"reason"\s*:\s*"([^"]*)"', text)
            conf_m = re.search(r'"confidence"\s*:\s*"(high|medium|low)"', text)
            return {
                "is_construction": is_const,
                "confidence": conf_m.group(1) if conf_m else "medium",
                "reason": reason_m.group(1) if reason_m else "(正则回退)",
                "parse_fallback": "regex",
            }

        raise ValueError(f"无法解析 LLM 响应: {text[:300]}")

    @staticmethod
    def _fallback_result(error_msg: str) -> dict:
        return {"is_construction": False, "confidence": "low",
                "reason": error_msg, "parse_error": True}

    # ═══════════════════════════════════════════════
    # 用量记账（蓝图 02 §3.4）
    # ═══════════════════════════════════════════════

    def _add_usage(self, response) -> None:
        """把一次响应的用量**累加**进 `last_usage`。

        累加而不是覆盖：重试的每一次调用都真花了 token，只记最后一次
        会让预估与实际对不上（蓝图 02 §8 验收第 8 条）。
        """
        tin, tout = usage_of(response)
        self.last_usage = (self.last_usage[0] + tin,
                           self.last_usage[1] + tout)

    def _stamp_usage(self, result):
        """把累计用量写进结果对象后再返回。

        中间那几个 `INFO_INSUFFICIENT` / 规则直判的返回分支不调 LLM，
        `last_usage` 停在零，写进去正好表达「这条没花钱」。
        """
        result.tokens_in, result.tokens_out = self.last_usage
        return result

    # ═══════════════════════════════════════════════
    # 判定合并
    # ═══════════════════════════════════════════════

    def _merge_results(self, item, text, rule_result, llm_result) -> ClassificationResult:
        title = item.title or ""
        source = getattr(item, 'source_name', '')

        if llm_result.get("parse_error"):
            return ClassificationResult(
                item_id=item.id, item_title=title,
                item_text=text[:200], item_source=source,
                is_construction=rule_result.get("is_construction", False),
                verdict=ClassificationVerdict.LLM_FAILED_FALLBACK,
                confidence="low", rule_result=rule_result, llm_result=llm_result,
                reason=f"LLM失败回退: {llm_result.get('reason', '')}",
            )

        rule_is = rule_result.get("is_construction", False)
        llm_is = llm_result.get("is_construction", False)

        if rule_is == llm_is:
            v = ClassificationVerdict.CONFIRMED_CONSTRUCTION if llm_is else ClassificationVerdict.CONFIRMED_NON_CONSTRUCTION
            return ClassificationResult(
                item_id=item.id, item_title=title,
                item_text=text[:200], item_source=source,
                is_construction=llm_is, verdict=v,
                confidence=llm_result.get("confidence", "high"),
                rule_result=rule_result, llm_result=llm_result,
                reason=llm_result.get("reason", ""),
            )

        # 冲突 → LLM 为准
        if llm_is:
            return ClassificationResult(
                item_id=item.id, item_title=title,
                item_text=text[:200], item_source=source,
                is_construction=True,
                verdict=ClassificationVerdict.LLM_OVERRIDE_CONSTRUCTION,
                confidence="medium", rule_result=rule_result, llm_result=llm_result,
                reason=f"LLM推翻规则: {llm_result.get('reason', '')}",
            )
        return ClassificationResult(
            item_id=item.id, item_title=title,
            item_text=text[:200], item_source=source,
            is_construction=False,
            verdict=ClassificationVerdict.LLM_OVERRIDE_NON_CONSTRUCTION,
            confidence="medium", rule_result=rule_result, llm_result=llm_result,
            reason=f"LLM推翻规则: {llm_result.get('reason', '')}",
        )

    # ═══════════════════════════════════════════════
    # 断点
    # ═══════════════════════════════════════════════

    def _save_checkpoint_item(self, result, checkpoint_path):
        if not checkpoint_path or result is None:
            return
        self._checkpoint[result.item_id] = result.to_dict()
        if len(self._checkpoint) % 10 == 0:
            self._flush_checkpoint(checkpoint_path)

    def _flush_checkpoint(self, checkpoint_path):
        try:
            os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(self._checkpoint, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"断点保存失败: {e}")
