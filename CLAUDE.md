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
