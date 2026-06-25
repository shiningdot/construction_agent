"""V4 200条评测脚本。"""
import json, time, sys, logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, ".")
from construction_agent.versions import get_pipeline

with open("data/v3_test_set.json", "r") as f:
    test_set = json.load(f)

p4 = get_pipeline("v4")
print(f"V4 ready: {p4.get_info()['description']}")

results = []
for i, item in enumerate(test_set):
    text = item.get("raw_text", "") or item.get("title", "")
    t0 = time.time()
    try:
        r = p4.process_single(text)
        elapsed = time.time() - t0
        if r.success and r.accident:
            a = r.accident
            results.append({
                "title": item["title"],
                "time": round(elapsed, 1),
                "success": True,
                "accident": a.to_dict(),
            })
            status = f"✅ {a.accident_type} ({elapsed:.0f}s)"
        else:
            results.append({"title": item["title"], "success": False, "error": r.error_message})
            status = f"❌ {r.error_message}"
    except Exception as e:
        results.append({"title": item["title"], "success": False, "error": str(e)})
        status = f"❌ {e}"

    if (i+1) % 20 == 0 or i == 0:
        print(f"[{i+1}/{len(test_set)}] {status}")

with open("data/v4_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

success = sum(1 for r in results if r.get("success"))
print(f"\n✅ V4 完成: {success}/{len(results)} 成功")
print(f"   结果保存: data/v4_results.json")
