"""创建工作库 `caiems_staging.db`（幂等）。

八张表（定义在 `construction_agent/web/staging_schema.py`，单一出口）：

    阶段①  collect_sources / collect_tasks / raw_accidents / raw_accident_assets
    阶段②③ extract_tasks / staging_accidents / staging_causes
    阶段④  image_manifest
    元数据  staging_metadata

与最终库 `caiems_v2.db` **物理分离**——展示层只有最终库的只读句柄，
不可能因代码缺陷污染正式数据；工作库可整库丢弃重建。

用法：
    python3 scripts/create_staging_db.py              # 建表（幂等）
    python3 scripts/create_staging_db.py --info       # 只看现状
    python3 scripts/create_staging_db.py --drop       # 删库重建（会二次确认）
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from construction_agent.web import db as rodb  # noqa: E402
from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.staging_schema import TABLES  # noqa: E402


def print_info(path: Path) -> None:
    print(f"工作库路径：{path}")
    if not path.exists():
        print("  状态：尚未创建")
        return

    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  大小：{size_mb:.1f} MB")

    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        ver = con.execute(
            "SELECT value FROM staging_metadata WHERE key='schema_version'"
        ).fetchone()
        print(f"  schema 版本：{ver[0] if ver else '(未记录)'}")
        print("\n  表                        行数    是否有")
        for t in TABLES:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if row else 0
            print(f"  {t:<24}{n:>7}    {'✓' if row else '✗'}")
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="创建工作库 caiems_staging.db")
    parser.add_argument("--db", help="库文件路径（默认 CAIEMS_STAGING_DB > DATA_DIR）")
    parser.add_argument("--info", action="store_true", help="只显示现状，不建表")
    parser.add_argument("--drop", action="store_true", help="删库后重建（危险，需确认）")
    args = parser.parse_args()

    path = (
        Path(args.db).expanduser().resolve()
        if args.db
        else sdb.resolve_staging_path()
    )
    print(f"目标工作库：{path}")
    print(f"（最终库为只读，位于 {rodb.resolve_db_path()}）\n")

    if args.info:
        print_info(path)
        return 0

    if args.drop:
        if not path.exists():
            print("库不存在，无需删除")
        else:
            answer = input(f"⚠️  将删除 {path} 及其全部数据，输入 YES 确认：").strip()
            if answer != "YES":
                print("已取消")
                return 1
            # WAL 模式的 -wal / -shm 旁文件一并清掉，否则重建后可能读出错乱状态
            for suffix in ("", "-wal", "-shm"):
                side = Path(str(path) + suffix)
                if side.exists():
                    side.unlink()
                    print(f"已删除 {side}")

    result = sdb.init_db(path)
    print(f"✓ 建表完成，schema 版本 {result['schema_version']}")
    print()
    print_info(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
