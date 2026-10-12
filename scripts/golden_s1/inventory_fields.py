"""全字段清点：31 个 schema 字段的三方一致情况 + 类型 + 剔除收益，供筛选用。

同时列出 accidents 表里**可由抓取元数据直接获得、完全不需要人工**的客观字段。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/suntianhao/construction_agent/.claude/worktrees/golden-standard-screen")
GOLD = ROOT / "experiments/golden_standard"
DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from count_threeway import (load_results, load_db, three_way, norm, DB_COL,  # noqa: E402
                            EMPTY, NUM, NUM_TOL)

TYPE = {}
for f in ["accident_type", "severity", "project_type", "building_structure",
          "construction_phase", "work_task", "contractor_type", "collapse_subtype",
          "equipment_involved", "material_involved", "worker_type", "report_type"]:
    TYPE[f] = "枚举"
for f in ["deaths", "injuries", "trapped_count", "direct_economic_loss", "fall_height"]:
    TYPE[f] = "数值"
for f in ["date", "accident_time"]:
    TYPE[f] = "日期"
for f in ["title", "location", "city", "district", "accident_location_detail", "weather",
          "project_name", "developer", "contractor", "supervision_company",
          "summary", "direct_cause"]:
    TYPE[f] = "文本"

CN = {
    "accident_type": "事故类型", "severity": "严重程度", "project_type": "工程类型",
    "building_structure": "建筑结构类型", "construction_phase": "施工阶段",
    "work_task": "作业类型", "contractor_type": "承包类型", "collapse_subtype": "坍塌子类",
    "equipment_involved": "涉及设备", "material_involved": "涉及材料", "worker_type": "伤亡人员工种",
    "report_type": "报告类型", "deaths": "死亡人数", "injuries": "受伤人数",
    "trapped_count": "被困人数", "direct_economic_loss": "直接经济损失", "fall_height": "坠落高度",
    "date": "发生日期", "accident_time": "发生时刻", "title": "标题", "location": "省",
    "city": "市", "district": "区县", "accident_location_detail": "具体部位", "weather": "天气",
    "project_name": "工程名称", "developer": "建设单位", "contractor": "施工单位",
    "supervision_company": "监理单位", "summary": "摘要", "direct_cause": "直接原因",
}
# 是否已在上一轮被剔除
ALREADY_OUT = {"summary", "direct_cause", "weather", "accident_location_detail",
               "district", "title", "report_type"}

# 抓取元数据直接可得、无需模型抽取也无需人工的客观字段
DB_META = [
    ("date", "事故日期（库内 date 列，与模型抽取互为校验）"),
    ("crawl_date", "抓取日期（爬虫写入）"),
    ("publish_date", "原文发布日期（爬虫写入）"),
    ("source_name", "来源站点/书目（爬虫写入）"),
    ("source_url", "原文链接（爬虫写入）"),
    ("source_count", "同一事故的来源数（去重统计）"),
    ("text_type", "文本类型 single/multi（分类器输出）"),
    ("province", "省（库内已抽，作三方校验）"),
]


def main():
    flash = load_results(GOLD / "S1_golden_results.jsonl")
    pro = load_results(GOLD / "pro/S1_golden_results.jsonl")
    cand = {json.loads(l)["id"]: json.loads(l)
            for l in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8")}
    ids = list(cand)
    db = load_db(ids)          # 只覆盖 24 个字段

    # 为被剔除的 7 个字段单独取库值
    extra = defaultdict(dict)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ",".join("?" * len(ids))
    for r in con.execute(f"select id, title, district, accident_location_detail, weather, "
                         f"summary, direct_cause, report_type, province from accidents "
                         f"where id in ({q})", ids):
        extra[r["id"]] = dict(r)
    con.close()

    stat = {}
    for f in TYPE:
        same = diff = pre = div = 0
        for i in ids:
            a, b = flash[i].get(f), pro[i].get(f)
            c = db[i].get(f) if f not in ALREADY_OUT else extra[i].get(
                "province" if f == "location" else f)
            is_same, _v, cands = three_way(f, a, b, c)
            if is_same:
                same += 1
            else:
                diff += 1
                vals = [x for x in cands if norm(x) is not None]
                from collections import Counter
                if vals and Counter(norm(x) for x in vals).most_common(1)[0][1] >= 2:
                    pre += 1
                else:
                    div += 1
        stat[f] = dict(same=same, diff=diff, pre=pre, div=div, type=TYPE[f])

    tot = len(ids)
    print("=" * 100)
    print("31 个 schema 字段全清点（三方 = flash / pro / 库内 Step1；本表未做任何剔除）")
    print("=" * 100)
    print(f"{'字段':26s}{'类型':5s}{'三方一致':>8s}{'待人工':>7s}{'可预填':>7s}{'三方互异':>8s}"
          f"{'状态':>8s}")
    for f in sorted(TYPE, key=lambda x: (-stat[x]["div"], -stat[x]["diff"])):
        s = stat[f]
        tag = "已剔除" if f in ALREADY_OUT else ""
        print(f"{CN[f]+' '+f:26s}{s['type']:5s}{s['same']:8d}{s['diff']:7d}{s['pre']:7d}"
              f"{s['div']:8d}{tag:>8s}")

    print("\n" + "=" * 100)
    print("剔除收益排序（按「三方互异」——这是真正非人判不可的量）")
    print("=" * 100)
    print(f"{'字段':26s}{'类型':5s}{'待人工':>7s}{'三方互异':>8s}{'剔除后累计互异':>14s}")
    run = sum(s["div"] for s in stat.values())
    print(f"{'（当前全部 31 字段）':26s}{'':5s}{sum(s['diff'] for s in stat.values()):7d}{run:8d}")
    for f in sorted(TYPE, key=lambda x: -stat[x]["div"]):
        run -= stat[f]["div"]
        print(f"  剔除 {CN[f]+' '+f:20s}{stat[f]['type']:5s}{stat[f]['diff']:7d}"
              f"{stat[f]['div']:8d}{run:14d}")

    print("\n" + "=" * 100)
    print("可完全跳过人工的字段：抓取元数据直接可得（无需模型抽取）")
    print("=" * 100)
    for k, desc in DB_META:
        print(f"  {k:18s} {desc}")

    print(f"\n合计：三方一致 {sum(s['same'] for s in stat.values())} | "
          f"待人工 {sum(s['diff'] for s in stat.values())} | "
          f"其中可预填 {sum(s['pre'] for s in stat.values())} | "
          f"三方互异 {sum(s['div'] for s in stat.values())}")


if __name__ == "__main__":
    main()
