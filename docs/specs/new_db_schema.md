# CAIEMS 新数据库 Schema 设计

## 设计原则
1. 全链路可追溯: 原始采集→搜索增强→基础提取→致因编码→多模态, 每环节保留
2. 推理可审计: 每个CAICS编码附带完整 evidence + inference + evidence_quality
3. 字段有类型: 明确 SQLite/Python 类型, NULL 和 0/空串 语义区分
4. 与旧DB隔离: 新建 `data/caiems_v2.db`, 不修改旧库

## 表结构 (7张表)

```
caiems_v2.db
├── accidents          主表, 1事故1行, ~55列
├── caics_causes       致因明细, 1:N (每个编码1行+推理链)
├── search_logs        搜索增强日志, 1:1 (仅触发的事故)
├── image_analyses     多模态分析, 1:N (每张图片1行)
├── processing_log     处理流水线, 1:1 (全链路参数)
├── source_urls        多源URL, 1:N
└── pipeline_metadata  全局元数据
```

---

## 表1: accidents (主表)

1事故1行。55列。

### 标识与来源 (6)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| id | TEXT PK | str | NO | MD5(title+date+city) 前12位 |
| title | TEXT | str | NO | |
| source_url | TEXT | str | NO | 原始采集URL |
| source_name | TEXT | str | NO | safehoo/esafety/... |
| crawl_date | TEXT | str | YES | ISO |
| publish_date | TEXT | str | YES | 原文发布日期 |

### 时空 (8)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| date | TEXT | str | YES | ISO YYYY-MM-DD |
| accident_time | TEXT | str | YES | HH:MM |
| province | TEXT | str | YES | 省级 |
| city | TEXT | str | YES | 地级市 |
| district | TEXT | str | YES | 区县 |
| accident_location_detail | TEXT | str | YES | 具体部位 |
| weather | TEXT | str | YES | NULL=正常天气 |
| lighting | TEXT | str | YES | 白天/夜间/黎明/黄昏 (派生) |

### 时间派生 (4, 规则计算)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| day_of_week | TEXT | str | YES | 星期一~日 |
| season | TEXT | str | YES | 春/夏/秋/冬 |
| time_period | TEXT | str | YES | 上午/下午/夜间/凌晨 |
| is_night_shift | INTEGER | bool | NO | 0/1 |

### 工程 (9)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| project_name | TEXT | str | YES | |
| project_type | TEXT | str | NO | 8种枚举 |
| building_structure | TEXT | str | YES | 钢结构/框架/... |
| construction_phase | TEXT | str | NO | 7种枚举 |
| work_task | TEXT | str | YES | 事发作业 |
| developer | TEXT | str | YES | 建设单位 |
| contractor | TEXT | str | YES | 施工单位 |
| contractor_type | TEXT | str | YES | 总包/分包/劳务/无资质 |
| supervision_company | TEXT | str | YES | 监理单位 |

### 事故 (12)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| accident_type | TEXT | str | NO | 11种枚举 |
| severity | TEXT | str | NO | 4级枚举 |
| deaths | INTEGER | int | NO | 默认0 |
| injuries | INTEGER | int | NO | 默认0 |
| trapped_count | INTEGER | int | NO | 默认0 |
| direct_economic_loss | REAL | float | YES | 万元 |
| collapse_subtype | TEXT | str | YES | 10种枚举 |
| fall_height | REAL | float | YES | 米 |
| equipment_involved | TEXT | str | YES | 自由文本 |
| material_involved | TEXT | str | YES | 自由文本 |
| worker_type | TEXT | str | YES | 自由文本(待Phase B) |
| direct_cause | TEXT | str | YES | Step2 primary_cause |

### 元数据 (6)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| report_type | TEXT | str | NO | 新闻报道/调查报告/政府通报/教材/其他 |
| text_type | TEXT | str | NO | single/multi |
| classification_verdict | TEXT | str | NO | rule_high_construction/confirmed_construction/... |
| classification_confidence | TEXT | str | NO | high/medium/low |
| source_count | INTEGER | int | NO | 多源URL数 |
| summary | TEXT | str | NO | 100-200字 |

### 原始文本 (3, 全链路保留!)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| raw_text | TEXT | str | NO | 原始采集全文 |
| search_enriched_text | TEXT | str | YES | 搜索增强后文本 |
| search_performed | INTEGER | bool | NO | 0/1 |

### 处理追踪 (4)
| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| pipeline_version | TEXT | str | NO | "caics_v1.0" |
| step1_duration_ms | INTEGER | int | YES | |
| step2_duration_ms | INTEGER | int | YES | |
| created_at | TEXT | str | NO | ISO |

---

## 表2: caics_causes (致因明细)

1事故 → N个编码行。每个编码保留完整推理链。

| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| id | INTEGER PK | int | NO | 自增 |
| accident_id | TEXT FK | str | NO | |
| code | TEXT | str | NO | M1~C2 |
| name | TEXT | str | NO | 编码中文名 |
| evidence | TEXT | str | NO | **原文证据引用** |
| inference | TEXT | str | NO | **推理链: 证据→结论** |
| evidence_quality | TEXT | str | NO | **direct_quote / indirect_infer / weak_infer** |
| sort_order | INTEGER | int | NO | M=100-104, F=200-203, T=300-303, H=400-403, C=500-501 |

**evidence_quality 定义**:
- `direct_quote`: evidence 包含原文具体语句
- `indirect_infer`: inference 含"推断""表明"等推理词
- `weak_infer`: evidence/inference 含"原文未直接描述"

---

## 表3: search_logs (搜索增强日志)

每事故1行。仅触发搜索的有记录。

| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| accident_id | TEXT FK | str | NO | |
| search_performed | INTEGER | bool | NO | 1 |
| queries_used | TEXT | str | YES | JSON数组 |
| total_results | INTEGER | int | YES | |
| same_accident_count | INTEGER | int | YES | 过滤后 |
| same_accident_snippets | TEXT | str | YES | JSON数组 [{title,snippet,...}] |
| engine | TEXT | str | NO | "qianfan" |
| error | TEXT | str | YES | |
| created_at | TEXT | str | NO | |

---

## 表4: image_analyses (多模态分析)

每张已分析图片1行。

| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| id | INTEGER PK | int | NO | |
| accident_id | TEXT FK | str | NO | |
| local_path | TEXT | str | NO | |
| source_url | TEXT | str | YES | |
| phase1_scene_type | TEXT | str | YES | 文档截图/建筑废墟/救援现场/... |
| phase1_objects | TEXT | str | YES | JSON数组 |
| phase1_people | TEXT | str | YES | |
| phase1_damage | TEXT | str | YES | |
| phase1_weather | TEXT | str | YES | |
| phase1_raw_response | TEXT | str | YES | VLM Phase1完整回复 |
| phase2_consistency | TEXT | str | YES | 一致/部分一致/矛盾 |
| phase2_confidence | TEXT | str | YES | 强佐证/中等佐证/弱佐证/矛盾 |
| phase2_new_info | TEXT | str | YES | JSON数组 |
| phase2_raw_response | TEXT | str | YES | VLM Phase2完整回复 |
| vlm_model | TEXT | str | NO | "qwen-vl-max" |
| duration_ms | INTEGER | int | YES | |
| created_at | TEXT | str | NO | |

---

## 表5: processing_log (处理流水线)

每事故1行。全链路关键参数。

| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| accident_id | TEXT FK | str | NO | |
| pipeline_version | TEXT | str | NO | "caics_v1.0" |
| text_type | TEXT | str | NO | LLM判定的文本类型 |
| text_type_confidence | TEXT | str | NO | high/medium/low |
| text_type_reason | TEXT | str | NO | |
| classification_verdict | TEXT | str | NO | |
| classification_rule_result | TEXT | str | YES | JSON (classify_v2输出) |
| classification_llm_result | TEXT | str | YES | JSON (LLM输出, 如有) |
| search_triggered | INTEGER | bool | NO | |
| step1_success | INTEGER | bool | NO | |
| step1_fields_extracted | INTEGER | int | YES | |
| step1_duration_ms | INTEGER | int | YES | |
| step2_success | INTEGER | bool | NO | |
| step2_cause_count | INTEGER | int | YES | |
| step2_duration_ms | INTEGER | int | YES | |
| image_count_analyzed | INTEGER | int | YES | |
| total_duration_ms | INTEGER | int | YES | |
| error_message | TEXT | str | YES | |
| created_at | TEXT | str | NO | |

---

## 表6: source_urls (多源URL)

| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| id | INTEGER PK | int | NO | |
| accident_id | TEXT FK | str | NO | |
| url | TEXT | str | NO | |
| source_name | TEXT | str | YES | |
| created_at | TEXT | str | NO | |
| UNIQUE(accident_id, url) | | | | |

---

## 表7: pipeline_metadata (全局)

| 列名 | SQLite | Python | NULL | 说明 |
|------|--------|--------|:--:|------|
| key | TEXT PK | str | NO | |
| value | TEXT | str | NO | |
| created_at | TEXT | str | NO | |

初始化插入:
- `pipeline_version`: "caics_v1.0"
- `total_raw_input`: "1842"
- `started_at`: ISO时间戳

---

## 文件

```
data/caiems_v2.db             ← 新库
data/caiems.db                ← 旧库(不动)
scripts/create_db_v2.py       ← Schema创建脚本
```

## 与旧库的关键差异

| | 旧库 caiems.db | 新库 caiems_v2.db |
|---|---|---|
| 致因编码 | factors表(自由category) + hfacs_details | caics_causes(19类+推理链+证据质量) |
| CAICS | 不存在 | 核心字段 |
| 搜索日志 | 4张空表, 未使用 | search_logs(有数据) |
| 多模态 | 仅存储图片路径 | image_analyses(两阶段VLM分析) |
| 处理追踪 | processing_history全空 | processing_log(填满) |
| 原始文本 | raw_input(有时为空) | raw_text + search_enriched_text |
| 来源追溯 | 95% source为空 | source_url + source_name必填 |
