# Review Report — R1: Methodology Reviewer (LLM 评测方法论)

**Manuscript**: CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction
**Target venue**: Expert Systems with Applications
**Reviewer profile**: LLM evaluation methodology specialist; benchmarks and reproducibility.

## Summary of the Manuscript

The paper presents an end-to-end LLM pipeline that converts Chinese construction accident narratives into a structured, causally annotated database (2,623 records), introduces the CAICS 19-category causal taxonomy, and reports three experiments: backbone model selection (4 models × 200 records), prompt strategy comparison (6 strategies × 100 records) including the diagnosis of a "Lexical Gatekeeping Effect," and a human-AI agreement study (n = 289).

## Strengths

- The evaluation chain (selection → optimization → certification) is logically well constructed and covers the full production decision sequence.
- The agreement study is methodologically careful: sample size fixed a priori via the Fleiss' kappa SE formula, two-stage stratified sampling with a per-cell floor, and 95% CIs reported throughout. This is better statistics than most applied-LLM papers.
- Evidence-bound coding is a genuinely sound design for auditable annotation, and the 100% schema-conformant output rate (Experiment 1) is a meaningful reliability metric.
- The paper reports cost and latency alongside quality, which is rare and valuable for deployment-oriented venues.

## Major Concerns

**M1. The v1→v2 comparison conflates two simultaneous changes (Section 4.4.3).** The remedy description states that v2 (a) replaced conditional checks with two-step judgment AND (b) replaced the conservative instruction with a coverage preference. The causal attribution of the +59% F1 recovery to the *phrasing* change is therefore confounded: the coverage-preference instruction alone ("prefer over-coding to under-coding") could plausibly account for most of the recall recovery, since v1's "do not fabricate" instruction directly suppresses recall. The paper names and generalizes an effect ("Lexical Gatekeeping Effect") on the basis of this confounded comparison. Required: an ablation (v2a: two-step judgment with conservative instruction; v2b: conditional checks with coverage preference) or a reframed causal claim. This is the paper's flagship finding; the evidence as presented does not isolate it.

**M2. No variance estimates or significance tests for the strategy comparison.** F1 differences of 0.737 vs 0.676 on n = 100 records are reported as a ranking reversal without any confidence interval or test. With 19 categories and several rare codes (the ground truth contains C1 once and C2 four times), per-code and aggregate F1 variance is substantial. The manuscript itself acknowledges low-frequency code unreliability only indirectly. Please report bootstrap CIs or McNemar-style tests for the head-to-head comparisons, especially S1 vs S4.

**M3. Model version opacity.** All DeepSeek results are reported as "DeepSeek V4" without tier/version identifiers, and the experimental setup states an "auto tier routing" API configuration. A reviewer, and any replicator, cannot know which model produced the reported numbers. Reproducibility requires exact model identifiers and, if routing was active, a statement of how routing decisions were logged.

**M4. The silver standard validation process is underspecified (Section 4.1).** "Manually validated ... checked the extracted basic fields against the source narratives and corrected errors" — no protocol detail, no inter-annotator agreement for the validation itself, no statement of what proportion of the 972 records required correction. The 972-record silver standard carries the evaluation; its own reliability must be documented.

**M5. Cross-sample comparison in the agreement study (Section 4.5).** The LLM-human kappa is computed on n = 289 with a single graduate-student annotator, while the "expert range" baseline comes from a different sample (n = 100, three experts). Comparing agreement statistics across different samples and different annotator counts weakens the "within expert range" inference. At minimum, discuss how sample composition differences could shift the comparison; ideally, have the same three experts annotate a subsample of the 289 for a matched comparison.

**M6. Experiment 1's model selection confounds tier with vendor.** If the DeepSeek candidate ran under auto tier routing (M3), the cost and latency comparisons against fixed-tier competitors are not like-for-like. The 22× cost claim (Section 4.3) inherits this issue.

## Minor Points

- Table 4: city accuracy (81.5–88.0%) is explained as an administrative-level mismatch. A small error-analysis table (county-level vs prefecture-level matches) would strengthen the explanation and is cheap to produce.
- Section 4.2: state the tie-breaking rule used when a model outputs multiple codes that map to one ground-truth dimension (set-level vs micro-averaged matching).
- The temperature-0.0 design gives point estimates only; state this as a limitation for stochastic behavior explicitly in the limitations (currently mentioned only for Kimi).
- Table 6's "valid-output rate" for S2 (85%) with post-processed recovery: clarify whether the recovered outputs are included in S2's F1 and whether the same recovery was applied to other strategies (fairness of comparison).

## Recommendation

Major Revision. The agreement study and pipeline evaluation are strong; the flagship Lexical Gatekeeping Effect claim requires the ablation in M1 before the paper can carry its central named finding.

**Score**: Originality 7/10, Methodological Rigor 6/10, Evidence Sufficiency 6/10, Argument Coherence 7/10, Writing Quality 7/10.
