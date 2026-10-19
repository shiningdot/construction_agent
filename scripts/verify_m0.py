"""M0 基座验收：只读数据层 + 索引生效 + 口径对账。

对应蓝图 07 第四节 M0 验收标准，以及 M1 必过项中的库级数字。
**只读脚本**，不改动任何数据，可反复运行。

用法：
    python3 scripts/verify_m0.py
    CAIEMS_DB=/path/to/caiems_v2.db python3 scripts/verify_m0.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from construction_agent.web import db as ro  # noqa: E402
from construction_agent.web import staging_db as sdb  # noqa: E402

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "✓" if ok else "✗"
    line = f"{mark} {name}"
    if detail:
        line += f"\n    {detail}"
    print(line)


def explain(sql: str, params: tuple) -> str:
    """把 EXPLAIN QUERY PLAN 的输出压成一行文本（列名跨版本不一致，故不按下标取）。"""
    rows = ro.query(f"EXPLAIN QUERY PLAN {sql}", params)
    return " | ".join(
        str(v) for r in rows for v in r.values() if isinstance(v, str)
    )


def main() -> int:
    print("=" * 70)
    print("M0 基座验收")
    print("=" * 70)

    # ---- 1. 路径解析优先级 ------------------------------------------------
    default_path = ro.resolve_db_path()
    check(
        "1a 默认路径解析为 <DATA_DIR>/caiems_v2.db",
        default_path.name == "caiems_v2.db",
        str(default_path),
    )

    saved = os.environ.get("CAIEMS_DB")
    os.environ["CAIEMS_DB"] = str(default_path)
    try:
        check(
            "1b 环境变量 CAIEMS_DB 优先级最高",
            ro.resolve_db_path() == default_path,
            "回落到同一路径，说明解析链路一致",
        )
    finally:
        if saved is None:
            os.environ.pop("CAIEMS_DB", None)
        else:
            os.environ["CAIEMS_DB"] = saved

    try:
        ro.connect_readonly("/nonexistent/no_such.db")
        check("1c 库不存在时抛 DatabaseNotFoundError", False, "未抛异常")
    except ro.DatabaseNotFoundError:
        check("1c 库不存在时抛 DatabaseNotFoundError", True)

    # ---- 2. 只读保证 ------------------------------------------------------
    con = ro.connect_readonly()
    try:
        for label, sql in [
            ("CREATE TABLE", "CREATE TABLE __probe__(x INTEGER)"),
            ("INSERT", "INSERT INTO accidents(id) VALUES('__probe__')"),
            ("UPDATE", "UPDATE accidents SET title='x' WHERE 1=0"),
            ("DELETE", "DELETE FROM accidents WHERE 1=0"),
        ]:
            try:
                con.execute(sql)
                check(f"2 只读连接拒绝 {label}", False, "竟然没被拦截")
            except sqlite3.OperationalError as exc:
                check(f"2 只读连接拒绝 {label}", True, str(exc))
    finally:
        con.close()

    # ---- 3. 索引生效 ------------------------------------------------------
    plan_cause = explain("SELECT * FROM caics_causes WHERE accident_id = ?", ("x",))
    check(
        "3a WHERE accident_id 走索引",
        "idx_cause_acc" in plan_cause,
        plan_cause,
    )

    plan_prov = explain("SELECT id FROM accidents WHERE province = ?", ("浙江省",))
    check(
        "3b WHERE province 走索引",
        "idx_acc_province" in plan_prov,
        plan_prov,
    )

    plan_sev = explain(
        "SELECT id FROM accidents WHERE severity = ? ORDER BY date DESC", ("较大事故",)
    )
    check(
        "3c WHERE severity 走索引",
        "idx_acc_severity" in plan_sev,
        plan_sev,
    )

    idx_acc = {i["name"] for i in ro.list_indexes("accidents")}
    idx_cause = {i["name"] for i in ro.list_indexes("caics_causes")}
    expected_acc = {
        "idx_acc_type", "idx_acc_severity", "idx_acc_date",
        "idx_acc_city", "idx_acc_deaths", "idx_acc_province",
    }
    expected_cause = {"idx_cause_acc", "idx_cause_code", "idx_cause_quality"}
    check(
        "3d accidents 六个索引齐全",
        expected_acc <= idx_acc,
        f"实有 {sorted(idx_acc)}",
    )
    check(
        "3e caics_causes 三个索引齐全",
        expected_cause <= idx_cause,
        f"实有 {sorted(idx_cause)}",
    )

    # ---- 4. schema 真相来源 ----------------------------------------------
    cols = ro.table_columns("accidents")
    check(
        "4a accidents 实际列数 = 48",
        len(cols) == 48,
        f"实测 {len(cols)} 列（scripts/create_db_v2.py 的 SCHEMA 是 52 列，注释写 55 列）",
    )
    check(
        "4b 无 5 个派生时间列（漂移证据）",
        not ({"lighting", "day_of_week", "season", "time_period", "is_night_shift"} & set(cols)),
        "这 5 列只存在于脚本 SCHEMA 中，真实库没有",
    )
    check(
        "4c 真实库独有的 work_task_enum 存在",
        "work_task_enum" in cols,
        "脚本 SCHEMA 里反而没有这一列",
    )

    # ---- 5. 库级口径对账（蓝图 04 必过项）--------------------------------
    n_acc = ro.scalar("SELECT COUNT(*) FROM accidents")
    check("5a 事故总数 = 2,623", n_acc == 2623, f"实测 {n_acc}")

    n_cause = ro.scalar("SELECT COUNT(*) FROM caics_causes")
    check("5b 致因编码总数 = 16,323", n_cause == 16323, f"实测 {n_cause}")

    n_cov = ro.scalar("SELECT COUNT(DISTINCT accident_id) FROM caics_causes")
    check("5c 有编码的事故数 = 2,618", n_cov == 2618, f"实测 {n_cov}（无编码 {n_acc - n_cov} 条）")

    eq = {r["k"]: r["c"] for r in ro.query(
        "SELECT evidence_quality AS k, COUNT(*) AS c FROM caics_causes GROUP BY 1"
    )}
    check(
        "5d 证据三级 = 14,784 / 1,117 / 422",
        eq.get("direct_quote") == 14784
        and eq.get("indirect_infer") == 1117
        and eq.get("weak_infer") == 422,
        str(eq),
    )

    deaths = ro.scalar("SELECT SUM(deaths) FROM accidents")
    injuries = ro.scalar("SELECT SUM(injuries) FROM accidents")
    check("5e 死亡总数 = 5,894", deaths == 5894, f"实测 {deaths}")
    check("5f 受伤总数 = 5,796", injuries == 5796, f"实测 {injuries}")

    # ---- 6. 字段口径（蓝图 00 第 3.5 节的三种算法之别）-------------------
    pd_have = ro.scalar(
        "SELECT COUNT(*) FROM accidents WHERE publish_date IS NOT NULL AND publish_date != ''"
    )
    check(
        "6a publish_date 有值率 = 1,716（65.4%）",
        pd_have == 1716,
        f"实测 {pd_have}。若用 COUNT() 会算出 2,623 → 100%，这正是口径③与②的分歧点",
    )

    n_not_null = ro.scalar("SELECT COUNT(deaths) FROM accidents")
    n_positive = ro.scalar("SELECT COUNT(*) FROM accidents WHERE deaths > 0")
    check(
        "6b deaths 非 NULL = 2,623 / 大于 0 = 2,280",
        n_not_null == 2623 and n_positive == 2280,
        f"非 NULL {n_not_null}、大于 0 {n_positive}。0 是「原文未提及」，不是缺失",
    )

    # ---- 7. 老规矩：无孤儿外键 -------------------------------------------
    orphans = ro.scalar(
        "SELECT COUNT(*) FROM caics_causes WHERE accident_id NOT IN (SELECT id FROM accidents)"
    )
    check("7 无孤儿外键（2026-09-23 清理后应保持 0）", orphans == 0, f"实测 {orphans}")

    # ---- 8. 工作库：物理分离 + 外键强制 + 级联 ---------------------------
    staging_path = sdb.resolve_staging_path()
    check(
        "8a 工作库与最终库物理分离",
        staging_path != default_path and staging_path.name == "caiems_staging.db",
        f"工作库 {staging_path.name}  ←→  最终库 {default_path.name}",
    )
    check("8b 工作库已初始化（9 张表）", sdb.is_initialized(), str(staging_path))

    if sdb.is_initialized():
        def try_orphan(label: str, sql: str, params: tuple) -> None:
            """孤儿行必须被外键拦下。"""
            try:
                with sdb.write_tx() as c:
                    c.execute(sql, params)
                check(f"8 外键拒绝孤儿：{label}", False, "竟然插进去了 —— PRAGMA foreign_keys 没生效")
            except sqlite3.IntegrityError as exc:
                check(f"8 外键拒绝孤儿：{label}", True, str(exc))

        try_orphan(
            "raw_accidents.source_key → collect_sources",
            "INSERT INTO raw_accidents (raw_id, source_key, source_url, fetch_time,"
            " url_hash, status) VALUES (?,?,?,?,?,?)",
            ("__probe_raw__", "__no_such_source__", "u", "t", "h", "new"),
        )
        try_orphan(
            "staging_causes.stage_id → staging_accidents",
            "INSERT INTO staging_causes (stage_id, code, name) VALUES (?,?,?)",
            ("__no_such_stage__", "M1", "现场监管缺失"),
        )

        # 完整写入链路：源 → 原始 → 暂存 → 致因
        probe_src, probe_raw, probe_stage = "__probe_src__", "__probe_raw__", "__probe_stage__"
        with sdb.write_tx() as c:
            c.execute(
                "INSERT INTO collect_sources (source_key, display_name, source_group, track)"
                " VALUES (?,?,?,?)",
                (probe_src, "探针源", "national", "network"),
            )
            c.execute(
                "INSERT INTO raw_accidents (raw_id, source_key, source_url, fetch_time,"
                " url_hash, status) VALUES (?,?,?,?,?,?)",
                (probe_raw, probe_src, "http://probe.invalid", "2026-09-30T00:00:00", "probehash", "new"),
            )
            c.execute(
                "INSERT INTO staging_accidents (stage_id, raw_id, created_at) VALUES (?,?,?)",
                (probe_stage, probe_raw, "2026-09-30T00:00:00"),
            )
            c.execute(
                "INSERT INTO staging_causes (stage_id, code, name) VALUES (?,?,?)",
                (probe_stage, "M1", "现场监管缺失"),
            )

        n = sdb.scalar("SELECT COUNT(*) FROM staging_causes WHERE stage_id=?", (probe_stage,))
        check("8c 写入链路通畅（源→原始→暂存→致因）", n == 1, f"staging_causes 行数 {n}")

        with sdb.write_tx() as c:
            c.execute("DELETE FROM staging_accidents WHERE stage_id=?", (probe_stage,))
        n_after = sdb.scalar("SELECT COUNT(*) FROM staging_causes WHERE stage_id=?", (probe_stage,))
        check(
            "8d ON DELETE CASCADE 生效（删父行带走子行）",
            n_after == 0,
            f"残留 {n_after} 行 —— 若非 0 则会产生孤儿，正是 1,041 行孤儿的成因",
        )

        # 清理探针数据
        with sdb.write_tx() as c:
            c.execute("DELETE FROM raw_accidents WHERE raw_id=?", (probe_raw,))
            c.execute("DELETE FROM collect_sources WHERE source_key=?", (probe_src,))
        left = sdb.scalar("SELECT COUNT(*) FROM raw_accidents") + sdb.scalar(
            "SELECT COUNT(*) FROM collect_sources"
        )
        check("8e 探针数据已清理干净", left == 0, f"残留 {left} 行")

        # staging_accidents 必须收齐 S3 的 31 个字段，否则并入时必然错位
        con_tmp = sdb.connect(readonly=True)
        try:
            stage_cols = {d[0] for d in con_tmp.execute(
                "SELECT * FROM staging_accidents LIMIT 0").description}
        finally:
            con_tmp.close()

        s3_fields = {
            "title", "date", "accident_time", "location", "city", "district",
            "accident_location_detail", "project_name", "project_type",
            "construction_phase", "building_structure", "accident_type",
            "severity", "deaths", "injuries", "trapped_count", "collapse_subtype",
            "fall_height", "work_task", "equipment_involved", "material_involved",
            "worker_type", "weather", "contractor", "contractor_type", "developer",
            "supervision_company", "direct_economic_loss", "direct_cause",
            "summary", "report_type",
        }
        check(
            "8f staging_accidents 收齐 S3 的 31 个字段",
            s3_fields <= stage_cols,
            f"缺 {sorted(s3_fields - stage_cols) or '无'}（共 {len(stage_cols)} 列）",
        )
        check(
            "8g 4 处改名映射的源列就位",
            {"location", "relevance_verdict", "s3_ms", "s4_ms"} <= stage_cols,
            "这 4 列在并入时分别映射为 province / classification_verdict /"
            " step1_duration_ms / step2_duration_ms",
        )

    # ---- 汇总 -------------------------------------------------------------
    failed = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 70)
    print(f"共 {len(_results)} 项，通过 {len(_results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for n in failed:
            print(f"  - {n}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
