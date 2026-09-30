"""全链路端到端串测：①采集 → 闸门 → ②提取 → ③暂存审核 → 并入 → ④展示。

与 `e2e_collect` / `e2e_extract` / `e2e_review` 的区别在于**段与段之间谁搭桥**。
那三个脚本各测一段，跨段的桥是脚本自己手工架的（直接 INSERT 一条 raw 行、
直接 INSERT 一条 staging 行）。本脚本不架桥：它从一条原始记录出发，只走
HTTP 接口和真实的库，一路走到最终库里的一行事故数据，再把这一行与主库里
**同一条**逐列比对。

比对是这里唯一真正的验收标准
----------------------------
蓝图 07 写的是「产出与主库 `accidents`/`caics_causes` 同构，可无缝并入」。
"同构"没法靠读代码确认，只能实测：把主库里已发表的一条数据读出来、喂进
在线管道、再落回一个新库，两边每一列都该一样。

不一样的列分两类，必须分开对待，否则不是漏报就是误报：

  · **已知口径差异**——两侧设计上本就不同（见下面 KNOWN_DIFF 的逐条依据），
    脚本显式列出并对值做断言，不当失败
  · **其余全部**——必须逐列相同

后者才是这个脚本真正要防的：**映射写错不报错**。`location → province` 抄错
一个名字的结果是省份写进城市列，接口 200、字段"有值"、页面看着正常。

样本来自主库，不是现编的
------------------------
`4125899c3a50`（海地学校坍塌，2008）是主库里一条形态齐全的记录：48 列里
36 列非空，4 条编码跨 M/F 两层，证据等级三种里占了两种。用它往返，才能
同时覆盖"有值的列"和"该为空却填了默认值的列"——后者是 S3 的 dataclass
默认值最容易捅出来的洞（`weather` 默认 `"未提及"`、`collapse_subtype` 默认
`"不适用"`，源数据里这两列都是 NULL）。
"""

import importlib.util
import json
import os
import sqlite3
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
sys.path.insert(0, str(Path(__file__).resolve().parent))     # 同目录的 _common

from _common import main_db_path  # noqa: E402

# 每次跑另起临时目录：本脚本会真建两个库、真并入、真写备份。复用同一目录
# 会让上一轮的库混进这一轮。目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_pipeline_"))
STAGING = TMP / "pipeline_staging.db"
FINAL = TMP / "pipeline_final.db"

# 主库只被**读**。路径解析见 `_common.main_db_path()`：默认从仓库根上溯找
# 主仓库的 data/，两种运行位置都对；`CAIEMS_MAIN_DB` 可显式覆盖。
MAIN_DB = main_db_path()

# 主库里一条形态齐全的真实事故：48 列中 36 列非空、4 条编码跨 M/F 两层、
# 证据等级含 direct_quote 与 indirect_infer 两种。
SOURCE_ID = "4125899c3a50"

for suffix in ("", "-wal", "-shm"):
    for base in (STAGING, FINAL):
        p = Path(str(base) + suffix)
        if p.exists():
            p.unlink()

os.environ["CAIEMS_STAGING_DB"] = str(STAGING)
os.environ["CAIEMS_DB"] = str(FINAL)

_spec = importlib.util.spec_from_file_location(
    "create_db_v2", ROOT / "scripts" / "create_db_v2.py")
create_db_v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create_db_v2)

from construction_agent.collector.llm_classifier import (  # noqa: E402
    ClassificationResult, ClassificationVerdict, TextType, TextTypeResult,
)
from construction_agent.extraction import staging_executor as ex  # noqa: E402
from construction_agent.extraction.caics_pipeline import (  # noqa: E402
    STATUS_COMPLETED, PipelineOutcome, StageRecord,
)
from construction_agent.extraction.step1_basic_extractor import Step1Result  # noqa: E402
from construction_agent.extraction.step2_caics_encoder import (  # noqa: E402
    CauseItem, Step2Result,
)
from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.factory import create_app  # noqa: E402

PORT = 5097                       # 采集 5091 · 提取 5093/5094 · 审核 5095/5096
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


def note(text):
    """已知缺口/口径差异的记录行。刻意用与 OK/FAIL 不同的前缀，免得被当成断言。"""
    print("  ···· " + text)


# ══════════════════════════════════════════════════════════════════════
# 读主库样本
# ══════════════════════════════════════════════════════════════════════

if not Path(MAIN_DB).exists():
    print(f"主库不存在：{MAIN_DB}\n用 CAIEMS_MAIN_DB 指定主库路径。")
    sys.exit(2)

_main = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
_main.row_factory = sqlite3.Row
ACC_COLS = [d[0] for d in _main.execute("SELECT * FROM accidents LIMIT 0").description]
_src_row = _main.execute("SELECT * FROM accidents WHERE id = ?", (SOURCE_ID,)).fetchone()
if _src_row is None:
    print(f"主库里没有 {SOURCE_ID}，样本数据变了，请改用现存的一条。")
    sys.exit(2)
SRC = {c: _src_row[c] for c in ACC_COLS}
SRC_CAUSES = [dict(r) for r in _main.execute(
    "SELECT code, name, evidence, inference, evidence_quality, sort_order "
    "FROM caics_causes WHERE accident_id = ? ORDER BY sort_order, id", (SOURCE_ID,))]

# 主库 classification_verdict 的实际词表——用来给下面的"已知口径差异"作证，
# 而不是靠印象断言它"应该不同"。
_verdict_dist = dict(_main.execute(
    "SELECT classification_verdict, COUNT(*) FROM accidents GROUP BY 1"))
_main.close()

print(f"样本 {SOURCE_ID}：{SRC['title'][:28]}…  "
      f"{sum(1 for c in ACC_COLS if SRC[c] not in (None, ''))}/{len(ACC_COLS)} 列非空，"
      f"{len(SRC_CAUSES)} 条编码")


# ══════════════════════════════════════════════════════════════════════
# 假 pipeline：把样本原样回放，绕过 LLM 但走真链路
# ══════════════════════════════════════════════════════════════════════
#
# 三处**刻意不补默认值**，这是本脚本最容易写错的地方：
#
# 1. `Step1Result` 的 dataclass 默认值不等于"空"。`weather` 默认 `"未提及"`、
#    `collapse_subtype` 默认 `"不适用"`、`contractor_type` 默认 `"未提及"`，
#    而源数据里这三列都是 NULL。构造时若省掉它们，落库会变成那些字符串，
#    往返比对就会"合理地"失败——而且会让人以为是映射写错了。
#    所以这里逐列从源行取值（含 None）显式传，不做任何智能补齐。
#
# 2. 耗时列给的是**测试值**（1234/5678）而不是源数据的真实耗时。用源值会让
#    断言变成"自造自验"；用不同值才能证明这两个数确实是从 pipeline 透传下来的。
#    因此这一列在往返比对里被排除，另立断言。
#
# 3. `confidence` 用源行的值（medium），不是随手给个 high——它进的就是
#    `accidents.classification_confidence` 那一列。

# 源列 → Step1Result 字段。唯一改名的一处是 province → location：
# 暂存区照 S3 的原名 `location`，到并入时才变成 `accidents.province`。
_S3_MAP = {
    "title": "title", "date": "date", "accident_time": "accident_time",
    "province": "location", "city": "city", "district": "district",
    "accident_location_detail": "accident_location_detail", "weather": "weather",
    "project_name": "project_name", "project_type": "project_type",
    "building_structure": "building_structure",
    "construction_phase": "construction_phase", "work_task": "work_task",
    "developer": "developer", "contractor": "contractor",
    "contractor_type": "contractor_type",
    "supervision_company": "supervision_company",
    "accident_type": "accident_type", "severity": "severity",
    "deaths": "deaths", "injuries": "injuries",
    "trapped_count": "trapped_count",
    "direct_economic_loss": "direct_economic_loss",
    "collapse_subtype": "collapse_subtype", "fall_height": "fall_height",
    "equipment_involved": "equipment_involved",
    "material_involved": "material_involved", "worker_type": "worker_type",
    "direct_cause": "direct_cause", "report_type": "report_type",
    "summary": "summary",
}
FAKE_S3_MS, FAKE_S4_MS = 1234, 5678


class FakePipe:
    """真 pipeline 的接口面：`run_once` 返回 `PipelineOutcome`。"""

    class _C:
        _model = "fake-model"

    classifier = _C()

    def __init__(self):
        self.extractor = self._C()
        self.encoder = self._C()

    def run_once(self, title, text, publish_date="", source_id="", source_name=""):
        out = PipelineOutcome(source_id=source_id, title=title,
                              publish_date=publish_date)

        # 体裁置信度落不到最终库（accidents 没有这一列，staging 有），
        # 所以这里给固定值——往返比对里没有它，别假装它来自源数据
        tt = TextTypeResult(text_type=TextType.SINGLE, confidence="high",
                            reason="单起事故报道，来源为新闻报道")
        tt.tokens_in, tt.tokens_out = 300, 60
        # verdict 取枚举值而非源行的 `batch_direct`——后者是旧批量脚本自造的
        # 来源码，不在 `ClassificationVerdict` 里。两套词表的差异见 KNOWN_DIFF。
        cl = ClassificationResult(
            item_id=source_id, item_title=title, is_construction=True,
            verdict=ClassificationVerdict.CONFIRMED_CONSTRUCTION,
            confidence=SRC.get("classification_confidence") or "medium",
            reason="报道主体为学校校舍坍塌事故")
        cl.tokens_in, cl.tokens_out = 700, 90

        b = Step1Result(**{f: SRC[c] for c, f in _S3_MAP.items()})
        b.tokens_in, b.tokens_out = 8000, 500

        c = Step2Result(
            primary_cause="建筑结构整体失稳导致坍塌",
            cause_chain="M1(监管缺失)→M4(规程漏洞)→F2(支撑体系失稳)",
            causes=[CauseItem(r["code"], r["name"], r["evidence"], r["inference"])
                    for r in SRC_CAUSES])
        c.tokens_in, c.tokens_out = 15000, 900

        out.text_type, out.classification, out.basic, out.causes = tt, cl, b, c
        out.records += [StageRecord("S1", True, 900, "ok"),
                        StageRecord("S2", True, 500, "ok"),
                        StageRecord("S3", True, FAKE_S3_MS, "ok"),
                        StageRecord("S4", True, FAKE_S4_MS, "ok")]
        out.status = STATUS_COMPLETED
        return out


ex._pipeline = lambda: FakePipe()

# ── 建两个真库 ───────────────────────────────────────────

sdb.init_db()
_con = sqlite3.connect(FINAL)
_con.executescript(create_db_v2.SCHEMA)
_con.commit()
_con.close()

with sdb.write_tx() as c:
    c.execute("INSERT OR IGNORE INTO collect_sources (source_key, display_name, "
              "source_group, track) VALUES ('safehoo','安全管理网','national','network')")

# ── 起真服务 ─────────────────────────────────────────────

app = create_app({"TESTING": False})
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.6)


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + quote(path, safe="/?=&"), data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}


def page(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, r.read().decode("utf-8")


def final_rows(sql, args=()):
    con = sqlite3.connect(f"file:{FINAL}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════
# ① 采集：一条原始记录进库
# ══════════════════════════════════════════════════════════════════════

print("\n── ① 在线采集：原始记录入 raw_accidents ──")

st, body = req("/api/collect/stats")
check("采集统计 200", st == 200, st)
check("漏斗四段齐全",
      [f["stage"] for f in body["data"]["funnel"]]
      == ["raw", "kept", "manual", "promoted"], body["data"]["funnel"])
check("此时原始抓取为 0", body["data"]["funnel"][0]["count"] == 0)

# raw_id 取样本的 accidents.id：并入时 `r.raw_id AS id`，两者同一列。
# 这样往返比对才有意义——不然新库的行与主库的行是两个 id，只能比字段不能比身份。
SRC_URL = SRC["source_url"]
with sdb.write_tx() as con:
    con.execute(
        "INSERT INTO raw_accidents (raw_id, source_key, source_url, fetch_time, "
        "publish_date, url_hash, title, content, status) "
        "VALUES (?,?,?,?,?,?,?,?, 'new')",
        (SOURCE_ID, "safehoo", SRC_URL, "2026-10-01T08:00:00",
         SRC["publish_date"], f"e2e_{SOURCE_ID}", SRC["title"],
         SRC["raw_text"]))

st, body = req("/api/collect/raw?status=new")
check("采集队列看得到这条",
      [r["raw_id"] for r in body["data"]] == [SOURCE_ID],
      [r["raw_id"] for r in body["data"]])
st, body = req(f"/api/collect/raw/{SOURCE_ID}")
check("原始记录详情带正文", bool(body["data"]["content"]))

st, body = req("/api/collect/stats")
check("漏斗「原始抓取」= 1", body["data"]["funnel"][0]["count"] == 1,
      body["data"]["funnel"])


# ══════════════════════════════════════════════════════════════════════
# ①② 衔接：人工闸门
# ══════════════════════════════════════════════════════════════════════

print("\n── ①② 衔接：人工闸门裁决 ──")

st, body = req("/api/collect/raw/gate", "POST",
               {"raw_ids": [SOURCE_ID], "verdict": "confirmed", "note": "确为施工相关"})
check("闸门通过 200", st == 200, (st, body))
check("更新 1 行", body["data"]["updated"] == 1, body["data"])

raw = sdb.query_one("SELECT gate_verdict, gate_stage, gate_confidence, "
                    "gate_at, status FROM raw_accidents WHERE raw_id = ?", (SOURCE_ID,))
check("gate_verdict 落库", raw["gate_verdict"] == "confirmed", dict(raw))
check("gate_stage = manual", raw["gate_stage"] == "manual", dict(raw))
check("status 置为 gated", raw["status"] == "gated", dict(raw))

st, body = req("/api/collect/stats")
funnel = {f["stage"]: f["count"] for f in body["data"]["funnel"]}
check("漏斗「人工确认」= 1", funnel["manual"] == 1, funnel)
check("漏斗「自动闸门通过」仍为 0（本批没人走自动档）", funnel["kept"] == 0, funnel)


# ══════════════════════════════════════════════════════════════════════
# ② 提取：任务编排 + 落暂存
# ══════════════════════════════════════════════════════════════════════

print("\n── ② 在线提取：只收 confirmed 的原文 ──")

st, body = req("/api/extract/pending")
check("待提取队列 200", st == 200, st)
check("闸门放行的进了待提取队列",
      [r["raw_id"] for r in body["data"]["items"]] == [SOURCE_ID],
      [r["raw_id"] for r in body["data"]["items"]])
check("带正文长度", body["data"]["items"][0]["content_len"] > 0)

# 先放一条只判到 review 的，用来验证提取准入真的看 gate_verdict 而不是"有行就收"
with sdb.write_tx() as con:
    con.execute(
        "INSERT INTO raw_accidents (raw_id, source_key, source_url, fetch_time, "
        "url_hash, title, content, gate_verdict, gate_stage, status) "
        "VALUES ('e2e_review_only','safehoo','https://example.com/x',"
        "'2026-10-01T08:00:00','e2e_h_x','待定样本','正文'*50,'review','manual','gated')")
st, body = req("/api/extract/pending")
check("只判到 review 的不进队列",
      [r["raw_id"] for r in body["data"]["items"]] == [SOURCE_ID],
      [r["raw_id"] for r in body["data"]["items"]])

st, body = req("/api/extract/tasks", "POST", {"raw_ids": [SOURCE_ID]})
check("建任务 201", st == 201, (st, body))
tid = body["data"]["task_id"]
check("建任务不落暂存行",
      sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 0)

st, body = req(f"/api/extract/tasks/{tid}/start", "POST")
check("启动 200", st == 200, st)
deadline = time.time() + 60
task = None
while time.time() < deadline:
    st, body = req(f"/api/extract/tasks/{tid}")
    task = body["data"]
    if task["status"] in ("done", "failed", "cancelled"):
        break
    time.sleep(0.3)
check("任务 done", task["status"] == "done", task["status"])
check("进度 1/1", (task["progress"]["done"], task["progress"]["total"]) == (1, 1),
      task["progress"])
check("入库 1 条", task["staged"] == 1, task["staged"])


# ══════════════════════════════════════════════════════════════════════
# ②→③ 暂存区的落点是否忠实
# ══════════════════════════════════════════════════════════════════════

print("\n── ②→③ 暂存区：S3/S4 落点是否忠实 ──")

srow = sdb.query_one("SELECT * FROM staging_accidents WHERE raw_id = ?", (SOURCE_ID,))
check("暂存行存在", srow is not None)
STAGE_ID = srow["stage_id"]
check("text_type 落库", srow["text_type"] == "single", srow["text_type"])
check("relevance_verdict 落 LLM 枚举值（非旧来源码）",
      srow["relevance_verdict"] == "confirmed_construction", srow["relevance_verdict"])
check("relevance_stage 标出该值来自哪一层", srow["relevance_stage"] == "llm",
      srow["relevance_stage"])
check("s3_ms 从 pipeline 透传", srow["s3_ms"] == FAKE_S3_MS, srow["s3_ms"])
check("s4_ms 从 pipeline 透传", srow["s4_ms"] == FAKE_S4_MS, srow["s4_ms"])
check("s3_model/s4_model 记下了档位", srow["s3_model"] and srow["s4_model"],
      (srow["s3_model"], srow["s4_model"]))

# S3 的字段是否一比一搬进来（把 None 与"默认值串"分开看）。
# 暂存区那侧叫 `location`，源库那侧叫 `province`——这是全程唯一改名的一列，
# 比对时按源库的名字取，别在这里直接把两个名字对齐了，那等于放过这个洞。
s3_diff = []
for col in ("title", "date", "accident_time", "location", "city", "district",
            "accident_type", "severity", "deaths", "injuries", "trapped_count",
            "project_type", "direct_cause", "summary", "report_type",
            "weather", "collapse_subtype", "contractor_type"):
    src_col = "province" if col == "location" else col
    if SRC[src_col] != srow[col]:
        s3_diff.append((col, SRC[src_col], srow[col]))
check("S3 字段逐列落库（含 NULL 列不被默认值顶替）", not s3_diff, s3_diff)

crows = sdb.query("SELECT * FROM staging_causes WHERE stage_id = ? "
                  "ORDER BY sort_order, id", (STAGE_ID,))
check("4 条编码落库", len(crows) == 4, len(crows))
check("sort_order 是层序值（M=1xx / F=2xx）",
      [c["sort_order"] for c in crows] == [101, 102, 104, 202],
      [c["sort_order"] for c in crows])
check("evidence_quality 现算 = 源库口径",
      [c["evidence_quality"] for c in crows]
      == [c["evidence_quality"] for c in SRC_CAUSES],
      [(c["code"], c["evidence_quality"]) for c in crows])
check("evidence_quality_rule 显式记下来源",
      all(c["evidence_quality_rule"] == c["evidence_quality"] for c in crows))
note("evidence_quality_llm 全为 NULL：S4 自报需改 prompt，属待决项 A（蓝图 02 §9）")

note("已知缺口：s4_primary_cause / s4_cause_chain 两列在 DDL 里存在，但 "
     "staging_executor._INSERT_STAGE 不写它们，恒为 NULL（因果链未落库）")
check("该缺口仍存在（修好后请更新本断言）",
      srow["s4_primary_cause"] is None and srow["s4_cause_chain"] is None,
      (srow["s4_primary_cause"], srow["s4_cause_chain"]))


# ══════════════════════════════════════════════════════════════════════
# ③ 审核与并入
# ══════════════════════════════════════════════════════════════════════

print("\n── ③ 暂存审核：批量通过 → 预演 → 并入 ──")

st, body = req("/api/review/items/" + STAGE_ID)
check("审核详情 200", st == 200, st)
check("详情 31 个可编辑字段齐全", len(body["data"]["fields"]) == 31,
      len(body["data"]["fields"]))
check("致因树按 sort_order 排", [c["code"] for c in body["data"]["causes"]]
      == ["M1", "M2", "M4", "F2"], [c["code"] for c in body["data"]["causes"]])

st, body = req("/api/review/batch-approve", "POST", {"stage_ids": [STAGE_ID]})
check("批量通过放行 1 条", body["data"]["approved"] == 1, body["data"])
check("审核状态 approved",
      sdb.scalar("SELECT review_status FROM staging_accidents WHERE stage_id = ?",
                 (STAGE_ID,)) == "approved")

st, body = req("/api/review/promote", "POST",
               {"stage_ids": [STAGE_ID], "dry_run": True})
check("预演 200", st == 200, (st, body))
check("预演计划：新增 1 条、4 行编码",
      body["data"]["plan"]["accidents_insert"] == 1
      and body["data"]["plan"]["causes_total"] == 4, body["data"]["plan"])
check("预演不写最终库", final_rows("SELECT id FROM accidents") == [])
token = body["data"]["confirm_token"]

st, body = req("/api/review/promote", "POST",
               {"stage_ids": [STAGE_ID], "dry_run": False, "confirm_token": token})
check("并入成功", st == 200, (st, body))
check("写入 1 条事故 4 行编码",
      body["data"]["promoted"] == 1 and body["data"]["causes_written"] == 4,
      body["data"])
check("备份文件真生成", Path(body["data"]["backup"]).exists())


# ══════════════════════════════════════════════════════════════════════
# 往返比对：本脚本的核心
# ══════════════════════════════════════════════════════════════════════

print("\n── 核心：并入结果与主库原文逐列比对 ──")

NEW = final_rows("SELECT * FROM accidents WHERE id = ?", (SOURCE_ID,))
check("最终库里能按同一个 id 查到", len(NEW) == 1, len(NEW))
NEW = NEW[0]

# 排除列：它们不是"映射"，是"时刻"。
#   crawl_date  采集那一刻的墙上时间（本次跑与 2026-07 那次采集必然不同）
#   created_at  并入那一刻，本就该是新的
#   step1/2_duration_ms  真实推理耗时；FakePipe 没有真实推理，给的是测试值，
#                        这两列的往返无意义，另立断言证明值透传了（下面）
EXCLUDE = {"crawl_date", "created_at", "step1_duration_ms", "step2_duration_ms"}

# 已知口径差异：两侧**设计上**就不同的列。逐条给依据，不是"看着不一样就算了"。
#
#   classification_verdict —— 主库实测分布：batch_direct 1683 / pdf_direct 895
#     （旧批量脚本自造的**来源码**，占 98.3%）、llm_override_construction 20 /
#     rule_high_construction 14 / confirmed_construction 11（45 条才是
#     `ClassificationVerdict` 枚举值）。在线管道闸门走的是枚举，所以产出必然是
#     `confirmed_construction`。**这是两套词表混在同一列里**，属于真实的数据
#     口径分叉，值得单独处置，但不是本次映射的错。
#   search_enriched_text / search_performed —— 主库 1636 条做过搜索增强（=1）、
#     987 条没做。在线管道没有搜索增强这一段，恒写 NULL / 0。
KNOWN_DIFF = {
    "classification_verdict": f"主库={SRC['classification_verdict']}（旧来源码，"
                              f"全库 2578/2623 条是这一套）→ 在线管道产出 "
                              f"confirmed_construction（枚举值）",
    "search_enriched_text": "主库有搜索增强文本 → 在线管道不做搜索增强，写 NULL",
    "search_performed": f"主库={SRC['search_performed']} → 在线管道恒写 0",
}

same, diff = [], []
for col in ACC_COLS:
    if col in EXCLUDE:
        continue
    a, b = SRC[col], NEW[col]
    (same if a == b else diff).append((col, a, b))

unexpected = [d for d in diff if d[0] not in KNOWN_DIFF]
check(f"42 列逐列相同（{len(same)} 列）", not unexpected,
      [(c, a, b) for c, a, b in unexpected])

for col, a, b in diff:
    if col in KNOWN_DIFF:
        note(f"已知口径差异 {col}：{KNOWN_DIFF[col]}")
check("差异列全是已声明的口径差异",
      {c for c, _, _ in diff} <= set(KNOWN_DIFF),
      sorted({c for c, _, _ in diff} - set(KNOWN_DIFF)))

# 改名映射逐条点名：这几处抄错不会报错，只会把值写进隔壁列
check("location → province", NEW["province"] == SRC["province"], NEW["province"])
check("location 没有串到 city", NEW["city"] == SRC["city"], NEW["city"])
check("relevance_verdict → classification_verdict（值按已知差异）",
      NEW["classification_verdict"] == "confirmed_construction")
check("relevance_confidence → classification_confidence",
      NEW["classification_confidence"] == SRC["classification_confidence"],
      NEW["classification_confidence"])
check("s3_ms → step1_duration_ms", NEW["step1_duration_ms"] == FAKE_S3_MS,
      NEW["step1_duration_ms"])
check("s4_ms → step2_duration_ms", NEW["step2_duration_ms"] == FAKE_S4_MS,
      NEW["step2_duration_ms"])
check("raw_text 从 raw_accidents.content 带过来",
      NEW["raw_text"] == SRC["raw_text"])
check("pipeline_version 与主库一致",
      NEW["pipeline_version"] == SRC["pipeline_version"] == "caics_v1.0",
      NEW["pipeline_version"])
check("source_name 从 raw_accidents.source_key 带过来",
      NEW["source_name"] == SRC["source_name"], NEW["source_name"])

# 子表：6 列逐条比对
NC = final_rows("SELECT code, name, evidence, inference, evidence_quality, "
                "sort_order FROM caics_causes WHERE accident_id = ? "
                "ORDER BY sort_order, id", (SOURCE_ID,))
check("编码条数一致", len(NC) == len(SRC_CAUSES), (len(NC), len(SRC_CAUSES)))
c_diff = [(i, a, b) for i, (a, b) in
          enumerate(zip(SRC_CAUSES, NC))
          if any(a[k] != b[k] for k in
                 ("code", "name", "evidence", "inference",
                  "evidence_quality", "sort_order"))]
check("6 列逐条相同", not c_diff, c_diff)


# ══════════════════════════════════════════════════════════════════════
# ④ 展示：并入的数据立刻可见、可筛
# ══════════════════════════════════════════════════════════════════════

print("\n── ④ 展示：新并入的数据立刻出现在 web 上 ──")

st, body = req("/api/overview")
check("概览 200", st == 200, st)
check("概览事故数 = 1", body["totals"]["accidents"] == 1, body["totals"])
check("概览三级证据齐全（0/0/4）",
      {e["key"]: e["count"] for e in body["evidence_quality"]}
      == {"direct_quote": 3, "indirect_infer": 1, "weak_infer": 0},
      body["evidence_quality"])
# 目录是**数据驱动**的：只列库里真出现过的码。冷启动的空库里它只有 4 条，
# 这是设计，不是缺了 15 类（理论全集在 /api/extract/taxonomy 里，恒 19）。
check("概览 CAICS 目录列出样本用到的 4 个码",
      {c["code"] for c in body["caics_catalog"]} == {"M1", "M2", "M4", "F2"},
      [c["code"] for c in body["caics_catalog"]])

st, body = req(f"/api/accidents/{SOURCE_ID}")
check("事故详情 200", st == 200, st)
check("详情标题与主库一致", body["data"]["fields"]["title"] == SRC["title"])
# causes 是分好组的 dict（同一 code 的多条标注合并进 citations），条数在 items 里
check("详情带 4 条致因编码", len(body["data"]["causes"]["items"]) == 4,
      len(body["data"]["causes"]["items"]))
check("每条编码带证据等级中文名",
      all(c["quality_label"] for c in body["data"]["causes"]["items"]),
      [c["quality_label"] for c in body["data"]["causes"]["items"]])
check("详情带原文", bool(body["data"]["raw_text"]))

st, html = page(f"/accident/{SOURCE_ID}")
check("详情页返回 200", st == 200, st)
# 详情页服务端只出壳，内容由 accident.js 拉 API 填——所以断言挂载点与
# 传进去的 id，而不是断言 HTML 里有标题（那永远不会有）
check("详情页把事故 id 交给前端（data-accident-id）",
      f'data-accident-id="{SOURCE_ID}"' in html and 'id="detail"' in html)
check("详情页引用 accident.js", "accident.js" in html)

st, body = req("/api/filters")
check("筛选器 200", st == 200, st)
prov = {p["value"] for p in body["province"]}
# 「海地」不该以原值混进标准省份下拉——筛选器按蓝图给它单独的「境外港澳台」组。
# 这一条同时验证了 province.py 的归一确实在生效，而不是原样 DISTINCT。
check("境外省份归组，不像标准省份那样平铺",
      "__overseas__" in prov and SRC["province"] not in prov, sorted(prov))

st, body = req(f"/api/accidents?province={SRC['province']}")
check("按省份筛选能命中", body["meta"]["total"] == 1, body["meta"])

st, body = req("/api/causes/distribution")
check("致因分布 200", st == 200, st)
layers = {x["key"]: x["count"] for x in body["data"]["by_layer"]}
check("分布里 M 层 3 条、F 层 1 条",
      layers.get("M") == 3 and layers.get("F") == 1, layers)


# ══════════════════════════════════════════════════════════════════════
# 链路断裂检查
# ══════════════════════════════════════════════════════════════════════

print("\n── 链路完整性：漏斗两处断口的实测状态 ──")

st, body = req("/api/collect/stats")
funnel = {f["stage"]: f["count"] for f in body["data"]["funnel"]}
raw_now = sdb.query_one("SELECT status, promoted_at, stage_id FROM raw_accidents "
                        "WHERE raw_id = ?", (SOURCE_ID,))

# 漏斗「已送提取」查的是 `status = 'promoted'`。契约上它必须与该 SQL 自洽——
# 这一条无论断口修没修都该成立，所以是硬断言。
check("漏斗「已送提取」与该 SQL 自洽",
      funnel["promoted"] == sdb.scalar(
          "SELECT COUNT(*) FROM raw_accidents WHERE status = 'promoted'"),
      funnel["promoted"])

if raw_now["status"] != "promoted":
    note(f"已知缺口：并入走完了，raw_accidents.status 仍是 {raw_now['status']!r}、"
         f"promoted_at 为 {raw_now['promoted_at']!r}——promote.py 与 "
         f"staging_executor 都不写这三列（status/promoted_at/stage_id），"
         f"漏斗「已送提取」因此恒为 0")
else:
    note("断口已修复：并入后 raw_accidents.status 变成了 promoted")

note(f"另一处（有意为之，非缺陷）：漏斗「自动闸门通过」查 gate_stage IN "
     f"('rule','llm')，全项目无人写该值，恒为 0。collect.py:457 注明这是"
     f"刻意的——清洗段还没接进在线管道（cleaners/ 四器），不编一个恒等于"
     f"上一段的数字来凑满五格")


server.shutdown()
print(f"\n{'=' * 56}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
print(f"现场目录：{TMP}")
if FAIL:
    print("失败清单：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
