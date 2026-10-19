#!/usr/bin/env python3
import sqlite3, httpx, os, time
db=sqlite3.connect("/home/suntianhao/construction_agent/data/caiems_v2.db")
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
r=db.execute("SELECT id, title, city, accident_type FROM accidents WHERE id='ae16d12d2c7f'").fetchone()
db.close()
key=os.getenv("UAPI_KEY")

tests=["齐齐哈尔体育馆坍塌","齐齐哈尔 体育馆 坍塌 2023","齐齐哈尔第三十四中学体育馆坍塌"]
for q in tests:
    print(f"查询: {q}")
    sr=httpx.post("https://uapis.cn/api/v1/search/aggregate",
        headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
        json={"query":q,"sort":"date"},timeout=15)
    if sr.status_code==200:
        data=sr.json()
        print(f"  ✅ {data.get('total_results',0)}条")
    else:
        print(f"  ❌ HTTP {sr.status_code}: {sr.text[:100]}")
    time.sleep(0.5)
