# Construction Agent 🏗️

建筑施工事故信息自动化处理系统。

## 项目背景

在安全事故研究中，人为收集、处理、提取建筑施工事故数据存在效率低、耗时长、标准不一致等问题。本项目旨在构建一个智能 Agent，实现：

1. 🔍 **联网检索** — 基于输入的事故信息，自动联网搜索更多内容
2. 📋 **智能提取** — 自动提取事故基础特征（时间、地点、类型等）和致因分析（人、机、管、环）
3. 📊 **结构化输出** — 以 JSON / CSV 格式输出结构化数据，构建事故数据集

## 技术栈

| 模块 | 技术 |
|------|------|
| LLM | Claude API (Anthropic) |
| 搜索 | Bing Search API |
| 后端 | Python + Flask |
| 输出 | JSON / CSV |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件填入 API Key
```

### 3. 使用 CLI

```bash
# 单条事故处理
python -m construction_agent.main process "某工地发生坍塌事故，造成3人死亡"

# 批量处理文件
python -m construction_agent.main batch input.txt

# 启动交互模式
python -m construction_agent.main interactive
```

### 4. 启动 Web UI

```bash
python -m construction_agent.main web
```

## 项目结构

```
construction_agent/
├── construction_agent/        # 核心代码
│   ├── main.py               # CLI 入口
│   ├── config.py             # 配置管理
│   ├── models/               # 数据模型
│   ├── search/               # 联网检索模块
│   ├── extraction/           # 特征提取模块（Claude API）
│   ├── output/               # 输出格式化（JSON/CSV）
│   ├── database/             # 数据集管理
│   └── web/                  # Web UI
├── data/                     # 数据存放目录
├── tests/                    # 测试
└── requirements.txt          # 依赖
```

## 事故特征提取维度

| 维度 | 内容 |
|------|------|
| 📅 基础信息 | 时间、地点、项目名称 |
| 🏷️ 事故类型 | 坍塌、高处坠落、触电、机械伤害等 |
| 📊 事故等级 | 一般/较大/重大/特别重大 |
| 👤 人员因素 | 违章操作、安全意识不足、疲劳作业等 |
| ⚙️ 设备因素 | 设备故障、防护缺陷等 |
| 📋 管理因素 | 监管缺失、制度不健全、培训不足等 |
| 🌤️ 环境因素 | 恶劣天气、地质条件等 |

## License

MIT
