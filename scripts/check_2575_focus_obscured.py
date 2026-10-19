#!/usr/bin/env python3
"""§2.5.75 自检：焦点遮挡——Tab 到哪会被顶栏 / 吸顶表头 / 批量条盖住。

WCAG 2.4.11 Focus Not Obscured (Minimum, AA)：拿到焦点的组件**不得被作者创建的
内容完全遮住**（原文 "the item with focus is not entirely hidden"）。部分遮挡在
这一档是合规的，Full/Enhanced 的 2.4.12 才管部分——故本脚本只把**完全遮挡**判红。

来路：`09_端到端对齐核查.md` §8.7 登记的八条未机器化核查里，第 4 条写着「2.4.11
根本没测」。补这一格补出**两处 AA 级硬违规**（顶部吸顶区、底部批量条），已修。

两件"判据自己会失效"的事，写在这里免得后人重犯：

  1. **纯 Tab 遍历永远绿**。浏览器把焦点交给元素时本来就会把它滚进视口，焦点一路
     留在视口内，压根没有"把视口外的元素滚进来"这个动作——而 sticky 遮挡只在这个
     动作里发生。必须复现**先滚动、后 Tab**。v1/v2 探针（76/98/20…步）就是这样全绿
     的，v3 改了遍历形态立刻抓到。
  2. **底部批量条只能靠 focus() 扫描**。工作库是空的（`/api/collect/raw` 实测
     `total=0`）⇒ 采集页无行可勾 ⇒ `.bulkbar` 永不出现 ⇒ Tab 遍历从原理上看不到它。

四段：
  A. **实观测**：五视口 × 五页完整往返遍历；采集页强制显示批量条后跑全聚焦扫描；
     外加三条**几何自洽**（表头停靠线 == 顶栏实测高、两个 scroll-padding 覆盖各自
     的遮挡带）。
  B. **判据在位 + 基线**：CSS/模板/脚本里六条声明在位；另在 `.grid thead th` 与
     `.bulkbar` 两个**规则块内**（大括号配平定位，不裸搜）核两条正例两条反例。
  C. **注入式自证**：把 `scroll-padding-top` 压回 0 → 断言**只有顶部场景转红、底部
     仍绿**；再把 `scroll-padding-bottom` 压回 0 → 断言**只有批量条场景转红**。
     红集合必须**恰好**是各自那一批（只断言"退出码非 0"太弱，§2.5.62）。
  D. 文档 §2.5.75 ↔ 实测数字。

口径：起本地服务 + 无头 Chromium。A/C 段只读，不改任何文件。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _serve import serve                              # noqa: E402

CSS = ROOT / "construction_agent" / "web" / "static" / "app.css"
BASEHTML = ROOT / "construction_agent" / "web" / "templates" / "base.html"
LAYOUTJS = ROOT / "construction_agent" / "web" / "static" / "layout.js"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"

APORT = 5150               # 本脚本自己要用的端口（#5140 对比度、#5141 配色来源、
                           #  #5138/5139 键盘路径、#5133/5134 截图、#5145–5155 探针）

SIZES = ((1440, 900), (1280, 720), (1100, 800), (900, 800), (700, 800))
PAGES = ("/", "/causes", "/collect", "/extract", "/review")
MAX_STEPS = 120            # 单程上限：五页可聚焦元素实测 12–92 个，够走完整圈

FAILS: list[str] = []


def ok(cond: bool, msg: str, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {msg}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(msg)
    return cond


# ───────────────────────── 共用 JS ─────────────────────────
#: 焦点元素的 5 个采样点里被"作者创建的遮挡者"命中的个数。
#: `elementFromPoint` 走真实渲染堆叠（z-index / stacking context 都算），比矩形相交准。
DIAG = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return {tag: 'BODY'};
  const r = el.getBoundingClientRect();
  const vw = innerWidth, vh = innerHeight;
  const blockers = [];
  document.querySelectorAll('*').forEach(n => {
    const cs = getComputedStyle(n);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') return;
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (n === el || el.contains(n) || n.contains(el)) return;   // 自身/祖先/后代不算遮挡
    const b = n.getBoundingClientRect();
    if (b.width <= 0 || b.height <= 0) return;
    blockers.push({node: n, desc: n.tagName.toLowerCase() + '.'
                   + (String(n.className || '').split(' ')[0] || '')});
  });
  const pts = [['C', r.left + r.width / 2, r.top + r.height / 2],
               ['LT', r.left + 2, r.top + 2], ['RT', r.right - 2, r.top + 2],
               ['LB', r.left + 2, r.bottom - 2], ['RB', r.right - 2, r.bottom - 2]];
  let blocked = 0, valid = 0; const who = [];
  for (const [, x, y] of pts) {
    if (x < 0 || y < 0 || x >= vw || y >= vh) continue;         // 视口外的点不参与
    valid++;
    const h = document.elementFromPoint(x, y);
    for (const b of blockers) {
      if (h === b.node || b.node.contains(h)) { blocked++; who.push(b.desc); break; }
    }
  }
  const label = (el.getAttribute('aria-label') || el.textContent || '')
                  .replace(/\\s+/g, ' ').trim().slice(0, 18);
  return {tag: el.tagName.toLowerCase(), cls: String(el.className || '').split(' ')[0],
          label: label, top: Math.round(r.top), blocked, valid,
          who: [...new Set(who)].join(',')};
}"""

#: 逐个 focus() 所有可聚焦元素——比 Tab 遍历多覆盖两类：**静止遮挡**（元素本来就
#: 停在遮挡带里，Tab 每次都先滚动因而走不到）与底部 fixed 遮挡者。
SWEEP = """async () => {
  const all = Array.from(document.querySelectorAll(
    'a[href],button,input,select,textarea,[tabindex]')).filter(e => {
      if (e.tabIndex < 0) return false;
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  const frame = () => new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
  const full = [], part = [];
  for (const el of all) {
    el.focus();
    await frame();
    const r = el.getBoundingClientRect();
    const vw = innerWidth, vh = innerHeight;
    const blockers = [];
    document.querySelectorAll('*').forEach(n => {
      const cs = getComputedStyle(n);
      if (cs.position !== 'fixed' && cs.position !== 'sticky') return;
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      if (n === el || el.contains(n) || n.contains(el)) return;
      const b = n.getBoundingClientRect();
      if (b.width <= 0 || b.height <= 0) return;
      blockers.push({node: n, desc: n.tagName.toLowerCase() + '.'
                     + (String(n.className || '').split(' ')[0] || '')});
    });
    const pts = [['C', r.left + r.width / 2, r.top + r.height / 2],
                 ['LT', r.left + 2, r.top + 2], ['RT', r.right - 2, r.top + 2],
                 ['LB', r.left + 2, r.bottom - 2], ['RB', r.right - 2, r.bottom - 2]];
    let blocked = 0, valid = 0; const who = [];
    for (const [, x, y] of pts) {
      if (x < 0 || y < 0 || x >= vw || y >= vh) continue;
      valid++;
      const h = document.elementFromPoint(x, y);
      for (const b of blockers) {
        if (h === b.node || b.node.contains(h)) { blocked++; who.push(b.desc); break; }
      }
    }
    if (!valid || !blocked) continue;
    const desc = el.tagName.toLowerCase() + '.' + String(el.className || '').split(' ')[0]
                 + ' «' + (el.getAttribute('aria-label') || el.textContent || '')
                     .replace(/\\s+/g, ' ').trim().slice(0, 18) + '» @'
                 + Math.round(r.top) + ' ' + blocked + '/' + valid
                 + ' by ' + [...new Set(who)].join(',');
    (blocked === valid ? full : part).push(desc);
  }
  return {n: all.length, full: full, part: part};
}"""

GEOM = """() => {
  const cs = getComputedStyle(document.documentElement);
  const bar = document.querySelector('.topbar');
  const th = document.querySelector('.grid thead th');
  const bulk = document.getElementById('bulkBar');
  const br = (bulk && !bulk.hidden) ? bulk.getBoundingClientRect() : null;
  return {
    barH: bar ? Math.round(bar.getBoundingClientRect().height) : null,
    thH: th ? Math.round(th.getBoundingClientRect().height) : null,
    thPos: th ? getComputedStyle(th).position : null,
    thTop: th ? getComputedStyle(th).top : null,
    padTop: cs.scrollPaddingTop,
    padBottom: cs.scrollPaddingBottom,
    varTopbar: cs.getPropertyValue('--topbar-h').trim(),
    varBulk: cs.getPropertyValue('--bulkbar-h').trim(),
    bulkBottomGap: cs.getPropertyValue('--bulkbar-gap').trim(),
    bulkRect: br ? [Math.round(br.left), Math.round(br.top),
                    Math.round(br.width), Math.round(br.height)] : null,
  };
}"""


# ───────────────────────── 遍历与场景 ─────────────────────────
def _sweep_tab(pg, key: str, bad: list, tag: str) -> None:
    for _ in range(MAX_STEPS):
        pg.keyboard.press(key)
        pg.wait_for_timeout(20)
        d = pg.evaluate(DIAG)
        if d.get("tag") == "BODY":
            return
        if d["blocked"]:
            kind = "完全" if d["blocked"] == d["valid"] else "部分"
            bad.append(f"{tag}{kind}{d['cls'] or d['tag']}@{d['top']} "
                       f"{d['blocked']}/{d['valid']} by {d['who']}")


def _open(pg, base: str, path: str, size, css: str) -> dict:
    """打开页面、可选注入样式、返回几何。"""
    pg.set_viewport_size({"width": size[0], "height": size[1]})
    pg.goto(base + path, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(900)
    if css:
        pg.add_style_tag(content=css)
        pg.wait_for_timeout(120)
    return pg.evaluate(GEOM)


def scene_tab(pg, base: str, path: str, size, css: str = "") -> list:
    """完整往返 Tab 遍历，返回被挡清单（只含**完全**遮挡，部分另计）。"""
    _open(pg, base, path, size, css)
    pg.evaluate("() => document.activeElement && document.activeElement.blur()")
    bad: list = []
    pg.keyboard.press("Tab")
    pg.wait_for_timeout(40)
    _sweep_tab(pg, "Tab", bad, "↓")
    _sweep_tab(pg, "Shift+Tab", bad, "↑")
    return [b for b in bad if "完全" in b]


def scene_bulk(pg, base: str, size, css: str = "") -> list:
    """采集页强制显示批量条后跑全聚焦扫描。工作库为空 ⇒ 无行可勾 ⇒ 只能强制显示；
    批量条尺寸只由 CSS 决定、与行数据无关，故几何等价。"""
    _open(pg, base, "/collect", size, css)
    pg.evaluate("""() => { const b = document.getElementById('bulkBar');
                           if (b) { b.hidden = false;
                                    document.getElementById('bulkCount').textContent = '已选 3 条'; } }""")
    pg.wait_for_timeout(200)
    return pg.evaluate(SWEEP)["full"]


#: C 段要分辨的五个场景：三个"顶部遮挡"、两个"底部遮挡"。
SCENES = [
    ("顶部 / 1440×900", lambda pg, base, css: scene_tab(pg, base, "/", (1440, 900), css)),
    ("顶部 /collect 1440×900",
     lambda pg, base, css: scene_tab(pg, base, "/collect", (1440, 900), css)),
    ("顶部 / 900×800", lambda pg, base, css: scene_tab(pg, base, "/", (900, 800), css)),
    ("底部 bulkbar 1440×900", lambda pg, base, css: scene_bulk(pg, base, (1440, 900), css)),
    ("底部 bulkbar 900×800", lambda pg, base, css: scene_bulk(pg, base, (900, 800), css)),
]
TOP_SCENES = {"顶部 / 1440×900", "顶部 /collect 1440×900", "顶部 / 900×800"}
BOTTOM_SCENES = {"底部 bulkbar 1440×900", "底部 bulkbar 900×800"}


# ───────────────────────── A 段 ─────────────────────────
def part_a() -> dict:
    from playwright.sync_api import sync_playwright

    print("\n[A] 实观测：五视口 × 五页完整往返 + 批量条全聚焦扫描 + 几何自洽")
    res: dict = {"tab": {}, "bulk": {}, "geom": {}}
    server, base = serve(APORT)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for size in SIZES:
                pg = b.new_context(viewport={"width": size[0], "height": size[1]}).new_page()
                tag = f"{size[0]}×{size[1]}"
                tot, geom0 = 0, None
                for path in PAGES:
                    try:
                        bad = scene_tab(pg, base, path, size)
                        g = pg.evaluate(GEOM)
                    except Exception as exc:                       # noqa: BLE001
                        bad, g = [f"!! {type(exc).__name__}: {exc}"], {}
                    tot += len(bad)
                    res["tab"][f"{tag} {path}"] = len(bad)
                    if path == "/":
                        geom0 = g
                res["geom"][tag] = geom0 or {}
                try:
                    full = scene_bulk(pg, base, size)
                except Exception as exc:                           # noqa: BLE001
                    full = [f"!! {type(exc).__name__}: {exc}"]
                res["bulk"][tag] = full
                print(f"    {tag:<9} Tab 往返 完全遮挡 {tot:2d}   "
                      f"批量条扫描 完全 {len(full)}")
                for x in full[:3]:
                    print(f"        {x}")
                pg.close()
            b.close()
    finally:
        server.shutdown()

    tot_tab = sum(res["tab"].values())
    tot_bulk = sum(len(v) for v in res["bulk"].values())
    ok(tot_tab == 0, "五视口 × 五页，完整往返 Tab 遍历（200/120 步双向）零完全遮挡",
       f"实测 {tot_tab}")
    ok(tot_bulk == 0, "五视口下强制显示批量条后，全聚焦扫描零完全遮挡",
       f"实测 {tot_bulk}")

    # ── 几何自洽三条 ──
    print("    几何：")
    bad_geo: list[str] = []
    for tag, g in res["geom"].items():
        if g.get("thPos") == "sticky" and g.get("thTop") != f"{g.get('barH')}px":
            bad_geo.append(f"{tag} thTop={g.get('thTop')} != topbar={g.get('barH')}px")
        if g.get("barH") and g.get("thH") and g.get("thPos") == "sticky":
            want = g["barH"] + g["thH"]
            if g.get("padTop") != f"{want}px":
                bad_geo.append(f"{tag} padTop={g.get('padTop')} != {want}px")
        wantb = 56 + 18            # --bulkbar-h + --bulkbar-gap
        if g.get("padBottom") != f"{wantb}px":
            bad_geo.append(f"{tag} padBottom={g.get('padBottom')} != {wantb}px")
    ok(not bad_geo, "几何自洽：表头停靠线 == 顶栏实测高；两个 scroll-padding 覆盖各自遮挡带",
       "；".join(bad_geo) or "四个视口全中")
    for tag, g in res["geom"].items():
        print(f"      {tag:<9} topbar={g.get('barH')} thH={g.get('thH')} "
              f"padTop={g.get('padTop')} padBottom={g.get('padBottom')}")

    # 动态跟随：至少要有两个不同的顶栏高度（证明不是写死的常量碰巧对上）
    heights = {g.get("barH") for g in res["geom"].values() if g.get("barH")}
    ok(len(heights) >= 2, "顶栏高度随视口变化、且 --topbar-h 跟着变（不是写死的常量碰巧对上）",
       f"实测出现 {sorted(heights)}")
    return res


# ───────────────────────── B 段 ─────────────────────────
def _decls(css: str, selector: str) -> str:
    """取出某条规则（`selector { … }`）的**声明体**，按大括号配平。

    ⚠️ 为什么不能裸搜属性串：反例判据 `"bottom: 18px" not in css` 首跑就报了一次
    **假红**——命中的是 `.layer-group { margin-bottom: 18px; }`（一条跟批量条毫无关系
    的规则），`margin-bottom` 里就含 `bottom: 18px` 这七个字符。这与 §2.5.63（裸子串
    `INSTR(col,'\\t')` 命中 LaTeX 命令名 `\\times`）、§2.5.66（裸 `.base_url` 命中别的
    类的同名字段）**是同一种病**：判据不收窄接收者，就会在混合语料里自己制造假警报。
    故正例反例一律**先在规则块内定位**再断言。
    """
    i = css.find(selector)
    while i != -1:
        # 选择器后面必须紧跟 `{`（允许空白/换行），否则是别处的同名前缀
        j = i + len(selector)
        while j < len(css) and css[j] in " \t\r\n":
            j += 1
        if j < len(css) and css[j] == "{":
            break
        i = css.find(selector, i + 1)
    if i == -1:
        return ""
    depth, k = 0, j
    while k < len(css):
        if css[k] == "{":
            depth += 1
        elif css[k] == "}":
            depth -= 1
            if depth == 0:
                return css[j + 1:k]
        k += 1
    return css[j + 1:]


def _rule_span(css: str, selector: str) -> tuple[int, int] | None:
    """`selector { … }` 声明体的 (起, 止) 下标；找不到返回 None。供注入用。"""
    head = _decls(css, selector)
    if not head:
        return None
    start = css.find(head, css.find(selector))
    if start == -1:
        return None
    return start, start + len(head)


NEEDLES = [
    (CSS, "scroll-padding-top: var(--scroll-pad);",
     "`html` 上声明了 scroll-padding-top（顶部吸顶区）"),
    (CSS, "scroll-padding-bottom: var(--scroll-pad-bottom);",
     "`html` 上声明了 scroll-padding-bottom（底部批量条）"),
    (CSS, "--topbar-h: 55px;", "`--topbar-h` 有首帧初值（layout.js 被禁用时的回落）"),
    (BASEHTML, "layout.js", "base.html 引入了 layout.js（量顶栏真实高度）"),
    (LAYOUTJS, "ResizeObserver", "layout.js 用 ResizeObserver 而不只听 resize 事件"),
    (LAYOUTJS, "if (h > 0)", "layout.js 不把 0 写进变量（隐藏/未布局时）"),
]


def part_b() -> None:
    print("\n[B] 判据在位 + 基线")
    for path, needle, why in NEEDLES:
        txt = path.read_text(encoding="utf-8") if path.exists() else ""
        ok(needle in txt, why, f"`{needle}` @ {path.name}")

    css = CSS.read_text(encoding="utf-8")
    # 两条正例：停靠线/底距**必须写在各自的规则块里**、且引用变量。
    th = _decls(css, ".grid thead th")
    bulk = _decls(css, ".bulkbar")
    ok(bool(th), "定位到 `.grid thead th` 规则块（配平取到声明体）")
    ok(bool(bulk), "定位到 `.bulkbar` 规则块（配平取到声明体）")
    ok("top: var(--topbar-h)" in th,
       "吸顶表头的停靠线引用同一个变量，不再硬编码 57px", f"`{th.strip().splitlines()[0]}`")
    ok("bottom: var(--bulkbar-gap)" in bulk,
       "批量条的底距引用变量，不再硬编码 18px", f"`{bulk.strip().splitlines()[0]}`")
    # 两条反例：硬编码的值一旦回来就是漂移复发。**只看各自那一块**。
    ok("top: 57px" not in th, "`.grid thead th` 里不再有硬编码的 `top: 57px`（顶栏实测 55px）")
    ok("bottom: 18px" not in bulk, "`.bulkbar` 里不再有裸的 `bottom: 18px`")


# ───────────────────────── C 段 ─────────────────────────
KILL_TOP = "html { scroll-padding-top: 0 !important; }"
KILL_BOTTOM = "html { scroll-padding-bottom: 0 !important; }"


def _run_scenes(css: str) -> dict[str, list]:
    from playwright.sync_api import sync_playwright

    out: dict[str, list] = {}
    server, base = serve(APORT + 1)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for name, fn in SCENES:
                size = (900, 800) if "900×800" in name else (1440, 900)
                pg = b.new_context(viewport={"width": size[0], "height": size[1]}).new_page()
                try:
                    out[name] = fn(pg, base, css)
                except Exception as exc:                           # noqa: BLE001
                    out[name] = [f"!! {type(exc).__name__}: {exc}"]
                pg.close()
            b.close()
    finally:
        server.shutdown()
    return out


def part_c() -> None:
    print("\n[C] 注入式自证：红的集合必须**恰好**是各自那一批")
    base_run = _run_scenes("")
    ok(all(not v for v in base_run.values()),
       "基线（无注入）：五个场景全零",
       "；".join(f"{k}={len(v)}" for k, v in base_run.items()))

    red_top = _run_scenes(KILL_TOP)
    got = {k for k, v in red_top.items() if v}
    ok(got == TOP_SCENES,
       "把 scroll-padding-top 压回 0 ⇒ 恰好三个**顶部**场景转红，两个底部场景仍绿",
       f"红集合 {sorted(got)}；底部实测 "
       + "；".join(f"{k}={len(red_top[k])}" for k in sorted(BOTTOM_SCENES)))

    red_bot = _run_scenes(KILL_BOTTOM)
    got2 = {k for k, v in red_bot.items() if v}
    ok(got2 == BOTTOM_SCENES,
       "把 scroll-padding-bottom 压回 0 ⇒ 恰好两个**底部**场景转红，三个顶部场景仍绿",
       f"红集合 {sorted(got2)}；顶部实测 "
       + "；".join(f"{k}={len(red_bot[k])}" for k in sorted(TOP_SCENES)))


# ───────────────────────── D 段 ─────────────────────────
def part_d(geom: dict) -> None:
    print("\n[D] 文档 §2.5.75 ↔ 实测")
    txt = DOC.read_text(encoding="utf-8")
    seg = txt.split("### 2.5.75 ", 1)
    if not ok(len(seg) == 2, "文档里有 §2.5.75 这一节"):
        return
    body = seg[1].split("\n### 2.5.76", 1)[0]

    # 1) 定档表里 94px 那一行必须仍然写着"全消"
    m = re.search(r"\|\s*`94px`\s*\|\s*\*\*全消\*\*\s*\|", body)
    ok(m is not None, "定档表写着 94px 全消（与 A 段实测一致）")
    # 2) scroll-padding-bottom 的算式 56 + 18 = 74
    ok("56 + 18" in body and "= **74px**" in body,
       "文档写明 scroll-padding-bottom = 56 + 18 = 74px")
    # 3) 900px 宽顶栏 78px 这个"动态跟随"的关键证据
    ok("900px 宽时顶栏实测 78px" in body, "文档记下 900px 宽时顶栏 78px（动态跟随的证据）")
    # 4) 两个实测高度的对账（跨 §2.5.75 之外也核一遍，防文档与现状分叉）
    hs = sorted({g.get("barH") for g in geom.values() if g.get("barH")})
    ok(78 in hs and 55 in hs, "A 段实测确实同时出现 55px 与 78px 两种顶栏高度",
       f"实测 {hs}")
    # 5) 判据边界六条在位
    ok("只判**完全遮挡**" in body, "判据边界写了「只判完全遮挡、部分遮挡不判红」")
    ok("`:has()`" in body, "文档解释了为什么用常驻而不是 :has()")


# ───────────────────────── 自证 ─────────────────────────
#: 漂移用例：(文档里的原串, 注入后的串, 标签, 期望**恰好**报红的那一条判据)
DRIFT_CASES = [
    ("| `94px` | **全消** |", "| `95px` | **全消** |",
     "定档表 94→95", "定档表写着 94px 全消（与 A 段实测一致）"),
    ("56 + 18", "56 + 19",
     "底部算式 18→19", "文档写明 scroll-padding-bottom = 56 + 18 = 74px"),
    ("900px 宽时顶栏实测 78px", "900px 宽时顶栏实测 79px",
     "顶栏 78→79", "文档记下 900px 宽时顶栏 78px（动态跟随的证据）"),
    ("只判**完全遮挡**", "只判**全部遮挡**",
     "判据边界措辞", "判据边界写了「只判完全遮挡、部分遮挡不判红」"),
]

#: D 段里那条"实测确实出现 55 与 78 两种顶栏高度"的判据，drift 时喂这个假几何，
#: 免得它也跟着红——那会把"锚点变了"和"判据报警"混在一起（§2.5.67 的教训）。
DRIFT_GEOM = {"a": {"barH": 55}, "b": {"barH": 78}}


def drift_test() -> int:
    """往**文档副本**注入漂移，断言 D 段转红、且红的**恰好**是目标那一条。

    只动临时副本，A/B/C 段与真实文件都不碰。
    """
    global DOC                                  # noqa: PLW0603  （临时指向副本）
    import contextlib
    import io
    import shutil
    import tempfile

    print("\n[drift] 文档面注入：改一处，断言**恰好**一条判据转红")
    original = DOC.read_text(encoding="utf-8")
    tmpdir = Path(tempfile.mkdtemp(prefix="c2575_"))
    caught = 0
    try:
        for old, new, label, want in DRIFT_CASES:
            if old not in original:
                print(f"  ✗ 锚点未命中：{label}   `{old}`")
                continue
            tmp = tmpdir / "doc.md"
            tmp.write_text(original.replace(old, new, 1), encoding="utf-8")
            keep, DOC = DOC, tmp
            FAILS.clear()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    part_d(DRIFT_GEOM)
                red = list(FAILS)
            finally:
                DOC = keep
                FAILS.clear()
            # 「红的恰好是那一条」——不是"见红就算过"
            hit = red == [want]
            print(f"  {'✓' if hit else '✗'} {label}   红 = {red}"
                  + ("" if hit else "   ← 期望恰好一条，用例失效"))
            caught += hit
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"  drift: {caught}/{len(DRIFT_CASES)} 转红")
    src = source_drift()
    return 0 if caught == len(DRIFT_CASES) and src == 0 else 1


#: 源码面漂移：往**真实 `app.css`** 的规则块里注入硬编码值（try/finally 恢复）。
#: 文档面测不到这一面——B 段首跑报的那次**假红**（`margin-bottom: 18px` 命中裸子串）
#: 正因 B 段当时没有任何失败路径，才只能靠实跑偶然撞见（§2.5.62 立的：源码面要自己的
#: `--drift-test`；§2.5.74 同款做法）。
#:
#: ⚠️ **期望是一个集合（两条），不是一条**。同一个事实（"停靠线不得硬编码"）有**两条
#: 判据**兜着：正例（`top: var(--topbar-h)` 在块内）与反例（块内无 `top: 57px`）。
#: 把 `var()` 换成 `57px` 会**同时**打破两条——首版写成 `red == [want]`（恰好一条）因而
#: 两个用例都判"失效"。这与 §2.5.74 首轮"`input_schema` 与 `properties` 被当两列"同款：
#: **期望集合必须照着判据实际覆盖的格来写**，不是照着"我希望它报几条"来写。
SRC_DRIFT = [
    (".grid thead th", "top: var(--topbar-h)", "top: 57px",
     {"吸顶表头的停靠线引用同一个变量，不再硬编码 57px",
      "`.grid thead th` 里不再有硬编码的 `top: 57px`（顶栏实测 55px）"}),
    (".bulkbar", "bottom: var(--bulkbar-gap)", "bottom: 18px",
     {"批量条的底距引用变量，不再硬编码 18px",
      "`.bulkbar` 里不再有裸的 `bottom: 18px`"}),
]


def source_drift() -> int:
    """往真实 app.css 注入两条硬编码，断言各自**恰好**打破对应那一对判据。"""
    import contextlib
    import io

    print("\n[drift] 源码面注入：改一处，断言红的**恰好**是覆盖那一格的那几条")
    original = CSS.read_text(encoding="utf-8")
    caught = 0
    red0: list[str] = []
    try:
        # 先把"别处的 margin-bottom: 18px 不该误伤"这一格钉住（它是本轮修判据的由来）
        ok("margin-bottom: 18px" in original,
           "基线里确实存在别处的 `margin-bottom: 18px`（`.layer-group`）——判据必须绕过它")
        FAILS.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            part_b()
        red0 = list(FAILS)
        ok(red0 == [], "基线 B 段全绿：别处的 `margin-bottom: 18px` 没有误伤", f"红 = {red0}")

        for selector, old, new, want in SRC_DRIFT:
            span = _rule_span(original, selector)
            if span is None:
                print(f"  ✗ 规则块未命中：{selector}")
                continue
            if old not in original[span[0]:span[1]]:
                print(f"  ✗ 锚点未命中：{selector} 的 `{old}`")
                continue
            s, e = span
            body = original[s:e].replace(old, new, 1)
            CSS.write_text(original[:s] + body + original[e:], encoding="utf-8")
            FAILS.clear()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    part_b()
                red = list(FAILS)
            finally:
                CSS.write_text(original, encoding="utf-8")
                FAILS.clear()
            hit = set(red) == want and len(red) == len(want)
            print(f"  {'✓' if hit else '✗'} {selector} 注入 `{new}`   红 = {red}"
                  + ("" if hit else f"   ← 期望恰好 {sorted(want)}，用例失效"))
            caught += hit
    finally:
        CSS.write_text(original, encoding="utf-8")
        FAILS.clear()
    print(f"  drift(源码面): {caught}/{len(SRC_DRIFT)} 转红"
          f"，元判据 {'✓' if not red0 else '✗'}（基线无误伤）")
    return 0 if caught == len(SRC_DRIFT) and not red0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--drift-test" in argv:
        return drift_test()

    print("=" * 76)
    print("§2.5.75 焦点遮挡（WCAG 2.4.11）自检")
    print("=" * 76)
    geom = part_a()["geom"]
    part_b()
    part_c()
    part_d(geom)

    print("\n" + "=" * 76)
    if FAILS:
        print(f"🔴 {len(FAILS)} 项不达标：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
