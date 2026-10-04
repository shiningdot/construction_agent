# Cover Letter（V1 版，存档）

> ⚠️ **存档，勿投。** 本版写于 V2 修订之前，**有 3 处论断在 V2 正文中已不存在**
> （声称反转了 Liu et al. 的排序、引用 Fleiss' Kappa 0.859–1.000、称 MECE
> other 类由 18.9% 降到 2.9%），另有 4 处禁用措辞。详见
> `docs/paper_v2/修订对账记录.md` 第十五节。
> **投稿请用 `docs/paper_v2/cover_letter_v2.md`**（校验：`scripts/check_cover_letter_v2.py`）。

---

**Subject**: Manuscript submission to Expert Systems with Applications — CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction

Dear Editor-in-Chief,

We are pleased to submit our manuscript entitled "CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction" for consideration for publication in Expert Systems with Applications.

Construction accident reports remain the least exploited resource in safety research because their conversion into analyzable records has resisted automation. Our manuscript presents CAIEMS, an end-to-end LLM-driven pipeline that converts unstructured Chinese construction accident narratives into a structured, causally annotated, interactive online database of 2,623 records with evidence-bound causal codes, and it reports three experiments that jointly certify the pipeline's reliability.

We believe the manuscript fits ESWA's scope for four reasons. First, it advances a finding that directly extends and challenges prior work published in this journal: where Liu et al. (2025, ESWA 269:126422) established that HFACS-guided chain-of-thought prompting outperforms few-shot prompting on GPT-4o, our six-strategy comparison on DeepSeek V4 finds the ranking reversed, with Few-Shot optimal — evidence that prompt-strategy effectiveness depends on the model or the task, and a practical consequence for every practitioner designing LLM extraction systems. Second, the manuscript identifies, causally isolates, and remedies a new prompt-design failure mode, which we term the Lexical Gatekeeping Effect: conditional-check CoT prompts convert illustrative keyword lists into mandatory gates, collapsing recall on exactly the highest-frequency implicit causes (M1 recall 0.141). A controlled 2×2 ablation on the same 100-record sample demonstrates that the phrasing change alone accounts for 0.80 of the total 0.847 M1-recall recovery, ruling out alternative explanations. To our knowledge, this is the first controlled decomposition of this failure mode, with direct design guidance for recall-critical domains (safety analysis, medical screening). Third, the manuscript contributes a construction-specific causal taxonomy (CAICS, 5 layers, 19 categories) with operational definitions and verified expert applicability (Fleiss' Kappa 0.859–1.000 across layers), plus an LLM-first MECE enumeration procedure that reduced the residual "other" class from 18.9% to 2.9%. Fourth, the resulting database — 2,623 records, 16,323 evidence-bound causal codes, Web platform and REST API access — will be released publicly upon publication, supporting reproducible research in construction safety informatics.

All results were verified against primary data files and expert annotations; the six prompting strategies' performance is reported with bootstrap confidence intervals under majority-vote expert ground truth; and all citations were individually verified.

We confirm that this manuscript has not been published elsewhere and is not under consideration by another journal. All authors have approved the manuscript and agree with its submission to Expert Systems with Applications. We declare no conflicts of interest.

Thank you for considering our manuscript. We look forward to your response.

Sincerely,

Tianhao Sun（孙天浩）
Nanjing Tech University（南京工业大学）
Email: 202461201086@njtech.edu.cn

[Supervisor name], Corresponding Author
Nanjing Tech University

---

**Suggested reviewers**（可选项，投稿系统通常要求 3-5 位推荐审稿人）：

1. 施工安全 × NLP 交叉方向学者（研究事故报告文本挖掘）
2. LLM 提示工程/结构化抽取方向学者
3. 人因可靠性/HFACS 应用方向学者

*注：请与导师商定是否提供推荐审稿人名单及具体人选。*
