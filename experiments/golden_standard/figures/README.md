# 论文核心三图（golden_300）

本目录包含「300 条施工事故 × 6 策略 × 4 模型」实验的论文核心 3 图，统一使用
**专家审核 GT**（`CAICS致因审核_孙天浩_2026-10-18.json`，平均 7.85 编码/条），
不使用旧的 `cause_gt` / 《最终说明》口径。

## 文件

| 文件 | 内容 |
|---|---|
| `figure_data.json` | 绘图唯一数据源：24 格逐类 F1/R/P/Acc、Ave/Ave+、Micro-F1、Exact、13 字段准确率、GT 统计与校验摘要 |
| `fig1_strategy_model_aveplus_f1.{pdf,png}` | 6 策略 × 4 模型 Ave+ F1 热力图（含均值行列） |
| `fig2_class_strategy_f1.{pdf,png}` | 19 类 × 6 策略 F1 热力图（4 模型均值，按 M/F/T/H/C 分层，右侧标 GT 正例数） |
| `fig3_strategy_multimetric.{pdf,png}` | 策略级 2×3 多指标对比（Ave+ F1/R/P、Micro-F1、Exact、13 字段准确率） |
| `fig4_cause_distribution.{pdf,png}` | 19 类致因频率分布（水平堆叠条形图：蓝=事故致因，橙=非致因；左侧只显示编码） |

## 复现

```bash
# 1. 重算数据并校验（工作树）
/home/suntianhao/construction_agent/.venv/bin/python \
  scripts/golden_s1/build_figure_data.py \
  --data-root experiments/golden_standard \
  --out-dir experiments/golden_standard/figures \
  --check-md experiments/golden_standard/strategies/专家GT_指标表.md \
  --strict

# 2. 绘图
MPLBACKEND=Agg /home/suntianhao/construction_agent/.venv/bin/python \
  scripts/golden_s1/plot_paper_figures.py \
  --data-json experiments/golden_standard/figures/figure_data.json \
  --out-dir experiments/golden_standard/figures \
  --formats pdf,png --dpi 300
```

只读快照也可作为数据源：

```bash
/home/suntianhao/construction_agent/.venv/bin/python \
  scripts/golden_s1/build_figure_data.py \
  --data-root /home/suntianhao/construction_agent/experiments/experiment_6strategies/golden_300 \
  --out-dir /tmp/golden300_figures --strict
```

工作树与快照的 `checks.digest_cells_sha256` 已验证完全一致：
`a5323e126bf31606ac5dbb2d41fdbda7b783fc2685e7a9e3be3c86d235c3de17`。

## 口径与注意事项

- **Ave**：19 类宏平均；**Ave+**：GT 正例 ≥5 的 18 类（排除 C2，n=4）。
- **准确率**：逐类 `(TP+TN)/N`，不是 runner 的 `TP/N`（后者等同召回）。
- **Micro-F1**：汇总所有（事故, 类别）的 TP/FP/FN。
- **Exact**：预测编码集合与 GT 完全相同的比例。
- **基础特征准确率**：只覆盖 13 个字段（GT 未敲定其余 18 个字段），不能表述为 31 字段全量准确率。
- **图 2** 是 4 模型均值，模型间差异见图 3 的散点；三角标记表示 S4′/S5′ 相对 S0′–S3′ 均值变化 ≥2 个百分点。
- **图 4** 使用专家 GT 的逐类正例数，按 CAICS 五层（M/F/T/H/C）分组，层内按频次降序；左侧只显示编码，图内底部英文图注说明「1 = contributing factor，0 = opposite」，每行总长固定为 300。
- **图 3** 的 y 轴为非零基线（a–d 60–100，e 0–30，f 90–100），图注需说明；误差棒是 4 个模型间的 SEM，不是重复运行的方差（每格只运行一次）。
- **类别英文名**为初译，集中在 `scripts/golden_s1/build_figure_data.py` 的 `CLASS_DISPLAY`，审校后可直接替换并重绘。
- **S4′/S5′ 的 C1 解读**：S4′ 的 C1 接近崩塌（7.3%），S5′ 恢复至 51.1%，不要将两者等同。

## 图 4 图注（可直接用于正文）

**English.** Fig. 4. Frequency distribution of CAICS cause codes: A value of 1 indicates that the code was identified as a contributing factor to the accident, while a value of 0 indicates the opposite. Codes are grouped by CAICS layer (M/F/T/H/C) and sorted by frequency within each layer.

**中文.** 图 4. CAICS 致因编码的频率分布：值为 1 表示该编码被认定为事故的促成因素，值为 0 表示相反情况。编码按 CAICS 层级（M/F/T/H/C）分组，层内按频次降序排列。

## 关键数值

Ave+ F1（%），与 `../strategies/专家GT_指标表.md` 一致：

| 策略 | GLM-5.2 | Flash | hy3 | Pro | 均值 |
|---|---:|---:|---:|---:|---:|
| S0′ | 78.29 | 80.79 | 74.62 | 80.97 | 78.67 |
| S1′ | 81.35 | 87.34 | 75.14 | 85.14 | 82.24 |
| S2′ | 80.90 | 84.80 | 75.35 | 83.55 | 81.15 |
| S3′ | 79.98 | 84.87 | 77.40 | 84.25 | 81.62 |
| S4′ | 77.55 | 74.50 | 76.64 | 76.93 | 76.41 |
| S5′ | 77.08 | 77.98 | 75.55 | 78.52 | 77.28 |
