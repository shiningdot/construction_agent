#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.82 GET 请求参数面：前端拼的 query 键 ↔ 实现读的键。

补 §2.5.80 只管写方法路由（POST + DELETE）留下的那一半：**读方法的入参面**。
§2.5.80 的口径写死了 `WRITE_METHODS = {POST, DELETE}`，GET 的 query 参数
**一条判据都没有过**。而 GET 的 query 与 POST 的 body 是同一个病的两种形态：
前端拼了 `?type=single`、后端读的是 `text_type`，结果是**筛选静默失效**——
页面照常渲染、数字照常返回，只是筛选条件被丢掉（`00` §4.1 的口径：列表接口
对未识别的省份键就是**静默忽略**，`accidents_query.py:260` 那段注释写着这件事）。

它与 §2.5.80 的差别只在**解析难度**上，不在判据的形态上：

  写方法：`fetch(url, {method:'POST', body: JSON.stringify({...})})` —— body 是
          一个对象字面量，键就在眼前。
  读方法：`fetchJson('/api/accidents?' + toQuery())` —— 键在**另一个函数体**里，
          还可能串到变量、数组、`MULTI_KEYS` 常量，甚至 `forEach` 的动态形参。

故本节的工作量几乎全在「跟到键」这一步：字面量 → 函数体 → 变量右值 → 数组
`push`/`join` → 常量数组，五级跳转，每一级都要**收窄接收者**（§2.5.66 的规矩：
裸判据在混合语料里制造假阳性）。

不发任何请求。退出码 0 = A 段无硬缺口。
"""

from __future__ import annotations

import ast
import json
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_2578_response_shape as S      # noqa: E402  （`_with_file` 注入 / point_at）
import check_2580_request_body as C        # noqa: E402  （URL 解析与 AST 工具）
import check_2581_response_body as R       # noqa: E402  （模块查找与 import 别名）

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"

# 锚点句用**本节独有**的整行标题——只写键名对（`前端读的键 ↔ 实现返回的键`）时，
# 一旦别节用了同族措辞，这条判据在**本节被删掉**的情况下仍会为真（§2.5.81 / §2.5.83
# 已经撞过一次，见 `check_2581_response_body.py` 的同一处注释）。
DOC_NEEDLES = ["### 2.5.82 展示层（续）：GET 请求参数面——**前端拼的 query 键 ↔ 实现读的键**"]

#: 参数读取器：它们的 **定义体** 里有一处 `source.get(key)` 是机械转发，
#: 不是业务读取点——`key` 是形参。解析时必须**跳过包装函数自身的定义体**，
#: 只取**调用点**传进去的那一个字面量（否则每个路由都会多出 `key` 这个键）。
WRAPPERS = {"_single", "_multi", "_int", "_bool3"}

#: `request.args.get("k")` / `args.getlist("k")` 这类直读。
READERS = {"get", "getlist"}

#: 前端带 query 的调用形式：函数名 → URL 实参序号。
#: `fetchJson(u)` / `jsonGet(u)` / `get(u)` 都是单参；`fetch(u, opts)` 是两参。
GET_CALLERS = {"fetchJson": 0, "jsonGet": 0, "get": 0}

LIT = re.compile(r"[?&]([A-Za-z_][\w]*)=(?!=)")
#: 字符串常量内、**以 `=` 收尾**的键：`'/api/causes/cooccurrence' + tail + '&' + 'order='
#: + state.order`。这种拼接式写法里 `order` 前面没有 `?` 也没有 `&`（分隔符是另
#: 一段拼进来的），`LIT` 整条看不见——`causes.js:617` 的 `order` 就是这么漏的。
#: `'&top=15'` 一类不以 `=` 收尾，仍由 `LIT` 收，两条不重叠。
QSLIT = re.compile(r"['\"][?&]?([A-Za-z_]\w*)=['\"]")
SETKEY = re.compile(r"\.(?:set|append)\(\s*'([A-Za-z_][\w]*)'")
#: `p.push('size=' + $('f_size').value)` —— 引号**不要求收尾**（收尾是首版多余的
#: 约束，`review.js` 六处 query 键全被它漏掉，连带 `/api/review/queue` 被误判成
#: 「实现读了、前端从不发」）。`p.push(k + '=')` 的动态形式另由 PUSHV 收。
PUSHKEY = re.compile(r"\.push\(\s*'([A-Za-z_][\w]*)=['\"]?")
PUSHV = re.compile(r"\.push\(\s*([A-Za-z_$][\w$]*)\s*\+\s*'='")
ARR = re.compile(r"const\s+MULTI_KEYS\s*=\s*\[([^\]]*)\]", re.S)
FNDEF = re.compile(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
VAR = re.compile(r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=")
KW = {"var", "let", "const", "return", "if", "else", "for", "function", "new",
      "typeof", "encodeURIComponent", "parseInt", "String", "Number"}
MAXJ = 4        # 跟跳深度上限


# ══════════════════════════════════════════════════════════════════
# 后端侧：路由 → 它读了哪些 query 键
# ══════════════════════════════════════════════════════════════════
SEARCH = [WEB, ROOT / "construction_agent" / "review", ROOT / "construction_agent"]
_MCACHE: dict[str, tuple] = {}


def _module(modname: str):
    """模块名 → (函数表, 类方法表, import 别名表)。

    函数表与别名表**直接取 §2.5.81 的 `_module`**（§2.5.76 的规矩：同一个源同一
    字段不写第二遍解析）。本节只补它没有的**类方法表**——`ListParams.from_request()`
    这类经类方法转发的读取点要靠它才跟得进去。
    """
    if modname in _MCACHE:
        return _MCACHE[modname]
    funcs, aliases = R._module(modname)
    methods: dict[str, ast.FunctionDef] = {}
    p = R._find_mod(modname)
    if p is not None:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                for b in n.body:
                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[f"{n.name}.{b.name}"] = b
    _MCACHE[modname] = (funcs, methods, aliases)
    return _MCACHE[modname]


def _key_of_arg(node, idx: int) -> str | None:
    """取第 idx 个实参的字符串字面量。"""
    if len(node.args) <= idx:
        return None
    a = node.args[idx]
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value
    return None


def _mentions_args(node: ast.Call) -> bool:
    """调用的实参里有没有出现 `args` / `values`（说明它接的是请求参数）。"""
    for a in node.args:
        src = ast.unparse(a)
        if "args" in src or "values" in src:
            return True
    return False


def _loop_key_literals(node) -> set[str]:
    """表驱动筛选：`for col, allowed, key in ((…, …, "review_status"), …): args.get(key)`。

    键名**不在 `.get()` 的参数位上**，而在循环元组的同一个下标上。§2.5.82 实测：
    `extract.py:372` 那一组把 `review_status`／`text_type`／`relevance_verdict`
    三个键整组漏收，其中两个前端确实发了 → 被误报成「发了而实现不读」。
    """
    out: set[str] = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.For):
            continue
        t = n.target
        names = ([e.id for e in t.elts if isinstance(e, ast.Name)]
                 if isinstance(t, (ast.Tuple, ast.List)) else [getattr(t, "id", None)])
        # 循环体里哪些变量被当成了参数键（`.get(x)` / `.getlist(x)`）
        used: set[str] = set()
        for c in ast.walk(n):
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr in READERS and c.args
                    and "args" in ast.unparse(c.func.value)
                    and isinstance(c.args[0], ast.Name)):
                used.add(c.args[0].id)
        if not used or not isinstance(n.iter, (ast.Tuple, ast.List)):
            continue
        # 形态一：`for col in ("source_key", "task_id", "status")` —— 单目标，
        # 元组的每个元素**本身就是**键名（`collect.py:298`）。上一版只认「元组的
        # 元组」那种表驱动写法，这里整组漏收，前端发的 `source_key`/`status`
        # 反被报成「发了而实现不读」（a1 两条）。
        if isinstance(t, ast.Name) and t.id in used:
            for elt in n.iter.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.add(elt.value)
            continue
        # 形态二：`for col, allowed, key in ((…, …, "review_status"), …)` —— 键在
        # 每个元组的**同一下标**上（`extract.py:372`）。
        for elt in n.iter.elts:
            if not isinstance(elt, (ast.Tuple, ast.List)):
                continue
            for i, nm in enumerate(names):
                if nm in used and i < len(elt.elts):
                    v = elt.elts[i]
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        out.add(v.value)
    return out


def _collect(node, modname: str, out: set[str], seen: set[str], depth: int) -> None:
    """在一段 AST 里收参数读取点，必要时跟一层被调函数。"""
    if depth > MAXJ:
        return
    funcs, methods, aliases = _module(modname)
    out |= _loop_key_literals(node)

    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            name = f.id
            # ① `_single(source, "q")` 这类包装
            if name in WRAPPERS:
                k = _key_of_arg(n, 1)
                if k:
                    out.add(k)
                continue
            # ② 同模块函数 → 跟进去。**不看实参**：Flask 的 `request` 是上下文
            #    全局，`_page_args()` 这种**无参**辅助函数照样读 `request.args`
            #    （`review.py:81` / `extract.py:53` 各一份）。首版按「实参里有没有
            #    `args`」放行，于是这两个路由的 `page`／`size` 全漏，前端明明发了
            #    却被报成「发了而实现不读」（a1 六条假警报）。
            #    代价：通用工具函数若读参数也会被算进调用方，见判据边界。
            if name in funcs:
                tag = f"{modname}.{name}"
                if tag not in seen:
                    seen.add(tag)
                    _collect(funcs[name], modname, out, seen, depth + 1)
            continue

        if not isinstance(f, ast.Attribute):
            continue
        recv = ast.unparse(f.value)
        # ③ `request.args.get("k")` / `.getlist("k")`
        if f.attr in READERS and ("args" in recv or "values" in recv):
            k = _key_of_arg(n, 0)
            if k:
                out.add(k)
            continue
        # ④ `ListParams.from_request(request.args)` —— 类方法。类名可能是**跨模块
        #    别名**（`from ... import ListParams`），本模块的 methods 里查不到，
        #    要按别名表找到真正定义它的那个模块（§2.5.66：判据要认准接收者）。
        if isinstance(f.value, ast.Name):
            tag = f"{f.value.id}.{f.attr}"
            owner = modname if tag in methods else aliases.get(f.value.id, "")
            fn = _module(owner)[1].get(tag) if owner else None
            if fn is not None and _mentions_args(n):
                full = f"{owner}.{tag}"
                if full not in seen:
                    seen.add(full)
                    _collect(fn, owner, out, seen, depth + 1)
            continue
        # ⑤ 跨模块调用。两种形态：
        #    `accidents_query.list_params(args)`   → 模块级函数
        #    `accidents_query.ListParams.from_request(args)` → `模块.类.方法`
        #    首版只认前者，于是 `ListParams.from_request` 这个**唯一的读参数入口**
        #    整条跟不进去——`api.py` 四个路由的键集全空，连带被误报成
        #    「前端调用点绑不到读参数的路由」（a2）。
        head, _, cls = recv.rpartition(".")
        if head and cls[:1].isupper():
            real = aliases.get(head, head)
            fn = _module(real)[1].get(f"{cls}.{f.attr}")
        else:
            real = aliases.get(recv, recv)
            fn = _module(real)[0].get(f.attr)
        if fn is not None and _mentions_args(n):
            tag = f"{real}.{recv}.{f.attr}"
            if tag not in seen:
                seen.add(tag)
                _collect(fn, real, out, seen, depth + 1)


def impl_keys(ep: str) -> tuple[set[str], str]:
    """endpoint `模块.函数` → (它读的 query 键, 备注)。"""
    if "." not in ep:
        return set(), "endpoint 无模块前缀"
    modname, fname = ep.split(".", 1)
    funcs, methods, _al = _module(modname)
    if fname in funcs:
        fn = funcs[fname]
    elif fname in methods:
        fn = methods[fname]
    else:
        return set(), f"{ep} 找不到函数"
    out: set[str] = set()
    _collect(fn, modname, out, {f"{modname}.{fname}"}, 0)
    return out, ""


def get_routes(app) -> list[tuple[str, str]]:
    """→ [(规则, endpoint)]，只收 **读方法**。"""
    out = []
    for rule in app.url_map.iter_rules():
        ms = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        if "GET" in ms:
            out.append((str(rule), rule.endpoint))
    return sorted(out)


# ══════════════════════════════════════════════════════════════════
# 前端侧：GET 调用点 → 它拼了哪些 query 键
# ══════════════════════════════════════════════════════════════════
def _brace_body(txt: str, i: int) -> str:
    """从 `{`（下标 i）起配对，返回花括号内文本。"""
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


def fn_body(txt: str, name: str) -> str:
    m = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", txt)
    return _brace_body(txt, m.end() - 1) if m else ""


def enclosing_fn(txt: str, pos: int) -> str:
    best, best_at = "", -1
    for m in FNDEF.finditer(txt):
        b = _brace_body(txt, m.end() - 1)
        if m.end() <= pos <= m.end() + len(b) and m.start() > best_at:
            best, best_at = b, m.start()
    return best


def enclosing_fn_name(txt: str, pos: int) -> str:
    """`pos` 落在哪个 function 体内（最内层）→ 函数名。

    与 `enclosing_fn` 同一套扫描，只是把名字也带出来——`fe_calls` 要用它
    认「这个 `fetch` 是不是落在请求包装函数体内」。
    """
    best, best_at = "", -1
    for m in FNDEF.finditer(txt):
        b = _brace_body(txt, m.end() - 1)
        if m.end() <= pos <= m.end() + len(b) and m.start() > best_at:
            best, best_at = m.group(1), m.start()
    return best


def _arr_keys(txt: str) -> set[str]:
    out: set[str] = set()
    for m in ARR.finditer(txt):
        out |= set(re.findall(r"'([A-Za-z_][\w]*)'", m.group(1)))
    return out


def follow(txt: str, name: str, scope: str, depth: int = 0) -> set[str]:
    """跟一层：函数体 → 变量右值 → 数组 push/join → MULTI_KEYS 常量。

    `scope` 是**调用点所在的那个函数体**——`p.push('k=')` 这类要在作用域里找，
    拿全文件找会把别的函数的键也算进来（首版就是这么把 `causes.js` 的
    `/api/causes/framework` 报出 5 个键的）。
    """
    if depth > MAXJ:
        return set()
    b = fn_body(txt, name)
    if b:
        return keys_in(txt, b)
    # `p.join('&')` 形态：p 是局部数组，populate 在同一函数体里
    if re.search(rf"\b{re.escape(name)}\.join\s*\(", scope or ""):
        return keys_in(txt, scope)
    # 变量：只在**调用点所在的那个函数体**里找赋值右值。
    # 全文件找会把形参串到别的函数去——`extract.js:93` 的 `fetch(url, opts)`
    # 在 `jsonReq(url, method, body)` 体内，`url` 是形参，却匹配到了
    # `loadResults` 里的 `var url = '/api/extract/results?…'`，于是包装函数内部
    # 的分发点被当成业务调用点，凭空报出 `k`/`page` 两个「发了而实现不读」。
    m = re.search(rf"(?:var|let|const)\s+{re.escape(name)}\s*=\s*([^;]+)", scope or "")
    if not m:
        return set()
    return keys_of_rhs(txt, m.group(1), scope, depth)


def keys_of_rhs(txt: str, rhs: str, scope: str, depth: int = 0) -> set[str]:
    """跟一段表达式右值：字面量 + 其中出现的**每一个**标识符。

    裸标识符也要跟——`extract.js:629` 是 `var url = '/api/extract/results?page=' +
    … + (q ? '&' + q : ''); fetchJson(url)`，调用点处**一个 `+` 都没有**，只跟
    「`+` 后面的名字」会整条漏掉（首版就是这样把 `/api/extract/results` 报成
    「实现读了、前端从不发」的）。
    """
    if depth > MAXJ:
        return set()
    ks = set(LIT.findall(rhs)) | set(QSLIT.findall(rhs))
    for nm in set(re.findall(r"[A-Za-z_$][\w$]*", rhs)) - KW:
        ks |= follow(txt, nm, scope, depth + 1)
    return ks


def keys_in(txt: str, body: str) -> set[str]:
    """一段 JS 体里拼进 query 的键。"""
    # 局部包装：`function add(k, v) { if (v) p.push(k + '=' + …) }` +
    # `add('task_id', …)` —— 键在**调用点的第一个实参**上，不在 push 那行。
    # 首版把 PUSHV 的形参名 `k` 直接当键收了，凭空报出 `k` 这个伪键。
    # 这里同时把形参名记下来，供下面 PUSHV 排除（同一个 `k` 不该既当伪键
    # 又当包装形参）。
    wrapped: set[str] = set()
    ks = (set(SETKEY.findall(body)) | set(PUSHKEY.findall(body))
          | set(QSLIT.findall(body)))
    for fm in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\(\s*([A-Za-z_$][\w$]*)"
                          r"[^)]*\)\s*\{", body):
        fname, p1 = fm.group(1), fm.group(2)
        inner = _brace_body(body, fm.end() - 1)
        if re.search(rf"push\(\s*{re.escape(p1)}\s*\+\s*'='", inner):
            wrapped.add(p1)
            ks |= set(re.findall(rf"\b{re.escape(fname)}\(\s*'([A-Za-z_][\w]*)'", body))
    for m in PUSHV.finditer(body):
        if m.group(1) in wrapped:       # 形参名，不是键
            continue
        ks.add(f"<动态:{m.group(1)}>")
    # 映射对象：`var RAW_PARAM = { f_q: 'q', f_verdict: 'gate_verdict', … }`
    # + `p.set(RAW_PARAM[id], v)` —— 键是**对象的字符串值**，属性名只是表单元素 id。
    # 首版完全解不出，`collect.js:521` 只收到 `page`/`size`，于是 `gate_verdict`
    # 与 `q` 被误报成「前端从未发」（而它俩正是采集页的两个核心筛选）。
    for mm in re.finditer(r"\.(?:set|append)\(\s*([A-Za-z_$][\w$]*)\s*\[", body):
        d = re.search(rf"(?:const|var|let)\s+{re.escape(mm.group(1))}\s*="
                      r"\s*\{([^}]*)\}", txt, re.S)
        if d:
            ks |= set(re.findall(r"['\"]([A-Za-z_][\w]*)['\"]\s*[,}\n]", d.group(1)))
    if "MULTI_KEYS" in body:
        ks |= _arr_keys(txt)
    # `forEach((k) => … append(k, v))` 这类：形参即 MULTI_KEYS 的键
    if re.search(r"\.(?:set|append)\(\s*[A-Za-z_$][\w$]*\s*,", body):
        ks |= _arr_keys(txt)
    return ks


def path_of(ut: str) -> str:
    """URL 模板 → 路由路径。GET 的 query 不参与路由匹配，必须剥掉。

    三种拼法都要覆盖：
      `/api/accidents?*`                        —— `?` 在模板里
      `/api/causes/cooccurrence**order=*&top=15` —— `?` 被 `(qs ? '&' : '?')`
        这个**三元表达式**吃掉了，模板里根本没有 `?` 字符，只剩拼接留下的 `*`
        与字面量 `order=`／`&top=15`——首版只按 `?` 切，此处整段留下，规则匹配
        失败后被误报成「绑不到读参数的路由」。
      `/api/review/queue?*`
    """
    p = ut.split("?", 1)[0]
    m = re.search(r"[&]?[A-Za-z_]\w*=", p)       # 拼接进来的 query 段
    if m and m.start() > 0:
        p = p[:m.start()]
    # 第一个 `*`：**紧跟在 `/` 之后的是路径参数**，必须留着（
    # `/api/accidents/' + id` → `/api/accidents/*`，截掉会得到 `/api/accidents/`
    # 这个谁都不匹配的路径，该调用点两头判据都不报、静默溜过）；
    # 其余是拼接残留（`distribution` + tail、`cooccurrence` + `**order=`），截掉。
    m2 = re.search(r"\*", p)
    if m2 and not p[:m2.start()].endswith("/"):
        head = p[:m2.start()]
        if head.count("/") >= 2:                 # 至少 `/api/x`
            p = head
    return p


def _var_rhs(txt: str, name: str, scope: str = "") -> str | None:
    """`var name = ...` 的右值（到分号或换行）。

    **只在 `scope`（调用点所在的那个函数体）里找**。全文件找会把形参串到
    别的函数去——`extract.js:93` 的 `fetch(url, opts)` 在 `send(url, method,
    body)` 体内，`url` 是形参，却匹配到了 `loadResults` 里的
    `var url = '/api/extract/results?…'`，于是包装函数内部的分发点被当成业务
    调用点，凭空报出 `page` 这个「发了而实现不读」的键。空 scope 不兜底成全
    文件（§2.5.73：取不到就判不成立，不降级成猜）。
    """
    m = re.search(rf"(?:var|let|const)\s+{re.escape(name)}\s*=\s*([^;\n]+)",
                  scope or "")
    return m.group(1) if m else None


def _one_call(txt: str, expr: str, scope: str):
    """一个 URL 表达式 → (模板, 键集)；不是带 query 的 GET 则 None。

    裸变量先**展开一次**（`fetchJson(url)` ← `var url = '/api/…' + …`）；
    不带 query 痕迹又解不出键的整条跳过——`fetchJson('/api/extract/pending')`
    与 `/api/extract/tasks` 是**无参**接口，收进来只会凭空多两条「query 解不出」
    （首版实报 b1 两条假警报）。
    """
    expr = expr.strip()
    # ① 单函数调用 → 取该函数的 `return` 表达式。URL 往往在被调函数里拼好
    #    （`get(query())`、`fetchJson(url)`），调用点处**看不到 `/api/` 二字**，
    #    拿「表达式里有没有 `/api/`」做入口过滤会把它们整批漏掉。
    m = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*\(\s*\)", expr)
    if m:
        body = fn_body(txt, m.group(1))
        r = re.search(r"\breturn\s+([^;]+);", body)
        if not r:
            return None
        expr, scope = r.group(1).strip(), body
    # ② 裸变量 → 展开一次（限定在调用点所在函数体内，见 `_var_rhs`）
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
        rhs = _var_rhs(txt, expr, scope)
        if rhs is None:
            return None
        expr = rhs
    ks = keys_of_rhs(txt, expr, scope)
    ut = C.url_template(expr)
    if ut is None:
        return None
    # `has_q` 判的是「这条请求带 query」，不是「表达式里有 `+`」——`'/api/accidents/'
    # + id` 有 `+` 却一个 query 键也不带，首版把它当成带 query 的调用点收进 calls，
    # 到了 B1 就报「query 解不出」（而它本来就不该有 query）。改看展开后的模板里
    # 有没有 `?` 或 `k=`。
    has_q = bool(ks) or "?" in ut or "=" in ut
    return ut, ks, has_q


def fe_calls() -> tuple[list[dict], list[dict]]:
    """→ (带 query 的 GET 调用点, **不带** query 的 GET 调用点)。

    后者也要收：`fetchJson('/api/extract/pending')` 是**调用点在、参数不在**，
    与「这条接口前端根本没用」是两回事，B2 的文案要分得开。
    """
    out: list[dict] = []
    bare: list[dict] = []
    for f in sorted(STATIC.glob("*.js")):
        txt = f.read_text(encoding="utf-8")

        def lineno(pos: int) -> int:
            return txt[:pos].count("\n") + 1

        def add(pos: int, expr: str, how: str) -> None:
            got = _one_call(txt, expr, enclosing_fn(txt, pos))
            if got is None:
                return
            ut, ks, has_q = got
            if "/api/" not in ut:          # 入口过滤放在**展开之后**
                return
            rec = {"file": f.name, "line": lineno(pos), "url": ut,
                   "path": path_of(ut), "raw": expr.strip(),
                   "keys": ks, "how": how}
            (out if has_q else bare).append(rec)

        for name, idx in GET_CALLERS.items():
            for m in re.finditer(r"(?<![\w$.])" + name + r"\s*\(", txt):
                if re.search(r"\bfunction\s+$", txt[max(0, m.start() - 20):m.start()]):
                    continue
                args = C._split_args(C._js_balanced(txt, m.end() - 1))
                if args and idx < len(args):
                    add(m.start(), args[idx], name)
        # 裸 `fetch(url, opts)` 的读方法（opts 无 method 或 method: 'GET'）
        for m in re.finditer(r"\bfetch\s*\(", txt):
            # 请求包装函数体内的那个 `fetch` **不是业务调用点**：`url` 是形参，
            # 由调用方传进来，这里只是分发。`send` 已由 §2.5.80 登记在
            # `C.WRAPPERS` 里（同一个源不写第二遍解析），直接跳过——不收它，
            # 自然也不会把它展开成 `/api/extract/results`。
            if enclosing_fn_name(txt, m.start()) in C.WRAPPERS:
                continue
            args = C._split_args(C._js_balanced(txt, m.end() - 1))
            if not args:
                continue
            mm = (re.search(r"method\s*:\s*['\"](\w+)['\"]", args[1], re.I)
                  if len(args) > 1 else None)
            if mm and mm.group(1).upper() != "GET":
                continue
            add(m.start(), args[0], "fetch")
    key = lambda d: (d["file"], d["line"])          # noqa: E731
    return sorted(out, key=key), sorted(bare, key=key)


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════
def compare(routes, impl, calls, bare=()) -> dict:
    """四档判据 + 逐路由对照表。

    B2 是**逐键**判的，不是「整条路由没有调用点」才判：后端支持而页面没画的
    每一维都要单独列出来——`/api/accidents` 支持 12 维、致因页只画了其中几维，
    这种差只有逐键比才看得见（`docs/web/00` §8.2「后端给了几维筛选 ↔ 页面画了
    几维」要的就是这个）。
    """
    a1: list[str] = []      # 前端发、实现不读（筛选静默失效）
    a2: list[str] = []      # 调用点绑不到路由（请求打空）
    b1: list[str] = []      # 调用点 query 解不出（不算通过）
    b2: list[str] = []      # 实现读、前端从不发（后端能力没被用上）
    table: list[dict] = []
    bound = 0

    for rule, ep in routes:
        keys, _why = impl.get(ep, (set(), "未解析"))
        if not keys:
            continue                        # 不读参数的路由不在本节范围
        rx = C.rule_re(rule)
        hits = [c for c in calls if rx.match(c["path"])]
        sent: set[str] = set()
        for h in hits:
            bound += 1
            if not h["keys"]:
                b1.append(f"{h['file']}:{h['line']} {h['raw'][:60]} query 解不出")
                continue
            sent |= {k for k in h["keys"] if not k.startswith("<")}
            for k in sorted({k for k in h["keys"] if not k.startswith("<")} - keys):
                a1.append(f"{h['file']}:{h['line']} 发 `{k}` 而 {ep} 不读（{rule}）")
        if not hits:
            why = ("前端没用这条接口" if not any(rx.match(b["path"]) for b in bare)
                   else "有调用点但不带任何参数")
        else:
            why = "调用点没带这一维"
        for k in sorted(keys - sent):
            b2.append(f"{rule}（{ep}）读 `{k}`，前端从未发（{why}）")
        table.append({"rule": rule, "ep": ep, "reads": sorted(keys),
                      "sends": sorted(sent),
                      "calls": [f"{h['file']}:{h['line']}" for h in hits]})

    # 调用点 → 路由（反向：绑不到就是打空）
    read_rules = [(r, e) for r, e in routes if impl.get(e, (set(), ""))[0]]
    for h in calls:
        if not h["keys"]:
            continue
        if not any(C.rule_re(r).match(h["path"]) for r, _ in read_rules):
            a2.append(f"{h['file']}:{h['line']} {h['path']} 绑不到读参数的路由")

    return {"a1": a1, "a2": a2, "b1": b1, "b2": b2, "table": table,
            "bound": bound}


def build_report():
    """一次完整跑：路由 / 实现侧 / 前端侧 / 判据。"""
    S.point_at(str(S.main_db_path()), str(S.staging_path()))
    from construction_agent.web.factory import create_app

    app = create_app()
    routes = get_routes(app)
    impl = {ep: impl_keys(ep) for _r, ep in routes}
    calls, bare = fe_calls()
    rep = compare(routes, impl, calls, bare)
    rep["n_routes"] = len(routes)
    rep["read_routes"] = sum(1 for e in impl.values() if e[0])
    rep["n_calls"] = len(calls)
    rep["n_bare"] = len(bare)
    rep["bare"] = [f"{b['file']}:{b['line']} {b['path']}" for b in bare]
    # endpoint 认不出函数（`impl_keys` 的第二个返回值非空）却键集为空时，
    # 会被 `compare` 当成「这条路由不读参数」静默跳过——这正是 §2.5.67 那条
    # 元判据防的形态：解析失败退化成豁免。
    #
    # 只收**带模块前缀**的：`static` 是 Flask 内建（无点，不读任何参数），
    # 把它算成「解析失败」是判据过宽——§2.5.63 立的规矩，宽度决定可信度。
    rep["unresolved"] = [f"{ep}（{why}）" for ep, (k, why) in impl.items()
                         if not k and why and "." in ep]
    rep["reds"] = sorted({k for k in ("a1", "a2", "b1") if rep[k]})
    return rep


# ══════════════════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════════════════
FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return bool(cond)


def part_a(rep) -> None:
    print("\n═══ A. 实测 ═══")
    print(f"  GET 路由 {rep['n_routes']} 条：读 query 的 {rep['read_routes']} 条、"
          f"不读的 {rep['n_routes'] - rep['read_routes']} 条")
    print(f"  前端 GET 调用点 {rep['n_calls'] + rep['n_bare']} 处："
          f"带 query {rep['n_calls']} 处、不带 {rep['n_bare']} 处")
    print(f"  绑定（调用点 × 读参数路由）{rep['bound']} 对")
    print("\n  逐路由对照（读 = 后端读的维、发 = 前端画到的维）：")
    for t in rep["table"]:
        miss = sorted(set(t["reads"]) - set(t["sends"]))
        print(f"    {t['rule']:38s} 读{len(t['reads']):2d} 发{len(t['sends']):2d}"
              f"  缺 {','.join(miss) if miss else '—'}")
        print(f"    {'':38s} 调用点 {','.join(t['calls']) or '（无）'}")
    print("\n  不带 query 的调用点（B2 文案用）：")
    for b in rep["bare"]:
        print(f"    {b}")


def part_b(rep) -> None:
    print("\n═══ B. 判据 ═══")
    ok(not rep["a1"], "A1 前端发的每个键，实现都读（否则筛选静默失效）",
       "" if not rep["a1"] else str(rep["a1"][:3]))
    ok(not rep["a2"], "A2 每个带 query 的调用点都绑得到读参数路由（否则请求打空）",
       "" if not rep["a2"] else str(rep["a2"][:3]))
    ok(not rep["b1"], "B1 每个绑上的调用点都解得出键（解不出必须列出，不算通过）",
       "" if not rep["b1"] else str(rep["b1"][:3]))
    ok(not rep["unresolved"],
       "A3 没有「找不到函数」却键集为空的路由（解析失败不静默降级成不读参数）",
       "" if not rep["unresolved"] else str(rep["unresolved"][:3]))
    bound_n = sum(len(t["calls"]) for t in rep["table"])
    ok(bound_n + len(rep["a2"]) == rep["n_calls"],
       f"A4 带 query 的调用点全部有归属：绑定 {bound_n} + 打空 {len(rep['a2'])}"
       f" = {bound_n + len(rep['a2'])}，应为 {rep['n_calls']}",
       f"实际 {bound_n}+{len(rep['a2'])}={bound_n + len(rep['a2'])}")

    print("\n═══ B'. 定量（不判红，分类见 01 号文档 §2.5.82）═══")
    print(f"  B2 实现读、前端从未发：{len(rep['b2'])} 条")
    for s in rep["b2"]:
        print(f"    · {s}")


def _run_diff() -> dict:
    p = subprocess.run([S.PY, __file__, "--diff"], capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
    if not line:
        raise RuntimeError(f"--diff 无 JSON：{p.stdout[-400:]}{p.stderr[-400:]}")
    return json.loads(line[0][len("@@JSON@@"):])


def part_c() -> None:
    """注入式自证：断言**红的恰好是哪几档**（§2.5.68）。"""
    print("\n═══ C. 注入式自证（红的恰好是哪几档）═══")
    base = _run_diff()
    ok(base["reds"] == [], "基线（未注入）红集合为空", f"实际 {base['reds']}")
    nb2 = len(base["b2"])
    ncalls = base["n_calls"]

    cases = [
        # ① 前端发一个后端不读的键 → A1 恰好多 1 条（`zzz_bogus` 全仓只此一处）
        ("前端 `overview.js` 多拼一个后端不读的 `zzz_bogus`",
         STATIC / "overview.js",
         "  p.set('sort', state.sort);",
         "  p.set('sort', state.sort);\n  p.set('zzz_bogus', '1');",
         {"a1"}),
        # ② 后端多读一个前端不发的键 → B2 恰好多 1 条
        #    （**不能用「改键名」**：改了之后旧键消失、新键出现，B2 条数一增一减
        #    净变化为零，用例会静默失效——§2.5.64「多出来的用例是空话」）
        ("后端 `extract.py` 多读一个前端从未发的 `zzz_new`",
         WEB / "extract.py",
         "    per_item = None\n",
         '    zzz_new = request.args.get("zzz_new")\n    per_item = None\n',
         set()),
        # ③ 前端把 URL 打空 → A2 恰好 1 条（`review.js` 的 queue 是唯一调用点）
        ("前端 `review.js` 把 `/api/review/queue?` 打空成 `/api/nope?`",
         STATIC / "review.js",
         "return '/api/review/queue?' + p.join('&');",
         "return '/api/nope?' + p.join('&');",
         {"a2"}),
    ]
    for i, (label, path, old, new, want) in enumerate(cases):
        with S._with_file(path, old, new):
            got = _run_diff()
        if i == 1:      # 例②断的是 B2 计数（B2 不判红，红集合应保持空）
            ok(len(got["b2"]) == nb2 + 1 and got["reds"] == [],
               f"{label} → B2 {nb2} → {len(got['b2'])}、红集合仍空",
               f"实际 B2 {len(got['b2'])}、红 {got['reds']}")
        else:
            ok(set(got["reds"]) == want,
               f"{label} → 红集合恰好 {sorted(want)}", f"实际 {got['reds']}")

    # ④ 「打空」必须能被 A2 抓到，而不是被 A4 闭式断言当成漏判
    with S._with_file(STATIC / "review.js",
                      "return '/api/review/queue?' + p.join('&');",
                      "return '/api/nope?' + p.join('&');"):
        got = _run_diff()
    ok(got["n_calls"] == ncalls and len(got["a2"]) == 1,
       f"A4 闭式断言对「打空」的反应：调用点仍 {got['n_calls']} 处、"
       f"其中 1 处进 A2（不静默漏判）", f"实际 {got['n_calls']}/{len(got['a2'])}")


def _doc_stat(rep) -> dict[str, int]:
    return {
        "GET 路由": rep["n_routes"],
        "读 query 的 GET 路由": rep["read_routes"],
        "带 query 的前端调用点": rep["n_calls"],
        "不带 query 的前端调用点": rep["n_bare"],
        "前端发而实现不读": len(rep["a1"]) + len(rep["a2"]),
        "实现读而前端从未发": len(rep["b2"]),
    }


def doc_checks(rep) -> list[str]:
    """D 段判据（不打印，让 `--diff` 的 stdout 仍只有一行 JSON）。"""
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
        ok(not hit, f"01 号文档表里「{label}」= {n}", "" if not hit else f"（{hit[0]}）")
    for needle in DOC_NEEDLES:
        ok(needle in DOC.read_text(encoding="utf-8"),
           f"01 号文档里有锚点「{needle[:22]}」")


def main() -> int:
    rep = build_report()
    if "--diff" in sys.argv:
        out = {k: v for k, v in rep.items() if k != "table"}
        print("@@JSON@@" + json.dumps(out, ensure_ascii=False))
        return 1 if rep["reds"] or doc_checks(rep) else 0

    part_a(rep)
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
