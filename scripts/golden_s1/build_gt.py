"""三方比对 24 个客观事故特征，生成 GT 初稿 + 人工标注网页。

规则（按用户口径）：
  - 只保留客观事故特征 24 个；剔除 摘要/直接原因/天气/具体部位/区县/标题/报告类型
  - flash / pro / 库内 Step1 三方一致 → 直接确定为 GT
  - 三方有异 → 生成人工标注项（网页里给候选值 + 原始语料，可选可自填）

产出（experiments/golden_standard/gt/）：
  gt_draft.jsonl      300 条，13 字段的当前取值 + 每格来源标记
  annotation.html     人工标注页（含原始语料、导出 JSONL 按钮）
  GT_REPORT.md        本轮口径、统计、待人工清单

⚠️ 本脚本从零重建 gt_draft.jsonl，会覆盖已有的人工裁决（human_decision）。
   重跑前请先备份；或用 apply_annotations.py 把人工标注合回。
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
from resolve_rules import resolve as rule_resolve  # noqa: E402

FIELD_CN = {
    "accident_type": "事故类型", "severity": "严重程度", "project_type": "工程类型",
    "collapse_subtype": "坍塌子类", "deaths": "死亡人数", "injuries": "受伤人数",
    "trapped_count": "被困人数", "direct_economic_loss": "直接经济损失(万元)",
    "fall_height": "坠落高度(米)", "date": "发生日期", "accident_time": "发生时刻",
    "location": "省", "city": "市",
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
        bf, prov, need, notes = {}, {}, [], {}
        for f in FIELDS:
            a, b, c = flash[i].get(f), pro[i].get(f), db[i].get(f)
            same, val, cands = three_way(f, a, b, c)
            if same:
                bf[f] = val
                prov[f] = "three_agree"
                per_field[f]["same"] += 1
            else:
                maj = majority(cands)
                per_field[f]["diff"] += 1
                if maj is not None:
                    # 三方中有两方相同 → 采用众数值，自动确定，不进人工
                    bf[f] = maj
                    prov[f] = "majority_auto"
                    per_field[f]["auto"] += 1
                else:
                    # 三方互异 → 先试规则判定（时刻/城市可机械判）
                    rv, note = rule_resolve(f, cands)
                    if note is not None:
                        bf[f] = rv                      # rv 为 None 表示应置空
                        prov[f] = "rule_resolved"
                        notes[f] = note
                        per_field[f]["rule"] += 1
                    else:
                        bf[f] = None                    # 真正需要人读原文
                        prov[f] = "needs_human"
                        per_field[f]["human"] += 1
                        need.append({"field": f, "cands": cands})
                        items.append({"id": i, "title": cand[i]["title"], "field": f,
                                      "cands": cands})
        out_rows.append({"id": i, "title": cand[i]["title"], "tier": cand[i]["tier"],
                         "source_name": cand[i]["source_name"],
                         "basic_features": bf, "provenance": prov,
                         "resolution_notes": notes,
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
             "items": [{"f": it["field"], "c": it["cands"]}
                       for it in items if it["id"] == r["id"]]}
            for r in out_rows if r["needs_human"]]

    n_items = len(items)                                   # 只剩真正需要人读原文的
    n_three = sum(per_field[f]["same"] for f in FIELDS)
    n_auto = sum(per_field[f].get("auto", 0) for f in FIELDS)
    n_rule = sum(per_field[f].get("rule", 0) for f in FIELDS)
    stats = {"nAcc": len(ids), "nItems": n_items, "nThree": n_three, "nAuto": n_auto,
             "nRule": n_rule,
             "fields": FIELDS, "fieldCn": FIELD_CN,
             "enum": {f: enum_of(f) for f in ENUM},
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
         f"- **三方一致，直接确定 {n_three} 格**（`three_agree`）",
         f"- **三方中有两方相同，采用众数值自动确定 {n_auto} 格**（`majority_auto`）",
         f"- **三方互异但规则可判 {n_rule} 格**（`rule_resolved`）："
         f"`accident_time` 取三方中唯一合法 HH:MM 值，无合法值置空（库内有把\"下午\"填进时刻字段的）；"
         f"`city` 取规范地级名称（自治州/地区/盟优先，同级取信息最全），脱敏占位符（××县）与县级名置空。"
         f"每格的判定说明记在 `resolution_notes`。",
         f"- **三方互异且规则判不了，需人工裁决 {n_items} 格**（`needs_human`），涉及 {len(page)} / {len(ids)} 条事故",
         f"- 自动确定合计 {n_three + n_auto + n_rule} / {len(FIELDS)*len(ids)} 格"
         f"（{(n_three+n_auto+n_rule)/(len(FIELDS)*len(ids))*100:.1f}%）", "",
         "## 剔除的字段（不在本次 GT 范围）", "",
         "**名称类文本**：工程名称 `project_name`、建设单位 `developer`、施工单位 `contractor`、"
         "监理单位 `supervision_company` —— 这 4 个在 24 字段口径下贡献 151 条三方互异（占 39%）。",
         "**书写类**：摘要 `summary`、直接原因 `direct_cause`、天气 `weather`、"
         "具体部位 `accident_location_detail`、区县 `district`、标题 `title`、报告类型 `report_type`。",
         "**其他客观但零收益**：施工阶段/作业类型/伤亡工种/承包类型/涉及设备/涉及材料/建筑结构类型"
         "（保留会各增 11–70 条互异）。", "",
         "被剔除字段的模型输出仍保留在 `S1_golden_results.jsonl` / `pro/S1_golden_results.jsonl`，"
         "需要时可随时取用。字段取舍依据见 `scripts/golden_s1/inventory_fields.py` 的全字段清点。", "",
         "## 各字段情况", "",
         "| 字段 | 三方一致 | 两方一致(自动) | 规则判定 | 三方互异(人工) | 自动率 |",
         "|---|---:|---:|---:|---:|---:|"]
    for f in FIELDS:
        c = per_field[f]
        t = c["same"] + c["diff"]
        auto = c.get("auto", 0)
        rule = c.get("rule", 0)
        L.append(f"| {FIELD_CN[f]} `{f}` | {c['same']} | {auto} | {rule} | {c.get('human', 0)} "
                 f"| {(c['same']+auto+rule)/t*100:.1f}% |")
    L += ["", "## 用法", "",
          "打开 `annotation.html`：左侧是原始语料（带查找高亮），右侧是每个待定字段的三方候选值。",
          "点候选值即选中；三方都不对时在输入框自填（枚举字段为限定下拉）。",
          "顶部「导出 JSONL」下载标注结果；「导出完整 GT」把自动确定值与人工决策合并导出。",
          "进度存在浏览器本地，刷新不丢。", ""]
    (GT / "GT_REPORT.md").write_text("\n".join(L), encoding="utf-8")

    tot = len(FIELDS) * len(ids)
    print(f"三方一致自动确定 {n_three} / {tot}")
    print(f"两方一致自动确定 {n_auto} / {tot}")
    print(f"规则判定确定     {n_rule} / {tot}")
    print(f"仍需人工         {n_items} / {tot}，涉及 {len(page)} 条事故")
    print(f"产出 → {GT}/gt_draft.jsonl, annotation.html, GT_REPORT.md")


if __name__ == "__main__":
    main()
