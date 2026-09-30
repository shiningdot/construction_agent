"""工作库 `caiems_staging.db` 的 schema 定义（单一出口）。

落实用户阶段③「结构化结果 → 临时数据库 → 人工审核 → 最终数据库」：
采集原始、提取暂存、审核状态全部落在**独立工作库**，与最终库物理分离。

| 库 | 路径 | 角色 | 连接方式 |
|---|---|---|---|
| 最终库 | `data/caiems_v2.db` | 已审核并入的正式数据 | 只读（`mode=ro` + `query_only`） |
| 工作库 | `data/caiems_staging.db` | 采集原始 + 提取暂存 + 审核状态 | 读写（WAL） |

本模块只放 DDL，不含连接逻辑（连接见 `staging_db.py`，建表入口见
`scripts/create_staging_db.py`）。这样建表、校验、迁移三处引用同一份定义，
不会再出现 `create_db_v2.py` 那种「脚本 SCHEMA 52 列 / 注释 55 列 / 真实库 48 列」
的三方差值。

十一张表分三组：

- 阶段①（蓝图 01）：`collect_sources` / `collect_tasks` /
  `raw_accidents` / `raw_accident_assets`
- 阶段②③（蓝图 02/03）：`extract_tasks` / `staging_accidents` /
  `staging_causes` / `promote_plans` / `promote_log`
- 阶段④（蓝图 06）：`image_manifest` / `staging_metadata`
"""

from __future__ import annotations

SCHEMA_VERSION = "staging_v1.1"

# 建表顺序即外键依赖顺序，不可随意调整
SCHEMA_SQL = """
-- ═══════════════════════════════════════════════════════════════════
-- 阶段① 在线采集
-- ═══════════════════════════════════════════════════════════════════

-- ① 源登记：把 25 个适配器的元信息从代码搬到表里
CREATE TABLE IF NOT EXISTS collect_sources (
    source_key      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    source_group    TEXT NOT NULL,          -- national | province | literature | academic
    track           TEXT NOT NULL,          -- network | literature —— 对应论文两轨
    entry_url       TEXT,
    crawl_delay     REAL DEFAULT 2.0,       -- 秒，政府站建议 >= 5
    enable_classify INTEGER DEFAULT 1,      -- 是否走自动闸门
    is_active       INTEGER DEFAULT 1,
    last_run_at     TEXT,
    last_run_ok     INTEGER,
    last_error      TEXT,
    total_raw       INTEGER DEFAULT 0,
    total_promoted  INTEGER DEFAULT 0,
    notes           TEXT
);

-- ② 采集任务：一次运行 = 一条，取代进程内的 _collect_tasks 字典
CREATE TABLE IF NOT EXISTS collect_tasks (
    task_id       TEXT PRIMARY KEY,         -- uuid4 hex[:12]
    source_keys   TEXT NOT NULL,            -- JSON 数组，支持多源一次任务
    params        TEXT,                     -- JSON: {since, until, limit, keywords}
    -- cancelling 是中间态：用户点了取消、任务还在收尾（适配器没有可中断的
    -- yield 点，粒度只能到源级）。跑完时它落成 cancelled，不落成 done。
    status        TEXT NOT NULL,            -- pending|running|cancelling|paused|done|failed|cancelled
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    cursor        TEXT,                     -- JSON: 断点续跑位置（每源一个游标）
    stats         TEXT,                     -- JSON: {per_source:{collected,cleaned,kept,dup}}
    error         TEXT
);

-- ③ 原始事故文本：采集层的唯一数据表
CREATE TABLE IF NOT EXISTS raw_accidents (
    raw_id        TEXT PRIMARY KEY,         -- md5(source_url)[:12]，沿用 base.py 既有算法
    source_key    TEXT NOT NULL REFERENCES collect_sources(source_key),
    task_id       TEXT REFERENCES collect_tasks(task_id),
    title         TEXT,
    content       TEXT,                     -- 清洗后纯文本
    raw_html      TEXT,                     -- 原始 HTML（<=50KB，便于二次解析）
    source_url    TEXT NOT NULL,
    fetch_time    TEXT NOT NULL,            -- ISO
    publish_date  TEXT,
    -- 三层去重键（base.py 既有设计）
    url_hash      TEXT NOT NULL,
    fingerprint   TEXT,                     -- 年月+城市+伤亡+类型
    title_hash    TEXT,
    -- 规则预提取字段（field_extractor 填充）
    ext_date TEXT, ext_province TEXT, ext_city TEXT,
    ext_deaths INTEGER, ext_injuries INTEGER,
    ext_accident_type TEXT, ext_severity TEXT,
    confidence_overall REAL,
    -- 闸门与人工裁决（取代 18 份状态机复制品）
    gate_verdict      TEXT,                 -- confirmed | rejected | review | NULL(未判)
    gate_reason       TEXT,
    gate_stage        TEXT,                 -- rule | llm | manual
    gate_confidence   TEXT,                 -- high | low
    gate_at           TEXT,
    -- 生命周期：与 gate_verdict 正交
    status        TEXT NOT NULL DEFAULT 'new',   -- new|gated|promoted|archived
    promoted_at   TEXT,
    stage_id      TEXT,
    extra         TEXT,                     -- JSON
    UNIQUE(url_hash)
);
CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_accidents(status, gate_verdict);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_accidents(source_key, fetch_time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_fp     ON raw_accidents(fingerprint);
CREATE INDEX IF NOT EXISTS idx_raw_task   ON raw_accidents(task_id);

-- ④ 关联资产：PDF / 图片 / 附件从文件系统路径登记进表
CREATE TABLE IF NOT EXISTS raw_accident_assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id       TEXT NOT NULL REFERENCES raw_accidents(raw_id) ON DELETE CASCADE,
    asset_type   TEXT NOT NULL,             -- pdf | image | attachment
    url          TEXT,
    local_path   TEXT,                      -- 相对 data/ 的路径
    bytes        INTEGER,
    ocr_status   TEXT,                      -- pending|done|failed|skipped
    ocr_text     TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_raw ON raw_accident_assets(raw_id);

-- ═══════════════════════════════════════════════════════════════════
-- 阶段②③ 提取与审核
-- ═══════════════════════════════════════════════════════════════════

-- ⑤ 提取任务
CREATE TABLE IF NOT EXISTS extract_tasks (
    task_id        TEXT PRIMARY KEY,        -- uuid4 hex[:12]
    raw_ids        TEXT NOT NULL,           -- JSON 数组
    stages         TEXT NOT NULL,           -- JSON: ["S1","S2","S3","S4"]
    enable_split   INTEGER DEFAULT 1,       -- MULTI 是否拆分
    status         TEXT NOT NULL,           -- pending|running|done|failed|cancelled
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    concurrency    INTEGER DEFAULT 3,
    est_tokens     INTEGER,                 -- 建任务时的预估
    est_cost_cny   REAL,
    actual_tokens_in  INTEGER DEFAULT 0,    -- 来自 response.usage
    actual_tokens_out INTEGER DEFAULT 0,
    stats          TEXT,                    -- JSON: {s1:{...},s2:{...},s3:{...},s4:{...}}
    error          TEXT
);

-- ⑥ 暂存事故：阶段③临时数据库的主表
--
-- 列名策略：**忠实保留 S3 的输出原名**，不改名。已知 4 处名字与最终库不同
-- （location / relevance_verdict / s3_ms / s4_ms），转换集中在并入映射层
-- （蓝图 03 第 5.1 节），只在这一处适配。
--
-- 不设 `source` / `source_url` 列：来源信息在 raw_accidents 表中是唯一出处，
-- 此处冗余存储只会引入不一致（并入映射本就从 raw_accidents 取）。
CREATE TABLE IF NOT EXISTS staging_accidents (
    stage_id       TEXT PRIMARY KEY,        -- uuid4 hex[:12]
    raw_id         TEXT REFERENCES raw_accidents(raw_id),
    task_id        TEXT REFERENCES extract_tasks(task_id),
    origin_segment TEXT,                    -- MULTI 拆分时的原文片段；single 为 NULL

    -- S1 产出：文本类型闸门
    text_type            TEXT,              -- single|multi|regulatory|aggregate|training|other
    text_type_reason     TEXT,
    text_type_confidence TEXT,
    split_index          INTEGER,           -- MULTI 拆分序号，从 1 起

    -- S2 产出：事故相关性闸门
    relevance_verdict    TEXT,              -- construction|non_construction|insufficient
    relevance_reason     TEXT,
    relevance_stage      TEXT,              -- rule | llm
    relevance_confidence TEXT,

    -- S3 产出：31 个基础字段（列名为 S3 原样输出）
    title TEXT, date TEXT, accident_time TEXT,
    location TEXT,                          -- ⚠️ 并入时映射到 accidents.province
    city TEXT, district TEXT, accident_location_detail TEXT,
    project_name TEXT, project_type TEXT, construction_phase TEXT,
    building_structure TEXT, accident_type TEXT, severity TEXT,
    deaths INTEGER, injuries INTEGER, trapped_count INTEGER,
    collapse_subtype TEXT, fall_height REAL,
    work_task TEXT, equipment_involved TEXT, material_involved TEXT,
    worker_type TEXT, weather TEXT,
    contractor TEXT, contractor_type TEXT,
    developer TEXT, supervision_company TEXT,
    direct_economic_loss REAL,
    direct_cause TEXT,                      -- ⚠️ 现有 S3 tool schema 尚未产出此字段，见下方说明
    summary TEXT, report_type TEXT,

    -- 过程元数据（可溯源，取代被丢弃的 response.usage）
    s3_model TEXT, s3_ms INTEGER, s3_tokens_in INTEGER, s3_tokens_out INTEGER,
    s4_model TEXT, s4_ms INTEGER, s4_tokens_in INTEGER, s4_tokens_out INTEGER,
    s4_primary_cause TEXT, s4_cause_chain TEXT,

    -- 阶段③人工审核（逻辑在蓝图 03）
    review_status   TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|edited|rejected
    review_note     TEXT,
    reviewer        TEXT,
    reviewed_at     TEXT,

    created_at     TEXT NOT NULL,
    UNIQUE(raw_id, split_index)
);
CREATE INDEX IF NOT EXISTS idx_stage_review ON staging_accidents(review_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stage_task   ON staging_accidents(task_id);
CREATE INDEX IF NOT EXISTS idx_stage_raw    ON staging_accidents(raw_id);

-- ⑦ 暂存致因编码
CREATE TABLE IF NOT EXISTS staging_causes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id     TEXT NOT NULL REFERENCES staging_accidents(stage_id) ON DELETE CASCADE,
    code         TEXT NOT NULL,             -- CAICS 码，如 "H1"
    name         TEXT NOT NULL,             -- 中文类名，来自 S4 输出
    evidence     TEXT,
    inference    TEXT,
    evidence_quality       TEXT,            -- 生效值（当前 = 现算规则），与论文口径一致
    evidence_quality_rule  TEXT,            -- 现算规则结果，显式记录来源
    evidence_quality_llm   TEXT,            -- S4 自报，对照列，可为 NULL
    sort_order   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scause_stage ON staging_causes(stage_id, sort_order);

-- ⑧ 并入确认令牌（蓝图 03 第 5.4 节）
--
-- 并入最终库是唯一会改论文数据的操作，所以强制「先 dry_run 再执行」：
-- dry_run 返回一个 token，执行时必须带上，且 token 与 stage_ids 集合绑定。
-- 这防的是「用户改了选择却沿用上一次的确认」——那种错误一旦发生就是往
-- 论文口径里混入未经预览的数据。
--
-- token 存工作库而不是进程内存：服务重启后仍在，也不受多 worker 影响。
CREATE TABLE IF NOT EXISTS promote_plans (
    token      TEXT PRIMARY KEY,        -- uuid4 hex[:12]
    stage_ids  TEXT NOT NULL,           -- JSON 数组，与 token 绑定
    plan       TEXT NOT NULL,           -- JSON：dry_run 结果快照，执行时比对
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL            -- 过期即作废，防止隔天误点
);

-- ⑨ 并入历史（蓝图 03 第 5.5 节的回滚依据）
--
-- 记下每次并入用了哪个备份、写了多少行、是否已回滚。回滚只开放「最近一次」，
-- 更早的走手工恢复——自动回滚越久远的越危险（其间可能已有别的并入）。
CREATE TABLE IF NOT EXISTS promote_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    token          TEXT NOT NULL,
    stage_ids      TEXT NOT NULL,       -- JSON 数组
    promoted       INTEGER NOT NULL,    -- 写入 accidents 的条数
    causes_written INTEGER NOT NULL,    -- 写入 caics_causes 的行数
    backup_path    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    rolled_back_at TEXT                 -- 非 NULL 表示已回滚
);
CREATE INDEX IF NOT EXISTS idx_promote_log_time ON promote_log(created_at DESC);

-- ═══════════════════════════════════════════════════════════════════
-- 阶段④ 图片清单
-- ═══════════════════════════════════════════════════════════════════

-- ⑧ 图片清单：以文件系统为真源重建，取代最终库 image_files 的路径错配
--    （实测 imageV2/ 550 + images/ 276 + imageV3/ 211 三版混存）
CREATE TABLE IF NOT EXISTS image_manifest (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id  TEXT NOT NULL,             -- 目录名，= accidents.id
    rel_path     TEXT NOT NULL,             -- 相对 data/ 的路径
    filename     TEXT NOT NULL,
    ext          TEXT NOT NULL,             -- jpg | png | webp
    bytes        INTEGER,
    sha1         TEXT,                      -- 内容哈希，用于识别跨目录重复
    -- 尽力回填（来自旧登记，可为 NULL）
    source_url      TEXT,
    source_page_url TEXT,
    width  INTEGER,
    height INTEGER,
    -- 尽力回填（来自 image_analyses）
    phase1_scene_type   TEXT,
    phase2_consistency  TEXT,
    phase2_confidence   TEXT,
    vlm_model           TEXT,
    analyzed     INTEGER DEFAULT 0,
    -- 溯源
    version      TEXT NOT NULL DEFAULT 'v3',
    manifest_at  TEXT NOT NULL,
    UNIQUE(rel_path)
);
CREATE INDEX IF NOT EXISTS idx_manifest_acc ON image_manifest(accident_id);

-- 元数据：schema 版本与建库时刻
CREATE TABLE IF NOT EXISTS staging_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 建表后的行数自检用；顺序与依赖一致
TABLES: tuple[str, ...] = (
    "collect_sources",
    "collect_tasks",
    "raw_accidents",
    "raw_accident_assets",
    "extract_tasks",
    "staging_accidents",
    "staging_causes",
    "promote_plans",
    "promote_log",
    "image_manifest",
    "staging_metadata",
)

# 最终库中没有、只有工作库才有的表
STAGING_ONLY_TABLES: tuple[str, ...] = TABLES
