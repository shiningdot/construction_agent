"""批处理脚本的调度逻辑测试。

`scripts/run_batch_pipeline.py` 是主库 2,623 条数据的产出脚本，也是论文数据的
唯一来源。它此前把 S1–S4 的编排内联在 run_batch() 里；重构后改为调用
`CaicsPipeline`。本测试守护那次重构的等价性。

**不发起任何网络调用**，也不碰真实数据库：用假组件替换四个 LLM 组件，
把 DB_PATH / RAW_PATH 指向临时目录，把 insert_accident 换成记录器。
验证的是「脚本把编排结果正确地分派到 成功/过滤/失败 三条路径」。

真正的端到端跑批需要 API key，且会写入主库，不在测试范围内。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from construction_agent.extraction.caics_pipeline import CaicsPipeline
from construction_agent.extraction.step1_basic_extractor import Step1Result

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_batch_pipeline.py"

# 复用编排层测试里的假组件，避免两处各造一套
from tests.test_caics_pipeline import (  # noqa: E402
    FakeClassifier,
    FakeEncoder,
    FakeEnhancer,
    FakeExtractor,
)
from construction_agent.collector.llm_classifier import TextType  # noqa: E402


def load_script():
    """按路径加载脚本模块（scripts/ 不是包，无法常规 import）。"""
    spec = importlib.util.spec_from_file_location("run_batch_pipeline_under_test", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def script_env(tmp_path, monkeypatch):
    """把脚本指向临时环境，并换掉会碰外部资源的部分。"""
    mod = load_script()

    raw_path = tmp_path / "raw.json"
    db_path = tmp_path / "t.db"
    monkeypatch.setattr(mod, "RAW_PATH", str(raw_path))
    monkeypatch.setattr(mod, "DB_PATH", str(db_path))

    # progress json 用的是相对路径，切到 tmp 并预建目录
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "audit").mkdir(parents=True, exist_ok=True)

    inserted: list[tuple] = []
    monkeypatch.setattr(mod, "insert_accident", lambda *a, **kw: inserted.append((a, kw)))

    return mod, raw_path, inserted


def install_pipeline(mod, monkeypatch, classifier=None, extractor=None, encoder=None):
    """把脚本里的 CaicsPipeline 换成注入了假组件的实例。"""
    pipe = CaicsPipeline(
        classifier=classifier or FakeClassifier(),
        extractor=extractor or FakeExtractor(),
        encoder=encoder or FakeEncoder(),
        enhancer=FakeEnhancer(needs=False),
    )
    monkeypatch.setattr(mod, "CaicsPipeline", lambda: pipe)
    return pipe


class TestRunBatchDispatch:

    def test_三条数据分派到三条路径(self, script_env, monkeypatch):
        """一条通过、一条被 S2 拦、一条 S3 失败。"""
        mod, raw_path, inserted = script_env
        raw_path.write_text(json.dumps([
            {"title": "甲", "content": "正文甲", "id": "1"},
            {"title": "乙", "content": "正文乙", "id": "2"},
            {"title": "丙", "content": "正文丙", "id": "3", "source_name": "丙"},
        ], ensure_ascii=False), encoding="utf-8")

        class ClassifyByTitle(FakeClassifier):
            """乙不是施工事故。"""

            def classify(self, item):
                r = super().classify(item)
                r.is_construction = item.title != "乙"
                return r

        class ExtractByTitle(FakeExtractor):
            """丙的 S3 无结果。"""

            def extract(self, text, metadata=None):
                self.calls.append((text, metadata))
                hit = metadata and metadata.get("source_name") == "丙"
                return None if hit else self.result

        install_pipeline(mod, monkeypatch,
                         classifier=ClassifyByTitle(), extractor=ExtractByTitle())

        mod.run_batch(3)

        progress = json.loads((Path("data/audit/batch50_progress.json")).read_text(encoding="utf-8"))
        assert progress["total"] == 3
        assert progress["success"] == 1, "只有甲走完全程"
        assert progress["purged"] == 1, "乙被 S2 拦下"
        assert progress["failed"] == 1, "丙 S3 无结果"
        assert len(inserted) == 1, "被拦下的与失败的都不该入库"

    def test_入库参数取自编排结果(self, script_env, monkeypatch):
        mod, raw_path, inserted = script_env
        raw_path.write_text(json.dumps([
            {"title": "甲", "content": "正文甲", "id": "raw-1", "source_name": "safehoo",
             "publish_date": "2024-03-16"},
        ], ensure_ascii=False), encoding="utf-8")
        install_pipeline(mod, monkeypatch)

        mod.run_batch(1)

        assert len(inserted) == 1
        (args, _), = inserted
        conn, s1_dict, s2_result, raw_dict, tt, cl, search, elapsed_ms = args

        assert s1_dict["title"] == "某工地坍塌事故", "第一个实参是 S3 的 dict"
        assert s2_result.cause_count == 2, "第二个是 S4 结果"
        assert raw_dict["id"] == "raw-1", "第三个是原始记录（供 source_url 等使用）"
        assert tt is not None and cl is not None, "两道闸门的产出都要落库"
        assert isinstance(elapsed_ms, int) and elapsed_ms >= 0

    def test_S4_失败仍入库(self, script_env, monkeypatch):
        """与原逻辑一致：S4 异常只记日志，该条照样入库。"""
        mod, raw_path, inserted = script_env
        raw_path.write_text(json.dumps([
            {"title": "甲", "content": "正文甲", "id": "1"},
        ], ensure_ascii=False), encoding="utf-8")
        install_pipeline(mod, monkeypatch,
                         encoder=FakeEncoder(raise_on=RuntimeError("编码失败")))

        mod.run_batch(1)

        progress = json.loads(Path("data/audit/batch50_progress.json").read_text(encoding="utf-8"))
        assert progress["success"] == 1 and progress["failed"] == 0
        (args, _), = inserted
        assert args[2] is None, "S4 结果为空"

    def test_S1_拦下不入库也不计失败(self, script_env, monkeypatch):
        mod, raw_path, inserted = script_env
        raw_path.write_text(json.dumps([
            {"title": "某政策通知", "content": "关于加强…的通知"},
        ], ensure_ascii=False), encoding="utf-8")
        install_pipeline(mod, monkeypatch,
                         classifier=FakeClassifier(text_type=TextType.REGULATORY))

        mod.run_batch(1)

        progress = json.loads(Path("data/audit/batch50_progress.json").read_text(encoding="utf-8"))
        assert progress == {"success": 0, "purged": 1, "failed": 0,
                            "total": 1, "timestamp": progress["timestamp"]}
        assert inserted == []

    def test_n_参数限制条数(self, script_env, monkeypatch):
        mod, raw_path, inserted = script_env
        raw_path.write_text(json.dumps([
            {"title": f"事故{i}", "content": f"正文{i}"} for i in range(5)
        ], ensure_ascii=False), encoding="utf-8")
        install_pipeline(mod, monkeypatch)

        mod.run_batch(2)

        progress = json.loads(Path("data/audit/batch50_progress.json").read_text(encoding="utf-8"))
        assert progress["total"] == 2
        assert progress["success"] == 2

    def test_S3_抛异常不中断整批(self, script_env, monkeypatch):
        """原版此处会炸掉整个批次；重构后归为该条 failed，批次继续。"""
        mod, raw_path, inserted = script_env
        raw_path.write_text(json.dumps([
            {"title": "甲", "content": "正文甲"},
            {"title": "乙", "content": "正文乙"},
        ], ensure_ascii=False), encoding="utf-8")

        calls = {"n": 0}
        inner = FakeExtractor()

        class FlakyExtractor:
            def extract(self, text, metadata=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("第一次调用失败")
                return inner.extract(text, metadata)

        install_pipeline(mod, monkeypatch, extractor=FlakyExtractor())

        mod.run_batch(2)  # 不抛异常即为通过

        progress = json.loads(Path("data/audit/batch50_progress.json").read_text(encoding="utf-8"))
        assert progress["failed"] == 1 and progress["success"] == 1
