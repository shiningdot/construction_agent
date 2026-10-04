#!/usr/bin/env python3
"""文字对比度（WCAG 2.1 AA）全站核查。

前几维核查（§六 渲染、§七 字段、§八 交互、§十二 中文名）都在问「内容对不对」，
§2.5.70 问的是「键盘够不够得着」。**没有一维问过「看不看得清」**——而它同样
是「内容全对、页面全通、鼠标键盘都点了，用户依然读不出来」的那种缺陷。

判据是 WCAG 2.1 AA 的 1.4.3（文字对比度）：
- 正文（< 18.66px，或 < 24px 的非粗体）须 ≥ **4.5:1**
- 大字（≥ 24px，或 ≥ 18.66px 且 font-weight ≥ 700）须 ≥ **3:1**

按 `(color, background-color)` 算相对亮度比，前景取 **computed** 值而不是读
CSS 源码——层叠、`var()` 展开、行内样式全都由浏览器算好，源码里看不出来的组合
（同一个 token 落在三种底色上）在这条判据下各自成立。

## 这条判据能看见什么、看不见什么

**看得见**：任何一处文字与它实际背景的对比度不足，无论来自哪个 token、哪条
CSS 规则、哪次改动。

**看不见**（如实登记，见 §2.5.72 §六）：
- **`opacity` 是近似处理**。整棵子树的 opacity 会与背景混色，这里按
  `eff_fg = mix(bg, fg, opacity)` 折算——忽略「元素自身背景色也被冲淡」这一层，
  误差方向是**高估**对比度（偏保守，宁漏报不误报）。
- **背景只认不透明色**。半透明背景、渐变、图片上的文字（`background-image`）
  只向上找到第一个 `alpha > 0.99` 的祖先色，找不到就落到 `body` 的底色。
  本项目实测无渐变文字，但这是**假设**不是**核验**。
- **半透明文字**（`color` 的 alpha < 0.99）直接跳过并计数，不参与判定。
- **判据管不了「这个颜色好不好看」**——它只说比值够不够。达标是下限不是目标。
- **只测无头 Chromium**。不同渲染引擎的默认字号、字体合成有差异。

用法：
    .venv/bin/python3 scripts/check_contrast_a11y.py
    .venv/bin/python3 scripts/check_contrast_a11y.py --drift-test
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402
from _serve import point_at, serve                            # noqa: E402

CSS = ROOT / "construction_agent" / "web" / "static" / "app.css"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
PORT = 5140

FAILS: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    """记一条判据。**只往 FAILS 里记，不改变返回值**。"""
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


#: 量一页的文字对比度。返回 `{items: [...], ...}`。
#:
#: 三个刻意的写法：
#: 1. **只看有直接文本子节点的元素**——否则祖先会把它整棵子树的文字重复计一遍。
#: 2. **用 `checkVisibility()` 而不是「在不在 DOM 里」**——§2.5.68 实测过：闭合的
#:    `<details>` 里的元素在 DOM 里、`offsetParent` 非 null、`clientHeight` 有值，
#:    但用户看不见。看不见的文字不该被判「对比度不足」。
#: 3. **`opacity` 折算**——`.is-off { opacity: .45 }` 这类整行压暗，不折算会漏报。
MEASURE = r"""
() => {
  function parseRGB(s) {
    const m = String(s).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,\s\/]+/).filter(x => x !== '').map(parseFloat);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function lum(c) {
    const f = v => { v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function ratio(a, b) {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  function mix(bg, fg, o) {   // fg 以 alpha=o 覆盖在 bg 上
    return { r: bg.r + (fg.r - bg.r) * o,
             g: bg.g + (fg.g - bg.g) * o,
             b: bg.b + (fg.b - bg.b) * o, a: 1 };
  }
  function opacityOf(el) {    // 自身与全部祖先的 opacity 累乘
    let o = 1, n = el;
    while (n && n.nodeType === 1) {
      const v = parseFloat(getComputedStyle(n).opacity);
      if (!isNaN(v)) o *= v;
      n = n.parentElement;
    }
    return o;
  }
  // WCAG 2.1 的 1.4.3 明文豁免「非活动用户界面组件」。判据取**结构性依据**
  // （DOM 上真有 disabled / aria-disabled），不是一张"允许不达标"的白名单——
  // §2.5.67 的教训是白名单只写理由就永远不复核。
  function inactive(el) {
    let n = el;
    while (n && n.nodeType === 1) {
      if (n.disabled === true) return true;
      if (n.getAttribute && n.getAttribute('aria-disabled') === 'true') return true;
      n = n.parentElement;
    }
    return false;
  }
  const items = [], skipped = { alphaText: 0, alphaBg: 0 };
  let total = 0, hidden = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let node = document.body;
  while (node) {
    const texts = Array.from(node.childNodes)
      .filter(n => n.nodeType === 3 && n.nodeValue.trim().length > 0);
    if (texts.length) {
      total++;
      const cs = getComputedStyle(node);
      const txt = texts.map(n => n.nodeValue.trim()).join(' ').slice(0, 40);
      const visible = cs.visibility !== 'hidden' && cs.display !== 'none' &&
                      node.checkVisibility && node.checkVisibility();
      if (!visible) { hidden++; }
      else {
        const fg0 = parseRGB(cs.color);
        if (fg0 && fg0.a > 0.99) {
          // 背景：向上找第一个不透明背景色
          let bg = null, n2 = node, sawAlpha = false;
          while (n2 && n2 !== document.documentElement) {
            const c = parseRGB(getComputedStyle(n2).backgroundColor);
            if (c && c.a > 0.99) { bg = c; break; }
            if (c && c.a > 0) sawAlpha = true;
            n2 = n2.parentElement;
          }
          if (!bg) {
            const bc = parseRGB(getComputedStyle(document.body).backgroundColor);
            bg = (bc && bc.a > 0.99) ? bc : { r: 255, g: 255, b: 255, a: 1 };
          }
          if (sawAlpha) skipped.alphaBg++;
          const o = opacityOf(node);
          const fg = o < 0.999 ? mix(bg, fg0, o) : fg0;   // 见 docstring「看不见」
          const fs = parseFloat(cs.fontSize) || 16;
          const fw = parseInt(cs.fontWeight) || 400;
          const large = fs >= 24 || (fs >= 18.66 && fw >= 700);
          items.push({
            txt: txt,
            cls: (node.className && String(node.className).slice(0, 40)) || node.tagName,
            color: cs.color,
            bg: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
            fs: fs, fw: fw, opacity: Math.round(o * 100) / 100,
            ratio: Math.round(ratio(fg, bg) * 100) / 100,
            need: large ? 3.0 : 4.5,
            inactive: inactive(node),
          });
        } else { skipped.alphaText++; }
      }
    }
    node = walker.nextNode();
  }
  return { items: items, total: total, hidden: hidden, skipped: skipped };
}
"""


def sample_detail_id(main_db: str) -> str | None:
    """与 `screenshot_web.py` 同一口径挑详情页样本：有编码 + 有摘要 + 有伤亡 + 坍塌子类。"""
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    try:
        r = con.execute(
            "SELECT a.id FROM accidents a JOIN caics_causes c ON c.accident_id = a.id"
            " WHERE a.summary IS NOT NULL AND TRIM(a.summary) <> ''"
            "   AND a.deaths IS NOT NULL AND a.collapse_subtype IS NOT NULL"
            "   AND a.collapse_subtype <> '不适用'"
            " GROUP BY a.id ORDER BY COUNT(c.code) DESC LIMIT 1").fetchone()
        return r[0] if r else None
    finally:
        con.close()


def measure_all(extra_css: str | None = None,
                pages: list[str] | None = None) -> list[dict]:
    """起服务、逐页量。`extra_css` 非空时往每页注入一段样式（用于反向自证）。

    `pages` 传值时只量这几页——C 段的自证只需要一页，六页跑三遍太慢。
    """
    main_db = main_db_path()
    staging = main_repo_root() / "data" / "caiems_staging.db"
    point_at(main_db, staging)
    server, base = serve(PORT)
    if pages is None:
        pages = ["/", "/causes", "/collect", "/extract", "/review"]
        aid = sample_detail_id(main_db)
        if aid:
            pages.append(f"/accident/{aid}")
    out: list[dict] = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            for path in pages:
                pg.goto(base + path, wait_until="networkidle")
                if path.startswith("/accident/"):
                    # 详情页的内容大半藏在折叠里（§2.5.68 实测首屏只覆盖 32.5%），
                    # 全展开再量——否则那部分是「没量到」而不是「量了没问题」。
                    pg.evaluate("() => document.querySelectorAll('details')"
                                ".forEach(d => d.open = true)")
                    pg.wait_for_timeout(300)
                if extra_css:
                    pg.add_style_tag(content=extra_css)
                    pg.wait_for_timeout(120)
                r = pg.evaluate(MEASURE)
                below = [x for x in r["items"] if x["ratio"] < x["need"]]
                bad = [x for x in below if not x["inactive"]]
                exempt = [x for x in below if x["inactive"]]
                out.append({"page": path, "res": r, "bad": bad, "exempt": exempt})
            b.close()
    finally:
        server.shutdown()
    return out


def _marks(rows: list[dict]) -> dict:
    """机器可读的断言面：每页违规数 + 违规的 (色, 底, 字号, 类名) 唯一集合。"""
    return {
        "per_page": {r["page"]: len(r["bad"]) for r in rows},
        "kinds": sorted({f"{x['color']}|{x['bg']}|{x['fs']:.0f}|{x['cls']}"
                         for r in rows for x in r["bad"]}),
    }


def part_a(rows: list[dict]) -> None:
    print("\n[A] 六页实测：可见文字 ↔ WCAG 2.1 AA")
    total_items = total_bad = total_ex = 0
    for r in rows:
        n, nb = len(r["res"]["items"]), len(r["bad"])
        ne = len(r["exempt"])
        total_items += n
        total_bad += nb
        total_ex += ne
        worst = min((x["ratio"] for x in r["bad"]), default=None)
        note = f"最低 {worst:.2f}" if worst is not None else "—"
        ex = f"  豁免 {ne}" if ne else ""
        print(f"    {r['page']:<26} 可见文字 {n:>4}  低于阈值 {nb:>4}{ex}  {note}")
    print(f"    ── 合计 {total_bad} / {total_items}（另有豁免 {total_ex}）")
    ok(total_bad == 0, "全部可见文字达到 WCAG 2.1 AA（正文 4.5:1 / 大字 3:1）",
       f"实测 {total_bad} / {total_items} 低于阈值")
    # 豁免的依据是**结构性**的（DOM 上真有 disabled / aria-disabled，见 MEASURE），
    # 不是一张"允许不达标"的文字白名单。这条判据防的是**后门**：给某处加个
    # `aria-disabled="true"` 就能让它从 A 段消失——那正是不该豁免的东西。
    ex_kinds = sorted({x["cls"] for r in rows for x in r["exempt"]})
    ok(set(ex_kinds) <= {"BUTTON"},
       f"豁免的只有禁用控件（分页按钮），实测类名 {ex_kinds or '（无）'}",
       f"出现了未登记的豁免类名：{sorted(set(ex_kinds) - {'BUTTON'})}")
    for r in rows:
        for x in r["exempt"]:
            print(f"    豁免 · {r['page']:<26} {x['ratio']:.2f}  {x['txt']!r}")
    print("    MARKS " + json.dumps(_marks(rows), ensure_ascii=False))


def part_b(rows: list[dict]) -> None:
    print("\n[B] 归因：按 (前景 / 底色 / 字号 / 类名) 归并")
    seen: dict[tuple, list] = {}
    for r in rows:
        for x in r["bad"]:
            k = (x["color"], x["bg"], round(x["fs"]), x["fw"], x["cls"], x["opacity"])
            seen.setdefault(k, [0, x["ratio"], x["need"], x["txt"]])
            seen[k][0] += 1
    if not seen:
        print("    （无违规，无需归因）")
        return
    for (color, bg, fs, fw, cls, op), (n, ratio, need, txt) in sorted(
            seen.items(), key=lambda kv: -kv[1][0])[:15]:
        opn = "" if op == 1 else f" opacity={op}"
        print(f"    ×{n:<4} {ratio:>6.2f}<{need}  {fs}px/{fw}{opn}  "
              f"{color} on {bg}  [{cls}]  {txt!r}")
    print(f"    ── 共 {len(seen)} 种（前 15 列出）")


#: 反向自证用的两段注入样式。不碰任何文件——`add_style_tag` 往页面里加一条即可。
#: ① 把 `--muted` 还原成改动前的值，**用它的那批文字**必须重新违规——这一条证
#:    的是「A 段那 757 → 0 确实来自 token 改动」，不是页面碰巧变了样。
#: ② 给一批文字加 `opacity: .45`，违规必须**新增**——这一条证的是 `MEASURE` 里
#:    的 opacity 折算真的在算（前提是这批文字单独看是达标的，不然说不清是它引起的）。
INJECTS = [
    ("把 `--muted` 还原成旧值 `#8A827A`", ":root { --muted: #8A827A !important; }",
     None, "/"),
    ("给一批文字加 `opacity: .45`",
     ".panel-note, .stat-label, .field-key, .citation-key, .title-meta, .muted "
     "{ opacity: .45 !important; }",
     {"panel-note", "stat-label", "field-key", "citation-key", "title-meta", "muted"},
     "/"),
]


def part_c(rows: list[dict]) -> None:
    print("\n[C] 注入式自证：判据能不能看见颜色变化")
    base_bad = sum(len(r["bad"]) for r in rows)
    print(f"    基线违规 {base_bad}（记录用，本身不作判据——它已在 A 段判过）")
    for label, css, want_cls, page in INJECTS:
        try:
            rows2 = measure_all(extra_css=css, pages=[page])
        except Exception as e:                                # noqa: BLE001
            ok(False, f"注入「{label}」后可重测", f"重测失败：{e}")
            continue
        injected = sum(len(r["bad"]) for r in rows2)
        base_page = sum(len(r["bad"]) for r in rows if r["page"] == page)
        got = {x["cls"] for r in rows2 for x in r["bad"]}
        ok(injected > base_page,
           f"注入「{label}」后违规新增（{page} 页 {base_page} → {injected}）",
           f"实测 {base_page} → {injected}，判据看不见这次注入")
        if want_cls is not None:
            # 只断言"新增的都是被注入的那批"——防的是折算把别的元素也算进去。
            ok(got and got <= want_cls,
               f"新增的违规只落在被注入的类名里（实测 {sorted(got)}）",
               f"出现了注入之外的类名：{sorted(got - want_cls)}")


#: 源码面自证：改**真实** `app.css` 的 token，端到端跑一遍，退出码必须转 1。
DRIFT = [
    dict(label="`--muted` 改回旧值", old="--muted:       #756E67;",
         new="--muted:       #8A827A;"),
    dict(label="`--sev-3` 改回旧值", old="--sev-3: #9B612C;",
         new="--sev-3: #C77D3A;"),
]


def part_d() -> int:
    print("\n[D] 注入式自证：改真实 app.css，断言退出码转红")
    orig = CSS.read_text(encoding="utf-8")
    bad = 0
    try:
        for i, c in enumerate(DRIFT, 1):
            if c["old"] not in orig:
                print(f"    🔴 用例 {i}「{c['label']}」锚点未命中——用例本身失效")
                bad += 1
                continue
            CSS.write_text(orig.replace(c["old"], c["new"]), encoding="utf-8")
            r = subprocess.run([PY, str(Path(__file__).resolve()), "--no-inject"],
                               capture_output=True, text=True, cwd=str(ROOT))
            good = r.returncode == 1
            n = re.search(r"合计 (\d+) / (\d+)", r.stdout)
            print(f"    {'✅' if good else '🔴'} 用例 {i}「{c['label']}」→ "
                  f"退出码 {r.returncode}，合计 "
                  f"{n.group(0)[3:] if n else '?'}" + ("" if good else "（期望 1）"))
            if not good:
                bad += 1
            CSS.write_text(orig, encoding="utf-8")
    finally:
        CSS.write_text(orig, encoding="utf-8")
    ok(CSS.read_text(encoding="utf-8") == orig, "app.css 逐字恢复")
    ok(bad == 0, f"注入用例全部按预期转红（{len(DRIFT)-bad}/{len(DRIFT)}）")
    return 0 if bad == 0 else 1


def part_e() -> None:
    print("\n[E] 文档 §2.5.72 ↔ 实测")
    if not DOC.exists():
        ok(False, "文档存在", str(DOC))
        return
    text = DOC.read_text(encoding="utf-8")
    for pat, label in [
        (r"§2\.5\.72", "文档含 §2.5.72"),
    ]:
        ok(re.search(pat, text) is not None, label, f"没匹配到 {pat!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="文字对比度（WCAG 2.1 AA）全站核查")
    ap.add_argument("--drift-test", action="store_true", help="注入式自证")
    ap.add_argument("--no-inject", action="store_true",
                    help="跳过 C 段（注入自证）——D 段要跑好几遍主检时用")
    args = ap.parse_args()

    if args.drift_test:
        return part_d()

    rows = measure_all()
    part_a(rows)
    part_b(rows)
    if not args.no_inject:
        part_c(rows)
    part_e()

    print("\n" + "=" * 60)
    if FAILS:
        print(f"🔴 {len(FAILS)} 条未通过：")
        for f in FAILS:
            print(f"    - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
