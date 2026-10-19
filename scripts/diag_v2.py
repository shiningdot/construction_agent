#!/usr/bin/env python3
"""诊断V2管线: 单条记录逐步执行, 看哪里卡住"""
import sqlite3, httpx, os, time, json, sys, re
sys.path.insert(0, "/home/suntianhao/construction_agent")
from construction_agent.extraction.step1_basic_extractor import Step1BasicExtractor
from anthropic import Anthropic

key_uapi = os.getenv("UAPI_KEY", "")
key_llm = os.getenv("ANTHROPIC_API_KEY", "")

db = sqlite3.connect("/home/suntianhao/construction_agent/data/caiems_v2.db")
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
r = db.execute("SELECT id, title, raw_text FROM accidents WHERE source_name IN ('safehoo','esafety') AND raw_text IS NOT NULL AND length(raw_text) > 300 ORDER BY RANDOM() LIMIT 1").fetchone()
db.close()
acc_id, title, raw_text = r

print("=" * 55)
print(f"事故: {title[:60]}")
print("=" * 55)

# Step 1: 提取
print("\n[1] Step1 提取...")
t0 = time.time()
try:
    extractor = Step1BasicExtractor.__new__(Step1BasicExtractor)
    extractor.client = Anthropic(api_key=key_llm, base_url=os.getenv("ANTHROPIC_BASE_URL"))
    extractor._model = "deepseek-v4-pro"
    extractor._max_tokens = 4096
    s1 = extractor.extract(raw_text, metadata={})
    if not s1:
        print("  ❌ Step1 返回 None")
        sys.exit(1)
    s1d = s1.to_dict()
    print(f"  ✅ {time.time()-t0:.1f}s")
    print(f"  city={s1d.get('city')}, accident_type={s1d.get('accident_type')}, deaths={s1d.get('deaths')}")
    print(f"  title={s1d.get('title','')[:50]}")
except Exception as e:
    print(f"  ❌ Step1 异常: {e}")
    sys.exit(1)

# Step 2: 构造查询词
print("\n[2] 构造查询词...")
city = (s1d.get('city') or '').replace('市','').replace('县','')
acc_type = s1d.get('accident_type', '')
title_clean = s1d.get('title','')[:60]
queries = []
# 主查询: 标题本身
if title_clean: queries.append(title_clean)
# 辅查询: 城市 + 事故类型
if city and acc_type:
    q = f"{city} {acc_type}事故"
    if q != queries[0]: queries.append(q)
queries = queries[:2]
if not queries: queries.append(title[:80])
print(f"  queries={queries}")

# Step 3: 搜索
print("\n[3] UAPI 搜索...")
auth = {"Authorization": f"Bearer {key_uapi}", "Content-Type": "application/json"}
all_urls = []
for q in queries:
    try:
        sr = httpx.post("https://uapis.cn/api/v1/search/aggregate",
                        headers=auth, json={"query": q, "sort": "date"}, timeout=15)
        if sr.status_code == 200:
            data = sr.json()
            n = data.get("total_results", 0)
            print(f"  查询'{q[:40]}': {n}条")
            for r2 in data.get("results", [])[:3]:
                u = r2.get("url", "")
                all_urls.append(u)
                print(f"    {r2.get('title','')[:50]}")
        else:
            print(f"  查询'{q[:40]}': HTTP {sr.status_code}")
    except Exception as e:
        print(f"  查询'{q[:40]}': 异常 {e}")
    time.sleep(0.3)

# Step 4: 图片
print(f"\n[4] 图片采集 ({len(all_urls)} 个URL)...")
imgs = 0
for page_url in all_urls[:5]:
    try:
        mr = httpx.get("https://uapis.cn/api/v1/webparse/metadata",
                       headers=auth, params={"url": page_url}, timeout=15)
        if mr.status_code == 200:
            og = (mr.json().get("open_graph") or {})
            og_imgs = []
            for k, v in og.items():
                if isinstance(v, str) and v.startswith("http") and any(
                    x in v.lower() for x in ['.jpg','.jpeg','.png','.webp','img','photo','gtimg','sinaimg']):
                    og_imgs.append(v)
            if og_imgs:
                print(f"  ✅ og:image ({len(og_imgs)}张): {og_imgs[0][:80]}")
                imgs += len(og_imgs)
            else:
                print(f"  ⚠ 无og:image: {page_url[:60]}")
        else:
            print(f"  ❌ metadata HTTP {mr.status_code}: {page_url[:60]}")
    except Exception as e:
        print(f"  ❌ metadata异常: {e} ({page_url[:60]})")
    time.sleep(0.3)

print(f"\n{'='*55}")
print(f"结果: Step1=✅, 搜索={len(all_urls)}URLs, 图片={imgs}张")
