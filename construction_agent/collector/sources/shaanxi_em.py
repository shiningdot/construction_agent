"""陕西省应急管理厅 (yjglt.hainan.gov.cn) 事故调查报告爬虫。

目标: https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/
- TRS CMS, 纯HTML, 单页25条
- URL: ./{YYYYMM}/t{YYYYMMDD}_{id}.html
- 详情: 标题 + 日期 + PDF附件
"""

from __future__ import annotations
import hashlib, logging, re, time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
from construction_agent.collector.classifier import classify_v2
from construction_agent.collector.stores import RawAccidentStore
from construction_agent.config import settings

logger = logging.getLogger(__name__)

SX_BASE = "https://yjt.shaanxi.gov.cn"
SX_LIST = "https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/index_1.html"
KW = ["建筑","建设","施工","坍塌","倒塌","工地","坠落","工程","项目","拆除","基坑","模板","脚手架"]

def _pdf_text(fp):
    try: import fitz; d=fitz.open(str(fp)); t="".join(p.get_text() for p in d); d.close(); return t if len(t)>50 else ""
    except: return ""

def _cand(title):
    if "挂牌督办" in title: return False, "挂牌督办"
    m=[k for k in KW if k in title]; return (True,f"KW({len(m)})") if m else (False,"无")

class ShaanxiEMCollector:
    def __init__(s, delay=1.0, enable_classify=True):
        s.d=delay; s.ec=enable_classify; s.dd=settings.DATA_DIR/"shaanxi_em"; s.dd.mkdir(parents=True,exist_ok=True)
        s.cl=httpx.Client(timeout=30,follow_redirects=True,headers={"User-Agent":"Mozilla/5.0"})
        s.rs=RawAccidentStore(); s.t=s.f=s.c=s.r=0
    @property
    def start_page(s): return 1
    @start_page.setter
    def start_page(s,v): pass
    @property
    def end_page(s): return 1
    @end_page.setter
    def end_page(s,v): pass
    def source_name(s): return "shaanxi_em"
    @property
    def collected_count(s): return s.c

    def collect(s):
        t0=time.time(); logger.info("开始采集陕西省应急管理厅...")
        h=s._f(SX_LIST)
        if not h: return s._st(0)
        recs=s._pl(h); s.t=len(recs); logger.info(f"列表:{s.t}条")
        batch=[]
        for i,r in enumerate(recs):
            title,url,date=r["title"],r["url"],r["date"]
            ok,reason=_cand(title)
            if not ok: s.f+=1; continue
            logger.info(f"  候选({i+1}): [{title[:60]}] - {reason}")
            time.sleep(s.d)
            d=s._cd(url); dt=(d or {}).get("title") or title
            dd=(d or {}).get("date") or date; dx=(d or {}).get("text") or ""
            pu=(d or {}).get("pdf_url") or ""; pt=""; lp=""
            if pu: pt,lp=s._dp(pu,title,dd)
            cx=pt or dx
            if s.ec and cx:
                rv=classify_v2(dt,cx)
                if rv["is_construction"] and rv["confidence"]=="high": cl,cr,cf,st="auto_construction",rv["reason"],"high","new"
                elif not rv["is_construction"] and rv["confidence"]=="high": s.f+=1; continue
                else: cl,cr,cf,st="pending_review",rv["reason"],"low","review"
            else: cl,cr,cf,st="pending_review","无正文","low","review"
            ps=[dt];
            if lp: ps.append(f"[本地:{lp}]")
            if pu: ps.append(f"PDF:{pu}")
            if cx: ps.append(f"\n正文:\n{cx[:3000]}")
            batch.append({"title":dt,"content":"\n".join(ps)[:50000],"source_url":url,"source_name":"shaanxi_em",
                "publish_date":dd,"status":st,"pdf_urls":[pu] if pu else [],
                "issuing_authority":"陕西省应急管理厅","classification":cl,"classification_reason":cr,"classification_confidence":cf})
            if st=="review": s.r+=1
            else: s.c+=1
            if len(batch)>=5: s.rs.add_batch(batch); batch=[]
        if batch: s.rs.add_batch(batch)
        s.rs._save(); return s._st(time.time()-t0)

    def _pl(s,html):
        soup=BeautifulSoup(html,"html.parser"); recs=[]
        for a in soup.find_all("a",href=True):
            if not re.search(r'/\d{6}/t\d{8}_\d+\.html',a['href']): continue
            t=a.get_text(strip=True)
            if len(t)<5: continue
            fu=urljoin(SX_LIST,a['href']) if not a['href'].startswith("http") else a['href']
            m=re.search(r'/(\d{4})(\d{2})/t(\d{8})_',fu)
            dt=f"{m.group(1)}-{m.group(2)}-{m.group(3)[6:8]}" if m else ""
            if t: recs.append({"title":t,"url":fu,"date":dt})
        return recs

    def _cd(s,url):
        h=s._f(url)
        if not h: return None
        soup=BeautifulSoup(h,"html.parser"); title=""; tt=soup.find("title")
        if tt: title=tt.get_text(strip=True).split("_")[0].strip()
        m=re.search(r'(\d{4}-\d{2}-\d{2})',h); date=m.group(1) if m else ""
        txt=soup.get_text() if soup.find("body") else ""; pu=""
        for a in soup.find_all("a",href=True):
            if ".pdf" in a['href'].lower(): pu=urljoin(url,a['href']); break
        return {"title":title,"date":date,"text":txt,"pdf_url":pu}

    def _dp(s,pu,title,date):
        hh=hashlib.md5(pu.encode()).hexdigest()[:12]; fn=re.sub(r'[\\/*?:"<>|]',"",title)[:50]
        lp=s.dd/f"{date}_{fn}_{hh}.pdf"
        if lp.exists(): return _pdf_text(lp),str(lp)
        try:
            r=s.cl.get(pu)
            if r.status_code==200 and len(r.content)>=100: lp.write_bytes(r.content); return _pdf_text(lp),str(lp)
        except: pass
        return "",""

    def _f(s,url):
        for _ in range(3):
            try: r=s.cl.get(url)
            except: time.sleep(2); continue
            if r.status_code==200: return r.text
            elif r.status_code==404: return None
            time.sleep(2)
        return None

    def _st(s,d): cs=s.rs.stats(); return {"total_records":s.t,"total_crawled":s.c,"pending_review":s.r,"filtered_out":s.f,"cached_total":cs["total"],"duration_seconds":round(d,1)}

    def __del__(s):
        try: s.cl.close()
        except: pass

def crawl_shaanxi_em(delay=1.0): return ShaanxiEMCollector(delay=delay).collect()
