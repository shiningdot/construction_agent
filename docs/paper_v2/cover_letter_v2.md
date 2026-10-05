# Cover Letter（V2 版草案）

> **与 V1 版的差异**：V1 版（`docs/paper/cover_letter.md`）写于 V2 修订之前，
> 有 3 处论断须处置，直接沿用会与正文不符——
> ①"Liu et al. 的排序被我们反转"（V2 仅将 Liu et al. 用作维度级匹配的指标
> 定义与表 10 对比项，已删去"挑战已发表工作"的表述）；
> ②"verified expert applicability (Fleiss' Kappa 0.859–1.000 across layers)"
> ——**这一处与①③性质不同**：该数字 V2 正文中英**各 2 处确在**（§3.3/§4.1），
> 问题不在数字而在措辞，见③后一句；
> ③"MECE 的 other 类从 18.9% 降到 2.9%"（V2 中的 2.9% 是 WT02 字段占比，
> 与此无关）。**②单独的缘由**：把**标注者之间的一致性**（inter-annotator
> agreement）说成了**体系已被验证为可用**，属过度解读，对应导师第 8 条。
> 另有"the first controlled decomposition""causally isolates"
> "verified expert applicability" 等禁用措辞。故重写。
>
> 本文件不参与 `build_v2_docs.py` 的四格式构建。

---

**Subject**: Manuscript submission to *Expert Systems with Applications* — CAIEMS: An Exploratory LLM-Driven Pipeline for Structuring Construction Accident Narratives into a Queryable Database

Dear Editor-in-Chief,

We are pleased to submit our manuscript entitled "CAIEMS: An Exploratory LLM-Driven Pipeline for Structuring Construction Accident Narratives into a Queryable Database" for consideration for publication in *Expert Systems with Applications*.

Construction accident reports constitute a large but underused source of safety knowledge, and their conversion into analyzable records remains labor-intensive. Our manuscript presents CAIEMS, an exploratory LLM-driven data engineering pipeline that processes publicly available Chinese construction accident narratives into a queryable database prototype of 2,623 records with 33 fields and 16,323 causal annotations. We report it as an explored system and data resource, with the scope of every claim stated explicitly.

We believe the manuscript fits the journal's scope for three reasons. First, it contributes a reusable data resource and an end-to-end pipeline that integrates dual-track collection, LLM classification, structured field extraction, causal coding under the CAICS operational scheme (5 layers, 19 categories), and a Web/REST interface — the kind of expert-system application that turns unstructured domain text into queryable structured knowledge. Second, it contributes a **three-tier evidence scheme** — verbatim-evidence, semantically induced, and domain-prior inference codes, stored and reported separately — which we regard as the manuscript's most transferable methodological element for safety informatics, where the reliability of extracted knowledge is the central concern. Third, all three supporting experiments are reported with their boundaries stated throughout: the four-model comparison ran under unequal conditions and is presented as a system-level selection exercise rather than a capability ranking; the six-strategy prompt comparison surfaced a prompt-phrasing sensitivity that we term the Lexical Gatekeeping Effect (M1 recall 0.141 under the affected phrasing, raised to 0.941 by a two-step judgment rephrasing in the same setting), reported as an observation with a practical remedy rather than an isolated causal claim; and the protocol study reports agreement between the LLM and one trained annotator on six basic fields (Cohen's kappa 0.91–0.97 for categorical fields; ICC 0.985 for counts; 99.7% year-month match for dates). We state explicitly that the silver standard is partly model-derived, that prompt development and final testing share one sample, and that the database reflects publicly retrievable accident coverage rather than a representative sample of all construction accidents.

The database, pipeline, evaluation scripts, and experiment runners will be released publicly upon publication; an anonymous read-only snapshot and the CAICS operational definitions will be provided for peer review upon request. Public accessibility of the source reports does not by itself authorize bulk collection and redistribution, so a per-source permission or fair-use assessment, a takedown mechanism, and masking of identifiable content are planned before any public release.

All results were verified against primary data files and expert annotations; the six prompting strategies' performance is reported with bootstrap confidence intervals under majority-vote expert ground truth; and all citations were individually verified.

We confirm that this manuscript has not been published elsewhere and is not under consideration by another journal. All authors have approved the manuscript and agree with its submission. We declare no conflicts of interest. Large language models are both the object of study and the instrument of this work; LLM-based assistance was used for language polishing during manuscript preparation, while all scientific content, experimental design, and data originate from the authors' work and were verified by the authors.

Thank you for considering our manuscript. We look forward to your response.

Sincerely,

Tianhao Sun（孙天浩）
Nanjing Tech University（南京工业大学）
Email: 202461201086@njtech.edu.cn

[Supervisor name], Corresponding Author
Nanjing Tech University

---

**Suggested reviewers**（可选项，投稿系统通常要求 3–5 位推荐审稿人）：

1. 施工安全 × NLP 交叉方向学者（研究事故报告文本挖掘）
2. LLM 提示工程/结构化抽取方向学者
3. 人因可靠性 / 事故致因分类体系应用方向学者

*注：请与导师商定是否提供推荐审稿人名单及具体人选。*
