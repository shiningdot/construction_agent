"""M4（阶段③审核）第二层验证：真 HTTP + 真 SQLite。

第一层 pytest 用 Flask test client，走的是进程内 WSGI 调用，绕过了 HTTP 栈、
真实 socket、真实文件锁。这一层把服务真起在 5095 端口上，用 urllib 发请求，
库也是两个真实的文件库——并入会真的备份、真的写 WAL。

审核这一环的失败模式大多只在"真"的时候才现形：SQLite 的写锁竞争、
`mode=ro` 与写连接并存、备份文件路径、并发事务。所以第二层在这里不是走过场。
"""

import hashlib
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

# 每次跑都另起一个临时目录：这几个脚本会真建库、真并入、真写备份，
# 复用同一个目录会让上一轮的库和备份混进这一轮，出了错也说不清是哪次留下的。
# 目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_"))
STAGING = TMP / "e2e_review_staging.db"
FINAL = TMP / "e2e_review_final.db"

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

from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.factory import create_app  # noqa: E402

PORT = 5095
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


# ── 建两个真库 ───────────────────────────────────────────

sdb.init_db()
con = sqlite3.connect(FINAL)
con.executescript(create_db_v2.SCHEMA)
con.commit()
con.close()

with sdb.write_tx() as c:
    c.execute("INSERT OR IGNORE INTO collect_sources (source_key, display_name, "
              "source_group, track) VALUES ('safehoo','安全管理网','national','network')")


def seed(stage_id, *, title=None, causes=(("H1", "违章操作"),),
         review_status="pending", **fields):
    row = {
        "title": title if title is not None else f"某工地坠落事故 {stage_id}",
        "date": "2024-05-06", "location": "江苏省", "city": "南京市",
        "accident_type": "高处坠落", "severity": "一般事故",
        "deaths": 2, "injuries": 0, "project_type": "房屋建筑",
        "report_type": "事故调查报告", "summary": "工人未系安全带坠落。",
        "text_type": "single", "relevance_verdict": "construction",
        "relevance_confidence": "high",
    }
    row.update(fields)
    raw_id = "raw" + stage_id
    with sdb.write_tx() as con2:
        con2.execute(
            "INSERT INTO raw_accidents (raw_id, source_key, source_url, fetch_time, "
            "url_hash, title, content) VALUES (?,?,?,?,?,?,?)",
            (raw_id, "safehoo", f"https://example.com/{stage_id}",
             "2026-10-01T10:00:00", hashlib.md5(raw_id.encode()).hexdigest(),
             row["title"], "原文正文。" * 60))
        cols = ["stage_id", "raw_id", "created_at", "review_status",
                "split_index"] + list(row.keys())
        vals = [stage_id, raw_id, "2026-10-01T10:00:00", review_status, 0] \
            + list(row.values())
        con2.execute(f"INSERT INTO staging_accidents ({','.join(cols)}) "
                     f"VALUES ({','.join('?' * len(cols))})", vals)
        for i, (code, name) in enumerate(causes or ()):
            con2.execute(
                "INSERT INTO staging_causes (stage_id, code, name, evidence, "
                "inference, evidence_quality, sort_order) VALUES (?,?,?,?,?,?,?)",
                (stage_id, code, name, "原文提到" + name, "推断：" + name,
                 "direct_quote", i + 1))
    return stage_id


seed("s1")
seed("s2")
seed("s3")
seed("bad_title", title="")
seed("bad_cause", causes=())
seed("done", review_status="approved")

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
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}


def text(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, r.read().decode("utf-8")


def final_hash():
    return hashlib.sha256(FINAL.read_bytes()).hexdigest()


def final_count(table):
    con = sqlite3.connect(f"file:{FINAL}?mode=ro", uri=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


print("\n── ① 页面与静态资源 ──")
st, html = text("/review")
check("审核页返回 200", st == 200, st)
check("页面含漏斗与队列表头", "审核漏斗" in html and "暂存队列" in html)
check("页面引用 review.js", "review.js" in html)
for asset in ("/static/review.js", "/static/app.css", "/static/dom.js"):
    st, _ = text(asset)
    check(f"{asset} 返回 200", st == 200, st)

print("\n── ② 队列（含「全部」这条会踩缺列 bug 的路径）──")
st, q = req("/api/review/queue?review_status=all")
check("不带筛选的队列 200", st == 200, st)
check("返回 6 条", q["meta"]["total"] == 6, q["meta"]["total"])
missing = [k for k in ("date", "title", "severity", "location", "city",
                       "text_type", "cause_count", "review_status")
           if q["data"] and k not in q["data"][0]]
check("行数据字段齐全（date 等不漏）", not missing, missing)
check("默认只看 pending", req("/api/review/queue")[1]["meta"]["total"] == 5)

counts = q["counts"]
check("counts.total 与 SQL 一致",
      counts["total"] == sdb.scalar("SELECT COUNT(*) FROM staging_accidents"))
check("counts.pending 与 SQL 一致", counts["pending"] == sdb.scalar(
    "SELECT COUNT(*) FROM staging_accidents WHERE review_status='pending'"))
check("counts.with_warnings = 2", counts["with_warnings"] == 2, counts)
check("counts.clean = total - with_warnings",
      counts["clean"] == counts["total"] - counts["with_warnings"])

by = {w["code"]: w["count"] for w in q["by_warning"]}
check("告警分布：empty_title 1 条", by.get("empty_title") == 1, by)
check("告警分布：no_causes 1 条", by.get("no_causes") == 1, by)

st, z = req("/api/review/queue?review_status=all&warnings=none")
check("零告警筛选只留干净的", {r["stage_id"] for r in z["data"]}
      == {"s1", "s2", "s3", "done"}, [r["stage_id"] for r in z["data"]])
st, w = req("/api/review/queue?review_status=all&warnings=no_causes")
check("按告警码筛选", [r["stage_id"] for r in w["data"]] == ["bad_cause"])

print("\n── ③ 详情 ──")
st, d = req("/api/review/items/s1")
check("详情 200", st == 200, st)
d = d["data"]
check("31 个可编辑字段全部有值键", len(d["fields"]) == 31, len(d["fields"]))
check("字段真取到值（不是一片 None）",
      d["fields"]["city"] == "南京市" and d["fields"]["deaths"] == 2)
check("原文一并返回", bool(d["raw"]["content"]))
check("致因树带证据等级中文名",
      d["causes"][0]["evidence_quality_label"] == "直引")
check("未知 id 返回 404", req("/api/review/items/zzz")[0] == 404)

print("\n── ④ 单条审核 ──")
st, r = req("/api/review/items/s1", "POST", {"action": "reject", "note": ""})
check("退回无原因被拒 400", st == 400, st)
st, r = req("/api/review/items/s1", "POST",
            {"action": "reject", "note": "事发地不在施工区域"})
check("退回带原因通过", st == 200 and r["data"]["review_status"] == "rejected")
check("退回落库", sdb.scalar("SELECT review_status FROM staging_accidents "
                             "WHERE stage_id='s1'") == "rejected")

st, r = req("/api/review/items/s2", "POST", {
    "action": "edit", "note": "订正",
    "edits": {"deaths": "3", "city": "苏州市",
              "causes": [{"code": "M2", "name": "制度缺失",
                          "evidence": "e", "inference": "i",
                          "evidence_quality": "weak_infer"}]}})
check("编辑保存通过", st == 200 and r["data"]["review_status"] == "edited")
row = sdb.query_one("SELECT deaths, city FROM staging_accidents WHERE stage_id='s2'")
check("字段真写进库", row["deaths"] == 3 and row["city"] == "苏州市", row)
cs = sdb.query("SELECT code, sort_order FROM staging_causes WHERE stage_id='s2'")
check("致因树整体替换并重排序号",
      [(c["code"], c["sort_order"]) for c in cs] == [("M2", 1)], cs)

st, r = req("/api/review/items/s3", "POST", {"action": "edit",
                                             "edits": {"stage_id": "hacked"}})
check("不可编辑的列改不动", st == 400
      and sdb.scalar("SELECT stage_id FROM staging_accidents "
                     "WHERE stage_id='s3'") == "s3")

print("\n── ⑤ 批量通过 ──")
st, r = req("/api/review/batch-approve", "POST",
            {"stage_ids": ["s2", "s3", "bad_title", "bad_cause"]})
d = r["data"]
check("只放行零告警的", d["approved"] == 2 and sorted(d["stage_ids"]) == ["s2", "s3"],
      d["stage_ids"])
check("有告警的被 skip 且带原因",
      {s["stage_id"] for s in d["skipped"]} == {"bad_title", "bad_cause"}
      and all(s["reason"] for s in d["skipped"]))
check("被 skip 的仍是 pending",
      sdb.scalar("SELECT review_status FROM staging_accidents "
                 "WHERE stage_id='bad_title'") == "pending")

print("\n── ⑥ 并入：预演不写 ──")
before = final_hash()
st, r = req("/api/review/promote", "POST",
            {"stage_ids": ["s2", "s3"], "dry_run": True})
check("预演 200", st == 200, st)
d = r["data"]
check("预演给出写入计划",
      d["mode"] == "dry_run" and d["plan"]["accidents_insert"] == 2
      and d["plan"]["causes_total"] == 2, d["plan"])
check("预演返回令牌", bool(d["confirm_token"]))
check("预演后最终库字节日志未变", final_hash() == before)
check("预演后 accidents 仍为空", final_count("accidents") == 0)
token = d["confirm_token"]

print("\n── ⑦ 并入：令牌闸门 ──")
st, r = req("/api/review/promote", "POST", {"stage_ids": ["s2", "s3"]})
check("不带令牌 → 409", st == 409, st)
check("拒绝信息提到令牌", "令牌" in r.get("message", ""), r.get("message"))
check("拒绝后仍未写入", final_count("accidents") == 0)

st, r = req("/api/review/promote", "POST",
            {"stage_ids": ["s2", "s3", "s1"], "dry_run": False,
             "confirm_token": token})
check("选择集变了 → 409", st == 409, st)
check("拒绝信息提到选择集", "选择集" in r.get("message", ""), r.get("message"))

print("\n── ⑧ 并入：真执行 ──")
st, r = req("/api/review/promote", "POST",
            {"stage_ids": ["s2", "s3"], "dry_run": False, "confirm_token": token})
check("并入成功", st == 200, (st, r))
d = r["data"]
check("写入 2 条事故 2 行编码",
      d["promoted"] == 2 and d["causes_written"] == 2, d)
check("备份文件真生成", Path(d["backup"]).exists()
      and Path(d["backup"]).stat().st_size > 0)
check("最终库 accidents = 2", final_count("accidents") == 2)
check("最终库 caics_causes = 2", final_count("caics_causes") == 2)

con = sqlite3.connect(f"file:{FINAL}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
acc = con.execute("SELECT * FROM accidents WHERE id='raws2'").fetchone()
check("改名映射正确：location → province", acc["province"] == "江苏省")
check("改名映射正确：relevance_verdict → classification_verdict",
      acc["classification_verdict"] == "construction")
check("pipeline_version 写入", acc["pipeline_version"] == "caics_v1.0")
check("raw_text 从原始表带过来", bool(acc["raw_text"]))
con.close()

st, h = req("/api/review/history")
check("并入历史留痕", len(h["data"]) == 1 and h["data"][0]["promoted"] == 2)

st, s = req("/api/review/stats")
check("漏斗「已并入」= 2", s["data"]["funnel"][3]["count"] == 2, s["data"]["funnel"])

print("\n── ⑨ 令牌一次性 ──")
st, r = req("/api/review/promote", "POST",
            {"stage_ids": ["s2", "s3"], "dry_run": False, "confirm_token": token})
check("旧令牌再用被拒 409", st == 409, st)

print("\n── ⑩ 回滚 ──")
st, r = req("/api/review/rollback", "POST", {})
check("回滚 200", st == 200, (st, r))
d = r["data"]
check("回滚删除事故与编码",
      d["removed_accidents"] == 2 and d["removed_causes"] == 2, d)
check("回滚后 accidents 归零", final_count("accidents") == 0)
check("回滚后 caics_causes 归零", final_count("caics_causes") == 0)
st, s = req("/api/review/stats")
check("漏斗「已并入」归零", s["data"]["funnel"][3]["count"] == 0)
st, r = req("/api/review/rollback", "POST", {})
check("无可回滚时报 409", st == 409, st)

print("\n── ⑪ 边角 ──")
check("空 stage_ids 400", req("/api/review/batch-approve", "POST",
                              {"stage_ids": []})[0] == 400)
check("未知 action 400", req("/api/review/items/s3", "POST",
                             {"action": "delete"})[0] == 400)
check("详情 404 不崩", req("/api/review/items/nope")[0] == 404)
check("页面里的未知路径 404", req("/api/review/nope")[0] == 404)

server.shutdown()
print(f"\n{'='*52}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败清单：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
