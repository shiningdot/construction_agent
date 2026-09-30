"""提取执行器：把 `raw_accidents` 里已确认相关的原文送进 S1–S4，产出落暂存区。

对应蓝图 02（阶段②）。与采集执行器（`collector/executor.py`）同构：
任务先落库再跑、逐条写、可中断、可续跑，进程重启后状态不丢。

## 三条硬约束

**① 成本确认是一道硬闸门。** 建任务只写 `pending` 行并把预估回给前端，
**不调任何 LLM**。只有显式 `start_task()` 才提交执行。旧 web 的
`/api/process` 没有限流也没有成本控制，40 秒的同步请求必然超时——这一条
直接是它的反面。

**② 每个工作线程各持一个 pipeline 实例。** `AccidentClassifier.last_usage`
是实例状态（S1/S2 两个入口都会先清零再累加）。共享一个实例会让并发的
两条互相冲掉用量，记账就废了。`_WORKER` 用 thread-local 保管。

**③ 单条失败不许掀掉整批。** S3 返回空文本（`max_tokens` 不足的已知失败
模式）在旧管线里是被 `except: continue` 埋掉的；这里记进 `errors` 数组，
页面上看得到，任务继续（蓝图 02 §8 验收第 10 条）。

## 一处刻意的"不实现"

`enable_split` 参数会落库，但**当前不生效**：`CaicsPipeline.run_once()` 拿到
MULTI 的拆分片段只存进 `multi_segments` 备查，仍把整段文本送进 S2–S4。
这是既有行为，主库那条唯一的 `multi` 就是这样产出的。改成真拆分等于换一套
产出口径（且拆分质量本身还没评估过，蓝图 02 §9 待决项 B），属于独立决策，
不在本模块擅自决定。`origin_segment` / `split_index` 因此当前恒为 NULL / 0。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..web import staging_db
from .caics_pipeline import (
    PURGE_NON_CONSTRUCTION,
    PURGE_TEXT_TYPE,
    STATUS_COMPLETED,
    STATUS_PURGED,
    CaicsPipeline,
)
from .evidence_quality import compute_evidence_quality, compute_sort_order

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 3

# 单条总预算（秒）。S3 实测 16–19 s + S4 21–30 s，加 S1/S2 与可选的搜索增强，
# 正常在 60 s 内。给到 300 s 是给慢站点（省级站 PDF 正文很长）留余量——
# 超时记失败并跳过该条，任务继续，不是整批中止。
ITEM_TIMEOUT_S = 300

# 成本预估系数（token/条）。来自蓝图 02 §3.4 的实测口径。
EST_TOKENS = {"S1": 500, "S2": 1000, "S3": 8000, "S4": 16000}

# 人民币单价（元 / 1M token）。DeepSeek V4 auto 档位的近似值，仅用于给
# 用户一个量级概念。**页面必须写明这是估算**——它不参与任何结算。
PRICE_IN_CNY_PER_M = 2.0
PRICE_OUT_CNY_PER_M = 8.0

_CANCEL: set[str] = set()
_CANCEL_LOCK = threading.Lock()

# 任务级调度池。与采集各用各的：一次采集与一次提取同时跑是常见场景，
# 共用一个池会让「采集占满 worker、提取排队」变成默认行为。
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="extract")

# 每个工作线程自己的 pipeline。见模块 docstring ②。
_WORKER = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _pipeline() -> CaicsPipeline:
    """取当前线程的 pipeline，没有就建一个。

    `enable_search_enhance=True` 与 `run_batch_pipeline.run_batch()` 一致——
    M2 采回来的新数据本来就该走完整链路。要复现历史批次才需要关掉。
    """
    pipe = getattr(_WORKER, "pipeline", None)
    if pipe is None:
        pipe = CaicsPipeline()
        _WORKER.pipeline = pipe
    return pipe


# --------------------------------------------------------------------------
# S3 输出 → staging_accidents 行
# --------------------------------------------------------------------------

# staging_accidents 里 S3 那 31 列，**顺序与 S3 的 to_dict() 完全对应**。
# 列名故意保留 S3 的输出原名（改名的活集中在并入映射层，见蓝图 03 §5.1），
# 因此这里是一比一搬运，不做任何改名——唯一的例外是 `location`，
# 它在并入时才会变成 `accidents.province`，暂存区照旧叫 location。
S3_COLUMNS: tuple[str, ...] = (
    "title", "date", "accident_time", "location", "city", "district",
    "accident_location_detail", "project_name", "project_type",
    "construction_phase", "building_structure", "accident_type", "severity",
    "deaths", "injuries", "trapped_count", "collapse_subtype", "fall_height",
    "work_task", "equipment_involved", "material_involved", "worker_type",
    "weather", "contractor", "contractor_type", "developer",
    "supervision_company", "direct_economic_loss", "direct_cause",
    "summary", "report_type",
)

_INSERT_STAGE = f"""
INSERT OR IGNORE INTO staging_accidents
    (stage_id, raw_id, task_id, origin_segment, split_index,
     text_type, text_type_reason, text_type_confidence,
     relevance_verdict, relevance_reason, relevance_stage, relevance_confidence,
     {", ".join(S3_COLUMNS)},
     s3_model, s3_ms, s3_tokens_in, s3_tokens_out,
     s4_model, s4_ms, s4_tokens_in, s4_tokens_out,
     created_at)
VALUES
    (:stage_id, :raw_id, :task_id, :origin_segment, :split_index,
     :text_type, :text_type_reason, :text_type_confidence,
     :relevance_verdict, :relevance_reason, :relevance_stage, :relevance_confidence,
     {", ".join(":" + c for c in S3_COLUMNS)},
     :s3_model, :s3_ms, :s3_tokens_in, :s3_tokens_out,
     :s4_model, :s4_ms, :s4_tokens_in, :s4_tokens_out,
     :created_at)
"""

_INSERT_CAUSE = """
INSERT INTO staging_causes
    (stage_id, code, name, evidence, inference,
     evidence_quality, evidence_quality_rule, evidence_quality_llm, sort_order)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# 缺键补默认值：加列时只要改这里和 S3_COLUMNS，不用在两条通路上各写一遍
_S3_DEFAULTS: dict[str, Any] = {c: None for c in S3_COLUMNS}


def _stage_row(raw: dict, out, task_id: str, *,
               s3_model: str = "", s4_model: str = "") -> Optional[dict]:
    """一条 `PipelineOutcome`（已完成）→ staging_accidents 的一行。

    只处理「两道闸门都过了、走完 S3」的情形；被闸门拦下的不在这里建行——
    那是 `by_type` 的计数，不是暂存区的记录（蓝图 02 §8 验收第 6 条）。
    """
    if out.basic is None:
        return None

    basic = out.basic.to_dict()
    tt = out.text_type
    cl = out.classification

    row: dict[str, Any] = {
        "stage_id": _new_id(),
        "raw_id": raw["raw_id"],
        "task_id": task_id,
        # MULTI 不拆分，见模块 docstring。两个字段留空是为了让「这条来自拆分」
        # 与「这条是原文」在数据上可区分——将来真开拆分时才有值。
        "origin_segment": None,
        # ⚠️ 必须是 0 不是 NULL：SQLite 的 UNIQUE 认为 NULL 互不相等，
        # `UNIQUE(raw_id, split_index)` 在 NULL 时**拦不住重复**（实测：
        # 同一 (raw_id, NULL) 能连插两次）。断点续跑全靠这个约束。
        "split_index": 0,
        "text_type": tt.text_type.value if tt else None,
        "text_type_reason": tt.reason if tt else None,
        "text_type_confidence": tt.confidence if tt else None,
        "relevance_verdict": cl.verdict.value if cl else None,
        "relevance_reason": cl.reason if cl else None,
        # 规则直判还是 LLM 仲裁，看 verdict 就知道；列里存的是「该值来自哪一层」
        "relevance_stage": (
            "rule" if cl and cl.verdict.value.startswith("rule_")
            else ("llm" if cl else None)),
        "relevance_confidence": cl.confidence if cl else None,
        # S3 的 31 列一比一搬运
        **{c: basic.get(c) for c in S3_COLUMNS},
        "s3_model": s3_model or None,
        "s3_ms": out.stage_ms("S3"),
        "s3_tokens_in": out.basic.tokens_in,
        "s3_tokens_out": out.basic.tokens_out,
        # 档位写在数据里而不是只写在配置里：论文实验脚本硬编码 pro、主管道
        # 读 .env（现为 flash），批次间混用档位时事后无从追溯（蓝图 02 §9 G）
        "s4_model": (s4_model or None) if out.causes else None,
        "s4_ms": out.stage_ms("S4"),
        "s4_tokens_in": out.causes.tokens_in if out.causes else 0,
        "s4_tokens_out": out.causes.tokens_out if out.causes else 0,
        "created_at": _now(),
    }
    return {**_S3_DEFAULTS, **row}


def _cause_rows(stage_id: str, out) -> list[tuple]:
    """S4 的致因项 → staging_causes 的行。

    三列证据等级：`evidence_quality` 是生效值（= 现算规则），`_rule` 显式记录
    它来自规则计算，`_llm` 留给 S4 自报——**当前恒为 NULL**。为什么：蓝图 02
    §6 明确「不改 prompt」，而让 S4 自报必须改 tool schema；那等于换一套理论
    版本，蓝图 §9 待决项 A 把它列为**需要用户确认**的事项。列先建着，
    等裁决下来再填，不用改表。
    """
    if out.causes is None:
        return []
    rows = []
    for c in out.causes.causes:
        eq = compute_evidence_quality(c.evidence, c.inference)
        rows.append((
            stage_id, c.code, c.name, c.evidence, c.inference,
            eq, eq, None, compute_sort_order(c.code),
        ))
    return rows


# --------------------------------------------------------------------------
# 单条与整批
# --------------------------------------------------------------------------


@dataclass
class ItemOutcome:
    """一条原文跑完的事实。字段直接对应 `extract_tasks.stats.errors`。"""

    raw_id: str
    title: str = ""
    ok: bool = False
    status: str = ""            # completed | purged | failed
    purge_reason: str = ""      # text_type | non_construction
    text_type: str = ""
    stage: str = ""             # 失败发生在哪一段
    error: str = ""
    causes: int = 0
    ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def as_dict(self) -> dict:
        return {"raw_id": self.raw_id, "title": self.title[:60],
                "stage": self.stage, "reason": self.error}


def run_one(raw: dict, task_id: str) -> ItemOutcome:
    """跑一条原文：S1→S2→S3→S4，把结果写进暂存区。

    异常一律收成 `ItemOutcome.error`，不让它掀掉整批。
    """
    rid = raw["raw_id"]
    out = ItemOutcome(raw_id=rid, title=raw.get("title") or "")
    started = time.monotonic()
    pipe = _pipeline()

    try:
        res = pipe.run_once(
            title=raw.get("title") or "",
            text=raw.get("content") or "",
            publish_date=(raw.get("publish_date") or "")[:10],
            source_id=rid,
            source_name=raw.get("source_key") or "",
        )
    except Exception as e:  # noqa: BLE001
        out.status, out.stage = "failed", "RUN"
        out.error = f"{type(e).__name__}: {e}"[:500]
        out.ms = int((time.monotonic() - started) * 1000)
        logger.warning("[%s] %s 执行异常: %s", task_id, rid, out.error)
        return out

    out.ms = int((time.monotonic() - started) * 1000)
    out.status = res.status
    out.purge_reason = res.purge_reason
    out.text_type = res.text_type_value
    out.tokens_in = sum(x.tokens_in for x in (res.text_type, res.classification,
                                              res.basic, res.causes) if x)
    out.tokens_out = sum(x.tokens_out for x in (res.text_type, res.classification,
                                                res.basic, res.causes) if x)

    # ── 被闸门拦下：不建暂存行，只回报原因 ──
    if res.status == STATUS_PURGED:
        out.ok = True
        logger.info("[%s] %s 被闸门拦下（%s）", task_id, rid, res.purge_reason)
        return out

    # ── 失败：说清楚死在哪一段，别让它静默 ──
    if res.status != STATUS_COMPLETED:
        out.stage = _failed_stage(res)
        out.error = (res.purge_reason or "未知失败")[:500]
        logger.warning("[%s] %s 失败于 %s: %s", task_id, rid, out.stage, out.error)
        return out

    row = _stage_row(
        raw, res, task_id,
        # 模型名从组件实例上读，不从 settings 读：pipeline 的组件是可以注入
        # 替换的（测试用假件），读 settings 会把「实际用的」记成「配置的」。
        s3_model=getattr(pipe.extractor, "_model", "") or "",
        s4_model=getattr(pipe.encoder, "_model", "") or "",
    )
    if row is None:
        out.stage, out.error = "S3", "S3 无结果（extract() 返回 None）"
        out.status = "failed"
        return out

    causes = _cause_rows(row["stage_id"], res)
    out.causes = len(causes)

    try:
        with staging_db.write_tx() as con:
            before = con.total_changes
            con.execute(_INSERT_STAGE, row)
            inserted = con.total_changes - before
            # 只有真正新插进去的行才写致因。重跑时 staging_accidents 被
            # UNIQUE(raw_id, split_index) 拦下，若这里照写，同一 stage_id
            # 会再挂一份完全相同的编码——暂存区就翻倍了。
            if inserted and causes:
                con.executemany(_INSERT_CAUSE, causes)
    except Exception as e:  # noqa: BLE001
        out.status, out.stage = "failed", "DB"
        out.error = f"{type(e).__name__}: {e}"[:500]
        logger.error("[%s] %s 落库失败: %s", task_id, rid, out.error)
        return out

    out.ok = True
    if not inserted:
        out.status = "duplicate"      # 已完成过，重跑时跳过，不算失败
    logger.info("[%s] %s 完成：%d 条编码，%d 字段，%d ms",
                task_id, rid, out.causes,
                len([v for v in row.items() if v[1] not in (None, "", 0)]), out.ms)
    return out


def _failed_stage(res) -> str:
    """失败发生在哪一段——从阶段记录里找第一条 ok=False 的。"""
    for r in res.records:
        if not r.ok:
            return r.stage
    return "UNKNOWN"


def execute_task(task_id: str) -> dict:
    """同步跑一个已建的任务。返回最终 stats。

    每完成一条就把 `stats` 写一次库，页面轮询因此能看到中间态，而不是
    等两小时拿一个结果（蓝图 02 §5.2「进度轮询」）。
    """
    task = staging_db.query_one(
        "SELECT * FROM extract_tasks WHERE task_id = ?", (task_id,))
    if task is None:
        raise KeyError(f"任务不存在: {task_id}")

    raw_ids: list[str] = json.loads(task["raw_ids"] or "[]")
    concurrency = max(1, int(task["concurrency"] or DEFAULT_CONCURRENCY))

    if task["status"] in ("pending", "running"):
        _update_task(task_id, status="running", started_at=_now())

    rows = _load_raws(raw_ids)
    errors: list[dict] = []
    by_type: dict[str, int] = {}
    done = failed = 0
    tok_in = tok_out = 0

    def record(out: ItemOutcome) -> None:
        nonlocal done, failed, tok_in, tok_out
        done += 1
        tok_in += out.tokens_in
        tok_out += out.tokens_out
        if out.text_type:
            by_type[out.text_type] = by_type.get(out.text_type, 0) + 1
        if not out.ok:
            failed += 1
            errors.append(out.as_dict())

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {}
            for raw in rows:
                # 排队期间被取消：连 LLM 都不要碰（取消是按条检查的，
                # 粒度比采集那边细——每条的 LLM 调用本身就是自然检查点）
                if _is_cancelled(task_id):
                    break
                futures[pool.submit(run_one, raw, task_id)] = raw["raw_id"]
            for fut in futures:
                try:
                    out = fut.result(timeout=ITEM_TIMEOUT_S)
                except Exception as e:  # noqa: BLE001 —— 超时或线程内异常
                    rid = futures[fut]
                    out = ItemOutcome(raw_id=rid, stage="TIMEOUT",
                                      error=f"{type(e).__name__}: {e}"[:500])
                    logger.warning("[%s] %s 超时/异常: %s", task_id, rid, out.error)
                record(out)
                _update_task(task_id, stats=json.dumps({
                    "done": done, "total": len(rows), "failed": failed,
                    "by_type": by_type, "errors": errors[-50:],
                }, ensure_ascii=False), actual_tokens_in=tok_in,
                    actual_tokens_out=tok_out)
    finally:
        with _CANCEL_LOCK:
            _CANCEL.discard(task_id)

    interrupted = staging_db.scalar(
        "SELECT status FROM extract_tasks WHERE task_id = ?", (task_id,))
    staged = staging_db.scalar(
        "SELECT COUNT(*) FROM staging_accidents WHERE task_id = ?", (task_id,))
    if interrupted == "cancelling":
        final = "cancelled"
    elif failed and failed == len(rows):
        final = "failed"        # 全军覆没才算失败，部分成功仍是 done
    else:
        final = "done"

    stats = {
        "done": done, "total": len(rows), "failed": failed,
        "staged": staged or 0, "by_type": by_type, "errors": errors[-50:],
        "tokens_in": tok_in, "tokens_out": tok_out,
        "cost_cny": round(_cost(tok_in, tok_out), 4),
    }
    _update_task(task_id, status=final, finished_at=_now(),
                 stats=json.dumps(stats, ensure_ascii=False),
                 actual_tokens_in=tok_in, actual_tokens_out=tok_out)
    return stats


def _load_raws(raw_ids: list[str]) -> list[dict]:
    """按传入顺序取原文。查不到的 id 直接跳过（可能已被清理）。"""
    if not raw_ids:
        return []
    marks = ",".join("?" * len(raw_ids))
    found = {r["raw_id"]: dict(r) for r in staging_db.query(
        f"SELECT * FROM raw_accidents WHERE raw_id IN ({marks})", raw_ids)}
    return [found[r] for r in raw_ids if r in found]


# --------------------------------------------------------------------------
# 任务级
# --------------------------------------------------------------------------


def _is_cancelled(task_id: str) -> bool:
    with _CANCEL_LOCK:
        return task_id in _CANCEL


def _update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with staging_db.write_tx() as con:
        con.execute(f"UPDATE extract_tasks SET {sets} WHERE task_id = :task_id",
                    {**fields, "task_id": task_id})


def _cost(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1_000_000 * PRICE_IN_CNY_PER_M
            + tokens_out / 1_000_000 * PRICE_OUT_CNY_PER_M)


def estimate(n_items: int, stages: Optional[list[str]] = None,
             per_item: Optional[int] = None) -> dict:
    """成本预估。**只算数，不调 LLM。**

    `per_item` 是给已完成批次反推平均值用的；不传就按蓝图 02 §3.4 的
    系数估。返回的 `note` 会原样显示在确认弹层上——金额是量级概念，
    不是结算依据，这句话必须跟着数字一起出现。
    """
    stages = stages or ["S1", "S2", "S3", "S4"]
    per = per_item if per_item else sum(EST_TOKENS.get(s, 0) for s in stages)
    # 输入:输出约 2:1（S3/S4 的 prompt 长、输出短）
    tokens_in = int(per * 2 / 3) * max(0, n_items)
    tokens_out = int(per / 3) * max(0, n_items)
    return {
        "items": n_items,
        "stages": stages,
        "est_tokens": tokens_in + tokens_out,
        "est_tokens_in": tokens_in,
        "est_tokens_out": tokens_out,
        "est_cost_cny": round(_cost(tokens_in, tokens_out), 2),
        "note": (f"按 {'+'.join(stages)} 约 {per:,} token/条 估算；"
                 f"实际用量在任务跑完后回填。金额是量级概念，非结算依据。"),
    }


def create_task(raw_ids: list[str], *, enable_split: bool = False,
                concurrency: int = DEFAULT_CONCURRENCY,
                stages: Optional[list[str]] = None) -> dict:
    """建任务行（`pending`），返回 `{task_id, estimate}`。**不执行、不调 LLM。**

    只收 `gate_verdict='confirmed'` 的记录：人工/自动闸门没放行的原文不该
    被提取。传进来的 id 里若有不合格的，这里直接拒绝而不是悄悄丢掉——
    静默过滤会让「我选了 10 条怎么只跑了 7 条」变成查不出的谜。
    """
    ids = [str(r).strip() for r in (raw_ids or []) if str(r).strip()]
    if not ids:
        raise ValueError("raw_ids 不能为空")

    marks = ",".join("?" * len(ids))
    rows = staging_db.query(
        f"SELECT raw_id, gate_verdict FROM raw_accidents WHERE raw_id IN ({marks})",
        ids)
    seen = {r["raw_id"]: r["gate_verdict"] for r in rows}
    missing = [i for i in ids if i not in seen]
    if missing:
        raise ValueError(f"以下 raw_id 不存在：{', '.join(missing[:5])}"
                         + ("…" if len(missing) > 5 else ""))
    unconfirmed = [i for i in ids if seen[i] != "confirmed"]
    if unconfirmed:
        raise ValueError(
            f"{len(unconfirmed)} 条未通过闸门（gate_verdict != confirmed），"
            f"如 {', '.join(unconfirmed[:3])}。请先在采集页标为相关。")

    stages = stages or ["S1", "S2", "S3", "S4"]
    est = estimate(len(ids), stages)
    task_id = _new_id()
    with staging_db.write_tx() as con:
        con.execute(
            """
            INSERT INTO extract_tasks
                (task_id, raw_ids, stages, enable_split, status, created_at,
                 concurrency, est_tokens, est_cost_cny, actual_tokens_in,
                 actual_tokens_out, stats)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, 0, '{}')
            """,
            (task_id, json.dumps(ids, ensure_ascii=False),
             json.dumps(stages), 1 if enable_split else 0, _now(),
             int(concurrency), est["est_tokens"], est["est_cost_cny"]),
        )
    return {"task_id": task_id, "estimate": est}


def start_task(task_id: str) -> bool:
    """确认后启动：状态转 `running` 并丢进线程池。返回是否启动成功。

    未 `start` 的任务永远停在 `pending`，**一毛钱不花**（蓝图 02 §8 验收第 9 条）。
    """
    row = staging_db.query_one(
        "SELECT status FROM extract_tasks WHERE task_id = ?", (task_id,))
    if row is None or row["status"] != "pending":
        return False
    _update_task(task_id, status="running", started_at=_now())
    _POOL.submit(execute_task, task_id)
    return True


def submit_task(raw_ids: list[str], *, enable_split: bool = False,
                concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    """建任务并立即执行（跳过确认）。**只给测试与脚本用**，API 走两步式。"""
    r = create_task(raw_ids, enable_split=enable_split, concurrency=concurrency)
    start_task(r["task_id"])
    return r


def cancel_task(task_id: str) -> bool:
    """请求取消。粒度到**条**——每条跑完检查一次标志位。

    比采集那边强：24 个采集适配器都是一次性 `return`，只能在源与源之间中断；
    这里每条本来就有一串 LLM 往返，天然是检查点。

    `pending` 的任务（建了但没 start）**立即**终结成 `cancelled`：它根本没进
    线程池，没有线程会在后面把 `cancelling` 收尾，写 `cancelling` 就等于把它
    永远卡在一个删不掉的状态上。
    """
    row = staging_db.query_one(
        "SELECT status FROM extract_tasks WHERE task_id = ?", (task_id,))
    if row is None or row["status"] in ("done", "failed", "cancelled"):
        return False
    if row["status"] == "pending":
        _update_task(task_id, status="cancelled", finished_at=_now())
        return True
    with _CANCEL_LOCK:
        _CANCEL.add(task_id)
    _update_task(task_id, status="cancelling")
    return True


def reconcile_stale_tasks() -> int:
    """把上次进程留下的 `pending` / `running` / `cancelling` 标为中断。

    与采集执行器同一套理由：进程没了，库里那几行还写着 `running`，页面会
    永远显示「进行中」。已落库的暂存条目不受影响——这正是不等整批才写库的
    意义（蓝图 02 §8 验收第 5 条）。
    """
    with staging_db.write_tx() as con:
        cur = con.execute(
            """
            UPDATE extract_tasks
               SET status = 'failed',
                   finished_at = ?,
                   error = COALESCE(error, '进程重启，任务中断（已完成条目不受影响，重跑会跳过）')
             WHERE status IN ('pending', 'running', 'cancelling')
            """,
            (_now(),),
        )
        return cur.rowcount


def get_task(task_id: str) -> Optional[dict]:
    task = staging_db.query_one(
        "SELECT * FROM extract_tasks WHERE task_id = ?", (task_id,))
    if task is None:
        return None
    task["raw_ids"] = json.loads(task["raw_ids"] or "[]")
    task["stages"] = json.loads(task["stages"] or "[]")
    task["stats"] = json.loads(task["stats"] or "{}")
    return task


def list_tasks(limit: int = 50) -> list[dict]:
    rows = staging_db.query(
        "SELECT * FROM extract_tasks ORDER BY created_at DESC LIMIT ?", (limit,))
    for r in rows:
        r["raw_ids"] = json.loads(r["raw_ids"] or "[]")
        r["stages"] = json.loads(r["stages"] or "[]")
        r["stats"] = json.loads(r["stats"] or "{}")
    return rows


def delete_task(task_id: str) -> bool:
    """删任务行。**不删已产出的暂存条目**——那些是阶段③的审核对象，
    删任务不该连带毁掉已经花过钱的产出。任务的删只在 `done|failed|cancelled`。
    """
    row = staging_db.query_one(
        "SELECT status FROM extract_tasks WHERE task_id = ?", (task_id,))
    if row is None or row["status"] not in ("done", "failed", "cancelled"):
        return False
    with staging_db.write_tx() as con:
        con.execute("UPDATE staging_accidents SET task_id = NULL "
                    "WHERE task_id = ?", (task_id,))
        con.execute("DELETE FROM extract_tasks WHERE task_id = ?", (task_id,))
    return True


# --------------------------------------------------------------------------
# 待提取队列
# --------------------------------------------------------------------------


def pending_raws(limit: int = 200, *, only_unstaged: bool = True) -> dict:
    """列出「已确认相关、还没提取过」的原文，供提取工作台的待办队列。

    **「已提取过」的判据是暂存区里有没有这个 `raw_id` 的行**，不是任务状态——
    任务可以删、可以重跑，暂存行才是事实。参数留 `only_unstaged=False`
    是为了让用户能重选一条已经提取过的记录来重跑。
    """
    where = ["r.gate_verdict = 'confirmed'"]
    if only_unstaged:
        where.append("NOT EXISTS (SELECT 1 FROM staging_accidents s "
                     "WHERE s.raw_id = r.raw_id)")
    sql = f"""
        SELECT r.raw_id, r.title, r.source_key, r.publish_date, r.fetch_time,
               r.ext_deaths, r.ext_injuries, r.ext_accident_type,
               (SELECT COUNT(*) FROM raw_accident_assets a
                 WHERE a.raw_id = r.raw_id) AS assets,
               LENGTH(COALESCE(r.content, '')) AS content_len
          FROM raw_accidents r
         WHERE {' AND '.join(where)}
         ORDER BY r.fetch_time DESC, r.raw_id
         LIMIT ?
    """
    rows = staging_db.query(sql, (int(limit),))
    total = staging_db.scalar(
        f"SELECT COUNT(*) FROM raw_accidents r WHERE {' AND '.join(where)}")
    return {"items": rows, "total": total or 0}
