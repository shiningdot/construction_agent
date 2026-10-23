# GT 评测报告：suntianhao.jsonl

## 1. 数据与口径

- GT：`suntianhao.jsonl`，300 条事故，平均 7.59 个编码/条。
- 该文件仅含 accident_id / title / codes / provenance，未声明 synthetic；本报告按 codes 为正例直接评测。若它是模拟专家标注，结论仅作敏感性分析。
- 预测：`experiments/golden_standard/strategies/{S0..S5}/{glm5.2,flash,hy3,pro}/results.jsonl`。
- 去重：同一 accident_id 取最后一条成功行；六策略 × 四模型 × 300 条全部对齐。
- 指标：逐编码二分类 F1 = 2TP / (2TP + FP + FN)，在 300 条上汇总。
- Ave：19 类宏平均；Ave+：GT 正例数 ≥5 的 18 类宏平均（排除 C2）。
- Ave17：排除 C1、C2 后的 17 类宏平均（M1–M5、F1–F4、T1–T4、H1–H4），论文纵向表（Letter，15.24 cm 正文宽）使用该口径。
- 每列最优加粗、次优加下划线（DOCX/PNG 中体现）。

## 2. GT 编码分布

| 编码 | 正例数 | 占比 | 计入 Ave+ | 计入 Ave17 |
|---|---:|---:|:--:|:--:|
| M1 | 229 | 76.33% | 是 | 是 |
| M2 | 237 | 79.00% | 是 | 是 |
| M3 | 212 | 70.67% | 是 | 是 |
| M4 | 198 | 66.00% | 是 | 是 |
| M5 | 105 | 35.00% | 是 | 是 |
| F1 | 73 | 24.33% | 是 | 是 |
| F2 | 154 | 51.33% | 是 | 是 |
| F3 | 72 | 24.00% | 是 | 是 |
| F4 | 97 | 32.33% | 是 | 是 |
| T1 | 74 | 24.67% | 是 | 是 |
| T2 | 124 | 41.33% | 是 | 是 |
| T3 | 37 | 12.33% | 是 | 是 |
| T4 | 16 | 5.33% | 是 | 是 |
| H1 | 229 | 76.33% | 是 | 是 |
| H2 | 138 | 46.00% | 是 | 是 |
| H3 | 222 | 74.00% | 是 | 是 |
| H4 | 17 | 5.67% | 是 | 是 |
| C1 | 40 | 13.33% | 是 | 否 |
| C2 | 2 | 0.67% | 否 | 否 |

## 3. 各模型最佳策略（按 Ave+ F1）

| 模型 | 最佳策略 | Ave+ | Ave | 该模型六策略 Ave+ 均值 |
|---|---|---:|---:|---:|
| GLM-5.2 | S5 CAICS-CoT+ | 78.39 | 77.78 | 68.99 |
| DeepSeek-V4-Flash | S5 CAICS-CoT+ | 79.03 | 79.08 | 68.76 |
| 混元 hy3 | S5 CAICS-CoT+ | 75.02 | 71.07 | 63.95 |
| DeepSeek-V4-Pro | S5 CAICS-CoT+ | 80.27 | 78.68 | 67.73 |

若按论文纵向表使用的 Ave17（排除 C1/C2）排序：

| 模型 | 最佳策略 | Ave17 |
|---|---|---:|
| GLM-5.2 | S5 CAICS-CoT+ | 80.45 |
| DeepSeek-V4-Flash | S5 CAICS-CoT+ | 81.28 |
| 混元 hy3 | S5 CAICS-CoT+ | 77.48 |
| DeepSeek-V4-Pro | S5 CAICS-CoT+ | 82.20 |

注意：排除 C1/C2 后，混元 hy3 由 Ave+ 口径下的 S4 > S5 变为 S5 > S4（77.48 vs 76.10），正文需说明该口径差异。

## 4. 24 格按 Ave+ F1 排名

| 排名 | 模型 | 策略 | Ave+ | Ave |
|---:|---|---|---:|---:|
| 1 | DeepSeek-V4-Pro | S5 CAICS-CoT+ | 80.27 | 78.68 |
| 2 | DeepSeek-V4-Flash | S5 CAICS-CoT+ | 79.03 | 79.08 |
| 3 | GLM-5.2 | S5 CAICS-CoT+ | 78.39 | 77.78 |
| 4 | 混元 hy3 | S5 CAICS-CoT+ | 75.02 | 71.07 |
| 5 | DeepSeek-V4-Pro | S4 CAICS-CoT | 73.79 | 73.42 |
| 6 | 混元 hy3 | S4 CAICS-CoT | 73.52 | 73.87 |
| 7 | GLM-5.2 | S4 CAICS-CoT | 72.68 | 72.37 |
| 8 | DeepSeek-V4-Flash | S4 CAICS-CoT | 72.18 | 69.78 |
| 9 | DeepSeek-V4-Flash | S2 自动链式思维 | 67.58 | 66.66 |
| 10 | GLM-5.2 | S3 规划与求解 | 67.50 | 67.45 |
| 11 | DeepSeek-V4-Flash | S3 规划与求解 | 66.13 | 65.66 |
| 12 | GLM-5.2 | S1 少样本 | 65.90 | 65.94 |
| 13 | GLM-5.2 | S2 自动链式思维 | 65.77 | 65.82 |
| 14 | DeepSeek-V4-Flash | S1 少样本 | 65.65 | 64.30 |
| 15 | DeepSeek-V4-Pro | S3 规划与求解 | 64.01 | 63.65 |
| 16 | GLM-5.2 | S0 零样本 | 63.71 | 63.87 |
| 17 | DeepSeek-V4-Pro | S1 少样本 | 63.27 | 64.15 |
| 18 | DeepSeek-V4-Pro | S2 自动链式思维 | 63.20 | 64.08 |
| 19 | DeepSeek-V4-Flash | S0 零样本 | 61.99 | 61.36 |
| 20 | DeepSeek-V4-Pro | S0 零样本 | 61.81 | 62.77 |
| 21 | 混元 hy3 | S3 规划与求解 | 60.64 | 61.66 |
| 22 | 混元 hy3 | S2 自动链式思维 | 59.08 | 60.18 |
| 23 | 混元 hy3 | S1 少样本 | 58.45 | 58.88 |
| 24 | 混元 hy3 | S0 零样本 | 56.99 | 58.20 |

## 5. 逐编码最优结果

| 编码 | GT 正例 | 最优 F1 | 模型 | 策略 |
|---|---:|---:|---|---|
| M1 | 229 | 86.58 | GLM-5.2 | S5 CAICS-CoT+ |
| M2 | 237 | 88.72 | GLM-5.2 | S5 CAICS-CoT+ |
| M3 | 212 | 83.14 | DeepSeek-V4-Pro | S4 CAICS-CoT |
| M4 | 198 | 87.98 | DeepSeek-V4-Flash | S5 CAICS-CoT+ |
| M5 | 105 | 87.07 | DeepSeek-V4-Flash | S5 CAICS-CoT+ |
| F1 | 73 | 82.72 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| F2 | 154 | 87.57 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| F3 | 72 | 73.74 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| F4 | 97 | 80.95 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| T1 | 74 | 78.43 | DeepSeek-V4-Pro | S3 规划与求解 |
| T2 | 124 | 83.79 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| T3 | 37 | 85.71 | GLM-5.2 | S5 CAICS-CoT+ |
| T4 | 16 | 85.71 | 混元 hy3 | S5 CAICS-CoT+ |
| H1 | 229 | 87.07 | DeepSeek-V4-Pro | S4 CAICS-CoT |
| H2 | 138 | 82.00 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| H3 | 222 | 85.32 | GLM-5.2 | S5 CAICS-CoT+ |
| H4 | 17 | 85.71 | DeepSeek-V4-Flash | S0 零样本 |
| C1 | 40 | 47.46 | DeepSeek-V4-Pro | S5 CAICS-CoT+ |
| C2 | 2 | 80.00 | DeepSeek-V4-Flash | S5 CAICS-CoT+ |

## 6. 说明

- `suntianhao.jsonl` 中 C2 仅 2 个正例，Ave+ 已按 ≥5 规则排除；Ave 仍按 19 类计算。
