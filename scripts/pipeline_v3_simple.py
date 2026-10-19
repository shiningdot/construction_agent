#!/usr/bin/env python3
"""V3简化版: Step1→搜索→图片采集→CLIP过滤→imageV3, 带日志"""
import sqlite3,json,os,time,hashlib,httpx,re,sys,logging
from datetime import datetime
from pathlib import Path
sys.path.insert(0,"/home/suntianhao/construction_agent")

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',
    handlers=[logging.FileHandler('/home/suntianhao/construction_agent/data/pipeline_v3.log'),logging.StreamHandler(sys.stdout)])
log=logging.getLogger()

os.environ.setdefault("HF_ENDPOINT","https://hf-mirror.com")

DB="/home/suntianhao/construction_agent/data/caiems_v2.db"
IMG_ROOT=Path("/home/suntianhao/construction_agent/data/imageV3")
CHECKPOINT="/home/suntianhao/construction_agent/data/v3_checkpoint.json"
SEARCH_URL="https://uapis.cn/api/v1/search/aggregate"
META_URL="https://uapis.cn/api/v1/webparse/metadata"
UAPI_KEY="uapi-todo3z1v5l75kdDIFV3iAZvBLllE34M1Wh0u46Po"
log.info(f"UAPI密钥: {UAPI_KEY[:15]}...")
def _uapi_key():
    return UAPI_KEY
LLM_KEY=os.getenv("ANTHROPIC_API_KEY","")
LLM_URL=os.getenv("ANTHROPIC_BASE_URL","")
log.info(f"UAPI_KEY={'OK' if UAPI_KEY else 'MISSING'} LLM_KEY={'OK' if LLM_KEY else 'MISSING'}")

# ---- 加载模型 ----
log.info("加载CLIP...")
from transformers import CLIPProcessor,CLIPModel
from PIL import Image
clip_model=CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
clip_proc=CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
CLIP_TEXTS=["施工现场事故现场照片","网站logo或二维码或图标"]
log.info("CLIP就绪")

# ---- Step1提取器 ----
from construction_agent.extraction.step1_basic_extractor import Step1BasicExtractor
from anthropic import Anthropic
extractor=Step1BasicExtractor.__new__(Step1BasicExtractor)
extractor.client=Anthropic(api_key=LLM_KEY,base_url=LLM_URL)
extractor._model="deepseek-v4-pro"
extractor._max_tokens=4096
log.info("Step1就绪")

# ---- 加载任务 ----
processed=set(json.load(open(CHECKPOINT))) if os.path.exists(CHECKPOINT) else set()
db=sqlite3.connect(DB)
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
candidates=db.execute("""SELECT id,title,raw_text FROM accidents
    WHERE source_name IN ('safehoo','esafety')
    AND raw_text IS NOT NULL AND length(raw_text)>200
    AND search_performed=1
    ORDER BY RANDOM()""").fetchall()
tasks=[(r[0],r[1],r[2]) for r in candidates if r[0] not in processed]
log.info(f"任务:{len(tasks)} 待处理")

def clip_ok(path):
    try:
        img=Image.open(path).convert("RGB")
        inputs=clip_proc(text=CLIP_TEXTS,images=img,return_tensors="pt",padding=True)
        return clip_model(**inputs).logits_per_image[0].softmax(dim=0)[0].item()>0.5
    except: return False

def extract_imgs(page_url):
    imgs=[]
    try:
        r=httpx.get(META_URL,headers={"Authorization":f"Bearer {_uapi_key()}"},params={"url":page_url},timeout=15)
        if r.status_code==200:
            og=(r.json().get("open_graph")or{})
            for k,v in og.items():
                if isinstance(v,str) and v.startswith("http") and any(x in v.lower() for x in ['.jpg','.jpeg','.png','.webp','img','photo','gtimg','sinaimg']):
                    imgs.append(v)
    except: pass
    if not imgs:
        try:
            r=httpx.get(page_url,headers={"User-Agent":"Mozilla/5.0"},timeout=10,follow_redirects=True)
            if r.status_code==200:
                from bs4 import BeautifulSoup
                soup=BeautifulSoup(r.text,"lxml")
                for meta in soup.find_all("meta"):
                    prop=(meta.get("property","")or meta.get("name","")).lower()
                    c=meta.get("content","")
                    if "image" in prop and c.startswith("http") and len(c)>30:
                        imgs.append(c)
        except: pass
    return imgs

# ---- 主循环 ----
ok=s1_ok=search_ok=img_ok=clip_ok_count=0
t0=time.time()
for i,(acc_id,title,raw_text) in enumerate(tasks):
    now=datetime.now().isoformat()
    try:
        s1=extractor.extract(raw_text,metadata={})
        if not s1: processed.add(acc_id); continue
        s1d=s1.to_dict(); s1_ok+=1
        city=(s1d.get('city')or'').replace('市','').replace('县','')
        queries=[q for q in [s1d.get('title','')[:60],f"{city} {s1d.get('accident_type','')}事故"] if len(q)>3][:2]
        all_urls=[]
        for q in queries:
            try:
                sr=httpx.post(SEARCH_URL,headers={"Authorization":f"Bearer {_uapi_key()}","Content-Type":"application/json"},json={"query":q,"sort":"date"},timeout=15)
                if sr.status_code==200:
                    search_ok+=1
                    for r2 in sr.json().get("results",[])[:3]:
                        all_urls.append(r2.get("url",""))
            except: pass
            time.sleep(0.3)
        count=0
        for page_url in all_urls[:5]:
            for img_url in extract_imgs(page_url)[:2]:
                try:
                    resp=httpx.get(img_url,headers={"User-Agent":"Mozilla/5.0","Referer":page_url},timeout=15,follow_redirects=True)
                    if resp.status_code!=200 or len(resp.content)<8192: continue
                    ct=resp.headers.get("content-type","")
                    ext=".jpg" if "jpeg" in ct or "jpg" in ct else (".png" if "png" in ct else ".webp")
                    d=IMG_ROOT/acc_id; d.mkdir(parents=True,exist_ok=True)
                    f=d/f"{hashlib.md5(img_url.encode()).hexdigest()[:12]}{ext}"
                    f.write_bytes(resp.content)
                    if clip_ok(str(f)):
                        count+=1; clip_ok_count+=1
                        db.execute("INSERT INTO image_files(accident_id,local_path,source_url,source_page_url,file_size,created_at) VALUES(?,?,?,?,?,?)",(acc_id,str(f.relative_to(IMG_ROOT.parent)),img_url,page_url,len(resp.content),now))
                    else:
                        f.unlink()
                except: pass
                time.sleep(0.3)
        img_ok+=count; ok+=1
    except Exception as e:
        log.error(f"[{i+1}] {acc_id[:10]} 异常: {e}")
    processed.add(acc_id)
    if (i+1)%10==0:
        db.commit()
        with open(CHECKPOINT,"w") as fp: json.dump(list(processed),fp)
        elapsed=time.time()-t0; rate=elapsed/(i+1)
        log.info(f"[{i+1}/{len(tasks)}] {rate:.1f}s/条 S1:{s1_ok} 搜索:{search_ok} 图:{img_ok}(CLIP:{clip_ok_count}) ETA:{rate*(len(tasks)-i-1)/60:.0f}min")

db.commit()
with open(CHECKPOINT,"w") as fp: json.dump(list(processed),fp)
log.info(f"完成: {ok}/{len(tasks)} S1:{s1_ok} 搜索:{search_ok} 图片:{img_ok}(CLIP通过:{clip_ok_count})")
db.close()
