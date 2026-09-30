"""批量管线: 原始数据 → 分类 → 提取 → 编码 → 入库。"""

import json, sqlite3, sys, logging, hashlib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from construction_agent.extraction.caics_pipeline import (
    STATUS_COMPLETED,
    STATUS_PURGED,
    CaicsPipeline,
)
# 证据等级上收为共享出口。本文件原先是 6 份复制品里**唯一分叉**的一份
# （`indirect_infer` 多认一个「指向」），且与主库 16,323 条对不上：
# 含「指向」只对 16,284 条，不含则全对。分叉已收敛，口径见该模块 docstring。
from construction_agent.extraction.evidence_quality import (
    compute_evidence_quality,
    compute_sort_order,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "data/caiems_v2.db"
RAW_PATH = "data/raw_accidents.json"
BATCH_SIZE = 50


def generate_id(title: str, date: str, city: str) -> str:
    parts = f"{title}|{date}|{city}"
    return hashlib.md5(parts.encode()).hexdigest()[:12]


def insert_accident(conn: sqlite3.Connection, s1: dict, s2_result, raw: dict,
                    text_type_result, class_result, search_result, elapsed_ms: int):
    """插入一条事故及其所有关联数据。"""
    now = datetime.now().isoformat()
    aid = generate_id(s1["title"], s1.get("date", ""), s1.get("city", ""))

    # accidents 主表
    conn.execute("""
        INSERT OR REPLACE INTO accidents (
            id,title,source_url,source_name,crawl_date,publish_date,
            date,accident_time,province,city,district,accident_location_detail,weather,
            project_name,project_type,building_structure,construction_phase,work_task,
            developer,contractor,contractor_type,supervision_company,
            accident_type,severity,deaths,injuries,trapped_count,direct_economic_loss,
            collapse_subtype,fall_height,equipment_involved,material_involved,worker_type,
            direct_cause,report_type,text_type,classification_verdict,classification_confidence,
            source_count,summary,raw_text,search_enriched_text,search_performed,
            pipeline_version,step1_duration_ms,step2_duration_ms,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        aid,
        s1.get("title",""), raw.get("source_url",""), raw.get("source_name",""),
        (raw.get("crawl_date","") or "")[:19], raw.get("publish_date",""),
        s1.get("date"), s1.get("accident_time"), s1.get("location"), s1.get("city"),
        s1.get("district"), s1.get("accident_location_detail"), s1.get("weather"),
        s1.get("project_name"), s1.get("project_type","其他/未知"),
        s1.get("building_structure"), s1.get("construction_phase","未知"),
        s1.get("work_task"),
        s1.get("developer"), s1.get("contractor"), s1.get("contractor_type"),
        s1.get("supervision_company"),
        s1.get("accident_type",""), s1.get("severity",""),
        s1.get("deaths",0), s1.get("injuries",0), s1.get("trapped_count",0),
        s1.get("direct_economic_loss"),
        s1.get("collapse_subtype"), s1.get("fall_height"),
        s1.get("equipment_involved"), s1.get("material_involved"), s1.get("worker_type"),
        s2_result.primary_cause if s2_result else "",
        s1.get("report_type","新闻报道"),
        text_type_result.text_type.value if text_type_result else "single",
        class_result.verdict.value if class_result else "unknown",
        class_result.confidence if class_result else "low",
        1,
        s1.get("summary",""),
        raw.get("content",""),
        search_result.get("enriched_text") if search_result else None,
        1 if (search_result and search_result.get("search_performed")) else 0,
        "caics_v1.0",
        s1.get("_processing_time", 0) * 1000,
        s2_result._processing_time * 1000 if s2_result else None,
        now,
    ))

    # source_urls
    if raw.get("source_url"):
        conn.execute(
            "INSERT OR IGNORE INTO source_urls (accident_id,url,source_name,created_at) VALUES (?,?,?,?)",
            (aid, raw["source_url"], raw.get("source_name",""), now),
        )

    # caics_causes —— 先清后写。
    #
    # 主表用 INSERT OR REPLACE（重跑=覆盖），子表若裸 INSERT 就会在原有基础上
    # 再加一遍：同一件事故补跑一次，编码翻倍。而事故详情页会把同一编码显示两遍，
    # 每份证据单看都成立，不数行数发现不了。用真 schema 实测过：写入两次后
    # caics_causes 从 2 行变 4 行。同类修法见 `review/promote.py:11` 记的历史
    # 教训与蓝图 03 §5.3 规则 2。
    #
    # 不能靠 UNIQUE(accident_id, code) 来挡：同一类致因在一件事故里有**多条不同
    # 证据**是正常标注（主库实测 278 组，例如「4 根挑杆未与楼层锚固环连接」与
    # 「未按方案设置钢丝绳拉接」同属 H1 违反操作规程），加约束会把真实标注当重复删掉。
    conn.execute("DELETE FROM caics_causes WHERE accident_id = ?", (aid,))
    if s2_result:
        for c in s2_result.causes:
            order = compute_sort_order(c.code)
            eq = compute_evidence_quality(c.evidence, c.inference)

            conn.execute(
                "INSERT INTO caics_causes (accident_id,code,name,evidence,inference,evidence_quality,sort_order) VALUES (?,?,?,?,?,?,?)",
                (aid, c.code, c.name, c.evidence, c.inference, eq, order),
            )

    # processing_log —— 同样先清后写（无 UNIQUE 可依，重跑会累积多份处理记录，
    # 而"这条事故处理过几次"正是查退化的入口，多出来的假记录会把排查引偏）
    conn.execute("DELETE FROM processing_log WHERE accident_id = ?", (aid,))
    conn.execute("""
        INSERT INTO processing_log (
            accident_id,pipeline_version,text_type,text_type_confidence,text_type_reason,
            classification_verdict,classification_rule_result,classification_llm_result,
            search_triggered,step1_success,step1_fields_extracted,step1_duration_ms,
            step2_success,step2_cause_count,step2_duration_ms,
            total_duration_ms,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        aid, "caics_v1.0",
        text_type_result.text_type.value if text_type_result else "?",
        text_type_result.confidence if text_type_result else "?",
        text_type_result.reason[:200] if text_type_result else "",
        class_result.verdict.value if class_result else "?",
        json.dumps(class_result.rule_result, ensure_ascii=False) if class_result and class_result.rule_result else None,
        json.dumps(class_result.llm_result, ensure_ascii=False) if class_result and class_result.llm_result else None,
        1 if (search_result and search_result.get("search_performed")) else 0,
        1, len([v for v in s1.values() if v not in (None,"","未提及",0)]), int(s1.get("_processing_time",0)*1000),
        1 if s2_result else 0, len(s2_result.causes) if s2_result else 0, int(s2_result._processing_time*1000) if s2_result else None,
        elapsed_ms, now,
    ))

    # search_logs —— 同上。source_urls 不在此列：它有 UNIQUE(accident_id, url)，
    # INSERT OR IGNORE 已经挡住了重复，再加 DELETE 反而会删掉同一事故的其它来源 URL
    conn.execute("DELETE FROM search_logs WHERE accident_id = ?", (aid,))
    if search_result and search_result.get("search_performed"):
        conn.execute("""
            INSERT INTO search_logs (accident_id,search_performed,queries_used,total_results,
                same_accident_count,same_accident_snippets,engine,error,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            aid, 1,
            json.dumps(search_result.get("queries_used",[]), ensure_ascii=False),
            search_result.get("total_results",0),
            search_result.get("same_accident_count",0),
            json.dumps(search_result.get("same_accident_snippets",[]), ensure_ascii=False),
            "qianfan",
            search_result.get("error"),
            now,
        ))


def run_batch(n: int = BATCH_SIZE):
    """运行批量管线。

    编排已抽到 `CaicsPipeline`（S1–S4 的唯一实现），本函数只管三件事：
    读原始数据、调度、入库统计。

    **行为与抽提前的内联版本一致**，逐条对照见
    `construction_agent/extraction/caics_pipeline.py` 的模块 docstring。
    唯一的差异是错误处置的粒度：

        原版      S1/S2 抛异常会中断整个批次（循环体里没有 try）
        现在      归为该条 failed，批次继续

    这一点在 caics_pipeline 的增量清单里已标注，属修复而非改变语义。
    S3/S4 的失败处置与原版逐字相同（S3 失败跳过该条，S4 失败仍入库）。
    """
    # 加载数据
    with open(RAW_PATH) as f:
        raw = json.load(f)
    raw = raw[:n]
    logger.info(f"处理 {len(raw)} 条")

    pipe = CaicsPipeline()
    conn = sqlite3.connect(DB_PATH)

    success = 0
    purged = 0
    failed = 0

    for i, d in enumerate(raw):
        title = d.get("title", "")
        logger.info(f"[{i+1}/{n}] {title[:50]}")

        out = pipe.run_once(
            title=title,
            text=d.get("content", ""),
            publish_date=d.get("publish_date", ""),
            source_id=d.get("id", ""),
            source_name=d.get("source_name", ""),
        )

        # 两道闸门拦下 → 与原版同样计入 purged
        if out.status == STATUS_PURGED:
            purged += 1
            logger.info(f"  ⛔ {out.purge_reason}, 跳过")
            continue

        if out.status != STATUS_COMPLETED:
            failed += 1
            logger.error(f"  ✗ {out.purge_reason}")
            continue

        # 入库
        try:
            insert_accident(
                conn, out.basic.to_dict(), out.causes, d,
                out.text_type, out.classification, out.search, out.total_ms,
            )
            conn.commit()
            success += 1
            codes_str = ",".join(out.caics_codes) if out.causes else "?"
            logger.info(
                f"  ✅ {out.basic.accident_type or '?'} | "
                f"{len(out.caics_codes)}编码: {codes_str}"
            )
        except Exception as e:
            conn.rollback()
            failed += 1
            logger.error(f"入库异常: {e}")

    conn.close()
    logger.info(f"\n{'='*50}")
    logger.info(f"完成: 成功{success} | 过滤{purged} | 失败{failed}")
    with open("data/audit/batch50_progress.json","w") as f:
        json.dump({"success":success,"purged":purged,"failed":failed,"total":n,"timestamp":datetime.now().isoformat()}, f)


if __name__ == "__main__":
    run_batch(50)
