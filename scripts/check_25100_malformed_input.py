#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.100 数值入参的**畸形输入面**——只读路由在非法/边界数值参数下还站不站得住。

## 为什么单独核这一面

前面四十一个维度问的都是「**正常输入**下对不对」（键在不在、值对不对、层级对不对）。
这一面问的是反过来的那句：**输入坏了，接口还站得住吗**。三档，一档比一档深：

| 档 | 命题 | 失败形态 |
|---|---|---|
| **存活** | 不 5xx | 裸 `int("abc")` → `ValueError` → 500 |
| **可控** | 返回值不得因参数坏掉而**规模失控** | `min(int("−1"), 500)` → `LIMIT -1` → **整张表** |
| **一致** | 同一个语义的参数在各路由上**同一套兜底** | `/api/collect/tasks` 裸 `int` vs 同名的 `/api/extract/tasks` 有 `try` |

第三档是 §2.5.66「有名无实」在**参数解析**上的形态：两个**同名函数**
（都叫 `api_list_tasks`）一个炸一个不炸——**同名不同物**。

## 实测取真值（2026-10-05）

全站「从请求读数值」的调用点 **14 处**，其中：
- **13 处**有 `try/except (TypeError, ValueError)` 兜底；
- **1 处**裸调用：`collect.py` 的 `api_list_tasks`（`/api/collect/tasks?limit=abc → 500`）；
- 该处还**缺下界夹紧**：`?limit=-1` → `LIMIT -1`（SQLite 里意为**不限条数**）
  → 有 3 行数据时吐 3 条、有 3 千行时吐 3 千条；`?limit=0` → 静默返回 0 条。

## 判据的盲区防护（本脚本最要紧的一处设计）

第一版探针（只打**真实**工作库）在 `/api/collect/tasks` 上抓到 1 处 5xx，
看起来"其余 13 处都没事"。**这是假绿**：多数路由第一行是
`if not staging_db.has_table(...): return unavailable_payload(...)`，
工作库缺表时**提前返回**，`int()` 那行**根本没被执行到**。
把工作库补成"表齐全"再跑，7 条含数值参数的路由**全部走主分支**
（降级格 0）——这才是"其余 13 处真有兜底"。

所以 B 段带一条**元判据 B3**：每条路由的响应必须落在**主分支**，
否则报「未执行到」——不允许"扫了但什么都没碰到"冒充"通过了"（§2.5.67）。

## 四段

- **A 段** 静态清单：AST 抽 14 处调用点，逐处判「有无 try」「有无下界夹紧」，
  与登记表**双向**相等（§2.5.74）。
- **B 段** 运行时真值面：临时库（backup + init_db + 灌 3 行）上把 `CASES` 里的路由
  逐条真打一遍（每条路由 × 它的每个数值参数格 × 5 种畸形值）。
  ★ 条数**不写死**——`len(CASES)` 从 7 补到 8 时，这行与收束语里的"7"都漂了
  （§2.5.71「加一处时别处断言的期望集合要跟着走」），故收束语改为现读。
- **C 段** 注入式自证：改**真实**源码三例，断言红的**恰好**是哪一档哪一条（§2.5.68）。
- **D 段** 文档对账。

**全程不碰真库**：副本走 SQLite `backup()` API（工作库是 WAL，`shutil.copy2`
只拿到 checkpoint 前的旧状态），灌数据灌在 `tempfile.mkdtemp()` 里的副本上。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

WEB = ROOT / "construction_agent" / "web"

#: 要扫的 web 模块（数值入参只出现在这几个文件里）
MODULES = ("api.py", "collect.py", "review.py", "extract.py")

#: 登记表（**直接层**）：`文件::函数::参数` → (有 try, 有下界夹紧, 上界夹紧, 说明)
#: **双向核**——新增的调用点没登记要报红，登记的调用点消失了也要报红。
REGISTRY: dict[str, tuple[bool, bool, bool, str]] = {
    "api.py::api_causes_cooccurrence::top": (True, False, False,
        "top 语义允许 0（返回空）；上界由数据规模天然兜住（19×19 共现对 ≤171）"),
    "collect.py::api_create_task::concurrency": (True, True, True,
        "body 参数（非 query）；夹紧在 submit_task 调用点 `max(1, min(concurrency, 8))`"),
    "collect.py::api_list_tasks::limit": (True, True, True,
        "★ 2026-10-05 修：此前裸 int() → 500，且缺 max(1,...) → LIMIT -1 吐全表"),
    "collect.py::api_raw_list::page": (True, True, False, "分页游标"),
    "collect.py::api_raw_list::size": (True, True, True, "分页大小"),
    "extract.py::_page_args::page": (True, True, False, "分页游标（本文件内共用）"),
    "extract.py::_page_args::size": (True, True, True,
        "上/下界夹紧写在 return 行（`return page, max(1, min(size, PAGE_SIZE_MAX))`）"),
    "extract.py::api_pending::limit": (True, True, True, "待提取队列上限"),
    "extract.py::api_estimate::n": (True, True, False, "估算条数，允许 0"),
    "extract.py::api_estimate::raw_per": (True, True, False,
        "实参是变量名（`int(raw_per)`，raw_per 由 `request.args.get(\"per_item\")` 来）"),
    "extract.py::api_create_task::concurrency": (True, True, True,
        "body 参数；夹紧在 `max(1, min(concurrency, 8))`"),
    "extract.py::api_list_tasks::limit": (True, True, True, "与 collect 侧同名同构"),
    "review.py::_page_args::page": (True, True, False, "分页游标（本文件内共用）"),
    "review.py::_page_args::size": (True, True, True,
        "上/下界夹紧写在 return 行，同 extract 侧"),
}

#: 登记表（**间接层**）：请求值先落到辅助函数再转数值，`int()` 的实参里没有 request。
#: 判据**收窄**在"实参里直接出现 request"上会漏掉这两族（§2.5.66：收窄换来准确、
#: 代价是覆盖变窄），所以显式登记、同样**双向核**。
INDIRECT: dict[str, tuple[bool, bool, bool, str]] = {
    "accidents_query.py::_int::raw": (True, False, False,
        "被 `ListParams.from_request` 用于 page/size/year_from/year_to 四格；失败退 None"),
    "review.py::_coerce::value": (True, False, False,
        "审核表单的 deaths/injuries/trapped_count 三列走 `int(float(value))`；失败退 None"),
}

#: B 段要打的路由 → 该路由的数值参数名。
#: **必须覆盖 A 段清单里每一条能打的 GET 路由**——A 段 14 处分布在 13 个函数上，
#: 其中两个是 POST（`collect.api_create_task`/`extract.api_create_task`，真打会起采集
#: 线程、改库，只静态核）。剩下 8 条 GET 路由全部在此：漏一条，针对它的注入用例
#: 就会**静默失效**（钻头换掉了 but 没人去打那条路由，红集合恒为空）。
CASES: dict[str, list[str]] = {
    "/api/causes/cooccurrence": ["top"],
    "/api/collect/tasks": ["limit"],
    "/api/collect/raw": ["page", "size"],
    "/api/extract/results": ["page", "size"],
    "/api/extract/pending": ["limit"],
    "/api/extract/estimate": ["n", "per_item"],
    "/api/extract/tasks": ["limit"],
    "/api/review/queue": ["page", "size"],
}

#: 畸形取值：非数值 / 负 / 零 / 科学计数 / 空串 五类
BAD = ["abc", "-1", "0", "1e99", ""]

#: 灌进临时库的行数（用于"规模可控"判据）
ROWS = 3


# ══════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════

class Report:
    def __init__(self) -> None:
        self.red: dict[str, list[str]] = {}

    def add(self, bucket: str, item: str) -> None:
        self.red.setdefault(bucket, []).append(item)

    @property
    def clean(self) -> bool:
        return not self.red

    def dump(self) -> None:
        for b in sorted(self.red):
            for it in self.red[b]:
                print(f"  ✗ [{b}] {it}")


def _seg(src: str, node: ast.AST) -> str:
    return ast.get_source_segment(src, node) or ""


def _num_calls(src: str) -> list[tuple[str, str, ast.stmt, str, bool]]:
    """抽「从 request 读数值」的 int()/float() 调用点。

    返回 `(函数名, 参数名, 所在叶子语句, try 保护语句 id 集合)`。

    **接收者收窄到「函数体」而不是「实参」**（§2.5.66）。三版才定下来：

    - 收「实参里直接出现 `request`」→ 漏掉 `int(body.get("concurrency"))` 与
      `int(raw_per)`（值先落到变量再转），少 3 处；
    - 收「全库所有 `int(`」→ 捞进 `int(stats.get("done"))` 这类**内部统计解析**，
      它们跟请求没关系（多 8 处假阳性）；
    - 收「函数体内出现过 `request.args`/`request.get_json` 的**所有** `int()`」
      → 既不漏也不滥：`_task_payload`（只有 stats 解析）因体内无 request 被排掉，
      `api_create_task`/`api_estimate`（含 body 参数与变量实参）被收进来。

    **只收叶子语句**（体内不含别的语句的那种）：`FunctionDef`/`Try`/`If` 自己
    也是 `ast.stmt`，不过滤掉的话函数体整段会被当成一条语句、同一个调用点
    以两种函数名各出现一次（首版实测报出一堆 `?::` 的重复项）。
    """
    tree = ast.parse(src)
    #: `ast.Try` 的 body 里的语句 id——用来判"这个调用点在不在 try 保护下"
    guarded: set[int] = set()
    for t in ast.walk(tree):
        if isinstance(t, ast.Try):
            for st in t.body:
                guarded.add(id(st))

    out: list[tuple[str, str, ast.stmt, str, bool]] = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        body_src = _seg(src, fn)
        if "request.args" not in body_src and "request.get_json" not in body_src:
            continue                       # 与请求无关的函数，整块跳过
        leaves = [st for st in ast.walk(fn)
                  if isinstance(st, ast.stmt)
                  and not any(isinstance(c, ast.stmt) for c in ast.iter_child_nodes(st))]
        for st in leaves:
            for call in [n for n in ast.walk(st) if isinstance(n, ast.Call)]:
                if not (isinstance(call.func, ast.Name)
                        and call.func.id in ("int", "float")):
                    continue
                arg = call.args[0] if call.args else None
                if arg is None:
                    continue
                key = _key_of(call)
                if not key:
                    continue
                out.append((fn.name, key, st, body_src, id(st) in guarded))
    return out


def _key_of(call: ast.Call) -> str:
    """`int(...)` 的参数名：实参里第一个字符串字面量；实参是变量就退回变量名。"""
    arg = call.args[0] if call.args else None
    if arg is None:
        return ""
    for n in ast.walk(arg):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
    return arg.id if isinstance(arg, ast.Name) else ""


def _clamp_flags(src: str) -> dict[str, tuple[bool, bool]]:
    """`函数::参数` → (有下界 `max(`、有上界 `min(`)。

    **两路合并，缺一不可**（各自都会单独出错，实测定下来）：

    - **① 父链**：从 `int()` 调用节点上溯到函数体边界，祖先里出现 `max(`/`min(`
      调用即算（`page = max(1, int(...))`）。只靠这一路会漏 `_page_args`——
      它的夹紧写在 `return` 行，与 `int()` 隔了好几条语句。
    - **② 同名实参**：函数体内 `max(`/`min(` 的实参里出现同名变量
      （`return page, max(1, min(size, PAGE_SIZE_MAX))`）。只靠这一路会把
      `api_raw_list::page` 误判成「有上界」——函数体里 `min(size, ...)` 的
      上界被张冠李戴到 `page` 头上（§2.5.63：判据的宽度决定结论的强度）。

    两路都收，但**每一路只作用于自己的 key**，因此不产生上面两种误判。
    """
    tree = ast.parse(src)
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    out: dict[str, tuple[bool, bool]] = {}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        clamps = [c for c in ast.walk(fn)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                  and c.func.id in ("max", "min")]
        # ② 本函数内 `max`/`min` 实参里出现过的变量名
        named: set[tuple[str, str]] = set()
        for c in clamps:
            for a in c.args:
                for n in ast.walk(a):
                    if isinstance(n, ast.Name):
                        named.add((c.func.id, n.id))
        for call in [c for c in ast.walk(fn)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                     and c.func.id in ("int", "float")]:
            key = _key_of(call)
            if not key:
                continue
            lo = hi = False
            cur: ast.AST | None = call                      # ① 父链
            while cur is not None and cur is not fn:
                p = parent.get(cur)
                if isinstance(p, ast.Call) and isinstance(p.func, ast.Name):
                    lo |= p.func.id == "max"
                    hi |= p.func.id == "min"
                cur = p
            lo |= ("max", key) in named                     # ② 同名实参
            hi |= ("min", key) in named
            out[f"{fn.name}::{key}"] = (lo, hi)
    return out


# ══════════════════════════════════════════════════════════════
# A 段：静态清单
# ══════════════════════════════════════════════════════════════

def scan() -> dict[str, tuple[bool, bool, bool]]:
    """扫全部 web 模块，返回 `文件::函数::参数` → (try, 下界, 上界)。

    下界/上界由 `_clamp_flags()` 判（父链 + 同名实参两路，见其 docstring）。
    """
    found: dict[str, tuple[bool, bool, bool]] = {}
    for mod in MODULES:
        src = (WEB / mod).read_text(encoding="utf-8")
        clamps = _clamp_flags(src)
        for fn, key, _st, _body, in_try in _num_calls(src):
            lo, hi = clamps.get(f"{fn}::{key}", (False, False))
            found[f"{mod}::{fn}::{key}"] = (in_try, lo, hi)
    return found


def scan_indirect() -> dict[str, tuple[bool, bool, bool]]:
    """间接层：辅助函数（体内无 request，`int()` 的实参里也没有）。

    判据：该函数**存在**、体内有 `int(`/`float(`、**有 try**；
    且**不被直接层收到**（元判据——两层不重叠，否则同一处被算两次）。
    """
    found: dict[str, tuple[bool, bool, bool]] = {}
    direct = scan()
    for spec in INDIRECT:
        mod, fn_name, _key = spec.split("::")
        src = (WEB / mod).read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == fn_name), None)
        if node is None:
            continue                        # 交给 A1 的"登记了但实测不到"报红
        body = _seg(src, node)
        guarded = any(isinstance(t, ast.Try) for t in ast.walk(node))
        has_num = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id in ("int", "float") for n in ast.walk(node))
        found[spec] = (guarded, has_num, not any(
            k.startswith(f"{mod}::{fn_name}::") for k in direct))
    return found


def part_a(rep: Report) -> None:
    print("═" * 78)
    print("A 段：静态清单（「从请求读数值」的调用点）")
    print("═" * 78)
    direct = scan()
    indirect = scan_indirect()

    print(f"\n  【直接层】实测 {len(direct)} 处 / 登记 {len(REGISTRY)} 处")
    print(f"  {'调用点':46s} {'try':5s} {'下界':5s} {'上界':5s}")
    for k in sorted(set(direct) | set(REGISTRY)):
        f, r = direct.get(k), REGISTRY.get(k)
        flag = "" if (f and r and f == r[:3]) else "  ← 不符"
        txt = "—" if f is None else \
            f"{'✓' if f[0] else '✗':3s}  {'✓' if f[1] else '—':3s}  {'✓' if f[2] else '—':3s}"
        print(f"  {k:46s} {txt}{flag}")

    print(f"\n  【间接层】辅助函数（实参里没有 request，值先落到变量）"
          f"  实测 {len(indirect)} / 登记 {len(INDIRECT)}")
    for k in sorted(set(indirect) | set(INDIRECT)):
        f = indirect.get(k)
        if f is None:
            print(f"  {k:46s} —  ← 实测不到")
        else:
            print(f"  {k:46s} try={'✓' if f[0] else '✗'}  有数值转换={'✓' if f[1] else '✗'}"
                  f"  不重叠={'✓' if f[2] else '✗'}")

    all_found = dict(direct)
    all_found.update({k: v for k, v in indirect.items()})
    all_reg = dict(REGISTRY)
    all_reg.update({k: v for k, v in INDIRECT.items()})

    # A1：双向相等（两层合起来作为一个清单）
    for k in sorted(set(all_found) - set(all_reg)):
        rep.add("a1", f"实测到但未登记：{k}")
    for k in sorted(set(all_reg) - set(all_found)):
        rep.add("a1", f"登记了但实测不到：{k}")

    # A2：每处须有 try 兜底
    for k, v in sorted(direct.items()):
        if not v[0]:
            rep.add("a2", f"{k} 裸调用（无 try/except）⇒ 非数值输入会 500")
    for k, v in sorted(indirect.items()):
        if not v[0]:
            rep.add("a2", f"{k}（间接层）无 try/except ⇒ 非数值输入会 500")

    # A3：进 SQL 的三类语义参数须有下界夹紧（`LIMIT -1` 会吐全表）
    for k, v in sorted(direct.items()):
        key = k.rsplit("::", 1)[1]
        if key in ("limit", "size", "page") and not v[1]:
            rep.add("a3", f"{k} 缺 max(1,...) 下界夹紧 ⇒ LIMIT -1 会吐全表")

    print(f"\n  合计 {len(all_found)} 处"
          f"（直接层 {len(direct)} + 间接层 {len(indirect)}）")
    print(f"  A1 清单双向相等：{'✓' if not rep.red.get('a1') else '✗'}  "
          f"A2 无裸调用：{'✓' if not rep.red.get('a2') else '✗'}  "
          f"A3 下界夹紧：{'✓' if not rep.red.get('a3') else '✗'}")


# ══════════════════════════════════════════════════════════════
# B 段：运行时真值面
# ══════════════════════════════════════════════════════════════

def _make_lib() -> tuple[Path, Path]:
    """造一份"表齐全 + collect_tasks 有 3 行"的临时工作库。真库一个字节不动。"""
    from _common import main_db_path, main_repo_root
    real = Path(main_repo_root()) / "data" / "caiems_staging.db"
    tmp = Path(tempfile.mkdtemp(prefix="c25100_"))
    dst_path = tmp / "staging.db"

    src = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    src.backup(dst)                     # WAL 库不能用 shutil.copy2
    dst.close()
    src.close()

    from construction_agent.web import staging_db
    staging_db.init_db(dst_path)

    con = sqlite3.connect(str(dst_path))
    cols = [r[1] for r in con.execute("PRAGMA table_info(collect_tasks)")]
    for i in range(ROWS):
        vals = {}
        for c in cols:
            vals[c] = {"task_id": f"t{i}", "status": "done",
                       "source_keys": '["mem"]', "params": "{}", "stats": "{}",
                       "created_at": f"2026-10-05 10:0{i}:00"}.get(c)
        con.execute(
            f"INSERT INTO collect_tasks ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})", [vals[c] for c in cols])
    con.commit()
    con.close()
    return tmp, dst_path


def _branch(d) -> str:
    """响应落在主分支还是降级分支。

    降级 = 守卫提前返回，`int()` **没被执行到** ⇒ 这一格不能算"通过"。
    """
    if not isinstance(d, dict):
        return "非dict"
    if d.get("available") is False or "missing" in d:
        return "降级"
    data = d.get("data")
    if isinstance(data, dict) and data.get("available") is False:
        return "降级"
    return "主分支"


def part_b(rep: Report, quiet: bool = False) -> dict:
    """真打一遍。返回机器可读的 `MARKS`（C 段要按它断言"红的恰好是哪几条"）。"""
    from _common import main_db_path
    from _serve import point_at

    tmp, staging = _make_lib()
    try:
        point_at(str(main_db_path()), staging)
        from construction_agent.web.factory import create_app
        c = create_app().test_client()

        if not quiet:
            print("═" * 78)
            print("B 段：运行时真值面（表齐全 + collect_tasks 灌 %d 行的临时库）" % ROWS)
            print("═" * 78)
            print(f"\n  临时库：{staging}\n")
            print(f"  {'路由':30s} {'参数':9s} {'值':7s} {'码':4s} {'分支':7s} 备注")

        b1: list[str] = []          # 5xx
        b2: list[str] = []          # 规模失控
        b3: list[str] = []          # 未执行到（元判据）
        sizes: dict[tuple[str, str], dict[str, int]] = {}

        for rule, keys in CASES.items():
            r0 = c.get(rule)
            d0 = r0.get_json(silent=True)
            br0 = _branch(d0)
            if br0 == "降级" and not quiet:
                print(f"  {rule:30s} {'—':9s} {'—':7s} {r0.status_code:<4d} {br0:7s} 基线")
            for k in keys:
                per: dict[str, int] = {}
                # 有效值基准：`-1` 与 `0` 该不该被夹到与 `1` 同档，得先知道 `1` 是多少
                r1 = c.get(rule, query_string={k: "1"})
                d1 = r1.get_json(silent=True)
                per["1"] = (len(d1["data"]) if isinstance(d1, dict)
                            and isinstance(d1.get("data"), list) else -1)
                for v in BAD:
                    r = c.get(rule, query_string={k: v})
                    d = r.get_json(silent=True)
                    br = _branch(d)
                    n = (len(d["data"]) if isinstance(d, dict)
                         and isinstance(d.get("data"), list) else None)
                    per[v] = -1 if n is None else n
                    note = ""
                    if r.status_code >= 500:
                        note = "★5xx"
                        b1.append(f"{rule} ?{k}={v!r} → {r.status_code}")
                    elif br == "降级":
                        note = "★未执行到"
                        b3.append(f"{rule} ?{k}={v!r} 走降级分支 ⇒ 该参数根本没解析")
                    if not quiet:
                        print(f"  {rule:30s} {k:9s} {v!r:7s} {r.status_code:<4d} {br:7s} {note}")
                sizes[(rule, k)] = per
                # B2 规模可控：`-1`（LIMIT -1）与 `0`（LIMIT 0）必须被夹到与 `1` 同档，
                # 而不是"吐全表"或"静默变空"——下界夹紧的直接后果。
                # **适用范围只限直接进 SQL 的 LIMIT/OFFSET==`limit`/`size`/`page`**：
                # `top`/`n`/`per_item` 语义不同且规模天然有界（共现对 ≤171、估算只算数），
                # 对它们套这条判据是判据过宽（§2.5.63：判据的宽度决定结论的强度）。
                if k in ("limit", "size", "page") and per.get("1", -1) >= 0:
                    if per.get("-1", -1) > per.get("1", 0):
                        b2.append(f"{rule} ?{k}=-1 返回 {per['-1']} 条 > ?{k}=1 的 "
                                  f"{per['1']} 条 ⇒ LIMIT -1 未夹紧（吐全表）")
                    if per.get("0", -1) != per.get("1", 0):
                        b2.append(f"{rule} ?{k}=0 返回 {per['0']} 条 ≠ ?{k}=1 的 "
                                  f"{per['1']} 条 ⇒ 0 未归一")

        for x in b1:
            rep.add("b1", x)
        for x in b2:
            rep.add("b2", x)
        for x in b3:
            rep.add("b3", x)

        if not quiet:
            print(f"\n  B1 无 5xx：{'✓' if not b1 else '✗ %d 处' % len(b1)}   "
                  f"B2 规模可控：{'✓' if not b2 else '✗ %d 处' % len(b2)}   "
                  f"B3 全部执行到：{'✓' if not b3 else '✗ %d 处' % len(b3)}")
            print(f"\nMARKS {json.dumps({'b1': b1, 'b2': b2, 'b3': b3}, ensure_ascii=False)}")
        return {"b1": b1, "b2": b2, "b3": b3}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# C 段：注入式自证
# ══════════════════════════════════════════════════════════════

#: (名字, 文件, 旧串, 新串, 期望转红的 MARKS 键集合)
_DRILLS: list[tuple[str, str, str, str, set[str]]] = [
    ("摘掉 collect 侧的 try",
     "collect.py",
     '    try:\n        limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))\n'
     '    except (TypeError, ValueError):\n        limit = 50\n',
     '    limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))\n',
     {"b1"}),
    ("摘掉 collect 侧的 max(1,...)",
     "collect.py",
     'limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))',
     'limit = min(int(request.args.get("limit", 50) or 50), 500)',
     {"b2"}),
    ("摘掉 extract 侧的 try（同名不同物）",
     "extract.py",
     '    try:\n        limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))\n'
     '    except (TypeError, ValueError):\n        limit = 50\n',
     '    limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))\n',
     {"b1"}),
]


def _run_b_marks() -> dict:
    """另起进程跑 B 段（模块已加载，同进程改文件无效——§2.5.84）。"""
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--part", "b"],
                       capture_output=True, text=True, cwd=str(ROOT))
    for line in r.stdout.splitlines():
        if line.startswith("MARKS "):
            return json.loads(line[6:])
    raise RuntimeError(f"子进程未输出 MARKS（rc={r.returncode}）\n{r.stdout[-800:]}\n{r.stderr[-800:]}")


def part_c(rep: Report) -> None:
    print("\n" + "═" * 78)
    print("C 段：注入式自证（改真实源码，断言红的**恰好**是哪一档）")
    print("═" * 78)

    base = _run_b_marks()
    base_red = {k for k, v in base.items() if v}
    print(f"\n  基线红集合：{sorted(base_red) or '空'}")
    if base_red:
        rep.add("c0", f"基线本身不干净：{sorted(base_red)}——下面的注入自证无意义")
        return

    for name, fname, old, new, want in _DRILLS:
        p = WEB / fname
        src = p.read_text(encoding="utf-8")
        if old not in src:
            rep.add("c", f"{name}：锚点未命中（源码已变？）")
            print(f"  ✗ {name}：锚点未命中")
            continue
        p.write_text(src.replace(old, new, 1), encoding="utf-8")
        try:
            got = {k for k, v in _run_b_marks().items() if v}
        finally:
            p.write_text(src, encoding="utf-8")
        ok = got == want
        print(f"  {'✓' if ok else '✗'} {name}：期望 {sorted(want)}，实测 {sorted(got)}")
        if not ok:
            rep.add("c", f"{name}：期望 {sorted(want)}，实测 {sorted(got)}")


# ══════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════

DOC = ROOT / "docs" / "web" / "01_在线采集.md"

#: 文档里必须逐字出现的锚点（`(正则, 说明)`）——**只核键不核值**（§2.5.73）
DOC_ANCHORS: list[tuple[str, str]] = [
    (r"###\s*2\.5\.100", "01 号文档有 §2.5.100 这一节"),
    (r"14\s*处调用点", "调用点总数 14"),
    (r"裸\s*`?int", "记了「裸 int」这个根因"),
    (r"LIMIT\s*-1", "记了 LIMIT -1 的语义"),
    (r"check_25100_malformed_input", "点了脚本名"),
]


def _doc_path() -> Path:
    """文档路径。`C25100_DOC` 环境变量可指到副本——`--drift-test` 用（§2.5.74）。"""
    override = os.environ.get("C25100_DOC")
    return Path(override) if override else DOC


def part_d(rep: Report) -> None:
    print("\n" + "═" * 78)
    print("D 段：文档对账")
    print("═" * 78)
    doc = _doc_path()
    if not doc.exists():
        rep.add("d", f"文档不存在：{doc}")
        return
    txt = doc.read_text(encoding="utf-8")
    for pat, why in DOC_ANCHORS:
        ok = re.search(pat, txt) is not None
        print(f"  {'✓' if ok else '✗'} {why}")
        if not ok:
            rep.add("d", f"锚点未命中：{pat}（{why}）")


# ══════════════════════════════════════════════════════════════
# 自证：判据自己的正反用例（`--self-test`）与文档面漂移（`--drift-test`）
# ══════════════════════════════════════════════════════════════

#: `_clamp_flags` 的正反用例：`(名字, 代码, 函数名, 参数名, 期望(下界, 上界))`。
#: **必须有反例**——只测正例的话，"把 `max(` 当信号"这种写法也能全绿。
_CLAMP_CASES: list[tuple[str, str, str, str, tuple[bool, bool]]] = [
    ("父链 max → 有下界", "def f():\n    x = max(1, int(p))\n", "f", "p", (True, False)),
    ("父链 min → 有上界", "def f():\n    x = min(int(p), 500)\n", "f", "p", (False, True)),
    ("父链 max+min → 两界",
     "def f():\n    x = max(1, min(int(p), 500))\n", "f", "p", (True, True)),
    ("裸 int → 两界皆无", "def f():\n    x = int(p)\n", "f", "p", (False, False)),
    # `_page_args` 形态：夹紧在 return 行，靠**同名实参**那一路兜住。
    # ★ 判据的边界就在这里——两路的 key 必须**字面相同**才命中，真实代码
    # `size = int(request.args.get("size", …))` 恰好同名，属**巧合**而非保证。
    # 下面两条是同一个形态的正反两面，把这条边界钉死。
    ("跨语句同名实参（_page_args 形态）",
     "def f():\n    size = int(request.args.get('size', 20))\n"
     "    return max(1, min(size, 500))\n", "f", "size", (True, True)),
    ("★边界：跨语句但**不同名**则不命中（判据的已知盲区）",
     "def f():\n    s = int(request.args.get('size', 20))\n"
     "    return max(1, min(s, 500))\n", "f", "size", (False, False)),
    # ★ 反例：同一函数体里 size 的 min( 不得张冠李戴到 page 头上
    ("张冠李戴反例：page 不得因 size 的 min( 而有上界",
     "def f():\n    page = max(1, int(a))\n    size = min(int(b), 500)\n    return page, size\n",
     "f", "a", (True, False)),
    ("变量实参：key 取变量名", "def f():\n    x = max(0, int(raw))\n", "f", "raw", (True, False)),
]

#: `_branch` 的三个降级出口
_BRANCH_CASES: list[tuple[str, object, str]] = [
    ("顶层 available=false", {"available": False}, "降级"),
    ("顶层含 missing", {"ok": False, "missing": ["t"]}, "降级"),
    ("data.available=false", {"data": {"available": False}}, "降级"),
    ("正常主分支", {"ok": True, "available": True, "data": []}, "主分支"),
    ("非 dict", [1, 2], "非dict"),
]

#: `(名字, 旧串(正则), 新串, 期望转红的集合)` —— **只作用于文档副本**，真文档一个字节不动
#: （§2.5.71：验证手段不能制造它要防的损害）。
#:
#: ★ 两条设计要点，**都是首跑 4 条红抓出来的**（不是想出来的）：
#:
#: ① **必须全局替换**。判据问的是"这个词在文档里出现过没有"，**只删一处不够**——
#:    `LIMIT -1` 在本文档出现 4 处、`14 处调用点` 出现 2 处，`replace(…, 1)` 改的是
#:    第一处，剩下几处照样命中锚点 ⇒ 用例**恒绿**。与 §2.5.87 里"PDF 只擦一处不够"
#:    是同一个动作（那边是 `search_for('16,323')` 命中 5 处）。
#:    替换 **0 次**必须点名叫红——否则"锚点写错"与"判据没报"在输出上一模一样（§2.5.89）。
#:
#: ② **改写后的串不得仍被原正则命中**。首版把 `### 2.5.100 …` 改成 `### 2.5.1000 …`，
#:    而 `###\s*2\.5\.100` 照样匹配 `2.5.1000` 的**前缀** ⇒ 用例恒绿。改用不相交的编号。
_DOC_DRILLS: list[tuple[str, str, str, set[str]]] = [
    ("① 标题锚点删掉",
     r"### 2\.5\.100 数值入参的畸形输入面",
     "### 2.5.995 数值入参的畸形输入面", {"d"}),
    ("②「14 处调用点」改成 13", r"14(?= 处调用点)", "13", {"d"}),
    ("③「裸 int」措辞抹掉", r"裸 `int", "直接调用 `int", {"d"}),
    ("④「LIMIT -1」语义删掉", r"LIMIT -1", "LIMIT 负数", {"d"}),
    ("⑤ 脚本名改错", r"check_25100_malformed_input", "check_25100_typo", {"d"}),
]

#: `_key_of` 用例：`(代码, 期望参数名)`
_KEY_CASES: list[tuple[str, str]] = [
    ("x = int(request.args.get('p', 1))", "p"),
    ("x = int(raw_per)", "raw_per"),
    ("x = max(0, int(body.get('concurrency') or 2))", "concurrency"),
]


def self_test() -> int:
    """判据自己的正反用例。`--self-test` 用。返回失败条数。"""
    bad = 0
    print("  ── `_clamp_flags`：下界/上界判定 ──")
    for name, code, fn, key, want in _CLAMP_CASES:
        got = _clamp_flags(code).get(f"{fn}::{key}")
        ok = got == want
        bad += not ok
        print(f"  {'✓' if ok else '✗'} {name}：期望 {want}，实测 {got}")

    print("  ── `_key_of`：参数名取法 ──")
    for code, want in _KEY_CASES:
        node = next(n for n in ast.walk(ast.parse(code))
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "int")
        got = _key_of(node)
        ok = got == want
        bad += not ok
        print(f"  {'✓' if ok else '✗'} {code}：期望 {want!r}，实测 {got!r}")

    print("  ── `_branch`：三档分支判定 ──")
    for name, payload, want in _BRANCH_CASES:
        got = _branch(payload)
        ok = got == want
        bad += not ok
        print(f"  {'✓' if ok else '✗'} {name}：期望 {want}，实测 {got}")

    print("  ── `_num_calls`：只收叶子语句（防函数体整段重复计数）──")
    src = ('def f():\n'
           '    if 1:\n'
           '        x = int(request.args.get("n", 0))\n')
    got = _num_calls(src)
    ok = len(got) == 1
    bad += not ok
    print(f"  {'✓' if ok else '✗'} 嵌套 if 里的一个调用点只计 1 次：实测 {len(got)} 次")

    print("  ── 清单规模（防转录时静默漏项）──")
    ok = len(REGISTRY) == 14 and len(INDIRECT) == 2
    bad += not ok
    print(f"  {'✓' if ok else '✗'} REGISTRY=14 / INDIRECT=2：实测 "
          f"{len(REGISTRY)} / {len(INDIRECT)}")

    print("  ── CASES 覆盖 A 段全部可打 GET 路由（防注入用例静默失效）──")
    # A 段清单里的函数 → 它服务哪些 GET 路由；两个 POST 打不了
    served = {  # 函数名 → 该函数被哪些路由用到（GET 才进 CASES）
        "api_causes_cooccurrence": {"/api/causes/cooccurrence"},
        "api_list_tasks": {"/api/collect/tasks", "/api/extract/tasks"},
        "api_raw_list": {"/api/collect/raw"},
        "_page_args": {"/api/extract/results", "/api/review/queue"},
        "api_pending": {"/api/extract/pending"},
        "api_estimate": {"/api/extract/estimate"},
    }
    want = set()
    for routes in served.values():
        want |= routes
    ok = want == set(CASES)
    bad += not ok
    print(f"  {'✓' if ok else '✗'} CASES 覆盖齐：缺 {sorted(want - set(CASES))}、"
          f"多 {sorted(set(CASES) - want)}")
    return bad


def drift_test() -> int:
    """**文档面**漂移：逐个锚点注入到**副本**，断言红的**恰好**是 `d` 那一条。

    §2.5.68 立的规矩：只断言"退出码非 0"太弱——分不清是哪条判据抓到的。
    源码面已有 C 段自己的注入式自证，这里补的是**文档面**（只测源码面时，
    文档判据被改坏了不会有任何用例转红）。

    全程只写临时副本：真文档若被改坏，"验证判据能报红"这件事就变成了
    "制造了它要防的损害"（§2.5.71）。
    """
    bad = 0
    if not DOC.exists():
        print(f"  ✗ 文档不存在：{DOC}")
        return 1
    original = DOC.read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="c25100drift_"))
    copy = tmp / DOC.name
    copy.write_text(original, encoding="utf-8")
    os.environ["C25100_DOC"] = str(copy)
    try:
        # 元判据：先确认基线红集合为空，否则"注入后转红"说明不了任何事
        rep = Report()
        part_d(rep)
        if rep.red.get("d"):
            print(f"  ✗ 元判据：基线文档已不干净 {rep.red['d']} —— 先把文档写好再跑本测试")
            return 1
        print("  ✓ 元判据：基线文档全部锚点在位（红集合为空）")

        for name, pat, new, want in _DOC_DRILLS:
            src = copy.read_text(encoding="utf-8")
            # **全局**替换：判据问的是"出现过没有"，只删一处不够（见 `_DOC_DRILLS` 注①）
            new_src, n = re.subn(pat, new, src)
            if n == 0:
                print(f"  ✗ {name}：锚点未命中（`{pat[:40]}…`）——用例静默失效")
                bad += 1
                continue
            try:
                copy.write_text(new_src, encoding="utf-8")
                rep = Report()
                part_d(rep)
                got = set(rep.red)
            finally:
                copy.write_text(src, encoding="utf-8")
            ok = got == want
            bad += not ok
            print(f"  {'✓' if ok else '✗'} {name}：替换 {n} 处，"
                  f"期望 {sorted(want)}，实测 {sorted(got)}")
    finally:
        os.environ.pop("C25100_DOC", None)
        shutil.rmtree(tmp, ignore_errors=True)

    # 真文档必须逐字未动——这一条是"验证手段没造成损害"的证据
    if DOC.read_text(encoding="utf-8") != original:
        print("  ✗ 真文档被改动了")
        bad += 1
    return bad


# ══════════════════════════════════════════════════════════════

def main(argv: list[str]) -> int:
    if "--part" in argv and argv[argv.index("--part") + 1] == "b":
        part_b(Report(), quiet=False)
        return 0

    if "--self-test" in argv:
        print("═" * 78)
        print("自证：判据自己的正反用例")
        print("═" * 78)
        n = self_test()
        print("\n" + ("✅ 自证全部通过" if not n else f"❌ 自证失败 {n} 条"))
        return 1 if n else 0

    if "--drift-test" in argv:
        print("═" * 78)
        print("漂移自证：往文档副本注入，断言红的**恰好**是哪一条")
        print("═" * 78)
        n = drift_test()
        print("\n" + ("✅ 漂移自证全部通过" if not n else f"❌ 漂移自证失败 {n} 条"))
        return 1 if n else 0

    rep = Report()
    part_a(rep)
    part_b(rep)
    part_c(rep)
    part_d(rep)

    print("\n" + "═" * 78)
    if rep.clean:
        print(f"✅ §2.5.100 全部通过：{len(REGISTRY)} 处直接层调用点全部有兜底，"
              f"运行时 {len(CASES)} 条路由 × {len(BAD)} 种畸形值无 5xx、规模可控、全部执行到")
        return 0
    print("❌ §2.5.100 有待处置项：")
    rep.dump()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
