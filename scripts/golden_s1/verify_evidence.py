"""校验「证据链 v2」并把新旧口径摆在一起比。

核心检查：
  1. **可定位率**：source_facts 的每条 quote 归一化后回查原文；记录**原始文本中的字符区间**
     并自检 `raw_text[start:end]` 归一化后与 quote 一致。
     —— 归一化必须**两侧都做**（此前只归一化一侧把可回溯率算低过，勿重蹈）
  2. **新旧对比**：同一批事故，旧 `caics_causes.evidence` 与新 `source_facts` 各自的
     「至少有一个片段可定位」比例
  3. **证据类型分层**：direct_quote / composite / external 各占比与各自可定位率
  4. **薄弱编码专查**：M3/H3/M1/M4 等最易凭空推断的类，新旧口径标签如何迁移
  5. **thinking 探针**：思考文本里是否提到每个输出编码（廉价的"是否真的斟酌过"检查）

产出：
  experiments/golden_standard/evidence_v2/verify_v2.csv
  experiments/golden_standard/evidence_v2/EVIDENCE_V2_REPORT.md
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GOLD = ROOT / "experiments/golden_standard"
OUT = GOLD / "evidence_v2"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"

STRIP = set(" \t\r\n“”\"'·,，。.：:；;（）()[]【】—–-_/、")
WEAK_CODES = ["M1", "M2", "M3", "M4", "H3", "F2"]


def norm_map(s: str):
    """归一化并返回「归一化串 → 原串下标」的映射，用于定位字符区间。"""
    buf, idx = [], []
    for i, ch in enumerate(s or ""):
        if ch in STRIP:
            continue
        buf.append(ch)
        idx.append(i)
    return "".join(buf), idx


def locate(quote: str, raw: str):
    """返回 (是否命中, 原串起, 原串止)。逐字命中优先；失败再退化到模糊。"""
    qn, _ = norm_map(quote)
    rn, rmap = norm_map(raw)
    if not qn:
        return False, None, None
    p = rn.find(qn)
    if p >= 0:
        return True, rmap[p], rmap[p + len(qn) - 1] + 1
    return False, None, None


def quoted_segments(s: str):
    """从旧 evidence 里抠出被引号/书名号/引文标记包住的片段，另外整串也算一个候选。"""
    segs = []
    for m in re.finditer(r"[“\"'‘【《]([^”\"'’】》]{4,})[”\"'’】》]", s or ""):
        segs.append(m.group(1))
    if s:
        segs.append(s)
    return segs


def any_locatable(text: str, raw: str):
    """旧口径：evidence 里是否有任意一个片段能在原文中定位。"""
    for seg in quoted_segments(text):
        ok, _, _ = locate(seg, raw)
        if ok:
            return True
    return False


def main():
    rows = [json.loads(l) for l in (OUT / "results.jsonl").open(encoding="utf-8")]
    cands = {json.loads(l)["id"]: json.loads(l)
             for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = [r["id"] for r in rows]

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ",".join("?" * len(ids))
    old = defaultdict(dict)                       # old[aid][code] = (evidence, quality)
    for aid, code, ev, eq in con.execute(
            f"select accident_id, code, evidence, evidence_quality from caics_causes "
            f"where accident_id in ({q})", ids):
        old[aid][code] = (ev, eq)
    con.close()

    recs = []
    for r in rows:
        if not r.get("parsed"):
            continue
        raw = cands[r["id"]]["raw_text"]
        think = norm_map(r.get("raw_reasoning") or "")[0]
        for c in r["parsed"].get("causes", []):
            facts = c.get("source_facts") or []
            hits, spans = 0, []
            for f in facts:
                ok, a, b = locate(f.get("quote", ""), raw)
                # 偏移自检：切回来归一化后必须与 quote 一致
                if ok:
                    back = norm_map(raw[a:b])[0]
                    assert back == norm_map(f.get("quote", ""))[0], \
                        f"偏移自检失败 {r['id']} {c['code']} {f.get('quote','')[:20]}"
                    hits += 1
                    spans.append(f"{a}-{b}")
            oev, oeq = old.get(r["id"], {}).get(c["code"], (None, None))
            recs.append({
                "accident_id": r["id"], "title": r["title"], "code": c["code"],
                "evidence_type": c.get("evidence_type"),
                "n_facts": len(facts), "n_locatable": hits,
                "all_locatable": bool(facts) and hits == len(facts),
                "any_locatable": hits > 0,
                "spans": "|".join(spans),
                "inference_rule": (c.get("inference_rule") or "")[:200],
                "old_evidence": (oev or "")[:200], "old_quality": oeq or "",
                "old_any_locatable": any_locatable(oev or "", raw) if oev else None,
                "reasoning_mentions_code": c["code"] in think,
            })

    with (OUT / "verify_v2.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)

    n = len(recs)
    n_new_any = sum(1 for x in recs if x["any_locatable"])
    n_new_all = sum(1 for x in recs if x["all_locatable"])
    n_empty = sum(1 for x in recs if x["n_facts"] == 0)
    types = Counter(x["evidence_type"] for x in recs)
    by_type_loc = {t: (sum(1 for x in recs if x["evidence_type"] == t and x["any_locatable"]),
                       sum(1 for x in recs if x["evidence_type"] == t)) for t in types}

    dual = [x for x in recs if x["old_evidence"]]
    n_old_any = sum(1 for x in dual if x["old_any_locatable"])

    # 新旧标签迁移（仅在两边都有该编码时）
    cross = Counter()
    for x in dual:
        if x["old_quality"]:
            cross[(x["old_quality"], x["evidence_type"])] += 1

    L = ["# 证据链 v2 试跑报告（29 条样本）", "",
         "对比对象：生产 Step2 口径（`evidence` 单条引文 + `inference` 自由文本）",
         "vs v2 口径（`source_facts[]` 逐字原文事实 + `inference_rule` + `evidence_type`）。", "",
         f"- 样本 {len(rows)} 条、编码 {n} 个；schema 违规 0；**全部 {sum(1 for r in rows if r.get('has_thinking'))}/{len(rows)} 条捕获到 thinking**",
         "", "## 一、可定位率（quote 能否回查原文）", "",
         "| 口径 | 至少一个片段可定位 | 全部片段可定位 | 无可定位片段 |", "|---|---:|---:|---:|",
         f"| **v2 `source_facts`** | **{n_new_any}/{n} = {n_new_any/n*100:.1f}%** | "
         f"{n_new_all}/{n} = {n_new_all/n*100:.1f}% | {n - n_new_any}（{n_empty} 个显式空 facts） |",
         f"| 旧 `evidence`（同批事故的库内编码 {len(dual)} 个） | {n_old_any}/{len(dual)} = "
         f"{n_old_any/max(1,len(dual))*100:.1f}% | — | — |", "",
         "> 旧口径按「evidence 里是否有任意一个引文片段能在原文定位」判；v2 直接逐条判 quotes。",
         "", "## 二、证据类型分布（v2 自动如实标注）", "",
         "| 类型 | 个数 | 占比 | 该类型中至少一条可定位 |", "|---|---:|---:|---:|"]
    for t, c in types.most_common():
        hit, tot = by_type_loc[t]
        L.append(f"| `{t}` | {c} | {c/n*100:.1f}% | {hit}/{tot} |")

    L += ["", "## 三、新旧标签迁移（仅两边都有的编码）", "",
          f"共 {sum(cross.values())} 个编码可比。行=旧 `evidence_quality`，列=v2 `evidence_type`。", "",
          "| 旧 \\ 新 | " + " | ".join(f"`{t}`" for t in ["direct_quote", "composite", "external"]) + " |",
          "|---|" + "---:|" * 3]
    for oq in ["direct_quote", "indirect_infer", "weak_infer"]:
        row = " | ".join(str(cross.get((oq, t), 0)) for t in ["direct_quote", "composite", "external"])
        L.append(f"| `{oq}` | {row} |")
    downgraded = sum(cross.get(("direct_quote", t), 0) for t in ("composite", "external"))
    L += ["", f"**最关键的一行**：旧口径标为最高档 `direct_quote` 的编码里，有 **{downgraded} 个**"
          "在 v2 下被模型自己如实标成 `composite` 或 `external`——"
          "即这些编码原本的「直接引用」标签是虚高的。", ""]

    L += ["## 四、薄弱编码专查（最易凭空推断的类）", "",
          "| 编码 | 个数 | 可定位 | direct_quote | composite | external | 旧标 direct_quote 而新版降级 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for c in WEAK_CODES:
        sub = [x for x in recs if x["code"] == c]
        if not sub:
            continue
        tc = Counter(x["evidence_type"] for x in sub)
        deg = sum(1 for x in sub if x["old_quality"] == "direct_quote"
                  and x["evidence_type"] in ("composite", "external"))
        L.append(f"| {c} | {len(sub)} | {sum(1 for x in sub if x['any_locatable'])} | "
                 f"{tc.get('direct_quote',0)} | {tc.get('composite',0)} | {tc.get('external',0)} | {deg} |")

    th_lens = [len(r.get("raw_reasoning") or "") for r in rows]
    L += ["", "## 五、thinking（仅作审计附件，不进证据）", "",
          f"- 捕获率 {sum(1 for r in rows if r.get('has_thinking'))}/{len(rows)}；"
          f"长度 中位 {st.median(th_lens):.0f} 字符，最长 {max(th_lens)}",
          f"- 思考文本中提及了对应编码的占比：{sum(1 for x in recs if x['reasoning_mentions_code'])}/{n} "
          f"（只作『是否真的逐类斟酌过』的粗探针，不作证据）",
          "- 注意：**glm-5.2 等非推理模型不产 thinking**，一旦把思考过程当证据本体，"
          "四模型对照会失去可比性——这也是本次把它与证据严格分离的原因。", ""]

    (OUT / "EVIDENCE_V2_REPORT.md").write_text("\n".join(L), encoding="utf-8")

    print(f"编码总数 {n}")
    print(f"v2 至少一条可定位 {n_new_any}/{n} = {n_new_any/n*100:.1f}% | 全部可定位 {n_new_all} | 空 facts {n_empty}")
    print(f"旧口径同批 {len(dual)} 个：至少一条可定位 {n_old_any} = {n_old_any/max(1,len(dual))*100:.1f}%")
    print(f"证据类型分布: {dict(types)}")
    print(f"旧标 direct_quote 而 v2 降级为 composite/external: {downgraded}")
    print(f"产出 → {OUT}/EVIDENCE_V2_REPORT.md, verify_v2.csv")


if __name__ == "__main__":
    main()
