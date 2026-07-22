# CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Classification, and Online Database Construction

## Abstract

Construction accident investigation reports contain rich causal information essential for safety management, yet they remain overwhelmingly unstructured, making systematic analysis at scale infeasible. This paper presents CAIEMS (Construction Accident Intelligent Extraction and Mining System), an end-to-end LLM-driven pipeline that transforms unstructured Chinese construction accident narratives into structured, causally annotated database records. At the core of CAIEMS is CAICS (Construction Accident Integrated Cause System), a purpose-built five-layer, 19-category causal taxonomy designed for the causal structure of construction accidents, complemented by dual-framework encoding into HFACS-CS (six layers, 29 categories). We systematically evaluate six prompting strategies on DeepSeek V4 against a silver-standard dataset of 972 expert-annotated records. Few-Shot prompting achieves the highest average F1 of 0.737, outperforming domain-knowledge Chain-of-Thought (F1 = 0.676) — a reversal of the strategy ranking established on GPT-4o by Liu et al. (2025). We identify and diagnose the **Lexical Gatekeeping Effect**: when domain knowledge is embedded as conditional keyword checks, the model treats listed keywords as mandatory gates rather than illustrative examples, causing systematic recall collapse (F1 = 0.425). A Two-Step Judgment remedy — replacing conditional checks with authorized semantic inference — recovers F1 to 0.676 (+59%). Human-AI agreement validation via Cohen's Kappa demonstrates that the LLM achieves inter-rater reliability (Kappa 0.79–0.96 across seven dimensions) within the range of human expert agreement. The resulting online database of 1,203 accident records with 38 structured fields and dual-taxonomy causal annotation is, to our knowledge, the first interactive construction accident database built through an LLM-driven extraction pipeline, deployed as a Web platform with REST API access.

**Keywords**: Large language model, construction safety, accident analysis, information extraction, HFACS, prompt engineering, causal classification, knowledge graph

---

## 中文摘要

建筑施工事故调查报告蕴含丰富的事故致因信息，但以非结构化文本形式存在，难以实现规模化的系统性分析。本文提出CAIEMS（建筑施工事故智能提取与挖掘系统），一个端到端的LLM驱动管线，将非结构化的中文建筑施工事故文本转化为结构化的、带有因果标注的数据库记录。CAIEMS的核心是CAICS（建筑施工事故综合致因系统），一个专为建筑施工事故因果结构设计的5层19类致因分类体系，辅以HFACS-CS（6层29类）双框架编码。我们在972条专家标注的银标准数据集上，系统评估了DeepSeek V4上的六种提示策略。少样本提示策略（Few-Shot）取得了最高平均F1值0.737，优于领域知识链式推理（CAICS-CoT，F1=0.676）——这一策略排名与Liu等（2025）在GPT-4o上建立的结果相反，揭示了最优提示策略的模型依赖性。我们识别并诊断了**词汇门控效应**：当领域知识以条件关键词检查形式嵌入提示时，模型将列出的关键词视为强制性门槛而非示例线索，导致系统性召回崩溃（F1=0.425）。通过"两步判断"补救方案——将条件检查替换为授权的语义推断——F1恢复到0.676（+59%）。人机一致性验证（Cohen's Kappa）表明，LLM在所有七个评估维度上均达到人类专家间信度范围（Kappa 0.79–0.96）。最终构建的包含1203条记录、38个结构化字段和双分类体系因果标注的在线数据库，是首个通过LLM驱动提取管线构建的交互式建筑施工事故数据库，已部署为Web平台并提供REST API访问。

**关键词**：大语言模型，建筑施工安全，事故分析，信息提取，HFACS，提示工程，致因分类，知识图谱

---

## 1. Introduction

The construction industry accounts for one of the highest occupational fatality rates worldwide. In China, the Ministry of Housing and Urban-Rural Development reported hundreds of fatal construction accidents annually over the past decade, with collapse, fall-from-height, and hoisting incidents being the most recurrent types (Togan et al., 2025). Each accident generates formal investigation reports that contain rich causal information, yet these documents remain unstructured natural language text, making systematic analysis difficult at scale.

Manual extraction and coding of accident reports is labor-intensive and does not scale to the volume of cases produced nationally. Traditional natural language processing approaches require large labeled datasets tailored to the construction safety domain, which are expensive and time-consuming to produce (Kong & Ahn, 2024). Rule-based and keyword-matching methods generalize poorly across the varied linguistic patterns found in accident narratives. Machine learning classifiers such as BERT and DistilBERT offer improvements but still depend on substantial annotated corpora for acceptable performance (Togan et al., 2025). These limitations create a pressing need for a more flexible, scalable paradigm for construction accident information extraction.

Recent advances in large language models have opened new possibilities for automated text analysis in safety-critical domains. Liu et al. (2025) demonstrated that GPT-4o with HFACS-guided Chain-of-Thought reasoning can extract causal classifications from aviation accident narratives with improved recall. Kim and Han (2024) applied Claude to Korean construction fall accidents under an HFACS framework, and Ahmadi et al. (2024) used GPT-4 to analyze OSHA construction reports. These studies, while pioneering, exhibit three notable gaps. First, no existing work has designed an LLM-optimized causal taxonomy specifically for the construction accident domain, with most studies adapting aviation-derived HFACS frameworks that misalign with the causal patterns found in construction narratives. Second, current approaches perform single-step extraction or classification tasks in isolation, without an end-to-end pipeline that connects data collection, information extraction, causal classification, database construction, and online access. Third, systematic human-AI agreement validation using Cohen's Kappa has not been conducted for LLM-based causal classification in the construction accident domain, leaving the reliability of LLM-generated causal labels uncharacterized.

To address these gaps, we propose CAIEMS (Construction Accident Intelligent Extraction and Mining System), a multi-strategy LLM-driven pipeline for construction accident information extraction, causal classification, and online database construction. At the core of CAIEMS is the CAICS (Construction Accident Integrated Cause System) taxonomy, a five-layer causal framework with 19 categories specifically designed and empirically validated for construction accident analysis. The system additionally implements an HFACS-CS encoding layer (6 layers, 29 categories) for cross-framework compatibility. CAIEMS integrates multi-source input processing (text, PDF, RSS, CSV), search-enhanced extraction, a six-strategy prompt engineering comparison framework, and an interactive web-based database platform.

The contributions of this work are fivefold. (C1) We propose CAICS, the first LLM-optimized causal taxonomy purpose-built for construction accident analysis, grounded in empirical analysis of 972 annotated Chinese accident records. (C2) Through systematic comparison of six prompting strategies on DeepSeek V4, we demonstrate that Few-Shot prompting achieves the highest F1 score (0.737), surpassing domain-knowledge Chain-of-Thought strategies — a reversal of the ranking established by Liu et al. (2025) on GPT-4o, revealing model-dependence in prompting effectiveness. (C3) We identify and diagnose the Lexical Gatekeeping Effect, a novel prompt engineering phenomenon wherein conditional-check Chain-of-Thought formulations induce systematic recall deficits due to the model treating listed keywords as necessary gates rather than illustrative examples, with broad implications for structured extraction system design. (C4) We construct and release a silver-standard dataset of 972 manually-validated Chinese construction accident records covering 38 fields across multiple accident types and 15 years, supporting reproducible evaluation. (C5) We develop the first online interactive construction accident database built entirely through an LLM-powered extraction pipeline, demonstrating practical feasibility for continuous operational deployment.

The remainder of this paper is organized as follows. Section 2 reviews related work on NLP, LLMs, HFACS-based classification, and knowledge graphs in construction safety. Section 3 describes the CAICS causal framework, the six prompting strategies, the HFACS-CS encoding methodology, and the CAIEMS system architecture. Section 4 presents the experimental evaluation, including overall strategy comparison, per-layer analysis, diagnostic investigation of the Lexical Gatekeeping Effect, model selection, and human-AI agreement validation. Section 5 details the dataset construction pipeline, statistical characterization, silver standard methodology, and online database implementation. Section 6 discusses the key findings, their theoretical and practical implications, comparisons with prior work, limitations, and future directions. Section 7 concludes the paper.

---

## 2. Related Work

### 2.1 NLP and Machine Learning for Construction Accident Analysis

Automated analysis of construction accident reports using computational methods has been a persistent research direction over the past decade. Early approaches relied on rule-based classification and keyword matching, which offered interpretability but suffered from limited coverage of the varied linguistic expressions found in accident narratives (Kong & Ahn, 2024). Togan et al. (2025) systematically evaluated a range of text-based analytics on construction safety documents, comparing TF-IDF, BERT, and DistilBERT for safety attribute extraction. Their results showed that transformer-based models significantly outperformed bag-of-words representations, yet all approaches required substantial labeled training data and exhibited degraded performance when applied to documents from projects or regions outside their training distribution.

Rafindadi et al. (2023) applied association rule mining to construction accident datasets, extracting patterns linking accident types with causal factors. The Apriori-based approach identified interpretable rules, but the mined associations were constrained by the quality and completeness of the input structured fields, which had themselves been manually coded. This circular dependency between data quality and analytical insight underscores a fundamental limitation: traditional ML and data mining approaches, however sophisticated, depend on pre-existing structured data that must be created through labor-intensive manual annotation. The bottleneck is not analytical capacity but the cost of producing structured data at scale.

### 2.2 Large Language Models for Accident Information Extraction

Large language models have introduced a paradigm shift in accident information extraction by enabling direct inference from unstructured text without task-specific training data. Ahmadi et al. (2024) evaluated GPT-4 on automatic analysis of OSHA construction accident reports, demonstrating that LLMs can extract accident type, severity, and causal factors with reasonable accuracy using zero-shot and few-shot prompts. Their work established the feasibility of LLM-based extraction for construction safety but was limited to a single model and did not explore systematic prompt engineering strategies.

Zhang et al. (2025) developed a GPT-based chatbot for subway construction accident knowledge retrieval, demonstrating the potential of conversational interfaces for safety information access. However, the system functioned as a question-answering tool rather than a structured extraction pipeline, and the extracted information was not persisted in a queryable database. Yan et al. (2025) extended the LLM-accident intersection to UAV accident forensics, applying HFACS-LLM reasoning to drone incident reports. These studies, taken together, establish that LLMs can extract meaningful information from accident texts across multiple domains. What remains absent is an end-to-end pipeline design that integrates extraction, validation, storage, and interactive access into a single operational system.

### 2.3 HFACS-Based Causal Classification with LLMs

The Human Factors Analysis and Classification System has been the dominant framework for accident causal analysis in high-risk industries. The intersection of HFACS with large language models represents a rapidly growing research frontier. Liu et al. (2025) proposed HFACS-CoT, a Chain-of-Thought prompting strategy embedding HFACS taxonomy knowledge into the reasoning chain, and evaluated it on 113 general aviation accident narratives from the NTSB database using GPT-4o. Their findings established domain-knowledge CoT as the optimal strategy, achieving recall of 74.9% and precision of 46.2% across ten HFACS sub-categories. This study is the most directly comparable benchmark to our work, differing in domain (aviation vs. construction), taxonomy (HFACS vs. CAICS), and evaluation model (GPT-4o vs. DeepSeek V4).

Kim and Han (2024) implemented an integrated HFACS-LLM approach for systematic analysis of fall accidents at Korean construction sites, using Claude to classify causal factors into HFACS categories. Their work demonstrated the feasibility of construction-domain HFACS classification but reported performance only at the aggregate level without per-category precision or recall metrics. Yan et al. (2025) and Ahmadi et al. (2025) extended HFACS-LLM integration to UAV forensics and GRPO-trained classifier systems respectively. Across all these studies, a common limitation emerges: each adapts a version of HFACS originally designed for aviation, and none proposes a causal taxonomy optimized for the causal structure of construction accidents, where management-root factors and physical barrier failures play proportionally larger roles than organizational factors.

### 2.4 Knowledge Graphs and Accident Databases

Structured accident databases and knowledge graphs represent the downstream application layer for extracted information. Liu and Luo (2025) proposed an LLM-based method for constructing knowledge graphs from construction accident reports, demonstrating that LLMs can extract entity-relation triples for graph population. Shi et al. (2025) developed a multi-model approach to construction site safety combining LLMs with computer vision, generating structured safety records from heterogeneous input sources. Xiao et al. (2026) presented KRAIL, a knowledge-driven framework for human reliability analysis that integrates accident data with cognitive models. Zhang et al. (2025) proposed GRAG-ProSafe, a question-answering system for construction safety that combines retrieval-augmented generation with knowledge graph structures. These contributions advance the state of the art in knowledge representation for construction safety. However, they share a structural limitation: each constructs a static knowledge artifact — a graph, a database dump, or an offline corpus — rather than an interactive, continuously updatable online database. Kong and Ahn (2024), in their systematic literature review of knowledge graphs for construction safety management, explicitly identified the gap between research prototypes and operational platforms. CAIEMS addresses this gap by implementing the extraction-classification pipeline as the backend of an interactive web platform.

---

## 3. Methodology

This section describes the methodological architecture of CAIEMS. The core challenge that shapes every design decision is that construction accident reports, as collected from public sources, are narrative documents written for administrative rather than analytic purposes. They describe what happened without explicitly labeling the causal structure. To extract structured causal classifications from such unstructured text, three interrelated problems must be solved: (1) what causal categories to use, (2) how to induce an LLM to reason through those categories reliably, and (3) how to build a system that does this at scale.

### 3.1 CAICS Causal Framework

The Construction Accident Intelligent Classification System (CAICS) is a 5-layer, 19-category causal taxonomy designed to capture the causal structure of construction accidents at multiple levels of abstraction. Its development was motivated by a structural limitation in existing taxonomies. The Human Factors Analysis and Classification System (HFACS) (Shappell & Wiegmann, 2000), originally designed for aviation accident investigation, has been adapted to construction through variants such as HFACS-CS (Garrett & Teizer, 2009) with 6 layers and 29 categories. While HFACS-CS provides a comprehensive framework, its categories reflect aviation's organizational hierarchy — where a defined chain of command and formal safety protocols are the norm — rather than the fragmented subcontracting structure typical of construction sites.

CAICS restructures the causal space into five layers that reflect the causal progression of a construction accident, following a "causal leakage" model from upstream latent failures to downstream consequence amplification:

**Management Root Layer (M)**: Five categories capture systemic safety management deficiencies that create the latent conditions for accidents. **M1** (inadequate site supervision) covers absence of on-site oversight and failure to detect unsafe conditions. **M2** (ineffective hazard inspection) addresses failure to inspect, failure to rectify identified hazards, and hazards persisting over time. **M3** (insufficient safety training) targets missing or perfunctory safety education and inadequate safety briefing. **M4** (deficient safety protocols) captures absence of safety plans, missing emergency procedures, and inadequate safety management systems. **M5** (market conduct violations) covers illegal subcontracting, unqualified contractors, and schedule compression — upstream market failures characteristic of the Chinese construction industry.

**Barrier Failure Layer (F)**: Four categories describe failures of physical barriers and protective systems. **F1** (missing edge/opening protection) includes absent or substandard scaffolding, edge protection, opening covers, and excavation barriers. **F2** (support system instability) covers formwork support, scaffolding, and temporary structure instability — the most frequent proximal cause of construction collapses. **F3** (equipment safety device failure) addresses limiters, anti-fall devices, and safety locks that have been damaged or bypassed. **F4** (PPE non-use) captures personal protective equipment not worn or substandard.

**Trigger Conditions Layer (T)**: Four categories capture the immediate material and environmental conditions that directly trigger accident occurrence. **T1** (equipment and machinery failure) covers sudden failure of tower cranes, hoists, and construction machinery. **T2** (material condition anomaly) addresses overloading, material defects, and material instability. **T3** (geological and environmental change) captures water inrush, slope instability, and foundation settlement. **T4** (meteorological and hydrological trigger) covers heavy rain, strong wind, ice/snow, and lightning that trigger accidents.

**Human Acts Layer (H)**: Four categories classify the direct human actions that constitute the "last line of defense" failure. **H1** (violation of operating procedures) captures clear deviations from construction plans and procedures. **H2** (improper command decisions) addresses management directing work despite known hazards. **H3** (risk perception failure) covers failure to recognize hazards and weak safety awareness. **H4** (inappropriate emergency response) captures uninformed rescue attempts and failure to evacuate after an accident.

**Consequence Amplification Layer (C)**: Two categories capture post-accident factors that worsen the outcome beyond what the initial event would predict. **C1** (delayed rescue response) covers missing emergency plans, delayed rescue, and secondary injuries during rescue. **C2** (information concealment) addresses failure to report in a timely manner and deliberate underreporting of accidents — a phenomenon documented in Chinese construction safety regulation.

Each CAICS code is paired with an operational definition and a set of diagnostic clues — explicit textual patterns and implicit indicators that an LLM can use for semantic inference. This design reflects a deliberate departure from keyword-based coding schemes. The full CAICS taxonomy is summarized in Table 1.

**Table 1.** CAICS causal taxonomy: 5 layers, 19 categories with operational definitions and original Chinese terms.

| Layer | Code | Category | 中文 | Operational Definition |
|-------|------|----------|------|----------------------|
| **M** Management Root | M1 | Inadequate site supervision | 现场监管缺失 | Absence or insufficiency of on-site oversight; failure to detect and correct unsafe conditions |
| | M2 | Ineffective hazard inspection | 隐患排查失效 | Failure to inspect, failure to rectify identified hazards, hazards persisting over time |
| | M3 | Insufficient safety training | 安全培训不足 | Missing or perfunctory safety education; inadequate safety briefing |
| | M4 | Deficient safety protocols | 制度规程漏洞 | Absence of safety plans, missing emergency procedures, inadequate safety management systems |
| | M5 | Market conduct violations | 市场行为违规 | Illegal subcontracting, unqualified contractors, schedule compression |
| **F** Barrier Failure | F1 | Missing edge/opening protection | 临边洞口防护缺失 | Absent or substandard scaffolding, edge protection, opening covers, or excavation barriers |
| | F2 | Support system instability | 支撑体系失稳 | Formwork support, scaffolding, or temporary structure instability |
| | F3 | Equipment safety device failure | 设备安全装置失效 | Limiters, anti-fall devices, safety locks damaged or bypassed |
| | F4 | PPE non-use | 个体防护未使用 | Personal protective equipment not worn or substandard |
| **T** Trigger Conditions | T1 | Equipment and machinery failure | 设备机具故障 | Sudden failure of tower cranes, hoists, or construction machinery |
| | T2 | Material condition anomaly | 物料状态异常 | Overloading, material defects, material instability |
| | T3 | Geological and environmental change | 地质环境突变 | Water inrush, slope instability, foundation settlement |
| | T4 | Meteorological and hydrological trigger | 气象水文触发 | Heavy rain, strong wind, ice/snow, lightning triggering accidents |
| **H** Human Acts | H1 | Violation of operating procedures | 违反操作规程 | Clear deviation from construction plans and procedures |
| | H2 | Improper command decisions | 违规指挥决策 | Management directing work despite known hazards |
| | H3 | Risk perception failure | 风险辨识失败 | Failure to recognize hazards; weak safety awareness |
| | H4 | Inappropriate emergency response | 应急行为失当 | Uninformed rescue attempts; failure to evacuate after accident |
| **C** Consequence Amplification | C1 | Delayed rescue response | 救援响应迟缓 | Missing emergency plans; delayed rescue; secondary injuries |
| | C2 | Information concealment | 信息迟报瞒报 | Failure to report in a timely manner; underreporting of accidents |

### 3.2 Six Prompt Strategies

To investigate how the structure of LLM prompts affects classification accuracy under the CAICS framework, we designed six prompting strategies following the taxonomy of Liu et al. (2025), which categorizes prompting approaches on two dimensions — whether the prompt includes intermediate reasoning steps and whether it incorporates domain knowledge.

**S0 (Zero-Shot)** provides only the CAICS code list and the target JSON output schema. No reasoning guidance, no examples, no domain knowledge beyond the code names themselves. This serves as a baseline condition.

**S1 (Few-Shot)** adds three expert-annotated example cases to the S0 prompt. Each example includes the full accident narrative, the CAICS codes assigned by a human annotator, and the annotator's causal reasoning chain. The examples were chosen to demonstrate variation in causal density, variation in evidence explicitness, and the implicit norm that over-labeling is preferred to under-labeling in safety-critical classification.

**S2 (Auto-CoT)** instructs the LLM to organize its reasoning along the M-to-F-to-T-to-H-to-C causal chain before producing the final classification. No domain-specific content beyond the chain order itself is provided.

**S3 (Plan-and-Solve)** requires the LLM to first generate an extraction plan — specifying which information from the narrative it will use to evaluate each CAICS layer — before executing the classification.

**S4 (CAICS-CoT)** embeds CAICS domain knowledge directly into the chain-of-thought prompt. Each of the 19 codes is presented alongside its diagnostic clues — the textual patterns and inferential heuristics described in Section 3.1. The prompt instructs the LLM to follow a **Two-Step Judgment** procedure for each code:

- **Step 1 (Explicit Matching)**: Scan the narrative for keyword-level evidence. If a diagnostic clue appears verbatim or near-verbatim, mark the code as textually supported.
- **Step 2 (Implicit Inference)**: If no explicit match is found, evaluate whether the code can be inferred from the nature of the accident. The authorization statement — "Safety analysis prefers over-labeling to under-labeling" — lowers the inference threshold appropriately for safety-critical classification.

This Two-Step design was introduced after initial experiments with a conditional-check formulation (S4 v1), which triggered the Lexical Gatekeeping Effect (detailed in Section 4.4). S4 v2 replaced the conditional-check format with Two-Step Judgment, where the implicit inference step explicitly authorizes the LLM to apply domain knowledge rather than match keywords.

**S5 (CAICS-CoT+)** extends S4 with a layer-wise decomposition: instead of evaluating all 19 codes in a single LLM call, the prompt dispatches five sequential calls, one per CAICS layer. The output of each call — a compressed summary of causal factors identified at that layer — is appended to the prompt for the next call, enforcing causal chain directionality.

**Table 2.** Comparison of six prompting strategies.

| Strategy | Reasoning guidance | Domain knowledge | Call structure |
|----------|-------------------|-----------------|----------------|
| S0 | None | None | Single |
| S1 | 3 examples | Examples only | Single |
| S2 | Causal chain order | None | Single |
| S3 | Plan-then-execute | None | Single (2-stage) |
| S4 | Two-Step Judgment | Full CAICS + diagnostic clues | Single |
| S5 | Two-Step Judgment | Full CAICS + diagnostic clues | Sequential (5 calls) |

### 3.3 HFACS-CS Encoding

After CAICS extraction, a secondary rule-based encoding step maps the 5-layer CAICS output onto the 6-layer, 29-category HFACS-CS taxonomy. This dual-framework approach addresses a tension inherent in domain-specific taxonomies: fine-grained categories improve sensitivity to domain-specific causal patterns but reduce comparability across domains. CAICS, with its construction-specific categories, enables granular within-domain analysis; HFACS-CS, with its broader categorization spanning aviation, maritime, and construction applications, enables cross-domain comparison.

The mapping is deterministic. Each CAICS code is assigned one or more target HFACS-CS codes. For illustration: CAICS M1 (inadequate site supervision) and M4 (deficient safety protocols) map to HFACS-CS organizational and supervisory categories; CAICS F2 (support system instability) and F3 (equipment safety device failure) map to HFACS-CS precondition categories; CAICS H1–H4 (human acts) map to HFACS-CS unsafe acts categories and their preconditions. CAICS C1–C2 (consequence amplification) have no direct HFACS-CS analogues and are mapped to an auxiliary marker indicating post-accident factors not covered by the standard taxonomy. Each mapping was validated by two domain experts with experience in both construction safety and HFACS-based coding.

### 3.4 System Architecture

The CAIEMS pipeline is organized into four layers:

**Input Layer**. The system accepts three input modalities: plain text, PDF parsing via the MinerU API, and tabular data in CSV/XLSX format for batch import. RSS feeds provide ongoing collection from construction-safety news aggregators and government accident report portals.

**Enhanced Retrieval Layer**. When processing an accident report, the system performs multi-source web search using both Baidu and Bing APIs to retrieve supplementary information. Three queries are generated from the report's title, location, and key phrases. Search results are concatenated with the original report text to form an enriched input document, compensating for the brevity typical of Chinese accident bulletins.

**Intelligent Extraction Layer**. The LLM executes one of the six prompting strategies (Section 3.2), returning structured JSON output containing assigned CAICS codes, evidence supporting each code, and confidence ratings. The HFACS-CS encoding module then maps the output to the 29-category HFACS-CS taxonomy. A final normalization step applies the 5M1E (Man, Machine, Material, Method, Measurement, Environment) framework.

**Data Management Layer**. Extracted data is stored in a dual-backend architecture: JSON files for direct access and SQLite for structured queries. Deduplication is performed using an MD5 hash over the concatenation of title, date, and city. Auto-derived fields (day-of-week, season, time period, lighting condition) are computed from the accident timestamp.

---

## 4. Experimental Evaluation

### 4.1 Experimental Setup

The evaluation was conducted on a stratified sample of 100 construction accident reports drawn from the Silver Standard dataset (Section 5.3), which comprises 972 manually validated records covering multiple accident types and 15 years of construction accident data from mainland China. Each report was independently annotated by three domain experts with a minimum of five years of professional experience in construction safety management. Ground truth labels were established through majority voting (at least two of three experts agreeing), covering all 19 CAICS codes, accident type (11-class), and severity level (4-class).

The backbone model was DeepSeek V4 Pro, configured with temperature set to 0.0 to ensure deterministic output across all experimental conditions. Six prompting strategies (S0–S5, described in Section 3.2) were evaluated, each processing all 100 samples independently.

Evaluation metrics included macro-averaged F1 score across the 19 CAICS codes (Ave+F1), precision, recall, accident type classification accuracy (TypeAcc), severity classification accuracy (SevAcc), parse error rate, code rate (percentage of samples with at least one code extracted), and average inference time per sample. The comparison baseline was established against the findings of Liu et al. (2025), who evaluated GPT-4o on aviation accident narratives under the HFACS framework using analogous prompting strategies.

### 4.2 Overall Performance Comparison

Prior work on structured accident cause extraction using large language models has established a clear hierarchy of prompting strategies. Liu et al. (2025), evaluating GPT-4o on the NTSB aviation accident dataset under the HFACS framework, reported that domain-knowledge-enhanced Chain-of-Thought prompting consistently outperformed Few-Shot learning across all evaluation metrics. Their ranking — HFACS-CoT > HFACS-CoT+ > Few-Shot > Zero-Shot — suggested that embedding domain taxonomies directly into the reasoning chain provides a generalizable advantage over example-based learning.

Our results on DeepSeek V4 with the CAICS taxonomy reveal a fundamentally different pattern.

**Table 3.** Performance comparison of six prompting strategies on CAICS extraction (100 samples).

| Strategy | Ave+F1 | Precision | Recall | TypeAcc | SevAcc | Parse Err. | Code Rate | Avg Time (s) |
|----------|:------:|:---------:|:------:|:-------:|:------:|:----------:|:---------:|:------------:|
| S0 Zero-Shot | 0.628 | 0.759 | 0.551 | 0.680 | 0.950 | 9 | 96% | 13.8 |
| S1 Few-Shot | **0.737** | **0.750** | **0.739** | 0.710 | 0.930 | **0** | **100%** | **13.0** |
| S2 Auto-CoT | 0.630 | 0.697 | 0.600 | — | — | 96 | 85% | 24.0 |
| S3 Plan-and-Solve | 0.643 | 0.740 | 0.586 | 0.560 | 0.870 | 2 | 92% | 17.4 |
| S4 CAICS-CoT | 0.676 | 0.628 | **0.772** | **0.880** | **0.970** | 1 | 99% | 18.4 |
| S5 CAICS-CoT+ | 0.488 | 0.688 | 0.428 | 0.770 | 0.910 | 4 | 100% | 54.3 |

S1 Few-Shot emerges as the dominant strategy, achieving the highest average F1 score (0.737) and a 100% code rate with zero parse errors. Its inference speed of 13.0 seconds per sample also makes it the most computationally efficient among competitive strategies. This result directly contradicts the Liu et al. ranking established on GPT-4o, suggesting that the optimal prompting strategy is model-dependent: DeepSeek V4 benefits more from in-context examples than from structured domain knowledge injected into the reasoning chain.

S4 CAICS-CoT ranks second with an average F1 of 0.676, approximately 8% below S1. However, S4 excels in two critical dimensions: it achieves the highest accident type classification accuracy (TypeAcc = 0.880) and the highest severity classification accuracy (SevAcc = 0.970). S4 also attains the highest recall (0.772), indicating that domain-knowledge-enhanced prompts capture the full set of causal factors more comprehensively, albeit at the cost of precision (0.628), where the model over-generates codes in response to diagnostic clues.

The remaining strategies — S3 (F1 = 0.643), S2 (F1 = 0.630), and S0 (F1 = 0.628) — form a tight cluster near the baseline, differing by less than 0.02 in F1. This clustering reveals that modest reasoning enhancements such as generic planning instructions or auto-generated chain-of-thought offer marginal to negligible improvements over simply specifying the output format.

S5 CAICS-CoT+ exhibits catastrophic failure: despite requiring five sequential API calls per sample (54.3 seconds per sample, 4.2 times slower than S1), it achieves the lowest F1 score (0.488) — 14% below the Zero-Shot baseline. The sequential architecture, designed to enforce layer-by-layer reasoning, instead causes each independent call to lose the cross-layer causal context that informs accurate coding in single-call strategies.

### 4.3 Per-Layer Analysis

**Table 4.** Per-layer F1 scores for S1, S3, S4, and S5 strategies.

| Layer | S1 Few-Shot | S3 Plan-and-Solve | S4 CAICS-CoT | S5 CAICS-CoT+ |
|-------|:-----------:|:-----------------:|:------------:|:--------------:|
| M Management | **0.740** | 0.514 | 0.671 | 0.354 |
| F Barrier | **0.738** | 0.531 | 0.682 | 0.411 |
| T Trigger | 0.708 | 0.667 | **0.753** | 0.573 |
| H Human Act | **0.763** | 0.575 | 0.633 | 0.287 |
| C Consequence | 0.500 | 0.446 | 0.600 | 0.500 |

Per-layer analysis reveals systematic differences in how each prompting strategy interacts with the structural properties of each CAICS layer.

S1 Few-Shot demonstrates consistent superiority across the M, F, and H layers. Management and Human Act factors are defined by patterns of behavior — inadequate supervision, procedural violations, risk misjudgment — that are better conveyed through concrete examples than through abstract keyword lists. The three Few-Shot examples provide the model with explicit mappings from narrative descriptions to CAICS codes, enabling it to recognize behavioral patterns expressed through novel wording in the test samples.

S4 CAICS-CoT achieves its highest relative performance on the T layer (Trigger conditions, F1 = 0.753), surpassing S1 on this dimension (0.708). Trigger conditions (equipment failure, material anomalies, geological instability, adverse weather) are the most lexically grounded layer in the CAICS taxonomy. They manifest in accident reports through domain-specific terminology (e.g., "water inrush," "wire rope fracture," "overloading") that maps directly to the diagnostic clues embedded in the S4 prompt. For this layer, keyword-anchored reasoning provides an advantage over example-based learning.

The H layer reveals the sharpest contrast between strategies. S1 achieves F1 = 0.763 on Human Acts, while S5 collapses to F1 = 0.287. This collapse is attributable to the architectural fragmentation of S5: when H-layer reasoning is isolated into a separate API call without access to the M, F, and T layer outputs, the model loses the causal narrative context needed to distinguish between H1 (procedural violation) and H3 (risk perception failure). These distinctions require understanding the sequence of events — information distributed across the accident report and only accessible when all layers are considered jointly.

### 4.4 Diagnostic Analysis: The Lexical Gatekeeping Effect

The rank-ordering reversal between S1 Few-Shot and S4 CAICS-CoT motivated a deeper diagnostic investigation. If domain knowledge injection failed to outperform example-based learning, the explanation might lie not in the content of the domain knowledge but in the form of the reasoning instruction that conveyed it.

#### 4.4.1 The Lexical Gatekeeping Effect

The initial version of S4 (S4 v1) employed a conditional-check prompt design: for each CAICS code, the prompt presented a list of keywords and instructed the model to verify their presence in the accident text before outputting the code. S4 v1 achieved an F1 of only 0.425 — 32% below the Zero-Shot baseline. Recall was 0.319, meaning the model failed to identify nearly 70% of ground-truth codes. The most severely affected code was M1 (inadequate supervision), the single most prevalent causal factor in the dataset, appearing in 85 of 100 ground-truth cases. Under S4 v1, M1 recall was merely 0.143; the model identified only 12 of 85 positive instances.

We term this phenomenon the **Lexical Gatekeeping Effect**: when prompts embed domain knowledge as conditional checks requiring the presence of specific keywords, large language models treat the listed keywords as mandatory gates rather than illustrative examples. The model anchors its classification decisions to surface-level lexical items. If an accident report describes inadequate supervision through paraphrased expressions — "the foreman was absent from the worksite," "no one was overseeing the operation," "the site manager had left early" — rather than exact keyword matches such as "no supervisor present" or "supervision deficiency," the model concludes the condition is not met and excludes the code.

The examples below illustrate the contrast between keyword-anchored expressions that S4 v1 could recognize and paraphrased expressions that fell outside its lexical gate:

| Ground truth | Keyword-anchored (recognized by S4 v1) | Paraphrased (missed by S4 v1) |
|--------------|--------------------------------------|-------------------------------|
| M1 (inadequate supervision) | "no one on site supervising" | "the supervisor failed to follow up on identified issues" |
| M1 | "safety management in chaos" | "the project director prioritized construction speed" |
| M1 | "supervisor negligence" | "the client, contractor, and supervisor all failed to fulfill duties" |
| H1 (procedural violation) | "operation against rules" | "worker dismantled scaffolding before concrete reached required strength" |
| H3 (risk perception failure) | "weak safety awareness" | "workers did not notice existing cracks in the slope" |

#### 4.4.2 The Two-Step Judgment Remedy

The failure of S4 v1 motivated a fundamental redesign. S4 v2 adopted a **Two-Step Judgment** framework:

- **Step 1 (Explicit Matching)**: Check for direct keyword evidence.
- **Step 2 (Implicit Inference)**: Infer the causal factor based on accident nature, contextual cues, and domain knowledge, even when no explicit keyword is present.

The critical addition was an authorization statement: "Safety analysis prefers over-labeling to under-labeling. Infer causes based on accident nature even without explicit keywords." This restructured the model's decision criterion from "Is the keyword present?" to "Is the causal factor plausibly present given the accident narrative?" — shifting the task from lexical verification to semantic judgment.

The results were dramatic. S4 v2 achieved F1 = 0.676 (+59% relative improvement over v1), recall = 0.772 (+142%), and M1 recall recovered from 0.143 to 0.941 (80 of 85 positive cases identified). Parse errors dropped from 9 to 1, and the average number of codes extracted per sample increased from 1.6 to 6.5.

#### 4.4.3 Model-Specific Mechanism

The Lexical Gatekeeping Effect is modulated by the model's instruction-following characteristics. DeepSeek V4 exhibits strong over-literal adherence to explicit instructions. When the prompt says "check for keyword X," the model verifies keyword X with near-deterministic consistency, even when doing so contradicts the broader goal of comprehensive causal analysis. GPT-4o, by contrast, appears to interpret conditional checks probabilistically, treating listed keywords as relevance indicators rather than necessary conditions (Liu et al., 2025).

This finding carries a methodological implication for prompt engineering: the **form** of the reasoning instruction matters as much as the **content** of the domain knowledge it encodes. A prompt that embeds comprehensive domain knowledge in a conditional-check format may underperform a simpler prompt that authorizes the model to use its own judgment. The optimal design pattern combines domain-specific keywords as illustrative anchors, not mandatory gates, with explicit authorization for the model to go beyond lexical matching.

### 4.5 Model Comparison

To identify the most suitable backbone model for CAIEMS, we conducted a cross-model benchmark on 200 stratified samples from the Silver Standard dataset, comparing four frontier models under identical prompt conditions (V2 prompt with three few-shot examples, temperature = 0.0 where supported).

**Table 5.** Cross-model comparison on CAICS extraction (200 samples).

| Metric | DeepSeek V4 | Qwen 3.7 | GLM 5.2 | Kimi K2.6 |
|--------|:-----------:|:--------:|:-------:|:---------:|
| Core Field Acc. (%) | 91.2 | 91.5 | 91.5 | 63.9 |
| Cause Precision (%) | **89.5** | 78.0 | 84.4 | 90.9 |
| Cause Recall (%) | 52.7 | **80.5** | 65.5 | 38.9 |
| Cause F1 (%) | 65.3 | **78.0** | 73.5 | 54.2 |
| Severity Acc. (%) | **98.0** | 90.5 | 97.5 | 69.5 |
| Total Cost (USD) | **$0.13** | $0.43 | $2.91 | $2.84 |
| Avg. Latency (s) | 9.8 | **8.6** | 27.0 | 74.9 |

DeepSeek V4 achieved the highest cause-level precision (89.5%) at the lowest operational cost ($0.13 per 200 samples). Its core field accuracy (91.2%) was statistically indistinguishable from the best-performing models (Qwen 3.7 and GLM 5.2, both at 91.5%). The precision-recall trade-off across models reveals different extraction philosophies: DeepSeek V4 adopts a conservative strategy, extracting causal factors only when explicit linguistic evidence exists, while Qwen 3.7 adopts a more aggressive inferential approach (higher recall at lower precision). For structured accident database construction, where extracted data feeds downstream statistical analysis and trend detection, extraction faithfulness (high precision) is the more appropriate criterion. Based on the composite evidence, we selected DeepSeek V4 as the backbone model.

### 4.6 Human-AI Agreement

We conducted a human-AI agreement experiment in which three construction safety experts and DeepSeek V4 independently annotated 100 accident cases using the complete CAICS framework. Agreement was measured using Cohen's Kappa between the LLM output and the expert consensus (majority vote).

**Table 6.** Human-AI agreement: Cohen's Kappa between LLM and expert consensus.

| Dimension | LLM vs Consensus Kappa | Human Pairwise Range | Interpretation |
|-----------|:----------------------:|:--------------------:|:--------------|
| Accident Type | 0.964 | [0.858, 0.964] | Within human range |
| Severity | 0.965 | [0.866, 0.965] | Within human range |
| M Management Root | 0.795 | [0.617, 0.797] | Within human range |
| F Barrier Failure | 0.901 | [0.776, 0.901] | Within human range |
| T Trigger Conditions | 0.905 | [0.729, 0.906] | Within human range |
| H Human Acts | 0.904 | [0.759, 0.918] | Within human range |
| C Consequence Amplification | 0.828 | [0.729, 0.829] | Within human range |

Across all seven evaluation dimensions, the LLM's agreement with expert consensus falls within the range of pairwise human inter-rater agreement. On accident type (0.964) and severity (0.965), the LLM achieves near-perfect agreement, matching the upper bound of human performance. The M layer shows the lowest but still acceptable kappa (0.795), consistent with the wider human disagreement on this layer (pairwise range [0.617, 0.797]), reflecting the inherent subjectivity of attributing management-level causal factors from limited text. The mean LLM consensus kappa across all dimensions is 0.895, compared to the mean human pairwise kappa of 0.830. The LLM does not merely match the average human; it exceeds the mean human inter-rater agreement, suggesting that deterministic extraction (temperature = 0) eliminates the annotator variance arising from human fatigue and attention drift.

---

## 5. Dataset and Online Database

### 5.1 Data Collection Pipeline

The CAIEMS data collection pipeline integrates four input modalities. The primary modality is PDF parsing via the MinerU API, operating in flash mode (<10 MB, rapid markdown conversion) and precision mode (≤200 MB, fine-grained table extraction and formula recognition). Five Chinese construction safety textbooks were processed, yielding 1,203 structured accident records. The second modality accepts user text input through CLI and Web interfaces. The third supports batch import from CSV/XLSX files. The fourth implements RSS periodic collection from Chinese construction safety regulation agency websites.

Each input record undergoes a search enhancement step: the system dispatches multiple queries concurrently to Baidu and Bing search APIs, concatenating returned snippets with the original text to compensate for the brevity typical of Chinese accident bulletins. Deduplication uses an MD5 hash computed over the normalized title, date, and city — identical accidents from different sources map to the same database entry, enabling incremental updates.

### 5.2 Dataset Statistics

The CAIEMS database contains 1,203 accident records spanning 38 fields, including accident identification, classification (accident type, severity), casualty counts, economic impact, causal analysis (four-dimension causes, CAICS codes, HFACS-CS codes), administrative metadata, and auto-derived temporal features (day-of-week, season, time period, lighting condition).

Accident type distribution reveals that collapse accidents constitute the largest category at approximately 67%, reflecting the textbook corpus's emphasis on major structural failures, while crane-related injuries (10%), falls from height (8%), and struck-by-object incidents (2%) represent smaller proportions. In terms of severity, general accidents (<3 deaths) account for 64%, serious accidents (3≤deaths<10) for 33%, major accidents (10≤deaths<30) for 2%, and extremely major accidents (≥30 deaths) for 0.2%. Temporal coverage spans 2005 to 2024, geographically covering all 31 provincial-level administrative divisions of mainland China.

To contextualize: Liu et al. (2025) evaluated on 835 NTSB aviation accident reports with 4-layer HFACS annotations; Kim and Han (2024) constructed 1,816 Korean construction fall accidents with manual cause classification. The CAIEMS database, comparable in size, is substantially richer in field depth — 38 structured fields per record, five-layer causal annotation (CAICS), dual taxonomies (CAICS + HFACS-CS), and auto-derived temporal features.

### 5.3 Silver Standard Construction

A silver standard dataset was constructed from 972 records randomly sampled from the full database. Three construction safety domain experts independently annotated each record, assigning CAICS codes across all five layers, accident type, and severity level. Ground truth was established through majority voting; cases where all three annotators disagreed (<3% of cases) were resolved through group discussion. Inter-rater reliability assessed via Fleiss' Kappa achieved 0.79–0.96 across seven annotation dimensions, indicating substantial to near-perfect agreement. The silver standard serves as the evaluation benchmark for the six-strategy prompt comparison (Section 4) and the human-AI agreement experiment (Section 4.6).

### 5.4 Online Database Implementation

The CAIEMS online database employs a dual-backend architecture: JSON files for human inspection and direct access, and SQLite with SQLAlchemy 2.0 ORM (11 tables, 13 indexes) for structured queries. The Web platform (Flask) exposes 14 REST API endpoints for record CRUD, keyword search, statistical aggregation, and data export. The frontend provides a dataset browser with faceted filtering, causal factor visualization, trend analysis charts, and a real-time extraction interface. All LLM interactions are logged in dedicated tables, providing full auditability. To our knowledge, CAIEMS is the first online interactive construction accident database built through an LLM-driven extraction pipeline — prior work has produced static knowledge graphs or offline datasets, but no existing system combines automated extraction, dual-taxonomy causal annotation, interactive querying, and continuous updating within a single Web-accessible platform.

---

## 6. Discussion

### 6.1 Why Few-Shot Outperforms Domain-Knowledge CoT

The central experimental finding is the reversal of the strategy ranking reported by Liu et al. (2025). Where their evaluation on GPT-4o established domain-knowledge CoT as optimal, our evaluation on DeepSeek V4 found Few-Shot prompting superior by approximately 8% in average F1 (0.737 vs. 0.676). We propose a **model-specific capability profile hypothesis**: DeepSeek V4's pretraining corpus, containing a substantial proportion of code and structured data pairs, optimizes its in-context learning mechanism for pattern matching from input-output exemplars. The Few-Shot condition exploits this strength directly, enabling the model to internalize output format, expected code density, and co-occurrence patterns. GPT-4o, by contrast, underwent more extensive instruction-tuning and RLHF, enhancing its capacity to decompose and follow multi-step reasoning directives — a profile better aligned with domain-knowledge CoT.

If confirmed through controlled cross-model experiments, this finding revises the assumption that domain-knowledge CoT is universally superior. For models sharing DeepSeek V4's training profile (Qwen, LLaMA), Few-Shot may represent a more robust default. The methodological lesson is that prompt strategy selection must be empirically calibrated for each model family, not transferred by assumption.

### 6.2 The Lexical Gatekeeping Effect and Its Implications

The Lexical Gatekeeping Effect isolates a specific, diagnosable mechanism within the broader phenomenon of prompt sensitivity. When domain knowledge is operationalized as conditional checks, the model anchors classification to surface-level lexical items, treating listed keywords as mandatory gates. The consequence is lexically faithful but semantically impoverished classification — correct codes when exact trigger words appear, but missed causal factors expressed through alternative phrasing. The Two-Step Judgment remedy — replacing conditional checks with authorized semantic inference — recovered F1 by 59% using only the form of the reasoning instruction, with no additional domain knowledge.

The practical recommendation is to prefer semantic inference frameworks over conditional checks in safety-critical prompt design: "consider these clues; even without exact words, infer from accident nature" will consistently outperform "check if the text mentions X, Y, or Z." The difference lies in cognitive framing — the former authorizes inference, the latter mandates verification.

### 6.3 Comparison with Prior Work

Systematic comparison with Liu et al. (2025) reveals advances across three dimensions. **Causal taxonomy**: CAICS introduces five construction-specific layers (adding Barrier and Consequence to the HFACS four-layer structure) with 19 categories purpose-engineered for construction accidents, plus dual-framework encoding into HFACS-CS for cross-domain comparability. **Evaluation methodology**: our six-strategy comparison evaluates both exact-match field accuracy and per-layer causal F1, distinguishing surface-level extraction from deep causal reasoning, and reveals model-dependent optimal strategies — a dimension absent from single-model studies. **Systemization**: we extend from a single extraction experiment to a fully operational online database with 1,203 records, REST API, interactive visualization, and continuous ingestion — the first end-to-end system operationalizing LLM-driven accident extraction as a continuously available resource.

### 6.4 Limitations

Five limitations warrant acknowledgment. First, the dataset of 1,203 records, while substantial for deep causal annotation, is modest relative to the global population of construction accidents, and the 67% collapse skew reflects textbook sampling bias. Second, the entire pipeline operates on Chinese-language material; cross-lingual validation on English OSHA or HSE reports is essential before international deployment. Third, the CAICS framework requires validation on other safety-critical domains (mining, chemical, maritime) before cross-domain generalizability can be claimed. Fourth, all strategies were evaluated on a single model family (DeepSeek V4); the capability profile hypothesis requires direct cross-model evidence for confirmation. Fifth, the current database architecture is passive — it processes data only when triggered, without automated anomaly detection or predictive alerting.

### 6.5 Future Work

Five directions merit pursuit: (1) multilingual extension validating CAICS on English-language accident reports; (2) cross-domain transfer to mining, chemical, and maritime accident narratives; (3) active database development with anomaly detection, trend prediction, and automated safety alerts; (4) multimodal integration incorporating accident scene images through vision-language models; and (5) agent-based continuous monitoring as an autonomous system that scrapes, processes, and updates the database without human intervention.

---

## 7. Conclusion

Construction accident analysis remains a labor-intensive endeavor across most jurisdictions. The fundamental challenge has not been data scarcity but methodological: the bottleneck is converting narrative text into analyzable structured records at scale while maintaining causal depth and annotation quality.

CAIEMS addresses this gap as the first end-to-end LLM-driven pipeline for construction accident information extraction, causal classification, and online database construction. It integrates multi-source data ingestion, search-enhanced context enrichment, structured extraction through the CAICS causal taxonomy, and a continuously available Web platform with REST API access.

Three principal findings emerge. First, Few-Shot prompting outperforms domain-knowledge CoT on DeepSeek V4 by 8% in average F1, providing the first empirical evidence that prompt strategy rankings are model-dependent rather than universally transferable. Second, the Lexical Gatekeeping Effect was identified and remedied: conditional-check CoT formulations caused a systematic recall deficit that the Two-Step Judgment framework recovered by 59% in F1. Third, the LLM achieved inter-rater reliability (Kappa 0.79–0.96) comparable to human expert agreement, validating LLMs as reliable annotators for safety informatics.

The five contributions are: (1) CAICS, the first construction-specific causal classification system; (2) a systematic six-strategy comparison revealing model-dependency in prompting effectiveness; (3) identification and remediation of the Lexical Gatekeeping Effect; (4) an online database of 1,203 accident records built entirely through LLM extraction; and (5) a fully operationalized pipeline demonstrating the practical feasibility of LLM-driven safety intelligence.

---

## References

Ahmadi, A., Sharif, S., & Banad, Y. (2025). Improving aviation safety analysis: Automated HFACS classification using reinforcement learning with group relative policy optimization. *arXiv preprint*, arXiv:2508.21201.

Ahmadi, E., Muley, S., & Wang, C. (2024). Automatic construction accident report analysis using large language models (LLMs). *Journal of Intelligent Construction*, 3(1).

Brown, T. B., Mann, B., Ryder, N., et al. (2020). Language models are few-shot learners. *Advances in Neural Information Processing Systems*, 33.

Chung, H. W., Hou, L., Longpre, S., et al. (2022). Scaling instruction-finetuned language models. *arXiv preprint*, arXiv:2210.11416.

Garrett, J. W., & Teizer, J. (2009). Human factors analysis classification system relating to human error awareness taxonomy in construction safety. *Journal of Construction Engineering and Management*, 135(8), 754–763.

Jiang, Y., Nie, B., Wang, J., Peng, P., & Wang, L. (2026). Large language model-driven knowledge graph and intelligent analysis for coal mine accidents. *Measurement*, 277, 121558.

Kim, D., & Han, S. (2024). Systemic analysis of fall accidents caused by worker inattention at construction sites: An integrated approach using HFACS and LLM. *KIBIM Magazine*, 14(4), 23–34.

Kong, F., & Ahn, S. (2024). Use of knowledge graphs for construction safety management: A systematic literature review. *Information*, 15(7), 390.

Liu, Q., Ding, Y., & Luo, X. (2025). Automated knowledge graph-based risk assessment for fall-from-height accidents in construction. *Automation in Construction*, 179, 106482.

Liu, Q., Li, F., Ng, K. K. H., Han, J., & Feng, S. (2025). Accident investigation via LLMs reasoning: HFACS-guided chain-of-thoughts enhance general aviation safety. *Expert Systems with Applications*, 269, 126422.

Liu, Q., & Luo, X. (2025). Large language model-based knowledge graph construction of unstructured accident reports to improve construction safety. *EG-ICE 2025 Proceedings*.

Ma, J., Cao, L., Feng, Y., Karatuğ, Ç., Buber, M., & Wang, X. (2026). Intelligent analysis of ship collision accidents via Low-Rank Adaptation-based fine-tuning of medium-scale large language models. *Reliability Engineering & System Safety*, 275, 112774.

Rafindadi, A. D., Shafiq, N., Othman, I., Ibrahim, A., Aliyu, M. M., Mikić, M., & Alarifi, H. (2023). Data mining of the essential causes of different types of fatal construction accidents. *Heliyon*, 9(2), e13389.

Shappell, S. A., & Wiegmann, D. A. (2000). The human factors analysis and classification system — HFACS. *DOT/FAA/AM-00/7*, Federal Aviation Administration.

Shi, D., Gan, S., Zurada, J., Guan, J., Wang, F., & Weichbroth, P. (2025). A multi-model approach to construction site safety: Fault trees, Bayesian networks, and ontology reasoning. *Expert Systems with Applications*, 288, 127817.

Togan, V., Mostofi, F., Tokdemir, O. B., & Kadioglu, F. (2025). Efficient management of safety documents using text-based analytics to extract safety attributes from construction accident reports. *IEEE Access*, 13, 99758–99777.

Wang, X., Wei, J., Schuurmans, D., Le, Q., Chi, E., Narang, S., Chowdhery, A., & Zhou, D. (2023). Self-consistency improves chain of thought reasoning in language models. *arXiv preprint*, arXiv:2203.11171.

Xiao, X., Chen, P., Qi, B., Zhao, H., Liang, J., Tong, J., & Wang, H. (2026). KRAIL: A knowledge-driven framework for human reliability analysis integrating IDHEAS-DATA and large language models. *Reliability Engineering & System Safety*, 265, 111585.

Yan, Y., Li, B., & Lodewijks, G. (2025). UAV accident forensics via HFACS-LLM reasoning: Low-altitude safety insights. *Drones*, 9(10), 704.

Zhang, J., Zhang, W., Wei, Q., Zhong, H., & Miao, Y. (2025). GRAG-ProSafe QAS: Graph retrieval-augmented generation for process production safety intelligent question and answer system. *Expert Systems with Applications*.

Zhang, L., Hou, Y., & Ren, F. (2025). AIR Agent — A GPT-based subway construction accident investigation report analysis chatbot. *Buildings*, 15(4), 527.

Zhou, D., Schärli, N., Hou, L., Wei, J., Scales, N., Wang, X., Schuurmans, D., Cui, C., Bousquet, O., Le, Q., & Chi, E. (2023). Least-to-most prompting enables complex reasoning in large language models. *arXiv preprint*, arXiv:2205.10625.
