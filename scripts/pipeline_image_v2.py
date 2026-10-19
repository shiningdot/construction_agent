#!/usr/bin/env python3
"""图片采集 V3: Step1提取 → 搜索增强 → 图片采集 → CLIP过滤(>0.5) → imageV3/"""
import sqlite3, json, os, time, hashlib, httpx, re, sys
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.insert(0, "/home/suntianhao/construction_agent")
from construction_agent.extraction.step1_basic_extractor import Step1BasicExtractor
from anthropic import Anthropic

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

DB = "/home/suntianhao/construction_agent/data/caiems_v2.db"
CHECKPOINT = "/home/suntianhao/construction_agent/data/clip_v3_checkpoint.json"
IMG_ROOT = Path("/home/suntianhao/construction_agent/data/imageV3")
SEARCH_URL = "https://uapis.cn/api/v1/search/aggregate"
META_URL = "https://uapis.cn/api/v1/webparse/metadata"
UAPI_KEY = os.getenv("UAPI_KEY", "")
AUTH = {"Authorization": f"Bearer {UAPI_KEY}", "Content-Type": "application/json"}

# CLIP 全局加载一次
from transformers import CLIPProcessor, CLIPModel
from PIL import Image
_clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
_clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
CLIP_TEXTS = ["施工现场事故现场照片", "网站logo或二维码或图标"]
print("[CLIP] 模型就绪")
# 验证 API 密钥
if not UAPI_KEY:
    print("[FATAL] UAPI_KEY 未设置, 退出"); sys.exit(1)
if not os.getenv("ANTHROPIC_API_KEY"):
    print("[FATAL] ANTHROPIC_API_KEY 未设置, 退出"); sys.exit(1)
print(f"[init] UAPI_KEY={UAPI_KEY[:12]}... ANTHROPIC_KEY={os.getenv('ANTHROPIC_API_KEY')[:12]}...")

processed = set()
if os.path.exists(CHECKPOINT):
    with open(CHECKPOINT) as f: processed = set(json.load(f))
    print(f"[checkpoint] 已处理 {len(processed)} 条")

# 选200条未采集的有效新闻
db = sqlite3.connect(DB)
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
candidates = db.execute("""
    SELECT id, title, raw_text FROM accidents
    WHERE source_name IN ('safehoo','esafety')
      AND raw_text IS NOT NULL AND length(raw_text) > 200
      AND accident_type IS NOT NULL AND accident_type != ''
    ORDER BY RANDOM() LIMIT 310
""").fetchall()
tasks = [(r[0], r[1], r[2]) for r in candidates if r[0] not in processed][:200]
print(f"[init] {len(tasks)} 条 (测试模式)")

# Step1 提取器
extractor = Step1BasicExtractor.__new__(Step1BasicExtractor)
extractor.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                             base_url=os.getenv("ANTHROPIC_BASE_URL"))
extractor._model = "deepseek-v4-pro"
extractor._max_tokens = 4096

def extract_images_from_url(page_url):
    """从网页提取图片URL: og:image → meta → <img>标签"""
    imgs = []
    # 1. UAPI metadata
    try:
        r = httpx.get(META_URL, headers=AUTH, params={"url": page_url}, timeout=15)
        if r.status_code == 200:
            og = (r.json().get("open_graph") or {})
            for k, v in og.items():
                if isinstance(v, str) and v.startswith("http") and any(
                    x in v.lower() for x in ['.jpg','.jpeg','.png','.webp','img','photo','gtimg','sinaimg']):
                    imgs.append(v)
    except: pass

    # 2. 直接HTML解析 (meta + <img>)
    try:
        r = httpx.get(page_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"},
                     timeout=10, follow_redirects=True)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "lxml")
            # 2a. meta og:image / twitter:image
            for meta in soup.find_all("meta"):
                prop = (meta.get("property","") or meta.get("name","")).lower()
                c = meta.get("content","")
                if "image" in prop and c.startswith("http") and len(c) > 30:
                    imgs.append(c)
            # 2b. <img> 标签 (过滤logo/icon等)
            if not imgs:
                skip = ['logo','icon','avatar','qrcode','emoji','wx_fmt','favicon','banner','btn','arrow','广告']
                for img in soup.find_all("img")[:20]:
                    src = img.get("src","") or img.get("data-src","") or img.get("data-original","")
                    if src.startswith("http") and len(src) > 30:
                        if not any(k in src.lower() for k in skip):
                            imgs.append(src)
    except: pass

    return imgs

def clip_is_accident(img_path):
    """CLIP判断: 是否事故现场照片 (>0.5 = 是)"""
    try:
        img = Image.open(img_path).convert("RGB")
        inputs = _clip_processor(text=CLIP_TEXTS, images=img, return_tensors="pt", padding=True)
        score = _clip_model(**inputs).logits_per_image[0].softmax(dim=0)[0].item()
        return score > 0.5
    except:
        return False  # 无法判断 → 不保留

def download(img_url, acc_id, page_url):
    """下载 → CLIP过滤 → 事故照片保留, 非事故删除"""
    try:
        r = httpx.get(img_url, headers={"User-Agent": "Mozilla/5.0", "Referer": page_url},
                     timeout=15, follow_redirects=True)
        if r.status_code != 200 or len(r.content) < 8192: return None
        ct = r.headers.get("content-type","")
        ext = ".jpg" if "jpeg" in ct or "jpg" in ct else (".png" if "png" in ct else ".webp")
        d = IMG_ROOT / acc_id; d.mkdir(parents=True, exist_ok=True)
        f = d / f"{hashlib.md5(img_url.encode()).hexdigest()[:12]}{ext}"
        f.write_bytes(r.content)
        # CLIP 过滤
        if not clip_is_accident(str(f)):
            f.unlink()  # 删除非事故图片
            return None
        return str(f.relative_to(IMG_ROOT.parent))
    except: return None

ok = step1_ok = search_ok = imgs_total = 0
t0 = time.time()

for i, (acc_id, title, raw_text) in enumerate(tasks):
    now = datetime.now().isoformat()
    img_count = 0

    try:
        # Step 1: 提取基础特征
        s1 = extractor.extract(raw_text, metadata={})
        if not s1:
            processed.add(acc_id); continue
        s1d = s1.to_dict()
        step1_ok += 1

        # Step 2: 构造查询词 (标题为主, 城市+事故类型为辅)
        city = (s1d.get('city') or '').replace('市','').replace('县','')
        acc_type = s1d.get('accident_type', '')
        title_clean = s1d.get('title', '')[:60]
        queries = []
        # 主查询: 标题本身 (信息量最大)
        if title_clean:
            queries.append(title_clean)
        # 辅查询: 城市 + 事故类型
        if city and acc_type:
            q = f"{city} {acc_type}事故"
            if q != queries[0] if queries else '':
                queries.append(q)
        queries = [q for q in queries if len(q) > 3][:2]
        if not queries:
            queries.append(title[:80])

        # Step 3: 搜索
        all_urls = []
        for q in queries:
            try:
                sr = httpx.post(SEARCH_URL, headers=AUTH, json={"query": q, "sort": "date"}, timeout=15)
                if sr.status_code == 200:
                    for r in sr.json().get("results", [])[:3]:
                        all_urls.append(r.get("url", ""))
                    search_ok += 1
            except: pass
            time.sleep(0.3)

        # Step 4: 图片采集
        for page_url in all_urls[:5]:
            og_imgs = extract_images_from_url(page_url)
            for img_url in og_imgs[:2]:
                path = download(img_url, acc_id, page_url)
                if path:
                    db.execute("INSERT INTO image_files (accident_id, local_path, source_url, source_page_url, file_size, created_at) VALUES (?,?,?,?,?,?)",
                               (acc_id, path, img_url, page_url, 0, now))
                    img_count += 1
            time.sleep(0.3)

        imgs_total += img_count
        ok += 1
    except Exception as e:
        print(f"  [{i+1}] 异常: {str(e)[:80]}", flush=True)

    processed.add(acc_id)
    if (i+1) % 10 == 0 or (i+1) == len(tasks):
        db.commit()
        elapsed = time.time() - t0
        rate = elapsed/(i+1) if (i+1) > 0 else 0
        with open(CHECKPOINT, "w") as f: json.dump(list(processed), f)
        print(f"[{i+1:3d}/{len(tasks)}] {rate:.1f}s/条 | S1:{step1_ok} | 搜索:{search_ok} | 图:{imgs_total} | ETA:{rate*(len(tasks)-i-1)/60:.0f}min", flush=True)

db.commit()
with open(CHECKPOINT, "w") as f: json.dump(list(processed), f)
total_imgs = db.execute("SELECT COUNT(*) FROM image_files").fetchone()[0]
v2_imgs = len(list(IMG_ROOT.rglob("*.*"))) if IMG_ROOT.exists() else 0
print(f"\n完成: {ok}/{len(tasks)} | Step1:{step1_ok} | 搜索:{search_ok} | 图片:{imgs_total}(V2文件:{v2_imgs})")
db.close()
