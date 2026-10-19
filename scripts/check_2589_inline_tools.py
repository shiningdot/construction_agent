#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.89 前端工具的第二份副本：`el()` 的键集**双向分叉**，与「必须一起改」的约定。

## 来路

`dom.js` 自己写着有第二份实现，就在 `overview.js` 里（两处注释，原文）：

> `overview.js` 至今还带着一份等价的内联副本（早于本文件存在）。两份实现目前
> **不会同时加载**，故不冲突。合并登记在蓝图 07 的 U1 表（「未完成事项登记」）
> ——原先那句把这笔债挂在 M1 里程碑上，而 M1 早在 2026-09-30 就收尾了，声明
> 因此逾期（§2.5.96 查明）。                                   ← `dom.js:7-11`

> **两处的库状态呈现必须一起改**（第四十一轮就是两处一起改的）。   ← `dom.js:85-86`

"必须一起改"是**约定不是机制**（§2.5.73 立）——所以本节把它变成一条会响的判据。
`overview.js:127-132` 有对应的呼应注释，两边都承认这件事，**但从来没有判据**。

## 本节的核心发现：不是「A 旧 B 新」，是「各补了一半」

两份 `el()` 的**键集双向分叉**：

| 键 | `dom.js` | `overview.js` |
|---|---|---|
| `id` | ✅ | ❌ |
| `cls` / `text` | ✅ | ✅ |
| `attrs` | ✅（值转 `String`、跳过 null） | ✅（值直接透传） |
| `on` | ❌ | ✅ |

为什么是**双向**而不是"一份落后"：`id` 支持是**审核页丢过一次 id 之后**补的——
`dom.js:22-27` 的注释记着那次事故（"审核页的致因树与操作回执就这么丢过一次，
而且是『页面看着正常、只是没内容』的那种丢法"），**出事的正是今天 `review.js:624`
的 `causeList` 与 482/484/486/492 的操作回执**。那份修复进了 `dom.js`，**没有进
`overview.js`**；而 `on` 反过来是 `overview.js` 自带的、`dom.js` 没有。

⇒ **合并不是「取新的那份」，是「取并集」**。这条结论改变了处置方式，故写进标题。

分叉今天**各有一处真依赖**（所以两份都不能删）：`review.js` 用顶层 `{id: …}`（2 处）、
`overview.js` 用 `{on: …}`（1 处）。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 两份 `el` 的**键集对称差** == 登记表，**双向**核 | 分叉漂移、登记表过期 | 红 |
| A2 | 两份 `el` 的**取值处理特征**（`String(`）== 登记表，**双向** | 同上，落在非键集维度 | 红 |
| B1 | 每个 js 里 `el({…})` 的**顶层键** ⊆ **该文件那份 `el`** 认的键 | 静默丢掉（`dom.js:22-27` 记的那次事故） | 红 |
| B2 | `el` 调用里第二实参**解不出**的调用点 | "我没比"伪装成"比过了" | 观察 |
| C1 | `overview.html` **不引** `dom.js`、其余五页**都引** | 两份同时加载（`loadDbChip` 跑两遍、chip 被写两次） | 红 |
| C2 | 每个 js 用到的公共工具 ⊆ **它所在页**能提供的那份 | 某页忘了引 dom.js 却用了 `num()` | 红 |
| D  | 文档里写的数 == 本节实测的数 | 文档过期 | 红 |

**A1 为什么"双向"**：只核"登记的两个分叉还在不在"会漏掉**新长出来的第三个分叉**
（登记表成了永久豁免，§2.5.67 立）；只核"有没有分叉"会把**已登记的**也报出来。
两个方向都核，登记表才是活的。

**B1 为什么能抓真事故**：`el()` 对不认识的键**静默丢弃**（`dom.js:22-27` 原话），
所以 `accident.js` 里写一个 `{on: …}` 不报错、不告警、事件就是不绑——**页面看着正常**。
B1 把"这份 `el` 认得哪些键"与"这一处用了哪些键"对上，正是那条注释警告的事。

## 判据核不到什么（边界）

- **B1 只核顶层键**：`attrs: { id: 'x' }` 里的 `id` 是 `attrs` 的值、不是 `el` 认的键，
  故不收（首版不切分顶层，报出了 5 个假键——§2.5.63/66 那个"判据过宽"病的又一次发作）。
  代价是**核不到 `attrs` 内部**：`attrs` 是透传给 `setAttribute` 的，键名非法只会在
  运行时由 DOM 自己忽略，**不在本节射程**。
- **B2 不判红**：第二实参是变量（`el(tag, opts)`）时静态解不出键集，那是**正常的**
  （键在运行时才定）。本节把它**报出来**而不是当通过——但也不判红，因为它不是缺陷。
- **A2 只有一条特征**：`String(`。`attrs` 的 null 跳过（`dom.js` 有、`overview` 没有）
  **没有进判据**——它靠一个内部循环变量名才能精确定位，写死变量名会随改名失效
  （§2.5.66 的教训），故只在文档里登记。
- **C2 只认「裸调用」**：`num(` 前若有点号（`x.num(`）不算，形参遮蔽（`function f(num)`）
  **会被误判为使用**——收窄换来准确、代价是覆盖变窄，两边的宽度都写在这里。
- **不看渲染**：本节全是静态解析，核不到"绑上的事件真的响"（那是 `check_web_interaction.py`
  与 §2.5.69/§2.5.70 的路）。
- **漂移自证自己也要自证**：`--drift-test` 的注入若没生效，输出只会显示"实测红 ∅"——
  看起来像"判据没报"，实际是"注入没做"，两者在输出上**一模一样**（§2.5.65 立：
  「锚点未命中」与「判据报警」在输出上都是红）。首版把替换次数默认值写成 `0`，
  而 `str.replace(old, new, 0)` 是「替换 **0** 次」**不是**「全部替换」⇒ 没写 `count`
  的四例注入**根本没发生**、四例齐报"实测红 ∅"；只有显式写了 `count: 1` 的 C1 通过，
  **两种结果并排出现也没人看得出来**。现每例注入后都断言 `injected != orig`，
  不生效即报"用例静默失效"——**这条断言比用例本身更要紧，它防的是用例整批空转**。
- 不起服务、不开浏览器，纯文本解析，约 1 秒。

用法：
    .venv/bin/python3 scripts/check_2589_inline_tools.py
    .venv/bin/python3 scripts/check_2589_inline_tools.py --self-test
    .venv/bin/python3 scripts/check_2589_inline_tools.py --drift-test

退出码 `0` = 四段全绿。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBD = ROOT / "construction_agent" / "web"
STATIC = WEBD / "static"
TPLDIR = WEBD / "templates"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"

JS_FILES = sorted(p.name for p in STATIC.glob("*.js"))

#: 公共工具名（`$` 单独处理：它在正则里要转义，且形态是 `$('id')`）。
TOOLS = ("el", "num", "blank", "fetchJson", "loadDbChip", "clear", "$")

#: **登记的分叉**——A 段双向核的就是它。改动这份表要同时改 `docs/web/01_在线采集.md`。
KNOWN_KEY_DIVERGENCE = {"dom.js": {"id"}, "overview.js": {"on"}}
#: 取值处理特征：`el` 函数体里有没有 `String(`。
#: **两份现在都是 True**——原先 `overview.js` 是直接透传（False），§2.5.95 给它的
#: `el` 加 URL 协议守卫时用了 `String(k).toLowerCase()`（属性名小写化），这条
#: 特征分叉随之消失。**这是"改共享源要复跑别节"的实例**：A 段判据在 §2.5.95
#: 落地后如实报出 `overview.js` 由 False 变 True，登记表跟着更新。
KNOWN_STRING_FEATURE = {"dom.js": True, "overview.js": True}


# ══════════════════════════════════════════════════════════════════
# 解析工具（纯文本，无依赖）
# ══════════════════════════════════════════════════════════════════
def strip_js_comments(src: str) -> str:
    """剥掉 `//` 与 `/* */`，**保留换行**（行号仍准）。

    必须先剥：`dom.js:25` 的 `$('x')` 就藏在注释里（讲 `el` 会静默丢键那段），
    不剥会把注释当代码读（§2.5.73 那次的同款教训）。
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def split_top(s: str, sep: str = ",") -> list[str]:
    """按**顶层**分隔符切分：括号/花括号/方括号/引号内的不切。

    B 段的键集全靠它——`{ cls: 'x', attrs: { id: 'y' } }` 切出来是
    `["cls: 'x'", " attrs: { id: 'y' } "]`，`id` 不会冒充顶层键。
    """
    out: list[str] = []
    depth, cur, q = 0, [], None
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "'\"`":
            q = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def _match_bracket(src: str, i: int, open_ch: str = "(", close_ch: str = ")") -> int:
    """从 `src[i]`（应为 `open_ch`）出发配对，返回 `close_ch` 的下标。"""
    depth, q, j = 0, None, i
    while j < len(src):
        ch = src[j]
        if q:
            if ch == q:
                q = None
        elif ch in "'\"`":
            q = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return len(src) - 1


def calls(src: str, fname: str) -> list[tuple[int, list[str]]]:
    """找 `fname(` 的调用（前面不是 `.` 或词字符），返回 `[(起点, 顶层实参列表)]`。"""
    res = []
    pat = r"(?<![\w$.])" + re.escape(fname) + r"\s*\("
    for m in re.finditer(pat, src):
        i = m.end() - 1
        j = _match_bracket(src, i)
        res.append((m.start(), split_top(src[i + 1:j])))
    return res


def fn_def(src: str, name: str) -> tuple[list[str], str] | None:
    """取 `function NAME(...) { ... }` 的 `(形参名列表, 函数体)`。

    两份 `el` 都是 `function` 声明，故够用（`$` 在 `overview.js` 里是箭头函数，
    本节不解析它）。
    """
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(([^)]*)\)\s*\{", src)
    if not m:
        return None
    params = [p.strip() for p in m.group(1).split(",") if p.strip()]
    i = m.end() - 1
    return params, src[i + 1:_match_bracket(src, i, "{", "}")]


def opts_local(body: str, param: str) -> str:
    """`el` 体里那个"装着选项"的局部变量名。

    两份写法不同（`dom.js`: `const o = opts || {}`；`overview.js`: `opts = opts || {}`），
    所以**不能从签名取**，要跟着赋值走——否则一份的键集恒为空、判据空转。
    """
    m = re.search(r"(?:const|let|var)?\s*([A-Za-z_$][\w$]*)\s*=\s*"
                  + re.escape(param) + r"\s*\|\|\s*\{\}", body)
    return m.group(1) if m else param


def el_keys_of(path: Path) -> set[str]:
    """该文件里 `el` 认的**顶层键**集合（从函数体里的 `o.键` 形态提）。"""
    src = strip_js_comments(path.read_text(encoding="utf-8"))
    d = fn_def(src, "el")
    if not d:
        return set()
    params, body = d
    if len(params) < 2:
        return set()
    local = opts_local(body, params[1])
    return set(re.findall(r"\b" + re.escape(local) + r"\.([A-Za-z_$][\w$]*)", body))


def el_calls(path: Path) -> tuple[set[str], int, int, list[tuple[int, list[str]]]]:
    """返回 `(顶层键集, 解不出的调用点数, 全部调用点数, [(行号, 键列表)])`。

    三个数**不能混**：`全部` = 所有 `el(` 调用（含只传 tag 的）；`解不出` = 传了
    第二实参但它不是对象字面量（`el('div', opts)`）；`可解析` = 两者之差。
    首版只报前两个、把"可解析"当分母，606 与 584 的差在输出里无从解释。
    """
    src = strip_js_comments(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    unresolved = 0
    total = 0
    detail: list[tuple[int, list[str]]] = []
    for pos, args in calls(src, "el"):
        total += 1
        if len(args) < 2:
            continue
        second = args[1].strip()
        k: list[str] = []
        if second.startswith("{") and second.endswith("}"):
            for seg in split_top(second[1:-1]):
                mm = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:", seg)
                if mm:
                    k.append(mm.group(1))
        else:
            unresolved += 1
        if k:
            keys.update(k)
            detail.append((src[:pos].count("\n") + 1, k))
    return keys, unresolved, total, detail


def scripts_of(tpl: Path) -> list[str]:
    """模板里加载的 js 文件名（按出现顺序，去重）。"""
    s = tpl.read_text(encoding="utf-8")
    out: list[str] = []
    for m in re.finditer(r"""filename=['"]([^'"]+\.js)['"]""", s):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def pages_of_all() -> dict[str, list[str]]:
    """js 文件名 → 引它的模板名列表。"""
    rev: dict[str, list[str]] = {}
    for tpl in sorted(TPLDIR.glob("*.html")):
        for js in scripts_of(tpl):
            rev.setdefault(js, []).append(tpl.name)
    return rev


def provides_of(path: Path) -> set[str]:
    """该 js 往**全局**提供哪些公共工具。

    `dom.js` 走 `global.X = X`；`overview.js` 是自包含 IIFE，它的工具只在自己
    作用域内可见——但对"同页可用性"来说效果一样（**同页只有它一个脚本**，
    C1 那条判据钉住了这件事），故一并算作它所在页的提供者。
    """
    src = strip_js_comments(path.read_text(encoding="utf-8"))
    provided = set(re.findall(r"\b(?:global|window)\.([A-Za-z_$][\w$]*)\s*=", src))
    # 自带定义（IIFE 内的顶层 function / const）
    for t in TOOLS:
        if fn_def(src, t) or re.search(r"\b(?:const|let|var)\s+" + re.escape(t) + r"\s*=", src):
            provided.add(t)
    return provided & set(TOOLS)


def uses_of(path: Path) -> dict[str, int]:
    """该 js 裸调用了哪些公共工具（不含定义处）。"""
    src = strip_js_comments(path.read_text(encoding="utf-8"))
    out = {}
    for t in TOOLS:
        n = len(calls(src, t))
        if n:
            out[t] = n
    return out


def provider_of(js: str, rev: dict[str, list[str]]) -> str | None:
    """这个 js 里的 `el(` 用的是**哪一份实现**。

    自带 `el` 的（`overview.js`、`dom.js`）用自己那份；其余用**同页加载的**那份。
    """
    if fn_def(strip_js_comments((STATIC / js).read_text(encoding="utf-8")), "el"):
        return js
    for tpl in rev.get(js, []):
        for other in scripts_of(TPLDIR / tpl):
            if other == js:
                continue
            if fn_def(strip_js_comments((STATIC / other).read_text(encoding="utf-8")), "el"):
                return other
    return None


# ══════════════════════════════════════════════════════════════════
# A 段：两份副本的分叉清单（双向核）
# ══════════════════════════════════════════════════════════════════
def part_a() -> list[str]:
    bad: list[str] = []
    print("A 段 两份 `el()` 的分叉清单（**双向**核：分叉须在表里、表里的须真分叉）")
    keys = {js: el_keys_of(STATIC / js) for js in KNOWN_KEY_DIVERGENCE}
    solo = {js: el_keys_of(STATIC / js) - (set().union(*[
        k for j, k in keys.items() if j != js]) if len(keys) > 1 else set())
        for js in keys}
    print(f"  · dom.js 独有键 {sorted(solo['dom.js'])}；"
          f"overview.js 独有键 {sorted(solo['overview.js'])}")
    print(f"  · 共有键 {sorted(keys['dom.js'] & keys['overview.js'])}")
    for js, want in KNOWN_KEY_DIVERGENCE.items():
        got = solo[js]
        if got != want:
            bad.append(f"A1 {js} 的独有键集={sorted(got)}，登记的是 {sorted(want)}")
        print(f"  {'✓' if got == want else '✗'} {js} 独有键 == 登记")
    # 双向：登记表里的键必须**真**是该文件独有的（防登记过期成本文件已有）
    for js, want in KNOWN_KEY_DIVERGENCE.items():
        extra = want - keys[js]
        if extra:
            bad.append(f"A1 登记表过期：{js} 登记了 {sorted(extra)}，但它已经不认这些键了")
    shared = keys["dom.js"] & keys["overview.js"]
    if not shared:
        bad.append("A1 两份 `el` 竟没有一个共有键——解析失败与真分叉在输出上都是红")

    feats = {}
    for js in KNOWN_STRING_FEATURE:
        src = strip_js_comments((STATIC / js).read_text(encoding="utf-8"))
        d = fn_def(src, "el")
        feats[js] = bool(d and "String(" in d[1])
    for js, want in KNOWN_STRING_FEATURE.items():
        if feats[js] != want:
            bad.append(f"A2 {js} 的 el 体 `String(` 特征={feats[js]}，登记的是 {want}")
        print(f"  {'✓' if feats[js] == want else '✗'} {js} 取值处理特征（`String(`）"
              f"== 登记（{want}）")
    print(f"  · 观察（不判红，见文档「形态差异」表）：`attrs` 的 null 跳过、"
          f"`clear`/`loadHealth` 的返回值、`footMeta` 写入守卫——"
          f"这三处靠内部变量名才能精确定位，写死会随改名失效，故不进判据")
    return bad


# ══════════════════════════════════════════════════════════════════
# B 段：传染面——各 js 的 `el({…})` 顶层键 ⊆ 它用的那份 `el` 认的键
# ══════════════════════════════════════════════════════════════════
def part_b() -> tuple[list[str], dict]:
    bad: list[str] = []
    rev = pages_of_all()
    print("\nB 段 传染面：每个 js 的 `el(…)` 顶层键 ⊆ 它用的那份 `el` 认的键")
    stat = {"calls": 0, "unresolved": 0, "files": 0, "total": 0}
    for js in JS_FILES:
        path = STATIC / js
        keys, unresolved, total, detail = el_calls(path)
        stat["total"] += total
        if not detail:
            continue
        prov = provider_of(js, rev)
        if prov is None:
            bad.append(f"B1 {js} 用了 `el(` 但同页找不到任何 `el` 定义——调用会抛 "
                       f"ReferenceError")
            print(f"  ✗ {js}：`el(` 无提供者")
            continue
        allowed = el_keys_of(STATIC / prov)
        stat["files"] += 1
        stat["calls"] += len(detail)
        stat["unresolved"] += unresolved
        over = keys - allowed
        tag = "✓" if not over else "✗"
        print(f"  {tag} {js}（{total} 处调用 / {len(detail)} 处可解析，提供者 {prov}）"
              f" 用键 {sorted(keys)} ⊆ 认键 {sorted(allowed)}")
        if over:
            for ln, k in detail:
                hit = sorted(set(k) & over)
                if hit:
                    bad.append(f"B1 {js}:{ln} 用了 {prov} 的 `el` 不认的键 "
                               f"{hit}——会被**静默丢掉**（元素没拿到，之后一路 null）")
        if unresolved:
            print(f"      · {unresolved} 处第二实参解不出（正常，见 B2）")
    print(f"  · 合计 {stat['files']} 个文件 / 全部 {stat['total']} 处 `el(` 调用 / "
          f"{stat['calls']} 处可解析 / {stat['unresolved']} 处第二实参解不出"
          f"（其余 {stat['total'] - stat['calls'] - stat['unresolved']} 处只传了 tag）")
    if stat["calls"] == 0:
        bad.append("B1 一处 `el({…})` 调用都没解析出来——判据空转（“全绿”是假的）")
    return bad, stat


# ══════════════════════════════════════════════════════════════════
# C 段：加载关系（把 `dom.js` 注释里「不会同时加载」这条断言钉住）
# ══════════════════════════════════════════════════════════════════
#: 反向映射：模板 → 例外。写死但**双向核**（模板改了这里不跟会报红）。
SELF_CONTAINED_PAGE = "overview.html"


def part_c() -> tuple[list[str], dict]:
    bad: list[str] = []
    print("\nC 段 加载关系与工具可用性")
    pages = {p.name: scripts_of(p) for p in sorted(TPLDIR.glob("*.html"))}
    stat = {"pages": 0, "tools": 0}

    # C1：自包含页不引 dom.js（否则 `loadDbChip` 与页内 `loadHealth` 各跑一遍，
    #     同一个 chip 被写两次、`/api/health` 请求两遍）；其余页面必须引
    for name, js in pages.items():
        if not js or "base.html" == name:
            continue
        has_dom = "dom.js" in js
        if name == SELF_CONTAINED_PAGE:
            if has_dom:
                bad.append(f"C1 {name} 引了 dom.js——两份 `el`/`loadDbChip` 同时加载，"
                           f"同一个 chip 被写两次")
            print(f"  {'✓' if not has_dom else '✗'} {name}（自包含）不引 dom.js")
        else:
            if not has_dom:
                bad.append(f"C1 {name} 没引 dom.js——页内脚本用到的 `$`/`el`/`num` 无处可来")
            print(f"  {'✓' if has_dom else '✗'} {name} 引了 dom.js")
        stat["pages"] += 1

    # C2：每个 js 用到的公共工具 ⊆ 它所在页能提供的
    rev = pages_of_all()
    for js in JS_FILES:
        used = uses_of(STATIC / js)
        if not used:
            continue
        avail = set(provides_of(STATIC / js))
        for tpl in rev.get(js, []):
            for other in scripts_of(TPLDIR / tpl):
                avail |= provides_of(STATIC / other)
        missing = set(used) - avail
        stat["tools"] += len(used)
        if missing:
            bad.append(f"C2 {js} 用了 {sorted(missing)}，但它所在页（"
                       f"{','.join(rev.get(js, [])) or '无处'}）没有任何脚本提供这些工具")
        print(f"  {'✓' if not missing else '✗'} {js} 用到 {sorted(used)} ⊆ 可用 "
              f"{sorted(avail)}")
    return bad, stat


# ══════════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════════
#: 锚点句用本节独有的整行标题（§2.5.83 的教训：与别节同族的措辞会让「锚点被删」用例恒真）。
DOC_TITLE = "### 2.5.89 展示层（续）：前端工具的第二份副本——`el()` 的键集双向分叉"


def part_d(stat_b: dict, stat_c: dict) -> list[str]:
    bad: list[str] = []
    print("\nD 段 文档对账（`docs/web/01_在线采集.md`）")
    txt = DOC.read_text(encoding="utf-8")
    wants = [
        ("本节标题锚点", DOC_TITLE),
        ("文件数", f"{stat_b['files']} 个文件"),
        ("全部 el 调用", f"{stat_b['total']} 处 `el(` 调用"),
        ("可解析调用", f"{stat_b['calls']} 处可解析"),
        ("解不出的第二实参", f"{stat_b['unresolved']} 处第二实参解不出"),
        ("页面数", f"{stat_c['pages']} 页"),
    ]
    for label, s in wants:
        hit = s in txt
        print(f"  {'✓' if hit else '✗'} {label}：`{s}`")
        if not hit:
            bad.append(f"D 文档锚点缺失（{label}）：{s}")
    if not bad:
        print("  ✅ 五项锚点全部命中（**值**来自本次实测，不是写死的期望）")
    return bad


# ══════════════════════════════════════════════════════════════════
# 自检：判据本身的正反用例
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    """`--self-test`：一个只会报绿的判据和没有判据是一回事（§2.5.87）。"""
    nbad = 0
    cases = [
        # split_top：B 段的键集全靠它。**实参是去掉花括号后的内容**（`second[1:-1]`），
        # 不是整个对象字面量——首版把花括号写进期望，四条全被判红（用例错，非判据错）。
        ("顶层切分：嵌套对象不切", " cls: 'x', attrs: { id: 'y', hidden: '' } ",
         [" cls: 'x'", " attrs: { id: 'y', hidden: '' } "]),
        ("顶层切分：字符串里的逗号不切", " text: 'a,b', cls: 'c' ",
         [" text: 'a,b'", " cls: 'c' "]),
        ("顶层切分：数组里的逗号不切", " text: f([1,2]), cls: 'c' ",
         [" text: f([1,2])", " cls: 'c' "]),
        ("顶层切分：无逗号", " cls: 'x' ", [" cls: 'x' "]),
    ]
    print("自检 顶层切分 split_top")
    for why, src, want in cases:
        got = split_top(src)
        ok = got == want
        nbad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：实测 {got}")

    # strip_js_comments：注释里的 `$('x')` 不许当代码读
    ccases = [
        ("行注释里的不算", "// el('div', {on: 1})\nel('div', {});", 1),
        ("块注释里的不算", "/* el('div', {on: 1}) */\nel('div', {});", 1),
        ("剥注释后行号仍准", "// a\n// b\nel('div', {});", 1),
        ("正常调用", "el('div', {});", 1),
    ]
    print("自检 剥注释 strip_js_comments")
    for why, src, want in ccases:
        got = len(calls(strip_js_comments(src), "el"))
        ok = got == want
        nbad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：期望 {want} 实测 {got}")

    # fn_def + opts_local：两份 el 的局部选项变量名不同，取错则键集恒空
    fcases = [
        ("换名：const o = opts || {}", "function el(t, opts, c) {\n"
         "  const o = opts || {};\n  if (o.id) t.id = o.id;\n  if (o.on) t.on = o.on;\n}",
         {"id", "on"}),
        ("原地赋值：opts = opts || {}", "function el(t, opts, c) {\n"
         "  opts = opts || {};\n  if (opts.cls) t.cls = opts.cls;\n  if (opts.on) t.on = 1;\n}",
         {"cls", "on"}),
        ("无 || {} 兜底时退回形参名", "function el(t, opts, c) {\n"
         "  if (opts.text) t.text = opts.text;\n}", {"text"}),
        ("**只取顶层**：attrs 内部的键不算",
         "function el(t, opts, c) {\n  const o = opts || {};\n  if (o.attrs) {\n"
         "    Object.keys(o.attrs).forEach(function (k) { t.setAttribute(k, o.attrs[k]); });\n"
         "  }\n}", {"attrs"}),
    ]
    print("自检 `el` 键集提取（fn_def + opts_local）")
    for why, src, want in fcases:
        got = el_keys_of_src(src)
        ok = got == want
        nbad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：期望 {sorted(want)} 实测 {sorted(got)}")

    # el_calls：解不出的第二实参要计数，不能当通过
    ucases = [
        ("字面量可解", "el('div', {cls: 'x'});", ({"cls"}, 0)),
        ("变量解不出", "el('div', opts);", (set(), 1)),
        ("混合", "el('a', {id: 'x'}); el('b', o);", ({"id"}, 1)),
        ("单个参数不算调用点", "el('div');", (set(), 0)),
    ]
    print("自检 `el` 调用点解析（el_calls）")
    for why, src, want in ucases:
        got = el_calls_src(src)
        ok = got == want
        nbad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：期望 {want} 实测 {got}")

    print(f"\n{'✅' if not nbad else '❌'} 自检 {len(cases) + len(ccases) + len(fcases) + len(ucases)}"
          f" 组，{nbad} 组不过")
    return 1 if nbad else 0


def el_keys_of_src(src: str) -> set[str]:
    """`el_keys_of` 的纯字符串版（自检用，避免为用例造临时文件）。"""
    src = strip_js_comments(src)
    d = fn_def(src, "el")
    if not d:
        return set()
    params, body = d
    if len(params) < 2:
        return set()
    return set(re.findall(r"\b" + re.escape(opts_local(body, params[1]))
                          + r"\.([A-Za-z_$][\w$]*)", body))


def el_calls_src(src: str) -> tuple[set[str], int]:
    """`el_calls` 的纯字符串版（自检用）。"""
    src = strip_js_comments(src)
    keys, unresolved = set(), 0
    for _pos, args in calls(src, "el"):
        if len(args) < 2:
            continue
        second = args[1].strip()
        if second.startswith("{") and second.endswith("}"):
            for seg in split_top(second[1:-1]):
                mm = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:", seg)
                if mm:
                    keys.add(mm.group(1))
        else:
            unresolved += 1
    return keys, unresolved


# ══════════════════════════════════════════════════════════════════
# C′ 段：注入式自证——「退出码 0」只有在判据有能力报 1 时才可信（§2.5.68）
#
# 五例（**源码面 4 + 文档面 1**），两个注入面都接：
#   C1  往 `accident.js` 注一个 `{on: …}` ⇒ B1 报 `accident.js:行`。`accident.js` 用的是
#       **`dom.js` 那份 `el`**（不认 `on`）——这正是"将来有人踩坑"的形态。
#   C2  往 `overview.js` 注一个 `{id: …}` ⇒ B1 报 `overview.js:行`。**反方向**：`overview.js`
#       那份不认 `id`。C1 与 C2 一起说明 **B1 是双向可测的**，不是只会抓一头。
#   C3  删 `dom.js` 的 `if (o.id) node.id = o.id;` ⇒ 红的**恰好是 `review.js` 里用顶层 `id`
#       的那几处**（不是全红）——这一例最有说服力：它复现的正是 `dom.js:22-27` 注释里
#       记的那次真实事故（审核页的致因树与操作回执丢了 id），且**归因唯一**。
#   C4  给 `overview.html` 加一行 dom.js ⇒ C1 档报"自包含页引了 dom.js"。
#   C5  **文档面**：改本节标题锚点 ⇒ D 段报锚点缺失。前四例全在改源码，**碰不到 D 段**；
#       只测源码面时文档判据被改坏了不会有任何用例转红（§2.5.68 立、§2.5.71 复立）。
# ══════════════════════════════════════════════════════════════════
INJECTIONS = [
    # ⚠ C1 的 `d` 与 C3 的 `a1` 都是**正确的连带**，不是遮蔽（§2.5.86 立的规矩）：
    #   · C1 让一处的第二实参多出一个可解析的键，**可解析调用数 584→585**，
    #     于是 D 段的 `584 处可解析` 锚点必然失配——同一既成事实的两个独立视角。
    #   · C3 删掉 `dom.js` 的 `id` 支持，**A1 的键集判据也必然报**（独有键集变了 +
    #     登记表过期）——这恰恰是 A1 双向核的价值：它独立发现了同一件事的另一面。
    #   期望集合必须照着判据**实际覆盖的格**来写（§2.5.75 首版写窄被判红的教训）。
    {"name": "C1 accident.js 注 `{on: …}`（dom.js 的 el 不认）",
     "file": STATIC / "accident.js",
     "old": "el('span', {",
     "new": "el('span', { on: { click: function () {} },",
     "count": 1,
     "expect_sub": {"b1": ["accident.js:70"]}},
    {"name": "C2 overview.js 注 `{id: …}`（overview 的 el 不认）",
     "file": STATIC / "overview.js",
     "old": "el('span', { cls: 'n', text: num(v.count) })",
     "new": "el('span', { id: 'zzz_probe', cls: 'n', text: num(v.count) })",
     "expect_sub": {"b1": ["overview.js:370"]}},
    {"name": "C3 删 dom.js 的 id 支持（复现审核页那次丢 id 的事故）",
     "file": STATIC / "dom.js",
     "old": "    if (o.id) node.id = o.id;\n",
     "new": "",
     "expect_sub": {"a1": ["独有键集", "登记表过期"], "b1": ["review.js:624"]}},
    {"name": "C4 overview.html 引 dom.js（两份同时加载）",
     "file": TPLDIR / "overview.html",
     "old": "<script src=\"{{ url_for('static', filename='overview.js') }}\"></script>",
     "new": "<script src=\"{{ url_for('static', filename='dom.js') }}\"></script>\n"
            "<script src=\"{{ url_for('static', filename='overview.js') }}\"></script>",
     "expect_sub": {"c1": ["overview.html"]}},
    {"name": "C5 文档标题锚点被改名（D 段）",
     "file": DOC,
     "old": "键集双向分叉",
     "new": "键集单向分叉",
     "expect_sub": {"d": ["文档锚点缺失"]}},
]


def _marks_of_child() -> dict:
    r = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                       capture_output=True, text=True, timeout=600)
    for ln in r.stdout.splitlines():
        if ln.startswith("MARKS "):
            return json.loads(ln[6:])
    raise RuntimeError("子进程没给出 MARKS 行（退出码 %d）\n--stdout 尾--\n%s"
                       "\n--stderr 尾--\n%s"
                       % (r.returncode, r.stdout[-800:], r.stderr[-800:]))


_MARK_KEYS = ("a1", "a2", "b1", "c1", "c2", "d")


def drift_test() -> int:
    """`--drift-test`：每例注入 → 断言**红的恰好是哪几条**（§2.5.68 的规矩）。"""
    print("基线（注入前）…")
    base = _marks_of_child()
    base_bad = [k for k in _MARK_KEYS if base.get(k)]
    if base_bad:
        print(f"❌ 基线就不干净：{base_bad} = "
              + "; ".join(f"{k}:{base[k]}" for k in base_bad))
        return 1
    print(f"  基线 MARKS 全空（total={base.get('total')}）✓\n")

    nbad = 0
    for i, inj in enumerate(INJECTIONS, 1):
        p: Path = inj["file"]
        orig = p.read_text(encoding="utf-8")
        if inj["old"] not in orig:
            print(f"  ✗ C{i} 锚点未命中：{inj['old'][:60]!r} —— 用例**根本没跑**")
            nbad += 1
            continue
        n = orig.count(inj["old"])
        cnt = inj.get("count")             # None = 全部替换（**不能默认 0**，见下）
        if n > 1:
            # 新增型注入（C1）只需命中一处：全量替换会把同族的 56 处一起改掉，
            # 红的条数从 1 涨到 56，噪音盖过判据本身。删除型注入才必须删净
            # （§2.5.70：只换一处等于没换）。
            print(f"  ⚠ C{i} 锚点命中 {n} 次，{'只替换前 %d 处' % cnt if cnt else '全部替换'}")
        # ⚠⚠ 默认值**不能**写成 `inj.get("count", 0)`：`str.replace(old, new, 0)`
        # 是「替换 **0** 次」而不是「全部替换」。首版就是这么写的，于是没写 `count`
        # 的四例**注入根本没发生**，而输出显示的是「实测红 ∅」——**看起来像判据没报，
        # 实际是注入没做**。下面那条 `injected == orig` 的断言就是为了让这种静默失效
        # 不可能再发生：**漂移自证自己也要自证**。
        injected = (orig.replace(inj["old"], inj["new"]) if cnt is None
                    else orig.replace(inj["old"], inj["new"], cnt))
        if injected == orig:
            print(f"  ✗ C{i} 注入后文件与原文**逐字相同**——注入没生效，用例静默失效")
            nbad += 1
            continue
        try:
            p.write_text(injected, encoding="utf-8")
            got = _marks_of_child()
        finally:
            p.write_text(orig, encoding="utf-8")
        red = {k: got.get(k) for k in _MARK_KEYS if got.get(k)}
        want_keys = set(inj["expect_sub"])
        ok = set(red) == want_keys
        for k, subs in inj["expect_sub"].items():
            hits = red.get(k, [])
            for s in subs:
                if not any(s in h for h in hits):
                    ok = False
        print(f"  {'✓' if ok else '✗'} C{i} {inj['name']}")
        print(f"      期望红 {sorted(want_keys)}；实测红 "
              + (json.dumps(red, ensure_ascii=False) if red else "∅"))
        nbad += 0 if ok else 1

    # 恢复后必须回到全绿（注入有残留时会在这里现形）
    after = _marks_of_child()
    after_bad = [k for k in _MARK_KEYS if after.get(k)]
    if after_bad:
        print(f"  ✗ 恢复后仍红：{after_bad}——注入有残留")
        nbad += 1
    else:
        print(f"\n  ✓ 全部恢复，MARKS 回到全空（total={after.get('total')}）")
    print(f"\n{'✅' if not nbad else '❌'} 漂移自证 {len(INJECTIONS)} 例，{nbad} 例不过")
    return 1 if nbad else 0


# ══════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="§2.5.89 前端工具副本的键集核验")
    ap.add_argument("--self-test", action="store_true", help="只跑判据自身的正反用例")
    ap.add_argument("--drift-test", action="store_true", help="注入漂移，断言退出码转 1")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.drift_test:
        return drift_test()

    a = part_a()
    b, stat_b = part_b()
    c, stat_c = part_c()
    d = part_d(stat_b, stat_c)

    bad = len(a) + len(b) + len(c) + len(d)
    marks = {
        "a1": [m for m in a if m.startswith("A1")],
        "a2": [m for m in a if m.startswith("A2")],
        "b1": [m for m in b],
        "c1": [m for m in c if m.startswith("C1")],
        "c2": [m for m in c if m.startswith("C2")],
        "d": list(d),
        "total": bad,
    }
    print("MARKS " + json.dumps(marks, ensure_ascii=False))
    print(f"\n{'═' * 60}")
    print(f"A1 键集分叉 {len(marks['a1'])} ／ A2 特征分叉 {len(marks['a2'])} ／ "
          f"B1 静默丢键 {len(b)} ／ C1 加载关系 {len(marks['c1'])} ／ "
          f"C2 工具可用性 {len(marks['c2'])} ／ D 文档锚点 {len(d)}")
    print(f"B 段覆盖：{stat_b['files']} 个文件 / {stat_b['calls']} 处可解析调用 / "
          f"{stat_b['unresolved']} 处解不出（不判红）")
    print("✅ 全部通过" if not bad else f"❌ {bad} 项不过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
