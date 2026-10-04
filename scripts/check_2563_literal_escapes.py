#!/usr/bin/env python3
"""§2.5.63 自检：字面量转义残留的**全库**排查 ↔ 文档。

§2.5.36（遗留登记表 M 行）修掉站点存进 `<p>` 的字面量转义后，主库侧结论
「**主库落地 0 条**」只覆盖 4 列（`raw_text` / `title` / `summary` / `direct_cause`）。
M 行的未决清单紧接着写着「`caics_causes.evidence` 等其它列没查」——本节把它查掉。

六段：
  A. **判据串自证**——每个判据串必须逐字符是「反斜杠+字母」两字符，且**不等于**真控制
     字符。⚠️ 不是摆设：Python 里 `"\\r\\n"`（4 字符）与 `"\r\n"`（2 字符，真换行）
     差一个反斜杠，写错会让判据变成"找真换行"——它**照样跑得出结果**，只是毫无意义。
  B. **全库强判据扫描**——95 个非空文本列 × 成对序列（`\\r\\n` / `\\n\\t`）+ 行首前缀正则。
  C. **两处弱提示的归因**——`raw_text` 的 73 个 `\\t` 命中点须**全部**落在 LaTeX 命令名
     字母表内（非字母开头 0 个）；`queries_used` 的 1,636 行须**全部**可 `json.loads`
     且解析后重序列化 **0** 残留。归因不做实，等于把两个假阳性当成结论。
  D. **正文卫生**——4 处零星残留按源分布（主库侧此前从未量过）。
  E. **错误列全空**——`processing_log.error_message` / `search_logs.error` 逐行 NULL。
  F. 文档 ↔ 实测。
  G. `--drift-test`——往文档注入漂移，逐个断言退出码转 1。

口径：主库**只读**（`file:...?mode=ro`），零网络请求，不写任何数据。
"""
from __future__ import annotations

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

# ── 判据串 ──────────────────────────────────────────────────────────────────
PAIR_CRLF = "\\r\\n"        # 4 字符：反斜杠 r 反斜杠 n（**不是**真 CRLF）
PAIR_NT = "\\n\\t"          # 4 字符
SOLO = ("\\t", "\\r", "\\n")  # 2 字符，弱提示（只计数、不定性）
#: §2.5.36 的原判据形态（行首前缀），比裸子串严格
PREFIX = re.compile(r"(?m)^[ \t]*(?:\\r\\n(?:\\t)?|\\n\\t)+")
#: LaTeX 命令名白名单——`raw_text` 里 `\\t` 命中点后跟的字母串只允许是这几个
LATEX_OK = {"times", "text", "tag", "tau"}

fails: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ---------------------------------------------------------------------------
# A. 判据串自证
# ---------------------------------------------------------------------------
def part_a() -> None:
    print("\nA. 判据串自证（`\\r\\n` 是 4 字符，不是 2 字符的真换行）")
    print("-" * 74)
    alls = (PAIR_CRLF, PAIR_NT, *SOLO)
    ok(all(s.startswith("\\") for s in alls),
       "每个判据串都以反斜杠开头（是**字面量**而不是控制字符）")
    ok(all(s[i] == "\\" and s[i + 1].islower()
           for s in alls for i in range(0, len(s), 2)),
       "每个判据串都是「反斜杠+小写字母」两两成组")
    ok(PAIR_CRLF != "\r\n" and len(PAIR_CRLF) == 4,
       "`\\r\\n` 是 4 字符、且**不等于**真 CRLF", f"len={len(PAIR_CRLF)}")
    ok(PAIR_NT != "\n\t" and len(PAIR_NT) == 4,
       "`\\n\\t` 是 4 字符、且**不等于**真「换行+制表」", f"len={len(PAIR_NT)}")
    ok("\\t" != "\t" and len("\\t") == 2, "`\\t` 是 2 字符、且**不等于**真制表符")
    # 反证：真控制字符**不应**被判据串命中
    ok(PAIR_CRLF not in "\r\n" and PAIR_NT not in "\n\t",
       "反证：真控制字符里找不到字面量判据串（判据不会误伤真换行）")


# ---------------------------------------------------------------------------
# B/C/D/E. 全库实测
# ---------------------------------------------------------------------------
def measure() -> dict:
    con = conn()
    m: dict = {"db": str(DB), "db_mb": round(DB.stat().st_size / 1e6, 1)}

    tabs = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    m["n_tabs"] = len(tabs)

    n_cols = 0
    strong: list[tuple] = []          # (表, 列, 成对命中行, 前缀命中行, 样本)
    weak: list[tuple] = []            # (表, 列, {形态: 命中行}, 样本)
    n_textcols = 0
    for t in tabs:
        cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")')]
        n_cols += len(cols)
        for c in cols:
            vals = [r[0] for r in con.execute(
                f'SELECT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL '
                f"AND TYPEOF(\"{c}\")='text' AND LENGTH(\"{c}\")>0").fetchall()]
            if not vals:
                continue
            n_textcols += 1
            s1 = sum(1 for v in vals if PAIR_CRLF in v or PAIR_NT in v)
            s2 = sum(1 for v in vals if PREFIX.search(v))
            if s1 or s2:
                smp = next(v for v in vals if PAIR_CRLF in v or PAIR_NT in v)
                strong.append((t, c, s1, s2, smp[:180]))
            solos = {k: sum(1 for v in vals if k in v) for k in SOLO}
            if any(solos.values()):
                hit = {k: v for k, v in solos.items() if v}
                smp = next(v for v in vals if any(k in v for k in hit))
                weak.append((t, c, hit, smp[:180]))
    m["n_cols"] = n_cols
    m["n_textcols"] = n_textcols
    m["strong"] = strong
    m["weak"] = weak

    # ── C① raw_text 的 `\t` 归因：命中点后跟什么字母 ─────────────────────
    rows = con.execute(
        "SELECT raw_text FROM accidents WHERE INSTR(raw_text, ?) > 0",
        ("\\t",)).fetchall()
    suffix: dict[str, int] = {}
    non_alpha = 0
    for (v,) in rows:
        for mt in re.finditer(re.escape("\\t"), v):
            w = re.match(r"[A-Za-z]+", v[mt.start() + 1:])   # 从 `t` 本身开始取
            if w:
                suffix[w.group(0)] = suffix.get(w.group(0), 0) + 1
            else:
                non_alpha += 1
    m["raw_rows"] = len(rows)
    m["raw_points"] = sum(suffix.values()) + non_alpha
    m["raw_suffix"] = suffix
    m["raw_non_alpha"] = non_alpha

    # ── C② queries_used 的 `\n` 归因：JSON 层转义？ ─────────────────────
    rows = con.execute("SELECT queries_used FROM search_logs").fetchall()
    okj = resid = bad = 0
    for (v,) in rows:
        try:
            parsed = json.loads(v)
        except Exception:                    # noqa: BLE001
            bad += 1
            continue
        okj += 1
        if not isinstance(parsed, (list, dict)):
            continue
        blob = json.dumps(parsed, ensure_ascii=False)
        if PAIR_CRLF in blob or PAIR_NT in blob:
            resid += 1
    m["json_rows"], m["json_ok"], m["json_bad"], m["json_resid"] = len(rows), okj, bad, resid
    m["json_nl_hits"] = sum(1 for (v,) in rows if "\\n" in v)

    # ── D. 正文卫生（4 处）────────────────────────────────────────────
    HYG = {"amp": "&amp;", "lt": "&lt;", "zwsp": "​"}
    hygiene = {}
    for tag, col in (("raw", "raw_text"), ("enr", "search_enriched_text")):
        for k, s in HYG.items():
            hygiene[f"{tag}_{k}"] = con.execute(
                f'SELECT COUNT(*) FROM accidents WHERE INSTR("{col}", ?) > 0',
                (s,)).fetchone()[0]
    m["hygiene"] = hygiene
    m["hygiene_src"] = {}
    for key, col, s in (("raw_amp", "raw_text", "&amp;"),
                        ("raw_lt", "raw_text", "&lt;"),
                        ("enr_zwsp", "search_enriched_text", "​"),
                        ("enr_amp", "search_enriched_text", "&amp;")):
        m["hygiene_src"][key] = dict(con.execute(
            f'SELECT source_name, COUNT(*) FROM accidents WHERE INSTR("{col}", ?) > 0 '
            f"GROUP BY source_name", (s,)).fetchall())

    # ── E. 两个错误列全 NULL ──────────────────────────────────────────
    errs = {}
    for t, c in (("processing_log", "error_message"), ("search_logs", "error")):
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        nul = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NULL').fetchone()[0]
        emp = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" = \'\'').fetchone()[0]
        errs[f"{t}.{c}"] = (n, nul, emp)
    m["errs"] = errs

    con.close()
    return m


def part_b(m: dict) -> None:
    print("\nB. 全库强判据扫描（成对序列 `\\r\\n` / `\\n\\t`）")
    print("-" * 74)
    print(f"  库：{m['db']}  （只读）")
    ok(m["n_tabs"] == 9, f"业务表 {m['n_tabs']} 张（`sqlite_sequence` 不计）")
    ok(m["n_cols"] == 133, f"列总数 {m['n_cols']}")
    ok(m["n_textcols"] == 95, f"**有字符串值的列 = {m['n_textcols']}**（= 本轮覆盖范围）",
       "§2.5.36 当时只覆盖 4 列")
    ok(not m["strong"], f"**强判据命中列数 = {len(m['strong'])}**（0 才算全库清）",
       "" if not m["strong"] else f"→ {m['strong']}")
    ok(not any(s[3] for s in m["strong"]), "行首前缀正则命中 = 0",
       "" if not any(s[3] for s in m["strong"]) else "→ 前缀判据也命中了")
    # `caics_causes.evidence` 单独点名：它存证据**原文**，最可能带进脏字面量
    ev = [s for s in m["strong"] if s[0] == "caics_causes" and s[1] == "evidence"]
    ok(not ev, "**`caics_causes.evidence`（16,323 行证据原文）= 0**"
       "——它最可能带进脏字面量，却最干净")


def part_c(m: dict) -> None:
    print("\nC. 两处弱提示的归因（不停在「应该是」）")
    print("-" * 74)
    bad = [k for k in m["raw_suffix"] if k not in LATEX_OK]
    ok(m["raw_non_alpha"] == 0 and not bad,
       f"C① `raw_text` 的 {m['raw_points']} 个命中点**全部**是 LaTeX 命令名",
       f"后缀={m['raw_suffix']}  非字母开头={m['raw_non_alpha']}"
       + ("" if not bad else f"  **白名单外的命令名：{bad}**"))
    ok(m["raw_suffix"] == {"times": 59, "text": 10, "tag": 3, "tau": 1},
       "C① 后缀分布 = times 59 / text 10 / tag 3 / tau 1",
       f"实测 {m['raw_suffix']}")
    ok(m["raw_rows"] == 29, f"C① 命中行数 = {m['raw_rows']}（29）")

    ok(m["json_bad"] == 0 and m["json_ok"] == m["json_rows"],
       f"C② `queries_used` {m['json_rows']:,} 行**全部**可 `json.loads`",
       f"可解析 {m['json_ok']:,} / 不可解析 {m['json_bad']}")
    ok(m["json_resid"] == 0,
       "C② 解析后重序列化**仍含**字面量换行序列的行 = 0",
       f"实测 {m['json_resid']}——{'>0 则 `\\n` 不只是 JSON 层转义' if m['json_resid'] else '纯粹是 JSON 层转义'}")


def part_d(m: dict) -> None:
    print("\nD. 正文卫生：4 处零星残留按源分布（主库侧首次量化）")
    print("-" * 74)
    h = m["hygiene"]
    ok(h["raw_amp"] == 1, "`raw_text` 含 `&amp;` = 1 行")
    ok(h["raw_lt"] == 2, "`raw_text` 含 `&lt;` = 2 行")
    ok(h["enr_zwsp"] == 1, "`search_enriched_text` 含零宽空格 U+200B = 1 行")
    ok(h["enr_amp"] == 1, "`search_enriched_text` 含 `&amp;` = 1 行")
    ok(h["raw_zwsp"] == 0 and h["enr_lt"] == 0,
       "对照：`raw_text` 无零宽、`search_enriched_text` 无 `&lt;`")
    s = m["hygiene_src"]
    ok(s["raw_amp"] == {"safehoo": 1}, "`&amp;` 出自 safehoo（站点**双重转义** `&amp;#8226;`）",
       f"{s['raw_amp']}")
    ok(s["raw_lt"] == {"建筑施工重大伤亡事故800例(樊锡仁)": 2},
       "`&lt;` 出自 800 例书源（PDF 提取链路上数学小于号被实体化）", f"{s['raw_lt']}")
    ok(s["enr_zwsp"] == {"safehoo": 1}, "零宽出自 safehoo 的**搜索摘要**", f"{s['enr_zwsp']}")


def part_e(m: dict) -> None:
    print("\nE. 两个错误列全 NULL（管道的失败不在库里留痕）")
    print("-" * 74)
    for key, (n, nul, emp) in m["errs"].items():
        ok(nul == n and emp == 0,
           f"`{key}`：{n:,} 行**全部 NULL**（非空 0）", f"NULL {nul:,} / 空串 {emp}")


# ---------------------------------------------------------------------------
# F. 文档 ↔ 实测
# ---------------------------------------------------------------------------
def part_f(m: dict) -> None:
    print("\nF. 文档 ↔ 实测（§2.5.63）")
    print("-" * 74)
    doc = DOC.read_text(encoding="utf-8")
    ok("### §2.5.63" in doc, "文档：§2.5.63 小节存在")
    ok("`caics_causes.evidence` 等其它列没查" in doc,
       "文档：M 行那条未决**原文仍在**（本节是对它的结清，不是删掉它）")

    def grab(pat: str, label: str, actual: int, group: int = 1) -> None:
        """把文档里某一格的数字抓出来与实测比。抓不到也报错（不许静默跳过）。"""
        mt = re.search(pat, doc)
        if not mt:
            ok(False, f"文档：`{label}` 那一格**定位不到**", f"模式 {pat[:52]}")
            return
        got = int(mt.group(group).replace(",", ""))
        ok(got == actual, f"文档：`{label}` = {got}  实测 {actual}")

    grab(r"\| 业务表 \| (\d+) 张 \|", "业务表张数", m["n_tabs"])
    grab(r"\| 列总数 \| (\d+) \|", "列总数", m["n_cols"])
    grab(r"其中有字符串值的列（= 本轮覆盖范围） \| \*\*(\d+)\*\*", "覆盖列数", m["n_textcols"])
    grab(r"\| \*\*强判据命中列数\*\* \| \*\*(\d+)\*\*", "强判据命中列数", len(m["strong"]))
    grab(r"\| 行首前缀命中列数 \| \*\*(\d+)\*\*", "行首前缀命中",
         sum(1 for s in m["strong"] if s[3]))
    grab(r"\| `accidents\.raw_text` \| (\d+) 行 \|", "raw_text 命中行数", m["raw_rows"])
    grab(r"\| `search_logs\.queries_used` \| ([\d,]+) 行 \|", "queries_used 命中行数",
         m["json_nl_hits"])
    grab(r"\| 总行 \| ([\d,]+) \|", "queries_used 总行", m["json_rows"])
    grab(r"\| 可 `json\.loads` \| \*\*([\d,]+)（100%）\*\* \|", "可解析行数", m["json_ok"])
    grab(r"\| `\\times` \| (\d+)", "后缀 times", m["raw_suffix"]["times"])
    grab(r"\| `\\text` \| (\d+)", "后缀 text", m["raw_suffix"]["text"])
    grab(r"\| `\\tag` \| (\d+)", "后缀 tag", m["raw_suffix"]["tag"])
    grab(r"\| `\\tau` \| (\d+)", "后缀 tau", m["raw_suffix"]["tau"])
    grab(r"\| \*\*非字母开头\*\* \| \*\*(\d+)\*\*", "非字母开头", m["raw_non_alpha"])

    # 卫生表：三行 × 两列，逐个抓（表格里的值带 `**` 强调）
    grab(r"\| 零宽空格 U\+200B \| (\d+) \| \*\*(\d+)\*\*", "零宽 raw", m["hygiene"]["raw_zwsp"])
    grab(r"\| 零宽空格 U\+200B \| (\d+) \| \*\*(\d+)\*\*", "零宽 enriched",
         m["hygiene"]["enr_zwsp"], group=2)
    grab(r"\| `&amp;` \| \*\*(\d+)\*\* \| \*\*(\d+)\*\*", "`&amp;` raw",
         m["hygiene"]["raw_amp"])
    grab(r"\| `&amp;` \| \*\*(\d+)\*\* \| \*\*(\d+)\*\*", "`&amp;` enriched",
         m["hygiene"]["enr_amp"], group=2)
    grab(r"\| `&lt;` \| \*\*(\d+)\*\* \| (\d+)", "`&lt;` raw", m["hygiene"]["raw_lt"])

    # 错误列表：两行「行数 | NULL | 空串 | 非空」
    for key, pat in (
            ("processing_log.error_message",
             r"\| `processing_log\.error_message` \| (\d+) \| \*\*(\d+)\*\* \| \d+ \| \*\*(\d+)\*\* \|"),
            ("search_logs.error",
             r"\| `search_logs\.error` \| ([\d,]+) \| \*\*([\d,]+)\*\* \| \d+ \| \*\*(\d+)\*\* \|")):
        n, nul, nn = m["errs"][key]
        mt = re.search(pat, doc)
        if not mt:
            ok(False, f"文档：错误列 `{key}` 那一行**定位不到**")
            continue
        g = [int(x.replace(",", "")) for x in mt.groups()]
        ok(g == [n, nul, nn], f"文档：错误列 `{key}` 三格 = {g}  实测 {[n, nul, nn]}")

    ok(m["hygiene_src"]["raw_amp"] == {"safehoo": 1}
       and m["hygiene_src"]["raw_lt"] == {"建筑施工重大伤亡事故800例(樊锡仁)": 2},
       "文档：卫生溯源表两个源名与实测一致",
       f"{m['hygiene_src']['raw_amp']} / {m['hygiene_src']['raw_lt']}")
    ok("站点自己写了双重转义" in doc and "PDF 提取链路上的**数学小于号**被实体化" in doc,
       "文档：4 处残留**各有归因**（不是一句『量小可忽略』带过）")


def run() -> int:
    del fails[:]
    m = measure()
    part_a()
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


# ---------------------------------------------------------------------------
# G. 漂移自证
# ---------------------------------------------------------------------------
DRIFT = [
    (r"其中有字符串值的列（= 本轮覆盖范围） \| \*\*95\*\*",
     r"其中有字符串值的列（= 本轮覆盖范围） | **96**"),
    (r"\| 列总数 \| 133 \|", r"| 列总数 | 134 |"),
    (r"\| 业务表 \| 9 张 \|", r"| 业务表 | 10 张 |"),
    (r"\| `accidents\.raw_text` \| 29 行 \|", r"| `accidents.raw_text` | 30 行 |"),
    (r"\| `search_logs\.queries_used` \| 1,594 行 \|",
     r"| `search_logs.queries_used` | 1,595 行 |"),
    (r"\| 总行 \| 1,636 \|", r"| 总行 | 1,637 |"),
    (r"\| 可 `json\.loads` \| \*\*1,636（100%）\*\*", r"| 可 `json.loads` | **1,635（100%）**"),
    (r"\| `\\times` \| 59", r"| `\times` | 58"),
    (r"\| `\\text` \| 10", r"| `\text` | 11"),
    (r"\| `\\tag` \| 3", r"| `\tag` | 4"),
    (r"\| `\\tau` \| 1", r"| `\tau` | 2"),
    (r"\| \*\*非字母开头\*\* \| \*\*0\*\*", r"| **非字母开头** | **1**"),
    (r"\| `&amp;` \| \*\*1\*\* \| \*\*1\*\* \| 0 \|",
     r"| `&amp;` | **7** | **1** | 0 |"),
    (r"\| `&lt;` \| \*\*2\*\* \| 0 \| 0 \|", r"| `&lt;` | **8** | 0 | 0 |"),
    (r"\| 零宽空格 U\+200B \| 0 \| \*\*1\*\* \| 0 \|",
     r"| 零宽空格 U+200B | 0 | **9** | 0 |"),
    (r"\| `processing_log\.error_message` \| 45 \| \*\*45\*\* \| 0 \| \*\*0\*\* \|",
     r"| `processing_log.error_message` | 45 | **44** | 0 | **0** |"),
    (r"\| `search_logs\.error` \| 1,636 \| \*\*1,636\*\*",
     r"| `search_logs.error` | 1,636 | **1,635**"),
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


def main() -> int:
    print("§2.5.63 字面量转义残留的全库排查 ↔ 文档")
    print("=" * 74)
    if not DOC.exists():
        print(f"❌ 文档不在：{DOC}")
        return 1
    if not DB.exists():
        print(f"❌ 主库不在：{DB}")
        return 1
    return run()


if __name__ == "__main__":
    if "--drift-test" in sys.argv:
        sys.exit(1 if drift_test() else 0)
    sys.exit(main())
