#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.81 写接口的响应面：前端读的键 ↔ 实现返回的键。

## 来路

`docs/web/09_端到端对齐核查.md` §10.5 顶部第 0 条（§2.5.80 新开）：

    只读核查不能打写库路由（打了就改数据），§2.5.80 只静态核了**入参**那一半；
    **「返回什么」这一半一条判据都没有**……要核它得另想办法
    （真打一次工作库？还是读实现的返回构造？）

§2.5.80 已把「打不了」与「不能核」分开过一次，本节把它分开第二次：**出参面**
同样完全静态可核——契约的两端都在磁盘上，一边是调用方读 `res.body.KEY` /
`.then(function (b) { b.KEY })`，另一边是被调用方 `jsonify({...})` 的构造。
本节选「读实现的返回构造」，**全程不发一个请求**，工作库一个字节都不会动。

## 与 §2.5.80 的对称

| | 前端 | 实现 | 脚本 |
|---|---|---|---|
| §2.5.80 入参面 | **发**的键 | **读**的键（`get_json()`） | `check_2580_request_body.py` |
| §2.5.81 出参面 | **读**的键 | **返**的键（`jsonify()`） | 本脚本 |

两者方向相反、判据同构，共用同一份 `routes / calls` 绑定（复用 §2.5.80 的
`collect_all()`，不重写第二遍解析——§2.5.76 立的规矩）。

**与 §2.5.77 的分工**：§2.5.77 核的是**读接口**的「结果集 → HTTP 响应」
（主库列 → SELECT → 响应键）；写接口不走 SELECT，返回的是**操作结果**
（`updated` / `promoted` / `removed_accidents`），故不在 §2.5.77 射程内。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 前端读的**顶层**键，实现顶层都有 | 读了个不存在的键 → 永远 `undefined`，静默 | 红 |
| A2 | 前端读的 `data.*` 键，实现 `data` 都有 | 同上，一层深 | 红 |
| A3 | 有调用点的路由：`data` 要么解出、要么显式进 B1 | 解析退化成静默豁免（§2.5.67 元判据） | 红 |
| B1 | `data` 结构解不出的写路由 | ——（必须列出，否则 A2 是"我没比"） | 定量 |
| B2a | 实现返回、写调用点从不读，但该键在前端别处出现过 | 同名不同物 / 由 GET 路径消费 | 定量 |
| B2b | 实现返回、**整个 `static/*.js` 一次都没出现** | 真死字段（造了没人用） | 定量 |

**A2 只对该路由 `data` 可解时判定**；解不出的进 B1，**不当作通过**——否则
"解不出"会变成一条静默豁免（§2.5.69 起反复立的规矩）。

## 两层路径粒度

顶层键（`ok` / `message` / `error` / `hint` / `available` / `note` / `stage`）
+ `data` 下的一层键（`data.updated`）。**第三级登记为超出核验范围**——实测
`review.js:828` 读 `p.accidents_insert`（`data.plan` 的子键），那要递归整个
`plan` 结构，收益与解析成本不成比例，如实列出而不假装核过。

## 共享封装：写接口顶层 `error` / `hint` 的消费者不在调用点

实测前端有两层消费面：**调用点直接读**（14 处）与**共享封装统一读**——

- `extract.js:84 send()`：`if (!r.ok || b.ok === false) throw new Error(b.message || ...)`
  ⇒ 读 `ok` / `message`。经它的 7 处调用点**自身不读任何键**，全靠这一层。
- `dom.js:102`（`fetchJson` 的错误 chip）：读 `body.hint` / `body.message`。
  `fetchJson` **只做 GET**（§2.5.80 已查明），故它消费的是读接口的 `hint`。

结论：写接口返回的 `error` / `hint`（`dberr.describe()` 与
`staging_db.unavailable_payload()` 的产物）**在前端没有调用点消费**——它们
只在 GET 路径上被读。这是 B2a 的典型样本，不是缺陷，但必须写清楚，
否则「`hint` 有用」这句话在写接口上是没有依据的。
"""
from __future__ import annotations

import ast
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

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
JS = sorted(STATIC.glob("*.js"))

#: 实现侧模块搜索路径：web/ 优先，其次 review/，再次包根
SEARCH = [WEB, ROOT / "construction_agent" / "review", ROOT / "construction_agent"]

#: 表达式递归深度上限。实测链长 5–8 层（路由 → 辅助函数 → 业务函数 → 字典
#: 字面量 → `**probe()` → `describe()` → 字面量），4 会误报"深度超限"。
MAXD = 20

DOC_NEEDLES = ["前端读的键 ↔ 实现返回的键"]


# ══════════════════════════════════════════════════════════════════
# 实现侧：写路由 → 返回的键树
# ══════════════════════════════════════════════════════════════════
_FCACHE: dict[str, tuple[dict, dict]] = {}


def _find_mod(modname: str) -> Path | None:
    for d in SEARCH:
        p = d / f"{modname}.py"
        if p.exists():
            return p
    return None


def _aliases_of(tree: ast.Module) -> dict[str, str]:
    """import 别名 → 真实模块 basename（`from ..review import promote as pm`）。"""
    out: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                out[a.asname or a.name] = a.name
        elif isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = a.name.split(".")[-1]
    return out


def _module(modname: str) -> tuple[dict, dict]:
    if modname not in _FCACHE:
        p = _find_mod(modname)
        if p is None:
            _FCACHE[modname] = ({}, {})
        else:
            tree = ast.parse(p.read_text(encoding="utf-8"))
            _FCACHE[modname] = (C._funcs_of(tree), _aliases_of(tree))
    return _FCACHE[modname]


def _is_jsonify(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "jsonify") or \
           (isinstance(node, ast.Attribute) and node.attr == "jsonify")


def _own_returns(fn: ast.FunctionDef) -> list[ast.Return]:
    """该函数**自己的** return（不穿进嵌套函数）。

    穿进去会把内层闭包的 return 算成外层路由的返回，实测会凭空多出键。
    """
    out: list[ast.Return] = []

    def walk(node):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(ch, ast.Return):
                out.append(ch)
            walk(ch)

    walk(fn)
    return out


def _merge(dst: dict, src) -> None:
    """并进键树。`None` 是"键在、子结构未知"的**哨兵值**，不是"没有"。

    `{}` = 键在且子结构已知为空（标量值）；`None` = 键在但子结构解不出。
    两者对 A1/A2 的意义相反：前者能证明"子键不存在"，后者不能——§2.5.73
    立的规矩（取不到要判"未知"，不能降级成空集合）。
    """
    if not isinstance(src, dict):
        return
    for k, v in src.items():
        if k not in dst:
            dst[k] = v
        elif isinstance(dst[k], dict) and isinstance(v, dict):
            dst[k].update(v)
        elif dst[k] is None and isinstance(v, dict):
            dst[k] = v          # 已知优于未知


def _tree_of_var(name: str, fn, mod, depth: int, why: list[str]):
    """变量赋值链：取同名变量的**最后一次**赋值（顺序执行下的有效定义）。"""
    if depth > MAXD:
        why.append(f"深度超限 变量{name}")
        return None
    last = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    last = n.value
    if last is None:
        return None
    return _tree(last, fn, mod, depth + 1, why)


def _tree_of_call(call: ast.Call, fn, mod, depth: int, why: list[str]):
    fname = hint = None
    if isinstance(call.func, ast.Name):
        fname = call.func.id
    elif isinstance(call.func, ast.Attribute):
        fname = call.func.attr
        if isinstance(call.func.value, ast.Name):
            hint = call.func.value.id
    if depth > MAXD:
        why.append(f"深度超限 {hint + '.' if hint else ''}{fname}")
        return None
    alias = _module(mod)[1].get(hint, hint) if hint else None
    for modname in [m for m in (alias, mod) if m]:
        funcs, _ = _module(modname)
        if fname in funcs:
            return _func_tree(funcs[fname], modname, depth + 1, why)
    why.append(f"调用解不出 {hint + '.' if hint else ''}{fname}")
    return None


def _tree(expr, fn, mod, depth: int, why: list[str]):
    """表达式 → `{key: 子树 or {}}`；`None` 表示**结构解不出**。

    `{}` 是"这个键存在、值不是可展开的字典"，与 `None`（解不出）语义不同：
    前者能证明键存在，后者不能证明任何事。
    """
    if depth > MAXD:
        why.append("深度超限（表达式）")
        return None
    if isinstance(expr, ast.Dict):
        out: dict = {}
        for k, v in zip(expr.keys, expr.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                if isinstance(v, (ast.Dict, ast.Name, ast.Call, ast.Subscript)):
                    sub = _tree(v, fn, mod, depth + 1, why)
                    # 解不出 ⇒ None（键在、子结构未知），**不是** `{}`
                    out[k.value] = sub if isinstance(sub, dict) else None
                else:
                    out[k.value] = {}
            elif k is None:
                # `**expr` 展开：解不出就丢失整片键，必须记 why
                n0 = len(why)
                sub = _tree(v, fn, mod, depth + 1, why)
                if sub is None and len(why) == n0:
                    why.append(f"**展开解不出 {getattr(v, 'id', type(v).__name__)}")
                _merge(out, sub)
            else:
                why.append("动态键")
        return out
    if isinstance(expr, ast.Name):
        return _tree_of_var(expr.id, fn, mod, depth + 1, why)
    if isinstance(expr, ast.Call):
        return _tree_of_call(expr, fn, mod, depth + 1, why)
    if isinstance(expr, ast.Subscript):
        base = _tree(expr.value, fn, mod, depth + 1, why)
        if isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, str):
            if isinstance(base, dict):
                v = base.get(expr.slice.value)
                return v if isinstance(v, dict) else {}
            return None
        return None
    return None


def _func_tree(fn, mod, depth: int = 0, why: list[str] | None = None):
    """函数所有 return 求值出的键树的并集。`jsonify(x)` 剥掉外壳取 `x`。

    只记**影响键集合**的失败（`**v` 展开失败、调用解不出、动态键），
    不记值层面的失败（`len(ids)` / f-string 解不出不影响键）——§2.5.65 立的
    规矩：判据噪音淹没真信号等于没有判据。
    """
    why = [] if why is None else why
    out: dict = {}
    for r in _own_returns(fn):
        v = r.value
        if v is None:
            continue
        if isinstance(v, ast.Tuple) and v.elts:
            v = v.elts[0]
        if isinstance(v, ast.Call) and _is_jsonify(v.func):
            if not v.args:
                why.append("jsonify() 无参")
                continue
            v = v.args[0]
        _merge(out, _tree(v, fn, mod, depth, why))
    return out


def impl_side(routes) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """写路由 → (键树, why)。why 非空且 `data` 解不出 ⇒ 该路由进 B1。"""
    trees: dict[str, dict] = {}
    whys: dict[str, list[str]] = {}
    for _m, _rule, ep in routes:
        mod, fname = ep.split(".", 1)
        fn = _module(mod)[0].get(fname)
        why: list[str] = []
        if fn is None:
            why.append("实现侧没找到该函数")
            trees[ep] = {}
        else:
            trees[ep] = _func_tree(fn, mod, why=why)
        whys[ep] = sorted(set(why))
    return trees, whys


# ══════════════════════════════════════════════════════════════════
# 前端侧：写调用点 → 读的键
# ══════════════════════════════════════════════════════════════════
NAMED_FN = re.compile(r"^\s*function\s+[A-Za-z_$][\w$]*\s*\(")
THEN_PARAM = re.compile(r"\.then\(\s*function\s*\(\s*([A-Za-z_$][\w$]*)\s*\)")
ALIAS = re.compile(r"var\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*\.data\b")
DATA_READ = re.compile(r"\.data\.([A-Za-z_$][\w$]*)")
BODY_READ = re.compile(r"\.body\.([A-Za-z_$][\w$]*)")
OR_DATA = re.compile(r"\(([\w$]+)\.data\s*\|\|\s*\{\}\)\.([A-Za-z_$][\w$]*)")
CROSS = re.compile(r"([A-Za-z_$][\w$]*)\s*\(\s*[A-Za-z_$][\w$]*\s*\.data\b")
FWDEF = r"function\s+%s\s*\(\s*([A-Za-z_$][\w$]*)"

#: 不是响应键的名字：JS 内置属性、promise 链方法。
#:
#: **`ok` 不在里面**：它既是 fetch Response 的属性（`r.ok`）、也是每个写响应
#: 的顶层键（`res.body.ok`）。放进 SKIP 会把后者一起漏掉——判据的宽度决定
#: 结论的强度（§2.5.63）。`r.ok` 那一侧改由下面两条更精确的规则排除。
SKIP = {"data", "body", "json", "catch", "then", "length", "map", "slice",
        "join", "forEach", "filter", "push", "text", "statusText"}


def region(lines: list[str], start: int) -> tuple[int, int]:
    """调用点所在函数的剩余部分（到下一个顶层 `function` 声明为止）。"""
    for i in range(start + 1, len(lines)):
        if NAMED_FN.match(lines[i]):
            return start, i
    return start, len(lines)


def _brace_body(txt: str, i: int) -> str:
    """从 `{` 起配对，返回花括号内的文本（跳过字符串字面量）。"""
    depth, n = 0, len(txt)
    first = i
    while i < n:
        c = txt[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and txt[i] != q:
                i += 2 if txt[i] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return txt[first + 1:i]
        i += 1
    return txt[first + 1:]


def reads_of(txt: str) -> tuple[set[str], set[str]]:
    """→ (顶层键, data 层键)。`txt` 是调用点所在区域的文本。"""
    top: set[str] = set()
    data: set[str] = set()
    for m in DATA_READ.finditer(txt):
        data.add(m.group(1))
    for m in OR_DATA.finditer(txt):
        data.add(m.group(2))
    for m in BODY_READ.finditer(txt):
        if m.group(1) not in SKIP:
            top.add(m.group(1))
    for m in THEN_PARAM.finditer(txt):
        p = m.group(1)
        brace = txt.find("{", m.start())
        if brace < 0:
            continue
        body = _brace_body(txt, brace)
        for a, b in ALIAS.findall(body):
            if b == p:
                data.update(re.findall(rf"\b{re.escape(a)}\.([A-Za-z_$][\w$]*)", body))
        # 形参不是响应体本体的两种形态：
        # ① `P.json()` —— 只有 fetch 的 Response 才有 ⇒ P 是响应对象。
        #    少了这一句，`r.ok` / `r.status` 会被当成"读了响应体的 ok/status"
        #    而制造假红（实测 collect.js:247）。
        # ② `P.body.X` —— P 是 `.then(function (r) { return {ok: r.ok, body: b}; })`
        #    造出来的**包装对象**，`P.ok` 是 `r.ok` 不是响应体的 `ok`
        #    （实测 collect.js:430）。此时真响应体走 `P.body.X`，已由 BODY_READ 收。
        if re.search(rf"\b{re.escape(p)}\.json\s*\(", body):
            continue
        if re.search(rf"\b{re.escape(p)}\.body\.", body):
            continue
        for mm in re.finditer(rf"\b{re.escape(p)}\.([A-Za-z_$][\w$]*)", body):
            if mm.group(1) not in SKIP:
                top.add(mm.group(1))
    return top, data


def cross_fn(region_txt: str, full: str) -> set[str]:
    """`renderPlan(b.data, ids)` → `function renderPlan(d, ...)` 里的 `d.<KEY>`。

    调用点**只在区域里找**、函数定义在**全文件**找。全文件找调用点会把无关
    函数的实参全算进来（实测 13 处调用点各被塞进 20 多个键）。
    """
    out: set[str] = set()
    for m in CROSS.finditer(region_txt):
        fn = m.group(1)
        d = re.search(FWDEF % re.escape(fn), full)
        if not d:
            continue
        brace = full.find("{", d.end())
        if brace < 0:
            continue
        body = _brace_body(full, brace)
        out.update(re.findall(rf"\b{re.escape(d.group(1))}\.([A-Za-z_$][\w$]*)", body))
    return out


def wrapper_reads() -> dict[str, tuple[set[str], set[str]]]:
    """共享封装函数体内的读键：`send()` 读 `b.ok` / `b.message`。

    §2.5.80 的 `WRAPPERS` 已经知道哪些函数是请求封装（`send` / `req` / `post`），
    这里复用同一份名单，不重写第二遍（§2.5.76）。
    """
    out: dict[str, tuple[set[str], set[str]]] = {}
    for f in JS:
        txt = f.read_text(encoding="utf-8")
        for name in C.WRAPPERS:
            d = re.search(rf"function\s+{re.escape(name)}\s*\(", txt)
            if not d:
                continue
            brace = txt.find("{", d.end())
            if brace < 0:
                continue
            out[f"{f.name}:{name}"] = reads_of(_brace_body(txt, brace))
    return out


def fe_side(calls, wrap: dict) -> list[dict]:
    out: list[dict] = []
    for c in calls:
        f = STATIC / c["file"]
        full = f.read_text(encoding="utf-8")
        lines = full.splitlines()
        s, e = region(lines, c["line"] - 1)
        txt = "\n".join(lines[s:e])
        top, data = reads_of(txt)
        data |= cross_fn(txt, full)
        # 经共享封装的调用点：自身区域读不到任何键，读发生在封装体内
        via = None
        if not top and not data and ("send(" in txt or "req(" in txt or "post(" in txt):
            for key, (wt, wd) in wrap.items():
                if key.split(":")[1] + "(" in txt:
                    via, top, data = key, set(wt), set(wd)
                    break
        out.append({**c, "top": top, "data": data, "via": via,
                    "region": (s + 1, e)})
    return out


# ══════════════════════════════════════════════════════════════════
# 比对
# ══════════════════════════════════════════════════════════════════
DEV_KEYS = {"data", "error", "hint", "message", "ok", "available", "note",
            "stage", "updated", "task_id"}


def compare(routes, trees, whys, reads_fe) -> dict:
    a1: list[str] = []
    a2: list[str] = []
    a3: list[str] = []
    b1: list[str] = []
    bound = 0
    with_calls = 0
    resolved = 0

    js_all = "\n".join(p.read_text(encoding="utf-8") for p in JS)

    # 路由 ←→ 调用点
    by_ep: dict[str, list[dict]] = {}
    for method, rule, ep in routes:
        rx = C.rule_re(rule)
        hits = [c for c in reads_fe
                if c["method"] == method and rx.match(c["url"])]
        if not hits:
            a3.append(f"{method} {rule}（{ep}）无前端调用点")
            continue
        with_calls += 1
        by_ep[ep] = hits
        tree = trees.get(ep) or {}
        top_got = set(tree.keys())
        data_got = tree.get("data")
        # 顶层一个键都没解出 ⇒ 这条路由整个跳过（连 A1 都没法判）。
        if not top_got:
            b1.append(f"{method} {rule}（{ep}）顶层与 data 全解不出"
                      f"   why={whys.get(ep)}")
            continue
        # `data` 值不是 dict ⇒ 子结构解不出（`None` 哨兵）或压根没这个键。
        # **只跳过 A2**，A1 照判——顶层解得出就是解得出，不该被下一层的
        # 未知连坐（首版把两者绑在一起判，`collect.api_cancel_task` 的
        # 顶层判据因此整条失效，C 段第四例把它抓了出来）。
        if isinstance(data_got, dict):
            resolved += 1
        else:
            why = "data 缺失" if "data" not in tree else "data 子结构解不出"
            b1.append(f"{method} {rule}（{ep}）{why}"
                      f"   why={whys.get(ep)}")
        for h in hits:
            bound += 1
            extra_t = h["top"] - top_got
            if extra_t:
                a1.append(f"{h['file']}:{h['line']} {method} {rule} "
                          f"读顶层 {sorted(extra_t)}，实现不返回")
            if not isinstance(data_got, dict):
                continue
            extra_d = h["data"] - set(data_got.keys())
            if extra_d:
                a2.append(f"{h['file']}:{h['line']} {method} {rule} "
                          f"读 data {sorted(extra_d)}，实现不返回")

    # B2：实现返回、前端从不读
    b2a: list[str] = []
    b2b: list[str] = []
    for ep, hits in sorted(by_ep.items()):
        tree = trees.get(ep) or {}
        if not tree:
            continue
        got = set(tree.keys())
        dg = tree.get("data")
        if isinstance(dg, dict):
            got |= set(dg.keys())
        read = set()
        for h in hits:
            read |= h["top"] | h["data"]
        never = got - read
        for k in sorted(never):
            if k in DEV_KEYS:
                continue
            (b2a if re.search(rf"\.{re.escape(k)}\b", js_all) else b2b).append(
                f"{ep} 返回 {k}，该接口的写调用点从不读")

    rep = {"a1": a1, "a2": a2, "a3": a3, "b1": b1,
           "b2a": b2a, "b2b": b2b,
           "bound": bound, "with_calls": with_calls, "resolved": resolved,
           "routes": len(routes), "calls": len(reads_fe),
           "via": {h["file"] + ":" + str(h["line"]): h["via"]
                   for h in reads_fe if h["via"]},
           "reds": {k: v for k, v in (("a1", a1), ("a2", a2), ("a3", a3))
                    if v},
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


def part_a(routes, trees, whys, reads_fe, rep) -> None:
    print("\n═══ A. 实测 ═══")
    print(f"  写路由 {len(routes)} 条"
          f"（POST {sum(1 for m, _, _ in routes if m == 'POST')}"
          f" + DELETE {sum(1 for m, _, _ in routes if m == 'DELETE')}）"
          f"，前端写调用点 {len(reads_fe)} 处，绑定 {rep['bound']} 对")
    print(f"  实现侧返回结构解出 {rep['resolved']} 条、解不出 {len(rep['b1'])} 条")
    for _m, _r, ep in routes:
        t = trees.get(ep) or {}
        d = t.get("data") if isinstance(t.get("data"), dict) else None
        tag = "" if d is not None else "  ⚠️ data 解不出"
        print(f"    {ep:32s} top={sorted(t)}")
        print(f"    {'':32s} data="
              f"{sorted(d) if d is not None else '—'}{tag}")
    print("  前端调用点读的键：")
    for h in reads_fe:
        via = f"   〔经 {h['via']}〕" if h["via"] else ""
        print(f"    {h['file']}:{h['line']:>4}  {h['method']:6} {h['url']:44}"
              f"  top={sorted(h['top']) or '∅'}  data={sorted(h['data']) or '∅'}{via}")
    wrap = rep["via"]
    print(f"  经共享封装的调用点 {len(wrap)} 处：{wrap}")


def part_b(rep) -> None:
    print("\n═══ B. 三档硬判据 ═══")
    ok(not rep["a1"], "A1 前端读的顶层键，实现都返回",
       "" if not rep["a1"] else str(rep["a1"][:3]))
    ok(not rep["a2"], "A2 前端读的 data 键，实现都返回",
       "" if not rep["a2"] else str(rep["a2"][:3]))
    tot = rep["resolved"] + len(rep["b1"])
    ok(tot == rep["with_calls"],
       f"A3 有调用点的路由 {rep['with_calls']}：解出 {rep['resolved']} + "
       f"B1 {len(rep['b1'])} = {tot}（解不出的没被静默跳过）",
       f"实际 {rep['resolved']}+{len(rep['b1'])}={tot}")
    print("\n═══ B'. 定量（不判红）═══")
    print(f"  B1 data 解不出的写路由（不进 A2，不算通过）：{len(rep['b1'])} 条")
    for s in rep["b1"]:
        print(f"    · {s}")
    print(f"  B2a 实现返回、写调用点不读，但前端别处出现过：{len(rep['b2a'])} 条")
    for s in rep["b2a"]:
        print(f"    · {s}")
    print(f"  B2b 实现返回、整个 static/*.js 一次都没出现（真死字段）："
          f"{len(rep['b2b'])} 条")
    for s in rep["b2b"]:
        print(f"    · {s}")


def _run_diff() -> dict:
    p = subprocess.run([S.PY, __file__, "--diff"], capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
    if not line:
        raise RuntimeError(f"--diff 无 JSON：{p.stdout[-400:]}{p.stderr[-400:]}")
    return json.loads(line[0][len("@@JSON@@"):])


def part_c() -> None:
    """注入式自证：断言**红的恰好是哪几档**，不是「见红就算过」（§2.5.68）。

    每一例都挑在**归因唯一**的锚点上：改的键只被一处调用点读、或只由一条
    路由返回，别的档不该跟着红。
    """
    print("\n═══ C. 注入式自证（红的恰好是哪几档）═══")
    base = _run_diff()
    ok(base["reds"] == {}, "基线（未注入）红集合为空",
       f"实际 {sorted(base['reds'])}")
    nb1 = len(base["b1"])

    cases = [
        ("前端把 `d.ready` 改名 `d.readiness`（实现不返回 → 永远 undefined）",
         STATIC / "review.js",
         "$('promoteRun').disabled = !d.ready;",
         "$('promoteRun').disabled = !d.readiness;",
         {"a2"}),
        ("前端把 `res.body.data.updated` 改成 `.data.update_count`",
         STATIC / "collect.js",
         "res.body.data.updated", "res.body.data.update_count",
         {"a2"}),
        ("**实现侧**把 `api_gate_raw` 的 `updated` 从 data 里删掉"
         "（反向也抓得到，不是只读前端）",
         WEB / "collect.py",
         '        "updated": cur.rowcount, "verdict": verdict,',
         '        "verdict": verdict,',
         {"a2"}),
        ("前端把顶层 `res.body.note` 改名 `note_text`"
         "（顶层键与 data 键分属两档，不该互相顶替）",
         STATIC / "collect.js",
         "notice('已请求取消。' + (res.body.note || ''));",
         "notice('已请求取消。' + (res.body.note_text || ''));",
         {"a1"}),
    ]
    for label, path, old, new, want in cases:
        with S._with_file(path, old, new):
            got = _run_diff()
            reds = set(got["reds"])
        ok(reds == want, f"{label} → 红集合恰好 {sorted(want) or '∅'}",
           f"实际 {sorted(reds)}")

    # B1 不是静默豁免：把一条可解路由的 data 换成解不出的调用
    with S._with_file(
            WEB / "collect.py",
            '    return jsonify({"ok": True, "data": {\n'
            '        "updated": cur.rowcount, "verdict": verdict,\n'
            '        "missing": len(ids) - cur.rowcount,\n'
            '    }})',
            '    return jsonify({"ok": True, "data": _gate_payload(ids, verdict)})'):
        got = _run_diff()
    ok(len(got["b1"]) == nb1 + 1,
       f"把一条路由的 data 换成解不出的调用 → B1 {nb1} → {len(got['b1'])}"
       f"（转进 B1 被显式列出，不静默通过）", f"实际 {len(got['b1'])}")
    ok(got["reds"] == {}, "  同一处转 B1 后不误报 A2（解不出不等于错了）",
       f"实际 {sorted(got['reds'])}")

    # 文档面：D 段也得有自己的失败路径
    dcases = [
        ("01 号文档的「写路由」数被改错",
         DOC, "| 写路由 | **13** |", "| 写路由 | **12** |",
         "文档「写路由」应为 13"),
        ("01 号文档的锚点被删",
         DOC, "前端读的键 ↔ 实现返回的键", "前端读的键与实现返回的键",
         "01 号文档缺锚点"),
    ]
    for label, path, old, new, want in dcases:
        with S._with_file(path, old, new):
            got = _run_diff()["docreds"]
        ok(any(want in g for g in got), f"{label} → D 段报错",
           f"实际 {got[:2] if got else '（D 段没报）'}")


def _doc_stat(rep) -> dict[str, int]:
    return {
        "写路由": rep["routes"],
        "前端写调用点": rep["calls"],
        "前端读而实现不返回": len(rep["a1"]) + len(rep["a2"]),
        "返回结构解不出的写路由": len(rep["b1"]),
        "真死字段": len(rep["b2b"]),
    }


def doc_checks(rep) -> list[str]:
    """D 段判据（**不打印**，让 `--diff` 的 stdout 仍只有一行 JSON）。"""
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


def main() -> int:
    if "--diff" in sys.argv:
        routes, trees, whys, reads_fe = collect_all()
        rep = compare(routes, trees, whys, reads_fe)
        rep["docreds"] = doc_checks(rep)
        print("@@JSON@@" + json.dumps(rep, ensure_ascii=False))
        return 1 if (rep["reds"] or rep["docreds"]) else 0

    routes, trees, whys, reads_fe = collect_all()
    rep = compare(routes, trees, whys, reads_fe)
    part_a(routes, trees, whys, reads_fe, rep)
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


def collect_all():
    """复用 §2.5.80 的写路由与调用点清单（同一份绑定，不重写第二遍）。"""
    routes, _reads, calls = C.collect_all()
    trees, whys = impl_side(routes)
    return routes, trees, whys, fe_side(calls, wrapper_reads())


if __name__ == "__main__":
    sys.exit(main())
