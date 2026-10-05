#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.83 读接口的响应面：前端读的键 ↔ 实现返回的键。

## 来路

契约有四格，本节补**最后一格**：

| | 前端 | 实现 | 脚本 |
|---|---|---|---|
| §2.5.80 写·入参 | **发**的键 | **读**的键（`get_json()`） | `check_2580_request_body.py` |
| §2.5.81 写·出参 | **读**的键 | **返**的键（`jsonify()`） | `check_2581_response_body.py` |
| §2.5.82 读·入参 | **拼**的 query 键 | **读**的键（`request.args`） | `check_2582_query_params.py` |
| §2.5.83 读·出参 | **读**的键 | **返**的键（`jsonify()`） | 本脚本 |

后两格（读方法）与 §2.5.80 先做的原因同源：**读方法不带 query 的和写方法一样便宜**，
带 query 的与写方法一样便宜——不，是因为读方法**能打**（不发请求也能静态核），
而写方法打不了。四格全是静态的，没有一格需要真发请求。

## 与 §2.5.81 的对称

判据同构（前端读的键 ⊆ 实现返的键），**对象换成 GET 路由 × 前端 GET 调用点**。
实测读侧比写侧大一倍多：GET 调用点 **29 处**（带 query 9 + 不带 20）对写接口 14 处。

**实现侧整段复用 §2.5.81 的 `impl_side()`**（同一个 `jsonify()` 键树解析，不写第二遍——
§2.5.76）。前端侧**必须新写**：写接口那边响应体的形参总在 `.then(function (b) {...})`
或 `res.body`，读接口这边形参有**五种绑定形态**（见下）。

## 前端响应体的五种绑定形态（实测定型）

| # | 形态 | 样本 | 形参怎么找 |
|---|---|---|---|
| 1 | `.then(function (P) {...})` / `.then((P) => {...})` | 22 处，主流 | 从调用点向后找**第一个** `.then(`，其首参 |
| 2 | `Promise.all([a, b, c]).then(([pa, pb, pc]) => ...)` | 5 处 | 按**索引**对应：先算调用点在第几段，再取数组解构的第几位 |
| 3 | `P = await fetchJson(...)` | 1 处（`accident.js:682`） | 赋值目标；读取范围是**所在函数体** |
| 4 | `.then(renderList)` —— 传函数引用 | 1 处（`overview.js:605`） | 被引用函数的**第一个形参**，读取在其函数体 |
| 5 | `.then(([a, img]) => renderQuality(ov, a, img))` —— 解构后**转发** | 2 处 | 先按形态 2 取到 `a`/`img`，再找它作为**第几个实参**传给谁，取那个函数的对应形参 |

形态 4/5 都是「键读取不在回调体内、在被调用函数体里」——与 §2.5.81 的 `cross_fn`
同一族，但方向不同：`cross_fn` 跟的是 `fn(b.data)`（实参是 data 层），这里跟的是
`fn(b)`（实参是整个响应体 ⇒ 读的是**顶层**键）。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 前端读的**顶层**键，实现顶层都有 | 读了个不存在的键 → 永远 `undefined`，静默 | 红 |
| A2 | 前端读的 `data.*` 键，实现 `data` 都有 | 同上，一层深 | 红 |
| A3 | 有调用点且**有 `data` 键**的路由：`data` 要么解出、要么显式进 B1 | 解析退化成静默豁免（§2.5.67 元判据） | 红 |
| A4 | 调用点**闭式**：定位到形参 + 定位失败 = GET 调用点总数 | 「两头都不报」地静默溜过 | 红 |
| B1 | `data` 存在但子结构解不出的路由 | ——（必须列出，否则 A2 是"我没比"） | 定量 |
| B2 | 响应体形参定位失败的调用点 | ——（必须列出，否则 A1/A2 是"我没比"） | 定量 |
| B3a | 实现返回、调用点从不读，但该键在前端别处出现过 | 同名不同物 / 由别的接口消费 | 定量 |
| B3b | 实现返回、**整个 `static/*.js` 一次都没出现** | 真死字段 | 定量 |
| B4 | `/api/` GET 路由前端一次都没调用 | 接口有能力、页面没入口 | 定量 |

**`data` 键缺失 ≠ B1**：GET 侧与写侧不同，`/api/health`（`{ok, db_path, tables}`）、
`/api/overview` 这类本就不包 `data`。缺失时只跳过 A2，另记 `no_data` 定量列出；
**`data` 键在、子结构解不出**才是 B1（那才是解析失败）。

## 共享封装的读取面（A1 豁免）

前端有三层消费面：调用点直接读（29 处）＋**共享封装统一读**——
`dom.js` 的 `fetchJson` 读 `body.hint` / `body.message`、`overview.js` 的 `jsonGet`
读 `body.message`、`review.js` 的 `req` 读 `b.ok` / `b.message`。经它们的所有调用点
都会（在出错时）消费这几个键。这几个键**由 `wrapper_reads()` 动态算出**（不硬编码），
既并入 B3 的「被读过」集合、又从 A1 豁免（`hint` 是可选的，没返回不算缺陷）。

## 两层路径粒度

顶层键（`data` / `meta` / `items` / `scope` / `ok` / `available` / `hint`）
+ `data` 下的一层键。**第三级登记为超出核验范围**——实测 `data.items` 的元素字段
（`it.title` / `r.raw`）、`d.fields.summary` 都在这条线之外，如实列出而不假装核过。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_2578_response_shape as S                    # noqa: E402
import check_2580_request_body as C                      # noqa: E402
import check_2581_response_body as B                     # noqa: E402
import check_2582_query_params as Q                      # noqa: E402

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
JS = sorted(STATIC.glob("*.js"))

# 锚点句用**本节独有**的整行标题。首版写作「前端读的键 ↔ 实现返回的键（读接口）」——
# 那句在文档里一个字都没有（文档标题按 §2.5.81 的对称写法收尾），而它的核心短语
# `前端读的键 ↔ 实现返回的键` 与 **§2.5.81 的标题逐字相同**。这次撞车反过来把
# **2581 的判据打成了恒真**（§2.5.83 进来后，2581 的"锚点被删"用例静默失效）——
# 两个脚本的 needle 一并收窄为整行标题。§2.5.77：判据在多数样本上恒真就等于没有。
DOC_NEEDLES = ["### 2.5.83 展示层（续）：读接口的响应面——**前端读的键 ↔ 实现返回的键**"]

#: `.then` 与调用点的最大字符距离。实测都在 200 内；放宽到 600 是给
#: `return fetchJson(...);` 之后跨 `})` 的下一级 `.then` 留余量（`causes.js:703`）。
THEN_SPAN = 600

#: 不是响应键的名字（复用 §2.5.81 的名单：JS 内置属性 + promise 链方法）。
#: **`ok` 不在里面**——理由同 §2.5.81：它既是 `Response.ok` 又是响应体顶层键，
#: 塞进 SKIP 会把后者一起漏掉。
SKIP = B.SKIP

#: 前端调用 GET 的四个名字（`fetchJson`/`jsonGet` 是本地封装、`get` 是 review.js
#: 的薄壳、`fetch` 是裸调用）。`Q.fe_calls()` 已经按这份名单找过调用点，这里
#: 只在**行内**重新定位一次字符位置——不重写第二遍解析（§2.5.76）。
CALLER = re.compile(r"(?<![\w$.])(fetchJson|jsonGet|get|fetch)\s*\(")

#: 共享封装：`(文件, 函数名)`。**响应体形参不写死**——封装的第一个形参是
#: `url`（入参），响应体是体内解析链**最后一层 `.then` 的回调形参**
#: （`fetchJson` 是 `body`、`jsonGet` 是 `body`、`req` 是 `b`）。写死会读成
#: `url.xxx`，一个键都收不到——首版就是这么把 `wraps` 三项全报成 `[]` 的。
WRAPS = [("dom.js", "fetchJson"), ("overview.js", "jsonGet"), ("review.js", "req")]

#: 体内解析链最后一层 `.then(...)` 的形参名。
LAST_THEN = re.compile(r"\.then\s*\(\s*(?:function\s*)?\(?\s*([A-Za-z_$][\w$]*)")


# ══════════════════════════════════════════════════════════════════
# 前端侧：调用点 → 响应体形参
# ══════════════════════════════════════════════════════════════════
def _balanced(txt: str, start: int) -> str:
    """`start` 指向 `(` / `[` / `{` 之一 → 返回配对内容（跳过字符串）。"""
    close_of = {"(": ")", "[": "]", "{": "}"}
    op = txt[start]
    cl = close_of.get(op)
    if not cl:
        return ""
    depth, i, n = 0, start, len(txt)
    while i < n:
        c = txt[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and txt[i] != q:
                i += 2 if txt[i] == "\\" else 1
        elif c == op:
            depth += 1
        elif c == cl:
            depth -= 1
            if depth == 0:
                return txt[start + 1:i]
        i += 1
    return txt[start + 1:]


def call_pos(txt: str, line: int) -> int:
    """行号 → 该行**第一个** GET 调用点的字符位置。

    29 处调用点实测每处独占一行、每行只有一个调用点（`causes.js:616/617/619`
    与 `overview.js:704/705` 虽同属一个表达式，但各占一行）。
    """
    ls = txt.splitlines(keepends=True)
    base = sum(len(x) for x in ls[:line - 1])
    m = CALLER.search(ls[line - 1])
    return base + (m.start() if m else 0)


def parse_then_arg(arg: str) -> tuple[list[str], str, str, bool]:
    """`.then(X)` 的 `X` → `(形参名表, 回调体, kind, 是否数组解构)`。

    kind ∈ `{'fn','arrow','ref','none'}`。`ref` 是 `.then(renderList)` 形态，
    此时「形参」是被引用函数的第一个形参、回调体是它的函数体（由调用方补）。
    """
    s = arg.strip()
    if not s:
        return [], "", "none", False
    m = re.match(r"function\s*\(([^)]*)\)", s)
    if m:
        ps = [x.strip() for x in m.group(1).split(",") if x.strip()]
        rest = s[m.end():].lstrip()
        return ps, (_balanced(rest, 0) if rest.startswith("{") else rest), "fn", False
    m = re.match(r"\(([^)]*)\)\s*=>", s)
    if m:
        inner = m.group(1).strip()
        destr = inner.startswith("[")
        ps = [x.strip() for x in inner.strip("[]").split(",") if x.strip()]
        rest = s[m.end():].lstrip()
        return ps, (_balanced(rest, 0) if rest.startswith("{") else rest), \
            "arrow", destr
    m = re.match(r"([A-Za-z_$][\w$]*)\s*=>", s)
    if m:
        rest = s[m.end():].lstrip()
        return [m.group(1)], (_balanced(rest, 0) if rest.startswith("{") else rest), \
            "arrow", False
    m = re.match(r"^([A-Za-z_$][\w$]*)$", s)
    if m:
        return [m.group(1)], "", "ref", False
    return [], "", "none", False


def then_at(txt: str, start: int):
    """从 `start` 起找**第一个** `.then(` → `参数文本`（`None` = 找不到/太远）。"""
    m = re.search(r"\.then\s*\(", txt[start:start + THEN_SPAN])
    if not m:
        return None
    i = start + m.end() - 1                     # 指向 `(`
    args = C._split_args(C._js_balanced(txt, i))
    return args[0] if args else ""


def in_promise_all(txt: str, pos: int):
    """调用点是否落在 `Promise.all([...])` 里 → `(数组闭合下标, 第几段, 段文本)`。"""
    i = txt.rfind("Promise.all", 0, pos)
    if i < 0:
        return None
    j = txt.find("(", i)
    if j < 0:
        return None
    close = j + 1 + len(C._js_balanced(txt, j))
    if close < pos:
        return None                              # 这个 Promise.all 已经结束
    k = txt.find("[", j)
    if k < 0 or k > pos:
        return None
    inner = _balanced(txt, k)
    segs = C._split_args(inner)
    off = 0
    for n, seg in enumerate(segs):
        s0 = inner.find(seg, off)
        if s0 < 0:
            continue
        if k + 1 + s0 <= pos <= k + 1 + s0 + len(seg):
            return close, n, seg
        off = s0 + len(seg)
    return None


def unwrapped(seg: str) -> bool:
    """段表达式是否**把响应体剥掉一层**（`.then((r) => r.data)` 一类）。

    剥了层之后，后面那位数组解构拿到的就不是响应体、而是响应体的 `data`——
    它读的"顶层键"其实是 **data 层键**。判据不看这一步，就会把
    `renderQuality` 里的 `img.available` 当成"读顶层键"，报出假缺陷
    （§2.5.63：判据的宽度决定结论的强度——这里缺的是"解包"这一维）。

    只认「回调体**只读响应体的 `data` 一个键**」这一种形态：读别的键、
    或读了两个以上，都不算解包（保守；宁可多报 B 档，不可少算）。
    """
    i = seg.find(".then")
    if i < 0:
        return False
    t = then_at(seg, i)
    if t is None:
        return False
    ps, body, _kind, _d = parse_then_arg(t)
    if not ps or not body:
        return False
    keys = set(re.findall(
        rf"(?<![\w$.]){re.escape(ps[0])}\.([A-Za-z_$][\w$]*)", body))
    return keys == {"data"}


def _fn_params(full: str, name: str) -> list[str]:
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(([^)]*)\)", full)
    if not m:
        return []
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def reads_param(scope: str, p: str) -> tuple[set[str], set[str]]:
    """形参 `p` 在 `scope` 里被读的键 → `(顶层键, data 层键)`。

    `p.data.KEY` 归 data 层；`p.KEY` 归顶层（`data` 在 SKIP 里，故 `p.data`
    本身不会被误记成顶层键——这是 §2.5.81 用的同一条豁免）。
    """
    top: set[str] = set()
    data: set[str] = set()
    if not p:
        return top, data
    rp = re.escape(p)
    for m in re.finditer(rf"\b{rp}\.data\.([A-Za-z_$][\w$]*)", scope):
        data.add(m.group(1))
    # 别名：`const dist = dRes.data;` ⇒ 后续 `dist.KEY` 也是 data 层键。
    # **必须过一遍 SKIP**：`data` 是数组时，`X.forEach` / `X.length` 是方法/属性
    # 不是键——首版不过滤，`extract.js:311` 与 `review.js:186` 因此报出
    # 「读 data ['forEach','length']，实现不返回」两条假红。
    for a, b in re.findall(
            r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\.data\b",
            scope):
        if b == p:
            data |= {k for k in re.findall(rf"\b{re.escape(a)}\.([A-Za-z_$][\w$]*)",
                                           scope) if k not in SKIP}
    # `((b.data || {}).caics || {})` 形态：括号里就地兜底再取键
    for m in re.finditer(
            r"\(\s*([A-Za-z_$][\w$]*)\s*\.data\s*\|\|\s*\{\}\s*\)\s*\.([A-Za-z_$][\w$]*)",
            scope):
        if m.group(1) == p and m.group(2) not in SKIP:
            data.add(m.group(2))
    for m in re.finditer(rf"\b{rp}\.([A-Za-z_$][\w$]*)", scope):
        if m.group(1) not in SKIP:
            top.add(m.group(1))
    # 形参不是响应体的两种形态（§2.5.81 的两条排除，原样搬过来）：
    # ① `P.json(` —— 只有 fetch 的 Response 才有 ⇒ P 是响应对象；
    # ② `P.body.X` —— P 是包装对象，真响应体走 P.body。
    if re.search(rf"\b{rp}\.json\s*\(", scope) or re.search(rf"\b{rp}\.body\.", scope):
        return set(), set()
    return top, data


def forward_reads(full: str, scope: str, p: str, depth: int = 0
                  ) -> tuple[set[str], set[str]]:
    """形态 5：`renderQuality(ov, a, img)` —— `a` 作为第 i 个实参传给 `fn`。

    只认**裸标识符实参**（`a` 而不是 `a.data` / `a.x`）：实参恰好等于形参名
    本身时，「传的就是整个响应体」才是确定的——`a.data` 那一路由 §2.5.81 的
    `cross_fn` 管（实参是 data 层的值）。这条收窄是 §2.5.66 立的规矩。

    **递归跟**（限 3 层）：`overview.js:762` 的 `ov` 经 `loadQuality(ov)` 再
    传给 `renderQuality(ov, ...)`，只跟一层就漏掉 `ov.data_quality` /
    `ov.province_group`——而那正是概览页质量面板真正读的东西。
    """
    top: set[str] = set()
    data: set[str] = set()
    if not p or depth > 3:
        return top, data
    for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", scope):
        fn = m.group(1)
        args = C._split_args(_balanced(scope, m.end() - 1))
        idx = None
        for n, a in enumerate(args):
            if a.strip() == p:
                idx = n
                break
        if idx is None:
            continue
        ps = _fn_params(full, fn)
        if idx >= len(ps):
            continue
        body = Q.fn_body(full, fn)
        if not body:
            continue
        t2, d2 = reads_param(body, ps[idx])
        top |= t2
        data |= d2
        t3, d3 = forward_reads(full, body, ps[idx], depth + 1)
        top |= t3
        data |= d3
    return top, data


def locate(txt: str, full: str, line: int) -> dict:
    """调用点 → `{'param','scope','kind','why'}`。`why` 非空 ⇒ 进 B2。"""
    pos = call_pos(txt, line)

    # 形态 3：`P = await fetchJson(...)`
    before = txt[max(0, pos - 200):pos]
    m = re.search(r"([A-Za-z_$][\w$]*)\s*=\s*await\s*$", before)
    if m:
        return {"param": m.group(1), "scope": Q.enclosing_fn(txt, pos),
                "kind": "await", "line": line}

    # 形态 2/5：`Promise.all([...]).then(([a, b, c]) => ...)`
    pa = in_promise_all(txt, pos)
    if pa:
        close, idx, seg = pa
        t = then_at(txt, close)
        if t is None:
            return {"param": "", "scope": "", "kind": "promise-all",
                    "why": "Promise.all 之后找不到 .then"}
        ps, body, kind, destr = parse_then_arg(t)
        if not destr:
            return {"param": "", "scope": "", "kind": "promise-all",
                    "why": "Promise.all 的 then 不是数组解构"}
        if idx >= len(ps):
            return {"param": "", "scope": "", "kind": "promise-all",
                    "why": f"数组解构只有 {len(ps)} 位、调用点是第 {idx + 1} 位"}
        return {"param": ps[idx], "scope": body, "kind": "promise-all",
                "unwrap": "data" if unwrapped(seg) else "", "line": line}

    # 形态 1/4：从调用点向后找第一个 `.then(`
    t = then_at(txt, pos)
    if t is None:
        return {"param": "", "scope": "", "kind": "then",
                "why": "调用点之后找不到 .then"}
    ps, body, kind, det = parse_then_arg(t)
    if kind == "ref":
        name = ps[0]
        ps2 = _fn_params(full, name)
        body = Q.fn_body(full, name)
        if not ps2 or not body:
            return {"param": "", "scope": "", "kind": "ref",
                    "why": f"`.then({name})` 找不到该函数或它没有形参"}
        return {"param": ps2[0], "scope": body, "kind": "ref", "line": line}
    if kind == "none" or not ps:
        return {"param": "", "scope": "", "kind": kind or "none",
                "why": f"`.then({t[:40]})` 解不出响应体形参"}
    return {"param": ps[0], "scope": body, "kind": kind, "line": line}


def cross_data(full: str, scope: str, p: str) -> set[str]:
    """`renderCooc(cRes.data)` —— 形参的**值**转给 `fn`，那些键全归 data 层。

    与 §2.5.81 的 `cross_fn` 同一族，但多了**形参名收窄**：只认实参恰好是
    `p.data` 的（`cross_fn` 原版不查是哪个变量，回调体里出现 `别的变量.data`
    就会误收——§2.5.66 的规矩）。
    """
    out: set[str] = set()
    for m in re.finditer(
            r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(\s*([A-Za-z_$][\w$]*)\.data\b", scope):
        if m.group(2) != p:
            continue
        ps = _fn_params(full, m.group(1))
        body = Q.fn_body(full, m.group(1))
        if not ps or not body:
            continue
        t, d = reads_param(body, ps[0])
        out |= t | d
    return out


def fe_reads(calls) -> list[dict]:
    """29 处 GET 调用点 → 各自读的键。"""
    out: list[dict] = []
    for c in calls:
        f = STATIC / c["file"]
        full = f.read_text(encoding="utf-8")
        loc = locate(full, full, c["line"])
        top: set[str] = set()
        data: set[str] = set()
        if loc.get("scope") and loc["param"]:
            top, data = reads_param(loc["scope"], loc["param"])
            data |= cross_data(full, loc["scope"], loc["param"])
            # 转发只在「自身一个键都没直接读到」时才跟——直接读的形参
            # （`dRes` / `body` 一类）不会被别的函数同形参名干扰
            if not top:
                t2, d2 = forward_reads(full, loc["scope"], loc["param"])
                top |= t2
                data |= d2
        if loc.get("unwrap") == "data":
            # 段里已经 `.then((r) => r.data)` 剥过一层：回调形参拿到的就是响应体
            # 的 `data` 本体，它读的"顶层键"统统是 **data 层键**。
            data |= top
            top = set()
        out.append({**c, "param": loc.get("param", ""), "kind": loc.get("kind", ""),
                    "why": loc.get("why", ""), "top": top, "data": data,
                    "unwrap": loc.get("unwrap", "")})
    return out


def wrapper_reads() -> dict[str, set[str]]:
    """共享封装体内的读键 → `{'dom.js:fetchJson': {'hint','message'}, ...}`。"""
    out: dict[str, set[str]] = {}
    for fname, fn in WRAPS:
        full = (STATIC / fname).read_text(encoding="utf-8")
        body = Q.fn_body(full, fn)
        if not body:
            continue
        ps = LAST_THEN.findall(body)
        p = ps[-1] if ps else ""
        t, _d = reads_param(body, p)
        out[f"{fname}:{fn}"] = t
    return out


# ══════════════════════════════════════════════════════════════════
# 比对
# ══════════════════════════════════════════════════════════════════
def compare(routes, trees, whys, reads_fe, wraps) -> dict:
    a1: list[str] = []
    a2: list[str] = []
    b1: list[str] = []
    b2: list[str] = []
    b4: list[str] = []
    no_data: list[str] = []
    bound = 0
    with_calls = 0
    resolved = 0
    has_data = 0
    unresolved_data = 0      # data 键在、子结构解不出 → B1（不参与 A2 比对）
    unresolved_top = 0       # 顶层全解不出 → B1（这条路由的键一个都没比）
    skipped_calls = 0        # ↑ 这些路由名下的调用点数：A1/A2 **没覆盖**它们

    js_all = "\n".join(p.read_text(encoding="utf-8") for p in JS)
    wrap_keys: set[str] = set()
    for ks in wraps.values():
        wrap_keys |= ks

    by_ep: dict[str, list[dict]] = {}
    for _m, rule, ep in routes:
        if not str(rule).startswith("/api/"):
            continue                                 # 页面路由不是「接口」
        rx = C.rule_re(rule)
        hits = [c for c in reads_fe if rx.match(c["path"])]
        if not hits:
            b4.append(f"GET {rule}（{ep}）前端一次都没调用")
            continue
        with_calls += 1
        by_ep[ep] = hits
        tree = trees.get(ep) or {}
        top_got = set(tree.keys())
        if not top_got:
            unresolved_top += 1
            skipped_calls += len(hits)
            b1.append(f"GET {rule}（{ep}）顶层与 data 全解不出"
                      f"   why={whys.get(ep)}")
            continue
        data_got = tree.get("data")
        if "data" not in tree:
            no_data.append(f"GET {rule}（{ep}）返回体不包 data（顶层 "
                           f"{sorted(top_got)}）")
        elif isinstance(data_got, dict):
            has_data += 1
            resolved += 1
        else:
            has_data += 1
            unresolved_data += 1
            b1.append(f"GET {rule}（{ep}）data 键在、子结构解不出"
                      f"   why={whys.get(ep)}")
        for h in hits:
            if h["why"]:
                b2.append(f"{h['file']}:{h['line']} {h['path']} "
                          f"响应体形参定位失败：{h['why']}")
                continue
            bound += 1
            extra_t = h["top"] - top_got - wrap_keys
            if extra_t:
                a1.append(f"{h['file']}:{h['line']} GET {rule} "
                          f"读顶层 {sorted(extra_t)}，实现不返回")
            if not isinstance(data_got, dict):
                continue
            extra_d = h["data"] - set(data_got.keys())
            if extra_d:
                a2.append(f"{h['file']}:{h['line']} GET {rule} "
                          f"读 data {sorted(extra_d)}，实现不返回")

    # B3：实现返回、调用点从不读
    b3a: list[str] = []
    b3b: list[str] = []
    for ep, hits in sorted(by_ep.items()):
        tree = trees.get(ep) or {}
        if not tree:
            continue
        got = set(tree.keys())
        dg = tree.get("data")
        if isinstance(dg, dict):
            got |= set(dg.keys())
        read = set(wrap_keys)
        for h in hits:
            read |= h["top"] | h["data"]
        for k in sorted(got - read):
            if k in B.DEV_KEYS:
                continue
            (b3a if re.search(rf"\.{re.escape(k)}\b", js_all) else b3b).append(
                f"{ep} 返回 {k}，该接口的 GET 调用点从不读")

    # A3 是**闭式**：每条路由/每个调用点各有归属，不重不漏——不是"必须全部解出"。
    # 「解不出」本身不是缺陷（进 B1 显式列出即合规），但**没被列出的**才是缺陷
    # （§2.5.67 的元判据：解析失败不得静默降级成空集合，也不得悄悄少算）。
    a3: list[str] = []
    if resolved + unresolved_data != has_data:
        a3.append(f"有 data 的路由 {has_data} 条 ≠ 解出 {resolved} + B1 "
                  f"{unresolved_data}")
    if has_data + len(no_data) + unresolved_top != with_calls:
        a3.append(f"有调用点的路由 {with_calls} 条 ≠ 包 data {has_data} + "
                  f"不包 {len(no_data)} + 顶层解不出 {unresolved_top}")
    tot = bound + len(b2) + skipped_calls
    if tot != len(reads_fe):
        a3.append(f"调用点 {len(reads_fe)} 处 ≠ 绑定 {bound} + 形参定位失败 "
                  f"{len(b2)} + 顶层解不出跳过 {skipped_calls}")

    rep = {"a1": a1, "a2": a2, "a3": a3, "b1": b1, "b2": b2, "b3a": b3a,
           "b3b": b3b, "b4": b4, "no_data": no_data,
           "bound": bound, "with_calls": with_calls, "resolved": resolved,
           "has_data": has_data, "unresolved_data": unresolved_data,
           "unresolved_top": unresolved_top, "skipped_calls": skipped_calls,
           "routes": len(routes),
           "api_routes": sum(1 for _m, r, _e in routes if str(r).startswith("/api/")),
           "calls": len(reads_fe),
           "wraps": {k: sorted(v) for k, v in wraps.items()},
           "reds": {k: v for k, v in (("a1", a1), ("a2", a2), ("a3", a3)) if v},
           "exit": 1 if (a1 or a2 or a3) else 0}
    return rep


# ══════════════════════════════════════════════════════════════════
# 段
# ══════════════════════════════════════════════════════════════════
FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return bool(cond)


def part_a(routes, trees, whys, reads_fe, wraps, rep) -> None:
    print("\n═══ A. 实测 ═══")
    print(f"  GET 路由 {rep['routes']} 条（其中 /api/ {rep['api_routes']} 条）"
          f"，前端 GET 调用点 {len(reads_fe)} 处，绑定 {rep['bound']} 对")
    print(f"  data 键存在的路由 {rep['has_data']} 条：解出 {rep['resolved']}"
          f"、B1 {len(rep['b1'])}；不包 data 的 {len(rep['no_data'])} 条")
    print(f"  调用点 {rep['calls']} 处：参与 A1/A2 比对 {rep['bound']} 处"
          f"、形参定位失败 {len(rep['b2'])} 处"
          f"、**未覆盖** {rep['skipped_calls']} 处（其路由顶层就解不出）")
    print("  路由 → 实现返回的键：")
    for _m, rule, ep in routes:
        if not str(rule).startswith("/api/"):
            continue
        t = trees.get(ep) or {}
        d = t.get("data") if isinstance(t.get("data"), dict) else None
        tag = "" if d is not None else "  ⚠️ data 解不出"
        print(f"    {rule:44s} {ep:34s} top={sorted(t)}")
        if "data" in t:
            print(f"    {'':44s} {'':34s} data="
                  f"{sorted(d) if d is not None else '—'}{tag}")
    print("  前端调用点读的键：")
    for h in reads_fe:
        if h["why"]:
            print(f"    {h['file']}:{h['line']:>4}  {h['path']:40s}"
                  f"  ⚠️ 形参定位失败（{h['kind']}）：{h['why']}")
            continue
        print(f"    {h['file']}:{h['line']:>4}  {h['path']:40s}"
              f"  形参 {h['param']:8s}({h['kind']})  top={sorted(h['top']) or '∅'}"
              f"  data={sorted(h['data']) or '∅'}")
    print(f"  共享封装的读取面：{rep['wraps']}")


def part_b(rep) -> None:
    print("\n═══ B. 硬判据 ═══")
    ok(not rep["a1"], "A1 前端读的顶层键，实现都返回",
       "" if not rep["a1"] else str(rep["a1"][:3]))
    ok(not rep["a2"], "A2 前端读的 data 键，实现都返回",
       "" if not rep["a2"] else str(rep["a2"][:3]))
    ok(not rep["a3"],
       f"A3 闭式：路由与调用点各有归属，不重不漏（有 data 的 {rep['has_data']} 条 "
       f"= 解出 {rep['resolved']} + B1 {rep['unresolved_data']}；调用点 "
       f"{rep['calls']} 处 = 绑定 {rep['bound']} + 解不出 {len(rep['b2'])} + "
       f"顶层解不出跳过 {rep['skipped_calls']}）",
       "" if not rep["a3"] else str(rep["a3"]))
    print("\n═══ B'. 定量（不判红）═══")
    print(f"  B1 data 存在但解不出的路由：{len(rep['b1'])} 条")
    for s in rep["b1"]:
        print(f"    · {s}")
    print(f"  B2 响应体形参定位失败的调用点：{len(rep['b2'])} 处")
    for s in rep["b2"]:
        print(f"    · {s}")
    print(f"  B3a 实现返回、调用点不读，但前端别处出现过：{len(rep['b3a'])} 条")
    for s in rep["b3a"]:
        print(f"    · {s}")
    print(f"  B3b 实现返回、整个 static/*.js 一次都没出现（真死字段）："
          f"{len(rep['b3b'])} 条")
    for s in rep["b3b"]:
        print(f"    · {s}")
    print(f"  B4 /api/ GET 路由前端一次都没调用：{len(rep['b4'])} 条")
    for s in rep["b4"]:
        print(f"    · {s}")
    print(f"  不包 data 的路由（顶层就是全部返回）：{len(rep['no_data'])} 条")
    for s in rep["no_data"]:
        print(f"    · {s}")


def _run_diff() -> dict:
    p = subprocess.run([S.PY, __file__, "--diff"], capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
    if not line:
        raise RuntimeError(f"--diff 无 JSON：{p.stdout[-400:]}{p.stderr[-400:]}")
    return json.loads(line[0][len("@@JSON@@"):])


def part_c() -> None:
    """注入式自证：断言**红的恰好是哪几条**（§2.5.68）。"""
    print("\n═══ C. 注入式自证（红的恰好是哪几条）═══")
    base = _run_diff()
    ok(base["reds"] == {}, "基线（未注入）红集合为空",
       f"实际 {sorted(base['reds'])}")
    nbound, nb2 = base["bound"], len(base["b2"])
    ncall = base["calls"]

    cases = [
        ("前端把 `/api/collect/raw` 的顶层 `body.meta` 改成 `body.meta2`"
         "（实现不返回 → 永远 undefined）",
         STATIC / "collect.js",
         "rawMeta = body.meta || rawMeta;", "rawMeta = body.meta2 || rawMeta;",
         {"a1"}),
        ("前端把 `/api/review/stats` 的 data 层 `d.available` 改成 `d.availables`",
         STATIC / "review.js",
         "if (!d.available) {", "if (!d.availables) {",
         {"a2"}),
    ]
    for label, path, old, new, want in cases[:2]:
        with S._with_file(path, old, new):
            got = _run_diff()
            reds = set(got["reds"])
        ok(reds == want, f"{label} → 红集合恰好 {sorted(want) or '∅'}",
           f"实际 {sorted(reds)}")

    # 形参定位失败必须进 B2、且**不静默算通过**：把一处 `.then(function (b))`
    # 换成无形参的 `.then(function ())`，该调用点应进 B2（闭式仍成立）
    with S._with_file(STATIC / "review.js",
                      ".then(function (b) {\n      var d = b.data || {};",
                      ".then(function () {\n      var d = b.data || {};"):
        got = _run_diff()
    ok(len(got["b2"]) == nb2 + 1 and got["reds"] == {},
       f"把一处回调改成无形参 → B2 {nb2} → {len(got['b2'])}、"
       f"红集合仍空（解不出不等于错了，但必须显式列出）",
       f"实际 B2={len(got['b2'])} reds={sorted(got['reds'])}")

    # B1 不是静默豁免：把一条**当前解得出**的路由的 data 换成解不出的调用
    if base["has_data"]:
        with S._with_file(WEB / "extract.py",
                          '"data": taxonomy_payload()})',
                          '"data": _gone()})'):
            got = _run_diff()
        ok(len(got["b1"]) == len(base["b1"]) + 1,
           f"把一条路由的 data 换成解不出的调用 → B1 "
           f"{len(base['b1'])} → {len(got['b1'])}"
           f"（转进 B1 被显式列出，不静默通过）", f"实际 {len(got['b1'])}")
        ok(got["reds"] == {}, "  同一处转 B1 后不误报 A2（解不出不等于错了）",
           f"实际 {sorted(got['reds'])}")

    # 闭式：给一处调用点加个尾注不改任何计数，恒等式必须仍成立
    with S._with_file(STATIC / "collect.js",
                      "return fetchJson('/api/collect/tasks?limit=30')",
                      "return fetchJson('/api/collect/tasks?limit=30') /* x */"):
        got = _run_diff()
    tot = got["bound"] + len(got["b2"]) + got["skipped_calls"]
    ok(tot == got["calls"] == ncall,
       f"闭式在注入后仍成立（绑定 {got['bound']} + 解不出 {len(got['b2'])} "
       f"+ 未覆盖 {got['skipped_calls']} = {ncall}）",
       f"实际 {got['bound']}+{len(got['b2'])}+{got['skipped_calls']}={got['calls']}")

    # **解包识别**也有失败路径：把 `.then((r) => (r && r.data) || null)`
    # 换成不解包的 `.then((r) => r)`，`img` 就回到"响应体本体"、
    # `img.available` 成了读顶层键——A1 必须转红（判据认得出这一维）
    with S._with_file(STATIC / "overview.js",
                      "jsonGet('/api/images/stats').then((r) => (r && r.data) || null)",
                      "jsonGet('/api/images/stats').then((r) => r)"):
        got = _run_diff()
    ok(set(got["reds"]) == {"a1"},
       "把图片统计的解包去掉（回调改回不解包） → 红集合恰好 ['a1']",
       f"实际 {sorted(got['reds'])}")

    # 文档面：D 段也得有自己的失败路径
    dcases = [
        ("01 号文档的「GET 调用点」数被改错",
         DOC, "| GET 调用点 | **29** |", "| GET 调用点 | **28** |",
         "文档「GET 调用点」应为 29"),
        ("01 号文档本节标题被改（锚点不再可辨）",
         DOC, "### 2.5.83 展示层（续）：读接口的响应面——**前端读的键 ↔ 实现返回的键**",
         "### 2.5.83 展示层（续）：读接口的响应界面——**前端读的键 ↔ 实现返回的键**",
         "01 号文档缺锚点"),
    ]
    for label, path, old, new, want in dcases:
        with S._with_file(path, old, new):
            got = _run_diff()["docreds"]
        ok(any(want in g for g in got), f"{label} → D 段报错",
           f"实际 {got[:2] if got else '（D 段没报）'}")


def _doc_stat(rep) -> dict[str, int]:
    return {
        "API GET 路由": rep["api_routes"],
        "GET 调用点": rep["calls"],
        "定位到响应体形参": rep["bound"],
        "GET 前端读而实现不返回": len(rep["a1"]) + len(rep["a2"]),
        "返回结构解不出的路由": len(rep["b1"]),
        "GET 真死字段": len(rep["b3b"]),
    }


def doc_checks(rep) -> list[str]:
    out: list[str] = []
    txt = DOC.read_text(encoding="utf-8")
    for needle in DOC_NEEDLES:
        if needle not in txt:
            out.append(f"01 号文档缺锚点「{needle}」")
    for label, n in _doc_stat(rep).items():
        mm = re.search(r"\|\s*" + re.escape(label)
                       + r"\s*\|\s*\*\*([\d,]+)\*\*\s*\|", txt)
        if not mm:
            out.append(f"文档表里缺「{label}」一行（应为 {n}）")
        elif int(mm.group(1).replace(",", "")) != n:
            out.append(f"文档「{label}」应为 {n}，实际写的是 {mm.group(1)}")
    return out


def part_d(rep) -> None:
    print("\n═══ D. 文档 ↔ 实测 ═══")
    bad = doc_checks(rep)
    for label, n in _doc_stat(rep).items():
        hit = [b for b in bad if label in b]
        ok(not hit, f"文档表里「{label}」= {n}", "" if not hit else f"（{hit[0]}）")
    for needle in DOC_NEEDLES:
        ok(needle in DOC.read_text(encoding="utf-8"),
           f"01 号文档里有锚点「{needle[:22]}」")


def collect_all():
    """复用 §2.5.82 的 GET 路由与调用点清单（同一份绑定，不重写第二遍）。"""
    S.point_at(str(S.main_db_path()), str(S.staging_path()))
    from construction_agent.web.factory import create_app

    app = create_app()
    routes = [("GET", rule, ep) for rule, ep in Q.get_routes(app)]
    # `static`（Flask 内建）等**无模块前缀**的 endpoint 交给 `impl_side` 会直接崩
    # （`ep.split(".", 1)` 解不出两段）——它们本来也不是接口，先摘出去。
    trees, whys = B.impl_side([r for r in routes if "." in r[2]])
    calls, bare = Q.fe_calls()
    return routes, trees, whys, fe_reads(calls + bare), wrapper_reads()


def main() -> int:
    if "--diff" in sys.argv:
        routes, trees, whys, reads_fe, wraps = collect_all()
        rep = compare(routes, trees, whys, reads_fe, wraps)
        rep["docreds"] = doc_checks(rep)
        print("@@JSON@@" + json.dumps(rep, ensure_ascii=False))
        return 1 if (rep["reds"] or rep["docreds"]) else 0

    routes, trees, whys, reads_fe, wraps = collect_all()
    rep = compare(routes, trees, whys, reads_fe, wraps)
    part_a(routes, trees, whys, reads_fe, wraps, rep)
    part_b(rep)
    part_c()
    part_d(rep)

    print()
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项：")
        for f in FAILED:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
