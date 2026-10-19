"""批量管线: 原始数据 → 分类 → 提取 → 编码 → 入库。"""

import json, re, sqlite3, sys, logging, hashlib
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


class AdmissionGuard:
    """入库前查重守门：按与主键 id 无关的客观特征查已有库，命中即跳过。

    为什么需要它：主键 `id = md5(title|date|city)`，而 `title` 由 step1 的 LLM
    **每次现场提取**、措辞会变。同一条原始记录跨批次重跑时 title 不同 → id 不同
    → 两道闸门（`INSERT OR REPLACE` 靠主键、`UNIQUE(accident_id,url)` 靠 id）
    **同时失效**，各落一行。2026-10-08 主库按 raw_text 全等查出 20 组 / 21 行
    这样的重复并删除（见 `scripts/dedup_accidents.py`）。本守门在写入之前，
    用「原文/URL/时间地点」这些不随 LLM 措辞漂移的特征拦住它们。

    三档（口径复用 collector/base.py 与 collector/cleaners/deduplicator.py）：

        L1 内容哈希    md5(raw_text)             硬，同一份原文
        L2 URL 哈希    md5(归一化 source_url)     硬，**排除本地书源**
        L3 结构化指纹  年月|城市|dN|iN|类型         软，仅告警不拦截

    **本地书源分流**：`source_url` 是本地 PDF 路径（非 http）的条目不进 L2 索引。
    一本书的几百条案例共用同一路径，进 URL 索引会把它们全判成同一事故。L1 内容
    哈希对书源仍有效——同案例全文相同才命中，正是它抓出了库里的书源重复。

    L3 只告警：指纹（年月+城市+伤亡+类型）会误伤「同城同月两起同类型同伤亡事故」，
    不能做硬判据，所以只记日志、不拦入库。
    """

    def __init__(self, conn: sqlite3.Connection):
        self._content: dict[str, str] = {}  # md5(raw_text) → accident_id
        self._url: dict[str, str] = {}      # md5(归一化 url) → accident_id（仅 http）
        self._fp: dict[str, str] = {}       # 指纹 → accident_id
        self._load(conn)

    # ── 三个哈希的口径 ──

    @staticmethod
    def _content_hash(text: str) -> str:
        if not text:
            return ""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _url_hash(url: str) -> str:
        """归一化 URL 后取哈希。归一化与 deduplicator._hash_url 一致：
        去首尾空白、去尾斜杠、去 www 前缀、去 fragment。"""
        normalized = url.strip().rstrip("/")
        normalized = re.sub(r"^https?://(?:www\.)?", "", normalized)
        normalized = normalized.split("#")[0]
        if not normalized:
            return ""
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _fingerprint(d: dict) -> str:
        """(年月|城市|d死亡|i受伤|类型) 的指纹，字段全空返回 ''。"""
        parts = []
        date = d.get("date") or ""
        if len(date) >= 7:
            parts.append(date[:7])
        if d.get("city"):
            parts.append(d["city"])
        deaths = d.get("deaths") or 0
        if deaths > 0:
            parts.append(f"d{deaths}")
        injuries = d.get("injuries") or 0
        if injuries > 0:
            parts.append(f"i{injuries}")
        if d.get("accident_type"):
            parts.append(d["accident_type"])
        if not parts:
            return ""
        return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _is_local_source(url: str) -> bool:
        """本地电子书源：source_url 是文件路径而非 http(s) URL。"""
        return not url.lower().startswith(("http://", "https://"))

    def _load(self, conn: sqlite3.Connection) -> None:
        """从已有库建三档内存索引（像 Deduplicator.load_existing_dataset）。

        空库 / `accidents` 表未建时静默返回空索引——测试用临时库没有该表，
        此时守门退化为不拦截，与加守门之前的行为一致。
        """
        try:
            rows = conn.execute(
                "SELECT id, raw_text, source_url, date, city, deaths, injuries, accident_type "
                "FROM accidents"
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"AdmissionGuard 读不到 accidents 表（{e}），本次不做查重守门")
            return

        for aid, raw_text, source_url, date, city, deaths, injuries, atype in rows:
            ch = self._content_hash(raw_text or "")
            if ch:
                self._content.setdefault(ch, aid)

            url = source_url or ""
            if url and not self._is_local_source(url):
                uh = self._url_hash(url)
                if uh:
                    self._url.setdefault(uh, aid)

            fp = self._fingerprint(
                {"date": date, "city": city, "deaths": deaths,
                 "injuries": injuries, "accident_type": atype}
            )
            if fp:
                self._fp.setdefault(fp, aid)

    # ── 查询 ──

    def check(self, s1: dict, raw: dict) -> tuple[str | None, str | None]:
        """硬判据：命中返回 (层, 已有 accident_id)，未命中返回 (None, None)。"""
        ch = self._content_hash(raw.get("content", "") or "")
        if ch and ch in self._content:
            return ("L1", self._content[ch])

        url = raw.get("source_url", "") or ""
        if url and not self._is_local_source(url):
            uh = self._url_hash(url)
            if uh and uh in self._url:
                return ("L2", self._url[uh])
        return (None, None)

    def fingerprint_hit(self, s1: dict) -> str | None:
        """软判据：指纹命中返回已有 accident_id（仅供告警，不拦入库）。"""
        fp = self._fingerprint(s1)
        return self._fp.get(fp) if fp else None

    def add(self, s1: dict, raw: dict, accident_id: str) -> None:
        """本条已成功入库后更新索引，使同批次内后续条目也能被拦住。"""
        ch = self._content_hash(raw.get("content", "") or "")
        if ch:
            self._content.setdefault(ch, accident_id)
        url = raw.get("source_url", "") or ""
        if url and not self._is_local_source(url):
            uh = self._url_hash(url)
            if uh:
                self._url.setdefault(uh, accident_id)
        fp = self._fingerprint(s1)
        if fp:
            self._fp.setdefault(fp, accident_id)


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
    # 主表用 INSERT OR REPLACE（重跑=覆盖）——**前提是 generate_id 稳定**。但 id
    # 里含 LLM 现场提取的 title，措辞一变 id 就变，REPLACE 退化成一条普通 INSERT，
    # 同一份原文会落成两行（2026-10-08 实测主库 20 组 / 21 行，见
    # `scripts/dedup_accidents.py`）。所以入库前还有一道 AdmissionGuard 按
    # raw_text/URL 拦。子表若裸 INSERT 就会在原有基础上再加一遍：同一件事故补跑
    # 一次，编码翻倍。而事故详情页会把同一编码显示两遍，每份证据单看都成立，
    # 不数行数发现不了。用真 schema 实测过：写入两次后 caics_causes 从 2 行变 4 行。
    # 同类修法见 `review/promote.py:11` 记的历史教训与蓝图 03 §5.3 规则 2。
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
    # 同一 accident_id 下的重复 url 由 INSERT OR IGNORE 挡住；再加 DELETE 反而会
    # 删掉同一事故的其它来源 URL。注意这条唯一约束**第一列就是 accident_id**，
    # 所以它挡不住「同一份原文因 id 变化而落成两条」的重复——那种靠 AdmissionGuard。
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
    # 外键是连接级开关，SQLite 默认 OFF。不开的话 schema 里那六张子表写的
    # `ON DELETE CASCADE` 形同虚设：孤儿行插得进去，且本文件下面"先清后写"
    # 之外的重跑保护也没有——REPLACE 删父行时子行会留着。开了之后 CASCADE
    # 接管，与下面的 DELETE 形成双保险。
    conn.execute("PRAGMA foreign_keys = ON")

    # 入库前查重守门：从已有库建三档索引，写入前拦住「同一份原文因 id 变化而
    # 重复」的条目（见 AdmissionGuard docstring）。放在编排层、insert 之外，
    # 不改 insert_accident 本身的语义。
    guard = AdmissionGuard(conn)

    success = 0
    purged = 0
    duplicate = 0
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

        s1 = out.basic.to_dict()

        # 查重守门：硬判据命中即跳过（不写库）；软判据（指纹）只告警不拦
        soft = guard.fingerprint_hit(s1)
        if soft:
            logger.warning(f"  ⚠ L3 指纹与已有 {soft} 相近（仅告警，不拦截）")
        layer, matched = guard.check(s1, d)
        if layer:
            duplicate += 1
            logger.info(f"  ⏭ {layer} 命中已有 {matched}，跳过入库")
            continue

        # 入库
        try:
            insert_accident(
                conn, s1, out.causes, d,
                out.text_type, out.classification, out.search, out.total_ms,
            )
            conn.commit()
            guard.add(s1, d, generate_id(s1["title"], s1.get("date", ""), s1.get("city", "")))
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
    logger.info(f"完成: 成功{success} | 过滤{purged} | 重复{duplicate} | 失败{failed}")
    with open("data/audit/batch50_progress.json","w") as f:
        json.dump({"success":success,"purged":purged,"duplicate":duplicate,
                   "failed":failed,"total":n,"timestamp":datetime.now().isoformat()}, f)


if __name__ == "__main__":
    run_batch(50)
