# Review Report — R2: Domain Reviewer (施工安全/事故分析)

**Manuscript**: CAIEMS
**Reviewer profile**: Construction safety researcher; accident causation modeling (HFACS lineage, 24Model, Accimap); Chinese construction accident reporting conventions.

## Summary

The manuscript contributes a construction-specific causal taxonomy (CAICS, 5 layers/19 codes), a 2,623-record LLM-extracted database of Chinese construction accidents, and three validation experiments. The domain-facing claims are: CAICS fills a gap left by aviation-derived HFACS adaptations; the database is the first LLM-built interactive construction accident database.

## Strengths

- The CAICS layer design (M/F/T/H/C) maps well to how Chinese construction accident reports are actually written: management failures and physical protection failures dominate narratives, and delayed/concealed reporting (C2) is a regulatory reality that genuinely compounds casualties. Encoding C2 as a causal layer rather than a metadata attribute is a defensible, domain-informed choice.
- The taxonomy size (19 categories) is reasonable for statistical power; the paper explicitly discusses sparsity as a design constraint, which most taxonomy papers ignore.
- The severity and type distributions (Table 3) are plausible against official Chinese statistics, lending credibility to the corpus.
- The MECE enumeration procedure (LLM observes the value distribution before designing categories) is a practical contribution to safety-report standardization, and the 18.9% → 2.9% "other" reduction for work task is concrete evidence of its value.

## Major Concerns

**D1. CAICS derivation methodology is not described.** Section 3.2 states the taxonomy is "empirically grounded in our corpus" but never explains the derivation procedure: how were the 19 categories generated, by whom, and through what iteration? Was this a grounded-theory analysis, expert panel, or LLM-assisted induction? A reviewer needs this to judge whether CAICS is a principled construct or a post-hoc binning of extraction outputs. The paper has the material for this (the operational definition table exists); a Methods subsection (data sources, coding iterations, expert validation of the taxonomy itself) is required.

**D2. Missing dialogue with existing construction-specific causation models.** The manuscript contrasts CAICS only with HFACS. Chinese construction safety research has well-established alternatives: the 24Model (Fu Gui's group, already in the authors' own bibliography as [Xuecai et al., 2024]), STAMP, and Accimap (used by Liu and Luo, 2025). A one-paragraph comparative positioning of CAICS against 24Model — the dominant Chinese accident causation model — is necessary for the domain audience; its absence will be noticed immediately by Chinese safety researchers.

**D3. The 12-category accident type scheme mixes two granularity levels.** 土方坍塌 (earthwork collapse) is listed as a type separate from 坍塌 (collapse), and 淹溺/中毒窒息 sit alongside mechanical categories. Chinese reporting standards (GB 6441-86 企业职工伤亡事故分类 and the 生产安全事故报告和调查处理条例 classifications) treat some of these at different levels of the same hierarchy. The paper should state which standard its scheme follows or justify the hybrid. This matters because the kappa study's "semantically adjacent boundaries" disagreement (Section 4.5) is at least partly an artifact of this hybrid scheme.

**D4. Era heterogeneity is unaddressed.** The corpus spans 1978–2026. Reporting practices, regulatory regimes (2002 安全生产法, 2007/2011 事故条例 revisions), and construction technology changed fundamentally across this window. A record from 1985 and one from 2025 are different text genres. Please report the year distribution and discuss whether extraction quality or code distributions vary by era; otherwise readers cannot interpret temporal analyses built on the database.

**D5. Foreign-location records.** The database contains accidents at overseas sites (the corpus includes records with foreign country names in the location field, per the sampling description of "31 provincial-level regions" in Section 3.3 — which implies non-mainland records exist). The paper claims "all 31 provincial-level regions of mainland China" coverage but never accounts for overseas records in the statistics. Clarify inclusion criteria.

## Minor Points

- The F-layer label "Protection Failure" is good; consider noting the parallel to barrier-based models (Swiss cheese / energy barrier), which would strengthen the theoretical grounding at near-zero cost.
- The C-layer (consequence amplification) conceptually overlaps with emergency-response phases of accident process models; one sentence of positioning suffices.
- Consider citing one Chinese-language HFACS-construction study (e.g., the 修正的HFACS study present in the authors' literature folder) to demonstrate engagement with the Chinese-language safety literature, which dominates practice in this domain.

## Recommendation

Major Revision. D1 and D2 are essential for the domain contribution claim (C1); D3/D4 affect the interpretability of the database itself.

**Score**: Originality 7/10, Methodological Rigor 6/10, Evidence Sufficiency 6/10, Argument Coherence 7/10, Writing Quality 7/10.
