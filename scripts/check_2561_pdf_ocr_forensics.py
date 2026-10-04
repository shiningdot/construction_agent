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
#: §2.5.62 已执行归档（从 git `0983599` 取，**不是**从 fix-collectors 磁盘取，
#: 磁盘那份是旧的 `1c14cde` 版）；没有则回退 fix-collectors。
_PP_LOCAL = ROOT / "construction_agent" / "input" / "pdf_processor.py"
_PP_FALLBACK = (MAIN / ".claude" / "worktrees" / "fix-collectors" /
                "construction_agent" / "input" / "pdf_processor.py")
_PP_REL = "construction_agent/input/pdf_processor.py"

#: 该文件在 git 里的**两个版本**——§2.5.61 第七节的归因靠的就是这个差：
#:   `1c14cde`（07-22）锚点只认中文数字 ⇒ 228 MB 那本走回退分支 ⇒ 95/97 粘连；
#:   `0983599`（10-01）锚点补了阿拉伯数字分支，**但那批数据是 07-27 处理的，
#:   没赶上这次修复**（库里仍是旧版产物）。两批数据的 `crawl_date` 分别为
#:   07-25 / 07-27，都早于 10-01 —— 这是「用的是旧版」的时间证据。
GIT_VER_AT_RUN = "1c14cde"     # 产出那两批数据时的版本
GIT_VER_LATEST = "0983599"     # git 里最新版（含锚点修复）


def git_file(sha: str, rel: str) -> str:
    """从**本仓库**的 git 对象库取指定提交的文件内容；取不到返回空串。

    不静默降级：调用方须把空串判为「源不可达」。
    """
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{sha}:{rel}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""

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
    #
    # ⚠️⚠️ 但 `in` 这个判据**太弱**，分不出是哪一版——两版的锚点串**都含** `一、`
    # 子串，于是「本节第七条归因依赖旧版锚点」这件事**核不到**（§2.5.62 归档后
    # 实测：核的是新版文件、判据照样绿）。故改为核**两版各自的完整锚点行**。
    m["anchor"] = "## 一、事故简介" in src
    m["anchor_line"] = next(
        (l.strip() for l in lines if "事故简介" in l and "re.match" in l), "")
    for tag, sha in (("old", GIT_VER_AT_RUN), ("new", GIT_VER_LATEST)):
        g = git_file(sha, _PP_REL)
        m[f"git_{tag}_ok"] = bool(g)
        m[f"git_{tag}_anchor"] = next(
            (l.strip() for l in g.splitlines()
             if "事故简介" in l and "re.match" in l), "")
        m[f"git_{tag}_lines"] = len(g.splitlines()) if g else 0
        m[f"git_{tag}_bytes"] = len(g.encode("utf-8")) if g else 0
    # 本分支的树里有没有这个路径（§2.5.62 第二节「可达性断」的直接判据）
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "HEAD", _PP_REL],
                       capture_output=True, text=True)
    m["head_tree_has_input"] = bool(r.stdout.strip())
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
    # ⚠️ 回退分支「不让脚本崩」≠「通过」：§2.5.62 的归档要求就是本仓库要有它。
    # 没有这条判据，回退路径平时不执行、执行时又全绿 —— 归档哪天掉了都没人知道。
    ok(local, "归档落在本仓库 `construction_agent/input/`（§2.5.62）",
       "" if local else "**回退中：归档未落地本仓库**")
    # ⚠️ 「磁盘上有」≠「归档完成」。归档的完成定义是**进了分支**——否则下次
    # cleanup / 换 checkout 它照样不见，本节第七节的坑原样重演。故须核 `git ls-tree HEAD`。
    ok(m["head_tree_has_input"],
       "归档**已进分支**（`git ls-tree HEAD` 有它）——「落在磁盘上」不等于归档完成",
       "" if m["head_tree_has_input"] else "**尚未提交进分支（磁盘有、分支没有）**")
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
    # ---- 版本演化：第七节的归因依据（§2.5.62 起）----
    # 归档版（本仓库现在的文件）应是 git 最新版；产出那批数据的是 `1c14cde` 旧版。
    ok(m["anchor"], "归档版锚点行含 `一、`（它是 `0983599` 那版的血缘）")
    # ⚠️ 上面那条 `in` 判据两版都为真，**核不到归档版落的是哪一版**。
    # 归档的用途是「留给将来能跑的那一版」，落到旧版就白归档了 —— 故须逐字比。
    ok(m["anchor_line"] == m["git_new_anchor"],
       f"**归档版锚点与 git 最新版（{GIT_VER_LATEST}）逐字相同**"
       "——归档须落最新版，落成旧版等于把那个 bug 一起归档",
       f"归档={m['anchor_line'][:60]}…" if m["anchor_line"] != m["git_new_anchor"] else "")
    ok(m["git_old_ok"] and m["git_new_ok"],
       f"git 两版可达（{GIT_VER_AT_RUN} / {GIT_VER_LATEST}）",
       f"{m['git_old_lines']} 行 / {m['git_new_lines']} 行")
    ok("(?:" not in m["git_old_anchor"] and "[0-9]" not in m["git_old_anchor"],
       f"**{GIT_VER_AT_RUN}（产出那批数据的版本）锚点只认中文数字**"
       "——这是「228 MB 那本走回退分支 ⇒ 95/97 粘连」的唯一依据",
       m["git_old_anchor"][:96])
    ok("[0-9]+" in m["git_new_anchor"],
       f"**{GIT_VER_LATEST}（git 最新版）锚点已补阿拉伯数字分支**"
       "——修了上面那个 bug，但**那两批数据是 07-25/07-27 处理的，没赶上**",
       m["git_new_anchor"][:96])
    ok(m["git_old_anchor"] != m["git_new_anchor"],
       "两版锚点**确实不同**（否则上面两条判据会同时为真而互相抵消）")


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

    # ---- §2.5.62：归档取证那节的数字 ↔ 实测 ----
    # 定位到该节（从标题到「## 三、目标架构」之前），再在**节内**找数字——
    # 不能裸查全文，`2.5.61` 里也有 `466 行` 这类同名 token。
    s62 = doc.split("### §2.5.62", 1)[-1].split("\n## 三、目标架构", 1)[0] \
        if "### §2.5.62" in doc else ""

    def find62(pat: str, want, label: str) -> None:
        # ⚠️ 文档里的千分位逗号要去掉再转 int——`int("17,161")` 会炸。
        mm = re.search(pat, s62)
        if not mm:
            got = None
        else:
            vals = tuple(int(x.replace(",", "")) for x in mm.groups())
            got = vals[0] if len(vals) == 1 else vals
        if isinstance(want, int) and not isinstance(got, int):
            got = None
        ok(got == want, label, f"文档 {got} / 实测 {want}")

    ok(bool(s62), "文档：§2.5.62 小节存在")
    if s62:
        # 字节数只有这一处（三行并排表）；上一版另写了一条「`1c14cde` | 17,161 B」
        # 的正则去核同一格——**文档里那格没有反引号包 sha**，永远匹配不上，
        # 且与下面这条核的是同一个数字，纯冗余，删掉。
        find62(r"\| \*\*([\d,]+) B\*\* \| ([\d,]+) B \| ([\d,]+) B \|",
               (m["git_new_bytes"], m["git_old_bytes"], m["git_new_bytes"]),
               "文档：三版字节表（新 / 磁盘旧 / 归档新）")
        find62(r"`__init__\.py`（(\d+) B）与 `models\.py`（(\d+) B）",
               (380, 624), "文档：另两个文件字节数")
        # 日期：git 提交日期与主库 crawl_date 都要与文档一致
        for pat, want, label in (
            (r"`1c14cde`（旧版锚点） \| (2026-\d\d-\d\d)", "2026-07-22",
             "文档：1c14cde 提交日"),
            (r"（`0983599`） \| (2026-\d\d-\d\d)", "2026-10-01",
             "文档：0983599 提交日"),
            (r"`crawl_date` 07-25", "crawl_date 07-25", "文档：51 条处理日 = 07-25"),
            (r"`crawl_date` 07-27", "crawl_date 07-27", "文档：97 条处理日 = 07-27"),
        ):
            hit = bool(re.search(pat, s62))
            ok(hit, label, "" if hit else "**文档里找不到这句**")
        # 临时拆分件（§2.5.62 第八节）
        pp3 = MAIN / "scripts" / "process_pdf3.py"
        src3 = pp3.read_text(encoding="utf-8") if pp3.exists() else ""
        ok("/tmp/scan_part1.pdf" in src3 and "scan_part2.pdf" in src3,
           "文档：处理那 97 条的输入是 /tmp/scan_part1.pdf + scan_part2.pdf",
           "（`scripts/process_pdf3.py`，主仓库）" if src3 else "**process_pdf3.py 不在**")
        ok('mode="precision"' in src3 and "os.getenv('MINERU_API_TOKEN'" in src3,
           "文档：三个脚本自己 os.getenv 取 token、不经 settings——"
           "「settings 里没这个字段」≠「项目没有 MinerU」",
           "" if src3 else "**源不在**")
        # 「settings.MINERU_API_TOKEN 没这个字段」这半句须仍成立
        try:
            sys.path.insert(0, str(ROOT))
            from construction_agent.config import settings as _st
            ok(not hasattr(_st, "MINERU_API_TOKEN"),
               "文档：settings.MINERU_API_TOKEN **确实**没这个字段（半对的那半）")
        except Exception as e:  # noqa: BLE001
            ok(False, "文档：能 import settings 以核 MINERU 字段", str(e)[:80])
        # §2.5.59 那段（`_handle_pdf` 死代码）的补注——**在 §2.5.62 之前**，
        # 故按全文找，不走 s62 切片。
        # ⚠️ 判据串必须与 **DRIFT 注入的串**逐字一致，且**不能跨 md 换行**——
        # 文档里写的是「是两个\n> 独立的死因」，`两个独立的死因` 这个串**不存在**
        # （第一版就是这么假红的）；drift 用宽串则替换落不了地、用例静默失效。
        # 故两端都取 `独立的死因` 这一截。
        ok("独立的死因" in doc,
           "文档：§2.5.59 死代码段已补注（两条佐证是**两个独立死因**，"
           "归档只解除了其中一条）")


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
    # ---- §2.5.62 起 ----
    # 下面两条只出现在 §2.5.62，模式天然唯一；仍按上面的规矩核「替换是否落地」。
    ("三版字节表 17,234 → 17,235",
     r"(\| \*\*17,)234( B\*\* \| )", r"\g<1>235\g<2>"),
    ("51 条处理日 07-25 → 07-26",
     r"(`crawl_date` )07-25", r"\g<1>07-26"),
    # ⚠️ 模式里**不能带 `**` 前缀**：文档里 `**` 在「是两个」之前，而「独立的死因」
    # 那一行以 `> ` 开头——`(\*\*)独立的死因` 匹配不上，替换落不了地（上一版如此）。
    # 只替关键词，判据串同样只取关键词。
    ("§2.5.59 补注「独立死因」→「同一死因」",
     r"独立的死因", r"同一的死因"),
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


SRC_CASES = 4        # 源码面用例条数（与 drift_test_src 内逐一对应，末尾核对）


def drift_test_src() -> int:
    """源码面注入——**文档面（DRIFT）覆盖不到的四条**，§2.5.62 新增。

    为什么必须单独做：§2.5.62 新增的 B 段判据全部依赖「归档文件是哪一版」与
    「git 里两版各是什么形态」，而 DRIFT 只改文档、**碰不到这两样**。
    不注入就只是「报绿」，不是「报了也不会错」。
    """
    global GIT_VER_AT_RUN          # ⚠️ 必须在函数体**最前**——用例 1 里就要引用它
    print("\nE2. 源码面漂移自证（归档落错版 / 版本常量指错 / 回退分支）")
    print("=" * 74)
    bad = 0
    ran = 0        # 实际跑了多少条用例——改 `SRC_CASES` 忘了改用例，会在末尾那条判据上红
    if not _PP_LOCAL.exists():
        print(f"  ❌ 归档文件不在：{_PP_LOCAL}（§2.5.62 归档未落地，无法自证）")
        return 1
    orig = _PP_LOCAL.read_bytes()

    # --- 用例 1：把归档文件换成旧版 —— 「归档版 == git 最新版」应转红 ---
    try:
        ran += 1
        old = git_file(GIT_VER_AT_RUN, _PP_REL)
        if not old:
            print(f"  ❌ git 取不到 {GIT_VER_AT_RUN}:{_PP_REL}")
            bad += 1
        else:
            _PP_LOCAL.write_text(old, encoding="utf-8")
            rc = run()
            print(f"  {'✅' if rc == 1 else '❌'} 归档版换成 {GIT_VER_AT_RUN} 旧版"
                  f"（锚点那个 bug 一起归档） → 退出码 {rc}")
            if rc != 1:
                bad += 1
    finally:
        _PP_LOCAL.write_bytes(orig)
        if _PP_LOCAL.read_bytes() != orig:
            print("  ❌ **归档文件还原不一致**")
            bad += 1

    # --- 用例 2：版本常量指错 —— 「旧版只认中文数字」应转红 ---
    saved = GIT_VER_AT_RUN
    try:
        ran += 1
        GIT_VER_AT_RUN = GIT_VER_LATEST          # 两版都指最新 ⇒ 两版无差
        rc = run()
        print(f"  {'✅' if rc == 1 else '❌'} GIT_VER_AT_RUN 指成最新版"
              f"（两版锚点差消失，第七节归因失去依据） → 退出码 {rc}")
        if rc != 1:
            bad += 1
    finally:
        GIT_VER_AT_RUN = saved

    # --- 用例 3：回退分支 —— 归档文件改名 ⇒ 应恰好报「归档未落地本仓库」 ---
    # ⚠️ 回退分支平时永不执行（`A if A.exists() else B` 的 B）。按 §2.5.28 的规矩
    # （`t7_selfproof`），不测它就只是一句承诺。
    hidden = _PP_LOCAL.with_suffix(".py.hidden")
    try:
        ran += 1
        _PP_LOCAL.rename(hidden)
        del fails[:]
        import io
        import contextlib
        import subprocess
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            b = measure_src()
            part_b(b)
        rc = 1 if fails else 0
        # ⚠️ 断言**红的集合**而不是「见红就算过」。归档改名会同时触发两条判据：
        #   ①「归档落在本仓库」——回退到 fix-collectors 了；
        #   ②「归档版锚点与 git 最新版逐字相同」——回退取到的是磁盘旧版。
        # 只断言「命中①且退出码为 1」的话，②哪天失灵了用例照样绿；而且标签里的
        # 「恰好」也就成了一句空话（§2.5.61 的规矩：判据的强度不超过覆盖范围）。
        want = ["归档落在本仓库", "归档版锚点与 git 最新版"]
        got = {w: any(w in f for f in fails) for w in want}
        ok_all = rc == 1 and all(got.values()) and len(fails) == len(want)
        print(f"  {'✅' if ok_all else '❌'} 归档文件改名 ⇒ 回退 fix-collectors"
              f"，且**恰好**报「归档未落地」+「锚点是旧版」两条"
              f" → 退出码 {rc}，命中={got}，红 {len(fails)} 条"
              f"（预期 {len(want)}）" + ("" if ok_all else f"｜实际：{fails}"))
        if not ok_all:
            bad += 1
    finally:
        hidden.rename(_PP_LOCAL)

    # --- 用例 4：归档未进分支（`HEAD~1` 就是这个状态，**不用伪造**）---
    # 「磁盘有、分支没有」不是假想状态：归档提交的那一次之前，工作区就已经有文件了。
    # 拿历史提交当对照，比人工构造可信。
    ran += 1
    import io
    import contextlib
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "HEAD~1", _PP_REL],
                       capture_output=True, text=True)
    prev_has = bool(r.stdout.strip())
    del fails[:]
    m = measure_src()
    m["head_tree_has_input"] = False        # 唯一注入点
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        part_b(m)
    hit = any("已进分支" in f for f in fails)
    rc = 1 if fails else 0
    print(f"  {'✅' if (not prev_has and rc == 1 and hit) else '❌'} "
          f"归档未进分支（`HEAD~1` 实测无此路径 = {not prev_has}）"
          f" ⇒ 判据转红 → 退出码 {rc}，命中={hit}")
    if not (not prev_has and rc == 1 and hit):
        bad += 1

    total = SRC_CASES
    # ⚠️ 这里的 `bad` 才是返回值——`ok()` 只往 `fails` 里记，**不影响退出码**。
    # 故条数核对必须自己 `bad += 1`，否则「改常量忘改用例」这句承诺是空的。
    if ran != total:
        bad += 1
        print(f"  ❌ 源码面用例条数与常量不符（实跑 {ran} 条，`SRC_CASES`={total}）"
              "——**改常量忘改用例会在这里红**")
    ok(bad == 0, f"源码面用例全部被捕获（{total - bad}/{total}）")
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
        rc_doc = drift_test()
        rc_src = drift_test_src()
        print("\n" + "=" * 74)
        if rc_doc or rc_src:
            print(f"❌ 漂移自证未全过：文档面 {len(DRIFT) - rc_doc}/{len(DRIFT)}，"
                  f"源码面 {SRC_CASES - rc_src}/{SRC_CASES}")
            sys.exit(1)
        print(f"✅ 漂移用例全部被捕获（文档面 {len(DRIFT)}/{len(DRIFT)} + "
              f"源码面 {SRC_CASES}/{SRC_CASES} = {len(DRIFT) + SRC_CASES} 条）")
        sys.exit(0)
    sys.exit(main())
