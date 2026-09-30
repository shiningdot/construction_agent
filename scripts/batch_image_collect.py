#!/usr/bin/env python3
"""图片采集: 新闻事故 → UAPI搜索 → 取前3条URL → og:image提取 → 下载 → 入库"""
import sqlite3, json, os, time, hashlib, httpx, re, sys
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
CHECKPOINT = "/home/suntianhao/construction_agent/data/image_collect_checkpoint.json"
IMG_ROOT = Path("/home/suntianhao/construction_agent/data/images")
SEARCH_URL = "https://uapis.cn/api/v1/search/aggregate"
META_URL = "https://uapis.cn/api/v1/webparse/metadata"
UAPI_KEY = os.getenv("UAPI_KEY", "")
AUTH = {"Authorization": f"Bearer {UAPI_KEY}", "Content-Type": "application/json"}

# 断点
processed = set()
if os.path.exists(CHECKPOINT):
    with open(CHECKPOINT) as f:
        processed = set(json.load(f))
    print(f"[checkpoint] 已处理 {len(processed)} 条")

# 选取未采集图片的新闻事故(优先已搜索增强过的, 因为搜索增强记录了 enriched_text)
db = sqlite3.connect(DB)
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
candidates = db.execute("""
    SELECT id, title, raw_text FROM accidents
    WHERE source_name IN ('safehoo','esafety')
      AND search_performed = 1
      AND raw_text IS NOT NULL AND length(raw_text) > 100
      AND id NOT IN (SELECT DISTINCT accident_id FROM image_files)
    ORDER BY RANDOM()
""").fetchall()

tasks = [(r[0], r[1], r[2]) for r in candidates if r[0] not in processed]
print(f"[init] {len(tasks)} 条待处理")

def build_query(title, raw_text):
    """从事故文本构造搜索查询词"""
    # 只取标题+正文前200字, 去掉新闻元数据行
    body_lines = [l for l in raw_text[:500].split('\n')
                  if not any(l.startswith(p) for p in ('作者','来源','评论','更新','发布','http','本报','记者'))]
    text = title + " " + " ".join(body_lines[:5])

    # 提取2-6字的中文词组(太短无意义, 太长是完整句子)
    keywords = re.findall(r'[一-鿿]{2,6}', text)

    # 扩展停用词
    stop = {'事故','发生','造成','已经','目前','一个','进行','相关','该起','调查','报道','记者','施工','工作',
            '已致','截至','来源','新华社','评论','更新日期','记者从','了解到','现场','消息','本报'}
    keywords = [w for w in keywords if w not in stop]

    # 去重去噪, 取前6个最有信息量的
    seen = set(); unique = []
    for w in keywords:
        if w not in seen and not w.isdigit():
            seen.add(w); unique.append(w)

    query = " ".join(unique[:6])
    return query[:150] if query else title[:80]

def extract_images(page_url):
    """从网页提取图片URL (og:image优先, 回退html img)"""
    imgs = []
    # 策略1: UAPI metadata
    try:
        r = httpx.get(META_URL, headers=AUTH, params={"url": page_url}, timeout=15)
        if r.status_code == 200:
            og = (r.json().get("open_graph") or {})
            for k, v in og.items():
                if "image" in k.lower() and isinstance(v, str) and v.startswith("http"):
                    imgs.append(v)
    except: pass

    # 策略2: 直接HTML
    if not imgs:
        try:
            r = httpx.get(page_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            }, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                for meta in soup.find_all("meta"):
                    prop = (meta.get("property","") or meta.get("name","")).lower()
                    if "image" in prop:
                        c = meta.get("content","")
                        if c.startswith("http") and len(c) > 20:
                            imgs.append(c); break
        except: pass
    return imgs

def download(img_url, acc_id, page_url):
    try:
        r = httpx.get(img_url, headers={"User-Agent": "Mozilla/5.0", "Referer": page_url},
                     timeout=15, follow_redirects=True)
        if r.status_code != 200 or len(r.content) < 8192: return None
        ct = r.headers.get("content-type","")
        ext = ".jpg" if "jpeg" in ct or "jpg" in ct else (".png" if "png" in ct else ".webp")
        d = IMG_ROOT / acc_id; d.mkdir(parents=True, exist_ok=True)
        f = d / f"{hashlib.md5(img_url.encode()).hexdigest()[:12]}{ext}"
        f.write_bytes(r.content)
        return str(f.relative_to(IMG_ROOT.parent))
    except: return None

# 主循环
ok = imgs_total = 0
t0 = time.time()
for i, (acc_id, title, raw_text) in enumerate(tasks):
    now = datetime.now().isoformat()
    query = build_query(title, raw_text)

    try:
        # 搜索
        sr = httpx.post(SEARCH_URL, headers=AUTH, json={"query": query, "sort": "date"}, timeout=15)
        if sr.status_code != 200: continue

        results = sr.json().get("results", [])
        img_count = 0

        # 取前3条URL
        for r in results[:3]:
            page_url = r.get("url", "")
            if not page_url: continue

            og_imgs = extract_images(page_url)
            for img_url in og_imgs[:2]:  # 每页最多2张
                path = download(img_url, acc_id, page_url)
                if path:
                    db.execute("""
                        INSERT INTO image_files (accident_id, local_path, source_url, source_page_url, file_size, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (acc_id, path, img_url, page_url, 0, now))
                    img_count += 1
            time.sleep(0.3)

        if img_count > 0:
            db.execute("UPDATE accidents SET search_enriched_text = search_enriched_text WHERE id = ?", (acc_id,))

        imgs_total += img_count
        ok += 1
    except Exception as e:
        pass

    processed.add(acc_id)
    if (i+1) % 5 == 0:
        db.commit()
        elapsed = time.time() - t0
        rate = elapsed/(i+1)
        print(f"[{i+1:4d}/{len(tasks)}] {rate:.1f}s/条 | 图片:{imgs_total} | ETA:{rate*(len(tasks)-i-1)/60:.0f}min")

db.commit()
elapsed = time.time() - t0
with open(CHECKPOINT, "w") as f: json.dump(list(processed), f)

total_imgs = db.execute("SELECT COUNT(*) FROM image_files").fetchone()[0]
acc_with = db.execute("SELECT COUNT(DISTINCT accident_id) FROM image_files").fetchone()[0]
print(f"\n{'='*55}")
print(f"完成: {ok}/{len(tasks)} 条, {elapsed/60:.1f}min")
print(f"图片: {imgs_total} 张(本次), DB累计 {total_imgs} 张, 覆盖 {acc_with} 个事故")
db.close()
