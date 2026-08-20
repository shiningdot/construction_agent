# EIC Review Report

**Manuscript**: CAIEMS
**Venue assessment**: Expert Systems with Applications
**Reviewer role**: Editor-in-Chief — journal fit, originality, significance, editorial viability.

## Journal Fit

ESWA publishes applied-AI system papers with substantive empirical evaluation, and this manuscript fits that profile: a working system (2,623-record database, Web/API access), a domain-specific causal taxonomy, and three experiments. The paper also builds directly on a prior ESWA publication (Liu et al., 2025a), which the journal's readership will recognize — this is both an asset (conversation continuity) and a risk (the manuscript must exceed "same study, different domain/model").

## Originality Assessment

The genuinely novel elements are: (i) the evidence-bound coding requirement as a structural anti-hallucination device; (ii) the LLM-first MECE enumeration procedure with measurable "other"-class reduction; (iii) the documented v1→v2 prompt failure with quantified recall collapse; (iv) the agreement certification at expert levels. Against this, the manuscript overclaims in two places: "LLM-optimized" (C1) is never operationalized, and "first database built entirely through LLM-driven extraction" (C2) invites priority disputes that the authors' own Related Work section makes easy to raise. The paper would be stronger claiming "the first construction accident database with evidence-bound causal annotations and MECE-standardized fields, released with an agreement-certified extraction pipeline" — specific, verifiable, and defensible.

## Significance

If the Lexical Gatekeeping Effect survives an unconfounded test, it is a genuinely transferable finding for the prompt-engineering literature — the kind of named, remedied failure mode that gets cited. The agreement study's rigor (a priori sample sizing, CIs, expert-range baselines) sets a benchmark for applied-LLM papers in safety domains. The database itself, once released, becomes an infrastructure contribution for the construction safety community. The potential is there; the current draft does not yet secure it.

## Editorial Concerns

1. **The flagship claims rest on confounded comparisons** (phrasing + emission instruction changed together; model + task changed together). I concur with the reviewers that these are repairable: the 2×2 ablation is inexpensive on the existing 100-record sample, and a same-task multi-model run is feasible given the authors' demonstrated API access to four models. A Major Revision should require the ablation; the multi-model same-task test can be scoped as strongly recommended.
2. **Taxonomy derivation must be documented** (Section 3.2). For ESWA's mixed readership, a Methods subsection describing the CAICS derivation (data-driven induction, expert validation) is mandatory; the operational definitions alone cannot carry the construct validity of the paper's core artifact.
3. **Claims versus evidence discipline.** The abstract currently leads with claims the body cannot yet support (model-dependence; "first"). ESWA editors see this pattern frequently; the fix is to align claim strength with Section 5's honest hedging, which the paper already contains.
4. **Reproducibility**: code availability statement is missing entirely. The pipeline, evaluation scripts, and prompts must be released or their availability declared; ESWA's reproducibility expectations apply to system papers.
5. The bilingual mirror and figure set are in good shape; Figure 5 (gatekeeping diagnosis) is the right hero figure.

## Decision Input for the Editorial Meeting

The manuscript is fundamentally sound engineering with one potentially important behavioral finding that is currently under-evidenced, plus correctable overclaims. It is not desk-reject material; it is also not Accept material in present form. With the ablation result and disciplined claims, this could be a strong ESWA paper.

**Preliminary recommendation**: Major Revision.

**Scores**: Originality 7/10, Significance 7/10, Rigor 6/10, Writing 8/10, Fit 8/10.
