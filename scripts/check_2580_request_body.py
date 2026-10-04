#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.80 写操作的入参面：前端发的键 ↔ 实现读的键。

## 来路

`docs/web/09_端到端对齐核查.md` §10.5 三条「比不出来」的第三条是 **POST 接口**：

    它们写库，只读核查不打，如实列在「未比对」里。这批的错误只能靠人核

前半句是**能力**限制（只读核查打不了写库路由），后半句是**结论**（因此这批
一个判据都没有）。本节把"打不了"与"不能核"分开：**打不了 ≠ 不能核**——写库
接口的**入参面**完全可以静态核，因为契约的两端都在磁盘上：一边是调用方
（`static/*.js`），另一边是被调用方（`web/*.py`）。

## 为什么基准是前端而不是文档样例

`check_api_contract.py` 拿文档样例当基准，因为它核的是**响应**——样例把响应
画出来了。POST 不行：**文档样例把请求体与响应体画在同一个 ```json 块里**，
读者分不清哪个是入参。实测 `/api/review/items/<stage_id>` 的样例有 7 个键
（`accident_type` / `action` / `causes` / `code` / `edits` / `note` / `sort_order`），
而实现只读 3 个（`action` / `note` / `edits`）——其余是**响应**里的字段。
`03_暂存与人工审核.md` 的 `promote` 样例更极端（24 个键，请求与响应拼在一起，
§10.6 已记过这一笔）。

**真正的调用方是前端。** 它发的键是运行时真发出去的，不随文档漂移。故本节的
基准取 `static/*.js` 里实际构造的 body。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 前端**发**而实现**不读**的键 | 「发了没人接」——静默丢弃，用户以为生效了 | 红 |
| A2 | 每条**写路由**至少要有一个前端调用点 | 死接口（实现了没人调） | 红 |
| A3 | 每个**前端写调用点**须绑到一条真实路由 | 前端调不存在的 URL | 红 |
| B1 | body 解不出的调用点（不透明） | ——（必须量化，否则 A1 是"我没比"） | 定量 |
| B2 | 实现读而前端从不发的键 | 可选参数 / 服务端内部调用 | 定量 |

**A1 只对能解出 body 的调用点判定**；解不出的进 B1，**不当作通过**——否则
"不透明"会变成一条静默豁免（§2.5.69 起反复立的规矩）。

## 口径

写路由 = **POST + DELETE**（09 号 §10.5 写的是「POST 接口」，但那批写操作里
还有两条 DELETE，同一个病、同一个判据，不一起管就会漏）。只读核查**不打**
这些路由，全是静态文本分析——这是它敢碰写接口的原因。
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
from _common import main_db_path                          # noqa: E402
from _serve import point_at                               # noqa: E402

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
DOC3 = ROOT / "docs" / "web" / "03_暂存与人工审核.md"
WEBPY = sorted(WEB.glob("*.py"))
JS = sorted(STATIC.glob("*.js"))

WRITE_METHODS = {"POST", "DELETE"}

#: 前端请求封装函数：名 → (url 参数序号, method 参数序号, body 参数序号)
#: `send(url, method, body)` / `req(url, method, body)` / `post(url, body)`
WRAPPERS = {
    "send": (0, 1, 2),
    "req": (0, 1, 2),
    "post": (0, None, 1),      # post = function (u, b) { return req(u, 'POST', b); }
}

DOC_NEEDLES = ["前端发的键 ↔ 实现读的键"]


# ══════════════════════════════════════════════════════════════════
# 实现侧：写路由 → 读的键
# ══════════════════════════════════════════════════════════════════
def _funcs_of(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def _decorator_methods(dec: ast.expr) -> set[str]:
    """`@bp.post(...)` / `@bp.route(..., methods=[...])` / `@bp.delete(...)`。"""
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return set()
    name = dec.func.attr
    if name in ("post", "put", "delete", "patch"):
        return {name.upper()}
    if name == "route":
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                return {el.value.upper() for el in kw.value.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)}
    return set()


def _loop_consts(func: ast.FunctionDef) -> dict[str, set[str]]:
    """收 `for k in ("a","b")` / `for k in ("a","b") if ...` 的常量元组。

    实现里 `body.get(k) for k in ("since","until","limit","keywords")` 这种写法
    用逐键正则一条都收不到——本函数是它不误报「实现没读」的前提。
    """
    out: dict[str, set[str]] = {}
    for node in ast.walk(func):
        iters = []
        if isinstance(node, ast.comprehension):
            iters.append((node.target, node.iter))
        elif isinstance(node, ast.For):
            iters.append((node.target, node.iter))
        for target, it in iters:
            if isinstance(target, ast.Name) and isinstance(it, (ast.Tuple, ast.List)):
                vals = {e.value for e in it.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if vals:
                    out.setdefault(target.id, set()).update(vals)
    return out


def _collect_from(func: ast.FunctionDef, var: str,
                  funcs: dict[str, ast.FunctionDef], depth: int = 0) -> set[str]:
    """收函数体里从 `var` 上读的键，并递归进以 `var` 为实参的辅助函数。"""
    keys: set[str] = set()
    loops = _loop_consts(func)
    for node in ast.walk(func):
        # var.get(<常量>) / var.get(<循环变量>)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == var
                and node.func.attr == "get" and node.args):
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                keys.add(a.value)
            elif isinstance(a, ast.Name) and a.id in loops:
                keys |= loops[a.id]
        # var[<常量>]
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == var):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
        # helper(var) —— 递归进辅助函数（如 `_ids_arg(body)` 读 stage_ids）
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == var and depth < 3):
            h = funcs.get(node.func.id)
            if h is not None and h.args.args:
                keys |= _collect_from(h, h.args.args[0].arg, funcs, depth + 1)
    return keys


def _json_var(fn: ast.FunctionDef) -> str | None:
    """函数里存 `request.get_json()` 结果的变量名。

    **不能只判 `node.value` 是 Call**：三个文件一律写成
    `body = request.get_json(silent=True) or {}`——`or {}` 让它成了 BoolOp，
    按 Call 判会一条都解不出（首版就是这么写的，A1 于是把前端发的每一个键
    都报成「实现不读」，红的是一片而不是一格）。故在右侧整个表达式里找。
    """
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        for sub in ast.walk(node.value):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get_json"):
                return node.targets[0].id
    return None


def impl_side() -> tuple[dict[str, set[str]], dict[str, str]]:
    """→ ({`模块.函数`: 读键}, {`模块.函数`: 文件:行})

    key 必须带模块名：`api_create_task` 在 `collect.py` 与 `extract.py` **各有一个**
    （蓝图注册名与文件名一致，url_map 的 endpoint 就是 `collect.api_create_task`）。
    首版只用函数名做 key，extract 的那份把 collect 的**盖掉了**——A1 于是报
    「collect.js 发的 source_keys/since/until/limit/keywords 实现不读」，其实读的
    是另一个同名函数（§2.5.66「同名不同物」在解析器里的复现）。
    """
    reads: dict[str, set[str]] = {}
    where: dict[str, str] = {}
    for f in WEBPY:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:                     # pragma: no cover
            continue
        funcs = _funcs_of(tree)
        for fn in funcs.values():
            methods: set[str] = set()
            for dec in fn.decorator_list:
                methods |= _decorator_methods(dec)
            if not (methods & WRITE_METHODS):
                continue
            var = _json_var(fn)
            reads[f"{f.stem}.{fn.name}"] = _collect_from(fn, var, funcs) if var else set()
            where[f"{f.stem}.{fn.name}"] = f"{f.name}:{fn.lineno}"
    return reads, where


def write_routes(app) -> list[tuple[str, str, str]]:
    """→ [(方法, 规则, endpoint 全名)]，只收写方法。"""
    out = []
    for rule in app.url_map.iter_rules():
        ms = sorted((rule.methods - {"HEAD", "OPTIONS"}) & WRITE_METHODS)
        for m in ms:
            out.append((m, str(rule), rule.endpoint))
    return sorted(out, key=lambda t: (t[1], t[0]))


def rule_re(rule: str) -> re.Pattern:
    """后端规则 → 正则：`<x>` 段换成 `[^/]+`，其余按字面。"""
    parts = re.split(r"<[^>]*>", rule)
    return re.compile("^" + r"[^/]+".join(re.escape(p) for p in parts) + "$")


# ══════════════════════════════════════════════════════════════════
# 前端侧：写调用点 → 发的键
# ══════════════════════════════════════════════════════════════════
def _js_balanced(txt: str, start: int) -> str:
    """从 `start`（指向一个 `(`）起做括号配对，返回括号内的文本。"""
    depth = 0
    i = start
    n = len(txt)
    while i < n:
        c = txt[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n and txt[i] != q:
                i += 2 if txt[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return txt[start + 1:i]
        i += 1
    return txt[start + 1:]


def _split_args(src: str, sep: str = ",") -> list[str]:
    """顶层分隔符切分（默认逗号；URL 拼接按 `+` 切）。"""
    out, depth, buf, i, n = [], 0, [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            q = c
            buf.append(c)
            i += 1
            while i < n and src[i] != q:
                buf.append(src[i])
                i += 2 if src[i] == "\\" else 1
            if i < n:
                buf.append(src[i])
        elif c in "([{":
            depth += 1
            buf.append(c)
        elif c in ")]}":
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if buf:
        out.append("".join(buf))
    return [s.strip() for s in out]


#: 裸标识符 = 封装函数内部的转发（`return req(u, 'POST', b)`）或变量传递
BARE = re.compile(r"[A-Za-z_$][\w$]*")
STR_LIT = re.compile(r"['\"]([^'\"]*)['\"]")


def url_template(src: str) -> str | None:
    """前端 URL 表达式 → 模板。字符串常量原样、其余（拼接进来的变量）换成 `*`。

    `'/api/review/items/' + encodeURIComponent(d.stage_id)` → `/api/review/items/*`

    裸标识符返回 None——它是 `var post = function (u, b) { return req(u, 'POST', b); }`
    这类**封装内部的转发**，不是业务调用点（首版把 `review.js:88` 那句当成了第
    15 个调用点，还报了「URL 解不出」）。调用处据此跳过并登记。
    """
    src = src.strip()
    if BARE.fullmatch(src):
        return None
    parts = [p for p in _split_args(src, "+") if p]
    if not parts:
        return None
    out = ""
    for p in parts:
        m = STR_LIT.fullmatch(p.strip())
        out += m.group(1) if m else "*"
    return out


def _obj_keys(src: str) -> set[str] | None:
    """对象字面量的**顶层**键；不是字面量则 None。"""
    s = src.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    inner = s[1:-1]
    return set(re.findall(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*:", "," + inner))


def _var_keys(txt: str, name: str) -> tuple[set[str], str]:
    """文件级近似：变量被赋的对象字面量键 + 该变量上的 `.k =` 赋值。

    返回 (键集合, 说明)。解不出时键为空、说明里写明原因。
    """
    keys: set[str] = set()
    why = []
    m = re.search(r"\bvar\s+" + re.escape(name) + r"\s*=\s*\{", txt)
    if m:
        seg = _js_balanced(txt, m.end() - 1)
        k = _obj_keys("{" + seg + "}")
        if k:
            keys |= k
            why.append(f"字面量 {len(k)} 键")
    asg = set(re.findall(r"\b" + re.escape(name) + r"\.([A-Za-z_$][\w$]*)\s*=", txt))
    if asg:
        keys |= asg
        why.append(f"逐字段赋值 {len(asg)} 键")
    # 该变量是某个函数的形参 —— 收所有调用点里同一位置的对象字面量
    for m in re.finditer(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", txt):
        params = [p.strip() for p in m.group(2).split(",")]
        if name not in params:
            continue
        idx = params.index(name)
        for call in re.finditer(r"\b" + re.escape(m.group(1)) + r"\s*\(", txt):
            args = _split_args(_js_balanced(txt, call.end() - 1))
            if idx < len(args):
                k = _obj_keys(args[idx])
                if k:
                    keys |= k
                    why.append(f"{m.group(1)}() 实参 {len(k)} 键")
    return keys, "／".join(why) if why else "解不出"


#: 跳过的调用点（封装内部转发）——不静默丢，A 段打印出来
SKIPPED: list[str] = []


def js_calls() -> list[dict]:
    """收全部写调用点：字面量 fetch / 封装函数调用。"""
    del SKIPPED[:]
    out: list[dict] = []
    for f in JS:
        txt = f.read_text(encoding="utf-8")
        lines = txt.splitlines()

        def lineno(pos: int) -> int:
            return txt[:pos].count("\n") + 1

        def body_of(expr: str | None) -> tuple[set[str], str]:
            if expr is None or not expr.strip():
                return set(), "无 body"
            k = _obj_keys(expr)
            if k is not None:
                return k, "字面量"
            if re.fullmatch(r"[A-Za-z_$][\w$]*", expr.strip()):
                return _var_keys(txt, expr.strip())
            return set(), "解不出（不是字面量也不是单变量）"

        # ① 字面量 fetch(url, {...})
        for m in re.finditer(r"\bfetch\s*\(", txt):
            args = _split_args(_js_balanced(txt, m.end() - 1))
            if len(args) < 2:
                continue
            mm = re.search(r"method\s*:\s*['\"](\w+)['\"]", args[1], re.I)
            if not mm or mm.group(1).upper() not in WRITE_METHODS:
                continue
            ut = url_template(args[0])
            if ut is None:
                SKIPPED.append(f"{f.name}:{lineno(m.start())} 封装内部转发（URL 是裸标识符）")
                continue
            bm = re.search(r"body\s*:\s*JSON\.stringify\s*\(", args[1])
            expr = None
            if bm:
                expr = _js_balanced(args[1], bm.end() - 1)
            keys, how = body_of(expr)
            out.append({"file": f.name, "line": lineno(m.start()),
                        "url": ut,
                        "method": mm.group(1).upper(), "keys": keys,
                        "how": how, "raw": "fetch"})

        # ② 封装函数 send/req/post
        for name, (_u, m_idx, b_idx) in WRAPPERS.items():
            for m in re.finditer(r"(?<![\w$.])" + name + r"\s*\(", txt):
                # 跳过定义本身（`function send(` / `var post = function (`）
                pre = txt[max(0, m.start() - 20):m.start()]
                if re.search(r"\bfunction\s+$", pre):
                    continue
                args = _split_args(_js_balanced(txt, m.end() - 1))
                if not args or (m_idx is not None and m_idx >= len(args)):
                    continue
                method = "POST"
                if m_idx is not None:
                    mm = re.search(r"['\"](\w+)['\"]", args[m_idx])
                    if not mm:
                        continue
                    method = mm.group(1).upper()
                if method not in WRITE_METHODS:
                    continue
                expr = args[b_idx] if b_idx is not None and b_idx < len(args) else None
                keys, how = body_of(expr)
                ut = url_template(args[_u])
                if ut is None:
                    SKIPPED.append(f"{f.name}:{lineno(m.start())} "
                                   f"{name}() 封装内部转发（URL 是裸标识符）")
                    continue
                out.append({"file": f.name, "line": lineno(m.start()),
                            "url": ut,
                            "method": method, "keys": keys,
                            "how": f"{how}（{name}）", "raw": name})
    return sorted(out, key=lambda d: (d["file"], d["line"]))


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════
def compare(routes, reads, calls) -> dict:
    a1: list[str] = []      # 前端发、实现不读
    a2: list[str] = []      # 写路由没有调用点
    a3: list[str] = []      # 调用点绑不到路由
    b1: list[str] = []      # body 解不出
    b2: list[str] = []      # 实现读、前端不发
    bound = 0
    with_calls = 0

    # 路由 ←→ 调用点
    for method, rule, ep in routes:
        rx = rule_re(rule)
        hits = [c for c in calls if c["method"] == method and rx.match(c["url"])]
        if not hits:
            a2.append(f"{method} {rule}（{ep}）无前端调用点")
            continue
        with_calls += 1
        got = reads.get(ep)
        if got is None:
            a2.append(f"{method} {rule}（{ep}）实现侧没解析到函数（脚本解析问题）")
            continue
        for h in hits:
            bound += 1
            if not h["how"].startswith(("无 body", "解不出")):
                extra = h["keys"] - got
                if extra:
                    a1.append(f"{h['file']}:{h['line']} {method} {rule} "
                              f"发了 {sorted(extra)}，实现不读")

    for c in calls:
        ok_url = any(c["method"] == mth and rule_re(rule).match(c["url"])
                     for mth, rule, _ in routes)
        if not ok_url:
            a3.append(f"{c['file']}:{c['line']} {c['method']} {c['url']} 无对应路由")
        elif c["how"].startswith(("无 body", "解不出")):
            b1.append(f"{c['file']}:{c['line']} {c['method']} {c['url']}（{c['how']}）")

    sent: dict[str, set[str]] = {}
    for c in calls:
        if c["how"].startswith(("无 body", "解不出")):
            continue
        for mth, rule, ep in routes:
            if c["method"] == mth and rule_re(rule).match(c["url"]):
                sent.setdefault(ep, set()).update(c["keys"])
    for ep, got in sorted(reads.items()):
        if ep not in sent or not sent[ep]:
            continue
        never = got - sent[ep]
        if never:
            b2.append(f"{ep} 读 {sorted(never)}，前端从不发")

    rep = {"a1": a1, "a2": a2, "a3": a3, "b1": b1, "b2": b2,
           "bound": bound, "with_calls": with_calls,
           "routes": len(routes), "calls": len(calls),
           "skipped": list(SKIPPED),
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


def part_a(routes, reads, calls, rep) -> None:
    print("\n═══ A. 实测 ═══")
    print(f"  写路由 {len(routes)} 条"
          f"（POST {sum(1 for m, _, _ in routes if m == 'POST')}"
          f" + DELETE {sum(1 for m, _, _ in routes if m == 'DELETE')}）"
          f"，前端写调用点 {len(calls)} 处，绑定 {rep['bound']} 对")
    opaque = [c for c in calls
              if c["how"].startswith(("无 body", "解不出"))]
    print(f"  body 解不出的调用点 {len(opaque)} 处"
          f"（{len(opaque) / max(len(calls), 1):.1%}）——它们**不进 A1**，"
          f"但要列出来：{[(c['file'], c['line']) for c in opaque]}")
    for c in calls:
        if c["how"].startswith(("无 body", "解不出")):
            continue
        print(f"    {c['file']}:{c['line']:>4}  {c['method']:6} {c['url']:44}"
              f"  发 {sorted(c['keys']) or '∅'}   〔{c['how']}〕")
    if rep["skipped"]:
        print(f"  跳过 {len(rep['skipped'])} 处（封装内部转发，不是业务调用点）：")
        for s in rep["skipped"]:
            print(f"    · {s}")


def part_b(rep) -> None:
    print("\n═══ B. 三档硬判据 ═══")
    ok(not rep["a1"], "A1 前端发的键，实现都读",
       "" if not rep["a1"] else str(rep["a1"][:3]))
    ok(not rep["a2"], "A2 每条写路由都有前端调用点",
       "" if not rep["a2"] else str(rep["a2"][:3]))
    ok(not rep["a3"], "A3 每个前端写调用点都绑到真实路由",
       "" if not rep["a3"] else str(rep["a3"][:3]))
    print("\n═══ B'. 两档定量（不判红）═══")
    print(f"  B1 body 解不出（不进 A1，不算通过）：{len(rep['b1'])} 处")
    for s in rep["b1"]:
        print(f"    · {s}")
    print(f"  B2 实现读、前端从不发（可选参数）：{len(rep['b2'])} 条")
    for s in rep["b2"]:
        print(f"    · {s}")
    tot = rep["with_calls"] + len(rep["a2"])
    ok(tot == rep["routes"],
       f"闭合：有调用点的路由 {rep['with_calls']} + 无调用点 {len(rep['a2'])} = "
       f"{rep['routes']} 条写路由", f"实际 {rep['with_calls']}+{len(rep['a2'])}={tot}")


def _run_diff() -> dict:
    p = subprocess.run([S.PY, __file__, "--diff"], capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
    if not line:
        raise RuntimeError(f"--diff 无 JSON：{p.stdout[-400:]}{p.stderr[-400:]}")
    return json.loads(line[0][len("@@JSON@@"):])


def part_c() -> None:
    """注入式自证：断言**红的恰好是哪几档**，不是「见红就算过」。

    §2.5.68 立的规矩。下面每一例都挑在**归因唯一**的锚点上：
    - A3 那例改的是 `promote` 两条调用点里的一条，另一条还在 ⇒ A2 不该跟着红；
    - A2 那例把 URL 改到**另一条已存在**的路由 ⇒ 调用点仍绑真路由，A3 不该红；
    - B1 那例给「无 body」的调用点补上 body ⇒ 证明 B1 不是静默豁免。
    """
    print("\n═══ C. 注入式自证（红的恰好是哪几档）═══")
    base = _run_diff()
    base_b1 = len(base["b1"])
    ok(base["reds"] == {}, "基线（未注入）红集合为空",
       f"实际 {sorted(base['reds'])}")

    cases = [
        ("前端把 `verdict` 改名 `gate_verdict`（后端读不到 → 静默丢弃）",
         STATIC / "collect.js",
         "body: JSON.stringify({ raw_ids: ids, verdict: verdict, note: note })",
         "body: JSON.stringify({ raw_ids: ids, gate_verdict: verdict, note: note })",
         {"a1"}),
        ("实现多读一个前端从不发的键（可选参数，应落 B2 而非 A1）",
         WEB / "collect.py",
         '    verdict = (body.get("verdict") or "").strip()',
         '    verdict = (body.get("verdict") or "").strip()\n'
         '    _ = body.get("legacy_verdict")',
         set()),
        ("前端一条 `promote` 调用点的 URL 指向不存在的路由（另一条仍在）",
         STATIC / "review.js",
         "post('/api/review/promote', { stage_ids: ids, dry_run: true })",
         "post('/api/review/promote-typo', { stage_ids: ids, dry_run: true })",
         {"a3"}),
        ("前端把 `/api/collect/tasks` 的调用点改成 GET（不再是写调用点）"
         "→ 该路由没人调了",
         STATIC / "collect.js",
         "fetch('/api/collect/tasks', {\n      method: 'POST',",
         "fetch('/api/collect/tasks', {\n      method: 'GET',",
         {"a2"}),
        ("给「无 body」的取消调用点补上 body（实现不读该键）"
         "——B1 不是静默豁免",
         STATIC / "collect.js",
         "fetch('/api/collect/tasks/' + t.task_id + '/cancel', { method: 'POST' })",
         "fetch('/api/collect/tasks/' + t.task_id + '/cancel', "
         "{ method: 'POST', body: JSON.stringify({ reason: 'x' }) })",
         {"a1"}),
    ]
    for label, path, old, new, want in cases:
        with S._with_file(path, old, new):
            got = _run_diff()
            reds = set(got["reds"])
            nb1 = len(got["b1"])
        ok(reds == want, f"{label} → 红集合恰好 {sorted(want) or '∅'}",
           f"实际 {sorted(reds)}")
        if "取消调用点" in label:
            ok(nb1 == base_b1 - 1,
               f"  同一处由 B1 转入 A1：B1 {base_b1} → {nb1}"
               f"（不是两处都报，也不是都没报）")

    # 文档面：D 段也得有自己的失败路径（只测源码面的话，文档判据改坏了不会有人发现）
    dcases = [
        ("01 号文档的「写路由」数被改错",
         DOC, "| 写路由 | **13** |", "| 写路由 | **12** |",
         "文档「写路由」应为 13"),
        ("01 号文档的锚点被删",
         DOC, "前端发的键 ↔ 实现读的键", "前端发的键与实现读的键",
         "01 号文档缺锚点"),
    ]
    for label, path, old, new, want in dcases:
        with S._with_file(path, old, new):
            got = _run_diff()["docreds"]
        hit = any(want in g for g in got)
        ok(hit, f"{label} → D 段报错",
           f"实际 {got[:2] if got else '（D 段没报）'}")


def _doc_stat(rep) -> dict[str, int]:
    return {
        "写路由": rep["routes"],
        "前端写调用点": rep["calls"],
        "前端发的键实现不读": len(rep["a1"]),
        "body 解不出的调用点": len(rep["b1"]),
    }


def doc_checks(rep) -> list[str]:
    """D 段判据（**不打印**）：part_d 打印它，`--diff` 把它带进 JSON。

    不打印是为了让 `--diff` 的输出仍然只有一行 JSON——文档面的失败路径要靠
    `_run_diff()` 读回来，而 `_run_diff()` 解析的就是 stdout。
    """
    out: list[str] = []
    txt = DOC.read_text(encoding="utf-8")
    for needle in DOC_NEEDLES:
        if needle not in txt:
            out.append(f"01 号文档缺锚点「{needle}」")
    for label, n in _doc_stat(rep).items():
        mm = S.re.search(r"\|\s*" + S.re.escape(label)
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
        ok(not any(label in b for b in bad), f"文档表里「{label}」= {n}",
           "" if not any(label in b for b in bad) else "（见失败清单）")
    for needle in DOC_NEEDLES:
        ok(needle in DOC.read_text(encoding="utf-8"),
           f"01 号文档里有锚点「{needle[:22]}」")


def main() -> int:
    if "--diff" in sys.argv:
        routes, reads, calls = collect_all()
        rep = compare(routes, reads, calls)
        rep["docreds"] = doc_checks(rep)
        print("@@JSON@@" + json.dumps(rep, ensure_ascii=False))
        return 1 if (rep["reds"] or rep["docreds"]) else 0

    routes, reads, calls = collect_all()
    rep = compare(routes, reads, calls)
    part_a(routes, reads, calls, rep)
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
    point_at(str(main_db_path()), str(S.staging_path()))
    from construction_agent.web.factory import create_app

    app = create_app()
    reads, _where = impl_side()
    return write_routes(app), reads, js_calls()


if __name__ == "__main__":
    sys.exit(main())
