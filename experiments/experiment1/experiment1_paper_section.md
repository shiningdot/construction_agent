## 6 Experimental Results and Model Selection

### 6.1 Experimental Setup

The evaluation was conducted on a stratified sample of 200 accident records from the Silver Standard dataset (Section 5), which contains 972 manually-validated records covering 11 accident types and 15 years of construction accident data from mainland China. Stratified sampling ensured proportional representation of each accident type, with a minimum of three samples per category.

Each model received an identical system prompt specifying a 16-field JSON output schema and the same three few-shot examples. All models were configured with temperature set to 0.0 for deterministic output, with two exceptions: Kimi K2.6 (which mandates temperature = 1.0 per its API specification) and GLM 5.2 (which required 16,384 max tokens due to its chain-of-thought reasoning overhead). No search enhancement was applied, as the objective was to isolate each model's intrinsic extraction capability.

The evaluation measured two dimensions:
- **Core field accuracy**: Exact-match accuracy on six fundamental attributes — accident type (11-class), severity (4-class), death toll (numeric), injury count (numeric), provincial location (closed set), and city (open set). City and location comparisons were normalized to ignore administrative suffixes (e.g., "广东" and "广东省" were treated as equivalent).
- **Causal factor extraction precision and recall**: For each of the four causal dimensions (human, machine, management, and environmental factors), we computed per-dimension precision, recall, and F1-score by comparing each model's extracted causes against the Silver Standard annotations.

This evaluation framework differs from prior work in two important respects. Liu et al. (2025) evaluated LLMs on aviation accident narratives using binary classification accuracy for each HFACS sub-category. Our approach instead measures field-level extraction accuracy and cause-level precision/recall, which more directly reflects the requirements of a production information extraction system.

### 6.2 Core Field Extraction

Table 3 presents the accuracy of each model across the six core fields. The three frontier models — DeepSeek V4, Qwen 3.7, and GLM 5.2 — achieved comparable overall accuracy of 91.2%, 91.5%, and 91.5% respectively, demonstrating that all three are capable of reliable structured extraction from Chinese construction accident narratives. Kimi K2.6 lagged substantially at 63.9%, primarily due to its mandatory temperature setting of 1.0, which introduced undesirable output variance across repeated runs and produced malformed JSON in 12.5% of cases.

**Table 3.** Core field extraction accuracy (%) across six attributes.

| Field | DeepSeek V4 | Qwen 3.7 | GLM 5.2 | Kimi K2.6 |
|-------|:-----------:|:--------:|:-------:|:---------:|
| Accident Type | 86.0 | 85.0 | 84.5 | 61.5 |
| Severity | 98.0 | 90.5 | 97.5 | 69.5 |
| Deaths | 96.0 | 96.0 | 97.0 | 68.5 |
| Injuries | 90.0 | 91.0 | 91.0 | 58.5 |
| Location | 96.0 | 98.5 | 96.0 | 67.0 |
| City | 81.5 | 88.0 | 83.0 | 58.5 |
| **Overall** | **91.2** | **91.5** | **91.5** | **63.9** |

All models exhibited high accuracy on severity (90.5%–98.0%), death toll (96.0%–97.0%), and provincial location (96.0%–98.5%), the last being simplified by China's well-defined set of 34 provincial-level administrative divisions. City-level extraction proved more challenging (81.5%–88.0% for the three capable models), not because the models failed to locate the city, but because Chinese accident reports frequently refer to county-level cities that are administratively subordinate to the prefecture-level city recorded in the Silver Standard. For example, an accident reported in "华蓥市" (Huaying, a county-level city) is coded under "广安市" (Guang'an, its prefecture-level parent city) in the ground truth. DeepSeek V4 was more likely to output the specific locality mentioned in the source text, which, while geographically accurate, was penalized by the exact-match metric.

Among the six fields, accident type classification showed the widest variation (84.5%–86.0% for the top three models), suggesting that distinguishing between categories such as "collapse" vs. "landslide" or "mechanical injury" vs. "vehicle injury" requires nuanced understanding of construction context that remains challenging for all current models.

### 6.3 Causal Factor Extraction

A more revealing pattern emerged in the causal factor extraction task, where models were required to identify and classify accident causes into four dimensions. Table 4 reports per-dimension precision and recall for each model.

**Table 4.** Causal factor extraction: precision / recall (%).

| Dimension | DeepSeek V4 | Qwen 3.7 | GLM 5.2 | Kimi K2.6 |
|-----------|:----------:|:--------:|:-------:|:---------:|
| Human | 95.0 / 44.7 | 83.3 / 76.5 | 83.9 / 61.2 | 88.2 / 35.3 |
| Machine | 91.9 / 41.9 | 88.8 / 64.0 | 87.6 / 67.6 | 96.4 / 39.0 |
| Management | 89.9 / 54.9 | 67.5 / 95.6 | 92.3 / 63.7 | 97.6 / 36.3 |
| Environment | 81.0 / 69.4 | 72.4 / 85.7 | 73.9 / 69.4 | 81.5 / 44.9 |
| **Average** | **89.5 / 52.7** | **78.0 / 80.5** | **84.4 / 65.5** | **90.9 / 38.9** |

DeepSeek V4 achieved the highest average precision (89.5%), substantially outperforming Qwen 3.7 (78.0%) and GLM 5.2 (84.4%). However, this came at the cost of lower recall (52.7% vs. 80.5% for Qwen 3.7). This pattern reflects fundamentally different extraction philosophies rather than a simple difference in capability. DeepSeek V4 employs a conservative strategy: it extracts a causal factor *only* when the source text provides explicit linguistic evidence (e.g., keywords such as "违章操作" [violation] or "监管缺失" [supervision deficiency] directly appearing in the narrative). Qwen 3.7, by contrast, adopts a more aggressive approach, inferring causes from contextual cues even when no explicit causal language is present.

This precision-recall trade-off invites comparison with the prior work of Liu et al. (2025), who reported that GPT-4o with HFACS-CoT prompting achieved an average recall of 74.9% and precision of 46.2% across ten HFACS sub-categories in aviation accident analysis. Their study prioritized recall ("failing to identify a true cause could result in overlooking critical information") and accepted lower precision as a trade-off. Our findings present a complementary perspective: in the domain of structured accident database construction — where the extracted data feeds downstream statistical analysis, trend detection, and data mining — the priority shifts. An extraction model that systematically hallucinates causes will inject noise into every downstream task, eroding the reliability of the entire database.

It is important to clarify that this is not a claim about safety domain preferences ("precision is more important than recall for safety"), but a claim about **extraction system design principles**. The primary function of an information extraction system is to faithfully represent what is stated in the source text, not to supplement or infer additional causal relationships. From this standpoint, DeepSeek V4's higher precision indicates greater faithfulness to the input: its extracted causes can be traced back to explicit textual evidence with high confidence. Qwen 3.7's higher recall includes instances where extracted causes are plausible given the context but lack direct support in the original narrative — effectively blurring the line between extraction and generation. For the purpose of constructing a reliable, auditable accident database, extraction faithfulness is the more appropriate criterion.

### 6.4 Additional Selection Criteria

Beyond extraction performance, three practical considerations further motivated the selection of DeepSeek V4 as the backbone model for CAIEMS.

**Operational Cost.** Over the 200-sample evaluation, DeepSeek V4 incurred a total API cost of USD 0.13, compared to USD 0.43 for Qwen 3.7, USD 2.91 for GLM 5.2, and USD 2.84 for Kimi K2.6. Extrapolating to the full Silver Standard dataset of 972 records, the projected costs are USD 0.63 for DeepSeek V4 versus USD 14.14 for GLM 5.2 — a factor of 22×. For a system designed to operate continuously, ingesting new accident reports daily, this cost differential is decisive for practical deployment.

**Inference Speed.** DeepSeek V4 processed each record in an average of 9.8 seconds, comparable to Qwen 3.7 (8.6 s) and significantly faster than GLM 5.2 (27.0 s) and Kimi K2.6 (74.9 s). The latency of GLM 5.2 and Kimi K2.6 arises from their reasoning-oriented architectures: GLM 5.2 employs step-by-step chain-of-thought reasoning requiring up to 16,384 output tokens per query, while Kimi K2.6's enforced temperature of 1.0 reduces output stability, occasionally requiring retries. In a real-time web application setting, sub-10-second inference per record is essential for acceptable user experience.

**API Ecosystem Compatibility.** DeepSeek V4 supports both Anthropic-compatible and OpenAI-compatible API protocols, providing deployment flexibility across different infrastructure environments. In the Chinese domestic computing environment, where direct access to foreign API endpoints (Anthropic, OpenAI, Google) is often unreliable due to network restrictions, DeepSeek V4 offers stable, low-latency connectivity without requiring proxy or relay services. Furthermore, the availability of open-weight variants of the DeepSeek model family enables future migration to on-premises deployment for organizations with data sovereignty requirements.

### 6.5 Model Selection Decision

Based on the composite evidence, we selected **DeepSeek V4** as the primary extraction backbone for CAIEMS. The decision rests on five convergent findings: (1) extraction faithfulness, as evidenced by the highest cause-level precision (89.5%); (2) core field accuracy statistically indistinguishable from the best-performing model (91.2% vs. 91.5%); (3) lowest operational cost by a wide margin ($0.13 vs. $2.91 for GLM 5.2 over 200 samples); (4) fastest inference speed (9.8 s/record), compatible with real-time web interaction; and (5) best API ecosystem compatibility for the Chinese computing environment. No single model excels across all dimensions, but DeepSeek V4 occupies the most favorable position on the performance-cost Pareto frontier.

This selection does not imply that DeepSeek V4 is universally superior; it is the optimal choice for the specific requirements of CAIEMS, where extraction faithfulness, operational cost, and inference speed are critical. In applications where recall is paramount — such as exploratory safety analysis where the cost of missing a potential cause outweighs the cost of false positives — Qwen 3.7's higher recall may be preferable. The modular pipeline architecture of CAIEMS (Section 3) accommodates this flexibility: users can substitute the default DeepSeek V4 backbone with alternative models depending on the specific requirements of their analysis task.
