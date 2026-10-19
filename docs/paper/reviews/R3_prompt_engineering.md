# Review Report — R3: Perspective Reviewer (提示工程/LLM 行为)

**Manuscript**: CAIEMS
**Reviewer profile**: LLM behavior and prompt-engineering researcher; CoT variants, in-context learning, instruction-following.

## Summary

The manuscript's most ambitious claims are behavioral: (i) a named prompt failure mode, the Lexical Gatekeeping Effect, in which interrogative conditional-check phrasing converts keyword cues into mandatory gates and collapses recall; (ii) a strategy-ranking reversal versus Liu et al. (2025a) interpreted as evidence that prompt effectiveness is model-dependent; (iii) a mechanistic account (pattern-matching mode vs. reasoning mode) for both.

## Strengths

- The v1 F1 = 0.425 observation is striking and well quantified: 30% empty outputs, M1 recall 0.143 on the most frequent ground-truth category. Whatever the causal story, this is a real, severe failure that the community needs documented.
- The two-step judgment design (explicit matching + authorized inference) is a sensible operational pattern for recall-critical structured extraction, and its effects are large.
- The discussion of serial vs. integrated reasoning (S5 failure) adds a genuinely useful design principle for layered causal frameworks.

## Major Concerns

**P1. The v1→v2 intervention is confounded — and the named effect rests on it.** Two prompt properties changed simultaneously: (a) interrogative → declarative phrasing with two-step judgment; (b) conservative instruction ("do not fabricate") → coverage preference ("over-coding preferred"). The paper attributes the F1 recovery (0.425 → 0.676) to the phrasing mechanism, but change (b) alone directly manipulates the model's emission threshold — the exact quantity that collapsed (recall). Without a 2×2 ablation, "Lexical Gatekeeping Effect" is a hypothesis, not an established effect. The manuscript's own Appendix A.5 documents both changes in one breath, which confirms the confound rather than dispelling it.

**P2. The model-dependence claim is confounded with task-dependence.** The ranking reversal is established across two dimensions simultaneously: model (GPT-4o vs. DeepSeek V4) AND task (aviation HFACS vs. construction CAICS). Liu et al.'s dataset (113 aviation narratives, 10-category HFACS question set) differs from ours (100 construction records, 19-category causal coding with evidence output) in difficulty, taxonomy density, and output format. The reversal could be task-driven, domain-driven, or model-driven; the paper asserts model-dependence without ruling out the alternatives. Section 5.1's capability-profile explanation (code-pretraining vs. RLHF alignment) is plausible but post-hoc. A same-task, multi-model comparison (e.g., running S1/S4 on 2-3 models with the CAICS task) would isolate the claim; the paper explicitly defers this to future work — which means the claim, as stated in the abstract, currently outruns its evidence.

**P3. The mechanistic account (reasoning mode vs. pattern-matching mode) is unverified.** The manuscript offers an attention-level explanation for the gatekeeping behavior ("attention concentrates on fragments containing the exact keywords") without any behavioral or representational probe. Suggest either (a) softening the mechanism language to hypothesis level, or (b) adding a cheap diagnostic: e.g., test whether the effect attenuates with paraphrase-augmented keyword lists, or whether declarative phrasing with the SAME keyword set recovers most of the loss (a partial ablation).

**P4. S2's post-processed recovery creates an unfair comparison.** 96/100 S2 outputs drifted to a hierarchical format and were recovered by a post-processing script; no other strategy received post-processing. S2's reported F1 = 0.630 is thus an upper-bound estimate of a different pipeline. State this explicitly at the point of comparison (currently only the valid-output rate hints at it).

**P5. Temperature 0.0 throughout, single run each.** All point estimates are single-sample; for a paper whose core contribution is a behavioral effect, a variance estimate (3 runs or a self-consistency-style majority vote) would materially strengthen the effect's robustness. At minimum, acknowledge the single-run limitation next to the effect, not only in the general limitations.

## Minor Points

- The term "Lexical Gatekeeping Effect" is catchy but invites the question of scope: is it specific to safety-domain prompts, Chinese text, or this model? One sentence defining the intended scope boundary would preempt overgeneralization criticism.
- The code-density calibration observation (S1 5.0 vs. GT 5.0) is a nice empirical nugget; consider foregrounding it as a secondary finding.
- Section 4.4.2's claim that few-shot "is the more reliable default for the open-model family" generalizes from a single open model (DeepSeek V4). Soften to a hypothesis or add the obvious Qwen/LLaMA follow-up citation where available.

## Recommendation

Major Revision. P1 and P2 target the paper's two headline behavioral claims; both require either new controlled evidence (ablation, multi-model same-task) or substantially narrowed claim language.

**Score**: Originality 8/10, Methodological Rigor 6/10, Evidence Sufficiency 6/10, Argument Coherence 7/10, Writing Quality 8/10.
