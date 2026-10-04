#!/usr/bin/env python3
"""§2.5.61 自检：那 97 条正文的来历（PDF 文字层取证 + MinerU 证据链）↔ 文档。

本节答的是 §2.5.60 留下的唯一未决：**那 97 条「住建部扫描版」正文，
来自 PDF 自带文字层，还是库外的某个工具？** 答案是后者，而且那个工具
**本来就在项目里**——`construction_agent/input/pdf_processor.py`，
调用 **MinerU 云端 OCR**（`flash_extract(is_ocr=True)` /
`extract(model="vlm", ocr=True, formula=True)`），返回 markdown。

五段：
  A. **PDF 形态**——两份 PDF 的字体对象数、`get_text()` 字符数、整页图像像素。
     这是本节的地基：`0 / 188` 页有字体 ⇒ 「自带文字层」这一支被排除。
  B. **源码形态**——`pdf_processor.py` 的 MinerU 调用点与两条切分路径的行号。
     ⚠️ **双路径解析**：本仓库优先、缺则回退 fix-collectors、两者皆无 ⇒ 报
     「源不可达」。**这个文件恰恰是本节结论的唯一钥匙，而它不在主仓库**——
     所以回退分支必须显式，且缺了要报错，不能静默算通过。
  C. **主库形态**——两批书源正文的中点/标题/切分粘连计数。
  D. **文档 ↔ 实测**——把 §2.5.61 的数字**定位到承载它的那一处**再比。
  E. `--drift-test`——往文档注入漂移，逐个断言退出码转 1。

口径：两份 PDF 与主库**全部只读**，零请求，不写任何数据。
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import main_db_path, main_repo_root  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]   # **本仓库**（worktree 或主仓库）
MAIN = main_repo_root()                       # **主仓库根**：两份 PDF 在这儿
DOC = ROOT / "docs" / "web" / "01_在线采集.md"

BOOK = MAIN / "asset" / "ebook"
BIG = BOOK / "建筑施工安全事故案例分析 住房和城乡建设部工程质量安全监管司.pdf"
SMALL = BOOK / ("建筑施工安全事故案例分析 (住房和城乡建设部工程质量安全监管司组织编写) "
                "(z-library.sk, 1lib.sk, z-lib.sk).pdf")

#: 那个唯一含 OCR 的文件。**本仓库优先**——§2.5.60 裁决 2 要求归档它，
#: 归档后本仓库就有了；没有则回退 fix-collectors（它现在唯一在的地方）。
_PP_LOCAL = ROOT / "construction_agent" / "input" / "pdf_processor.py"
_PP_FALLBACK = (MAIN / ".claude" / "worktrees" / "fix-collectors" /
                "construction_agent" / "input" / "pdf_processor.py")

SCAN = "建筑施工安全事故案例分析(住建部扫描版)"
CASE = "建筑施工安全事故案例分析(住建部)"

DATE = re.compile(r"[“\"'](\d{1,2})([·•▪∙‧\s.．。\-－])(\d{1,2})[”\"']")

fails: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def pp_path() -> Path | None:
    """返回 pdf_processor.py 的路径；两处都没有则 None（不静默降级）。"""
    if _PP_LOCAL.exists():
        return _PP_LOCAL
    if _PP_FALLBACK.exists():
        return _PP_FALLBACK
    return None


# ------------------------------------------------------------ A. PDF 形态
def measure_pdf() -> dict:
    import fitz  # 局部 import：缺 PyMuPDF 时报「源不可达」而不是崩在顶部

    m: dict[str, object] = {}
    for tag, p, key in (("BIG", BIG, "scan"), ("SMALL", SMALL, "case")):
        d = fitz.open(p)
        n = d.page_count
        pages_with_font = 0
        chars = 0
        img_objs: list[int] = []
        px: list[str] = []
        for i in range(n):
            pg = d[i]
            if pg.get_fonts(full=True):
                pages_with_font += 1
            chars += len(pg.get_text().strip())
        for i in (4, 14, 24):                      # 抽样三页看图像规格
            imgs = d[i].get_images(full=True)
            img_objs.append(len(imgs))
            if imgs:
                try:
                    info = d.extract_image(imgs[0][0])
                    px.append(f"{info['width']}x{info['height']}")
                except Exception:                  # noqa: BLE001
                    px.append("?")
        m[f"{key}_size_mb"] = round(p.stat().st_size / 1e6, 1)
        m[f"{key}_pages"] = n
        m[f"{key}_mb_per_page"] = round(p.stat().st_size / n / 1e6, 2)
        m[f"{key}_pages_with_font"] = pages_with_font
        m[f"{key}_text_chars"] = chars
        m[f"{key}_img_objs"] = img_objs[0] if img_objs else 0
        m[f"{key}_px"] = px[0] if px else "?"
        d.close()
    return m


def part_a(m: dict) -> None:
    print("\nA. PDF 形态（PyMuPDF 只读）")
    print("-" * 74)
    if not BIG.exists() or not SMALL.exists():
        ok(False, "两份 PDF 在位",
           f"缺：{'' if BIG.exists() else BIG.name} {'' if SMALL.exists() else SMALL.name}")
        return
    ok(m["scan_pages"] == 188, "BIG 页数 = 188", f"实测 {m['scan_pages']}")
    ok(m["scan_pages_with_font"] == 0,
       "BIG **有字体的页 = 0 / 188**（没有文字层可提）",
       f"实测 {m['scan_pages_with_font']}")
    ok(m["scan_text_chars"] == 0, "BIG get_text 提取 0 字符",
       f"实测 {m['scan_text_chars']}")
    ok(m["scan_img_objs"] == 93, "BIG 每页图像对象 93（去重后仍 93）",
       f"实测 {m['scan_img_objs']}")
    ok(m["scan_px"] == "2174x3072", "BIG 整页图像 2174 × 3072 JPEG",
       f"实测 {m['scan_px']}")
    ok(m["scan_mb_per_page"] == 1.22, "BIG 每页 1.22 MB",
       f"实测 {m['scan_mb_per_page']}")

    ok(m["case_pages"] == 179, "SMALL 页数 = 179", f"实测 {m['case_pages']}")
    ok(m["case_pages_with_font"] == 3,
       "SMALL 有字体的页 = 3 / 179（**只有目录页**，正文页同样 0）",
       f"实测 {m['case_pages_with_font']}")
    ok(m["case_img_objs"] == 1, "SMALL 每页图像对象 1",
       f"实测 {m['case_img_objs']}")
    ok(m["case_px"] == "983x1499", "SMALL 整页图像 983 × 1499 PNG",
       f"实测 {m['case_px']}")
    ok(m["case_mb_per_page"] == 0.13, "SMALL 每页 0.13 MB",
       f"实测 {m['case_mb_per_page']}")

    # 分辨率倍数（本节第四节那个 4.5）
    try:
        w1, h1 = (int(x) for x in str(m["scan_px"]).split("x"))
        w2, h2 = (int(x) for x in str(m["case_px"]).split("x"))
        ratio = (w1 * h1) / (w2 * h2)
    except Exception:  # noqa: BLE001
        ratio = 0.0
    ok(abs(ratio - 4.5) < 0.06, "整页图像面积比 = 4.5 倍（6.7 MP vs 1.5 MP）",
       f"实测 {ratio:.2f}")


# ------------------------------------------------------------ B. 源码形态
def measure_src() -> dict:
    m: dict[str, object] = {}
    p = pp_path()
    m["pp_path"] = str(p) if p else None
    if p is None:
        return m
    lines = p.read_text(encoding="utf-8").splitlines()
    m["pp_lines"] = len(lines)

    def lineof(pat: str) -> int:
        for i, l in enumerate(lines, 1):
            if re.search(pat, l):
                return i
        return 0

    m["flash_extract"] = lineof(r"flash_extract")
    m["is_ocr"] = lineof(r"is_ocr=True")
    m["extract_vlm"] = lineof(r'model="vlm"')
    m["ocr_true"] = lineof(r"^\s+ocr=True,")
    m["formula"] = lineof(r"^\s+formula=True,")
    m["clean_markdown"] = lineof(r"def _clean_markdown")
    m["split_book"] = lineof(r"def _split_book_cases")
    m["split_into"] = lineof(r"def _split_into_accidents")
    src = "\n".join(lines)
    m["has_mineru"] = "MinerU" in src
    # ⚠️ 不能用 `^` 锚行首：这一句在源码里是**缩进的**（`re.match(r'^## 一、事故简介…`），
    # 正则的 `^` 配 `re.M` 只认行首，缩进后会落空（第一版就是这么假红的）。
    m["anchor"] = "## 一、事故简介" in src
    return m


def part_b(m: dict) -> None:
    print("\nB. 源码形态（pdf_processor.py —— 本节结论的唯一钥匙）")
    print("-" * 74)
    if m["pp_path"] is None:
        ok(False, "pdf_processor.py 可达",
           f"两处都没有：{_PP_LOCAL} / {_PP_FALLBACK}（**不静默降级**）")
        return
    local = _PP_LOCAL.exists()
    print(f"  路径：{m['pp_path']}"
          f"{'（本仓库）' if local else '（**回退 fix-collectors**——优先归档到本仓库）'}")
    ok(m["pp_lines"] == 466, "文件 466 行", f"实测 {m['pp_lines']}")
    ok(m["has_mineru"], "文件头写的是 MinerU PDF 处理器")
    ok(m["flash_extract"] and m["is_ocr"],
       "flash 模式：flash_extract(..., is_ocr=True)——就是那个 OCR",
       f"L{m['flash_extract']} / L{m['is_ocr']}")
    ok(m["extract_vlm"] and m["ocr_true"] and m["formula"],
       "precision 模式：extract(model=\"vlm\", ..., formula=True, ocr=True)",
       f"L{m['extract_vlm']} / L{m['ocr_true']} / L{m['formula']}")
    ok(250 <= m["flash_extract"] <= 270,
       "调用点落在第 251–269 行区间", f"flash_extract 在 L{m['flash_extract']}")
    ok(m["clean_markdown"] == 286, "_clean_markdown 在第 286 行",
       f"实测 L{m['clean_markdown']}")
    ok(m["split_book"] == 323, "_split_book_cases 在第 323 行",
       f"实测 L{m['split_book']}")
    ok(m["split_into"] == 404, "_split_into_accidents 在第 404 行",
       f"实测 L{m['split_into']}")
    ok(m["anchor"], "专用切分锚点 = ^## 一、事故简介\\s*$（只匹配 SMALL 那本）")


# ------------------------------------------------------------ C. 主库形态
def measure_db() -> dict:
    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    m: dict[str, object] = {}
    for tag, name, key in (("scan", SCAN, "scan"), ("case", CASE, "case")):
        rows = con.execute("SELECT raw_text FROM accidents WHERE source_name=?",
                           (name,)).fetchall()
        t = "\n".join((r["raw_text"] or "") for r in rows)
        m[f"{key}_n"] = len(rows)
        m[f"{key}_dot"] = len(re.findall(r"[“\"']\d{1,2}·\d{1,2}[”\"']", t))
        m[f"{key}_per"] = len(re.findall(r"[“\"']\d{1,2}\.\d{1,2}[”\"']", t))
        m[f"{key}_h2"] = len(re.findall(r"^## ", t, re.M))
        m[f"{key}_h1"] = len(re.findall(r"^# ", t, re.M))
        m[f"{key}_chars"] = len(t)
        m[f"{key}_latex"] = len(re.findall(r"\$[^$\n]{1,40}\$", t))
        # 尾部粘连：最后一行是「#…案例…」的
        m[f"{key}_tail"] = sum(
            1 for r in rows
            if (lambda last: last.startswith("#") and "案例" in last)(
                (r["raw_text"] or "").rstrip().split("\n")[-1].strip()))
    return m


def part_c(m: dict) -> None:
    print("\nC. 主库形态（两批书源正文）")
    print("-" * 74)
    ok(m["scan_n"] == 97 and m["case_n"] == 51, "条数 97 / 51",
       f"实测 {m['scan_n']} / {m['case_n']}")
    ok(m["scan_dot"] == 226 and m["scan_per"] == 0,
       "97 条：中点 · 226 / . 0（识别全对）",
       f"实测 {m['scan_dot']} / {m['scan_per']}")
    ok(m["case_dot"] == 0 and m["case_per"] == 124,
       "51 条：中点 · 0 / . 124（识别全错）",
       f"实测 {m['case_dot']} / {m['case_per']}")
    ok(m["scan_h2"] == 906 and m["scan_h1"] == 1,
       "97 条：'## ' 906 / '# ' 1（MinerU 的 markdown 标题）",
       f"实测 {m['scan_h2']} / {m['scan_h1']}")
    ok(m["case_h2"] == 351, "51 条：'## ' 351", f"实测 {m['case_h2']}")
    ok(m["scan_tail"] == 95, "97 条尾部粘着下一个案例标题的 95 / 97",
       f"实测 {m['scan_tail']}")
    ok(m["case_tail"] == 0, "51 条尾部粘连 0 / 51（走了专用切分分支）",
       f"实测 {m['case_tail']}")
    ok(m["case_latex"] > 0,
       "51 条含 LaTeX 数学（$…$）——formula=True 的产物，原件不可能印着 $",
       f"实测 {m['case_latex']} 处")
    ok(m["scan_chars"] and m["case_chars"], "两批正文合计字数可算",
       f"{m['scan_chars']:,} / {m['case_chars']:,}")


# ------------------------------------------------------------ D. 文档 ↔ 实测
def part_d(m: dict) -> None:
    print("\nD. 文档 ↔ 实测（定位到承载它的那一处）")
    print("-" * 74)
    doc = DOC.read_text(encoding="utf-8")
    if "### §2.5.61" not in doc:
        ok(False, "§2.5.61 在文档里")
        return
    sec = doc.split("### §2.5.61", 1)[1].split("\n### §2.5.6", 1)[0]

    def find(pat: str, want, label: str, cast=int):
        mm = re.search(pat, sec)
        if not mm:
            ok(False, label, "**文档里没找到承载它的那一处**")
            return
        got = cast(mm.group(1).replace(",", ""))
        ok(got == want, label, f"文档 {got} / 实测 {want}")

    find(r"\| 页数 \| (\d+) \| 179 \|", 188, "文档：BIG 页数 188")
    find(r"\*\*0 / (\d+)\*\* \| \*\*3 / 179\*\*", 188, "文档：字体页 0 / 188")
    find(r"\*\*(\d+) × 3072 JPEG\*\*", 2174, "文档：BIG 图像 2174 × 3072")
    find(r"\*\*983 × (\d+) PNG\*\*", 1499, "文档：SMALL 图像 983 × 1499")
    find(r"\| 每页 MB \| 1\.22 \| (\d+\.\d+) \|", 0.13, "文档：每页 MB 1.22 / 0.13",
         float)
    find(r"`·` \*\*(\d+)\*\* / `\.` 0 \| `·` \*\*0\*\*", 226, "文档：97 条中点 226")
    find(r"`·` \*\*0\*\* / `\.` \*\*(\d+)\*\*", 124, "文档：51 条中点 124")
    find(r"末段是「案例N」标题的 \| \*\*(\d+) / 97\*\*", 95, "文档：97 条粘连 95 / 97")
    find(r"（(\d+) 行）", 466, "文档：pdf_processor.py 466 行")
    find(r"\*\*(\d+) 个\*\*", 906, "文档：97 条 '## ' 906")
    find(r"\*\*(\d+) 个\*\*（`## 1\. 事故简介`", 351, "文档：51 条 '## ' 351")
    ok("**分辨率差 4.5 倍。**" in sec, "文档：分辨率差 4.5 倍")
    ok("MinerU 云端" in sec, "文档：点名 MinerU 云端 OCR")
    ok("互相掩盖" in sec, "文档：写下「两个结论互相掩盖」这条教训")


# ------------------------------------------------------------ E. 漂移自证
def run() -> int:
    """只跑 A–C + D（不打印），供 drift 用例判退出码。"""
    del fails[:]
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        a = measure_pdf()
        part_a(a)
        b = measure_src()
        part_b(b)
        c = measure_db()
        part_c(c)
        part_d({**a, **b, **c})
    return 1 if fails else 0


DRIFT = [
    ("BIG 页数 188 → 189",
     r"(\| 页数 \| )188( \| 179 \|)", r"\g<1>189\g<2>"),
    ("BIG 字体页 0 → 1（文字层结论的地基）",
     r"(\*\*)0( / 188\*\* \| \*\*3 / 179\*\*)", r"\g<1>1\g<2>"),
    ("BIG 图像像素 2174 → 2173",
     r"(\*\*)2174( × 3072 JPEG\*\*)", r"\g<1>2173\g<2>"),
    ("SMALL 图像像素 1499 → 1498",
     r"(983 × )1499( PNG\*\*)", r"\g<1>1498\g<2>"),
    ("97 条中点 226 → 227",
     r"(`·` \*\*)226(\*\* / `\.` 0 \|)", r"\g<1>227\g<2>"),
    ("51 条中点 124 → 125",
     r"(`\.` \*\*)124(\*\*)", r"\g<1>125\g<2>"),
    ("97 条粘连 95 → 94",
     r"(末段是「案例N」标题的 \| \*\*)95( / 97\*\*)", r"\g<1>94\g<2>"),
    ("pdf_processor.py 466 行 → 467",
     r"（466 行）", r"（467 行）"),
    # ⚠️ 模式必须**唯一定位到 §2.5.61**：`re.sub(count=1)` 只替换全文第一处，
    # 而 §2.5.60 第十节的结案注里也写了「差 4.5 倍的分辨率」——第一版用宽的
    # `(差 )4\.5( 倍)`，替换落在 §2.5.60、被测段分毫未动，用例静默失效（退出码 0）。
    ("分辨率 4.5 倍 → 4.4 倍",
     r"(\*\*分辨率差 )4\.5( 倍。\*\*)", r"\g<1>4.4\g<2>"),
]


def drift_test() -> int:
    print("\nE. 漂移自证（往文档注入，逐个断言退出码转 1）")
    print("=" * 74)
    if not DOC.exists():
        print(f"❌ 文档不在：{DOC}")
        return 1
    orig = DOC.read_text(encoding="utf-8")
    bad = 0
    try:
        for label, pat, repl in DRIFT:
            new = re.sub(pat, repl, orig, count=1)
            if new == orig:
                print(f"  ❌ {label}：**替换没落地**，用例静默失效")
                bad += 1
                continue
            if new.count("### §2.5.61") != orig.count("### §2.5.61"):
                print(f"  ❌ {label}：**改动了小节边界**")
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
    print("§2.5.61 那 97 条正文的来历：PDF 文字层取证 + MinerU 证据链 ↔ 文档")
    print("=" * 74)
    if not DOC.exists():
        print(f"❌ 文档不在：{DOC}")
        return 1
    if not BIG.exists() or not SMALL.exists():
        print(f"❌ 两份 PDF 不在 {BOOK}（本节的地基，缺则无法取证）")
        return 1
    try:
        import fitz  # noqa: F401
    except ImportError:
        print("❌ 缺 PyMuPDF（fitz）：本节 A 段靠它读 PDF，缺则无法取证")
        return 1
    a = measure_pdf()
    part_a(a)
    b = measure_src()
    part_b(b)
    c = measure_db()
    part_c(c)
    part_d({**a, **b, **c})
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
