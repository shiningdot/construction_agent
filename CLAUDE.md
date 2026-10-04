# 语言要求：全部使用中文回答。所有思考过程、代码注释、对话回复都必须使用中文。

# 建筑施工事故信息采集与分析项目

## 项目概述
建筑施工事故信息采集与分析的 Python 项目，支持多源搜索、信息提取、数据管理和 Web 展示。

## 目录结构
- `construction_agent/main.py` - 主入口
- `construction_agent/config.py` - 配置
- `construction_agent/models/accident.py` - 事故数据模型
- `construction_agent/search/` - 搜索模块（百度、Bing、图片搜索）
- `construction_agent/extraction/claude_extractor.py` - 信息提取
- `construction_agent/web/app.py` - Web 应用
- `construction_agent/database/dataset_manager.py` - 数据集管理
- `construction_agent/output/` - 输出格式化（JSON、CSV）
- `data/` - 数据文件（accidents.json 等）
- `tests/` - 测试

> 🗺️ **端到端文件地图**：`docs/项目文件地图.md` — 按 ①源头 → ②采集 → ③清洗筛选 → ④提取编码 → ⑤存储 → ⑥展示 → ⑦论文实验 → ⑧过程审计 说明全链路文件位置、规模、路径依赖红线与可清理项。**动手改动任何文件位置前先看第十节。**

## 技术栈
- Python 3
- Claude API（信息提取）
- 百度/Bing 搜索 API
- Web 界面（Flask/FastAPI）

## 运行方式
```bash
# 激活虚拟环境
source .venv/bin/activate
# 运行主程序
python -m construction_agent.main
```

---

# 论文项目索引（ESWA 投稿）

> 目标期刊：Expert Systems with Applications（Q1）｜第一作者：孙天浩，南京工业大学，202461201086@njtech.edu.cn｜导师共同署名（通讯作者），无基金

## ⚠️ 双目录结构（先看这个）
| 位置 | 内容 |
|---|---|
| `/home/suntianhao/construction_agent/` | 项目代码 + `data/caiems_v2.db`（主数据库，28 MB，9 表） |
| `/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/` | **论文稿件、论文脚本、消融实验数据** |

以下论文相关路径均相对 worktree 根目录。

## 论文稿件
- `docs/paper_v2/eswa_paper_v2.md` — **当前版本（V2）英文主源，修改以它为准**
- `docs/paper_v2/eswa_paper_v2_zh.md` — 中文镜像（**改英文后必须同步改中文**）
- `docs/paper_v2/修订对账记录.md` — 导师 14 项意见→修订动作对照表、数字对账清单、待绘图片清单
- `docs/paper_v2/eswa_paper_v2_{en,zh}.{docx,pdf}` — 四格式成品
- `docs/paper_v2/figures/` — 论文插图 fig1–fig6（PNG + SVG），由 `scripts/gen_paper_figures.py` 生成，正文以 `![](figures/...)` 引用
- `docs/paper_v2/v1_v2_对比.{html,docx}` — V1→V2 红绿对比（红=删除、绿=新增）
- `docs/paper/eswa_paper.md` — V1 初稿：**存档，勿改**
- `docs/paper/reviews/` — 模拟评审 5 份 + `00_editorial_decision.md`（大修结论）
- `docs/superpowers/specs/2026-08-20-caiems-eswa-paper-design.md` — 论文设计蓝图（故事线/贡献点/图表规划）
- `docs/paper/cover_letter.md` — 投稿信（导师姓名仍为占位符）

## 论文实验数据
- `experiments/ablation_gatekeeping/summary.json` — 2×2 消融（新样本 4×100）
- `experiments/ablation_gatekeeping/gt_recovered.json` — 三专家多数投票真值
- `experiments/ablation_original/f1_summary.json` — 原样本 F1 级四格
- `experiments/figures/` — 6 张统计图（论文插图需另行手绘）

## 论文相关脚本（`scripts/`）
- 消融：`ablation_gatekeeping.py`、`ablation_original_sample.py`
- 真值：`recover_gt.py`（多数投票 + Fleiss κ）
- 评测：`eval_with_gt.py`（6 策略 + bootstrap 置信区间）
- 格式：`build_v2_docs.py`（四格式一键构建）、`gen_v1v2_diff.py`（红绿对比）
- 引用：`convert_numbered_citations.py`、`check_citations.py`、**`check_citations_v2.py`**（V2 双向对账，退出码 0 = 无孤立文献/无悬空引用）、`verify_dois.py`
- 核验：`stage45_final_verify.py`、`stage25_data_verify.py`

## 核心代码（主仓库）
- `construction_agent/extraction/step1_basic_extractor.py` — Step1：33 个基础字段提取
- `construction_agent/extraction/step2_caics_encoder.py` — Step2：CAICS 5 层 19 类致因编码 + 证据链
- `construction_agent/collector/llm_classifier.py` — 文本三分类（single/multi/other）
- `construction_agent/config.py` — 全局配置（`load_dotenv(override=True)`）
- `construction_agent/web/app.py` — Web 平台
- `construction_agent/hfacs.py` — CAICS/HFACS 分类体系

## 写作时不得违反的既定决策
1. **故事线**：探索性数据工程系统与数据资源（**不是**因果机制发现）
2. **词汇门控效应**：支撑性方法观察，V2 已从旗舰论点降调
3. **模型命名**：统一写 "DeepSeek V4"（auto 档位路由，不写 Pro/Flash）
4. **数据发布**：发表后公开，审稿期按需提供
5. **禁用措辞**：首次 / 证明 / 因果机制 / 专家级 / 替代人工 / 全部编码有证据（导师明确要求删除）
6. **证据三级分层**：explicit / inferred / prior，对应数据库 `evidence_quality` 字段

## 当前待办
- [x] **2026-10-01 论文插图接入**：6 张图由 `scripts/gen_paper_figures.py` 生成（300 DPI PNG + SVG，输出 `docs/paper_v2/figures/`）并插入 V2 中英文正文（图在上、图注在下），四格式成品各内嵌 6 张。改前脚本里的分布数字停在孤儿外键清理前的 17,364，本次改为**从主库现读**（实测 16,323）；图 2 重画为「五层结构 + 一条真实记录的证据链」，图 1 多模态列溢出、图 5 标签重叠、图 6 图例压数据行一并修掉。**待决**：图 3 未采纳"按证据层级分层着色"（现按 CAICS 五层着色）；`[表2占位]` 已由下条补全。
- [x] **2026-10-04 表 2 补全 + LaTeX 排版修缺陷**：`[表2占位]` 换成 25 行「5 层 19 类操作定义表」（定义逐条对照 `step2_caics_encoder.py` 的生产提示词，与主库落库类名一致；表注给出实测各层推断占比 M 12.0% / T 3.1%）。同轮修掉 7 处排版缺陷：中文表 2 逐行溢出、**成品纸张误为 US Letter**、中文标题 23 字渲染成豆腐块（`\title{}` 漏 `esc()`）、两版 12 个圈码 ①–⑫ 无字形、章节编号叠加成 `2 1. Introduction`、**9 个四级标题（3.2.1–4.4.5）整行丢失**（LaTeX 与 DOCX 两条路径都缺 `#### ` 分支）、中文 `vbox too high` 232pt。验收：中文 Overfull **0**、Missing **0**，英文仅剩表 10 末行 2.86pt。**第二轮又修 1 处**：§3.1 的 `$t_i$`/`$x_{ij}$` 数学被 `esc()` 的 `.replace('_','\_')` 破坏成字面下划线（DOCX 版更糟，美元号与反斜杠命令原样暴露）——`esc()` 改为先抽 `$...$` 占位保护，DOCX 侧新增 `_add_math()` 转「斜体 + 真下标」。四格式一致性验收见 `docs/paper_v2/修订对账记录.md` 第七节 7.4（DOCX 残留 `$` 两版均 0、真下标各 18 个、标题层级 H1/H2/H3 = 10/18/9 两版对称）。
- [x] **2026-10-04 引用双向对账（V2 首次）**：既有 `check_citations.py` 硬编码指向 V1（27 条），新增 **`scripts/check_citations_v2.py`** 对 V2 做双向对账（文献→正文查孤立文献 ／ 正文→文献查悬空引用，退出码可接改稿流程）。首轮报 3 条疑似孤立文献，逐条复核实为 **1 条**：`Zhang (2025a)`/`Zhang (2025b)` 是假阳性（正文写作 `Zhang, Hou, et al. (2025a)`「姓氏+第二作者+et al.」形式，正则漏了这一种）；只剩 `Devlin (2019) BERT` 真孤立（正文 `Devlin` 0 次，`BERT` 5 次但仅作技术名词）。**处置：正文首次提及 BERT 处补 `(Devlin et al., 2019)`**（不删文献——它是 BERT 原始出处），中英同步。现两版均 28 条文献 / 24 个引用名 / 孤立 **0** / 悬空 **0**；补引用未引入新排版溢出（英文仍仅表 10 那 1 处 2.86pt）。见 `docs/paper_v2/修订对账记录.md` 第八节
- [ ] 导师真实姓名替换 `[Supervisor name]`（CRediT 与投稿信）
- [ ] 数据仓库选择
- [ ] 可靠性路线图 P0–P7 未执行：独立人工金标准、冻结测试集、正式因子实验、多模型同任务对比、编码一致性研究、数据审计、模型快照
- [ ] 已知数据问题：`image_files` 旧登记与磁盘差 123 张图 / 83 个事故（原先记「imageV3 少注册 15 条」只算了 `imageV3/` 一个目录，见 `docs/web/08_图片资产复核与更正.md` 第八节）；约 17 条地址级错误
- [x] **2026-10-01 平铺池接入**：`data/images/` 顶层 3,701 个文件（初版管线产物，此前从未进清单）经三道桥归属 **412 种 / 751 张**，`image_manifest` 1,020→**1,771** 行、有图事故 578(22.0%)→**805(30.7%)**。归属依据：旧库标题桥 279 种 + 主库 id 前 8 位前缀桥 130 种（新发现，实测零碰撞、与标题桥零冲突）+ 手工表 3 种；**668 种第三套 hex 与 761 种两桥不通的留空不猜**，6 张广州 7·22 塔吊明确排除。见 `docs/web/08_图片资产复核与更正.md` 第十节
- [x] **2026-09-23 数据清理**：`caics_causes` 孤儿外键 1,041 行（109 个失效 accident_id，系旧版重复编码）已删除，致因标注总数 17,364→**16,323**，论文中英双版各 6 处数字已同步；旧模板手工采集数据（`原始数据/data_STH.csv` 等 4 个文件，1,093 条，与 DB 重复 90.7%）已移出项目，归档于 `/home/suntianhao/TEM/archive_旧模板数据_20260923`；清理前备份：`data/db_backups/caiems_v2.db.bak.before_orphan_cleanup_20260923_155533`
- [x] **2026-09-23 作者决定**：不做"人工清单 vs 管道采样"的对比（102 条差额不入论文）；人工收集数据已备份后自项目删除；3 条无 CAICS 编码的真实事故暂不补编码；**暂不推送远端仓库**

## 外部资产（worktree 与主仓库之外）
- `/home/suntianhao/TEM/CAIEMS_项目交接文档.md` — 项目交接总文档（8 节：架构/代码/数据/论文/陷阱/环境）
- `/home/suntianhao/TEM/expert{A,B,C}.json` — **三专家原始标注导出，多数投票真值的唯一来源，勿删**
- `/home/suntianhao/TEM/technical_roadmap.svg`、`caiems_roadmap.png` — 技术路线图
- `/home/suntianhao/construction_agent/article/articles.bib` — 参考文献库（另有 `export.bib`）
- `~/diagrams/` — 图源文件：`简易流程图.drawio`、`caiems_roadmap.drawio`、`CAICS_five_layer_tree_v5.drawio`
- `docs/HFACS-CS_Taxonomy.md`、`docs/enums/*.{md,json}` — 分类体系与 MECE 枚举定义（含 LLM 框架/映射）
- `plan/特征表.md` — 特征设计表

## 论文改稿流程
1. 只改 `docs/paper_v2/eswa_paper_v2.md`（英文主源），随后**同步**修改 `eswa_paper_v2_zh.md`
2. 重新生成四格式成品：`.venv/bin/python3 scripts/build_v2_docs.py`
3. 重新生成 V1→V2 对比：`python3 scripts/gen_v1v2_diff.py`
4. 正文任何数字必须与 `修订对账记录.md` 的"数字对账清单"一致
5. 跑引用双向对账：`.venv/bin/python3 scripts/check_citations_v2.py`（退出码须为 0）

## 环境与陷阱
- **Python 解释器**：`/home/suntianhao/construction_agent/.venv/bin/python3`（系统 python 缺 dotenv/anthropic）
- **模型与 key 配置分散三处**，切档位需同步改：`~/.bashrc`、`~/.claude/settings.json`、`construction_agent/.env`（后者 `override=True`，对项目代码有最终决定权）
- **模型不一致风险**：论文实验脚本硬编码 `deepseek-v4-pro`，主管道读 `.env`（现为 `deepseek-v4-flash`）；补跑数据前先确认档位
- **WAL 是持久属性；库一旦被切成 WAL，就不能再用 `shutil.copy2` 备份**：`scripts/create_db_v2.py` 建库、`review/promote.py` 并入都会 `PRAGMA journal_mode=WAL`（写进文件头，此后一直是 WAL）。WAL 模式下主文件在 checkpoint 之前是滞后的，文件级复制只拿到旧状态（实测 51 行只备到 1 行）。备份一律用 `construction_agent.database.backup.safe_backup`
- **禁止裸 `git stash`**（与其它 worktree 共享栈）；worktree 会话禁止操作主仓库 git
- 调用 DeepSeek 时思考块会吃掉输出预算，`max_tokens` 需 ≥16384，否则返回空文本
- 会话记录与备份文件中留有 API key 明文，勿同步到云端
