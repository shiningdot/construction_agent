#!/usr/bin/env python3
import sys, os, sqlite3, time
from pathlib import Path

# 本仓库根（脚本在 scripts/ 下，上溯一级），worktree 与主仓库都成立。
# 原先两行 sys.path 分别指主仓库与 classifier-mod worktree，后者写死。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from construction_agent.extraction.vlm_analyzer import VLMAnalyzer

db = sqlite3.connect("/home/suntianhao/construction_agent/data/caiems_v2.db")
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
ids = ("55cee856a09e","28cc000bce2f","b7d2d036d638","58b66050e8b2","f78d268438ee")
rows = db.execute(
    "SELECT a.id,a.title,a.accident_type,a.severity,a.deaths,a.injuries,a.city,a.work_task,i.local_path "
    "FROM image_files i JOIN accidents a ON i.accident_id=a.id "
    "WHERE i.local_path LIKE 'imageV3%' AND a.id IN (?,?,?,?,?)", ids
).fetchall()
db.close()

analyzer = VLMAnalyzer()
for i, r in enumerate(rows):
    acc_id, title, at, sev, d, inj, city, wt, img_path = r
    fp = "/home/suntianhao/construction_agent/data/" + img_path
    ctx = {"accident_type":at,"severity":sev,"deaths":d,"injuries":inj,"city":city or "","work_task":wt or ""}

    print("=" * 60)
    print("[%d] %s (%s)" % (i+1, title, at))
    print("=" * 60)

    result = analyzer.analyze(fp, ctx)
    result.accident_id = acc_id
    if result.error:
        print("ERROR: " + result.error)
        if "402" in str(result.error) or "429" in str(result.error):
            print("STOP: 额度不足或限流")
            sys.exit(1)
    else:
        analyzer.save_to_db(result, "/home/suntianhao/construction_agent/data/caiems_v2.db")
        print("--- Phase1 ---")
        print((result.phase1_text or "")[:1500])
        print()
        print("--- Phase2 ---")
        print((result.phase2_text or "")[:1000])
        print()
        print("解析: 一致性=%s 置信度=%s" % (result.phase2_consistency, result.phase2_confidence))
        print("新信息: %s" % ((result.phase2_new_info or "")[:200]))
        print("已入库 image_analyses")
    print()
    time.sleep(0.5)
