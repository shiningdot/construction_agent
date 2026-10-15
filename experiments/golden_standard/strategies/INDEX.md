# golden_standard 结果归档说明

> 实验：**策略 × 模型** 交叉矩阵。样本为新筛选的 300 条
> （`experiments/golden_standard/golden_candidates.jsonl`）。
> 所有策略统一输出 `codes`（CAICS 5 层 19 类）+ 31 个基础特征；`temperature=0`、
> `max_tokens=16384`、原文整段送入；四模型串行、每个模型 `--workers 3`。

## 当前状态与暂缓项（2026-10-15）

- **已完成**：S0′、S1′ 各 4 模型全部 300/300；S2′ 的 glm5.2 与 flash 已 300/300。
- **DeepSeek 暂缓**（`deepseek-v4-flash` / `deepseek-v4-pro`）：2026-10-15 DeepSeek 官网服务器
  故障。S3′/S4′/S5′ 的 flash 与 pro 两格**暂不跑**，等官方恢复后补。
  S2′ 的 flash 已在故障前跑满，pro 未跑。
- **正在跑**：仅用 glm-5.2 与 hy3 推进 S2′（hy3 剩余）→ S3′ → S4′ → S5′。
- **补跑方式**（DeepSeek 恢复后，无需任何额外准备）：
  ```bash
  MODELS="flash pro" bash scripts/golden_s1/run_matrix_remaining.sh S2 S3 S4 S5
  ```
  断点续跑只跳过「完全成功」的条目，已跑满的格会被秒过。
- `run_s0_all_models.sh` 支持 `MODELS` 环境变量筛选模型（`glm` / `flash` / `hy3` / `pro`），
  默认全跑。服务商故障期间就用它挑可用模型。

## 目录布局

```
experiments/golden_standard/
├── golden_candidates.jsonl          # 300 条输入（唯一权威副本）
├── gt/                              # 基础特征 GT（3900 格已定）+ 致因标注
├── gt_manual/                       # 用户 2026-10-15 手工标注的 GT（源目录只读复制而来）
│   ├── cause_gt.jsonl               # ★ 致因 GT，300 条（评测用这个）
│   ├── gt_draft.jsonl               # ★ 基础特征 GT，300 条 × 13 字段
│   ├── gt_causes_full.jsonl         #   用户导出原件（294 条）
│   ├── gt_causes_draft.jsonl        #   标注前的草稿（300 条，补齐 6 条时用）
│   ├── cause_annotations.jsonl      #   人工裁决流水（932 条）
│   └── *.html / causes_threeway.json#   标注页与三方投票原始数据
├── {glm5.2,flash,hy3,pro}/          # S1 的原始归档（冻结，含 review CSV / diff HTML）
│   ├── golden_candidates.jsonl
│   └── S1_golden_results.jsonl
└── strategies/                      # 策略 × 模型矩阵（统一布局）
    ├── INDEX.md                     # 本文件
    ├── STRATEGY_MATRIX.md           # 矩阵总表：模型 × 策略，随各策略完成逐行补写
    ├── EVAL_CAUSES.md / .csv        # ★ 致因 F1 评测结果（由 eval_matrix.py 生成）
    ├── MATRIX_REPORT.md             # 自动数值报表（编码数/一致度/19 类包含率）
    ├── MATRIX_STATUS.md             # 完成度巡检快照
    ├── S0_vs_S1.md                  # 逐类完整对照（由 compare_strategies.py 生成）
    ├── _pilots/{策略}/{模型}/       # 冒烟/试点结果，不参与矩阵统计
    ├── {S0..S5}/
    │   └── {glm5.2,flash,hy3,pro}/
    │       └── results.jsonl        # 该格的全部结果
    └── S1/                          # ← 统一布局的 S1
        └── {glm5.2,flash,hy3,pro}/results.jsonl
                                     # 符号链接 → ../../../{模型}/S1_golden_results.jsonl
```

**为什么 S1 是符号链接**：S1 的 1200 条数据已产出且冻结（其提示词拼装结果 md5 为
`6f628981`，重构后逐字节未变）。复制一份会产生双份数据、日后可能漂移；符号链接
让矩阵只认一种路径，同时保持「S1 归档在原处」这一事实。**不要删除顶层四个模型目录。**

## results.jsonl 的行结构

每行一次调用的结果。**文件是追加写的**——重跑补齐会在同 id 后追加新行，
旧失败行仍留在文件里，所以**行数不等于条数**。

| 字段 | 含义 |
|---|---|
| `id` / `title` / `tier` / `source_name` / `text_len` | 输入侧标识 |
| `parsed` | 模型输出的 JSON（`codes` + 31 基础特征） |
| `parse_error` / `schema_errors` / `error` | 三类失败标记；三者皆空才算**成功行** |
| `hit_cap` | 是否撞顶（completion == max_tokens） |
| `prompt_tokens` / `completion_tokens` / `elapsed_sec` / `attempts` | 开销 |
| `reason_chars` | JSON 之前的行文长度——用于判断模型是否真的先写了计划/推理 |
| `n_explicit` / `n_inferred` | S4′/S5′ 的显式 vs 推断标记计数 |
| `missing_fields` / `repaired_fields` | 缺失的字段名；本次「补全重试」补回了几个（见下） |
| `layer_codes` / `rules_fired` / `layers_failed` | **仅 S5′**：分层编码、代码级规则命中记录、失败层 |

> ⚠️ `reason_chars` / `n_explicit` / `n_inferred` 三个派生字段是 2026-10-15 加进运行器的。
> S0′/S1′ 的行没有这些字段（其数据早于该改动），分析时按缺失处理即可。

### 关于「模型漏写字段」与 `repaired_fields`

要求先写长文推理再输出 JSON 的策略（S2′/S3′/S4′/S5′）里，模型偶尔会在最终 JSON 中
**整块跳过字段**（实测缺失的是 `accident_type`、`severity`、`deaths`、`injuries` 这一组连续字段）。
发生率：S0′×glm 0/300、S2′×glm 2/300、S2′×flash 18/300（6.0%）、S3′×glm 9/119（7.6%）——
**长文推理是其触发条件**，零样本的 S0′ 完全不出现。这是模型输出行为，不是截断、不是解析错误、
也不是输入决定的（同一事故重复 8 次仅 1 次复现）。

2026-10-15 起运行器加了**补全重试**（`scripts/golden_s1/completion_repair.py`）：解析成功但字段不全时，
把模型自己的残缺输出回灌给它、点名缺失字段、要求重发一份完整 JSON，只补一次。
**不改动任何提示词**，六个策略的 system 逐字节不变。因此：

- `repaired_fields > 0` 的行 = 该行触发过补全重试并补回了这么多字段（属于正常流程，不是异常）。
- 仍拿到完整 32 键的最终结果，与「下一轮重跑补漏」等效但省一轮。
- 该机制上线**之前**跑的行（S0′/S1′ 全部、S2′ 全部、S3′ 前 ~130 行）没有这两个字段，
  其偶发漏写由下一轮续跑补上——最终数据集同样每 id 一份完整 JSON。

**取数的正确姿势**：按 `id` 去重，**同 id 取最后一条成功行**。
`scripts/golden_s1/verify_matrix.py` 与 `compare_strategies.py` 都已按此实现。

## 六个策略的设计定位（能力阶梯三条正交轴）

| 策略 | 轴A 示例 | 轴B 推理结构 | 轴C 领域知识注入（=推断授权） |
|---|:--:|---|---|
| S0′ 零样本 | ✗ | ✗ | 完整分类体系，**无推断授权** |
| S1′ 少样本 | 3 例 | ✗ | 同上 |
| S2′ 逐层推理 | ✗ | 人工给定因果链 M→F→T→H→C | 同上 |
| S3′ 计划与求解 | ✗ | **模型自主规划**（真 Plan-and-Solve） | 同上 |
| S4′ CAICS-CoT | ✗ | 人工设计两阶段问题链（对齐 HFACS-CoT） | **19 类 ×（线索 + 推断授权）** |
| S5′ CAICS-CoT+ | ✗ | 人工设计 + **5 层独立调用 + 代码级门控** | 同上 **+ 建筑施工安全领域知识** |

S3′/S4′/S5′ 是本轮重设计的（正文在 `scripts/golden_s1/prompts/{S3,S4,S5}.md`，
经 `prompt_build.py` 的覆盖机制生效，**未改动上游 run_S*.py**）。
两处关键设计决定见 `STRATEGY_MATRIX.md` 与各提示词文件顶部的注释：
其一，S4′ 刻意**不复刻**对标论文的疑问式条件检查（我们自己的实验二已证明那会触发
词汇门控效应）；其二，S5′ 的门控与互斥约束**由代码裁定而非提示词**，
最终 `codes` 由 `s5_layers.apply_programmatic_rules()` 覆写以保证可复现。

## 巡检与复现

```bash
# 一眼看全矩阵完成度（含未开跑/进行中/待补）
python3 scripts/golden_s1/verify_matrix.py

# 跑某个策略 × 四个模型（串行，每模型 3 并发）
bash scripts/golden_s1/run_s0_all_models.sh S3

# 只用部分模型（服务商故障期间）
MODELS="glm hy3" bash scripts/golden_s1/run_matrix_remaining.sh S3 S4 S5

# 致因 F1 评测（需 gt_manual/cause_gt.jsonl）
python3 scripts/golden_s1/eval_matrix.py

# 策略间逐条一致度（不需要人工真值）
python3 scripts/golden_s1/compare_strategies.py --pairs S0,S1 \
    --md experiments/golden_standard/strategies/S0_vs_S1.md
```

## GT 与评测口径

- **致因 GT**：`gt_manual/cause_gt.jsonl`（300 条）。用户的导出原件 `gt_causes_full.jsonl`
  只有 294 条——导出页只遍历「有待裁格」的事故，漏掉了 6 条三方完全一致的；
  `merge_manual_cause_gt.py` 已从草稿补齐，并用 `gt_source` 标明来源。
- **基础特征 GT**：`gt_manual/gt_draft.jsonl`，300 条 × **13 个核心客观特征**。
  ⚠️ 模型输出 31 个字段，但 GT 只敲定了其中 13 个，故基础特征准确率只在这 13 个上算。
- **对齐**：GT 与预测都按 `accident_id` 键控，但**行序不同**，评测前必须按 id 排序做位置对齐
  （`runner.compute_metrics` 是按位置对齐的，顺序错了会静默算错）。
- **主口径**：`runner.compute_metrics`（与论文已报告数字同源）；`eval_matrix.py` 同时用
  自实现的一份交叉校验，实测最大偏差 0.007。
