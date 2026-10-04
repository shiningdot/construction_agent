#!/usr/bin/env python3
"""合并中文别名到家目录配置文件 /home/suntianhao/private-folder-alias.json
（VSCode 工作区根 = /home/suntianhao，路径均相对家目录）

别名按端到端阶段编号组织：① 源头 → ② 采集 → ③ 清洗筛选 → ④ 提取编码
→ ⑤ 存储 → ⑥ 展示 → ⑦ 论文实验 → ⑧ 过程审计
总览见 docs/项目文件地图.md
"""
import json, os, shutil
from pathlib import Path

CFG = '/home/suntianhao/private-folder-alias.json'

# 备份
shutil.copy(CFG, CFG + '.bak')
d = json.load(open(CFG, encoding='utf-8'))

MAIN = 'construction_agent'
# VSCode 工作区根 = 家目录，故别名一律写"相对家目录"的路径。WT 由本脚本位置反推
# （worktree 改名后自动跟随，不必手改）；MAIN 是主仓库名，从 worktree 位置推不出来，
# 保持字面量（主仓库名不随 worktree 变动）。
WT = str(Path(__file__).resolve().parents[1].relative_to(Path.home()))

new_aliases = {
    # ============ 总览 ============
    f'{WT}/docs/项目文件地图.md': {
        "description": "🗺️ 项目文件地图（端到端总览）",
        "tooltip": "源头→采集→清洗→编码→存储→展示→论文，各阶段文件位置与规模"
    },

    # ============ ① 源头 ============
    f'{MAIN}/asset/ebook': {
        "description": "① 源头 · 📚 图书文献库（7 本）",
        "tooltip": "电子书 PDF 总库 437 MB，含 3 本尚未数字化的书"
    },
    f'{MAIN}/data/Input-PDF': {
        "description": "① 源头 · 📄 待数字化 PDF（6 本）",
        "tooltip": "PDFdatasets/raw_PDF/ — 文献数字化流水线的输入口，220 MB"
    },
    f'{MAIN}/data/collector_sources': {
        "description": "① 源头 · 🌐 各源原始落地（22 源）",
        "tooltip": "省应急厅调查报告 PDF 原文 + 各站采集产物，38 MB"
    },
    f'{MAIN}/data/collector_sources/academic_import': {
        "description": "① 源头 · 📊 外部学术数据集（未纳入）",
        "tooltip": "571 条 xlsx，仅备查，未进入 2,623 条语料"
    },
    f'{MAIN}/data/misc': {
        "description": "① 源头 · 🗂️ 源清单与标记",
        "tooltip": "collector_sources.json 全源清单 / 应急管理局.txt / flagged_multi_accidents.json"
    },
    f'{MAIN}/data/taxonomy': {
        "description": "③ 清洗 · 📐 分类与归一化规则",
        "tooltip": "taxonomy.json 事故类型 + normalization_rules.json 归一化规则"
    },
    f'{MAIN}/data/enums': {
        "description": "④ 提取 · 🏷️ MECE 枚举定义",
        "tooltip": "工序/设备/材料/工种/建筑结构 5 字段枚举映射"
    },

    # ============ ② 采集 ============
    f'{MAIN}/construction_agent/collector': {
        "description": "② 采集 · 🕷️ 采集子系统",
        "tooltip": "sources 适配器 + cleaners 清洗器 + pipeline 调度 + classifier 筛选"
    },
    f'{MAIN}/construction_agent/collector/sources': {
        "description": "② 采集 · 🕷️ 25 个站点适配器",
        "tooltip": "safehoo / esafety / 13 个省应急厅 / mohurd / open_gov …"
    },
    f'{MAIN}/construction_agent/collector/pipeline.py': {
        "description": "② 采集 · 🚚 采集调度器",
        "tooltip": "按源逐个执行，产出落 data/collector_sources/<源名>/"
    },

    # ============ ③ 清洗与筛选 ============
    f'{MAIN}/construction_agent/collector/cleaners': {
        "description": "③ 清洗 · 🧹 清洗四器",
        "tooltip": "text_cleaner 正文清洗 / field_extractor 正则预抽取 / normalizer 归一化 / deduplicator 四层去重"
    },
    f'{MAIN}/construction_agent/collector/classifier.py': {
        "description": "③ 筛选 · 🔎 关键词相关性闸门",
        "tooltip": "第一道闸门：规则关键词判定事故相关性"
    },
    f'{MAIN}/construction_agent/collector/llm_classifier.py': {
        "description": "③ 筛选 · 🔍 LLM 文本分类器",
        "tooltip": "第二道闸门：single/multi/other 三分类，Few-Shot 5 示例"
    },

    # ============ ④ 提取与编码 ============
    f'{MAIN}/construction_agent/extraction': {
        "description": "④ 提取 · 📝 Step1 + Step2 + CLIP",
        "tooltip": "33 字段提取 + CAICS 5 层 19 类编码 + 图片 CLIP 过滤（clip_filter.py）"
    },
    f'{MAIN}/construction_agent/extraction/step1_basic_extractor.py': {
        "description": "④ 提取 · 📝 Step1 基础字段提取器",
        "tooltip": "DeepSeek V4 + tool_use，33 字段结构化输出"
    },
    f'{MAIN}/construction_agent/extraction/step2_caics_encoder.py': {
        "description": "④ 提取 · 🧩 Step2 CAICS 致因编码器",
        "tooltip": "两步判断 + 证据链标注，5 层 19 类，evidence_quality 三级分层"
    },
    f'{MAIN}/construction_agent/extraction/search_enhancer.py': {
        "description": "④ 提取 · 🌐 搜索增强模块",
        "tooltip": "UAPI 聚合搜索（fetch_full=true），补全缺失字段"
    },
    f'{MAIN}/construction_agent/versions': {
        "description": "④ 提取 · 🔀 管道版本 v1–v4",
        "tooltip": "五个版本对照物，消融实验的基线与变体"
    },
    f'{MAIN}/construction_agent/hfacs.py': {
        "description": "④ 提取 · 📐 CAICS/HFACS 分类体系",
        "tooltip": "5 层 19 类致因编码的体系定义"
    },

    # ============ ⑤ 存储 ============
    f'{MAIN}/data/caiems_v2.db': {
        "description": "⑤ 存储 · 🗄️ 主数据库（最终产物）",
        "tooltip": "SQLite 9 表：2,623 条事故 · 16,323 条致因编码（explicit 14,784 / inferred 1,117 / prior 422）"
    },
    f'{MAIN}/data/datasets': {
        "description": "⑤ 存储 · 📦 语料 JSON 导出",
        "tooltip": "accidents_merged.json 2,623 条 / raw_accidents.json 1,842 条"
    },
    f'{MAIN}/data/db_backups': {
        "description": "⑤ 存储 · 💾 数据库备份",
        "tooltip": "含 2026-09-23 孤儿外键清理前备份（清理 1,041 行，17,364→16,323）"
    },
    f'{MAIN}/data/imageV3': {
        "description": "⑤ 存储 · 🖼️ 事故现场图片库（最终版）",
        "tooltip": "CLIP 过滤后保留 223 张图片，按事故 ID 分目录"
    },
    f'{MAIN}/data/clip_test': {
        "description": "⑤ 存储 · 🔬 CLIP 判读测试样本",
        "tooltip": "keep / reject 两组，用于确定图片过滤阈值"
    },

    # ============ ⑥ 展示 ============
    f'{MAIN}/construction_agent/web': {
        "description": "⑥ 展示 · 💻 Web 平台",
        "tooltip": "Flask 应用：templates 四页面 + static 资源"
    },
    f'{MAIN}/construction_agent/web/app.py': {
        "description": "⑥ 展示 · 💻 Web 平台入口",
        "tooltip": "事故查询/筛选/证据链展示/REST API"
    },

    # ============ ⑦ 论文与实验 ============
    f'{WT}/docs/paper_v2': {
        "description": "⑦ 论文 · 📄 V2 修订稿（当前版本）",
        "tooltip": "EN 主源 + ZH 镜像 + DOCX/PDF 四格式 + 修订对账记录"
    },
    f'{WT}/docs/paper': {
        "description": "⑦ 论文 · 📄 V1 初稿（存档勿改）",
        "tooltip": "V1 全文 + reviews/ 模拟评审 5 份 + 编辑大修决议"
    },
    f'{MAIN}/docs/paper': {
        "description": "⑦ 论文 · 📄 早期草稿归档",
        "tooltip": "archive_20260713/ — 2026-07-13 前的初步草稿"
    },
    f'{WT}/docs/superpowers/specs': {
        "description": "⑦ 论文 · 🎯 设计蓝图",
        "tooltip": "故事线 / 贡献点 / 图表规划"
    },
    f'{MAIN}/experiments/experiment-models': {
        "description": "⑦ 实验 · 🧪 一：4 模型选型",
        "tooltip": "n=200 分层样本，DeepSeek/Qwen/GLM/Kimi 对比"
    },
    f'{MAIN}/experiments/experiment_6strategies': {
        "description": "⑦ 实验 · 🧪 二：6 策略提示对比",
        "tooltip": "n=100 三专家 GT，词汇门控效应发现地（S0–S5）"
    },
    f'{MAIN}/experiments/experiment_kappa': {
        "description": "⑦ 实验 · 🧪 三：人机一致性",
        "tooltip": "n=289 分层抽样，κ/ICC 协议研究"
    },
    f'{WT}/experiments/ablation_gatekeeping': {
        "description": "⑦ 实验 · 🧪 消融：新样本发射级",
        "tooltip": "2×2 四格（v1/v2a/v2b/v2），summary.json + 三专家多数投票 GT"
    },
    f'{WT}/experiments/ablation_original': {
        "description": "⑦ 实验 · 🧪 消融：原样本 F1 级",
        "tooltip": "原样本四格 checkpoint + f1_summary.json"
    },
    f'{WT}/experiments/figures': {
        "description": "⑦ 实验 · 📊 统计图（6 张）",
        "tooltip": "论文插图需另行手绘，此处为数据图"
    },
    f'{WT}/scripts': {
        "description": "⑦ 论文 · 🛠️ 论文与实验脚本",
        "tooltip": "消融 / GT 恢复 / 评测 / 四格式构建 / 引用转换 / 核验"
    },
    f'{WT}/plan/特征表.md': {
        "description": "⑦ 论文 · 📋 特征设计表",
        "tooltip": "33 字段的特征定义与取值设计"
    },

    # ============ ⑧ 过程与审计 ============
    f'{MAIN}/data/audit': {
        "description": "⑧ 过程 · 🧾 试跑结果与检查点",
        "tooltip": "batch / test50 / ebook800 / step1 / step2 各阶段调试产物"
    },
    f'{MAIN}/data/pipeline_v3.log': {
        "description": "⑧ 过程 · 📜 主管道运行日志",
        "tooltip": "v3 管道完整运行记录，2.1 MB"
    },

    # ============ 包内次级模块 ============
    f'{MAIN}/construction_agent/input': {
        "description": "① 源头 · 📄 文献 PDF 数字化",
        "tooltip": "pdf_processor 切分 + llm_splitter 逐条拆分（文献轨的代码入口）"
    },
    f'{MAIN}/construction_agent/search': {
        "description": "② 采集 · 🔍 搜索模块",
        "tooltip": "baidu_search / bing_search / image_search / search_integration"
    },
    f'{MAIN}/construction_agent/utils': {
        "description": "③ 清洗 · 🧰 归一化工具",
        "tooltip": "date_utils 日期归一化 / location 地点解析"
    },
    f'{MAIN}/construction_agent/models': {
        "description": "🗂️ 数据模型定义",
        "tooltip": "accident.py — 事故记录字段模型"
    },
    f'{MAIN}/construction_agent/database': {
        "description": "⑤ 存储 · 🗃️ 数据访问层",
        "tooltip": "sqlite_repository / dataset_manager / models + migrations"
    },
    f'{MAIN}/construction_agent/output': {
        "description": "⑤ 存储 · 📤 导出格式化",
        "tooltip": "json_formatter / csv_formatter"
    },
    f'{MAIN}/construction_agent/evaluation': {
        "description": "⑦ 实验 · 📊 评测框架",
        "tooltip": "evaluator / metrics / reporter / hfacs_evaluator + experiment1-3"
    },

    # ============ 入口与依赖 ============
    f'{MAIN}/construction_agent/main.py': {
        "description": "🚀 主程序入口",
        "tooltip": "管道总调度"
    },
    f'{MAIN}/construction_agent/config.py': {
        "description": "⚙️ 全局配置（含路径依赖）",
        "tooltip": "API 密钥、模型档位与路径；DATA_DIR 硬编码为包上一级 /data，勿移动"
    },
    f'{WT}/construction_agent/extraction/vlm_analyzer.py': {
        "description": "④ 提取 · 👁️ VLM 多模态分析器",
        "tooltip": "GLM-5V-Turbo 两阶段（盲观察 + 上下文融合）；此文件仅存在于 worktree"
    },
    f'{MAIN}/scripts': {
        "description": "🛠️ 工具与实验脚本",
        "tooltip": "管道 / 评测 / 消融 / 建库 / 核验脚本"
    },
    f'{MAIN}/requirements.txt': {
        "description": "📦 Python 依赖清单",
        "tooltip": "虚拟环境 .venv 已安装"
    },
    f'{MAIN}/CLAUDE.md': {
        "description": "🤖 项目 AI 协作规范",
        "tooltip": "语言要求与项目指引（论文索引亦在此）"
    },
}

# 移除失效条目（路径不存在或已归档）
for k in [
    'construction_agent/construction_agent/extraction/vlm_analyzer.py',  # 实际只在 worktree
]:
    if k in d:
        del d[k]
        print(f'🗑️ 移除失效条目: {k}')

added = 0
for k, v in new_aliases.items():
    if k not in d:
        d[k] = v
        added += 1
    else:
        d[k].update(v)

json.dump(d, open(CFG, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f'✅ 新增 {added} 条，更新 {len(new_aliases) - added} 条，总条目 {len(d)} 条')
print(f'备份: {CFG}.bak')

# 清理之前写错位置的文件
for f in [
    f'{MAIN}/.vscode/private-folder-alias.json',
    f'{WT}/.vscode/private-folder-alias.json',
]:
    if os.path.exists(f):
        os.remove(f)
        print(f'🗑️ 清理误置文件: {f}')
