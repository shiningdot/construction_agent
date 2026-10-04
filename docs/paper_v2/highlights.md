# Highlights（草案，待作者与导师确认）

> Elsevier 要求：**3–5 条，每条不超过 85 个字符（含空格）**，作为**独立文件**
> 随稿提交（不并入正文）。本文件不参与 `build_v2_docs.py` 的四格式构建。
>
> 措辞约束：遵守 CLAUDE.md 既定决策第 5 条（不得出现 首次/推翻/证明/
> 因果机制/专家级/替代人工 等）。全部数字取自正文统一值，未引入新数字。

1. An LLM pipeline turns Chinese accident reports into a queryable database

2. 2,623 records with 16,323 evidence-bound causal codes

3. Every code carries one of three evidence tiers: explicit, inferred, or prior

4. A 2×2 ablation isolates the gatekeeping step and prompt wording effects

5. A web interface and API expose the records for interactive filtering

---

## 中文对照（供作者核对，不随稿提交）

1. 一条 LLM 管道将中文施工事故报道转化为可查询数据库

2. 2,623 条记录、16,323 条证据约束致因编码

3. 每条编码归属三级证据之一：explicit / inferred / prior

4. 2×2 消融分离门控环节与提示措辞各自的影响

5. Web 界面与 API 支持交互式筛选查询

---

## 逐条溯源（作者自查用）

| # | Highlight | 正文依据 |
|---|---|---|
| 1 | 管道定位 | 摘要；§3 系统；§5.1 |
| 2 | 记录与编码规模 | 表 1、表 4；数字对账清单（2,623 / 16,323） |
| 3 | 三级证据分层 | §3.1；表 4；清单（explicit 14,784 / inferred 1,117 / prior 422） |
| 4 | 2×2 消融 | §4.3；表 8 |
| 5 | 交互式访问 | §5.4；表 10「Web + API」行 |
