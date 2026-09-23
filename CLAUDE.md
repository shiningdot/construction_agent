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
- 引用：`convert_numbered_citations.py`、`check_citations.py`、`verify_dois.py`
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
- [ ] 6 张论文插图手绘（V2 中已留占位符，清单见对账记录）
- [ ] 导师真实姓名替换 `[Supervisor name]`（CRediT 与投稿信）
- [ ] 数据仓库选择
- [ ] 可靠性路线图 P0–P7 未执行：独立人工金标准、冻结测试集、正式因子实验、多模型同任务对比、编码一致性研究、数据审计、模型快照
- [ ] 已知数据问题：imageV3 少注册 15 条、约 17 条地址级错误
- [x] **2026-09-23 数据清理**：`caics_causes` 孤儿外键 1,041 行（109 个失效 accident_id，系旧版重复编码）已删除，致因标注总数 17,364→**16,323**，论文中英双版各 6 处数字已同步；旧模板手工采集数据（`原始数据/data_STH.csv` 等 4 个文件，1,093 条，与 DB 重复 90.7%）已移出项目，归档于 `/home/suntianhao/TEM/archive_旧模板数据_20260923`；清理前备份：`data/db_backups/caiems_v2.db.bak.before_orphan_cleanup_20260923_155533`
- [ ] **待决**：3 条真实事故无 CAICS 编码（`6938d2cdb1e7` 北京新机场高速"9·26"、`b5c090f2be12` 煌上煌海南洋浦"3·15"、`ec30cc84bef0` 上海漕泾"10·17"）；旧数据中有 102 条事故未被管道采集覆盖（管道召回缺口，可考虑写入局限性）

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

## 环境与陷阱
- **Python 解释器**：`/home/suntianhao/construction_agent/.venv/bin/python3`（系统 python 缺 dotenv/anthropic）
- **模型与 key 配置分散三处**，切档位需同步改：`~/.bashrc`、`~/.claude/settings.json`、`construction_agent/.env`（后者 `override=True`，对项目代码有最终决定权）
- **模型不一致风险**：论文实验脚本硬编码 `deepseek-v4-pro`，主管道读 `.env`（现为 `deepseek-v4-flash`）；补跑数据前先确认档位
- **禁止裸 `git stash`**（与其它 worktree 共享栈）；worktree 会话禁止操作主仓库 git
- 调用 DeepSeek 时思考块会吃掉输出预算，`max_tokens` 需 ≥16384，否则返回空文本
- 会话记录与备份文件中留有 API key 明文，勿同步到云端
