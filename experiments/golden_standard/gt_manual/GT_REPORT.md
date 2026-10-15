# 基础特征 GT 报告（已完成）

> **状态：全部 3900 格已确定，无待人工项。`gt_draft.jsonl` 即最终 GT。**

- 事故 300 条 × 客观特征 13 个 = 3900 格
- **三方一致，直接确定 3690 格**（`three_agree`）
- **三方中有两方相同，采用众数值自动确定 196 格**（`majority_auto`）
- **三方互异但规则可判 10 格**（`rule_resolved`）：`accident_time` 取三方中唯一合法 HH:MM 值，无合法值置空（库内有把"下午"填进时刻字段的）；`city` 取规范地级名称（自治州/地区/盟优先，同级取信息最全），脱敏占位符（××县）与县级名置空。每格的判定说明记在 `resolution_notes`。
- **三方互异且规则判不了，人工裁决 4 格**（`human_decision`），来源已记入 `resolution_notes`：
  安徽防汛调度大楼 `collapse_subtype`=脚手架坍塌（自填）、武进县马杭小学 `accident_type`=高处坠落（取 pro）、
  安康县商业局 3 号住宅楼 `accident_type`=高处坠落（取 pro）、秦山核电站 `project_type`=其他/未知（取库内）

| provenance | 格数 | 占比 |
|---|---:|---:|
| `three_agree` | 3690 | 94.6% |
| `majority_auto` | 196 | 5.0% |
| `rule_resolved` | 10 | 0.3% |
| `human_decision` | 4 | 0.1% |
| **合计** | **3900** | **100%** |

> ⚠️ 重跑 `scripts/golden_s1/build_gt.py` 会**从零重建 `gt_draft.jsonl`，覆盖掉人工裁决的 4 格**。
> 如需重跑，先备份本文件；或保留标注文件后用 `apply_annotations.py` 合回。

## 剔除的字段（不在本次 GT 范围）

**名称类文本**：工程名称 `project_name`、建设单位 `developer`、施工单位 `contractor`、监理单位 `supervision_company` —— 这 4 个在 24 字段口径下贡献 151 条三方互异（占 39%）。
**书写类**：摘要 `summary`、直接原因 `direct_cause`、天气 `weather`、具体部位 `accident_location_detail`、区县 `district`、标题 `title`、报告类型 `report_type`。
**其他客观但零收益**：施工阶段/作业类型/伤亡工种/承包类型/涉及设备/涉及材料/建筑结构类型（保留会各增 11–70 条互异）。

被剔除字段的模型输出仍保留在 `S1_golden_results.jsonl` / `pro/S1_golden_results.jsonl`，需要时可随时取用。字段取舍依据见 `scripts/golden_s1/inventory_fields.py` 的全字段清点。

## 各字段情况

| 字段 | 三方一致 | 两方一致(自动) | 规则判定 | 三方互异(人工) | 自动率 |
|---|---:|---:|---:|---:|---:|
| 事故类型 `accident_type` | 265 | 33 | 0 | 2 | 99.3% |
| 严重程度 `severity` | 300 | 0 | 0 | 0 | 100.0% |
| 工程类型 `project_type` | 269 | 30 | 0 | 1 | 99.7% |
| 坍塌子类 `collapse_subtype` | 264 | 35 | 0 | 1 | 99.7% |
| 死亡人数 `deaths` | 300 | 0 | 0 | 0 | 100.0% |
| 受伤人数 `injuries` | 300 | 0 | 0 | 0 | 100.0% |
| 被困人数 `trapped_count` | 261 | 39 | 0 | 0 | 100.0% |
| 直接经济损失(万元) `direct_economic_loss` | 295 | 5 | 0 | 0 | 100.0% |
| 坠落高度(米) `fall_height` | 297 | 3 | 0 | 0 | 100.0% |
| 发生日期 `date` | 296 | 4 | 0 | 0 | 100.0% |
| 发生时刻 `accident_time` | 289 | 5 | 6 | 0 | 100.0% |
| 省 `location` | 295 | 5 | 0 | 0 | 100.0% |
| 市 `city` | 259 | 37 | 4 | 0 | 100.0% |

## 用法

打开 `annotation.html`：左侧是原始语料（带查找高亮），右侧是每个待定字段的三方候选值。
点候选值即选中；三方都不对时在输入框自填（枚举字段为限定下拉）。
顶部「导出 JSONL」下载标注结果；「导出完整 GT」把自动确定值与人工决策合并导出。
进度存在浏览器本地，刷新不丢。
