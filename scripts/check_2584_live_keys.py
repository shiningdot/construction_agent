#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.84 真响应体的键路径面：用运行时真值复核静态解出的键树。

## 来路

§2.5.83（读接口的响应面）把契约四格矩阵闭合了，但它留了三条边界，本节逐条去碰：

| §2.5.83 的边界 | 本节怎么处理 |
|---|---|
| 「**静态解析，不发一个请求**」 | 反过来——**真打每条只读 GET 路由**，拿响应 JSON 当基准 |
| 「**第三级超出射程**」（`data.fields.summary`、`d.raw.source_key` 都不在比对里） | 真值面是**全深度**的（递归到叶子），射程随之取消 |
| 「B1 的 11 条是"解不出"不是"错了"，这类路由的键**一个都没比**」 | 真值面不依赖解析器能力，解不解得出都能比 |

更根本的动机：§2.5.83 的三处解析缺陷**全是"报了假缺陷才发现"的**——静态解析器
自己既当运动员又当裁判。本节给它配一个**独立真值**：运行时真正返回了什么。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 前端读的键路径（全深度）⊆ 真值面的键路径 | 读了个不存在的键 → 永远 `undefined`，静默 | 红 |
| A2 | 每条「有调用点」的 `/api/` 路由**必须有真值面** | 打不出 JSON 的路由被静默跳过 ⇒ A1 是"我没比"（§2.5.67） | 红 |
| A3 | 闭式：调用点 = 比过 + 宿主为空核不了 + 提取不出 | "两头都不报"地静默溜过 | 红 |
| B1 | 宿主为空 ⇒ **核不了**的路径（空数组 / 接口不可用） | ——（必须列出，不是"通过"） | 定量 |
| B2 | 前端路径**提取不出**的调用点 | ——（判据的解析能力缺口，不是"没问题"） | 定量 |
| B3 | 真值返回、前端**从不读**的深层路径 | 造了没人用 vs 前端读漏 | 定量 |
| B4 | 静态（§2.5.83 的键树）↔ 真值的对照 | 静态**虚报** / 静态**缺口** | 定量 |
| B5 | 打不出真响应的路由（**必须列出**） | 静默跳过 ⇒ A1 变成"我没比" | 定量 |

**B4 是本节对 §2.5.83 的正面回答**：静态说"有"而真值没有的 = 虚报；静态"解不出"
而真值有键的 = 缺口。两者都不判红（真值面受**当前数据量**影响），但数字必须列出来——
"静态解析器错了多少"这个问题，此前从没有独立真值能回答。

## 「核不了」与「核了没有」要分开

工作库（`caiems_staging.db`）在本环境里 **12 张表有 9 张是 0 行**（`raw_accidents` /
`staging_accidents` / `staging_causes` / `collect_sources` / `collect_tasks` /
`extract_tasks` / `promote_log` / `promote_plans` / `raw_accident_assets`），
有数据的只有 `image_manifest` 1,771 行、`staging_metadata` 2 行与 SQLite 内部的
`sqlite_sequence` 1 行。

⚠️ 那个 **1,771 是 `STAGING_DB`（下方 `main_repo_root()` 派生 ⇒ 主仓库）** 的数，
而本脚本**驱动的是 `create_app()`**，它按 `settings.DATA_DIR`（`__file__` 相对）
解析 ⇒ 读的是**本 checkout** 那份工作库（`image_manifest` 693 行，§2.5.94）。
本节不用 `image_manifest` 取值造参数，故两个数差 1,078 行**不影响任何判据**；
写在这里是为了不让下一个读它的人以为两边是同一个文件。所以
`/api/collect/raw`、
`/api/review/queue` 这类接口真返回的 `data` 就是个空数组——**它元素的键无从核起**。
这类路径进 B1 **显式列出**，不判红、也不算通过（§2.5.73：宿主取不到要判红、
不能降级成空集合；本章的落地形态是"核不了就明说核不了"）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root             # noqa: E402
from _serve import point_at                                  # noqa: E402
import check_2580_request_body as C                          # noqa: E402
import check_2582_query_params as Q                          # noqa: E402
import check_2583_get_response as G                          # noqa: E402

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
MAIN_DB = main_db_path()
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

#: 锚点句用**本节独有**的整行标题。§2.5.83 的教训：与别节同族的措辞会让"锚点被删"
#: 用例恒真（判据的强度随语料增长而衰减），故从第一版起就写整行、且不含别节标题的子串。
DOC_NEEDLES = ["### 2.5.84 展示层（续）：真响应体的键路径面——**运行时真值 ↔ 前端读的路径**"]

#: 本节**自己的**方法名名单，在共享 `SKIP` 上补齐。
#:
#: **为什么不直接改共享 `SKIP`**：`SKIP` 被 §2.5.81 / §2.5.83 共用，往里加名字会让
#: 那两节已封存的 B3 计数变小——那是"改共享源要复跑别节"（§2.5.83 的教训）的又一次
#: 兑现。本节只在这里补齐，别节的结论一个字不动。
METHODS = {
    "flatMap", "reduce", "some", "every", "find", "findIndex", "indexOf",
    "includes", "concat", "sort", "reverse", "trim", "split", "replace",
    "toFixed", "toString", "keys", "values", "entries", "hasOwnProperty",
    "toLowerCase", "toUpperCase", "charAt", "substring", "substr", "padStart",
    "startsWith", "endsWith", "repeat", "match", "test", "exec", "getTime",
    "toISOString", "isArray", "from", "assign", "now", "parse", "stringify",
    "abs", "max", "min", "round", "floor", "ceil", "random", "getElementById",
    "push", "pop", "shift", "unshift", "splice", "addEventListener",
}
SKIP = G.SKIP | METHODS

# ══════════════════════════════════════════════════════════════════
# 真值面：真打每条只读 GET 路由
# ══════════════════════════════════════════════════════════════════
def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _sample_ids() -> dict[str, str]:
    """给路由里的 `<...>` 变量名造**真**参数。

    与 §2.5.76 有一处关键差别：那边把所有占位符一律换成 `aid`（它只关心 SQL 有没有
    执行到），本节要的是**真响应体**——参数错了拿到的是错误响应，键面目全非。
    所以按变量名映射，且**表空就不造**（`None` ⇒ 该路由本环境没有可核样本）。
    """
    out: dict[str, str] = {}
    con = _ro(MAIN_DB)
    row = con.execute("SELECT id FROM accidents ORDER BY id LIMIT 1").fetchone()
    out["accident_id"] = row[0] if row else None
    con.close()
    if not STAGING_DB.exists():
        return out
    st = _ro(STAGING_DB)
    for tbl, key in [("raw_accidents", "raw_id"), ("staging_accidents", "stage_id"),
                     ("collect_tasks", "task_id"), ("extract_tasks", "task_id"),
                     ("promote_plans", "plan_id")]:
        try:
            r = st.execute(f'SELECT id FROM "{tbl}" LIMIT 1').fetchone()
        except sqlite3.Error:
            r = None
        out[key] = r[0] if r else None
    st.close()
    return out


def walk(obj, prefix: str, out: set, empty: set) -> None:
    """递归收键路径；数组取**所有元素**的并集，空数组记进 `empty`（核不了）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out.add(p)
            walk(v, p, out, empty)
    elif isinstance(obj, list):
        if not obj:
            empty.add(prefix)
        for el in obj:
            walk(el, prefix + "[]", out, empty)


def live_face(do_get) -> tuple[dict, dict, dict]:
    """→ `(rule → 键路径集合, rule → 空宿主集合, rule → 实打路径)`。"""
    ids = _sample_ids()
    paths: dict[str, set] = {}
    empties: dict[str, set] = {}
    urls: dict[str, str] = {}
    for rule in sorted({str(r) for r in do_get("url_map") if "GET" in r.methods}):
        if not rule.startswith("/api/") or "<path:" in rule:
            continue
        url, ok = rule, True
        for var in re.findall(r"<([^>]*)>", rule):
            v = ids.get(var)
            if v is None:
                ok = False
                break
            url = url.replace(f"<{var}>", str(v))
        if not ok:
            paths[rule] = None            # 无样本 ⇒ 该路由真值面缺失
            continue
        r = do_get("get", url)
        j = r.get_json(silent=True)
        if j is None:
            paths[rule] = None            # 打不出 JSON ⇒ 同"缺失"，A2 判红
            urls[rule] = f"{url} → HTTP {r.status_code} 非 JSON"
            continue
        urls[rule] = url
        got: set = set()
        emp: set = set()
        walk(j, "", got, emp)
        paths[rule] = got
        empties[rule] = emp
    return paths, empties, urls


# ══════════════════════════════════════════════════════════════════
# 前端面：调用点 → 全深度键路径（带前缀链）
# ══════════════════════════════════════════════════════════════════
def _fn_params(full: str, name: str) -> list[str]:
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(([^)]*)\)", full)
    if not m:
        m = re.search(r"(?:const|let|var)\s+" + re.escape(name) +
                      r"\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", full)
    if not m:
        m = re.search(r"(?:const|let|var)\s+" + re.escape(name) +
                      r"\s*=\s*(?:async\s*)?([A-Za-z_$][\w$]*)\s*=>", full)
        if m:
            return [m.group(1)]
    return [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []


def paths_in(scope: str, name: str, prefix: str) -> set[str]:
    """`name` 在 `scope` 里被读的全深度路径（数组元素归一成 `[]`）。

    `prefix` 是**这个形参在响应体里的位置**（形参本身在顶层 ⇒ `''`；
    `const d = body.data` 之后 `d` 的前缀是 `data`）。
    """
    out: set[str] = set()
    if not name:
        return out
    pre = (prefix + ".") if prefix else ""
    rp = re.escape(name)
    # 数组元素：`name.a.b.map(it => it.K)` / `.forEach(it => …)` / `.filter(function (x) …)`
    #
    # 回调形参要**两种写法都收**（本节第四个坑）：首版只认 `\(?\s*([A-Za-z_$][\w$]*)`，
    # 于是 `d.images.filter(function (x) { return x.analyzed; })` 把 `function`
    # 当成了形参名，`it` 成了 `function` ⇒ 这条链一个键都收不到，B3 因此虚高
    # （`data.images[].analyzed` 被报成"前端一次都不读"，而 `accident.js:549`
    # 明明在读）。改后箭头与 `function` 两种形态都认，且**要求箭头或 `function`**
    # ——`filter(Boolean)` 这种没有形参的调用不再产出假 it。
    for m in re.finditer(
            rf"\b{rp}((?:\.[A-Za-z_$][\w$]*)+?)\s*\."
            rf"(?:map|forEach|filter|some|every|reduce)\s*\(\s*"
            rf"(?:function\s*\(\s*([A-Za-z_$][\w$]*)|\(?\s*([A-Za-z_$][\w$]*)"
            rf"\s*\)?\s*=>)",
            scope):
        seg, it = m.group(1).lstrip("."), (m.group(2) or m.group(3))
        if not seg or not it:
            continue
        for k in re.findall(rf"\b{re.escape(it)}\.([A-Za-z_$][\w$]*)", scope):
            if k not in SKIP:
                out.add(f"{pre}{seg}[].{k}")
    # `for (const x of name.a.b)` / `for (const [i, x] of ...)`
    for it, arr in re.findall(
            rf"for\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s+of\s+"
            rf"{rp}((?:\.[A-Za-z_$][\w$]*)+)", scope):
        seg = arr.lstrip(".")
        for k in re.findall(rf"\b{re.escape(it)}\.([A-Za-z_$][\w$]*)", scope):
            if k not in SKIP:
                out.add(f"{pre}{seg}[].{k}")
    # 普通点号链（含 `name.a[0].b` 的索引访问，归一成 `[]`）
    #
    # **末尾 token 与中间 token 用两套规则**（本节踩出来的第二个坑）：
    # `SKIP` 是**方法名/属性名**名单，它在链的**末尾**是对的（`d.items.length`
    # 的 `length` 不是键），但在**中间**会吃掉同名的**真键**——实测
    # `data.filter.min_count` 被砍成 `data.min_count`、A1 报出一条假缺陷
    # （`causes.py:288` 的 `data.filter` 是真键，`min_count` 在它下面）。
    # 中间 token 的判据改成**看紧跟其后的字符**：`filter(` 是调用 ⇒ 链到此为止；
    # `filter.` 是取值 ⇒ 它是键。§2.5.66「判据必须收窄接收者」的又一形态。
    for m in re.finditer(
            rf"\b{rp}((?:\s*(?:\.\s*[A-Za-z_$][\w$]*|\[\s*\d+\s*\]))+)", scope):
        raw = m.group(1)
        toks = list(re.finditer(r"\.\s*([A-Za-z_$][\w$]*)|\[\s*\d+\s*\]", raw))
        chain: list[str] = []
        lead_idx = False                             # 开头的数字索引：`years[0].key`
        for i, tok in enumerate(toks):
            if tok.group(1) is None:                 # 数字索引
                if chain:
                    chain[-1] += "[]"
                elif i == 0:
                    # `years[0].key` 的 `[0]` 落在**形参/别名自己**身上（chain 还空），
                    # 归一成 `[]` 挂到 prefix 上——否则会读成 `year.key` 而真值是
                    # `year[].key`，A1 又报假缺陷（本节第三个坑）。
                    lead_idx = True
                continue
            name = tok.group(1)
            after = scope[m.start(1) + tok.end():m.start(1) + tok.end() + 1]
            if after == "(":                         # 方法调用 ⇒ 链到此为止
                break
            last = (i == len(toks) - 1)
            if last and name in SKIP:                # 末尾的方法/属性名，不是键
                continue
            chain.append(name)
        while chain and chain[-1].endswith("[]"):
            chain.pop()                              # 结尾的索引无键可读，丢掉
        if chain:
            join_ = pre[:-1] + "[]." if (lead_idx and pre) else pre
            out.add(join_ + ".".join(chain))
    return out


def bound_paths(full: str, scope: str, p: str, prefix: str = "",
                depth: int = 0, seen: frozenset = frozenset()) -> set[str]:
    """从响应体形参 `p` 出发，跟别名与转发，收全深度路径。

    三条链，全是 §2.5.83 已登记形态的**加深版**：
      ① `const d = body.data`（别名）⇒ `d` 的前缀是 `data`；
      ② `renderX(d)`（转发）⇒ 被调函数体里同名形参的前缀一样；
      ③ `(body.data || {}).KEY`（就地兜底）⇒ 前缀仍归 `data`。
    """
    if depth > 4:
        return set()
    out = paths_in(scope, p, prefix)
    rp = re.escape(p)

    def join(rest: str) -> str:
        rest = rest.lstrip(".")
        if not rest:
            return prefix
        return f"{prefix}.{rest}" if prefix else rest

    # ① 别名
    #
    # `rest` 那个 `*` 是**贪婪**的 ⇒ 会把方法名一起吃进来：实测
    # `const years = ov.year.filter((y) => y.count > 0)` 的 rest 成了
    # `.year.filter`，于是 prefix 变 `year.filter`、路径读成
    # `year.filter.key`——A1 报出本节最后一条假缺陷。修法与 219 行同一条
    # 规则：**逐段看紧跟字符**，`(` ⇒ 是调用，链到此为止。
    for m in re.finditer(
            r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)"
            r"((?:\.[A-Za-z_$][\w$]*)*)", scope):
        alias, base, rest = m.group(1), m.group(2), m.group(3)
        if base != p:
            continue
        keep: list[str] = []
        for m2 in re.finditer(r"\.([A-Za-z_$][\w$]*)", rest):
            pos = m.start(3) + m2.end()
            if pos < len(scope) and scope[pos] == "(":
                break
            keep.append(m2.group(1))
        out |= bound_paths(full, scope, alias, join(".".join(keep)),
                           depth + 1, seen)
    # ③ 就地兜底：`(p.data || {}).KEY` / `(p || {}).KEY`
    for base, rest in re.findall(
            rf"\(\s*{rp}((?:\.[A-Za-z_$][\w$]*)*)\s*\|\|\s*\{{}}\s*\)"
            rf"((?:\.[A-Za-z_$][\w$]*)+)", scope):
        chain = [x for x in rest.lstrip(".").split(".") if x not in SKIP]
        if chain:
            out.add(join(base) + "." + ".".join(chain))
    # ② 转发：`fn(p)` / `fn(p.data)`
    for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(([^()]*)\)", scope):
        fname, argstr = m.group(1), m.group(2)
        if fname in SKIP or fname == p:
            continue
        for i, a in enumerate([x.strip() for x in argstr.split(",")]):
            if not (a == p or a.startswith(p + ".")):
                continue
            key = (fname, i)
            if key in seen:
                continue
            ps = _fn_params(full, fname)
            body = Q.fn_body(full, fname)
            if not ps or not body or i >= len(ps):
                continue
            # 实参可能是表达式（`renderCost(body.data || {})`）——只取**开头那段
            # 合法点号链**当前缀。首版直接 `a[len(p):]`，于是 `data || {}` 整个
            # 当了前缀，A1 报出 8 条路径名里带 `||` 的假缺陷（§2.5.66）。
            rest = re.match(r"(?:\s*\.\s*[A-Za-z_$][\w$]*)*", a[len(p):]).group(0)
            out |= bound_paths(full, body, ps[i],
                               join(rest), depth + 1, seen | {key})
    return out


def fe_paths(reads_fe) -> list[dict]:
    """29 处 GET 调用点 → 各自的键路径集合。"""
    out: list[dict] = []
    for c in reads_fe:
        full = (STATIC / c["file"]).read_text(encoding="utf-8")
        loc = G.locate(full, full, c["line"])
        p, scope = loc.get("param") or "", loc.get("scope") or ""
        paths: set[str] = set()
        if p and scope:
            paths = bound_paths(full, scope, p)
            if loc.get("unwrap") == "data":
                # 段里已经 `.then((r) => r.data)` 剥过一层（§2.5.83 的 `unwrapped`）
                paths = {(f"data.{x}" if x else "data") for x in paths}
        out.append({**c, "param": p, "why": loc.get("why", ""), "paths": paths})
    return out


# ══════════════════════════════════════════════════════════════════
# 比对
# ══════════════════════════════════════════════════════════════════
def host_empty(path: str, empty: set) -> str:
    """路径的宿主是不是空数组／空结构 ⇒ 核不了。"""
    for e in empty:
        if path == e or path.startswith(e + ".") or path.startswith(e + "[]"):
            return e
    return ""


def static_says_maybe(path: str, tree) -> bool:
    """静态面（§2.5.83 解出的键树）能不能**否定**这条路径的存在。

    `False` = 静态面确证"这个键不在这里"（可以据此判红）；
    `True` = 静态面解不出 / 树里没有这一段 ⇒ **不能否定**，只能进定量。

    **为什么必须有这一层**：真值面是**一条运行路径**，静态面是**全部分支**，
    两者互有盲区。实测 `/api/extract/pending` 的顶层 `available`——
    表不存在时 `unavailable_payload` 返回它、表存在时正常返回里没有它；
    本节真打的是后者 ⇒ 真值说"没有"，但它**不是缺陷**（前端写 `!== false`
    正是为这条路留的）。判红因此收窄为**两面都说没有**。
    """
    node = tree
    for s in path.split("."):
        if node is None:
            return True                       # 解不出 ⇒ 不能否定
        if not isinstance(node, dict):
            return False                      # 已知不是字典 ⇒ 后面不可能有键
        if s not in node:
            return False                      # 静态确证：这个键不在这里
        node = node[s]
    return True


def compare(live, empties, fe, trees, eps, wrap_keys) -> dict:
    a1: list[str] = []          # 两面都说没有 ⇒ 真缺陷
    a1_struct: list[dict] = []  # 同一批的（文件, 路径），供 `--marks` 断言用
    b1: list[str] = []          # 宿主为空 ⇒ 核不了
    b2: list[str] = []          # 前端路径提取不出
    b3: list[str] = []          # 真值有、前端不读的深层路径
    b4: list[str] = []          # 真值没有、静态说有 ⇒ 分支差异（不是缺陷）
    b5: list[str] = []          # 打不出真响应的路由（必须列出）
    n_compared = n_uncoreable = n_noextract = 0
    n_wrapped = 0
    seen_rules: set = set()

    for h in fe:
        rule = h.get("rule") or ""
        if not rule:
            continue
        seen_rules.add(rule)
        got = live.get(rule)
        if got is None:
            b5.append(f"{h['file']}:{h['line']} 调用 {rule}——该路由本环境"
                      f"**打不出真响应**（无可用样本参数或非 JSON）⇒ 这处调用点的键一个都没比")
            continue
        if h["why"]:
            n_noextract += 1
            b2.append(f"{h['file']}:{h['line']} {rule} 响应体形参定位失败：{h['why']}")
            continue
        if not h["paths"]:
            n_noextract += 1
            b2.append(f"{h['file']}:{h['line']} {rule} 路径提取为空（判据解析能力缺口）")
            continue
        emp = empties.get(rule, set())
        tree = trees.get(eps.get(rule)) or {}
        for path in sorted(h["paths"]):
            tail = path.rsplit(".", 1)[-1]
            if tail in wrap_keys:
                # 共享封装读的可选键（`hint` / `message` / `ok`）：实现只在**出错时**
                # 才返回它们，真值面正常情况下必然没有。§2.5.83 的同一处豁免。
                n_wrapped += 1
                continue
            e = host_empty(path, emp)
            if e:
                n_uncoreable += 1
                b1.append(f"{h['file']}:{h['line']} {rule} 读 `{path}`——"
                          f"宿主 `{e}` 本次为空（核不了，不是通过）")
                continue
            n_compared += 1
            if path in got or path + "[]" in got:
                continue
            if static_says_maybe(path, tree):
                b4.append(f"{h['file']}:{h['line']} {rule} 读 `{path}`——真响应本次"
                          f"没有，但静态面显示出现在别的分支里（分支差异，不是缺陷）")
            else:
                a1.append(f"{h['file']}:{h['line']} {rule} 读 `{path}`，"
                          f"真响应里没有、静态解出的键树里也没有")
                a1_struct.append({"file": h["file"], "line": h["line"],
                                  "rule": rule, "path": path})

    # B3：真值返回、前端一次都不读的**深层**路径（>= 3 段，两层 §2.5.83 已记过）
    fe_all: set = set()
    for h in fe:
        fe_all |= set(h["paths"])
    for rule, got in sorted(live.items()):
        if got is None:
            continue
        for path in sorted(got):
            if path.count(".") < 2:
                continue
            if path in fe_all or any(path.startswith(x + ".") for x in fe_all):
                continue
            b3.append(f"{rule} 真值有 `{path}`，前端一次都不读")

    return {"a1": a1, "a1_struct": a1_struct,
            "b1": b1, "b2": b2, "b3": b3, "b4": b4, "b5": b5,
            "n_compared": n_compared, "n_uncoreable": n_uncoreable,
            "n_noextract": n_noextract, "n_wrapped": n_wrapped,
            "rules_with_calls": len(seen_rules),
            "reds": a1}


# ══════════════════════════════════════════════════════════════════
# 段
# ══════════════════════════════════════════════════════════════════
FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return cond


def collect_all():
    """复用 §2.5.83 的调用点与路由（同一份绑定，不重写第二遍——§2.5.76）。"""
    base = G.collect_all()
    routes, reads_fe = base[0], base[3]
    return routes, reads_fe


def bind_rules(routes, reads_fe) -> list[dict]:
    """调用点 → 路由（与 §2.5.83 同一套 `rule_re` 匹配），并带上路由的 endpoint。"""
    api = [(rule, ep) for _m, rule, ep in routes if str(rule).startswith("/api/")]
    out = []
    for h in reads_fe:
        hit = ep = ""
        for rule, e in api:
            if C.rule_re(rule).match(h["path"]):
                hit, ep = rule, e
                break
        out.append({**h, "rule": hit, "ep": ep})
    return out


def run() -> dict:
    point_at(str(MAIN_DB), str(STAGING_DB))
    from construction_agent.web.factory import create_app

    app = create_app()
    c = app.test_client()

    def do_get(what, url=None):
        if what == "url_map":
            return app.url_map.iter_rules()
        return c.get(url)

    live, empties, urls = live_face(do_get)
    routes, trees, _whys, reads_fe, wraps = G.collect_all()
    wrap_keys: set = set()
    for ks in wraps.values():
        wrap_keys |= ks
    fe = bind_rules(routes, fe_paths(reads_fe))
    eps = {h["rule"]: h["ep"] for h in fe if h["rule"]}
    rep = compare(live, empties, fe, trees, eps, wrap_keys)
    rep["fe"] = fe
    rep["live"] = live
    rep["empties"] = empties
    rep["urls"] = urls
    rep["trees"] = trees
    rep["eps"] = eps
    rep["wrap_keys"] = sorted(wrap_keys)
    return rep


def part_a(rep) -> None:
    live = rep["live"]
    ok_rules = [r for r, v in live.items() if v is not None]
    miss = [r for r, v in live.items() if v is None]
    allp: set = set()
    for v in live.values():
        if v:
            allp |= v
    print("═══ A. 实测 ═══")
    print(f"  GET 路由 {len(live)} 条：真响应拿到 {len(ok_rules)} 条、"
          f"拿不到 {len(miss)} 条")
    for r in miss:
        print(f"    · {r}  {rep['urls'].get(r, '本环境无可用样本参数')}")
    print(f"  真值面的键路径合计 {len(allp)} 条"
          f"（三层及以上 {len([x for x in allp if x.count('.') >= 2])} 条）")
    fe = rep["fe"]
    npaths = sum(len(h["paths"]) for h in fe)
    print(f"  前端调用点 {len(fe)} 处 → 提取出键路径 {npaths} 条")
    print(f"  比过 {rep['n_compared']} 条路径／核不了 {rep['n_uncoreable']} 条／"
          f"提取不出 {rep['n_noextract']} 处调用点")


def part_b(rep) -> None:
    print("\n═══ B. 判据 ═══")
    a1 = rep["a1"]
    ok(not a1, f"A1 前端读的键，真响应与静态键树**两面都没有**：{len(a1)} 条"
               if a1 else "A1 前端读的键，真响应与静态键树**两面都没有**：0 条")
    for x in a1[:12]:
        print(f"      · {x}")

    # A2（元判据，§2.5.67）：打不出真响应的路由**必须显式列出**，不得静默跳过。
    # 注意判据是"列出"而不是"必须打得出来"——工作库空是本环境的**状态**，
    # 不是缺陷；静默跳过才是缺陷。
    miss = {r for r, v in rep["live"].items() if v is None}
    # 前端**有调用点**的无真值面路由——只有这些才可能"该列而未列"。
    # 首版拿 `listed == miss` 判，实测报红：`miss` 5 条里
    # `/api/collect/tasks/<task_id>` 与 `/api/extract/tasks/<task_id>`
    # **前端根本没有调用点**（没有读取面，自然进不了 B5）⇒ 判据的接收者
    # 没收窄（§2.5.66）。改为只核交集，同时把无调用点的另列出来。
    miss_used = miss & {h["rule"] for h in rep["fe"] if h["rule"]}
    listed = {h["rule"] for h in rep["fe"] if h["rule"] and
              rep["live"].get(h["rule"]) is None}
    ok(listed == miss_used and len(rep["b5"]) == sum(
        1 for h in rep["fe"] if h["rule"] and rep["live"].get(h["rule"]) is None),
       f"A2 有调用点且打不出真响应的 {len(miss_used)} 条路由**全部**进了 B5 "
       f"显式列出（{len(rep['b5'])} 处调用点）")
    print(f"      （另 {len(miss - miss_used)} 条无真值面路由前端没有调用点："
          f"{'、'.join(sorted(miss - miss_used)) or '无'}）")

    # A3 闭式（两条）：路径级 + 调用点级
    #
    # A3a 的分母必须是**进入比对循环**的路径——首版用"全部调用点的路径"
    # （222），把 B5（无真值面）与 B2（提取不出）的路径也算进分子外的分母，
    # 于是 147+4+4=155≠222 报红。这与 §2.5.60「按源逐格对了不代表合计对了」
    # 同型：**闭式的两端得是同一个集合**。
    tot_paths = sum(len(h["paths"]) for h in rep["fe"]
                    if h["rule"] and rep["live"].get(h["rule"]) is not None
                    and not h["why"] and h["paths"])
    got_paths = rep["n_compared"] + rep["n_uncoreable"] + rep["n_wrapped"]
    n_b5p = sum(len(h["paths"]) for h in rep["fe"] if h["rule"]
                and rep["live"].get(h["rule"]) is None)
    n_b2p = sum(len(h["paths"]) for h in rep["fe"] if h["rule"] and h["why"])
    ok(got_paths == tot_paths,
       f"A3a 路径闭式：比过 {rep['n_compared']} + 核不了 {rep['n_uncoreable']} + "
       f"豁免 {rep['n_wrapped']} = {got_paths}（进入比对的路径 {tot_paths} 条；"
       f"另 B5 {n_b5p} 条 + B2 {n_b2p} 条未进比对）")
    n_calls = len(rep["fe"])
    n_with = sum(1 for h in rep["fe"] if h["paths"] and h["rule"]
                 and rep["live"].get(h["rule"]) is not None)
    n_b5 = sum(1 for h in rep["fe"] if h["rule"]
               and rep["live"].get(h["rule"]) is None)
    ok(n_with + rep["n_noextract"] + n_b5 == n_calls,
       f"A3b 调用点闭式：有路径 {n_with} + 提取不出 {rep['n_noextract']} + "
       f"无真值面 {n_b5} = {n_with + rep['n_noextract'] + n_b5}（调用点 {n_calls}）")

    for name, key in [
        ("B1 宿主为空 ⇒ 核不了（不是通过）", "b1"),
        ("B2 前端路径提取不出（判据缺口）", "b2"),
        ("B3 真值有、前端一次都不读的深层路径", "b3"),
        ("B4 真值没有、静态显示在别的分支（分支差异，非缺陷）", "b4"),
        ("B5 打不出真响应的路由", "b5"),
    ]:
        lst = rep[key]
        print(f"  · {name}：{len(lst)} 条")
        for x in lst[:8]:
            print(f"      {x}")


def _child_marks() -> dict:
    """另起一个**干净进程**重跑本脚本，取回机器可读的红集合。

    **为什么要另起进程**：后端路由实现（`construction_agent/web/overview.py`）
    在 `create_app()` 之前就被 import 且进了 `sys.modules`，同进程里改文件对
    已加载的模块无效——注入会"改了但没生效"，用例静默失效（§2.5.64 的
    "锚点写错 = 用例根本没跑"的又一形态）。前端 `static/*.js` 是运行时
    `read_text` 的，同进程有效，但两条面用同一条路径更好核。
    """
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--marks"],
                       capture_output=True, text=True, cwd=str(ROOT))
    lines = [x for x in r.stdout.splitlines() if x.startswith("MARKS ")]
    if not lines:
        raise RuntimeError("子进程没给出 MARKS 行：\n"
                           + r.stdout[-1500:] + "\n" + r.stderr[-1500:])
    return json.loads(lines[-1][len("MARKS "):])


def _inject_reds(path: Path, old: str, new: str) -> set:
    """改**真实文件** → 重跑 → 收红集合 → `finally` 恢复。

    断言的是「红的**恰好**是哪几条」（§2.5.68），所以返回 `(文件, 路径)`
    二元组集合，不返回整段中文描述——描述里带行列号，改一行就会漂。
    """
    src = path.read_text(encoding="utf-8")
    if old not in src:
        raise RuntimeError(f"注入锚点未命中：{path} 里找不到 {old!r}")
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    try:
        marks = _child_marks()
    finally:
        path.write_text(src, encoding="utf-8")
    return {(x["file"], x["path"]) for x in marks["a1"]}


def _extract_cases() -> list[tuple[str, str, str, str, set, set]]:
    """C1 的用例：本节踩出来的三个提取坑，各钉一条**正反**判据。

    期望的路径里**不含形参名**——形参指向的就是响应体本身，`prefix` 才是
    "它在响应体里的位置"（首版把 `d.year[].key` 写成期望值，用例自己报红，
    属于用例写错而非提取器错；§2.5.64「锚点写错 = 用例根本没跑」的近亲）。
    """
    return [
        ("方法调用 ⇒ 链到此为止（`d.filter(...)` 不产出 `filter`）",
         "function f(d){ return d.filter(x => x.k).length; }",
         "d", "", set(), {"filter"}),
        ("同名的 `filter` 当**键**读 ⇒ 保留（`d.filter.min_count`）",
         "function f(d){ return d.filter.min_count; }",
         "d", "", {"filter.min_count"}, set()),
        ("别名 rest 不吃方法名（`const y = d.year.filter(...)` ⇒ 前缀 `year`）",
         "function f(d){ const y = d.year.filter(t => t.count); return y[0].key; }",
         "d", "", {"year[].key"}, {"year.filter", "year.filter.key"}),
        ("形参身上的开头索引归一（前缀 `year` 上读 `years[0].key` ⇒ `year[].key`）",
         "function g(years){ return years[0].key; }",
         "years", "year", {"year[].key"}, {"year.key"}),
    ]


def part_c(rep) -> None:
    print("\n═══ C. 注入式自证 ═══")
    # C1 提取器的四个形态。三个坑对应正文"判据自身修正"表的第 1/2/3 条。
    for label, src, nm, pre, want, bad in _extract_cases():
        # 用 `bound_paths` 而不是 `paths_in`：别名/转发/就地兜底三条链都在前者里，
        # 第三例（`const y = ...`）正是靠别名那条才产出 `year[].key`。
        got = bound_paths(src, src, nm, pre)
        ok(want <= got and not (bad & got), f"C1 {label}",
           f"→ {sorted(got) or '（空）'}")

    # C2 前端面：往**真实** `static/overview.js` 的调用点里插一行读一个不存在的键。
    # 两面（真值 / 静态树）都没有它 ⇒ 必须恰好报这一条。
    reds = _inject_reds(
        STATIC / "overview.js",
        "overviewData = ov;\n        renderStats(ov);",
        "overviewData = ov;\n        ov.__probe_absent_key;\n        renderStats(ov);")
    ok(reds == {("overview.js", "__probe_absent_key")},
       f"C2 前端多读一个不存在的键 ⇒ A1 恰好报它（实得 {sorted(reds)}）")

    # C3 后端面：把**真实** `/api/overview` 的响应键改名。前端读的还是旧名，
    # 真值面与静态树（从同一份实现解出）双双改口 ⇒ 该键下的读全部转红。
    reds = _inject_reds(WEB / "overview.py",
                        '"year": year_series,', '"year_renamed": year_series,')
    ok(reds == {("overview.js", "year"), ("overview.js", "year[].key"),
                ("overview.js", "year[].count")},
       f"C3 后端把 `year` 改名 ⇒ A1 恰好报该键下的三条读（实得 {sorted(reds)}）")


def _doc_needles(rep) -> list[tuple[str, str]]:
    """文档里的数字 ↔ 本次实测，**逐格**对。

    needle 全部由**实测值现拼**，不在脚本里再抄一遍数字——同一个源不写第二遍
    （§2.5.76）。锚点串与文档正文必须逐字一致，错一个字符就报"锚点未命中"，
    而它与"判据报警"在输出上都是红（§2.5.67 的坑），所以锚点一律取**整行的定值段**。

    抽成独立函数是为了让 `--drift-test` 能对**每一条**锚点逐个注入漂移
    （§2.5.68：两个注入面都要测；只测源码面时文档判据改坏了不会有任何用例转红）。
    """
    live = rep["live"]
    allp: set = set()
    for v in live.values():
        if v:
            allp |= v
    n_ok = sum(1 for v in live.values() if v is not None)
    deep = sum(1 for x in allp if x.count(".") >= 2)
    npaths = sum(len(h["paths"]) for h in rep["fe"])
    n_b5 = sum(1 for h in rep["fe"] if h["rule"] and live.get(h["rule"]) is None)
    n_b5p = sum(len(h["paths"]) for h in rep["fe"] if h["rule"]
                and live.get(h["rule"]) is None)
    got = rep["n_compared"] + rep["n_uncoreable"] + rep["n_wrapped"]
    return [
        (DOC_NEEDLES[0], "本节标题"),
        (f"| `/api/` 只读 GET 路由 | **{len(live)}** 条 |", "路由总数"),
        (f"| 真响应拿到 JSON | **{n_ok}** 条 |", "真响应数"),
        (f"| **打不出真响应**（B5） | **{len(live) - n_ok}** 条 |", "打不出数"),
        (f"| 真值面键路径合计 | **{len(allp)}** 条（三层及以上 **{deep}** 条） |",
         "真值面"),
        (f"| 前端 GET 调用点 | **{len(rep['fe'])}** 处 → 提取出键路径 **{npaths}** 条 |",
         "前端路径"),
        (f"| 比过 / 核不了 / 豁免 | **{rep['n_compared']} / {rep['n_uncoreable']} / "
         f"{rep['n_wrapped']}** = {got}（闭式通过） |", "闭式"),
        (f"| 提取不出的调用点（B2） | **{rep['n_noextract']}** 处", "B2 处数"),
        (f"| A1 真缺陷 | **{len(rep['a1'])}** 条 |", "A1 条数"),
        (f"B1 **{len(rep['b1'])}** 条", "B1 定量"),
        (f"B3 **{len(rep['b3'])}** 条", "B3 定量"),
        (f"B4 **{len(rep['b4'])}** 条", "B4 定量"),
        (f"B5 **{n_b5}** 处调用点", "B5 处数"),
        (f"共 **{n_b5p}** 条路径未进比对", "B5 路径数"),
    ]


def part_d(rep) -> None:
    print("\n═══ D. 文档 ↔ 实测 ═══")
    txt = DOC.read_text(encoding="utf-8")
    for n, label in _doc_needles(rep):
        ok(n in txt, f"D 文档 ↔ 实测：{label}")


def _doc_reds(rep, doc_path: Path) -> set:
    """把 `DOC` 临时指向另一份文档、跑一次 D 段、收回红集合。"""
    import contextlib
    import io

    saved_doc, saved_failed = globals()["DOC"], FAILED[:]
    del FAILED[:]
    globals()["DOC"] = doc_path
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            part_d(rep)
        return set(FAILED)
    finally:
        globals()["DOC"] = saved_doc
        del FAILED[:]
        FAILED.extend(saved_failed)


def drift_test() -> int:
    """文档面漂移：**每条**锚点的末位数字 +1，断言红的**恰好**是那一条。

    §2.5.68 的规矩——只测源码面时，文档判据被改坏了不会有任何用例转红。
    基线必须先跑一次确认是空集：否则"注入后红了"可能只是文档本来就不对。
    """
    import tempfile

    rep = run()
    txt = DOC.read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="c2584_")) / "01.md"

    base = _doc_reds(rep, DOC)
    print(f"基线红集合：{sorted(base) or '空'}")
    bad = 0
    for needle, label in _doc_needles(rep):
        if needle not in txt:
            print(f"  ✗ 漂移用例「{label}」跳过：锚点本来就不在文档里")
            continue
        nums = list(re.finditer(r"\d+", needle))
        if not nums:
            print(f"  ✗ 漂移用例「{label}」跳过：锚点里没有数字可改")
            continue
        m = nums[-1]
        drifted = (needle[:m.start()] + str(int(m.group(0)) + 1)
                   + needle[m.end():])
        tmp.write_text(txt.replace(needle, drifted, 1), encoding="utf-8")
        reds = _doc_reds(rep, tmp)
        want = {f"D 文档 ↔ 实测：{label}"}
        if reds == want:
            bad += 1
            print(f"  ✓ 漂移「{label}」：红的恰好是它")
        else:
            print(f"  ✗ 漂移「{label}」：期望 {sorted(want)}，实得 {sorted(reds)}")
    print(f"文档面漂移 {bad}/{len(_doc_needles(rep))}")
    return 0 if bad == len(_doc_needles(rep)) and not base else 1


def main() -> int:
    rep = run()
    if "--marks" in sys.argv:
        # 机器可读出口：注入式自证要断言"红的**恰好**是哪几条"，
        # 解析中文输出行太脆（§2.5.68）。字段与 A1 一一对应。
        print("MARKS " + json.dumps({"a1": rep["a1_struct"]}, ensure_ascii=False))
        return 0
    if "--drift-test" in sys.argv:
        return drift_test()
    part_a(rep)
    part_b(rep)
    part_c(rep)
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
