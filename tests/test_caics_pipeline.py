"""S1–S4 编排层测试。

用假组件（fake）替换四个 LLM 组件，验证**编排逻辑**本身：哪一步该走、
哪一步该停、某一步失败时终态是什么。不发起任何网络调用。

重点覆盖「与 run_batch_pipeline.run_batch() 的等价行为」：
S1 拦下 → 不进 S2；S2 拦下 → 不进 S3；S4 失败 → 该条仍算完成；
MULTI → 保留拆分结果但不拆分执行。
"""

from __future__ import annotations

import json

import pytest

from construction_agent.collector.llm_classifier import (
    ClassificationResult,
    ClassificationVerdict,
    TextType,
    TextTypeResult,
)
from construction_agent.extraction.caics_pipeline import (
    PURGE_NON_CONSTRUCTION,
    PURGE_TEXT_TYPE,
    S1_TEXT_TYPE,
    S2_RELEVANCE,
    S3_FIELDS,
    S4_CAUSES,
    SEARCH_ENHANCE,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PURGED,
    CaicsPipeline,
)
from construction_agent.extraction.step1_basic_extractor import Step1Result
from construction_agent.extraction.step2_caics_encoder import CauseItem, Step2Result


# ══════════════════════════════════════════════════════════════
# 假组件
# ══════════════════════════════════════════════════════════════

class FakeClassifier:
    """假 S1+S2。可分别指定两段的返回或异常。"""

    def __init__(self, text_type=TextType.SINGLE, is_construction=True,
                 verdict=ClassificationVerdict.RULE_HIGH_CONSTRUCTION,
                 raise_on_s1=None, raise_on_s2=None, segments=None):
        self.text_type = text_type
        self.is_construction = is_construction
        self.verdict = verdict
        self.raise_on_s1 = raise_on_s1
        self.raise_on_s2 = raise_on_s2
        self.segments = segments or []
        self.s1_calls = 0
        self.s2_calls = []
        self.last_item = None

    def classify_text_type(self, title, text):
        self.s1_calls += 1
        if self.raise_on_s1:
            raise self.raise_on_s1
        return TextTypeResult(
            text_type=self.text_type, confidence="high",
            reason="fake", accidents=self.segments,
        )

    def classify(self, item):
        self.s2_calls.append(item)
        self.last_item = item
        if self.raise_on_s2:
            raise self.raise_on_s2
        return ClassificationResult(
            item_id=item.id, item_title=item.title,
            is_construction=self.is_construction, verdict=self.verdict,
        )


class FakeExtractor:
    """假 S3。记录调用次数，便于验证「搜索增强后重跑 S3」。"""

    def __init__(self, result=None, raise_on=None):
        self.result = result if result is not None else Step1Result(
            title="某工地坍塌事故", accident_type="坍塌", severity="较大事故",
            deaths=3, direct_cause="沟槽未按规定放坡致边坡坍塌",
        )
        self.raise_on = raise_on
        self.calls = []

    def extract(self, text, metadata=None):
        self.calls.append((text, metadata))
        if self.raise_on:
            raise self.raise_on
        return self.result


class FakeEncoder:
    """假 S4。"""

    def __init__(self, result=None, raise_on=None):
        self.result = result if result is not None else Step2Result(
            primary_cause="现场监管缺失",
            causes=[
                CauseItem(code="M1", name="现场监管缺失", evidence="未见监理到位"),
                CauseItem(code="H2", name="违规作业", evidence="未按规定放坡"),
            ],
        )
        self.raise_on = raise_on
        self.calls = []

    def encode(self, text, basic_info=None):
        self.calls.append((text, basic_info))
        if self.raise_on:
            raise self.raise_on
        return self.result


class FakeEnhancer:
    """假搜索增强器。"""

    def __init__(self, needs=False, hit_count=0):
        self.needs = needs
        self.hit_count = hit_count
        self.enhance_calls = 0

    def needs_search(self, text, step1):
        return self.needs

    def enhance(self, text, step1):
        self.enhance_calls += 1
        return {
            "enriched_text": text + "\n【补充】另一媒体报道同一事故…",
            "same_accident_count": self.hit_count,
            "same_accident_snippets": [],
        }


def make_pipeline(**kwargs) -> tuple[CaicsPipeline, dict]:
    """造一个全假组件的编排器，返回 (pipe, 组件字典)。"""
    parts = {
        "classifier": kwargs.pop("classifier", FakeClassifier()),
        "extractor": kwargs.pop("extractor", FakeExtractor()),
        "encoder": kwargs.pop("encoder", FakeEncoder()),
        "enhancer": kwargs.pop("enhancer", FakeEnhancer()),
    }
    return CaicsPipeline(**parts, **kwargs), parts


# ══════════════════════════════════════════════════════════════
# 闸门
# ══════════════════════════════════════════════════════════════

class TestGatekeeping:

    @pytest.mark.parametrize("tt", [
        TextType.REGULATORY, TextType.AGGREGATE, TextType.TRAINING, TextType.OTHER,
    ])
    def test_S1_拦下非事故文本(self, tt):
        pipe, parts = make_pipeline(classifier=FakeClassifier(text_type=tt))
        out = pipe.run_once("标题", "正文")

        assert out.status == STATUS_PURGED
        assert out.purge_reason == PURGE_TEXT_TYPE
        assert out.text_type.text_type is tt
        # 关键：不该走到 S3/S4
        assert parts["extractor"].calls == []
        assert out.basic is None and out.causes is None
        assert out.record(S4_CAUSES) is None

    def test_S1_判_MULTI_不中止(self):
        """MULTI 不该被 purge——它要进入提取（是否拆分另说）。"""
        segs = [{"title": "事故甲", "text_segment": "…"}, {"title": "事故乙", "text_segment": "…"}]
        pipe, parts = make_pipeline(classifier=FakeClassifier(text_type=TextType.MULTI, segments=segs))
        out = pipe.run_once("两起事故通报", "正文")

        assert out.status == STATUS_COMPLETED
        assert out.multi_segments == segs, "拆分结果应原样保留备查"
        assert len(parts["extractor"].calls) == 1, "当前不拆分，S3 只跑一次"

    def test_S2_拦下非施工事故(self):
        pipe, parts = make_pipeline(classifier=FakeClassifier(
            is_construction=False, verdict=ClassificationVerdict.CONFIRMED_NON_CONSTRUCTION))
        out = pipe.run_once("标题", "正文")

        assert out.status == STATUS_PURGED
        assert out.purge_reason == PURGE_NON_CONSTRUCTION
        assert out.text_type is not None, "S1 的产出应保留"
        assert parts["extractor"].calls == []
        assert out.basic is None

    def test_S2_收到的_item_带_id_与_source_name(self):
        """RawAccidentText 现在正式带这两个字段，classify() 内可直接取。"""
        cls = FakeClassifier()
        pipe, _ = make_pipeline(classifier=cls)
        pipe.run_once("标题", "正文", source_id="raw-001", source_name="safehoo")

        item = cls.last_item
        assert item.id == "raw-001"
        assert item.source_name == "safehoo"


# ══════════════════════════════════════════════════════════════
# 通过路径
# ══════════════════════════════════════════════════════════════

class TestHappyPath:

    def test_全通过产出齐全(self):
        pipe, parts = make_pipeline()
        out = pipe.run_once("某工地坍塌事故", "正文…", publish_date="2024-03-16",
                            source_id="raw-1", source_name="safehoo")

        assert out.status == STATUS_COMPLETED
        assert out.accepted
        assert out.basic.deaths == 3
        assert out.caics_codes == ["H2", "M1"]
        assert out.causes.cause_count == 2
        assert out.is_construction

    def test_全部阶段都有记录(self):
        """SEARCH 夹在 S3 与 S4 之间——与原 run_batch 的调用次序一致。"""
        pipe, _ = make_pipeline()
        out = pipe.run_once("标题", "正文")
        stages = [r.stage for r in out.records]
        assert stages == [S1_TEXT_TYPE, S2_RELEVANCE, S3_FIELDS, SEARCH_ENHANCE, S4_CAUSES]

    def test_成交字段传到_S3_与_S4(self):
        pipe, parts = make_pipeline()
        pipe.run_once("标题", "正文", publish_date="2024-03-16", source_name="safehoo")

        _, meta = parts["extractor"].calls[0]
        assert meta["publish_date"] == "2024-03-16"
        assert meta["source_name"] == "safehoo"

    def test_增强命中时_S3_重跑而_S4_仍用原始文本(self):
        """同构约束之一：encoder.encode(content, ...)，不是 enriched_text。

        原 run_batch 里搜索增强只影响 S3 的输入，S4 拿的始终是采集原文。
        """
        pipe, parts = make_pipeline(enhancer=FakeEnhancer(needs=True, hit_count=2))
        text = "原始事故文本"
        pipe.run_once("标题", text)

        # 增强器被调用，S3 被重跑
        assert parts["enhancer"].enhance_calls == 1
        assert len(parts["extractor"].calls) == 2
        assert parts["extractor"].calls[0][0] == text
        assert "补充" in parts["extractor"].calls[1][0], "第二次 S3 用的是增强文本"

        # S4 拿到的仍是原始文本
        assert parts["encoder"].calls[0][0] == text
        assert "补充" not in parts["encoder"].calls[0][0]


# ══════════════════════════════════════════════════════════════
# 失败路径
# ══════════════════════════════════════════════════════════════

class TestFailurePaths:

    def test_S1_异常即失败(self):
        pipe, _ = make_pipeline(classifier=FakeClassifier(raise_on_s1=RuntimeError("超时")))
        out = pipe.run_once("标题", "正文")
        assert out.status == STATUS_FAILED
        assert "S1 异常" in out.purge_reason
        assert out.record(S1_TEXT_TYPE).ok is False

    def test_S2_异常即失败(self):
        pipe, _ = make_pipeline(classifier=FakeClassifier(raise_on_s2=RuntimeError("超时")))
        out = pipe.run_once("标题", "正文")
        assert out.status == STATUS_FAILED
        assert "S2 异常" in out.purge_reason

    def test_S3_返回_None_即失败(self):
        class NullExtractor:
            def extract(self, text, metadata=None):
                return None

        pipe, _ = make_pipeline(extractor=NullExtractor())
        out = pipe.run_once("标题", "正文")
        assert out.status == STATUS_FAILED
        assert out.basic is None
        assert out.record(S4_CAUSES) is None, "S3 失败不该继续走 S4"

    def test_S3_异常即失败(self):
        pipe, _ = make_pipeline(extractor=FakeExtractor(raise_on=RuntimeError("解析失败")))
        out = pipe.run_once("标题", "正文")
        assert out.status == STATUS_FAILED
        assert out.record(S3_FIELDS).ok is False

    def test_S4_异常仍算完成(self):
        """同构约束：run_batch 中 S4 异常只 logger.error，该条照样入库。"""
        pipe, _ = make_pipeline(encoder=FakeEncoder(raise_on=RuntimeError("编码失败")))
        out = pipe.run_once("标题", "正文")

        assert out.status == STATUS_COMPLETED
        assert out.basic is not None, "S3 的产出应保留"
        assert out.causes is None
        assert out.caics_codes == []
        assert out.record(S4_CAUSES).ok is False

    def test_S4_返回_None_仍算完成(self):
        class NullEncoder:
            def encode(self, text, basic_info=None):
                return None

        pipe, _ = make_pipeline(encoder=NullEncoder())
        out = pipe.run_once("标题", "正文")
        assert out.status == STATUS_COMPLETED
        assert out.causes is None


# ══════════════════════════════════════════════════════════════
# 搜索增强
# ══════════════════════════════════════════════════════════════

class TestSearchEnhance:

    def test_不需要增强时_S3_只跑一次(self):
        pipe, parts = make_pipeline(enhancer=FakeEnhancer(needs=False))
        out = pipe.run_once("标题", "正文")
        assert parts["enhancer"].enhance_calls == 0
        assert len(parts["extractor"].calls) == 1
        assert "跳过" in out.record(SEARCH_ENHANCE).detail

    def test_需要但没命中同事故_S3_也只跑一次(self):
        pipe, parts = make_pipeline(enhancer=FakeEnhancer(needs=True, hit_count=0))
        pipe.run_once("标题", "正文")
        assert parts["enhancer"].enhance_calls == 1
        assert len(parts["extractor"].calls) == 1

    def test_命中同事故则重跑_S3(self):
        pipe, parts = make_pipeline(enhancer=FakeEnhancer(needs=True, hit_count=3))
        out = pipe.run_once("标题", "正文")
        assert len(parts["extractor"].calls) == 2
        assert out.search["same_accident_count"] == 3

    def test_增强抛异常不影响主流程(self):
        class BoomEnhancer:
            def needs_search(self, text, step1):
                return True

            def enhance(self, text, step1):
                raise RuntimeError("搜索服务挂了")

        pipe, _ = make_pipeline(enhancer=BoomEnhancer())
        out = pipe.run_once("标题", "正文")
        assert out.status == STATUS_COMPLETED, "搜索失败不该让整条失败"
        assert out.basic is not None
        assert out.record(SEARCH_ENHANCE).ok is False

    def test_关闭增强则完全不调用(self):
        """关掉增强后，连 SEARCH 阶段记录都不该出现，S3 只跑一次。"""
        enh = FakeEnhancer(needs=True, hit_count=5)
        pipe, parts = make_pipeline(enhancer=enh, enable_search_enhance=False)
        out = pipe.run_once("标题", "正文")

        assert enh.enhance_calls == 0
        assert len(parts["extractor"].calls) == 1
        assert out.record(SEARCH_ENHANCE) is None


# ══════════════════════════════════════════════════════════════
# 批量与序列化
# ══════════════════════════════════════════════════════════════

class TestBatchAndSerialization:

    def test_批量跑并统计(self):
        pipe, _ = make_pipeline()
        items = [
            {"title": "甲", "content": "正文甲", "id": "1"},
            {"title": "乙", "content": "正文乙", "id": "2"},
        ]
        outs = pipe.run_batch(items, progress_every=1)
        assert len(outs) == 2
        assert all(o.accepted for o in outs)
        assert [o.source_id for o in outs] == ["1", "2"]

    def test_批量中单条被拦不影响其它(self):
        class MixedClassifier(FakeClassifier):
            def classify(self, item):
                r = super().classify(item)
                r.is_construction = item.title != "乙"
                return r

        pipe, _ = make_pipeline(classifier=MixedClassifier())
        outs = pipe.run_batch([{"title": "甲", "content": "x"}, {"title": "乙", "content": "y"}])
        assert outs[0].accepted and outs[1].status == STATUS_PURGED

    def test_to_dict_可_JSON_序列化(self):
        """web 端要把它塞进 JSON 响应，必须序列化得动。"""
        pipe, _ = make_pipeline()
        out = pipe.run_once("标题", "正文", source_id="raw-1")
        d = out.to_dict()
        s = json.dumps(d, ensure_ascii=False)
        assert "raw-1" in s
        back = json.loads(s)
        assert back["status"] == STATUS_COMPLETED
        assert back["causes"]["caics_codes"] == ["H2", "M1"]
        assert len(back["stages"]) == 5

    def test_to_dict_失败路径也不崩(self):
        pipe, _ = make_pipeline(classifier=FakeClassifier(raise_on_s1=RuntimeError("x")))
        d = pipe.run_once("标题", "正文").to_dict()
        assert d["basic"] is None and d["causes"] is None
        json.dumps(d, ensure_ascii=False)

    def test_stage_ms_对未执行阶段返回_0(self):
        pipe, _ = make_pipeline(classifier=FakeClassifier(text_type=TextType.REGULATORY))
        out = pipe.run_once("标题", "正文")
        assert out.stage_ms(S4_CAUSES) == 0
        assert out.stage_ms(S1_TEXT_TYPE) >= 0
