#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.101 数值参数的**夹紧链**——夹紧可能写在读取点之外。

## 为什么单独核这一面

§2.5.100 核的是「畸形数值参数下接口还站不站得住」，它的 A 段判据 A3 是：

    if key in ("limit", "size", "page") and not has_lower_bound:
        报红「缺 max(1,...) 下界夹紧 ⇒ LIMIT -1 会吐全表」

这条判据是「规模可控」的**代理**，代理与事实之间隔着三样东西：

| 隔阂 | 形态 | 本节实测 |
|---|---|---|
| **名字** | 判据按**字面名字**挑格子，`top`/`n`/`per_item` 名字不在名单上一律免检 | `top` 语义就是「取前 N 对」，与 `limit` 同类 |
| **层** | 判据只读**读取点**那一行，夹紧可能写在调用点或消费函数里 | `top` 的夹紧在 `causes.py:265`，离读取点隔了一个函数 |
| **依据** | 排除 `/api/causes/cooccurrence` 的理由是「**规模天然有界**（共现对 ≤171）」——**这条理由来自数据、不来自代码**，语料一变就过期 | 真实上界是代码里的 `min(100, top)` |

第三条是本节的旗舰：**「数据天然有界」是会过期的理由，「代码夹紧」不会**。
§2.5.75 立过「写死的固定值本身可能就是响应式的」——这是同族的另一形态。

## 实测取真值（2026-10-05）

主库面（2,623 条）真打：

| 路由 | 参数 | 畸形值 → 实测 |
|---|---|---|
| `/api/accidents` | `size` | `999999999` → **200 条**（`min(MAX_SIZE,...)` 生效）；`-1`/`0` → 50（默认） |
| `/api/accidents` | `page` | `999999999` → 0 条（OFFSET 超界，无失控） |
| `/api/causes/cooccurrence` | `top` | `0` → **1 对**（`max(1,...)` 生效，**不是"返回空"**）；`101`/`999999999` → **100 对**（`min(100,...)`） |

`?top=0 → 1 对` 这一格同时证伪了 §2.5.100 登记表里的两句话：
「top 语义**允许 0（返回空）**」与「上界由**数据规模**天然兜住」。

## 四段

- **A 段** 夹紧链登记表：每个数值参数登记「夹紧在哪一层」+「依据是 C（代码）还是 D（数据）」，
  与实测集合**双向**相等（§2.5.74）；标 D 的须**当场实测数据规模**并断言其确实有界（§2.5.67）。
- **B 段** 运行时真值面：主库面 + 工作库面，**上界**与**下界**分开判——
  §2.5.100 只判了下界（`LIMIT -1` 不比 `LIMIT 1` 多），
  上界要判「巨大值下条数 ≤ 登记上限」，且须**夹具规模 > 上限**才有分辨力（§2.5.73：
  分辨不了要判「未知」，不能降级成「通过」）。
- **C 段** 注入式自证：改**真实**源码，断言红的**恰好**是哪几条（§2.5.68）。
- **D 段** 文档与**登记文本**对账：登记表里的理由本身是事实断言，就得有判据
  （§2.5.96：引述一律描述而非复述）。

**全程不碰真库**：主库面走 `create_app()` 的只读连接，工作库面走 `backup()` 造副本。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

WEB = ROOT / "construction_agent" / "web"

#: 夹紧链登记表（§2.5.101 的核心产物）。
#:
#: 键 = `文件::函数::参数`（**与 §2.5.100 的编号空间一致**，便于两节对照）。
#: 值 = (夹紧层, 位置与表达式, 依据, 说明)
#:
#: **夹紧层**四档：
#:   `L1 读取点`   —— `int()` 那一行的同一个语句里就有 `max(`/`min(`
#:   `L2 调用点`   —— 读取是通用转换（如 `_int()`），夹紧在**谁调用它**那里
#:   `L3 消费函数` —— 传进下游函数后由**那边**夹（离读取点最远，最容易漏）
#:   `L4 SQL 层`   —— 由参数化 SQL 的语义兜住（如 `LIMIT ?` 配一个已夹紧的变量）
#:
#: **依据**两档：
#:   `C` —— 代码里显式夹紧（**不随数据变**）
#:   `D` —— 靠数据规模天然有界（**会随语料变**）⇒ A 段须当场实测其规模
CHAIN: dict[str, tuple[str, str, str, str, str]] = {
    # 值 = (夹紧层, "文件:行", 依据, **该行须逐字含的子串**, 说明)
    "api.py::api_causes_cooccurrence::top": (
        "L3 消费函数", "causes.py:265", "C", "top = max(1, min(100, top))",
        "★ 本节旗舰格：读取点无任何夹紧（`api.py:142` 只有 `try`），"
        "真实上下界都在**下游消费函数**里。§2.5.100 的登记表把它解释成"
        "「规模天然有界（共现对 ≤171）」——那是数据侧的巧合，不是设计："
        "语料长大到 200 对时，那句理由就不成立了"),
    "accidents_query.py::_int::raw": (
        "L2 调用点", "accidents_query.py:253", "C", "page=max(1, page)",
        "`_int()` 是**通用转换器**（page/size/year_from/year_to 四格用途），"
        "夹紧规则各不相同，故它本身**不该**夹——夹紧落在调用点的具名参数上"),
    "collect.py::api_raw_list::page": (
        "L1 读取点", "collect.py:332", "C",
        'page = max(1, int(request.args.get("page", 1) or 1))', "同语句下界"),
    "collect.py::api_raw_list::size": (
        # `size` 的赋值**跨两行**（L333 起、L334 续），子串取 L333 上能逐字命中
        # 的那一段即可——A1a 判的是"该行含不含这个子串"，不必跨行。
        "L1 读取点", "collect.py:333", "C",
        "size = min(PAGE_SIZE_MAX, max(1, int(", "上下界挤在同一个语句里"),
    "collect.py::api_list_tasks::limit": (
        "L1 读取点", "collect.py:234", "C",
        'limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))',
        "★ 2026-10-05（§2.5.100）修的那一处：此前裸 `int()` + 缺下界"),
    "collect.py::api_create_task::concurrency": (
        "L2 调用点", "collect.py:216", "C", "concurrency=max(1, min(concurrency, 8))",
        "body 参数（非 query），夹紧在 `submit_task` 的调用点"),
    "extract.py::_page_args::page": (
        "L1 读取点", "extract.py:55", "C",
        'page = max(1, int(request.args.get("page", 1) or 1))', "同语句下界"),
    "extract.py::_page_args::size": (
        "L1 读取点", "extract.py:62", "C", "return page, max(1, min(size, PAGE_SIZE_MAX))",
        "上界在 `return` 行——`_clamp_flags()` 的第二路（同名实参）专为它而写"),
    "extract.py::api_pending::limit": (
        "L1 读取点", "extract.py:138", "C",
        'limit = max(1, min(int(request.args.get("limit", 200) or 200), 1000))',
        "上限 **1000**（比其他路由的 500/200 宽）"),
    "extract.py::api_estimate::n": (
        "L1 读取点", "extract.py:217", "C", 'n = max(0, int(request.args.get("n", 0) or 0))',
        "★ 只有下界、**没有上界**——但 `n` 不进 SQL，只作 `estimate()` 里的乘法因子"
        "（`tokens_in = int(per * 2 / 3) * max(0, n_items)`），**不产生条数**，"
        "故不受「规模可控」这一档约束（判据边界，见 B 段）"),
    "extract.py::api_estimate::raw_per": (
        "L1 读取点", "extract.py:224", "C", "per_item = max(0, int(raw_per))",
        "同上：只进乘法、只算数，不落库"),
    "extract.py::api_list_tasks::limit": (
        "L1 读取点", "extract.py:292", "C",
        'limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))',
        "与 collect 侧同名同构（两者都叫 `api_list_tasks`，读的表不同）"),
    "extract.py::api_create_task::concurrency": (
        "L2 调用点", "extract.py:254", "C", "concurrency=max(1, min(concurrency, 8))", "body 参数"),
    "review.py::_page_args::page": (
        "L1 读取点", "review.py:83", "C",
        'page = max(1, int(request.args.get("page", 1) or 1))', "同语句下界"),
    "review.py::_page_args::size": (
        "L1 读取点", "review.py:90", "C", "return page, max(1, min(size, PAGE_SIZE_MAX))",
        "同 extract 侧，上界在 `return` 行"),
    "review.py::_coerce::value": (
        "L1 读取点", "review.py:372", "C", "def _coerce(field: str, value: Any) -> Any:",
        "★ 兜底形态与其他 15 处**都不同**：不是夹紧，而是**失败退 `None`**"
        "（审核表单的 deaths/injuries/trapped_count 三列，退 None 即「未提供」）"
        "——A1b 判据专为它留了一档"),
}

#: B 段要打的格：路由 → {参数: 上界 or None}。
#: `None` = 无登记上界（只判下界）。**主库面与工作库面都要有**——
#: §2.5.100 的 CASES 8 条**全是工作库路由**，主库那 2,623 条的数据面从未被运行时验过。
B_CASES: dict[str, dict[str, int | None]] = {
    # ── 主库面（走 create_app() 的只读连接）──
    "/api/accidents": {"size": 200, "page": None},
    "/api/causes/cooccurrence": {"top": 100},
    # ── 工作库面（走临时副本）──
    "/api/collect/tasks": {"limit": 500},
    # `collect.py:366-371` 返回 `meta.total` ⇒ `_scale` 第一档就能取到规模，
    # 是工作库面里唯一不必靠夹具行数兜底的格。
    "/api/collect/raw": {"size": 200, "page": None},
    "/api/extract/tasks": {"limit": 500},
    "/api/extract/results": {"size": 200},
    # ⚠ 这条路由的 `size` 上限生效于**内存切片**（`review.py:243` 取全量 →
    # L253 `total = len(rows)` → L255 `rows[start:start+size]`），**不在 SQL**。
    # 故这格测的是「内存切片有没有被夹住」，不是「`LIMIT -1` 有没有吐全表」
    # ——与其余各格语义不同，读数时要分开看。
    "/api/review/queue": {"size": 200},
}

#: 主库面路由（其余走临时工作库）
MAIN_ROUTES = ("/api/accidents", "/api/causes/cooccurrence")

#: 巨大值：用来逼出**上界**。必须大于登记上限，否则判不出上界是否生效。
HUGE = "999999999"

#: 下界格：`-1` 会变 `LIMIT -1`（吐全表）、`0` 会变 `LIMIT 0`（静默空）
LOWER_VALUES = ("-1", "0")

#: 工作库夹具的行数。**它决定夹具的分辨力**——上限 500 的参数在 3 行的库上
#: 结构上分辨不出「上界生效」与「没有上界」（两者都返回 3 条）。
#: 取 600：大于工作库面全部登记上限（500 与 200），使每格都拿得到分辨力。
ROWS = 600

#: 路由 → 它读的表。`_scale` 的第三档回退用——响应里取不到规模时改用夹具的
#: **实测已插行数**（那是真值，不是从响应读的；与"响应说 0 条"必须分开）。
ROUTE_TABLE: dict[str, str] = {
    "/api/collect/tasks": "collect_tasks",
    "/api/collect/raw": "raw_accidents",
    "/api/extract/tasks": "extract_tasks",
    "/api/extract/results": "staging_accidents",
    "/api/review/queue": "staging_accidents",
}


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


def _load_25100():
    """载入 §2.5.100 的脚本，复用它的扫描面（§2.5.76：同一个源不写第二遍解析）。"""
    p = ROOT / "scripts" / "check_25100_malformed_input.py"
    spec = importlib.util.spec_from_file_location("c25100", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _src(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def _text_at(rel: str, lineno: int) -> str:
    """取某文件的第 lineno 行（1 起，越界返回空串）。"""
    lines = _src(rel).splitlines()
    return lines[lineno - 1].strip() if 1 <= lineno <= len(lines) else ""


def _fn_body(rel: str, fname: str) -> str:
    """取某函数的**完整源码段**（含签名与函数体）。取不到返回空串。

    A1b 用它：位置行本身不含 `max(`/`min(` 时，兜底可能落在同函数体的别处
    （`review.py::_coerce` 就是这一类）。
    """
    src = _src(rel)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname:
            return ast.get_source_segment(src, n) or ""
    return ""


# ══════════════════════════════════════════════════════════════
# A 段：夹紧链
# ══════════════════════════════════════════════════════════════

#: 位置摘要里 `文件:行` 的解析（用于把登记的每一处落到源码上核一遍）
_LOC = re.compile(r"([a-z_]+\.py):(\d+)")


def _fact_assertions() -> list[tuple[str, str, str]]:
    """登记表里那些**事实断言**——每条都要有判据（§2.5.96）。

    返回 `(来源, 断言原文, 判据说明)`。D 段会拿它去核 `check_25100` 的同名断言。
    """
    return [
        ("CHAIN[api.py::api_causes_cooccurrence::top]",
         "上界是代码里的 min(100, top)",
         "实测 ?top=101 → 100 对"),
        ("CHAIN[api.py::api_causes_cooccurrence::top]",
         "下界是代码里的 max(1, top)（0 会被提成 1，**不是**返回空）",
         "实测 ?top=0 → 1 对"),
    ]


def part_a(rep: Report, quiet: bool = False) -> None:
    c25100 = _load_25100()
    direct = c25100.scan()
    indirect = c25100.scan_indirect()
    all_found = set(direct) | set(indirect)

    if not quiet:
        print("═" * 78)
        print("A 段：夹紧链（每个数值参数的夹紧写在哪一层）")
        print("═" * 78)
        print(f"\n  {'参数':46s} {'层':12s} {'依据':5s} 位置")
        for k in sorted(CHAIN):
            layer, loc, why, _sub, _note = CHAIN[k]
            print(f"  {k:46s} {layer:12s} {why:5s} {loc[:44]}")

    # A1：登记的每一处夹紧，位置行须**逐字含**声明的子串（§2.5.67：只写理由不验证＝永久豁免）。
    # A1a 判「位置指对了地方」——比"那一行里有 max(/min("强：后者在同函数体有多处夹紧时
    # 无法分辨是**哪一行**（本节首跑的 8 条报红就是这个原因：位置只写了文件名、没写行号）。
    # A1b 判「兜底真的在」——位置行本身不含 `max(`/`min(` 时（`review.py::_coerce`），
    # 须在**同一个函数体**里另有夹紧或失败兜底。
    for k, (layer, loc, why, sub, _note) in sorted(CHAIN.items()):
        hits = _LOC.findall(loc)
        if not hits:
            rep.add("a1", f"{k} 登记了「{layer}」却没有可核的 `文件:行` 位置")
            continue
        fname, ln = hits[0]
        if not (WEB / fname).exists():
            rep.add("a1", f"{k} 位置里的 {fname} 不存在")
            continue
        line = _text_at(fname, int(ln))
        if sub not in line:
            rep.add("a1", f"{k} 位置 {fname}:{ln} 那一行不含声明的子串 {sub!r}："
                          f"实测那一行是 {line[:70]!r}")
            continue
        if "max(" not in line and "min(" not in line:
            fn = k.split("::")[1]
            body = _fn_body(fname, fn)
            if not body:
                rep.add("a1", f"{k} 函数 {fn} 在 {fname} 里取不到函数体 ⇒ A1b 判不了")
            elif not ("max(" in body or "min(" in body
                      or ("except" in body and "None" in body)):
                rep.add("a1", f"{k} 位置行既无 max(/min(，函数 {fn} 体内也没有夹紧/"
                              f"失败兜底 ⇒ 这一处的「兜底在」是空话")

    # A2：双向相等（§2.5.74）
    for k in sorted(all_found - set(CHAIN)):
        rep.add("a2", f"{k} 实测到但未登记在夹紧链上")
    for k in sorted(set(CHAIN) - all_found):
        rep.add("a2", f"{k} 登记在夹紧链上但实测不到（登记过期）")

    # A3：依据是 D（数据规模）的须当场实测其规模——否则"天然有界"只是一句推测
    for k, (_l, _loc, why, _sub, _note) in sorted(CHAIN.items()):
        if why == "D":
            rep.add("a3", f"{k} 的依据是「数据规模」（D），须由 A3 的实测面证明，"
                          f"但本节没给它配数据规模探针")
        elif why not in ("C", "D"):
            rep.add("a3", f"{k} 的依据档位 {why!r} 不是 C/D")

    # A4：与 §2.5.100 的结论对照——凡它判「无下界夹紧」的，本节须说出在哪一层有
    for k, (in_try, lo, hi) in sorted(direct.items()):
        if lo or hi:
            continue
        if k not in CHAIN:
            rep.add("a4", f"{k} 在 §2.5.100 里判「读取点无夹紧」，"
                          f"本节也没登记它在哪一层有 ⇒ 两节合起来仍是「无夹紧」")
        elif CHAIN[k][0].startswith("L1"):
            rep.add("a4", f"{k} 在 §2.5.100 里判「读取点无夹紧」，"
                          f"本节却登记成 L1（读取点）——两节结论互相矛盾")

    if not quiet:
        n_l1 = sum(1 for v in CHAIN.values() if v[0].startswith("L1"))
        n_else = len(CHAIN) - n_l1
        print(f"\n  A1 夹紧位置在位：{'✓' if not any('a1' in b for b in rep.red) else '✗'}")
        print(f"  A2 登记面 ↔ 实测面：{'✓' if not any('a2' in b for b in rep.red) else '✗'}")
        print(f"  A3 依据性质：{'✓' if not any('a3' in b for b in rep.red) else '✗'}")
        print(f"  A4 与 §2.5.100 对照：{'✓' if not any('a4' in b for b in rep.red) else '✗'}")
        print(f"\n  **夹紧分层：L1 读取点 {n_l1} 处 / 读取点之外 {n_else} 处**"
              f"（后者正是 §2.5.100 的 A3 看不见的那批）")


# ══════════════════════════════════════════════════════════════
# B 段：运行时真值面（上界 + 下界，主库面 + 工作库面）
# ══════════════════════════════════════════════════════════════

def _fill_value(table: str, col: str, i: int) -> object:
    """按列名造一个类型合适的夹具值。

    **`staging_accidents.review_status` 必须填 `'pending'`**：`review.py:207` 默认
    `review_status="pending"` 并据此加 `s.review_status = ?`（L219）——填错这一列
    会让灌进去的 600 行在 `/api/review/queue` 上全被滤掉，B3 于是重新报
    「分辨力不足」（而那看着像「夹具没灌进去」，实际是**过滤列没配对上**）。

    `table` 目前不用，留在签名里：将来某表的列名撞车时按表分岔，不必改调用点。
    """
    if col == "review_status":
        return "pending"
    if col in ("task_id", "raw_id", "stage_id") or col.endswith("_id"):
        return f"fx{i:04d}"
    # ── NOT NULL 列（漏一个整张表就一行都插不进去，且 `_make_lib` 会记 0）──
    # 实测（2026-10-05）：`raw_accidents` 与 `extract_tasks` 各有一组 NOT NULL 列，
    # 首轮全落默认 `None` ⇒ `IntegrityError` **在 i=0 就 break** ⇒ 该表 inserted=0
    # ⇒ B3 报「分辨力不足」。**那不是夹具没配对，是这里少写了列。**
    if col == "source_key":
        return "mem"
    if col == "source_url":
        return f"https://example.invalid/{i:06d}"
    if col == "url_hash":                       # UNIQUE：每行必须不同
        return f"uh{i:06d}"
    if col == "raw_ids":
        return '["fx0000"]'
    if col == "stages":
        return "[]"
    if col in ("status", "text_type", "relevance_verdict"):
        return {"status": "done", "text_type": "single",
                "relevance_verdict": "single"}[col]
    if col in ("source_keys", "params", "stats"):
        return {"source_keys": '["mem"]', "params": "{}", "stats": "{}"}[col]
    if "time" in col or "date" in col or col.endswith("_at"):
        return f"2026-10-05 10:{i % 60:02d}:00"
    return None


def _make_lib() -> tuple[Path, Path, dict[str, int]]:
    """造一份「表齐全 + 四张表各 ROWS 行」的临时工作库。真库一个字节不动。

    返回 `(tmp, dst_path, inserted)`。`inserted` 是**实际插入成功的行数**，
    不是「打算写的行数」——逐行遇 `IntegrityError` 即停（唯一约束挡住时
    如实记下停在哪，否则判据会拿一个从未达成的规模当真值）。
    """
    from _common import main_repo_root
    real = Path(main_repo_root()) / "data" / "caiems_staging.db"
    tmp = Path(tempfile.mkdtemp(prefix="c25101_"))
    dst_path = tmp / "staging.db"

    src = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    src.backup(dst)                     # WAL 库不能用 shutil.copy2
    dst.close()
    src.close()

    from construction_agent.web import staging_db
    staging_db.init_db(dst_path)

    inserted: dict[str, int] = {}
    con = sqlite3.connect(str(dst_path))
    for table in ("collect_tasks", "extract_tasks", "raw_accidents",
                  "staging_accidents"):
        # ⚠「表存不存在」必须问**正在写的这个连接**。首版用的是
        #   `staging_db.has_table(table, strict=False)`——它问的是**模块默认库**
        #   （由 `CAIEMS_STAGING_DB` 决定），与 `con` 指向的 `dst_path` 是
        #   **两个库**。主检里两者恰好一致（环境干净、默认库就是真库，与新库同构），
        #   所以看不出来；**C 段子进程继承了 B 段留下的环境变量**（那份 `mkdtemp`
        #   临时目录已被清掉）⇒ `has_table` 一律 False ⇒ 四张表全记 0、
        #   **一个异常都不抛**（走的是"表不存在"的正常分支）⇒ 夹具规模 0
        #   ⇒ B3 报「分辨力不足」。
        #   这与 §2.5.94 同族：**判据（与判据的操作对象）必须同源**。
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone() is not None
        if not exists:
            inserted[table] = 0         # 表不存在：记 0，不静默跳过
            continue
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        n = 0
        for i in range(ROWS):
            vals = [_fill_value(table, c, i) for c in cols]
            try:
                con.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})", vals)
            except sqlite3.IntegrityError:
                break                   # 唯一约束挡住了：如实记下实际行数
            n += 1
        inserted[table] = n
    con.commit()
    con.close()
    return tmp, dst_path, inserted


def _count(d) -> int | None:
    """从响应里取「这一格返回了几条」。

    **取不到要判 `None`，不能降级成 0**（§2.5.73）——`/api/causes/cooccurrence`
    的条数在 `data.pairs` 下，只看 `data` 是 list 会取不到；
    §2.5.100 对它是 `per[v] = -1`，然后被 `k in (...)` 的名单条件吃掉 ⇒ **静默**。
    """
    if not isinstance(d, dict):
        return None
    if isinstance(d.get("items"), list):
        return len(d["items"])
    data = d.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k in ("pairs", "items", "rows"):
            if isinstance(data.get(k), list):
                return len(data[k])
    return None


def _scale(d, injected: int | None = None) -> int | None:
    """夹具的**真实规模**——用于分辨力判据。

    三档取值，前者取不到才退下一档：
      ① `/api/accidents` 给 `meta.total`（2,623）
      ② `cooccurrence` 给 `data.pair_total`（171）
      ③ `injected`——**夹具的实测已插行数**（由 `_make_lib()` 返回的 `inserted`）

    第三档是必需的：`/api/collect/tasks`（`collect.py:238`）返回体里**没有 meta**，
    `/api/extract/tasks` 同理，光看响应永远取不到规模。但"响应没说"不等于
    "规模未知"——夹具灌了多少行是**跑之前就确定的真值**，直接拿来用。
    三档都取不到才返回 `None`（⇒ 分辨力判不了 ⇒ 报红，不降级成「通过」）。
    """
    if not isinstance(d, dict):
        return injected
    meta = d.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("total"), int):
        return meta["total"]
    data = d.get("data")
    if isinstance(data, dict) and isinstance(data.get("pair_total"), int):
        return data["pair_total"]
    return injected


def part_b(rep: Report, quiet: bool = False) -> dict:
    from _common import main_db_path
    from _serve import point_at

    tmp, staging, inserted = _make_lib()
    try:
        point_at(str(main_db_path()), staging)
        from construction_agent.web.factory import create_app
        c = create_app().test_client()

        if not quiet:
            print("\n" + "═" * 78)
            print("B 段：运行时真值面（上界「巨大值 ≤ 上限」+ 下界「-1/0 不比 1 多」）")
            print("═" * 78)

        b1: list[str] = []      # 上界失效
        b2: list[str] = []      # 下界失效
        b3: list[str] = []      # 夹具分辨力不足（判「未知」，不判「通过」）
        rows: list[str] = []

        for rule, keys in B_CASES.items():
            base = c.get(rule)
            db0 = base.get_json(silent=True)
            scale = _scale(db0, inserted.get(ROUTE_TABLE.get(rule, "")))
            for k, cap in keys.items():
                n1 = _count(c.get(rule, query_string={k: "1"}).get_json(silent=True))
                n_huge = _count(c.get(rule, query_string={k: HUGE}).get_json(silent=True))
                n_lo = {v: _count(c.get(rule, query_string={k: v}).get_json(silent=True))
                        for v in LOWER_VALUES}
                where = "主库" if rule in MAIN_ROUTES else "工作库"
                rows.append(
                    f"  {rule:30s} {k:6s} [{where}] 规模={scale} 上限={cap} "
                    f"1→{n1} 巨→{n_huge} -1→{n_lo['-1']} 0→{n_lo['0']}")

                # B3 分辨力：夹具规模必须**超过**上限，否则「巨→N」证明不了上界生效
                if cap is not None:
                    if scale is None:
                        b3.append(f"{rule} ?{k} 取不到夹具规模 ⇒ 分辨力未知")
                    elif scale <= cap:
                        b3.append(f"{rule} ?{k} 夹具规模 {scale} ≤ 上限 {cap} ⇒ "
                                  f"分辨力不足，‘巨大值返回 {n_huge}’证明不了上界生效")
                    elif n_huge is None:
                        b3.append(f"{rule} ?{k} 巨大值那格取不到条数 ⇒ 分辨力未知")

                if n_huge is None or n1 is None or None in n_lo.values():
                    b3.append(f"{rule} ?{k} 有条数取不到（响应结构不在 _count 的射程）")
                    continue

                # B1 上界：巨大值不得越过登记上限
                if cap is not None and n_huge > cap:
                    b1.append(f"{rule} ?{k}={HUGE} 返回 {n_huge} 条 > 登记上限 {cap}")
                # B2 下界：`-1` 会变 `LIMIT -1`（吐全表）、`0` 会变 `LIMIT 0`（静默空）。
                # 判据取「**不得超过登记上限**」而**不是**「必须与 ?k=1 同档」——
                # 后者在 `size` 上产生假阳性：`accidents_query.py:237-238` 的注释明写
                # 非正数一律退回 `DEFAULT_SIZE`（50）而不截断成 1，理由是「?size=-5 是
                # 调用方写错了，给 1 条会让人以为数据只有一条」——那是**有意归一**，
                # 不是失控。故 `size=-1 → 50 ≤ 200` 应当放行，`limit=-1 → 全表 > 500` 报红。
                for v in LOWER_VALUES:
                    if cap is not None and n_lo[v] is not None and n_lo[v] > cap:
                        b2.append(f"{rule} ?{k}={v} 返回 {n_lo[v]} 条 > 登记上限 {cap} "
                                  f"⇒ 下界未夹紧")

        if not quiet:
            print("\n" + "\n".join(rows))
            print(f"\n  B1 上界有效：{'✓' if not b1 else '✗ %d 处' % len(b1)}   "
                  f"B2 下界有效：{'✓' if not b2 else '✗ %d 处' % len(b2)}   "
                  f"B3 分辨力充足：{'✓' if not b3 else '✗ %d 处' % len(b3)}")

        # ⚠ `MARKS` 是**机器可读出口**，与上面那几行「给人看的输出」是两件事，
        #    **不能共用一个开关**——首版把它写在 `if not quiet:` 里，而 C 段正是
        #    用 `--marks`（即 `quiet=True`）去取它的 ⇒ 子进程 **stdout 全空**、
        #    正则取不到、`c0` 报「基线跑不出 MARKS」，**而主检自己的输出里那句
        #    MARKS 明明白白印着**（§2.5.89：注入没做与判据没报在输出上一模一样；
        #    这里是它的镜像——**出口没关，但被另一个开关顺手关掉了**）。
        #   `n` 是**出口自证**：三个红名单全空，既可能是"跑了且无红"，也可能是
        #   "根本没跑"——两者在 stdout 上一模一样（§2.5.89）。带上格数，C 段才判得动。
        print(f"MARKS {json.dumps({'n': len(rows), 'b1': b1, 'b2': b2, 'b3': b3}, ensure_ascii=False)}")

        for x in b1:
            rep.add("b1", x)
        for x in b2:
            rep.add("b2", x)
        for x in b3:
            rep.add("b3", x)
        return {"b1": b1, "b2": b2, "b3": b3}
    finally:
        _rmtree(tmp)


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# C 段：注入式自证
# ══════════════════════════════════════════════════════════════

#: 注入用例：`(标签, 文件, 旧文本, 新文本, 期望红的档位)`。
#: **每例断言红的「恰好」是哪一档**（§2.5.68）——只断言"退出码非 0"太弱。
#: `live=True` 的须重打 B 段（改的是运行时行为），`live=False` 的只重跑 A 段。
#:
#: ⚠ 期望集合要照着**判据实际覆盖的格**写：第 1 条摘掉的是**整个** `max(1, min(...))`
#:   （上下界同一个表达式），于是 `?top=巨` 不被夹（`b1`）而 `?top=-1` 在 SQLite 里
#:   本就是 `LIMIT -1`＝**不限条数**（`b2`）——**两档同时红**。首版把期望写成 `{b1}`，
#:   实测 `{b1, b2}`，红的是**用例**不是代码（§2.5.74 同款：判据覆盖几格、期望就写几格）。
#:   后两条各自只摘一半（一条留上界、一条留下界），所以各自只红一档——三条并不重复。
DRIFTS: list[tuple[str, str, str, str, set[str], bool]] = [
    ("摘掉 top 的消费函数夹紧（causes.py:265）",
     "causes.py", "    top = max(1, min(100, top))\n", "",
     {"b1", "b2"}, True),
    ("摘掉 size 的上界（accidents_query.py:254）",
     "accidents_query.py",
     "            size=min(MAX_SIZE, size) if size > 0 else DEFAULT_SIZE,",
     "            size=size if size > 0 else DEFAULT_SIZE,",
     {"b1"}, True),
    ("摘掉 limit 的下界（collect.py）",
     "collect.py",
     "limit = max(1, min(int(request.args.get(\"limit\", 50) or 50), 500))",
     "limit = min(int(request.args.get(\"limit\", 50) or 50), 500)",
     {"b2"}, True),
]


# ⚠ 判据档与元数据必须分开：`MARK_KEYS` 才是"红不红"的三档，`n` 是**出口自证**。
#   首版 `{k for k, v in base.items() if v}` 会把 `n`（实测格数，恒非零）算进红集合
#   ⇒ 「基线红集合」永远非空 ⇒ C 段的每条断言都退化成松判，且**看不出来**。
MARK_KEYS = ("b1", "b2", "b3")
EXPECT_GRID = sum(len(v) for v in B_CASES.values())     # B 段应有的实测格数


def _marks_reds(marks: dict) -> set[str] | None:
    """取红集合；**格数不对时返回 `None`**（＝这一次的结论不可用，不是"没有红"）。

    `b1/b2/b3` 全空既可能是「跑了且无红」，也可能是「**根本没跑**」——两者在
    stdout 上一模一样（§2.5.89）。格数就是用来分开这两件事的。
    """
    if marks.get("n") != EXPECT_GRID:
        return None
    return {k for k in MARK_KEYS if marks.get(k)}


def _run_b_marks() -> dict:
    """另起进程跑 B 段，取机器可读 `MARKS`。

    **必须另起进程**——`create_app()` 装配的模块在**导入时**就进了 `sys.modules`，
    同进程改文件对已加载模块无效，"改了但没生效"会让用例**静默失效**（§2.5.84）。

    也**必须换一份干净的环境**：`point_at()` 把 `CAIEMS_DB`/`CAIEMS_STAGING_DB`/
    `CAIEMS_DATA_DIR` 写进了 `os.environ`，子进程默认继承 ⇒ 指向 B 段那份
    **已被清掉的 `mkdtemp` 目录**。首次踩到时表现为「主检里 B3 全绿、C 段的子进程里
    B3 报夹具规模 0」——**同一个脚本两次运行结论不同**，而两边的输出各自都自洽。
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("CAIEMS_")}
    p = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--marks"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300, env=env)
    m = re.search(r"^MARKS (\{.*\})$", p.stdout, re.M)
    if not m:
        return {"_err": (p.stdout[-400:] + p.stderr[-400:])}
    return json.loads(m.group(1))


def part_c(rep: Report) -> None:
    print("\n" + "═" * 78)
    print("C 段：注入式自证（改真实源码，断言红的**恰好**是哪一档）")
    print("═" * 78)

    base = _run_b_marks()
    if "_err" in base:
        rep.add("c0", f"基线跑不出 MARKS：{base['_err']}")
        print("  ✗ 基线失败，C 段无法继续")
        return
    base_red = _marks_reds(base)
    if base_red is None:
        rep.add("c0", f"基线 MARKS 格数不是 {EXPECT_GRID}（实测 n={base.get('n')!r}）"
                      f"——红名单全空说明不了任何事（可能根本没跑）")
        print(f"  ✗ 基线格数不符（n={base.get('n')!r}，应为 {EXPECT_GRID}），C 段无法继续")
        return
    if base_red:
        rep.add("c0", f"基线本身不是绿的：{sorted(base_red)} ⇒ 注入后的红集合无从解释")
        print(f"  ✗ 基线为红 {sorted(base_red)}，C 段无法继续")
        for k in sorted(base_red):
            for x in base[k]:
                print(f"      · {k}: {x}")
        return
    print(f"  基线格数 {base['n']}（应 {EXPECT_GRID}）   基线红集合：空")

    for label, rel, old, new, want, live in DRIFTS:
        path = WEB / rel
        src = path.read_text(encoding="utf-8")
        if src.count(old) != 1:
            rep.add("c", f"{label}：锚点在 {rel} 里命中 {src.count(old)} 次（须恰好 1 次）")
            print(f"  ✗ {label}：锚点命中 {src.count(old)} 次")
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        try:
            marks = _run_b_marks()
        finally:
            path.write_text(src, encoding="utf-8")       # 逐字恢复
        got = _marks_reds(marks)
        if got is None:
            rep.add("c", f"{label}：注入后取不到有效 MARKS（n={marks.get('n')!r}）")
            print(f"  ✗ {label}：注入后无有效 MARKS（n={marks.get('n')!r}）")
            continue
        if got == want:
            print(f"  ✓ {label}：期望 {sorted(want)}，实测 {sorted(got)}")
        else:
            rep.add("c", f"{label}：期望红 {sorted(want)}，实测 {sorted(got)}")
            print(f"  ✗ {label}：期望红 {sorted(want)}，实测 {sorted(got)}")


#: 文档面注入用例：`(标签, 锚点, 期望红的档位集合)`。
#:
#: ⚠ 期望集合要照着**判据实际覆盖的格**写（§2.5.74）。四条各打一处、两两不重叠：
#:   前两条各打 `d1` 的一种形态（标题行 / 锚点词），后两条各打 `d2` 的一支
#:   （更正标注被删 / 被更正的原句被删）。
#: ⚠ 删除型注入**必须删净**（`str.replace(anchor, "")` 全量替换，§2.5.70）——
#:   只删一处等于没删，而"注入没做"与"判据没报"在输出上一模一样（§2.5.89）。
DOC_DRIFTS: list[tuple[str, str, set[str]]] = [
    ("删掉 §2.5.101 标题行", "### 2.5.101 ", {"d1"}),
    ("删净锚点词「L3 消费函数」", "L3 消费函数", {"d1"}),
    ("删掉更正标注", "§2.5.101 更正", {"d2"}),
    ("删掉被更正的原句", "数据结构决定的上限", {"d2"}),
]


def part_c_doc(rep: Report) -> None:
    """D 段判据的失败路径——**文档面的注入自证**（§2.5.68：两个注入面都要接）。

    与源码面（C 段）不同，这里**一个字节都不动真文档**：把 01 号复制进临时目录、
    改写副本、经 `C25101_DOC` 让 `_doc_path()` 读副本。理由同 §2.5.71 的
    「验证手段不能制造它要防的损害」——注入改真文档再 `finally` 恢复，一旦中途
    崩溃留下的就是一份被删了标题行的 01 号文档。

    ⚠ 只测源码面时，**文档面判据改坏了不会有任何用例转红**（§2.5.68 立、§2.5.71
    复立），所以这一面必须自己有一条失败路径。
    """
    print("\n" + "-" * 78)
    print("C 段（文档面）：D 段判据的失败路径")
    print("-" * 78)

    orig = _doc_path().read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="c25101doc_")) / "01.md"
    prev = os.environ.get("C25101_DOC")
    try:
        for label, anchor, want in DOC_DRIFTS:
            n = orig.count(anchor)
            if n == 0:
                # 锚点不在真文档里 ⇒ 用例整批空转，须**点名叫红**而不是静默 continue
                rep.add("cd", f"{label}：锚点在真文档里出现 0 次 ⇒ 用例写错了"
                              f"（这不是判据报红，是注入根本没做）")
                print(f"  ✗ {label}：锚点 0 次（用例错，不是判据报红）")
                continue
            tmp.write_text(orig.replace(anchor, ""), encoding="utf-8")
            os.environ["C25101_DOC"] = str(tmp)
            r = Report()
            part_d(r, quiet=True)
            got = set(r.red)
            if got == want:
                print(f"  ✓ {label}（删净 {n} 处）：期望 {sorted(want)}，实测 {sorted(got)}")
            else:
                rep.add("cd", f"{label}：期望红 {sorted(want)}，实测 {sorted(got)}")
                print(f"  ✗ {label}：期望红 {sorted(want)}，实测 {sorted(got)}")
    finally:
        if prev is None:
            os.environ.pop("C25101_DOC", None)
        else:
            os.environ["C25101_DOC"] = prev
        _rmtree(tmp.parent)


# ══════════════════════════════════════════════════════════════
# D 段：文档与登记文本对账
# ══════════════════════════════════════════════════════════════

def _doc_path() -> Path:
    """01 号文档的位置。**允许环境变量覆盖**——C 段的文档面注入要把判据指向副本。

    ⚠ 不覆盖的话，注入改的是副本、判据读的仍是真文件 ⇒ **注入等于没做**，而
    「注入没做」与「判据没报」在输出上一模一样（§2.5.89；§2.5.95 的 I10 用例
    就是这么静默失效的）。
    """
    override = os.environ.get("C25101_DOC")
    return Path(override) if override else ROOT / "docs" / "web" / "01_在线采集.md"


def part_d(rep: Report, quiet: bool = False) -> None:
    say = (lambda *a, **k: None) if quiet else print
    say("\n" + "═" * 78)
    say("D 段：文档与登记文本对账")
    say("═" * 78)

    doc = _doc_path().read_text(encoding="utf-8")
    if not re.search(r"^### 2\.5\.101 ", doc, re.M):
        rep.add("d1", "01 号文档里没有 §2.5.101 的标题行")
    else:
        say("  ✓ 01 号文档有 §2.5.101 这一节")

    for anchor in ("夹紧链", "L3 消费函数", HUGE):
        if anchor in doc:
            say(f"  ✓ 文档记了「{anchor}」")
        else:
            rep.add("d1", f"文档没记「{anchor}」")

    # D2：**更正标注必须真的挂在被更正的那句话上**（§2.5.67：只写理由不验证
    #     ＝永久豁免；这里是那条规矩的**判据自身版本**）。
    #
    #     ⚠ 首版把锚点取成了 `check_25100_clamp_chain.py` 里 REGISTRY 的两个
    #     键（「上界由数据规模天然兜住」/「top 语义允许 0（返回空）」），而
    #     那两句**从来不在文档里**（文档写的是「规模**天然有界**」「是**数据
    #     结构决定的上限**」）⇒ `s in doc` 恒假 ⇒ **这两条判据永远为绿**。
    #     判据锚在"我以为文档写了什么"上，而不是"文档实际写了什么"上——
    #     与 §2.5.89「注入没做 vs 判据没报在输出上一模一样」同族。
    #
    #     真判据应当是**双向可证伪**的：原句若还在 ⇒ 必须带更正标注；
    #     原句若已被删掉 ⇒ 标注失去锚点，本节第七节的说法得跟着改。
    mark = "§2.5.101 更正"
    sent = "数据结构决定的上限"
    i100 = doc.find("### 2.5.100 ")
    i101 = doc.find("### 2.5.101 ")
    if i100 < 0:
        rep.add("d2", "文档里找不到 §2.5.100 小节——本节要更正的对象的出处")
    elif i101 < 0 or i101 <= i100:
        pass            # d1 已报「没有 §2.5.101 标题行」，不重复报
    else:
        seg = doc[i100:i101]
        if sent not in seg:
            rep.add("d2", f"§2.5.100 里那句过期理由（「…{sent}」）已不在文档里 ⇒ "
                          f"「{mark}」标注失去锚点，本节第七节的说法要跟着改")
        elif mark not in seg:
            rep.add("d2", f"§2.5.100 的过期理由仍无更正标注——"
                          f"「{sent}」还在，但同节里找不到「{mark}」")
        else:
            say(f"  ✓ §2.5.100 的过期理由带「{mark}」标注（原句在、标注也在）")

    if not rep.red:
        say("  ✓ 全部文档判据通过")


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def self_test() -> int:
    """纯函数与 D 段判据的正反用例（项目惯例）。

    ⚠ 「退出码 0」只有在脚本**有能力报 1** 时才可信（§2.5.64）——故每条判据都摆
    两侧：应当通过的与**应当报红**的。只摆顺手的正向断言，等于自己给自己发绿灯。
    """
    bad: list[str] = []
    n_run = 0

    def chk(name: str, cond: bool) -> None:
        nonlocal n_run
        n_run += 1
        print(f"  {'✓' if cond else '✗'} {name}")
        if not cond:
            bad.append(name)

    def d_reds(text: str) -> set[str]:
        """把合成文档喂给 D 段，取红集合（不动真文档，同 `part_c_doc` 的手法）。"""
        tmp = Path(tempfile.mkdtemp(prefix="c25101st_")) / "01.md"
        tmp.write_text(text, encoding="utf-8")
        prev = os.environ.get("C25101_DOC")
        os.environ["C25101_DOC"] = str(tmp)
        try:
            r = Report()
            part_d(r, quiet=True)
            return set(r.red)
        finally:
            if prev is None:
                os.environ.pop("C25101_DOC", None)
            else:
                os.environ["C25101_DOC"] = prev
            _rmtree(tmp.parent)

    print("── _marks_reds：格数就是出口自证（§2.5.89） ──")
    OK = EXPECT_GRID
    chk("全绿 ⇒ 空集合", _marks_reds({"n": OK, "b1": [], "b2": [], "b3": []}) == set())
    chk("b1 非空 ⇒ {b1}", _marks_reds({"n": OK, "b1": ["x"], "b2": [], "b3": []}) == {"b1"})
    chk("b2+b3 非空 ⇒ {b2,b3}",
        _marks_reds({"n": OK, "b1": [], "b2": ["y"], "b3": ["z"]}) == {"b2", "b3"})
    chk("格数不符 ⇒ None（是「未知」，不是「无红」）",
        _marks_reds({"n": OK - 1, "b1": [], "b2": [], "b3": []}) is None)
    chk("缺 n ⇒ None", _marks_reds({"b1": [], "b2": [], "b3": []}) is None)
    chk("空 dict ⇒ None", _marks_reds({}) is None)
    chk("先看格数：b1 有值但 n=0 ⇒ 仍 None",
        _marks_reds({"n": 0, "b1": ["x"]}) is None)
    chk("非判据档的键（why）不参与红集合",
        _marks_reds({"n": OK, "b1": [], "b2": [], "b3": [], "why": ["…"]}) == set())
    chk("MARK_KEYS 里没有元数据键", "n" not in MARK_KEYS and "why" not in MARK_KEYS)
    chk("EXPECT_GRID == 9（B_CASES 改了格数要回来看这里）", EXPECT_GRID == 9)

    print("── part_d：段切分与两支 ──")
    tpl = ("### 2.5.100 甲\n"
           "理由是 数据结构决定的上限 这一条。\n"
           "> **§2.5.101 更正（2026-10-05）**：订正如下。\n"
           "\n### 2.5.101 数值参数的夹紧链\n"
           f"L3 消费函数 与 {HUGE}\n")
    chk("完整合成文档 ⇒ 无红", d_reds(tpl) == set())
    chk("删标题行 ⇒ {d1}", d_reds(tpl.replace("### 2.5.101 ", "")) == {"d1"})
    chk("删锚点词 ⇒ {d1}", d_reds(tpl.replace("L3 消费函数", "")) == {"d1"})
    chk("删更正标注 ⇒ {d2}", d_reds(tpl.replace("§2.5.101 更正", "")) == {"d2"})
    chk("删被更正的原句 ⇒ {d2}", d_reds(tpl.replace("数据结构决定的上限", "")) == {"d2"})
    chk("删 §2.5.100 标题 ⇒ {d2}（找不到被更正的对象，出处本身要报）",
        d_reds(tpl.replace("### 2.5.100 ", "")) == {"d2"})
    chk("§2.5.101 排在 §2.5.100 之前 ⇒ 空集合"
        "（该分支有意 pass：两处报同一个事实会让红集合不唯一，§2.5.93）",
        d_reds("### 2.5.101 夹紧链\nL3 消费函数 " + HUGE + "\n\n### 2.5.100 甲\n"
               "数据结构决定的上限\n") == set())
    chk("更正标注写在 §2.5.101 段内 ⇒ 仍报 {d2}（标注必须挂进被更正的那节）",
        d_reds("### 2.5.100 甲\n数据结构决定的上限\n\n### 2.5.101 夹紧链\n"
               f"L3 消费函数 {HUGE}\n**§2.5.101 更正**\n") == {"d2"})
    chk("§2.5.101 整节连同锚点被删 ⇒ 只剩 {d1}"
        "（d2 的出处随标题一起没了，走 pass 不重复报——否则红集合不唯一）",
        d_reds("### 2.5.100 甲\n甲。\n") == {"d1"})

    print("── _doc_path：环境变量覆盖 ──")
    prev = os.environ.pop("C25101_DOC", None)
    try:
        chk("默认指向 01 号文档",
            _doc_path() == ROOT / "docs" / "web" / "01_在线采集.md")
        os.environ["C25101_DOC"] = "/nonexistent/x.md"
        chk("被 C25101_DOC 覆盖", _doc_path() == Path("/nonexistent/x.md"))
    finally:
        os.environ.pop("C25101_DOC", None)
        if prev is not None:
            os.environ["C25101_DOC"] = prev

    print()
    if bad:
        print(f"❌ --self-test {len(bad)}/{n_run} 条未通过：")
        for b in bad:
            print(f"   · {b}")
        return 1
    print(f"✅ --self-test 全部通过（{n_run}/{n_run}）")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--marks" in sys.argv:
        part_b(Report(), quiet=True)
        return 0
    quiet = "--quiet" in sys.argv
    rep = Report()
    part_a(rep, quiet=quiet)
    part_b(rep, quiet=quiet)
    if "--no-inject" not in sys.argv:
        part_c(rep)
        part_c_doc(rep)
    part_d(rep)

    print("\n" + "═" * 78)
    if rep.clean:
        print("✅ §2.5.101 全部通过：夹紧链无断点、主库面与工作库面的上下界均生效、"
              "夹具分辨力充足")
        return 0
    print("❌ §2.5.101 有待处置项：")
    rep.dump()
    return 1


if __name__ == "__main__":
    sys.exit(main())
