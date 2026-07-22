# 建筑施工事故信息采集与分析项目 — 数据库架构分析报告

> 生成日期：2026-07-06  
> 分析范围：`construction_agent/database/`、`construction_agent/models/accident.py`、`construction_agent/config.py` 及相关脚本

---

## 1. 概述

项目采用**双存储后端架构**，通过 Repository 模式抽象数据访问层：

```
业务代码 (main.py, web/app.py, scripts/*)
         │
         ▼
  AccidentRepository (ABC 抽象接口)
         │
    ┌────┴────┐
    ▼         ▼
 DatasetManager          SqliteAccidentRepository
 (JSON 文件/内存)         (SQLAlchemy 2.0 + SQLite)
 【当前生产默认】           【已实现，未激活】
```

| 维度 | JSON 模式 (DatasetManager) | SQLite 模式 (SqliteAccidentRepository) |
|------|---------------------------|---------------------------------------|
| 存储文件 | `data/datasets/accidents_merged.json` | `data/caiems.db`（默认路径） |
| 技术栈 | 原生 `json` 模块 + 内存 `list[dict]` | SQLAlchemy 2.0 ORM |
| ID 策略 | MD5(标题+日期+城市) 前 8 位 | 同上（字符串主键） |
| 去重 | 标题模糊匹配（相似度 ≥ 0.65） | 主键唯一约束 |
| 中间数据日志 | ❌ 不支持 | ✅ 支持（4 张日志表） |
| 当前激活状态 | ✅ 默认激活 | ❌ 未激活 |

---

## 2. 数据库架构总览

### 2.1 ER 图（逻辑关系）

```
┌──────────────────────────────────────────────────────────────┐
│                      accidents (主表)                         │
│  PK: id (TEXT)                                               │
│  基础信息 / 伤亡 / 时间派生 / 原因 / 来源 / HFACS              │
└────┬───────┬────────┬─────────┬──────────┬───────────────────┘
     │       │        │         │          │
     │ 1:N  │ 1:N   │ 1:N    │ 1:N     │ 1:N
     ▼       ▼        ▼         ▼          ▼
┌────────┐┌──────┐┌───────┐┌────────┐┌──────────┐
│factors ││hfacs ││source ││search  ││search    │┌────────┐
│        ││detail││_urls  ││snippets││_queries  ││images  │
│ 1:N    ││ 1:N  ││ 1:N   ││ 1:N    ││ 1:N      ││ 1:N    │
└────────┘└──────┘└───────┘└────────┘└──────────┘└────────┘
     │
     │ 1:N (可为 NULL)
     ▼
┌────────────────────┐
│ processing_history │  ◄── 处理流水线记录
└──┬─────┬──────┬────┘
   │     │      │
   │ 1:N │ 1:N  │ 1:N
   ▼     ▼      ▼
┌──────┐┌─────┐┌──────────┐
│llm   ││search││pdf       │
│api   ││logs ││processing│
│logs  ││     ││_logs     │
└──────┘└─────┘└──────────┘
```

### 2.2 表清单

| # | 表名 | 用途 | 行数预估 |
|---|------|------|---------|
| 1 | `accidents` | 事故主记录 | 与事故数量相当 |
| 2 | `factors` | 四维度原因（人/机/管/环） | 事故数 × 1~N |
| 3 | `hfacs_details` | HFACS-CS 编码明细 | 事故数 × 1~N |
| 4 | `source_urls` | 来源 URL | 事故数 × 1~N |
| 5 | `search_snippets` | 搜索摘要片段 | 事故数 × 0~N |
| 6 | `search_queries` | 搜索查询词 | 事故数 × 0~N |
| 7 | `images` | 关联图片 | 事故数 × 0~N |
| 8 | `processing_history` | 处理流水线记录 | 处理批次数 |
| 9 | `llm_api_logs` | LLM API 调用日志 | 处理次数 × 调用数 |
| 10 | `search_logs` | 搜索引擎调用日志 | 处理次数 × 搜索数 |
| 11 | `pdf_processing_logs` | PDF 处理日志 | PDF 文件数 |

---

## 3. 表结构详细分析

### 3.1 主表：`accidents`

```sql
CREATE TABLE accidents (
    id              TEXT PRIMARY KEY,          -- MD5(标题+日期+城市) 前8位
    title           TEXT NOT NULL,             -- 事故标题
    date            TEXT,                       -- ISO 日期 (YYYY-MM-DD)
    accident_time   TEXT,                       -- 时间 (HH:MM)
    location        TEXT NOT NULL DEFAULT '',   -- 地点
    city            TEXT NOT NULL DEFAULT '',   -- 城市
    accident_location_detail TEXT NOT NULL DEFAULT '',
    project_name    TEXT NOT NULL DEFAULT '',   -- 项目名称
    project_type    TEXT NOT NULL DEFAULT '',   -- 项目类型
    building_structure TEXT NOT NULL DEFAULT '',-- 建筑结构
    building_completion_year INTEGER,          -- 建筑竣工年份
    construction_phase TEXT NOT NULL DEFAULT '',-- 施工阶段
    weather         TEXT NOT NULL DEFAULT '',   -- 天气
    accident_type   TEXT NOT NULL DEFAULT '',   -- 事故类型 (11种枚举)
    severity        TEXT NOT NULL DEFAULT '',   -- 严重等级 (4级)
    deaths          INTEGER NOT NULL DEFAULT 0,-- 死亡人数
    injuries        INTEGER NOT NULL DEFAULT 0,-- 受伤人数
    trapped_count   INTEGER NOT NULL DEFAULT 0,-- 被困人数
    direct_economic_loss FLOAT,                -- 直接经济损失(万元)
    day_of_week     TEXT,                       -- 星期几 (自动派生)
    season          TEXT,                       -- 季节 (自动派生)
    time_period     TEXT,                       -- 时段 (自动派生)
    lighting        TEXT,                       -- 光照条件 (自动派生)
    direct_cause    TEXT NOT NULL DEFAULT '',   -- 直接原因
    contractor      TEXT NOT NULL DEFAULT '',   -- 承包商
    summary         TEXT NOT NULL DEFAULT '',   -- 事故摘要
    source          TEXT NOT NULL DEFAULT '',   -- 数据来源
    raw_input       TEXT NOT NULL DEFAULT '',   -- 原始输入文本
    hfacs_codes     TEXT NOT NULL DEFAULT '[]', -- HFACS 编码 (JSON 数组)
    hfacs_encoding_version TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL               -- 创建时间 (ISO)
);

-- 索引
CREATE INDEX idx_acc_type ON accidents(accident_type);
CREATE INDEX idx_acc_severity ON accidents(severity);
CREATE INDEX idx_acc_location ON accidents(location);
CREATE INDEX idx_acc_date ON accidents(date);
CREATE INDEX idx_acc_deaths ON accidents(deaths);
CREATE INDEX idx_acc_type_sev_date ON accidents(accident_type, severity, date);
```

**字段统计**：32 列，其中 17 列设了 `NOT NULL DEFAULT ''`（空字符串默认值策略）。

### 3.2 原因表：`factors`

统一存储 4 个维度（人因、机器、管理、环境）的原因，通过 `dimension` 字段区分。

```sql
CREATE TABLE factors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    dimension   TEXT NOT NULL CHECK(dimension IN ('human','machine','management','environmental')),
    category    TEXT NOT NULL,          -- 原因类别
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,  -- 排序（human=0~999, machine=1000~1999, 以此类推）
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_factors_accident ON factors(accident_id);
CREATE INDEX idx_factors_dim ON factors(dimension);
```

**设计要点**：
- 4 种维度映射到同一张表，避免 4 张独立子表
- `sort_order` 使用数值区间区分维度（0-999/1000-1999/2000-2999/3000-3999），允许在应用层稳定排序

### 3.3 HFACS 编码明细表：`hfacs_details`

```sql
CREATE TABLE hfacs_details (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id       TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    code              TEXT NOT NULL,      -- HFACS-CS 编码 (如 AA101)
    level             INTEGER NOT NULL,   -- 层级 (1-4)
    level_name_cn     TEXT NOT NULL,      -- 层级中文名
    category_name_cn  TEXT NOT NULL,      -- 类别中文名
    source_dimension  TEXT NOT NULL,      -- 来源维度
    source_category   TEXT NOT NULL,      -- 来源类别
    source_description TEXT NOT NULL DEFAULT '',
    encoding_method   TEXT NOT NULL DEFAULT 'llm'  -- 编码方式 (llm/rule/manual)
);

CREATE INDEX idx_hfacs_accident ON hfacs_details(accident_id);
CREATE INDEX idx_hfacs_code ON hfacs_details(code);
```

### 3.4 辅助数据表

#### `source_urls`
```sql
CREATE TABLE source_urls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(accident_id, url)  -- 防止重复 URL
);
```

#### `search_snippets`
```sql
CREATE TABLE search_snippets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    snippet     TEXT NOT NULL DEFAULT '',
    query       TEXT NOT NULL DEFAULT '',  -- 使用的搜索查询词
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_snippets_accident ON search_snippets(accident_id);
```

#### `search_queries`
```sql
CREATE TABLE search_queries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    query       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
```

#### `images`
```sql
CREATE TABLE images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT NOT NULL REFERENCES accidents(id) ON DELETE CASCADE,
    local_path  TEXT NOT NULL DEFAULT '',
    source_url  TEXT NOT NULL DEFAULT '',
    thumb_url   TEXT NOT NULL DEFAULT '',
    file_size   INTEGER,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
```

### 3.5 日志表组（仅 SQLite 模式支持）

#### `processing_history` — 处理流水线记录
```sql
CREATE TABLE processing_history (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id           TEXT REFERENCES accidents(id) ON DELETE SET NULL,
    pipeline_version      TEXT NOT NULL,          -- 流水线版本号
    status                TEXT NOT NULL CHECK(status IN ('success','failed','partial')),
    retry_count           INTEGER NOT NULL DEFAULT 0,
    input_summary         TEXT,                   -- 输入摘要
    error_message         TEXT,
    error_category        TEXT,                   -- 错误分类
    duration_ms           INTEGER,
    token_usage_prompt    INTEGER,               -- prompt token 用量
    token_usage_completion INTEGER,              -- completion token 用量
    created_at            TEXT NOT NULL
);
CREATE INDEX idx_ph_accident ON processing_history(accident_id);
CREATE INDEX idx_ph_version ON processing_history(pipeline_version);
CREATE INDEX idx_ph_created ON processing_history(created_at);
```

#### `llm_api_logs` — LLM API 调用日志
```sql
CREATE TABLE llm_api_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    processing_id     INTEGER NOT NULL REFERENCES processing_history(id) ON DELETE CASCADE,
    model             TEXT NOT NULL,         -- 模型名称
    system_prompt     TEXT,                  -- 系统提示词（全文）
    user_prompt       TEXT,                  -- 用户提示词（全文）
    raw_response      TEXT,                  -- API 原始返回
    thinking_block    TEXT,                  -- 思维链内容
    json_extracted    TEXT,                  -- 提取的结构化 JSON
    parse_success     INTEGER NOT NULL DEFAULT 0,  -- 0/1 布尔
    parse_error       TEXT,                  -- 解析错误信息
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    duration_ms       INTEGER,
    created_at        TEXT NOT NULL
);
CREATE INDEX idx_llm_processing ON llm_api_logs(processing_id);
CREATE INDEX idx_llm_parse ON llm_api_logs(parse_success);
```

#### `search_logs` — 搜索日志
```sql
CREATE TABLE search_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    processing_id     INTEGER NOT NULL REFERENCES processing_history(id) ON DELETE CASCADE,
    engine            TEXT NOT NULL,         -- 搜索引擎 (baidu/bing)
    query             TEXT NOT NULL,         -- 搜索查询词
    raw_html          TEXT,                  -- 原始 HTML 响应
    raw_api_response  TEXT,                  -- API 原始响应
    result_count      INTEGER,              -- 结果数量
    result_positions  TEXT,                  -- 结果位置 (JSON)
    success           INTEGER NOT NULL DEFAULT 1,  -- 0/1 布尔
    error_message     TEXT,
    duration_ms       INTEGER,
    created_at        TEXT NOT NULL
);
```

#### `pdf_processing_logs` — PDF 处理日志
```sql
CREATE TABLE pdf_processing_logs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    processing_id         INTEGER REFERENCES processing_history(id) ON DELETE SET NULL,
    pdf_filename          TEXT NOT NULL,
    mode                  TEXT NOT NULL,    -- 处理模式
    mineru_raw_output     TEXT,             -- MinerU 原始输出
    mineru_zip_path       TEXT,
    cleaned_text          TEXT,             -- 清洗后文本
    segment_count         INTEGER,          -- 分段数
    segments_discarded    INTEGER,          -- 丢弃分段数
    discarded_text_sample TEXT,             -- 丢弃文本样本
    split_strategy_used   TEXT,             -- 分割策略
    success               INTEGER NOT NULL DEFAULT 1,
    error_message         TEXT,
    duration_ms           INTEGER,
    created_at            TEXT NOT NULL
);
```

---

## 4. 数据模型映射

### 4.1 Pydantic 域模型 ↔ SQLAlchemy ORM

项目存在两套模型定义：

| 层级 | 定义位置 | 用途 |
|------|---------|------|
| Pydantic `Accident` | `construction_agent/models/accident.py` | 业务逻辑、验证、序列化 |
| SQLAlchemy `OrmAccident` | `construction_agent/database/models.py` | 数据库持久化 |

转换由 `sqlite_repository.py` 中的两个方法负责：

```python
_pydantic_to_orm(accident: Accident) -> OrmAccident   # 写入时
_orm_to_dict(orm: OrmAccident) -> dict                 # 读取时
```

**关键映射差异**：

| 域模型 (Pydantic) | 数据库模型 (SQLAlchemy) |
|-------------------|------------------------|
| `causes: list[dict]` | 展开为 `factors` 表行（按维度 + sort_order） |
| `hfacs_details: list[HfacsCode]` | 映射到 `hfacs_details` 表 (`HfacsDetail`) |
| `source_urls: list[str]` | `source_urls` 表 |
| `images: list[str]` | `images` 表 |
| `search_snippets: list[dict]` | `search_snippets` 表 |
| `search_queries: list[str]` | `search_queries` 表 |
| `date: Optional[date]` | `date: Optional[str]` (ISO 格式) |

### 4.2 枚举定义

**AccidentType**（11 种事故类型）：
坍塌、高处坠落、触电、机械伤害、起重伤害、物体打击、土方坍塌、火灾爆炸、中毒窒息、淹溺、其他

**SeverityLevel**（4 级严重程度）：
一般事故、较大事故、重大事故、特别重大事故

---

## 5. 存储后端对比

### 5.1 JSON 模式 (DatasetManager)

**文件**：`construction_agent/database/dataset_manager.py`

```
数据流：
  JSON 文件 → json.load() → list[dict] (内存) → 业务逻辑 → json.dump() → JSON 文件
```

**特点**：
- ✅ 零依赖，纯 Python 标准库
- ✅ 人类可读，直接用编辑器查看/修改
- ✅ 适合小规模数据集（< 10,000 条）
- ❌ 全量加载到内存，大文件性能差
- ❌ 无并发安全（多进程写入会损坏数据）
- ❌ 无中间数据日志（LLM 调用、搜索日志无法记录）
- ❌ 去重依赖标题模糊匹配（O(n²) 复杂度）
- ❌ 版本追踪困难（整个文件一个快照）

**去重逻辑**：
```python
# 标题相似度 ≥ 0.65 视为重复
def _is_duplicate(title, existing_titles):
    for t in existing_titles:
        if difflib.SequenceMatcher(None, title, t).ratio() >= 0.65:
            return True
    return False
```

### 5.2 SQLite 模式 (SqliteAccidentRepository)

**文件**：`construction_agent/database/sqlite_repository.py`

```
数据流：
  业务逻辑 → Pydantic Accident → _pydantic_to_orm() → SQLAlchemy Session → SQLite 文件
  业务逻辑 ← dict ← _orm_to_dict() ← SQLAlchemy Session ← SQLite 文件
```

**特点**：
- ✅ 支持事务（ACID）
- ✅ 支持并发读（SQLite WAL 模式可支持）
- ✅ 索引加速查询（accident_type、severity、date 等 6 个索引）
- ✅ 外键约束保证数据完整性
- ✅ 级联删除自动清理关联数据
- ✅ 4 张日志表完整记录处理过程
- ✅ 支持增量更新（UPDATE 而非全量重写）
- ❌ 需要 SQLAlchemy 依赖（当前未在 requirements.txt 中声明）
- ❌ 不可直接人类阅读
- ❌ 当前未被激活使用

---

## 6. CRUD 操作分析

### 6.1 SQLite 模式核心操作

已实现的全部 CRUD 方法在 `SqliteAccidentRepository` 中：

| 方法 | SQL 等价操作 | 说明 |
|------|-------------|------|
| `add(accident)` | `INSERT` 或 `UPDATE` | 存在则更新各字段，不存在则插入（含所有子表行） |
| `add_batch(accidents)` | 多条 `INSERT`/`UPDATE` | 批量写入，`session.commit()` 在外层统一调用 |
| `get_by_id(id)` | `SELECT ... WHERE id = ?` | Session.get() 直接获取 |
| `get_all()` | `SELECT ... ORDER BY created_at DESC` | 全量返回 |
| `search(kw)` | `SELECT ... WHERE title/summary/direct_cause LIKE '%kw%'` | 三字段模糊搜索 |
| `stats()` | `SELECT count()`, `SELECT sum()`, `GROUP BY` | 返回 `DatasetStats` 数据类 |
| `export()` | `SELECT` 全表 + 关系 | 返回 `list[dict]` |
| `clear()` | `DELETE FROM accidents` | 级联删除所有关联子表数据 |
| `initialize()` | `CREATE TABLE IF NOT EXISTS` × 11 | Base.metadata.create_all() |

### 6.2 日志操作（仅 SQLite）

| 方法 | 对应表 | 返回值 |
|------|--------|--------|
| `log_processing(record)` | `processing_history` | `int` (processing_id) |
| `log_llm_api(record)` | `llm_api_logs` | `int` |
| `log_search(record)` | `search_logs` | `int` |
| `log_pdf_processing(record)` | `pdf_processing_logs` | `int` |

---

## 7. 索引策略

SQLite 模式共创建了 **13 个索引**：

| 表 | 索引名 | 列 | 用途场景 |
|----|--------|-----|---------|
| accidents | `idx_acc_type` | `accident_type` | 按事故类型筛选/统计 |
| accidents | `idx_acc_severity` | `severity` | 按严重等级筛选/统计 |
| accidents | `idx_acc_location` | `location` | 按地点筛选 |
| accidents | `idx_acc_date` | `date` | 按日期范围查询 |
| accidents | `idx_acc_deaths` | `deaths` | 按死亡人数筛选 |
| accidents | `idx_acc_type_sev_date` | `(accident_type, severity, date)` | 复合条件查询/统计 |
| factors | `idx_factors_accident` | `accident_id` | JOIN 查询 |
| factors | `idx_factors_dim` | `dimension` | 按维度筛选原因 |
| hfacs_details | `idx_hfacs_accident` | `accident_id` | JOIN 查询 |
| hfacs_details | `idx_hfacs_code` | `code` | 按 HFACS 编码查找 |
| search_snippets | `idx_snippets_accident` | `accident_id` | JOIN 查询 |
| processing_history | `idx_ph_accident` | `accident_id` | 按事故查处理记录 |
| processing_history | `idx_ph_version` | `pipeline_version` | 按版本查处理记录 |
| processing_history | `idx_ph_created` | `created_at` | 按时间查处理记录 |
| llm_api_logs | `idx_llm_processing` | `processing_id` | JOIN 查询 |
| llm_api_logs | `idx_llm_parse` | `parse_success` | 查找解析失败记录 |

---

## 8. 级联删除策略

```
accidents (主表)
  ├── factors              → ON DELETE CASCADE (all, delete-orphan)
  ├── hfacs_details        → ON DELETE CASCADE (all, delete-orphan)
  ├── source_urls          → ON DELETE CASCADE (all, delete-orphan)
  ├── search_snippets      → ON DELETE CASCADE (all, delete-orphan)
  ├── search_queries       → ON DELETE CASCADE (all, delete-orphan)
  ├── images               → ON DELETE CASCADE (all, delete-orphan)
  └── processing_history   → ON DELETE SET NULL (普通 relationship)

processing_history
  ├── llm_api_logs         → ON DELETE CASCADE (all, delete-orphan)
  ├── search_logs          → ON DELETE CASCADE (all, delete-orphan)
  └── pdf_processing_logs  → ON DELETE CASCADE (all, delete-orphan)
```

**设计理由**：
- 事故删除时，其所有原因、HFACS、URL、图片等附属数据一并删除（CASCADE）
- 事故删除时，处理历史记录保留但 `accident_id` 置 NULL（SET NULL），避免丢失处理流水线的审计轨迹
- 处理记录删除时，关联的 LLM/搜索/PDF 日志级联删除

---

## 9. 迁移路径

`scripts/migrate_to_sqlite.py` 提供了完整的 JSON → SQLite 迁移能力：

```
JSON 文件 (accidents_merged.json)
    │
    ▼  json.load()
list[dict]
    │
    ▼  Pydantic 类型转换 + 字段映射
Accident (Pydantic 模型)
    │
    ▼  _pydantic_to_orm() 转换
OrmAccident (SQLAlchemy 模型)
    │
    ▼  session.add() + commit()
SQLite 文件 (caiems.db)
```

迁移脚本支持：
- `--stats`：仅统计，不写入
- `--dry-run`：试运行，打印操作而不提交
- `--reset`：清空 SQLite 后重新导入
- 事后自动统计对比（JSON 记录数 vs SQLite 记录数）

---

## 10. 关键发现

### 10.1 SQLAlchemy 未声明为依赖

`sqlite_repository.py` 和 `database/models.py` 大量使用 SQLAlchemy 2.0，但 `requirements.txt` 中未包含该依赖。运行 SQLite 模式前需要手动安装。

### 10.2 布尔值存储为整数

日志表中的 `parse_success`、`success` 等布尔字段使用 `INTEGER` + 0/1 值，而非 SQLite 的 native boolean 或 Python bool。这与 SQLite 缺少原生布尔类型有关，但 ORM 层未做自动转换。

### 10.3 时间戳统一为 TEXT 格式

所有日期/时间字段（`date`、`created_at` 等）存储为 ISO 格式字符串，而非 SQLite 的 datetime 类型或 Unix 时间戳。优点是可读性好，缺点是无法直接使用 SQLite 的日期函数。

### 10.4 空字符串默认值策略

17 个 TEXT 列使用 `NOT NULL DEFAULT ''`，而非 `NULL`。这简化了 Python 侧的类型处理（避免 None 检查），但丢失了"无数据"与"数据为空"的语义区分。

### 10.5 复合索引覆盖常见查询

`idx_acc_type_sev_date` 复合索引覆盖了 Web 面板中最常见的筛选组合（类型 + 严重等级 + 日期），是合理的设计决策。

### 10.6 生产环境当前使用 JSON 模式

所有运行代码（`main.py`、`web/app.py`）直接实例化 `DatasetManager()`，未使用 `get_repository()` 工厂函数。如需切换到 SQLite，需要在调用点修改或通过环境变量 `DATABASE_TYPE=sqlite` 触发。

---

## 11. 改进建议

| # | 建议 | 优先级 | 说明 |
|---|------|--------|------|
| 1 | 将 `sqlalchemy` 加入 `requirements.txt` | 高 | 缺少依赖会导致 SQLite 模式无法运行 |
| 2 | 统一使用 `get_repository()` 工厂 | 高 | 消除业务代码中的直接实例化，实现真正的后端切换 |
| 3 | 为 `DatasetManager` 添加中间数据日志 | 中 | 补齐 JSON 模式的功能差距 |
| 4 | 添加全文搜索（FTS5） | 中 | 当前 LIKE 搜索在大数据集上性能差，SQLite FTS5 可显著提升搜索体验 |
| 5 | 布尔字段使用 TypeDecorator 自动转换 | 低 | 提升代码可读性，避免 0/1 魔法数字 |
| 6 | 考虑 WAL 模式 | 低 | SQLite WAL 模式提升并发读写性能 |
| 7 | 添加数据库版本迁移工具（Alembic） | 低 | 未来 Schema 变更时避免手动 SQL |

---

## 附录：文件索引

| 文件 | 角色 |
|------|------|
| `construction_agent/database/__init__.py` | Repository 工厂 + 单例 |
| `construction_agent/database/repository.py` | ABC 抽象接口 + 数据类 |
| `construction_agent/database/models.py` | SQLAlchemy ORM 模型 (11 表) |
| `construction_agent/database/sqlite_repository.py` | SQLite Repository 实现 |
| `construction_agent/database/dataset_manager.py` | JSON Repository 实现 |
| `construction_agent/models/accident.py` | Pydantic 域模型 + 枚举 |
| `construction_agent/config.py` | 数据库 URL 配置 |
| `scripts/migrate_to_sqlite.py` | JSON → SQLite 迁移脚本 |
| `scripts/show_schema.py` | Schema 文档生成脚本 |
