# S1'(FewShot + 基础特征) 在 300 条 Golden 候选上的结果

- 模型：`deepseek-v4-flash`
- 成功 300/300，JSON 解析失败 0，schema 违规 0
- CAICS 编码数/条：均值 6.80，min 2，max 13

## CAICS 编码分布（19 类，按 5 层）

| 层 | 编码 | 命中条数 | 占比 |
|---|---|---:|---:|
| M | M1 | 264 | 88.0% |
| M | M2 | 240 | 80.0% |
| M | M3 | 195 | 65.0% |
| M | M4 | 173 | 57.7% |
| M | M5 | 136 | 45.3% |
| F | F1 | 37 | 12.3% |
| F | F2 | 136 | 45.3% |
| F | F3 | 44 | 14.7% |
| F | F4 | 52 | 17.3% |
| T | T1 | 68 | 22.7% |
| T | T2 | 79 | 26.3% |
| T | T3 | 30 | 10.0% |
| T | T4 | 24 | 8.0% |
| H | H1 | 283 | 94.3% |
| H | H2 | 115 | 38.3% |
| H | H3 | 138 | 46.0% |
| H | H4 | 18 | 6.0% |
| C | C1 | 6 | 2.0% |
| C | C2 | 3 | 1.0% |

## 基础特征字段填充率

| 字段 | 填充条数 | 填充率 |
|---|---:|---:|
| title | 300 | 100.0% |
| date | 299 | 99.7% |
| location | 299 | 99.7% |
| city | 286 | 95.3% |
| accident_type | 300 | 100.0% |
| severity | 300 | 100.0% |
| deaths | 300 | 100.0% |
| injuries | 300 | 100.0% |
| accident_time | 262 | 87.3% |
| district | 136 | 45.3% |
| accident_location_detail | 300 | 100.0% |
| weather | 27 | 9.0% |
| project_name | 293 | 97.7% |
| project_type | 300 | 100.0% |
| building_structure | 79 | 26.3% |
| construction_phase | 287 | 95.7% |
| work_task | 300 | 100.0% |
| contractor | 135 | 45.0% |
| contractor_type | 225 | 75.0% |
| trapped_count | 300 | 100.0% |
| direct_economic_loss | 229 | 76.3% |
| collapse_subtype | 300 | 100.0% |
| fall_height | 38 | 12.7% |
| equipment_involved | 259 | 86.3% |
| material_involved | 286 | 95.3% |
| worker_type | 300 | 100.0% |
| report_type | 300 | 100.0% |
| summary | 300 | 100.0% |
| developer | 52 | 17.3% |
| supervision_company | 4 | 1.3% |
| direct_cause | 300 | 100.0% |

## 事故类型 / 严重程度分布

| 事故类型 | 条数 |
|---|---:|
| 坍塌 | 105 |
| 起重伤害 | 72 |
| 高处坠落 | 51 |
| 土方坍塌 | 25 |
| 物体打击 | 13 |
| 触电 | 12 |
| 中毒窒息 | 8 |
| 火灾爆炸 | 6 |
| 车辆伤害 | 3 |
| 机械伤害 | 3 |
| 淹溺 | 2 |

| 严重程度 | 条数 |
|---|---:|
| 较大事故 | 183 |
| 一般事故 | 107 |
| 重大事故 | 9 |
| 特别重大事故 | 1 |