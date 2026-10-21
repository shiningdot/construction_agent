# GT × 六策略 × 四模型：19 类 CAICS 编码 F1

本目录是用 `gt_manual/suntianhao.jsonl` 作为 GT，对六种提示策略 × 四个模型的
19 类 CAICS 编码做逐类 F1 评测的产物。

## 数据与口径

| 项 | 说明 |
|---|---|
| GT | `gt_manual/suntianhao.jsonl`（300 条，平均 7.59 编码/条） |
| 预测 | `strategies/{S0..S5}/{glm5.2,flash,hy3,pro}/results.jsonl` |
| 对齐 | 按 `accident_id` 对齐；24 份预测各 300 条唯一 id，与 GT 完全一致 |
| 去重 | 同一 id 取最后一条成功行（跳过 error / parse_error / schema_errors） |
| 指标 | 逐编码二分类 F1 = 2TP / (2TP + FP + FN)，在 300 条上汇总 |
| Ave | 19 类宏平均 |
| Ave+ | GT 正例数 ≥5 的 18 类宏平均（排除仅 2 个正例的 C2） |
| 样式 | 每列最优加粗、次优加下划线；模型分组之间细线分隔 |

## 文件

| 文件 | 内容 |
|---|---|
| `f1_three_line.png` | 三线表位图（300 dpi，模型分组 × 6 策略 × 19 编码 + Ave/Ave+） |
| `f1_three_line.docx` | 同上，A4 横向可编辑版 |
| `f1_three_line.md` | Markdown 预览（不含加粗/下划线样式） |
| `f1_per_code.csv` | 24 格 × 19 编码的 F1，附逐类 P/R 与 Micro-F1 |
| `gt_code_distribution.csv` | 19 类的 GT 正例数与占比 |
| `eval_report.md` | 数据口径、GT 分布、24 格 Ave+ 排名、逐编码最优结果 |

## 主要结果（Ave+ F1）

| 模型 | 最佳策略 | Ave+ | Ave |
|---|---|---:|---:|
| GLM-5.2 | S5 CAICS-CoT+ | 78.39 | 77.78 |
| DeepSeek-V4-Flash | S5 CAICS-CoT+ | 79.03 | 79.08 |
| 混元 hy3 | S5 CAICS-CoT+ | 75.02 | 71.07 |
| DeepSeek-V4-Pro | S5 CAICS-CoT+ | 80.27 | 78.68 |

四个模型的最佳策略均为 S5；S4 次之。24 格完整排名见 `eval_report.md`。

## 复现

```bash
cd /home/suntianhao/construction_agent
.venv/bin/python3 scripts/golden_s1/eval_gt_f1_table.py \
  --gt experiments/golden_standard/gt_manual/suntianhao.jsonl

# 只想快速重算 CSV/Markdown，不渲染 PNG：
.venv/bin/python3 scripts/golden_s1/eval_gt_f1_table.py \
  --gt experiments/golden_standard/gt_manual/suntianhao.jsonl --skip-png
```

## 校验

- 用同一脚本在 `CAICS致因审核_孙天浩_2026-10-18.json` 上复算 S0/S1，
  与 `strategies/专家GT_指标表.md` 逐项一致（如 S0/GLM Ave=79.43、Ave+=78.29）。
- 对本次 `suntianhao.jsonl` 的 24 × 19 = 456 个 F1 单元格，用独立实现
  （直接按 TP/FP/FN 重算）复核，差异为 0。

## 说明

`suntianhao.jsonl` 仅含 `accident_id` / `title` / `codes` / `provenance`，未声明
`synthetic`。本目录按 `codes` 为正例直接评测；若该文件是模拟专家标注，结论仅作
敏感性分析，不宜作为论文主结果。
