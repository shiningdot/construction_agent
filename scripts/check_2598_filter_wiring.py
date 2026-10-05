#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.98 中段三页的筛选接线：控件 ↔ 绑定 ↔ 后端能力。

09 号「尚未机器化核查」清单里**最后挂着的一条**：概览页与致因页的「能力 ↔ 使用」
有 `/api/filters` 可比（§八 8.2 的判据是 `chips 的 data-key 集合 == /api/filters
里非空列表键的集合`），而**中段三页（采集 / 提取 / 审核）没有那种接口**，原文写
「要靠人对着蓝图 01–03 逐个核」。

没有「可选值接口」不等于没有基准——**基准换成「后端该路由真读了哪些 query 键」**
（§2.5.82 的解析器早就能解，`impl_keys(ep)`，不写第二遍）。于是这条链有三节：

    页面控件  ──A──▶  JS 绑定  ──B──▶  后端读的键

**A 段（控件 ↔ 绑定）是本节独有的一格**：§2.5.82 的链条起点是**调用点**（JS 里
拼 query 的地方），它根本不看 HTML。而一个控件可以画了却从不被读（**死控件**——
用户设了值不生效），或 JS 读了一个页面上不存在的 id（`$(id)` 为 null）。这两种
失效**都没有症状**：不报错、不空白、控制台干净。

**B 段**与 §2.5.82 的 A1 同向不同源——那条从「调用点发的键」出发，这条从「控件
绑的键」出发。**两者相等**才说明「画出来的控件都接到了请求、请求里的键都来自
控件」；不等即中间有断层。这条交叉核是本节与 §2.5.82 的接口。

**C 段**把「后端读了、页面没画」的键逐条登记，**每条附蓝图锚点**——锚点须在文档
里逐字命中（可证伪，§2.5.67：只写理由不验证＝永久豁免）。

**D 段是链的终点**：静态面核不到「submit handler 有没有接上」（函数定义在、绑定也
在，但事件没监听上，用户按「查询」就完全不过滤，静态面全绿）。故真起服务、真
浏览器、每个控件设**探针值**、真点「查询」，断言请求 URL 里**键对、值也对**。

**E 段**文档对账 + 两处注入式自证（源码面 + 文档面，§2.5.68/§2.5.71 立的规矩：
只测源码面时文档判据被改坏了不会有任何用例转红）。

用法：
    .venv/bin/python3 scripts/check_2598_filter_wiring.py
    .venv/bin/python3 scripts/check_2598_filter_wiring.py --self-test
    .venv/bin/python3 scripts/check_2598_filter_wiring.py --drift-test

退出码 `0` = 全过，`1` = 有不过的。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402
from _serve import point_at, serve                            # noqa: E402

import check_2582_query_params as C2582                       # noqa: E402

WEB = ROOT / "construction_agent" / "web"
TPL = WEB / "templates"
JS = WEB / "static"
DOC01 = ROOT / "docs" / "web" / "01_在线采集.md"

# (页面, 表单 id, JS 文件, 路由, 重置按钮 id)
PAGES = [
    ("collect", "rawFilters", "collect.js", "/api/collect/raw", "rawReset"),
    ("extract", "resFilters", "extract.js", "/api/extract/results", "resReset"),
    ("review", "qFilters", "review.js", "/api/review/queue", "qReset"),
]

# 分页键：不是筛选维度，两边都扣掉再比
PAGING = {"page", "size"}

# C 段登记表：后端读了、页面上没画控件的键。
# 每条的值是**蓝图锚点**（须在文档里逐字命中，C2 核）。**这张表不许只写理由**——
# 每加一条就要能指到蓝图的哪一行声明了它。
GAPS = {
    ("/api/collect/raw", "task_id"):
        ("01_在线采集.md", "| `source_key` `task_id` | 来源过滤 |"),
    ("/api/extract/results", "review_status"):
        ("02_审核与提取.md",
         "| `task_id` / `review_status` / `text_type` / `relevance_verdict` | 筛选 |"),
    ("/api/review/queue", "accident_type"):
        ("03_暂存与人工审核.md",
         "| `task_id` / `text_type` / `accident_type` | 常规筛选 |"),
    ("/api/review/queue", "task_id"):
        ("03_暂存与人工审核.md",
         "| `task_id` / `text_type` / `accident_type` | 常规筛选 |"),
}

FAILED: list[str] = []
CHECKED: list[str] = []          # 本次跑过的全部判据标签（E7 用它核条数）


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print("  %s %s%s" % ("✓" if cond else "✗", label, ("   " + extra) if extra else ""))
    CHECKED.append(label)
    if not cond:
        FAILED.append(label)
    return bool(cond)


def note(label: str, extra: str = "") -> None:
    print("  · %s%s" % (label, ("   " + extra) if extra else ""))


# ══════════════════════════════════════════════════════════════════
# 解析（纯函数，`--self-test` 直接喂字符串验）
# ══════════════════════════════════════════════════════════════════
def strip_js_comments(s: str) -> str:
    """剥 `//` 与 `/* */`，字符串内的照留。

    **必须剥**：§2.5.73 的教训是注释里写一句「这里的颜色要跟 :root 一致」就把
    那个色号算进了"定义源"。本脚本扫的是 `f_*` 标识符，注释里提一句
    `// 注意 f_q 是搜索框` 就会被当成"这个控件被引用了"。
    """
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'`":
            q = c
            out.append(c)
            i += 1
            while i < n:
                if s[i] == "\\":
                    out.append(s[i:i + 2])
                    i += 2
                    continue
                out.append(s[i])
                if s[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def form_chunk(html: str, fid: str) -> str | None:
    """取 `<form id="fid">…</form>` 的体。"""
    m = re.search(r'<form[^>]*id="%s"[^>]*>(.*?)</form>' % re.escape(fid),
                  html, re.S)
    return m.group(1) if m else None


def controls_of(chunk: str) -> dict[str, str]:
    """表单里的**筛选控件**：`{id: tag}`。

    排除 `type` 为 submit/button/reset/hidden 的 `input`——它们是按钮不是控件。
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"<(select|input)\b([^>]*?)/?>", chunk):
        tag, attrs = m.group(1), m.group(2)
        idm = re.search(r'id="([^"]+)"', attrs)
        if not idm:
            continue
        tm = re.search(r'type="([^"]+)"', attrs)
        typ = tm.group(1) if tm else ""
        if tag == "input" and typ in ("submit", "button", "reset", "hidden"):
            continue
        out[idm.group(1)] = tag
    return out


def ctrl_names_in(js: str) -> set[str]:
    """JS 里出现的**全部** `f_*` 标识符。

    收宽一档是**故意的**：控件 id 在 JS 里有两个来源——直接字面量 `$('f_x')`
    与**对象字面量的键**（`RAW_PARAM = { f_x: 'key' }` 的表驱动）。首版只收前者，
    把 collect.js 整页判成死控件（3/5 假阳性）——与 §2.5.82 第 ⑩ 处「映射对象的
    键是对象的值」同族：**键名藏在数据结构里**。宽判据的方向是安全的（不会误报
    死控件），代价是可能漏报"拼出来的 id"——本仓库无此形态。

    **边界**：id 的命名约定是**全小写**（`f_` + `[a-z0-9_]`），含大写字母的
    id 不在正则射程内。放宽到大写会开始收 `f_XXX` 形态的常量名，得不偿失；
    代价是注入式自证**不能用大写 id**（实测用 `f_warnX` 时它被截成 `f_warn`，
    用例空转不报红）。**收窄换来准确、代价是覆盖变窄，两边的宽度都写出来**
    （§2.5.66）。
    """
    return set(re.findall(r"\b(f_[a-z][a-z0-9_]*)\b", js))


def key_specs(stmt: str) -> dict[str, str | None]:
    """一条语句里的候选**后端键** → 它的**常量值**（无则 `None`）。

    两种形态：`'key='` 字面量拼接、`set('key', …)`/`add('key', …)` 首实参。
    **常量值不是装饰**——`p.push('no_cause=1')` 的值 `1` 是**写死的**，不来自
    任何控件；D 段的期望值就得按 `1` 算而不是按控件值算（首版没区分，
    把 `no_cause` 判成"请求里少了这个键"）。
    """
    out: dict[str, str | None] = {}
    for m in re.finditer(r"'([a-z_][a-z0-9_]*)=([A-Za-z0-9_.-]*)'", stmt):
        out[m.group(1)] = m.group(2) or None
    for m in re.finditer(r"\b(?:set|get|has|add|append)\(\s*'([a-z_][a-z0-9_]*)'",
                         stmt):
        out.setdefault(m.group(1), None)
    return out


def key_literals(stmt: str) -> set[str]:
    return set(key_specs(stmt))


def trigger_value(js: str, cid: str) -> str | None:
    """控件被 JS `=== 'V'` 比较时的那个 `V`。

    采集/提取/审核三页里只有一个这种控件（`f_cause`）：它的存在与否**不看自己
    有没有值，而看值等不等于 `'1'`**——`if ($('f_cause').value === '1')
    p.push('no_cause=1')`。给它设一个随便的非空值，那个键根本不会进 URL，
    判据就会报"少了 no_cause"——**而这是判据自己不懂这个控件**。
    """
    for pat in (r"""\$\(\s*['"]%s['"]\s*\)\s*\.value\s*===?\s*['"]([^'"]*)['"]""",
                r"""['"]([^'"]*)['"]\s*===?\s*\$\(\s*['"]%s['"]\s*\)\s*\.value"""):
        m = re.search(pat % re.escape(cid), js)
        if m:
            return m.group(1)
    return None


def ctrl_refs(stmt: str) -> set[str]:
    """一条语句里 `$('f_x')` 形态的控件引用。"""
    return set(re.findall(r"""\$\(\s*['"](f_[a-z0-9_]+)['"]\s*\)""", stmt))


def bindings(js: str, ids: set[str]) -> dict[str, set[str]]:
    """解出 `{控件 id: {它绑定的后端键}}`。三页三种写法都要解得出：

    * 形态 A **对象字面量表**：`RAW_PARAM = { f_source: 'source_key', … }`（采集页）
    * 形态 B **同语句**：`add('task_id', $('f_task').value)`（提取页）
    * 形态 C **变量中转**：`var st = $('f_status').value;` … `p.push('review_status=' + st)`
      （审核页）——桥是**变量名**，两条语句分别建桥与用桥。
    """
    b: dict[str, set[str]] = {cid: set() for cid in ids}

    # 形态 A：对象字面量 `f_x: 'key'`（只收值在引号里的）
    for m in re.finditer(r"\b(f_[a-z0-9_]+)\s*:\s*'([a-z_][a-z0-9_]*)'", js):
        if m.group(1) in b:
            b[m.group(1)].add(m.group(2))

    stmts = [s for s in js.split(";") if s.strip()]

    # 形态 C 的桥：`var V = … $('f_x') …`
    bridge: dict[str, set[str]] = {}
    for st in stmts:
        for m in re.finditer(
                r"\bvar\s+([A-Za-z_$][\w$]*)\s*=[^;]*?"
                r"\$\(\s*['\"](f_[a-z0-9_]+)['\"]\s*\)", st):
            bridge.setdefault(m.group(1), set()).add(m.group(2))

    for st in stmts:
        ks = key_literals(st)
        if not ks:
            continue
        for c in ctrl_refs(st):                       # 形态 B
            if c in b:
                b[c] |= ks
        for v, cids in bridge.items():                # 形态 C
            if re.search(r"\b%s\b" % re.escape(v), st):
                for c in cids:
                    if c in b:
                        b[c] |= ks
    return b


def reset_ids(js: str, btn: str) -> list[str]:
    """重置按钮 handler 体里出现的控件 id（数组清单形态与逐条赋值形态都收）。

    **这是同一份控件清单的第二次书写**（§2.5.66/§2.5.73 的"第二份副本"族）：
    加一个控件忘了往重置清单里加，重置就会漏掉它。本节只硬核**清单里的 id 必须
    是页面真实控件**（写错了一个不存在的 id，`$(id)` 为 null 会当场抛），
    覆盖是否完整只作定量列出。
    """
    m = re.search(r"\$\(\s*['\"]%s['\"]\s*\)\s*\.addEventListener\(" % re.escape(btn), js)
    if not m:
        return []
    body = C2582._brace_body(js, js.find("{", m.end()))
    if not body:
        return []
    ids = re.findall(r"['\"](f_[a-z0-9_]+)['\"]", body)
    ids += re.findall(r"""\$\(\s*['"](f_[a-z0-9_]+)['"]\s*\)""", body)
    seen: list[str] = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return seen


def resolve_jsfile(name: str) -> str:
    return strip_js_comments((JS / name).read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════
# 实测：三页的三方数据
# ══════════════════════════════════════════════════════════════════
def measure() -> dict:
    """一次把 A/B/C 三段要的数据都取齐。"""
    from construction_agent.web.factory import create_app
    app = create_app()
    ep_of = dict(C2582.get_routes(app))

    pages: dict[str, dict] = {}
    for page, fid, jsf, route, btn in PAGES:
        html = (TPL / ("%s.html" % page)).read_text(encoding="utf-8")
        chunk = form_chunk(html, fid)
        ids = controls_of(chunk) if chunk else {}
        js = resolve_jsfile(jsf)
        named = ctrl_names_in(js)
        bind = bindings(js, set(ids))
        back, why = C2582.impl_keys(ep_of.get(route, ""))
        bound_keys = set().union(*bind.values()) if bind else set()
        # 写死的键值（`'no_cause=1'` 里的 `1`）：D 段的期望值要按它算，不按控件值
        consts: dict[str, str] = {}
        for st in [s for s in js.split(";") if s.strip()]:
            for k, v in key_specs(st).items():
                if v is not None:
                    consts.setdefault(k, v)
        # D 段的探针值：被 `=== 'V'` 比较的控件用 V，其余用 `P<控件 id>`
        probes = {cid: (trigger_value(js, cid) or ("P" + cid)) for cid in ids}
        pages[page] = {
            "form": fid, "js": jsf, "route": route, "btn": btn,
            "controls": ids, "named": named, "bind": bind,
            "back": back, "back_why": why, "bound_keys": bound_keys,
            "consts": consts, "probes": probes,
            "reset": reset_ids(js, btn),
            "unbound": sorted(c for c, ks in bind.items() if not ks),
            "bad_bind": sorted({k for ks in bind.values() for k in ks} - back),
            "missing_ctrl": sorted((back - PAGING) - bound_keys),
            "phantom": sorted(named - set(ids)),
            "dead": sorted(i for i in ids if i not in named),
            "bad_reset": sorted(i for i in reset_ids(js, btn) if i not in ids),
        }

    # §2.5.82 的「前端发的键」（按路由）——**不写第二遍解析**，直接引它的账
    rep = C2582.build_report()
    sends = {row["rule"]: set(row["sends"]) - PAGING for row in rep["table"]}
    return {"pages": pages, "rep": rep, "sends": sends}


# ══════════════════════════════════════════════════════════════════
# A 段：控件 ↔ 绑定（本节独有的一格）
# ══════════════════════════════════════════════════════════════════
def part_a(m: dict) -> None:
    print("\n── A 段：页面控件 ↔ JS 绑定 ──")
    total = 0
    for page, d in m["pages"].items():
        total += len(d["controls"])
    # 元判据：解析面必须是空的（解出 0 个控件时"0 违规"是假的）
    ok(total == 16, "A0 解析面非空：三页共解出 16 个筛选控件", "实测 %d" % total)

    for page, d in m["pages"].items():
        n = len(d["controls"])
        ok(not d["dead"], "[%s] A1 每个控件都被 JS 引用（无死控件）" % page,
           "控件 %d 个 / 死控件 %s" % (n, d["dead"] or "无"))
        ok(not d["phantom"], "[%s] A2 JS 引用的 id 都在页面上存在" % page,
           "幽灵 id %s" % (d["phantom"] or "无"))
        ok(not d["bad_reset"], "[%s] A3 重置清单里的 id 都是真实控件" % page,
           "清单 %s" % (d["reset"] or "无"))
    note("重置覆盖面（同一份控件清单的第二处书写，只定量不判红）")
    for page, d in m["pages"].items():
        cover = sorted(set(d["controls"]) - set(d["reset"]))
        if not d["reset"]:
            note("  %s" % page, "用 form.reset()，无清单")
        else:
            note("  %s" % page, "清单 %d 个，未覆盖 %s"
                 % (len(d["reset"]), cover or "无"))


# ══════════════════════════════════════════════════════════════════
# B 段：绑定键 ↔ 后端能力（与 §2.5.82 交叉核）
# ══════════════════════════════════════════════════════════════════
def part_b(m: dict) -> None:
    print("\n── B 段：控件绑定的键 ↔ 后端该路由读的键 ──")
    for page, d in m["pages"].items():
        ok(not d["unbound"], "[%s] B1 每个控件都解得出它绑的键" % page,
           "解不出 %s" % (d["unbound"] or "无"))
        ok(not d["bad_bind"], "[%s] B2 绑定的键后端都读（否则筛选静默失效）" % page,
           "越界 %s" % (d["bad_bind"] or "无"))

    print("  交叉核（本节从控件算的键集 vs §2.5.82 从前端调用点算的发键集）")
    for page, d in m["pages"].items():
        mine = d["bound_keys"] - PAGING
        theirs = m["sends"].get(d["route"], set())
        same = mine == theirs
        ok(same, "[%s] B3 两处算出的键集相等" % page,
           ("都 %d 个" % len(mine)) if same
           else "控件侧∖调用点侧 %s ／ 调用点侧∖控件侧 %s"
                % (sorted(mine - theirs), sorted(theirs - mine)))


# ══════════════════════════════════════════════════════════════════
# C 段：能力 ↔ 控件（登记面，双向核）
# ══════════════════════════════════════════════════════════════════
def part_c(m: dict) -> None:
    print("\n── C 段：后端读了、页面没画控件的键 ──")
    found: set[tuple[str, str]] = set()
    for page, d in m["pages"].items():
        for k in d["missing_ctrl"]:
            found.add((d["route"], k))

    ok(found == set(GAPS), "C1 实测缺口集 == 登记表（双向：不多不少）",
       "实测 %d 条 / 登记 %d 条%s"
       % (len(found), len(GAPS),
          "" if found == set(GAPS) else "  差 %s" % sorted(found ^ set(GAPS))))

    bad_anchor: list[str] = []
    for (route, key), (doc, anchor) in sorted(GAPS.items()):
        txt = (ROOT / "docs" / "web" / doc).read_text(encoding="utf-8")
        if anchor not in txt:
            bad_anchor.append("%s → %s 的锚点在 %s 里找不到" % (route, key, doc))
    ok(not bad_anchor, "C2 每条登记的蓝图锚点在文档里逐字命中",
       "；".join(bad_anchor) if bad_anchor else "%d 条全中" % len(GAPS))

    note("逐条")
    for (route, key), (doc, _a) in sorted(GAPS.items()):
        note("  %s" % route, "缺 `%s`（蓝图 %s 已声明）" % (key, doc))

    # 三页的账（只列不判红）：缺口数 = 后端筛选键数 − 控件绑定键数。
    # **不设成判据**——它与 C1 在固定样本上等价（两侧任一退化，C1 的集合相等
    # 先红），方向相同、归因不唯一（§2.5.93）。摆出来是为了让"4 条"这个数
    # 有规模感：三条路由一共 **17** 个「路由 × 键」筛选位，页面画了 **13** 个。
    # 按键去重后是 **11** 个后端键 / **10** 个页面键——所以"4 条缺口"不是 4 个
    # 不同的键（`task_id` 在两条路由上各缺一次）；三页全无的键只有
    # `accident_type` 一个。（这两个数**按「路由 × 键」计**，与 C1 的
    # `(route, key)` 元组口径一致；写成去重口径会让两处对不上。）
    # ⚠️ 初版这条注释写的是"后端一共给了 13 个筛选维度，页面画了 9 个"——
    # **实测 11 / 10，注释里的数字没人核过**（§2.5.65「口径没写明」的同族：
    # 注释里的数是"看着像"写下的，不进任何判据的射程）。
    note("三页账目")
    for page, d in m["pages"].items():
        note("  %s" % page, "后端筛选键 %d / 控件绑定 %d / 缺口 %d  →  %s"
             % (len(d["back"] - PAGING), len(d["bound_keys"] - PAGING),
                len(d["missing_ctrl"]), sorted(d["missing_ctrl"]) or "无"))


# ══════════════════════════════════════════════════════════════════
# E 段：文档对账（本节写进 01 号的数字 ↔ 实测）
# ══════════════════════════════════════════════════════════════════
DOC01 = ROOT / "docs" / "web" / "01_在线采集.md"
SEC_TITLE = "### 2.5.98 中段三页的筛选接线"
SEC_END = "\n## 三、目标架构"
ZH = {"collect": "采集", "extract": "提取", "review": "审核"}


def sec_text() -> str:
    """本节在 01 号文档里的正文（到下一个一级标题为止）。"""
    txt = DOC01.read_text(encoding="utf-8")
    i = txt.find(SEC_TITLE)
    if i < 0:
        return ""
    j = txt.find(SEC_END, i)
    return txt[i:j] if j > i else txt[i:]


def part_e(m: dict) -> None:
    """核本节正文里的每一个数都来自实测。

    **不是**"文档里有没有写过 16"这种定性核对——锚点由**实测值拼出来**，
    实测一变、文档没跟，锚点立刻落空（§2.5.60：判据两端要同源）。

    ⚠️ **故意不设**"过期口径不得出现"的反向判据：本节第七节「坑 3」为了说清
    修了什么，必须**逐字引述**那个错值（"13 个筛选维度"）。一条正向判据分不出
    「作出声明」与「引述声明」——§2.5.96 在别处立过同一条，此处按同样的理由
    放弃这一格，而不是把文档改得读不通。
    """
    print("\n── E 段：文档对账（本节写进 01 号的数字 ↔ 实测）──")
    sec = sec_text()
    ok(len(sec) > 2000, "E0 本节在 01 号文档里存在且完整", "节内 %d 字" % len(sec))
    if not sec:
        return

    pages = m["pages"]
    order = [p for p, *_ in PAGES]
    ctrls = {p: len(d["controls"]) for p, d in pages.items()}

    # E1 控件总数与逐页数（A 段的账，写进第三节）
    n_all = sum(ctrls.values())
    got = " / ".join("%s %d" % (ZH[p], ctrls[p]) for p in order)
    want = "三页共解出 **%d** 个筛选控件（%s，均含每页条数 `f_size`）" % (n_all, got)
    ok(want in sec, "E1 本节写的控件数与实测一致",
       "%d 个（%s）" % (n_all, got))

    # E2 三页账目表（第五节的表）：后端筛选键 / 控件绑定 / 缺口逐行
    rows, bad2 = [], []
    for p in order:
        d = pages[p]
        miss = d["missing_ctrl"]
        cell = "（%s）" % "、".join("`%s`" % k for k in miss) if miss else ""
        row = "| %s | %d | %d | %d%s |" % (
            ZH[p], len(d["back"] - PAGING), len(d["bound_keys"] - PAGING),
            len(miss), cell)
        rows.append(row)
        if row not in sec:
            bad2.append(row)
    ok(not bad2, "E2 三页账目表逐行与实测一致",
       "；".join(bad2) if bad2 else "%d 行全中" % len(rows))

    # E3 「路由 × 键」口径的两个数
    n_back = sum(len(pages[p]["back"] - PAGING) for p in order)
    n_bound = sum(len(pages[p]["bound_keys"] - PAGING) for p in order)
    want3 = "按「路由 × 键」计：**%d** 个筛选位，页面画了 **%d** 个。" % (n_back, n_bound)
    ok(want3 in sec, "E3 「路由 × 键」口径的两个数与实测一致",
       "%d 个筛选位 / %d 个有控件" % (n_back, n_bound))

    # E4 去重口径的两个数（原文里跨了行，故用正则）
    all_back: set[str] = set()
    all_bound: set[str] = set()
    for d in pages.values():
        all_back |= d["back"] - PAGING
        all_bound |= d["bound_keys"] - PAGING
    pat = r"\*\*%d\*\*\s+个后端键\s*/\s*\*\*%d\*\*" % (len(all_back), len(all_bound))
    ok(re.search(pat, sec) is not None, "E4 去重口径的两个数（后端键 / 页面键）与实测一致",
       "%d / %d" % (len(all_back), len(all_bound)))

    # E5 缺口表四行（路由 × 键 × 蓝图出处），逐行
    bad5 = []
    for (route, key), (doc, _a) in sorted(GAPS.items()):
        row = "| `%s` | `%s` | `%s` |" % (route, key, doc)
        if row not in sec:
            bad5.append(row)
    ok(not bad5, "E5 缺口表逐行与登记的出处一致",
       "；".join(bad5) if bad5 else "%d 行全中" % len(GAPS))

    # E6 去重口径的推论：三页全无的键
    only = sorted(all_back - all_bound)
    want6 = "三页**全无**的键只有 %s" % "、".join("`%s`" % k for k in only)
    ok(want6 in sec, "E6 「三页全无的键」与实测一致", want6)

    # E7 判据总条数：文档写了"A 10 / B 9 / C 2 / D 12 / E 8 = 41"。
    # 跑到这里已入账的是 A/B/C/E 的前 7 条（E7 自己尚未入账），D 段 12 条按
    # 「3 页 × 4 条」推导（D 段在本节流程里最后跑，此时还没跑）。
    # 这条抓的是「加了判据忘改文档」——§2.5.71「加一处时别处断言的期望集合
    # 要跟着走」的另一面：**判据条数也是一处会被加判据改动的数字**。
    total = len(CHECKED) + 1 + len(PAGES) * 4
    ok(total == 41, "E7 五段判据总条数与本节写的一致",
       "实测 %d 条（A/B/C/E %d + D 段 %d）" % (total, len(CHECKED) + 1, len(PAGES) * 4))

    note("本节耗时（实测值写进文档，不作判据——每次跑都会变）", "见第九节")


# ══════════════════════════════════════════════════════════════════
# D 段：运行时真值面（真点「查询」，看请求 URL）
# ══════════════════════════════════════════════════════════════════
SET_CTRL_JS = """(e, v) => {
  if (e.tagName === 'SELECT') {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    e.appendChild(o);
  }
  e.value = v;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
}"""


def set_ctrl(page, cid: str, value: str) -> None:
    """给控件设探针值。

    `select` 不能直接 `select_option`：有些下拉是**接口填的**（采集页的
    `f_source` 靠 `loadSources()` 填 options），工作库为空时它可能只有一个空
    选项，设不上值。统一改成**往 select 里临时插一个 option 再选它**——与后端
    无关，值一定进得去。

    直接改 `.value` 而不走 `fill()` 有两个好处：对 `select`/`input` 一视同仁，
    且不必猜输入框有没有 `maxlength` 或 `pattern` 把值拦下（`fill` 走的是
    真实的键入路径，会被前端校验吃掉）。
    """
    page.eval_on_selector("#" + cid, SET_CTRL_JS, value)


def part_d(m: dict, args) -> None:
    print("\n── D 段：运行时真值面（真点「查询」，抓请求 URL）──")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ok(False, "D0 playwright 可用",
           "需 .venv/bin/pip install playwright && .venv/bin/playwright install chromium")
        return

    server, base = serve(args.port)
    print("  服务：%s（端口 %d）" % (base, args.port))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
            for page_name, d in m["pages"].items():
                pg = ctx.new_page()
                urls: list[str] = []
                pg.on("request", lambda r, u=urls: u.append(r.url))
                pg.goto("%s/%s" % (base, page_name),
                        wait_until="networkidle", timeout=30000)
                pg.wait_for_timeout(800)

                probes = d["probes"]
                for cid in sorted(d["controls"]):
                    set_ctrl(pg, cid, probes[cid])

                # 每个键的**期望值**：写死的键（`no_cause=1`）用常量，其余用
                # 它自己那个控件的探针值。**首版这里是错的**——`want` 按控件 id
                # 索引，却在按后端键索引的地方取用，`want.get('source_key')` 恒为
                # None，三页 D3 全报红而真值面其实全对（坐标系错位，§2.5.87
                # 「判据指向了错的格」同族）。
                want = {}
                for cid, ks in d["bind"].items():
                    for k in ks:
                        if k in PAGING:
                            continue
                        want[k] = d["consts"].get(k, probes[cid])

                before = len(urls)
                pg.click("#%s button[type=submit]" % d["form"])
                pg.wait_for_timeout(1200)
                new = [u for u in urls[before:] if d["route"] + "?" in u]
                if not ok(len(new) >= 1, "[%s] D1 点「查询」真的发了请求" % page_name,
                          "新增匹配请求 %d 条" % len(new)):
                    pg.close()
                    continue
                q = dict(re.findall(r"([A-Za-z_]+)=([^&]*)", new[-1].split("?", 1)[1]))

                got = set(q) - PAGING
                exp = d["bound_keys"] - PAGING
                ok(got == exp, "[%s] D2 请求里的键集 == 绑定的键集" % page_name,
                   ("%d 个全中" % len(exp)) if got == exp
                   else "多 %s ／ 少 %s" % (sorted(got - exp), sorted(exp - got)))

                wrong = ["%s=%s（应 %s）" % (k, q[k], want.get(k))
                         for k in sorted(exp & set(q)) if q[k] != want.get(k)]
                ok(not wrong, "[%s] D3 每个键的值都对得上它的来处" % page_name,
                   "值不对 %s" % (wrong or "无"))

                ok(q.get("page") == "1",
                   "[%s] D4 提交后回第一页（page=1）" % page_name,
                   "实测 page=%s" % q.get("page"))
                pg.close()
            browser.close()
    finally:
        server.shutdown()


# ══════════════════════════════════════════════════════════════════
# 自证
# ══════════════════════════════════════════════════════════════════
# 注入表：(标签, 目标文件, old, new, **期望红的恰好是哪些标签**)
#
# 三条设计约束（都是踩过坑的）：
#  * **锚点须在该文件里出现恰好 1 次**——否则替换落到别处，用例静默失效
#    （§2.5.90；实测第 3 条最初写成 `id="f_q"` 就命中 1 次，够用）。
#  * **反例注入只能追加、不能改写**（§2.5.97）——第 3 条是在 form 里**插**一个
#    没人引用的控件，而不是把某个控件改名（改名会连带 A2/B3/C1 一起红，
#    归因就不唯一了）。
#  * **注入值必须落在 id 命名约定里**（本轮实测踩到）：第 2 条最初写
#    `f_warnX`，而判据的正则是 `f_[a-z0-9_]+`——**大写 X 被截掉**，`f_warnX`
#    被读成 `f_warn`（一个真实控件），于是"红的 = []"、用例空转。**注入值
#    出了判据的覆盖范围，红的就是"没测到"而不是"没能力"**（§2.5.64
#    「多出来的用例是空话」）。
#  * **两个注入面都要接**（§2.5.68）——前三条改模板与 JS（源码面），第四条改
#    **蓝图文档**（文档面）。只测源码面的话，C2 那条判据被改坏了不会有任何
#    用例转红。
DRIFTS = [
    ("绑错键：collect.js 的 f_status 绑成 'statuss'",
     JS / "collect.js", "f_status: 'status',", "f_status: 'statuss',",
     # B3 **不**红——它比的两侧（本节控件绑定 / §2.5.82 调用点）读的是同一份
     # JS，键名一改两边同时变。红的是 C1：后端有 `status`、控件绑不到它了，
     # 于是"缺口"多一条、与登记表不等。
     # E2/E6 是**正确的连带**（E 段加上来之后才出现的，§2.5.71「加一处时别处
     # 断言的期望集合要跟着走」）：那两条核的正是"缺口"，缺口多一条它们就该红。
     {"[collect] B2 绑定的键后端都读（否则筛选静默失效）",
      "C1 实测缺口集 == 登记表（双向：不多不少）",
      "E2 三页账目表逐行与实测一致",
      "E6 「三页全无的键」与实测一致"}),
    ("重置清单写了不存在的 id：review.js 的 $('f_warn') → $('f_warnz')",
     JS / "review.js", "$('f_warn').value = '';", "$('f_warnz').value = '';",
     # A2 连带：`f_warnX` 进了 JS 的 `f_*` 名单而页面没有它（正确连带——
     # 这正是"改 id 忘改别处"的真实后果，两个方向各报一条）。
     {"[review] A2 JS 引用的 id 都在页面上存在",
      "[review] A3 重置清单里的 id 都是真实控件"}),
    ("追加一个没人引用的控件：collect.html 的 form 里插 f_bogus",
     TPL / "collect.html", 'id="f_q"', 'id="f_bogus"><input id="f_q"',
     # B1 连带：没人引用的控件自然也解不出绑定键。E1 也是**正确的连带**——
     # 它核的就是控件总数，多插一个控件就该红（§2.5.71）。
     {"A0 解析面非空：三页共解出 16 个筛选控件",
      "[collect] A1 每个控件都被 JS 引用（无死控件）",
      "[collect] B1 每个控件都解得出它绑的键",
      "E1 本节写的控件数与实测一致"}),
    ("URL 里多一个不来自控件的常量键：collect.js 的 rawQuery 加 p.set('zzz','1')",
     JS / "collect.js", "p.set('size', $('f_size').value);",
     "p.set('size', $('f_size').value); p.set('zzz', '1');",
     # **B3 唯一的失败路径**。前三条注入都动不了它（两侧同源），若不给它自己的
     # 用例，"B3 恒真"就无法排除——§2.5.77「判据在多数样本上恒真就等于没有」。
     {"[collect] B3 两处算出的键集相等"}),
    ("蓝图锚点被删：03 号文档的 review/queue 参数行少了 accident_type",
     ROOT / "docs" / "web" / "03_暂存与人工审核.md",
     "| `task_id` / `text_type` / `accident_type` | 常规筛选 |",
     "| `task_id` / `text_type` | 常规筛选 |",
     {"C2 每条登记的蓝图锚点在文档里逐字命中"}),
    ("本节正文的数字被改：01 号文档的控件总数 16 → 15",
     DOC01, "三页共解出 **16** 个筛选控件（采集 5 / 提取 6 / 审核 5",
     "三页共解出 **15** 个筛选控件（采集 5 / 提取 6 / 审核 5",
     # **E 段自己的失败路径**。前五条里只有第 5 条动文档，而它喂的是 C2；
     # E 段（文档对账）若从没被改红过，"E1 与实测一致"就只是一句话
     # （§2.5.77）。只动总数不动逐页数，故 E2–E6 不受影响、归因唯一。
     {"E1 本节写的控件数与实测一致"}),
]


def self_test() -> int:
    """判据本身的正反用例。**跑在纯函数上**，不起服务、不碰真实文件。"""
    print("── --self-test：解析器的正反用例 ──")
    bad: list[str] = []

    def case(name: str, got, want) -> None:
        good = got == want
        print("  %s %s%s" % ("✓" if good else "✗", name,
                             "" if good else "   got=%r want=%r" % (got, want)))
        if not good:
            bad.append(name)

    case("注释里的控件名不算引用",
         ctrl_names_in(strip_js_comments("// $('f_x') gone\nvar a = 1;")), set())
    case("字符串里的 // 不被当注释",
         ctrl_names_in(strip_js_comments("var u = 'http://a/f_x';")), {"f_x"})
    case("按钮不算筛选控件",
         set(controls_of('<input type="submit" id="go">'
                         '<select id="f_a"></select>')), {"f_a"})
    case("no_cause 的常量值被认出",
         key_specs("p.push('no_cause=1')"), {"no_cause": "1"})
    case("拼接式键的常量为 None",
         key_specs("p.push('q=' + x)"), {"q": None})
    case("set 形态", key_specs("p.set('size', v)"), {"size": None})
    case("触发值：控件在左", trigger_value("if ($('f_c').value === '1') {}", "f_c"), "1")
    case("触发值：控件在右", trigger_value("if ('1' === $('f_c').value) {}", "f_c"), "1")
    case("无比较则无触发值", trigger_value("$('f_c').value", "f_c"), None)
    case("绑定形态 A：对象字面量表",
         bindings("var M = { f_a: 'ka' };", {"f_a"}), {"f_a": {"ka"}})
    case("绑定形态 A：只收表里有的控件",
         bindings("var M = { f_a: 'ka' };", {"f_b"}), {"f_b": set()})
    case("绑定形态 B：同语句",
         bindings("add('kb', $('f_b').value);", {"f_b"}), {"f_b": {"kb"}})
    case("绑定形态 C：变量桥",
         bindings("var v = $('f_c').value; p.push('kc=' + v);", {"f_c"}),
         {"f_c": {"kc"}})
    case("变量桥没接上就不算",
         bindings("var v = $('f_d').value;", {"f_d"}), {"f_d": set()})
    case("重置清单：数组形态",
         reset_ids("$('b').addEventListener('click', function () {"
                   " ['f_a', 'f_b'].forEach(function (id) { $(id).value = ''; }); });",
                   "b"), ["f_a", "f_b"])
    case("重置清单：逐条赋值形态",
         reset_ids("$('b').addEventListener('click', function () {"
                   " $('f_a').value = 'x'; });", "b"), ["f_a"])

    print("\n%s  %d 条不过" % ("✅ 全部通过" if not bad else "❌ 有不过的", len(bad)))
    return 1 if bad else 0


def drift_test() -> int:
    """往被测文件里注入漂移，断言红的**恰好**是哪几条。

    「退出码 0」只有在脚本有能力报 1 时才可信（§2.5.64）——这一步就是给那条
    能力留下可复跑的证据。断言到**标签集合**而不只是"非 0"：§2.5.68 立的规矩，
    否则判据之间的连带遮蔽分不清。
    """
    print("── --drift-test：注入式自证 ──")
    bad: list[str] = []
    for name, path, old, new, want in DRIFTS:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        if n != 1:
            print("  ✗ %s\n      锚点命中 %d 次（应为 1），用例失效" % (name, n))
            bad.append(name)
            continue
        try:
            path.write_text(src.replace(old, new), encoding="utf-8")
            reds = set(run_static(quiet=True))
        finally:
            path.write_text(src, encoding="utf-8")
        good = reds == want
        print("  %s %s" % ("✓" if good else "✗", name))
        if not good:
            print("      红的   = %s\n      期望   = %s"
                  % (sorted(reds), sorted(want)))
            bad.append(name)

    print("\n%s  %d 条不过" % ("✅ 全部通过" if not bad else "❌ 有不过的", len(bad)))
    return 1 if bad else 0


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════
def run_static(quiet: bool = False) -> list[str]:
    """A/B/C/E 四段（不起服务、不跑浏览器）。`--drift-test` 只跑这一段。"""
    global FAILED, CHECKED
    FAILED = []
    CHECKED = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m = measure()
        part_a(m)
        part_b(m)
        part_c(m)
        part_e(m)
    if not quiet:
        print(buf.getvalue(), end="")
    return FAILED


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # 端口约定见 scripts/e2e/README.md：5095–5100、5110、5128、5133/5134、
    # 5138/5139、5140/5141、5150、5152 都已被占，本节取 5160。
    ap.add_argument("--port", type=int, default=5160)
    ap.add_argument("--main-db", default=None)
    ap.add_argument("--staging-db", default=None)
    ap.add_argument("--static-only", action="store_true",
                    help="只跑 A/B/C（不起服务）")
    ap.add_argument("--self-test", action="store_true",
                    help="只跑解析器的正反用例（纯函数，不起服务）")
    ap.add_argument("--drift-test", action="store_true",
                    help="注入式自证：改真实文件，断言红的恰好是哪几条")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    main_db = args.main_db or main_db_path()
    staging_db = args.staging_db or str(main_repo_root() / "data" / "caiems_staging.db")
    point_at(main_db, staging_db)

    if args.drift_test:
        return drift_test()

    global FAILED
    FAILED = []
    m = measure()
    part_a(m)
    part_b(m)
    part_c(m)
    part_e(m)
    if not args.static_only:
        part_d(m, args)

    n = len(FAILED)
    print("\n%s  %d 条不过" % ("✅ 全部通过" if not n else "❌ 有不过的", n))
    # 机器可读出口：注入式自证要断言「红的**恰好**是哪几条」，解析中文输出行太脆
    print("MARKS %s" % {"reds": FAILED})
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
