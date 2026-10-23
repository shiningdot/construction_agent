# 图注与交付说明 — CAICS 编码覆盖图

## English caption (ESWA / Elsevier style)

**Fig. X.** Distribution of CAICS cause codes in the 300-case ground-truth set. Each of the 19 CAICS categories is shown as one horizontal bar; blue denotes the number of cases annotated positive for that category, and orange denotes the remaining cases (N = 300). Categories are ordered by positive incidence. All 19 categories have at least one positive case, but incidence is highly uneven: the violation-of-procedure (H1), on-site supervision (M1), and hidden-danger detection (M2) codes occur in at least 85% of cases, whereas the consequence-amplifying codes C1 and C2 occur in fewer than 3%. Source data: `fig_caics_code_coverage_data.csv`.

## 中文图注

**图 X.** 300 条标准答案集中 CAICS 致因编码的分布。19 个 CAICS 类各占一行；蓝色为该类被标注为正例的事故数，橙色为其余事故数（N = 300）。编码按正例数降序排列。19 类均至少有一条正例，但分布高度不均：违反操作规程（H1）、现场监管缺失（M1）与隐患排查失效（M2）出现在 85% 以上的事故中，而后果放大层的 C1 与 C2 不足 3%。

## 19 类编码对照

| 编码 | 英文名称 | 中文名称 | 正例数 |
|---|---|---|---:|
| H1 | Violation of operating procedures | 违反操作规程 | 288 |
| M1 | Absence of on-site supervision | 现场监管缺失 | 279 |
| M2 | Failure of hidden-danger detection | 隐患排查失效 | 255 |
| M3 | Inadequate safety training | 安全培训不足 | 211 |
| M4 | Deficient rules and procedures | 制度规程漏洞 | 207 |
| H3 | Risk-identification failure | 风险辨识失败 | 201 |
| H2 | Unsafe command or decision | 违规指挥决策 | 167 |
| F2 | Support-system instability | 支撑体系失稳 | 156 |
| M5 | Illegal market behaviour | 市场行为违规 | 145 |
| T2 | Abnormal material state | 物料状态异常 | 93 |
| T1 | Equipment or machinery failure | 设备机具故障 | 90 |
| F3 | Failure of safety devices | 设备安全装置失效 | 56 |
| F4 | Non-use of personal protective equipment | 个体防护未使用 | 50 |
| T3 | Abrupt geological or environmental change | 地质环境突变 | 39 |
| H4 | Improper emergency behaviour | 应急行为失当 | 22 |
| T4 | Meteorological or hydrological trigger | 气象水文触发 | 22 |
| F1 | Missing edge or opening protection | 临边洞口防护缺失 | 21 |
| C1 | Delayed rescue response | 救援响应迟缓 | 7 |
| C2 | Late or concealed reporting | 信息迟报瞒报 | 4 |

## 制图与交付参数

- 画布：190 × 120 mm（Elsevier 双栏宽），PDF MediaBox 实测 538.58 × 340.16 pt。
- 导出：`fig_caics_code_coverage.pdf`（矢量，文本可编辑，TrueType 内嵌）、`.svg`（文本为 `<text>` 节点）、`.png`（600 dpi，4488 × 2834 px）。
- 字体：Arial 优先，本机回退到 Liberation Sans（与 Arial 同度量，PDF 内嵌 `LiberationSans`）。
- 字号：坐标轴标签 7.5 pt、编码标签 8 pt、条末计数 6.4 pt、图例 7.4 pt。
- 颜色：正例 `#A7C9E8`，负例 `#F2A57A`；蓝/橙组合对红绿色觉障碍安全，灰度打印仍可区分。
- 统计口径：本图为 300 条 GT 的全量计数，不含误差棒；每条事故的编码集合去重后计数（一条事故同一编码只计一次）。
- 源数据：`fig_caics_code_coverage_data.csv`（code, present, absent, present_rate）。
- 复现脚本：`make_fig_caics_coverage.py`。

## 使用提示

- 图中只显示编码（如 H1、M1），首次出现时建议在正文或图注中给出层名（M = management，F = defence failure，T = trigger condition，H = human behaviour，C = consequence amplification），完整定义可引 CAICS 分类表。
- 条形总长为 300（正例 + 负例），横轴绘制到 350，与示例图"条长=N、轴留余量"的版式一致。
