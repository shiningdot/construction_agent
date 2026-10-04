"""M3 在线提取的端到端验证：起真 HTTP 服务，走完一条提取任务的全过程。

不碰 LLM——`staging_executor._pipeline` 被换成假 pipeline，但**落库、幂等、
闸门、重跑、进度回填全是真的**，工作库也是真的 sqlite 文件（WAL + 外键）。

顺序上先验「工作库还没建」的那一段：用户第一次打开 /extract 时正好是这个
状态，它不能 500。
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/_common

from _common import main_db_path  # noqa: E402

# 每次跑另起临时目录：这几个脚本会真建库、真并入、真写备份，复用同一目录
# 会让上一轮的库和备份混进这一轮。目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_"))
DB = TMP / "e2e_extract.db"
# 主库只被**读**（取 CAICS 类名与枚举口径），不被写。路径解析见
# `_common.main_db_path()`：默认从仓库根上溯找主仓库的 data/，两种运行位置
# 都对；`CAIEMS_MAIN_DB` 可显式覆盖。拿到空库会让提取静默判不出任何编码。
MAIN_DB = main_db_path()
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()
os.environ["CAIEMS_STAGING_DB"] = str(DB)
os.environ["CAIEMS_DB"] = MAIN_DB

from construction_agent.collector.llm_classifier import (  # noqa: E402
    ClassificationResult, ClassificationVerdict, TextType, TextTypeResult,
)
from construction_agent.extraction import staging_executor as ex  # noqa: E402
from construction_agent.extraction.caics_pipeline import (  # noqa: E402
    PURGE_NON_CONSTRUCTION, STATUS_COMPLETED, STATUS_PURGED, PipelineOutcome,
    StageRecord,
)
from construction_agent.extraction.step1_basic_extractor import Step1Result  # noqa: E402
from construction_agent.extraction.step2_caics_encoder import (  # noqa: E402
    CauseItem, Step2Result,
)
from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.factory import create_app  # noqa: E402

PORT = 5093
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


# ── 假 pipeline ──────────────────────────────────────────

class FakePipe:
    """真 pipeline 的接口面：run_once 返回 PipelineOutcome。"""

    class _C:
        _model = "fake-model"

    classifier = _C()

    def __init__(self):
        self.extractor = self._C()
        self.encoder = self._C()

    def run_once(self, title, text, publish_date="", source_id="", source_name=""):
        out = PipelineOutcome(source_id=source_id, title=title,
                              publish_date=publish_date)

        # 造一条 S3 失败：正文太短 → 提取器返回空（真实世界里 max_tokens
        # 不足就是这个表现）
        if "失败样本" in title:
            out.text_type = TextTypeResult(text_type=TextType.SINGLE)
            out.classification = ClassificationResult(
                item_id=source_id, item_title=title, is_construction=True,
                verdict=ClassificationVerdict.CONFIRMED_CONSTRUCTION)
            out.records += [StageRecord("S1", True, 10, "ok"),
                            StageRecord("S2", True, 30, "ok"),
                            StageRecord("S3", False, 400, error="S3 返回空")]
            out.status, out.purge_reason = "failed", "S3 无结果"
            return out

        # 造一条被 S2 拦下的：非施工事故
        if "交通事故" in title:
            out.text_type = TextTypeResult(text_type=TextType.SINGLE)
            out.classification = ClassificationResult(
                item_id=source_id, item_title=title, is_construction=False,
                verdict=ClassificationVerdict.CONFIRMED_NON_CONSTRUCTION)
            out.records += [StageRecord("S1", True, 10, "type=single"),
                            StageRecord("S2", True, 40, "verdict=non")]
            out.status, out.purge_reason = STATUS_PURGED, PURGE_NON_CONSTRUCTION
            return out

        tt = TextTypeResult(text_type=TextType.SINGLE, confidence="high",
                            reason="单起事故报道")
        tt.tokens_in, tt.tokens_out = 300, 60
        cl = ClassificationResult(
            item_id=source_id, item_title=title, is_construction=True,
            verdict=ClassificationVerdict.CONFIRMED_CONSTRUCTION,
            confidence="high", reason="建筑施工事故")
        cl.tokens_in, cl.tokens_out = 700, 90
        b = Step1Result(
            title=title, date=publish_date, location="江苏省", city="南京市",
            accident_type="坍塌", severity="较大事故", deaths=3, injuries=2,
            direct_cause="模板支撑体系失稳", project_type="房屋建筑",
            construction_phase="主体结构", summary="某工地浇筑时支撑体系失稳坍塌。",
            contractor="某建设集团有限公司",
        )
        b.tokens_in, b.tokens_out = 8000, 500
        c = Step2Result(
            primary_cause="模板支撑体系失稳导致坍塌",
            cause_chain="M1(监管缺失)→H1(违规操作)→T2(物料超载)→F2(支撑失稳)",
            causes=[
                CauseItem("M1", "现场监管缺失", "监理单位未对违规堆放物料进行制止",
                          "监理未履行监督职责，表明现场监管环节失效"),
                CauseItem("H1", "违反操作规程", "未按专项施工方案搭设支撑体系",
                          "施工方违反方案要求，属于典型的违规操作"),
                CauseItem("F2", "支撑体系失稳", "原文未直接提及，但从整体坍塌可推断",
                          "支撑体系失去承载力是坍塌的直接物理原因"),
            ])
        c.tokens_in, c.tokens_out = 15000, 900
        out.text_type, out.classification, out.basic, out.causes = tt, cl, b, c
        out.records += [StageRecord("S1", True, 900, "ok"),
                        StageRecord("S2", True, 500, "ok"),
                        StageRecord("S3", True, 17000, "ok"),
                        StageRecord("S4", True, 24000, "ok")]
        out.status = STATUS_COMPLETED
        return out


ex._pipeline = lambda: FakePipe()

# ── 起服务（此时工作库还不存在） ─────────────────────────

app = create_app({"TESTING": False})
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.6)


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + quote(path, safe="/?=&"), data=data, method=method,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(raw) if "json" in ct
                                 else raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[")
                        else raw.decode("utf-8", "replace"))


print("\n=== ① 页面与静态资源 ===")
st, html = req("/extract")
check("GET /extract 200", st == 200, st)
check("页面引用 extract.js", "extract.js" in html)
check("页面引用 dom.js", "dom.js" in html)
check("页面有待提取表体", 'id="pendBody"' in html)
check("页面有成本确认弹层", 'id="costModal"' in html)
check("页面有详情抽屉", 'id="drawer"' in html)
check("导航里有「在线提取」入口", "在线提取" in req("/")[1])
check("提取页里导航有高亮项", 'is-active' in html)
check("概览页只有一处高亮（不会同时点亮两个）",
      req("/")[1].count('is-active') == 1, req("/")[1].count('is-active'))

st, css = req("/static/app.css")
check("GET /static/app.css 200", st == 200, st)
check("css 含弹层样式 .modal", ".modal {" in css and ".modal[hidden]" in css)
check("css 含编码分层的五层配色", all(f".chip.layer-{L}" in css
      for L in "MFTHC"), [L for L in "MFTHC" if f".chip.layer-{L}" not in css])
check("css 含证据链列表样式", ".cause-item" in css and ".cause-key" in css)

st, js = req("/static/extract.js")
check("GET /static/extract.js 200", st == 200, st)
check("js 用正确的接口前缀", "/api/extract/" in js)
check("js 不往 DOM 里塞 HTML（原文里有尖括号是常态）",
      ".innerHTML" not in js and "insertAdjacentHTML" not in js)
check("js 用 setInterval 轮询任务", "setInterval(" in js)

print("\n=== ② 工作库还没建：不 500 ===")
for path in ("/api/extract/pending", "/api/extract/tasks", "/api/extract/results"):
    st, body = req(path)
    ok = st == 200 and body.get("available") is False
    check(f"{path} 200 且 available=false", ok,
          f"{st} {body.get('available')}")
st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["x"]})
check("库没建时建任务 503 而不是 500", st == 503, st)
# 契约变更（`ca41bc4` 引入 `dberr.py`）：`message` 只说「哪里不对」，
# **下一步动作在 `hint` 里**。这条原先只查 message——那是 dberr 之前的口径
# （当时 503 的 message 就是 BUILD_HINT 本身）。dberr 落地时没跑 e2e，
# 断言静默失效，直到 2026-10-05 复跑才发现。两键合并查，不依赖动作放哪一栏。
check("503 里给出建库提示",
      "create_staging_db" in (body.get("message", "") + body.get("hint", "")))

st, body = req("/api/extract/taxonomy")
check("理论版本不依赖工作库（读最终库）", st == 200
      and body["data"]["caics"]["total"] == 19, st)

# ── 建库 + 造原文 ────────────────────────────────────────
sdb.init_db()
ROWS = [
    ("r1", "南京市某工地模板支撑坍塌致3人死亡", "confirmed"),
    ("r2", "苏州市某厂房脚手架失稳事故", "confirmed"),
    ("r3", "某工地失败样本（正文缺失）", "confirmed"),
    ("r4", "无锡市某项目交通事故通报", "confirmed"),
    ("r5", "常州市某项目高处坠落事故", "confirmed"),
    ("r6", "尚未确认的原文", "review"),
]
with sdb.write_tx() as con:
    con.execute("INSERT INTO collect_sources (source_key, display_name, "
                "source_group, track) VALUES ('safehoo','安全管理网','national','network')")
    for rid, title, verdict in ROWS:
        con.execute(
            "INSERT INTO raw_accidents (raw_id, source_key, title, content, "
            "source_url, publish_date, fetch_time, url_hash, gate_verdict, status) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'gated')",
            (rid, "safehoo", title, f"{title}。经调查，事故起因为现场管理缺失。" * 6,
             f"https://www.safehoo.com/News/{rid}.shtml", "2026-03-11",
             "2026-09-30T02:00:00Z", f"h_{rid}", verdict))

print("\n=== ③ 理论版本（查表得来，无硬编码） ===")
st, body = req("/api/extract/taxonomy")
d = body["data"]
check("CAICS 19 类", d["caics"]["total"] == 19, d["caics"]["total"])
check("版本号来自 pipeline_metadata",
      d["caics"]["version"] == "caics_v1.0", d["caics"]["version"])
check("五层齐全", set(c["code"][0] for c in d["caics"]["classes"])
      == {"M", "F", "T", "H", "C"})
check("每类都带中文名", all(c["name"] for c in d["caics"]["classes"]))
check("枚举版本 5 个", len(d["enums"]) == 5, d["enums"])
check("枚举键名是人可读的（去掉了 _enum_version 后缀）",
      set(d["enums"]) == {"work_task", "equipment_involved", "material_involved",
                          "worker_type", "building_structure"}, list(d["enums"]))
check("枚举版本号解析自 JSON（不是整段原文）",
      set(d["enums"].values()) == {"2.0-llm", "1.0-llm"}, d["enums"])
check("证据三级词表带中文名",
      [x["label"] for x in d["evidence_levels"]] == ["直引", "推断", "弱推断"],
      d["evidence_levels"])
check("类计数与主库一致（M1 2208）",
      [c for c in d["caics"]["classes"] if c["code"] == "M1"][0]["n"] == 2208,
      [c["n"] for c in d["caics"]["classes"] if c["code"] == "M1"])

print("\n=== ④ 待提取队列 ===")
st, body = req("/api/extract/pending")
check("pending 200", st == 200, st)
ids = [r["raw_id"] for r in body["data"]["items"]]
check("只收 confirmed 的（r6 不在）", "r6" not in ids, ids)
check("5 条待提取", len(ids) == 5, ids)
check("带正文长度", all(r["content_len"] > 0 for r in body["data"]["items"]))
check("带来源 key", all(r["source_key"] == "safehoo" for r in body["data"]["items"]))

print("\n=== ⑤ 预估：只算数不落库 ===")
st, body = req("/api/extract/estimate?n=5")
est = body["data"]
check("estimate 200", st == 200, st)
check("bar 条数如实回报", est["items"] == 5, est["items"])
check("输入:输出约 2:1",
      abs(est["est_tokens_in"] - 2 * est["est_tokens_out"]) <= 5,
      (est["est_tokens_in"], est["est_tokens_out"]))
check("带免责说明", "非结算依据" in est["note"])
check("没建任何任务行", sdb.scalar("SELECT COUNT(*) FROM extract_tasks") == 0)

print("\n=== ⑥ 成本闸门：建任务不花钱 ===")
st, body = req("/api/extract/tasks", "POST",
               {"raw_ids": ["r1", "r2", "r3", "r4", "r5"]})
check("POST /tasks 201", st == 201, st)
tid = body["data"]["task_id"]
print("      task_id =", tid)
check("返回预估", body["data"]["estimate"]["est_tokens"] > 0)
check("状态 pending", body["data"]["task"]["status"] == "pending")
check("**未 start 前暂存区一行都没有**",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 0)
check("未 start 前一条 LLM 都没调（tokens 全 0）",
      body["data"]["task"]["usage"]["tokens"] == 0)

print("\n=== ⑦ 启动并轮询 ===")
st, body = req(f"/api/extract/tasks/{tid}/start", "POST")
check("start 200", st == 200, st)
deadline = time.time() + 60
task = None
while time.time() < deadline:
    st, body = req(f"/api/extract/tasks/{tid}")
    task = body["data"]
    if task["status"] in ("done", "failed", "cancelled"):
        break
    time.sleep(0.3)

check("任务 done", task["status"] == "done", task["status"])
check("进度 5/5 100%", (task["progress"]["done"], task["progress"]["total"],
                        task["progress"]["percent"]) == (5, 5, 100),
      task["progress"])
check("失败 1 条（那条「失败样本」）", task["progress"]["failed"] == 1,
      task["progress"])
check("errors 里说清死在哪一段",
      task["errors"][0]["stage"] == "S3" and "r3" == task["errors"][0]["raw_id"],
      task["errors"])
check("入库 3 条（r1/r2/r5；r4 被闸门拦下，r3 失败）", task["staged"] == 3,
      task["staged"])
# by_type 统计的是**跑过 S1 的条数**（5 条），staged 才是真落库的（3 条）。
# 两个数不一样是设计如此：被闸门拦下的原文不进暂存区，但它在类型分布里
# 该被看见，否则「选了 5 条怎么只入库 3 条」就成了查不出的谜。
check("by_type 统计到跑过的全部 5 条（含被拦下的）",
      task["by_type"] == {"single": 5}, task["by_type"])
check("账对得上：跑 5 条 = 落库 3 + 失败 1 + 拦下 1",
      task["items"] == 5 and task["staged"] == 3
      and task["progress"]["failed"] == 1
      and task["items"] - task["staged"] - task["progress"]["failed"] == 1,
      (task["items"], task["staged"], task["progress"]["failed"]))
check("用量已回填且非 0", task["usage"]["tokens"] > 0, task["usage"])
check("成本折算出来", task["usage"]["cost_cny"] > 0, task["usage"]["cost_cny"])
check("预估/实际偏差可算", task["usage"]["deviation_pct"] is not None,
      task["usage"])

print("\n=== ⑧ 暂存产出与主库对账 ===")
check("暂存 3 行", sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 3)
check("编码 9 条（3 行 × 3 条）",
      sdb.scalar("SELECT COUNT(*) FROM staging_causes") == 9)
st, body = req("/api/extract/results")
check("results 200", st == 200, st)
check("列表 3 条", body["meta"]["total"] == 3, body["meta"])
check("每行带编码条数", all(r["cause_count"] == 3 for r in body["data"]))
check("列表按时间倒序（最新的在前）",
      body["data"][0]["created_at"] >= body["data"][-1]["created_at"])

check("split_index 全为 0（NULL 会让 UNIQUE 失效）",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents WHERE split_index IS 0") == 3)
check("UNIQUE(raw_id, split_index) 真的拦得住重复",
      sdb.scalar("SELECT COUNT(*) FROM (SELECT raw_id, split_index, COUNT(*) c "
                 "FROM staging_accidents GROUP BY raw_id, split_index "
                 "HAVING c > 1)") == 0)

# 对齐检查：暂存列名必须在主库 accidents 的列里有对应物，否则阶段③并不过去
import sqlite3  # noqa: E402
_main = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
main_cols = {r[1] for r in _main.execute("PRAGMA table_info(accidents)")}
main_cause_cols = {r[1] for r in _main.execute("PRAGMA table_info(caics_causes)")}
_main.close()
stage_cols = {r["name"] for r in sdb.query(
    "SELECT * FROM pragma_table_info('staging_accidents')")}
staging_cause_cols = {r["name"] for r in sdb.query(
    "SELECT * FROM pragma_table_info('staging_causes')")}
check("staging_accidents 的字段列在主库 accidents 里都能找到（S3 的 31 列对得上）",
      len({c for c in stage_cols if c in main_cols or c in
           ("stage_id", "raw_id", "task_id", "origin_segment", "split_index",
            "text_type", "text_type_reason", "text_type_confidence",
            "relevance_verdict", "relevance_reason", "relevance_stage",
            "relevance_confidence", "s3_model", "s3_ms", "s3_tokens_in",
            "s3_tokens_out", "s4_model", "s4_ms", "s4_tokens_in",
            "s4_tokens_out", "s4_primary_cause", "s4_cause_chain",
            "review_status", "review_note", "reviewer", "reviewed_at",
            "created_at", "location")}) == len(stage_cols),
      sorted(stage_cols - main_cols))
check("staging_causes 的核心列在主库 caics_causes 里都有",
      {"code", "name", "evidence", "inference", "evidence_quality"}
      <= main_cause_cols, sorted(main_cause_cols))

print("\n=== ⑨ 证据等级三级 ===")
rows = sdb.query("SELECT evidence_quality, evidence_quality_rule, "
                 "evidence_quality_llm, COUNT(*) c FROM staging_causes "
                 "GROUP BY evidence_quality")
by_eq = {r["evidence_quality"]: r["c"] for r in rows}
# 每条记录 3 个编码：M1（inference 里有「表明」→ 推断）、H1（直引）、
# F2（evidence 里写了「原文未直接」→ 弱推断）。3 行 × 每级各 1 = 3/3/3。
check("三级各 3 条（3 行 × 每级 1 条）",
      by_eq == {"direct_quote": 3, "indirect_infer": 3, "weak_infer": 3}, by_eq)
check("inference 里的「表明」判成推断（不是直引）",
      by_eq.get("indirect_infer") == 3, by_eq)
check("evidence 里的「原文未直接」优先判成弱推断",
      by_eq.get("weak_infer") == 3, by_eq)
check("生效值 = 规则值", all(r["evidence_quality"] == r["evidence_quality_rule"]
      for r in sdb.query("SELECT * FROM staging_causes")))
check("_llm 列恒为 NULL（等用户裁决，未擅自改 prompt）",
      sdb.scalar("SELECT COUNT(*) FROM staging_causes "
                 "WHERE evidence_quality_llm IS NOT NULL") == 0)

print("\n=== ⑩ 单条详情 ===")
st, body = req("/api/extract/results")
sid = body["data"][0]["stage_id"]
st, body = req(f"/api/extract/results/{sid}")
d = body["data"]
check("详情 200", st == 200, st)
check("31 个基础字段在 fields 里", d["fields"]["direct_cause"] == "模板支撑体系失稳")
check("location 保留 S3 原名（并入时才改成 province）",
      d["fields"]["location"] == "江苏省")
check("审核元数据没混进 fields",
      not ({"stage_id", "created_at", "review_status"} & set(d["fields"])))
check("闸门判定单独成块", d["gates"]["text_type"] == "single", d["gates"])
check("审核状态初始为 pending", d["review"]["review_status"] in ("pending", None))
# 顺序是 CAICS 的层序 M→F→T→H→C（sort_order 101/202/401），**不是**模型
# 返回编码的先后（假数据里给的是 M1/H1/F2）。展示时按层排，读者才能按
# 「管理根基 → 防线失效 → 触发条件 → 人的行为」一路看下来。
check("编码按 CAICS 层序排（M→F→T→H→C，不是模型返回顺序）",
      [c["code"] for c in d["causes"]] == ["M1", "F2", "H1"],
      [c["code"] for c in d["causes"]])
check("sort_order 单调不减",
      [c["sort_order"] for c in d["causes"]] == sorted(
          [c["sort_order"] for c in d["causes"]]),
      [c["sort_order"] for c in d["causes"]])
check("层序与 compute_sort_order 的口径一致（M=101 < F=202 < H=401）",
      [c["sort_order"] for c in d["causes"]] == [101, 202, 401],
      [c["sort_order"] for c in d["causes"]])
check("证据中文名来自后端词表（顺序与编码一一对应）",
      [c["evidence_quality_label"] for c in d["causes"]] == ["推断", "弱推断", "直引"],
      [c["evidence_quality_label"] for c in d["causes"]])
check("过程元数据四段齐（S3/S4 各有时长、模型、token）",
      set(d["meta"]) >= {"s3_model", "s3_ms", "s3_tokens_in", "s3_tokens_out",
                         "s4_model", "s4_ms", "s4_tokens_in", "s4_tokens_out"},
      list(d["meta"]))
check("S3 模型名记录下来（批次档位可追溯）", d["meta"]["s3_model"] == "fake-model")
check("带原始文本可逐条对照", bool(d["raw"]["content"]))

print("\n=== ⑪ 筛选与分页下推 SQL ===")
st, body = req("/api/extract/results?q=南京市")
check("关键词命中标题（1 条）", body["meta"]["total"] == 1, body["meta"])
st, body = req("/api/extract/results?q=失稳")
check("关键词也搜摘要（3 条假数据的 summary 都含「失稳」）",
      body["meta"]["total"] == 3, body["meta"])
st, body = req("/api/extract/results?q=不存在的词")
check("无命中返回空", body["meta"]["total"] == 0, body["meta"])
st, body = req("/api/extract/results?text_type=single")
check("按类型筛出 3 条", body["meta"]["total"] == 3, body["meta"])
st, body = req("/api/extract/results?text_type=bogus")
check("非法枚举被忽略（不报错也不注入）", body["meta"]["total"] == 3, body["meta"])
st, body = req("/api/extract/results?no_cause=1")
check("「没编出致因的」筛出 0 条（三条都编出来了）",
      body["meta"]["total"] == 0, body["meta"])
st, body = req(f"/api/extract/results?task_id={tid}")
check("按任务筛出 3 条", body["meta"]["total"] == 3, body["meta"])
st, body = req("/api/extract/results?size=2&page=1")
p1 = {r["stage_id"] for r in body["data"]}
check("每页 2 条、共 2 页", len(body["data"]) == 2 and body["meta"]["pages"] == 2,
      body["meta"])
st, body = req("/api/extract/results?size=2&page=2")
p2 = {r["stage_id"] for r in body["data"]}
check("两页无重叠", not (p1 & p2))
st, body = req("/api/extract/results?size=99999&page=0")
check("越界分页被夹回", body["meta"]["size"] == 200 and body["meta"]["page"] == 1,
      body["meta"])

print("\n=== ⑫ 幂等：重跑同一个任务不翻倍 ===")
before = sdb.scalar("SELECT COUNT(*) FROM staging_causes")
ex.execute_task(tid)   # 直接再跑一遍（等价于用户点两次）
check("暂存仍是 3 行", sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 3)
check("编码没翻倍（仍是 %d 条）" % before,
      sdb.scalar("SELECT COUNT(*) FROM staging_causes") == before,
      sdb.scalar("SELECT COUNT(*) FROM staging_causes"))

print("\n=== ⑬ 单条重跑（覆盖式） ===")
st, body = req("/api/extract/results")
sid2 = [r["stage_id"] for r in body["data"] if r["raw_id"] == "r1"][0]
st, body = req(f"/api/extract/results/{sid2}/rerun", "POST")
check("重跑 200", st == 200, st)
new_sid = body["data"]["stage_id"]
check("旧 stage_id 被替换", body["data"]["replaced"] == sid2 and new_sid != sid2,
      body["data"])
check("旧行已删", sdb.scalar(
    "SELECT COUNT(*) FROM staging_accidents WHERE stage_id = ?", (sid2,)) == 0)
check("旧行的编码随之清掉", sdb.scalar(
    "SELECT COUNT(*) FROM staging_causes WHERE stage_id = ?", (sid2,)) == 0)
check("新行带 3 条编码", sdb.scalar(
    "SELECT COUNT(*) FROM staging_causes WHERE stage_id = ?", (new_sid,)) == 3)
check("总数不变（覆盖，不是新增）",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 3)
check("沿用原 task_id", sdb.scalar(
    "SELECT task_id FROM staging_accidents WHERE stage_id = ?", (new_sid,)) == tid)
st, body = req("/api/extract/results/nope/rerun", "POST")
check("不存在的条目 404", st == 404, st)

print("\n=== ⑭ 取消与删除 ===")
st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["r6"]})
check("未确认的原文被拒（gate_verdict != confirmed）", st == 400, st)
check("错误里点名是哪条", "r6" in body["message"], body["message"][:60])

st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["r1"]})
tid2 = body["data"]["task_id"]
st, body = req(f"/api/extract/tasks/{tid2}/cancel", "POST")
check("未启动的任务取消即终结", st == 200 and body["data"]["status"] == "cancelled",
      body["data"]["status"])
check("提示语说明是立即取消", "立即" in body["note"], body["note"])
st, body = req(f"/api/extract/tasks/{tid2}", "DELETE")
check("取消后能删", st == 200, st)
check("删任务不动产出（仍 3 行）",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 3)
check("但产出的 task_id 被抹空（不指向已删的任务）",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents WHERE task_id = ?",
                 (tid2,)) == 0)

st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["r1"]})
tid3 = body["data"]["task_id"]
st, body = req(f"/api/extract/tasks/{tid3}", "DELETE")
check("pending 任务不许直接删（得先取消）", st == 409, st)
req(f"/api/extract/tasks/{tid3}/cancel", "POST")
req(f"/api/extract/tasks/{tid3}", "DELETE")

print("\n=== ⑮ 闸门拦下不建行 ===")
# r4 是「交通事故」→ S2 拦下。它出现在 by_type 里，但暂存区没有它的行
check("暂存区没有 r4 的行",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents WHERE raw_id = 'r4'") == 0)
check("r4 仍在待提取队列里（可重选重跑）",
      "r4" in [r["raw_id"] for r in req("/api/extract/pending")[1]["data"]["items"]])

print("\n=== ⑯ 中断恢复：进程重启后的僵死任务 ===")
st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["r2"]})
tid4 = body["data"]["task_id"]
# 手工把它改成 running 模拟「进程没了但库里还写着运行中」
with sdb.write_tx() as con:
    con.execute("UPDATE extract_tasks SET status='running' WHERE task_id=?",
                (tid4,))
n = ex.reconcile_stale_tasks()
check("清理函数找到了僵死任务", n >= 1, n)
st, body = req(f"/api/extract/tasks/{tid4}")
check("状态被标为 failed 并说明原因",
      body["data"]["status"] == "failed"
      and "进程重启" in (body["data"]["error"] or ""),
      (body["data"]["status"], body["data"]["error"]))
check("已完成任务的产出不受影响（仍 3 行）",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 3)

print("\n=== ⑰ 坏输入 ===")
st, body = req("/api/extract/results/nope")
check("不存在的 stage_id → 404", st == 404, st)
st, body = req("/api/extract/tasks/nope")
check("不存在的 task_id → 404", st == 404, st)
st, body = req("/api/extract/tasks/nope/cancel", "POST")
check("取消不存在的任务 → 409", st == 409, st)
st, body = req("/api/extract/tasks", "POST", {"raw_ids": "r1"})
check("raw_ids 不是数组 → 400", st == 400, st)
st, body = req("/api/extract/tasks", "POST", {})
check("缺 raw_ids → 400", st == 400, st)
st, body = req("/api/extract/tasks", "POST", {"raw_ids": []})
check("空数组 → 400", st == 400, st)
st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["不存在"]})
check("不存在的 raw_id → 400", st == 400, st)
st, body = req("/api/extract/tasks", "POST", {"raw_ids": ["r1", "r6"]})
check("一坏全拒（不静默丢掉坏的那条）", st == 400, st)

server.shutdown()

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
print("=" * 52)
sys.exit(1 if FAIL else 0)
