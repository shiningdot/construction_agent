"""§2.5.92 破坏性写操作的确认级别：要删的东西，问过谁。

## 这一节要回答什么

§2.5.80–§2.5.87 把**写路由**的前后端契约（入参面、响应面、错误分支、前端消费面）
逐格核过一遍，§2.5.86 还专门在**临时副本**上真打了 43 格错误输入。那些节问的是
「**接口通不通**」；本节换一个此前没人问过的问题：

> **一次会删/覆盖数据的操作，问过谁？**

判据不是「有没有确认」这种是非题——`window.confirm` 到处都是，写了不等于有用。
判据是**确认的强度是否与后果的不可逆程度相称**：

| 级别 | 形态 | 挡得住什么 |
|---|---|---|
| **L0** | 无确认 | 什么都不挡 |
| **L1** | 客户端确认（`window.confirm`） | **只防误点**。绕过前端直接 `curl` 即无效——它是一句 UI 话术，不是一个判据 |
| **L2** | 服务端凭证（不带能对上**既有状态**的入参就拒绝） | 客户端之外的一切调用方 |

`promote` 是 L2 的样板：`issue_token` → 一次性令牌（12 位 hex、落 `promote_plans`
表、有 `PLAN_TTL` 过期、**绑定 stage_ids**）→ `consume_token` 校验后**立即删除**，
且执行前**强制备份最终库**。它的 docstring 自己写着为什么不默认 dry_run：
「这样『没带 confirm_token 的请求』一定会被 `consume_token` 拦下，而不是悄悄
降级成一次无害的预演、让调用方以为成了」。

## 实测（修复前）

| 路由 | 目标库 | 删/覆盖 | 服务端凭证 | 客户端确认 |
|---|---|---|---|---|
| `POST /api/review/promote` | **最终库** | 写 | ✅ 一次性令牌 | ✅ 弹层二次确认 |
| `POST /api/review/rollback` | **最终库** | **删** | ❌ **零凭证** | ✅ `window.confirm` |
| `DELETE /api/collect/tasks/<id>` | 工作库 | 删 | ❌ | ❌ **连 confirm 都没有** |
| `DELETE /api/extract/tasks/<id>` | 工作库 | 删 | ❌ | ✅（文案还写明"产出会保留"） |
| `POST /api/extract/results/<id>/rerun` | 工作库 | 覆盖 | ❌ | ✅（写明"会先删掉""会产生新费用"） |

两处不相称，**方向相反**：

1. **`rollback` 删的是最终库，防线却只在客户端**——前端 `review.js:888` 有
   `window.confirm`，后端 `review.py:586` 的 handler 一个入参都不读（前端发的
   就是空对象 `post('/api/review/rollback', {})`）。§2.5.86 早就记过这一条，
   当时归在「不敢打的快乐路径」里**跳过**了——本节把它变成会响的判据。
2. **同为删任务，`extract` 问了、`collect` 没问**——同一个动作在两条链路上
   确认级别不同，差别不在后果（都只删工作库的**任务行**，产出都保留），
   而在于**写这两段代码时有没有顺手加**。

## 判据

- **A 段（客户端确认面，静态）**：13 条写路由逐条绑到前端调用点，取**调用点所在
  的函数体**（不是"前后 N 行窗口"——§2.5.87 立过：字符窗口是假判据），
  核里面有没有 `window.confirm`。
- **B 段（服务端凭证面，运行时）**：目标为**最终库**的路由，在 `tempfile.mkdtemp()`
  下的**副本**上打一次「不带任何凭证」的请求，断言**被拒且最终库逻辑内容未变**。
  副本用 SQLite `backup()` API 复制（§2.5.86：WAL 库不能用 `shutil.copy2`）。
  **必须先把库造出「有可回滚的并入记录」的状态**——否则 `rollback` 会因为
  "没有可回滚的并入记录"报 409，判据变成恒真、什么都没测到（§2.5.77：
  判据在多数样本上恒真就等于没有）。
- **C 段（分级判据）**：把 A/B 的实测代入 `LEVEL_MIN` —— 实测级别 < 要求即报红。
- **D 段（文档对账）**：01/09/10/11 号、`CLAUDE.md`、`scripts/e2e/README.md`。

## 判据边界（明写）

1. 本节核的是**"问过谁"**，核不到**"问得对不对"**——`window.confirm` 的文案写得
   准不准（`extract.js` 那两句写了"产出会保留""会产生新费用"，是好的；换一句
   含糊的照样绿）不在射程内。
2. **L2 只验"不带凭证被拒"**，不验凭证本身够不够强（令牌位数、TTL 长短、
   是否绑定选择集都没核；`promote` 的那几项是读代码看到的，不是本节测出来的）。
3. **L1 的判定是本节的立场，不是标准**——「客户端确认只防误点」这个判断写在
   `LEVEL_MIN` 里，作者若认为某类操作 L1 足够，改的是那张表、不是判据结构。
4. 本节只覆盖**前端有调用点**的写路由；某个路由若根本没有前端调用面
   （§2.5.80 的 A2 已经单独核过这一维），它在 A 段的确认面是"没有客户端确认"
   而不是"漏测"。
5. B 段只打**目标最终库**的两条。工作库的删除动作**不打**——它们删的是可以
   重跑重采的任务行，打它们的收益远小于"又要在副本上造一批状态"的成本，
   如实登记在案而不是假装核过。
"""
from __future__ import annotations

import importlib
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
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_repo_root                            # noqa: E402

C80 = importlib.import_module("check_2580_request_body")
C86 = importlib.import_module("check_2586_write_error_branches")
C87 = importlib.import_module("check_2587_frontend_error_face")

PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
ALIGN = ROOT / "docs" / "web" / "09_端到端对齐核查.md"
TODO = ROOT / "docs" / "web" / "10_待执行清单.md"
SNAP = ROOT / "docs" / "web" / "11_执行状态快照_2026-10-05.md"
CM = ROOT / "CLAUDE.md"
E2E = ROOT / "scripts" / "e2e" / "README.md"

WEB = ROOT / "construction_agent" / "web"
JS = sorted((WEB / "static").glob("*.js"))

#: 最终库与工作库（与 §2.5.86 同源：主仓库 data/ 下那两份）
FINAL_DB = main_repo_root() / "data" / "caiems_v2.db"
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

#: 确认级别
L0, L1, L2 = 0, 1, 2

#: 最低要求：**这张表是本节唯一的立场，其余全是实测**
LEVEL_MIN = {
    ("final", True): L2,      # 删/覆盖最终库：不可逆，影响的是论文数据
    ("final", False): L2,     # 写最终库同理
    ("staging", True): L1,    # 删/覆盖工作库已有产出：可重跑，但误点的代价是钱或重采
    ("staging", False): L0,   # 只改工作库状态：无要求
}

#: 13 条写路由的登记表。
#:   target     —— 作用哪个库（"final" | "staging"）
#:   destruct   —— 是否删除或覆盖**已有**数据（新建/改状态都不算）
#:   proof      —— 该判断在 handler 源码段里必须能命中的子串（可证伪判据，
#:                 §2.5.67 的规矩：只写理由不验证＝永久豁免）
ROUTES: list[tuple[str, str, str, str, bool]] = [
    # (method, rule, 模块.函数, target, destruct)
    ("POST",   "/api/collect/tasks",              "collect.api_create_task", "staging", False),
    ("POST",   "/api/collect/tasks/<task_id>/cancel", "collect.api_cancel_task", "staging", False),
    ("DELETE", "/api/collect/tasks/<task_id>",    "collect.api_delete_task", "staging", True),
    ("POST",   "/api/collect/raw/gate",           "collect.api_gate_raw",    "staging", False),
    ("POST",   "/api/extract/tasks",              "extract.api_create_task", "staging", False),
    ("POST",   "/api/extract/tasks/<task_id>/start", "extract.api_start_task", "staging", False),
    ("POST",   "/api/extract/tasks/<task_id>/cancel", "extract.api_cancel_task", "staging", False),
    ("DELETE", "/api/extract/tasks/<task_id>",    "extract.api_delete_task", "staging", True),
    ("POST",   "/api/extract/results/<stage_id>/rerun", "extract.api_rerun",  "staging", True),
    ("POST",   "/api/review/items/<stage_id>",    "review.review_item",      "staging", False),
    ("POST",   "/api/review/batch-approve",       "review.batch_approve",    "staging", False),
    ("POST",   "/api/review/promote",             "review.promote",          "final",   False),
    ("POST",   "/api/review/rollback",            "review.rollback",         "final",   True),
]

#: 写入口家族——**从实测反推的**，不是猜的。要证明"这条路由写的是哪个库"，
#: 只能靠它到底经哪个入口写；而工作库的入口不止一个（`staging_db.write_tx`
#: 是通用事务，两个执行器各有一套自己的助手）。
#: 家族不完备时 A3 会报红（新增一条经别的入口写的路由 ⇒ 家族该扩了），
#: 这使它是一个**会过期的清单**而不是白名单（§2.5.67）。
STAGING_WRITERS = (
    "staging_db.write_tx(",         # 通用工作库事务
    "executor.submit_task(", "executor.cancel_task(",   # web/collect.py 的执行器
    "ex.create_task(", "ex.start_task(", "ex.cancel_task(",
    "ex.delete_task(", "ex.run_one(",                   # web/extract.py 的执行器
)
#: 写最终库的**唯一**入口。全项目只有 `review/promote.py` 能写最终库——
#: `web/db.py` 只提供只读句柄，那是**故意的**（`web/db.py:41-48` 附近有说明）。
FINAL_WRITERS = ("pm.execute(", "pm.rollback(")


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"　{extra}" if extra else ""))
    return cond


# ══════════════════════════════════════════════════════════════
# 源码面：路由 → handler 源码段
# ══════════════════════════════════════════════════════════════


def handler_src(ident: str) -> tuple[str, str] | None:
    """取 `模块.函数` 的函数体源码。返回 (模块名, 源码) —— 取不到返回 None。"""
    mod, _, fn = ident.partition(".")
    path = WEB / f"{mod}.py"
    if not path.is_file():
        return None
    import ast
    txt = path.read_text(encoding="utf-8")
    tree = ast.parse(txt)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            seg = "\n".join(txt.splitlines()[node.lineno - 1:node.end_lineno])
            return mod, seg
    return None


# ══════════════════════════════════════════════════════════════
# 前端面：调用点 → 所属函数体 → 有没有 window.confirm
# ══════════════════════════════════════════════════════════════

FN_HEAD = re.compile(
    r"(?:\bfunction\s+(\w+)\s*\(|"
    r"\b(?:var|let|const)\s+(\w+)\s*=\s*function\s*\(|"
    r"\b(\w+)\s*:\s*function\s*\()")


def _span_at(txt: str, m: re.Match) -> tuple[int, int, int] | None:
    """从一处函数定义匹配点出发，按大括号配平取 `(起点, 函数体起点, 结束)`。

    **为什么不用 §2.5.87 的 `_fn_span`**：它按 `function <名字>(` 反查，认不出
    `var f = function () {` 与 `{ k: function () {` 这两种写法（实测两条自检
    用例因此报红），而本项目三个前端文件里这两种写法都有。此外按名反查在
    "文件里有两个同名函数"时会取错——本节要的恰恰是"离调用点最近的那一个"。
    配平的字符跳过复用 `C87._skip_str`／`C87._QUOTES`（同一套实现，不另写）。
    """
    b = txt.find("{", m.end())
    if b < 0:
        return None
    depth = 1
    j = b + 1
    while j < len(txt):
        c = txt[j]
        if c in C87._QUOTES:
            j = C87._skip_str(txt, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return m.start(), b, j + 1
        j += 1
    return None


def enclosing_fn(txt: str, pos: int) -> tuple[str, int, int] | None:
    """返回包含 `pos` 的**最内层**函数名与 [起点, 结束)。

    先扫出**全部**函数定义位置，再取包含 `pos` 且 body 最短的那个
    （最短 = 最内层，因为外层的 body 必然包含内层）。
    """
    best: tuple[str, int, int] | None = None
    for m in FN_HEAD.finditer(txt):
        name = m.group(1) or m.group(2) or m.group(3)
        sp = _span_at(txt, m)
        if sp is None or not (sp[0] <= pos < sp[2]):
            continue
        if best is None or (sp[2] - sp[0]) < (best[2] - best[1]):
            best = (name, sp[0], sp[2])
    return best


def client_confirm(call: dict) -> bool:
    """调用点所属函数体里有没有 `window.confirm`。"""
    path = WEB / "static" / call["file"]
    txt = path.read_text(encoding="utf-8")
    #: 行号 → 字符位置
    pos = sum(len(l) + 1 for l in txt.splitlines()[:call["line"] - 1])
    enc = enclosing_fn(txt, pos)
    body = txt[enc[1]:enc[2]] if enc else ""
    return "window.confirm" in body


def fe_confirm_table() -> dict[str, dict]:
    """破坏性路由 → 前端确认面实测。按 URL 模板与路由表匹配。"""
    calls = C80.js_calls()
    out: dict[str, dict] = {}
    for method, rule, ident, _target, destruct in ROUTES:
        if not destruct:
            continue
        rx = C80.rule_re(rule)
        hits = [c for c in calls
                if c["method"] == method and rx.fullmatch(c["url"] or "")]
        rec = [{"file": c["file"], "line": c["line"],
                "confirm": client_confirm(c)} for c in hits]
        out[ident] = {"rule": rule, "calls": rec,
                      "confirm": any(r["confirm"] for r in rec)}
    return out


# ══════════════════════════════════════════════════════════════
# 运行时面：副本上打"不带凭证"的请求
# ══════════════════════════════════════════════════════════════


def seed_rollbackable(sdb: Path, fdb: Path) -> dict:
    """把副本造出「有一条可回滚的并入记录」的状态。

    挑一条**真实存在且带编码**的事故当靶子：只有这样删出来的差异才看得见
    （挑一条没有编码的事故，`removed_causes` 恒 0，与"什么都没删"区分不开）。
    """
    con = sqlite3.connect(fdb)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT a.id, a.title FROM accidents a "
        "WHERE EXISTS (SELECT 1 FROM caics_causes c WHERE c.accident_id = a.id) "
        "ORDER BY a.id LIMIT 1").fetchone()
    con.close()
    if row is None:
        raise RuntimeError("最终库里找不到「有编码」的事故，造不出可回滚状态")
    target = row["id"]

    sid = "C2592TEST"
    con = sqlite3.connect(sdb)
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute(
        "INSERT OR REPLACE INTO staging_accidents (stage_id, raw_id, title, "
        "review_status, created_at) VALUES (?,?,?,?,?)",
        (sid, target, row["title"], "approved", "2026-10-05T00:00:00"))
    con.execute(
        "INSERT INTO promote_log (token, stage_ids, promoted, causes_written, "
        "backup_path, created_at, rolled_back_at) VALUES (?,?,?,?,?,?,NULL)",
        ("c2592token", json.dumps([sid]), 1, 1, "/tmp/c2592.db",
         "2026-10-05T00:00:00"))
    promo_id = con.execute(
        "SELECT id FROM promote_log WHERE token = ?", ("c2592token",)).fetchone()[0]
    con.commit()
    con.close()
    return {"accident_id": target, "stage_id": sid, "promo_id": promo_id}


def probe(app, fdb: Path, seeded: dict) -> list[dict]:
    """打两条目标最终库的路由，每次前后取最终库逻辑摘要。"""
    c = app.test_client()
    out: list[dict] = []

    def shot(method: str, url: str, body) -> dict:
        before = C86._logical(fdb)
        r = c.open(url, method=method, json=body)
        after = C86._logical(fdb)
        try:
            j = r.get_json() or {}
        except Exception:                                     # pragma: no cover
            j = {}
        return {"status": r.status_code, "json": j,
                "db_changed": before != after}

    out.append({"ident": "review.promote", "want": "reject",
                "note": "带 stage_ids 但不带 confirm_token",
                "res": shot("POST", "/api/review/promote",
                            {"stage_ids": [seeded["stage_id"]], "dry_run": False})})
    out.append({"ident": "review.rollback", "want": "reject",
                "note": "空 body（修复前就是这一格删了库）",
                "res": shot("POST", "/api/review/rollback", {})})
    out.append({"ident": "review.rollback", "want": "reject",
                "note": "promote_id 指错（页面过期时的情形）",
                "res": shot("POST", "/api/review/rollback",
                            {"promote_id": int(seeded["promo_id"]) + 999})})
    #: 正例放最后 —— 它会**真的**删掉副本里那条（副本，不是真库）。
    #: 没有这一格，"所有请求都被拒"与"该拒的拒了"在输出上分不开（§2.5.86）。
    out.append({"ident": "review.rollback", "want": "accept",
                "note": "指名当前可回滚的那条 —— 应当成功",
                "res": shot("POST", "/api/review/rollback",
                            {"promote_id": seeded["promo_id"]})})
    return out


def run_runtime() -> tuple[list[dict], dict]:
    """起一份副本环境，真打两条主库路由。"""
    tmp = Path(tempfile.mkdtemp(prefix="c2592_"))
    fdb = tmp / "final.db"
    sdb = tmp / "staging.db"
    C86._snapshot(FINAL_DB, fdb)
    C86._snapshot(STAGING_DB, sdb)
    seeded = seed_rollbackable(sdb, fdb)

    old = {k: os.environ.get(k) for k in ("CAIEMS_DB", "CAIEMS_STAGING_DB")}
    os.environ["CAIEMS_DB"] = str(fdb)
    os.environ["CAIEMS_STAGING_DB"] = str(sdb)
    try:
        from construction_agent.web.factory import create_app
        app = create_app()
        return probe(app, fdb, seeded), {"tmp": str(tmp), **seeded}
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ══════════════════════════════════════════════════════════════
# A / B / C 段
# ══════════════════════════════════════════════════════════════


def part_a() -> list[str]:
    """登记面：`target` 必须能被 handler 源码段里的写入口证明。

    双向：
      - 登记 `final` ⇒ 必须命中 `FINAL_WRITERS`（证明它真能写最终库）
      - 登记 `staging` ⇒ 必须命中 `STAGING_WRITERS` **且不命中** `FINAL_WRITERS`
        （前半句证明它真写了工作库而不是纯读；后半句证明它没被登记低）
    """
    fails: list[str] = []
    for method, rule, ident, target, _d in ROUTES:
        got = handler_src(ident)
        if got is None:
            fails.append(f"A1 {ident} 找不到 handler 源码（{method} {rule}）")
            continue
        _mod, src = got
        hits_final = [n for n in FINAL_WRITERS if n in src]
        hits_staging = [n for n in STAGING_WRITERS if n in src]
        if target == "final":
            if not hits_final:
                fails.append(
                    f"A2 {ident} 登记 target=final 却找不到写最终库的入口"
                    f"（{method} {rule}）——应为 {' 或 '.join(FINAL_WRITERS)}")
        else:
            if hits_final:
                fails.append(
                    f"A3 {ident} 登记 target=staging 但函数体里出现写最终库的入口 "
                    f"({'/'.join(hits_final)}）（{method} {rule}）——目标库登记反了")
            elif not hits_staging:
                fails.append(
                    f"A4 {ident} 登记 target=staging 却找不到任何工作库写入口"
                    f"（{method} {rule}）——不是纯读路由就是写入口家族该扩了")
    if not fails:
        print(f"  A 段 {len(ROUTES)} 条写路由的目标库登记全部被源码证明 ✓")
    return fails


def part_b() -> tuple[list[str], dict[str, bool]]:
    """服务端凭证面：不带凭证打主库路由，须被拒且库未变。

    判据按 `want` 分两档（§2.5.86：单目标与批量要分开判）——
    `reject` 格（无凭证/错 id）**必须**被拒且库不能动；`accept` 格（指名正确）
    **必须**成功且库必须真的动。少了后者，"一律拒绝"也会全绿。
    """
    fails: list[str] = []
    cred: dict[str, bool] = {}
    rejected_by: dict[str, list[bool]] = {}
    rows, meta = run_runtime()
    print(f"  （副本 {meta['tmp']}｜靶子事故 {meta['accident_id']}）")
    for r in rows:
        res, want = r["res"], r["want"]
        rejected = res["status"] >= 400
        if want == "reject":
            rejected_by.setdefault(r["ident"], []).append(rejected)
        else:
            rejected_by.setdefault(r["ident"], []).append(True)
        msg = str(res["json"].get("message") or res["json"].get("error") or "")
        #: 被接受时把"真干了什么"打出来——"库变了"是一个布尔值，
        #: 而 `removed_accidents: 1` 是一个**可以拿去对账的**数字
        data = res["json"].get("data")
        if isinstance(data, dict) and not msg:
            msg = "、".join(f"{k}={v}" for k, v in data.items()
                            if isinstance(v, (int, str, bool)))[:56]
        print(f"    {r['ident']:<17}{'拒绝' if want == 'reject' else '接受'}　"
              f"→ {res['status']}　库{'变了' if res['db_changed'] else '未变'}　"
              f"{msg[:38]}　← {r['note']}")
        if want == "reject":
            if not rejected:
                fails.append(
                    f"B1 {r['ident']} 在「{r['note']}」下仍返回 {res['status']}"
                    f"（服务端没有凭证校验）")
            elif res["db_changed"]:
                fails.append(f"B2 {r['ident']} 虽被拒（{res['status']}）"
                             f"但最终库被改动")
        else:
            if rejected:
                fails.append(f"B3 {r['ident']} 指明了正确的 promote_id 却仍被拒"
                             f"（{res['status']}：{msg[:40]}）"
                             f"——不是『任何输入都拦』，是判据过严")
            elif not res["db_changed"]:
                fails.append(f"B4 {r['ident']} 返回成功但最终库没有任何改动"
                             f"——所谓『确认』之后什么都没发生")
    for ident, flags in rejected_by.items():
        cred[ident] = all(flags)
    if not fails:
        print("  B 段 无凭证/错 id 全被拒且库未变、指名正确时真删掉了靶子 ✓")
    return fails, cred


def measured_level(ident: str, cred: dict[str, bool], fe: dict) -> int:
    if cred.get(ident):
        return L2
    if fe.get(ident, {}).get("confirm"):
        return L1
    return L0


def part_c(cred: dict[str, bool]) -> list[str]:
    """分级判据：实测级别 < 要求即报红。"""
    fe = fe_confirm_table()
    fails: list[str] = []
    print(f"  {'路由':<40}{'目标':<9}{'删/覆盖':<8}{'实测':<6}{'要求':<6}")
    for method, rule, ident, target, destruct in ROUTES:
        need = LEVEL_MIN[(target, destruct)]
        got = measured_level(ident, cred, fe)
        tag = "final" if target == "final" else "staging"
        mark = "✓" if got >= need else "✗"
        print(f"  {mark} {method} {rule:<36}{tag:<9}"
              f"{'是' if destruct else '否':<8}L{got:<5}L{need:<5}")
        if got < need:
            why = ("服务端零凭证，防线只在客户端" if got == L1
                   else "连客户端确认都没有")
            fails.append(f"C1 {ident}（{method} {rule}）后果需要 L{need}，"
                         f"实测 L{got}——{why}")
    #: 前端确认面的细节：谁有、谁没有
    print("  前端确认面：")
    for ident, rec in sorted(fe.items()):
        for c in rec["calls"]:
            print(f"    {'有' if c['confirm'] else '无'}　"
                  f"{c['file']}:{c['line']}　{ident}")
        if not rec["calls"]:
            print(f"    ——　（无前端调用点）　{ident}")
    return fails


# ══════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════


def part_d() -> list[str]:
    """每条失败带编号 D1–D6（§2.5.91：不编号则用例断言不出"恰好是哪一条"）。"""
    fails: list[str] = []
    checks = [
        (DOC, r"^### 2\.5\.92 ", "01 号 §2.5.92 标题行"),
        (ALIGN, r"§2\.5\.92", "09 号引用 §2.5.92"),
        (TODO, r"check_2592_confirm_level", "10 号清单登记本节脚本"),
        (SNAP, r"§2\.5\.92", "11 号快照登记本节"),
        (CM, r"§2\.5\.92", "CLAUDE.md 登记本节"),
        (E2E, r"check_2592_confirm_level\.py", "e2e/README 只读脚本表登记本节脚本"),
    ]
    for i, (path, pat, what) in enumerate(checks, start=1):
        if not path.is_file():
            fails.append(f"D{i} {what}：文件不存在 {path}")
            continue
        if not re.search(pat, path.read_text(encoding="utf-8"), flags=re.M):
            fails.append(f"D{i} {what}：锚点未命中 {pat}")
    if not fails:
        print(f"  D 段 {len(checks)} 处文档锚点全中 ✓")
    return fails


# ══════════════════════════════════════════════════════════════
# 自检与注入式自证
# ══════════════════════════════════════════════════════════════


def self_test() -> int:
    """判据自身的正反用例（不碰真库、不碰真源码）。"""
    bad = total = 0

    def chk(cond: bool, label: str, extra: str = "") -> None:
        nonlocal bad, total
        total += 1
        if not ok(cond, label, extra):
            bad += 1

    #: 1. 最低要求表
    for args, want in ((("final", True), L2), (("final", False), L2),
                       (("staging", True), L1), (("staging", False), L0)):
        got = LEVEL_MIN[args]
        chk(got == want, f"最低要求 {args} → L{want}",
            "" if got == want else f"实测 L{got}")
    #: 2. 实测级别四格矩阵
    for cred, conf, want in ((True, False, L2), (True, True, L2),
                             (False, True, L1), (False, False, L0)):
        got = measured_level("x", {"x": cred}, {"x": {"confirm": conf}})
        chk(got == want, f"实测级别 cred={cred} confirm={conf} → L{want}",
            "" if got == want else f"实测 L{got}")
    #: 3. enclosing_fn：嵌套时取最内层、外层可辨认、函数外返回 None
    src = ("function outer() {\n  function inner() {\n"
           "    x();\n  }\n  y();\n}\nz();\n")
    g = enclosing_fn(src, src.index("x();"))
    chk(g is not None and g[0] == "inner", "enclosing_fn 取最内层", f"实测 {g}")
    g = enclosing_fn(src, src.index("y();"))
    chk(g is not None and g[0] == "outer", "enclosing_fn 取外层", f"实测 {g}")
    g = enclosing_fn(src, src.index("z();"))
    chk(g is None, "函数外返回 None", f"实测 {g}")
    #: 4. enclosing_fn 认三种写法（`function f()` / `var f = function()` / `k: function()`）
    for head, name in (("function alpha() {", "alpha"),
                       ("var beta = function () {", "beta"),
                       ("obj = { gamma: function () {" , "gamma")):
        s = head + "\n  window.confirm('x');\n};\n"
        g = enclosing_fn(s, s.index("window.confirm"))
        chk(g is not None and g[0] == name, f"enclosing_fn 认 {head.split()[0]}",
            f"实测 {g}")
    #: 5. client_confirm 正反（建 file，再指 WEB 过去）
    d = Path(tempfile.mkdtemp(prefix="c2592st_"))
    (d / "static").mkdir()
    (d / "static" / "yes.js").write_text(
        "function a() {\n  if (!window.confirm('ok?')) return;\n  fetch('/x');\n}\n",
        encoding="utf-8")
    (d / "static" / "no.js").write_text(
        "function b() {\n  fetch('/x', { method: 'DELETE' });\n}\n",
        encoding="utf-8")
    real = globals()["WEB"]
    globals()["WEB"] = d
    try:
        for fn, want in (("yes.js", True), ("no.js", False)):
            got = client_confirm({"file": fn, "line": 2, "url": "", "method": "X"})
            chk(got is want, f"client_confirm({fn}) → {want}", f"实测 {got}")
    finally:
        globals()["WEB"] = real
    #: 6. 写入口家族两个方向（用真源码里的字符串，不用编的）
    s = "with staging_db.write_tx() as con:\n    con.execute('DELETE ...')"
    chk(any(n in s for n in STAGING_WRITERS) and not any(n in s for n in FINAL_WRITERS),
        "工作库写入口 → 家族认得、且不误判为最终库")
    s = "outcome = pm.rollback()"
    chk(any(n in s for n in FINAL_WRITERS), "最终库写入口 → 家族认得")
    print(f"self-test：{total - bad}/{total}")
    return 1 if bad else 0


def markers(fails: list[str]) -> list[str]:
    """把失败行压成「档 + 标识」的记号，供注入式自证断言"红的**恰好**是哪几条"。

    逐字比对整行不现实（B 段两条 `reject` 格的文案里只有 id 不同），故取前两个
    词；第二个词带 `.` 才当标识用（`B1 review.rollback` / `C1 collect.api_delete_task`
    / `D6`）。**这是 §2.5.68 立的规矩**：只断言"退出码非 0"太弱，"一律拒绝"与
    "该拒的拒了"在输出上就分不开了。
    """
    out = []
    for f in fails:
        #: 标识本身只含标识符字符——**不能按空格切**：`C1 collect.api_delete_task（DELETE`
        #: 的第二个"词"连全角括号一起切进来了（首轮四个用例有两个因此假红）。
        m = re.match(r"([A-Z]\d+)\s+([A-Za-z_][A-Za-z0-9_.]*)", f)
        #: 第二个词带 `.` 才当标识用——`D6 e2e/README …` 里的 `e2e` 是文件名
        #: 片段、`B1 review.rollback` 里的才是 `模块.函数`
        if m and "." in m.group(2):
            out.append(f"{m.group(1)} {m.group(2)}")
        else:
            out.append(f.split()[0])
    return sorted(out)


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--inject-test" in sys.argv:
        return inject_test()

    print("=" * 72)
    print("§2.5.92 破坏性写操作的确认级别：要删的东西，问过谁")
    print("=" * 72)

    a = part_a()
    b, cred = part_b()
    c = part_c(cred)
    d = part_d()
    fails = a + b + c + d
    print("=" * 72)
    if fails:
        for f in fails:
            print(f"  ✗ {f}")
        if "--marks" in sys.argv:
            print("MARKS " + json.dumps(markers(fails), ensure_ascii=False))
        print(f"共 {len(fails)} 项待处置")
        return 1
    if "--marks" in sys.argv:
        print("MARKS []")
    print("✅ 全部通过")
    return 0


# ══════════════════════════════════════════════════════════════
# 注入式自证：判据真的会报红吗（§2.5.64：退出码 0 只有在脚本
# 有能力报 1 时才可信）
# ══════════════════════════════════════════════════════════════

#: 四个注入面。**两个注入面都要测**（§2.5.68）：源码面三例 + 文档面一例。
#:   path      被改的真实文件
#:   subs      [(旧串, 新串)] —— 逐个 replace，一处都不许漏（§2.5.89：
#:             `replace(old,new,1)` 只换一处，判据若在别处也有同名子串
#:             就会静默失效）
#:   want      期望红的**恰好**是哪几条
INJECTS = [
    dict(tag="I1", path=lambda: WEB / "static" / "collect.js",
         subs=[("window.confirm", "__never_confirm__")],
         want=["C1 collect.api_delete_task"],
         why="删掉 collect.js 的客户端确认 ⇒ 那条路由从 L1 掉回 L0"),
    dict(tag="I2", path=lambda: ROOT / "construction_agent" / "review" / "promote.py",
         subs=[("if expect_id is None:", "if False:"),
               ('if str(entry["id"]) != str(expect_id):', "if False:")],
         want=["B1 review.rollback", "B3 review.rollback", "C1 review.rollback"],
         why="摘掉 promote.py 两处 expect_id 校验 ⇒ 空 body 与错 id 都被放行、"
             "掉回 L1；**B3 是正确连带**——第一条 reject 探针此时真把靶子删了，"
             "轮到正例时 `last_promotion()` 已返回 None（§2.5.86：正确的连带"
             "不是遮蔽，两条判据是两个独立视角）"),
    dict(tag="I3", path=lambda: Path(__file__),
         subs=[('"review.promote",          "final",   False',
                '"review.promote",          "staging", False')],
         want=["A3 review.promote"],
         why="把 promote 的目标库登记改成 staging ⇒ 源码里却写着 pm.execute()"),
    dict(tag="I4", path=lambda: E2E,
         subs=[("check_2592_confirm_level", "__gone_2592__")],
         want=["D6"],
         why="文档面：把 e2e/README 里的脚本名抹掉 ⇒ D6 锚点未命中"),
]


def inject_test() -> int:
    """逐个注入、逐个断言，红的**恰好**是那几条。`try/finally` 逐字恢复现场。"""
    print("=" * 72)
    print("§2.5.92 注入式自证：把判据该看见的东西改坏，看它报不报")
    print("=" * 72)
    bad = 0
    for case in INJECTS:
        p: Path = case["path"]()
        orig = p.read_text(encoding="utf-8")
        if any(o not in orig for o, _n in case["subs"]):
            print(f"  ✗ {case['tag']} 锚点未命中（原文里找不到要替换的串）"
                  f"——用例根本没跑，不是判据没报")
            bad += 1
            continue
        mod = orig
        for o, n in case["subs"]:
            mod = mod.replace(o, n)
        try:
            p.write_text(mod, encoding="utf-8")
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                "--marks"],
                               capture_output=True, text=True, cwd=str(ROOT))
            out = r.stdout + r.stderr
            line = [l for l in out.splitlines() if l.startswith("MARKS ")]
            got = json.loads(line[0][6:]) if line else None
            if got is None:
                print(f"  ✗ {case['tag']} 没读到 MARKS 行（rc={r.returncode}）")
                print("      " + out[-400:].replace("\n", "\n      "))
                bad += 1
                continue
            same = got == sorted(case["want"])
            print(f"  {'✓' if same else '✗'} {case['tag']} 红的恰好是 "
                  f"{case['want']}　—　{case['why']}"
                  + ("" if same else f"　实测 {got}"))
            if not same:
                bad += 1
        finally:
            p.write_text(orig, encoding="utf-8")
    print(f"inject-test：{len(INJECTS) - bad}/{len(INJECTS)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
