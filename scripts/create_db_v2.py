"""创建 caiems_v2 数据库及全部7张表。"""

import sqlite3
import os
from datetime import datetime

DB_PATH = "data/caiems_v2.db"

SCHEMA = """
-- 表1: 事故主表 (55列)
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
    lighting TEXT,
    day_of_week TEXT,
    season TEXT,
    time_period TEXT,
    is_night_shift INTEGER NOT NULL DEFAULT 0,
    project_name TEXT,
    project_type TEXT NOT NULL,
    building_structure TEXT,
    construction_phase TEXT NOT NULL,
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
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_acc_type ON accidents(accident_type);
CREATE INDEX IF NOT EXISTS idx_acc_severity ON accidents(severity);
CREATE INDEX IF NOT EXISTS idx_acc_date ON accidents(date);
CREATE INDEX IF NOT EXISTS idx_acc_city ON accidents(city);
CREATE INDEX IF NOT EXISTS idx_acc_deaths ON accidents(deaths);

-- 表2: CAICS致因明细
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

CREATE INDEX IF NOT EXISTS idx_cause_acc ON caics_causes(accident_id);
CREATE INDEX IF NOT EXISTS idx_cause_code ON caics_causes(code);

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

-- 表4: 多模态分析
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

-- 表6: 多源URL
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
"""


def create_database(db_path: str = DB_PATH) -> sqlite3.Connection:
    """创建数据库和全部表。"""
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"已删除旧库: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)

    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO pipeline_metadata VALUES (?, ?, ?)",
        ("pipeline_version", "caics_v1.0", now),
    )
    conn.execute(
        "INSERT INTO pipeline_metadata VALUES (?, ?, ?)",
        ("created_at", now, now),
    )
    conn.commit()

    # 验证
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"创建 {len(tables)} 张表: {[t[0] for t in tables]}")

    col_count = len(conn.execute("PRAGMA table_info(accidents)").fetchall())
    print(f"accidents 表: {col_count} 列")
    print(f"数据库: {db_path}")
    return conn


if __name__ == "__main__":
    conn = create_database()
    conn.close()
    print("✅ 数据库创建完成")
