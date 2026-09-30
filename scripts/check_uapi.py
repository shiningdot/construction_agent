#!/usr/bin/env python3
"""检查 UAPI 免费额度"""
import httpx, os, sqlite3

key = os.getenv("UAPI_KEY", "")
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

print("检查 UAPI 搜索 API...")
try:
    resp = httpx.post("https://uapis.cn/api/v1/search/aggregate",
                      headers=headers, json={"query": "测试", "sort": "date"}, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        print(f"  ✅ 正常, 返回 {data.get('total_results',0)} 条 ({resp.elapsed.total_seconds():.1f}s)")
    elif resp.status_code == 429:
        print("  ❌ 速率限制(429) — 免费额度可能耗尽")
    elif resp.status_code == 401:
        print("  ❌ 认证失败(401) — API Key 无效")
    else:
        print(f"  ⚠ HTTP {resp.status_code}: {resp.text[:200]}")
except Exception as e:
    print(f"  ❌ 网络异常: {e}")

print("\n检查 UAPI Metadata API...")
try:
    resp = httpx.get("https://uapis.cn/api/v1/webparse/metadata",
                     headers=headers, params={"url": "https://www.baidu.com"}, timeout=10)
    if resp.status_code == 200:
        print("  ✅ 正常")
    elif resp.status_code == 429:
        print("  ❌ 速率限制(429)")
    else:
        print(f"  ⚠ HTTP {resp.status_code}")
except Exception as e:
    print(f"  ❌ 网络异常: {e}")

print("\nDB 状态:")
db = sqlite3.connect("/home/suntianhao/construction_agent/data/caiems_v2.db")
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
total = db.execute("SELECT COUNT(*) FROM accidents").fetchone()[0]
searched = db.execute("SELECT COUNT(*) FROM accidents WHERE search_performed=1").fetchone()[0]
news = db.execute("""
    SELECT COUNT(*) FROM accidents
    WHERE source_name IN ('safehoo','esafety') AND (search_performed=0 OR search_performed IS NULL)
""").fetchone()[0]
imgs = db.execute("SELECT COUNT(*) FROM image_files").fetchone()[0]
print(f"  总事故: {total}, 已增强: {searched}, 新闻待增强: {news}, 已有图片: {imgs}")
db.close()
