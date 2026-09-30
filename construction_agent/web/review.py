"""阶段③「暂存与人工审核」的页面与 API。蓝图 03 的落地。

## 这一环解决什么问题

在此之前，从原始文本到最终库是**一竿子到底**的批处理脚本，中间没有人工
确认点。没有人有机会在看到数据之后再决定收不收——这正是 1,041 行孤儿外键
与 338 行重复编码能够产生并长期潜伏的结构性原因。

本模块在中间放一道看得见、可回退、可追溯的关口：告警把该看的条目浮出来 →
人工逐条确认或批量通过 → 预演（dry_run）→ 确认并入 → 全程留痕可回滚。

## 分层

- 告警规则在 `review/warnings.py`（读时算，不存表）
- 并入逻辑在 `review/promote.py`（唯一会写最终库的地方）
- 本模块只做路由、参数校验、拼查询

## 两种查询，各司其职

队列页要同时回答两个问题，代价不一样：

| 问题 | 需要什么 | 做法 |
|---|---|---|
| 「一共多少条、多少条有告警」 | 全量 | `_warn_scan()` 轻量全扫，只取算告警要的 8 列 |
| 「第 3 页、只看有告警的」 | 筛选+分页 | `_fetch_rows()` 带 WHERE 下推，取回匹配行后算告警 |

两处都躲不掉「算告警」这一步——告警是当前数据的函数而不是表里的列，
`WHERE warnings = ...` 没法交给 SQLite。所以重活（计数、求和）全部下推成
SQL 子查询，Python 侧只做常数级判断。暂存区当前是几百条量级；真要长到
十万条，该做的是物化一列并在写暂存时就更新，而不是在这里加缓存。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request

from ..extraction.evidence_quality import LEVEL_LABELS
from ..review import promote as pm
from ..review import warnings as wn
from . import db, staging_db

review_bp = Blueprint("review", __name__, url_prefix="/api/review")

PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 200

BUILD_HINT = "工作库未建：请先运行 python3 scripts/create_staging_db.py"

_REVIEW_STATUS = ("pending", "approved", "edited", "rejected")
_TEXT_TYPES = ("single", "multi", "regulatory", "aggregate", "training", "other")

# 全量扫描上限。达到上限说明该改架构（物化告警列），而不是调大这个数。
WARN_SCAN_LIMIT = 5000

# 审核时允许改的字段。**白名单制**：这些键会直接拼进 UPDATE 的列名，
# 白名单是这里唯一的安全边界，也是唯一事实来源。测试里有一条断言比对
# `PRAGMA table_info` 与此列表的差集，schema 加列而这里漏加会失败。
EDITABLE_FIELDS: tuple[str, ...] = (
    "title", "date", "accident_time", "location", "city", "district",
    "accident_location_detail", "project_name", "project_type",
    "construction_phase", "building_structure", "accident_type", "severity",
    "deaths", "injuries", "trapped_count", "collapse_subtype", "fall_height",
    "work_task", "equipment_involved", "material_involved", "worker_type",
    "weather", "contractor", "contractor_type", "developer",
    "supervision_company", "direct_economic_loss", "direct_cause",
    "summary", "report_type",
)

_INT_FIELDS = ("deaths", "injuries", "trapped_count")
_FLOAT_FIELDS = ("fall_height", "direct_economic_loss")


# ══════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════


def _unavailable() -> dict:
    return {"ok": True, "available": False, "hint": BUILD_HINT, "data": []}


def _has_table(name: str) -> bool:
    try:
        return bool(staging_db.scalar(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,), default=0))
    except Exception:  # noqa: BLE001 — 库文件不存在时连接即抛
        return False


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


def _taxonomy_codes() -> set[str]:
    """CAICS 现役编码集合，用来发现 S4 产出的未知编码。

    从最终库 `caics_causes` 查**实际出现过的**编码，而不是硬编码 19 个字符串
    ——硬编码的那份迟早会跟理论脱节，届时它给出的不是提醒而是噪声。
    最终库读不到时返回空集，`unknown_code_count` 相应退化为 0 而非误报。
    """
    try:
        return {r["code"] for r in db.query(
            "SELECT DISTINCT code FROM caics_causes WHERE code IS NOT NULL")}
    except Exception:  # noqa: BLE001
        return set()


def _warn_aggregates(codes: set[str]) -> str:
    """三个告警所需的聚合子查询。编码集合为空时不查未知编码。"""
    unknown = "0"
    if codes:
        marks = ",".join("?" * len(codes))
        unknown = (f"(SELECT COUNT(*) FROM staging_causes c "
                   f"WHERE c.stage_id = s.stage_id AND c.code NOT IN ({marks}))")
    return f"""
        (SELECT COUNT(*) FROM staging_causes c WHERE c.stage_id = s.stage_id)
            AS cause_count,
        {unknown} AS unknown_code_count,
        (SELECT COUNT(*) FROM staging_causes c WHERE c.stage_id = s.stage_id
            AND c.evidence_quality_llm IS NOT NULL
            AND c.evidence_quality_llm <> c.evidence_quality) AS quality_diff
    """


def _bind(codes: set[str], params: list, tail: list) -> list:
    """按 `?` 在语句文本中出现的顺序拼参数表。

    ⚠️ SQLite 按 `?` 在**语句文本**里出现的先后绑定，而 SELECT 子句在 WHERE
    之前——`_warn_aggregates` 里的编码参数必须排在 WHERE 参数**前面**，否则
    编码集合会被喂进标题 LIKE 的槽位。这条顺序只在这里固化，别处不要手拼。
    """
    return list(codes) + list(params) + list(tail)


_WARN_COLS = """
    s.stage_id, s.raw_id, s.review_status, s.title, s.deaths, s.injuries,
    s.accident_type, s.text_type,
    LENGTH(COALESCE(NULLIF(s.origin_segment, ''), r.content, '')) AS raw_len
"""

# 详情与队列共用的完整列。用 `s.*` 而不是逐列列举——暂存表有 48 列，逐列写
# 一遍就意味着「schema 加了一列而这里忘了跟」会以字段恒为 None 的形式静默
# 发生（本次初稿正是栽在这上面）。raw_accidents 的列一律加 `raw_` 前缀别名，
# 避免与暂存表同名列（title、source_url）在 dict(row) 里互相覆盖。
_FULL_COLS = """
    s.*,
    r.title        AS raw_title,
    r.content      AS raw_content,
    r.source_key   AS raw_source_key,
    r.source_url   AS raw_source_url,
    r.publish_date AS raw_publish_date,
    r.fetch_time   AS raw_fetch_time,
    LENGTH(COALESCE(NULLIF(s.origin_segment, ''), r.content, '')) AS raw_len
"""


def _warn_scan(codes: set[str]) -> list[dict]:
    """轻量全量扫描，只为算 counts 与 by_warning。"""
    sql = (f"SELECT {_WARN_COLS}, {_warn_aggregates(codes)} "
           f"FROM staging_accidents s "
           f"LEFT JOIN raw_accidents r ON r.raw_id = s.raw_id "
           f"ORDER BY s.created_at DESC, s.stage_id LIMIT ?")
    rows = staging_db.query(sql, _bind(codes, [], [WARN_SCAN_LIMIT]))
    for row in rows:
        row["warnings"] = wn.compute(row)
    return rows


def _fetch_rows(where: str, params: list, codes: set[str]) -> list[dict]:
    """完整行 + 告警，供队列筛选与详情页使用。"""
    sql = (f"SELECT {_FULL_COLS}, {_warn_aggregates(codes)} "
           f"FROM staging_accidents s "
           f"LEFT JOIN raw_accidents r ON r.raw_id = s.raw_id "
           f"{where} ORDER BY s.created_at DESC, s.stage_id LIMIT ?")
    rows = staging_db.query(sql, _bind(codes, params, [WARN_SCAN_LIMIT]))
    for row in rows:
        row["warnings"] = wn.compute(row)
    return rows


def _counts(warn_rows: list[dict], promoted_ids: set[str]) -> dict[str, Any]:
    """审核漏斗计数。基于**全量**算，不随当前筛选变化。"""
    by_status = {s: 0 for s in _REVIEW_STATUS}
    for row in warn_rows:
        st = row.get("review_status") or "pending"
        by_status[st] = by_status.get(st, 0) + 1
    with_warn = sum(1 for r in warn_rows if r["warnings"])
    promoted = sum(1 for r in warn_rows if r.get("raw_id") in promoted_ids)
    return {
        **by_status,
        "total": len(warn_rows),
        "with_warnings": with_warn,
        "clean": len(warn_rows) - with_warn,
        "promoted": promoted,
    }


# ══════════════════════════════════════════════════════════════
# ① 待审队列
# ══════════════════════════════════════════════════════════════


@review_bp.get("/queue")
def queue():
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())

    status = request.args.get("review_status", "pending")
    want = request.args.get("warnings", "")
    task_id = request.args.get("task_id", "")
    text_type = request.args.get("text_type", "")
    accident_type = request.args.get("accident_type", "")
    q = (request.args.get("q") or "").strip()
    page, size = _page_args()

    where, params = [], []
    if status and status != "all":
        if status not in _REVIEW_STATUS:
            status = "pending"
        where.append("s.review_status = ?")
        params.append(status)
    if task_id:
        where.append("s.task_id = ?")
        params.append(task_id)
    if text_type in _TEXT_TYPES:
        where.append("s.text_type = ?")
        params.append(text_type)
    if accident_type:
        where.append("s.accident_type = ?")
        params.append(accident_type)
    if q:
        where.append("(s.title LIKE ? OR s.summary LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    codes = _taxonomy_codes()
    warn_rows = _warn_scan(codes)
    promoted_ids = pm.promoted_accident_ids()
    counts = _counts(warn_rows, promoted_ids)

    # 队列行一律走 `_fetch_rows`，即使没有筛选条件。`_warn_scan` 取的是轻量
    # 列集，只够算告警；拿它当队列数据源会在"全部"这类无筛选查询上缺列
    # （date 等），页面直接崩。
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = _fetch_rows(clause, params, codes)

    # 告警条件只能在内存里过（见模块 docstring）
    if want == "any":
        rows = [r for r in rows if r["warnings"]]
    elif want == "none":
        rows = [r for r in rows if not r["warnings"]]
    elif want in wn.WARNING_LABELS:
        rows = [r for r in rows if want in r["warnings"]]

    total = len(rows)
    start = (page - 1) * size
    data = [{
        "stage_id": r["stage_id"], "raw_id": r["raw_id"],
        "title": r["title"], "date": r["date"],
        "accident_type": r["accident_type"], "severity": r["severity"],
        "deaths": r["deaths"], "injuries": r["injuries"],
        "location": r["location"], "city": r["city"],
        "text_type": r["text_type"],
        "relevance_verdict": r["relevance_verdict"],
        "cause_count": r["cause_count"],
        "warnings": r["warnings"],
        "warning_labels": [wn.label(c) for c in r["warnings"]],
        "blocking": [c for c in r["warnings"] if c in wn.BLOCKING],
        "review_status": r["review_status"],
        "review_note": r["review_note"],
        "reviewed_at": r["reviewed_at"],
        "created_at": r["created_at"],
        "promoted": r["raw_id"] in promoted_ids,
    } for r in rows[start:start + size]]

    return jsonify({
        "ok": True, "available": True,
        "meta": {"total": total, "page": page, "size": size,
                 "pages": max(1, -(-total // size)),
                 "scan_truncated": len(warn_rows) >= WARN_SCAN_LIMIT},
        "counts": counts,
        "by_warning": wn.summarize(warn_rows),
        "labels": wn.WARNING_LABELS,
        "data": data,
    })


# ══════════════════════════════════════════════════════════════
# ② 单条详情
# ══════════════════════════════════════════════════════════════


@review_bp.get("/items/<stage_id>")
def item(stage_id: str):
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())

    rows = _fetch_rows("WHERE s.stage_id = ?", [stage_id], _taxonomy_codes())
    if not rows:
        return jsonify({"ok": False, "message": "找不到这条暂存记录"}), 404
    row = rows[0]

    fields = {key: row.get(key) for key in EDITABLE_FIELDS}
    meta = {
        "task_id": row["task_id"],
        "pipeline_version": pm.PIPELINE_VERSION,
        "origin_segment": row.get("origin_segment"),
        "split_index": row.get("split_index"),
    }
    for key in ("s3_model", "s3_ms", "s3_tokens_in", "s3_tokens_out",
                "s4_model", "s4_ms", "s4_tokens_in", "s4_tokens_out",
                "s4_primary_cause", "s4_cause_chain",
                "text_type_reason", "relevance_reason", "relevance_stage"):
        meta[key] = row.get(key)

    causes = staging_db.query(
        "SELECT id, code, name, evidence, inference, evidence_quality, "
        "evidence_quality_llm, sort_order FROM staging_causes "
        "WHERE stage_id = ? ORDER BY sort_order, id", (stage_id,))
    for c in causes:
        c["evidence_quality_label"] = LEVEL_LABELS.get(c["evidence_quality"],
                                                       c["evidence_quality"])
        c["mismatch"] = (c["evidence_quality_llm"] is not None
                         and c["evidence_quality_llm"] != c["evidence_quality"])

    return jsonify({"ok": True, "available": True, "data": {
        "stage_id": stage_id,
        "raw_id": row["raw_id"],
        "warnings": row["warnings"],
        "warning_labels": [wn.label(c) for c in row["warnings"]],
        "blocking": [c for c in row["warnings"] if c in wn.BLOCKING],
        "review": {
            "status": row["review_status"],
            "note": row["review_note"],
            "reviewer": row.get("reviewer"),
            "reviewed_at": row["reviewed_at"],
        },
        "fields": fields,
        "gates": {
            "text_type": row["text_type"],
            "text_type_confidence": row.get("text_type_confidence"),
            "relevance_verdict": row["relevance_verdict"],
            "relevance_confidence": row.get("relevance_confidence"),
        },
        "meta": meta,
        "causes": causes,
        "raw": {
            "title": row.get("raw_title"),
            "content": row.get("raw_content"),
            "source_key": row.get("raw_source_key"),
            "source_url": row.get("raw_source_url"),
            "publish_date": row.get("raw_publish_date"),
            "fetch_time": row.get("raw_fetch_time"),
        },
        "promoted": row["raw_id"] in pm.promoted_accident_ids(),
        "editable": list(EDITABLE_FIELDS),
    }})


# ══════════════════════════════════════════════════════════════
# ③ 单条审核
# ══════════════════════════════════════════════════════════════


def _coerce(field: str, value: Any) -> Any:
    """把前端来的值转成列能接受的类型。空串一律当 NULL。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field in _INT_FIELDS:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if field in _FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value).strip()


def _apply_edits(stage_id: str, edits: dict, note: str) -> list[str]:
    """写入字段与致因树。返回实际改动的项，供界面回显。"""
    changed: list[str] = []
    sets, params = [], []
    for field in EDITABLE_FIELDS:
        if field not in edits:
            continue
        sets.append(f"{field} = ?")
        params.append(_coerce(field, edits[field]))
        changed.append(field)

    now = datetime.now().isoformat(timespec="seconds")
    with staging_db.write_tx() as con:
        if sets:
            con.execute(
                f"UPDATE staging_accidents SET {', '.join(sets)} "
                f"WHERE stage_id = ?", params + [stage_id])

        if "causes" in edits:
            items = edits["causes"] or []
            con.execute("DELETE FROM staging_causes WHERE stage_id = ?",
                        (stage_id,))
            rows = [(
                stage_id, str(c.get("code") or "").strip(),
                str(c.get("name") or "").strip(), c.get("evidence"),
                c.get("inference"), c.get("evidence_quality"),
                # 重排为连续整数，不留空洞（蓝图 03 待决项 F）：人工增删之后
                # 若沿用旧序号，展示顺序就成了不可预测的
                i + 1,
            ) for i, c in enumerate(items)]
            if rows:
                con.executemany(
                    "INSERT INTO staging_causes (stage_id, code, name, "
                    "evidence, inference, evidence_quality, sort_order) "
                    "VALUES (?,?,?,?,?,?,?)", rows)
            changed.append(f"causes×{len(rows)}")

        con.execute(
            "UPDATE staging_accidents SET review_status = 'edited', "
            "review_note = ?, reviewer = ?, reviewed_at = ? WHERE stage_id = ?",
            (note or None, "本机用户", now, stage_id))
    return changed


@review_bp.post("/items/<stage_id>")
def review_item(stage_id: str):
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").strip()
    note = (body.get("note") or "").strip()

    if staging_db.query_one(
            "SELECT 1 FROM staging_accidents WHERE stage_id = ?", (stage_id,)
    ) is None:
        return jsonify({"ok": False, "message": "找不到这条暂存记录"}), 404

    now = datetime.now().isoformat(timespec="seconds")

    if action == "reject":
        # 退回必须说明原因：被退回的条目将来还会有人看到，
        # 一句「不合适」等于把判断成本原样转嫁给下一个人
        if not note:
            return jsonify({"ok": False, "message": "退回必须填写原因"}), 400
        with staging_db.write_tx() as con:
            con.execute(
                "UPDATE staging_accidents SET review_status='rejected', "
                "review_note=?, reviewer='本机用户', reviewed_at=? "
                "WHERE stage_id=?", (note, now, stage_id))
        return jsonify({"ok": True, "data": {"stage_id": stage_id,
                                             "review_status": "rejected"}})

    if action == "approve":
        with staging_db.write_tx() as con:
            con.execute(
                "UPDATE staging_accidents SET review_status='approved', "
                "review_note=?, reviewer='本机用户', reviewed_at=? "
                "WHERE stage_id=?", (note or None, now, stage_id))
        return jsonify({"ok": True, "data": {"stage_id": stage_id,
                                             "review_status": "approved"}})

    if action == "edit":
        edits = body.get("edits")
        if not isinstance(edits, dict):
            return jsonify({"ok": False, "message": "edits 必须是对象"}), 400
        changed = _apply_edits(stage_id, edits, note)
        if not changed:
            return jsonify({"ok": False,
                            "message": "没有任何可修改的字段"}), 400
        return jsonify({"ok": True, "data": {"stage_id": stage_id,
                                             "review_status": "edited",
                                             "changed": changed}})

    return jsonify({"ok": False,
                    "message": "action 必须是 approve / edit / reject"}), 400


# ══════════════════════════════════════════════════════════════
# ④ 批量通过
# ══════════════════════════════════════════════════════════════


@review_bp.post("/batch-approve")
def batch_approve():
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())
    body = request.get_json(silent=True) or {}
    ids = _ids_arg(body)
    if ids is None:
        return jsonify({"ok": False, "message": "stage_ids 必须是非空数组"}), 400

    codes = _taxonomy_codes()
    marks = ",".join("?" * len(ids))
    rows = _fetch_rows(f"WHERE s.stage_id IN ({marks})", ids, codes)
    got = {r["stage_id"]: r for r in rows}

    now = datetime.now().isoformat(timespec="seconds")
    approved, skipped = [], []
    for sid in ids:
        row = got.get(sid)
        if row is None:
            skipped.append({"stage_id": sid, "reason": "记录不存在"})
            continue
        if row["warnings"]:
            # 不静默跳过：把原因带回去，否则用户只会看到「选了 12 条过了 9 条」
            # 而不知道那 3 条为什么没进去，也就无从复核
            skipped.append({
                "stage_id": sid, "title": row["title"],
                "reason": "；".join(wn.label(c) for c in row["warnings"]),
                "warnings": row["warnings"],
            })
            continue
        approved.append(sid)

    if approved:
        with staging_db.write_tx() as con:
            marks2 = ",".join("?" * len(approved))
            con.execute(
                f"UPDATE staging_accidents SET review_status='approved', "
                f"reviewer='本机用户', reviewed_at=? "
                f"WHERE stage_id IN ({marks2})", [now] + approved)

    return jsonify({"ok": True, "data": {
        "requested": len(ids),
        "approved": len(approved),
        "stage_ids": approved,
        "skipped": skipped,
    }})


# ══════════════════════════════════════════════════════════════
# ⑤ 并入最终库
# ══════════════════════════════════════════════════════════════


def _ids_arg(body: dict) -> list[str] | None:
    ids = body.get("stage_ids")
    if not isinstance(ids, list) or not ids:
        return None
    clean = [str(s).strip() for s in ids if str(s).strip()]
    return clean or None


@review_bp.post("/promote")
def promote():
    if not _has_table("staging_accidents"):
        return jsonify(_unavailable())
    body = request.get_json(silent=True) or {}
    ids = _ids_arg(body)
    if ids is None:
        return jsonify({"ok": False, "message": "stage_ids 必须是非空数组"}), 400

    # 默认走**执行**路径而不是预演：这样「没带 confirm_token 的请求」一定会被
    # `consume_token` 拦下，而不是悄悄降级成一次无害的预演、让调用方以为成了
    dry_run = bool(body.get("dry_run", False))

    try:
        result = pm.plan(ids)
    except pm.PromoteError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    if dry_run:
        token = pm.issue_token(ids, result)
        return jsonify({"ok": True, "data": {
            "mode": "dry_run", "ready": result["ok"],
            "plan": result["plan"], **token,
        }})

    try:
        outcome = pm.execute(ids, body.get("confirm_token") or "")
    except pm.PromoteError as e:
        # 409 = 当前状态不允许这次并入（缺令牌、有冲突、缺索引）
        return jsonify({"ok": False, "message": str(e)}), 409

    return jsonify({"ok": True, "data": {"mode": "executed", **outcome}})


@review_bp.post("/rollback")
def rollback():
    if not _has_table("promote_log"):
        return jsonify(_unavailable())
    try:
        out = pm.rollback()
    except pm.PromoteError as e:
        return jsonify({"ok": False, "message": str(e)}), 409
    return jsonify({"ok": True, "data": out})


@review_bp.get("/history")
def history():
    if not _has_table("promote_log"):
        return jsonify(_unavailable())
    return jsonify({"ok": True, "available": True, "data": pm.history(20)})


# ══════════════════════════════════════════════════════════════
# ⑥ 审核漏斗
# ══════════════════════════════════════════════════════════════


def funnel_payload() -> dict:
    """供审核页与总览页共用，避免两处各算一遍、口径不同。"""
    if not _has_table("staging_accidents"):
        return {"available": False, "hint": BUILD_HINT}
    warn_rows = _warn_scan(_taxonomy_codes())
    counts = _counts(warn_rows, pm.promoted_accident_ids())
    return {
        "available": True,
        "funnel": [
            {"stage": "暂存总数", "count": counts["total"]},
            {"stage": "零告警", "count": counts["clean"]},
            {"stage": "审核通过", "count": counts["approved"] + counts["edited"]},
            {"stage": "已并入", "count": counts["promoted"]},
        ],
        "by_status": {s: counts[s] for s in _REVIEW_STATUS},
        "by_warning": wn.summarize(warn_rows),
        "promote_history": pm.history(5),
    }


@review_bp.get("/stats")
def stats():
    return jsonify({"ok": True, "data": funnel_payload()})
