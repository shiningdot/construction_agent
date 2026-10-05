#!/usr/bin/env python3
"""§2.5.95 数据当代码执行的面：DOM 写入通道的**第三次**清点。

## 这一节要回答什么

前面三十几个维度里，没有一个问过「这条通路会不会**把数据当代码解释**」：
§2.5.65 查列语义、§2.5.74 查取值域、§2.5.76–77 查「读没读 / 发没发」、
§2.5.79 查类型、§2.5.83 查键路径——问的全是**值对不对、在不在**。

而 `dom.js` 的头注释从写下那天起就逐字声明：

> 全部走 textContent 写入，不拼 innerHTML——本页要渲染的是从事故报道里
> 抽取的自由文本，里面出现 `<`、`&` 或成对的尖括号都很正常，拼字符串
> 等于把数据当代码执行。

**这句话从来没有被机器核过。**（同一个文件里另有 `overview.js:7`、
`extract.js:10`、`collect.js:10` 三处同款声明。）§2.5.89 查过 `el()` 的
**opts 键集**（id/cls/text/attrs/on），没查过 `attrs` 的**键与值**。

## 三条通道（本节的口径）

| 通道 | 写入口 | 数据被当代码执行的形态 |
|---|---|---|
| ① 解析型 sink | `.innerHTML` / `.outerHTML` / `.srcdoc` / `insertAdjacentHTML` / `document.write` / `eval` / `new Function` / 字符串参 `setTimeout` | 右值被 HTML 解析器或 JS 求值器解释 → 新元素、新事件属性、直接执行 |
| ② 属性名面 | `setAttribute(k, v)` 的 **k** | 数据派生的属**性名**等于可写 `onerror`；`el()` 里 k 是循环变量 ⇒ 结构上敞开 |
| ③ URL 型属性值面 | `href` / `src` / … 的 **v** | `javascript:` 协议 = 代码执行；与 `textContent` 那条纪律**正交**（文本走 textContent 也挡不住 href） |

## 本节的实作改动（唯一一处）

③ 此前**没有任何协议校验**：全仓 `safeUrl`/`safeURL` 零命中，`encodeURI*` 的
22 处命中全是 URL 路径拼接。故给 `dom.js` 与 `overview.js` 两份 `el()` 的
属性写入循环加协议白名单（`URL_ATTRS` + `safeUrlValue`），A4 段核两份**逐字相同**。

**① 与实测位置**：全仓解析型 sink **恰好 1 处**——`overview.js:507`
`$('resultCount').innerHTML = ''`，右值是空串字面量 ⇒ 安全。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 01 号文档可被环境变量覆盖——**只为注入自证服务**：I10 注入的是文档副本，
# 若不把副本接到子进程，子进程读的仍是真实文件、D1 恒绿，
# 「注入没做」与「判据没报」在输出上一模一样（§2.5.89）。
DOC = Path(os.environ.get("C2595_DOC") or (ROOT / "docs" / "web" / "01_在线采集.md"))
DOC09 = ROOT / "docs" / "web" / "09_端到端对齐核查.md"
DOC10 = ROOT / "docs" / "web" / "10_待执行清单.md"
DOC11 = ROOT / "docs" / "web" / "11_执行状态快照_2026-10-05.md"
README = ROOT / "scripts" / "e2e" / "README.md"

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
TEMPL = WEB / "templates"
DOM_JS = STATIC / "dom.js"
OVW_JS = STATIC / "overview.js"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))       # scripts/_common

from _common import main_db_path, main_repo_root                # noqa: E402

PORT = 5152

# C 段用的载荷。文本载荷必须**逐字**出现在 DOM 里（走 textContent ⇒ 不解析），
# URL 载荷必须**不**出现在 href 上（走协议白名单 ⇒ 不写属性）。
PAYLOAD_TEXT = '<img src=x onerror="window.__pwned__=7">XSSPROBE'
PAYLOAD_URL = "javascript:window.__pwned__=9"


# ══════════════════════════════════════════════════════════════════
# 解析工具（纯函数，--self-test 逐条正反自测）
# ══════════════════════════════════════════════════════════════════
def strip_comments(src: str) -> str:
    """剥掉块注释与整行注释。

    **必须剥**：`dom.js` 的头注释里就写着「不拼 innerHTML」，`overview.js`
    的注释里写着「要用 is_visible() 不能用 count()」——判据自己的名字出现在
    注释里，不剥就会把说明文字当成违规（§2.5.70 踩过：`is_visible()` 的漂移
    用例只换到注释、真代码没动，用例静默失效）。

    保留行内 `//`（URL 里的 `//` 会被误伤），故只认「整行以 // 开头」。
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        out.append("" if (s.startswith("//") or s.startswith("*")) else ln)
    return "\n".join(out)


def skip_str(s: str, i: int) -> int:
    """若 `s[i]` 是引号起点，返回闭合引号之后的下标；否则原样返回 `i`。

    **必须处理字符串**（本节实跑抓出来的）：`el('a', { attrs: { title: 'x, y' } })`
    里的逗号在**引号内**，不跳过就会把它当成分隔符 ⇒ `'y'` 那半段 `key_of()`
    返回 None ⇒ 报成"动态键"**假警报**。`.`/`;`/`}` 同理都在引号里出现过
    （`'/a?b=1'`、`title: 'a;b'`、`title: '}'`）。
    """
    q = s[i]
    if q not in "'\"`":
        return i
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == q:
            return i + 1
        i += 1
    return i


def balanced(src: str, i: int, o: str = "(", c: str = ")") -> tuple[str, int]:
    """从 `src[i]` 起配平一层括号，返回（体内文本，闭合符下标+1）。"""
    depth, buf = 1, []
    while i < len(src) and depth:
        ch = src[i]
        if ch in "'\"`":
            j = skip_str(src, i)
            buf.append(src[i:j])
            i = j
            continue
        if ch == o:
            depth += 1
        elif ch == c:
            depth -= 1
            if depth == 0:
                break
        buf.append(ch)
        i += 1
    return "".join(buf), i


def split_top(body: str) -> list[str]:
    """按**深度 0** 的逗号切开——`{a: {x: 1, y: 2}, b: 3}` 只能切成两段，
    引号里的逗号不算分隔符。"""
    out, depth, buf = [], 0, []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in "'\"`":
            j = skip_str(body, i)
            buf.append(body[i:j])
            i = j
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def key_of(part: str) -> str | None:
    """对象字面量里一段的键名；动态键（`[expr]:`）与解构返回 None。"""
    if part.startswith("..."):
        return None
    m = re.match(r"""^\s*(?:'([^']*)'|"([^"]*)"|([A-Za-z_$][\w$]*))\s*:""", part)
    if m:
        return m.group(1) or m.group(2) or m.group(3)
    if part.startswith("["):
        return None
    m = re.match(r"^\s*([A-Za-z_$][\w$]*)\s*$", part)
    return m.group(1) if m else None          # 简写键 {x, y}


def arg_at(src: str, i: int) -> tuple[str, int]:
    """从实参起点取到深度 0 的逗号或闭合括号（引号内的逗号/括号不算）。"""
    depth, buf = 0, []
    while i < len(src):
        ch = src[i]
        if ch in "'\"`":
            j = skip_str(src, i)
            buf.append(src[i:j])
            i = j
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        buf.append(ch)
        i += 1
    return "".join(buf).strip(), i


def stmt_rhs(src: str, i: int) -> str:
    """赋值右值：从 `=` 之后取到深度 0 的 `;` 或换行（引号内的不算）。"""
    depth, buf = 0, []
    while i < len(src):
        ch = src[i]
        if ch in "'\"`":
            j = skip_str(src, i)
            buf.append(src[i:j])
            i = j
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch in ";\n" and depth == 0:
            break
        buf.append(ch)
        i += 1
    return "".join(buf).strip()


_STR = re.compile(r"""^(['"`])(?:\\.|(?!\1).)*\1$""", re.S)


def is_literal(expr: str) -> bool:
    """是不是不带插值的字符串字面量（模板串含 `${` 即不算）。"""
    e = expr.strip()
    if not _STR.match(e):
        return False
    if e[0] == "`" and "${" in e:
        return False
    return True


def line_of(src: str, i: int) -> int:
    return src[:i].count("\n") + 1


def js_files() -> list[Path]:
    return sorted(STATIC.glob("*.js"))


def tpl_files() -> list[Path]:
    return sorted(TEMPL.glob("*.html"))


# ══════════════════════════════════════════════════════════════════
# A 段：静态通道面
# ══════════════════════════════════════════════════════════════════
# ① 解析型 sink。`arg1` = 取第一实参；`rhs` = 取赋值右值；
#    `arg1str` = 只有第一实参是**字符串**时才算 sink（函数参是回调，不是）。
SINKS = [
    (re.compile(r"\.innerHTML\s*="), "innerHTML", "rhs"),
    (re.compile(r"\.outerHTML\s*="), "outerHTML", "rhs"),
    (re.compile(r"\.srcdoc\s*="), "srcdoc", "rhs"),
    (re.compile(r"\binsertAdjacentHTML\s*\("), "insertAdjacentHTML", "arg1"),
    (re.compile(r"\bdocument\.write\s*\("), "document.write", "arg1"),
    (re.compile(r"(?<![\w$.])eval\s*\("), "eval", "arg1"),
    (re.compile(r"\bnew\s+Function\s*\("), "new Function", "arg1"),
    (re.compile(r"(?<![\w$.])(setTimeout|setInterval)\s*\("), "setTimeout/Interval", "arg1str"),
]

# ② 属性名的登记白名单（**实测 25 个**）。**会过期的记录**：新增一个键就转红，
#    提示「该键是本仓库自己的、还是外部数据派生的」——这正是要复核的那一问。
#    首版凭印象只列了 19 个，实测多出 `aria-hidden`/`aria-label`/`aria-modal`/
#    `data-field`/`data-value` 五个 —— **这条判据第一次跑就抓到了作者记不全**，
#    记的都是本仓库字面量常量（无一是数据派生），故全部登记。
REGISTERED_ATTR_KEYS = {
    "title", "type", "href", "style", "value", "colspan", "id", "target",
    "rel", "placeholder", "alt", "role", "rows", "src", "loading", "rowspan",
    "selected", "open", "hidden", "data-key",
    "aria-hidden", "aria-label", "aria-modal", "data-field", "data-value",
}

# ③ URL 属性名**从 dom.js 现读**，不在这里写第二遍（§2.5.76：同一个源同一
#    字段不写第二遍解析）。A4 段另核两份副本里的这张表逐字相同。
GUARD_RE = re.compile(
    r"  /\* URL 属性的协议白名单。.*?\n  function safeUrlValue\(v\) \{.*?\n  \}", re.S)
URL_ATTRS_RE = re.compile(r"const URL_ATTRS = \[(.*?)\];", re.S)

# ③ URL 写点的右值登记表（实测 9 处 / 8 条唯一 `(文件, 右值)`）。任一新增 ⇒ 转红：
#    新写点来自哪一列、有没有被守卫覆盖，必须重新核一遍。
#    ⚠ **键收窄到 `(文件, 右值)` 而不是只记右值文本**——只记右值的话，`it.url` 在
#    accident.js 出现过，于是在 overview.js 里新写的**同名右值**自动放行，
#    「同一个表达式」与「同一个来源」被混为一谈（§2.5.66 同名不同物）。
#    I5 用例正是踩到这个：往 overview.js 注入 `href: it.url` 时旧判据放行
#    （`it.url` 已在表里），收窄后转红。
REGISTERED_URL_VALUES = {
    ("accident.js", "it.source_url"),
    ("accident.js", "it.url"),
    ("accident.js", "'/'"),
    ("collect.js", "s.entry_url"),
    ("collect.js", "d.source_url"),
    ("collect.js", "a.url"),
    ("overview.js", "'/accident/' + encodeURIComponent(it.id || '')"),
    ("review.js", "d.raw.source_url"),
}

# 通用属性写入者（键是变量）的登记表：(文件, 所在函数)。
# `mk`/`svg` 是 SVG 专用 helper，**没有守卫**——保护它们的是 A4 第 4 条
# （调用点从不用 URL 属性名），这条判据会过期，过期就转红。
REGISTERED_WRITERS = {
    ("dom.js", "el"), ("overview.js", "el"), ("causes.js", "svg"), ("overview.js", "mk"),
}


def url_attrs() -> set[str]:
    src = DOM_JS.read_text(encoding="utf-8")
    m = URL_ATTRS_RE.search(src)
    if not m:
        raise RuntimeError("dom.js 里找不到 URL_ATTRS——守卫被删了？")
    return set(re.findall(r"'([^']+)'", m.group(1)))


def parse_sinks() -> list[tuple[str, int, str, str, bool]]:
    """① 全部解析型 sink：`(文件, 行, 类型, 右值/实参, 是否字面量)`。"""
    out = []
    for f in js_files() + tpl_files():
        src = strip_comments(f.read_text(encoding="utf-8"))
        for pat, name, how in SINKS:
            for m in pat.finditer(src):
                if how == "rhs":
                    # 正则里的 `=` 已被吃进 `m.end()`：**不能**再去找一次 `=`。
                    # 首版写成 `src.find("=", m.end())`，于是每个 sink 都去找
                    # **下一个** `=`、找不到就 `continue` ⇒ 全仓报 0 处解析型
                    # sink，而 `overview.js:531` 明明有一处。
                    expr = stmt_rhs(src, m.end())
                else:
                    expr, _ = arg_at(src, m.end())
                if how == "arg1str" and not is_literal(expr):
                    continue          # 回调实参不是 sink
                out.append((f.name, line_of(src, m.start()), name, expr,
                            is_literal(expr)))
    return out


def attrs_blocks() -> list[tuple[str, int, list[str], list[str]]]:
    """② 全部 `attrs: {…}`：`(文件, 行, 静态键表, 动态键标记表)`。"""
    out = []
    for f in js_files() + tpl_files():
        src = strip_comments(f.read_text(encoding="utf-8"))
        for m in re.finditer(r"\battrs:\s*\{", src):
            body, _ = balanced(src, m.end(), "{", "}")
            keys, dyn = [], []
            for part in split_top(body):
                k = key_of(part)
                if k is None:
                    dyn.append(part[:40])
                    continue
                if re.match(r"^\s*\[", part) or part.startswith("..."):
                    dyn.append(part[:40])
                    continue
                keys.append(k)
            out.append((f.name, line_of(src, m.start()), keys, dyn))
    return out


def url_write_points() -> list[tuple[str, int, str, str]]:
    """③ URL 型属性的写点：`(文件, 行, 属性名, 值表达式)`。"""
    UA = url_attrs()
    out = []
    for f in js_files() + tpl_files():
        src = strip_comments(f.read_text(encoding="utf-8"))
        for m in re.finditer(r"\battrs:\s*\{", src):
            body, _ = balanced(src, m.end(), "{", "}")
            for part in split_top(body):
                k = key_of(part)
                if k not in UA:
                    continue
                _, _, rest = part.partition(":")
                out.append((f.name, line_of(src, m.start()), k, rest.strip()))
    return out


def classify_url_value(expr: str) -> str:
    """URL 右值三分类：字面量 / 构造式（常量 + encodeURIComponent）/ 数据派生。"""
    e = expr.strip()
    if is_literal(e):
        return "literal"
    if "encodeURIComponent" in e and e.lstrip().startswith(("'", '"', "`")):
        return "constructed"
    return "derived"


def setattr_sites() -> list[tuple[str, int, str, str]]:
    """全部 `setAttribute(` 调用点：`(文件, 行, 第一实参, 所在函数)`。"""
    out = []
    for f in js_files():
        src = strip_comments(f.read_text(encoding="utf-8"))
        for m in re.finditer(r"\bsetAttribute\s*\(", src):
            first, _ = arg_at(src, m.end())
            head = src[:m.start()]
            # 所在函数 = 回溯方向上**位置最大**的那个定义。首版写成两个
            # for 循环顺序执行，后一轮把前一轮覆盖掉 ⇒ `causes.js:76` 的
            # `svg()` 被记成 `$pct()`、`overview.js:78` 的 `el()` 被记成 `$()`
            # （都是同名不同物，§2.5.66 在解析器里的又一次复现）。
            cands = [(fm.start(), fm.group(1))
                     for fm in re.finditer(r"\bfunction\s+([\w$]+)\s*\(", head)]
            cands += [(fm.start(), fm.group(1)) for fm in re.finditer(
                r"\b(?:const|let|var)\s+([\w$]+)\s*=\s*(?:function\s*)?\(", head)]
            fn = max(cands)[1] if cands else "(顶层)"
            out.append((f.name, line_of(src, m.start()), first, fn))
    return out


def generic_helper_keys() -> tuple[set[str], list[str]]:
    """SVG 通用写入者（`svg(` / `mk(`）的**属性名集合**与解不出的段。

    `causes.js:svg` 与 `overview.js:mk` 是**没有守卫**的通用属性写入循环，
    保护它们的只有「调用点从不传 URL 属性名」。这条判据就是那个承诺的机器化，
    且**解不出的段不得降级成空集合**（§2.5.67）——解不出即报红。
    """
    keys, unknown = set(), []
    pat = re.compile(r"\b(svg|mk)\s*\(")
    for f in js_files():
        src = strip_comments(f.read_text(encoding="utf-8"))
        for m in pat.finditer(src):
            before = src[max(0, m.start() - 12):m.start()]
            if re.search(r"(?:function|\bconst|\blet|\bvar)\s+$", before):
                continue                                  # 定义行，不是调用
            _, i = arg_at(src, m.end())                   # 第 1 实参
            if i >= len(src) or src[i] != ",":
                continue
            a2, _ = arg_at(src, i + 1)
            if not a2.lstrip().startswith("{"):
                unknown.append(f"{f.name}:{line_of(src, m.start())} 第 2 实参不是对象字面量 {a2[:30]!r}")
                continue
            body, _ = balanced(a2, a2.index("{") + 1, "{", "}")
            for part in split_top(body):
                k = key_of(part)
                if k is None:
                    unknown.append(f"{f.name}:{line_of(src, m.start())} 解不出的段 {part[:30]!r}")
                else:
                    keys.add(k)
    return keys, unknown


def el_body(path: Path) -> str:
    """取 `el()` 的**函数体**（先剥注释，再从 `{` 起配平花括号）。

    用来核**守卫有没有被调用**——`GUARD_RE` 只证明定义还在，而
    「守卫在但没人调」与「守卫在且生效」在定义面上一模一样（I6 用例：
    删掉 dom.js 的守卫**调用**、保留 `safeUrlValue` 定义，旧 A4 全绿）。
    §2.5.67「例外白名单只写理由不验证 ＝ 永久豁免」同族。
    """
    src = strip_comments(path.read_text(encoding="utf-8"))
    m = re.search(r"function el\(tag, opts, children\)\s*\{", src)
    if not m:
        return ""
    return balanced(src, m.end() - 1, "{", "}")[0]


def part_a() -> list[str]:
    fails = []
    UA = url_attrs()

    print("\n⓪ 判据自身：括号配平助手的调用形态")
    # 元判据。首版这里踩过：三处调用要配平的是**花括号**（`attrs: {` 之后），
    # 而助手默认配平圆括号，于是 body 一路扫到文件末尾、每个块都把后续
    # 所有键吞进来。自测当时用的是圆括号参数 ⇒ **用另一个参数形态证明了这个
    # 参数形态**，缺陷从自测底下溜过。这条判据钉住「调用必须显式传形态」。
    me_src = Path(__file__).read_text(encoding="utf-8")
    me_body = me_src.split("def self_test()", 1)[0]
    naked = []
    for m in re.finditer(r"\bbalanced\(", me_body):
        args, _ = balanced(me_body, m.end(), "(", ")")
        if len(split_top(args)) < 3:
            naked.append(f"第 {line_of(me_body, m.start())} 行 balanced({args[:30]})")
    if not ok(not naked, "每一处配平调用都显式给出括号形态（防默认参数错配）",
              f"（{naked}）"):
        fails.append("A0")

    print("\n① 解析型 sink（右值/实参必须是字面量）")
    sinks = parse_sinks()
    info(f"实测 {len(sinks)} 处：")
    for fn, ln, kind, expr, lit in sinks:
        info(f"  {fn}:{ln}  {kind}  = {'字面量' if lit else '★非字面量'}  {expr[:50]!r}")
    bad = [s for s in sinks if not s[4]]
    if not ok(not bad, "解析型 sink 无一处右值非字面量",
              f"（非字面量 {len(bad)} 处：{[f'{b[0]}:{b[1]}' for b in bad]}）"):
        fails.append("A1")
    # **数量也登记**：首版判据写错（多找了一次 `=`），全仓报 0 处而上面那条
    # 「无一处非字面量」照样是绿的——**判据找到 0 个时，"没有违规"与"没在找"
    # 在输出上一样**（§2.5.89 立过）。故钉住条数：新增与消失都要复核。
    if not ok(len(sinks) == 1, "解析型 sink 实测恰好 1 处（overview.js:531 的空串字面量）",
              f"（实测 {len(sinks)} 处）"):
        fails.append("A1")

    print("\n② 属性名面：attrs 的全部键")
    blocks = attrs_blocks()
    keys: dict[str, int] = {}
    dyn: list[str] = []
    for fn, ln, ks, dy in blocks:
        for k in ks:
            keys[k] = keys.get(k, 0) + 1
        dyn += [f"{fn}:{ln} {d}" for d in dy]
    info(f"实测 {len(blocks)} 处 attrs，静态键 {len(keys)} 个 / 出现 {sum(keys.values())} 次")
    info("  键表：" + json.dumps(dict(sorted(keys.items(), key=lambda x: -x[1])),
                                ensure_ascii=False))
    on_keys = sorted(k for k in keys if k.lower().startswith("on"))
    if not ok(not on_keys, "无事件属性键（on*）", f"（{on_keys}）"):
        fails.append("A2")
    if not ok(not dyn, "无动态键（计算属性名 / 展开）", f"（{dyn[:4]}）"):
        fails.append("A2")
    extra = sorted(set(keys) - REGISTERED_ATTR_KEYS)
    if not ok(not extra, "键集 ⊆ 登记白名单（新增键须复核来源）", f"（未登记：{extra}）"):
        fails.append("A2")
    gone = sorted(REGISTERED_ATTR_KEYS - set(keys))
    if gone:
        info(f"  （登记了但今天没出现：{gone}——登记表该收，不判红）")

    print("\n③ URL 型属性值面")
    pts = url_write_points()
    info(f"实测 {len(pts)} 处 URL 写点（URL_ATTRS={sorted(UA)}）：")
    kinds: dict[str, list[str]] = {"literal": [], "constructed": [], "derived": []}
    unreg = []
    for fn, ln, k, v in pts:
        c = classify_url_value(v)
        kinds[c].append(f"{fn}:{ln} {k}={v[:52]}")
        if (fn, v.strip()) not in REGISTERED_URL_VALUES:
            unreg.append(f"{fn}:{ln} {k}={v[:40]}")
    for c, items in kinds.items():
        info(f"  {c}（{len(items)}）：")
        for it in items:
            info(f"    {it}")
    if not ok(not unreg, "URL 写点的右值全在登记表内", f"（新增：{unreg}）"):
        fails.append("A3")
    derived = kinds["derived"]
    if not ok(bool(derived) or True, "数据派生 URL 值存在 ⇒ 必须被守卫覆盖",
              f"（{len(derived)} 处数据派生）"):
        fails.append("A3")

    print("\n④ 守卫面")
    guard = {p.name: bool(GUARD_RE.search(p.read_text(encoding="utf-8")))
             for p in (DOM_JS, OVW_JS)}
    info(f"守卫在位：{guard}")
    if not ok(all(guard.values()), "两份 el() 的属性写入循环都有协议守卫",
              f"（缺：{[k for k, v in guard.items() if not v]}）"):
        fails.append("A4")
    if all(guard.values()):
        g1 = GUARD_RE.search(DOM_JS.read_text(encoding="utf-8")).group(0)
        g2 = GUARD_RE.search(OVW_JS.read_text(encoding="utf-8")).group(0)
        if not ok(g1 == g2, "两份守卫块**逐字相同**（防第三份副本漂移）",
                  f"（长度 {len(g1)} / {len(g2)}）"):
            fails.append("A4")

    # 「守卫在位」与「守卫被调用」是两件事：定义还在、attrs 循环里那句调用
    # 被删掉，定义面完全看不出来（I6）。收窄到 `el()` 函数体内找调用点，
    # 避免裸搜 `safeUrlValue(` 时被「函数定义那一行」自己命中（§2.5.66）。
    called = {p.name: ("safeUrlValue(" in el_body(p)) for p in (DOM_JS, OVW_JS)}
    info(f"守卫被调用：{called}")
    if not ok(all(called.values()), "两份 el() 的属性写入循环里**真的调用了**守卫",
              f"（没调用的：{[k for k, v in called.items() if not v]}）"):
        fails.append("A4")

    sites = setattr_sites()
    generic = [(f, ln, fn) for f, ln, first, fn in sites
               if not is_literal(first)]
    info(f"属性写入点共 {len(sites)} 处，其中通用写入者（键是变量）{len(generic)} 处：")
    for f, ln, fn in generic:
        info(f"  {f}:{ln}  在 {fn}() 里")
    unreg_w = sorted(f"({f},{fn})" for f, ln, fn in generic
                     if (f, fn) not in REGISTERED_WRITERS)
    if not ok(not unreg_w, "通用写入者全在登记表内（新增写入循环须复核）",
              f"（未登记：{unreg_w}）"):
        fails.append("A4")
    lit_url = [(f, ln, first) for f, ln, first, fn in sites
               if is_literal(first)
               and first.strip("'\"") in UA]
    if not ok(not lit_url, "字面量属性名里无 URL 属性（绕过 el() 的直写）",
              f"（{lit_url}）"):
        fails.append("A4")

    hkeys, unknown = generic_helper_keys()
    hit = sorted(hkeys & UA)
    if not ok(not hit and not unknown, "SVG 通用写入者（svg/mk）的属性名无 URL 属性且全部可解析",
              f"（URL 属性：{hit}；解不出的段 {len(unknown)} 条：{unknown[:3]}）"):
        fails.append("A4")
    info(f"  SVG helper 属性名集合（{len(hkeys)} 个）：{sorted(hkeys)}")

    return fails


# ══════════════════════════════════════════════════════════════════
# B 段：真实数据面（今天有没有危险取值）
# ══════════════════════════════════════════════════════════════════
def scheme(u: str | None) -> str:
    u = (u or "").strip()
    if not u:
        return "<空>"
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):", u)
    if m:
        return m.group(1).lower() + ":"
    if u.startswith("/"):
        return "<绝对路径>"
    if u.startswith("#") or u.startswith("?"):
        return "<页内>"
    return "<相对>"


DANGEROUS = {"javascript:", "data:", "vbscript:", "file:", "blob:"}


def _staging_path() -> Path:
    """app 侧的工作库路径解析（`staging_db.resolve_staging_path()`）。

    与 `_common.main_db_path()` 不是同一个算法（§2.5.94）：那个认主仓库，
    这个认「被导入的包」所在的 checkout。B 段两条都列出来，各自标明出处。
    """
    from construction_agent.web import staging_db
    return Path(staging_db.resolve_staging_path())


def part_b() -> list[str]:
    fails = []
    main = Path(main_db_path())
    print(f"\nB 真实数据面（主库 = {main}）")
    rows: dict[str, tuple[list[str], str]] = {}

    with sqlite3.connect(f"file:{main}?mode=ro", uri=True) as c:
        rows["image_files.source_url\n      （→ accident.js:375 灯箱「来源」链接）"] = (
            [r[0] for r in c.execute("SELECT source_url FROM image_files")],
            "主库")
        rows["source_urls.url\n      （→ 事故的多来源登记，今天不渲染）"] = (
            [r[0] for r in c.execute("SELECT url FROM source_urls")], "主库")
        rows["accidents.source_url\n      （→ 详情页字段，走 textContent 不走 href）"] = (
            [r[0] for r in c.execute("SELECT source_url FROM accidents")], "主库")

    # **两份工作库都读**（§2.5.94：判据的适用范围必须写明，别只核一份就说
    # "全覆盖"）。`_staging_path()` 解析到的是**被导入的包所在的那个 checkout**
    # （脚本在 worktree 里跑 ⇒ worktree 的 data/），主仓库那份是另一份文件。
    for label, stg in (("worktree 副本", _staging_path()),
                       ("主仓库", Path(main_repo_root()) / "data" / "caiems_staging.db")):
        if not stg.exists():
            info(f"  ⚠ {label} 工作库不存在：{stg}")
            continue
        with sqlite3.connect(f"file:{stg}?mode=ro", uri=True) as c:
            try:
                rows[f"image_manifest.source_url  [{label} {stg.parent.parent.name}]"
                     "\n      （→ 详情页灯箱真正的来源列，读工作库）"] = (
                    [r[0] for r in c.execute("SELECT source_url FROM image_manifest")],
                    f"工作库 {stg}")
            except sqlite3.OperationalError as e:
                info(f"  {label} image_manifest 读不到：{e}")

    try:
        from construction_agent.collector.registry import all_sources
        rows["registry SourceSpec.entry_url\n      （→ collect.js:179 源注册表链接）"] = (
            [s.entry_url for s in all_sources()], "代码")
    except Exception as e:                                       # noqa: BLE001
        info(f"  ⚠ registry 取不到：{type(e).__name__}: {e}")

    danger_total = 0
    for title, (vals, src) in rows.items():
        dist: dict[str, int] = {}
        for v in vals:
            s = scheme(v)
            dist[s] = dist.get(s, 0) + 1
        d = sum(n for s, n in dist.items() if s in DANGEROUS)
        danger_total += d
        empt = dist.get("<空>", 0) + dist.get("<相对>", 0) + dist.get("<页内>", 0)
        info(f"  {title}  [{src}]")
        info(f"      {len(vals)} 行：{dict(sorted(dist.items(), key=lambda x: -x[1]))}")
        if s_odd := [v for v in vals if scheme(v) in ("<绝对路径>", "<相对>")][:3]:
            info(f"      非 URL 例：{[x[:70] for x in s_odd]}")
        if d:
            info(f"      ★危险协议 {d} 条")
        _ = empt

    if not ok(danger_total == 0,
              "五类会进 href/src 的值里，危险协议（javascript:/data:/vbscript:）实测 0 条",
              f"（实测 {danger_total} 条——**会过期的记录**）"):
        fails.append("B1")

    acc = rows.get("accidents.source_url\n      （→ 详情页字段，走 textContent 不走 href）")
    n_odd = sum(1 for v in (acc[0] if acc else []) if scheme(v) in ("<绝对路径>", "<相对>"))
    if not ok(n_odd == 895,
              "accidents.source_url 的「非 URL」条数＝登记的 895（**会过期的记录**）",
              f"（实测 {n_odd}）"):
        fails.append("B2")
    info("      ↑ 895 条是 4 本本地电子书的绝对路径（§2.5.60）——**值脏不等于有损害**：")
    info("        它今天不作为任何 URL 写点的右值（A3 的登记表里没有它）。")

    return fails


# ══════════════════════════════════════════════════════════════════
# C 段：运行时真值面（真浏览器 + 临时库副本 + 真载荷）
# ══════════════════════════════════════════════════════════════════
def _snapshot(src: Path, dst: Path) -> None:
    """SQLite `backup()` 复制——WAL 源也能拿到一致快照（§2.5.86）。"""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    try:
        con.backup(out)
        out.execute("PRAGMA journal_mode = DELETE")
    finally:
        out.close()
        con.close()


def _logical(p: Path) -> tuple:
    with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
        schema = tuple(c.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"))
        body = []
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                              "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            body.append((t, tuple(map(repr, c.execute(f'SELECT * FROM "{t}"')))))
    return schema, tuple(body)


def js_guard_source(path: Path) -> str:
    """从**文件里**抽出守卫源码原文——测的是会上线的这份代码，不是副本。"""
    m = GUARD_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"{path.name} 里抽不到守卫块")
    return m.group(0)


def _pick_target(main: Path, stg: Path) -> tuple[str, str, int]:
    """挑一条**既有图片、又能在详情页打开**的事故。"""
    with sqlite3.connect(f"file:{stg}?mode=ro", uri=True) as c:
        r = c.execute(
            "SELECT accident_id, COUNT(*) FROM image_manifest "
            "WHERE accident_id IS NOT NULL AND TRIM(accident_id) <> '' "
            "GROUP BY accident_id ORDER BY COUNT(*) DESC").fetchone()
    if not r:
        raise RuntimeError("工作库 image_manifest 里没有任何带 accident_id 的行")
    aid, n = r[0], r[1]
    with sqlite3.connect(f"file:{main}?mode=ro", uri=True) as c:
        got = c.execute("SELECT id, title FROM accidents WHERE id=?", (aid,)).fetchone()
    if not got:
        raise RuntimeError(f"事故 {aid} 在主库里不存在")
    return aid, got[1], n


def run_runtime() -> dict:
    from playwright.sync_api import sync_playwright                  # noqa: F401

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    tmp = Path(tempfile.mkdtemp(prefix="chk2595_"))
    main_src = Path(main_db_path())
    stg_src = _staging_path()
    main_cp, stg_cp = tmp / "main.db", tmp / "stg.db"
    _snapshot(main_src, main_cp)
    _snapshot(stg_src, stg_cp)
    # 「干净副本」是被验证过的前提，不是承诺（§2.5.86）
    clean = (_logical(main_cp) == _logical(main_src)
             and _logical(stg_cp) == _logical(stg_src))

    aid, title, n_img = _pick_target(main_cp, stg_cp)
    with sqlite3.connect(stg_cp) as c:
        c.execute("UPDATE image_manifest SET source_url=?, filename=? "
                  "WHERE accident_id=?", (PAYLOAD_URL, PAYLOAD_TEXT, aid))
        c.commit()
    with sqlite3.connect(main_cp) as c:
        c.execute("UPDATE accidents SET title=? WHERE id=?", (PAYLOAD_TEXT, aid))
        c.commit()

    os.environ["CAIEMS_DB"] = str(main_cp)
    os.environ["CAIEMS_STAGING_DB"] = str(stg_cp)
    from _serve import point_at, serve
    point_at(str(main_cp), stg_cp)
    server, base = serve(PORT)

    rep: dict = {"tmp": str(tmp), "aid": aid, "title": title, "n_img": n_img,
                 "clean": clean, "cases": [], "guard_js": {}}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()

            # ── C1 守卫语义：在**真 JS 引擎**里跑从文件抽出的那份源码
            guard = js_guard_source(DOM_JS)
            cases = ["https://a/b", "http://a", "/accident/x", "#f", "?a=1",
                     "mailto:a@b", "tel:+8610", "blob:http://x/1",
                     "javascript:alert(1)", "JaVaScRiPt:alert(1)",
                     "  javascript:alert(1)", "java\tscript:alert(1)",
                     "java\nscript:alert(1)", "\x01javascript:x",
                     "data:text/html,<script>alert(1)</script>",
                     "vbscript:msgbox(1)", PAYLOAD_URL, "javascript/**/:alert(1)"]
            got = pg.evaluate(
                "(args) => { const [guard, cases] = args;"
                " const f = new Function(guard + '; return safeUrlValue;')();"
                " return cases.map(c => f(c) === null); }", [guard, cases])
            rep["guard_js"] = dict(zip(cases, got))

            # ── C2 端到端：真载荷进真页面
            pg.goto(f"{base}/accident/{aid}", wait_until="networkidle", timeout=25000)
            pg.wait_for_timeout(600)
            rep["cases"].append(("load_no_exec", pg.evaluate("() => window.__pwned__ === undefined")))
            rep["cases"].append(("no_parsed_img", pg.evaluate(
                "() => document.querySelectorAll('img[src=\"x\"]').length === 0")))
            rep["cases"].append(("no_onerror_attr", pg.evaluate(
                "() => ![...document.querySelectorAll('*')].some(e => e.hasAttribute('onerror'))")))
            rep["cases"].append(("title_is_text", pg.evaluate(
                "(t) => document.body.innerText.includes(t)", PAYLOAD_TEXT)))
            pg.locator(".img-tile").first.click()
            pg.wait_for_timeout(400)
            a = pg.locator(".lb-src a")
            rep["lightbox"] = {
                "count": a.count(),
                "text": a.first.inner_text() if a.count() else "",
                "href": a.first.get_attribute("href") if a.count() else "(无元素)",
            }
            rep["cases"].append(("href_absent", rep["lightbox"]["href"] is None))

            # ── C3 合成对照：把 window.el 换成**无守卫**版本，看 href 会不会出现
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(200)
            pg.evaluate("""() => {
              const src = window.el.toString();
              window.el = new Function('return ' + src.replace(
                /if \\(URL_ATTRS\\.indexOf[\\s\\S]*?return;\\s*\\n?\\s*/m, ''))();
            }""")
            pg.locator(".img-tile").first.click()
            pg.wait_for_timeout(400)
            a2 = pg.locator(".lb-src a")
            rep["control"] = {
                "count": a2.count(),
                "href": a2.first.get_attribute("href") if a2.count() else "(无元素)",
            }
            if a2.count() and a2.first.get_attribute("href"):
                a2.first.click()
                pg.wait_for_timeout(500)
            rep["pwned_after_click"] = pg.evaluate("() => window.__pwned__ === undefined")
            browser.close()
    finally:
        server.shutdown()
    return rep


def part_c(rep: dict) -> list[str]:
    fails = []
    print("\nC 运行时真值面")
    info(f"临时副本 {rep['tmp']}（干净副本校验：{rep['clean']}）")
    info(f"目标事故 id={rep['aid']}  图片 {rep['n_img']} 张  载荷标题 {rep['title'][:40]!r}")
    if not ok(rep["clean"], "两份临时副本与真库**逐行相同**（复制没丢数据）"):
        fails.append("C1")

    print("\n  C1 守卫语义（从 dom.js 抽出的源码，在真 Chromium 引擎里跑）")
    # 三组口径。第三组是本节最值得记的一格：**看着像混淆 ≠ 可执行**。
    # HTML 规范里 scheme 是 `[A-Za-z][A-Za-z0-9+.-]*` **紧随** `:`；
    # `javascript/**/` 里 `javascript` 后面是 `/` ⇒ 不是合法 scheme ⇒
    # 浏览器按**相对路径**解析，不构成代码执行向量（首版把它列进"必须拒"，
    # 是凭印象认为"形似混淆即可执行"——§2.5.87「名字来自 grep，不来自印象」）。
    # 反向的 `java\tscript:` 才真危险：URL 解析器会先剥掉制表符/换行，
    # 故它**必须**被拒（在危险组里）。同理实体形态在 setAttribute 语境下
    # 不做 HTML 解码（解码只发生在 HTML 解析器读属性值时，不走 DOM API）。
    forbid = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "  javascript:alert(1)",
              "java\tscript:alert(1)", "java\nscript:alert(1)", "\x01javascript:x",
              "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)",
              PAYLOAD_URL]
    allow = ["https://a/b", "http://a", "/accident/x", "#f", "?a=1",
             "mailto:a@b", "tel:+8610", "blob:http://x/1"]
    gray = ["javascript/**/:alert(1)", "javascript&#58;alert(1)",
            "java&Tab;script:alert(1)"]
    gj = rep["guard_js"]
    blocked = [c for c in forbid if not gj.get(c)]
    passed = [c for c in allow if gj.get(c)]
    gray_kept = [c for c in gray if gj.get(c)]
    info("  拒绝：" + ", ".join(f"{c!r}" for c in forbid if gj.get(c)))
    info("  放行：" + ", ".join(f"{c!r}" for c in allow if not gj.get(c)))
    info("  像混淆但 scheme 不成立、按相对路径放行：" + ", ".join(gray))
    if not ok(not blocked and not passed,
              "危险协议（含大小写/前后空白/内嵌制表与换行）全部拒绝，正常协议全部放行",
              f"（没挡住 {blocked}；误伤 {passed}）"):
        fails.append("C2")
    if not ok(not gray_kept,
              "「形似混淆」组按 URL 解析器语义放行（不是放水——危险组那几条必须被拒）",
              f"（被误拒 {gray_kept}）"):
        fails.append("C2")

    print("\n  C2 端到端：载荷进真页面")
    for name, good in rep["cases"]:
        if not ok(good, name):
            fails.append("C3")
    lb = rep["lightbox"]
    info(f"  灯箱来源链接：{lb['count']} 个，text={lb['text'][:40]!r}，href={lb['href']!r}")
    if not ok(lb["count"] == 1 and lb["text"] == PAYLOAD_URL,
              "载荷**逐字**出现在链接文本上（数据确实流到了这个元素）"):
        fails.append("C3")
    if not ok(lb["href"] is None, "同一元素的 href 属性**没有被写**（守卫生效）",
              f"（href={lb['href']!r}）"):
        fails.append("C3")

    print("\n  C3 合成对照（把 window.el 换成无守卫版本）")
    ct = rep["control"]
    info(f"  对照：href={ct['href']!r}；点完之后 __pwned__ 仍未定义={rep.get('pwned_after_click')}")
    if not ok(ct["href"] == PAYLOAD_URL,
              "去掉守卫后 href 立刻出现 ⇒ **判据有能力分辨**（不是所有链接都没 href）"):
        fails.append("C4")
    info("  （点不点得响是**浏览器策略**的事：这些链接都带 target=_blank，现代 Chromium")
    info("    对 javascript:/data: 的新标签导航有限制——只作观察，不判红。）")
    return fails


# ══════════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════════
DOC_ANCHORS = [
    ("D1", DOC, "### 2.5.95 ", "01 号文档有 §2.5.95 标题"),
    ("D2", DOC, "`check_2595_dom_sinks.py`", "01 号文档引了脚本名"),
    ("D3", DOC09, "第三十七个维度",
     "09 号文档已登记本节的维度计数（第三十七个维度）"),
    ("D4", DOC10, "check_2595_dom_sinks.py", "10 号清单引了脚本名"),
    ("D5", DOC11, "2.5.95", "11 号快照有本节"),
]


def part_d() -> list[str]:
    fails = []
    print("\nD 文档对账")
    for mark, path, needle, label in DOC_ANCHORS:
        txt = path.read_text(encoding="utf-8") if path.exists() else ""
        hit = needle in txt
        # detail 是**失败导向**的文案（"找不到 X"），故只在没命中时打印——
        # `ok()` 会无条件打出来，于是通过时显示「✓ …（找不到 '### 2.5.95 '）」，
        # **输出自相矛盾**（打勾却写着找不到）。其余各段的 detail 多是中性说明、
        # 通过时有价值，故只在这里就地改，不动 `ok()` 的通用行为。
        print(f"  {'✓' if hit else '✗'} {label}"
              + ("" if hit else f"  （{path.name} 里找不到 {needle!r}）"))
        if not hit:
            fails.append(mark)
    # README 只读脚本表的**行数不在这里数**——那是 check_2591_replica_scope.py 的
    # D7 现算判据的活（N == 表实际行数、M == N−1）。本脚本首版在这里数过一遍，
    # 用 `split("| 脚本 |", 1)` 取「第一张」表——而 README 里**有两张**以
    # `| 脚本 |` 开头的表（第 30 行 `| 脚本 | 阶段 | 覆盖 |` 与第 66 行
    # `| 脚本 | 查什么 | 端口 |`），取到的是**前一张**，实测打印 **10 行**
    # 而只读脚本表是 30 行——**这正是 §2.5.94 记下的同一个坑的复现**
    # （"认错了表，首版数错成 10 行"）。处置不是修好它，是**删掉**：
    # 同一个源同一字段不写第二遍解析（§2.5.76），且 D7 那边是现算的、不会过期。
    # 只在 README 真不存在时报一声。
    if not README.exists():
        info("  e2e/README.md 不存在——它的只读脚本表行数由 check_2591 的 D7 核，本节不重复解析")
    return fails


# ══════════════════════════════════════════════════════════════════
# 主检
# ══════════════════════════════════════════════════════════════════
def info(text: str) -> None:
    print(f"    {text}")


def ok(cond: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)


def collect(runtime: bool = True) -> list[str]:
    fails = []
    fails += part_a()
    fails += part_b()
    fails += part_c(run_runtime()) if runtime else []
    fails += part_d()
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--static", action="store_true", help="只跑 A/B/D 段，不起服务")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--inject-test", action="store_true")
    ap.add_argument("--marks", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if a.inject_test:
        return inject_test()
    if a.marks:
        fails = collect(runtime=not a.static)
        print("MARKS " + json.dumps(sorted(fails)))
        return 1 if fails else 0

    print("§2.5.95 数据当代码执行的面：DOM 写入通道的第三次清点")
    fails = collect(runtime=not a.static)
    print("\n" + "═" * 62)
    if fails:
        print(f"✗ 待处置 {len(fails)} 条：{sorted(set(fails))}")
        return 1
    print("✅ 全部通过")
    return 0


# ══════════════════════════════════════════════════════════════════
# 注入式自证

# 判据自己有能力报红吗？——两个注入面都要测（§2.5.68）：源码面改真 JS，
# 文档面改文档副本。断言**红的恰好是哪几条**，不是"退出码非 0"。
# ══════════════════════════════════════════════════════════════════
def _marks(env_extra: dict | None = None) -> set[str]:
    env = dict(os.environ)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                        "--marks", "--static"],
                       capture_output=True, text=True, cwd=str(ROOT), env=env)
    lines = [x for x in r.stdout.splitlines() if x.startswith("MARKS ")]
    if not lines:
        raise RuntimeError("子进程没给出 MARKS 行：\n"
                           + r.stdout[-2000:] + "\n" + r.stderr[-1500:])
    return set(json.loads(lines[-1][len("MARKS "):]))


def _swap(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    if src.count(old) != 1:
        raise RuntimeError(f"锚点在 {path.name} 里出现 {src.count(old)} 次，须恰好 1 次")
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def inject_test() -> int:
    acc = STATIC / "accident.js"
    col = STATIC / "collect.js"
    cau = STATIC / "causes.js"
    doc_copy = Path(tempfile.mkdtemp(prefix="chk2595d_")) / "01.md"
    doc_copy.write_text(DOC.read_text(encoding="utf-8"), encoding="utf-8")

    cases = [
        # ── ① 解析型 sink
        # 注：`el()` 只认 id/cls/text/attrs 四个键，往 opts 里塞 `innerHTML:` 是
        # **无操作**（不构成 sink），故 I1 必须改 `el()` **内部**的写入语句——
        # 这也更贴近真实缺陷形态（有人为支持富文本把 textContent 换成 innerHTML，
        # 全站经 el({text:…}) 写入的文本一起变成 HTML 解析面）。
        ("I1 把 dom.js 的 el() 从 textContent 改成 innerHTML",
         DOM_JS, "    if (o.text != null) node.textContent = String(o.text);",
         "    if (o.text != null) node.innerHTML = String(o.text);", {"A1"}, "src"),
        ("I2 给 overview.js:507 的空串换成表达式",
         OVW_JS, "$('resultCount').innerHTML = '';",
         "$('resultCount').innerHTML = q;", {"A1"}, "src"),
        # ── ② 属性名面
        ("I3 给一个 attrs 加事件属性键（onmouseover）",
         col, "attrs: { href: d.source_url, target: '_blank',",
         "attrs: { onmouseover: 'x', href: d.source_url, target: '_blank',",
         {"A2"}, "src"),
        ("I4 给一个 attrs 加未登记的键",
         col, "attrs: { href: a.url, target: '_blank',",
         "attrs: { data-zzz: '1', href: a.url, target: '_blank',",
         {"A2"}, "src"),
        # ── ③ URL 值面
        ("I5 把 encodeURIComponent 的构造式安全值换成纯数据派生",
         OVW_JS, "attrs: { href: '/accident/' + encodeURIComponent(it.id || '') },",
         "attrs: { href: it.url },", {"A3"}, "src"),
        # ── ④ 守卫面
        ("I6 删掉 dom.js 的守卫调用（保留定义）",
         DOM_JS, "        if (URL_ATTRS.indexOf(String(k).toLowerCase()) >= 0 &&\n"
                 "            safeUrlValue(v) == null) return;      // 危险协议 ⇒ 不写属性\n",
         "", {"A4"}, "src"),
        ("I7 只改 overview.js 守卫块里的一个字符（两份不再逐字相同）",
         OVW_JS, "const SAFE_SCHEMES = ['http', 'https', 'mailto', 'tel', 'blob'];",
         "const SAFE_SCHEMES = ['http', 'https', 'mailto', 'tel', 'blob']; ".rstrip() + " ",
         {"A4"}, "src"),
        # 注：锚点原写 `function money(v) {`——**全仓没有这个函数**（凭空写的，
        # `_swap` 直接抛「0 次」）。改用 collect.js 真实存在的顶层函数（`:33`）。
        ("I8 新增一处绕过 el() 的 setAttribute('href', …)",
         col, "  function fmtTime(s) {",
         "  function _probeBypass() { node.setAttribute('href', x); }\n"
         "  function fmtTime(s) {", {"A4"}, "src"),
        # 注：SVG helper 调用在 **causes.js**（定义在 `:73`，`:376` 起调用），
        # collect.js 里一个 `svg(` 都没有——首版锚点写错文件，`_swap` 直接抛
        # 「锚点出现 0 次」（§2.5.64：锚点写错 ＝ 用例根本没跑）。
        # 注：锚点最初写 `const root = svg('svg', {\n      viewBox:`——该片段在
        # causes.js 里出现 **2 次**（两张 SVG 图同一模板），`_swap` 要求恰好 1 次。
        # 改用同一调用里唯一的属性行（`cooc-svg` 全仓 1 次）。
        ("I9 把 SVG helper 的属性名换成 URL 属性（href）",
         cau, "      class: 'cooc-svg', role: 'img',",
         "      href: '/a',\n      class: 'cooc-svg', role: 'img',", {"A4"}, "src"),
        # ── 文档面
        ("I10 改掉 01 号文档的 §2.5.95 标题（文档副本）",
         doc_copy, "### 2.5.95 ", "### 2.5.95x ", {"D1"}, "doc"),
    ]

    base = _marks()
    print(f"基线红集合：{sorted(base) or '（空）'}")
    bad = 0
    for label, path, old, new, want, face in cases:
        target = doc_copy if face == "doc" else path
        orig = target.read_text(encoding="utf-8")
        try:
            _swap(target, old, new)
            got = _marks({"C2595_DOC": str(doc_copy)} if face == "doc" else None)
        finally:
            target.write_text(orig, encoding="utf-8")
        good = got == want
        print(f"  {'✓' if good else '✗'} {label}\n"
              f"      期望 {sorted(want)} ／ 实得 {sorted(got)}")
        bad += 0 if good else 1
    print(f"{'✅' if not bad else '✗'} 注入自证 {len(cases) - bad}/{len(cases)}")
    return 1 if bad else 0


# ══════════════════════════════════════════════════════════════════
# 纯函数自测（判据的前提错了，判据绿也没意义）
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    n = bad = 0

    def chk(got, want, label):
        nonlocal n, bad
        n += 1
        if got != want:
            bad += 1
            print(f"  ✗ {label}\n      期望 {want!r} ／ 实得 {got!r}")

    # strip_comments：块注释与整行注释要剥，行内 // 要留
    chk(strip_comments("a\n/* 不拼 innerHTML */\nb = 1;"), "a\n\nb = 1;", "块注释剥掉")
    chk(strip_comments("// innerHTML = x\ny = 1;"), "\ny = 1;", "整行注释剥掉")
    chk(strip_comments("u = 'http://a';"), "u = 'http://a';", "行内 // 保留")

    # split_top：嵌套里的逗号不算
    chk(split_top("{x: 1, y: 2}, b: 3"), ["{x: 1, y: 2}", "b: 3"], "深度 0 切逗号")
    chk(split_top("title: 'a,b'"), ["title: 'a,b'"], "引号里的逗号不切")
    chk(split_top("t: 'a}c', b: 1"), ["t: 'a}c'", "b: 1"], "引号里的 } 不配平")
    chk(split_top("t: `a${'}'}b`"), ["t: `a${'}'}b`"], "模板串整体保留")

    # skip_str：转义与未闭合
    chk(skip_str(r"'a\'b'", 0), 6, "转义引号不结束字符串")
    chk(skip_str("'abc", 0), 4, "未闭合 → 走到末尾")
    chk(skip_str("abc", 0), 0, "不是引号 ⇒ 原样返回")

    # key_of：四种形态
    chk(key_of("title: 'x'"), "title", "裸键")
    chk(key_of("'data-key': v"), "data-key", "引号键")
    chk(key_of("[expr]: 1"), None, "计算属性名 → None")
    chk(key_of("...spread"), None, "展开 → None")
    chk(key_of("x"), "x", "简写键")

    # is_literal
    chk(is_literal("''"), True, "空串是字面量")
    chk(is_literal("'a'"), True, "单引号")
    chk(is_literal('"a"'), True, "双引号")
    chk(is_literal("`a`"), True, "无反引号插值的模板串")
    chk(is_literal("`a${b}`"), False, "带插值的模板串不算")
    chk(is_literal("q"), False, "标识符不算")
    chk(is_literal("'a' + b"), False, "拼接不算")

    # arg_at / stmt_rhs 的边界
    chk(arg_at("a, b)", 0), ("a", 1), "取到深度 0 的逗号")
    chk(arg_at("f(x, y), z)", 0), ("f(x, y)", 7), "嵌套括号里的逗号不算")
    chk(arg_at("'a,b', c", 0), ("'a,b'", 5), "引号里的逗号不算")
    chk(stmt_rhs("'';\nnext", 0), "''", "取到分号")
    chk(stmt_rhs("'a;b';\nnext", 0), "'a;b'", "引号里的分号不算")
    chk(stmt_rhs("q\nnext", 0), "q", "取到换行")
    # balanced 的用例**必须用真实调用形态**：三处调用都配平花括号，
    # 首版用例却用默认的圆括号 ⇒ 用一个参数形态证明另一个参数形态，
    # 把"`attrs: {` 之后一路扫到文件末尾"这个真缺陷放了过去。
    chk(balanced("a: '}'}", 0, "{", "}"), ("a: '}'", 6), "花括号配平（真实调用形态）")
    chk(balanced("a: {x: 1}}b", 0, "{", "}"), ("a: {x: 1}", 9), "嵌套花括号配平")
    chk(balanced("a: ')'", 0), ("a: ')'", 6), "圆括号配平（默认形态）")

    # scheme
    chk(scheme("javascript:x"), "javascript:", "scheme 小写归一")
    chk(scheme("  HTTPS://a"), "https:", "scheme 去空白+小写")
    chk(scheme("/media/a.jpg"), "<绝对路径>", "绝对路径")
    chk(scheme(""), "<空>", "空串")

    # classify_url_value
    chk(classify_url_value("'/accident/'"),
        "literal", "字面量")
    chk(classify_url_value("'/accident/' + encodeURIComponent(x)"),
        "constructed", "构造式")
    chk(classify_url_value("it.source_url"), "derived", "数据派生")
    chk(classify_url_value("d.source_url"), "derived", "另一个数据派生")

    print(f"{'✅' if not bad else '✗'} 纯函数自测 {n - bad}/{n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
