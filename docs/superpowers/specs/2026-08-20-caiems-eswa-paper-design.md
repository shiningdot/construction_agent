# CAIEMS ESWA 论文设计方案

> 日期：2026-08-20 ｜ 状态：**历史设计快照，数字已过时** ｜ 目标：Expert Systems with Applications（一区）
> 旧草稿已归档至 `docs/paper/archive_20260713/`，本方案为全新构思。
>
> ⚠️ 本文档的数字（致因编码 17,364、F1 0.425/0.676 等）为设计期取值，**写作时一律以
> `docs/paper_v2/修订对账记录.md` 的数字对账清单为准**（当前口径：致因编码 16,323）。

---

## 1. 核心定位

**一句话定位**：构建首个 LLM 驱动的施工事故信息抽取-致因分类-在线数据库端到端系统，并在提示策略实验中诊断出"词汇门控效应"这一新的提示工程失效模式。

**故事线结构**：A（端到端系统）为骨架 + B（词汇门控效应）为旗舰发现钩子。

## 2. 标题方向

| # | 标题 | 风格 |
|---|------|------|
| 1 | *CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction* | 系统导向 |
| 2 | *When Few-Shot Outperforms Domain CoT: The Lexical Gatekeeping Effect and an LLM-Driven Pipeline for Construction Accident Causal Extraction* | **钩子前置（推荐）** |
| 3 | *Diagnosing the Lexical Gatekeeping Effect: Prompt Failures and Remedies in LLM-Based Construction Accident Causal Coding* | 纯发现导向 |

推荐 #2，摘要前两句抛出钩子。

## 3. 贡献清单（C1–C4）

| # | 贡献 | 支撑数据 |
|---|------|----------|
| C1 | **CAICS 致因分类体系**：5 层 19 类，面向 LLM 提取优化的施工事故致因框架，基于对 2,623 条事故语料的实证归纳 | 17,364 条致因编码 + 操作定义表 |
| C2 | **端到端管道与交互式数据库**：多源网页文本 → 33 字段结构化记录，产出 2,623 条事故、17,364 条致因标注的在线数据库（Web + REST API），**数据发表后公开** | 数据库 + Web 平台 |
| C3 | **词汇门控效应（旗舰发现）**：命名、诊断、补救一种新的提示工程失效模式——条件式 CoT 中关键词被当作"强制门槛"导致召回崩溃（F1=0.425）；"两步判断"补救后 F1 恢复至 0.676（**+59%**）。同时发现提示策略排名的**模型依赖性**（Few-Shot 0.737 最优，与 Liu et al. 2025 在 GPT-4o 上的排名相反） | 实验二（6 策略 × 100 条） |
| C4 | **三重实证验证**：① 4 模型对比选型（200 条，DeepSeek V4 胜出）；② 人机一致性协议（n=289，κ=0.91–0.97，达到专家间信度区间）；③ 972 条银标准数据集支撑可复现评测 | 实验一 + 实验三 |

## 4. 与 Liu et al. (ESWA 2025) 的对话定位

| 维度 | Liu et al. (2025) | 本文 |
|------|-------------------|------|
| 领域 | 航空事故（HFACS） | 建筑施工事故（CAICS） |
| 模型 | GPT-4o 单模型 | DeepSeek V4 + 4 模型对比 |
| 策略排名 | 领域 CoT > Few-Shot | Few-Shot > 领域 CoT（逆转） |
| 深度 | 排名报告 | 机制诊断 + 命名效应 + 补救方案 |
| 交付物 | 评估结论 | 系统 + 数据库 + 数据集 |

## 5. 章节结构大纲（目标 ~28 页正文）

| 章 | 内容 | 篇幅 |
|----|------|------|
| 1. Introduction | 问题背景 → 三大研究缺口 → 贡献 C1-C4 → 论文结构 | ~2 页 |
| 2. Related Work | 2.1 传统 NLP/ML 事故分析 → 2.2 LLM 事故信息抽取 → 2.3 HFACS×LLM → 2.4 事故数据库与知识图谱 | ~2.5 页 |
| 3. System Design | 3.1 问题定义 → 3.2 CAICS 体系 → 3.3 管道架构（采集→分类→两阶段提取→枚举标准化→入库）→ 3.4 多模态扩展（搜索增强+CLIP+VLM，0.5-1 页）→ 3.5 数据库与 Web 平台 | ~5 页 |
| 4. Experimental Evaluation | 4.1 数据集与抽样 → 4.2 评测指标 → 4.3 实验一（选型）→ 4.4 实验二（策略+门控效应，旗舰）→ 4.5 实验三（Kappa）→ 4.6 讨论性分析 | ~11 页 |
| 5. Discussion | 理论意义 → 实践意义 → 与 Liu et al. 对比 → 局限性 | ~2 页 |
| 6. Conclusion | 总结 + 未来工作 | ~0.5 页 |
| Appendix | A: 6 策略完整 Prompt；B: CAICS 操作定义表 | ~3 页 |

**实验编排（方案甲）**：实验一（选型）→ 实验二（策略优化，旗舰）→ 实验三（信度验证收官）。

## 6. 图表规划（8 图 + 6 表）

### 图（Figures）

| # | 内容 | 状态 |
|---|------|------|
| Fig.1 | 系统架构图 | ✅ 已有（技术路线图，需英文化） |
| Fig.2 | CAICS 五层体系可视化（树状图） | 需新绘 |
| Fig.3 | 实验一：核心字段准确率 + 成本/速度双面板 | ✅ 已有（experiment-models/figures） |
| Fig.4 | 实验一：四致因维度 P/R/F1 | ✅ 已有 |
| Fig.5 | 实验二：6 策略 F1/R/P 对比 | ✅ 已有（experiment_6strategies/figures） |
| Fig.6 | 词汇门控效应诊断图（召回崩溃 vs 两步判断恢复 +59%） | 需新绘（旗舰图） |
| Fig.7 | 实验三：Kappa 森林图（LLM κ vs 专家区间 + 95% CI） | ✅ 已有（fig_kappa_icc.png） |
| Fig.8 | Web 平台界面截图 | 需截取 |

### 表（Tables）

| # | 内容 | 状态 |
|---|------|------|
| Table 1 | CAICS 19 类定义 | ✅ 已有（操作定义表） |
| Table 2 | 数据集统计（2,623 条、12 类型分布、1978-2026、5,894 死） | 需生成 |
| Table 3 | 4 模型对比总表（准确率+P/R+成本+延迟） | ✅ 已有 |
| Table 4 | 6 策略性能对比 | ✅ 已有 |
| Table 5 | Kappa/ICC 汇总表（含 95% CI 与专家区间对比） | ✅ 已有 |
| Table 6 | 与既有工作对比表（Liu 2025 / Ahmadi 2024 / Kim & Han 2024） | 需编写 |

## 7. 三个实验的样本关系

| 实验 | n | Ground Truth 来源 | 用途 |
|------|---|-------------------|------|
| 实验一 | 200 | 972 条银标准（人工验证）分层抽样 | 模型选型 |
| 实验二 | 100 | 3 专家多数投票（Fleiss' κ>0.85） | 策略对比 |
| 实验三 | 289 | 1 名安全工程研究生标注 | 人机信度 |

数据章节绘制"2,623 全量 → 972 银标准 → 200/100/289 三路抽样"流程图。

## 8. 模型命名一致性方案

- **全文统一写 "DeepSeek V4"**，不分 Pro/Flash 档位（auto 模式下 API 自动路由，属同一模型族）
- 实验设置标注：`DeepSeek V4, temperature=0.0, via official API (auto tier routing)`
- 其他对比模型保持原名：Qwen 3.7、GLM 5.2、Kimi K2.6
- 生产管道中 DeepSeek V4 用于两阶段提取与分类，不区分档位

## 9. 双语同步机制

```
docs/paper/
├── eswa_paper.md        ← 英文主源（投稿版本，source of truth）
├── eswa_paper_zh.md     ← 中文镜像（供用户阅读）
└── figures/             ← 图表共享（英文标注）
```

| 机制 | 规则 |
|------|------|
| 写作顺序 | 每章先写英文（学术英文直接写作）→ 完成后立即产出中文镜像 |
| 同步粒度 | 逐章同步 |
| 图表语言 | 统一英文；中文镜像引用同一文件 |
| 修订同步 | 修改英文后同步更新中文，章末校验中英段落一一对应 |
| 学位论文衔接 | 中文镜像章节后续可移植入学位论文 |

## 10. 与学术管道的对接

设计文档批准后进入 academic-pipeline Stage 2（WRITE），本方案作为 12-agent 写作团队的输入蓝图：

```
设计文档 → Stage 2: academic-paper (full 模式)
  → Stage 2.5: 完整性验证（引用/数据 100% 核验，强制闸门）
  → Stage 3: 5 人审稿模拟（EIC + 3 审稿人 + 魔鬼代言人）
  → Stage 4: 修订 + Response to Reviewers
  → Stage 3': 复审 → Stage 4.5: 最终完整性验证
  → Stage 5: 格式化（MD → DOCX/PDF）
  → Stage 6: 过程记录
```

**风险规避**：
- 引用核验：文献以 `article/` 目录 PDF 为唯一来源，不引用未下载文献
- 数据核验：所有数字（2,623 / 17,364 / κ / F1 等）从 DB 和实验 JSON 文件直接取值，不凭记忆

## 11. 关键数据速查（写作时直接引用）

| 指标 | 数值 | 来源 |
|------|------|------|
| 事故总数 | 2,623 | caiems_v2.db |
| 致因编码总数 | 17,364 | caics_causes |
| 结构化字段数 | 33 | accidents 表 |
| 日期范围 | 1978-08 ~ 2026-06 | accidents.date |
| 累计死亡/受伤 | 5,894 / 5,796 | SUM(deaths/injuries) |
| 事故类型数 | 12（坍塌 1,092 最多） | GROUP BY |
| 搜索增强 | 1,636 条 | search_performed=1 |
| CLIP 过滤图片 | 226 张 | imageV3 |
| VLM 分析 | 282 条 / 176 事故 | image_analyses |
| 银标准 | 972 条人工验证 | experiment-models 记录 |
| 实验一核心字段准确率 | DS 91.2% / Qwen 91.5% / GLM 91.5% / Kimi 63.9% | experiment1_metrics |
| 实验一致因 P/R | DS 89.5%/52.7%，Qwen 78.0%/80.5% | experiment1_metrics |
| 实验二 S1 Few-Shot F1 | 0.737（最优） | 5.2-5.4 分析文档 |
| 门控效应 | 条件式 CoT F1=0.425 → 两步判断 0.676（+59%） | 5.2-5.4 分析文档 |
| 实验三 Kappa | 类型 0.9136 / 等级 0.9477 / 省份 0.9746；ICC 死 0.9849 伤 0.9853 | agreement_metrics.json |

## 12. 明确的非目标（YAGNI）

- ❌ 不写 HFACS-CS 双框架（当前 DB 无 HFACS 列，避免查证穿帮）
- ❌ 不深入 VLM 评估（系统模块简要介绍即可；图片源头质量限制写入局限性）
- ❌ 不做新实验（三个实验已足够；如需补充只做图表层面的重绘）
- ❌ 不引用未下载的文献
