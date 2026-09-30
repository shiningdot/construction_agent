"""补齐 caiems_v2.db 的索引。

背景（实测，2026-09-30）
-----------------------
`scripts/create_db_v2.py` 声明了 7 个索引（:66-70 五个、:84-85 两个），
但真实库里**一个都没有**。根因是该脚本 :165-167 有

    if os.path.exists(db_path): os.remove(db_path)

的破坏性行为，从未在真实库上执行过——真实库由另一个版本的表结构所建。
同文件还留下更硬的漂移证据：:10 注释写「事故主表 (55列)」，SCHEMA 实际 52 列，
真实库 48 列（少 lighting / day_of_week / season / time_period / is_night_shift
五列，多 work_task_enum）。**脚本、注释、真实库三者互不相同。**

本脚本把这 7 个索引补齐，并追加展示层高频筛选所需的 2 个。

用法
----
    python3 scripts/add_indexes.py --dry-run     # 只预览，不写任何东西
    python3 scripts/add_indexes.py               # 自动备份 + 建索引
    python3 scripts/add_indexes.py --db <path>   # 指定库文件
    python3 scripts/add_indexes.py --no-backup   # 跳过备份（不推荐）

索引是纯新增、可 `DROP INDEX` 回退，但执行前仍会自动复制一份带时间戳的备份。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 复用 web 层的库路径解析，避免又一处「路径漂移」
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from construction_agent.database.backup import safe_backup  # noqa: E402
from construction_agent.web.db import resolve_db_path  # noqa: E402

# (索引名, 表名, 列名) —— 命名沿用 create_db_v2.py 的既有风格，不另起一套
INDEXES: list[tuple[str, str, str]] = [
    # --- create_db_v2.py:66-70 声明过的 5 个 ---
    ("idx_acc_type", "accidents", "accident_type"),
    ("idx_acc_severity", "accidents", "severity"),
    ("idx_acc_date", "accidents", "date"),
    ("idx_acc_city", "accidents", "city"),
    ("idx_acc_deaths", "accidents", "deaths"),
    # --- create_db_v2.py:84-85 声明过的 2 个 ---
    ("idx_cause_acc", "caics_causes", "accident_id"),
    ("idx_cause_code", "caics_causes", "code"),
    # --- 展示层需要、脚本未声明的 2 个（蓝图 00 第 3.3 节）---
    ("idx_acc_province", "accidents", "province"),
    ("idx_cause_quality", "caics_causes", "evidence_quality"),
]

TABLES = ("accidents", "caics_causes")


def existing_indexes(con: sqlite3.Connection) -> dict[str, set[str]]:
    """返回 {表名: {索引名}}。"""
    result: dict[str, set[str]] = {}
    for table in TABLES:
        rows = con.execute(f"PRAGMA index_list({table})").fetchall()
        # PRAGMA index_list 的列序：seq, name, unique, origin, partial
        result[table] = {r[1] for r in rows}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐 caiems_v2.db 的索引")
    parser.add_argument("--db", help="库文件路径（默认按 CAIEMS_DB > settings.DATA_DIR 解析）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写库、不备份")
    parser.add_argument("--no-backup", action="store_true", help="跳过备份（不推荐）")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve() if args.db else resolve_db_path()
    print(f"目标库：{db_path}")
    if not db_path.exists():
        print("✗ 库文件不存在", file=sys.stderr)
        return 2

    con = sqlite3.connect(db_path)
    try:
        # 前置检查：确认表都在，避免建到一半失败
        for table in TABLES:
            hit = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not hit:
                print(f"✗ 表不存在：{table}", file=sys.stderr)
                return 2

        before = existing_indexes(con)
        todo = [
            (name, table, col)
            for (name, table, col) in INDEXES
            if name not in before[table]
        ]

        print(f"\n现有索引：accidents {sorted(before['accidents'])}")
        print(f"           caics_causes {sorted(before['caics_causes'])}")

        if not todo:
            print("\n✓ 9 个索引已全部存在，无需操作")
            return 0

        print(f"\n待创建 {len(todo)} 个：")
        for name, table, col in todo:
            print(f"  + {name:<20} ON {table}({col})")

        if args.dry_run:
            print("\n[dry-run] 未执行任何写操作")
            return 0

        if not args.no_backup:
            # 带微秒，避免同一秒内连跑两次撞名（safe_backup 拒绝覆盖已有备份）
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = db_path.parent / f"{db_path.name}.bak.before_indexes_{ts}"
            # 不用 shutil.copy2：库若处于 WAL，主文件在 checkpoint 之前是滞后的，
            # 文件级复制会静默丢数据（实测 51 行只备到 1 行）
            safe_backup(db_path, backup)
            size_mb = backup.stat().st_size / 1024 / 1024
            print(f"\n已备份 → {backup}  ({size_mb:.1f} MB)")

        con.execute("BEGIN")
        for name, table, col in todo:
            con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({col})")
        con.execute("COMMIT")

        after = existing_indexes(con)
        print(f"\n建索引后：accidents {sorted(after['accidents'])}")
        print(f"          caics_causes {sorted(after['caics_causes'])}")

        missing = [
            name
            for (name, table, _) in INDEXES
            if name not in after[table]
        ]
        if missing:
            print(f"\n✗ 仍有索引未建成：{missing}", file=sys.stderr)
            return 1

        print(f"\n✓ 9 个索引全部就绪")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
