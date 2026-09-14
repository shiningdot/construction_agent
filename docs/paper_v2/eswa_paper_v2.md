# CAIEMS: An Exploratory LLM-Driven Pipeline for Structuring Construction Accident Narratives into a Queryable Database

> V2 修订稿（英文主源）｜依据导师综合评阅意见修订 ｜ 2026-09-14
> 图片位置为占位符（图X占位：待手绘），文字与数据已按评阅意见逐项对账
> V1 稿保存在 docs/paper/，本稿独立存放于 docs/paper_v2/

## Abstract

Construction accident reports constitute a large but underused source of safety knowledge, and their conversion into analyzable records remains labor-intensive. This paper presents CAIEMS, an exploratory LLM-driven data engineering pipeline that processes publicly available Chinese construction accident narratives into a queryable database prototype of 2,623 records with 33 fields and 17,364 causal annotations. The annotations follow a three-tier evidence scheme: verbatim-evidence codes, semantically induced codes, and domain-prior inference codes, stored and reported separately. The pipeline integrates multi-source collection, LLM text classification, structured field extraction, causal coding under the CAICS operational scheme (5 layers, 19 categories), MECE field standardization, and an interactive Web/REST interface. Three supporting experiments are reported with their scope stated explicitly. First, a constrained comparison of four LLMs on 200 records, in which the adopted model reached 91.2% basic-field accuracy at the lowest cost among candidates; we note that the compared models ran under unequal conditions (differing temperature constraints and output budgets), so the comparison is a system-level selection exercise rather than a general capability ranking. Second, a six-strategy prompt comparison on 100 records with three-expert majority-vote ground truth (Few-Shot best, F1 = 0.669), during which we observed a prompt-phrasing sensitivity that we term the Lexical Gatekeeping Effect: interrogative conditional-check phrasing suppressed paraphrastic evidence for implicit categories (M1 recall 0.141), and a two-step judgment rephrasing raised M1 recall to 0.941 in the same setting. We report this as an observation with a practical remedy, not as an isolated causal mechanism. Third, a protocol study (n = 289) showing high agreement between the LLM and one trained annotator on six basic fields (Cohen's kappa 0.91–0.97 for categorical fields; ICC 0.985 for counts; 99.7% year-month match for dates). The boundaries of these results are stated explicitly throughout: the silver standard is partly model-derived, prompt development and final testing share one sample, and the database reflects publicly retrievable accident coverage rather than a representative sample of all construction accidents.

**Keywords**: Large language model; Construction safety; Information extraction; Prompt engineering; Accident database; Causal annotation; Inter-annotator agreement

---

## 1. Introduction

Construction remains among the most hazardous industries worldwide. Each fatal construction accident generates investigation reports and news narratives that record what happened, how it happened, and why. These texts contain causal information accumulated at national scale over decades, yet they remain unstructured natural language that resists systematic analysis. In China, accident narratives are dispersed across safety information portals, local emergency-management bureaus, and news media, which makes manual compilation expensive and automated compilation a recurring engineering challenge (Kong and Ahn, 2024).

Existing computational approaches to this conversion problem fall into three groups. Supervised classifiers such as BERT and its distilled variants require labeled corpora that are expensive to produce in this domain (Togan et al., 2025). LLM-based studies have demonstrated extraction of accident attributes from OSHA narratives (Ahmadi et al., 2024), HFACS-guided classification of Korean fall accidents (Kim and Han, 2024), and knowledge-graph construction from construction reports (Liu and Luo, 2025). The resulting artifacts, however, are mostly study-bound: extraction feeds a single analysis or a static graph rather than an inspectable, updatable database.

This paper reports on CAIEMS, an exploratory data engineering effort rather than a theory-confirming study. The single organizing question is practical: can a small team, using commodity LLM APIs and modest computing resources, turn publicly available Chinese accident narratives into a queryable database whose labels can be traced to the source text at defined confidence levels? We report what the pipeline produces, what we measured about its reliability, and what we could not yet establish.

Three contributions follow from this framing. (C1) A data engineering pipeline and database prototype: 2,623 records, 33 fields, and 17,364 causal annotations in a three-tier evidence scheme, with a Web interface and REST API; the data and code are released for inspection upon publication. (C2) The CAICS operational coding scheme — a 5-layer, 19-category set of operational definitions for construction accident causes, positioned explicitly as an operational scheme for this database rather than a new causal theory. (C3) An empirically documented prompt-phrasing sensitivity — the Lexical Gatekeeping Effect — observed during prompt development, together with a two-step judgment remedy, reported with its scope boundaries.

Three supporting experiments anchor the reliability discussion. Experiment 1 compares four LLMs on 200 records for backbone selection under the explicit caveat of unequal running conditions. Experiment 2 compares six prompting strategies on 100 records with three-expert majority-vote ground truth; this experiment surfaced the phrasing sensitivity summarized above. Experiment 3 measures agreement between the LLM and one trained annotator on six basic fields over 289 records. The samples underlying the experiments have known limitations — the silver standard is partly model-derived, and the strategy sample served both prompt development and final reporting — and these limitations are stated at each point of use rather than collected in a single caveat paragraph.

The remainder of the paper is organized as follows. Section 2 reviews related work. Section 3 describes the evidence-tier annotation scheme, the CAICS operational definitions, the pipeline architecture, and the database. Section 4 reports the three experiments with their scope statements. Section 5 discusses implications and limitations. Section 6 concludes.

---

## 2. Related Work

### 2.1 Text-based analytics for construction accident documents

Computational analysis of construction safety documents predates LLMs and remains instructive for what it could not do. Association rule mining over manually coded records identified interpretable links between accident types and causal factors (Rafindadi et al., 2023), and decision-tree models over OSHA roofing records produced rules for injury prediction (Onuchukwu et al., 2024). Ayhan and Tokdemir (2019) combined regression and fuzzy logic on an expert-coded incident set. The common constraint across these studies is circular: the analytics are only as good as the structured input, and the structured input was itself produced by costly manual coding. Togan et al. (2025) directly addressed the conversion step, benchmarking TF-IDF, BERT, RoBERTa, XLNet, and DistilBERT for safety-attribute extraction from 5,224 construction accident cases, finding DistilBERT competitive at roughly half the computational cost. Their result typifies the supervised paradigm: workable accuracy (F1 = 0.72 on severity classification) at the price of a large labeled corpus.

### 2.2 LLMs for accident information extraction

LLMs shift the bottleneck by inferring structure directly from text without task-specific training (Brown et al., 2020; OpenAI, 2023). Ahmadi et al. (2024) evaluated GPT-3.5, GPT-4.0, Gemini Pro, and LLaMA 3.1 on classifying root cause, injury cause, body part, severity, and time from OSHA construction reports. Zhang, Hou, et al. (2025a) built AIR Agent, a GPT-based chatbot for subway construction report analysis. Liu, Ding, et al. (2025b) applied zero-shot GPT-4o extraction to 1,097 fall-from-height reports, reaching F1 = 0.94 on entity recognition and constructing a weighted risk network. In adjacent domains, Huang et al. (2026) built an LLM pipeline for marine accident knowledge graphs with an accompanying quality assessment framework, and Jiang et al. (2026) constructed a coal mine accident knowledge graph with ontology modeling and interactive visualization. These systems already combine collection, LLM extraction, and graph storage to varying degrees; what varies is the depth of data-quality reporting, the existence of human-validated references, and whether the artifact is maintained as an updatable service.

### 2.3 HFACS-based causal classification with LLMs

The Human Factors Analysis and Classification System (Shappell and Wiegmann, 2000) dominates causal classification in high-risk industries. Chain-of-thought prompting (Wei et al., 2022) and its variants — zero-shot reasoning (Kojima et al., 2022), self-consistency decoding (Wang et al., 2023), and least-to-most decomposition (Zhou et al., 2023) — form the methodological substrate of prompt-based classification. Liu et al. (2025a) proposed HFACS-CoT, embedding HFACS knowledge into chain-of-thought prompting, and compared six prompting strategies on 113 general aviation narratives with GPT-4o, finding domain CoT superior (recall 74.9%, precision 46.2%). Kim and Han (2024) integrated HFACS with Claude 3.5 Sonnet to analyze 1,816 Korean construction fall cases. Yan et al. (2025) applied HFACS-guided prompting to UAV incident narratives across seven LLMs. Ahmadi et al. (2025) explored GRPO fine-tuning of Llama-3.1 8B for HFACS classification. Across this literature, strategy rankings are established within single model–task pairs, and prompt designs are reported as working configurations without systematic study of how the phrasing of embedded knowledge conditions model behavior.

### 2.4 Knowledge graphs and accident databases

Kong and Ahn (2024) systematically reviewed knowledge graphs for construction safety management and identified the gap between research prototypes and operational platforms. Liu and Luo (2025) generated construction accident knowledge graphs with iterative CoT prompting. Yang and Park (2025) built a road-accident news knowledge graph. Zhang, Wei, et al. (2025b) developed GRAG-ProSafe, a graph-RAG question-answering system over process-industry accident knowledge. Shi et al. (2025) combined fault trees, Bayesian networks, and ontology reasoning for foundation-pit collapse analysis. Xiao et al. (2026) integrated a knowledge graph as a retrieval layer for LLM-based human reliability analysis. Each of these systems constructs a knowledge artifact of some kind; the present work differs in emphasis rather than in kind — it prioritizes traceable, tiered evidence annotations and an updatable query interface, and it reports the reliability of those annotations with their measured limits.

---

## 3. System

### 3.1 Problem formulation and the evidence-tier annotation scheme

Let $T = \{t_1, t_2, \dots, t_N\}$ denote a corpus of unstructured accident narratives (median 517 characters in our corpus). The pipeline transforms each $t_i$ into a structured record $r_i = (b_i, c_i)$: a basic information vector $b_i$ covering 33 pre-defined fields, and a causal annotation set $c_i = \{(x_{ij}, e_{ij}, \iota_{ij}, q_{ij})\}$, where $x_{ij}$ is a causal code from the CAICS scheme, $e_{ij}$ is the supporting text, $\iota_{ij}$ is the inference chain, and $q_{ij} \in \{\text{explicit}, \text{inferred}, \text{prior}\}$ is the evidence tier.

The three tiers carry different epistemic commitments, and the database stores and reports them separately:

- **explicit**: the source text contains a passage that directly supports the code; $e_{ij}$ is a verbatim quotation. Only explicit codes are presented as evidence-backed in the Web interface.
- **inferred**: the code is induced from the narrative by semantic paraphrase (e.g., a text stating "safety management inadequate" supports M1 without quoting any fixed keyword). The inference chain is recorded.
- **prior**: the code follows from domain assumptions authorized in the prompt (e.g., a fatal accident is taken to imply deficient supervision even when the text is silent). These codes are displayed with a prior label and are excluded from evidence-backed statistics.

This scheme replaces the stronger claim, made in an earlier draft, that every code carries quotable evidence and that evidence-free codes are hallucinations by construction. The tiered scheme is the accurate description of what the production prompt actually does: direct extraction, paraphrase induction, and domain-prior inference are all useful for downstream analysis, but conflating them would present model priors as empirical findings. Across the database, 15,809 annotations (91.0%) are explicit, 1,131 (6.5%) are inferred, and 424 (2.4%) are prior; the remainder of this paper reports totals and, where relevant, tier-level breakdowns.

### 3.2 CAICS: an operational coding scheme

The Construction Accident Integrated Cause System (CAICS) is a 5-layer, 19-category coding scheme designed for construction accident narratives. Its layers follow the causal leak model: the management layer (M) asks why the site was vulnerable; the protection layer (F) asks why physical barriers failed; the trigger layer (T) asks what material condition initiated the event; the human act layer (H) asks what unsafe behavior preceded it; and the consequence layer (C) asks why the outcome exceeded expectation. Table 1 lists the nineteen categories with operational definitions and typical evidence keywords.

**Table 1.** The CAICS coding scheme: operational definitions.

[表1占位：待手绘 — 5 层 19 类操作定义表]

Two design features distinguish CAICS from aviation-derived HFACS adaptations (Shappell and Wiegmann, 2000; Liu et al., 2025a): the F layer captures physical protection failures endemic to construction, and the C layer records consequence-amplification factors such as delayed or concealed reporting. The scheme was derived through corpus-driven induction under the leak model: causal statements were extracted from the corpus, clustered into candidate categories, and consolidated into the five layers, with categories below a frequency floor merged or dropped; each retained category received an operational definition and corpus-derived evidence keywords. We emphasize the scope of this claim: the derivation protocol was an iterative engineering procedure rather than a formal content-validity study, and no comparative validation against 24Model (Xuecai et al., 2024), Accimap, or HFACS on independent data has yet been performed. CAICS is therefore positioned in this paper as an operational scheme for this database — a finite, LLM-oriented coding frame with explicit definitions — not as an established causal theory. Its usability evidence consists of the expert annotation agreement on the 100-record strategy sample (Fleiss' kappa 0.859–1.000 across layers, Section 4.1).

### 3.3 Pipeline architecture

Figure 1 shows the overall architecture. The pipeline consists of six processing stages followed by storage and access layers.

**Figure 1.** CAIEMS system architecture.

[图1占位：待手绘 — 系统架构图]

**Stage 1: Multi-source collection.** A crawler ingests accident reports from public safety information portals (safehoo.com, esafety.cn) and local emergency-management bureaus. Raw HTML is parsed into candidate records. The corpus spans two blocks — a historical block (1978–1990, 698 records, 26.6%) digitized from a published accident compilation, and a contemporary block (2005–2026, 1,925 records, 73.4%) collected from news portals; 1991–2004 is unrepresented. Reports at overseas sites of Chinese contractors (22 records) are retained but excluded from provincial statistics. Because the sources are public web pages, the database covers publicly retrievable accident coverage and should not be read as a representative sample of all construction accidents; severe accidents are overrepresented relative to the underlying population, and the same accident may appear through multiple mirrored reports.

**Stage 2: LLM text classification.** Candidate pages are heterogeneous. A lightweight LLM classifier assigns each record one of three text types: *single*, *multi*, or *other*, using few-shot prompting with five annotated examples. This stage removed 895 textbook-derived records and all advertisement and navigation pages. We note that the classifier is imperfect: in the agreement study's 300-record draw, 11 records were excluded at annotation time as multi-accident texts, indicating residual classification error that the current pipeline does not fully audit; multi-accident splitting rules and cross-source deduplication of mirrored reports remain partially manual.

**Stage 3: Basic field extraction (Step 1).** For each accepted record, an LLM extracts the 33-field basic information vector under a strict JSON tool schema. Schema-enforced output was obtained for 100% of records in the evaluation runs. The extractor also produces a normalized title summary used for search enrichment.

**Stage 4: CAICS causal coding (Step 2).** The encoder applies the two-step judgment procedure — explicit matching followed by authorized inference — and records, per code, the evidence tier, the supporting text, and the inference chain (Section 3.1). Figure 2 illustrates the five-layer structure with an example from a real record.

**Figure 2.** CAICS five-layer causal structure with an example evidence chain.

[图2占位：待手绘 — CAICS 五层结构与示例证据链]

**Stage 5: LLM-first MECE enum standardization.** Five free-text fields (work task, equipment, material, worker type, building structure) are normalized through an LLM-first procedure: the LLM reviews the value distribution across the corpus, designs a MECE framework, and maps every observed value. Table 2 reports the resulting enumerations with non-null denominators stated explicitly.

**Table 2.** MECE enumerations produced by the LLM-first procedure.

| Field | Categories | Non-null records (share of 2,623) | Largest category (share of non-null) | Residual "other" (share of non-null) |
|-------|-----------:|----------------------------------:|--------------------------------------|--------------------------------------|
| Work task (WT) | 11 | 2,202 (83.9%) | WT02 main structure works (397, 18.0%) | 2.9% |
| Equipment involved (EQP) | 13 | 1,373 (52.3%) | EQP01 hoisting machinery (335, 24.4%) | 1.9% |
| Material involved (MAT) | 7 | 1,465 (55.9%) | MAT01 concrete and cement products (423, 28.9%) | 11.1% |
| Worker type (WKR) | 13 | 1,905 (72.6%) | WKR01 general laborers (928, 48.7%) | 6.3% |
| Building structure (BLD) | 12 | 359 (13.7%) | BLD01 steel structures (127, 35.4%) | 3.6% |

**Stage 6: Storage and access.** All artifacts are stored in a SQLite database with nine tables. An interactive Web platform and REST API expose the database: users filter accidents by type, severity, region, work task, and causal code, and inspect the evidence behind each annotation, with evidence tiers displayed separately. The database and code are released for inspection upon publication; until then they are available from the authors on request.

### 3.4 Multimodal extension

Beyond text, the pipeline enriches records through three display-oriented modules: search enhancement via an aggregated search API (1,636 records, 62.4%), image collection filtered by a zero-shot CLIP classifier (226 images above the 0.5 similarity threshold), and two-phase VLM description with GLM-5V-Turbo (282 analyses across 176 accidents). These modules are extensions rather than evaluated contributions: image quality at the source constrains what VLM analysis can contribute, and the descriptions are presented as supplementary material, not verified annotations.

### 3.5 Database statistics

Table 3 summarizes the database with counts that close against the total.

**Table 3.** Database statistics.

| Dimension | Value |
|-----------|-------|
| Records | 2,623 |
| Time span | 1978-08 to 2026-06 (1991–2004 unrepresented) |
| Accident types | 12 categories, 2,617 records labeled (collapse 1,092; fall-from-height 570; hoisting 225; earthwork collapse 222; electrocution 160; struck-by 119; other 50; fire/explosion 47; mechanical 46; poisoning/asphyxiation 41; vehicle 38; drowning 7); 6 records unlabeled |
| Severity | general 1,889; relatively major 672; major 53; particularly major 8; 1 record unlabeled |
| Casualties | 5,894 deaths; 5,796 injuries |
| Causal annotations | 17,364 total (explicit 15,809 / inferred 1,131 / prior 424) |
| Search-enriched records | 1,636 |
| CLIP-filtered images | 226 |
| VLM analyses | 282 across 176 accidents |

Figure 3 shows the distribution of the nineteen CAICS categories; the top codes (M1 deficient supervision, H1 procedure violation, H3 failed risk perception) partly reflect the production prompt's authorized prior inference, which is why the tier breakdown is reported alongside the totals.

**Figure 3.** Distribution of causal annotations across the nineteen CAICS categories.

[图3占位：待手绘 — CAICS 编码分布条形图]

---

## 4. Experimental Evaluation

The evaluation addresses three questions with explicitly stated scopes: which backbone model the pipeline should adopt under its actual cost constraints (Section 4.3), how the causal-coding prompt should be phrased (Section 4.4), and whether the LLM's basic-field annotations agree with a trained human annotator (Section 4.5). Sections 4.1 and 4.2 define the datasets and metrics.

### 4.1 Datasets and sampling

**Full corpus.** The pipeline produced 2,623 single-accident records spanning 1978–2026, with 33 fields and 17,364 tiered causal annotations.

**Silver standard.** The 972-record silver standard was constructed by pipeline-version agreement rather than independent human re-annotation: basic fields were adopted directly when two pipeline versions agreed and resolved by rule-based arbitration when they disagreed, and causal-dimension labels merged the two versions' outputs with deduplication. We state this construction explicitly because it bounds what the silver standard can certify: it is reliable to the extent that the two source versions are reliable, and it is not an independent human gold standard. An empty causal dimension in the silver standard indicates that the dimension is not mentioned in the source text. A 100-record subset received fully independent human validation: three construction-safety experts annotated the CAICS codes on the LabelStudio platform, with Fleiss' kappa between 0.859 and 1.000 across the five layers; this subset's ground truth is the majority vote of the three experts and carries 5.1 codes per record on average.

**Evaluation samples.**

1. *Model comparison sample* (n = 200). Stratified by accident type (11 classes, minimum three per class) drawn from the silver standard.
2. *Strategy comparison sample* (n = 100). Drawn from the silver standard covering all 12 accident types and all 19 CAICS categories; causal ground truth is the three-expert majority vote described above. **Scope statement:** this sample served prompt development — discovering the v1 recall problem, analyzing error categories, designing v2, running the ablation, and selecting the production configuration. Because development and final reporting share one sample, the strategy results carry a development-set reuse risk; the three few-shot examples were drawn from the same silver standard and were not isolated from the test records by an explicit procedure. The reported numbers are therefore best read as a development evaluation, not a frozen-test-set certification.
3. *Agreement sample* (n = 289). Two-stage stratified sampling (accident type × severity, proportional allocation with a floor of two per cell) from the full 2,623-record corpus — not from the silver standard. One trained annotator (a safety-engineering graduate student) labeled six basic features (accident type, severity, province, deaths, injuries, date) via LabelStudio without access to the pipeline's extracted values. Eleven records from the original 300 were excluded as multi-accident texts.

Figure 4 summarizes the sample hierarchy.

**Figure 4.** Dataset hierarchy and the three evaluation samples.

[图4占位：待手绘 — 样本关系流程图]

### 4.2 Evaluation metrics

Basic field extraction is evaluated with exact-match accuracy per field, with administrative-suffix normalization for location fields. Causal coding is evaluated with per-dimension precision, recall, and F1 under the dimension-level match definition of Liu et al. (2025a); the six-strategy comparison additionally reports percentile bootstrap 95% confidence intervals (1,000 resamples). Inter-annotator agreement uses Cohen's kappa with 95% confidence intervals for categorical fields, ICC(2,1) for count fields, and year-month match rate for dates; these three statistics are reported under their proper names and are not summarized under a single label.

### 4.3 Experiment 1: Backbone selection under explicit constraints

Four candidate models were evaluated on the model comparison sample: DeepSeek V4, Qwen 3.7, GLM 5.2, and Kimi K2.6, all with the same system prompt and three few-shot examples. Running conditions were not fully equal across models: Kimi K2.6 enforces temperature 1.0 by API specification, GLM 5.2 required a 16,384-token output budget for its reasoning traces, and DeepSeek V4 was accessed with the provider's automatic tier routing enabled, with recorded request identifiers spanning the DeepSeek V4 tier family. Because of these differences, and because each configuration ran once, we frame this experiment as a constrained system comparison supporting an adoption decision, not as a general model capability ranking.

Table 4 reports per-field accuracy. The three strongest models reach 91.2–91.5% overall, with uniformly high accuracy on severity, death toll, and provincial location. City extraction is the hardest field (81.5–88.0%): Chinese reports frequently name county-level cities while the reference records the parent prefecture-level city. Kimi K2.6 lags far behind (63.9%), consistent with its enforced temperature introducing output instability.

**Table 4.** Basic field extraction accuracy (%), Experiment 1.

| Field | DeepSeek V4 | Qwen 3.7 | GLM 5.2 | Kimi K2.6 |
|-------|:-----------:|:--------:|:-------:|:---------:|
| Accident type | 86.0 | 85.0 | 84.5 | 61.5 |
| Severity | 98.0 | 90.5 | 97.5 | 69.5 |
| Deaths | 96.0 | 96.0 | 97.0 | 68.5 |
| Injuries | 90.0 | 91.0 | 91.0 | 58.5 |
| Province | 96.0 | 98.5 | 96.0 | 67.0 |
| City | 81.5 | 88.0 | 83.0 | 58.5 |
| **Overall** | **91.2** | **91.5** | **91.5** | **63.9** |

Table 5 reports causal coding by dimension. DeepSeek V4 shows the highest average precision (89.5%) and the lowest recall among the capable models (52.7%), while Qwen 3.7 shows the mirror trade-off (78.0% precision, 80.5% recall). Cost and latency complete the adoption picture: USD 0.13 for DeepSeek V4 versus 0.43 (Qwen 3.7), 2.91 (GLM 5.2), and 2.84 (Kimi K2.6) over 200 records; latency 9.8 s versus 8.6 s, 27.0 s, and 74.9 s per record. We adopted DeepSeek V4 for the pipeline.

**Table 5.** Causal coding precision / recall / F1 (%) by dimension, Experiment 1.

| Dimension | DeepSeek V4 | Qwen 3.7 | GLM 5.2 | Kimi K2.6 |
|-----------|:----------:|:--------:|:-------:|:---------:|
| Management | 89.9 / 54.9 / 68.1 | 67.5 / 95.6 / 79.1 | 92.3 / 63.7 / 75.4 | 97.6 / 36.3 / 52.9 |
| Protection | 91.9 / 41.9 / 57.6 | 88.8 / 64.0 / 74.4 | 87.6 / 67.6 / 76.3 | 96.4 / 39.0 / 55.5 |
| Trigger | 81.0 / 69.4 / 74.7 | 72.4 / 85.7 / 78.5 | 73.9 / 69.4 / 71.6 | 81.5 / 44.9 / 57.9 |
| Human | 95.0 / 44.7 / 60.8 | 83.3 / 76.5 / 79.8 | 83.9 / 61.2 / 70.7 | 88.2 / 35.3 / 50.4 |
| **Average** | **89.5 / 52.7 / 65.3** | **78.0 / 80.5 / 78.0** | **84.4 / 65.5 / 73.5** | **90.9 / 38.9 / 54.2** |

### 4.4 Experiment 2: Prompt strategy comparison and the Lexical Gatekeeping observation

#### 4.4.1 Design

Six prompting strategies were compared on the strategy sample (n = 100, three-expert majority-vote ground truth) using DeepSeek V4 (temperature 0.0): S0 Basic Zero-Shot, S1 Basic Few-Shot (three expert-annotated examples), S2 Auto-CoT, S3 Plan-and-Solve, S4 CAICS-CoT (domain-knowledge CoT with the two-step judgment), and S5 CAICS-CoT+ (serial per-layer calls with summary injection). Appendix A reproduces the prompts in condensed form.

**Table 6.** Performance of six prompting strategies (majority-vote ground truth, n = 100).

| Strategy | Avg. F1 [95% CI] | Avg. Recall | Avg. Precision | Type acc. | Severity acc. | Valid-output rate | Time (s) |
|----------|:-------:|:-----------:|:--------------:|:---------:|:-------------:|:-----------------:|:--------:|
| S0 Zero-Shot | 0.600 [0.520, 0.644] | 0.515 | 0.808 | 0.630 | 0.960 | 96% | 1,382 |
| S1 Few-Shot | **0.669 [0.615, 0.722]** | 0.634 | 0.753 | 0.840 | 0.970 | 100% | 1,302 |
| S2 Auto-CoT | 0.634 [0.564, 0.691] | 0.564 | 0.772 | 0.860 | 0.980 | 85% | 2,401 |
| S3 Plan-and-Solve | 0.598 [0.529, 0.652] | 0.540 | 0.741 | 0.850 | 0.990 | 92% | 1,735 |
| S4 CAICS-CoT | 0.656 [0.603, 0.695] | **0.872** | 0.565 | 0.830 | 0.990 | 99% | 1,843 |
| S5 CAICS-CoT+ | 0.665 [0.606, 0.719] | 0.750 | 0.618 | 0.830 | 0.980 | 100% | 5,430 |

*Notes.* Ground truth is the majority vote of three independent expert annotators. Confidence intervals are percentile bootstrap (1,000 resamples). S2's coding metrics and its type/severity accuracies were computed after a post-processing step that recovered 96/100 outputs exhibiting hierarchical-format drift; no other strategy received post-processing, so S2's scores describe a post-processed pipeline rather than raw output. All configurations ran once at temperature 0.0. F1 differences among S1, S4, and S5 are within overlapping confidence intervals; we therefore describe S1 as the highest point estimate rather than as a statistically confirmed winner.

#### 4.4.2 Few-Shot outperforms domain CoT on this model–task pair

S1's examples perform joint format-content calibration: they demonstrate the output schema, the expected code density, code combination patterns, and inference depth. S1's average code density matched the ground truth closely (5.0 vs. 5.1), while S0 under-coded (3.6) and S4 over-coded (6.5). The comparison with Liu et al. (2025a) is informative but confounded: their study and ours differ simultaneously in model (GPT-4o vs. DeepSeek V4), task (aviation HFACS vs. construction CAICS), corpus, and evaluation conditions. The observed ranking difference is therefore consistent with model dependence, task dependence, or both; we do not claim to have isolated model dependence, and the practical reading is simply that the optimal strategy for a new model–task pair cannot be assumed from a single published ranking.

#### 4.4.3 The Lexical Gatekeeping observation and its remedy

The initial S4 prompt (v1) embedded domain knowledge as conditional checks: each code was presented with an interrogative gate ("does the text mention 'no supervision' or 'supervision failure'?") plus an instruction not to fabricate codes. On the strategy sample, v1 reached F1 = 0.503, recall = 0.423, 21% empty outputs, and M1 recall of 0.141 — M1 being the most frequent ground-truth code (present in 85 of 100 records). Most accident narratives never use the exact keyword phrases; they say "safety management inadequate" or "failed to detect and stop in time." Under the conditional-check phrasing, the model appeared to treat the listed keywords as mandatory gates rather than illustrative cues.

We term this observed pattern the **Lexical Gatekeeping Effect**: interrogative conditional-check CoT phrasing suppresses paraphrastic evidence for implicit categories, concentrating recall loss on the highest-frequency implicit codes. The term describes an observation; it does not by itself identify a mechanism.

The remedy replaced the gate with a two-step judgment: (1) explicit matching — does the text support this code directly, under any paraphrase? (2) authorized inference — given the accident's nature and construction-safety knowledge, is this cause necessarily present even without explicit phrasing? The conservative instruction was also replaced by a coverage preference. The combined v1→v2 change raised F1 from 0.503 to 0.656, recall from 0.423 to 0.872, M1 recall from 0.141 to 0.988, and eliminated empty outputs (21% → 0). Figure 5 visualizes the change.

**Figure 5.** The Lexical Gatekeeping observation and its remedy.

[图5占位：待手绘 — 门控效应观察与补救双面板图]

#### 4.4.4 A constrained ablation of the phrasing change

The v1→v2 transition changed several prompt properties at once — the question form, the addition of the two-step judgment, the explicit authorization of inference, and the output-policy instruction. We therefore ran a four-cell comparison on the same sample: v1 (conditional checks + conservative), v2a (two-step judgment + conservative), v2b (conditional checks + coverage), and v2 (two-step judgment + coverage). Table 7 reports the results. Holding the conservative instruction constant, the phrasing change alone (v1 → v2a) raises M1 recall from 0.141 to 0.941 — 0.800 of the total 0.847 M1-recall recovery — while holding phrasing constant, the policy change alone (v1 → v2b) raises it only to 0.329. At the aggregate level the two manipulations contribute comparably (F1 +0.161 vs. +0.150).

Two scope boundaries follow. First, these are simple pairwise differences, not a formal factorial analysis with interaction estimates, and the four cells ran once each on a development sample shared with the prompt design process. Second, the results describe output behavior, not model-internal mechanisms: whether the interrogative phrasing affects attention, representation, or generation is beyond what this behavioral experiment can establish. The defensible reading is that, in the current model and CAICS task, prompt phrasing materially changes recall on implicit categories, and the two-step judgment is a practical remedy. We do not claim an isolated causal mechanism.

**Table 7.** Four-cell comparison of phrasing and output policy (n = 100, majority-vote ground truth, DeepSeek V4, temperature 0.0).

| Cell | Phrasing | Output policy | F1 | Recall | Precision | Empty output | M1 recall | M3 recall | H3 recall |
|------|----------|---------------|:---:|:------:|:---------:|:------------:|:---------:|:---------:|:---------:|
| v1 | Conditional checks | Conservative | 0.503 | 0.423 | 0.856 | 21% | 0.141 | 0.250 | 0.111 |
| v2a | Two-step judgment | Conservative | 0.665 | 0.677 | 0.709 | 2% | 0.941 | 0.667 | 0.511 |
| v2b | Conditional checks | Coverage preference | 0.653 | 0.641 | 0.724 | 5% | 0.329 | 0.444 | 0.533 |
| v2 | Two-step judgment | Coverage preference | **0.656** | **0.872** | 0.565 | 0% | 0.988 | 0.972 | 1.000 |
| | Δ(phrasing) = v2a − v1 | | +0.161 | +0.254 | −0.147 | −19 pp | +0.800 | +0.417 | +0.400 |
| | Δ(policy) = v2b − v1 | | +0.150 | +0.218 | −0.132 | −16 pp | +0.188 | +0.194 | +0.422 |

#### 4.4.5 Strategy selection for the production pipeline

The production encoder adopts the S4 two-step judgment design rather than S1. The choice favors recall (0.872 vs. 0.634) and the tiered evidence output that the database requires, at the cost of precision (0.565), with an F1 difference from S1 that is small and within overlapping confidence intervals. The three-tier evidence scheme (Section 3.1) is the compensating design: low-precision codes are labeled by tier, so downstream users can distinguish verbatim-evidence codes from prior-inference codes instead of treating every annotation as equally supported.

### 4.5 Experiment 3: Agreement between the LLM and one trained annotator

The third experiment measures whether the LLM's basic-field annotations agree with a trained human annotator. The scope is stated precisely: this is an agreement study between one LLM configuration and one annotator on six of 33 fields, not a certification of expert-level accuracy and not a test of the causal codes.

On the agreement sample (n = 289), the LLM and the annotator labeled the six fields independently. Table 8 reports the results. For the three categorical fields, Cohen's kappa is 0.9136 (accident type), 0.9477 (severity), and 0.9746 (province). For the two count fields, ICC(2,1) is 0.9849 (deaths) and 0.9853 (injuries), with mean absolute errors of 0.118 and 0.087 per record. Date agreement is 99.7% at year-month granularity. These are three different statistics applied to three different kinds of fields; we report them under their proper names rather than summarizing them under a single kappa label.

**Table 8.** Agreement between the LLM and one trained annotator on six basic fields (n = 289).

| Feature | Metric | Value [95% CI] | Agreement |
|---------|--------|----------------|-----------|
| Accident type | Cohen's κ | 0.9136 [0.8747, 0.9466] | 93.4% |
| Severity | Cohen's κ | 0.9477 [0.9024, 0.9841] | 97.6% |
| Province | Cohen's κ | 0.9746 [0.9563, 0.9927] | 97.6% |
| Deaths | ICC(2,1) | 0.9849 [0.9292, 0.9991] | MAE 0.118 |
| Injuries | ICC(2,1) | 0.9853 [0.9426, 0.9995] | MAE 0.087 |
| Date (year-month) | Match rate | 0.9965 | 99.7% |

Two further boundaries apply. First, agreement does not establish accuracy: both parties can share errors, and no independent arbitration labels exist for this sample. Second, the comparison spans six basic fields only; the causal codes — the paper's core annotation object — have no analogous large-sample agreement study, and the three-expert annotation covers only the 100-record strategy subset. The earlier draft cited an expert inter-rater range from a separate 100-record three-expert sample as an equivalence band; because that sample differs from the 289-record sample in composition and difficulty, we no longer present it as a bound and report the agreement values on their own terms. Figure 6 plots the kappa values with their confidence intervals.

**Figure 6.** Agreement between the LLM and one annotator on six fields.

[图6占位：待手绘 — 一致性森林图]

---

## 5. Discussion

### 5.1 Implications

For data engineering practice, three implications follow from the measured results, each stated within its scope. First, the tiered evidence scheme offers a workable alternative to the all-or-nothing claim that every code must carry verbatim support: prior-inference codes are useful for downstream analysis provided they are stored, displayed, and reported separately, which is what the pipeline does. Second, the phrasing observation generalizes as a design caution: in this model and task, interrogative conditional-check phrasing suppressed paraphrastic evidence for implicit categories, and the two-step judgment phrasing recovered most of it (0.800 of the total 0.847 M1-recall recovery). Practitioners designing recall-critical extraction prompts should test phrasing variants rather than assume that listed keywords behave as illustrative cues. Third, the agreement results justify a limited claim: for six basic fields, one LLM configuration and one trained annotator reached high protocol agreement, which supports using the LLM to reduce — not replace — human labeling effort for those fields under human supervision.

### 5.2 Comparison with prior work

Table 9 positions CAIEMS against representative prior systems along dimensions that can be compared from published reports: data source, record scale, human validation, evidence binding, and system accessibility. We present this as a descriptive comparison, not as evidence of exclusive novelty; several prior systems already combine collection, LLM extraction, and graph or database storage, and the present work's emphasis is on tiered evidence annotations and an updatable query interface.

**Table 9.** Descriptive comparison with representative prior systems.

| System | Domain | LLM | Coding scheme | Evidence binding | Human validation | Interactive access |
|--------|--------|-----|---------------|------------------|------------------|--------------------|
| Liu et al. (2025a) | Aviation | GPT-4o | HFACS | No | Expert-labeled dataset | No |
| Kim and Han (2024) | Construction (KR) | Claude 3.5 | HFACS | No | No | No |
| Ahmadi et al. (2024) | Construction (US) | GPT-4.0 | None | No | No | No |
| Liu and Luo (2025) | Construction | GPT-4o | Accimap | No | No | No |
| Jiang et al. (2026) | Coal mining | LLM | Custom ontology | No | No | Visualization |
| **CAIEMS (this work)** | **Construction (CN)** | **DeepSeek V4** | **CAICS (operational)** | **Tiered** | **Partial (100-record subset)** | **Web + API** |

### 5.3 Limitations

We list the limitations that bound the main conclusions, organized by their severity.

(1) *Reference independence.* The 972-record silver standard is partly model-derived (pipeline-version agreement with rule arbitration); only the 100-record causal subset carries independent three-expert validation. Conclusions drawn on the silver standard inherit this bound, and a fully independent human gold standard — with a published annotation guide, annotator backgrounds, blinding, arbitration rules, and per-class sample sizes — remains future work.

(2) *Development–test overlap.* The 100-record strategy sample served prompt development (v1 diagnosis, v2 design, ablation) and final reporting simultaneously, and the three few-shot examples were not isolated from the test records by an explicit procedure. The strategy results therefore carry development-set reuse risk and should be read as a development evaluation; a frozen test set separated from all prompt-design decisions is planned for the next iteration.

(3) *Prompt-phrasing inference.* The four-cell comparison reports simple pairwise differences on one sample with single runs; it does not estimate formal interaction effects, and it cannot support claims about model-internal mechanisms. The Lexical Gatekeeping Effect is reported as an observation with a practical remedy, not as an isolated causal mechanism.

(4) *Model comparison conditions.* The four models ran under unequal conditions (enforced temperature, output budget, tier routing), each configuration ran once, and model identifiers were recorded at family granularity. The comparison supports an adoption decision under these constraints, not a general capability ranking.

(5) *Agreement scope.* The agreement study covers one annotator, six of 33 fields, and no independent arbitration; the causal codes lack a large-sample agreement study. The results support effort reduction on basic fields under supervision, not expert substitution.

(6) *Database audit.* The corpus has known quality gaps: the classifier's residual multi-accident errors are not fully audited, mirrored reports across sources are not systematically deduplicated, field missingness varies widely across fields (from 13.7% to 52.3% non-null on the five enum fields), and the corpus overrepresents severe accidents because its sources are public news portals. The database covers publicly retrievable accident coverage; it is not a representative sample of all construction accidents.

(7) *Data governance.* The pipeline collects and plans to redistribute text and images from news portals and government sites. Public accessibility does not by itself settle re-publication rights; per-source permissions, takedown mechanisms, and access controls for traumatic imagery are discussed in Section 7 and require completion before any public release.

(8) *Reproducibility.* Data, code, and full prompts are released upon publication rather than at submission; single-run configurations and family-level model identifiers limit exact reproduction. We commit to releasing an anonymous read-only database, verbatim prompts, a minimal reproduction package, and a system demo before resubmission.

---

## 6. Conclusion

This paper reported CAIEMS, an exploratory LLM-driven pipeline that converts publicly available Chinese construction accident narratives into a queryable database prototype: 2,623 records, 33 fields, and 17,364 causal annotations organized in a three-tier evidence scheme, served through a Web interface and REST API. Three supporting experiments, each with stated scope, anchor the reliability discussion: a constrained four-model comparison that motivated the adoption of DeepSeek V4 (91.2% basic-field accuracy at the lowest cost), a six-strategy prompt comparison that surfaced the Lexical Gatekeeping observation and its two-step judgment remedy, and a protocol study showing high agreement between the LLM and one trained annotator on six basic fields. The boundaries of these results are stated throughout: the silver standard is partly model-derived, development and test samples overlap, and no causal mechanism or expert substitution is claimed. The next iteration of this work follows a defined sequence: an independent human gold standard for the causal codes, a frozen test set separated from prompt development, a formal factorial design for the phrasing question, multi-model repetition under matched conditions, and a completed data audit with governance before any public release.

---

## 7. Declarations

**Data availability.** An anonymous read-only snapshot of the database, the field dictionary, the source inventory, and the CAICS operational definitions will be provided for peer review upon request and released publicly upon publication. The six prompt templates are reproduced in condensed form in Appendix A; verbatim prompts are included in the reproduction package.

**Code availability.** The pipeline, evaluation scripts, and experiment runners will be released under an open-source license together with the data upon publication; a minimal reproduction package is available from the authors on request during review.

**Ethics and data governance.** This study analyzes publicly available news reports and government-published accident briefings; it involves no human subjects or identifiable personal data in the structured records. Public accessibility, however, does not automatically authorize bulk collection, long-term storage, and redistribution. Before any public release, the following will be completed: a per-source permission or fair-use assessment; retention of original URLs and crawl timestamps for every record; removal or masking of names and identifiable faces appearing in images; a takedown and deletion mechanism on request; and access controls for graphic or traumatic imagery. Third-party images for which redistribution rights cannot be established will be excluded from any public release.

**CRediT author statement.** Tianhao Sun (Nanjing Tech University): Conceptualization, Methodology, Software, Data Curation, Formal Analysis, Investigation, Writing — Original Draft. [Supervisor name] (Nanjing Tech University): Conceptualization, Supervision, Validation, Writing — Review & Editing.

**Declaration of competing interest.** The authors declare no competing interests.

**Funding.** This research received no specific grant from funding agencies in the public, commercial, or not-for-profit sectors.

**Use of AI tools.** Large language models constitute the research object and the core technical instrument of this study: all extraction, classification, and coding results reported in Sections 3–4 were produced by the evaluated models through the documented pipeline. During manuscript preparation, LLM-based writing assistance was used for language polishing; all scientific content, experimental design, and data originate from the authors' work and were verified by the authors.

## References

Ahmadi, A., Sharif, S., Banad, Y. (2025). Improving Aviation Safety Analysis: Automated HFACS Classification Using Reinforcement Learning with Group Relative Policy Optimization. arXiv:2508.21201.

Ahmadi, E., Muley, S., Wang, C. (2024). Automatic Construction Accident Report Analysis Using Large Language Models (LLMs). Journal of Intelligent Construction, 3(1). https://doi.org/10.26599/jic.2024.9180039

Ayhan, B.U., Tokdemir, O.B. (2019). Predicting the outcome of construction incidents. Safety Science, 113, 91–104. https://doi.org/10.1016/j.ssci.2018.11.001

Brown, T., Mann, B., Ryder, N., et al. (2020). Language Models are Few-Shot Learners. Advances in Neural Information Processing Systems, 33, 1877–1901.

Devlin, J., Chang, M.-W., Lee, K., Toutanova, K. (2019). BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding. In Proceedings of NAACL-HLT 2019, 4171–4186.

Huang, Y., Yan, R., Zhang, Z. (2026). Automated knowledge extraction from marine accident reports using large language models: Graph construction and evaluation. Ocean & Coastal Management, 272, 108015. https://doi.org/10.1016/j.ocecoaman.2025.108015

Jiang, Y., Nie, B., Wang, J., Peng, P., Wang, L. (2026). Large language model-driven knowledge graph and intelligent analysis for coal mine accidents. Measurement, 277, 121558. https://doi.org/10.1016/j.measurement.2026.121558

Kim, D., Han, S. (2024). Systemic Analysis of Fall Accidents Caused by Worker Inattention at Construction Sites: An Integrated Approach using HFACS and LLM. KIBIM Magazine, 14(4), 23–34. https://doi.org/10.13161/kibim.2024.14.4.023

Kojima, T., Gu, S.S., Reid, M., Matsuo, Y., Iwasawa, Y. (2022). Large Language Models are Zero-Shot Reasoners. Advances in Neural Information Processing Systems, 35, 22199–22213.

Kong, F., Ahn, S. (2024). Use of Knowledge Graphs for Construction Safety Management: A Systematic Literature Review. Information, 15(7), 390. https://doi.org/10.3390/info15070390

Liu, Q., Ding, Y., Luo, X. (2025b). Automated knowledge graph-based risk assessment for fall-from-height accidents in construction. Automation in Construction, 179, 106482. https://doi.org/10.1016/j.autcon.2025.106482

Liu, Q., Li, F., Ng, K.K.H., Han, J., Feng, S. (2025a). Accident investigation via LLMs reasoning: HFACS-guided Chain-of-Thoughts enhance general aviation safety. Expert Systems with Applications, 269, 126422. https://doi.org/10.1016/j.eswa.2025.126422

Liu, Q., Luo, X. (2025). Large language model-based knowledge graph construction of unstructured accident reports to improve construction safety. EG-ICE 2025. https://doi.org/10.17868/strath.00093269

Onuchukwu, I.S., Gholizadeh, P., Liko, G., Esmaeili, B. (2024). Nature of Occupational Incidents among Roofing Contractors: A Data Mining Approach. Buildings, 14(3), 595. https://doi.org/10.3390/buildings14030595

OpenAI (2023). GPT-4 Technical Report. arXiv:2303.08774.

Rafindadi, A.D., Shafiq, N., Othman, I., et al. (2023). Data mining of the essential causes of different types of fatal construction accidents. Heliyon, 9(2), e13389. https://doi.org/10.1016/j.heliyon.2023.e13389

Shappell, S.A., Wiegmann, D.A. (2000). The Human Factors Analysis and Classification System–HFACS. Report DOT/FAA/AM-00/7, Office of Aviation Medicine, Federal Aviation Administration.

Shi, D., Gan, S., Zurada, J., Guan, J., Wang, F., Weichbroth, P. (2025). A multi-model approach to construction site safety: Fault trees, Bayesian networks, and ontology reasoning. Expert Systems with Applications, 288, 127817. https://doi.org/10.1016/j.eswa.2025.127817

Togan, V., Mostofi, F., Tokdemir, O.B., Kadioglu, F. (2025). Efficient Management of Safety Documents Using Text-Based Analytics to Extract Safety Attributes From Construction Accident Reports. IEEE Access, 13, 99758–99777. https://doi.org/10.1109/ACCESS.2025.3576442

Wang, X., Wei, J., Schuurmans, D., et al. (2023). Self-Consistency Improves Chain of Thought Reasoning in Language Models. In Proceedings of ICLR 2023.

Wei, J., Wang, X., Schuurmans, D., et al. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models. Advances in Neural Information Processing Systems, 35, 24824–24837.

Xiao, X., Chen, P., Qi, B., et al. (2026). KRAIL: A knowledge-driven framework for human reliability analysis integrating IDHEAS-DATA and large language models. Reliability Engineering & System Safety, 265, 111585. https://doi.org/10.1016/j.ress.2025.111585

Xuecai, X., Gui, F., Shifei, S., et al. (2024). Accident case data-accident causation model driven safety training method. Process Safety and Environmental Protection, 182, 1208–1226. https://doi.org/10.1016/j.psep.2023.12.042

Yan, Y., Li, B., Lodewijks, G. (2025). UAV Accident Forensics via HFACS-LLM Reasoning: Low-Altitude Safety Insights. Drones, 9(10), 704. https://doi.org/10.3390/drones9100704

Yang, J., Park, S. (2025). LLM-Assisted Knowledge Graph Construction for Managing Road Accident Information From News Articles. IEEE Access, 13, 217846–217865. https://doi.org/10.1109/ACCESS.2025.3648842

Zhang, L., Hou, Y., Ren, F. (2025a). AIR Agent—A GPT-Based Subway Construction Accident Investigation Report Analysis Chatbot. Buildings, 15(4), 527. https://doi.org/10.3390/buildings15040527

Zhang, J., Zhang, W., Wei, Q., Zhong, H., Miao, Y. (2025b). GRAG-ProSafe QAS: Graph retrieval-augmented generation for process production safety intelligent question and answer system. Expert Systems with Applications. https://doi.org/10.1016/j.eswa.2025.129947

Zhou, D., Schärli, N., Hou, L., et al. (2023). Least-to-Most Prompting Enables Complex Reasoning in Large Language Models. In Proceedings of ICLR 2023.

## Appendix A. Prompt Templates

The six prompting strategies are summarized below; verbatim prompts are included in the reproduction package.

**A.1 S0 Basic Zero-Shot**: the CAICS taxonomy (nineteen codes with definitions) and the JSON output format, with no reasoning guidance.

**A.2 S1 Basic Few-Shot**: S0 plus three expert-annotated examples (text → codes with evidence), drawn from the silver standard.

**A.3 S2 Auto-CoT**: S0 plus an instruction to self-organize reasoning along the M→F→T→H→C causal chain before coding.

**A.4 S3 Plan-and-Solve**: S0 plus an instruction to first draft an analysis plan, then execute it.

**A.5 S4 CAICS-CoT**: two versions were tested. v1 phrased every code as an interrogative conditional check ("does the text mention …?") with a conservative instruction. v2 replaced the checks with the two-step judgment (explicit matching + authorized inference, including domain-prior rules such as inferring supervision deficiency from a fatal outcome) and a coverage preference.

**A.6 S5 CAICS-CoT+**: five independent per-layer API calls with inter-layer summary injection, plus a final aggregation call.

---

*V2 修订说明（供作者与导师查阅，投稿时删除本段）：本版依据综合评阅意见完成以下文字级修订——①主线确定为"探索性数据工程系统与数据资源"，删除"首次/推翻/证明模型依赖/因果机制/专家级/替代人工/全部编码有证据"等结论；②致因标注重构为 explicit/inferred/prior 三层证据分级（对应数据库 evidence_quality 字段），全文统一分层统计口径；③银标准构建方式如实描述并标注独立性边界；④开发集与测试集重叠风险在 4.1 显式声明；⑤2×2 消融降级为"措辞敏感性观察+实用补救"，删除机制性归因与"排除替代解释"表述；⑥人机一致性限定为"一名标注者、六项字段、协议一致性"，κ/ICC/匹配率分列不再统称 Kappa；⑦全部计数对账闭合（类型 2,617+未标注6、等级 2,622+未标注1、表2 补非空分母列）；⑧M1 数字全文统一为 0.847/0.800；⑨表引用勘误（4.5 节表 7→表 8）、表 6 脚注与数值矛盾修正；⑩数据治理声明扩充（逐源许可/撤回机制/创伤影像访问控制）；⑪局限性扩为 8 项分级。图片为占位符，待作者手绘后替换。*
