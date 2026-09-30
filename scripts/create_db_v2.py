"""创建 / 补齐 caiems_v2 数据库。

⚠️ 这个脚本曾经与真实库严重漂移（实测 2026-09-30）
--------------------------------------------------
三处事实互不相同：

| | 列数 | 表数 |
|---|---|---|
| 原脚本注释 | 55 | 7 |
| 原脚本 SCHEMA | 52 | 7 |
| **真实库** | **48** | **9** |

差异明细：SCHEMA 多了 `lighting` / `day_of_week` / `season` / `time_period` /
`is_night_shift` 五个派生时间列，少了 `work_task_enum` 列；并且**整个少了
`enum_definitions` 与 `image_files` 两张表**。

更糟的是原脚本 :165-167 有

    if os.path.exists(db_path): os.remove(db_path)

所以它从未在真实库上跑过——真实库是另一条途径建的，脚本里声明的 7 个索引
一个都没生效，所有查询都在全表扫描。

现在的行为
----------
**以真实库为准**，SCHEMA 逐列对齐实测到的 48 列 9 表。建表语句全部是
`CREATE TABLE IF NOT EXISTS`，天然幂等：

    python3 scripts/create_db_v2.py --dry-run     # 只报告差异，不写
    python3 scripts/create_db_v2.py               # 补齐缺失的表与索引，不动数据
    python3 scripts/create_db_v2.py --rebuild     # 先备份再整库重建（会删数据）

默认**绝不删库**。要重建必须显式给 `--rebuild`，且执行前自动带时间戳备份。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 表定义以真实库（data/caiems_v2.db）为准逐列核对得来，不要凭记忆改。
# `accidents` 48 列 —— 派生时间字段（lighting/day_of_week/season/time_period/
# is_night_shift）当年设计过但从未落入真实库，不再往回加；`work_task_enum`
# 是 1.0 残留列，保留结构但新管道恒写 NULL（见 docs/web/00 第 3.7 节）。
SCHEMA = """
-- 表1: 事故主表 (48列)
CREATE TABLE IF NOT EXISTS accidents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    crawl_date TEXT,
    publish_date TEXT,
    date TEXT,
    accident_time TEXT,
    province TEXT,
    city TEXT,
    district TEXT,
    accident_location_detail TEXT,
    weather TEXT,
    project_name TEXT,
    project_type TEXT NOT NULL,
    building_structure TEXT,
    construction_phase TEXT NOT NULL DEFAULT '未知',
    work_task TEXT,
    developer TEXT,
    contractor TEXT,
    contractor_type TEXT,
    supervision_company TEXT,
    accident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    deaths INTEGER NOT NULL DEFAULT 0,
    injuries INTEGER NOT NULL DEFAULT 0,
    trapped_count INTEGER NOT NULL DEFAULT 0,
    direct_economic_loss REAL,
    collapse_subtype TEXT,
    fall_height REAL,
    equipment_involved TEXT,
    material_involved TEXT,
    worker_type TEXT,
    direct_cause TEXT,
    report_type TEXT NOT NULL,
    text_type TEXT NOT NULL,
    classification_verdict TEXT NOT NULL,
    classification_confidence TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 1,
    summary TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    search_enriched_text TEXT,
    search_performed INTEGER NOT NULL DEFAULT 0,
    pipeline_version TEXT NOT NULL,
    step1_duration_ms INTEGER,
    step2_duration_ms INTEGER,
    created_at TEXT NOT NULL,
    work_task_enum TEXT
);

CREATE INDEX IF NOT EXISTS idx_acc_type     ON accidents(accident_type);
CREATE INDEX IF NOT EXISTS idx_acc_severity ON accidents(severity);
CREATE INDEX IF NOT EXISTS idx_acc_date     ON accidents(date);
CREATE INDEX IF NOT EXISTS idx_acc_city     ON accidents(city);
CREATE INDEX IF NOT EXISTS idx_acc_deaths   ON accidents(deaths);
CREATE INDEX IF NOT EXISTS idx_acc_province ON accidents(province);

-- 表2: CAICS 致因明细
CREATE TABLE IF NOT EXISTS caics_causes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    evidence TEXT NOT NULL,
    inference TEXT NOT NULL,
    evidence_quality TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cause_acc     ON caics_causes(accident_id);
CREATE INDEX IF NOT EXISTS idx_cause_code    ON caics_causes(code);
CREATE INDEX IF NOT EXISTS idx_cause_quality ON caics_causes(evidence_quality);

-- 表3: 搜索增强日志
CREATE TABLE IF NOT EXISTS search_logs (
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    search_performed INTEGER NOT NULL DEFAULT 1,
    queries_used TEXT,
    total_results INTEGER,
    same_accident_count INTEGER,
    same_accident_snippets TEXT,
    engine TEXT NOT NULL DEFAULT 'qianfan',
    error TEXT,
    created_at TEXT NOT NULL
);

-- 表4: 多模态图片分析
-- vlm_model 的默认值是历史值（这一列记录的是**当时实际调用的模型**，
-- 不是当前配置），不要跟着 config 改，否则历史行的含义会被改写。
CREATE TABLE IF NOT EXISTS image_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    local_path TEXT NOT NULL,
    source_url TEXT,
    phase1_scene_type TEXT,
    phase1_objects TEXT,
    phase1_people TEXT,
    phase1_damage TEXT,
    phase1_weather TEXT,
    phase1_raw_response TEXT,
    phase2_consistency TEXT,
    phase2_confidence TEXT,
    phase2_new_info TEXT,
    phase2_raw_response TEXT,
    vlm_model TEXT NOT NULL DEFAULT 'qwen-vl-max',
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

-- 表5: 处理流水线日志
CREATE TABLE IF NOT EXISTS processing_log (
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    pipeline_version TEXT NOT NULL,
    text_type TEXT NOT NULL,
    text_type_confidence TEXT NOT NULL,
    text_type_reason TEXT NOT NULL,
    classification_verdict TEXT NOT NULL,
    classification_rule_result TEXT,
    classification_llm_result TEXT,
    search_triggered INTEGER NOT NULL DEFAULT 0,
    step1_success INTEGER NOT NULL DEFAULT 0,
    step1_fields_extracted INTEGER,
    step1_duration_ms INTEGER,
    step2_success INTEGER NOT NULL DEFAULT 0,
    step2_cause_count INTEGER,
    step2_duration_ms INTEGER,
    image_count_analyzed INTEGER,
    total_duration_ms INTEGER,
    error_message TEXT,
    created_at TEXT NOT NULL
);

-- 表6: 多源 URL
CREATE TABLE IF NOT EXISTS source_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    source_name TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(accident_id, url)
);

-- 表7: 全局元数据
CREATE TABLE IF NOT EXISTS pipeline_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 表8: MECE 枚举定义（原脚本漏建，真库里有 57 行 5 个枚举版本）
CREATE TABLE IF NOT EXISTS enum_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enum_type TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    definition TEXT NOT NULL,
    decision_rule TEXT,
    version TEXT NOT NULL DEFAULT '2.0-llm',
    sort_order INTEGER NOT NULL DEFAULT 0,
    sample_count INTEGER DEFAULT 0,
    parent_code TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(enum_type, code, version)
);

-- 表9: 图片文件登记（原脚本漏建，真库里有 1,037 行）
CREATE TABLE IF NOT EXISTS image_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    local_path TEXT NOT NULL,
    source_url TEXT,
    source_page_url TEXT,
    file_size INTEGER DEFAULT 0,
    width INTEGER,
    height INTEGER,
    mime_type TEXT,
    download_status TEXT NOT NULL DEFAULT 'downloaded',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 索引名沿用真实库里既有那个（idx_image_files_accident），不另起新名，
-- 免得同一列上挂两个等价索引
CREATE INDEX IF NOT EXISTS idx_image_files_accident ON image_files(accident_id);
"""

TABLE_COUNT = 9


def inspect(conn: sqlite3.Connection) -> dict:
    """报告库现状：表、accidents 列数、索引。"""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(accidents)").fetchall()]
    indexes = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name NOT LIKE 'sqlite_%'").fetchall())
    return {"tables": tables, "cols": cols, "indexes": indexes}


def expected() -> dict:
    """把 SCHEMA 灌进内存库，得到「脚本认为应该长什么样」。"""
    mem = sqlite3.connect(":memory:")
    mem.executescript(SCHEMA)
    info = inspect(mem)
    mem.close()
    return info


def create_database(db_path: str | None = None, *, rebuild: bool = False,
                    dry_run: bool = False) -> sqlite3.Connection | None:
    """建库或补齐。已有库默认只补缺的表与索引，**不删任何数据**。"""
    db_path = Path(db_path or "data/caiems_v2.db")
    want = expected()

    if dry_run:
        print("脚本期望：")
        print(f"  表 {len(want['tables'])}：{want['tables']}")
        print(f"  accidents {len(want['cols'])} 列")
        print(f"  索引 {len(want['indexes'])}：{want['indexes']}")
        if db_path.exists():
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            got = inspect(con)
            con.close()
            print(f"\n{db_path} 现状：")
            print(f"  表 {len(got['tables'])}：{got['tables']}")
            print(f"  accidents {len(got['cols'])} 列")
            print(f"  索引 {len(got['indexes'])}：{got['indexes']}")
            miss_t = [t for t in want["tables"] if t not in got["tables"]]
            miss_i = [i for i in want["indexes"] if i not in got["indexes"]]
            miss_c = [c for c in want["cols"] if c not in got["cols"]]
            extra_c = [c for c in got["cols"] if c not in want["cols"]]
            print(f"\n差异：缺表 {miss_t or '无'}")
            print(f"      缺索引 {miss_i or '无'}")
            print(f"      缺列 {miss_c or '无'}  多列 {extra_c or '无'}")
        else:
            print(f"\n{db_path} 不存在，将新建")
        print("\n[dry-run] 未执行任何写操作")
        return None

    if db_path.exists() and rebuild:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db_path.parent / f"{db_path.name}.bak.before_rebuild_{ts}"
        shutil.copy2(db_path, backup)
        size_mb = backup.stat().st_size / 1024 / 1024
        print(f"已备份 → {backup}  ({size_mb:.1f} MB)")
        db_path.unlink()
        print(f"已删除旧库：{db_path}")
    elif db_path.exists():
        print(f"库已存在，只补缺失的表与索引（不删数据）：{db_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    # 建库连接也要开外键；但注意 SQLite 的外键是**连接级**开关，
    # 这里开了不等于别的连接也开（这正是历史上 1,041 行孤儿的成因）
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)

    now = datetime.now().isoformat()
    # OR IGNORE：补建已有库时不覆盖既有元数据
    conn.execute("INSERT OR IGNORE INTO pipeline_metadata VALUES (?, ?, ?)",
                 ("pipeline_version", "caics_v1.0", now))
    conn.execute("INSERT OR IGNORE INTO pipeline_metadata VALUES (?, ?, ?)",
                 ("created_at", now, now))
    conn.commit()

    got = inspect(conn)
    print(f"\n现有 {len(got['tables'])} 张表: {got['tables']}")
    print(f"accidents: {len(got['cols'])} 列")
    print(f"索引: {len(got['indexes'])} 个")
    print(f"数据库: {db_path}")
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 / 补齐 caiems_v2 数据库")
    parser.add_argument("--db", help="库文件路径（默认 data/caiems_v2.db）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只报告差异，不写库")
    parser.add_argument("--rebuild", action="store_true",
                        help="先备份再整库重建（会删数据，谨慎）")
    args = parser.parse_args()

    conn = create_database(args.db, rebuild=args.rebuild, dry_run=args.dry_run)
    if conn is not None:
        conn.close()
        print("\n✅ 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
