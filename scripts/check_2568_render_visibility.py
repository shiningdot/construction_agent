#!/usr/bin/env python3
"""§2.5.68 自检：`raw_text` 的渲染可见性——从「前端读了」到「用户看得见」。

来路：§2.5.67 给 `DETAIL_NOT_SHOWN` 的两条例外各配了一条可证伪判据，其中
`raw_text` 那条查的是**静态文件里有没有 `raw_text` 这个名字**。那只能证明
"前端读了"，证不到"用户看得见"——判据边界当时已明写，本节就是去补这一格。

五段：
  A. **五层可见性判据的强弱实测**——同一页面、同一条记录，折叠态与展开态各测
     一遍五条判据。`querySelector(...) !== null` / `offsetParent !== null` /
     `clientHeight > 0` 三条**在折叠态全部成立**（假绿），只有 `checkVisibility()`
     与 `body.innerText` 包含性能分辨。
  B. **核查覆盖率的缺口量化**——六个页面各有几个 `<details>`、首屏可见多少字、
     全展开多少字。此前 `screenshot_web.py` 的"文字 ≥ 200 字"判据全跑在首屏上。
  C. **判据在位 + 基线跑通**——`screenshot_web.py` 里那几条新判据的源码在位，
     且完整跑一遍退出码 0。另加一条**行为性**判据：`--out` 的默认落点必须是
     `docs/web/figures`（web 核查产物），不是 `docs/paper/figures`（V1 归档论文
     插图目录）——见 §2.5.71。
  D. **注入式自证**——往真实源码注入缺陷，逐个断言**红的恰好是哪几条**（不是
     "见红就算过"）。三个面在 `static/accident.js`（跑真实截图读 `MARKS`），
     一个面在 `screenshot_web.py` 自己（`--out` 默认值，**只重解析源码、不跑
     脚本**——跑它就会真往那个目录写图，正是该判据要防的事）。
  E. 文档 ↔ 实测。

口径：起本地服务 + 无头 Chromium；除 D 段注入（`try/finally` 恢复）外不写文件。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root          # noqa: E402
from _serve import point_at, serve                        # noqa: E402

DOC = ROOT / "docs" / "web" / "01_在线采集.md"
SHOT = ROOT / "scripts" / "screenshot_web.py"
BUILD = ROOT / "scripts" / "build_v2_docs.py"
JS = ROOT / "construction_agent" / "web" / "static" / "accident.js"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
PORT = 5128

FAILS: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    """记一条判据。**只往 FAILS 里记，不改变返回值**（§2.5.62 教训）。"""
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


#: 五条判据同测一遍。前三条是"看起来合理"的写法，后两条才分得清折叠。
FIVE = """() => {
  const pre = document.querySelector('pre.raw-text');
  if (!pre) return null;
  const t = pre.textContent;
  return {
    q_nonnull:   true,                                  // querySelector 命中
    offset_ok:   pre.offsetParent !== null,
    client_h:    pre.clientHeight,
    visible:     pre.checkVisibility(),
    in_text:     document.body.innerText.includes(t.slice(0, 40)),
    len:         t.length,
    head:        t.slice(0, 40),
  };
}"""

#: 某页的折叠账：几个 details、几个闭合、首屏与全展开各多少字。
FOLD = """() => {
  const ds = Array.from(document.querySelectorAll('details'));
  return { n: ds.length, closed: ds.filter(d => !d.open).length,
           text: document.body.innerText.trim().length };
}"""


def _sample(main_db: str) -> tuple[str, str]:
    """从 `image_manifest` 挑「有图 + 有编码」的事故（与截图脚本同一口径）。

    挑有图的：这样 A 段测可见性时页面元素最全（12 个折叠），顺带覆盖图片面板。
    """
    import sqlite3
    staging = main_repo_root() / "data" / "caiems_staging.db"
    con_s = sqlite3.connect(f"file:{staging}?mode=ro", uri=True)
    try:
        with_img = [r[0] for r in con_s.execute(
            "SELECT accident_id FROM image_manifest GROUP BY accident_id "
            "ORDER BY COUNT(*) DESC")][:40]
    finally:
        con_s.close()
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    try:
        for aid in with_img:
            r = con.execute(
                "SELECT title, LENGTH(raw_text) FROM accidents WHERE id = ?",
                (aid,)).fetchone()
            if not r:
                continue
            n = con.execute("SELECT COUNT(*) FROM caics_causes WHERE accident_id = ?",
                            (aid,)).fetchone()[0]
            if n and r[1]:
                return aid, r[0] or ""
    finally:
        con.close()
    raise SystemExit("挑不出「有图 + 有编码 + 有 raw_text」的样本")


# ────────────────── A + B 段：可见性判据 + 覆盖率 ─────────────────
def parts_ab(main_db: str, staging: str) -> dict:
    from playwright.sync_api import sync_playwright

    aid, title = _sample(main_db)
    print(f"\n[A] 五层可见性判据的强弱（样本 {aid}「{title[:22]}…」）")
    server, base = serve(PORT)
    cov: dict[str, dict] = {}
    five_before = five_after = None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()

            pg.goto(f"{base}/accident/{aid}", wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(1800)
            five_before = pg.evaluate(FIVE)
            pg.evaluate("() => document.querySelectorAll('details')"
                        ".forEach(d => d.open = true)")
            pg.wait_for_timeout(900)
            five_after = pg.evaluate(FIVE)

            # B 段：六页的折叠账
            print("\n[B] 核查覆盖率：首屏 vs 全展开")
            fold_map: dict[str, dict] = {}
            for route, name in [("/", "01_概览"), ("/collect", "02_采集"),
                                ("/extract", "03_提取"), ("/review", "04_审核"),
                                ("/causes", "05_致因分析"),
                                (f"/accident/{aid}", "06_详情")]:
                pg.goto(base + route, wait_until="networkidle", timeout=30000)
                pg.wait_for_timeout(1500)
                f0 = pg.evaluate(FOLD)
                if f0["closed"]:
                    pg.evaluate("() => document.querySelectorAll('details')"
                                ".forEach(d => d.open = true)")
                    pg.wait_for_timeout(900)
                    f0["text_full"] = pg.evaluate(
                        "() => document.body.innerText.trim().length")
                else:
                    f0["text_full"] = f0["text"]
                fold_map[name] = f0
                pct = 100.0 * f0["text"] / f0["text_full"] if f0["text_full"] else 100.0
                print(f"    {name:<10} details {f0['n']:>2}（闭合 {f0['closed']:>2}）"
                      f"  首屏 {f0['text']:>5} 字 → 全展开 {f0['text_full']:>5} 字"
                      f"  （首屏只覆盖 {pct:.1f}%）")
            cov = fold_map
            b.close()
    finally:
        server.shutdown()

    assert five_before and five_after

    # 三条弱判据在折叠态就成立 ⇒ 分辨不出"看不见"
    weak = {
        "querySelector(...) !== null": bool(five_before["q_nonnull"]),
        "pre.offsetParent !== null": bool(five_before["offset_ok"]),
        "pre.clientHeight > 0": five_before["client_h"] > 0,
    }
    strong = {
        "pre.checkVisibility()": (five_before["visible"], five_after["visible"]),
        "body.innerText 含 pre 开头": (five_before["in_text"], five_after["in_text"]),
    }
    for name, val in weak.items():
        ok(val, f"弱判据「{name}」**折叠态也成立**（{val}）——分辨不了",
           "它没能落进「假绿」那格，本节结论不成立")
    for name, (b4, af) in strong.items():
        ok(b4 is False and af is True,
           f"强判据「{name}」折叠 {b4} → 展开 {af}", f"实测 {b4} → {af}")

    detail = cov["06_详情"]
    ok(detail["n"] > 0 and detail["closed"] > 0,
       f"详情页实测 {detail['n']} 个 `<details>`、{detail['closed']} 个默认闭合",
       f"实测 {detail}")
    ok(detail["text_full"] > detail["text"],
       f"详情页首屏 {detail['text']} 字 → 全展开 {detail['text_full']} 字",
       f"实测 {detail}")

    return {"aid": aid, "title": title, "five_before": five_before,
            "five_after": five_after, "cov": cov}


# ────────────────────── C 段：判据在位 + 基线 ──────────────────────
def default_out_expr() -> str:
    """取 `--out` 那一条 `add_argument` 的 default 实参原文。

    **不跑脚本**、只重解析源码——跑它就会真往那个目录写 8 张 PNG，而"别往那儿写"
    正是这条判据要防的事。解析失败返回空串，判据因此报红而**不会静默通过**。
    """
    src = SHOT.read_text(encoding="utf-8")
    m = re.search(r'ap\.add_argument\(\s*"--out"\s*,\s*default=([^,]+),', src)
    return m.group(1).strip() if m else ""


def default_out_is_web() -> bool:
    """默认落点是 web 核查产物目录，不是论文插图目录。

    两半都要：**出现 `web`** 且 **不出现 `paper`**。只查前者的话，把默认值改成
    `docs/web/../paper/figures` 这种写法照样通过；只查后者的话，指到 `docs/x/figures`
    也放行。判据收窄的是那条 `add_argument` 的参数（§2.5.66：收窄接收者）。
    """
    expr = default_out_expr()
    return "web" in expr and "paper" not in expr


def figdir_expr() -> str:
    """取 `build_v2_docs.py` 里 `FIGDIR` 赋值的实参原文。"""
    m = re.search(r"FIGDIR\s*=\s*str\(([^)]*)\)",
                  BUILD.read_text(encoding="utf-8"))
    return m.group(1).strip() if m else ""


def figdir_is_v2() -> bool:
    """论文构建链读的是 V2 插图目录（`docs/paper_v2/figures`）。

    §2.5.71 整节的论证建立在这一点上——"往 `docs/paper/figures/` 写再多也进不了
    V2 论文"。这格一旦变（`FIGDIR` 改指别处），那节的结论就要重新判。
    核的是 `FIGDIR` 的**实参**，不是裸搜 `figures`（§2.5.63：裸子串在混合语料里
    制造假阳性——那个文件里到处是 `figures`）。
    """
    return "'paper_v2'" in figdir_expr()


def part_c() -> str:
    print("\n[C] `screenshot_web.py` 的判据在位 + 基线跑通")
    src = SHOT.read_text(encoding="utf-8")
    for needle, label in [
        ("def _fold_state(", "折叠态量测函数 `_fold_state`"),
        ("def _expand_all(", "展开函数 `_expand_all`"),
        ("def _raw_text_state(", "原文可见性函数 `_raw_text_state`"),
        ("checkVisibility()", "判据用了 `checkVisibility()`"),
        ("stalled", "「闭合折叠展开后必须变长」的判据"),
        ("MARKS ", "机器可读的判据汇总行"),
    ]:
        ok(needle in src, f"源码在位：{label}")

    # 默认输出目录：web 核查产物 ↔ 论文插图，两个类别不该共用落点。
    # 判据是**行为性**的（解析 default 实参），不是"文件里出现过 web 这个词"。
    ok(default_out_is_web(),
       "`--out` 默认落在 `docs/web/figures`（web 核查产物），"
       "不是 `docs/paper/figures`（V1 论文插图）",
       f"实测 default={default_out_expr() or '锚点未命中'}")

    rc, out = _run_shot()
    ok(rc == 0, f"`screenshot_web.py` 基线退出码 0（实为 {rc}）", out[-300:])
    marks = _marks(out)
    ok(all(not v for v in marks.values()),
       f"基线六页判据全空（{len(marks)} 页）", f"实测 {marks}")
    return out


# ────────────────────────── D 段：漂移自证 ──────────────────────────
#: 四个注入面，每例只碰一格判据。`live=True` 的跑一次真实截图、读 `MARKS` 的
#: 红集合；`live=False` 的**只重解析源码**——跑它就会真往那个目录写 8 张 PNG，
#: 而「别往那儿写」正是那一例要防的事。
#: ⚠️ 用例 1 会**连带**让「折叠 1 个没内容」转红——`pre` 被 display:none 后，
#: 它所在的那个折叠展开确实一字未增。这是判据算对了，不是归因不唯一，所以
#: 期望的**红集合**写两条而不是一条（§2.5.62：断言要精确到集合）。
DRIFT = [
    dict(label="原文不可见", target=JS, live=True,
         old="    const body = el('div', { cls: 'raw-body' }, [pre]);",
         new="    pre.style.display = 'none';\n"
             "    const body = el('div', { cls: 'raw-body' }, [pre]);",
         expect={"折叠 1 个没内容", "原文不可见"}),
    dict(label="无 raw-text", target=JS, live=True,
         old="    if (!d.raw_text || !String(d.raw_text).trim()) return null;",
         new="    return null;",
         expect={"无 raw-text"}),
    # ⚠️ 缩进是 10 空格（`el` 在 `summary` 数组里）——按 Read 的行号看着像 8 空格，
    #    第一版这么写真就锚点未命中，用例静默失效。
    dict(label="折叠没内容", target=JS, live=True,
         old="          el('span', { cls: 'tier-n', text: '来源 / 判定路径 / 模型耗时' }),\n"
             "        ]),\n"
             "        body,",
         new="          el('span', { cls: 'tier-n', text: '来源 / 判定路径 / 模型耗时' }),\n"
             "        ]),",
         expect={"折叠 1 个没内容"}),
    # ── 源码面（2026-10-05 §2.5.71 补）──
    # 上面三例都拿 `accident.js` 当注入面，§2.5.71 那两条新判据于是**没接过
    # 失败路径**——报绿只说明"没报错"，不说明"报了也不会错"（§2.5.70）。
    # 这两例补的就是它们，注入面分别是各自**被测的那个文件**。
    # `live=False`：只重解析源码。跑 `screenshot_web.py` 会真往目录里写 8 张 PNG，
    # 而「别往那儿写」正是第一例要防的事——**验证手段不能制造它要防的损害**。
    dict(label="截图默认落回论文插图目录", target=SHOT, live=False,
         probe=default_out_is_web,
         old='default=str(ROOT / "docs" / "web" / "figures")',
         new='default=str(ROOT / "docs" / "paper" / "figures")'),
    dict(label="V2 构建链改读论文插图目录", target=BUILD, live=False,
         probe=figdir_is_v2,
         old="'paper_v2' / 'figures'", new="'paper' / 'figures'"),
]


def part_d() -> int:
    print("\n[D] 注入式自证：改真实源码，断言红的恰好是哪几条")
    origs: dict[Path, str] = {}

    def _orig(p: Path) -> str:
        if p not in origs:
            origs[p] = p.read_text(encoding="utf-8")
        return origs[p]

    bad = 0
    try:
        for i, c in enumerate(DRIFT, 1):
            base, label = _orig(c["target"]), c["label"]
            if c["old"] not in base:
                print(f"    🔴 用例 {i}「{label}」锚点未命中——用例本身失效")
                bad += 1
                continue
            c["target"].write_text(base.replace(c["old"], c["new"]), encoding="utf-8")
            if c["live"]:
                rc, out = _run_shot()
                red = {m for ms in _marks(out).values() for m in ms}
                good = rc == 1 and red == c["expect"]
                got = f"退出码 {rc}，红的 {sorted(red)}"
                want = f"（期望 红的 {sorted(c['expect'])}）"
            else:
                # 静态面：被测判据必须**由绿转红**。不跑脚本——跑它会真往目录里
                # 写 8 张 PNG，正是第一例要防的事。
                probe = c["probe"]
                still = probe()
                good = not still
                got = f"`{probe.__name__}()` = {still}"
                want = "（期望注入后转假）"
            print(f"    {'✅' if good else '🔴'} 用例 {i}「{label}」→ {got}"
                  + ("" if good else f" {want}"))
            if not good:
                bad += 1
            c["target"].write_text(base, encoding="utf-8")
    finally:
        for p, text in origs.items():
            p.write_text(text, encoding="utf-8")
        ok(all(p.read_text(encoding="utf-8") == t for p, t in origs.items()),
           "注入面全部逐字恢复（" + "、".join(sorted(p.name for p in origs)) + "）")
    ok(bad == 0, f"注入用例全部按预期转红（{len(DRIFT)-bad}/{len(DRIFT)}）")
    return 0 if bad == 0 else 1


def _run_shot() -> tuple[int, str]:
    """跑一遍 `screenshot_web.py`，只要退出码与 `MARKS`。

    截图显式落到临时目录（跑完即删）——否则每次核验都会塞八张 PNG，而本脚本
    只关心判据、不看图。`screenshot_web.py` 的默认落点已于 2026-10-05 由
    `docs/paper/figures/`（V1 归档论文插图目录）改到 `docs/web/figures/`，
    见 §2.5.71；这里仍显式传 `--out` 是为了**不依赖默认值**——核验脚本的产物
    落点不该随被测脚本的默认值漂移。
    """
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([PY, str(SHOT), "--out", d],
                           capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, r.stdout


def _marks(out: str) -> dict:
    for line in out.splitlines():
        if line.startswith("MARKS "):
            return json.loads(line[len("MARKS "):])
    return {}


# ────────────────────────── E 段：文档对账 ──────────────────────────
def grab(pat: str, label: str, expect: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    m = re.search(pat, text)
    ok(bool(m) and m.group(1) == expect,
       label, f"文档={m.group(1) if m else '锚点未命中'} 期望={expect}")


def part_e() -> None:
    print("\n[E] 文档 §2.5.68 / §2.5.71 ↔ 实测")
    # ⚠️ 锚点四坑，本段一次踩中三个（§2.5.64 立，§2.5.66/§2.5.67 各犯一次）：
    #    ①**千分位**：文档数字带逗号（3,038），`(\d+)` 会在逗号前停住 ⇒ `[\d,]+`；
    #    ②**`**` 的位置**：文档是 `**首屏可见文字 988 字**`（`**` 在整句最外），
    #      写成 `首屏可见文字 \*\*(\d+) 字\*\*` 匹配不上——§2.5.67 原样犯过；
    #    ③**跨行**：`折叠态` 与 `` `clientHeight` `` 之间在 md 里有换行，须 `\s*`。
    #    「锚点未命中」与「判据报警」在输出上都是红，不逐字符 dump 分不清。
    grab(r"详情页实测 \*\*(\d+) 个 `<details>`\*\*", "正文：详情页 12 个折叠", "12")
    grab(r"\*\*首屏可见文字 ([\d,]+) 字\*\*", "正文：详情页首屏 988 字", "988")
    grab(r"\*\*全展开 ([\d,]+) 字\*\*", "正文：详情页全展开 3,038 字", "3,038")
    grab(r"折叠态\s*`clientHeight` 有 \*\*(\d+) px\*\*",
         "正文：折叠态 clientHeight 235", "235")

    # §2.5.71 整节的论证建立在 `figdir_is_v2()` 上（判据与其边界见该函数）。
    ok(figdir_is_v2(),
       "§2.5.71：`build_v2_docs.py` 的 FIGDIR 仍指向 `docs/paper_v2/figures`",
       f"实测 FIGDIR={figdir_expr() or '锚点未命中'}")


def run() -> int:
    print("=" * 74)
    print("§2.5.68 raw_text 渲染的可见性：从「读了」到「看得见」")
    print("=" * 74)
    main_db = main_db_path()
    staging = str(main_repo_root() / "data" / "caiems_staging.db")
    point_at(main_db, staging)
    parts_ab(main_db, staging)
    part_c()
    part_d()
    part_e()
    print("\n" + "=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


# ────────────────────── 漂移自证：文档面 + 源码面 ──────────────────────
#: 文档面四个锚点各改一个数。断言的是**退出码转 1**，不是"报点什么"。
DOC_DRIFT = [
    (r"\*\*首屏可见文字 988 字\*\*", r"**首屏可见文字 989 字**"),
    (r"\*\*全展开 3,038 字\*\*", r"**全展开 3,039 字**"),
    (r"\*\*12 个 `<details>`\*\*", r"**13 个 `<details>`**"),
    (r"有 \*\*235 px\*\*", r"有 **236 px**"),
]


def _run_doc_only() -> int:
    """只跑 E 段——文档面漂移每例若都要跑一遍 A/B/C，四例就是十分钟。"""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--doc-only"],
        capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode


def drift_all() -> int:
    """两个注入面都要测（§2.5.62 立的规矩）。

    只测一面会漏：文档对账的判据改坏了，源码面的用例一个都不会转红。
    """
    bad = 0
    orig = DOC.read_text(encoding="utf-8")
    print("\n[D1] 文档面：改 §2.5.68 的数字，断言退出码转 1")
    try:
        for i, (old, new) in enumerate(DOC_DRIFT, 1):
            if not re.search(old, orig):
                print(f"    🔴 用例 {i} 锚点未命中（用例本身失效）：{old[:40]}")
                bad += 1
                continue
            DOC.write_text(re.sub(old, new, orig, count=1), encoding="utf-8")
            rc = _run_doc_only()
            print(f"    {'✅' if rc == 1 else '🔴'} 用例 {i}：改「{old[:28]}…」"
                  f"→ 退出码 {rc}")
            if rc != 1:
                bad += 1
            DOC.write_text(orig, encoding="utf-8")
    finally:
        DOC.write_text(orig, encoding="utf-8")
    if DOC.read_text(encoding="utf-8") != orig:
        print("    🔴 文档未逐字恢复")
        bad += 1
    bad += part_d()
    return 0 if bad == 0 else 1


def main() -> int:
    if "--drift-test" in sys.argv:
        return drift_all()
    if "--doc-only" in sys.argv:
        part_e()
        return 1 if FAILS else 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
