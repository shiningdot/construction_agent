#!/usr/bin/env python3
"""§2.5.70 自检：键盘路径——鼠标点得开**不等于**键盘打得开。

来路：§2.5.69 的判据边界第五条自己写着「键盘可达性只验了一半」。补那半格的过程
里查出**两个真缺陷**（灯箱 `aria-modal="true"` 是声明不是实现、关闭后焦点不归还），
已修；判据由 15 条增到 **17** 条。

为什么它不是"把同一件事再验一遍"：第 12–15 条虽然走的是真事件，但走的是**鼠标**
事件链；键盘走「focus + keydown → 浏览器激活语义」。两条链各有各的断法，且**互为
盲区**——一段吃掉 `keydown` 的 JS 让 Enter 失效而鼠标照常；把按钮从 Tab 序里拿掉
（`tabindex="-1"`）鼠标与程序式 `focus()` 都照常、只有真实 Tab 遍历到不了。

四段：
  A. **同页对照实测**：一条 page 上先按 12 次 Tab（焦点循环在灯箱内），再注入
     捕获阶段的 `stopImmediatePropagation` **屏蔽掉焦点陷阱**，此时再按 Tab 就
     跑出去了——同一个页面、同一套 handler，只差陷阱在不在。
  B. **判据在位 + 基线**：`check_web_interaction.py` 里第 16/17 条的源码在位，
     完整跑一遍退出码 0、**十七条**全 ✓。
  C. **注入式自证**：往真实的 `static/accident.js` 注入四处缺陷，逐个断言
     **红的恰好是哪一条**（不是"见红就算过"，§2.5.62）。用例 4 是本节的命题
     本身：只把缩略图移出 Tab 序，鼠标链与程序式聚焦全绿、只有键盘那条转红。
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
APORT = 5138          # A 段自己起的服务
IPORT = 5139          # 跑 check_web_interaction.py 时给它的端口

FAILS: list[str] = []

#: 焦点此刻在谁身上（够判"在不在灯箱内"就行）。
WHO = """() => {
  const e = document.activeElement;
  const h = document.querySelector('.lightbox');
  if (!e) return '(无)';
  const inside = !!(h && h.contains(e));
  const cls = (e.className && String(e.className).trim()) || '';
  return (e === document.body ? 'BODY' : e.tagName
          + (cls ? '.' + cls.split(' ')[0] : '')) + (inside ? '·内' : '·外');
}"""

INSIDE = """() => {
  const a = document.activeElement;
  const h = document.querySelector('.lightbox');
  return !!(a && h && h.contains(a));
}"""

#: 屏蔽焦点陷阱：陷阱注册在**冒泡**阶段（`document.addEventListener('keydown', …)`），
#: 捕获阶段的 `stopImmediatePropagation` 先跑、把它整条掐掉；**不**
#: `preventDefault`，所以 Tab 的默认行为（真正移动焦点）照常发生。这复现的正是
#: "当时还没写陷阱"那个状态。
BLOCK_TRAP = """() => {
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Tab') e.stopImmediatePropagation();
  }, true);
}"""


def ok(cond: bool, label: str, detail: str = "") -> None:
    """记一条判据。**只往 FAILS 里记，不改变返回值**（§2.5.62 教训）。"""
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


def _sample(main_db: str, staging: str) -> str:
    """挑一条「图 ≥2 + 有正文」的事故——「下一张」与灯箱焦点陷阱都要多图才验得出。"""
    import sqlite3
    con_s = sqlite3.connect(f"file:{staging}?mode=ro", uri=True)
    try:
        ids = [aid for aid, n in con_s.execute(
            "SELECT accident_id, COUNT(*) c FROM image_manifest "
            "GROUP BY accident_id HAVING c >= 2 ORDER BY c DESC LIMIT 200")]
    finally:
        con_s.close()
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    try:
        for aid in ids:
            r = con.execute("SELECT LENGTH(raw_text) FROM accidents WHERE id=?",
                            (aid,)).fetchone()
            if r and r[0]:
                return aid
    finally:
        con.close()
    raise SystemExit("挑不出「图 ≥2 + 有正文」的样本")


# ─────────── A 段：陷阱在 → 出不去；陷阱被屏蔽 → 立刻出去 ───────────
def part_a(main_db: str, staging: str) -> dict:
    from playwright.sync_api import sync_playwright

    aid = _sample(main_db, staging)
    print(f"\n[A] 同一页对照：焦点陷阱在不在，Tab 的去向（样本 {aid}）")
    res: dict[str, object] = {"trail": []}
    server, base = serve(APORT)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()
            pg.goto(f"{base}/accident/{aid}", wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(2000)

            tile = pg.locator("button.img-tile").nth(1)
            tile.scroll_into_view_if_needed()
            tile.focus()
            pg.keyboard.press("Enter")            # 键盘开，不是点
            pg.wait_for_timeout(400)
            res["键盘能开灯箱"] = bool(pg.locator(".lightbox").evaluate(
                "e => e.classList.contains('is-open')"))

            for _ in range(12):
                pg.keyboard.press("Tab")
                res["trail"].append(pg.evaluate(WHO))
            res["有陷阱·12 次 Tab 全在灯箱内"] = all(
                t.endswith("·内") for t in res["trail"])

            pg.evaluate(BLOCK_TRAP)
            escaped: list[str] = []
            for _ in range(6):
                pg.keyboard.press("Tab")
                escaped.append(pg.evaluate(WHO))
            res["屏蔽陷阱后"] = escaped
            res["屏蔽陷阱·Tab 跑出灯箱"] = any(
                t.endswith("·外") for t in escaped)
            b.close()
    finally:
        server.shutdown()

    print(f"    轨迹（陷阱在）：{res['trail']}")
    print(f"    轨迹（陷阱屏蔽后）：{res['屏蔽陷阱后']}")
    ok(res["键盘能开灯箱"] is True, "键盘 Enter 能开灯箱（基线，判据本身测得到成功）",
       f"实测 {res['键盘能开灯箱']}")
    ok(res["有陷阱·12 次 Tab 全在灯箱内"] is True,
       "陷阱在：连按 12 次 Tab，焦点一次都没出去",
       f"实测 {res['trail']}")
    ok(res["屏蔽陷阱·Tab 跑出灯箱"] is True,
       "**同一页**屏蔽掉陷阱：Tab 立刻跑到灯箱外——这就是修复前用户遇到的状态",
       f"实测 {res['屏蔽陷阱后']}")
    return res


# ────────────────── B 段：判据在位 + 基线 ──────────────────
NEEDLES = [
    ('step("键盘路径", s13)', "第 16 条「键盘路径」挂在 `step()` 上"),
    ('step("灯箱键盘与焦点", s14)', "第 17 条「灯箱键盘与焦点」挂在 `step()` 上"),
    ('pg.keyboard.press("Tab")', "可达性走**真实 Tab 遍历**而非 `.focus()` 代替"),
    ('is_visible()', "「按钮看不看得见」用 `is_visible()` 而非 `count()`"),
    ('for key in ("Enter", "Space", "Enter")', "摘要键逐次与前一次状态比对"),
    ('f"第 {i + 1} 次 Tab 焦点跑出了灯箱', "灯箱内逐次断言焦点没跑出去"),
    ('"img-tile" in who', "Esc 关闭后断言焦点归还给触发它的缩略图"),
]


def _run_interaction() -> tuple[int, dict[str, str]]:
    """跑一遍 `check_web_interaction.py`，返回（退出码，{step 名: ✓/✗}）。

    ⚠️ 名字取到**全角冒号或空白为止**（`[^：\\s]+`），不能写 `\\S+`：目标脚本对
    同一条失败会打**两遍**——step 当场打一遍、末尾汇总再打一遍（带全角冒号）。
    `\\S+` 会把汇总行解析成 `详情页折叠能真点开：第` 这种**带尾巴的第二个键**，
    于是「红的恰好是哪几条」这条断言永远不成立（§2.5.69 踩过）。
    """
    r = subprocess.run([PY, str(INTER), "--port", str(IPORT)],
                       capture_output=True, text=True, cwd=str(ROOT))
    res: dict[str, str] = {}
    for line in r.stdout.splitlines():
        m = re.match(r"\s+([✓✗])\s+([^：\s]+)", line)
        if m:
            res[m.group(2)] = m.group(1)
    return r.returncode, res


def part_b() -> dict[str, str]:
    print("\n[B] `check_web_interaction.py` 的判据在位 + 基线跑通")
    src = INTER.read_text(encoding="utf-8")
    for needle, label in NEEDLES:
        ok(needle in src, f"源码在位：{label}")

    rc, res = _run_interaction()
    ok(rc == 0, f"基线退出码 0（实为 {rc}）")
    ok(len(res) == 17, f"十七条判据都在跑（实测 {len(res)} 条）", f"实测 {sorted(res)}")
    red = sorted(k for k, v in res.items() if v == "✗")
    ok(not red, "基线没有一条红", f"红的：{red}")
    for name in ("键盘路径", "灯箱键盘与焦点"):
        ok(res.get(name) == "✓", f"新判据「{name}」在跑且通过")
    return res


def part_b_static() -> None:
    """只跑 B 段的**静态**那一半（不启浏览器）——给 `--drift-test` 用。"""
    src = INTER.read_text(encoding="utf-8")
    for needle, label in NEEDLES:
        ok(needle in src, f"源码在位：{label}")


# ────────────────────── C 段：注入式自证 ──────────────────────
#: 四处注入面，每处只碰一条判据。锚点全部取自 `accident.js` 真实字节。
DRIFT = [
    # ① 陷阱作废——在 `if (e.key !== 'Tab') return;` 之后直接返回。
    ("焦点陷阱作废",
     "        if (e.key !== 'Tab') return;\n",
     "        if (e.key !== 'Tab') return;\n"
     "        return;   // 注入：焦点陷阱作废\n"),
    # ② 焦点不归还——整段替换成注释。
    ("关闭后不归还焦点",
     "      if (lastFocus && document.contains(lastFocus)\n"
     "          && typeof lastFocus.focus === 'function') lastFocus.focus();\n",
     "      // 注入：不归还焦点\n"),
    # ③ 键盘开不了折叠——捕获阶段吃掉 summary 上的 Enter/Space。
    #    锚点取文件末尾的 `load();\n})();`（与 §2.5.69 同一处，实测唯一）。
    ("折叠键盘失效",
     "  load();\n})();",
     "  document.addEventListener('keydown', function (e) {\n"
     "    if ((e.key === 'Enter' || e.key === ' ')\n"
     "        && e.target && e.target.closest && e.target.closest('summary')) {\n"
     "      e.preventDefault(); e.stopPropagation();\n"
     "    }\n"
     "  }, true);\n"
     "  load();\n})();"),
    # ④ 缩略图移出 Tab 序。**改成 `<div>` 不行**——`el('div', …)` 会让
    #    `button.img-tile` 这个选择器选空，第 14 条（鼠标点灯箱）跟着转红，
    #    红的就不是"恰好一条"了，也分不清是键盘链还是鼠标链断的。
    #    `tabindex="-1"` 才是干净的单变量：不进 Tab 序，其余一切照旧。
    ("缩略图移出 Tab 序",
     "    const tile = el('button', { cls: 'img-tile',\n"
     "                                attrs: { type: 'button',\n",
     "    const tile = el('button', { cls: 'img-tile',\n"
     "                                attrs: { type: 'button', tabindex: '-1',\n"),
]
#: 每例**期望红的恰好那条**。
EXPECT = [
    {"灯箱键盘与焦点"},
    {"灯箱键盘与焦点"},
    {"键盘路径"},
    {"键盘路径"},
]


def part_c() -> int:
    print("\n[C] 注入式自证：改真实 `accident.js`，断言红的恰好是哪一条")
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
def grab(pat: str, label: str, *expect: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    m = re.search(pat, text)
    got = [g for g in (m.groups() if m else ())]
    ok(bool(m) and got == list(expect),
       label, f"文档={got or '锚点未命中'} 期望={list(expect)}")


def part_d() -> None:
    print("\n[D] 文档 §2.5.70 ↔ 实测")
    # ⚠️ 锚点坑（§2.5.64 立，§2.5.66/67/68/69 各犯过）：千分位要用 `[\d,]+`、
    #    `**` 的位置要照抄、跨行要 `\s*`。写锚点前先 dump 真实字节。
    grab(r"判据由 15 条增到 \*\*(\d+)\*\* 条", "正文：判据 15 → 17 条", "17")
    grab(r"Tab \*\*(\d+)\*\* 次 \+ Shift\+Tab \*\*(\d+)\*\* 次",
         "正文：Tab 12 次 + Shift+Tab 4 次", "12", "4")
    grab(r"能（(\d+) 个都在序里）", "正文：9 个缩略图都在 Tab 序里", "9")
    grab(r"5000 → ([\d,]+) 字", "正文：展开全文 5000 → 43,124 字", "43,124")
    grab(r"整脚本约 (\d+) → 约 (\d+) 秒", "正文：脚本 70 → 100 秒", "70", "100")


def run() -> int:
    print("=" * 74)
    print("§2.5.70 键盘路径：鼠标点得开不等于键盘打得开")
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
    (r"判据由 15 条增到 \*\*17\*\* 条", r"判据由 15 条增到 **18** 条"),
    (r"Tab \*\*12\*\* 次", r"Tab **13** 次"),
    (r"5000 → 43,124 字", r"5000 → 43,125 字"),
]
SRC_DRIFT = [
    ('step("键盘路径", s13)', 'step("键盘路径", s99)'),
    ('is_visible()', 'count()'),
    ('"img-tile" in who', '"img-tile" in ""'),
]


def _run_mode(flag: str) -> int:
    """只跑 D 段 / 只跑 B 段静态——每例若都要跑一遍浏览器，三例就是十分钟。"""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), flag],
        capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode


def drift_all() -> int:
    """两个注入面都要测（§2.5.62 立的规矩）。"""
    bad = 0
    orig_doc = DOC.read_text(encoding="utf-8")
    print("\n[D1] 文档面：改 §2.5.70 的数字，断言退出码转 1")
    try:
        for i, (old, new) in enumerate(DOC_DRIFT, 1):
            if not re.search(old, orig_doc):
                print(f"    🔴 用例 {i} 锚点未命中（用例本身失效）：{old[:40]}")
                bad += 1
                continue
            DOC.write_text(re.sub(old, new, orig_doc, count=1), encoding="utf-8")
            rc = _run_mode("--doc-only")
            print(f"    {'✅' if rc == 1 else '🔴'} 用例 {i}：改「{old[:28]}…」"
                  f"→ 退出码 {rc}")
            if rc != 1:
                bad += 1
            DOC.write_text(orig_doc, encoding="utf-8")
    finally:
        DOC.write_text(orig_doc, encoding="utf-8")
    if DOC.read_text(encoding="utf-8") != orig_doc:
        print("    🔴 文档未逐字恢复")
        bad += 1

    orig_src = INTER.read_text(encoding="utf-8")
    print("\n[D2] 源码面：改 `check_web_interaction.py` 的判据，断言退出码转 1")
    try:
        for i, (old, new) in enumerate(SRC_DRIFT, 1):
            if old not in orig_src:
                print(f"    🔴 用例 {i} 锚点未命中（用例本身失效）：{old[:40]}")
                bad += 1
                continue
            # ⚠️ **全部替换，不是 `count=1`**。B 段的判据是「这个子串在不在」，
            # 一个出现多次的子串替换掉一处、剩下的照样命中，用例就是**空转**。
            # 实测踩过：`is_visible()` 在 `check_web_interaction.py` 里出现 2 次，
            # 第一次在**注释**里（"要用 `is_visible()`，不能用 `count()`"）——
            # 只换一处换的正是注释，真正的代码分毫未动、退出码照旧 0。
            # 与 §2.5.69「锚点跳过注释行」是同一个坑的第二形态：注释里提到判据
            # 名字，正好把判据自己的钥匙交出去。
            INTER.write_text(orig_src.replace(old, new), encoding="utf-8")
            rc = _run_mode("--static-only")
            print(f"    {'✅' if rc == 1 else '🔴'} 用例 {i}：把「{old[:32]}…」"
                  f"改成别的 → 退出码 {rc}")
            if rc != 1:
                bad += 1
            INTER.write_text(orig_src, encoding="utf-8")
    finally:
        INTER.write_text(orig_src, encoding="utf-8")
    if INTER.read_text(encoding="utf-8") != orig_src:
        print("    🔴 `check_web_interaction.py` 未逐字恢复")
        bad += 1
    return 0 if bad == 0 else 1


def main() -> int:
    if "--drift-test" in sys.argv:
        return drift_all()
    if "--doc-only" in sys.argv:
        part_d()
        return 1 if FAILS else 0
    if "--static-only" in sys.argv:
        part_b_static()
        return 1 if FAILS else 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
