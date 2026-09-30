#!/usr/bin/env python3
"""批量处理管线 v2: 跳过预分类，直接并发提取。"""
import json, sqlite3, sys, time, hashlib, os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/suntianhao/construction_agent")
from construction_agent.extraction.step1_basic_extractor import Step1BasicExtractor
from construction_agent.extraction.step2_caics_encoder import Step2CaicsEncoder
from construction_agent.extraction.search_enhancer import SearchEnhancer
# 证据等级与排序号原先在本文件内联计算，现已上收为共享出口
# （construction_agent/extraction/evidence_quality.py）。口径与主库 16,323 条
# 逐条核对过，行为不变——不要再改回内联，那正是复制品分叉的起点。
from construction_agent.extraction.evidence_quality import (
    compute_evidence_quality,
    compute_sort_order,
)

DB = sys.argv[1] if len(sys.argv) > 1 else "data/caiems_v2.db"
RAW = sys.argv[2] if len(sys.argv) > 2 else "data/raw_accidents.json"
N = int(sys.argv[3]) if len(sys.argv) > 3 else 50
WORKERS = int(sys.argv[4]) if len(sys.argv) > 4 else 3

db = sqlite3.connect(DB)
existing_ids = set(r[0] for r in db.execute("SELECT id FROM accidents").fetchall())
existing_urls = set(r[0] for r in db.execute("SELECT source_url FROM accidents").fetchall())
print(f"[init] DB已有 {len(existing_ids)} 条", flush=True)

with open(RAW) as f:
    raw = json.load(f)
raw = raw[:N]
print(f"[init] 处理前 {N} 条", flush=True)

def process_one(task):
    i, d = task
    content = d.get("content", "")
    title = d.get("title", "")
    meta = {"publish_date": d.get("publish_date", ""), "source_name": d.get("source_name", "")}
    t0 = time.time()
    e1 = Step1BasicExtractor()
    eh = SearchEnhancer()
    e2 = Step2CaicsEncoder()
    s1 = e1.extract(content, metadata=meta)
    if not s1: return None
    s1d = s1.to_dict()
    sr = None
    # 搜索增强已禁用 (千帆免费额度耗尽), 后续单独运行
    # if eh.needs_search(content, s1d):
    #     try: ...
    s2 = e2.encode(content, basic_info=s1d)
    if not s2: return None
    ems = int((time.time() - t0) * 1000)
    aid = hashlib.md5(f"{s1d.get('title','')}|{s1d.get('date','') or ''}|{s1d.get('city','') or ''}".encode()).hexdigest()[:12]
    return (i, d, s1d, s2, sr, ems, aid)

# 构建任务列表：跳过已有
tasks = []
for i, d in enumerate(raw):
    # 快速预判：标题过短或不是施工相关 → 跳过（节省API）
    title = d.get("title", "")
    content = d.get("content", "")[:100]
    quick_check = title + content
    if len(title) < 6 and len(content) < 30:
        continue
    # 用简单规则快速过滤明显非施工的
    skip_kw = ["交通肇事", "煤矿", "化工厂爆炸", "山洪", "自然灾害", "地震"]
    if any(kw in quick_check for kw in skip_kw):
        continue
    # 跳过已入库的(按 source_url 匹配)
    if d.get("source_url","") in existing_urls:
        continue
    tasks.append((i, d))

print(f"[init] 任务 {len(tasks)} 条", flush=True)

# 并发处理
print(f"[proc] {WORKERS} workers, {len(tasks)} 条", flush=True)
results = []
t_start = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futures = {ex.submit(process_one, t): t for t in tasks}
    done = 0
    for f in as_completed(futures):
        done += 1
        try:
            r = f.result()
        except Exception:
            r = None
        if r: results.append(r)
        if done % 20 == 0 or done == len(tasks):
            elapsed = time.time() - t_start
            rate = elapsed / max(done, 1)
            print(f"[proc] {done}/{len(tasks)} | {rate:.0f}s/条 | {len(results)} 成功", flush=True)
proc_time = time.time() - t_start

# 入库
print(f"[save] {len(results)} 条...", flush=True)
ok = 0
for i, d, s1d, s2, sr, ems, aid in results:
    if aid in existing_ids:
        continue
    now = datetime.now().isoformat()
    def safe(v, defv=""): return defv if v is None else v
    db.execute(
        """INSERT OR REPLACE INTO accidents (id,title,source_url,source_name,crawl_date,publish_date,date,accident_time,province,city,district,accident_location_detail,weather,project_name,project_type,building_structure,construction_phase,work_task,developer,contractor,contractor_type,supervision_company,accident_type,severity,deaths,injuries,trapped_count,direct_economic_loss,collapse_subtype,fall_height,equipment_involved,material_involved,worker_type,direct_cause,report_type,text_type,classification_verdict,classification_confidence,source_count,summary,raw_text,search_enriched_text,search_performed,pipeline_version,step1_duration_ms,step2_duration_ms,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (aid, safe(s1d.get("title")), safe(d.get("source_url")), safe(d.get("source_name")), safe((d.get("crawl_date", "") or "")[:19]), safe(d.get("publish_date")), s1d.get("date"), s1d.get("accident_time"), s1d.get("location"), s1d.get("city"), s1d.get("district"), s1d.get("accident_location_detail"), s1d.get("weather"), s1d.get("project_name"), safe(s1d.get("project_type", "其他/未知"), "其他/未知"), s1d.get("building_structure"), safe(s1d.get("construction_phase"), "未知"), s1d.get("work_task"), s1d.get("developer"), s1d.get("contractor"), s1d.get("contractor_type"), s1d.get("supervision_company"), safe(s1d.get("accident_type")), safe(s1d.get("severity")), s1d.get("deaths", 0), s1d.get("injuries", 0), s1d.get("trapped_count", 0), s1d.get("direct_economic_loss"), s1d.get("collapse_subtype"), s1d.get("fall_height"), s1d.get("equipment_involved"), s1d.get("material_involved"), s1d.get("worker_type"), safe(s2.primary_cause if s2 else ""), safe(s1d.get("report_type", "新闻报道"), "新闻报道"), "single", "batch_direct", "medium", 1, safe(s1d.get("summary")), safe(d.get("content", "")), sr.get("enriched_text") if sr else None, 1 if (sr and sr.get("search_performed")) else 0, "caics_v1.0", s1d.get("_processing_time", 0) * 1000, s2._processing_time * 1000 if s2 else None, now),
    )
    db.execute("INSERT OR IGNORE INTO source_urls (accident_id,url,source_name,created_at) VALUES (?,?,?,?)", (aid, safe(d.get("source_url")), safe(d.get("source_name")), now))
    if sr and sr.get("search_performed"):
        for u in sr.get("same_accident_urls", []):
            db.execute("INSERT OR IGNORE INTO source_urls (accident_id,url,source_name,created_at) VALUES (?,?,?,?)", (aid, u["url"], "baidu_search", now))
    if s2:
        for c2 in s2.causes:
            eq = compute_evidence_quality(c2.evidence, c2.inference)
            order = compute_sort_order(c2.code)
            db.execute("INSERT INTO caics_causes (accident_id,code,name,evidence,inference,evidence_quality,sort_order) VALUES (?,?,?,?,?,?,?)", (aid, c2.code, c2.name, c2.evidence, c2.inference, eq, order))
    nf = len([v for v in s1d.values() if v not in (None, "", "未提及", 0)])
    db.execute("INSERT INTO processing_log (accident_id,pipeline_version,text_type,text_type_confidence,text_type_reason,classification_verdict,classification_rule_result,classification_llm_result,search_triggered,step1_success,step1_fields_extracted,step1_duration_ms,step2_success,step2_cause_count,step2_duration_ms,image_count_analyzed,total_duration_ms,error_message,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (aid, "caics_v1.0", "single", "medium", "batch_direct_skip", "batch_direct", None, None, 1 if (sr and sr.get("search_performed")) else 0, 1, nf, int(s1d.get("_processing_time", 0) * 1000), 1, len(s2.causes) if s2 else 0, int(s2._processing_time * 1000) if s2 else None, 0, ems, None, now))
    if sr and sr.get("search_performed"):
        db.execute("INSERT INTO search_logs (accident_id,search_performed,queries_used,total_results,same_accident_count,same_accident_snippets,engine,error,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (aid, 1, json.dumps(sr.get("queries_used", []), ensure_ascii=False), sr.get("total_results", 0), sr.get("same_accident_count", 0), json.dumps(sr.get("same_accident_snippets", []), ensure_ascii=False), "qianfan", sr.get("error"), now))
    db.commit()
    ok += 1
    existing_ids.add(aid)

total_time = time.time() - t_start
acc = db.execute("SELECT COUNT(*) FROM accidents").fetchone()[0]
causes = db.execute("SELECT COUNT(*) FROM caics_causes").fetchone()[0]
db.commit()
db.close()
print(f"[done] +{ok} 条 | 并发 {proc_time/60:.0f}min | 总 {total_time/60:.0f}min | DB {acc} 条 {causes} 编码", flush=True)
print(f"[done] 吞吐 {total_time/max(ok,1):.0f}s/条", flush=True)
