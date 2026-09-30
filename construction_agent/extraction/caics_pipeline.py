"""S1–S4 统一编排层：主管道的唯一实现。

## 背景

主管道此前散落三处，各自编排、互不复用：

| 入口 | 位置 | 编排方式 |
|---|---|---|
| 批处理 | `scripts/run_batch_pipeline.py:144 run_batch()` | S1→S2→S3→(搜索增强→S3)→S4→入主库 |
| CLI | `main.py:52 / :101 / :131` | `ExtractionPipeline.process()`，旧理论（V1 四维度 + HFACS 编码） |
| Web | `construction_agent/web/app.py` | 同上，且 `pipeline` 为模块级全局单例 |

三条链路的 S1–S4 实现其实是同一套，但只有批处理那条真正在用（主库数据由它产出）。
本模块把那段编排收拢为一处，供三者共用。

## 同构约束（改动前必读）

主库 2,623 条数据由 `run_batch_pipeline.run_batch()` 产出，是论文的对照数据。
`run_once()` 的**默认行为逐行等价于该函数的单条循环体**：

- S3 之后尝试搜索增强，命中同事故多源时**重跑 S3**（`search_enhancer`）
- S4 编码用的是**原始文本**，不是增强后的文本
- S1 判为 MULTI 时**不拆分**，整段进入 S3/S4
- S4 异常不阻断入库，该条的致因编码记为空

改动其中任何一条都会改变产出，中断数据可复现性。新增能力一律做成可选参数，
默认值取与原逻辑一致的那一侧。

## 已知偏差（只记录，不擅改）

`classify_text_type()` 对 MULTI 文本会返回 `accidents` 拆分结果，但
`run_batch()` 从未读取它。主库 `text_type` 实测 2,622 single + 1 multi，
那一条 multi 也按整段处理。是否启用拆分是待决项——本模块把拆分结果原样
保留在 `PipelineOutcome.multi_segments` 里备查，不改变既有行为。

## 用法

    from construction_agent.extraction.caics_pipeline import CaicsPipeline

    pipe = CaicsPipeline()
    outcome = pipe.run_once(title="…", text="…", publish_date="2024-03-16")
    if outcome.status == "completed":
        print(outcome.basic.to_dict(), outcome.causes.caics_codes)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from construction_agent.collector.base import RawAccidentText
from construction_agent.collector.llm_classifier import (
    AccidentClassifier,
    ClassificationResult,
    TextTypeResult,
)
from construction_agent.extraction.search_enhancer import SearchEnhancer
from construction_agent.extraction.step1_basic_extractor import (
    Step1BasicExtractor,
    Step1Result,
)
from construction_agent.extraction.step2_caics_encoder import (
    Step2CaicsEncoder,
    Step2Result,
)

logger = logging.getLogger(__name__)

# 阶段名。S1/S2 在 llm_classifier 内部被注释为 "Step 0"/"Step 1"，
# 与 extraction 里的 "Step 1/2" 撞名（见 docs/web/02 第 2.1 节），此处统一为 S 编号。
S1_TEXT_TYPE = "S1"
S2_RELEVANCE = "S2"
S3_FIELDS = "S3"
S4_CAUSES = "S4"
SEARCH_ENHANCE = "SEARCH"

# 终态。对应工作库 raw_accidents.status（new/gated/promoted/archived）的中间态：
# purged → archived，completed → 可 promotion，failed → 留 new 待重试。
STATUS_COMPLETED = "completed"
STATUS_PURGED = "purged"
STATUS_FAILED = "failed"

# 被闸门拦下的原因
PURGE_TEXT_TYPE = "text_type"
PURGE_NON_CONSTRUCTION = "non_construction"


@dataclass
class StageRecord:
    """单个阶段的一次执行记录。"""

    stage: str
    ok: bool
    ms: int = 0
    detail: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage, "ok": self.ok, "ms": self.ms,
            "detail": self.detail, "error": self.error,
        }


@dataclass
class PipelineOutcome:
    """一条原始文本走完 S1–S4 的完整结果。

    字段可为 None，表示该段未执行或失败——调用方据此判断走到哪一步。
    """

    # ── 输入回显 ──
    source_id: str = ""
    source_name: str = ""
    title: str = ""
    publish_date: str = ""

    # ── 各段产出 ──
    text_type: Optional[TextTypeResult] = None
    classification: Optional[ClassificationResult] = None
    basic: Optional[Step1Result] = None
    causes: Optional[Step2Result] = None
    search: Optional[dict] = None

    # ── MULTI 拆分结果（S1 产出，当前不消费，仅备查）──
    multi_segments: list[dict] = field(default_factory=list)

    # ── 终态 ──
    status: str = STATUS_FAILED
    purge_reason: str = ""
    records: list[StageRecord] = field(default_factory=list)
    total_ms: int = 0

    # ── 便捷属性 ──

    @property
    def accepted(self) -> bool:
        """是否通过两道闸门、走完提取与编码。"""
        return self.status == STATUS_COMPLETED

    @property
    def text_type_value(self) -> str:
        return self.text_type.text_type.value if self.text_type else ""

    @property
    def is_construction(self) -> bool:
        return bool(self.classification and self.classification.is_construction)

    @property
    def caics_codes(self) -> list[str]:
        return self.causes.caics_codes if self.causes else []

    def stage_ms(self, stage: str) -> int:
        """取某阶段耗时；该阶段未执行时返回 0。"""
        for r in self.records:
            if r.stage == stage:
                return r.ms
        return 0

    def record(self, stage: str) -> Optional[StageRecord]:
        for r in self.records:
            if r.stage == stage:
                return r
        return None

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的字典（落工作库与前端轮询用）。"""
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "title": self.title,
            "publish_date": self.publish_date,
            "status": self.status,
            "purge_reason": self.purge_reason,
            "text_type": self.text_type.to_dict() if self.text_type else None,
            "classification": self.classification.to_dict() if self.classification else None,
            "basic": self.basic.to_dict() if self.basic else None,
            "causes": self.causes.to_dict() if self.causes else None,
            "multi_segments": self.multi_segments,
            "stages": [r.to_dict() for r in self.records],
            "total_ms": self.total_ms,
        }


class CaicsPipeline:
    """S1→S2→S3→S4 编排器。

    四个组件可注入，便于测试与替换；不传则按需自建（构造很轻，
    真正的 API key 校验发生在各组件的首次调用）。

    Args:
        classifier: S1+S2（`AccidentClassifier`）
        extractor: S3（`Step1BasicExtractor`）
        encoder: S4（`Step2CaicsEncoder`）
        enhancer: S3 后的搜索增强（`SearchEnhancer`）
        enable_search_enhance: 是否执行搜索增强。默认 True，与
            `run_batch_pipeline.run_batch()` 一致；置 False 可省下网络往返，
            但产出会与该函数不同（适用于新采集数据而非复现论文数据）。

    用法：
        pipe = CaicsPipeline()
        outcome = pipe.run_once(title=..., text=...)
    """

    def __init__(
        self,
        classifier: Optional[AccidentClassifier] = None,
        extractor: Optional[Step1BasicExtractor] = None,
        encoder: Optional[Step2CaicsEncoder] = None,
        enhancer: Optional[SearchEnhancer] = None,
        enable_search_enhance: bool = True,
    ):
        self.classifier = classifier if classifier is not None else AccidentClassifier()
        self.extractor = extractor if extractor is not None else Step1BasicExtractor()
        self.encoder = encoder if encoder is not None else Step2CaicsEncoder()
        self.enhancer = enhancer if enhancer is not None else SearchEnhancer()
        self.enable_search_enhance = enable_search_enhance

    # ══════════════════════════════════════════════════
    # 单条
    # ══════════════════════════════════════════════════

    def run_once(
        self,
        title: str,
        text: str,
        publish_date: str = "",
        source_id: str = "",
        source_name: str = "",
    ) -> PipelineOutcome:
        """一条文本走完 S1–S4。

        **行为与原 run_batch() 的单条循环体等价**，详见模块 docstring 的同构约束。
        """
        out = PipelineOutcome(
            source_id=source_id, source_name=source_name,
            title=title, publish_date=publish_date,
        )
        t_start = time.time()
        metadata = {"publish_date": publish_date, "source_name": source_name}

        # ── S1 类型闸门 ──────────────────────────────
        stage = self._timed(S1_TEXT_TYPE)
        try:
            tt = self.classifier.classify_text_type(title, text)
        except Exception as exc:  # noqa: BLE001 — 单条失败不该中断整批
            out.records.append(stage.fail(exc))
            out.status, out.purge_reason = STATUS_FAILED, f"S1 异常: {exc}"
            out.total_ms = self._elapsed(t_start)
            logger.error("S1 异常: %s", exc)
            return out

        out.text_type = tt
        out.records.append(stage.ok(
            f"type={tt.text_type.value} conf={tt.confidence}",
        ))
        # MULTI 的拆分结果原样留着备查，不消费（见模块 docstring「已知偏差」）
        if tt.accidents:
            out.multi_segments = tt.accidents

        if tt.text_type.should_purge():
            out.status, out.purge_reason = STATUS_PURGED, PURGE_TEXT_TYPE
            out.total_ms = self._elapsed(t_start)
            logger.info("S1 拦下 text_type=%s，不入 S2–S4", tt.text_type.value)
            return out

        # ── S2 相关性闸门 ────────────────────────────
        stage = self._timed(S2_RELEVANCE)
        item = RawAccidentText(
            id=source_id, source_name=source_name, title=title, raw_text=text,
        )
        try:
            cl = self.classifier.classify(item)
        except Exception as exc:  # noqa: BLE001
            out.records.append(stage.fail(exc))
            out.status, out.purge_reason = STATUS_FAILED, f"S2 异常: {exc}"
            out.total_ms = self._elapsed(t_start)
            logger.error("S2 异常: %s", exc)
            return out

        out.classification = cl
        out.records.append(stage.ok(f"verdict={cl.verdict.value} construction={cl.is_construction}"))

        if not cl.is_construction:
            out.status, out.purge_reason = STATUS_PURGED, PURGE_NON_CONSTRUCTION
            out.total_ms = self._elapsed(t_start)
            logger.info("S2 拦下非施工事故（%s）", cl.verdict.value)
            return out

        # ── S3 字段提取 ──────────────────────────────
        stage = self._timed(S3_FIELDS)
        try:
            s1 = self.extractor.extract(text, metadata=metadata)
        except Exception as exc:  # noqa: BLE001
            out.records.append(stage.fail(exc))
            out.status, out.purge_reason = STATUS_FAILED, f"S3 异常: {exc}"
            out.total_ms = self._elapsed(t_start)
            logger.error("S3 异常: %s", exc)
            return out

        if s1 is None:
            out.records.append(stage.fail_str("extract() 返回 None"))
            out.status, out.purge_reason = STATUS_FAILED, "S3 无结果"
            out.total_ms = self._elapsed(t_start)
            logger.error("S3 无结果")
            return out

        out.basic = s1
        out.records.append(stage.done(f"字段数={len([v for v in s1.to_dict().values() if v not in ('', None, 0)])}"))

        # ── S3 后的搜索增强（可选，命中则重跑 S3）────
        if self.enable_search_enhance:
            out.search = self._search_enhance(out, text, metadata)

        # ── S4 致因编码（用原始文本）─────────────────
        stage = self._timed(S4_CAUSES)
        try:
            s2 = self.encoder.encode(text, basic_info=out.basic.to_dict())
        except Exception as exc:  # noqa: BLE001 — 与原逻辑一致：S4 失败不阻断
            out.records.append(stage.fail(exc))
            out.status = STATUS_COMPLETED
            out.total_ms = self._elapsed(t_start)
            logger.error("S4 异常（该条仍算完成，编码为空）: %s", exc)
            return out

        if s2 is None:
            out.records.append(stage.fail_str("encode() 返回 None"))
        else:
            out.causes = s2
            out.records.append(stage.done(f"编码数={s2.cause_count} codes={','.join(s2.caics_codes)}"))

        out.status = STATUS_COMPLETED
        out.total_ms = self._elapsed(t_start)
        return out

    def _search_enhance(self, out: PipelineOutcome, text: str, metadata: dict) -> Optional[dict]:
        """S3 之后的搜索增强：命中同事故多源时重跑 S3，用增强文本覆盖 basic。

        等价于 `run_batch_pipeline.py:196-205`。异常一律吞掉——原逻辑把搜索
        当作"锦上添花"，失败不该影响主流程。返回命中的增强结果或 None。
        """
        stage = self._timed(SEARCH_ENHANCE)
        try:
            if not self.enhancer.needs_search(text, out.basic.to_dict()):
                out.records.append(stage.skip("无需增强"))
                return None

            sr = self.enhancer.enhance(text, out.basic.to_dict())
            if sr.get("same_accident_count", 0) <= 0:
                out.records.append(stage.done("无同事故多源"))
                return None

            # 重跑 S3：用拼接了多源片段的文本
            s1 = self.extractor.extract(sr["enriched_text"], metadata=metadata)
            if s1 is not None:
                out.basic = s1
            out.records.append(stage.done(
                f"同事故 {sr['same_accident_count']} 源，已重跑 S3"
                + ("" if s1 is not None else "（重跑无结果，保留原结果）")
            ))
            return sr
        except Exception as exc:  # noqa: BLE001
            out.records.append(stage.fail(exc))
            logger.warning("搜索增强异常: %s", exc)
            return None

    # ══════════════════════════════════════════════════
    # 批量
    # ══════════════════════════════════════════════════

    def run_batch(self, items: list[dict], progress_every: int = 0) -> list[PipelineOutcome]:
        """批量跑。`items` 为 dict 列表，键同 `run_once` 的入参名。

        **串行执行**，与 `run_batch_pipeline.run_batch()` 一致。并发会改变
        外部服务（LLM、搜索）的调用时序，也改变出错时的部分写入结果，
        属于独立决策，不在此处默认开启。

        Args:
            progress_every: >0 时每处理这么多条打一行进度日志。
        """
        results: list[PipelineOutcome] = []
        for i, d in enumerate(items, 1):
            out = self.run_once(
                title=d.get("title", ""),
                text=d.get("content", "") or d.get("raw_text", ""),
                publish_date=d.get("publish_date", ""),
                source_id=d.get("id", ""),
                source_name=d.get("source_name", ""),
            )
            results.append(out)
            if progress_every and i % progress_every == 0:
                done = sum(1 for r in results if r.accepted)
                logger.info("进度 %d/%d，已完成 %d", i, len(items), done)
        return results

    # ══════════════════════════════════════════════════
    # 计时辅助
    # ══════════════════════════════════════════════════

    @staticmethod
    def _timed(stage: str) -> "_StageTimer":
        return _StageTimer(stage)

    @staticmethod
    def _elapsed(t_start: float) -> int:
        return int((time.time() - t_start) * 1000)


class _StageTimer:
    """阶段计时器。三选一的收尾方法，写法上强制每条路径都留痕。"""

    __slots__ = ("stage", "_t0")

    def __init__(self, stage: str):
        self.stage = stage
        self._t0 = time.time()

    @property
    def ms(self) -> int:
        return int((time.time() - self._t0) * 1000)

    def ok(self, detail: str = "") -> StageRecord:
        """通过。"""
        return StageRecord(self.stage, True, self.ms, detail)

    def done(self, detail: str = "") -> StageRecord:
        """执行完成（与非通过路径区分，语义上同 ok）。"""
        return StageRecord(self.stage, True, self.ms, detail)

    def skip(self, detail: str = "") -> StageRecord:
        """跳过。`ok=True`——跳过不是失败，是这条路径的正常分支。"""
        return StageRecord(self.stage, True, self.ms, f"[跳过] {detail}")

    def fail(self, exc: BaseException) -> StageRecord:
        return StageRecord(self.stage, False, self.ms, error=f"{type(exc).__name__}: {exc}")

    def fail_str(self, reason: str) -> StageRecord:
        return StageRecord(self.stage, False, self.ms, error=reason)
