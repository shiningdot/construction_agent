#!/usr/bin/env python3
"""批量搜索增强+图片采集: 100条新闻事故, 每5条保存一次"""
import sqlite3, json, os, time, hashlib, httpx, re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/home/suntianhao/construction_agent")
from construction_agent.extraction.search_enhancer import SearchEnhancer

DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
CHECKPOINT = "/home/suntianhao/construction_agent/data/search_image_checkpoint.json"
IMG_ROOT = Path("/home/suntianhao/construction_agent/data/images")

UAPI_KEY = os.getenv("UAPI_KEY", "")
META_URL = "https://uapis.cn/api/v1/webparse/metadata"
META_HEADERS = {"Authorization": f"Bearer {UAPI_KEY}"}

# 加载断点
processed = set()
if os.path.exists(CHECKPOINT):
    with open(CHECKPOINT) as f:
        processed = set(json.load(f))
    print(f"[checkpoint] 已处理 {len(processed)} 条, 续传中...")

# 选取100条未增强的新闻事故
db = sqlite3.connect(DB)
candidates = db.execute("""
    SELECT id, title, raw_text, source_url FROM accidents
    WHERE source_name IN ('safehoo','esafety')
      AND (search_performed = 0 OR search_performed IS NULL)
      AND raw_text IS NOT NULL AND length(raw_text) > 100
    ORDER BY RANDOM()
""").fetchall()

tasks = [(r[0], r[1], r[2], r[3]) for r in candidates if r[0] not in processed]
print(f"[init] {len(tasks)} 条待处理, {len(processed)} 条已跳过")

enhancer = SearchEnhancer()

# 工具函数
def extract_og_images(page_url: str) -> list[dict]:
    """从网页 URL 提取 og:image"""
    images = []
    # 策略1: UAPI metadata
    try:
        resp = httpx.get(META_URL, headers=META_HEADERS, params={"url": page_url}, timeout=15)
        if resp.status_code == 200:
            og = resp.json().get("open_graph", {}) or {}
            for k, v in og.items():
                if "image" in k.lower() and isinstance(v, str) and v.startswith("http"):
                    images.append({"url": v, "source": f"og:{k}", "page_url": page_url})
    except:
        pass
    # 策略2: 直接HTML解析
    if not images:
        try:
            html_resp = httpx.get(page_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            }, timeout=10, follow_redirects=True)
            if html_resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_resp.text, "lxml")
                for meta in soup.find_all("meta"):
                    prop = (meta.get("property", "") or meta.get("name", "")).lower()
                    if "image" in prop:
                        content = meta.get("content", "")
                        if content.startswith("http") and len(content) > 20:
                            images.append({"url": content, "source": f"meta:{prop}", "page_url": page_url})
                            break
        except:
            pass
    return images

def download_image(img_url: str, accident_id: str, page_url: str) -> dict | None:
    """下载图片到 data/images/{accident_id}/"""
    try:
        resp = httpx.get(img_url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": page_url
        }, timeout=15, follow_redirects=True)
        if resp.status_code != 200 or len(resp.content) < 8192:
            return None

        ct = resp.headers.get("content-type", "")
        ext = ".jpg" if "jpeg" in ct or "jpg" in ct else (".png" if "png" in ct else ".webp")

        img_dir = IMG_ROOT / accident_id
        img_dir.mkdir(parents=True, exist_ok=True)

        url_hash = hashlib.md5(img_url.encode()).hexdigest()[:12]
        fname = img_dir / f"{url_hash}{ext}"
        fname.write_bytes(resp.content)

        return {
            "local_path": str(fname.relative_to(IMG_ROOT.parent)),
            "source_url": img_url,
            "source_page_url": page_url,
            "file_size": len(resp.content),
        }
    except:
        return None

# 主循环
ok = 0
search_success = 0
img_total = 0
t0 = time.time()

for i, (acc_id, title, raw_text, source_url) in enumerate(tasks):
    step_tag = ""

    # Step 1: 搜索增强
    try:
        sr = enhancer.enhance(raw_text)
        search_performed = sr.get("search_performed", False)
        if search_performed:
            search_success += 1
            same_urls = sr.get("same_accident_urls", [])
            enriched = sr.get("enriched_text", raw_text)

            # 写入 search_logs
            now = datetime.now().isoformat()
            db.execute("""
                INSERT OR REPLACE INTO search_logs
                (accident_id, search_performed, queries_used, total_results,
                 same_accident_count, same_accident_snippets, engine, created_at)
                VALUES (?, 1, ?, ?, ?, ?, 'uapi', ?)
            """, (acc_id, json.dumps(sr.get("queries_used",[]), ensure_ascii=False),
                  sr.get("total_results",0), sr.get("same_accident_count",0),
                  json.dumps(sr.get("same_accident_snippets",[]), ensure_ascii=False), now))

            # Step 2: 图片采集
            img_urls_to_try = [u["url"] for u in same_urls[:8]]  # 最多8个来源URL
            for page_url in img_urls_to_try:
                og_imgs = extract_og_images(page_url)
                for oi in og_imgs[:3]:  # 每页最多3张
                    result = download_image(oi["url"], acc_id, page_url)
                    if result:
                        db.execute("""
                            INSERT INTO image_files (accident_id, local_path, source_url, source_page_url, file_size, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (acc_id, result["local_path"], result["source_url"],
                              result["source_page_url"], result["file_size"], now))
                        img_total += 1
                    time.sleep(0.3)  # 避免请求过快
                time.sleep(0.5)
        else:
            enriched = raw_text

        # 更新 accidents 表
        db.execute("""
            UPDATE accidents SET search_enriched_text = ?, search_performed = ? WHERE id = ?
        """, (enriched if search_performed else None, 1 if search_performed else 0, acc_id))

        ok += 1
        step_tag = f"搜索{'✅' if search_performed else '⊘'}"

    except Exception as e:
        step_tag = f"❌ {str(e)[:40]}"

    processed.add(acc_id)

    # 每5条保存一次
    if (i + 1) % 5 == 0:
        db.commit()
        elapsed = time.time() - t0
        rate = elapsed / (i + 1)
        eta = rate * (len(tasks) - i - 1)
        print(f"[{i+1:3d}/{len(tasks)}] {rate:.1f}s/条 | 搜索成功:{search_success} | 图片:{img_total} | ETA:{eta/60:.0f}min")

# 最终保存
db.commit()
elapsed = time.time() - t0

# 更新检查点
with open(CHECKPOINT, "w") as f:
    json.dump(list(processed), f)

# 最终统计
total_searched = db.execute("SELECT COUNT(*) FROM accidents WHERE search_performed=1").fetchone()[0]
total_imgs = db.execute("SELECT COUNT(*) FROM image_files").fetchone()[0]
acc_with_imgs = db.execute("SELECT COUNT(DISTINCT accident_id) FROM image_files").fetchone()[0]

print(f"\n{'='*55}")
print(f"完成: {ok}/{len(tasks)} 条, {elapsed/60:.1f}min ({elapsed/max(ok,1):.1f}s/条)")
print(f"搜索增强成功: {search_success} 条")
print(f"图片下载: {img_total} 张, 覆盖 {acc_with_imgs} 个事故")
print(f"DB 累计: {total_searched} 条已增强, {total_imgs} 张图片")
print(f"断点: {CHECKPOINT}")
db.close()
