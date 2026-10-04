#!/usr/bin/env python3
"""§2.5.65 自检：主库 133 列的语义体检 ↔ 文档。

§2.5.63 查了 95 列的转义残留、§2.5.64 查出两个错误列全空——两轮都落在
「**列在、语义在、数据不在**」这同一类缺陷上，但都是**被具体线索牵过去的**。
本节做一次**全表体检**：9 张表 133 列里，还有多少列不携带信息。

六段：
  A. **列分类计数**（全空 2 / 单值 14 / 低区分度 10 / 其余 107）+ 逐表分布，
     并**自证**已知三项（`error_message` 全空、`error` 全空、`pipeline_version` 单值）。
     自证是前提：一个体检脚本若报不出已知的两列全空，它报的"其余 107 列正常"一文不值。
  B. **14 个单值列逐个**核唯一值 + 覆盖行数（值抄错即红）。
  C. **两套缺失表示**——表级 NULL 单元 / 空串单元，纯 NULL 三表与纯空串两表分工；
     空串总量 2,055 与高度集中（前 5 列占 92.5%）。
  D. **口径陷阱三连**——`publish_date` 100% vs 65.4%、`weather` 的「未提及」46 条、
     `fall_height` / `trapped_count` 的记载值是「>0 率」而非「非空率」。
  E. **四个配套发现**——`source_count` 恒 1、`source_urls` 45 个 id、
     `processing_log` 覆盖率 1.7%、`vlm_model` 的 DEFAULT 死代码与 phase2 空串 218。
  F. 文档 ↔ 实测。
  G. `--drift-test`——往文档注入漂移，逐个断言退出码转 1。

口径：主库**只读**（`file:...?mode=ro`），零网络请求，不写任何数据。
"""
from __future__ import annotations

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

fails: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ---------------------------------------------------------------------------
# 体检
# ---------------------------------------------------------------------------
def measure() -> dict:
    con = conn()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    m: dict = {
        "tables": tables,
        "empty": [],          # 全空列名
        "single": {},         # 列名 -> (唯一值, 覆盖行数)
        "low": [],            # 低区分度列名
        "per_table": [],      # (表, 列数, 行数, 全空, 单值, 低)
        "blank": [],          # (列名, 空串数)
        "null_of": {},        # 列名 -> NULL 单元数
        "blank_of": {},       # 列名 -> 空串单元数
        "tbl_null": {},       # 表 -> NULL 单元数
        "tbl_blank": {},      # 表 -> 空串单元数
        "tbl_rows": {},
        "tbl_cols": {},
    }
    for t in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        tn = tb = 0
        e = s = low = 0
        for c in cols:
            key = f"{t}.{c}"
            nn = con.execute(
                f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NULL').fetchone()[0]
            nb = con.execute(
                f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" = \'\'').fetchone()[0]
            m["null_of"][key], m["blank_of"][key] = nn, nb
            tn += nn
            tb += nb
            if nb:
                m["blank"].append((key, nb))
            notnull = n - nn
            if notnull == 0:
                e += 1
                m["empty"].append(key)
                continue
            nd = con.execute(f'SELECT COUNT(DISTINCT "{c}") FROM "{t}"').fetchone()[0]
            if nd == 1:
                v = con.execute(f'SELECT "{c}" FROM "{t}" LIMIT 1').fetchone()[0]
                s += 1
                m["single"][key] = (str(v), notnull)
            elif nd <= 3:
                low += 1
                m["low"].append(key)
        m["per_table"].append((t, len(cols), n, e, s, low))
        m["tbl_null"][t], m["tbl_blank"][t] = tn, tb
        m["tbl_rows"][t], m["tbl_cols"][t] = n, len(cols)

    m["n_cols"] = sum(x[1] for x in m["per_table"])
    m["blank_total"] = sum(b for _, b in m["blank"])
    m["blank_sorted"] = sorted(m["blank"], key=lambda x: -x[1])

    # accidents 非 NULL 单元总数（0.90% 的分母）
    tot = 0
    for c, _ in [(r[1], r[2]) for r in con.execute("PRAGMA table_info(accidents)")]:
        tot += con.execute(
            f'SELECT COUNT(*) FROM accidents WHERE "{c}" IS NOT NULL').fetchone()[0]
    m["acc_notnull"] = tot

    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    N = m["tbl_rows"]["accidents"]
    m["N"] = N
    # 口径三连
    m["pub_notnull"] = one("SELECT COUNT(*) FROM accidents WHERE publish_date IS NOT NULL")
    m["pub_real"] = one(
        "SELECT COUNT(*) FROM accidents WHERE publish_date IS NOT NULL AND publish_date <> ''")
    m["pub_blank"] = one("SELECT COUNT(*) FROM accidents WHERE publish_date = ''")
    m["pub_src"] = con.execute(
        "SELECT source_name, SUM(CASE WHEN publish_date='' THEN 1 ELSE 0 END), COUNT(*) "
        "FROM accidents GROUP BY 1 HAVING SUM(CASE WHEN publish_date='' THEN 1 ELSE 0 END) > 0 "
        "ORDER BY 2 DESC").fetchall()
    m["wx_notnull"] = one("SELECT COUNT(*) FROM accidents WHERE weather IS NOT NULL")
    m["wx_unmentioned"] = one(
        "SELECT COUNT(*) FROM accidents WHERE weather = '未提及'")
    m["fh_notnull"] = one(
        "SELECT COUNT(*) FROM accidents WHERE fall_height IS NOT NULL AND fall_height <> ''")
    m["fh_pos"] = one(
        "SELECT COUNT(*) FROM accidents WHERE CAST(fall_height AS REAL) > 0")
    m["tc_notnull"] = one(
        "SELECT COUNT(*) FROM accidents WHERE trapped_count IS NOT NULL AND trapped_count <> ''")
    m["tc_pos"] = one(
        "SELECT COUNT(*) FROM accidents WHERE CAST(trapped_count AS INTEGER) > 0")
    # 配套发现
    m["sc_values"] = con.execute(
        "SELECT source_count, COUNT(*) FROM accidents GROUP BY 1").fetchall()
    m["su_rows"] = one("SELECT COUNT(*) FROM source_urls")
    m["su_ids"] = one("SELECT COUNT(DISTINCT accident_id) FROM source_urls")
    m["pl_rows"] = one("SELECT COUNT(*) FROM processing_log")
    m["pl_ids"] = one("SELECT COUNT(DISTINCT accident_id) FROM processing_log")
    m["vlm_vals"] = con.execute(
        "SELECT vlm_model, COUNT(*) FROM image_analyses GROUP BY 1").fetchall()
    m["vlm_default"] = [
        r[4] for r in con.execute("PRAGMA table_info(image_analyses)")
        if r[1] == "vlm_model"][0]
    m["p2_conf_blank"] = one(
        "SELECT COUNT(*) FROM image_analyses WHERE phase2_confidence = ''")
    m["p2_cons_blank"] = one(
        "SELECT COUNT(*) FROM image_analyses WHERE phase2_consistency = ''")
    m["p2_conf_ok"] = one(
        "SELECT COUNT(*) FROM image_analyses WHERE phase2_confidence <> ''")
    m["p2_cons_ok"] = one(
        "SELECT COUNT(*) FROM image_analyses WHERE phase2_consistency <> ''")
    con.close()
    return m


# ---------------------------------------------------------------------------
# A. 列分类计数 + 自证已知三项
# ---------------------------------------------------------------------------
def part_a(m: dict) -> None:
    print("\nA. 列分类计数（全库 133 列）")
    print("-" * 74)
    ok(len(m["empty"]) == 2, "全空列 2", str(m["empty"]))
    ok(len(m["single"]) == 14, "单值列 14", f"实 {len(m['single'])}")
    ok(len(m["low"]) == 10, "低区分度列 10", f"实 {len(m['low'])}")
    ok(m["n_cols"] == 133 and len(m["tables"]) == 9,
       "9 表 133 列", f"{len(m['tables'])} 表 {m['n_cols']} 列")
    ok(m["n_cols"] - 2 - 14 - 10 == 107, "其余 107", str(m["n_cols"] - 26))

    print("  —— 自证：脚本能不能重新发现已知的三项？")
    ok("processing_log.error_message" in m["empty"],
       "自证 1：processing_log.error_message 全空（§2.5.64）")
    ok("search_logs.error" in m["empty"],
       "自证 2：search_logs.error 全空（§2.5.64）")
    v = m["single"].get("accidents.pipeline_version")
    ok(v is not None and v[0] == "caics_v1.0",
       "自证 3：accidents.pipeline_version 单值 'caics_v1.0'", str(v))

    print("  —— 逐表分布：")
    for t, nc, nr, e, s, low in m["per_table"]:
        print(f"    {t:<20} 列 {nc:>3}  行 {nr:>6}  全空 {e}  单值 {s}  低 {low}")


# ---------------------------------------------------------------------------
# B. 14 个单值列
# ---------------------------------------------------------------------------
def part_b(m: dict) -> None:
    print("\nB. 14 个单值列逐个核（唯一值 + 覆盖行数）")
    print("-" * 74)
    WANT = {
        "accidents.source_count": ("1", 2623),
        "accidents.pipeline_version": ("caics_v1.0", 2623),
        "enum_definitions.is_active": ("1", 57),
        "image_analyses.source_url": ("", 282),
        "image_analyses.phase1_objects": ("", 282),
        "image_analyses.vlm_model": ("glm-5v-turbo", 282),
        "image_files.download_status": ("downloaded", 1108),
        "processing_log.pipeline_version": ("caics_v1.0", 45),
        "processing_log.text_type_confidence": ("high", 45),
        "processing_log.step1_success": ("1", 45),
        "processing_log.step2_success": ("1", 45),
        "processing_log.image_count_analyzed": ("0", 45),
        "search_logs.search_performed": ("1", 1636),
        "search_logs.engine": ("uapi", 1636),
    }
    for k, (v, n) in WANT.items():
        got = m["single"].get(k)
        ok(got == (v, n), f"{k} = {v!r} × {n}",
           f"实测 {got!r}" if got != (v, n) else "")
    ok(set(WANT) == set(m["single"]), "14 个单值列的**名单**与文档一致",
       f"多={sorted(set(m['single'])-set(WANT))} 少={sorted(set(WANT)-set(m['single']))}")


# ---------------------------------------------------------------------------
# C. 两套缺失表示
# ---------------------------------------------------------------------------
def part_c(m: dict) -> None:
    print("\nC. 两套缺失表示：表级 NULL 单元 / 空串单元")
    print("-" * 74)
    pure_null = [t for t in m["tables"] if m["tbl_null"][t] and not m["tbl_blank"][t]]
    pure_blank = [t for t in m["tables"] if m["tbl_blank"][t] and not m["tbl_null"][t]]
    ok(sorted(pure_null) == ["image_files", "processing_log", "search_logs"],
       "纯 NULL 表恰为 3 张", str(sorted(pure_null)))
    ok(sorted(pure_blank) == ["caics_causes", "image_analyses"],
       "纯空串表恰为 2 张", str(sorted(pure_blank)))
    ok(m["tbl_null"]["search_logs"] == 1636 and m["tbl_blank"]["search_logs"] == 0,
       "search_logs：NULL 1,636 / 空串 0（§2.5.64 那两列）",
       f"{m['tbl_null']['search_logs']} / {m['tbl_blank']['search_logs']}")
    ok(m["tbl_blank"]["image_analyses"] == 1068 and m["tbl_null"]["image_analyses"] == 0,
       "image_analyses：空串 1,068 / NULL 0",
       f"{m['tbl_blank']['image_analyses']} / {m['tbl_null']['image_analyses']}")

    ok(m["blank_total"] == 2055, "全库空串单元 2,055", str(m["blank_total"]))
    ok(len(m["blank"]) == 19, "含空串的列 19", str(len(m["blank"])))
    top5 = [b for _, b in m["blank_sorted"][:5]]
    ok(top5 == [907, 282, 282, 218, 212], "空串前 5 列 = 907/282/282/218/212", str(top5))
    ok(sum(top5) == 1901, "前 5 列合计 1,901", str(sum(top5)))
    ok(abs(sum(top5) / m["blank_total"] - 0.925) < 0.001,
       "前 5 列占 92.5%", f"{sum(top5)/m['blank_total']:.1%}")
    ab = m["blank_of"].get("accidents.publish_date", 0) + \
        m["blank_of"].get("accidents.accident_type", 0) + \
        m["blank_of"].get("accidents.severity", 0) + \
        m["blank_of"].get("accidents.direct_cause", 0) + \
        m["blank_of"].get("accidents.summary", 0)
    ok(ab == 922, "accidents 表空串合计 922", str(ab))
    ok(m["acc_notnull"] == 102944, "accidents 非 NULL 单元 102,944", f"{m['acc_notnull']:,}")
    ok(abs(922 / m["acc_notnull"] - 0.009) < 0.0005,
       "922 / 102,944 = 0.90%", f"{922/m['acc_notnull']:.2%}")


# ---------------------------------------------------------------------------
# D. 口径陷阱三连
# ---------------------------------------------------------------------------
def part_d(m: dict) -> None:
    print("\nD. 口径陷阱三连")
    print("-" * 74)
    N = m["N"]
    ok(m["pub_notnull"] == N, "publish_date：IS NOT NULL = 100%", f"{m['pub_notnull']}/{N}")
    ok(m["pub_real"] == 1716, "publish_date：去空串 1,716", str(m["pub_real"]))
    ok(abs(m["pub_real"] / N - 0.654) < 0.001,
       "publish_date：65.4%", f"{m['pub_real']/N:.1%}")
    ok(m["pub_blank"] == 907, "publish_date：空串 907", str(m["pub_blank"]))
    srcs = {r[0]: (r[1], r[2]) for r in m["pub_src"]}
    ok(srcs.get("建筑施工重大伤亡事故800例(樊锡仁)") == (703, 703),
       "800 例书源：703/703 全空串", str(srcs.get("建筑施工重大伤亡事故800例(樊锡仁)")))
    ok(srcs.get("beijing_em") == (12, 12), "beijing_em：12/12 全空串",
       str(srcs.get("beijing_em")))
    ok(len(m["pub_src"]) == 5, "含空串的来源恰 5 个", str(len(m["pub_src"])))

    ok(m["wx_notnull"] == 208, "weather：非空 208", str(m["wx_notnull"]))
    ok(abs(m["wx_notnull"] / N - 0.079) < 0.001, "weather：7.9%",
       f"{m['wx_notnull']/N:.1%}")
    ok(m["wx_unmentioned"] == 46, "weather：其中「未提及」46", str(m["wx_unmentioned"]))
    real = m["wx_notnull"] - m["wx_unmentioned"]
    ok(real == 162 and abs(real / N - 0.062) < 0.001,
       "weather：真实记录 162 = 6.2%", f"{real} = {real/N:.1%}")

    ok(m["fh_notnull"] == 373, "fall_height：非空 373", str(m["fh_notnull"]))
    ok(abs(m["fh_notnull"] / N - 0.142) < 0.001, "fall_height：非空率 14.2%",
       f"{m['fh_notnull']/N:.1%}")
    ok(m["fh_pos"] == 340, "fall_height：> 0 的 340", str(m["fh_pos"]))
    ok(abs(m["fh_pos"] / N - 0.130) < 0.001, "fall_height：>0 率 13.0%（= 记载值）",
       f"{m['fh_pos']/N:.1%}")
    ok(m["tc_notnull"] == N, "trapped_count：非空 2,623 = 100%", str(m["tc_notnull"]))
    ok(m["tc_pos"] == 269, "trapped_count：> 0 的 269", str(m["tc_pos"]))
    ok(abs(m["tc_pos"] / N - 0.103) < 0.001, "trapped_count：>0 率 10.3%（= 记载值）",
       f"{m['tc_pos']/N:.1%}")


# ---------------------------------------------------------------------------
# E. 四个配套发现
# ---------------------------------------------------------------------------
def part_e(m: dict) -> None:
    print("\nE. 四个配套发现")
    print("-" * 74)
    # 列可能是 INTEGER 型，取出的 1 是 int——统一转 str 再比，
    # 否则"值是 '1'"与 int 1 无法相等（这里栽过一次）
    sc = [(str(v), n) for v, n in m["sc_values"]]
    ok(sc == [("1", 2623)], "发现 1：source_count 恒 '1' × 2,623", str(sc))
    ok((m["su_rows"], m["su_ids"]) == (580, 45),
       "发现 1：source_urls 580 行只对应 45 个 accident_id",
       f"{m['su_rows']} / {m['su_ids']}")
    ok((m["pl_rows"], m["pl_ids"]) == (45, 45), "发现 2：processing_log 45 行 / 45 个 id",
       f"{m['pl_rows']} / {m['pl_ids']}")
    cov = m["pl_ids"] / m["N"]
    ok(abs(cov - 0.017) < 0.001, "发现 2：覆盖率 1.7%", f"{cov:.1%}")
    ok(m["single"]["processing_log.step1_success"] == ("1", 45)
       and m["single"]["processing_log.step2_success"] == ("1", 45),
       "发现 2：两个 success 列恒 1（幸存者偏差）")
    ok(m["vlm_default"] == '"qwen-vl-max"',
       '发现 3：表 DEFAULT = "qwen-vl-max"（双引号）', m["vlm_default"])
    ok(m["vlm_vals"] == [("glm-5v-turbo", 282)],
       "发现 3：实测全为 'glm-5v-turbo' × 282 ⇒ DEFAULT 是死代码", str(m["vlm_vals"]))
    ok(m["p2_conf_blank"] == 218 and m["p2_conf_ok"] == 64,
       "发现 4：phase2_confidence 空串 218 / 有值 64",
       f"{m['p2_conf_blank']} / {m['p2_conf_ok']}")
    ok(m["p2_cons_blank"] == 212 and m["p2_cons_ok"] == 70,
       "发现 4：phase2_consistency 空串 212 / 有值 70",
       f"{m['p2_cons_blank']} / {m['p2_cons_ok']}")
    ok(abs(218 / 282 - 0.77) < 0.006, "发现 4：218/282 = 77%", f"{218/282:.0%}")


# ---------------------------------------------------------------------------
# F. 文档 ↔ 实测
# ---------------------------------------------------------------------------
def part_f(m: dict) -> None:
    print("\nF. 文档 ↔ 实测")
    print("-" * 74)
    doc = DOC.read_text(encoding="utf-8")

    def grab(pat: str, label: str, expect: str) -> None:
        f = re.search(pat, doc)
        if not f:
            ok(False, f"文档：{label}（锚点找不到）", pat[:50])
            return
        got = f.group(1)
        ok(got == expect, f"文档：{label}", f"文档={got} 实测={expect}")

    def grab_row(pat: str, label: str, expects: list[str]) -> None:
        """抓一行里**全部**数字 token（去千分位）与实测逐列比对。
        比逐列写正则稳：少覆盖一列不会静默漏过，多覆盖一列会当场红。"""
        f = re.search(pat, doc)
        if not f:
            ok(False, f"文档：{label}（行锚点找不到）", pat[:50])
            return
        got = [t.replace(",", "") for t in re.findall(r"[\d,]+", f.group(0))]
        ok(got == expects, f"文档：{label}", f"文档={got} 实测={expects}")

    # 逐表分布表（文档该表是 **6 列：表/列数/全空/单值/低/其余**，不含行数）。
    # 锚点带**列数**——它与"缺失表示"表第 2 字段（行数）对 9 张表全不相等，
    # 故两条正则互不串味。数字模式含 `*`（文档里部分格加了粗）。
    for t, nc, nr, e, s, low in m["per_table"]:
        grab_row(r"\| `" + t + r"` \| " + str(nc) + r" \| [\d*]+ \| [\d*]+ \| "
                 r"[\d*]+ \| [\d*]+ \|",
                 f"逐表分布 {t}", [str(nc), str(e), str(s), str(low),
                                   str(nc - e - s - low)])
    grab_row(r"\| \*\*合计\*\* \| \*\*133\*\* \| \*\*2\*\* \| \*\*14\*\* \| "
             r"\*\*10\*\* \| \*\*107\*\* \|",
             "合计行 133/2/14/10/107", ["133", "2", "14", "10", "107"])

    # 单值列表抽三行（值是文本，用 grab 取覆盖数那格）
    for key in ("accidents.source_count", "image_analyses.vlm_model",
                "search_logs.engine"):
        v, n = m["single"][key]
        grab(r"\| `" + re.escape(key) + r"` \| `'" + re.escape(v) +
             r"'` \| ([\d,]+)/[\d,]+ \|", f"单值列 {key}", f"{n:,}")

    # 两套缺失表示表（第 2 字段是**行数**，带千分位）
    for t in m["tables"]:
        grab_row(r"\| `" + t + r"` \| " + f"{m['tbl_rows'][t]:,}" + r" \| \d+ \| "
                 r"[\d,]+ \| [\d,]+ \|",
                 f"缺失表示 {t}",
                 [str(m["tbl_rows"][t]), str(m["tbl_cols"][t]),
                  str(m["tbl_null"][t]), str(m["tbl_blank"][t])])

    # 空串集中度
    grab(r"全库空串单元 \*\*([\d,]+) 个\*\*", "空串总量", f"{m['blank_total']:,}")
    grab(r"前 5 列占 \*\*([\d,]+) / [\d,]+ = ([\d.]+)%\*\*", "前 5 列合计", "1,901")
    grab(r"前 5 列占 \*\*1,901 / [\d,]+ = ([\d.]+)%\*\*", "前 5 列占比", "92.5")
    grab(r"非 NULL 单元就有\s*\n\s*\*\*([\d,]+)\*\* 个",
         "accidents 非 NULL 单元", f"{m['acc_notnull']:,}")
    ok(bool(re.search(r"其余 14 列的空串都是\*\*个数位\*\*（8 / 11 / 6 / 5 / 3 / 1 …）", doc)),
       "文档：其余空串是个数位（行存在）")

    # 口径三连
    grab(r"`IS NOT NULL` 算出 \*\*([\d.]+)%\*\*", "publish_date 口径A", "100")
    grab(r"去空串算出 \*\*([\d.]+)%\*\*", "publish_date 口径B", "65.4")
    grab(r"差的 \*\*([\d,]+) 条空串\*\*", "publish_date 空串数", "907")
    grab(r"\| 建筑施工重大伤亡事故800例\(樊锡仁\) \| ([\d,]+) / [\d,]+ = 100% \|",
         "800 例书源 703/703", "703")
    grab(r"真实有天气记录的是 \*\*([\d,]+) 条 = ([\d.]+)%\*\*", "weather 真实记录", "162")
    grab(r"取值第一高频是 `'未提及'`（([\d,]+) 条）", "weather 未提及", "46")
    grab(r"\| `fall_height` \| ([\d.]+)%（[\d,]+） \| \*\*([\d.]+)%（[\d,]+）\*\* \|",
         "fall_height 非空率", "14.2")
    grab(r"\| `trapped_count` \| ([\d.]+)%（[\d,]+） \| \*\*([\d.]+)%（[\d,]+）\*\* \|",
         "trapped_count 非空率", "100")
    # 同一行里的**第二格**（>0 率）也要核——上面那条只取 group(1)，
    # 改 10.3→10.2 它照样绿：**判据必须覆盖用例改的那一格**
    grab(r"`trapped_count` \| 100%（2,623） \| \*\*([\d.]+)%（[\d,]+）\*\*",
         "trapped_count >0 率", "10.3")
    grab(r"`fall_height` \| 14\.2%（373） \| \*\*([\d.]+)%（[\d,]+）\*\*",
         "fall_height >0 率", "13.0")
    grab(r"\*\*102,944\*\* 个（其中 922 个是空串 = \*\*([\d.]+)%\*\*）",
         "accidents 空串占比 0.90%", "0.90")

    # 配套发现
    grab(r"`source_urls` 表 ([\d,]+) 行\*\*只对应 ([\d,]+) 个\*\*", "source_urls 行数", "580")
    grab(r"只覆盖 ([\d,]+)/[\d,]+ = ([\d.]+)% 的事故", "processing_log 覆盖率", "45")
    grab(r"只覆盖 45/2,623 = ([\d.]+)% 的事故", "processing_log 覆盖率%", "1.7")
    grab(r"`phase2_confidence` 空串 \*\*([\d,]+)/[\d,]+\*\*", "phase2 空串 218", "218")
    grab(r"`phase2_consistency` 空串 ([\d,]+)/[\d,]+", "phase2_consistency 空串 212", "212")


# ---------------------------------------------------------------------------
# G. 漂移自证
# ---------------------------------------------------------------------------
DRIFT = [
    # A 段：分类计数
    (r"\| \*\*合计\*\* \| \*\*133\*\* \| \*\*2\*\* \| \*\*14\*\* \| \*\*10\*\* \| \*\*107\*\* \|",
     r"| **合计** | **133** | **2** | **15** | **10** | **107** |"),
    (r"\| `accidents` \| 48 \| 0 \| 2 \| 3 \| 43 \|",
     r"| `accidents` | 48 | 0 | 2 | 3 | 44 |"),
    (r"\| `processing_log` \| 19 \| \*\*1\*\* \| \*\*5\*\* \| 3 \| 10 \|",
     r"| `processing_log` | 19 | **1** | **4** | 3 | 10 |"),
    (r"\| `search_logs` \| 9 \| \*\*1\*\* \| 2 \| 0 \| 6 \|",
     r"| `search_logs` | 9 | **1** | 2 | 0 | 7 |"),
    # B 段：单值列
    (r"\| `accidents\.source_count` \| `'1'` \| 2,623/2,623 \|",
     r"| `accidents.source_count` | `'1'` | 2,622/2,623 |"),
    (r"\| `search_logs\.engine` \| `'uapi'` \| 1,636/1,636 \|",
     r"| `search_logs.engine` | `'uapi'` | 1,637/1,636 |"),
    (r"\| `image_analyses\.vlm_model` \| `'glm-5v-turbo'` \| 282/282 \|",
     r"| `image_analyses.vlm_model` | `'glm-5v-turbo'` | 281/282 |"),
    # C 段：缺失表示
    (r"\| `search_logs` \| 1,636 \| 9 \| 1,636 \| 0 \| \*\*纯 NULL\*\* \|",
     r"| `search_logs` | 1,636 | 9 | 1,635 | 0 | **纯 NULL** |"),
    (r"\| `image_analyses` \| 282 \| 17 \| 0 \| 1,068 \| \*\*纯空串\*\* \|",
     r"| `image_analyses` | 282 | 17 | 0 | 1,067 | **纯空串** |"),
    (r"\| `accidents` \| 2,623 \| 48 \| 22,960 \| 922 \| 混合 \|",
     r"| `accidents` | 2,623 | 48 | 22,960 | 921 | 混合 |"),
    (r"全库空串单元 \*\*2,055 个\*\*", r"全库空串单元 **2,054 个**"),
    (r"前 5 列占 \*\*1,901 / 2,055 = 92\.5%\*\*", r"前 5 列占 **1,901 / 2,055 = 92.4%**"),
    (r"\*\*102,944\*\* 个（其中 922 个是空串 = \*\*0\.90%\*\*）",
     r"**102,944** 个（其中 922 个是空串 = **0.91%**）"),
    # D 段：口径三连
    (r"去空串算出 \*\*65\.4%\*\*（1,716/2,623）", r"去空串算出 **65.3%**（1,716/2,623）"),
    (r"差的 \*\*907 条空串\*\*", r"差的 **906 条空串**"),
    (r"\| 建筑施工重大伤亡事故800例\(樊锡仁\) \| 703 / 703 = 100% \|",
     r"| 建筑施工重大伤亡事故800例(樊锡仁) | 702 / 703 = 100% |"),
    (r"即真实有天气记录的是 \*\*162 条 = 6\.2%\*\*", r"即真实有天气记录的是 **161 条 = 6.2%**"),
    (r"取值第一高频是 `'未提及'`（46 条）", r"取值第一高频是 `'未提及'`（45 条）"),
    (r"\| `fall_height` \| 14\.2%（373） \| \*\*13\.0%（340）\*\* \|",
     r"| `fall_height` | 14.1%（373） | **13.0%（340）** |"),
    (r"\| `trapped_count` \| 100%（2,623） \| \*\*10\.3%（269）\*\* \|",
     r"| `trapped_count` | 100%（2,623） | **10.2%（269）** |"),
    # E 段：配套发现
    (r"`source_urls` 表 580 行\*\*只对应 45 个\*\*", r"`source_urls` 表 581 行**只对应 45 个**"),
    (r"\*\*但该表只覆盖 45/2,623 = 1\.7% 的事故\*\*", r"**但该表只覆盖 45/2,623 = 1.8% 的事故**"),
    (r"`phase2_confidence` 空串 \*\*218/282\*\*", r"`phase2_confidence` 空串 **217/282**"),
]




def drift_test() -> int:
    print("\nG. 漂移自证（往文档注入，逐个断言退出码转 1）")
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


def run() -> int:
    del fails[:]
    m = measure()
    part_a(m)
    part_b(m)
    part_c(m)
    part_d(m)
    part_e(m)
    part_f(m)
    print("\n" + "=" * 74)
    if fails:
        print(f"❌ 未通过 {len(fails)} 项：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


def main() -> int:
    print("§2.5.65 主库 133 列的语义体检 ↔ 文档")
    print("=" * 74)
    for p, what in ((DOC, "文档"), (DB, "主库")):
        if not p.exists():
            print(f"❌ {what}不在：{p}")
            return 1
    return run()


if __name__ == "__main__":
    if "--drift-test" in sys.argv:
        sys.exit(1 if drift_test() else 0)
    sys.exit(main())
