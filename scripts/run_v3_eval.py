"""V3 200条评测脚本。"""
import json, time, sys, logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, ".")
from construction_agent.versions import get_pipeline

with open("data/v3_test_set.json", "r") as f:
    test_set = json.load(f)

p3 = get_pipeline("v3")
print(f"V3 ready: {p3.get_info()['description']}")

results = []
for i, item in enumerate(test_set):
    text = item.get("raw_text", "") or item.get("title", "")
    t0 = time.time()
    try:
        r = p3.process_single(text)
        elapsed = time.time() - t0
        if r.success and r.accident:
            a = r.accident
            cause_count = len(a.human_factors)+len(a.machine_factors)+len(a.management_factors)+len(a.environmental_factors)
            results.append({
                "title": item["title"],
                "time": round(elapsed, 1),
                "success": True,
                "accident": a.to_dict(),
                "cause_count": cause_count,
            })
            status = f"✅ {a.accident_type} 致因{cause_count}条 ({elapsed:.0f}s)"
        else:
            results.append({"title": item["title"], "success": False, "error": r.error_message})
            status = f"❌ {r.error_message}"
    except Exception as e:
        results.append({"title": item["title"], "success": False, "error": str(e)})
        status = f"❌ {e}"

    if (i+1) % 10 == 0 or i == 0 or i == len(test_set)-1:
        print(f"[{i+1}/{len(test_set)}] {status}")

# 保存
with open("data/v3_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

success = sum(1 for r in results if r.get("success"))
print(f"\n✅ V3 完成: {success}/{len(results)} 成功")
print(f"   结果保存: data/v3_results.json")
