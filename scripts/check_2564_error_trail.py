#!/usr/bin/env python3
"""§2.5.64 自检：错误留痕链路 ↔ 文档。

§2.5.63 附带二记下「`processing_log.error_message` 45/45 NULL、
`search_logs.error` 1,636/1,636 NULL，从来没写进过任何值」。本节查到底：
**是运行中恰好没出错，还是结构上就写不进去？** 并量「不留痕」的代价。

八段：
  A. **三条写入路径的列清单**——从源码里读 `INSERT` 的列名，断言「含/不含
     `error_message`」「含/不含 `error`」「`engine` 字面量」。
     ⚠️ **这一段查的是今天的代码**。它的作用是"发现代码又变了"，**不是**证明跑批当时
     如此——本节第五节已实测出三处「今天的代码 ≠ 当时的代码」（fallback 的
     `.replace`、`engine` 的字面量、被注释掉的判断），故 A 段的结论不当作历史事实用。
  B. **两列全空** + `engine` 单一取值 + 45 行 verdict 非 `batch_direct`（双向指认来源）。
  C. **97.4% 的一整套数**（1,636 / 1,594 / 42 / 987）与 `1,594+42=1,636` 的闭合。
  D. **query 形态**——LCP=80 须 100%、`== raw[:80]` 须 1,594、
     `== raw[:80].replace('\\n',' ')` 须 **0**。最后一条是**故意反过来断言**的：
     它为 0 恰恰证明"当时跑的代码与今天不同"，为正就说明代码被改回来了。
  E. **checkpoint 交集四则**（1,494 / 1,494 / 1,494 / **0**）+ 时间指纹（08-05 14:25–14:55）。
  F. **`OR REPLACE` 隐患**——表定义无 UNIQUE，但重复行 0：**两半都要断言**，
     只说前半会把"结构缺陷"说成"已造成损害"。
  G. **四个出口的组合**（读源码）——出口 ① 的 `search_performed=False` 必须成立，
     否则"最严重的失败落在落库条件之外"这句话不成立。
  H. 文档 ↔ 实测。
  I. `--drift-test`——往文档注入漂移，逐个断言退出码转 1。

口径：主库**只读**（`file:...?mode=ro`），零网络请求，不写任何数据。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import main_db_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]      # 本仓库（worktree 或主仓库）
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
DB = Path(main_db_path())
#: 跑批 checkpoint 与主库同在 data/ 下——用 DB.parent 派生，不写第二套路径推导
CKPT = DB.parent / "search_image_checkpoint.json"

SCRIPT_A = ROOT / "scripts" / "run_batch.py"
SCRIPT_B = ROOT / "scripts" / "run_batch_pipeline.py"
SCRIPT_C = ROOT / "scripts" / "batch_search_image_100.py"
ENHANCER = ROOT / "construction_agent" / "extraction" / "search_enhancer.py"

fails: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def inserts(path: Path) -> dict[str, str]:
    """从源码里提取 {表名: 列清单原文}，支持跨行与 `INSERT OR REPLACE`。"""
    src = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in re.finditer(
            r"INSERT\s+(?:OR\s+REPLACE\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", src, re.S | re.I):
        out[m.group(1)] = m.group(2)
    return out


def cols_of(colblob: str) -> list[str]:
    return [c.strip() for c in colblob.replace("\n", " ").split(",") if c.strip()]


# ---------------------------------------------------------------------------
# A. 三条写入路径的列清单（读**今天的**源码）
# ---------------------------------------------------------------------------
def part_a() -> None:
    print("\nA. 三条写入路径的列清单（读源码；只说明「今天的代码」，不等于跑批当时）")
    print("-" * 74)
    ia, ib, ic = inserts(SCRIPT_A), inserts(SCRIPT_B), inserts(SCRIPT_C)

    def has(d: dict, t: str, c: str) -> bool:
        return t in d and c in cols_of(d[t])

    # run_batch.py：processing_log 含 error_message，但实参写死 None
    ok("processing_log" in ia, "run_batch.py 写 processing_log")
    ok(has(ia, "processing_log", "error_message"),
       "run_batch.py 的 processing_log **列清单含** error_message")
    src_a = SCRIPT_A.read_text(encoding="utf-8")
    ok(bool(re.search(r"ems,\s*None,\s*now", src_a)),
       "run_batch.py 该列实参是**字面量 None**（`…ems, None, now))`）"
       "——写成常量，不是变量")

    # run_batch_pipeline.py：processing_log 不含 error_message；search_logs 含 error
    ok("processing_log" in ib, "run_batch_pipeline.py 写 processing_log")
    ok(not has(ib, "processing_log", "error_message"),
       "run_batch_pipeline.py 的 processing_log **列清单不含** error_message",
       f"实测列数 {len(cols_of(ib.get('processing_log', '')))}")
    ok(has(ib, "search_logs", "error"),
       "run_batch_pipeline.py 的 search_logs **列清单含** error（有变量位）")

    # batch_search_image_100.py：search_logs 不含 error
    ok("search_logs" in ic, "batch_search_image_100.py 写 search_logs")
    ok(not has(ic, "search_logs", "error"),
       "batch_search_image_100.py 的 search_logs **列清单不含** error",
       f"实测列数 {len(cols_of(ic.get('search_logs', '')))}")

    # engine 字面量
    src_b, src_c = SCRIPT_B.read_text(encoding="utf-8"), SCRIPT_C.read_text(encoding="utf-8")
    ok('"qianfan"' in src_b, "run_batch_pipeline.py 的 engine 字面量是 'qianfan'")
    ok("'uapi'" in src_c, "batch_search_image_100.py 的 engine 字面量是 'uapi'")
    print("     ⚠️ 上面这几行描述的是**今天的**代码。实测 42 条的 engine 是 'uapi'，")
    print("        而写它们的是 run_batch_pipeline —— 即当时的字面量不是 'qianfan'。")


# ---------------------------------------------------------------------------
# B/C/D. 主库实测
# ---------------------------------------------------------------------------
def measure() -> dict:
    con = conn()
    m: dict = {"db": str(DB), "db_mb": round(DB.stat().st_size / 1e6, 1)}

    # 两列全空
    m["pl_rows"] = con.execute("SELECT COUNT(*) FROM processing_log").fetchone()[0]
    m["pl_err"] = con.execute(
        "SELECT COUNT(*) FROM processing_log WHERE error_message IS NOT NULL").fetchone()[0]
    m["sl_rows"] = con.execute("SELECT COUNT(*) FROM search_logs").fetchone()[0]
    m["sl_err"] = con.execute(
        "SELECT COUNT(*) FROM search_logs WHERE error IS NOT NULL").fetchone()[0]

    # engine / verdict
    m["engines"] = dict(con.execute(
        "SELECT COALESCE(engine, '(NULL)'), COUNT(*) FROM search_logs GROUP BY 1"))
    m["verdicts"] = dict(con.execute(
        "SELECT COALESCE(classification_verdict, '(NULL)'), COUNT(*) "
        "FROM processing_log GROUP BY 1"))
    m["n_batch_direct"] = m["verdicts"].get("batch_direct", 0)

    # C 段：97.4% 一整套
    m["sl_tot0"] = con.execute(
        "SELECT COUNT(*) FROM search_logs WHERE total_results = 0").fetchone()[0]
    m["sl_sac0"] = con.execute(
        "SELECT COUNT(*) FROM search_logs WHERE same_accident_count = 0").fetchone()[0]
    m["acc_raw"] = con.execute(
        "SELECT COUNT(*) FROM accidents WHERE search_enriched_text = raw_text").fetchone()[0]
    m["acc_enr"] = con.execute(
        "SELECT COUNT(*) FROM accidents "
        "WHERE INSTR(search_enriched_text, '【同事故多源信息') > 0").fetchone()[0]
    m["acc_null"] = con.execute(
        "SELECT COUNT(*) FROM accidents WHERE search_enriched_text IS NULL "
        "OR LENGTH(search_enriched_text) = 0").fetchone()[0]
    m["acc_sp1"] = con.execute(
        "SELECT COUNT(*) FROM accidents WHERE search_performed = 1").fetchone()[0]

    # D 段：query 形态
    lcp_hist: dict[int, int] = {}
    n_eq, n_eqrep, n_len = 0, 0, {}
    layer: dict[int, list[int]] = {}   # query 条数 -> [行数, tot0, 增强, query是正文前缀]
    for aid, qu, tot, enr, raw in con.execute(
            "SELECT sl.accident_id, sl.queries_used, sl.total_results, "
            "  a.search_enriched_text, a.raw_text "
            "FROM search_logs sl JOIN accidents a ON a.id = sl.accident_id"):
        q = json.loads(qu)
        k = len(q)
        n_len[k] = n_len.get(k, 0) + 1
        cell = layer.setdefault(k, [0, 0, 0, 0])
        cell[0] += 1
        if tot == 0:
            cell[1] += 1
        if enr and "【同事故多源信息" in enr:
            cell[2] += 1
        if raw and q and q[0] == raw[:80]:
            cell[3] += 1
        if k != 1 or not raw:
            continue
        n = 0
        for x, y in zip(q[0], raw):
            if x != y:
                break
            n += 1
        lcp_hist[n] = lcp_hist.get(n, 0) + 1
        if q[0] == raw[:80]:
            n_eq += 1
        if q[0] == raw[:80].replace("\n", " "):
            n_eqrep += 1
    m["lcp_hist"] = lcp_hist
    m["lcp80"] = lcp_hist.get(80, 0)
    m["q_eq80"] = n_eq
    m["q_eq80rep"] = n_eqrep
    m["q_len_hist"] = n_len
    m["q_layer"] = dict(sorted(layer.items()))
    m["pl_time"] = sorted({str(r[0])[:16] for r in con.execute(
        "SELECT created_at FROM processing_log")})
    m["pl_time_n"] = dict(con.execute(
        "SELECT substr(created_at, 1, 16), COUNT(*) FROM processing_log GROUP BY 1"))

    # E 段：checkpoint 交集 + 时间指纹
    m["ckpt"] = len(json.load(open(CKPT))) if CKPT.exists() else -1
    if CKPT.exists():
        ck = set(json.load(open(CKPT)))
        rows = con.execute("SELECT accident_id, json_array_length(queries_used), created_at "
                           "FROM search_logs").fetchall()
        s1 = {a for a, k, _ in rows if k == 1}
        sN = {a for a, k, _ in rows if k >= 2}
        m["ck_all"] = len(ck & {a for a, _, _ in rows})
        m["ck_len1"] = len(ck & s1)
        m["ck_lenN"] = len(ck & sN)
        m["set_len1"] = len(s1)
        m["ck_pl"] = len(ck & {r[0] for r in con.execute(
            "SELECT accident_id FROM processing_log")})
        m["time_len1"] = sorted({str(c)[:16] for a, k, c in rows if k == 1})
        m["time_lenN"] = sorted({str(c)[:16] for a, k, c in rows if k >= 2})
    m["ckpt_mtime"] = (
        _dt.datetime.fromtimestamp(CKPT.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        if CKPT.exists() else "(文件不在)")

    # 时间指纹同至秒
    r = con.execute(
        "SELECT sl.created_at, a.created_at FROM search_logs sl "
        "JOIN accidents a ON a.id = sl.accident_id "
        "WHERE json_array_length(sl.queries_used) >= 2").fetchall()
    m["n_enr_rows"] = len(r)
    m["n_same_sec"] = sum(1 for x, y in r if str(x)[:19] == str(y)[:19])
    return m


def part_b(m: dict) -> None:
    print("\nB. 两列全空：结构上写不进去（不是运行中恰好没出错）")
    print("-" * 74)
    ok(m["pl_err"] == 0 and m["pl_rows"] == 45,
       "processing_log.error_message 全 NULL", f"{m['pl_err']}/{m['pl_rows']}")
    ok(m["sl_err"] == 0 and m["sl_rows"] == 1636,
       "search_logs.error 全 NULL", f"{m['sl_err']}/{m['sl_rows']}")
    ok(m["engines"] == {"uapi": 1636},
       "search_logs.engine 单一取值 'uapi'（写它的那条路径列清单里没有 error）",
       str(m["engines"]))
    ok(m["n_batch_direct"] == 0,
       "45 行 processing_log 的 verdict **不是** batch_direct "
       "⇒ 出自 run_batch_pipeline（列清单无 error_message 的那条）",
       str(m["verdicts"]))


def part_c(m: dict) -> None:
    print("\nC. 代价：97.4% 的搜索增强失败，一个字都没留下")
    print("-" * 74)
    ok(m["sl_tot0"] == 1594, "total_results=0 的 1,594 行", str(m["sl_tot0"]))
    ok(m["sl_sac0"] == 1594, "same_accident_count=0 的 1,594 行", str(m["sl_sac0"]))
    ok(m["acc_raw"] == 1594,
       "事故正文侧独立印证：search_enriched_text == raw_text 的 1,594 条",
       str(m["acc_raw"]))
    ok(m["acc_enr"] == 42, "真被增强（带「同事故多源信息」前缀）的 42 条", str(m["acc_enr"]))
    ok(m["acc_sp1"] == 1636, "search_performed=1 的 1,636 条", str(m["acc_sp1"]))
    ok(m["acc_null"] == 987, "search_enriched_text 为 NULL 的 987 条", str(m["acc_null"]))
    ok(m["acc_enr"] + m["acc_raw"] == m["acc_sp1"],
       "闭合：42 + 1,594 = 1,636 = search_performed=1 的总数",
       f"{m['acc_enr']}+{m['acc_raw']}={m['acc_enr'] + m['acc_raw']} vs {m['acc_sp1']}")
    rate = 1 - m["acc_enr"] / m["acc_sp1"]
    ok(round(rate, 3) == 0.974, "⇒ 声称搜过、实则无收获的比例 97.4%", f"{rate:.1%}")


def part_d(m: dict) -> None:
    print("\nD. 失败的机制：query 就是正文前 80 字（形态证据，不靠读码）")
    print("-" * 74)
    ok(m["lcp80"] == 1594 and len(m["lcp_hist"]) == 1,
       "最长公共前缀 = 80 的占 100%（分布只有一个键）", str(m["lcp_hist"]))
    ok(m["q_eq80"] == 1594, "query 逐字等于 raw_text[:80]", str(m["q_eq80"]))
    ok(m["q_eq80rep"] == 0,
       "query **不等于** raw_text[:80].replace(chr(10),' ') —— **为 0 才对**："
       "证明当时跑的代码里没有那个 replace（今天的代码有）", str(m["q_eq80rep"]))
    ok(m["q_len_hist"].get(1) == 1594,
       "query 条数 = 1 的 1,594 条（= 正文当检索词的那批）", str(m["q_len_hist"]))
    ok(m["q_len_hist"].get(3, 0) + m["q_len_hist"].get(2, 0) == 42,
       "query 条数 = 2/3 的合计 42 条（= 关键词那批，恰好等于真增强数）",
       str(m["q_len_hist"]))


def part_e(m: dict) -> None:
    print("\nE. 归属判定：落盘的 checkpoint 与时间指纹（源码只能提假设，数据才能定案）")
    print("-" * 74)
    if not CKPT.exists():
        ok(False, "checkpoint 文件不在——归属判定失去第一手凭据", str(CKPT))
        return
    ok(m["ckpt"] == 1494, "checkpoint 记录 1,494 条", str(m["ckpt"]))
    ok(m["ck_all"] == 1494, "checkpoint ∩ 全部 search_logs = 1,494（无一条缺日志）",
       str(m["ck_all"]))
    ok(m["ck_len1"] == 1494,
       f"checkpoint ∩ len=1 批 = 1,494（覆盖该批 {m['ck_len1']/m['set_len1']:.1%}）",
       str(m["ck_len1"]))
    ok(m["ck_lenN"] == 0,
       "checkpoint ∩ len≥2 批 = **0** ⇒ 那 42 条**不是** batch_search_image_100 跑的",
       str(m["ck_lenN"]))
    ok(m["time_len1"] and all(t.startswith("2026-08-05T14:") for t in m["time_len1"]),
       "len=1 批的 created_at 全部落在 2026-08-05 14 时",
       f"{len(m['time_len1'])} 个时刻")
    ok(set(m["time_lenN"]) == {"2026-07-23T22:43", "2026-07-24T12:14"},
       "len≥2 批的 created_at 与 processing_log 同批（07-23 22:43 / 07-24 12:14）",
       str(m["time_lenN"]))
    ok(m["n_same_sec"] == m["n_enr_rows"] == 42,
       "42 条中 search_log 与 accident 的 created_at **同至秒**的 42/42",
       f"{m['n_same_sec']}/{m['n_enr_rows']}")


def part_f(m: dict) -> None:
    print("\nF. `INSERT OR REPLACE` 空承诺：**隐患成立、实际未触发**（两半都要断言）")
    print("-" * 74)
    con = conn()
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='search_logs'").fetchone()[0]
    ok("UNIQUE" not in sql.upper(),
       "search_logs 表定义**无 UNIQUE 约束** ⇒ OR REPLACE 只是普通 INSERT（隐患成立）")
    dup = con.execute(
        "SELECT COUNT(*) FROM (SELECT accident_id FROM search_logs "
        "GROUP BY accident_id HAVING COUNT(*) > 1)").fetchone()[0]
    mx = con.execute(
        "SELECT MAX(c) FROM (SELECT COUNT(*) c FROM search_logs GROUP BY accident_id)"
    ).fetchone()[0]
    ok(dup == 0 and mx == 1, "但全库重复行 **0** 条、最大行数 1（实际损害未发生）",
       f"重复 {dup}，最大 {mx}")
    n_cand = con.execute(
        "SELECT COUNT(*) FROM accidents WHERE source_name IN ('safehoo','esafety') "
        "AND (search_performed = 0 OR search_performed IS NULL) "
        "AND raw_text IS NOT NULL AND length(raw_text) > 100").fetchone()[0]
    ok(n_cand == 0,
       "候选条件（search_performed=0）当前剩 0 条 ⇒ 不会撞车（这是未触发的原因）",
       str(n_cand))
    con.close()


def part_g() -> None:
    print("\nG. `enhance()` 四个出口：最严重的那个落在落库条件之外")
    print("-" * 74)
    src = ENHANCER.read_text(encoding="utf-8")
    for needle, label in (
            ('"API未配置"', "出口 ① 写 error='API未配置'"),
            ('"搜索无结果"', "出口 ② 写 error='搜索无结果'"),
            ('"未找到同事故信息"', "出口 ③ 写 error='未找到同事故信息'"),
            ("error\": None if same", "出口 ④ 有同事故时 error=None"),
    ):
        ok(needle in src, label)
    m = re.search(r'"search_performed":\s*False[^}]*"error":\s*"API未配置"'
                  r'|"error":\s*"API未配置"[^}]*"search_performed":\s*False', src, re.S)
    ok(bool(m), "出口 ① 的 search_performed 是 **False** "
                "⇒ 被 `if …get('search_performed')` 排除，**整行日志都不写**")
    ok("if not queries:" in src and "text[:80]" in src,
       "fallback 分支存在（`if not queries:` → `text[:80]`）——正是 1,594 条 query 的来源")
    ok('enhance(raw_text)' in SCRIPT_C.read_text(encoding="utf-8"),
       "调用方 batch_search_image_100.py 是**单参数**调用 ⇒ 必然触发 fallback")


def part_h(m: dict) -> None:
    print("\nH. 文档 ↔ 实测")
    print("-" * 74)
    doc = DOC.read_text(encoding="utf-8")

    def grab(pat: str, label: str, actual: str, expect: str) -> None:
        f = re.search(pat, doc)
        if not f:
            ok(False, f"文档：{label}（锚点找不到）", pat[:50])
        else:
            ok(f.group(1) == expect, f"文档：{label}", f"文档={f.group(1)} 实测={actual}")

    grab(r"\| `search_logs` 总行数[^|]*\| \*\*([\d,]+)\*\* \|",
         "总行数 1,636", str(m["sl_rows"]), f"{m['sl_rows']:,}")
    grab(r"\| `accidents\.search_enriched_text` 与 `raw_text` \*\*完全相同\*\* \| \*\*([\d,]+)\*\* \|",
         "正文未增强 1,594", str(m["acc_raw"]), f"{m['acc_raw']:,}")
    grab(r"\| 带「`【同事故多源信息】`」前缀（= 真搜到同事故） \| \*\*([\d,]+)\*\* \|",
         "真增强 42", str(m["acc_enr"]), str(m["acc_enr"]))
    grab(r"\| `search_image_checkpoint\.json` 记录数 \| \*\*([\d,]+)\*\* \|",
         "checkpoint 1,494", str(m["ckpt"]), f"{m['ckpt']:,}")
    grab(r"\| checkpoint ∩ len≥2 批 \| \*\*(\d+)\*\* \|",
         "checkpoint∩len≥2 = 0", str(m["ck_lenN"]), str(m["ck_lenN"]))
    grab(r"\| `query == raw_text\[:80\]` \| \*\*([\d,]+)\*\* \|",
         "query==raw[:80] 1,594", str(m["q_eq80"]), f"{m['q_eq80']:,}")
    grab(r"\| `query == raw_text\[:80\]\.replace\('\\n', ' '\)` \| \*\*(\d+)\*\* \|",
         "query==replace版 0", str(m["q_eq80rep"]), str(m["q_eq80rep"]))
    grab(r"\| 42 条中 `search_log` 与 `accident` 的 `created_at` \*\*同至秒\*\*的 \| \*\*(\d+) / \d+\*\* \|",
         "同至秒 42/42", f"{m['n_same_sec']}/{m['n_enr_rows']}", str(m["n_same_sec"]))

    # ⚠️ 下面这几格是**漂移自证逼出来的**：首版 H 段只核了 8 格，而 DRIFT 列了 18 条
    #    用例——10 条用例改完数字脚本照样返 0，即那 10 个数字**根本没接判据**。
    #    「用例条数」与「判据覆盖的格数」不一致时，多出来的用例是空话。
    grab(r"\| 其中 `total_results = 0` \| \*\*([\d,]+)\*\* \|",
         "total_results=0 的 1,594", str(m["sl_tot0"]), f"{m['sl_tot0']:,}")
    grab(r"\| 其中 `same_accident_count = 0` \| \*\*([\d,]+)\*\* \|",
         "same_accident_count=0 的 1,594", str(m["sl_sac0"]), f"{m['sl_sac0']:,}")
    grab(r"\| `search_enriched_text` 为 NULL \| ([\d,]+) \|",
         "enriched 为 NULL 的 987", str(m["acc_null"]), str(m["acc_null"]))
    grab(r"\| `search_performed = 1` \| \*\*([\d,]+)\*\* \|",
         "search_performed=1 的 1,636", str(m["acc_sp1"]), f"{m['acc_sp1']:,}")
    grab(r"\| checkpoint ∩ 全部 `search_logs` \| \*\*([\d,]+)\*\*",
         "checkpoint∩全部 1,494", str(m["ck_all"]), f"{m['ck_all']:,}")
    grab(r"\| checkpoint ∩ len=1 批 \| \*\*([\d,]+)\*\*",
         "checkpoint∩len1 1,494", str(m["ck_len1"]), f"{m['ck_len1']:,}")

    def grab_row(pat: str, label: str, expects: list[str]) -> None:
        """抓一行里**全部**数字 token（去千分位）与实测逐列比对。
        比逐列写正则稳：少覆盖一列不会静默漏过，多覆盖一列会当场红。"""
        f = re.search(pat, doc)
        if not f:
            ok(False, f"文档：{label}（行锚点找不到）", pat[:50])
            return
        got = [t.replace(",", "") for t in re.findall(r"[\d,]+", f.group(0))]
        ok(got == expects, f"文档：{label}", f"文档={got} 实测={expects}")

    # 这一行**全行**数字按序是：LCP 长度 80 → 命中数 → 总数 → 百分比。
    # 首版漏了行首那个 80（正则里的字面常量），expects 少一列 ⇒ 当场红。
    # 这正是 grab_row 存在的意义：抓全行所有 token，漏一列就藏不住。
    grab_row(r"\| query 与 `raw_text` 的最长公共前缀 = 80 \| \*\*[\d,]+ / [\d,]+（\d+%）\*\* \|",
             "LCP=80 的 1,594/1,594（100%）",
             ["80", str(m["lcp80"]), str(m["q_layer"][1][0]), "100"])
    # 行锚点精确写（抄错会被"找不到"抓出来——用例 18 就是这么暴露的），
    # 但**比对值是算出来的**——锚点负责定位，判据负责结论。
    for k, anchor in (
        (1, r"\| \*\*1\*\* \| \*\*1,594\*\* \| \*\*1,594\*\* \| \*\*0\*\* \| \*\*1,594\*\* \|"),
        (2, r"\| 2 \| 1 \| 0 \| 1 \| 0 \|"),
        (3, r"\| 3 \| 41 \| 0 \| 41 \| 0 \|"),
    ):
        cell = m["q_layer"].get(k, [0, 0, 0, 0])
        grab_row(anchor, f"query 分层第 {k} 行（{cell}）",
                 [str(k)] + [str(x) for x in cell])
    grab(r"\| `processing_log` 45 行 `created_at` \| \*\*07-23T22:43\*\*（(\d+)）\+ \*\*07-24T12:14\*\*（\d+） \|",
         "processing_log 时间分布 07-23 段",
         str(m["pl_time_n"]), str(m["pl_time_n"].get("2026-07-23T22:43")))

    ok("**隐患成立、实际未触发**" in doc or "隐患成立、实际未触发" in doc,
       "文档：`OR REPLACE` 明确写成「隐患成立、实际未触发」"
       "（没把结构缺陷说成已造成损害）")
    ok("**代码会被改，数据冻结在跑批那一刻**" in doc,
       "文档：第五节把「拿今天的代码解释昨天的数据」写成教训")


def run() -> int:
    del fails[:]
    m = measure()
    part_a()
    part_b(m)
    part_c(m)
    part_d(m)
    part_e(m)
    part_f(m)
    part_g()
    part_h(m)
    print("\n" + "=" * 74)
    if fails:
        print(f"❌ 未通过 {len(fails)} 项：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


# ---------------------------------------------------------------------------
# I. 漂移自证
# ---------------------------------------------------------------------------
DRIFT = [
    (r"\| `search_logs` 总行数（= 覆盖的事故数，1:1） \| \*\*1,636\*\* \|",
     r"| `search_logs` 总行数（= 覆盖的事故数，1:1） | **1,637** |"),
    (r"\| 其中 `total_results = 0` \| \*\*1,594\*\* \|",
     r"| 其中 `total_results = 0` | **1,595** |"),
    (r"\| 其中 `same_accident_count = 0` \| \*\*1,594\*\* \|",
     r"| 其中 `same_accident_count = 0` | **1,593** |"),
    (r"\| `accidents\.search_enriched_text` 与 `raw_text` \*\*完全相同\*\* \| \*\*1,594\*\* \|",
     r"| `accidents.search_enriched_text` 与 `raw_text` **完全相同** | **1,595** |"),
    (r"\| 带「`【同事故多源信息】`」前缀（= 真搜到同事故） \| \*\*42\*\* \|",
     r"| 带「`【同事故多源信息】`」前缀（= 真搜到同事故） | **43** |"),
    (r"\| `search_enriched_text` 为 NULL \| 987 \|",
     r"| `search_enriched_text` 为 NULL | 988 |"),
    (r"\| `search_performed = 1` \| \*\*1,636\*\* \|",
     r"| `search_performed = 1` | **1,637** |"),
    (r"\| query 与 `raw_text` 的最长公共前缀 = 80 \| \*\*1,594 / 1,594（100%）\*\* \|",
     r"| query 与 `raw_text` 的最长公共前缀 = 80 | **1,593 / 1,594（100%）** |"),
    (r"\| `query == raw_text\[:80\]` \| \*\*1,594\*\* \|",
     r"| `query == raw_text[:80]` | **1,595** |"),
    (r"\| `query == raw_text\[:80\]\.replace\('\\n', ' '\)` \| \*\*0\*\* \|",
     r"| `query == raw_text[:80].replace('\n', ' ')` | **7** |"),
    (r"\| \*\*1\*\* \| \*\*1,594\*\* \| \*\*1,594\*\* \| \*\*0\*\* \| \*\*1,594\*\* \|",
     r"| **1** | **1,595** | **1,594** | **0** | **1,594** |"),
    (r"\| 3 \| 41 \| 0 \| 41 \| 0 \|", r"| 3 | 40 | 0 | 41 | 0 |"),
    (r"\| `search_image_checkpoint\.json` 记录数 \| \*\*1,494\*\* \|",
     r"| `search_image_checkpoint.json` 记录数 | **1,495** |"),
    (r"\| checkpoint ∩ 全部 `search_logs` \| \*\*1,494\*\*",
     r"| checkpoint ∩ 全部 `search_logs` | **1,493**"),
    (r"\| checkpoint ∩ len=1 批 \| \*\*1,494\*\*",
     r"| checkpoint ∩ len=1 批 | **1,493**"),
    (r"\| checkpoint ∩ len≥2 批 \| \*\*0\*\* \|",
     r"| checkpoint ∩ len≥2 批 | **9** |"),
    (r"\| 42 条中 `search_log` 与 `accident` 的 `created_at` \*\*同至秒\*\*的 \| \*\*42 / 42\*\* \|",
     r"| 42 条中 `search_log` 与 `accident` 的 `created_at` **同至秒**的 | **41 / 42** |"),
    # ⚠️ old 里的 `+` 必须写成 `\+`：裸 `+` 是量词（"一个或多个 `）`"），
    #    文档里 `（35）+` 的 `+` 是字面加号，正则会**匹配不上**——
    #    结果是"锚点找不到"而不是判据报警，用例静默失效。
    #    这与本条首版把 old 写成 `（34）`（文档是 `（35）`）是**同一种失效**：
    #    锚点写错 = 用例根本没跑。
    (r"\| `processing_log` 45 行 `created_at` \| \*\*07-23T22:43\*\*（35）\+ \*\*07-24T12:14\*\*（10） \|",
     r"| `processing_log` 45 行 `created_at` | **07-23T22:43**（34）+ **07-24T12:14**（10） |"),
    # ⚠️ 本条首版把 old 也写成了 `（34）`，锚点在文档里根本不存在——**用例静默失效**。
    #    「用例的 old 必须在文档里找得到」这条不成立时，红的是"锚点找不到"而不是判据。
    #    grab_row 的锚点同样按这个标准写：抄错就报"找不到"，不静默放过。
]


def drift_test() -> int:
    print("\nI. 漂移自证（往文档注入，逐个断言退出码转 1）")
    print("=" * 74)
    src = DOC.read_text(encoding="utf-8")
    bad = 0
    for i, (old, new) in enumerate(DRIFT, 1):
        if not re.search(old, src):
            print(f"  ❌ 用例 {i} 的锚点在文档里找不到：{old[:60]}")
            bad += 1
            continue
        DOC.write_text(re.sub(old, new, src, count=1), encoding="utf-8")
        rc = run()
        DOC.write_text(src, encoding="utf-8")
        print(f"  {'✅' if rc == 1 else '❌'} 用例 {i}: {old[:52]} → 退出码 {rc}")
        if rc != 1:
            bad += 1
    print(f"\n{'✅' if not bad else '❌'} 漂移用例全部被捕获（{len(DRIFT) - bad}/{len(DRIFT)}）")
    return bad


def main() -> int:
    print("§2.5.64 错误留痕链路取证 ↔ 文档")
    print("=" * 74)
    for p, what in ((DOC, "文档"), (DB, "主库"), (CKPT, "checkpoint")):
        if not p.exists():
            print(f"❌ {what}不在：{p}")
            return 1
    for p in (SCRIPT_A, SCRIPT_B, SCRIPT_C, ENHANCER):
        if not p.exists():
            print(f"❌ 源码不在：{p}")
            return 1
    return run()


if __name__ == "__main__":
    if "--drift-test" in sys.argv:
        sys.exit(1 if drift_test() else 0)
    sys.exit(main())
