"""并入最终库：把审核通过的暂存行写进 `accidents` + `caics_causes`。

这是全项目**唯一会写最终库的操作**。最终库里有 2,623 条已发表的论文数据，
一次错误的并入会直接污染论文口径。所以本模块的全部设计只围绕一件事：

    并入要么完整成功，要么完全不影响现有数据。

三条不可协商的规则
------------------
1. **主表与子表在同一事务**——不允许出现「主表已更新、子表还是旧的」中间态
2. **子表先 DELETE 再 INSERT**——这是消除重复编码的直接手段。历史教训：
   主表用 `INSERT OR REPLACE`（在 SQLite 上等于 DELETE+INSERT）而子表用裸
   `INSERT`，重跑一次就多一批重复行，正是实测 338 行 (accident_id, code)
   重复的成因
3. **写入连接显式开 `PRAGMA foreign_keys=ON`**——SQLite 的外键是**连接级**
   开关，默认 OFF。历史上那 1,041 行孤儿，技术条件就是没人开它

为什么强制先 dry_run
--------------------
`plan()` 与实际执行分成两步，执行必须带上 `plan()` 返回的 confirm token。
token 与 `stage_ids` 集合绑定——防的是「用户改了选择却沿用上一次的确认」，
那种错误一旦发生，就是往论文数据里混入一批没被预览过的东西。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..database.backup import safe_backup
from ..web import db as final_db
from ..web import staging_db

PIPELINE_VERSION = "caics_v1.0"
BACKUP_DIRNAME = "db_backups"
PLAN_TTL = timedelta(hours=2)


class PromoteError(RuntimeError):
    """并入被拒绝。消息面向用户，应原样展示。"""


# ══════════════════════════════════════════════════════════════════════
# 字段映射：staging_accidents → accidents 48 列
# ══════════════════════════════════════════════════════════════════════
#
# 逐列写出，**不允许"大约对应"**。写成 SQL 选择列表而不是 Python 字典，
# 是因为映射本身就该是可读的一行 SQL：改错一个名字在这里一眼能看见。
#
# ⚠️ 有 4 处两边名字不同，是本模块最容易出错的地方，已就地标注：
#     staging.location          → accidents.province
#     staging.relevance_verdict → accidents.classification_verdict
#     staging.s3_ms             → accidents.step1_duration_ms
#     staging.s4_ms             → accidents.step2_duration_ms
#
# 别指望"名字差不多"来推——推错不会报错，只会安静地把省份写进城市列。

SELECT_LIST = """
    r.raw_id                  AS id,
    s.title                   AS title,
    r.source_url              AS source_url,
    r.source_key              AS source_name,
    r.fetch_time              AS crawl_date,
    r.publish_date            AS publish_date,
    s.date                    AS date,
    s.accident_time           AS accident_time,
    s.location                AS province,
    s.city                    AS city,
    s.district                AS district,
    s.accident_location_detail AS accident_location_detail,
    s.weather                 AS weather,
    s.project_name            AS project_name,
    s.project_type            AS project_type,
    s.building_structure      AS building_structure,
    s.construction_phase      AS construction_phase,
    s.work_task               AS work_task,
    s.developer               AS developer,
    s.contractor              AS contractor,
    s.contractor_type         AS contractor_type,
    s.supervision_company     AS supervision_company,
    s.accident_type           AS accident_type,
    s.severity                AS severity,
    s.deaths                  AS deaths,
    s.injuries                AS injuries,
    s.trapped_count           AS trapped_count,
    s.direct_economic_loss    AS direct_economic_loss,
    s.collapse_subtype        AS collapse_subtype,
    s.fall_height             AS fall_height,
    s.equipment_involved      AS equipment_involved,
    s.material_involved       AS material_involved,
    s.worker_type             AS worker_type,
    s.direct_cause            AS direct_cause,
    s.report_type             AS report_type,
    s.text_type               AS text_type,
    s.relevance_verdict       AS classification_verdict,
    s.relevance_confidence    AS classification_confidence,
    1                         AS source_count,
    s.summary                 AS summary,
    r.content                 AS raw_text,
    NULL                      AS search_enriched_text,
    0                         AS search_performed,
    '{ver}'                   AS pipeline_version,
    s.s3_ms                   AS step1_duration_ms,
    s.s4_ms                   AS step2_duration_ms,
    NULL                      AS work_task_enum
""".strip()

# 列顺序须与 SELECT_LIST 完全一致（下面按同样顺序拆出来喂 INSERT）
COLUMNS: list[str] = [
    "id", "title", "source_url", "source_name", "crawl_date", "publish_date",
    "date", "accident_time", "province", "city", "district",
    "accident_location_detail", "weather", "project_name", "project_type",
    "building_structure", "construction_phase", "work_task", "developer",
    "contractor", "contractor_type", "supervision_company", "accident_type",
    "severity", "deaths", "injuries", "trapped_count", "direct_economic_loss",
    "collapse_subtype", "fall_height", "equipment_involved", "material_involved",
    "worker_type", "direct_cause", "report_type", "text_type",
    "classification_verdict", "classification_confidence", "source_count",
    "summary", "raw_text", "search_enriched_text", "search_performed",
    "pipeline_version", "step1_duration_ms", "step2_duration_ms",
    "work_task_enum",
]

# `created_at` 不来自暂存行，是并入时刻，单独补在最后
CREATED_AT_COL = "created_at"

# NOT NULL 且**没有** DEFAULT 的列：值为空就必须拦下来，否则 INSERT 直接报错。
# 拦住并报给用户，比让整批并入失败好——审核员能就地补上再并入。
REQUIRED: dict[str, str] = {
    "title": "标题",
    "source_url": "来源网址",
    "source_name": "来源站点",
    "project_type": "工程类型",
    "accident_type": "事故类型",
    "severity": "事故等级",
    "report_type": "报告类型",
    "text_type": "文本体裁",
    "classification_verdict": "相关性判定",
    "classification_confidence": "相关性置信度",
    "summary": "摘要",
    "raw_text": "原文正文",
}

# NOT NULL 但**有** DEFAULT 的列：空值用默认值顶上。
# 注意 SQLite 的 DEFAULT 只在「该列不出现在 INSERT 列表里」时生效，
# 显式写 NULL 仍然会撞 NOT NULL，所以必须在这里换成实值。
DEFAULTS: dict[str, Any] = {
    "construction_phase": "未知",
    "deaths": 0,
    "injuries": 0,
    "trapped_count": 0,
    "source_count": 1,
    "search_performed": 0,
}

# 期望最终库上存在的索引（并入前检查；缺了说明用户还没跑 add_indexes.py）
REQUIRED_INDEXES = (
    "idx_cause_acc", "idx_cause_code",
    "idx_acc_type", "idx_acc_severity", "idx_acc_date",
    "idx_acc_city", "idx_acc_deaths",
)


# ══════════════════════════════════════════════════════════════════════
# 取数与校验
# ══════════════════════════════════════════════════════════════════════


def _select_sql() -> str:
    return (
        "SELECT " + SELECT_LIST.format(ver=PIPELINE_VERSION)
        + " FROM staging_accidents s "
          "LEFT JOIN raw_accidents r ON r.raw_id = s.raw_id "
          "WHERE s.stage_id = ?"
    )


def _load_rows(stage_ids: Sequence[str]) -> list[dict[str, Any]]:
    """按并入口径取数。缺 `raw_accidents` 关联的行会被标出来。"""
    sql = _select_sql()
    rows: list[dict[str, Any]] = []
    for sid in stage_ids:
        got = staging_db.query(sql, (sid,))
        if not got:
            rows.append({"_stage_id": sid, "_missing": True})
            continue
        row = got[0]
        row["_stage_id"] = sid
        rows.append(row)
    return rows


def _review_states(stage_ids: Sequence[str]) -> dict[str, str]:
    marks = ",".join("?" * len(stage_ids))
    rows = staging_db.query(
        f"SELECT stage_id, review_status FROM staging_accidents "
        f"WHERE stage_id IN ({marks})", list(stage_ids))
    return {r["stage_id"]: r["review_status"] for r in rows}


def _cause_rows(stage_id: str) -> list[tuple]:
    rows = staging_db.query(
        "SELECT code, name, evidence, inference, evidence_quality, sort_order "
        "FROM staging_causes WHERE stage_id = ? ORDER BY sort_order, id",
        (stage_id,))
    return [
        (r["code"], r["name"], r["evidence"], r["inference"],
         r["evidence_quality"], r["sort_order"] or 0)
        for r in rows
    ]


def _check_schema() -> tuple[bool, str]:
    """确认最终库的列与映射一致。少一列都不许写。"""
    try:
        have = set(final_db.table_columns("accidents"))
    except Exception as e:                      # 库不在/表不在
        return False, f"读不到最终库结构：{e}"
    need = set(COLUMNS) | {CREATED_AT_COL}
    missing = sorted(need - have)
    if missing:
        return False, f"最终库 accidents 缺列：{missing}"
    return True, "pass"


def _check_indexes() -> dict[str, Any]:
    """并入库前检查索引。缺索引时全表扫描的写入会慢到影响并发并入。"""
    try:
        have = {r["name"] for r in final_db.list_indexes()}
    except Exception:
        have = set()
    missing = [i for i in REQUIRED_INDEXES if i not in have]
    return {"present": sorted(have & set(REQUIRED_INDEXES)),
            "missing": missing, "ok": not missing}


def plan(stage_ids: Sequence[str]) -> dict[str, Any]:
    """预演一次并入。**不写任何库**，返回计划与冲突。"""
    ids = [str(s).strip() for s in stage_ids if str(s).strip()]
    if not ids:
        raise PromoteError("没有选中任何条目")

    schema_ok, schema_msg = _check_schema()
    index_info = _check_indexes()

    # 审核状态：只有 approved / edited 能并入
    states = _review_states(ids)
    not_approved = [
        {"stage_id": sid, "reason": f"审核状态是 {states.get(sid) or '不存在'}，"
                                    f"只有「通过」或「已修改」的条目能并入"}
        for sid in ids
        if states.get(sid) not in ("approved", "edited")
    ]
    unknown = [sid for sid in ids if sid not in states]

    # 逐条取数与必填校验
    rows = _load_rows(ids)
    conflicts: list[dict[str, str]] = list(not_approved)
    checked: list[dict[str, Any]] = []
    causes_total = 0

    final_ids: set[str] = set()
    try:
        marks = ",".join("?" * len(ids))
        final_ids = {
            r["id"] for r in final_db.query(
                f"SELECT id FROM accidents WHERE id IN ({marks})", ids)
        }
    except Exception:
        # 最终库读不到时上面 _check_schema 已经会报错，这里保持安静，
        # 让「缺索引/缺列」那条更具体的信息成为用户看到的唯一原因
        pass

    for row in rows:
        sid = row["_stage_id"]
        if row.get("_missing"):
            conflicts.append({"stage_id": sid,
                              "reason": "暂存行的原文关联丢失（raw_accidents "
                                        "里找不到它），无法确定来源"})
            continue
        bad = [f"{label}（{col}）" for col, label in REQUIRED.items()
               if row.get(col) in (None, "")]
        if bad:
            conflicts.append({"stage_id": sid,
                              "reason": "必填字段为空：" + "、".join(bad)})
            continue
        n_causes = len(_cause_rows(sid))
        causes_total += n_causes
        checked.append({
            "stage_id": sid,
            "accident_id": row["id"],
            "title": row["title"],
            "causes": n_causes,
            "replaces": row["id"] in final_ids,
        })

    # 同一批里两条暂存行映射到同一个 accident_id（同一 URL 被抓了两次）
    seen: dict[str, int] = {}
    for item in checked:
        seen[item["accident_id"]] = seen.get(item["accident_id"], 0) + 1
    for aid, n in seen.items():
        if n > 1:
            dups = [i["stage_id"] for i in checked if i["accident_id"] == aid]
            for sid in dups:
                conflicts.append({
                    "stage_id": sid,
                    "reason": f"本批内有 {n} 条暂存行指向同一个事故 "
                              f"（{aid}），它们会互相覆盖，请先去重",
                })

    insert_n = sum(1 for i in checked if not i["replaces"])
    replace_n = sum(1 for i in checked if i["replaces"])

    return {
        "stage_ids": ids,
        "plan": {
            "accidents_insert": insert_n,
            "accidents_replace": replace_n,
            "causes_total": causes_total,
            "conflicts": conflicts,
            "schema_check": schema_msg if not schema_ok else "pass",
            "index_check": index_info,
            "items": checked,
        },
        "ok": schema_ok and not conflicts and index_info["ok"],
    }


# ══════════════════════════════════════════════════════════════════════
# 令牌
# ══════════════════════════════════════════════════════════════════════


def issue_token(stage_ids: Sequence[str], plan_result: dict[str, Any]) -> dict[str, Any]:
    """把这次 dry_run 的结果固化成一次性令牌，存工作库。"""
    _purge_expired()
    token = uuid.uuid4().hex[:12]
    now = datetime.now()
    with staging_db.write_tx() as con:
        con.execute(
            "INSERT INTO promote_plans (token, stage_ids, plan, created_at, "
            "expires_at) VALUES (?,?,?,?,?)",
            (token, json.dumps(list(stage_ids), ensure_ascii=False),
             json.dumps(plan_result["plan"], ensure_ascii=False),
             now.isoformat(timespec="seconds"),
             (now + PLAN_TTL).isoformat(timespec="seconds")),
        )
    return {"confirm_token": token,
            "expires_at": (now + PLAN_TTL).isoformat(timespec="seconds")}


def _purge_expired() -> None:
    with staging_db.write_tx() as con:
        con.execute("DELETE FROM promote_plans WHERE expires_at < ?",
                    (datetime.now().isoformat(timespec="seconds"),))


def consume_token(token: str, stage_ids: Sequence[str]) -> None:
    """校验令牌：存在、未过期、且绑定的 stage_ids 与本次完全一致。"""
    if not token:
        raise PromoteError("缺少确认令牌：并入前必须先做一次预演（dry_run）")
    row = staging_db.query_one(
        "SELECT stage_ids, expires_at FROM promote_plans WHERE token = ?",
        (token,))
    if row is None:
        raise PromoteError("确认令牌无效或已被使用，请重新预演")
    if row["expires_at"] < datetime.now().isoformat(timespec="seconds"):
        raise PromoteError("确认令牌已过期，请重新预演")
    bound = json.loads(row["stage_ids"])
    if sorted(bound) != sorted(str(s) for s in stage_ids):
        raise PromoteError(
            f"选择集变了：令牌绑定的是 {len(bound)} 条，本次是 "
            f"{len(stage_ids)} 条。请重新预演。")
    with staging_db.write_tx() as con:
        con.execute("DELETE FROM promote_plans WHERE token = ?", (token,))


# ══════════════════════════════════════════════════════════════════════
# 执行
# ══════════════════════════════════════════════════════════════════════


def backup_final_db(db_path: Path | None = None) -> Path:
    """并入前强制备份最终库。返回备份文件路径。"""
    path = Path(db_path) if db_path else final_db.resolve_db_path()
    if not path.exists():
        raise PromoteError(f"最终库不存在：{path}")
    out_dir = path.parent / BACKUP_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    # 时间戳带微秒：同一秒内连续两次并入会撞名。撞名时 safe_backup 会拒绝写入，
    # 而"两次并入各留一份备份"本来就更该是行为——旧写法 copy2 是静默覆盖，
    # 第一次并入前的状态会被第二次冲掉
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = out_dir / f"{path.name}.bak.before_promote_{ts}"
    # 不用 shutil.copy2：本模块的 _connect_writable 会把最终库切成 WAL，而 WAL
    # 是写进文件头的持久属性——从那以后库一直是 WAL，文件级复制会静默丢数据。
    # 这份备份是 rollback docstring 里说的「手工恢复的最后手段」，不能是坏的。
    safe_backup(path, dest)
    return dest


def _connect_writable(path: Path) -> sqlite3.Connection:
    """最终库的可写连接。

    `web/db.py` 只提供只读句柄是**故意的**——展示层没有写权限这条边界，
    比任何注释都可靠。写权限集中在本模块这一个洞里，便于审计。
    """
    con = sqlite3.connect(path, isolation_level=None, timeout=15.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")      # ← 规则 3
    con.execute("PRAGMA journal_mode = WAL")
    return con


def execute(stage_ids: Sequence[str], token: str,
            db_path: Path | None = None,
            *, skip_index_check: bool = False) -> dict[str, Any]:
    """真正并入。带令牌校验、强制备份、单事务写入。

    返回写入统计与备份路径；失败时抛 `PromoteError`（库保持原样）。
    """
    ids = [str(s).strip() for s in stage_ids if str(s).strip()]
    consume_token(token, ids)

    result = plan(ids)
    if not result["ok"]:
        conflicts = result["plan"]["conflicts"]
        if conflicts:
            raise PromoteError("预演未通过：" + "；".join(
                c["reason"] for c in conflicts[:3]))
        if not result["plan"]["index_check"]["ok"]:
            raise PromoteError(
                "最终库缺索引 "
                f"{result['plan']['index_check']['missing']}，请先运行："
                "python3 scripts/add_indexes.py")
        raise PromoteError(f"预演未通过：{result['plan']['schema_check']}")

    path = Path(db_path) if db_path else final_db.resolve_db_path()
    backup = backup_final_db(path)

    rows = [r for r in _load_rows(ids) if not r.get("_missing")]
    now = datetime.now().isoformat(timespec="seconds")
    placeholders = ",".join("?" * (len(COLUMNS) + 1))
    insert_sql = (
        f"INSERT OR REPLACE INTO accidents ({','.join(COLUMNS + [CREATED_AT_COL])}) "
        f"VALUES ({placeholders})")

    causes_written = 0
    con = _connect_writable(path)
    try:
        con.execute("BEGIN IMMEDIATE")           # 规则 1：立刻取写锁
        for row in rows:
            values = []
            for col in COLUMNS:
                v = row.get(col)
                if v in (None, "") and col in DEFAULTS:
                    v = DEFAULTS[col]
                values.append(v)
            values.append(now)
            con.execute(insert_sql, values)

            # 规则 2：先清后写，不裸 INSERT
            con.execute("DELETE FROM caics_causes WHERE accident_id = ?",
                        (row["id"],))
            items = _cause_rows(row["_stage_id"])
            if items:
                con.executemany(
                    "INSERT INTO caics_causes (accident_id, code, name, "
                    "evidence, inference, evidence_quality, sort_order) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [(row["id"], *c) for c in items])
                causes_written += len(items)
        con.execute("COMMIT")
    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass                                  # SQLite 可能已自动回滚
        raise PromoteError(f"并入失败，已整体回滚，最终库未改动：{e}") from e
    finally:
        con.close()

    with staging_db.write_tx() as con2:
        con2.execute(
            "INSERT INTO promote_log (token, stage_ids, promoted, "
            "causes_written, backup_path, created_at) VALUES (?,?,?,?,?,?)",
            (token, json.dumps(ids, ensure_ascii=False), len(rows),
             causes_written, str(backup), now))

    return {"promoted": len(rows), "causes_written": causes_written,
            "backup": str(backup), "backup_bytes": backup.stat().st_size,
            "at": now}


# ══════════════════════════════════════════════════════════════════════
# 回滚
# ══════════════════════════════════════════════════════════════════════


def last_promotion() -> dict[str, Any] | None:
    row = staging_db.query_one(
        "SELECT * FROM promote_log WHERE rolled_back_at IS NULL "
        "ORDER BY id DESC LIMIT 1")
    return row


def rollback(db_path: Path | None = None) -> dict[str, Any]:
    """撤销最近一次并入：按 `stage_ids` 反查写入的事故，事务内删主表+子表。

    **实现为逆操作而不是还原备份文件**：还原整个文件会连带丢掉这之后别人
    做的任何改动（采集、其他并入），而逆操作只动这次并入写进去的那几行。
    备份文件仍然保留，作为手工恢复的最后手段。
    """
    entry = last_promotion()
    if entry is None:
        raise PromoteError("没有可回滚的并入记录")

    stage_ids = json.loads(entry["stage_ids"])

    # ⚠️ 必须先映射：`promote_log.stage_ids` 记的是暂存行的 stage_id，而
    # `accidents.id` 是 raw_id（= md5(source_url)[:12]），两者不同名。直接拿
    # stage_id 去 accidents 里查会一条都匹配不上，回滚就会"成功但什么都没删"
    # ——静默失效比报错危险得多。
    marks0 = ",".join("?" * len(stage_ids))
    accident_ids = [r["raw_id"] for r in staging_db.query(
        f"SELECT raw_id FROM staging_accidents WHERE stage_id IN ({marks0})",
        stage_ids) if r["raw_id"]]
    if not accident_ids:
        raise PromoteError(
            "这次并入对应的暂存记录已不存在，无法按逆操作回滚；"
            f"如需恢复请手工使用备份：{entry['backup_path']}")

    path = Path(db_path) if db_path else final_db.resolve_db_path()
    con = _connect_writable(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        marks = ",".join("?" * len(accident_ids))
        written = [r["id"] for r in con.execute(
            f"SELECT id FROM accidents WHERE id IN ({marks})",
            accident_ids).fetchall()]
        removed_causes = 0
        if written:
            marks2 = ",".join("?" * len(written))
            removed_causes = con.execute(
                f"DELETE FROM caics_causes WHERE accident_id IN ({marks2})",
                written).rowcount
            con.execute(f"DELETE FROM accidents WHERE id IN ({marks2})", written)
        con.execute("COMMIT")
    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise PromoteError(f"回滚失败，最终库未改动：{e}") from e
    finally:
        con.close()

    now = datetime.now().isoformat(timespec="seconds")
    with staging_db.write_tx() as con2:
        con2.execute("UPDATE promote_log SET rolled_back_at = ? WHERE id = ?",
                     (now, entry["id"]))

    return {"rollback_of": entry["id"], "removed_accidents": len(written),
            "removed_causes": removed_causes, "at": now,
            "backup": entry["backup_path"]}


# ══════════════════════════════════════════════════════════════════════
# 统计
# ══════════════════════════════════════════════════════════════════════


def history(limit: int = 10) -> list[dict[str, Any]]:
    """并入历史，最近的在前。

    与 `promoted_accident_ids()` 同理：早期版本建的工作库没有 `promote_log`，
    缺表就是「还没有任何并入记录」，返回空列表。漏斗统计
    （`/api/review/stats` 的 `funnel_payload`）直接调它，返回空集比整页 500 合理。
    """
    try:
        return staging_db.query(
            "SELECT id, token, stage_ids, promoted, causes_written, backup_path, "
            "created_at, rolled_back_at FROM promote_log "
            "ORDER BY id DESC LIMIT ?", (limit,))
    except sqlite3.OperationalError:
        return []


def promoted_accident_ids() -> set[str]:
    """所有已并入最终库的 accident_id（= raw_id）。用于统计漏斗。

    早期版本建的工作库没有 `promote_log` 表（`staging_schema.py` 后来才加，
    DDL 是 `CREATE TABLE IF NOT EXISTS`，重跑一次建表即可补上）。缺表等价于
    「从没并入过任何东西」，返回空集——调用它的是审核队列与漏斗统计，都在
    页面主路径上，不该为一张空表把整页打成 500。
    """
    out: set[str] = set()
    try:
        rows = staging_db.query(
            "SELECT stage_ids FROM promote_log WHERE rolled_back_at IS NULL")
    except sqlite3.OperationalError:
        return out
    for row in rows:
        try:
            ids = json.loads(row["stage_ids"])
        except (TypeError, ValueError):
            continue
        if not ids:
            # 空列表会让下面的 `IN ()` 变成语法错误——SQLite 不接受空集合
            continue
        rows = staging_db.query(
            f"SELECT raw_id FROM staging_accidents WHERE stage_id IN "
            f"({','.join('?' * len(ids))})", ids)
        out.update(r["raw_id"] for r in rows if r["raw_id"])
    return out


def counts_by(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """审核漏斗里「已并入」那一格。"""
    return {"promoted": len(rows)}
