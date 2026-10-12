"""三方比对 24 个客观事故特征，生成 GT 初稿 + 人工标注网页。

规则（按用户口径）：
  - 只保留客观事故特征 24 个；剔除 摘要/直接原因/天气/具体部位/区县/标题/报告类型
  - flash / pro / 库内 Step1 三方一致 → 直接确定为 GT
  - 三方有异 → 生成人工标注项（网页里给候选值 + 原始语料，可选可自填）

产出（experiments/golden_standard/gt/）：
  gt_draft.jsonl      300 条，24 字段的当前取值 + 每格来源标记
  annotation.html     人工标注页（含原始语料、导出 JSONL 按钮）
  GT_REPORT.md        本轮口径、统计、待人工清单
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
GT = GOLD / "gt"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from count_threeway import (FIELDS, EXCLUDED, ENUM, NUM, NUM_TOL, DATE, TEXT,
                            load_results, load_db, three_way, norm)  # noqa: E402

FIELD_CN = {
    "accident_type": "事故类型", "severity": "严重程度", "project_type": "工程类型",
    "building_structure": "建筑结构类型", "construction_phase": "施工阶段",
    "work_task": "作业类型", "contractor_type": "承包类型", "collapse_subtype": "坍塌子类",
    "equipment_involved": "涉及设备", "material_involved": "涉及材料", "worker_type": "伤亡人员工种",
    "deaths": "死亡人数", "injuries": "受伤人数", "trapped_count": "被困人数",
    "direct_economic_loss": "直接经济损失(万元)", "fall_height": "坠落高度(米)",
    "date": "发生日期", "accident_time": "发生时刻", "location": "省",
    "city": "市", "project_name": "工程名称", "developer": "建设单位",
    "contractor": "施工单位", "supervision_company": "监理单位",
}


def majority(cands):
    """三方非空取值里是否存在 ≥2 票相同的值；返回该值或 None。"""
    vals = [v for v in cands if norm(v) is not None]
    if not vals:
        return None
    c = Counter(norm(v) for v in vals)
    top, n = c.most_common(1)[0]
    if n >= 2:
        for v in cands:                      # 返回原始写法
            if norm(v) == top:
                return v
    return None


def enum_of(field):
    """取该字段的合法枚举（用于网页做下拉/校验）。"""
    try:
        import sys as _s
        _s.path.insert(0, str(ROOT / "scripts/golden_s1"))
        from prompt_build import _load_step1_tool
        return _load_step1_tool()["input_schema"]["properties"].get(field, {}).get("enum", [])
    except Exception:  # noqa: BLE001
        return []


def main():
    flash = load_results(GOLD / "S1_golden_results.jsonl")
    pro = load_results(GOLD / "pro/S1_golden_results.jsonl")
    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = list(cand)
    db = load_db(ids)

    out_rows, items, per_field = [], [], defaultdict(Counter)
    for i in ids:
        bf, prov, need = {}, {}, []
        for f in FIELDS:
            a, b, c = flash[i].get(f), pro[i].get(f), db[i].get(f)
            same, val, cands = three_way(f, a, b, c)
            if same:
                bf[f] = val
                prov[f] = "three_agree"
                per_field[f]["same"] += 1
            else:
                maj = majority(cands)
                bf[f] = maj                      # 有则预填众数值，无则留空待人工
                prov[f] = "majority_prefill" if maj is not None else "blank"
                per_field[f]["diff"] += 1
                need.append({"field": f, "cands": cands, "prefill": maj,
                             "has_majority": maj is not None})
                items.append({"id": i, "title": cand[i]["title"], "field": f,
                              "cands": cands, "prefill": maj,
                              "has_majority": maj is not None})
        out_rows.append({"id": i, "title": cand[i]["title"], "tier": cand[i]["tier"],
                         "source_name": cand[i]["source_name"],
                         "basic_features": bf, "provenance": prov,
                         "needs_human": [n["field"] for n in need]})

    GT.mkdir(parents=True, exist_ok=True)
    with (GT / "gt_draft.jsonl").open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 网页数据：只带需要决策的字段 + 原文（保留段落结构，只收敛多余空行）
    import re as _re
    page = [{"id": r["id"], "t": r["title"],
             "txt": _re.sub(r"\n{2,}", "\n", "\n".join(
                 l.rstrip() for l in (cand[r["id"]]["raw_text"] or "").splitlines())).strip(),
             "items": [{"f": it["field"], "c": it["cands"], "p": it["prefill"],
                        "m": it["has_majority"]}
                       for it in items if it["id"] == r["id"]]}
            for r in out_rows if r["needs_human"]]

    n_items = len(items)
    n_maj = sum(1 for it in items if it["has_majority"])
    stats = {"nAcc": len(ids), "nItems": n_items, "nMajority": n_maj,
             "nBlank": n_items - n_maj, "fields": FIELDS,
             "fieldCn": FIELD_CN, "enum": {f: enum_of(f) for f in ENUM},
             "perField": {f: dict(per_field[f]) for f in FIELDS}}

    draft = {r["id"]: {"title": r["title"], "basic_features": r["basic_features"],
                       "provenance": r["provenance"]} for r in out_rows}
    tpl = (Path(__file__).parent / "annotate_template.html").read_text(encoding="utf-8")
    html = (tpl.replace("/*__DATA__*/", json.dumps(page, ensure_ascii=False))
               .replace("/*__STATS__*/", json.dumps(stats, ensure_ascii=False))
               .replace("/*__DRAFT__*/", json.dumps(draft, ensure_ascii=False)))
    (GT / "annotation.html").write_text(html, encoding="utf-8")

    L = ["# GT 初稿与人工标注报告", "",
         f"- 事故 {len(ids)} 条 × 客观特征 {len(FIELDS)} 个 = {len(FIELDS)*len(ids)} 格",
         f"- **三方一致直接确定 {len(FIELDS)*len(ids) - n_items} 格**",
         f"- **待人工 {n_items} 格**（其中 {n_maj} 格三方中有两方相同，网页已预填众数值可直接确认；"
         f"{n_items-n_maj} 格三方互异需实判）",
         f"- 涉及事故 {len(page)} / {len(ids)} 条", "",
         "## 剔除的字段（不在本次 GT 范围）", "",
         "摘要、直接原因、天气、具体部位、区县、标题、报告类型", "",
         "## 各字段情况", "", "| 字段 | 三方一致 | 待人工 | 其中可预填 | 自动率 |", "|---|---:|---:|---:|---:|"]
    for f in FIELDS:
        c = per_field[f]
        t = c["same"] + c["diff"]
        nm = sum(1 for it in items if it["field"] == f and it["has_majority"])
        L.append(f"| {FIELD_CN[f]} `{f}` | {c['same']} | {c['diff']} | {nm} | {c['same']/t*100:.1f}% |")
    L += ["", "## 用法", "",
          "打开 `annotation.html`：左侧是原始语料（带查找高亮），右侧是每个待定字段的三方候选值。",
          "点候选值即选中；三方都不对时在输入框自填。底部「导出 JSONL」下载标注结果。",
          "「一键采纳多数」可把三方中有两方相同的格子批量填入众数值，只需复核剩下的。",
          "进度存在浏览器本地，刷新不丢。", ""]
    (GT / "GT_REPORT.md").write_text("\n".join(L), encoding="utf-8")

    print(f"三方一致直接确定 {len(FIELDS)*len(ids)-n_items} / {len(FIELDS)*len(ids)}")
    print(f"待人工 {n_items} 格（{n_maj} 可预填，{n_items-n_maj} 三方互异），涉及 {len(page)} 条事故")
    print(f"产出 → {GT}/gt_draft.jsonl, annotation.html, GT_REPORT.md")


if __name__ == "__main__":
    main()
