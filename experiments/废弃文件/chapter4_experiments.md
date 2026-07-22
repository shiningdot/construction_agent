# Chapter 4: Experimental Evaluation

*This file will be converted to the final paper chapter.*

## 4.1 Experimental Setup (to be written separately)

## 4.2 Overall Performance Comparison

Prior work on structured accident cause extraction using large language models has established a clear hierarchy of prompting strategies. Liu et al. (2025), evaluating GPT-4o on the National Transportation Safety Board (NTSB) aviation accident dataset under the HFACS (Human Factors Analysis and Classification System) framework, reported that domain-knowledge-enhanced Chain-of-Thought (CoT) prompting consistently outperformed Few-Shot learning across all evaluation metrics. Their ranking — HFACS-CoT > HFACS-CoT+ > Few-Shot > Zero-Shot — suggested that embedding domain taxonomies directly into the reasoning chain provides a generalizable advantage over example-based learning for structured classification tasks.

Our results on DeepSeek V4 with the CAICS (Construction Accident Intelligent Classification System) taxonomy reveal a fundamentally different pattern.

[Table 1 about here: Overall performance of six prompting strategies on 100 construction accident reports]

Table 1 presents the overall performance of six prompting strategies on 100 ground-truth-annotated construction accident reports. Contrary to the findings of Liu et al. (2025), Few-Shot prompting (S1) emerges as the dominant strategy, achieving the highest average F1 score (0.737), the highest precision (0.750), and a 100% code rate with zero parse errors. Its inference speed — 13.0 seconds per sample — also makes it the most computationally efficient among competitive strategies.

Our proposed CAICS-CoT strategy (S4) ranks second with an average F1 of 0.676, approximately 8% below S1. However, S4 excels in two critical dimensions of classification accuracy. First, it achieves the highest accident type classification accuracy (TypeAcc = 0.880) among all strategies that explicitly predict type labels. Second, it attains the highest severity classification accuracy (SevAcc = 0.970), correctly identifying accident severity in all but three cases. This suggests that domain-knowledge-embedded reasoning, while not universally superior, provides particular advantages for holistic classification tasks that require integrating multiple textual cues into a single categorical judgment.

The remaining strategies — S3 (Plan-and-Solve, F1 = 0.643), S2 (Auto-CoT, F1 = 0.630), and S0 (Zero-Shot, F1 = 0.628) — form a tight cluster near the baseline, differing by less than 0.02 in F1. This clustering reveals a striking pattern: modest reasoning enhancements, such as generic planning instructions or auto-generated chain-of-thought, offer marginal to negligible improvements over simply specifying the output format. The small gap between S0 and these intermediate strategies suggests that on DeepSeek V4, the primary performance bottleneck is not the presence or absence of intermediate reasoning steps, but rather the quality and specificity of the domain knowledge embedded in those steps.

The most surprising result is the performance of S5 (CAICS-CoT+), which, despite requiring six sequential API calls per sample — more than quadruple the cost of any other strategy — achieves the lowest F1 score (0.488) among all six strategies. This score falls 14% below the Zero-Shot baseline, indicating that serial decomposition not only fails to improve extraction quality but actively harms it. We defer a detailed analysis of this failure to Section 4.3.3.

**Per-layer analysis.** Examining performance across the five CAICS layers (Management, Barrier, Trigger, Human, and Consequence) reveals strategy-specific strengths that the aggregate metrics partially obscure (Table 2).

[Table 2 about here: Per-layer F1 scores for six strategies]

S1 demonstrates consistent superiority on three of the five layers: Management (0.740), Barrier (0.738), and Human (0.763). These layers — particularly Human-factor classification (e.g., safety behavior, violation types) — require nuanced semantic understanding and contextual interpretation. The three expert-annotated examples in S1's prompt provide rich contextual templates that guide the model toward human-like classification judgments. In contrast, these layers involve the kind of contextual reasoning that Few-Shot examples, by demonstrating the inferential process rather than prescribing it, appear to facilitate most effectively.

S4, however, achieves its highest relative performance on the Trigger layer (T-layer F1 = 0.753 vs. S1's 0.708). This reversal is informative: trigger identification (e.g., "collapse of formwork due to inadequate bracing", "electric shock from exposed wiring") depends less on contextual nuance and more on aligning observed event descriptions with predefined CAICS trigger categories. The keyword-anchored reasoning embedded in CAICS-CoT — each trigger code is paired with diagnostic clues — directly supports this matching process, giving S4 an advantage precisely where the task shifts from open-ended interpretation to constrained classification.

The Consequence layer (C-layer) presents an anomaly: S1 achieves only 0.500 F1, substantially below S0's 0.762 and S4's 0.600. This counterintuitive result likely stems from S1's Few-Shot examples including consequence patterns that, while representative of the training set, introduced systematic bias in the classification distribution.

S5's per-layer performance reveals a catastrophic collapse on the Human layer (H-layer F1 = 0.287) and poor Management layer performance (M-layer F1 = 0.354). The H-layer collapse is particularly telling: human behavior classification requires the model to integrate information from management conditions (M-layer), barrier failures (F-layer), and triggering events (T-layer) to make a holistic judgment about why a worker acted unsafely. The serial decomposition architecture, by feeding only compressed summaries between layers, systematically erodes the contextual information necessary for this integrative judgment.

**Cost-efficiency analysis.** The relationship between computational cost and extraction quality further sharpens the practical implications. S1 achieves the best F1 at the lowest inference time (13.0 s/sample). S4, at 18.4 s/sample, offers a reasonable trade-off for applications prioritizing type and severity accuracy. S5, at 54.3 s/sample — 4.2 times slower than S1 — delivers substantially worse results across nearly all metrics, making it both economically and scientifically unsustainable.

## 4.3 Diagnostic Analysis: Why Domain-Knowledge CoT Initially Failed

The results in Section 4.2 present a puzzle. Domain-knowledge CoT (S4), despite embedding rich CAICS taxonomy information and explicit reasoning chains, underperforms Few-Shot learning on DeepSeek V4 — a finding that contradicts the GPT-4o-based literature. To understand why, we must examine not the final version of S4, but its developmental trajectory. The story of why CAICS-CoT initially failed, and how it was diagnosed and recovered, reveals fundamental insights about the interaction between prompt formulation and model architecture.

### 4.3.1 The Lexical Gatekeeping Effect

The initial formulation of CAICS-CoT (version v1) adopted what we term a *conditional check* format. For each CAICS code, the prompt instructed the model to verify whether the accident text contained specific keywords or phrases. For example, for the code M1 (Management: inadequate supervision), the prompt asked: "Does the text mention 'unsupervised' or 'lack of supervision' or 'no supervisor present'?" The model was then expected to output the code only if the conditional check passed.

This formulation was theoretically motivated. It operationalizes the auditability principle in explainable AI: if a causal code can be traced back to a specific textual cue, the model's reasoning becomes transparent and verifiable. It also reflects standard practice in rule-based information extraction, where keyword matching serves as the primary extraction mechanism.

The empirical results were disastrous.

S4 v1 achieved an average F1 of 0.425 — 32% below Zero-Shot and 42% below Few-Shot. More critically, its recall was 0.319, meaning the model failed to identify nearly 70% of the ground-truth codes. Analysis of 85 cases where the ground truth included M1 (inadequate supervision) revealed that the model missed this code in 73 of them — an M1 recall of merely 0.143.

Why did the model systematically miss codes that any human annotator would readily identify?

The answer lies in how the conditional check formulation interacts with DeepSeek V4's instruction-following mechanism. When instructed to verify keyword presence, the model treated listed keywords as *necessary gates* for code activation. If an accident report described inadequate supervision through paraphrased expressions — "the foreman was absent from the worksite", "no one was overseeing the operation", "the site manager had left early" — rather than exact keyword matches like "unsupervised" or "no supervisor present", the model concluded that the condition was not met and excluded the code.

We term this phenomenon the **Lexical Gatekeeping Effect**: under conditional-check prompting, the model anchors its classification decisions to the surface-level lexical items explicitly listed in the prompt, treating them as mandatory prerequisites rather than illustrative examples. The consequence is a severe and systematic recall deficit — the model fails to generalize across synonymous or paraphrastic expressions, producing classifications that are lexically faithful but semantically impoverished.

Notably, this phenomenon was not reported in Liu et al. (2025) on GPT-4o, despite their HFACS-CoT strategy similarly embedding domain knowledge within the reasoning chain. This discrepancy suggests fundamental differences in instruction-following mechanisms between model families. GPT-4o appears to interpret conditional checks probabilistically, treating listed keywords as relevance indicators rather than necessary conditions. DeepSeek V4, by contrast, exhibits conservative keyword anchoring — a literal interpretation bias that, while potentially advantageous for tasks requiring strict format compliance, becomes a liability when conditional checks are used as a reasoning scaffold for semantic inference.

### 4.3.2 The Two-Step Judgment Remedy

The diagnosis of the Lexical Gatekeeping Effect directly motivated a reformulation of the CAICS-CoT prompt. Rather than asking the model to check keyword presence as a gate condition, we redesigned the prompt around a **Two-Step Judgment** framework:

**Step 1 - Explicit Matching**: "Does the text explicitly mention conditions that match this code? If yes, output the code."

**Step 2 - Implicit Inference**: "Even if explicit keywords are absent, can this cause be reasonably inferred from the accident description? If the nature of the accident implies this causal factor, output the code."

Crucially, Step 2 was accompanied by an *authorization statement* that explicitly addressed the model's anchoring bias: "Safety analysis prefers over-labeling to under-labeling. Infer causes based on accident nature even without explicit keywords."

This reformulation shifted the model's reasoning from lexical verification to semantic inference. The keywords, rather than being removed entirely, were retained as diagnostic clues within Step 1 — but Step 2 explicitly authorized the model to go beyond them.

The reformulation also had a practical benefit: prompt compression. Removing the rigid conditional-check scaffolding reduced the prompt length from approximately 1,500 characters to approximately 600 characters per code cluster, reducing token consumption while simultaneously improving extraction quality.

The results were transformative. S4 v2 (the Two-Step formulation) achieved an average F1 of 0.676, a gain of 0.251 over v1. Recall improved from 0.319 to 0.772 — a 0.453 increase — while precision decreased from 0.819 to 0.628, a predictable trade-off given the explicit preference for over-labeling. The number of parse errors dropped from 9 to 1, and empty code outputs (samples where the model returned no codes for a layer) collapsed from 30 to 1. The average number of codes per sample increased from 1.6 to 6.5, far better reflecting the multi-causal nature of construction accidents.

Per-class analysis confirms that the improvement is concentrated on the most severely affected codes. M1 recall increased from 0.143 to 0.941 — from missing 73 out of 85 positive cases to catching 80 out of 85. M2 recall rose from 0.100 to 0.940. H3 recall improved from 0.250 to 0.800. These are not marginal improvements but fundamental recoveries of entire code categories that the v1 formulation had effectively rendered inaccessible.

The key theoretical implication is precise: the poor performance of S4 v1 was not due to an intrinsic limitation of domain-knowledge CoT reasoning, nor to an inherent inability of DeepSeek V4 to perform structured causal classification. It was due to an *inappropriate prompt formulation* that imposed lexical gatekeeping rather than enabling semantic inference. When the formulation was corrected — replacing conditional checks with the Two-Step Judgment framework — the same model, taxonomy, and task configuration produced a 59% relative improvement in F1.

This finding carries a methodological lesson for prompt engineering in structured extraction tasks: *the form of the reasoning instruction matters as much as the content of the domain knowledge it encodes*. A prompt that encodes correct domain taxonomy but constrains the model's inferential latitude can perform worse than a prompt with less domain knowledge but better reasoning architecture.

### 4.3.3 Why Serial Decomposition (S5) Failed

Given the success of S4's integrated CoT reasoning, a natural question arises: would further decomposition of the reasoning process yield additional gains? S5 (CAICS-CoT+) addressed this question by decomposing the extraction into six sequential API calls — one per CAICS layer plus a final summary call for accident type and severity — with each layer receiving a compressed summary of the previous layer's outputs as context.

The answer is emphatically negative.

S5 achieved an F1 of 0.488 — the lowest among all six strategies, 14% below the Zero-Shot baseline. Despite requiring six API calls per sample (54.3 seconds per sample, 4.2× slower than S1), it failed to approach the performance of any strategy that performed single-pass inference.

Diagnostic analysis reveals two root causes.

**First, cross-layer context decay.** Each S5 layer call receives not the full accident report but a summary of the previous layer's outputs. This compressed representation progressively loses information. The management-layer analysis, for instance, must summarize "inadequate safety training" and "poor supervisory oversight" into a short textual summary that is then passed to the barrier-layer call. By the time the Human layer receives its input, the rich contextual details of the original accident narrative have been filtered through three layers of summarization. The model attempting to classify human factors is effectively working with a distorted and impoverished version of the evidence.

**Second, holistic judgment necessity for the Human layer.** The H-layer collapse (F1 = 0.287) is the most dramatic symptom of this decay. Human behavior classification — determining whether an action was a routine violation, exceptional violation, perceptual error, or skill-based mistake — requires simultaneous consideration of management conditions (What training was provided?), barrier states (What safeguards were present or absent?), and triggering events (What immediate circumstances precipitated the action?). These factors are causally interdependent; the reason a worker bypasses a safety procedure can only be understood by examining the full ecological context. S5's serial architecture, by construction, prevents the model from performing this integrative judgment in a single reasoning pass.

The cost-benefit analysis is unequivocal. S5 consumes 4.2 times more computational resources than S1 while delivering 66% of its F1 performance. It consumes 2.9 times more resources than S4 while delivering 72% of its F1. For practical deployment, serial decomposition of causal chain analysis is not merely suboptimal — it is counterproductive.

The broader implication is theoretical: **causal chain analysis in accident investigation requires the LLM to perceive the entire accident narrative holistically within a single inference pass.** The causal codes across the M-F-T-H-C chain are not independent variables to be classified separately; they form a coherent explanatory structure in which each layer's meaning is partially constituted by its relationship to the others. Decomposing this structure into independent serial subtasks breaks the very explanatory coherence that makes causal analysis valuable.

## 4.4 Discussion

### 4.4.1 Model-Prompt Dependency: Revisiting Liu et al. (2025)

The most significant finding of this experimental study is the reversal of the strategy ranking reported in Liu et al. (2025). Table 3 provides a direct comparison.

[Table 3 about here: Comparison of strategy rankings between this study and Liu et al. (2025)]

Where Liu et al. found domain-knowledge CoT (HFACS-CoT) to be optimal on GPT-4o, our study finds Few-Shot (S1) to be optimal on DeepSeek V4. Where they found Few-Shot to be inferior to CoT-based strategies, we find it dominant. Where they reported CoT+ (serial decomposition with cross-layer context) as the second-best strategy, we find it the worst.

This reversal demands explanation, not merely documentation. We propose that the determining factor is not the task domain (construction vs. aviation), nor the taxonomy (CAICS vs. HFACS), but the underlying model architecture and its interaction with prompt formulation.

GPT-4o and DeepSeek V4 differ in training data composition, instruction-tuning methodology, and optimization objectives. These differences manifest in distinct instruction-following styles. GPT-4o, as suggested by Liu et al.'s results, exhibits robust semantic generalization under CoT prompts — it treats embedded domain knowledge as guidance for reasoning rather than as literal constraints. DeepSeek V4, as our Lexical Gatekeeping Effect demonstrates, exhibits a more literal interpretation bias in conditional formulations, making it simultaneously more format-compliant and less semantically flexible.

The golden sentence of this discussion is therefore: **The optimal prompting strategy exhibits strong coupling with the underlying model architecture — a finding that urges caution in directly transferring prompting strategies across model families without empirical verification.**

This finding has direct implications for safety informatics applications. Many construction industry deployments in China rely on domestic large language models due to data sovereignty requirements, cost considerations, or API availability limitations. Our results demonstrate that these models cannot simply replicate prompting strategies proven effective on Western models. They require independent empirical calibration, and our finding that Few-Shot outperforms CoT-based strategies provides a practical starting point for such calibration.

### 4.4.2 Practical Recommendations

Based on the experimental evidence, we offer three practical recommendations for deploying LLM-based structured extraction in construction accident analysis.

**First, for structured extraction on domestic LLMs, Few-Shot prompting is the recommended default strategy.** S1 achieves the highest F1 score, the lowest inference time, zero parse errors, and complete output rates. Its three expert-annotated examples provide sufficient contextual guidance without the risk of lexical gatekeeping introduced by conditional CoT formulations. For organizations deploying similar systems, investing in high-quality expert annotations for the Few-Shot prompt will yield better returns than investing in detailed CoT prompt engineering.

**Second, if Chain-of-Thought reasoning is required — for applications that prioritize classification accuracy over extraction completeness, or that require auditable reasoning traces — adopt the Two-Step Judgment framework and explicitly avoid conditional-check formulations.** The S4 v1 to v2 transition demonstrates that the difference between a failed and successful CoT strategy can be purely a matter of prompt formulation, not model capability or task difficulty. The authorization statement ("safety analysis prefers over-labeling to under-labeling") is a critical component that directly counteracts the Lexical Gatekeeping Effect.

**Third, serial decomposition for causal chain analysis should be avoided.** The S5 results demonstrate that breaking the causal chain into independent serial subtasks systematically degrades extraction quality. Causal codes across layers form an interdependent explanatory structure that requires holistic single-pass inference. Any architectural design that fragments this structure — whether through independent API calls, cascade pipelines, or modular processing — risks introducing cross-layer context decay that undermines the very purpose of causal analysis.

### 4.4.3 Limitations and Future Work

Several limitations of this study warrant acknowledgment and suggest directions for future research.

**Single-model focus.** All experiments were conducted on DeepSeek V4. While this provides controlled comparability across strategies, it limits the generalizability of our findings. The Lexical Gatekeeping Effect was diagnosed on this specific model, and while we hypothesize that it reflects broader architectural tendencies in instruction-tuned LLMs, direct evidence from GPT-4o, Claude, Gemini, and other model families would strengthen this claim. Future work should conduct cross-model comparisons to establish whether the strategy ranking we observed is specific to DeepSeek V4 or reflects a more general property of LLM-based structured extraction.

**Domain specificity.** The CAICS taxonomy is purpose-built for construction accident analysis. Whether the Lexical Gatekeeping Effect and the Two-Step Judgment remedy generalize to other domains — such as medical error analysis, industrial incident investigation, or financial fraud detection — remains an open empirical question. We suspect that the phenomenon is task-agnostic (stemming from general instruction-following mechanisms) but acknowledge that domain complexity, taxonomy granularity, and text characteristics may modulate its severity.

**Ground truth scale.** The evaluation is based on 100 manually annotated construction accident reports. While this sample size is comparable to prior work in LLM-based structured extraction (Liu et al., 2025 used 113 samples), it represents a fraction of the diversity in construction accident scenarios. Larger-scale validation — ideally across 1,000+ samples spanning multiple construction types, geographic regions, and severity levels — would provide more robust estimates of the reported effect sizes.

**Temperature and determinism.** All experiments used temperature = 0.0 to ensure deterministic outputs. While this controls for sampling variance and enables clean strategy comparison, it does not reflect the deployment conditions under which many practitioners operate. Future work should characterize how strategy rankings change under non-zero temperature settings and assess the stability of the Two-Step Judgment remedy across varying stochasticity levels.

**Cost optimization.** The S5 results demonstrate that more API calls do not necessarily produce better outcomes. Future work should explore more sophisticated cost-performance trade-offs, including adaptive strategies that allocate more computational resources to uncertain cases and fewer to clear-cut ones.

## 4.5 Chapter Summary

Three principal findings emerge from this experimental evaluation. First, contrary to prior work on GPT-4o, Few-Shot prompting (S1) is the most robust and cost-effective strategy on DeepSeek V4 for structured accident cause extraction, achieving the highest F1 score (0.737) at the lowest inference cost (13.0 s/sample). Second, the Lexical Gatekeeping Effect — a systematic recall deficit induced by conditional-check CoT formulations — was diagnosed as the root cause of the initial failure of our proposed CAICS-CoT strategy. The Two-Step Judgment remedy reformulated the reasoning process from lexical verification to semantic inference, raising average F1 from 0.425 to 0.676 and recovering per-class recall by up to 0.84. Third, serial decomposition of the causal chain (S5) is fundamentally unsuitable for causal analysis due to cross-layer context decay and the necessity of holistic single-pass inference, performing below the Zero-Shot baseline despite consuming 4.2 times more computational resources. Together, these findings establish that the optimal prompting strategy is strongly coupled to the underlying model architecture, and that prompt formulation quality — not merely the presence or absence of domain knowledge — determines the effectiveness of CoT-based extraction in safety informatics applications.
