#!/usr/bin/env python3
"""批量VLM分析: 全部226张V3图片 → Phase1+Phase2 → 入库 image_analyses"""
import sys, os, sqlite3, json, time
sys.path.insert(0, "/home/suntianhao/construction_agent")
sys.path.insert(0, "/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod")
from construction_agent.extraction.vlm_analyzer import VLMAnalyzer

DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
CHECKPOINT = "/home/suntianhao/construction_agent/data/vlm_checkpoint.json"

processed = set(json.load(open(CHECKPOINT))) if os.path.exists(CHECKPOINT) else set()
db = sqlite3.connect(DB)

rows = db.execute("""
    SELECT a.id, a.title, a.accident_type, a.severity, a.deaths, a.injuries, a.city, a.work_task,
           i.local_path
    FROM image_files i JOIN accidents a ON i.accident_id = a.id
    WHERE i.local_path LIKE 'imageV3%'
    ORDER BY a.id
""").fetchall()
db.close()

tasks = [(r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8]) for r in rows if r[8] not in processed]  # 按图片路径去重
print("任务: %d 张图片" % len(tasks))

analyzer = VLMAnalyzer()
ok = fail = 0
t0 = time.time()

for i, (acc_id, title, at, sev, d, inj, city, wt, img_path) in enumerate(tasks):
    fp = "/home/suntianhao/construction_agent/data/" + img_path
    ctx = {"accident_type": at, "severity": sev, "deaths": d, "injuries": inj, "city": city or "", "work_task": wt or ""}

    try:
        result = analyzer.analyze(fp, ctx)
        result.accident_id = acc_id
        if result.error:
            fail += 1
            print("[%d/%d] %s ERROR: %s" % (i+1, len(tasks), acc_id[:10], result.error), flush=True)
            if "402" in str(result.error) or "429" in str(result.error):
                print("\nSTOP: 额度不足或限流, 请更换API Key", flush=True)
                break
        else:
            analyzer.save_to_db(result, DB)
            ok += 1
            p1scene = (result.phase1_scene_type or "")[:30]
            p2desc = (result.phase2_new_info or "")[:50]
            print("[%d/%d] %s %s | P1=%s | P2=%s | %dms" % (
                i+1, len(tasks), acc_id[:10], title[:25], p1scene, p2desc, int(result.duration_ms)), flush=True)
    except Exception as e:
        fail += 1
        print("[%d/%d] %s EXCEPTION: %s" % (i+1, len(tasks), acc_id[:10], str(e)[:80]), flush=True)

    processed.add(img_path)
    time.sleep(1)  # 付费账户QPS较高
    if (i+1) % 5 == 0:
        with open(CHECKPOINT, "w") as f: json.dump(list(processed), f)
        elapsed = time.time() - t0
        rate = elapsed / (i+1)
        print("  [checkpoint: %d/%d, %.1fs/张, ETA %.0fmin]" % (i+1, len(tasks), rate, rate*(len(tasks)-i-1)/60), flush=True)

with open(CHECKPOINT, "w") as f: json.dump(list(processed), f)
elapsed = time.time() - t0
print("\n完成: %d成功 %d失败 | 耗时 %.0fmin" % (ok, fail, elapsed/60), flush=True)
