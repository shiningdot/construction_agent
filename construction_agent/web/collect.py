"""阶段①「在线采集」的页面与 API。蓝图 01 的落地。

## 与展示层的分工

`api.py` 读**最终库**（只读），本模块读**工作库**（读写）。两个库的边界不
混：采集页看到的原始文本、任务进度、闸门判定全部来自 `caiems_staging.db`，
一条都不会出现在最终的展示页上——它们要先经过阶段②③的提取与人工审核。

## 工作库还没建的时候不能 500

`caiems_staging.db` 要跑 `scripts/create_staging_db.py` 才有。用户第一次打开
`/collect` 时它多半不存在。这里所有查询都先过 `_has_table()`：

- **源清单以 `registry` 为准**，左连接表里的运行期状态。所以库没建也返回
  完整的 24 条，只是健康度列全空——代码才是源清单的唯一真源，表只是它的
  运行记录。这条让蓝图 01 §8 验收第 1 条（返回 24 条）在空库上也成立。
- 其余端点返回 `unavailable` 标记 + 建库命令，前端据此给出「先去跑这个脚本」
  的提示，而不是甩一个 500 页面。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from ..collector import executor, registry
from . import staging_db

collect_bp = Blueprint("collect", __name__, url_prefix="/api/collect")

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200

# 人工裁决只认这三个值。`review` 在这里的含义是「退回待判」——把自动闸门的
# 判定清掉、挂回人工队列，而不是第三种结论。
VERDICTS = ("confirmed", "rejected", "review")

BUILD_HINT = "工作库未建：请先运行 python3 scripts/create_staging_db.py"

# 取消只到源级：24 个适配器都是一次性 `return` 整个列表，中间没有可检查
# 标志位的 yield 点。这句话成功与失败两条分支都要说——用户点了取消之后
# 页面显示「取消中」却还在跑，不解释一句会被当成按钮没生效。
CANCEL_NOTE = "取消会在当前源采集完成后生效（适配器没有更细的中断点）"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _has_table(name: str) -> bool:
    try:
        return bool(staging_db.scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,), default=0))
    except Exception:  # noqa: BLE001 —— 库文件损坏等极端情况也不该 500
        return False


def _unavailable() -> dict:
    """工作库不可用时的统一响应体。前端认 `available: false`。"""
    return {"ok": True, "available": False, "hint": BUILD_HINT, "data": []}


# ══════════════════════════════════════════════════════════════
# 源清单
# ══════════════════════════════════════════════════════════════


def sources_payload() -> dict:
    """24 条源 + 运行期健康度。

    清单来自 `registry`（代码），状态来自 `collect_sources`（库）。两者按键
    对齐——这样「代码里加了源但还没跑过」也能显示出来，而不是从表里消失。
    """
    available = _has_table("collect_sources")
    live: dict[str, dict] = {}
    pending: dict[str, int] = {}
    if available:
        for r in staging_db.query("SELECT * FROM collect_sources"):
            live[r["source_key"]] = r
        if _has_table("raw_accidents"):
            for r in staging_db.query(
                    "SELECT source_key, COUNT(*) AS n FROM raw_accidents "
                    "WHERE status = 'new' GROUP BY source_key"):
                pending[r["source_key"]] = r["n"]

    data = []
    for spec in registry.all_sources():
        r = live.get(spec.key, {})
        data.append({
            "source_key": spec.key,
            "display_name": spec.display_name,
            "source_group": spec.group,
            "group_label": registry.GROUP_LABELS.get(spec.group, spec.group),
            "authority": spec.authority,
            "entry_url": spec.entry_url or None,
            "track": registry.TRACK_NETWORK,
            "crawl_delay": spec.crawl_delay,
            "note": spec.note or None,
            # 库里没有这一行 = 还没跑过；此时按登记的默认值「启用」
            "is_active": bool(r.get("is_active", 1)),
            "enable_classify": bool(r.get("enable_classify", 1)),
            "last_run_at": r.get("last_run_at"),
            "last_run_ok": None if r.get("last_run_ok") is None
                            else bool(r["last_run_ok"]),
            "last_error": r.get("last_error"),
            "total_raw": r.get("total_raw") or 0,
            "total_promoted": r.get("total_promoted") or 0,
            "raw_pending": pending.get(spec.key, 0),
            # 不能用 `not r`：`registry.sync_to_db` 一次就把 24 条全插进去，
            # 于是「表里有行」只说明登记过，不代表跑过。真正的「没跑过」
            # 看有没有留下运行时间。
            "never_run": r.get("last_run_at") is None,
        })

    return {
        "ok": True,
        "available": available,
        "hint": None if available else BUILD_HINT,
        "groups": registry.GROUP_LABELS,
        "data": data,
    }


# ══════════════════════════════════════════════════════════════
# 任务
# ══════════════════════════════════════════════════════════════


def _resolve_keys(raw: Any) -> list[str]:
    """把请求里的 `source_keys` 归一成一份 source_key 列表。

    `["*"]` 或 `"all"` 取全部**启用中**的源——采集页的「全选已启用」按钮
    走这个。未登记的名字在这里就报错，不留到执行器里才炸。
    """
    if raw in ("all", ["*"], "*"):
        return [s.key for s in registry.all_sources()]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError("source_keys 不能为空")
    keys = []
    for k in raw:
        spec = registry.get(str(k))       # 未登记 → KeyError
        keys.append(spec.key)
    return keys


def _task_payload(task: dict) -> dict:
    """任务行 → 页面要的形状。

    `progress` 与 `cursor` 分开：`progress` 是「收了多少条」，`cursor` 是
    「抓到第几页」。蓝图 01 §4 的响应把两者并列，是为了断点续跑，但当前
    24 个适配器都没有暴露页游标（`collect()` 一次性返回整个列表），所以
    `cursor` 恒为空——如实地返回空对象，不编造进度。
    """
    stats = task.get("stats") or {}
    per_source = stats.get("per_source", {})
    keys = task.get("source_keys") or []
    progress = {}
    for k in keys:
        if k in per_source:
            v = per_source[k]
            progress[k] = {"state": "failed" if not v.get("ok") else "done", **v}
        elif task["status"] in ("pending",):
            progress[k] = {"state": "pending"}
        elif task["status"] in ("done", "failed", "cancelled"):
            progress[k] = {"state": "skipped"}
        else:
            progress[k] = {"state": "running"}

    done = sum(1 for v in per_source.values())
    return {
        "task_id": task["task_id"],
        "source_keys": keys,
        "status": task["status"],
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "params": task.get("params") or {},
        "progress": progress,
        "progress_done": done,
        "progress_total": len(keys),
        # `filtered` 要一起给：用户勾了日期或关键词之后，最想知道的正是
        # 「抓回来多少、被条件拦掉多少」，少了这一格就只能猜。
        "totals": {k: stats.get(k) for k in
                   ("collected", "inserted", "dup", "filtered") if k in stats},
        "failed_sources": stats.get("failed_sources", []),
        "cursor": {},
        "error": task.get("error"),
    }


@collect_bp.get("/sources")
def api_sources():
    return jsonify(sources_payload())


@collect_bp.post("/tasks")
def api_create_task():
    """起一场采集。立刻返回 task_id，采集在线程池里跑。"""
    if not _has_table("collect_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503

    body = request.get_json(silent=True) or {}
    try:
        keys = _resolve_keys(body.get("source_keys"))
    except KeyError as e:
        return jsonify({"ok": False, "error": "unknown_source",
                        "message": str(e)}), 400
    except ValueError as e:
        return jsonify({"ok": False, "error": "bad_request",
                        "message": str(e)}), 400

    params = {k: body.get(k) for k in ("since", "until", "limit", "keywords")
              if body.get(k) is not None}
    try:
        concurrency = int(body.get("concurrency") or executor.DEFAULT_CONCURRENCY)
    except (TypeError, ValueError):
        concurrency = executor.DEFAULT_CONCURRENCY

    try:
        task_id = executor.submit_task(keys, params or None,
                                       concurrency=max(1, min(concurrency, 8)))
    except Exception as e:  # noqa: BLE001 —— 建任务失败要说清原因
        return jsonify({"ok": False, "error": "task_create_failed",
                        "message": f"{type(e).__name__}: {e}"}), 500

    return jsonify({"ok": True, "data": {
        "task_id": task_id, "status": "pending", "source_keys": keys,
    }}), 202


@collect_bp.get("/tasks")
def api_list_tasks():
    if not _has_table("collect_tasks"):
        return jsonify(_unavailable())
    limit = min(int(request.args.get("limit", 50) or 50), 500)
    tasks = [_task_payload(t) for t in executor.list_tasks(limit)]
    return jsonify({"ok": True, "available": True, "data": tasks})


@collect_bp.get("/tasks/<task_id>")
def api_get_task(task_id: str):
    if not _has_table("collect_tasks"):
        return jsonify(_unavailable())
    task = executor.get_task(task_id)
    if task is None:
        return jsonify({"ok": False, "error": "task_not_found",
                        "message": f"任务 {task_id} 不存在"}), 404
    # `params` 在 get_task 里已解析成 dict，_task_payload 直接用
    return jsonify({"ok": True, "data": _task_payload(task)})


@collect_bp.post("/tasks/<task_id>/cancel")
def api_cancel_task(task_id: str):
    if not _has_table("collect_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503
    if not executor.cancel_task(task_id):
        return jsonify({
            "ok": False, "error": "not_cancellable",
            "message": "任务不存在或已经结束",
            "note": CANCEL_NOTE,
        }), 409
    return jsonify({"ok": True, "note": CANCEL_NOTE,
                    "data": executor.get_task(task_id)})


@collect_bp.delete("/tasks/<task_id>")
def api_delete_task(task_id: str):
    """删任务行。`running` 的必须先取消——删掉一行不会让线程停下来，
    只会留下一个往不存在的任务里写统计的线程。"""
    if not _has_table("collect_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503
    task = executor.get_task(task_id)
    if task is None:
        return jsonify({"ok": False, "error": "task_not_found",
                        "message": f"任务 {task_id} 不存在"}), 404
    if task["status"] in ("pending", "running", "cancelling"):
        return jsonify({"ok": False, "error": "task_still_active",
                        "message": "任务尚未结束，请先取消"}), 409

    with staging_db.write_tx() as con:
        # `raw_accidents.task_id` 是外键且允许为空：把产出的归属抹成 NULL
        # 而不是连记录一起删——采到的原始文本是资产，任务只是它怎么来的。
        con.execute("UPDATE raw_accidents SET task_id = NULL WHERE task_id = ?",
                    (task_id,))
        con.execute("DELETE FROM collect_tasks WHERE task_id = ?", (task_id,))
    return jsonify({"ok": True, "data": {"task_id": task_id, "deleted": True}})


# ══════════════════════════════════════════════════════════════
# 原始数据
# ══════════════════════════════════════════════════════════════

_PREVIEW_CHARS = 120


def _raw_where(args) -> tuple[str, list[Any]]:
    """把查询串编成 WHERE。`gate_verdict=__none__` 表示「未判」——

    用哨兵值而不是空串，是因为「判定为空」与「判定是个空字符串」在语义上
    是两件事，路由层不该靠真值判断把它们混起来（实测未判定的记录占多数）。
    """
    clauses, params = [], []
    for col in ("source_key", "task_id", "status"):
        v = (args.get(col) or "").strip()
        if v:
            clauses.append(f"{col} = ?")
            params.append(v)

    verdict = (args.get("gate_verdict") or "").strip()
    if verdict == "__none__":
        clauses.append("gate_verdict IS NULL")
    elif verdict:
        clauses.append("gate_verdict = ?")
        params.append(verdict)

    q = (args.get("q") or "").strip()
    if q:
        clauses.append("(title LIKE ? OR content LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


@collect_bp.get("/raw")
def api_raw_list():
    if not _has_table("raw_accidents"):
        return jsonify(_unavailable())

    where, params = _raw_where(request.args)
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
        size = min(PAGE_SIZE_MAX, max(1, int(request.args.get("size",
                                                             PAGE_SIZE_DEFAULT))))
    except (TypeError, ValueError):
        page, size = 1, PAGE_SIZE_DEFAULT

    total = staging_db.scalar(
        f"SELECT COUNT(*) FROM raw_accidents{where}", params, default=0)
    rows = staging_db.query(
        f"""SELECT raw_id, source_key, task_id, title, source_url, fetch_time,
                   publish_date, status, gate_verdict, gate_reason, gate_stage,
                   gate_confidence, ext_date, ext_province, ext_city,
                   ext_deaths, ext_injuries, ext_accident_type, ext_severity,
                   substr(content, 1, {_PREVIEW_CHARS}) AS content_preview,
                   length(content) AS content_len
              FROM raw_accidents{where}
             ORDER BY fetch_time DESC, raw_id
             LIMIT ? OFFSET ?""",
        [*params, size, (page - 1) * size])

    # 资产数一次查完（`has_assets` 是列表页的图标，N+1 查询会让 200 行变慢）
    with_assets: set[str] = set()
    if rows and _has_table("raw_accident_assets"):
        ids = [r["raw_id"] for r in rows]
        marks = ",".join("?" * len(ids))
        with_assets = {
            r["raw_id"] for r in staging_db.query(
                f"SELECT DISTINCT raw_id FROM raw_accident_assets "
                f"WHERE raw_id IN ({marks})", ids)
        }

    for r in rows:
        r["has_assets"] = r["raw_id"] in with_assets

    return jsonify({
        "ok": True,
        "available": True,
        "meta": {"total": total, "page": page, "size": size,
                 "pages": max(1, (total + size - 1) // size)},
        "data": rows,
    })


@collect_bp.get("/raw/<raw_id>")
def api_raw_detail(raw_id: str):
    if not _has_table("raw_accidents"):
        return jsonify(_unavailable())
    row = staging_db.query_one(
        "SELECT * FROM raw_accidents WHERE raw_id = ?", (raw_id,))
    if row is None:
        return jsonify({"ok": False, "error": "raw_not_found",
                        "message": f"原始记录 {raw_id} 不存在"}), 404

    # `extra` 是采集器塞进来的自定义字段（pdf_urls / needs_ocr 之类），
    # 存的是 JSON 字符串。解析失败就原样给字符串——总比丢掉强。
    if row.get("extra"):
        try:
            row["extra"] = json.loads(row["extra"])
        except (json.JSONDecodeError, TypeError):
            pass

    row["assets"] = staging_db.query(
        "SELECT id, asset_type, url, ocr_status FROM raw_accident_assets "
        "WHERE raw_id = ? ORDER BY id", (raw_id,)
    ) if _has_table("raw_accident_assets") else []
    row["source"] = next(
        (s for s in ({"key": spec.key, "name": spec.display_name,
                      "authority": spec.authority, "group": spec.group}
                     for spec in registry.all_sources())
         if s["key"] == row["source_key"]), None)
    return jsonify({"ok": True, "data": row})


@collect_bp.post("/raw/gate")
def api_gate_raw():
    """人工裁决一批原始记录（蓝图 01 §5.3）。

    走出去的记录 `gate_stage='manual'`、`status='gated'`——阶段②的提取入口
    就查这批，不重复筛选。
    """
    if not _has_table("raw_accidents"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503

    body = request.get_json(silent=True) or {}
    ids = body.get("raw_ids")
    verdict = (body.get("verdict") or "").strip()
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "error": "bad_request",
                        "message": "raw_ids 不能为空"}), 400
    if verdict not in VERDICTS:
        return jsonify({"ok": False, "error": "bad_verdict",
                        "message": f"verdict 只能是 {list(VERDICTS)}"}), 400

    note = (body.get("note") or "").strip() or None
    # `review` 是「退回重判」，判定该清空；另两个是结论，写进去。
    gate_verdict = None if verdict == "review" else verdict
    marks = ",".join("?" * len(ids))
    with staging_db.write_tx() as con:
        cur = con.execute(
            f"""UPDATE raw_accidents
                   SET gate_verdict = ?, gate_reason = ?,
                       gate_stage = 'manual', gate_confidence = 'high',
                       gate_at = ?, status = 'gated'
                 WHERE raw_id IN ({marks})""",
            [gate_verdict, note, _now(), *ids])

    return jsonify({"ok": True, "data": {
        "updated": cur.rowcount, "verdict": verdict,
        "missing": len(ids) - cur.rowcount,
    }})


# ══════════════════════════════════════════════════════════════
# 漏斗统计
# ══════════════════════════════════════════════════════════════

# 漏斗各段的判定条件。**没有清洗段**：清洗是阶段②的事（`cleaners/` 四器
# 还没接进在线管道），这里不编一个恒等于上一段的数字来凑满五格。
_FUNNEL = (
    ("raw", "原始抓取", None),
    ("kept", "自动闸门通过",
     "gate_verdict = 'confirmed' AND gate_stage IN ('rule','llm')"),
    ("manual", "人工确认",
     "gate_verdict = 'confirmed' AND gate_stage = 'manual'"),
    ("promoted", "已送提取", "status = 'promoted'"),
)


def stats_payload() -> dict:
    if not _has_table("raw_accidents"):
        return {**_unavailable(),
                "data": {"funnel": [], "by_source": [], "by_verdict": {}}}

    total = staging_db.scalar("SELECT COUNT(*) FROM raw_accidents", default=0)
    funnel = []
    for key, label, cond in _FUNNEL:
        n = total if cond is None else staging_db.scalar(
            f"SELECT COUNT(*) FROM raw_accidents WHERE {cond}", default=0)
        funnel.append({"stage": key, "label": label, "count": n})

    by_source = staging_db.query(
        """SELECT r.source_key, COUNT(*) AS total,
                  SUM(CASE WHEN r.status = 'new' THEN 1 ELSE 0 END) AS pending
             FROM raw_accidents r
            GROUP BY r.source_key
            ORDER BY total DESC""")

    by_verdict = {"confirmed": 0, "rejected": 0, "review": 0, "__none__": 0}
    for r in staging_db.query(
            """SELECT COALESCE(gate_verdict, '__none__') AS v, COUNT(*) AS n
                 FROM raw_accidents GROUP BY v"""):
        by_verdict[r["v"]] = r["n"]

    return {
        "ok": True,
        "available": True,
        "data": {
            "total": total,
            "funnel": funnel,
            "by_source": by_source,
            "by_verdict": by_verdict,
            "stages_missing": [
                {"stage": "cleaned", "label": "清洗保留",
                 "reason": "清洗器（cleaners/）尚未接进在线管道，计划在阶段②"},
            ],
        },
    }


@collect_bp.get("/stats")
def api_stats():
    return jsonify(stats_payload())
