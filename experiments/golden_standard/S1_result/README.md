# S1'(FewShot + 基础特征) 在 Golden 300 上的运行结果

## 这是什么

对 `../golden_candidates.jsonl` 的 **300 条**优质事故，用 S1 少样本提示词策略调用 `deepseek-v4-flash`，
**单次调用同时产出【基础特征 31 字段】+【CAICS 5 层 19 类致因编码】**。

## 提示词构造（S1'）

在 S1 原提示词基础上扩展输出 schema，没有改动 S1 的策略内核：

| 组成 | 来源 |
|---|---|
| CAICS 5 层 19 类定义 + 3 个少样本示例 + user 前缀 | `experiment3/runner.py` 的 `STRATEGIES["S1_FewShot"]`（**唯一权威源，按文件路径加载，不手工转录**） |
| 基础特征 31 字段定义（含全部枚举与判定规则） | `step1_basic_extractor.py` 的 `STEP1_TOOL`，直接渲染，保证与主管道 Step1 口径一致 |
| 输出要求 + 字段形状示例 | 本方案新增 |

生成脚本：`scripts/golden_s1/prompt_build.py`；快照见 `system_prompt_snapshot.txt`（约 9.1k 字符）。

## 运行参数

| 项 | 值 |
|---|---|
| 模型 | `deepseek-v4-flash`（读 `.env` 的 `ANTHROPIC_MODEL`） |
| temperature | 0.0 |
| max_tokens | **16384**（DeepSeek 思考块占预算，2048 会返回空文本） |
| 并发 | 4 |
| 事故文本 | 原文整段送入，不截断 |
| 单条调用 | 1 次；失败重试 3 次（指数退避） |
| 断点续跑 | 结果逐条追加到 jsonl，重跑自动跳过已完成 id |

```bash
.venv/bin/python3 scripts/golden_s1/run_S1_golden.py --workers 4            # 全量
.venv/bin/python3 scripts/golden_s1/run_S1_golden.py --only-ids ids.txt     # 指定 id
.venv/bin/python3 scripts/golden_s1/repair_S1_golden.py                     # 补跑异常条
.venv/bin/python3 scripts/golden_s1/analyze_S1_golden.py                    # 出报告
```

## 结果

- **300/300 全部成功**，JSON 解析失败 0，schema 违规 0（首轮 7 条异常已由 `repair_S1_golden.py` 补跑修复）
- CAICS 编码数/条：均值 **6.93**，min 2，max 13
- 耗时 14.7 s/条；tokens 326k prompt + 801k completion

### 编码分布（19 类，全类覆盖）

命中率高的：H1 违反操作规程 94%、M1 现场监管缺失 88%、M2 隐患排查失效 80%；
命中率低的：C2 迟报瞒报 1%、C1 救援迟缓 2%、H4 应急行为失当 6% —— 与文本是否提及救援/上报相符。

### 基础特征字段填充率

满填（100%）：title/accident_type/severity/deaths/injuries/accident_location_detail/
project_type/work_task/trapped_count/collapse_subtype/worker_type/report_type/summary/direct_cause。
低填（合理）：supervision_company 1.3%、weather 9%、fall_height 12.7%、developer 17.3% —— 书籍案例普遍不写监理/气象。

### 质量抽检

- 死亡人数与库内既有字段一致 **300/300（100%）**；严重程度与筛选阶段一致 300/300
- 与库内既有 `caics_causes`（主管道产出）的 Jaccard 中位 **0.750**，无一条零交集
- 随机抽检：编码均有原文依据（如"未关洞口盖板"→F1、"近一年未编施工方案"→M4）

## 产物

| 文件 | 内容 |
|---|---|
| `S1_golden_v2.jsonl` | **最终 300 条结果**（逐条：parsed 编码 + 31 字段 + token/耗时） |
| `S1_golden_v2_results.json` | 同上 + summary |
| `S1_golden_v2_analysis.md` | 编码分布 / 字段填充率 / 异常清单 |
| `S1_golden_v2_review.csv` | **人工审核用**：编码 + 基础字段 + 原文节选 |
| `S1_golden_v2_vs_db.csv` | 与库内既有标注的逐条对照（差异信号，非真值） |
| `system_prompt_snapshot.txt` | 提示词快照 |
| `S1_golden_full*.jsonl` | 首轮 300 条（**含 51 条多事故合并块，已作废**，留档） |
| `S1_golden_v1_removed.jsonl` | 被替换掉的 51 条及其结果 |

## ⚠️ 一次返工记录（重要）

首轮跑完后抽检发现：800 例来源里有 **51 条记录实际塞了两起及以上事故**（"发生简况:"是该书每起事故一条的
字段标签，出现 ≥2 次即多起）。S1 对这类文本会**把两起事故混成一条答案**（实测直接原因取自第二起、类型按第一起判）。

已修 `scripts/screen_golden_candidates.py` 的合并检测（新增 `发生(?:事故)?[简高]况` 边界标记），
重选后 300 条**零合并残留**；249 条文本未变的复用首轮结果，51 条被替换的重跑。
首轮结果保留在 `S1_golden_full*.jsonl` 仅供对照，**不要用于评测**。

## 下一步

1. 人工审核 `S1_golden_v2_review.csv` 的编码是否与原文相符（S1 只给编码不给证据，需回原文核对）
2. 有争议的条目三人标注取多数（复用 `scripts/recover_gt.py` 的 Fleiss κ 流程）
3. 冻结为 CAICS 标准答案数据集，用于评测 Step1/Step2 与六策略对比
