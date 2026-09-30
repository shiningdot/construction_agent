"""M2 在线采集的端到端验证：起真 HTTP 服务，走完一条采集任务的全过程。

不碰外网——`registry.build` 被换成返回假采集器的版本，但**两条产出通路、
落库、判定、统计全是真的**，工作库也是真的 sqlite 文件。

顺序上刻意先验「工作库还没建」的那一段：用户第一次打开 /collect 时正好
是这个状态，它不能 500，而且源清单必须照样出 24 条。
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 每次跑另起临时目录：这几个脚本会真建库、真并入、真写备份，复用同一目录
# 会让上一轮的库和备份混进这一轮。目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_"))
DB = TMP / "e2e_staging.db"
os.environ["CAIEMS_STAGING_DB"] = str(DB)
if DB.exists():
    DB.unlink()

from construction_agent.collector import registry                      # noqa: E402
from construction_agent.collector.base import BaseCollector, RawAccidentText  # noqa: E402
from construction_agent.web import staging_db as sdb                   # noqa: E402
from construction_agent.web.factory import create_app                  # noqa: E402

PORT = 5091
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + ("  " + str(extra) if extra else ""))


# ── 假采集器 ─────────────────────────────────────────────

class FakeStore:
    def __init__(self):
        self.sink = None
        self.n = 0

    def attach(self, sink):
        self.sink = sink
        return self

    def add(self, record):
        self.n += 1
        if self.sink is not None:
            self.sink(record)
        return record.get("id")


TITLES = [
    ("南京市某工地脚手架坍塌致3人死亡", "2026-03-11", 3, 2),
    ("苏州市某厂房高处坠落事故", "2026-03-12", 1, 0),
    ("徐州市某项目塔吊倾覆事故通报", "2026-02-28", 2, 5),
    ("无锡市某工地触电事故调查结果", "2025-12-04", 1, 1),
    ("常州市某项目模板支撑体系坍塌", "2026-01-19", 0, 4),
]


class StoreSource(BaseCollector):
    """通路 A：写 store。safehoo 那类源的真实形态。"""

    def __init__(self, data_dir=None, key="safehoo"):
        self._key = key
        self.data_dir = data_dir
        self.raw_store = FakeStore()

    def source_name(self):
        return self._key

    def collect(self):
        for i, (title, day, dead, hurt) in enumerate(TITLES):
            url = f"https://www.safehoo.com/NewsSpecial/Build/{self._key}{i}.shtml"
            self.raw_store.add({
                "id": f"rec{self._key}{i}",
                "title": title,
                "content": f"{title}。经调查，事故起因为现场管理缺失、防护措施不到位。" * 4,
                "source_url": url,
                "source_name": self._key,
                "crawl_date": "2026-09-30T02:00:00+00:00",
                "publish_date": day,
                "status": "new",
                "deaths": dead,
                "injuries": hurt,
                "pdf_urls": [url.replace(".shtml", ".pdf")],
            })
        return {"collected": len(TITLES)}


class ReturnSource(BaseCollector):
    """通路 B：返回值。mohurd 那类源的真实形态。"""

    def __init__(self, data_dir=None):
        self.data_dir = data_dir

    def source_name(self):
        return "mohurd"

    def collect(self):
        return [
            RawAccidentText(
                id=f"mohurd-{i}",
                source="mohurd",
                title=f"住建部通报：某地建筑施工事故 {i}",
                raw_text="住房和城乡建设部通报，该起事故造成人员伤亡。" * 5,
                raw_html=f"<html><body><p>通报第 {i} 号</p></body></html>",
                source_url=f"https://www.mohurd.gov.cn/tongbao/{i}.html",
                source_name="住房和城乡建设部",
                fetch_time="2026-09-30T02:05:00+00:00",
                extracted_date=date(2026, 3, 1),
                extracted_city="北京市",
                extracted_province="北京市",
                extracted_deaths=1,
                confidence_overall=0.82,
            )
            for i in range(3)
        ]


def fake_build(key, *, data_dir=None):
    spec = registry.get(key)
    if key == "safehoo":
        return StoreSource(data_dir=data_dir), spec
    if key == "mohurd":
        return ReturnSource(data_dir=data_dir), spec
    raise RuntimeError(f"端到端脚本没为 {key} 准备假采集器")


registry.build = fake_build

# ── 起服务（此时工作库还不存在） ──────────────────────────

app = create_app({"TESTING": False})
from werkzeug.serving import make_server                          # noqa: E402

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
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(raw) if "json" in ct
                                 else raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[")
                        else raw.decode("utf-8", "replace"))


print("\n=== ① 页面与静态资源 ===")
st, html = req("/collect")
check("GET /collect 返回 200", st == 200, st)
check("页面引用了 collect.js", "collect.js" in html)
check("页面引用了 dom.js", "dom.js" in html)
check("页面有源清单表体", 'id="srcBody"' in html)
check("页面有批量裁决条", 'id="bulkBar"' in html)
st, css = req("/static/app.css")
check("GET /static/app.css 200", st == 200, st)
check("app.css 含采集页样式 .bulkbar", ".bulkbar" in css)
check("app.css 含 .drawer 且处理了 [hidden]",
      ".drawer {" in css and ".drawer[hidden]" in css)
st, js = req("/static/collect.js")
check("GET /static/collect.js 200", st == 200, st)
check("collect.js 用了正确的接口前缀", "/api/collect/" in js)
check("collect.js 用 setTimeout 递归轮询（不用 setInterval 堆请求）",
      "setTimeout(" in js and "setInterval(" not in js)

print("\n=== ② 工作库还没建：源清单照样出 24 条 ===")
st, body = req("/api/collect/sources")
check("sources 200", st == 200, st)
check("available=false（如实反映库没建）", body["available"] is False,
      body["available"])
check("仍返回 24 个源（以代码登记表为准）", len(body["data"]) == 24,
      len(body["data"]))
check("附建库命令", "create_staging_db" in (body.get("hint") or ""),
      body.get("hint"))
check("每源都有中文分组标签",
      all(s.get("group_label") for s in body["data"]))
check("库没建时 never_run 全为 True",
      all(s["never_run"] for s in body["data"]))

print("\n=== ③ 工作库还没建：其它端点不 500 ===")
for path in ("/api/collect/tasks", "/api/collect/raw", "/api/collect/stats"):
    st, body = req(path)
    check(f"{path} 200 且 available=false",
          st == 200 and body["available"] is False, f"{st} {body.get('available')}")
st, body = req("/api/collect/tasks", "POST", {"source_keys": ["safehoo"]})
check("库没建时起任务是 503 而不是 500", st == 503, st)
check("503 里给出建库提示", "create_staging_db" in body.get("message", ""))

# ── 建库 ─────────────────────────────────────────────────
sdb.init_db()
print("      （已建工作库，继续）")

print("\n=== ④ 建库后源清单 ===")
st, body = req("/api/collect/sources")
check("available=true", body["available"] is True)
groups = {}
for s in body["data"]:
    groups[s["source_group"]] = groups.get(s["source_group"], 0) + 1
check("分组 18/2/3/1", groups == {"province": 18, "national": 2,
                                  "media": 3, "open_data": 1}, groups)

print("\n=== ⑤ 起一个采集任务（两条产出通路各一个源） ===")
st, body = req("/api/collect/tasks", "POST",
               {"source_keys": ["safehoo", "mohurd"]})
check("POST /tasks 返回 202", st == 202, st)
task_id = body["data"]["task_id"]
print("      task_id =", task_id)

deadline = time.time() + 30
task = None
while time.time() < deadline:
    st, body = req(f"/api/collect/tasks/{task_id}")
    task = body["data"]
    if task["status"] in ("done", "failed", "cancelled"):
        break
    time.sleep(0.4)

check("任务完成", task["status"] == "done", task["status"])
check("进度 2/2", (task["progress_done"], task["progress_total"]) == (2, 2),
      f'{task["progress_done"]}/{task["progress_total"]}')
check("totals 含 filtered", "filtered" in (task["totals"] or {}), task["totals"])
check("入库 8 条（通路 A 5 + 通路 B 3）",
      task["totals"].get("inserted") == 8, task["totals"])
check("两源状态都是 done",
      all(v["state"] == "done" for v in task["progress"].values()),
      {k: v["state"] for k, v in task["progress"].items()})
check("cursor 如实为空（适配器不报页游标）", task["cursor"] == {})
check("任务列表能查到它",
      any(t["task_id"] == task_id for t in req("/api/collect/tasks")[1]["data"]))

print("\n=== ⑥ 原始数据（两条通路都落了库） ===")
st, body = req("/api/collect/raw?size=50")
check("raw 列表 200", st == 200, st)
check("命中 8 条", body["meta"]["total"] == 8, body["meta"]["total"])
row = body["data"][0]
check("列表行带 has_assets 标记", "has_assets" in row)
check("列表行只给预览不给全文", "content" not in row and "content_preview" in row)

st, body = req("/api/collect/raw?source_key=mohurd")
check("按源筛选出 3 条", body["meta"]["total"] == 3, body["meta"]["total"])
st, body = req("/api/collect/raw?source_key=safehoo")
check("按源筛选出 5 条", body["meta"]["total"] == 5, body["meta"]["total"])
st, body = req("/api/collect/raw?q=塔吊")
check("关键词命中 1 条", body["meta"]["total"] == 1, body["meta"]["total"])
st, body = req("/api/collect/raw?gate_verdict=__none__")
check("未判定筛出全部 8 条", body["meta"]["total"] == 8, body["meta"]["total"])
st, body = req("/api/collect/raw?status=new")
check("按状态筛选 8 条", body["meta"]["total"] == 8, body["meta"]["total"])

print("\n=== ⑦ 单条详情 ===")
st, body = req("/api/collect/raw?source_key=mohurd&size=1")
rid = body["data"][0]["raw_id"]
st, body = req(f"/api/collect/raw/{rid}")
d = body["data"]
check("详情 200", st == 200, st)
check("详情带全文", bool(d.get("content")))
check("详情带原始 HTML（通路 B）", bool(d.get("raw_html")))
check("详情带 assets 数组", isinstance(d.get("assets"), list))
check("详情带 source 对象", bool(d.get("source", {}).get("name")), d.get("source"))
check("通路 B 的规则预提取字段落到了 ext_* 列",
      d.get("ext_city") == "北京市" and d.get("ext_deaths") == 1,
      (d.get("ext_city"), d.get("ext_deaths")))
check("通路 B 的发布日期落了库", d.get("publish_date") == "2026-03-01",
      d.get("publish_date"))

st, body = req("/api/collect/raw?source_key=safehoo&size=1")
rid2 = body["data"][0]["raw_id"]
st, body = req(f"/api/collect/raw/{rid2}")
d2 = body["data"]
check("通路 A 的 extra 解析成对象", isinstance(d2.get("extra"), dict),
      type(d2.get("extra")).__name__)
check("extra 保住了 pdf_urls", bool((d2.get("extra") or {}).get("pdf_urls")),
      (d2.get("extra") or {}).get("pdf_urls"))
check("PDF 已登记成待处理资产（阶段②要去下载）",
      len(d2.get("assets") or []) >= 1,
      [(a["asset_type"], a["ocr_status"]) for a in (d2.get("assets") or [])])
check("附件类型标成 pdf",
      all(a["asset_type"] == "pdf" for a in (d2.get("assets") or [])),
      [a["asset_type"] for a in (d2.get("assets") or [])])
check("通路 A 的 raw_html 为空（拿不到就如实留空）", not d2.get("raw_html"))
check("通路 A 的发布日期落了库",
      d2.get("publish_date") in {t[1] for t in TITLES}, d2.get("publish_date"))

print("\n=== ⑧ 人工裁决 ===")
st, body = req("/api/collect/raw/gate", "POST",
               {"raw_ids": [rid, rid2], "verdict": "confirmed",
                "note": "确认为建筑施工事故"})
check("gate 200", st == 200, st)
check("updated=2", body["data"]["updated"] == 2, body["data"])
check("missing=0", body["data"]["missing"] == 0)

st, body = req("/api/collect/raw?gate_verdict=confirmed")
check("确认的能筛出来", body["meta"]["total"] == 2, body["meta"]["total"])
row = body["data"][0]
check("判定来源记为 manual", row["gate_stage"] == "manual", row["gate_stage"])
check("状态改为 gated", row["status"] == "gated", row["status"])
check("理由已落库", row["gate_reason"] == "确认为建筑施工事故", row["gate_reason"])

st, body = req("/api/collect/raw/gate", "POST",
               {"raw_ids": [rid], "verdict": "review"})
check("退回待判 200", st == 200, st)
st, body = req(f"/api/collect/raw/{rid}")
check("退回待判会清掉判定", body["data"]["gate_verdict"] is None,
      body["data"]["gate_verdict"])

st, body = req("/api/collect/raw/gate", "POST",
               {"raw_ids": [rid], "verdict": "bogus"})
check("非法 verdict → 400", st == 400, st)
st, body = req("/api/collect/raw/gate", "POST", {"raw_ids": [], "verdict": "confirmed"})
check("空 raw_ids → 400", st == 400, st)

print("\n=== ⑨ 漏斗 ===")
st, body = req("/api/collect/stats")
d = body["data"]
check("stats 200", st == 200, st)
check("漏斗四段", len(d["funnel"]) == 4, [f["stage"] for f in d["funnel"]])
funnel = {f["stage"]: f["count"] for f in d["funnel"]}
check("原始抓取 8", funnel["raw"] == 8, funnel)
check("自动闸门通过 0（这批全是人工判的）", funnel["kept"] == 0, funnel)
check("人工确认 1（退回待判那条不计）", funnel["manual"] == 1, funnel)
check("显式声明缺「清洗保留」段，不编造数字",
      any(m["stage"] == "cleaned" for m in d["stages_missing"]),
      d["stages_missing"])
check("by_verdict 未判定 7 条", d["by_verdict"]["__none__"] == 7, d["by_verdict"])

print("\n=== ⑩ 跨任务去重与累加 ===")
st, body = req("/api/collect/tasks", "POST", {"source_keys": ["safehoo"]})
tid2 = body["data"]["task_id"]
deadline = time.time() + 30
while time.time() < deadline:
    st, body = req(f"/api/collect/tasks/{tid2}")
    if body["data"]["status"] in ("done", "failed"):
        break
    time.sleep(0.4)
check("第二轮 5 条全被 UNIQUE 拦下",
      body["data"]["totals"].get("dup") == 5, body["data"]["totals"])
check("第二轮入库 0 条",
      body["data"]["totals"].get("inserted") == 0, body["data"]["totals"])
st, body = req("/api/collect/raw")
check("总数仍是 8（去重生效）", body["meta"]["total"] == 8, body["meta"]["total"])
st, body = req(f"/api/collect/raw/{rid2}")
check("重跑没有把附件再登记一遍",
      len(body["data"].get("assets") or []) == 1,
      len(body["data"].get("assets") or []))

st, body = req("/api/collect/sources")
sh = [s for s in body["data"] if s["source_key"] == "safehoo"][0]
mh = [s for s in body["data"] if s["source_key"] == "mohurd"][0]
check("safehoo 累计入库 5（累加而非覆盖）", sh["total_raw"] == 5, sh["total_raw"])
check("safehoo 上次运行成功", sh["last_run_ok"] is True)
check("safehoo 有上次运行时间", bool(sh["last_run_at"]))
check("safehoo 待处理 4（5 条里 1 条已判）", sh["raw_pending"] == 4,
      sh["raw_pending"])
check("mohurd never_run 翻转为 False", mh["never_run"] is False)
check("没跑过的源仍 never_run=True",
      all(s["never_run"] for s in body["data"]
          if s["source_key"] not in ("safehoo", "mohurd")))

print("\n=== ⑪ 取消与删除 ===")
st, body = req(f"/api/collect/tasks/{tid2}/cancel", "POST")
check("已结束的任务不能再取消（409）", st == 409, st)
check("409 附带取消粒度说明", "中断点" in (body.get("note") or ""), body.get("note"))

st, body = req(f"/api/collect/tasks/{tid2}", "DELETE")
check("删除已完成任务 200", st == 200, st)
st, body = req("/api/collect/raw")
check("删任务不删数据（仍 8 条）", body["meta"]["total"] == 8, body["meta"]["total"])
st, body = req("/api/collect/tasks")
check("任务列表剩 1 条", len(body["data"]) == 1, len(body["data"]))
check("被删任务的产出 task_id 已抹空",
      all(r.get("task_id") != tid2 for r in req("/api/collect/raw?size=50")[1]["data"]))

print("\n=== ⑫ 分页 ===")
st, body = req("/api/collect/raw?size=3&page=2")
check("第 2 页返回 3 条", len(body["data"]) == 3, len(body["data"]))
check("pages = 3", body["meta"]["pages"] == 3, body["meta"])
check("page = 2", body["meta"]["page"] == 2, body["meta"])
st, body = req("/api/collect/raw?size=3&page=2")
ids2 = {r["raw_id"] for r in body["data"]}
st, body = req("/api/collect/raw?size=3&page=1")
ids1 = {r["raw_id"] for r in body["data"]}
check("两页无重叠", not (ids1 & ids2))

print("\n=== ⑬ 坏输入 ===")
st, body = req("/api/collect/raw/nope")
check("不存在的 raw → 404", st == 404, st)
st, body = req("/api/collect/tasks/nope")
check("不存在的 task → 404", st == 404, st)
st, body = req("/api/collect/tasks", "POST", {"source_keys": ["not_a_source"]})
check("未登记的源 → 400", st == 400, st)
st, body = req("/api/collect/tasks", "POST", {"source_keys": []})
check("空 source_keys → 400", st == 400, st)
st, body = req("/api/collect/raw?size=99999&page=0")
check("越界分页参数被夹回", st == 200 and body["meta"]["size"] == 200
      and body["meta"]["page"] == 1, body["meta"])

server.shutdown()

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
print("=" * 52)
sys.exit(1 if FAIL else 0)
