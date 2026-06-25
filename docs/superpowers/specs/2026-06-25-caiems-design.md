# CAIEMS: Construction Accident Intelligent Extraction and Mining System

> **设计文档 v1.0**
> **日期**: 2026-06-25
> **目标期刊**: Expert Systems with Applications (ESWA)

---

## 1. 项目概述

### 1.1 背景

建筑施工事故是建筑行业面临的重大安全问题。传统的事故分析方法依赖人工从大量非结构化文本中提取信息，效率低且难以规模化。大语言模型（LLM）的发展为自动化事故信息提取提供了新可能，但现有研究仍停留在单一提示词策略，缺乏系统性的多策略对比和完整的端到端解决方案。

### 1.2 目标

构建一个**多策略 LLM 驱动的建筑施工事故智能提取与挖掘系统（CAIEMS）**，实现：

1. 多样化输入源（文本、PDF、OCR、CSV、RSS、定时抓取）
2. 多源搜索增强（百度、Bing、图片搜索）
3. 五策略智能提取（V1-V4 + V2.5 含 HFACS-CS 编码）
4. 致因双框架分析（5M1E + HFACS-CS 六层 29 类）
5. 数据挖掘（时序趋势、关联规则、事故聚类）
6. Web 在线平台与数据服务

### 1.3 核心贡献

1. **系统**: 提出 CAIEMS——多策略 LLM 驱动的施工事故智能提取系统
2. **数据集**: 基于最优 Pipeline 构建 1153 条 × 37 字段的高质量结构化事故数据库
3. **评估**: 在 972 条银标准上系统对比五种提取策略，V2（CoT+FS）达 97.1% 综合准确率
4. **挖掘**: 时序趋势/关联规则/模式聚类揭示深层事故规律
5. **平台**: 部署在线 Web 平台，支持实时提取和数据导出

---

## 2. 系统架构

### 2.1 四层架构

```
┌─────────────────┐   ┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│   输入层         │   │   增强检索层      │   │   智能提取层      │   │   数据分析层     │
├─────────────────┤   ├─────────────────┤   ├──────────────────┤   ├─────────────────┤
│ ├ 用户文本输入  │   │ ├ 百度搜索      │   │ ├ V1: 基础提取   │   │ ├ 时序趋势分析   │
│ ├ PDF 事故简报  │──▶│ ├ Bing搜索      │──▶│ ├ V2: CoT+FS    │──▶│ ├ 关联规则挖掘   │
│ │ ├ 文字型PDF  │   │ ├ 图片搜索      │   │ ├ V2.5: +HFACS  │   │ ├ 事故模式聚类   │
│ │ └ 扫描型PDF  │   │ └ 多查询去重    │   │ ├ V3: 多技能协同 │   │ ├ HFACS共现网络  │
│ ├ CSV/XLSX批量 │   │                 │   │ └ V4: Agent驱动 │   │ └ 严重程度预测   │
│ ├ RSS/定时抓取 │   │  多模态融合     │   │                  │   │                  │
│ └ 网页爬虫     │   │ ├ 搜索增强文本  │   │ 致因双框架      │   │                  │
│                 │   │ └ 图片元数据    │   │ ├ 5M1E 归一化    │   │                  │
│ PDF预处理       │   │                 │   │ └ HFACS-CS 6层  │   │                  │
│ ├ PyMuPDF      │   │                 │   │   29类编码       │   │                  │
│ ├ PaddleOCR    │   │                 │   │                  │   │                  │
│ └ Tesseract    │   │                 │   │                  │   │                  │
└─────────────────┘   └─────────────────┘   └──────────────────┘   └─────────────────┘
                                                          │
                                                          ▼
                                                 ┌──────────────────┐
                                                 │  Web 展示与数据服务│
                                                 │                   │
                                                 │ ├ 数据集浏览器    │
                                                 │ ├ 致因可视化      │
                                                 │ ├ 趋势分析展示    │
                                                 │ ├ 数据挖掘展示    │
                                                 │ ├ 实时提取入口    │
                                                 │ └ 数据导出下载    │
                                                 └──────────────────┘
```

### 2.2 模块职责与接口

| 模块 | 职责 | 输入 | 输出 | 依赖 |
|------|------|------|------|------|
| 输入层 | 多样化输入源统一处理 | 文本/PDF/CSV/RSS | 标准化文本列表 | PyMuPDF, PaddleOCR, feedparser |
| 增强检索层 | 多源搜索 + 多查询 + 图片 | 事故文本 | 增强文本 + 图片路径 | Baidu/Bing API, ImageSearcher |
| 智能提取层 | V1-V4 五策略提取 | 增强文本 | Accident 结构化对象 | Claude API |
| 致因分析 | 5M1E + HFACS-CS 编码 | 致因文本字段 | 编码结果 + 统计 | hfacs.py, cause_dictionary.json |
| 数据挖掘层 | 时序/关联/聚类分析 | 数据集（Accident[]） | 统计结果 + 可视化 | statsmodels, mlxtend, sklearn |
| Web 展示层 | 用户交互 & 数据服务 | 数据集 + 分析结果 | HTML + JSON + 导出文件 | Flask |

---

## 3. 模块详细设计

### 3.1 多样化输入源（Module 1）

#### 3.1.1 现有输入

- **用户文本输入**: CLI `process` 子命令 + `interactive` REPL 模式 + Web 表单
- **CSV/XLSX 批量**: 通过 `batch` 子命令读取，经 `DatasetManager` 导入

#### 3.1.2 PDF 输入（新增）

**文件**: `construction_agent/input/pdf_processor.py`

```python
class PDFProcessor:
    """
    PDF 事故文档处理器
    支持文字型 PDF（PyMuPDF）和扫描型 PDF（PaddleOCR）
    
    Usage:
        processor = PDFProcessor()
        accidents = processor.process("事故通报.pdf")
    """
    
    DETECTION_STRATEGIES = {
        "text": {"engine": "PyMuPDF", "min_text_ratio": 0.1},
        "scanned": {"engine": "PaddleOCR", "lang": "ch"},
    }
    
    def process(self, pdf_path: str) -> List[AccidentInput]:
        # 1. 类型检测
        pdf_type = self._detect_type(pdf_path)
        # 2. 文本提取
        raw_text = self._extract_text(pdf_path, pdf_type)
        # 3. 段落分割
        segments = self._split_into_accidents(raw_text)
        # 4. 标准化
        return [AccidentInput(text=seg) for seg in segments]
    
    def _detect_type(self, path) -> str: ...
    def _extract_text(self, path, type) -> str: ...
    def _split_into_accidents(self, text) -> List[str]: ...
```

**段落分割策略**: 规则启发式（空行分隔符 `\n{2,}` + 标题模式 `\d+[、.．]`）+ LLM 辅助判断。

#### 3.1.3 RSS 订阅与定时抓取（新增）

**文件**: `construction_agent/input/rss_collector.py`

```python
class RSSCollector:
    """自动采集事故通报"""
    
    SOURCES = [
        {"name": "应急管理部", "url": "https://...", "type": "rss"},
        {"name": "住建部安全通报", "url": "https://...", "type": "web"},
    ]
    
    def collect(self) -> List[AccidentInput]:
        results = []
        for source in self.SOURCES:
            if source.type == "rss":
                results += self._parse_rss(source.url)
            else:
                results += self._crawl_web(source.url)
        return self._deduplicate(results)
```

### 3.2 联网搜索增强（Module 2）

**已实现**: `construction_agent/search/`

```python
# 搜索增强 Pipeline
class SearchPipeline:
    def enhance(self, text: str) -> AugmentedText:
        # 1. 三查询策略
        queries = self._generate_queries(text)  # [原文, 事件, 事件+调查报告]
        # 2. 多源并行搜索
        bing_results = BingSearchEngine().search_accident(text)
        baidu_results = BaiduSearchEngine().search_accident(text)
        # 3. 去重合并
        merged = self._deduplicate(bing_results + baidu_results)
        # 4. 图片搜索
        images = ImageSearcher().search_accident_images(text)
        # 5. 构建增强文本
        return self._build_enhanced(text, merged, images)
```

**后续增强**: 增加搜狗微信/头条搜索源、BM25 相关性评分。

### 3.3 智能提取层（Module 3）

**已实现**: `construction_agent/versions/`

| 版本 | 策略 | API 调用/条 | Pipeline 文件 | 提取器 |
|:----:|------|:----------:|---------------|--------|
| V1 | 基础 tool_use 提取 | 1 | `v1/pipeline.py` | `extraction/claude_extractor.py` |
| V2 | CoT + Five-shot | 1 | `v2/pipeline.py` | + CoT 系统提示词 |
| V2.5 | V2 + HFACS 编码 | 2 | `v2_5/pipeline.py` | + `v2_5/encoder.py` |
| V3 | 六技能协同 | 6 | `v3/pipeline.py` | 独立 HFACS-CS 技能模块 |
| V4 | ReAct Agent | 1+ | `v4/pipeline.py` | Agent 自主决策 |

### 3.4 致因分析双框架（Module 4）

**已实现**:

1. **5M1E 归一化**: `data/cause_5m_normalization.json`，将致因文本映射到人/机/料/法/环/测六维度
2. **HFACS-CS 编码**: `construction_agent/hfacs.py` + `data/hfacs_cs_taxonomy.json`

HFACS-CS 六层结构:

| 层级 | 编码 | 名称 | 子类数 | 映射 V1 维度 |
|:----:|:----:|------|:------:|:------------:|
| L1 | O | 组织因素 | 4 | management |
| L2 | S | 不安全监督 | 5 | management |
| L3 | P | 不安全行为前提条件 | 4 | human |
| L4 | A | 不安全行为 | 5 | human |
| L5 | W | 施工现场条件 | 6 | machine |
| L6 | E | 物理环境 | 5 | environment |

### 3.5 数据挖掘层（Module 5）

#### 3.5.1 时序趋势分析（必做）

**文件**: `construction_agent/mining/time_series_analyzer.py`

```python
class TimeSeriesAnalyzer:
    def yearly_trend(self, data) -> Dict:
        """年度趋势 + Mann-Kendall 检验"""
    def seasonal_pattern(self, data) -> Dict:
        """季节模式 + 热力图"""
    def accident_type_temporal(self, data) -> Dict:
        """事故类型的时间分布差异"""
    def time_period_distribution(self, data) -> Dict:
        """时段/光照条件分析"""
```

#### 3.5.2 致因关联规则（必做）

**文件**: `construction_agent/mining/association_miner.py`

```python
class AssociationMiner:
    def mine_hfacs_rules(self, data, min_support=0.02, min_confidence=0.5):
        """HFACS-CS 编码关联规则"""
    def mine_5m1e_rules(self, data):
        """5M1E 维度关联规则"""
    def mine_cross_field_rules(self, data):
        """事故类型 × 致因 × 等级的跨字段规则"""
```

#### 3.5.3 事故模式聚类（必做）

**文件**: `construction_agent/mining/accident_clusterer.py`

```python
class AccidentClusterer:
    def cluster_by_features(self, data, n_clusters=5):
        """基于事故特征（类型/等级/致因等）的聚类"""
    def cluster_by_hfacs(self, data):
        """基于 HFACS-CS 编码向量的聚类"""
    def visualize_clusters(self, data, labels):
        """t-SNE + 类中心热力图"""
```

### 3.6 Web 展示层（Module 6）

**现有页面**: 首页 `/`、数据集 `/dataset`、致因分析 `/causes`（Flask + 14 REST API）

**新增页面**:

| 页面 | 路由 | 功能 |
|:----:|:----:|------|
| 趋势分析 | `/trends` | 时序图 + 季节/时段分析 + 地域热力图 |
| 数据挖掘 | `/mining` | 聚类结果 + 关联规则 + 共现网络 |
| PDF 上传 | `/upload` | 上传 PDF 自动处理并提取 |

---

## 4. 数据模型

### Accident 核心模型（37字段）

```python
class Accident(BaseModel):
    # 标识
    id: str                          # MD5(title+date+city)[:8]
    
    # 基础信息 (12)
    title: str                       # 事故标题
    date: Optional[date]             # 事故发生日期
    accident_time: Optional[str]     # 具体时刻 HH:MM
    location: Optional[str]          # 省级地点
    city: Optional[str]              # 地级市
    accident_location_detail: Optional[str]  # 具体部位
    project_name: Optional[str]      # 项目名称
    project_type: Optional[str]      # 工程类型
    building_structure: Optional[str]  # 建筑结构
    building_completion_year: Optional[int]
    construction_phase: Optional[str]  # 施工阶段
    weather: Optional[str]           # 天气
    
    # 伤亡经济 (5)
    deaths: Optional[int]            # 死亡人数
    injuries: Optional[int]          # 受伤人数
    trapped_count: Optional[int]     # 被困人数
    direct_economic_loss: Optional[float]  # 直接经济损失
    
    # 时间派生 (4)
    day_of_week: Optional[str]       # 星期
    season: Optional[str]            # 季节
    time_period: Optional[str]       # 时段
    lighting: Optional[str]          # 光照
    
    # 事故属性 (2)
    accident_type: AccidentType      # 事故类型 (11枚举)
    severity: SeverityLevel          # 事故等级 (4级)
    
    # 致因分析 (5)
    direct_cause: Optional[str]
    contractor: Optional[str]
    human_factors: List[HumanFactor]
    machine_factors: List[MachineFactor]
    management_factors: List[ManagementFactor]
    environmental_factors: List[EnvironmentalFactor]
    
    # HFACS 扩展 (3)
    hfacs_codes: List[str]
    hfacs_details: List[HfacsCode]
    hfacs_encoding_version: Optional[str]
    
    # 搜索元数据 (6)
    source_urls: List[str]
    summary: Optional[str]
    search_snippets: List[str]
    search_queries: List[str]
    images: List[str]
    image_urls: List[str]
    
    # 内部元数据 (2)
    raw_input: Optional[str]
    created_at: datetime
```

---

## 5. 实验设计

### 5.1 数据集

| 数据集 | 规模 | 用途 |
|-------|:----:|------|
| Merged Dataset | 1153 × 37字段 | 最终数据库 + 数据挖掘 |
| Silver Standard | 972 × 32字段 | 提取精度评估基准 |
| Ground Truth | 1093 × 15字段 | 人工标注基准 |

### 5.2 提取精度对比

| 对比组 | 参与版本 | 指标 | 状态 |
|-------|:--------:|------|:----:|
| 基础字段 | V1/V2/V4 | Accuracy/F1 | ✅ 已有 |
| 致因维度 | V1/V2/V3/V4 | Precision/Recall/F1 | ✅ 已有 |
| 消融分析 | V1 vs V2 | 各指标提升幅度 | ✅ 已有 |

### 5.3 数据挖掘实验

| 实验 | 方法 | 评估方式 | 状态 |
|:----:|:----:|---------|:----:|
| 时序趋势 | STL + Mann-Kendall | p值 + 趋势强度 | 🆕 待实施 |
| 关联规则 | Apriori | Support/Confidence/Lift | 🆕 待实施 |
| 事故聚类 | K-Means/DBSCAN | Silhouette Score + 类特征 | 🆕 待实施 |
| 共现网络 | NetworkX | 模块度 + 度中心性 | 🆕 补充 |
| 严重度预测 | XGBoost/RF | Acc/F1/AUC | 🆕 补充 |

---

## 6. 论文大纲

### 候选标题

> **CAIEMS: A Multi-Strategy LLM-Powered Intelligent Extraction and Mining System for Construction Accident Data**

### 章节结构（10章）

| 章 | 标题 | 页数 | 内容要点 |
|:--:|------|:----:|---------|
| 1 | Introduction | 2-3 | 施工事故分析痛点 → 人工/传统 NLP 局限 → LLM 机遇 → 现有系统不足 → 本系统贡献 |
| 2 | Related Work | 2-3 | LLM 事故分析 / HFACS 在施工领域 / Expert Systems 安全应用 |
| 3 | System Architecture | 3-4 | 四层架构 + 六模块 + 数据流 |
| 4 | Multi-Strategy Extraction | 4-5 | V1-V4 设计细节 + 致因双框架 + 搜索增强 |
| 5 | Dataset Construction | 3-4 | 数据来源 → 提取 → 去重合并 → Silver Standard → 统计 |
| 6 | Experimental Evaluation | 4-5 | 提取精度对比 + 消融实验 + 关键发现 |
| 7 | Data Mining Results | 5-6 | 时序/关联/聚类/共现/预测 — 核心增量 |
| 8 | Web Platform | 2-3 | 在线系统功能 + API + 与现有工具对比 |
| 9 | Discussion | 2-3 | 发现总结 / 局限性 / 与参考论文对比 / 未来方向 |
| 10 | Conclusion | 1 | 总结 + 贡献重申 |

### 图表清单

| 编号 | 类型 | 内容 | 状态 |
|:----:|:----:|------|:----:|
| Fig 1 | 系统架构图 | CAIEMS 四层架构 | 📝 待绘制 |
| Fig 2 | 流程图 | V1-V4 Pipeline 流程对比 | 📝 待绘制 |
| Fig 3 | 统计图 | 1153 条数据集分布（类型/年份/等级/地域） | 📝 待绘制 |
| Fig 4 | 柱状图 | V1/V2/V4 基础字段 Accuracy 对比 | ✅ 已有数据 |
| Fig 5 | 雷达图 | V1-V4 四维度 F1 对比 | ✅ 已有数据 |
| Fig 6 | 趋势图 | 年度趋势 + 季节热力图 | 🆕 待生成 |
| Fig 7 | 网络图 | HFACS-CS 关联规则 / 共现网络 | 🆕 待生成 |
| Fig 8 | 散点图 | t-SNE 聚类 + 类中心热力图 | 🆕 待生成 |
| Fig 9 | 截图 | Web 平台界面 | ✅ 可截图 |
| Table 1 | 对比表 | V1-V4 策略对比 | ✅ 已有 |
| Table 2 | 统计表 | 数据集字段覆盖 | ✅ 已有 |
| Table 3 | 精度表 | 16字段 × 3版本的 Accuracy/F1 | ✅ 已有 |
| Table 4 | 规则表 | 关联规则 Top-10 | 🆕 待生成 |
| Table 5 | 聚类表 | 各类事故的典型特征 | 🆕 待生成 |

---

## 7. 实施路线图

| 阶段 | 时间 | 任务 | 产出 |
|:----:|:----:|------|------|
| **1. 数据增强** | 第1-2月 | PDF/OCR/RSS/定时抓取 + 扩充数据 + 完善 HFACS | 输入 Pipeline + 扩充数据集 |
| **2. 数据挖掘** | 第2-3月 | 时序/关联/聚类/共现/预测实现 + 可视化 | 数据挖掘模块 + 图表 |
| **3. Web 升级** | 第2-3月 | 趋势/挖掘页面 + PDF 上传 + 交互优化 | 升级版 Web 平台 |
| **4. 实验完善** | 第3-4月 | 补充实验 + 统计检验 + 图表美化 | 完整实验数据 |
| **5. 论文撰写** | 第4-5月 | 逐章撰写 + 制图制表 + 英文润色 | 论文初稿 |
| **6. 投稿准备** | 第5-6月 | 内部审稿 + 格式调整 + 投稿 | 正式投稿 |

---

## 8. 技术栈

| 层级 | 技术 | 版本 | 用途 |
|------|------|:----:|------|
| 语言 | Python | 3.9+ | 主开发语言 |
| LLM API | Anthropic Claude (tool_use) | - | 智能提取 |
| 搜索 | Baidu/Bing Search API | - | 联网检索 |
| PDF | PyMuPDF / PaddleOCR | - | 文档解析 |
| 数据挖掘 | statsmodels / mlxtend / sklearn | - | 时序/关联/聚类 |
| 可视化 | matplotlib / seaborn / networkx / pyvis | - | 图表生成 |
| Web | Flask | - | Web 平台 |
| 数据模型 | Pydantic v2 | - | 结构化建模 |
| 图片 | httpx / BeautifulSoup4 | - | 爬虫 |
| 任务调度 | schedule / APScheduler | - | 定时任务 |

---

## 9. 与参考论文的对比

| 维度 | 参考论文 (Liu et al., ESWA) | 本系统 (CAIEMS) |
|------|---------------------------|-----------------|
| 领域 | 通用航空 (NTSB) | 建筑施工（中国） |
| 数据 | GAHFACS: 15230条（航空） | Merged: 1153条 × 37字段 |
| 方法 | HFACS-CoT / HFACS-CoT+ | V1-V4五种策略对比选择最优 |
| 致因框架 | HFACS 4层 (组织/监督/前提/行为) | HFACS-CS 6层 (加现场/环境) + 5M1E |
| 多模态 | 仅文本（目击者叙述） | 文本 + 图片 + PDF + RSS |
| 系统化 | 单一提取实验 | 完整系统：采集→提取→挖掘→展示 |
| 数据挖掘 | 无 | 时序/关联/聚类/预测 |
| 代码/平台 | 未开放 | 开源 + Web 在线平台 |

---

## 10. 风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|:----:|:------:|:----:|---------|
| API 费用过高 | 中 | 中 | V2 策略仅需 1 call/条，控制成本 |
| OCR 精度不足（老旧PDF） | 中 | 中 | 人工复核 + LLM 辅助纠正 |
| 挖掘结果不显著 | 低 | 低 | 多种方法尝试，选最优展示 |
| 论文审稿周期长 | 高 | 中 | 选择 ESWA（一审约 6-8 周） |
| 数据量不足被质疑 | 中 | 中 | 扩充数据至 1500+，强调质量重于数量 |
