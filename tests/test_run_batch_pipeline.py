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

import ast
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*\(([^)]*)\)(.*)", re.S | re.I)

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
        assert progress == {"success": 0, "purged": 1, "duplicate": 0, "failed": 0,
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


# ── 下面这批测的是**真的 `insert_accident`** ──────────────────────────
#
# 上面 `install_pipeline` 把 `insert_accident` 换成了记录器，验的是编排分派，
# 从没碰过入库本身——而「补跑一条事故，编码翻倍」正是发生在入库里。
# 所以这里另起一组：不 monkeypatch，拿真 conn + 真 schema 直接调它。


def _fake_rows():
    """造一组最小但形状真实的入参。

    编码**故意含两条同码（M1）**：同一类致因在一件事故里有多条不同证据是正常
    标注（主库实测 278 组），如果哪天有人为了挡重跑给 `caics_causes` 加上
    `UNIQUE(accident_id, code)`，`test_same_code_multi_evidence_survives`
    会立刻变红。
    """

    def cause(code, name, evidence, inference):
        return SimpleNamespace(code=code, name=name,
                               evidence=evidence, inference=inference)

    s1 = {
        "title": "某工地模板支撑坍塌事故", "date": "2024-03-16", "city": "南京市",
        "location": "江苏省", "accident_type": "坍塌事故", "severity": "一般事故",
        "deaths": 1, "injuries": 2, "trapped_count": 0,
        "project_type": "房屋建筑工程", "construction_phase": "主体结构施工",
        "report_type": "新闻报道", "summary": "摘要", "_processing_time": 1.5,
        "weather": "未提及", "district": None, "accident_time": None,
        "accident_location_detail": None, "project_name": None,
        "building_structure": None, "work_task": None, "developer": None,
        "contractor": None, "contractor_type": None, "supervision_company": None,
        "direct_economic_loss": None, "collapse_subtype": None, "fall_height": None,
        "equipment_involved": None, "material_involved": None, "worker_type": None,
    }
    s2 = SimpleNamespace(
        primary_cause="现场管理缺失",
        causes=[
            cause("M1", "现场管理缺失", "证据甲", "推断甲"),
            cause("M1", "现场管理缺失", "证据乙", "推断乙"),   # 同码多证
            cause("F2", "支撑体系失稳", "证据丙", "推断丙"),
        ],
        _processing_time=2.0,
    )
    raw = {
        "id": "raw-1", "content": "事故正文",
        "source_url": "https://example.invalid/a", "source_name": "safehoo",
        "crawl_date": "2024-03-17T10:00:00", "publish_date": "2024-03-16",
    }
    text_type = SimpleNamespace(text_type=SimpleNamespace(value="single"),
                                confidence="high", reason="是单起事故报道")
    classification = SimpleNamespace(verdict=SimpleNamespace(value="confirmed"),
                                     confidence="high",
                                     rule_result=None, llm_result=None)
    return s1, s2, raw, text_type, classification


class TestInsertAccidentIdempotent:
    """重跑同一件事故，子表不得翻倍。

    主表走 `INSERT OR REPLACE`（重跑=覆盖），子表原先走裸 `INSERT`（重跑=累加），
    于是「补跑一条采集失败的事故」这个最正常的操作会产出双份编码。而事故详情页
    会把同一编码显示两遍，每份证据单看都成立，不数行数发现不了。
    `review/promote.py:11` 把同型问题称作「历史教训」，那边早已改成先 DELETE 再
    INSERT，`scripts/run_batch_pipeline.py` 这次补上。
    """

    # generate_id() 在同一组 title/date/city 上必须稳定，否则「重跑」根本认不出
    # 是同一条事故（不同 aid → 主表也不覆盖）。下面的测试会核对这个值。
    AID = "0abf248bd977"

    @pytest.fixture
    def mod(self):
        return load_script()

    @pytest.fixture
    def conn(self):
        """内存库 + `create_db_v2.py` 的真 schema（照抄 test_web_m3 的做法）。"""
        spec = importlib.util.spec_from_file_location(
            "create_db_v2_for_test", REPO_ROOT / "scripts" / "create_db_v2.py")
        schema_mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = schema_mod
        spec.loader.exec_module(schema_mod)
        con = sqlite3.connect(":memory:")
        con.executescript(schema_mod.SCHEMA)
        yield con
        con.close()

    @staticmethod
    def _count(conn, table):
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_rerun_does_not_double_causes(self, mod, conn):
        """跑两遍 = 重跑，三张子表都该保持单份。"""
        s1, s2, raw, text_type, classification = _fake_rows()
        aid = mod.generate_id(s1["title"], s1["date"], s1["city"])
        assert aid == self.AID, "generate_id 的实现变了，本组用例的固定 aid 需同步"

        for _ in range(2):
            mod.insert_accident(conn, s1, s2, raw, text_type, classification,
                                None, 1234)

        assert self._count(conn, "accidents") == 1, "主表是 INSERT OR REPLACE"
        # 3 条编码（含同码两条）。裸 INSERT 的旧写法在这里会得到 6。
        assert self._count(conn, "caics_causes") == 3, (
            f"重跑后编码 {self._count(conn, 'caics_causes')} 条，"
            "应为 3 条——多出来的就是重跑累加")
        assert self._count(conn, "processing_log") == 1, (
            "processing_log 重跑会累积假记录，「这条事故处理过几次」正是查退化的入口")
        assert self._count(conn, "source_urls") == 1, "UNIQUE(accident_id,url) 兜底"

    def test_same_code_multi_evidence_survives(self, mod, conn):
        """同码多条不同证据是真实标注，不能被当重复删掉。"""
        s1, s2, raw, text_type, classification = _fake_rows()
        mod.insert_accident(conn, s1, s2, raw, text_type, classification, None, 1234)

        rows = conn.execute(
            "SELECT evidence FROM caics_causes WHERE accident_id = ? AND code = 'M1'",
            (self.AID,)).fetchall()
        assert {r[0] for r in rows} == {"证据甲", "证据乙"}, (
            "M1 的两条证据应都在。若加了 UNIQUE(accident_id, code) 会只剩一条")

    def test_rerun_does_not_double_search_logs(self, mod, conn):
        """search_logs 同样先清后写；source_urls 不在此列，勿连带删掉。"""
        s1, s2, raw, text_type, classification = _fake_rows()
        search = {"search_performed": 1, "queries_used": ["q1"], "total_results": 7,
                  "same_accident_count": 1, "same_accident_snippets": ["片段"],
                  "error": None}

        for _ in range(2):
            mod.insert_accident(conn, s1, s2, raw, text_type, classification,
                                search, 900)

        assert self._count(conn, "search_logs") == 1
        assert self._count(conn, "source_urls") == 1


class TestInsertStatementsWellFormed:
    """每条 INSERT 的占位符个数必须等于传入值的个数。

    `accidents` 那条曾经是 **47 列名 / 50 占位符 / 47 值**——列名和值都对得上，
    只有 `VALUES (...)` 里多打了 3 个问号。sqlite3 不会在导入时报，只在**执行时**
    抛 `50 values for 47 columns`；而 `run_batch()` 把入库包在 try/except 里记成
    failed——于是不崩、不报警，只是每一条都静默失败，日志里滚动着"入库异常"，
    跑完打印"失败 N"，看起来像是数据源的问题。

    换句话说：**这个脚本的入库路径从来没成功过一次**。静态数一遍，比等运行时
    撞上早得多。

    判据只用「占位符数 == 值个数」。**不要**改回「列名数 == 占位符数」——
    列名列表里写字面量是合法的，那样写会在 `VALUES (?, 'pending', ?)` 上误报。
    全项目扫过一遍（52 条字面量 INSERT），用这条判据只有上述一处不合格。
    """

    def test_every_insert_is_aligned(self):
        tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
        checked = []

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute" and node.args):
                continue
            sql_node = node.args[0]
            if not (isinstance(sql_node, ast.Constant)
                    and isinstance(sql_node.value, str)):
                continue
            m = _INSERT_RE.search(sql_node.value)
            if not m:
                continue

            table = m.group(1)
            cols = [c for c in m.group(2).replace("\n", " ").split(",") if c.strip()]
            n_q = m.group(3).count("?")
            checked.append(table)

            # 列名数只作上界：占位符比列名还多，必然有问号配不上
            assert n_q <= len(cols), (
                f"{SCRIPT_PATH.name}:{node.lineno} {table}——"
                f"列名 {len(cols)} 个，占位符却有 {n_q} 个")

            # 这条才是 sqlite3 真正校验的
            if len(node.args) >= 2 and isinstance(node.args[1], (ast.Tuple, ast.List)):
                n_v = len(node.args[1].elts)
                assert n_v == n_q, (
                    f"{SCRIPT_PATH.name}:{node.lineno} {table}——"
                    f"值 {n_v} 个，占位符 {n_q} 个")

        # 防止正则失效导致整条测试空跑——空转的测试是最危险的绿
        assert len(checked) >= 5, (
            f"只查到 {len(checked)} 条 INSERT（{checked}），正则或脚本结构变了")


# ── 入库前查重守门 ──────────────────────────────────────────────────────


class TestAdmissionGuard:
    """`AdmissionGuard` 的三档判据。

    守护的是它的**语义**：L1/L2 硬拦、L3 只告警、本地书源不被 URL 档误伤。
    跑批前真库由它建索引，所以这里用真 schema + 内存库。
    """

    @pytest.fixture
    def mod(self):
        return load_script()

    @pytest.fixture
    def conn(self):
        spec = importlib.util.spec_from_file_location(
            "create_db_v2_for_guard", REPO_ROOT / "scripts" / "create_db_v2.py")
        schema_mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = schema_mod
        spec.loader.exec_module(schema_mod)
        con = sqlite3.connect(":memory:")
        con.executescript(schema_mod.SCHEMA)
        yield con
        con.close()

    @staticmethod
    def seed(conn, **cols):
        """插一条已有事故。NOT NULL 且无默认值的列给最小合法值。"""
        base = {
            "id": "old", "title": "旧事故", "source_url": "",
            "source_name": "safehoo", "project_type": "其他/未知",
            "accident_type": "坍塌事故", "severity": "一般事故",
            "report_type": "新闻报道", "text_type": "single",
            "classification_verdict": "confirmed",
            "classification_confidence": "high", "summary": "",
            "raw_text": "", "pipeline_version": "caics_v1.0",
            "created_at": "2020-01-01T00:00:00",
        }
        base.update(cols)
        conn.execute(
            f"INSERT INTO accidents ({','.join(base)}) "
            f"VALUES ({','.join('?' * len(base))})",
            list(base.values()),
        )
        conn.commit()

    def test_L1_内容哈希命中即拦(self, mod, conn):
        self.seed(conn, id="old1", raw_text="同一份原文")
        g = mod.AdmissionGuard(conn)
        layer, matched = g.check({}, {"content": "同一份原文"})
        assert layer == "L1" and matched == "old1"

    def test_L2_URL归一化后命中即拦(self, mod, conn):
        self.seed(conn, id="old2", source_url="https://www.example.com/a/")
        g = mod.AdmissionGuard(conn)
        # 去 www、去尾斜杠后与已存的同一条 URL
        layer, matched = g.check({}, {"source_url": "https://example.com/a"})
        assert layer == "L2" and matched == "old2"

    def test_本地书源不进URL索引(self, mod, conn):
        """同书路径、不同正文 → 不该被 L2 判重（L1 也因正文不同而不命中）。"""
        self.seed(conn, id="old3", source_url="/books/案例集.pdf",
                  raw_text="案例甲的正文")
        g = mod.AdmissionGuard(conn)
        layer, matched = g.check(
            {}, {"source_url": "/books/案例集.pdf", "content": "案例乙的正文"})
        assert (layer, matched) == (None, None), (
            "同一本书的路径不应把不同案例判成重复")

    def test_本地书源仍受L1内容哈希约束(self, mod, conn):
        """同书同案例全文相同 → L1 该判重（书源重复的正是这种）。"""
        self.seed(conn, id="old4", source_url="/books/案例集.pdf",
                  raw_text="案例甲的完整正文")
        g = mod.AdmissionGuard(conn)
        layer, matched = g.check(
            {}, {"source_url": "/books/案例集.pdf", "content": "案例甲的完整正文"})
        assert layer == "L1" and matched == "old4"

    def test_L3_指纹只告警不拦(self, mod, conn):
        self.seed(conn, id="old5", date="2024-03-16", city="南京市",
                  deaths=1, injuries=2, accident_type="坍塌事故")
        g = mod.AdmissionGuard(conn)
        s1 = {"date": "2024-03-16", "city": "南京市", "deaths": 1,
              "injuries": 2, "accident_type": "坍塌事故"}
        assert g.fingerprint_hit(s1) == "old5", "指纹应命中"
        # 但硬判据不该命中（正文与 URL 都没见过）
        layer, matched = g.check(
            s1, {"content": "另一份没人见过的正文", "source_url": "https://x.invalid/new"})
        assert (layer, matched) == (None, None), "指纹不能做硬判据"

    def test_空表不炸(self, mod, conn):
        g = mod.AdmissionGuard(conn)  # schema 建好但无数据
        assert g.check({"title": "新"}, {"content": "正文"}) == (None, None)

    def test_无表库不炸(self, mod):
        """测试/首次建库时 accidents 表可能还不存在，守门应退化不拦。"""
        empty = sqlite3.connect(":memory:")
        try:
            g = mod.AdmissionGuard(empty)
            assert g.check({}, {"content": "正文"}) == (None, None)
        finally:
            empty.close()
