#!/usr/bin/env python3
"""§2.5.69 自检：详情页的**真实点击**——`d.open = true` 与用户真点不等价。

来路：§2.5.68 把 `raw_text` 从「前端读了」核到「用户看得见」，量出详情页首屏只
覆盖 **32.5%** 的正文，其余全在 12 个 `<details>` 折叠里。但那一节的折叠判据用的是

    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")

——**程序式改 DOM 属性**，绕过了「click 事件 → 浏览器默认行为」这条真实链路。
两者不等价：只要有任何 JS 在 `summary` 上 `preventDefault()`（或 `stopPropagation`
后不转发），程序式照样把 `open` 置上、§2.5.68 照样报绿，而用户点破手指也展不开。

同一页的 `raw_text` 也还有两处**藏在交互后面**、此前没有任何判据的东西：
「展开全文」按钮（默认只渲染前 5,000 字，点它才换成全文）与图片灯箱。

三段：
  A. **同页对照实测**：一条 page 上测三格——注入前真点（开）／注入
     `preventDefault` 后真点（**不开**）／注入后程序式展开（**照样开**）。
     第三格就是 §2.5.68 那条判据的处境，它看不见这个缺陷。
  B. **判据在位 + 基线**：`check_web_interaction.py` 里四条新判据的源码在位，
     完整跑一遍退出码 0、十七条全 ✓（§2.5.70 起由 15 条增到 17 条）。
  C. **注入式自证**：往真实的 `static/accident.js` 注入四处缺陷，逐个断言
     **红的恰好是哪几条**（不是"见红就算过"，§2.5.62）。⚠️ 前三例自 §2.5.70
     起各多红一条（同一缺陷被鼠标链与键盘链同时看见），见 `EXPECT` 上方注释。
  D. 文档 ↔ 实测。

口径：起本地服务 + 无头 Chromium；除 C 段注入（`try/finally` 恢复）外不写文件。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root          # noqa: E402
from _serve import point_at, serve                        # noqa: E402

DOC = ROOT / "docs" / "web" / "01_在线采集.md"
INTER = ROOT / "scripts" / "check_web_interaction.py"
JS = ROOT / "construction_agent" / "web" / "static" / "accident.js"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
APORT = 5134          # A 段自己起的服务
IPORT = 5133          # 跑 check_web_interaction.py 时给它的端口

FAILS: list[str] = []

#: 拦截 `summary` 的点击——模拟"页面上有段 JS 把折叠的默认行为吃掉了"。
#: 用**捕获阶段**（第三参 `true`），这样它比任何冒泡阶段的 handler 都先跑。
BLOCK = """() => {
  document.addEventListener('click', function (e) {
    if (e.target && e.target.closest && e.target.closest('summary')) {
      e.preventDefault();
    }
  }, true);
}"""

#: 三格各要一条：真点能开吗、程序式能开吗
PROBE = """(i) => {
  const d = document.querySelectorAll('details')[i];
  return d ? { open: d.open, len: d.innerText.trim().length } : null;
}"""


def ok(cond: bool, label: str, detail: str = "") -> None:
    """记一条判据。**只往 FAILS 里记，不改变返回值**（§2.5.62 教训）。"""
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


def _sample(main_db: str, staging: str) -> str:
    """挑一条「有图 + 有编码 + 有正文」的事故（与截图/交互脚本同一口径）。"""
    import sqlite3
    con_s = sqlite3.connect(f"file:{staging}?mode=ro", uri=True)
    try:
        ids = [aid for aid, _ in con_s.execute(
            "SELECT accident_id, COUNT(*) c FROM image_manifest "
            "GROUP BY accident_id ORDER BY c DESC LIMIT 200")]
    finally:
        con_s.close()
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    try:
        for aid in ids:
            r = con.execute("SELECT LENGTH(raw_text) FROM accidents WHERE id=?",
                            (aid,)).fetchone()
            if not r or not r[0]:
                continue
            if con.execute("SELECT COUNT(*) FROM caics_causes WHERE accident_id=?",
                           (aid,)).fetchone()[0]:
                return aid
    finally:
        con.close()
    raise SystemExit("挑不出「有图 + 有编码 + 有正文」的样本")


# ─────────────── A 段：真点 vs 程序式，同页对照 ───────────────
def part_a(main_db: str, staging: str) -> dict:
    """一条 page 上测三格。第三格是 §2.5.68 那条判据的处境。"""
    from playwright.sync_api import sync_playwright

    aid = _sample(main_db, staging)
    print(f"\n[A] 真点 vs 程序式展开（样本 {aid}）")
    res: dict[str, bool] = {}
    server, base = serve(APORT)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()
            pg.goto(f"{base}/accident/{aid}", wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(1800)

            # ① 注入前真点第 0 个
            d0 = pg.locator("details").nth(0)
            assert not d0.evaluate("d => d.open"), "第 0 个折叠默认就是开的，换一个"
            d0.locator("summary").first.click(timeout=5000)
            pg.wait_for_timeout(300)
            res["真点·注入前"] = bool(d0.evaluate("d => d.open"))

            # ② 注入拦截后真点第 1 个
            pg.evaluate(BLOCK)
            d1 = pg.locator("details").nth(1)
            assert not d1.evaluate("d => d.open"), "第 1 个折叠默认就是开的"
            d1.locator("summary").first.click(timeout=5000)
            pg.wait_for_timeout(400)
            res["真点·注入后"] = bool(d1.evaluate("d => d.open"))

            # ③ 注入拦截后**程序式**展开第 2 个——§2.5.68 就是这么干的
            pg.evaluate("() => { document.querySelectorAll('details')[2].open = true; }")
            pg.wait_for_timeout(300)
            res["程序式·注入后"] = bool(pg.evaluate(PROBE, 2)["open"])
            b.close()
    finally:
        server.shutdown()

    for k, v in res.items():
        print(f"    {k:<14} 展开={'开了' if v else '没开'}")
    ok(res["真点·注入前"] is True,
       "注入前真点：开（基线，判据本身能测到成功）", f"实测 {res}")
    ok(res["真点·注入后"] is False,
       "注入 `preventDefault` 后真点：**开不了**——用户遇到的就是这个",
       f"实测 {res}")
    ok(res["程序式·注入后"] is True,
       "同一时刻程序式 `d.open = true`：**照样开**——§2.5.68 的判据看不见这个缺陷",
       f"实测 {res}")
    return res


# ────────────────── B 段：判据在位 + 基线 ──────────────────
def part_b() -> dict[str, str]:
    print("\n[B] `check_web_interaction.py` 的判据在位 + 基线跑通")
    src = INTER.read_text(encoding="utf-8")
    for needle, label in [
        ("def pick_samples(", "样本挑选函数 `pick_samples`"),
        ("many_img, long_text = pick_samples(", "主流程真的调了它"),
        ("为什么是两条而不是一条", "「两条样本无交集」的理由写在了源码里"),
        ('d.locator("summary").first.click(', "折叠走真实 `click()` 而非 `d.open = true`"),
        ("text_content()", "读折叠内的按钮文字用 `text_content()`"),
        ("aria-hidden", "灯箱判 `aria-hidden`"),
    ]:
        ok(needle in src, f"源码在位：{label}")

    rc, res = _run_interaction()
    ok(rc == 0, f"基线退出码 0（实为 {rc}）")
    # ⚠️ §2.5.70 又补了第 16、17 条（键盘路径 / 灯箱焦点），总数 15 → **17**。
    # 这条断言必须跟着走——它是"判据在跑"的护栏，数错了就等于没护栏。
    ok(len(res) == 17, f"十七条判据都在跑（实测 {len(res)} 条）", f"实测 {sorted(res)}")
    red = sorted(k for k, v in res.items() if v == "✗")
    ok(not red, "基线没有一条红", f"红的：{red}")
    for name in ("详情页折叠能真点开", "展开全文按钮", "图片灯箱", "列表→详情→返回"):
        ok(res.get(name) == "✓", f"新判据「{name}」在跑且通过")
    return res


def _run_interaction() -> tuple[int, dict[str, str]]:
    """跑一遍 `check_web_interaction.py`，返回（退出码，{step 名: ✓/✗}）。

    ⚠️ 名字取到**全角冒号或空白为止**（`[^：\\s]+`），不能写 `\\S+`：目标脚本对
    同一条失败会打**两遍**——step 当场打一遍（`✗ 名字  详情`，两个空格分隔），
    末尾汇总再打一遍（`✗ 名字：详情`，全角冒号）。`\\S+` 会把汇总行解析成
    `详情页折叠能真点开：第` 这种**带尾巴的第二个键**，于是「红的恰好是哪几条」
    这条断言永远不成立。实测踩过：C 段四例里有三例因此假红。
    """
    r = subprocess.run([PY, str(INTER), "--port", str(IPORT)],
                       capture_output=True, text=True, cwd=str(ROOT))
    res: dict[str, str] = {}
    for line in r.stdout.splitlines():
        m = re.match(r"\s+([✓✗])\s+([^：\s]+)", line)
        if m:
            res[m.group(2)] = m.group(1)
    return r.returncode, res


# ────────────────────── C 段：注入式自证 ──────────────────────
#: 四处注入面，每处只碰一条判据。锚点全部取自 `accident.js` 真实字节。
DRIFT = [
    ("折叠被拦",
     "  load();\n})();",
     "  document.addEventListener('click', function (e) {\n"
     "    if (e.target.closest && e.target.closest('summary')) e.preventDefault();\n"
     "  }, true);\n"
     "  load();\n})();"),
    ("展开全文失效",
     "      more.addEventListener('click', () => {\n"
     "        pre.textContent = full;\n"
     "        more.remove();\n"
     "      });",
     "      more.addEventListener('click', () => { /* 注入：什么都不做 */ });"),
    ("灯箱不响应",
     "    tile.addEventListener('click', function () { Lightbox.open(all, i); });",
     "    tile.addEventListener('click', function () { /* 注入：不打开 */ });"),
    # ⚠️ 锚点只取到 `back.addEventListener(...) {` 为止：那一行到 `if
    # (window.history.length > 1) {` 之间**夹着两行注释**（讲 back() 的用意），
    # 第一版把锚点一路写到底、跳过了注释 ⇒ 锚点未命中、用例静默失效。
    # 只改 href 也不够：从列表点进来 `history.length > 1` 会 back()，href 用不上。
    # 所以注入的是「在 handler 开头就抢先跳去 /causes」——一处替换就能让判据转红。
    ("返回指向错",
     "    back.addEventListener('click', function (e) {\n",
     "    back.addEventListener('click', function (e) {\n"
     "      e.preventDefault(); location.href = '/causes'; return;\n"),
]
#: 每例**期望红的恰好那几条**。⚠️ §2.5.70 给 `check_web_interaction.py` 补了第 16
#: 条「键盘路径」与第 17 条「灯箱键盘与焦点」之后，前三例各自**多红一条**——不是
#: 判据打架，是**同一个缺陷被两条链同时看得见**：折叠、展开全文、灯箱这三处内容
#: 既能鼠标点开也能键盘打开，把 click 的 `preventDefault` 掉或让 handler 空转，
#: 两条链一起断。第 16 条第四格正是"聚焦「展开全文」按 Space"，第 17 条正是
#: "键盘 Enter 开灯箱"，跟着红是**算对了**。
#: ⚠️ **加判据时，别处断言的期望集合要跟着走**：本次就是把 `len(res) == 15 → 17`
#: 改了计数、忘了这两处集合，四例里三例**假红**——§2.5.69 自己立的"断言要精确到
#: 集合"的反面：集合本身变了却没跟着改，精确就变成了失准。
#: 第四例不连带：「返回」是纯导航，没有键盘等价物。
EXPECT = [
    {"详情页折叠能真点开", "键盘路径"},
    {"展开全文按钮", "键盘路径"},
    {"图片灯箱", "灯箱键盘与焦点"},
    {"列表→详情→返回"},
]


def part_c() -> int:
    print("\n[C] 注入式自证：改真实 `accident.js`，断言红的恰好是哪几条")
    orig = JS.read_text(encoding="utf-8")
    bad = 0
    try:
        for i, (label, old, new) in enumerate(DRIFT, 1):
            if old not in orig:
                print(f"    🔴 用例 {i}「{label}」锚点未命中——用例本身失效")
                bad += 1
                continue
            JS.write_text(orig.replace(old, new, 1), encoding="utf-8")
            rc, res = _run_interaction()
            red = {k for k, v in res.items() if v == "✗"}
            want = EXPECT[i - 1]
            good = rc == 1 and red == want
            print(f"    {'✅' if good else '🔴'} 用例 {i}「{label}」→ 退出码 {rc}，"
                  f"红的 {sorted(red)}" + ("" if good else f"（期望 {sorted(want)}）"))
            if not good:
                bad += 1
            JS.write_text(orig, encoding="utf-8")
    finally:
        JS.write_text(orig, encoding="utf-8")
        ok(JS.read_text(encoding="utf-8") == orig, "`accident.js` 已逐字恢复")
    ok(bad == 0, f"注入用例全部按预期转红（{len(DRIFT) - bad}/{len(DRIFT)}）")
    return 0 if bad == 0 else 1


# ────────────────────────── D 段：文档对账 ──────────────────────────
def grab(pat: str, label: str, expect: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    m = re.search(pat, text)
    ok(bool(m) and m.group(1) == expect,
       label, f"文档={m.group(1) if m else '锚点未命中'} 期望={expect}")


def part_d() -> None:
    print("\n[D] 文档 §2.5.69 ↔ 实测")
    # ⚠️ 锚点坑（§2.5.64 立，§2.5.66/§2.5.67/§2.5.68 各犯过）：千分位要用
    #    `[\d,]+`、`**` 的位置要照抄、跨行要 `\s*`。写锚点前先 dump 真实字节。
    grab(r"\*\*交互验收由 (\d+) 条组成\*\*", "正文：交互验收十五条", "15")
    grab(r"逐个真点了 \*\*(\d+) 个 `<details>`\*\*（其一默认\s*展开）",
         "正文：逐个真点 12 个折叠", "12")
    grab(r"5000 \+ ([\d,]+) = 43,124", "正文：5000+38,124=43,124", "38,124")
    grab(r"图最多的那条有 \*\*(\d+) 张图\*\*", "正文：图最多 9 张", "9")
    grab(r"\*\*没有\*\*任何一条记录同时满足[^\n]*?正文 > ([\d,]+) 字",
         "正文：两样本无交集，阈值 5,000", "5,000")


def run() -> int:
    print("=" * 74)
    print("§2.5.69 详情页真实点击：`d.open = true` 与用户真点不等价")
    print("=" * 74)
    main_db = main_db_path()
    staging = str(main_repo_root() / "data" / "caiems_staging.db")
    point_at(main_db, staging)
    part_a(main_db, staging)
    part_b()
    part_c()
    part_d()
    print("\n" + "=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


# ────────────────── 漂移自证：文档面 + 源码面 ──────────────────
DOC_DRIFT = [
    (r"\*\*交互验收由 15 条组成\*\*", r"**交互验收由 16 条组成**"),
    (r"逐个真点了 \*\*12 个", r"逐个真点了 **13 个"),
    (r"\*\*9 张图\*\*", r"**10 张图**"),
]


def _run_doc_only() -> int:
    """只跑 D 段——文档面每例若都要跑一遍 A/B/C，三例就是十分钟。"""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--doc-only"],
        capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode


def drift_all() -> int:
    """两个注入面都要测（§2.5.62 立的规矩）。"""
    bad = 0
    orig = DOC.read_text(encoding="utf-8")
    print("\n[D1] 文档面：改 §2.5.69 的数字，断言退出码转 1")
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
    bad += part_c()
    return 0 if bad == 0 else 1


def main() -> int:
    if "--drift-test" in sys.argv:
        return drift_all()
    if "--doc-only" in sys.argv:
        part_d()
        return 1 if FAILS else 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
