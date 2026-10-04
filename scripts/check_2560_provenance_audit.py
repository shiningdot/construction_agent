#!/usr/bin/env python3
"""§2.5.60 自检：主库正文的溯源审计 ↔ 文档数字。

本节问的不是「正文写得好不好」，而是「**它从哪来、今天还取得回来吗**」。
四个量构成结论，四个都得能复算：

  A. **溯源对账**——主库 2,623 条 ↔ 原始层 `raw_accidents.json` 1,842 条，按
     `source_url` 桥接，`raw_text` 逐字比。结论三格：逐字相同 / 不同 / 原始层无对应。
  B. **书源四格 + 编码权重三格**——895 条书源在论文编码统计里占多少。这是本节
     最要紧的数字：它决定「论文的 16,323 条编码里有多少来自离线电子书」。
  C. **中点保真度**——一条**有唯一正解**的判据（`“9·24”` 的 `·` 是否还在）。
     ⚠️ 判据必须**强制分隔符存在**：第一版写成可空，`“120”`（急救电话）被当成
     `12`+``+`0` 命中，esafety 凭空多出 409 次「中点错」——分母里混进了不是日期
     的东西，整张表就都错了。脚本里这条注释是判据本身的一部分。
  D. **文档 ↔ 实测**——把 §2.5.60 的数字**定位到承载它的那一处**再比。
  E. **源码形态**——三个处理脚本的 import 行、`pdf_processor.py` 是否在本仓库、
     全仓 OCR 实现是否为 0。**「断链」这个论断成立与否，前提是这几条确实如此。**

口径：主库只读（零请求）。本节是**先验审计**，不改任何数据。
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import main_db_path, main_repo_root  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]   # **本仓库**（worktree 或主仓库）
MAIN = main_repo_root()                       # **主仓库根**：原始层与处理脚本在这儿
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
RAW = MAIN / "data" / "datasets" / "raw_accidents.json"

#: 四个书源（`source_url` 指向 `asset/ebook/` 下的本地 PDF，不是 URL）
BOOKS = ("建筑施工重大伤亡事故800例(樊锡仁)", "建筑施工安全事故案例分析(住建部扫描版)",
         "建筑施工安全事故案例分析(住建部)", "建筑施工生产安全事故案例分析(住建部)")

#: 事故名里的日期分隔符。**必须存在**（不允许为空）——见文件头 C 段的说明。
#: 允许的「正确」只有 `·`；其余（空格/句号/半角点/连字符）都算丢。
_SEPS = r"[·•▪∙‧\s.．。\-－]"
DATE = re.compile(r"[“\"'](\d{1,2})(" + _SEPS + r")(\d{1,2})[”\"']")

fails: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def num(s: str) -> int:
    return int(s.replace(",", ""))


# ------------------------------------------------------------ A/B/C. 实测
def measure() -> dict:
    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    m: dict[str, object] = {}
    main = con.execute("SELECT id, source_name, source_url, raw_text FROM accidents"
                       ).fetchall()
    m["主库条数"] = len(main)

    raw_items = json.loads(RAW.read_text(encoding="utf-8"))
    raw_items = raw_items if isinstance(raw_items, list) else list(raw_items.values())
    by_url: dict = {}
    for it in raw_items:
        if isinstance(it, dict) and it.get("source_url"):
            by_url.setdefault(it["source_url"], it)
    m["原始层条数"] = len(raw_items)

    same = diff = norowlayer = 0
    for r in main:
        it = by_url.get(r["source_url"])
        if it is None:
            norowlayer += 1
            continue
        if (r["raw_text"] or "").strip() == (it.get("content") or "").strip():
            same += 1
        else:
            diff += 1
    m["逐字相同"] = same
    m["不同"] = diff
    m["原始层无对应"] = norowlayer

    # 书源四格
    m["书源条数"] = {}
    for b in BOOKS:
        n = con.execute("SELECT COUNT(*) FROM accidents WHERE source_name=?", (b,)
                        ).fetchone()[0]
        m["书源条数"][b] = n
    m["书源合计"] = sum(m["书源条数"].values())

    # 编码权重
    tot = con.execute("SELECT COUNT(*) FROM caics_causes").fetchone()[0]
    m["编码总数"] = tot
    ph = ",".join("?" * len(BOOKS))
    book_codes = con.execute(
        "SELECT COUNT(*) FROM caics_causes c JOIN accidents a ON a.id=c.accident_id "
        f"WHERE a.source_name IN ({ph})", BOOKS).fetchone()[0]
    m["书源编码"] = book_codes
    eq = con.execute("SELECT evidence_quality q, COUNT(*) n FROM caics_causes "
                     "GROUP BY q").fetchall()
    m["证据三级"] = {r["q"]: r["n"] for r in eq}
    book_dq = con.execute(
        "SELECT COUNT(*) FROM caics_causes c JOIN accidents a ON a.id=c.accident_id "
        f"WHERE a.source_name IN ({ph}) AND c.evidence_quality='direct_quote'",
        BOOKS).fetchone()[0]
    m["书源direct"] = book_dq

    # 中点保真度（按源）
    per = defaultdict(lambda: {"ok": 0, "bad": 0, "rows_with": 0, "mixed": 0,
                               "n": 0, "lines": [], "short": []})
    for r in main:
        s = r["source_name"] or "?"
        t = r["raw_text"] or ""
        p = per[s]
        p["n"] += 1
        lines = [x for x in t.split("\n") if x.strip()]
        if lines:
            p["lines"].append(len(lines))
            p["short"].append(sum(1 for x in lines if len(x.strip()) < 6) / len(lines))
        a = b = 0
        for mm in DATE.finditer(t):
            if mm.group(2) == "·":
                a += 1
            else:
                b += 1
        p["ok"] += a
        p["bad"] += b
        if a + b:
            p["rows_with"] += 1
            if a and b:
                p["mixed"] += 1
    m["按源"] = per
    m["中点对"] = sum(p["ok"] for p in per.values())
    m["中点错"] = sum(p["bad"] for p in per.values())
    return m


def part_ab(m: dict) -> None:
    print("\nA. 溯源对账（主库 ↔ 原始层，按 source_url 桥接，逐字比）")
    print("=" * 74)
    n = m["主库条数"]
    ok(n == 2623, "主库 accidents = 2,623 条", f"实测 {n}")
    ok(m["原始层条数"] == 1842, "原始层 raw_accidents.json = 1,842 条",
       f"实测 {m['原始层条数']}")
    ok(m["逐字相同"] == 1728, "逐字相同 = 1,728", f"实测 {m['逐字相同']}")
    ok(m["不同"] == 0, "**不同 = 0**（主库正文未被中途改过）", f"实测 {m['不同']}")
    ok(m["原始层无对应"] == 895, "原始层无对应 = 895", f"实测 {m['原始层无对应']}")
    ok(m["逐字相同"] + m["不同"] + m["原始层无对应"] == n, "三格之和 = 主库条数")

    print("\nB. 书源与它们在论文里的权重")
    print("=" * 74)
    want = {"建筑施工重大伤亡事故800例(樊锡仁)": 703,
            "建筑施工安全事故案例分析(住建部扫描版)": 97,
            "建筑施工安全事故案例分析(住建部)": 51,
            "建筑施工生产安全事故案例分析(住建部)": 44}
    for b, w in want.items():
        ok(m["书源条数"][b] == w, f"书源 {b[:22]} = {w}", f"实测 {m['书源条数'][b]}")
    ok(m["书源合计"] == 895, "书源合计 = 895", f"实测 {m['书源合计']}")
    ok(m["编码总数"] == 16323, "caics_causes 总数 = 16,323", f"实测 {m['编码总数']}")
    ok(m["书源编码"] == 6270, "书源贡献编码 = 6,270", f"实测 {m['书源编码']}")
    ok(abs(m["书源编码"] / m["编码总数"] - 0.384) < 0.001,
       "书源编码占比 = 38.4%", f"实测 {m['书源编码']/m['编码总数']:.1%}")
    ok(abs(m["书源合计"] / m["主库条数"] - 0.341) < 0.001,
       "书源事故占比 = 34.1%", f"实测 {m['书源合计']/m['主库条数']:.1%}")
    dq = m["证据三级"].get("direct_quote", 0)
    ok(dq == 14784, "direct_quote 总数 = 14,784", f"实测 {dq}")
    ok(m["书源direct"] == 6081, "书源 direct_quote = 6,081", f"实测 {m['书源direct']}")
    ok(abs(m["书源direct"] / dq - 0.411) < 0.001,
       "书源 direct_quote 占比 = 41.1%", f"实测 {m['书源direct']/dq:.1%}")

    print("\nC. 中点保真度（`“9·24”` 的 `·` 是否还在）")
    print("=" * 74)
    per = m["按源"]
    want_mid = {"esafety": (0, 348), "建筑施工安全事故案例分析(住建部)": (0, 124),
                "建筑施工安全事故案例分析(住建部扫描版)": (226, 0),
                "建筑施工生产安全事故案例分析(住建部)": (261, 2),
                "safehoo": (53, 10), "sh_em": (136, 80), "hebei_em": (58, 4)}
    for s, (ao, ab) in want_mid.items():
        p = per.get(s, {"ok": 0, "bad": 0})
        ok(p["ok"] == ao and p["bad"] == ab,
           f"中点 {s[:26]} = {ao}对/{ab}错", f"实测 {p['ok']}对/{p['bad']}错")
    ok(m["中点对"] == 828 and m["中点错"] == 574,
       f"全库中点 = 828 对 / 574 错（首版探针漏了条数 <3 的源，走 803）",
       f"实测 {m['中点对']} 对 / {m['中点错']} 错")
    # 800例(樊锡仁)：判据在这个源上**分母为 0**，保真度不可测（文档记 `—`）
    p800 = per.get("建筑施工重大伤亡事故800例(樊锡仁)", {"ok": 0, "bad": 0})
    ok(p800["ok"] == 0 and p800["bad"] == 0,
       "800例(樊锡仁) 中点模式命中 0 次（分母为 0，保真记 `—` 不是 100%）",
       f"实测 {p800['ok']}对/{p800['bad']}错")
    # 「整批统一错」的形态：esafety 含该模式的记录里没有一条是部分对部分错
    pe = per.get("esafety", {"rows_with": 0, "mixed": 0})
    ok(pe["mixed"] == 0 and pe["rows_with"] == 284,
       "esafety 284 条含该模式的记录里「部分对部分错」= 0（批处理指纹）",
       f"实测 {pe['mixed']}/{pe['rows_with']}")
    ps = per.get("sh_em", {"rows_with": 0, "mixed": 0})
    ok(ps["mixed"] == 11 and ps["rows_with"] == 31,
       "sh_em 31 条里 11 条「部分对部分错」（逐条不一致，对照）",
       f"实测 {ps['mixed']}/{ps['rows_with']}")
    # 画像：行数中位与短行率
    for s, (lm, sm) in (("esafety", (182, 0.257)), ("safehoo", (11, 0.200)),
                        ("建筑施工重大伤亡事故800例(樊锡仁)", (10, 0.0)),
                        ("建筑施工安全事故案例分析(住建部扫描版)", (23, 0.0))):
        p = per.get(s, {"lines": [], "short": []})
        gl = st.median(p["lines"]) if p["lines"] else -1
        gs = st.median(p["short"]) if p["short"] else -1
        ok(gl == lm and abs(gs - sm) < 0.001,
           f"画像 {s[:26]} 行数中位 {lm} / 短行率 {sm:.1%}",
           f"实测 {gl:.0f} / {gs:.1%}")


# ------------------------------------------------------------ D. 源码形态
def part_d() -> None:
    print("\nD. 源码形态（读代码，不读注释）")
    print("=" * 74)
    scripts = ["process_pdf.py", "process_pdf3.py", "process_ebook800.py"]
    IMP = re.compile(r"from\s+construction_agent\.input\.pdf_processor\s+import\s+"
                     r"PDFProcessor")
    for s in scripts:
        p = MAIN / "scripts" / s
        if not p.exists():
            ok(False, f"{s} 在主仓库存在", f"不在 {p}")
            continue
        ok(bool(IMP.search(p.read_text(encoding="utf-8"))),
           f"{s} import PDFProcessor")

    # pdf_processor.py 在不在本仓库 / 主仓库
    here = list(ROOT.rglob("input/pdf_processor.py"))
    # ⚠️ 必须排除 `.claude/worktrees/`：fix-collectors 就在主仓库**内部**，
    # 不排除的话 `rglob` 会命中它，判据「主仓库工作副本里没有」直接假绿。
    mainc = [p for p in MAIN.rglob("input/pdf_processor.py")
             # ⚠️ `p.parts` 是**逐层**分段的，`".claude/worktrees"` 永远不会是其中一段
             # （第一版这么写，判据假绿）。按路径字符串判。
             if ".claude/worktrees" not in str(p)]
    ok(not here, "`pdf_processor.py` **不在本仓库**（故 895 条不可复现）",
       f"本仓库命中 {len(here)} 处" if here else "")
    ok(not mainc, "`pdf_processor.py` **不在主仓库工作副本**",
       f"主仓库命中 {len(mainc)} 处" if mainc else "")
    # 但须指出它在别处存在——否则这条判据会退化成「文件不存在」这种无意义的真
    fc = Path("/home/suntianhao/construction_agent/.claude/worktrees/fix-collectors/"
              "construction_agent/input/pdf_processor.py")
    ok(fc.exists(), "它只存在于 fix-collectors worktree（断链的具体位置）",
       str(fc) if fc.exists() else "**也不在 fix-collectors，须重新定位**")

    # 全仓 OCR 实现为 0
    OCR = re.compile(r"^\s*(?:import|from)\s+(pytesseract|paddleocr|easyocr|cnocr|"
                     r"rapidocr\w*)\b|\bimage_to_string\b", re.M)
    hits = []
    for base in (MAIN / "construction_agent", MAIN / "scripts"):
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if OCR.search(py.read_text(encoding="utf-8", errors="ignore")):
                hits.append(str(py.relative_to(MAIN)))
    ok(not hits, "全仓 OCR 实现 = 0（`needs_ocr` 只有标记、没有消费方）",
       f"命中 {hits}" if hits else "")

    # 文档里不许把推断写成已查明
    doc = DOC.read_text(encoding="utf-8")
    ok("**不能证的一条**" in doc, "文档分列「能证」与「不能证」（不把猜测写成根因）")


# ------------------------------------------------------------ E. 文档 ↔ 实测
#: (说明, 定位正则, 期望值)。**必须定位到承载该数字的那一处**，不能裸查 token。
def part_e(m: dict) -> None:
    print("\nE. 文档 §2.5.60 的数字 ↔ A/B/C 的实测")
    print("=" * 74)
    doc = DOC.read_text(encoding="utf-8")
    checks = [
        ("可复现性 A 逐字相同",
         r"\| \*\*A\. 逐字相同\*\* \| ([\d,]+) \| ([\d.]+)% \|",
         (m["逐字相同"], 65.9)),
        ("可复现性 B 不同",
         r"\| \*\*B\. 不同\*\* \| \*\*(\d+)\*\* \| \*\*([\d.]+)%\*\* \|", (0, 0.0)),
        ("可复现性 D 无对应",
         r"\| \*\*D\. 原始层无对应\*\* \| ([\d,]+) \| ([\d.]+)% \|",
         (m["原始层无对应"], 34.1)),
        ("原始层条数",
         r"`data/datasets/raw_accidents\.json`，\*\*([\d,]+) 条\*\*", (1842,)),
        ("主库条数", r"`accidents` 表，\*\*([\d,]+) 条\*\*", (2623,)),
        ("书源表 800例", r"\| 建筑施工重大伤亡事故800例\(樊锡仁\) \| (\d+) \|",
         (703,)),
        ("书源表 扫描版",
         r"\| 建筑施工安全事故案例分析\(住建部\*\*扫描版\*\*\) \| (\d+) \|", (97,)),
        ("书源表 案例分析",
         r"\| 建筑施工安全事故案例分析\(住建部\) \| (\d+) \|", (51,)),
        ("书源表 生产安全",
         r"\| 建筑施工生产安全事故案例分析\(住建部\) \| (\d+) \|", (44,)),
        ("权重表 事故", r"\| 事故 \| (\d+) \| 2,623 \| \*\*([\d.]+)%\*\* \|",
         (895, 34.1)),
        ("权重表 编码", r"\| CAICS 编码 \| \*\*([\d,]+)\*\* \| 16,323 \| \*\*([\d.]+)%\*\* \|",
         (6270, 38.4)),
        ("权重表 direct_quote",
         r"\| `direct_quote` \| ([\d,]+) \| 14,784 \| \*\*([\d.]+)%\*\* \|", (6081, 41.1)),
        ("中点表 esafety",
         r"\| \*\*esafety\*\* \| 389 \| \*\*(\d+)\*\* \| \*\*(\d+)\*\* \| "
         r"\*\*(\d+)%\*\* \| \*\*(\d+) / (\d+) \(0%\)\*\* \|",
         (0, 348, 0, 0, 284)),
        ("中点表 扫描版",
         r"\| 住建部扫描版 \| 97 \| \*\*(\d+)\*\* \| (\d+) \| \*\*(\d+)%\*\* \|",
         (226, 0, 100)),
        ("中点表 案例分析",
         r"\| 案例分析\(住建部\) \| 51 \| (\d+) \| (\d+) \| (\d+)% \| "
         r"(\d+) / (\d+) \(0%\) \|", (0, 124, 0, 0, 48)),
        ("中点表 生产安全",
         r"\| 生产安全\(住建部\) \| 44 \| (\d+) \| (\d+) \| (\d+)% \|", (261, 2, 99)),
        ("中点表 safehoo",
         r"\| safehoo \| 1,247 \| (\d+) \| (\d+) \| (\d+)% \|", (53, 10, 84)),
        ("中点表 sh_em", r"\| sh_em \| 31 \| (\d+) \| (\d+) \| (\d+)% \| \*\*(\d+) / (\d+) \(35%\)\*\* \|",
         (136, 80, 63, 11, 31)),
        ("中点表 全库", r"\| \*\*全库\*\* \| 2,623 \| (\d+) \| (\d+) \| \*\*(\d+)%\*\* \|",
         (828, 574, 59)),
        ("画像表 esafety 行数/短行",
         r"\| esafety \| 389 \| \*\*(\d+)\*\* \| \*\*([\d.]+)%\*\* \| \*\*(\d+)%\*\* \|",
         (182, 25.7, 0)),
        ("画像表 800例 行数/短行",
         r"\| 800例\(樊锡仁\) \| 703 \| (\d+) \| \*\*([\d.]+)%\*\* \| — \|", (10, 0.0)),
        ("画像表 扫描版 行数/短行",
         r"\| 扫描版\(住建部\) \| 97 \| (\d+) \| ([\d.]+)% \| (\d+)% \|", (23, 0.0, 100)),
        ("脚本 mtime 表 pdf",
         r"\| `scripts/process_pdf\.py` \| 2026-07-25 15:00 \|", ()),
        ("脚本 mtime 表 pdf3",
         r"\| `scripts/process_pdf3\.py` \| 2026-07-27 21:24 \|", ()),
        ("脚本 mtime 表 ebook800",
         r"\| `scripts/process_ebook800\.py` \| 2026-07-28 00:00 \|", ()),
    ]
    for label, pat, want in checks:
        mm = re.search(pat, doc)
        if not mm:
            ok(False, f"{label}：**文档里找不到该处**", pat[:60])
            continue
        if not want:
            ok(True, label)
            continue
        got = tuple(num(g) if re.fullmatch(r"[\d,]+", g) else float(g)
                    for g in mm.groups())
        ok(got == want, f"{label} = {want}", f"文档 {got}")


# ------------------------------------------------------------ F. 漂移自证
def drift_test() -> int:
    """往文档注入漂移，逐个断言退出码转 1。

    **不做这一步，「退出码 0」只说明脚本没崩，不说明它有鉴别力。**
    """
    import subprocess
    script = str(Path(__file__).resolve())

    def run() -> int:
        return subprocess.run([sys.executable, script], capture_output=True).returncode

    if run() != 0:
        print("\n❌ 基线不是 0，漂移测试无从谈起")
        return 1

    DRIFT = [
        ("可复现性 A 1,728 → 1,729",
         r"(\| \*\*A\. 逐字相同\*\* \| 1,)728( \|)", r"\g<1>729\g<2>"),
        ("无对应 895 → 896",
         r"(\| \*\*D\. 原始层无对应\*\* \| )895( \|)", r"\g<1>896\g<2>"),
        ("书源编码 6,270 → 6,271",
         r"(CAICS 编码 \| \*\*6,)270(\*\* \| 16,323)", r"\g<1>271\g<2>"),
        ("esafety 中点错 348 → 349",
         r"(\| \*\*esafety\*\* \| 389 \| \*\*0\*\* \| \*\*)348(\*\* \|)", r"\g<1>349\g<2>"),
        ("扫描版中点 226 → 227",
         r"(\| 住建部扫描版 \| 97 \| \*\*)226(\*\* \| 0 \|)", r"\g<1>227\g<2>"),
        ("全库中点 828 → 829",
         r"(\| \*\*全库\*\* \| 2,623 \| 8)28( \|)", r"\g<1>29\g<2>"),
        ("画像 esafety 行数 182 → 183",
         r"(\| esafety \| 389 \| \*\*)182(\*\* \| \*\*25\.7%\*\*)", r"\g<1>183\g<2>"),
        ("800例分母为 0 的描述：— → 100%",
         r"(\| 800例\(樊锡仁\) \| 703 \| 10 \| \*\*0\.0%\*\* \| )—( \|)",
         r"\g<1>100%\g<2>"),
        # ⚠️ 分组要切在**数字中间**（`**2,` | `623 条**`）。第一版切在 `**2,` | `623** 条**`，
        # 多带一个 `**`，模式与文档不符、替换静默落空——**用例失效而不是报错**。
        ("主库条数 2,623 → 2,624", r"(`accidents` 表，\*\*2,)623( 条\*\*)",
         r"\g<1>624\g<2>"),
    ]
    print("\nF. 漂移自证（往文档注入，逐个断言退出码转 1）")
    print("=" * 74)
    orig = DOC.read_text(encoding="utf-8")
    bad = 0
    try:
        for label, pat, repl in DRIFT:
            new = re.sub(pat, repl, orig, count=1)
            if new == orig:
                print(f"  ❌ {label}：**替换没落地**，用例静默失效")
                bad += 1
                continue
            DOC.write_text(new, encoding="utf-8")
            rc = run()
            print(f"  {'✅' if rc == 1 else '❌'} {label} → 退出码 {rc}")
            if rc != 1:
                bad += 1
    finally:
        DOC.write_text(orig, encoding="utf-8")
        if DOC.read_text(encoding="utf-8") != orig:
            print("  ❌ **还原不一致**")
            bad += 1
    ok(bad == 0, f"漂移用例全部被捕获（{len(DRIFT) - bad}/{len(DRIFT)}）")
    return bad


def main() -> int:
    print("§2.5.60 主库正文溯源审计：可复现性 + 书源权重 + 中点保真 ↔ 文档")
    print("=" * 74)
    if not DOC.exists():
        print(f"❌ 文档不在：{DOC}")
        return 1
    if not RAW.exists():
        print(f"❌ 原始层不在：{RAW}（本节的分母之一，缺则无法审计）")
        return 1
    m = measure()
    part_ab(m)
    part_d()
    part_e(m)
    print("\n" + "=" * 74)
    if fails:
        print(f"❌ 共 {len(fails)} 项不符：")
        for f in fails:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    if "--drift-test" in sys.argv:
        sys.exit(drift_test())
    sys.exit(main())
