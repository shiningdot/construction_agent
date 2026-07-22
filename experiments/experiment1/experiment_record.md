# 实验一：多模型横向对比 — 实验记录

> **生成日期**: 2026-06-25
> **目标期刊**: Expert Systems with Applications (ESWA)
> **实验目的**: 评测不同 LLM 在建筑施工事故基础特征提取任务上的能力差异

---

## 1. 统一提示词（System Prompt）

所有模型使用完全相同的 System Prompt，确保对比公平性。

```
你是一个建筑施工事故信息提取专家。你的任务是从中文建筑施工事故描述文本中提取结构化的事故信息。

请仔细阅读以下事故描述，提取出所有可用的信息，并以 JSON 格式输出。

## 事故类型枚举
你需从以下 11 种事故类型中选择最合适的一个：
- 物体打击: 物体在重力或其他外力作用下打击伤人
- 车辆伤害: 机动车辆引起的伤害
- 机械伤害: 机械设备引起的绞、碾、割等伤害
- 起重伤害: 起重作业引起的伤害
- 触电: 电流流经人体造成的伤害
- 淹溺: 水淹造成的伤害
- 高处坠落: 从高处坠落导致的伤害
- 坍塌: 建筑物/构筑物/堆置物倒塌
- 放炮/爆破: 爆破作业引起的伤害
- 火灾爆炸: 火灾或爆炸事故
- 其他伤害: 不属于以上类型的其他伤害

## 事故等级标准
- 一般事故: 死亡 < 3人
- 较大事故: 3 ≤ 死亡 < 10人
- 重大事故: 10 ≤ 死亡 < 30人
- 特别重大事故: 死亡 ≥ 30人

## 致因四维度定义
1. 人的因素 (human_factors): 与人的不安全行为相关的致因，如违章操作、疲劳作业、安全意识不足等
2. 设备/物因素 (machine_factors): 与机械设备、材料、工具相关的致因，如设备故障、安全装置失效等
3. 管理因素 (management_factors): 与组织管理相关的致因，如管理制度不健全、监管缺失等
4. 环境因素 (environmental_factors): 与作业环境相关的致因，如恶劣天气、照明不足等

## 输出格式要求
你必须输出一个严格的 JSON 对象，不要 Markdown 代码块包裹，直接输出 JSON：

{
  "title": "事故标题（必填，简短概括）",
  "date": "事故发生日期（ISO 格式 YYYY-MM-DD，未知则填 null）",
  "accident_time": "具体时刻（HH:MM 格式，未知则填 null）",
  "location": "省级行政区名称（未知则填 null）",
  "city": "地级市名称（未知则填 null）",
  "accident_location_detail": "具体部位描述（未知则填 null）",
  "project_name": "工程项目名称（未知则填 null）",
  "project_type": "工程类型（如房建工程、市政工程，未知则填 null）",
  "building_structure": "建筑结构（如框架结构，未知则填 null）",
  "construction_phase": "施工阶段（如主体施工，未知则填 null）",
  "accident_type": "事故类型（从枚举中选择，必填）",
  "severity": "事故等级（从枚举中选择，必填）",
  "deaths": 死亡人数（数值，未知则填 null，必填）,
  "injuries": 受伤人数（数值，未知则填 null，必填）,
  "direct_economic_loss": 直接经济损失（万元，数值，未知则填 null）,
  "trapped_count": 被困人数（数值，未知则填 null）,
  "direct_cause": "直接原因简述（未知则填 null）",
  "contractor": "施工单位名称（未知则填 null）",
  "weather": "天气状况（未知则填 null）",
  "human_factors": [{"category": "致因类别", "description": "具体描述"}],
  "machine_factors": [{"category": "致因类别", "description": "具体描述"}],
  "management_factors": [{"category": "致因类别", "description": "具体描述"}],
  "environmental_factors": [{"category": "致因类别", "description": "具体描述"}],
  "summary": "事故经过摘要（必填，100-200字概述）"
}

注意：title、accident_type、severity、deaths、injuries、summary 为必填。无法推断的字段输出 null。致因数组为空数组 [] 表示未提及相关致因。
```

**代码位置**: `construction_agent/evaluation/experiment1/unified_prompt.py`

---

## 2. 用户消息模板

```
请提取以下建筑施工事故描述的结构化信息：

{accident_text}

请严格按照要求输出 JSON 格式的结果。
```

所有模型的用户消息使用相同的模板，仅替换 `{accident_text}` 为实际的 Silver Standard 样本文本。

---

## 3. 模型参数配置

### 3.1 DeepSeek V4

| 参数 | 值 |
|------|-----|
| **内部标识 (key)** | `deepseek_v4` |
| **模型 ID** | `deepseek-v4-flash` |
| **厂商** | DeepSeek |
| **API 协议** | Anthropic 兼容 |
| **Base URL** | `https://api.deepseek.com/anthropic` |
| **API Key** | `ANTHROPIC_API_KEY` |
| **temperature** | `0.0` |
| **max_tokens** | `4096` |
| **response_format** | 不支持（`supports_json_response=False`） |
| **说明** | 通过 DeepSeek 的 Anthropic 兼容端点调用，速度最快 |

### 3.2 Qwen 3.7

| 参数 | 值 |
|------|-----|
| **内部标识 (key)** | `qwen3` |
| **模型 ID** | `qwen-plus` |
| **厂商** | Alibaba (DashScope) |
| **API 协议** | OpenAI 兼容 |
| **Base URL** | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| **API Key** | `QWEN_API_KEY` |
| **temperature** | `0.0` |
| **max_tokens** | `4096` |
| **response_format** | 不支持（`supports_json_response=False`） |
| **说明** | 通义千问最新版，中文理解能力突出 |

### 3.3 GLM 5.2

| 参数 | 值 |
|------|-----|
| **内部标识 (key)** | `glm52` |
| **模型 ID** | `glm-5.2` |
| **厂商** | Zhipu AI (智谱) |
| **API 协议** | OpenAI 兼容 |
| **Base URL** | `https://open.bigmodel.cn/api/paas/v4` |
| **API Key** | `ZHIPU_API_KEY` |
| **temperature** | `0.0` |
| **max_tokens** | `16384` |
| **response_format** | 不支持（`supports_json_response=False`） |
| **说明** | 深度推理模型，response 中含 `reasoning_content`（内部推理链）；需要更大的 max_tokens 保证推理完成后有足够空间输出内容 |

### 3.4 Kimi K2.6

| 参数 | 值 |
|------|-----|
| **内部标识 (key)** | `kimi` |
| **模型 ID** | `kimi-k2.6` |
| **厂商** | Moonshot AI (月之暗面) |
| **API 协议** | OpenAI 兼容 |
| **Base URL** | `https://api.moonshot.cn/v1` |
| **API Key** | `KIMI_API_KEY` |
| **temperature** | `1.0`（**K2.x 系列强制要求**，不可设为 0.0） |
| **max_tokens** | `8192` |
| **response_format** | 不支持（`supports_json_response=False`） |
| **说明** | 强制 temperature=1.0，每次输出略有随机性；响应可能包含 Markdown 代码块包裹的 JSON |

---

## 4. 各模型参数对比总表

| 参数 | DeepSeek V4 | Qwen 3.7 | GLM 5.2 | Kimi K2.6 |
|------|:-----------:|:--------:|:-------:|:---------:|
| temperature | 0.0 | 0.0 | 0.0 | **1.0** |
| max_tokens | 4096 | 4096 | **16384** | 8192 |
| response_format | ❌ | ❌ | ❌ | ❌ |
| API 协议 | Anthropic | OpenAI | OpenAI | OpenAI |
| 推理模型 | ❌ | ❌ | ✅ | ❌ |
| 典型延迟/条 | ~7s | ~10s | ~50s | ~46s |

---

## 5. 评测数据集

| 项目 | 值 |
|------|-----|
| **数据集** | Silver Standard |
| **样本规模** | 200 条（分层抽样） |
| **分层维度** | 事故类型（11 种） |
| **数据路径** | `data/silver_standard.json` |
| **GT 字段** | 16 个基础字段 + 四维度致因 |

**抽样策略**: 按事故类型比例分层抽样，每类型至少 3 条，不足 200 条时从剩余数据随机补全。

---

## 6. 评测指标

### 子任务 A：基础字段提取

| 字段 | 类型 | 指标 |
|------|:----:|:----:|
| accident_type | 分类(11) | Accuracy |
| severity | 分类(4) | Accuracy |
| location, city, date | 字符串 | Exact Match |
| project_type, construction_phase | 字符串 | 语义等价匹配 |
| building_structure, weather | 字符串 | 语义等价匹配 |
| direct_cause | 文本 | 语义等价匹配 |
| deaths, injuries | 数值 | Exact Match |

### 子任务 B：致因维度提取

| 维度 | 指标 |
|------|:----:|
| human_factors (二元) | Precision / Recall / F1 |
| machine_factors (二元) | Precision / Recall / F1 |
| management_factors (二元) | Precision / Recall / F1 |
| environmental_factors (二元) | Precision / Recall / F1 |
| **四维度平均** | **Macro-Avg F1** |

### 子任务 B2：致因类目匹配

| 维度 | 指标 |
|------|:----:|
| 各维度致因类别 | 类别级 Precision / Recall / F1 (Jaccard) |

### 综合得分

```
综合 = 基础字段Accuracy × 0.30 + 致因F1 × 0.35
```

---

## 7. 评估基准 (Ground Truth)

使用 Silver Standard（972 条）作为评估基准。Silver Standard 由以下方式构建：
1. CSV 原始标签（事故类型编码、等级、伤亡人数、省份）
2. V1+V2 一致的值直接采用
3. 不一致时按规则裁决（如优先 V2 的 CoT 提取值）
4. 致因字段取 V1+V2 的合并去重结果

Silver Standard 中的致因维度的空数组 `[]` 表示该事故的文本中未提及该维度致因（非标注遗漏）。

---

## 8. 实验可重复性说明

为保证实验的可重复性和科学性：

1. **统一提示词**: 所有模型使用完全相同的 System Prompt 和用户消息模板
2. **固定随机种子**: Python `random.seed()` 在抽样时使用固定种子
3. **温度控制**: 除 Kimi 强制 temperature=1.0 外，其他模型均设为 0.0
4. **相同评测集**: 所有模型在相同的 200 条分层抽样样本上评测
5. **相同评测代码**: 指标计算代码在 `metrics.py` 中统一定义
6. **扩展性**: 新增模型只需在 `config.py` 中添加 ModelConfig 条目，无需修改评测代码

### 添加国外模型的步骤

```python
# 在 config.py 中添加：
ModelConfig(
    name="Claude Sonnet 4.6",
    key="claude_sonnet",
    provider="Anthropic",
    model_id="claude-sonnet-4-6",
    api_key_env="ANTHROPIC_API_KEY",
    api_type="anthropic",          # 使用 AnthropicCompatibleClient
    description="性价比标杆",
    enabled=True,
    supports_json_response=True,   # Anthropic 支持 response_format
    max_tokens=4096,
    temperature=0.0,
)

ModelConfig(
    name="GPT-5",
    key="gpt5",
    provider="OpenAI",
    model_id="gpt-5",
    api_key_env="OPENAI_API_KEY",
    api_type="openai",             # 使用 OpenAICompatibleClient
    description="行业标杆",
    enabled=True,
    supports_json_response=True,   # OpenAI 支持 response_format
    max_tokens=4096,
    temperature=0.0,
)

ModelConfig(
    name="Gemini 2.5 Pro",
    key="gemini_pro",
    provider="Google",
    model_id="gemini-2.5-pro",
    api_key_env="GEMINI_API_KEY",
    api_type="openai",             # Gemini 也走 OpenAI 兼容
    description="原生多模态",
    enabled=True,
    supports_json_response=True,
    max_tokens=4096,
    temperature=0.0,
)
```

---

## 9. 代码文件结构

```
construction_agent/evaluation/experiment1/
├── __init__.py              # 包声明
├── config.py                # 模型配置（ModelConfig 定义 + 所有模型参数）
├── model_clients.py         # API 客户端（OpenAICompatibleClient + AnthropicCompatibleClient）
├── unified_prompt.py        # 统一 System Prompt + 用户消息模板
├── experiment_runner.py     # 实验编排器（抽样 → API调用 → 结果收集）
├── metrics.py               # 指标计算（基础字段 + 致因维度 + 类目匹配）
├── comparison_report.py     # 对比报告生成（MD报告 + JSON指标 + 图表数据）
└── main.py                  # CLI 入口

experiments/experiment1/     # 实验结果输出目录
```

---

## 10. 运行命令

```bash
# 运行评测（200条 × 指定模型）
source .venv/bin/activate && source .env && PYTHONPATH=/home/suntianhao/construction_agent \
  python -m construction_agent.evaluation.experiment1.main run \
    --models deepseek_v4 qwen3 glm52 kimi

# 生成对比报告
python -m construction_agent.evaluation.experiment1.main report \
  experiments/experiment1/experiment1_20260625_*.json
```
