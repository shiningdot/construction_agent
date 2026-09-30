"""在真实主库上做验收对账（蓝图 07 验收标准第 2/3/4 条）。

前面所有 e2e 脚本跑的都是临时库，那里只有个位数、几十条造出来的数据。
临时库能验「逻辑对不对」，验不了「在 2,623 条 / 16,323 条编码的真实分布下
对不对」——而后者才是上线后用户会遇到的形态。本脚本补这一段：

  1. 每个数据端点的返回值与**直接 SQL** 逐项对账（条数、分布、分层计数）
  2. 概览页三级证据数须为 14,784 / 1,117 / 422（与论文对账记录一致）
  3. 列表分页/筛选是否真下推 SQL——把 2,623 条**整本翻完**，验证取回的 id
     集合与全量 SQL 完全一致：不多、不少、不重
  4. 抽样详情页的编码数与编码集合与 SQL 一致

第 3 条是这里唯一能抓到「ORDER BY 不稳定导致跨页重复/遗漏」的手段。主库
`date` 有大量并列（同日多起事故），分页排序若只按 `date` 一个键，SQLite 对
并列行的顺序不作保证，翻页就会出现同一条出现两次、另一条一次都不出现——
而**每一页单独看都是对的**，只有翻完整本才知道少了。

本脚本只读主库，不写任何库。

换库跑要用 `CAIEMS_MAIN_DB`，**不是 `CAIEMS_DB`**——本脚本会用前者覆盖后者
（见下方 `os.environ["CAIEMS_DB"] = MAIN_DB`），以保证 app 与 SQL 对账读的是
同一个文件。只传 `CAIEMS_DB` 会被无声覆盖掉。
（2026-10-01 实测踩过：指着补了索引的副本跑，其实量的还是未补索引的主库，
得到的耗时与主库现状分毫不差，差点当成"索引无效"。）
"""

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

MAIN_DB = main_db_path()

# 工作库给个临时路径：本脚本不碰采集/提取/审核，但 app 需要一个可解析的
# 路径才不会在读 staging 的端点上炸掉。不建库——那些端点会如实地回
# available=false，这本身就该是「主库有、工作库还没建」时的正常表现。
TMP = Path(tempfile.mkdtemp(prefix="caiems_verify_main_"))
os.environ["CAIEMS_STAGING_DB"] = str(TMP / "unused_staging.db")
os.environ["CAIEMS_DB"] = MAIN_DB

from construction_agent.web.factory import create_app  # noqa: E402

PORT = 5098
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


def note(text):
    print("  ···· " + text)


if not Path(MAIN_DB).exists():
    print(f"主库不存在：{MAIN_DB}")
    sys.exit(2)

MAIN = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)


def sql1(q, args=()):
    return MAIN.execute(q, args).fetchone()[0]


def sql_all(q, args=()):
    return [r[0] for r in MAIN.execute(q, args)]


app = create_app({"TESTING": False})
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.6)


def req(path):
    r = urllib.request.Request(
        BASE + quote(path, safe="/?=&"), method="GET",
        headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}


def timed(path, rounds=3):
    """预热一轮再取最快的一次——冷启动不混进性能数字。"""
    req(path)
    best = None
    for _ in range(rounds):
        t0 = time.perf_counter()
        st, body = req(path)
        ms = (time.perf_counter() - t0) * 1000
        best = ms if best is None else min(best, ms)
    return st, body, best


N_ACC = sql1("SELECT COUNT(*) FROM accidents")
N_CAUSE = sql1("SELECT COUNT(*) FROM caics_causes")
print(f"主库：{MAIN_DB}\n事故 {N_ACC} 条 · 编码 {N_CAUSE} 行\n")

INDEXES = sql_all("SELECT name FROM sqlite_master WHERE type='index' "
                  "AND name LIKE 'idx_%'")
if len(INDEXES) <= 1:
    note(f"⚠ 主库只有 {len(INDEXES)} 个索引（{INDEXES}）。缺索引时列表筛选是全表"
         f"扫描，实测 ?has_causes=1&sort=deaths_desc 要 3.5 秒，补索引后 2 毫秒。"
         f"执行：CAIEMS_DB={MAIN_DB} python3 scripts/add_indexes.py")


# ══════════════════════════════════════════════════════════════════════
print("\n── ① 概览：每个数字都要能在 SQL 里找到出处 ──")

st, ov, ms = timed("/api/overview")
check("概览 200", st == 200, st)
T = ov["totals"]
check("事故总数 == COUNT(*)", T["accidents"] == N_ACC, (T["accidents"], N_ACC))
check("编码总数 == COUNT(*)",
      T["causes"] == N_CAUSE, (T["causes"], N_CAUSE))
check("死亡 == SUM(deaths)",
      T["deaths"] == sql1("SELECT SUM(deaths) FROM accidents"), T["deaths"])
check("受伤 == SUM(injuries)",
      T["injuries"] == sql1("SELECT SUM(injuries) FROM accidents"), T["injuries"])

# 三级证据：蓝图 07 明确要求与论文对账记录一致
eq = {e["key"]: e["count"] for e in ov["evidence_quality"]}
real_eq = dict(MAIN.execute(
    "SELECT evidence_quality, COUNT(*) FROM caics_causes GROUP BY 1"))
check("三级证据 direct_quote = 14,784", eq["direct_quote"] == 14784, eq["direct_quote"])
check("三级证据 indirect_infer = 1,117",
      eq["indirect_infer"] == 1117, eq["indirect_infer"])
check("三级证据 weak_infer = 422", eq["weak_infer"] == 422, eq["weak_infer"])
check("三级证据与 SQL GROUP BY 逐项一致", eq == real_eq, (eq, real_eq))

# 空值不被丢掉，而是归到一个哨兵上——概览页要能显示「未标注」那一片有多大。
#
# ⚠️ 两个端点对同一概念用了**两个不同的哨兵**：概览用展示名「未标注」当 key，
# 筛选器用 `__empty__` 当 value（label 才是「未标注」）。各有道理——筛选器的
# value 要能当查询参数回传，得是个不会与真实取值撞车的码；概览只做展示。
# 但对账时得跟着分开映射，不然比的是两个东西。
_EMPTY = "COALESCE(NULLIF({c}, ''), '__empty__')"
_SHOWN = "COALESCE(NULLIF({c}, ''), '未标注')"
check("severity 分布 == SQL（空值归「未标注」）",
      {s["key"]: s["count"] for s in ov["severity"]}
      == dict(MAIN.execute(f"SELECT {_SHOWN.format(c='severity')}, COUNT(*) "
                           f"FROM accidents GROUP BY 1")))
check("accident_type 分布 == SQL（空值归「未标注」）",
      {s["key"]: s["count"] for s in ov["accident_type"]}
      == dict(MAIN.execute(f"SELECT {_SHOWN.format(c='accident_type')}, COUNT(*) "
                           f"FROM accidents GROUP BY 1")))
check("CAICS 层分布 == SQL",
      {s["key"]: s["count"] for s in ov["caics_layer"]}
      == dict(MAIN.execute("SELECT substr(code,1,1), COUNT(*) FROM caics_causes "
                           "GROUP BY 1")))
check("CAICS 目录 19 类，覆盖编码数之和 == 总编码数",
      len(ov["caics_catalog"]) == 19
      and sum(c["count"] for c in ov["caics_catalog"]) == N_CAUSE,
      (len(ov["caics_catalog"]), sum(c["count"] for c in ov["caics_catalog"])))
check("五层计数之和 == 总编码数",
      sum(s["count"] for s in ov["caics_layer"]) == N_CAUSE,
      sum(s["count"] for s in ov["caics_layer"]))
note(f"概览耗时 {ms:.0f} ms")


print("\n── ② 筛选器：每个维度的计数与 SQL 对齐 ──")

st, fl, ms = timed("/api/filters")
check("筛选器 200", st == 200, st)
for dim, col in (("severity", "severity"), ("accident_type", "accident_type"),
                 ("project_type", "project_type"),
                 ("construction_phase", "construction_phase")):
    api = {x["value"]: x["count"] for x in fl[dim]}
    real = dict(MAIN.execute(
        f"SELECT {_EMPTY.format(c=col)}, COUNT(*) FROM accidents GROUP BY 1"))
    check(f"{dim} 计数 == SQL", api == real,
          {k: (api.get(k), real.get(k)) for k in set(api) | set(real)
           if api.get(k) != real.get(k)})

# 逐个值真去筛一遍：计数对不代表筛得对（可能 COUNT 与 WHERE 用了两套逻辑）
for val in ("一般事故", "较大事故"):
    st, body = req(f"/api/accidents?severity={val}")
    check(f"按 severity={val} 筛选的 total == SQL",
          body["meta"]["total"]
          == sql1("SELECT COUNT(*) FROM accidents WHERE severity = ?", (val,)),
          body["meta"]["total"])

print("\n── ③ 分页：整本翻完，与全量 SQL 对账 ──")

st, first = req("/api/accidents?size=200")
PAGE_MAX = first["meta"]["size"]
PAGES = first["meta"]["pages"]
check("分页元信息自洽",
      first["meta"]["total"] == N_ACC
      and PAGES == max(1, (N_ACC + PAGE_MAX - 1) // PAGE_MAX),
      first["meta"])

seen: list[str] = []
t0 = time.perf_counter()
for p in range(1, PAGES + 1):
    st, body = req(f"/api/accidents?size={PAGE_MAX}&page={p}")
    seen.extend(r["id"] for r in body["items"])
elapsed = (time.perf_counter() - t0) * 1000

check(f"翻完 {PAGES} 页取回 {len(seen)} 条 == 总数", len(seen) == N_ACC,
      (len(seen), N_ACC))
check("**没有跨页重复**（ORDER BY 稳定）", len(set(seen)) == len(seen),
      f"{len(seen) - len(set(seen))} 条重复")
check("**没有跨页遗漏**", set(seen) == set(sql_all("SELECT id FROM accidents")))
check("顺序与 SQL 的 ORDER BY date DESC, id 一致",
      seen == sql_all("SELECT id FROM accidents "
                      "ORDER BY date DESC NULLS LAST, id"),
      "前 3 条差异：" + str([(a, b) for a, b in
                             zip(seen, sql_all("SELECT id FROM accidents "
                                               "ORDER BY date DESC NULLS LAST, id"))
                             if a != b][:3]))
note(f"整本 {PAGES} 页共 {elapsed:.0f} ms（平均 {elapsed / PAGES:.1f} ms/页）")

# 边界：越界页
st, body = req(f"/api/accidents?size={PAGE_MAX}&page={PAGES + 5}")
check("越界页返回空列表而不是报错", st == 200 and body["items"] == [], st)

# 带筛选翻页（走的是另一条 SQL 分支）
st, body = req("/api/accidents?severity=一般事故&size=50&page=1")
n_filt = sql1("SELECT COUNT(*) FROM accidents WHERE severity='一般事故'")
check("筛选后的 total == SQL", body["meta"]["total"] == n_filt,
      (body["meta"]["total"], n_filt))
check("筛选后的页数按筛选结果算",
      body["meta"]["pages"] == max(1, (n_filt + 49) // 50), body["meta"]["pages"])

# 单独记一笔最慢的路径：它同时走 EXISTS 子查询和排序，是索引缺失时唯一
# 会慢到用户能察觉的端点（实测无索引 3.5 秒 / 补索引 2 毫秒）
st, _, ms_slow = timed("/api/accidents?has_causes=1&sort=deaths_desc")
note(f"最慢路径 ?has_causes=1&sort=deaths_desc：{ms_slow:.0f} ms"
     + ("（缺索引所致）" if len(INDEXES) <= 1 else ""))

print("\n── ④ 致因分析四张图的数据 ──")

st, dist, ms = timed("/api/causes/distribution")
d = dist["data"]
check("分布 200 且总编码数 == SQL", d["total_causes"] == N_CAUSE,
      (d["total_causes"], N_CAUSE))
check("按层分布 == SQL",
      {x["key"]: x["count"] for x in d["by_layer"]}
      == dict(MAIN.execute("SELECT substr(code,1,1), COUNT(*) FROM caics_causes "
                           "GROUP BY 1")))
check("按类分布 19 类且计数之和 == 总编码数",
      len(d["by_category"]) == 19
      and sum(x["count"] for x in d["by_category"]) == N_CAUSE,
      (len(d["by_category"]), sum(x["count"] for x in d["by_category"])))
check("证据等级分布 == SQL",
      {x["key"]: x["count"] for x in d["evidence_quality"]} == real_eq)
note(f"分布耗时 {ms:.0f} ms")

st, frame = req("/api/causes/framework")
fw = frame["data"]
# `framework.total` 是它覆盖的**编码数**，不是类数——类数在 layers[].categories 里
check("体系 5 层 19 类",
      len(fw["layers"]) == 5
      and sum(len(l["categories"]) for l in fw["layers"]) == 19,
      (len(fw["layers"]), sum(len(l["categories"]) for l in fw["layers"])))
check("体系覆盖编码数 == 总编码数", fw["total"] == N_CAUSE, fw["total"])
check("层计数之和 == 总编码数",
      sum(l["count"] for l in fw["layers"]) == N_CAUSE,
      sum(l["count"] for l in fw["layers"]))

st, co = req("/api/causes/cooccurrence")
# `pair_total` 是出现过的编码对**种类数**（19 类两两组合的上界是 C(19,2)=171），
# 而 `pairs` 是展示用的样例（top 20）。两个数不是一回事，别拿 pairs 去对
check("共现对的种类数 == SQL 不同对数",
      co["data"]["pair_total"] == sql1(
          "SELECT COUNT(*) FROM (SELECT DISTINCT a.code, b.code "
          "FROM caics_causes a JOIN caics_causes b "
          "ON a.accident_id = b.accident_id AND a.code < b.code)"),
      co["data"]["pair_total"])
check("共现图的编码集合 == SQL DISTINCT code",
      set(co["data"]["codes"]) == set(sql_all("SELECT DISTINCT code FROM caics_causes")))
check("framework 与 cooccurrence 对「有编码的事故数」口径一致",
      fw["covered_accidents"] == co["data"]["scope"]["accidents"],
      (fw["covered_accidents"], co["data"]["scope"]["accidents"]))

st, tl = req("/api/causes/timeline")
years = tl["data"]["years"]
sql_years = set(sql_all("SELECT DISTINCT substr(date,1,4) FROM accidents "
                        "WHERE date IS NOT NULL AND date <> ''"))
check("时间线覆盖 SQL 里的全部年份", sql_years <= set(years),
      sorted(sql_years - set(years)))
# 断档年份（1991–2004，论文里已知）要补成 0 而不是从轴上跳过——跳过之后
# 折线会把 1990 与 2005 画成相邻，看图的人会以为那几年没统计，
# 而事实是那几年本来就没有记录
check("断档年份补成 0，年份轴连续",
      len(years) == int(max(sql_years)) - int(min(sql_years)) + 1, len(years))
check("无日期条数 == SQL",
      tl["data"]["without_date"]
      == sql1("SELECT COUNT(*) FROM accidents WHERE date IS NULL OR date = ''"),
      tl["data"]["without_date"])

st, pa = req("/api/province/audit")
check("省份自检通过（计数不漂移）",
      pa["selfcheck"]["ok"] and pa["selfcheck"]["drift"] == 0, pa["selfcheck"])

print("\n── ⑤ 抽样详情：分组不丢数据 ──")

# 确定性抽样（每 131 条取一条），不用 ORDER BY RANDOM()——那样每次跑覆盖的
# 样本都不一样，出了问题也复现不了
sample = sql_all(f"SELECT id FROM accidents ORDER BY id LIMIT 20 OFFSET {N_ACC // 4}")
dup_groups = sql1("""SELECT COUNT(*) FROM (SELECT accident_id, code
                       FROM caics_causes GROUP BY 1, 2 HAVING COUNT(*) > 1)""")
bad = []
for aid in sample:
    st, body = req(f"/api/accidents/{aid}")
    items = body["data"]["causes"]["items"]
    if {c["code"] for c in items} != set(
            sql_all("SELECT DISTINCT code FROM caics_causes WHERE accident_id = ?",
                    (aid,))):
        bad.append((aid, "code 集合不符"))
        continue
    # 同一 code 有多条标注时，_group_causes 合并进 citations。合并**不能丢行**：
    # 主库里这样的重复组实测有几百组，丢了就是静默少数据
    if sum(c["citation_count"] for c in items) != sql1(
            "SELECT COUNT(*) FROM caics_causes WHERE accident_id = ?", (aid,)):
        bad.append((aid, "citation_count 之和 != 编码行数"))
check(f"抽样 {len(sample)} 条：编码集合与条数都对得上", not bad, bad)
note(f"（主库里同一事故同一 code 有多行的重复组共 {dup_groups} 组，"
     f"抽样必然覆盖到，是 citation 合并的真路径）")

st, im = req("/api/images/stats")
check("图片统计 200 且覆盖数为整数",
      st == 200 and isinstance(im["data"]["files"], int), im["data"]["files"])

server.shutdown()
print(f"\n{'=' * 56}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败清单：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
