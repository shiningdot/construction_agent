#!/usr/bin/env python3
"""配色的单一定义源：JS / 模板里不许有 CSS token 的第二份色值。

§2.5.72 按 WCAG 调过 `:root` 的 8 个色号，并在 `app.css` 里写下「CAICS 五层
与事故等级同值是有意的：同一件事在两处出现，颜色不该有肉眼可辨的差别」——
但那是一句**约定**，没有任何东西在守着它。

§2.5.73 去看这句话守没守住，发现它当时就已经破了：`causes.js` 把五层色、
品牌色、浅底各抄了一份十六进制（7 处），改 token 时没人会想起来还有这一份。
后果是**同一页面上同一个语义两种颜色**——层标号（CSS token，`#9B612C`）与
年代折线（JS 副本，旧橘 `#C77D3A`）都表示 F 层。

判据分两段，缺一不可：

- **静态**：扫 `static/*.js` 与 `templates/*.html` 里所有十六进制色，逐个问
  「它是 `:root` 里某个 token 的当前值吗」。**注释要先剥掉**——本文件自己的
  注释里就写着 `#9B612C` / `#C77D3A` 举例，裸搜会命中（§2.5.63 / §2.5.70 立
  的规矩：注释里提到判据自己的名字，正好把判据的钥匙交出去）。
- **运行时**：打开 `/causes`，把 SVG 里**真的画出来**的颜色与 token 现值比。
  静态那段只能证明「源码里没有孤儿色值」，证明不了「代码真去读了 token」——
  把 7 处硬编码换成另外 7 处硬编码（恰好等于当前 token 值），静态照样绿。

**注入自证是这一节的支点**：往页面注入 `:root { --layer-F: #123456 }`，断言
折线的 stroke **跟着变**。硬编码的实现不会跟着变——那一刻两个实现长得一样，
只有这条能分开。

## 这条判据能看见什么、看不见什么

**看得见**：JS/模板里任何一处与 token 分叉的十六进制色；「代码是否真的从 CSS
读色」这件事。

**看不见**（如实登记，见 §2.5.73 §六）：
- **只看十六进制字面量**。`rgb(13,124,107)`、`rgba(...)`、`hsl(...)` 这些写法
  抓不到（§2.5.73 修掉的 7 处里就有 1 处是 `rgba()` 形式，靠运行时那段才看见）。
- **只扫 `static/*.js` 与 `templates/*.html`**，不含 CSS 自身（`app.css` 里除
  `:root` 外的十六进制色不在范围内——那里本来就该用 `var()`，但那是另一回事），
  也不含内联在 Python 里的 HTML/JS 片段。
- **`templates/*.html` 的登记表是人工维护的**：允许列出确有理由的独立色值，
  **登记不会自动复核**——这是 §2.5.67 立过的那条边界（白名单只写理由就永远不
  复核），故这里对 HTML 干脆不开口子，只对 JS 开口子。
- **颜色对不对、好不好看不在射程内**——它只说「是不是同一个源」。

用法：
    .venv/bin/python3 scripts/check_color_source.py
    .venv/bin/python3 scripts/check_color_source.py --drift-test
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402
from _serve import point_at, serve                            # noqa: E402

CSS = ROOT / "construction_agent" / "web" / "static" / "app.css"
JS_DIR = ROOT / "construction_agent" / "web" / "static"
TPL_DIR = ROOT / "construction_agent" / "web" / "templates"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
PORT = 5141

#: JS 里允许出现的十六进制色：**空表**。有色值就从 CSS 读。
#: 保留这个常量是为了让「加一条豁免」成为一个显式动作，而不是随手写个字面量。
JS_ALLOW: dict[str, str] = {}

FAILS: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


# ── 颜色归一 ────────────────────────────────────────────────────────────

def to_rgb_str(hex_or_rgb: str) -> str:
    """`#9B612C` / `rgb(155, 97, 44)` → `rgb(155,97,44)`（统一便于比较）。"""
    s = str(hex_or_rgb).strip()
    m = re.match(r"^rgba?\(([^)]+)\)$", s)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        return "rgb({},{},{})".format(*[int(round(float(p))) for p in parts[:3]])
    h = s.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return s
    n = int(h, 16)
    return f"rgb({(n >> 16) & 255},{(n >> 8) & 255},{n & 255})"


def strip_comments(src: str) -> str:
    """剥掉 `/* … */` 与行首 `// …`。

    **必须先剥再搜**：本文件（以及 `causes.js`）的注释里就写着色值举例，
    裸搜会命中注释而不是代码——§2.5.63 / §2.5.70 立的规矩。
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def root_tokens() -> dict[str, str]:
    """读 `app.css` 的 `:root { … }`，返回 `{'--name': 归一 rgb}`。

    键**带 `--` 前缀**：这批名字原样传给 `getComputedStyle().getPropertyValue()`，
    去掉前缀会得到一串空字符串（第一版就是这样，`tok` 恒为空 dict，B 段每条都拿
    空集合去比——**判据还没开始判就在报红**，与真空绿是同一个坑的两面）。
    """
    src = CSS.read_text(encoding="utf-8")
    m = re.search(r":root\s*\{(.*?)\}", src, flags=re.S)
    if not m:
        return {}
    out = {}
    for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)):
        v = val.strip()
        if v.startswith("#"):
            out[name] = to_rgb_str(v)
    return out


HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")
#: 函数式写法。**只认数字字面量**：`rgba(${x},0.4)` 里含 `$`/字母，不会命中——
#: 那正是「从变量拼出来的」，本来就不是副本。含 `var(` 的亦然。
RGB_RE = re.compile(r"\brgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+(?:\s*,\s*[\d.]+)?\s*\)")


def scan_files() -> list[tuple[str, int, str]]:
    """扫 JS 与模板，返回 `[(相对路径, 行号, 色值)]`（已剥注释）。

    十六进制与函数式两种写法都收。**第一版只收十六进制**，于是 `rgba(13,124,107,α)`
    这种写了法的副本会被漏掉——而这恰恰是 §2.5.73 修掉的那 8 处里的一处。
    """
    hits = []
    for p in sorted(list(JS_DIR.glob("*.js")) + list(TPL_DIR.glob("*.html"))):
        raw = p.read_text(encoding="utf-8")
        rel = str(p.relative_to(ROOT))
        for i, line in enumerate(raw.splitlines(), 1):
            code = strip_comments(line)
            for m in HEX_RE.finditer(code):
                hits.append((rel, i, m.group(0)))
            for m in RGB_RE.finditer(code):
                hits.append((rel, i, m.group(0)))
    return hits


# ── 运行时取色 ──────────────────────────────────────────────────────────

RUNTIME = r"""
(names) => {
  const root = getComputedStyle(document.documentElement);
  const tokens = {};
  for (const n of names) tokens[n] = root.getPropertyValue(n).trim();
  // 取不到宿主元素时返回 null，**不是空数组**——空数组会让判据静默变成真空绿
  // （第一版把宿主 id 写成 `coocChart`、真实是 `coocMatrix`，fill 恒为空、
  //  `if fills:` 把那条判据整个跳过，而输出照常显示"通过"）。
  const colors = (host, sel, prop) => {
    const h = document.getElementById(host);
    if (!h) return null;
    return [...h.querySelectorAll(sel)].map((el) => getComputedStyle(el)[prop]);
  };
  return {
    tokens,
    lineStroke: colors('timelineChart', 'polyline', 'stroke'),
    dotBg: colors('timelineChart', '.trend-dot:not(.is-base)', 'backgroundColor'),
    dotBaseBg: colors('timelineChart', '.trend-dot.is-base', 'backgroundColor'),
    coocFill: colors('coocMatrix', 'rect.cooc-cell', 'fill'),
  };
}
"""

#: 注入用的假色，故意与原值无关联。
INJECT_HEX = "#123456"
INJECT_RGB = to_rgb_str(INJECT_HEX)

LAYER_TOKENS = ["--layer-M", "--layer-F", "--layer-T", "--layer-H", "--layer-C"]


def run_runtime(extra_css: str | None = None) -> dict:
    """打开 `/causes` 取运行时色值。

    `extra_css` 走**路由拦截**而不是 `add_style_tag`：`causes.js` 把层色写进
    SVG 的 `stroke` **属性**（一个算好的字符串字面量），页面渲染完再改 CSS 变量
    不会回溯改它——`add_style_tag` 那一版因此**永远看不到变化**，连正确实现都
    会被判红（判据本身错了，不是代码有问题）。拦截 css 让它**在首次渲染前**
    生效，才验得到"代码是从 CSS 读的"。
    """
    from playwright.sync_api import sync_playwright

    point_at(main_db_path(), main_repo_root() / "data" / "caiems_staging.db")
    server, base = serve(PORT)
    names = list(root_tokens())
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            if extra_css:
                def handler(route):
                    r = route.fetch()
                    route.fulfill(response=r, body=r.text() + "\n" + extra_css,
                                  headers={**r.headers, "content-type": "text/css"})
                pg.route("**/static/app.css", handler)
            pg.goto(base + "/causes", wait_until="load")
            pg.wait_for_timeout(600)
            data = pg.evaluate(RUNTIME, names)
            b.close()
        return data
    finally:
        server.shutdown()


# ── A 段 ────────────────────────────────────────────────────────────────

def part_a() -> None:
    print("\n[A] 静态：JS / 模板里有没有 CSS token 的第二份色值")
    toks = root_tokens()
    ok(bool(toks), f"读到 app.css 的 :root token（{len(toks)} 个）")
    known = set(toks.values()) | {to_rgb_str(v) for v in JS_ALLOW.values()}
    hits = scan_files()
    orphans = []
    for rel, ln, hexv in hits:
        if to_rgb_str(hexv) in known:
            continue
        orphans.append((rel, ln, hexv, to_rgb_str(hexv)))
    print(f"    JS + 模板里共 {len(hits)} 处色值字面量（已剥注释），"
          f"其中不在 :root token 上的 {len(orphans)} 处")
    # 前缀用 `→` 不用 `·`：`·` 是失败清单的行首，D 段按行首解析失败条目时
    # 会把这份明细一并抓走（§2.5.68 那个坑的同款——"抓输出里某一行"的断言
    # 要问一句"这一行会不会在别的场合也出现"）。
    for rel, ln, hexv, norm in orphans:
        print(f"       → {rel}:{ln}  {hexv}")
    ok(not orphans,
       "JS / 模板里的每个色值都是 :root 某个 token 的当前值",
       "上述色值在 :root 里找不到——要么改从 CSS 读，要么它是谁的第二份副本")


# ── B 段 ────────────────────────────────────────────────────────────────

def part_b(data: dict) -> None:
    print("\n[B] 运行时：图上画出来的颜色 ↔ token 现值")
    tok = {k: to_rgb_str(v) for k, v in data["tokens"].items() if v}
    layer_set = {tok[k] for k in LAYER_TOKENS if k in tok}
    print(f"    五层 token 现值 = {sorted(layer_set)}")

    strokes, dots = data["lineStroke"], data["dotBg"]
    base_bg, cooc = data["dotBaseBg"], data["coocFill"]

    # 宿主取不到 → 返回 None，判红。**不能让它降级成空集合**：
    # 空集合会让下面每条断言都真空通过（第一版的选择器错就栽在这里）。
    for name, got in (("timelineChart > polyline", strokes),
                      ("timelineChart > .trend-dot", dots),
                      ("timelineChart > .trend-dot.is-base", base_bg),
                      ("coocMatrix > rect.cooc-cell", cooc)):
        ok(got is not None, f"{name} 宿主与元素都取到")
    if strokes is None or dots is None or base_bg is None or cooc is None:
        return

    for label, got, want in (
        ("年代折线 stroke", {to_rgb_str(s) for s in strokes}, layer_set),
        ("趋势图例点（非基线）", {to_rgb_str(s) for s in dots}, layer_set),
        ("趋势图例点（基线）", {to_rgb_str(s) for s in base_bg},
         {tok.get("--border", "")}),
    ):
        print(f"    {label}：{sorted(got)}")
        ok(got == want, f"{label} 恰好是 {len(want)} 个 token 现值",
           f"实测 {sorted(got)}，应为 {sorted(want)}")

    # 共现热图：有值的格子是 rgba(brand, α)，只核色相；对角（v=0）走浅底
    # 浏览器返回的 computed 值带空格：`rgba(13, 120, 103, 0.5)`
    rgba_re = re.compile(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+)")
    srcs, flats = set(), set()
    for f in cooc:
        m = rgba_re.match(f)
        if m:
            srcs.add(f"rgb({m.group(1)},{m.group(2)},{m.group(3)})")
        else:
            flats.add(to_rgb_str(f))
    print(f"    共现热图 {len(cooc)} 格：带 alpha {len(srcs)} 种色相 + "
          f"不透明底 {len(flats)} 种")
    ok(bool(srcs), f"共现热图有带色相的格子（{len(srcs)} 种）",
       "一格都没有——宿主或选择器可能又不匹配了")
    bad = srcs - {tok.get("--brand", "")}
    ok(not bad, "共现热图的色相来自 --brand", f"不在 --brand 上的：{sorted(bad)}")
    ok(flats <= {tok.get("--surface-2", ""), ""},
       "共现热图不透明的格子走 --surface-2",
       f"实测 {sorted(flats)}")


# ── C 段 ────────────────────────────────────────────────────────────────

def part_c() -> None:
    print("\n[C] 注入自证：首次渲染就改 token，图上画出来的颜色跟不跟")
    css = f":root {{ --layer-F: {INJECT_HEX}; --brand: {INJECT_HEX}; }}"
    base, inj = run_runtime(), run_runtime(css)

    b_line = {to_rgb_str(s) for s in (base["lineStroke"] or [])}
    i_line = {to_rgb_str(s) for s in (inj["lineStroke"] or [])}
    ok(INJECT_RGB in i_line,
       f"注入 --layer-F 后折线里出现注入色（{INJECT_RGB}）",
       f"折线色没跟着 token 变：{sorted(i_line)}——说明代码是硬编码的，"
       "不是从 CSS 读的（静态那一段分不出这两者：换成另一份恰好等于当前值的"
       "硬编码，A 段照样绿）")
    ok(to_rgb_str(base["tokens"]["--layer-F"]) not in i_line,
       "注入后原来的 --layer-F 现值不再出现（确实是换掉了，不是多画一条）")

    rgba_re = re.compile(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+)")
    i_hue = {f"rgb({m.group(1)},{m.group(2)},{m.group(3)})"
             for f in (inj["coocFill"] or []) if (m := rgba_re.match(f))}
    ok(i_hue == {INJECT_RGB},
       f"注入 --brand 后共现热图色相变成注入色（实测 {sorted(i_hue)}）",
       "共现热图色相没跟着 token 变")


# ── D 段 ────────────────────────────────────────────────────────────────

DRIFT = [
    dict(label="JS 里把层色改回硬编码",
         old="  const layerColor = (key) => cssVar(`--layer-${key}`) || cssVar('--muted') || 'currentColor';",
         new="  const layerColor = (key) => ({M:'#5B6ABF',F:'#C77D3A',T:'#3E8E7E',H:'#B5544A',C:'#7A6BA8'})[key] || '#8A827A';",
         # 硬编码的那组色里 M 恰好等于现值、其余四个是旧值 ⇒ A 段（色值不在 token 上）
         # 与 B 段（集合不再等于五层现值）都会红；基线点用 --border、未动，故不红。
         red={"JS / 模板里的每个色值都是 :root 某个 token 的当前值",
              "年代折线 stroke 恰好是 5 个 token 现值",
              "趋势图例点（非基线） 恰好是 5 个 token 现值"}),
    dict(label="共现热图色相改回硬编码",
         old="`rgba(${rgbTriplet(cssVar('--brand'))},",
         new="`rgba(13,124,107,",
         # 这一处 **A 段抓不到**（`rgba(13,124,107,${…})` 被模板串截断，函数式正则
         # 要求以 `)` 收尾，不命中）——它专门验证"B 段在 A 段看不见时也能抓"。
         red={"共现热图的色相来自 --brand"}),
]


def part_d() -> None:
    print("\n[D] 注入式自证：改真实 causes.js，断言**红的恰好是哪几条**")
    me = Path(__file__).resolve()
    JS = JS_DIR / "causes.js"
    baseline = JS.read_text(encoding="utf-8")       # 循环外存，循环内每次覆盖
    for i, case in enumerate(DRIFT, 1):
        orig = JS.read_text(encoding="utf-8")
        if case["old"] not in orig:
            ok(False, f"用例 {i}「{case['label']}」锚点命中", f"找不到：{case['old']}")
            continue
        try:
            JS.write_text(orig.replace(case["old"], case["new"], 1), encoding="utf-8")
            r = subprocess.run([PY, str(me), "--no-inject"],
                               capture_output=True, text=True, cwd=str(ROOT))
            # 只断言"退出码非 0"太弱：判据有一半在跑也算红，分不清是哪一条抓到的
            # （§2.5.68）。解析失败清单，逐条对上。
            red = {l.strip().lstrip("·").strip()
                   for l in r.stdout.splitlines() if l.strip().startswith("·")}
            ok(red == case["red"], f"用例 {i}「{case['label']}」→ 红的恰好是 "
               f"{len(case['red'])} 条",
               f"实测红 {sorted(red)}，应为 {sorted(case['red'])}")
        finally:
            JS.write_text(orig, encoding="utf-8")
    # 与**跑用例之前**的原文比，不是与它自己比（自比恒真 = 一条不会失败的判据）
    ok(JS.read_text(encoding="utf-8") == baseline, "causes.js 逐字恢复")


# ── E 段 ────────────────────────────────────────────────────────────────

def part_e() -> None:
    print("\n[E] 文档 §2.5.73 ↔ 实测")
    text = DOC.read_text(encoding="utf-8")
    # 核**标题行**而不是 `§2.5.73` 这个字符串：本节正文里没有自引（自引出现在
    # 别的节），拿 `§` 形式去搜会把"标题写了、正文没写"误判成"文档没写这一节"。
    ok(re.search(r"^### 2\.5\.73 ", text, flags=re.M) is not None,
       "文档有 §2.5.73 的标题行")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drift-test", action="store_true")
    ap.add_argument("--no-inject", action="store_true",
                    help="跳过 C 段（D 段的子进程用，避免重复起服务）")
    args = ap.parse_args()

    if args.drift_test:
        part_d()
    else:
        part_a()
        data = run_runtime()
        part_b(data)
        if not args.no_inject:
            part_c()
        part_e()

    print("\n" + "=" * 60)
    if FAILS:
        print(f"🔴 未通过 {len(FAILS)} 条：")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
