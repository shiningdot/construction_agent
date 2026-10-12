"""致因（CAICS）GT 装配：逐编码三方投票，只把非全一致的格子交人工。

规则（用户口径）：
  3/3 三方都选   → auto_include（直接采纳）
  0/3 三方都不选 → auto_exclude（直接否决）
  2/3 或 1/3     → pending_human（交人工裁决）

同时给每个待裁格计算「库内证据强度」（evidence 与原文的 10-gram 重叠），
<0.2 的标为「重点核对」并排在标注页最前。
注意：算重叠时 evidence 与原文**两侧都要归一化**——此前漏归一化原文导致数字被严重低估。

产出（experiments/golden_standard/gt/）：
  gt_causes_draft.jsonl   300 条，19 类逐编码 provenance + 当前编码集合
  cause_annotation.html   人工裁决页（294 条事故 / 916 格）
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
GT = GOLD / "gt"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from analyze_causes_threeway import ALL_CODES, CODE_NAME, LAYERS  # noqa: E402

NGRAM = 10
STRONG = 0.8
MID = 0.5
WEAK = 0.2


def norm(s):
    return re.sub(r"[\s“”\"'·,，。.：:；;（）()\[\]【】—\-–_/、]", "", s or "")


def overlap(evidence: str, raw: str) -> float | None:
    """evidence 与原文的 10-gram 重叠率。两侧都归一化。无 evidence 返回 None。"""
    e, t = norm(evidence), norm(raw)
    if not e:
        return None
    if len(e) < NGRAM or len(t) < NGRAM:
        return 1.0
    g = {e[i:i + NGRAM] for i in range(len(e) - NGRAM + 1)}
    tg = {t[i:i + NGRAM] for i in range(len(t) - NGRAM + 1)}
    return len(g & tg) / len(g)


def strength_label(s):
    if s is None:
        return "无证据"
    if s >= STRONG:
        return "≥0.8 基本原句"
    if s >= MID:
        return "0.5-0.8 有改写"
    if s >= WEAK:
        return "0.2-0.5 部分重合"
    return "<0.2 重点核对"


def codes_of(path: Path):
    out = {}
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        out[r["id"]] = set((r.get("parsed") or {}).get("codes") or [])
    return out


def db_codes_evidence(ids):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ",".join("?" * len(ids))
    codes, ev = defaultdict(set), {}
    for aid, code, evidence in con.execute(
            f"select accident_id, code, evidence from caics_causes where accident_id in ({q})", ids):
        codes[aid].add(code)
        ev[(aid, code)] = evidence
    con.close()
    return codes, ev


def main():
    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = list(cand)
    F = codes_of(GOLD / "S1_golden_results.jsonl")
    P = codes_of(GOLD / "pro/S1_golden_results.jsonl")
    D, EV = db_codes_evidence(ids)

    rows, items, auto_inc, auto_exc = [], [], 0, 0
    strength = Counter()
    for i in ids:
        prov, codes_final, pending = {}, [], []
        for c in ALL_CODES:
            v = {"flash": c in F.get(i, set()), "pro": c in P.get(i, set()),
                 "db": c in D.get(i, set())}
            n = sum(v.values())
            if n == 3:
                prov[c] = "auto_include"
                codes_final.append(c)
                auto_inc += 1
            elif n == 0:
                prov[c] = "auto_exclude"
                auto_exc += 1
            else:
                prov[c] = "pending_human"
                pending.append(c)
                s = overlap(EV.get((i, c)), cand[i]["raw_text"])
                strength[strength_label(s)] += 1
                items.append({"id": i, "title": cand[i]["title"], "code": c,
                              "votes": v, "evidence": EV.get((i, c)) if v["db"] else None,
                              "strength": s, "label": strength_label(s),
                              "hard": s is not None and s < WEAK})
        rows.append({"id": i, "title": cand[i]["title"], "tier": cand[i]["tier"],
                     "base_codes": codes_final, "provenance": prov,
                     "pending_human": pending})

    assert auto_inc + auto_exc + len(items) == len(ids) * len(ALL_CODES), "格数对不上"

    GT.mkdir(parents=True, exist_ok=True)
    with (GT / "gt_causes_draft.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 标注页数据：只带有待裁格的事故；含重点核对项的事故排前
    page = []
    for r in rows:
        if not r["pending_human"]:
            continue
        its = [it for it in items if it["id"] == r["id"]]
        page.append({"id": r["id"], "t": r["title"],
                     "txt": re.sub(r"\n{2,}", "\n", "\n".join(
                         l.rstrip() for l in (cand[r["id"]]["raw_text"] or "").splitlines())).strip(),
                     "items": [{"c": it["code"], "v": it["votes"],
                                "ev": it["evidence"], "s": it["strength"],
                                "lab": it["label"], "hard": it["hard"]} for it in its],
                     "hard": sum(1 for it in its if it["hard"])})
    page.sort(key=lambda a: (-a["hard"], -len(a["items"]), a["t"]))

    stats = {"nAcc": len(ids), "nCells": len(ids) * len(ALL_CODES),
             "autoInc": auto_inc, "autoExc": auto_exc, "pending": len(items),
             "cards": len(page), "hard": sum(strength["<0.2 重点核对"] for _ in [0]),
             "codeName": CODE_NAME, "allCodes": ALL_CODES,
             "layers": [{"k": k, "n": nm, "cs": cs} for k, nm, cs in LAYERS],
             "strength": dict(strength),
             "fieldCn": {}}
    draft = {r["id"]: {"title": r["title"], "base_codes": r["base_codes"],
                       "provenance": r["provenance"]} for r in rows}
    tpl = (Path(__file__).parent / "cause_annotate_template.html").read_text(encoding="utf-8")
    html = (tpl.replace("/*__DATA__*/", json.dumps(page, ensure_ascii=False))
               .replace("/*__STATS__*/", json.dumps(stats, ensure_ascii=False))
               .replace("/*__DRAFT__*/", json.dumps(draft, ensure_ascii=False)))
    (GT / "cause_annotation.html").write_text(html, encoding="utf-8")

    L = ["# 致因（CAICS）GT 报告", "",
         "> 口径：逐编码三方投票，**三方全一致的格子自动确定，非全一致的格子交人工裁决**。", "",
         f"- 事故 {len(ids)} 条 × CAICS 19 类 = {stats['nCells']} 格", "",
         "| 票型 | 格数 | 处理 |", "|---|---:|---|",
         f"| 3/3 三方都选 | {auto_inc} | 自动**采纳**（`auto_include`） |",
         f"| 0/3 三方都不选 | {auto_exc} | 自动**否决**（`auto_exclude`） |",
         f"| 2/3 或 1/3 分歧 | {len(items)} | **人工裁决**（`pending_human`） |",
         "", f"待裁 {len(items)} 格分布在 **{len(page)} 条事故**（{len(ids)-len(page)} 条无争议）。", "",
         "## 待裁格的库内证据强度（10-gram 重叠，越高越贴原文）", "",
         "| 强度 | 格数 | 标注页 |", "|---|---:|---|"]
    for k in ["≥0.8 基本原句", "0.5-0.8 有改写", "0.2-0.5 部分重合", "<0.2 重点核对", "无证据"]:
        L.append(f"| {k} | {strength.get(k,0)} | {'标「重点核对」并排最前' if k=='<0.2 重点核对' else ('库内未编码，无证据可比' if k=='无证据' else '')} |")
    L += ["", "## 用法", "",
          "打开 `cause_annotation.html`：左侧原始语料，右侧每个待裁编码显示三方票型与库内证据。",
          "点「采纳 / 否决」即定；三方都没提但原文支持的，可在「补充编码」里自填（19 类限定下拉）。",
          "顶部「导出 JSONL」下载裁决结果，「导出完整 GT」导出自动确定 + 人工裁决合并后的完整编码集。",
          "", "合并回草稿：`python3 scripts/golden_s1/apply_annotations.py`（自动识别致因标注格式，",
          "校验通过后写回 `gt_causes_draft.jsonl` 并删除标注文件）。", "",
          "## 为什么不能直接拿库内当 GT", "",
          "`caics_causes` 的编码、证据、推理链均由本项目自己的 **Step2 管道一次 LLM 调用**生成",
          "（`step2_caics_encoder.py:273`），由批处理脚本直写最终库，**入库前无人工审核**。",
          "用它当 GT 去评测 Step2 属于循环论证。库内证据可作人工核对草稿——实测其 10-gram 重叠中位 0.85，",
          "但整句逐字命中原文仅 45.2%，且 `evidence_quality` 是关键词规则现算的（默认即 `direct_quote`），",
          "不能当作\"证据可靠\"的凭证。", ""]
    (GT / "CAUSE_GT_REPORT.md").write_text("\n".join(L), encoding="utf-8")

    print(f"格数 {stats['nCells']} = 三方都选 {auto_inc} + 三方都不选 {auto_exc} + 待裁 {len(items)}")
    print(f"待裁涉及事故 {len(page)} / {len(ids)}")
    print("待裁格证据强度:", dict(strength))
    print(f"产出 → {GT}/gt_causes_draft.jsonl, cause_annotation.html")


if __name__ == "__main__":
    main()
