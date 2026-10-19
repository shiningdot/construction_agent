"""删除 caiems_v2.db 中 raw_text 逐字相同的重复事故。

背景（实测，2026-10-08）
-----------------------
Web 列表页出现两对几乎相同的事故相邻显示（成都锦江区沙井脚手架、合肥肥西
习友路土方坍塌）。根因是主库写入路径 `scripts/run_batch_pipeline.py` 的主键

    id = md5(title|date|city)[:12]

而 `title` 由 step1 的 LLM 每次现场提取、措辞会变，于是**同一份原文**跨批次
重跑就落成两行：两道闸门（`INSERT OR REPLACE` 靠主键、`INSERT OR IGNORE`
靠 `UNIQUE(accident_id,url)`）的种子都含这个 id，同时失效。详见该脚本 docstring。

判据：`raw_text` **逐字相同**（不做归一化）。这是「同一份原文被入库两次」的
铁证，误删风险最低。同组内 `date` / `source_name` 任一不一致即**中止并转人工
复核**——那意味着两条可能只是巧合相似，不能当重复删。

保留策略（确定性排序）：编码（caics_causes 行数）最多 → `created_at` 最早
→ `id` 字典序最小。编码最多的保留，是「哪一条承载的信息更全」的自然口径。

级联：`accidents` 的 6 张子表全写了 `ON DELETE CASCADE`，但 SQLite 的外键是
**连接级开关、默认 OFF**。本脚本显式 `PRAGMA foreign_keys = ON`，否则删父行
会留下一堆孤儿行（child.accident_id 指向已不存在的父）。

用法
----
    python3 scripts/dedup_accidents.py --db <path> --dry-run   # 只预览，不写任何东西
    python3 scripts/dedup_accidents.py --db <path>             # 自动备份 + 删除
    python3 scripts/dedup_accidents.py                         # --db 缺省用 web 层解析的库路径
    python3 scripts/dedup_accidents.py --db <path> --no-backup # 跳过备份（不推荐）

备份、被删数据存档、宽判据候选清单都落在 `<库目录>/db_backups/`。备份用
`safe_backup`（backup API），不用 `shutil.copy2`——WAL 库上文件级复制会静默
丢数据（见 `construction_agent/database/backup.py` docstring）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from construction_agent.database.backup import safe_backup  # noqa: E402
from construction_agent.web.db import resolve_db_path  # noqa: E402

# accidents 下的全部子表（关联列一律是 accident_id），全部 ON DELETE CASCADE
SUBTABLES = (
    "caics_causes",
    "search_logs",
    "image_analyses",
    "processing_log",
    "source_urls",
    "image_files",
)


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def find_duplicate_groups(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """按 raw_text 分组，只返回 size>1 的组。

    返回 {raw_text: [ {id,date,source_name,title,created_at}, ... ]}。
    空/缺失的 raw_text 不参与（无法比对），且实测全库为 0 条。
    """
    rows = con.execute(
        "SELECT id, raw_text, date, source_name, title, created_at FROM accidents "
        "WHERE raw_text IS NOT NULL AND raw_text != ''"
    ).fetchall()
    groups: dict[str, list[dict]] = defaultdict(list)
    for rid, raw_text, date, source_name, title, created_at in rows:
        groups[raw_text].append(
            {
                "id": rid,
                "date": date or "",
                "source_name": source_name or "",
                "title": title or "",
                "created_at": created_at or "",
            }
        )
    return {rt: g for rt, g in groups.items() if len(g) > 1}


def code_counts(con: sqlite3.Connection, ids: list[str]) -> dict[str, int]:
    """每个 accident_id 的 caics_causes 行数（= 编码数）。"""
    if not ids:
        return {}
    rows = con.execute(
        f"SELECT accident_id, COUNT(*) FROM caics_causes "
        f"WHERE accident_id IN ({_placeholders(len(ids))}) GROUP BY accident_id",
        ids,
    ).fetchall()
    return dict(rows)


def pick_keeper(group: list[dict], counts: dict[str, int]) -> dict:
    """保留编码最多的；并列取 created_at 最早；再并列取 id 字典序最小。"""
    return sorted(
        group,
        key=lambda r: (-counts.get(r["id"], 0), r["created_at"], r["id"]),
    )[0]


def export_removed(con: sqlite3.Connection, ids: list[str]) -> dict:
    """导出被删记录的完整数据（主表行 + 6 张子表关联行），供日后恢复或复核。"""
    out: dict = {"deleted_ids": ids, "accidents": [], "children": {}}
    if not ids:
        return out
    ph = _placeholders(len(ids))

    cur = con.execute(f"SELECT * FROM accidents WHERE id IN ({ph})", ids)
    cols = [d[0] for d in cur.description]
    out["accidents"] = [dict(zip(cols, r)) for r in cur.fetchall()]

    for table in SUBTABLES:
        cur = con.execute(f"SELECT * FROM {table} WHERE accident_id IN ({ph})", ids)
        cols = [d[0] for d in cur.description]
        out["children"][table] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return out


def find_wide_candidates(con: sqlite3.Connection) -> list[dict]:
    """宽判据候选：同 (date, province, district, accident_type) 但 raw_text 不全等。

    这些**不删**（误删风险高：同城同日的两起同类事故本就可能），只导出清单，
    供后续人工裁决。四要素需齐全才参与，否则键太松。
    """
    rows = con.execute(
        "SELECT id, raw_text, date, province, district, accident_type, title, city "
        "FROM accidents"
    ).fetchall()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for rid, raw_text, date, province, district, atype, title, city in rows:
        key = (date, province, district, atype)
        if all(key):
            groups[key].append(
                {
                    "id": rid,
                    "raw_hash": hashlib.md5((raw_text or "").encode("utf-8")).hexdigest(),
                    "raw_text_len": len(raw_text or ""),
                    "title": title or "",
                    "city": city or "",
                }
            )

    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # 组内 raw_text 全等的已被铁证判据覆盖（会删）；这里只留「未确认」的
        if len({m["raw_hash"] for m in members}) < 2:
            continue
        out.append(
            {
                "key": {
                    "date": key[0],
                    "province": key[1],
                    "district": key[2],
                    "accident_type": key[3],
                },
                "members": members,
            }
        )
    return out


def orphan_counts(con: sqlite3.Connection) -> dict[str, int]:
    """每张子表里 accident_id 指向已不存在的父行的行数，应为 0。"""
    return {
        t: con.execute(
            f"SELECT COUNT(*) FROM {t} "
            f"WHERE accident_id NOT IN (SELECT id FROM accidents)"
        ).fetchone()[0]
        for t in SUBTABLES
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="删除 raw_text 完全相同的重复事故")
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
    con.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
    try:
        for table in ("accidents", *SUBTABLES):
            hit = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not hit:
                print(f"✗ 表不存在：{table}", file=sys.stderr)
                return 2

        n_acc = _count(con, "accidents")
        n_cause = _count(con, "caics_causes")

        groups = find_duplicate_groups(con)
        if not groups:
            print("✓ 无 raw_text 完全相同的重复组，无需操作")
            return 0

        all_ids = [r["id"] for g in groups.values() for r in g]
        counts = code_counts(con, all_ids)

        to_delete: list[str] = []
        total_codes_lost = 0
        print(f"\n发现 {len(groups)} 组重复：涉及 {len(all_ids)} 行，"
              f"应冗余 {len(all_ids) - len(groups)} 行")

        for i, (raw_text, group) in enumerate(
            sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[1][0]["id"])), 1
        ):
            dates = {r["date"] for r in group}
            names = {r["source_name"] for r in group}
            if len(dates) > 1 or len(names) > 1:
                print(
                    f"\n✗ 第 {i} 组一致性断言失败：date={dates} source_name={names}\n"
                    f"  raw_text 前 80 字：{raw_text[:80]!r}\n"
                    "  同组 raw_text 相同但 date/source_name 不同，可能不是同一事故，"
                    "请人工复核后再决定。本次未做任何写操作。",
                    file=sys.stderr,
                )
                return 3

            keeper = pick_keeper(group, counts)
            victims = [r for r in group if r["id"] != keeper["id"]]
            print(f"\n[{i}] {len(group)} 条 → 保留 1 删 {len(victims)}"
                  f"  （原文 {len(raw_text)} 字，date={keeper['date']}，"
                  f"source={keeper['source_name']}）")
            print(f"    保留  {keeper['id']}  编码{counts.get(keeper['id'], 0):>3}  "
                  f"{keeper['created_at']}  {keeper['title'][:44]}")
            for v in victims:
                print(f"    删除  {v['id']}  编码{counts.get(v['id'], 0):>3}  "
                      f"{v['created_at']}  {v['title'][:44]}")
                to_delete.append(v["id"])
                total_codes_lost += counts.get(v["id"], 0)

        print(f"\n小结：删 {len(to_delete)} 行事故，带走 {total_codes_lost} 条编码")
        print(f"      accidents {n_acc} → {n_acc - len(to_delete)}；"
              f"caics_causes {n_cause} → {n_cause - total_codes_lost}")

        if args.dry_run:
            print("\n[dry-run] 未执行任何写操作")
            return 0

        # ── 备份 ──
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # 带微秒，防同秒撞名
        backup_dir = db_path.parent / "db_backups"
        if not args.no_backup:
            backup = backup_dir / f"{db_path.name}.bak.before_dedup_{ts}"
            safe_backup(db_path, backup)
            print(f"\n已备份 → {backup} ({backup.stat().st_size / 1024 / 1024:.1f} MB)")

        # ── 存被删数据（可回滚）──
        archive_path = backup_dir / f"dedup_removed_{ts}.json"
        archive_path.write_text(
            json.dumps(export_removed(con, to_delete), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"被删数据存档 → {archive_path}")

        # ── 宽判据候选清单（不删，供人工裁决）──
        wide = find_wide_candidates(con)
        wide_path = backup_dir / f"dedup_candidates_wide_{ts}.json"
        wide_path.write_text(
            json.dumps(wide, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        n_wide = sum(len(w["members"]) for w in wide)
        print(f"宽判据候选清单 → {wide_path}  （{len(wide)} 组 / {n_wide} 行，未删）")

        # ── 删除（单事务，CASCADE 清子表）──
        con.commit()  # 收掉前面 SELECT 可能留下的隐式事务
        con.execute("BEGIN")
        removed = con.execute(
            f"DELETE FROM accidents WHERE id IN ({_placeholders(len(to_delete))})",
            to_delete,
        ).rowcount
        con.execute("COMMIT")

        # ── 复查 ──
        after_acc = _count(con, "accidents")
        after_cause = _count(con, "caics_causes")
        orphans = orphan_counts(con)
        print(f"\n删除 {removed} 行")
        print(f"复查：accidents {after_acc}（期望 {n_acc - len(to_delete)}）；"
              f"caics_causes {after_cause}（期望 {n_cause - total_codes_lost}）")
        print(f"      孤儿子表行：{orphans}")

        ok = (
            removed == len(to_delete)
            and after_acc == n_acc - len(to_delete)
            and after_cause == n_cause - total_codes_lost
            and all(v == 0 for v in orphans.values())
        )
        if not ok:
            print("\n✗ 复查未通过，请用备份回滚核对", file=sys.stderr)
            return 1
        print("\n✓ 完成")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
