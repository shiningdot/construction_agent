"""阶段②「审核与提取」的页面与 API。蓝图 02 的落地。

## 与前后两段的关系

输入是采集页标为相关的 `raw_accidents`（`gate_verdict='confirmed'`），输出是
暂存区的 `staging_accidents` + `staging_causes`。两头都在**工作库**里，
最终库一行不碰——并入是阶段③的事（蓝图 03）。

## 成本确认是一道硬闸门

`POST /api/extract/tasks` 只写一行 `pending` 并把预估回给前端，**一次 LLM
调用都不发**；点了「确认执行」走 `POST .../start` 才真的开始跑。旧 web 的
`/api/process` 是同步单条、无超时无成本控制，40 秒的请求在生产里必然超时
（蓝图 02 §2.5）——这里是它的反面。

## 四段进度是从阶段记录里数出来的，不是另存一份

`CaicsPipeline.run_once()` 每段都留 `StageRecord`，执行器把它们折算成
`staging_accidents.s3_ms` 之类的列。任务详情里的 S1–S4 四格因此是对
**真实执行**的回读，而不是另一套自报计数——本项目吃过「两处各算一遍、
结果对不上」的亏（`evidence_quality` 的 6 份复制品），这里不重复。
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from ..extraction import staging_executor as ex
from ..extraction.evidence_quality import LEVEL_LABELS
from . import db, staging_db

extract_bp = Blueprint("extract", __name__, url_prefix="/api/extract")

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200

# 与采集页同一个提示语，两个页面共用一份工作库
BUILD_HINT = "工作库未建：请先运行 python3 scripts/create_staging_db.py"

# 取消粒度到**条**，比采集那边强：每条本来就有一串 LLM 往返，天然是检查点。
CANCEL_NOTE = "取消在当前这条提取完成后生效（每条跑完检查一次）"

# 暂存区可筛选的枚举列。白名单而不是拼字符串——这几个值直接来自查询串。
_TEXT_TYPES = ("single", "multi", "regulatory", "aggregate", "training", "other")
_RELEVANCE = ("construction", "non_construction", "insufficient")
_REVIEW = ("pending", "approved", "edited", "rejected")


def _has_table(name: str) -> bool:
    try:
        return bool(staging_db.scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,), default=0))
    except Exception:  # noqa: BLE001
        return False


def _unavailable() -> dict:
    return {"ok": True, "available": False, "hint": BUILD_HINT, "data": []}


def _page_args() -> tuple[int, int]:
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.args.get("size", PAGE_SIZE_DEFAULT) or PAGE_SIZE_DEFAULT)
    except (TypeError, ValueError):
        size = PAGE_SIZE_DEFAULT
    return page, max(1, min(size, PAGE_SIZE_MAX))


# ══════════════════════════════════════════════════════════════
# 理论版本（§3.5 的单一出口）
# ══════════════════════════════════════════════════════════════


def taxonomy_payload() -> dict:
    """当前生效的 CAICS 19 类 + MECE 枚举版本 + 证据等级词表。

    类名**查表得来**，不硬编码：`caics_causes` 自己带着 `code` + 中文 `name`，
    19 类实测齐备。旧 web 的 500 崩溃正是因为现场重算 HFACS-CS，而 `hfacs.py`
    的默认路径指向一个不存在的文件、`load()` 静默失败——查表从根上没有这个坑。

    主库读不到（没建/没数据）时返回空列表而不是报错：提取页要能打开，
    理论版本条显示「未读到」，比整页 503 有用。
    """
    try:
        classes = db.query(
            """
            SELECT code, name, COUNT(*) AS n
              FROM caics_causes
             GROUP BY code, name
             ORDER BY code
            """)
    except Exception:  # noqa: BLE001 —— 最终库不可用不该让提取页打不开
        classes = []

    enums: dict[str, str] = {}
    version = ""
    try:
        # `db.query` 返回的是字典行，**不能 `for k, v in rows` 直接解包**——
        # 那解出来的是行字典的两个键名（'key'、'value'），不是值
        for row in db.query("SELECT key, value FROM pipeline_metadata"):
            key, value = row["key"], row["value"]
            if key == "pipeline_version":
                version = value or ""
            elif key.endswith("_enum_version"):
                # 值是一段 JSON，版本号在里面；取不到就把原文放回去，
                # 让页面显示点东西而不是空白
                name = key[:-len("_enum_version")]
                try:
                    enums[name] = json.loads(value).get("version", value)
                except (ValueError, TypeError, AttributeError):
                    enums[name] = value
    except Exception:  # noqa: BLE001
        pass

    return {
        "caics": {
            "version": version or "caics_v1.0",
            "total": len(classes),
            "classes": classes,
        },
        "enums": enums,
        "evidence_levels": [{"key": k, "label": v} for k, v in LEVEL_LABELS.items()],
    }


@extract_bp.get("/taxonomy")
def api_taxonomy():
    return jsonify({"ok": True, "available": True, "data": taxonomy_payload()})


# ══════════════════════════════════════════════════════════════
# 待提取队列
# ══════════════════════════════════════════════════════════════


@extract_bp.get("/pending")
def api_pending():
    """已确认相关、还没进过暂存区的原文。页面②的列表用它。"""
    if not _has_table("raw_accidents"):
        return jsonify(_unavailable())
    try:
        limit = max(1, min(int(request.args.get("limit", 200) or 200), 1000))
    except (TypeError, ValueError):
        limit = 200
    only_unstaged = request.args.get("all") not in ("1", "true", "yes")
    data = ex.pending_raws(limit, only_unstaged=only_unstaged)
    data["available"] = True
    data["include_staged"] = not only_unstaged
    return jsonify({"ok": True, "data": data})


# ══════════════════════════════════════════════════════════════
# 任务
# ══════════════════════════════════════════════════════════════


def _task_payload(task: dict) -> dict:
    stats = task.get("stats") or {}
    total = int(stats.get("total") or len(task.get("raw_ids") or []))
    done = int(stats.get("done") or 0)
    est_tokens = task.get("est_tokens") or 0
    actual_in = task.get("actual_tokens_in") or 0
    actual_out = task.get("actual_tokens_out") or 0

    # 预估是按总量给的，进度里要按同一比例折算，否则「预估 3.2M / 已用 0.8M」
    # 这两栏单位不一致，看着像超额
    est_so_far = int(est_tokens * done / total) if total else 0

    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "concurrency": task.get("concurrency"),
        "enable_split": bool(task.get("enable_split")),
        "raw_ids": task.get("raw_ids") or [],
        "items": total,
        "progress": {
            "done": done,
            "total": total,
            "failed": int(stats.get("failed") or 0),
            "percent": round(done * 100 / total) if total else 0,
            # 页面靠它显示「正在跑第几条」，而不是只有一个百分比
            "current": stats.get("current") or "",
        },
        "by_type": stats.get("by_type") or {},
        "staged": int(stats.get("staged") or 0),
        "errors": stats.get("errors") or [],
        "usage": {
            "tokens_in": actual_in,
            "tokens_out": actual_out,
            "tokens": actual_in + actual_out,
            "cost_cny": stats.get("cost_cny") or 0.0,
            "est_tokens": est_tokens,
            "est_tokens_so_far": est_so_far,
            # 蓝图 02 §8 验收第 8 条要的就是这个偏差
            "deviation_pct": (
                round((actual_in + actual_out - est_so_far) * 100 / est_so_far)
                if est_so_far else None),
        },
        "estimate": {
            "est_tokens": est_tokens,
            "est_cost_cny": task.get("est_cost_cny") or 0.0,
        },
        "error": task.get("error"),
    }


@extract_bp.get("/estimate")
def api_estimate():
    """只算数，不落库。给确认弹层用。

    前端不自己算：`EST_TOKENS` 的系数只有一份，写在执行器里。前端复制一份
    迟早会漂移，而这个项目刚吃过「两处各算一遍、结果对不上」的亏
    （`evidence_quality` 的 6 份复制品）。
    """
    if not _has_table("raw_accidents"):
        return jsonify(_unavailable())
    try:
        n = max(0, int(request.args.get("n", 0) or 0))
    except (TypeError, ValueError):
        n = 0
    per_item = None
    raw_per = (request.args.get("per_item") or "").strip()
    if raw_per:
        try:
            per_item = max(0, int(raw_per))
        except (TypeError, ValueError):
            per_item = None
    return jsonify({"ok": True, "available": True,
                    "data": ex.estimate(n, per_item=per_item)})


@extract_bp.post("/tasks")
def api_create_task():
    """建任务 + 给预估。**不执行**，等 `POST .../start`。"""
    if not _has_table("extract_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("raw_ids")
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "bad_request",
                        "message": "raw_ids 必须是数组"}), 400

    try:
        concurrency = int(body.get("concurrency") or ex.DEFAULT_CONCURRENCY)
    except (TypeError, ValueError):
        concurrency = ex.DEFAULT_CONCURRENCY

    try:
        created = ex.create_task(
            raw_ids,
            # 默认关：MULTI 拆分当前不生效（见 staging_executor 模块说明），
            # 前端把开关打成「暂不生效」的样子，别让用户以为勾了就会拆
            enable_split=bool(body.get("enable_split")),
            concurrency=max(1, min(concurrency, 8)),
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": "bad_request",
                        "message": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": "task_create_failed",
                        "message": f"{type(e).__name__}: {e}"}), 500

    task = ex.get_task(created["task_id"])
    return jsonify({"ok": True, "data": {
        "task_id": created["task_id"],
        "status": "pending",
        "estimate": created["estimate"],
        "task": _task_payload(task) if task else None,
    }}), 201


@extract_bp.post("/tasks/<task_id>/start")
def api_start_task(task_id: str):
    if not _has_table("extract_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503
    task = ex.get_task(task_id)
    if task is None:
        return jsonify({"ok": False, "error": "task_not_found",
                        "message": f"任务 {task_id} 不存在"}), 404
    if not ex.start_task(task_id):
        return jsonify({"ok": False, "error": "not_startable",
                        "message": f"任务当前状态是 {task['status']}，只有 pending 能启动"
                        }), 409
    return jsonify({"ok": True, "data": _task_payload(ex.get_task(task_id))})


@extract_bp.get("/tasks")
def api_list_tasks():
    if not _has_table("extract_tasks"):
        return jsonify(_unavailable())
    try:
        limit = max(1, min(int(request.args.get("limit", 50) or 50), 500))
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"ok": True, "available": True,
                    "data": [_task_payload(t) for t in ex.list_tasks(limit)]})


@extract_bp.get("/tasks/<task_id>")
def api_get_task(task_id: str):
    if not _has_table("extract_tasks"):
        return jsonify(_unavailable())
    task = ex.get_task(task_id)
    if task is None:
        return jsonify({"ok": False, "error": "task_not_found",
                        "message": f"任务 {task_id} 不存在"}), 404
    return jsonify({"ok": True, "data": _task_payload(task)})


@extract_bp.post("/tasks/<task_id>/cancel")
def api_cancel_task(task_id: str):
    if not _has_table("extract_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503
    if not ex.cancel_task(task_id):
        return jsonify({"ok": False, "error": "not_cancellable",
                        "message": "任务不存在或已经结束",
                        "note": CANCEL_NOTE}), 409
    task = ex.get_task(task_id)
    # 两种取消的语义不一样，提示语不能共用一句：没启动过的任务已经终结，
    # 说「跑完这条才生效」会让用户一直等一个不会来的事件
    note = ("任务尚未启动，已立即取消。" if task["status"] == "cancelled"
            else CANCEL_NOTE)
    return jsonify({"ok": True, "note": note, "data": _task_payload(task)})


@extract_bp.delete("/tasks/<task_id>")
def api_delete_task(task_id: str):
    """删任务行。**产出留着**——暂存区的条目是阶段③的审核对象，
    删任务不该连带毁掉已经花过 token 的东西。"""
    if not _has_table("extract_tasks"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503
    task = ex.get_task(task_id)
    if task is None:
        return jsonify({"ok": False, "error": "task_not_found",
                        "message": f"任务 {task_id} 不存在"}), 404
    if task["status"] in ("pending", "running", "cancelling"):
        return jsonify({"ok": False, "error": "task_still_active",
                        "message": "任务尚未结束，请先取消"}), 409
    staged = staging_db.scalar(
        "SELECT COUNT(*) FROM staging_accidents WHERE task_id = ?", (task_id,), 0)
    ex.delete_task(task_id)
    return jsonify({"ok": True, "data": {
        "task_id": task_id, "deleted": True, "staged_kept": staged,
    }})


# ══════════════════════════════════════════════════════════════
# 暂存结果
# ══════════════════════════════════════════════════════════════

# 列表页只送这些列。33 列全送的话，50 行就是几千个字段，其中大多是
# 有值率个位数（weather 7.9%、supervision_company 10.2%）的空值
_LIST_COLS = """
    s.stage_id, s.raw_id, s.task_id, s.title, s.date, s.accident_type,
    s.severity, s.deaths, s.injuries, s.location, s.city,
    s.text_type, s.relevance_verdict, s.review_status,
    s.s3_ms, s.s4_ms, s.s3_model, s.s4_model, s.created_at,
    (SELECT COUNT(*) FROM staging_causes c WHERE c.stage_id = s.stage_id)
        AS cause_count
"""


def _result_where(args) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    task_id = (args.get("task_id") or "").strip()
    if task_id:
        where.append("s.task_id = ?")
        params.append(task_id)

    for col, allowed, key in (
        ("review_status", _REVIEW, "review_status"),
        ("text_type", _TEXT_TYPES, "text_type"),
        ("relevance_verdict", _RELEVANCE, "relevance_verdict"),
    ):
        v = (args.get(key) or "").strip()
        if v in allowed:
            where.append(f"s.{col} = ?")
            params.append(v)

    q = (args.get("q") or "").strip()
    if q:
        where.append("(COALESCE(s.title,'') LIKE ? OR COALESCE(s.summary,'') LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    # 「没编码的」是个常用视角：S4 失败或返回空时暂存行照样建，但一条编码都没有。
    # 用 EXISTS 而不是 JOIN，免得把它写成需要 GROUP BY 的形式
    if args.get("no_cause") in ("1", "true", "yes"):
        where.append("NOT EXISTS (SELECT 1 FROM staging_causes c "
                     "WHERE c.stage_id = s.stage_id)")

    return (" AND ".join(where) if where else "1=1"), params


@extract_bp.get("/results")
def api_results():
    """暂存区列表——阶段③的审核入口。"""
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())

    where, params = _result_where(request.args)
    page, size = _page_args()
    total = staging_db.scalar(
        f"SELECT COUNT(*) FROM staging_accidents s WHERE {where}", params, 0)

    rows = staging_db.query(
        f"""
        SELECT {_LIST_COLS}
          FROM staging_accidents s
         WHERE {where}
         ORDER BY s.created_at DESC, s.stage_id
         LIMIT ? OFFSET ?
        """,
        [*params, size, (page - 1) * size])

    return jsonify({
        "ok": True, "available": True,
        "meta": {"total": total, "page": page, "size": size,
                 "pages": max(1, math.ceil(total / size)) if total else 1},
        "data": rows,
    })


# 详情里单列出去的键，不再混进 `fields`。这些是溯源与审核状态，
# 跟「这张事故卡片的内容」不是一回事
_META_KEYS = {
    "stage_id", "raw_id", "task_id", "origin_segment", "split_index",
    "text_type", "text_type_reason", "text_type_confidence",
    "relevance_verdict", "relevance_reason", "relevance_stage",
    "relevance_confidence",
    "s3_model", "s3_ms", "s3_tokens_in", "s3_tokens_out",
    "s4_model", "s4_ms", "s4_tokens_in", "s4_tokens_out",
    "s4_primary_cause", "s4_cause_chain",
    "review_status", "review_note", "reviewer", "reviewed_at",
    "created_at",
}


@extract_bp.get("/results/<stage_id>")
def api_result_detail(stage_id: str):
    """单条完整产出：31 字段 + 致因树 + 过程元数据。"""
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())
    row = staging_db.query_one(
        "SELECT * FROM staging_accidents WHERE stage_id = ?", (stage_id,))
    if row is None:
        return jsonify({"ok": False, "error": "not_found",
                        "message": f"暂存条目 {stage_id} 不存在"}), 404

    causes = staging_db.query(
        """
        SELECT id, code, name, evidence, inference, evidence_quality,
               evidence_quality_rule, evidence_quality_llm, sort_order
          FROM staging_causes WHERE stage_id = ? ORDER BY sort_order, id
        """, (stage_id,))

    # 页面上三级证据要显示中文（直引/推断/弱推断），词表跟展示页共用一份
    for c in causes:
        c["evidence_quality_label"] = LEVEL_LABELS.get(
            c.get("evidence_quality") or "", c.get("evidence_quality") or "")

    raw = staging_db.query_one(
        "SELECT raw_id, source_key, source_url, publish_date, title, content "
        "FROM raw_accidents WHERE raw_id = ?", (row.get("raw_id"),)) if row.get("raw_id") else None

    return jsonify({"ok": True, "data": {
        "stage_id": stage_id,
        "raw_id": row.get("raw_id"),
        "task_id": row.get("task_id"),
        "fields": {k: v for k, v in row.items()
                   if k not in _META_KEYS},
        "gates": {
            "text_type": row.get("text_type"),
            "text_type_reason": row.get("text_type_reason"),
            "text_type_confidence": row.get("text_type_confidence"),
            "relevance_verdict": row.get("relevance_verdict"),
            "relevance_reason": row.get("relevance_reason"),
            "relevance_stage": row.get("relevance_stage"),
            "relevance_confidence": row.get("relevance_confidence"),
        },
        "review": {
            "review_status": row.get("review_status"),
            "review_note": row.get("review_note"),
            "reviewer": row.get("reviewer"),
            "reviewed_at": row.get("reviewed_at"),
        },
        "causes": causes,
        "meta": {
            "s3_model": row.get("s3_model"),
            "s3_ms": row.get("s3_ms"),
            "s3_tokens_in": row.get("s3_tokens_in"),
            "s3_tokens_out": row.get("s3_tokens_out"),
            "s4_model": row.get("s4_model"),
            "s4_ms": row.get("s4_ms"),
            "s4_tokens_in": row.get("s4_tokens_in"),
            "s4_tokens_out": row.get("s4_tokens_out"),
            "created_at": row.get("created_at"),
        },
        "raw": raw,
    }})


@extract_bp.post("/results/<stage_id>/rerun")
def api_rerun(stage_id: str):
    """整条重跑。

    **当前是覆盖，不是留版本**：删掉旧暂存行（级联删编码）后重新跑一遍。
    蓝图 02 §9 待决项 F 把「加 version + supersedes 留历史」列为待定——
    它会明显抬高查询复杂度，而重跑目前是低频操作，先用覆盖。响应里把
    `replaced` 带上，让调用方知道旧的那条没了。
    """
    if not _has_table("staging_accidents"):
        return jsonify({"ok": False, "error": "staging_db_missing",
                        "message": BUILD_HINT}), 503
    row = staging_db.query_one(
        "SELECT stage_id, raw_id, task_id FROM staging_accidents WHERE stage_id = ?",
        (stage_id,))
    if row is None:
        return jsonify({"ok": False, "error": "not_found",
                        "message": f"暂存条目 {stage_id} 不存在"}), 404
    if not row["raw_id"]:
        return jsonify({"ok": False, "error": "no_raw",
                        "message": "这条暂存记录没有关联原文，无法重跑"}), 409

    raw_id = row["raw_id"]
    raw = staging_db.query_one(
        "SELECT * FROM raw_accidents WHERE raw_id = ?", (raw_id,))
    if raw is None:
        return jsonify({"ok": False, "error": "raw_missing",
                        "message": f"原文 {raw_id} 已不存在，无法重跑"}), 409
    if raw.get("gate_verdict") != "confirmed":
        return jsonify({"ok": False, "error": "not_confirmed",
                        "message": "原文当前不处于「已确认」状态，请先在采集页恢复"
                        }), 409

    with staging_db.write_tx() as con:
        # 编码靠 ON DELETE CASCADE 一起走；显式删一遍是为了不依赖 PRAGMA
        # foreign_keys 在每个连接上都开着（它是连接级开关，默认 OFF）
        con.execute("DELETE FROM staging_causes WHERE stage_id = ?", (stage_id,))
        con.execute("DELETE FROM staging_accidents WHERE stage_id = ?", (stage_id,))

    # 重跑走的是同一条执行路径，不另开分支：跑法一致，产出才可比
    # 沿用原来的 task_id（重跑多半是接着某次任务的结果改），不要凭空造一个
    # 「rerun」——那会在 staging_accidents.task_id 里留下一个指向
    # extract_tasks 中并不存在的任务的值
    out = ex.run_one(raw, task_id=row.get("task_id") or "")
    if not out.ok:
        return jsonify({"ok": False, "error": "rerun_failed",
                        "message": out.error or "重跑失败",
                        "stage": out.stage}), 500

    new_row = staging_db.query_one(
        "SELECT stage_id FROM staging_accidents WHERE raw_id = ?", (raw_id,))
    return jsonify({"ok": True, "data": {
        "replaced": stage_id,
        "stage_id": new_row["stage_id"] if new_row else None,
        "status": out.status,
        "causes": out.causes,
    }})
