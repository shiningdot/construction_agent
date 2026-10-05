#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.86 写接口的错误分支真值面：兼副作用边界取证。

## 来路

§2.5.85 把 28 条**只读 GET** 路由的错误分支逐个真打了一遍，但它自己框死在
"只读、无副作用"上——`rules = ... if "GET" in r.methods`。于是另外 **13 条含写
方法的路由**（`POST` / `DELETE`，全挂在 `/api/collect`、`/api/extract`、
`/api/review` 三个前缀下）的错误分支至今一次都没被真打过。

写路由相对 GET 多出一整类风险，是本节存在的理由：

| | GET | 写路由 |
|---|---|---|
| 打错输入的后果 | 读错了，状态不变 | **可能改库**（部分写入、幂等性） |
| 打"快乐路径"的后果 | 无 | 起采集线程发网络 / 跑 LLM 花钱 / **把暂存并入主库** |

所以本节不能照抄 §2.5.85 的九场景：**有些格子是不该打的**。把它打出去的代价
不是"多一次请求"，而是"真的改了数据"。于是本节多了两条 GET 那节不需要的判据
——A2（错误输入不得改变任何库）与 A4（不敢打的必须显式登记，不许默默不测）。

## 怎么打才不碰真库

三条措施，缺一条这节就不能做：

1. **先复制、再打**：`sqlite3.Connection.backup()` 把工作库与最终库各复制一份到
   `$TMPDIR`，`CAIEMS_DB` / `CAIEMS_STAGING_DB` 指过去。**不是 `shutil.copy2`**——
   工作库是 WAL，主文件在 checkpoint 之前是滞后的（CLAUDE.md 已记过这个坑，实测
   51 行只备到 1 行）。用 SQLite 自己的 backup API 才拿得到一致快照。
2. **只打错误输入**：本节全部用例都是缺参数 / 类型错 / id 不存在 / 库不可用。凡
   能走到真写入、真线程、真 LLM 的输入，一条都不打。
3. **打前打后对摘要**：每格记录两份库的**逻辑摘要**，前后必须逐字相同。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 每个错误输入必须得到**可识别的失败**（非 2xx，或 2xx 但带 `ok:false`/`error`/`message`） | 错误输入被静默吞成 200 成功 | 红 |
| A2 | 每格打完后，工作库与最终库的**逻辑摘要必须与打前相同** | 部分写入 / 错误分支也落库 | 红 |
| A3 | 闭式：路由 × 用例 = 真打的 + 显式跳过的 | 静默漏格（§2.5.67） | 红 |
| A4 | 每条"有副作用·跳过"必须带文件名与原因，且每条写路由至少有一个**能打**的用例 | 整条路由因为"危险"被跳过 ⇒ 该路由其实一次没测 | 红 |
| B1 | 状态码矩阵 | ——（基线） | 定量 |
| B2 | 错误分支**独有**的键路径 | "只能靠分支兑现"的键清单 | 定量 |
| B3 | 静态面许诺、本节真值面未兑现的写读 | 供裁决（**不判红**：快乐路径根本没打） | 定量 |

## A2 为什么用"逻辑摘要"而不是文件哈希

首版想用 `sha256(文件字节)`，一测就废：`staging_db.py:77/286` 在**每次建连接**时
执行 `PRAGMA journal_mode = WAL`——库一被应用打开就被切回 WAL、文件头跟着变、
`-wal`/`-shm` 冒出来，**一次查询都没发生，字节哈希就已经变了**。那不是"写入"，
是"打开"。

改用逻辑摘要（`sqlite_master` 的 schema + 每张表逐行的 `repr`），并且用
`file:...?mode=ro&immutable=1` 只读打开、不碰文件。两个口径的差别正好是本节要
写的那个道理：**判据的宽度决定结论的强度**——文件哈希会把"打开了"报成"写了"，
逻辑摘要只报"内容真的变了"。

代价要写出来：逻辑摘要**抓不到"改了又改回去"**（净变化为零），也**抓不到纯页
重排**（VACUUM 式整理）。这两类都不是本节要抓的东西（本节要抓"错误输入留下
痕迹"），但边界得说清。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root             # noqa: E402
from _serve import point_at                                  # noqa: E402
import check_2580_request_body as R                          # noqa: E402
import check_2581_response_body as B                         # noqa: E402
import check_2584_live_keys as K                             # noqa: E402

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
MAIN_DB = Path(main_db_path())
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

DOC_NEEDLES = ["### 2.5.86 展示层（续）：写接口的错误分支真值面——**兼副作用边界取证**"]

#: 一个一定不存在的 id/键。用它打「id 不存在」这一档。
FAKE = "__no_such_2586__"

#: 三条"错误必须可识别"的通道。与 §2.5.85 的 `marked()` 同族，但那个是判
#: 「库不可用有没有标记」，这里是判「错误输入有没有被识别」——**同一个词的两次
#: 收窄**：库不可用是一条特定的错误，不能拿它当"所有错误"的判据。
ERR_KEYS = ("error", "message", "hint", "note")

_NOBODY = object()          # 哨兵：这一格不发 body


# ══════════════════════════════════════════════════════════════════
# 库副本与逻辑摘要
# ══════════════════════════════════════════════════════════════════
def _snapshot(src: Path, dst: Path) -> None:
    """用 SQLite 自己的 backup API 复制——WAL 源也能拿到一致快照。"""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    try:
        con.backup(out)
        # 切回 DELETE：副本自洽，不留 -wal/-shm 干扰后面的读取
        out.execute("PRAGMA journal_mode = DELETE")
    finally:
        out.close()
        con.close()


def _logical(p: Path) -> str:
    """逻辑摘要：schema + 每张表逐行。**只读打开，不碰文件字节**。

    ⚠️ **不能用 `immutable=1`**（首版差点栽在这里）：副本一旦被应用打开，
    `staging_db.py:77` 的 `PRAGMA journal_mode = WAL` 就把它切回 WAL——此后
    写入先进 `-wal`、主文件保持旧状态。`immutable=1` 明确告诉 SQLite **忽略
    `-wal`**，于是"真写了"会读成"没写"，A2 变成一条只会报绿的判据。
    用普通 `mode=ro`：读得到 WAL 里的内容，又不改主文件字节。
    """
    if not p.exists():
        return "<不存在>"
    h = hashlib.sha256()
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return f"<打不开：{e}>"
    try:
        rows = list(con.execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name"))
        for name, sql in rows:
            h.update(f"{name}|{sql}\n".encode())
            if name.startswith("sqlite_"):
                continue
            try:
                for row in con.execute(f'SELECT * FROM "{name}"'):
                    h.update((repr(row) + "\n").encode())
            except sqlite3.Error:
                h.update(b"<unreadable>\n")
    finally:
        con.close()
    return h.hexdigest()[:16]


def _file_digest(p: Path) -> str:
    """文件字节摘要——**只作提示、不作判据**（见模块 docstring 末尾）。"""
    if not p.exists():
        return "-"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════
# 用例
# ══════════════════════════════════════════════════════════════════
#: 每条 = `(方法, 路由模板, 用例名, body, 工作库形态, 最终库形态)`。
#: body 为 `_NOBODY` 表示不带 body；`"__RAW__"` 表示发一段非 JSON 文本。
#: 形态 `copy` = 真库副本；`gone` / `blank` / `notdb` = 坏库（本节自建）。
#:
#: **每一条的 body 都是"错误输入"**。判断"这条输入会不会真写"的依据写在
#: `SKIPS` 里——凡不能确证"在写之前就返回"的，一律不打。
CASES: list[tuple] = [
    # ── 采集：建任务（快乐路径会真起采集线程发网络 ⇒ 不提）──
    ("POST", "/api/collect/tasks", "缺 source_keys", {}, "copy", "copy"),
    ("POST", "/api/collect/tasks", "source_keys 空数组",
     {"source_keys": []}, "copy", "copy"),
    ("POST", "/api/collect/tasks", "source_keys 未登记",
     {"source_keys": ["__no_such_source_2586__"]}, "copy", "copy"),
    ("POST", "/api/collect/tasks", "body 非 JSON", "__RAW__", "copy", "copy"),
    ("POST", "/api/collect/tasks", "工作库不可用", {}, "gone", "copy"),
    # ── 采集：任务控制 ──
    ("POST", "/api/collect/tasks/<task_id>/cancel", "任务不存在", _NOBODY,
     "copy", "copy"),
    ("DELETE", "/api/collect/tasks/<task_id>", "任务不存在", _NOBODY,
     "copy", "copy"),
    ("POST", "/api/collect/tasks/<task_id>/cancel", "工作库不可用", _NOBODY,
     "gone", "copy"),
    ("DELETE", "/api/collect/tasks/<task_id>", "工作库不可用", _NOBODY,
     "gone", "copy"),
    # ── 采集：闸门裁决（⚠️ 假 id 仍会走 write_tx，是本节的"部分写入"探针）──
    ("POST", "/api/collect/raw/gate", "raw_ids 缺失", {}, "copy", "copy"),
    ("POST", "/api/collect/raw/gate", "raw_ids 空数组",
     {"raw_ids": [], "verdict": "confirmed"}, "copy", "copy"),
    ("POST", "/api/collect/raw/gate", "verdict 非法",
     {"raw_ids": [FAKE], "verdict": "__bogus__"}, "copy", "copy"),
    ("POST", "/api/collect/raw/gate", "raw_id 不存在（进写事务但匹配 0 行）",
     {"raw_ids": [FAKE], "verdict": "confirmed"}, "copy", "copy"),
    ("POST", "/api/collect/raw/gate", "工作库不可用",
     {"raw_ids": [FAKE], "verdict": "confirmed"}, "gone", "copy"),
    # ── 提取：建任务（快乐路径写暂存；start 后跑 LLM ⇒ 都不提）──
    ("POST", "/api/extract/tasks", "raw_ids 缺失", {}, "copy", "copy"),
    ("POST", "/api/extract/tasks", "raw_ids 非数组",
     {"raw_ids": "abc"}, "copy", "copy"),
    ("POST", "/api/extract/tasks", "raw_ids 空数组",
     {"raw_ids": []}, "copy", "copy"),
    ("POST", "/api/extract/tasks", "raw_id 不存在",
     {"raw_ids": [FAKE]}, "copy", "copy"),
    ("POST", "/api/extract/tasks", "工作库不可用",
     {"raw_ids": [FAKE]}, "gone", "copy"),
    # ── 提取：任务控制 ──
    ("POST", "/api/extract/tasks/<task_id>/start", "任务不存在", _NOBODY,
     "copy", "copy"),
    ("POST", "/api/extract/tasks/<task_id>/cancel", "任务不存在", _NOBODY,
     "copy", "copy"),
    ("DELETE", "/api/extract/tasks/<task_id>", "任务不存在", _NOBODY,
     "copy", "copy"),
    ("POST", "/api/extract/tasks/<task_id>/start", "工作库不可用", _NOBODY,
     "gone", "copy"),
    # ── 提取：重跑（快乐路径删旧行 + 跑 LLM ⇒ 不提）──
    ("POST", "/api/extract/results/<stage_id>/rerun", "暂存条目不存在", _NOBODY,
     "copy", "copy"),
    ("POST", "/api/extract/results/<stage_id>/rerun", "工作库不可用", _NOBODY,
     "gone", "copy"),
    # ── 审核：单条裁决（真 id + approve 会写 ⇒ 只用假 id）──
    ("POST", "/api/review/items/<stage_id>", "暂存条目不存在", _NOBODY,
     "copy", "copy"),
    ("POST", "/api/review/items/<stage_id>", "暂存条目不存在（带 action）",
     {"action": "approve"}, "copy", "copy"),
    ("POST", "/api/review/items/<stage_id>", "工作库不可用", _NOBODY,
     "gone", "copy"),
    # ── 审核：批量通过 ──
    ("POST", "/api/review/batch-approve", "stage_ids 缺失", {}, "copy", "copy"),
    ("POST", "/api/review/batch-approve", "stage_ids 空数组",
     {"stage_ids": []}, "copy", "copy"),
    ("POST", "/api/review/batch-approve", "stage_id 全不存在（approved 为空 ⇒ 不进写事务）",
     {"stage_ids": [FAKE]}, "copy", "copy"),
    ("POST", "/api/review/batch-approve", "工作库不可用",
     {"stage_ids": [FAKE]}, "gone", "copy"),
    # ── 审核：并入最终库（⚠️ 只在错误输入下打）──
    ("POST", "/api/review/promote", "stage_ids 缺失", {}, "copy", "copy"),
    ("POST", "/api/review/promote", "stage_ids 空数组",
     {"stage_ids": []}, "copy", "copy"),
    ("POST", "/api/review/promote", "stage_id 不存在且无令牌",
     {"stage_ids": [FAKE]}, "copy", "copy"),
    ("POST", "/api/review/promote", "工作库不可用",
     {"stage_ids": [FAKE]}, "gone", "copy"),
    # ── 审核：回滚（⚠️⚠️ 工作库正常时**绝不打**——无参数、无令牌，直接回滚主库）──
    ("POST", "/api/review/rollback", "工作库不可用（文件不存在）", _NOBODY,
     "gone", "copy"),
    ("POST", "/api/review/rollback", "工作库不可用（空库）", _NOBODY,
     "blank", "copy"),
    ("POST", "/api/review/rollback", "工作库不可用（非 SQLite）", _NOBODY,
     "notdb", "copy"),
    # ── 坏形态的覆盖面：三条工作库坏法各打一遍主力路由 ──
    ("POST", "/api/collect/raw/gate", "工作库是空库",
     {"raw_ids": [FAKE], "verdict": "confirmed"}, "blank", "copy"),
    ("POST", "/api/collect/raw/gate", "工作库不是 SQLite",
     {"raw_ids": [FAKE], "verdict": "confirmed"}, "notdb", "copy"),
    ("POST", "/api/review/promote", "工作库是空库",
     {"stage_ids": [FAKE]}, "blank", "copy"),
    ("POST", "/api/review/promote", "工作库不是 SQLite",
     {"stage_ids": [FAKE]}, "notdb", "copy"),
]

#: `(方法, 路由, 为什么不敢打)`——**每一条都必须有名有因**（A4 判据）。
#: 这不是"漏测"，是"测了会改数据"。不加这张表，本节就会变成"打了 13 条路由里
#: 的 13 条"的假象，实际上每条路由只有错误面被验过。
SKIPS: list[tuple] = [
    ("POST", "/api/collect/tasks",
     "快乐路径 `executor.submit_task()` 真起采集线程、真发网络请求"),
    ("POST", "/api/collect/raw/gate",
     "合法 raw_id + 合法 verdict 会真 UPDATE raw_accidents"),
    ("POST", "/api/extract/tasks",
     "合法 raw_ids 会真写 staging_accidents/extract_tasks"),
    ("POST", "/api/extract/tasks/<task_id>/start",
     "启动一个 pending 任务 ⇒ `run_one` 真调 DeepSeek、花 token"),
    ("POST", "/api/extract/results/<stage_id>/rerun",
     "先 DELETE 旧暂存行再跑一遍 LLM（**覆盖不是留版本**）"),
    ("POST", "/api/review/items/<stage_id>",
     "真 stage_id + action=approve/reject/edit 会真 UPDATE 审核状态"),
    ("POST", "/api/review/batch-approve",
     "真 stage_id 命中会真 UPDATE review_status='approved'"),
    ("POST", "/api/review/promote",
     "`pm.execute()` 把暂存并入**主库**（无 confirm_token 才被拦）"),
    ("POST", "/api/review/rollback",
     "无参数、无令牌，直接 `pm.rollback()` 撤销上一次并入——"
     "工作库正常时打它等于真回滚主库，故本节只在工作库不可用时打"),
]


class Bad:
    """三种"坏库"形态，落在临时目录里。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.gone = root / "gone.db"
        self.blank = root / "blank.db"
        self.notdb = root / "notdb.db"
        if self.blank.exists():
            self.blank.unlink()
        sqlite3.connect(self.blank).close()
        self.notdb.write_text("这不是一个 SQLite 数据库文件。\n" * 8,
                              encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# 打一格
# ══════════════════════════════════════════════════════════════════
def _fill(rule: str) -> str:
    """路由模板 → 实 URL：所有变量填 `FAKE`。"""
    return re.sub(r"<[^>]*>", FAKE, rule)


def _ident_of(rule: str) -> str:
    """这条路由带哪个变量名（`task_id` / `stage_id` / `raw_id`）。"""
    m = re.search(r"<([^>]*)>", rule)
    return m.group(1) if m else ""


def _send(client, method: str, url: str, body):
    if body is _NOBODY:
        return client.open(url, method=method)
    if body == "__RAW__":
        return client.open(url, method=method, data="not json at all",
                           content_type="application/json")
    return client.open(url, method=method, json=body)


def _err_expr(j) -> tuple[bool, str]:
    """这份响应有没有「我失败了」的可识别表达。"""
    if not isinstance(j, dict):
        return True, "非 JSON"
    if j.get("ok") is False:
        return True, "ok:false"
    for k in ERR_KEYS:
        v = j.get(k)
        if isinstance(v, str) and v.strip():
            return True, f"{k}"
    d = j.get("data")
    if isinstance(d, dict):
        for k in ERR_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return True, f"data.{k}"
    return False, ""


def _batch_expr(j) -> str:
    """**批量**接口的第三种失败表达：计数与逐条原因。

    首版 A1 只认 `error`/`message`/`hint`/`note` 四个键，实测报出两条假红：

    | 格 | 真响应 | 为什么不是缺陷 |
    |---|---|---|
    | `raw/gate` 假 raw_id | `200 {ok:true, data:{updated:0, missing:1}}` | 批量裁决，**部分 id 不存在**用 `missing` 计数表达比整批 400 更贴切；前端可据此提示 |
    | `batch-approve` 假 stage_id | `200 {ok:true, data:{requested:1, approved:0, skipped:[{reason:"记录不存在"}]}}` | 源码注释明写「不静默跳过：把原因带回去」 |

    两条都**不是**"错误被吞掉"，而是"失败用另一种形状说出来了"。判据的毛病与
    §2.5.66 同族：**接收者没收窄**——把"单目标接口的失败"与"批量接口的部分失败"
    混在一个判据里。故 A1 一分为二：

    * **A1a 单目标**（id 在 URL 路径里）：必须 `ok:false` 或非 2xx。整条请求就是
      对一个对象做的事，做不成**没有第二种说法**。
    * **A1b 批量**（id 在 body 数组里）：允许 200，但**必须能在 `data` 里指出
      哪些没做成**——`missing` / `skipped` / `updated:0` / `approved < requested`
      四者之一。否则就是"整批一个都没成，却回了 `ok:true`"。

    注意这不是"放宽到随便什么都行"：`({"ok": True, "data": {"updated": 0}}`.
    这种**只报计数、不报"没做成的有哪些"**的形状仍然报红（C1 打的正是它）。
    """
    if not isinstance(j, dict):
        return ""
    d = j.get("data")
    if not isinstance(d, dict):
        return ""
    if isinstance(d.get("missing"), int) and d["missing"] > 0:
        return f"missing={d['missing']}"
    if isinstance(d.get("skipped"), list) and d["skipped"]:
        return f"skipped={len(d['skipped'])}"
    if "missing" in d and d.get("updated") == 0:
        return "updated=0"
    a, r = d.get("approved"), d.get("requested")
    if isinstance(a, int) and isinstance(r, int) and a < r:
        return f"approved {a}/{r}"
    return ""


def sweep(app, bad: Bad, copies: dict) -> list[dict]:
    """把 `CASES` 逐格真打一遍，每格前后对库摘要。"""
    c = app.test_client()
    out: list[dict] = []
    for method, rule, name, body, st_shape, fin_shape in CASES:
        st = copies["staging"] if st_shape == "copy" else getattr(bad, st_shape)
        fin = copies["final"] if fin_shape == "copy" else getattr(bad, fin_shape)
        point_at(str(fin), str(st))
        before = (_logical(Path(copies["staging"])), _logical(Path(copies["final"])))
        fbefore = (_file_digest(Path(copies["staging"])),
                   _file_digest(Path(copies["final"])))
        url = _fill(rule)
        try:
            r = _send(c, method, url, body)
            status = r.status_code
            j = r.get_json(silent=True)
            raw = "" if j is not None else r.get_data(as_text=True)[:100]
        except Exception as exc:            # noqa: BLE001 —— 崩了也是结论
            status, j, raw = None, None, f"{type(exc).__name__}: {exc}"
        after = (_logical(Path(copies["staging"])), _logical(Path(copies["final"])))
        fafter = (_file_digest(Path(copies["staging"])),
                  _file_digest(Path(copies["final"])))
        got, emp = (set(), set())
        if isinstance(j, dict):
            K.walk(j, "", got, emp)
        identified, how = _err_expr(j)
        out.append({
            "method": method, "rule": rule, "name": name, "id_name": _ident_of(rule),
            "url": url, "shape": (st_shape, fin_shape), "status": status,
            "top": sorted(j.keys()) if isinstance(j, dict) else [],
            "paths": got if isinstance(j, dict) else None,
            "jobj": j, "batch_how": _batch_expr(j) if isinstance(j, dict) else "",
            "raw": raw, "identified": identified, "how": how,
            "st_changed": before[0] != after[0],
            "fin_changed": before[1] != after[1],
            "st_bytes_changed": fbefore[0] != fafter[0],
            "fin_bytes_changed": fbefore[1] != fafter[1],
            "digest": (before, after),
        })
    point_at(str(MAIN_DB), str(STAGING_DB))
    return out


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════
def test_a1(rows) -> tuple[list, list, list]:
    """A1a 单目标 / A1b 批量：错误输入必须得到可识别的失败。

    `id_name` 非空 ⟺ 这条路由的变量在 URL 路径里 ⟺ 一次调用只针对一个对象。
    这是本判据唯一的分档依据，也是它不该被合并的理由（见 `_batch_expr`）。
    """
    reds_a, reds_b, conf_b = [], [], []
    for r in rows:
        if r["id_name"]:                       # ── 单目标 ──
            if r["identified"]:
                continue
            reds_a.append(f"{r['method']} {r['rule']}「{r['name']}」→ "
                          f"HTTP {r['status']}，既非 2xx-带错误标记、也无 "
                          f"`error`/`message`/`hint`/`note`  顶层={r['top']}")
            continue
        # ── 批量 ──
        if r["identified"]:
            conf_b.append(f"{r['rule']}「{r['name']}」→ {r['how']}")
            continue
        if r["batch_how"]:
            conf_b.append(f"{r['rule']}「{r['name']}」→ {r['batch_how']}")
            continue
        reds_b.append(f"{r['method']} {r['rule']}「{r['name']}」→ HTTP {r['status']}、"
                      f"`ok:true`，且 data 里没有 `missing`/`skipped`/"
                      f"`updated:0`/`approved<requested` 任何一种"
                      f"  顶层={r['top']} data={r['jobj'].get('data') if isinstance(r['jobj'], dict) else None}")
    return reds_a, reds_b, conf_b


def test_a2(rows) -> list[str]:
    """A2：**错误输入不得改变任何库**（本节招牌判据）。"""
    reds = []
    for r in rows:
        if not (r["st_changed"] or r["fin_changed"]):
            continue
        which = []
        if r["st_changed"]:
            which.append("工作库")
        if r["fin_changed"]:
            which.append("最终库")
        reds.append(f"{r['method']} {r['rule']}「{r['name']}」改了"
                    f"{'、'.join(which)}的逻辑内容（HTTP {r['status']}）")
    return reds


def test_a3(rows, routes_w) -> tuple[list, dict]:
    """A3 闭式 + A4：跳过项必须有因，且每条写路由至少有一条能打的用例。"""
    reds: list[str] = []
    covered = {f"{r['method']} {r['rule']}" for r in rows}
    all_routes = {f"{m} {rule}" for m, rule, _ep in routes_w}
    skipped = {f"{m} {rule}" for m, rule, _why in SKIPS}

    # A3：真打的 + 显式跳过的 **必须覆盖全部写路由**——否则就是静默漏了一条
    uncovered = all_routes - covered - skipped
    for x in sorted(uncovered):
        reds.append(f"{x} 既没被真打、也没登记为跳过")

    # A4a：跳过项必须有原因（空原因 = 等于没登记）
    for m, rule, why in SKIPS:
        if not why.strip():
            reds.append(f"跳过项 {m} {rule} 没写原因")

    # A4b：**每条写路由至少有一条能打的用例**——整条被跳过就等于该路由一次没测
    for x in sorted(all_routes):
        if x not in covered:
            reds.append(f"{x} 只有跳过项、一条能打的用例都没有（该路由等于没测）")

    # A4c：登记为跳过的路由，不能同时又有"快乐路径"式的用例混进来
    return reds, {"covered": len(covered), "skipped_routes": len(skipped),
                  "all": len(all_routes)}


# ══════════════════════════════════════════════════════════════════
# 段
# ══════════════════════════════════════════════════════════════════
FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return cond


def part_a(rows) -> None:
    print("═══ A. 实测 ═══")
    by_route: dict = {}
    for r in rows:
        by_route.setdefault(f"{r['method']} {r['rule']}", []).append(r)
    print(f"  写路由 {len(by_route)} 条，真打 {len(rows)} 格"
          f"（另有登记跳过 {len(SKIPS)} 条路由的快乐路径）")
    for route in sorted(by_route):
        rs = by_route[route]
        codes = sorted({r["status"] for r in rs})
        keys: set = set()
        for r in rs:
            keys |= set(r["top"])
        print(f"    {route:46s} {len(rs)} 格  状态码 {codes}")
        print(f"      └ 顶层键并集 {sorted(keys)}")


def part_b(rows, routes_w, fe_w) -> dict:
    print("\n═══ B. 判据 ═══")
    a1a, a1b, conf_b = test_a1(rows)
    n_single = sum(1 for r in rows if r["id_name"])
    ok(not a1a, "A1a 单目标写路由的失败都用状态码/错误键表达",
       f"{n_single - len(a1a)}/{n_single}")
    for x in a1a:
        print(f"      ✗ {x}")
    ok(not a1b, "A1b 批量写路由必须指出哪些没做成（计数/逐条原因）",
       f"{len(conf_b)}/{len(rows) - n_single}")
    for x in a1b:
        print(f"      ✗ {x}")
    for x in conf_b[:4]:
        print(f"      · {x}")

    a2 = test_a2(rows)
    ok(not a2, "A2 错误输入没有改变任何库的逻辑内容")
    for x in a2:
        print(f"      ✗ {x}")

    a3, stat = test_a3(rows, routes_w)
    ok(not a3, "A3/A4 覆盖闭式 + 跳过项有名有因 + 每条路由至少一格能打",
       f"覆盖 {stat['covered']}/{stat['all']} 条写路由"
       f"（其中 {stat['skipped_routes']} 条另有登记跳过项）")
    for x in a3:
        print(f"      ✗ {x}")

    # 文件字节口径 vs 逻辑口径：两者不一致的格子单独列出来（写进文档的那段分析）
    byte_only = [r for r in rows if (r["st_bytes_changed"] or r["fin_bytes_changed"])
                 and not (r["st_changed"] or r["fin_changed"])]
    print(f"\n  · 文件字节变了、逻辑内容没变：{len(byte_only)} 格"
          f"（`PRAGMA journal_mode=WAL` 在建连接时执行 ⇒ 打开即改文件头）")
    for r in byte_only[:4]:
        print(f"      · {r['method']} {r['rule']}「{r['name']}」")
    both = [r for r in rows if (r["st_bytes_changed"] or r["fin_bytes_changed"])
            and (r["st_changed"] or r["fin_changed"])]
    print(f"  · 两个口径都变了：{len(both)} 格")

    # B2：只出现在少数格子里的键路径
    cnt: Counter = Counter()
    for r in rows:
        for p in (r["paths"] or ()):
            cnt[p] += 1
    only = sorted(p for p, n in cnt.items() if n <= 2)
    print(f"\n  定量：{len(cnt)} 条键路径里，只在 ≤2 格出现的有 {len(only)} 条")
    for p in only[:14]:
        print(f"    · {p}  ({cnt[p]} 格)")

    # B3：静态面许诺、本节真值面未兑现的写读（**不判红**——快乐路径没打）
    unmet: list[str] = []
    for h in fe_w:
        rule = h.get("rule") or ""
        if not rule:
            continue
        got: set = set()
        for r in rows:
            if r["rule"] == rule and r["paths"]:
                got |= r["paths"]
        for p in sorted(h.get("paths") or ()):
            if p in got or p + "[]" in got:
                continue
            unmet.append(f"{h['file']}:{h['line']} {h['method']} {rule} 读 `{p}`")
    print(f"\n  · 静态面许诺、本节真值面未兑现的写读 {len(unmet)} 条"
          f"（**不判红**：本节只打了错误路径，快乐路径的键本来就打不到）")
    for x in unmet[:10]:
        print(f"      · {x}")

    return {"a1a": a1a, "a1b": a1b, "conf_b": conf_b, "a2": a2, "a3": a3,
            "stat": stat, "only": only,
            "unmet": unmet, "byte_only": byte_only, "n_keys": len(cnt),
            "reds": a1a + a1b + a2 + a3}


# ══════════════════════════════════════════════════════════════════
# C. 注入式自证
# ══════════════════════════════════════════════════════════════════
def _child_marks(*args) -> dict:
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--marks", *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=900)
    for line in (r.stdout or "").splitlines():
        if line.startswith("MARKS "):
            return json.loads(line[6:])
    raise RuntimeError(f"子进程没打出 MARKS：stdout={r.stdout[-500:]!r} "
                       f"stderr={r.stderr[-500:]!r}")


def _inject_reds(edits: list, *args) -> tuple[set, str]:
    """改真实文件 → 跑子进程 → `finally` 恢复（§2.5.85 的多文件多处版）。"""
    saved: list[tuple[Path, str]] = []
    for fname, old, new in edits:
        path = DOC if fname == "01_在线采集.md" else WEB / fname
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
    ("C1", "A1b 批量写路由必须指出哪些没做成（计数/逐条原因）", "源码面",
     [("collect.py",
       '        return jsonify({"ok": False, "error": "bad_verdict",\n'
       '                        "message": f"verdict 只能是 {list(VERDICTS)}"}), 400',
       '        return jsonify({"ok": True, "data": {"updated": 0}}), 200')],
     "把 `/api/collect/raw/gate` 的「verdict 非法」分支改成**静默成功**"
     "（`200 {ok:true, data:{updated:0}}`）⇒ 那一格的失败表达消失：既无错误键、"
     "`data` 里也没有 `missing`/`skipped`/`approved<requested` ⇒ 打 A1b。\n"
     "**同一路由的「raw_id 不存在」那格照旧绿**（它走的是分支**之后**的 "
     "`write_tx`，响应里带 `missing=1`）⇒ 打红的是**恰好一格**，不是整条路由。\n"
     "**不打 A1a**：「verdict 非法」的 id 在 body 数组里，属批量档。\n"
     "**不打 A2**：改的这一支仍在 `write_tx` 之前返回，库里一个字没动。\n"
     "**不打 A3**：路由照旧被真打、跳过项照旧登记，覆盖闭式不变。"),
    ("C2", ("A2 错误输入没有改变任何库的逻辑内容",
            "副本打完后仍与真库同内容"), "源码面",
     [("collect.py",
       '''    if verdict not in VERDICTS:
        return jsonify({"ok": False, "error": "bad_verdict",
''',
       '''    if verdict not in VERDICTS:
        with staging_db.write_tx() as _c:
            _c.execute("CREATE TABLE IF NOT EXISTS __probe_2586__(x)")
        return jsonify({"ok": False, "error": "bad_verdict",
''')],
     "**本节招牌注入**：在 `verdict 非法` 这条错误分支里塞一句建表语句——"
     "错误输入仍然返回同一个 400（A1 照绿），但库的逻辑内容真的变了 ⇒ 打 A2。\n"
     "**它同时打红「副本打完后仍与真库同内容」那条守卫**——那是**正确的连带**、"
     "不是遮蔽：注入真的往副本里建了表，守卫自然要报。两条说的是同一个既成事实的"
     "两个独立视角（A2 从「内容变了」报、守卫从「副本脏了」报），而守卫在**基线**上"
     "是绿的 ⇒ 它不掩盖 A2。\n"
     "**这条要证的是「A2 有能力报红」**：一个永远发现不了写入的判据，"
     "退出码 0 只是「我没看」（§2.5.67）。\n"
     "语义上这不是「真写业务数据」，但判据抓的是「错误路径碰了库」——"
     "**建表与 UPDATE 在这条判据眼里是同一件事**，这正是我们要的宽度。"),
    ("C3", "A3/A4 覆盖闭式 + 跳过项有名有因 + 每条路由至少一格能打", "源码面",
     [("review.py",
       '''@review_bp.post("/rollback")
def rollback():''',
       '''@review_bp.post("/rollback-typo-2586")
def rollback():''')],
     "把 `/api/review/rollback` 的**路由路径改掉**（`url_map` 里少一条写路由）"
     "⇒ 它名下的 3 格用例打不到（404 到别处）而 `SKIPS` 里还登记着它 ⇒ "
     "覆盖闭式缺口 ⇒ 打 A3。\n"
     "**只有 A3 依赖 `url_map` 的路由全集**，A1/A2 都是逐格判的 ⇒ 归因唯一。"),
]


def part_c(rep) -> dict:
    print("\n═══ C. 注入式自证 ═══")
    base = _child_marks()
    base_names = set(base.get("names") or [])
    print(f"  基线（未注入）：红 {len(base_names)} 条")
    out = {"base": base}
    ok(not base_names, "基线必须是绿的（有红就没有可归因的基线）")
    if base_names:
        for n in sorted(base_names):
            print(f"      ✗ {n}")
        return out

    doc_label = f"文档锚点：{DOC_NEEDLES[0]}"
    cases = list(INJECTS) + [
        ("C4", doc_label, "文档面",
         [("01_在线采集.md", "\n### 2.5.86 ", "\n### 2.5.86x ")],
         "改文档里的本节标题 ⇒ 打 D 段那一条锚点；正文与源码都不动"),
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
        #: `want` 允许给多条（元组/集合）——有些注入会**真实地**触发两条判据
        #: （C2 就是：建表既改了库内容，也让"副本仍与真库同内容"这条守卫报警）。
        #: 前者是靶子、后者是**正确的连带**，不是互相遮蔽：两条说的是同一个既成
        #: 事实的两个独立视角，且守卫那条在**基线**上是绿的（不会掩盖 A2）。
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
    out.append(("真打格数", f"真打 **{rep['n_rows']}** 格"))
    out.append(("写路由条数", f"{rep['n_routes']} 条写路由"))
    out.append(("跳过项条数", f"登记跳过 **{rep['n_skips']}** 条"))
    out.append(("键路径条数", f"{rep['n_keys']} 条键路径"))
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
def collect() -> dict:
    point_at(str(MAIN_DB), str(STAGING_DB))
    from construction_agent.web.factory import create_app

    app = create_app()
    routes_w, _reads, calls = R.collect_all()
    wrap = B.wrapper_reads()
    fe_w = K.fe_paths(B.fe_side(calls, wrap))
    # 绑到写路由：`K.bind_rules` 用 `h["path"]`（§2.5.83 的字段名），写调用点
    # 用的是 §2.5.80 的 `url` ⇒ 这里自己绑，并**加上方法匹配**
    # （§2.5.85 的教训：判据的接收者要收窄，跨方法的同路径不算数）。
    rules_w2 = [(m, rule, ep) for m, rule, ep in routes_w if "." in ep]
    for h in fe_w:
        h["rule"], h["ep"] = "", ""
        for m, rule, ep in rules_w2:
            if m == h.get("method") and R.rule_re(rule).match(h["url"]):
                h["rule"], h["ep"] = rule, ep
                break

    tmp = Path(tempfile.mkdtemp(prefix="c2586_"))
    copies = {"staging": tmp / "staging.db", "final": tmp / "final.db"}
    rows: list[dict] = []
    try:
        _snapshot(STAGING_DB, copies["staging"])
        _snapshot(MAIN_DB, copies["final"])
        bad = Bad(tmp)
        rows = sweep(app, bad, copies)
        # 打完之后，两份副本必须与真库仍是同一份逻辑内容——这是 A2 的反向断言：
        # **副本本身在被写之前是干净的**。少了这条，"副本早就脏了"与"用例写了"
        # 在输出上分不开。
        clean = (_logical(copies["staging"]) == _logical(STAGING_DB)
                 and _logical(copies["final"]) == _logical(MAIN_DB))
    finally:
        point_at(str(MAIN_DB), str(STAGING_DB))
        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows, "routes_w": routes_w, "fe_w": fe_w, "clean": clean}


def run() -> dict:
    got = collect()
    rows = got["rows"]
    part_a(rows)
    b = part_b(rows, got["routes_w"], got["fe_w"])
    print(f"\n  {'✓' if got['clean'] else '✗'} 副本打完后仍与真库同内容"
          f"（证「干净副本」这个前提成立）")
    if not got["clean"]:
        FAILED.append("副本打完后仍与真库同内容")
    return {**got, **b, "n_rows": len(rows),
            "n_routes": len({f"{r['method']} {r['rule']}" for r in rows}),
            "n_skips": len(SKIPS)}


def main() -> int:
    if "--marks" in sys.argv:
        try:
            r = run()
            part_d(r)
        except Exception as exc:            # noqa: BLE001
            print(f"MARKS {json.dumps({'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}")
            return 1
        print("MARKS " + json.dumps({"names": FAILED}, ensure_ascii=False))
        return 0

    print("§2.5.86 写接口的错误分支真值面（兼副作用边界取证）\n")
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
