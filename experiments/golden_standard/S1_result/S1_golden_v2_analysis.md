# S1'(FewShot + 基础特征) 在 300 条 Golden 候选上的结果

- 模型：`deepseek-v4-flash`
- 成功 300/300，JSON 解析失败 0，schema 违规 0
- CAICS 编码数/条：均值 6.93，min 2，max 13

## CAICS 编码分布（19 类，按 5 层）

| 层 | 编码 | 命中条数 | 占比 |
|---|---|---:|---:|
| M | M1 | 266 | 88.7% |
| M | M2 | 241 | 80.3% |
| M | M3 | 194 | 64.7% |
| M | M4 | 184 | 61.3% |
| M | M5 | 139 | 46.3% |
| F | F1 | 23 | 7.7% |
| F | F2 | 138 | 46.0% |
| F | F3 | 49 | 16.3% |
| F | F4 | 48 | 16.0% |
| T | T1 | 81 | 27.0% |
| T | T2 | 84 | 28.0% |
| T | T3 | 31 | 10.3% |
| T | T4 | 24 | 8.0% |
| H | H1 | 286 | 95.3% |
| H | H2 | 133 | 44.3% |
| H | H3 | 132 | 44.0% |
| H | H4 | 18 | 6.0% |
| C | C1 | 5 | 1.7% |
| C | C2 | 3 | 1.0% |

## 基础特征字段填充率

| 字段 | 填充条数 | 填充率 |
|---|---:|---:|
| title | 300 | 100.0% |
| date | 300 | 100.0% |
| location | 298 | 99.3% |
| city | 284 | 94.7% |
| accident_type | 300 | 100.0% |
| severity | 300 | 100.0% |
| deaths | 300 | 100.0% |
| injuries | 300 | 100.0% |
| accident_time | 269 | 89.7% |
| district | 137 | 45.7% |
| accident_location_detail | 300 | 100.0% |
| weather | 28 | 9.3% |
| project_name | 288 | 96.0% |
| project_type | 300 | 100.0% |
| building_structure | 80 | 26.7% |
| construction_phase | 288 | 96.0% |
| work_task | 300 | 100.0% |
| contractor | 135 | 45.0% |
| contractor_type | 222 | 74.0% |
| trapped_count | 300 | 100.0% |
| direct_economic_loss | 235 | 78.3% |
| collapse_subtype | 300 | 100.0% |
| fall_height | 27 | 9.0% |
| equipment_involved | 268 | 89.3% |
| material_involved | 285 | 95.0% |
| worker_type | 300 | 100.0% |
| report_type | 300 | 100.0% |
| summary | 300 | 100.0% |
| developer | 60 | 20.0% |
| supervision_company | 4 | 1.3% |
| direct_cause | 300 | 100.0% |

## 事故类型 / 严重程度分布

| 事故类型 | 条数 |
|---|---:|
| 坍塌 | 109 |
| 起重伤害 | 86 |
| 高处坠落 | 36 |
| 土方坍塌 | 24 |
| 触电 | 20 |
| 物体打击 | 8 |
| 中毒窒息 | 7 |
| 火灾爆炸 | 4 |
| 车辆伤害 | 2 |
| 淹溺 | 2 |
| 机械伤害 | 2 |

| 严重程度 | 条数 |
|---|---:|
| 较大事故 | 193 |
| 一般事故 | 96 |
| 重大事故 | 10 |
| 特别重大事故 | 1 |