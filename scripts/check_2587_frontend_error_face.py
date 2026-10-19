#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.87 错误路径的前端消费面：后端报的错，前端读没读、有没有让用户看见。

## 来路

§2.5.83/84 核了**快乐路径**的响应键（前端读的键 ↔ 实现返回的键），§2.5.85/86 把
**后端**的错误分支全打了一遍（GET 错误分支独有 67 条键路径、写路由 16 条）。三段
合起来只说明一件事：**后端确实把错报出来了**。但报出来了不等于有人读：

    后端 503 {error, message, hint}  →  前端 fetchJson 把它转成 Error(msg)  →
    调用点有没有 catch  →  catch 里有没有写进 DOM  →  用户看不看得见

这条链上有四道可以断的地方，**此前一道都没核过**。本节核的就是这四道。

## 判据

| 判据 | 内容 | 抓什么 |
|---|---|---|
| **B1** | 每个业务调用点都有失败处理（链上 `.catch` 或 `await` 在 `try` 内） | 错误被 promise 链吞掉，控制台一条 unhandled rejection |
| **B2** | 失败处理不许**只有** `console.*` | 「开发者看得见、用户看不见」 |
| **B3** | 静默 catch（空体 / 只 `return null`）必须有理由 | 沉默地降级成空面板，用户以为是"本来就没有" |
| **B4** | catch 里读的错误键必须真出现在后端错误分支实测响应里 | 读了个后端从没给的键 ⇒ 恒 `undefined` ⇒ 用户看到写死的兜底话术 |
| **B5** | 错误键面覆盖（**观察项，不判红**） | 后端给了 `hint`（下一步动作），前端在哪读它 |

**B4 的真值面是实测的，不是引文档的。** §2.5.85 立过一条规矩：`"别的分支里应该
有" 本身是个推断，没被任何一次真实响应验证过`。所以本节直接复用 §2.5.85 的
`sweep()`（280 格）拿真响应体，取其中**错误分支**那些格的键路径并集当真值面——
引的是已被验证过的打点函数，不是转述它写在文档里的结论（§2.5.52）。

## 边界（先说清楚，免得把绿灯当免检）

- B1 核的是「有没有失败处理」，**核不到处理得对不对**（是不是该重试、该不该降级）。
- B2 认的"可见落地"是**源码层面**的三个动作（`textContent` / `appendChild` /
  `alert|notice|renderError|showDbNotice|failPlan`）。写进了一个 `display:none`
  的容器、或写进一个根本不存在的 id（`$('x')` 返回 null 时直接抛）都算"绿"——
  **真正的可见性要 DOM 断言**（§2.5.68 那条路的延伸，本节没做）。
- B3 的"理由"判据只认**注释非空**，核不到理由**成立不成立**（§2.5.67 的教训：
  例外白名单不能只写理由不验证——本节的处置是**不给白名单**，理由写在哪都得是注释，
  且注释贴在 catch 上，改代码时看得见）。
- B4 的宽度：核的是**键名**在后端错误分支键面里出现过，**没有绑到调用点自己的
  那条路由上**（§2.5.85 的首版就是因为跨路由借据才假绿）。本节把这条宽度**明写在
  这里**而不是假装没有——绑路由需要 §2.5.84 的 `bind_rules` 那一整套，而本节要核的
  键只有 `message` / `hint` 两个，是 `dberr` 的通用键，绑路由对结论没有增益。
- 全部判据都建立在「`static/*.js` 是页面上真正跑的那份」之上。**内联在模板
  `.html` 里的脚本不在本节射程内**——`overview.js` 的注释说它自带一份等效内联副本，
  那是历史遗留，两份不会同时加载（§2.5.85 已记）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root             # noqa: E402
from _serve import point_at                                  # noqa: E402
import check_2583_get_response as G                          # noqa: E402
import check_2584_live_keys as K                             # noqa: E402
import check_2585_error_branches as E85                      # noqa: E402

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
MAIN_DB = main_db_path()
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

#: 锚点句用**本节独有**的整行标题（§2.5.83 的教训：与别节同族的措辞会让
#: "锚点被删"用例恒真）。
DOC_NEEDLES = [
    "### 2.5.87 展示层（续）：错误路径的前端消费面——"
    "**后端报的错，前端读没读、有没有让用户看见**"
]

#: 请求封装：`文件 → 函数名`。这些函数**体内**的裸 `fetch` 不是业务调用点——
#: 它们的"失败处理"是把响应体转成 `Error` 再 `throw`，交给调用方。
#:
#: **四个文件的封装名各不相同，而且不能猜**：`extract.js` 那份叫 `send`
#: （`extract.js:84`，注释写着"dom.js 的 fetchJson 只做 GET，POST/DELETE 得自己来"），
#: `collect.js` 干脆**没有封装函数**——三处写操作各自内联了一遍
#: `r.json().then(b => ({ok: r.ok, body: b}))`。第一版把 `extract.js` 的名字猜成
#: `req`，于是 `extract.js:93` 那个封装体内的 `fetch` 被判成"业务调用点、无失败处理"
#: ——一条**凭空造出来的假红**。名字来自 grep，不来自印象。
WRAPPERS = {
    "dom.js": "fetchJson",
    "overview.js": "jsonGet",
    "extract.js": "send",
    "review.js": "req",
}

#: 调用点正则。**不含单独的 `get`**：§2.5.83 的 `CALLER` 里带 `get` 是为了
#: `review.js:87` 那种 `var get = function (u) { return req(u, 'GET'); }`，但那是
#: **别名定义**不是调用点，而裸 `get` 在普通代码里太常见（`map`/`filter` 的邻居、
#: 变量名）。本脚本改成：`fetchJson|jsonGet|send|req|fetch` 一网打尽，另用
#: `ALIAS_CALL` 单独捞 `review.js` 的 `get('/api/…')` 形态。
CALLER = re.compile(r"(?<![\w$.])(fetchJson|jsonGet|send|req|fetch)\s*\(")
ALIAS_CALL = re.compile(r"(?<![\w$.])(get|post)\s*\(\s*(['\"`])(/api/[^'\"`]*)")

#: `.catch(` 的方法式匹配（用于走 Promise 链）。
CHAIN_STEP = re.compile(r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")

#: catch 参数名（`.catch(function (e)` / `.catch((e) =>` / `.catch(e =>`）。
CATCH_ARG = re.compile(
    r"\.catch\s*\(\s*(?:function\s*)?\(?\s*([A-Za-z_$][\w$]*)\s*\)?\s*(?:=>|\{)")

#: "用户看得见"的源码动作。**不含 `console.*`**——那正是 B2 要抓的。
#:
#: `applyDbDown` 是**间接**落地：它自己写 `chip.className` / `text.textContent`
#: （`dom.js:94-104`），在调用点上看不见赋值。第一版把它算成"other"档，
#: `dom.js:110`／`overview.js:145` 两条就成了归类不明的记录——**判据不认识的形态
#: 必须有名有姓，不能落在"其他"里**（否则"其他"会变成事实上的白名单）。
VISIBLE = re.compile(
    r"\.textContent\s*=|\.innerText\s*=|\.innerHTML\s*=|\.hidden\s*=|"
    r"appendChild\s*\(|\balert\s*\(|\bnotice\s*\(|\brenderError\s*\(|"
    r"\bshowDbNotice\s*\(|\bfailPlan\s*\(|\bshowDrawerMsg\s*\(|\bcostError\s*\(|"
    r"\bapplyDbDown\s*\(")

CONSOLE = re.compile(r"\bconsole\s*\.\s*(?:error|warn|log|info|debug)\s*\(")

#: 「把**响应体**挂到抛出的错误上」的两种写法（全库实测就这两种）：
#:   ① `e.body = body`                      —— `dom.js:60` / `review.js`
#:   ② `Object.assign(new Error(...), { body })` —— `overview.js:72`
#:
#: **裸 `\.body\s*=` 两种都判不对**：它会把 `opts.body = JSON.stringify(body)`
#: 当成①（那是 fetch 的**请求体**，`extract.js:91` / `review.js:81`），又漏掉②。
#: 第一版正是这样——`extract.js` 的 `send` 被判成"挂了 body"，而它挂的是请求体；
#: `overview.js` 的 `jsonGet` 被判成"没挂"，而它 `Object.assign` 挂了。
#: **两个方向都错**，且错得刚好相反。
BODY_HOOK = re.compile(r"(?<!opts)(?<!options)(?:\.\s*body\s*=(?!=)|\{\s*body\s*[,}]"
                       r"|\bObject\s*\.\s*assign\s*\([^)]*,\s*\{[^}]*\bbody\b)")

#: B4 的键比对面：`键名 → 该键在响应体里的路径集合（供打印用）`。
#: `e.status` 不在表里——它不是响应体的键，是 `fetchJson` 挂在 Error 上的属性，
#: 单列一档核（见 `test_b4`）。
BODY_KEY_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\.(?:body\.)?([A-Za-z_$][\w$]*)")

FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    """记一条判据结果。**`extra` 只在失败时打印**，免得绿的时候刷屏。"""
    print(f"  {'✓' if cond else '✗'} {label}" + (f"　{extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return cond


# ══════════════════════════════════════════════════════════════════
# JS 结构解析
# ══════════════════════════════════════════════════════════════════
_QUOTES = "'\"`"


def _skip_str(txt: str, i: int) -> int:
    """`i` 指向引号字符，返回配对引号之后的位置（含转义与模板串）。"""
    q = txt[i]
    j = i + 1
    while j < len(txt):
        c = txt[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        #: 模板串里可能嵌 `${…}`——那些花括号落在 `_skip_call` 的计数里，
        #: Python 侧只要不把引号数错就够（JS 的 `${…}` 不会含裸的同类引号）。
        j += 1
    return len(txt)


def _skip_call(txt: str, i: int) -> int:
    """`i` 指向 `(` 之后，返回配对 `)` 之后的位置。

    **必须跳过字符串**：`el('p', {text: '（已过滤）'})` 是平衡的，但
    `notice('失败 (' + e.message)` 这类半角括号在文案里不罕见，光数括号会走偏。
    """
    depth = 1
    j = i
    while j < len(txt):
        c = txt[j]
        if c in _QUOTES:
            j = _skip_str(txt, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return len(txt)


def _chain(txt: str, pos: int) -> tuple[int, list[str], int]:
    """从调用点的 `(` 之后走完整条链。

    → `(链尾位置, 链上的方法名列表, 链尾所在行号)`。

    **必须走链而不是看紧邻下一行**：`causes.js:616-619` 是
    `Promise.all([fetchJson(…), fetchJson(…), fetchJson(…)])` 下面挂一个
    `.catch`——三个调用点共用一个 catch，隔了 18 行；而 `collect.js:455` 的
    catch 在 37 行之外。第一版曾经用"往后 700 字符内找 `.catch(`"来判，
    三个调用点全被判成"无失败处理"——**窗口宽度是个假判据**，链才是真边界。
    """
    p = _skip_call(txt, pos)
    methods: list[str] = []
    while True:
        m = CHAIN_STEP.match(txt, p)
        if not m:
            break
        methods.append(m.group(1))
        p = _skip_call(txt, m.end())
    return p, methods, txt.count("\n", 0, p) + 1


def _fn_span(txt: str, name: str) -> tuple[int, int, int] | None:
    """函数声明 `function name(...) { … }` → `(签名起点, 函数体起点, 结束)`。

    **签名也要含进来**：定义行自身 `function fetchJson(url) {` 里的 `fetchJson(`
    会被调用点正则匹配到，只给函数体范围的话它落在范围外 ⇒ 被判成"业务调用点"
    （首版实测：`dom.js:53`／`overview.js:70`／`extract.js:93`／`review.js:68`
    四个**定义行**全成了 B1 的假红）。
    """
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", txt)
    if not m:
        return None
    b = txt.find("{", m.end())
    if b < 0:
        return None
    depth = 1
    j = b + 1
    while j < len(txt):
        c = txt[j]
        if c in _QUOTES:
            j = _skip_str(txt, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return m.start(), b, j + 1
        j += 1
    return m.start(), b, len(txt)


#: 链容器：一个表达式的链尾会挂 `.then`/`.catch` 的那些起点。
#: `Promise.all` 必须在册——`causes.js:616/617/619` 三个调用点被它包着，
#: 各自的 `)` 后面什么都没有，**失败处理整个挂在 `Promise.all(...)` 的链尾**。
#: 首版没把它当容器，三个调用点全被判成"无失败处理"。
CONTAINER = re.compile(
    r"(?<![\w$.])(Promise\s*\.\s*allSettled|Promise\s*\.\s*all|"
    r"fetchJson|jsonGet|send|req|fetch)\s*\(")


def containers_in(txt: str) -> list[tuple[int, int, list[str]]]:
    """全文所有的链容器 → `[(起点, 终点, 链上方法名)]`。

    容器**可以嵌套**（`.then` 的回调体里再发一个请求），嵌套时两条都在册——
    判定用哪个由 `handling_chain()` 决定。
    """
    out: list[tuple[int, int, list[str]]] = []
    hits = [(m.start(), m.end() - 1) for m in CONTAINER.finditer(txt)]
    #: `review.js` 的 `get('/api/…')`／`post('/api/…', …)` 别名调用也是容器
    for m in ALIAS_CALL.finditer(txt):
        hits.append((m.start(), m.start() + len(m.group(1))))
    for start, op in hits:
        end, methods, _ln = _chain(txt, op + 1)
        out.append((start, end, methods))
    return out


def handling_chain(txt: str, pos: int,
                   conts: list) -> tuple[int, int, list[str]] | None:
    """这个调用点的失败处理挂在**哪一条**链上。

    规则：**从内往外找第一个带 `.catch` 的容器**。不是"取最外层"，也不是
    "取最内层"，理由是两种取法各错一半：

    - 取最外层 ⇒ `overview.js:709` 自己的 `.catch(() => null)`（有理由注释）
      会被 `712` 的外层 catch 顶掉，那条"有理由的静默降级"看不见了；
    - 取最内层 ⇒ `causes.js:616`（自己只是个 `fetchJson(...)`）会被判成"没有
      失败处理"，而**错误其实会被 `Promise.all` 的 catch 接住**——假红。

    从内往外找第一个带 catch 的，两种都对：错误在 JavaScript 里的传播路径本来
    就是"谁先接住算谁的"。
    """
    cands = [c for c in conts if c[0] <= pos < c[1]]
    if not cands:
        return None
    cands.sort(key=lambda c: c[0], reverse=True)      # 内层优先
    for c in cands:
        if "catch" in c[2]:
            return c
    return None


def _line_of(txt: str, pos: int) -> int:
    return txt.count("\n", 0, pos) + 1


def _enclosing_try(txt: str, pos: int) -> bool:
    """`pos` 所在的 `await` 调用是否被某个 `try {` 罩着（且该 try 有 catch）。

    只处理 `accident.js:679-686` 那一种形态：`try { body = await fetchJson(…) }
    catch (err) { renderError(err); }`。判定方式是往前找最近的 `try`，再看它到
    下一个 `function` 之间有没有 `catch`——**够用且保守**（找不到就返回 False，
    宁可多报一条"无失败处理"让人去看，也不假装有）。
    """
    head = txt[:pos]
    t = head.rfind("try")
    while t >= 0:
        #: `try` 后面必须紧跟 `{`（否则是变量名里含 try 的巧合）
        if re.match(r"\s*\{", head[t + 3:]):
            tail = txt[t:t + 4000]
            return bool(re.search(r"\}\s*catch\s*\(", tail))
        t = head.rfind("try", 0, t)
    return False


def _catch_body(txt: str, m: re.Match) -> tuple[str, str]:
    """`.catch(...)` 的 `(参数名, 函数体文本)`。"""
    arg = ""
    am = re.search(r"\(\s*(?:function\s*)?\(?\s*([A-Za-z_$][\w$]*)", txt[m.start():m.start() + 60])
    if am:
        cand = am.group(1)
        if cand not in ("function",):
            arg = cand
    end = _skip_call(txt, m.end())
    seg = txt[m.end():end]
    b = seg.find("{")
    if b < 0:
        #: 箭头简写体：`.catch(() => null)` —— `seg` 尾巴上带着那个配对的 `)`，
        #: 不去掉的话 `() => null)` 会被判成"有代码"，静默降级就漏出 `empty` 档。
        return arg, seg.rstrip().rstrip(")").strip()
    depth = 1
    j = b + 1
    while j < len(seg):
        c = seg[j]
        if c in _QUOTES:
            j = _skip_str(seg, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return arg, seg[b + 1:j]
        j += 1
    return arg, seg[b + 1:]


def _has_reason(txt: str, m: re.Match) -> bool:
    """这个 `.catch(` **自己**有没有理由。

    查两处：①`.catch` 之前的 3 行里有没有注释（多行链把理由写在链上方是
    常见写法，`overview.js:705-706` 就是）；②`.catch` 到它的闭合括号之间有没有
    注释（`review.js:207` 写在体内）。**两处都不许借**——别处的注释救不了这一条。
    """
    line = _line_of(txt, m.start())
    lines = txt.split("\n")
    head = "\n".join(lines[max(0, line - 4):line - 1])
    end = _skip_call(txt, m.end())
    body = txt[m.start():end]
    for seg in (head, body):
        if re.search(r"/\*[\s\S]*?\*/", seg) or re.search(r"//[^\n]*\S", seg):
            return True
    return False


# ══════════════════════════════════════════════════════════════════
# A. 实测
# ══════════════════════════════════════════════════════════════════
def scan_calls() -> list[dict]:
    """扫全部 `static/*.js`，给每个**调用点**一条记录。

    `kind` 四档：`def`（封装函数**定义行**自身，不是调用）／`wrapper`（封装函数
    体内的裸 fetch）／`alias`（`review.js` 的 `get`/`post` 定义体里的 `req`）／
    `call`（业务调用点，本节的判据对象）。
    """
    out: list[dict] = []
    for js in sorted(STATIC.glob("*.js")):
        txt = js.read_text(encoding="utf-8")
        fname = js.name
        spans = {n: sp for n in set(WRAPPERS.values())
                 if (sp := _fn_span(txt, n))}
        conts = containers_in(txt)
        alias_refs = {m.start() for m in ALIAS_CALL.finditer(txt)}

        def kind_at(pos: int) -> str:
            for sp in spans.values():
                if sp[0] <= pos < sp[2]:
                    #: 落在函数体之前 = 就是那行定义（`function fetchJson(url) {`）
                    return "def" if pos < sp[1] else "wrapper"
            return "call"

        seen: set[int] = set()
        hits: list[tuple[int, str]] = [(m.start(), m.group(1))
                                       for m in CALLER.finditer(txt)]
        for m in ALIAS_CALL.finditer(txt):
            if m.start() not in alias_refs:            # pragma: no cover
                continue
            hits.append((m.start(), m.group(1)))
        for pos, fn in sorted(hits):
            if pos in seen:
                continue
            seen.add(pos)
            kind = kind_at(pos)
            #: `review.js:87-88` 的 `var get = function (u) { return req(u, 'GET'); }`
            #: ——`req` 在那里是**别名转发**，业务调用点在 `get('/api/…')` 那 40 多处。
            #: 这两行落在 `req` 的**函数体之外**（`get`/`post` 各自是独立的函数表达式），
            #: 所以 `kind_at` 判不出来，得按行形态单独认。
            if fn == "req":
                ln = txt.split("\n")[_line_of(txt, pos) - 1].replace(" ", "")
                if "returnreq(" in ln or "=>req(" in ln:
                    kind = "alias"
            out.append({"file": fname, "line": _line_of(txt, pos), "fn": fn,
                        "kind": kind, "pos": pos, "txt": txt, "conts": conts})
    return out


def classify(rec: dict) -> dict:
    """给一个**业务调用点**判：失败处理在哪、catch 体长什么样、读了什么键。"""
    txt, pos = rec["txt"], rec["pos"]

    chain = handling_chain(txt, pos, rec["conts"])
    if chain is None:
        #: 链上没有 catch，还可能是 `await` 形态（`accident.js:682`）——
        #: `try { body = await fetchJson(…) } catch (err) { renderError(err) }`
        #: 里那个 catch 不在 Promise 链上，`handling_chain` 看不见它。
        ln = txt.split("\n")[rec["line"] - 1]
        if "await" in ln and _enclosing_try(txt, pos):
            rec.update({"mode": "try", "catch_arg": "",
                        "catch_body": "renderError(" if "renderError(" in txt else "",
                        "catch_line": 0, "reason": True, "methods": ["try/catch"]})
            return rec
        rec.update({"mode": "none", "catch_arg": "", "catch_body": "",
                    "catch_line": 0, "reason": False, "methods": []})
        return rec

    start, _end, methods = chain
    rec["methods"] = methods
    rec["chain_start_line"] = _line_of(txt, start)

    #: 形态一：链上有 `.catch(`
    if "catch" in methods:
        op = txt.find("(", start)
        p = _skip_call(txt, op + 1)
        cm = None
        while True:
            m = CHAIN_STEP.match(txt, p)
            if not m:
                break
            if m.group(1) == "catch":
                cm = m
                break
            p = _skip_call(txt, m.end())
        rec["mode"] = "chain"
        rec["catch"] = cm
        arg, body = _catch_body(txt, cm)
        rec["catch_arg"] = arg
        rec["catch_body"] = body
        rec["catch_line"] = _line_of(txt, cm.start())
        rec["reason"] = _has_reason(txt, cm)
        return rec

    #: 形态二：`await` 在 try 内
    ln = txt.split("\n")[rec["line"] - 1]
    if "await" in ln and _enclosing_try(txt, pos):
        rec.update({"mode": "try", "catch_arg": "",
                    "catch_body": "renderError(" if "renderError(" in txt else "",
                    "catch_line": 0, "reason": True})
        return rec

    rec.update({"mode": "none", "catch_arg": "", "catch_body": "",
                "catch_line": 0, "reason": False})
    return rec


def catch_shape(rec: dict) -> tuple[str, str]:
    """catch 体的形态与说明。

    → `(档位, 人话)`。档位：`visible`（写进了 DOM / 弹了窗）／`console`
    （只有 console）／`empty`（空体或只 return）／`n/a`（无 catch）。
    """
    if rec["mode"] == "none":
        return "n/a", "无失败处理"
    body = rec["catch_body"]
    if rec["mode"] == "try":
        #: await 形态的 catch 体不带在调用点上——回退到"该文件里那个 catch 有
        #: 没有可见动作"（`accident.js` 只有一处 catch，就是它）。
        body = "renderError(" if "renderError(" in rec["txt"] else ""
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", body).strip()
    vis = VISIBLE.search(body)
    con = CONSOLE.search(body)
    if vis:
        return "visible", vis.group(0)
    if con:
        return "console", "只有 " + con.group(0)
    #: 箭头简写体 `() => null` 等价于 `{ return null; }`——先脱掉箭头再判，
    #: 否则 `overview.js:709`（`.catch(() => null)`，注释里明写"图片统计失败
    #: 不该拖垮整个质量面板"）会掉进 `other` 档，一条**有理由的静默降级**
    #: 就成了"归类不明"。
    norm = re.sub(r"^[^=]*?=>", "", code, count=1).strip().rstrip(";").strip()
    if not norm or re.fullmatch(r"(return\s+)?(null|undefined)?", norm):
        return "empty", ("空体" if not code else code[:30])
    return "other", code[:40]


def read_keys(rec: dict) -> list[tuple[str, str]]:
    """catch 体里读的错误键 → `[(表达式, 键名)]`。

    只认两种：`参数.键`（如 `e.message`）与 `参数.body.键`（如 `e.body.hint`）。
    **`e.body.` 那一档要有 `fetchJson` 真的挂了 `e.body` 才算数**——否则前端读的是
    一个不存在的属性。本节把这件事单列一档核（见 `test_b4`）。

    **跟进一层本地函数**：`dom.js:110` 的 catch 体只有一句
    `applyDbDown(chip, text, e)`，`e.message` / `e.body.hint` 都写在
    `applyDbDown` 体内。不跟进的话，**恰好 B5 最关心的那两个读**（后端 503 的
    `message` 与 `hint`）在本节里是看不见的——判据报绿只是因为"我没看"。
    映射按**实参序号 → 形参名**，只认同文件里 `function f(…) {…}` 那一种形态，
    认不出来就不猜（少报好过乱报）。
    """
    arg = rec["catch_arg"]
    if not arg or rec["mode"] == "try":
        return []
    body = rec["catch_body"]
    txt = rec["txt"]
    out: list[tuple[str, str]] = []
    pat = r"\b" + re.escape(arg) + r"\.(?:body\.)?([A-Za-z_$][\w$]*)"
    for m in re.finditer(pat, body):
        if m.group(1) == "body":
            #: `e.body`（后面不跟键名）**不是"读某个响应键"**——它是封装自己挂
            #: 上去的中间层（`dom.js:60` 的 `e.body = body`），语义是"进入响应体"。
            #: 第一版把 `(?:body\.)?` 写成可选前缀，于是 `dom.js:101` 的
            #: `(info && info.body) || info` 被读成"读键 `body`"⇒ 凭空一条 B4
            #: 假红（`body` 不在后端错误分支键面里，实测 46 键无此键）。
            #: **宽度**：真有人读一个后端就叫 `body` 的响应键，这里会漏——登记在案。
            continue
        out.append((m.group(0), m.group(1)))

    for fm in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(([^()]*)\)", body):
        fname, argstr = fm.group(1), fm.group(2)
        parts = [a.strip() for a in argstr.split(",")]
        if arg not in parts:
            continue
        sp = _fn_span(txt, fname)
        if not sp:
            continue
        pm = re.search(r"\(([^)]*)\)", txt[sp[0]:sp[1]])
        params = [p.strip() for p in pm.group(1).split(",")] if pm else []
        idx = parts.index(arg)
        if idx >= len(params):
            continue
        pname = params[idx]
        for m in re.finditer(
                r"\b" + re.escape(pname) + r"\.(?:body\.)?([A-Za-z_$][\w$]*)",
                txt[sp[1]:sp[2]]):
            if m.group(1) == "body":
                continue
            out.append((f"{fname}()→{m.group(0)}", m.group(1)))
    return sorted(set(out))


def err_face() -> tuple[set, dict]:
    """后端**错误分支**实测键面（复用 §2.5.85 的 `sweep`，280 格）。

    只取三类格：`status >= 400`、`available is False`、非 JSON。其余是快乐路径，
    §2.5.83/84 管过了。返回 `(键名集合, 明细)`。
    """
    point_at(str(MAIN_DB), str(STAGING_DB))
    from construction_agent.web.factory import create_app

    app = create_app()
    tmp = Path(tempfile.mkdtemp(prefix="c2587_"))
    shapes = E85.Shapes(tmp)
    try:
        face, _combos = E85.sweep(app, shapes)
    finally:
        shapes.cleanup()

    keys: set = set()
    detail: dict = {}
    for (scene, rule), rec in face.items():
        bad = (rec["status"] is not None and rec["status"] >= 400) \
            or rec["avail"] is False or rec["raw"]
        if not bad:
            continue
        for p in sorted(rec["paths"] or ()):
            tail = p.rsplit(".", 1)[-1].rstrip("[]")
            keys.add(tail)
            detail.setdefault(tail, []).append((scene, rule, rec["status"]))
    return keys, {"n_err_cells": sum(
        1 for r in face.values()
        if (r["status"] is not None and r["status"] >= 400) or r["avail"] is False or r["raw"]),
        "by_key": {k: len(v) for k, v in sorted(detail.items())}}


def part_a(calls: list[dict]) -> dict:
    print("\n═══ A. 实测 ═══")
    by_kind: dict = {}
    for c in calls:
        by_kind.setdefault(c["kind"], []).append(c)
    print(f"  调用点 {len(calls)} 个：" + "／".join(
        f"{k} {len(v)}" for k, v in sorted(by_kind.items())))

    biz = by_kind.get("call", [])
    for c in biz:
        classify(c)
    modes: dict = {}
    shapes: dict = {}
    for c in biz:
        modes[c["mode"]] = modes.get(c["mode"], 0) + 1
        shapes[catch_shape(c)[0]] = shapes.get(catch_shape(c)[0], 0) + 1
    print(f"  失败处理形态：" + "／".join(f"{k} {v}" for k, v in sorted(modes.items())))
    print(f"  catch 体形态：" + "／".join(f"{k} {v}" for k, v in sorted(shapes.items())))

    print("\n  ── 逐条 ──")
    for c in sorted(biz, key=lambda x: (x["file"], x["line"])):
        sh, why = catch_shape(c)
        ks = ",".join(k for _e, k in read_keys(c)) or "—"
        flag = "  " if (c["mode"] != "none" and sh == "visible") else "⚠ "
        print(f"  {flag}{c['file']}:{c['line']:<5} {c['fn']:<10} "
              f"{c['mode']:<6} {sh:<8} keys={ks:<28} {why}")

    #: 封装体内那几处：它们的"失败处理"是 throw，另核它挂了什么。
    #:
    #: **`c["fn"]` 是调用点函数名（`fetch`），不是封装名**——第一版拿它去
    #: `_fn_span` 找 `function fetch(…)`，找不到就 `seg = ""`，于是四行全印
    #: 「⚠ 没有 throw / 挂 body — / 挂 status —」。**解析失败静默降级成"没有"**
    #: 正是 §2.5.65 立过的那条；而它只 print 不判定，连红都不会红。封装名走
    #: `WRAPPERS`，另立 B1b 判据兜住「解析不到」与「真的没有 throw」两种情形。
    print("\n  ── 封装内部（不判 B1/B2，另核两条）──")
    for c in by_kind.get("wrapper", []):
        fn = WRAPPERS.get(c["file"], c["fn"])
        src = (STATIC / c["file"]).read_text(encoding="utf-8")
        sp = _fn_span(src, fn)
        seg = src[sp[1]:sp[2]] if sp else ""
        has_throw = "throw" in seg
        hb = BODY_HOOK.search(seg) is not None
        hs = re.search(r"(?<!opts)(?<!options)\.\s*status\s*=", seg) is not None
        print(f"    {c['file']}:{c['line']:<5} {fn:<10} "
              f"{'把失败转成 rejected promise（throw）' if has_throw else '⚠ 没有 throw'}"
              f"　挂 body {'✓' if hb else '—'}　挂 status {'✓' if hs else '—'}")

    print("\n  ── 后端错误分支实测键面（复用 §2.5.85 的 280 格）──")
    keys, meta = err_face()
    print(f"    错误分支格数 {meta['n_err_cells']}；键 {len(keys)} 个：" + ", ".join(sorted(keys)))
    return {"calls": calls, "biz": biz, "by_kind": by_kind, "modes": modes,
            "shapes": shapes, "keys": keys, "meta": meta}


# ══════════════════════════════════════════════════════════════════
# B. 判据
# ══════════════════════════════════════════════════════════════════
def test_b1(rep) -> None:
    biz = rep["biz"]
    miss = [c for c in biz if c["mode"] == "none"]
    ok(not miss, "B1 每个业务调用点都有失败处理（链上 catch 或 await 在 try 内）",
       f"无失败处理 {len(miss)} 条：" + "；".join(
           f"{c['file']}:{c['line']}" for c in miss) if miss else
       f"实测 {len(biz)}/{len(biz)} 条都有（链上 catch {rep['modes'].get('chain', 0)}／"
       f"await-in-try {rep['modes'].get('try', 0)}）")


def test_b1b(rep) -> None:
    """四个请求封装必须把失败转成 rejected promise（体内有 `throw`）。

    体内没有 `throw` 的封装等于**把失败咽掉**：调用点的 `.catch` 永远不触发，
    而调用点又都老老实实写好了 catch —— 一条从头到尾都看不见的错误。这条判据
    也把「封装体解析不到」算成红，不让它降级成「没看见就当没有」（§2.5.65）。
    """
    bad = []
    for fname, fn in WRAPPERS.items():
        src = (STATIC / fname).read_text(encoding="utf-8")
        sp = _fn_span(src, fn)
        if not sp:
            bad.append(f"{fname}:{fn}（⚠ 封装体解析不到）")
            continue
        if "throw" not in src[sp[1]:sp[2]]:
            bad.append(f"{fname}:{fn}")
    ok(not bad, "B1b 四个请求封装都必须把失败转成 rejected promise（体内有 throw）",
       f"没有 throw：{bad}" if bad else f"实测 {len(WRAPPERS)}/{len(WRAPPERS)} 个封装体里都有 throw")


def test_b2(rep) -> None:
    bad = [(c, catch_shape(c)) for c in rep["biz"]
           if catch_shape(c)[0] == "console"]
    ok(not bad, "B2 失败处理不许只有 console（须写 DOM / alert / notice）",
       "；".join(f"{c['file']}:{c['line']} → {w}" for c, w in bad) if bad
       else f"实测 console-only 0 条（全库 console.error/warn {rep['meta'].get('console_n', 0)} 处）")


def test_b3(rep) -> None:
    """静默 catch（空体 / 只 return null）必须带理由。

    **不给白名单**：§2.5.67 的教训是"例外清单只写理由、脚本从不复核"等于永久豁免。
    本节的处置是把理由**钉在源码里**（注释贴在 catch 上或链上方 3 行内），
    改代码的人一定看得见，脚本也一定核得到。
    """
    bad = []
    silent = []
    for c in rep["biz"]:
        if catch_shape(c)[0] != "empty":
            continue
        silent.append(c)
        if not c["reason"]:
            bad.append(c)
    ok(not bad, "B3 静默 catch 必须有理由（注释贴在 catch 上或链上方 3 行内）",
       "；".join(f"{c['file']}:{c['line']}" for c in bad) if bad
       else f"实测静默 catch {len(silent)} 条，全部带理由")


def test_b4(rep) -> None:
    """catch 里读的错误键必须真出现在后端错误分支实测响应里。"""
    keys = rep["keys"]
    pairs: list[tuple[str, str]] = []
    for c in rep["biz"]:
        for expr, k in read_keys(c):
            pairs.append((f"{c['file']}:{c['line']}", expr, k))
    bad = [p for p in pairs if p[2] not in keys]
    ok(not bad, "B4 catch 读的错误键必须真出现在后端错误分支实测响应里",
       "；".join(f"{w} 读 {e}" for w, e, _k in bad) if bad
       else f"实测 {len(pairs)} 处读，键 {sorted({k for _w, _e, k in pairs})} "
            f"全部命中后端实测键面")

    #: `e.body.*` 那一档单列：它要求封装真的把响应体挂上了。
    #:
    #: **接收者要收窄**（§2.5.66 同族）：只有「**真有调用点在读** `e.body.*`」的
    #: 文件才要求它的封装挂 `e.body`。第一版无条件要求四个封装**都**挂——而
    #: `extract.js` 的 `send` 从不挂 body（它只挂 `message`），却**没有任何调用点
    #: 读 `e.body.*`**，那条"缺陷"是判据自己造出来的：拿一份没人用的能力去要求
    #: 一份没人调的封装。
    body_files = {w.split(":")[0] for w, e, _k in pairs if ".body." in e}
    hooks: list[tuple[str, bool | None]] = []
    for fname, fn in WRAPPERS.items():
        src = (STATIC / fname).read_text(encoding="utf-8")
        sp = _fn_span(src, fn)
        if not sp:
            #: 解析失败**不得静默降级成"没有"**（§2.5.65）：取不到封装体直接报红。
            hooks.append((f"{fname}:{fn}", None))
            continue
        seg = src[sp[1]:sp[2]]      #: `sp[1]:sp[2]` 是**函数体**；`sp[0]:sp[1]` 是签名
        hooks.append((f"{fname}:{fn}", BODY_HOOK.search(seg) is not None))
    broken = [h for h, y in hooks if y is None]
    ok(not broken, "B4b-0 四个请求封装都能解析出函数体（不得静默降级）",
       f"解析失败：{broken}" if broken else f"四个封装体全部取到（{len(hooks)}/4）")
    miss = [h for h, has in hooks if h.split(":")[0] in body_files and not has]
    ok(not miss, "B4b 凡有调用点读 `e.body.*` 的封装必须真的挂上 `e.body`",
       f"没挂 body 的封装：{miss}" if miss else
       (f"读 `e.body.*` 的文件 {sorted(body_files)}；适用封装 "
        + "／".join(f"{h}{'有' if y else '无'} body"
                    for h, y in hooks if h.split(":")[0] in body_files)
        if body_files else
        #: 「没打到」与「没有标记」必须分开说（§2.5.85）：0 处适用**不是绿**，
        #: 是"本轮没有适用对象"，这条判据的能力由 C4 注入自证。
        "本轮 0 处 `e.body.*` 读 ⇒ 无适用对象（判据能力由 C4 注入自证）"))


def test_b5(rep) -> dict:
    """B5 观察项（**不判红**）：后端给了 `hint`，前端在哪读它。

    §2.5.86 修 e2e 那次发现"下一步动作已经被后端移到 `hint`"——如果前端只显示
    `message`，用户拿到的就是一句没有方向的话（"数据库不可用"而不是"跑
    create_staging_db.py"）。**这条不判红**：`hint` 是设计给"库不可用"那条通道的
    （`available:false` 与 db chip），不是每条错误分支都必须展示。
    """
    reads = []
    for c in rep["biz"]:
        for _e, k in read_keys(c):
            if k == "hint":
                reads.append(f"{c['file']}:{c['line']}")
    #: 还有两处不在 catch 里、在**成功响应的降级分支**里读 `body.hint`
    for js in sorted(STATIC.glob("*.js")):
        txt = js.read_text(encoding="utf-8")
        for m in re.finditer(r"body\.hint|\bd\.hint|\.body\.hint", txt):
            reads.append(f"{js.name}:{_line_of(txt, m.start())}")
    uniq = sorted(set(reads), key=lambda s: (s.split(":")[0], int(s.split(":")[1])))
    print(f"  ○ B5（观察项，不判红）后端 `hint` 的前端消费点 {len(uniq)} 处：" + "、".join(uniq))
    return {"hint_reads": uniq}


def part_b(rep) -> dict:
    print("\n═══ B. 判据 ═══")
    #: 先补一个 console 计数给 B2 的说明行用
    rep["meta"]["console_n"] = sum(
        len(CONSOLE.findall(js.read_text(encoding="utf-8")))
        for js in sorted(STATIC.glob("*.js")))
    test_b1(rep)
    test_b1b(rep)
    test_b2(rep)
    test_b3(rep)
    test_b4(rep)
    out = test_b5(rep)
    return {**out, "console_n": rep["meta"]["console_n"]}


# ══════════════════════════════════════════════════════════════════
# C. 注入式自证
# ══════════════════════════════════════════════════════════════════
#: 判据标签**逐字**抄自 `ok()` 的调用处——`--marks` 报回的就是这个字符串。
_L_B1 = "B1 每个业务调用点都有失败处理（链上 catch 或 await 在 try 内）"
_L_B2 = "B2 失败处理不许只有 console（须写 DOM / alert / notice）"
_L_B3 = "B3 静默 catch 必须有理由（注释贴在 catch 上或链上方 3 行内）"
_L_B4 = "B4 catch 读的错误键必须真出现在后端错误分支实测响应里"
_L_B4B = "B4b 凡有调用点读 `e.body.*` 的封装必须真的挂上 `e.body`"


def _child_marks(*args) -> dict:
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--marks", *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
    for line in (r.stdout or "").splitlines():
        if line.startswith("MARKS "):
            return json.loads(line[6:])
    raise RuntimeError(f"子进程没打出 MARKS：stdout={r.stdout[-800:]!r} "
                       f"stderr={r.stderr[-800:]!r}")


def _inject_reds(edits: list, *args) -> tuple[set, str]:
    """改真实文件 → 跑子进程 → `finally` 恢复。锚点未命中则**先回滚再返回**。

    JS 在 `static/` 下，不是 `web/` 根（2586 那边注入的是 `*.py` 路由）。
    """
    saved: list[tuple[Path, str]] = []
    for fname, old, new in edits:
        path = DOC if fname == "01_在线采集.md" else STATIC / fname
        src = path.read_text(encoding="utf-8")
        if old not in src:
            for p, s in saved:
                p.write_text(s, encoding="utf-8")
            return set(), f"锚点未命中（{fname}）：{old!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        saved.append((path, src))
    try:
        marks = _child_marks(*args)
        return set(marks.get("names") or []), ""
    finally:
        for p, s in saved:
            p.write_text(s, encoding="utf-8")


#: `(标签, 期望转红的那一条, 注入面, [(文件名, old, new), …], 说明)`
INJECTS: list = [
    ("C1", _L_B1, "源码面",
     [("causes.js",
       "        return loadAll();\n      })\n      .catch((e) => {",
       "        return loadAll();\n      })\n      .then((e) => {")],
     "把 `causes.js:710` 的 `.catch(` 改成 `.then(`——**链上再无失败处理**。\n"
     "这个 catch 同时罩着 `causes.js:700`（`/api/causes/framework`）与 `:703`"
     "（`/api/filters`）两个调用点，**相隔 18 行**：探针阶段那条「往后 700 字符内找 "
     "`.catch`」的字符窗口判据，正是把这两条判成「无失败处理」的。本节改走 Promise 链\n"
     "边界后才判对——**链边界是真判据，字符窗口是假判据**。\n"
     "改后两条一起落进 `mode=none` ⇒ B1 报红（**一条判据**，`extra` 里列 2 个调用点）。\n"
     "**不打 B2/B3**：链上根本没 catch 了，谈不上「只有 console」或「静默」。\n"
     "**不打 B4**：没有 catch 就没有 catch 体可读键。"),
    ("C2", _L_B2, "源码面",
     [("overview.js",
       "        $('chartArea').textContent = '概览加载失败：' + e.message;",
       "        console.error(e);")],
     "把 `overview.js:787` 那句写 DOM 的提示**换成一句 `console.error`**——"
     "开发者看得见、用户看不见，正是 B2 要抓的形态。\n"
     "该 catch 体只有这一句（777-779），换掉后 `catch_shape` 判 `console` 档 ⇒ B2 红。\n"
     "**不打 B1**：链上照样有 catch。\n"
     "**不打 B3**：`console` 不是「静默」——B3 只管空体/只 return null。\n"
     "**不打 B4**：`console.error(e)` 里没有 `e.键` 读（`read_keys` 认的是 `e.xxx`，"
     "裸 `e` 不算）。"),
    ("C3", _L_B3, "源码面",
     [("review.js",
       "}).catch(function () { /* 历史读不到不影响主流程 */ });",
       "}).catch(function () { });")],
     "把 `review.js:207` 那条**有理由的**静默 catch 的注释抹掉——体仍是空的。\n"
     "⇒ 只剩「静默且无理由」⇒ B3 红。\n"
     "**不打 B2**：体里没有 `console.*`（`console` 档与 `empty` 档互斥）。\n"
     "**不打 B4**：空体没有键读。\n"
     "**这条同时也是一条回归**：它把「静默 catch 的豁免**钉在源码注释上**」这件事"
     "变成了可证伪的——注释一没，判据立刻报（§2.5.67 的教训：例外白名单只写理由、"
     "脚本从不复核，等于永久豁免）。"),
    ("C4", (_L_B4, _L_B4B), "源码面",
     [("extract.js",
       "'理论版本读取失败：' + e.message;",
       "'理论版本读取失败：' + e.body.detail;")],
     "**本节招牌注入**：把 `extract.js:207` 读的键从真实存在的 `message` 换成后端"
     "**从来没有过的** `detail`（实测 46 个错误分支键里无此键）。\n"
     "⇒ ① B4 红：键不在真值面里。\n"
     "⇒ ② B4b 红：`extract.js` 的封装 `send` **没挂 `e.body`**——它只 `throw new "
     "Error(b.message)`，`opts.body = JSON.stringify(...)` 挂的是 **fetch 的请求体**"
     "不是响应体。\n"
     "**两条说的是同一个缺陷形态的两个视角**（读了一个拿不到的键），"
     "归因是「同一件事」，不是互相遮蔽——两条在**基线**上都是绿的：\n"
     "  · B4b 基线无适用对象（全库 0 处 `e.body.*` 读）⇒ 它**有能力报红**这件事"
     "只能靠这条注入证明；\n"
     "  · B4 基线 4 个读全中真值面。\n"
     "**不打 B1/B2/B3**：链与 catch 体都不动。"),
]


def part_c(rep) -> dict:
    print("\n═══ C. 注入式自证 ═══")
    base = _child_marks()
    base_names = set(base.get("names") or [])
    print(f"  基线（未注入）：红 {len(base_names)} 条")
    out: dict = {"base": base}
    ok(not base_names, "基线必须是绿的（有红就没有可归因的基线）")
    if base_names:
        for n in sorted(base_names):
            print(f"      ✗ {n}")
        return out

    cases = list(INJECTS) + [
        ("C5", f"文档锚点：{DOC_NEEDLES[0]}", "文档面",
         [("01_在线采集.md", "\n### 2.5.87 ", "\n### 2.5.87x ")],
         "改文档里的本节标题 ⇒ 打 D 段那一条锚点；正文与源码都不动。"),
    ]

    for tag, want, face, edits, why in cases:
        names, note = _inject_reds(edits, "--marks")
        if note:
            ok(False, f"{tag} 注入锚点未命中", note)
            out[tag] = {"reds": [], "note": note}
            continue
        got = set(names)
        doc_reds = {n for n in got if n.startswith("文档锚点")}
        src_reds = got - doc_reds
        inner = doc_reds if face == "文档面" else src_reds
        #: `want` 允许给多条（元组/集合）——C4 就是**真实地**同时打红两条：
        #: 读了一个拿不到的键，B4（键不在真值面）与 B4b（封装没挂 body）是同一个
        #: 既成事实的两个独立视角，两条在基线上都绿 ⇒ 不互相遮蔽。
        #: 放宽的是**条数**，不是判据（§2.5.86）。
        want_set = set(want) if isinstance(want, (set, tuple, list)) else {want}
        ok(inner == want_set, f"{tag} 注入后{face}红的**恰好**是那一条",
           f"期望「{'、'.join(sorted(want_set))}」，"
           f"实测 {sorted(inner) if inner else '无红'}"
           + (f"（附带打红文档锚点 {len(doc_reds)} 条）"
              if face == "源码面" and doc_reds else ""))
        if inner != want_set:
            print(f"      · 说明：{why}")
        out[tag] = {"reds": sorted(got), "want": want, "face": face, "note": ""}
    return out


# ══════════════════════════════════════════════════════════════════
# D. 文档对账
# ══════════════════════════════════════════════════════════════════
def _doc_needles(rep) -> list[tuple[str, str]]:
    out = [(n, n) for n in DOC_NEEDLES]
    out.append(("调用点总数", f"调用点 **{rep['n_calls']}** 个"))
    out.append(("业务调用点", f"业务调用点 **{rep['n_biz']}** 个"))
    out.append(("封装数", f"请求封装 **{rep['n_wrap']}** 个"))
    out.append(("静默 catch", f"静默 catch **{rep['n_silent']}** 条"))
    out.append(("错误分支格数", f"错误分支 **{rep['meta']['n_err_cells']}** 格"))
    out.append(("错误分支键数", f"键面 **{rep['n_keys']}** 个"))
    return out


def part_d(rep) -> None:
    print("\n═══ D. 文档对账 ═══")
    src = DOC.read_text(encoding="utf-8")
    for label, needle in _doc_needles(rep):
        hit = needle in src
        print(f"  {'✓' if hit else '✗'} {label}")
        if not hit:
            FAILED.append(f"文档锚点：{label}")


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# 自检：判据自身的正反用例（不碰后端、不读仓库现状）
# ══════════════════════════════════════════════════════════════════
#: 用例里用来定位 `.catch` 的 match。**必须与 `CHAIN_STEP` 遵守同一个约定：
#: `m.end()` 落在那个 `(` 之后**——`_catch_body` / `_has_reason` 都直接把这个
#: 位置交给 `_skip_call(txt, i)`，而它按"`i` 指向 `(` 之后"起算括号深度。
#: 第一版写成 `r"\.\s*catch\b"`（`m.end()` 停在 `catch` 后、`(` 之前），于是
#: `_skip_call` 从 `(` 本身开始扫、深度多算一层、一路扫到片段末尾，`() => null)`
#: 变成 `() => null);`，尾部那个 `;` 挡住了 `rstrip(")")` ⇒ 判成 `other`。
#: **那是用例构造错了，不是判据报错**——自检的第一价值就是逼出这种"分不清是
#: 谁错"的时刻。
_CATCH_M = re.compile(r"\.\s*catch\s*\(")


def _self_rec(body: str, arg: str = "e", mode: str = "chain",
              txt: str = "") -> dict:
    return {"catch_arg": arg, "mode": mode, "catch_body": body, "txt": txt}


def self_test() -> tuple[int, int, list[str]]:
    """判据自己的正反用例 → `(用例数, 失败数, 失败说明)`。

    本节在 `body` 这一个词上叠了**三次**误判（读的时候把中间层当键、判挂载的
    时候把请求体当响应体、取段的时候把签名当函数体），所以下面每个坑都配**一对**
    用例——只测「该报的报了」证明不了判据分辨得出来，必须同时证明「不该报的不报」。
    §2.5.63 立的规矩：判据的宽度与强度决定结论的可信度。

    **断言里带「解析不到即判红」的用例**：解析失败时 `sp` 为 `None`，那里不能
    静默跳过（用例条数会随解析结果变——§2.5.64「用例条数与判据覆盖的格数不一致
    时，多出来的用例是空话」），故把 `None` 折成一个必然与期望相反的取值。
    """
    n = 0
    bad: list[str] = []

    def case(label: str, got, want) -> None:
        nonlocal n
        n += 1
        if got != want:
            bad.append(f"{label}：期望 {want!r}，实测 {got!r}")

    # ── read_keys：`e.body` 是中间层，不是响应键 ────────────────
    case("读键·`e.body`（不跟键名）不算读键",
         read_keys(_self_rec("$('x').textContent = e.body;")), [])
    case("读键·`e.body.hint` 算键 hint",
         read_keys(_self_rec("log(e.body.hint)")), [("e.body.hint", "hint")])
    case("读键·`e.message` 算键 message",
         read_keys(_self_rec("log(e.message)")), [("e.message", "message")])
    case("读键·catch 无参数名时一个键都不报",
         read_keys(_self_rec("log(e.message)", arg="")), [])

    # ── BODY_HOOK：挂响应体 ≠ 设请求体 ─────────────────────────
    case("挂载·`opts.body =` 是 fetch 请求体，不算挂载",
         bool(BODY_HOOK.search("opts.body = JSON.stringify(body);")), False)
    case("挂载·`Object.assign(…, { body })` 算挂载",
         bool(BODY_HOOK.search("Object.assign(new Error(m), { body })")), True)
    case("挂载·`e.body = body` 算挂载",
         bool(BODY_HOOK.search("e.body = body;")), True)

    # ── _fn_span：签名与函数体是两个段，取错一段判据就废 ───────
    src = "function f(a) {\n  throw new Error('x');\n}\n"
    sp = _fn_span(src, "f")
    case("取段·函数体段里有 throw（解析不到即判红）",
         "throw" in (src[sp[1]:sp[2]] if sp else ""), True)
    case("取段·签名段里没有 throw（解析不到即判红）",
         "throw" in (src[sp[0]:sp[1]] if sp else "throw"), False)

    # ── catch 形态：四档都要有名有姓，不许落进「其他」 ─────────
    def shape(js: str) -> str:
        m = _CATCH_M.search(js)
        arg, body = _catch_body(js, m)
        return catch_shape(_self_rec(body, arg))[0]

    case("形态·箭头简写 `() => null` 是 empty",
         shape(".catch(() => null);"), "empty")
    case("形态·空块 `() => {}` 是 empty", shape(".catch(() => {});"), "empty")
    case("形态·写 DOM 是 visible",
         shape(".catch((e) => { $('a').textContent = e.message; });"), "visible")
    case("形态·只有 console 是 console",
         shape(".catch((e) => { console.error(e); });"), "console")
    case("形态·不认识的体落 other，不落 empty",
         shape(".catch((e) => { tick(); });"), "other")

    # ── _has_reason：理由不许借 ────────────────────────────────
    def reason(js: str) -> bool:
        return _has_reason(js, _CATCH_M.search(js))

    case("理由·链上方一行注释算",
         reason("      //: 失败不该拖垮面板\n      .catch(() => null);"), True)
    case("理由·写在体内的注释算",
         reason(".catch(() => { /* 降级 */ });"), True)
    case("理由·隔 3 行以上的注释不算（不借别处的）",
         reason("// 这条是给上面用的\n\n\n\n\n.catch(() => null);"), False)
    case("理由·完全没有注释不算", reason(".catch(() => null);"), False)

    # ── handling_chain：从内往外找第一个带 catch 的容器 ────────
    nest = ("Promise.all([\n"
            "  fetchJson('/a'),\n"
            "  fetchJson('/b'),\n"
            "]).then((r) => render(r))\n"
            "  .catch((e) => { $('x').textContent = e.message; });\n")
    hc = handling_chain(nest, nest.index("fetchJson('/a')"), containers_in(nest))
    case("链·`Promise.all` 尾上的 catch 能接住内部调用点", hc is not None, True)

    nest2 = ("Promise.all([\n"
             "  jsonGet('/api/quality'),\n"
             "  jsonGet('/api/images').catch(() => null),\n"
             "]).then((r) => render(r))\n"
             "  .catch(() => {});\n")
    hc2 = handling_chain(nest2, nest2.index("jsonGet('/api/images')"),
                         containers_in(nest2))
    case("链·内外都有 catch 时取**内层**那个（有理由的降级不被顶掉）",
         hc2 is not None and hc2[0] == nest2.index("jsonGet('/api/images')"), True)

    hc3 = handling_chain(nest, nest.index("fetchJson('/b')"), containers_in(nest))
    case("链·同一个 `Promise.all` 里的第二个调用点也被接住",
         hc3 is not None and hc3[0] != nest.index("fetchJson('/b')"), True)

    solo = "const x = 1;\n"
    case("链·没有任何链容器时返回 None",
         handling_chain(solo, solo.index("1"), containers_in(solo)), None)

    return n, len(bad), bad


#: `--drift-test` 的三处注入：`(注入名, 自身源码里的锚点, 换成, 期望转红的那条用例)`。
#: 每处只把**一条用例的期望值**改反，别的一个字不动——这样"红了几条、红的是哪条"
#: 才可归因。注的是**自检用例**，不是主检判据（主检的注入自证在 C 段，每跑必做）。
_DRIFTS = [
    ("读键判反",
     'read_keys(_self_rec("log(e.message)")), [("e.message", "message")])',
     'read_keys(_self_rec("log(e.message)")), [("e.message", "MESSAGE")])',
     "读键·`e.message` 算键 message"),
    ("挂载判反",
     'BODY_HOOK.search("opts.body = JSON.stringify(body);")), False)',
     'BODY_HOOK.search("opts.body = JSON.stringify(body);")), True)',
     "挂载·`opts.body =` 是 fetch 请求体，不算挂载"),
    ("形态判反",
     'shape(".catch((e) => { console.error(e); });"), "console")',
     'shape(".catch((e) => { console.error(e); });"), "visible")',
     "形态·只有 console 是 console"),
]


def drift_test() -> int:
    """`--self-test` 自己的失败路径：改反一条期望，看它**恰好**转红那一条。

    一个永远返回 0 的自检不是判据，是装饰。C 段证明的是主检判据能报红，这一段
    证明的是**自检用例**能报红——两者不是同一件事。

    副本**必须落在 `scripts/` 里**：脚本用 `parents[1]` 定位仓库根，副本放临时
    目录会让 `from _common import …` 直接 ImportError——首版就踩了，三条注入全部
    退出码 1、stdout 里一个 `✗` 都没有，**看着像"判据没报红"，其实是脚本根本没
    跑起来**。这类"退出码非 0 但原因不是被测判据"的失败，只有打印出红的是哪几条
    才分得清（§2.5.67 起反复立的规矩）。
    """
    src = Path(__file__).resolve()
    text0 = src.read_text(encoding="utf-8")
    #: 替换范围**收窄到 `self_test()` 函数体内**：`_DRIFTS` 表自己就写在源码里，
    #: 表里那两个子串各含一份锚点 ⇒ 全文计数恒为 2，三条注入会全部报"锚点命中
    #: 2 次"（首版实测如此）。**自指的判据要限定在自己身上数**。
    i0 = text0.index("def self_test(")
    i1 = text0.index("_DRIFTS = [")
    seg0 = text0[i0:i1]
    nbad = 0
    for name, old, new, want in _DRIFTS:
        if seg0.count(old) != 1:
            print(f"✗ {name}：锚点在 `self_test()` 里命中 {seg0.count(old)} 次"
                  f"（须恰好 1）")
            nbad += 1
            continue
        p = src.parent / ("_drift_tmp_" + src.name)
        try:
            p.write_text(text0[:i0] + seg0.replace(old, new) + text0[i1:],
                         encoding="utf-8")
            r = subprocess.run([sys.executable, str(p), "--self-test"],
                               capture_output=True, text=True, cwd=str(src.parent))
        finally:
            p.unlink(missing_ok=True)
        labels = [l.strip().lstrip("✗ ").split("：")[0]
                  for l in r.stdout.splitlines() if l.strip().startswith("✗")]
        good = r.returncode == 1 and labels == [want]
        print(f"{'✓' if good else '✗'} {name}：退出码 {r.returncode}，"
              f"红 {labels}（期望 [{want!r}]）")
        if not good:
            nbad += 1
    print(f"\n{'✅' if not nbad else '❌'} 自检的失败路径 "
          f"{len(_DRIFTS) - nbad}/{len(_DRIFTS)}")
    return 1 if nbad else 0


def run() -> dict:
    calls = scan_calls()
    a = part_a(calls)
    b = part_b(a)
    return {**a, **b,
            "n_calls": len(calls),
            "n_biz": len(a["biz"]),
            "n_wrap": len(a["by_kind"].get("wrapper", [])),
            "n_silent": sum(1 for c in a["biz"] if catch_shape(c)[0] == "empty"),
            "n_keys": len(a["keys"])}


def main() -> int:
    if "--self-test" in sys.argv:
        n, nbad, bad = self_test()
        print("§2.5.87 判据自检（正反用例，不碰后端、不读仓库现状）\n")
        for b in bad:
            print(f"  ✗ {b}")
        print(f"\n{'✅' if not nbad else '❌'} 自检 {n - nbad}/{n} 通过")
        return 1 if nbad else 0

    if "--drift-test" in sys.argv:
        return drift_test()

    if "--marks" in sys.argv:
        try:
            r = run()
            part_d(r)
        except Exception as exc:            # noqa: BLE001
            print("MARKS " + json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            return 1
        print("MARKS " + json.dumps({"names": FAILED}, ensure_ascii=False))
        return 0

    print("§2.5.87 错误路径的前端消费面（后端报的错，前端读没读、有没有让用户看见）\n")
    rep = run()
    part_c(rep)
    part_d(rep)
    print("\n" + "═" * 60)
    if FAILED:
        print(f"❌ 有 {len(FAILED)} 项未通过：")
        for f in FAILED:
            print(f"  · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
