"""在线采集执行器：把采集器的产出收进工作库的 `raw_accidents`。

**两条产出通路，一条都不能漏。** 实测 24 个源分三种形态：

| 形态 | 源 | 产出怎么出来 |
|---|---|---|
| A. 走 store | 21 个（9 个直接调、12 个经 `TrsCmsCollector`） | `raw_store.add()` → 我们注入的 sink 回调 |
| B. 走返回值 | `mohurd` / `news_aggregator` / `open_gov` | `collect()` 返回 `list[RawAccidentText]` |
| C. 两者都有 | `safehoo` | 既写 store **又**返回真列表 |

形态 C 是「同一条收两次」的直接原因，也是 DDL 里 `UNIQUE(url_hash)` 存在的理由：
执行器不靠内存判断重不漏，把去重交给数据库约束，`INSERT OR IGNORE` 收尾。

**还有一个坑**：9 个 store-backed 的非 TRS 源（`beijing_em` / `hebei_em` /
`henan_em` / `ln_em` / `nmg_em` / `esafety` / `gd_em` / `mem` / `safehoo`）的
`collect()` **返回的是 `_make_stats()` 统计字典，不是列表**，尽管类型标注写着
`-> list[RawAccidentText]`。所以返回值这一路不能假设是列表——类型判断放在
运行期做，写成 `isinstance(ret, list)`，不是断言。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ..web import staging_db
from . import registry
from .base import RawAccidentText
from .registry import SourceSpec

logger = logging.getLogger(__name__)

# 跨源并行、同源串行的并发度。3 是经验值：政府站对同一 IP 的并发容忍度低，
# 而 24 个源各自是独立站点，跨源并行不会给任一站点加压。
DEFAULT_CONCURRENCY = 3

# 单源总超时（秒）。超时不再等，把该源记 failed 继续下一个——一个源挂住
# 不该拖死整场采集。
SOURCE_TIMEOUT_S = 30 * 60

_CANCEL: set[str] = set()          # task_id 集合，协作式取消信号
_CANCEL_LOCK = threading.Lock()

# 任务级线程池。旧 web 的 `pipeline` 模块级单例出过并发竞态（蓝图 00 §1），
# 这里的模块级对象只做「调度」，每个任务的状态各自独立、入口按 task_id 索引，
# 没有共享可变状态，所以并发是安全的。
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="collect")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------
# 归一化：两条通路 → `raw_accidents` 行
# --------------------------------------------------------------------------


# 采集器把附件 URL 塞在自定义字段里，键名不统一（21 个走 store 的源各写各的）。
# 这张表是实测出来的：`pdf_urls` 是唯一定义在 `RawAccidentStore` 契约里的，
# 其余几个是零星几个源自己的叫法。
#
# `office_urls` 2026-10-01 新增：`gd_em` 原先把 Word 附件也塞进 `pdf_urls`
# （判据是 `".doc" in href`，而 `.doc` 是 `.docx` 的子串），于是登记出来是
# `asset_type="pdf"` 而文件其实是 `.docx`/`.doc`。**按键名映射类型**这张表
# 本身没错，错的是上游把两种东西塞进了同一个键。
_ASSET_KEYS: tuple[tuple[str, str], ...] = (
    ("pdf_urls", "pdf"),
    ("office_urls", "office"),
    ("image_urls", "image"),
    ("attachment_urls", "attachment"),
    ("attachments", "attachment"),
)


def _assets_from(extra: dict) -> list[dict]:
    """从采集器的自定义字段里认出附件 URL，登记到 `raw_accident_assets`。

    为什么要登记：这些 PDF 常常是**正文的唯一来源**（省级站的通报正文直接
    以附件形式挂出来，页面本身只有一行标题）。阶段② 要靠这张表知道该去下
    哪些文件，不然信息就断在采集这一步了。

    键名不认识的不猜——宁可少登记一条，也不要把一个普通链接当附件去下载。
    元素允许是字符串或 `{"url": ...}` 形状的字典（两种都实测出现过）。

    **`local_path` 这一列为什么不填**（2026-10-01 想过要填，查完决定不填）：
    表里确实有这一列，而且空了很久。但采集器落盘用的是 `_run_source` 传进来的
    **临时目录**，那个目录在 `finally` 里被 `shutil.rmtree` 删掉——任务的产出
    刻意「不散在文件系统」。于是这里填进去的路径，任务一结束就指向一个不存在
    的文件：**空着只是缺信息，填错值是在撒谎**。

    真正需要原件的是阶段②，而它拿到的是 URL。要让它用上本地文件，得先由采集
    阶段决定把附件存到一个**持久**位置（比如 `data/attachments/`），那是采集
    蓝图里要单独定的一件事，不该在登记这一步顺手改掉。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for key, kind in _ASSET_KEYS:
        val = (extra or {}).get(key)
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, (list, tuple)):
            continue
        for item in val:
            if isinstance(item, dict):
                url = item.get("url") or item.get("href")
            else:
                url = item
            url = str(url or "").strip()
            if url and url not in seen:
                seen.add(url)
                out.append({"asset_type": kind, "url": url})
    return out


def _row_from_sink_record(rec: dict, source_key: str, task_id: str
                          ) -> Optional[dict]:
    """通路 A：`RawAccidentStore.add()` 交出来的 record → 一行。

    没有 URL 的记录直接丢：`raw_accidents` 的 `raw_id` / `url_hash` 都由 URL
    算出，没有 URL 就没有主键。这类记录在源里本就不该被 add，属于源的缺陷。
    """
    url = (rec.get("source_url") or "").strip()
    if not url:
        return None

    # `RawAccidentStore._hash_url` 用的就是 md5(url)[:12]，与 `raw_id` 的
    # 主键约定相同；`id` 字段直接复用，不重算一遍。
    rid = rec.get("id") or hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    mapped = {"id", "title", "content", "source_url", "source_name",
              "crawl_date", "publish_date", "status", "filter_reason"}
    extra = {k: v for k, v in rec.items() if k not in mapped}

    return {
        "raw_id": rid,
        "source_key": source_key,
        "task_id": task_id,
        "title": rec.get("title") or "",
        "content": rec.get("content") or "",
        # 通路 A 拿不到原始 HTML：`RawAccidentStore` 只留正文。
        # 二次解析需要 HTML 时得回到源适配器，这里如实地留空。
        "raw_html": "",
        "source_url": url,
        "fetch_time": rec.get("crawl_date") or _now(),
        "publish_date": rec.get("publish_date") or None,
        "url_hash": rid,
        "fingerprint": None,        # 规则提取器还没跑（阶段②的事）
        "title_hash": None,
        "extra": json.dumps(extra, ensure_ascii=False, default=str) if extra else None,
        # `_` 前缀的键不是 `raw_accidents` 的列，写库前会被摘出去单独处理
        "_assets": _assets_from(extra),
    }


def _row_from_raw_item(item: RawAccidentText, source_key: str, task_id: str
                       ) -> Optional[dict]:
    """通路 B：`collect()` 返回值里的 `RawAccidentText` → 一行。

    这条通路的字段比 A 全（有 `raw_html` 和三层去重键），因为 `RawAccidentText`
    本来就是为这个设计的。`raw_id` 仍取 `generate_url_hash()`——`id` 字段可能
    被源填成指纹或外部 id，不能当主键用。
    """
    url = (item.source_url or "").strip()
    if not url:
        return None

    url_hash = item.generate_url_hash() or hashlib.md5(
        url.encode("utf-8")).hexdigest()[:12]
    ext_date = item.extracted_date.isoformat() if item.extracted_date else None

    extra = dict(item.extra or {})
    if item.source:
        extra["source"] = item.source
    if item.id and item.id != url_hash:
        extra["source_id"] = item.id

    return {
        "raw_id": url_hash,
        "source_key": source_key,
        "task_id": task_id,
        "title": item.title or "",
        "content": item.raw_text or "",
        "raw_html": (item.raw_html or "")[:50_000],   # DDL 注释：<=50KB
        "source_url": url,
        "fetch_time": item.fetch_time or _now(),
        "publish_date": ext_date,
        "url_hash": url_hash,
        "fingerprint": item.generate_fingerprint() or None,
        "title_hash": item.generate_title_hash() or None,
        "ext_date": ext_date,
        "ext_province": item.extracted_province or None,
        "ext_city": item.extracted_city or None,
        "ext_deaths": item.extracted_deaths or None,
        "ext_injuries": item.extracted_injuries or None,
        "ext_accident_type": item.extracted_accident_type or None,
        "ext_severity": item.extracted_severity or None,
        "confidence_overall": item.confidence_overall or None,
        "extra": json.dumps(extra, ensure_ascii=False, default=str) if extra else None,
        "_assets": _assets_from(extra),
    }


_INSERT_SQL = """
INSERT OR IGNORE INTO raw_accidents
    (raw_id, source_key, task_id, title, content, raw_html, source_url,
     fetch_time, publish_date, url_hash, fingerprint, title_hash,
     ext_date, ext_province, ext_city, ext_deaths, ext_injuries,
     ext_accident_type, ext_severity, confidence_overall, status, extra)
VALUES
    (:raw_id, :source_key, :task_id, :title, :content, :raw_html, :source_url,
     :fetch_time, :publish_date, :url_hash, :fingerprint, :title_hash,
     :ext_date, :ext_province, :ext_city, :ext_deaths, :ext_injuries,
     :ext_accident_type, :ext_severity, :confidence_overall, 'new', :extra)
"""

# `executemany` 要求每行的键齐平，缺键会 KeyError；补默认值比在两条通路上
# 各写全 22 个键省事，也避免将来加列时漏改一处。
_ROW_DEFAULTS: dict[str, Any] = {
    "raw_id": None, "source_key": None, "task_id": None, "title": "",
    "content": "", "raw_html": "", "source_url": "", "fetch_time": None,
    "publish_date": None, "url_hash": None, "fingerprint": None,
    "title_hash": None, "ext_date": None, "ext_province": None,
    "ext_city": None, "ext_deaths": None, "ext_injuries": None,
    "ext_accident_type": None, "ext_severity": None,
    "confidence_overall": None, "extra": None,
}


def _fill(row: dict) -> dict:
    return {**_ROW_DEFAULTS, **row}


# --------------------------------------------------------------------------
# 采集后过滤
# --------------------------------------------------------------------------


def normalize_params(params: Optional[dict]) -> dict:
    """把请求参数归一成执行器认的形状。

    `keywords` 允许传列表或逗号分隔的字符串（表单里是后者），统一成列表；
    `limit` 统一成 int 或 None。**不改语义**——它到底是「采集后筛选」还是
    「只抓这些」，见 `matches_filters()` 的说明。
    """
    p = dict(params or {})
    kw = p.get("keywords")
    if isinstance(kw, str):
        p["keywords"] = [k.strip() for k in kw.replace("，", ",").split(",")
                         if k.strip()]
    elif isinstance(kw, (list, tuple)):
        p["keywords"] = [str(k).strip() for k in kw if str(k).strip()]
    else:
        p["keywords"] = []

    for k in ("since", "until"):
        v = p.get(k)
        p[k] = str(v)[:10] if v else None

    if p.get("limit") is not None:
        try:
            p["limit"] = max(1, int(p["limit"]))
        except (TypeError, ValueError):
            p["limit"] = None
    return p


def matches_filters(row: dict, filters: dict) -> bool:
    """入库前过滤的一道闸。

    **这是「采集后筛选」，不是「只抓这些」。** 24 个适配器没有一个接受
    `since` / `until` / `keywords`（唯一沾边的是 `safehoo.max_items` 和
    `mem.start_year/end_year`，那属于源自己的构造参数），所以条件只能在
    记录回来之后才判——网络请求数不会因此减少，页面上必须这么写，否则
    用户会以为限了日期就能少抓几页。

    `publish_date` 提不出来的记录在有日期条件时**一律保留**：过滤的目的是
    缩小范围，不是把提不出日期的记录判死。省级站有相当比例的正文把日期
    埋在 PDF 里，一律丢掉等于白跑一趟。
    """
    since, until = filters.get("since"), filters.get("until")
    if since or until:
        d = (row.get("publish_date") or "")[:10]
        if d:
            if since and d < since:
                return False
            if until and d > until:
                return False

    kws = filters.get("keywords") or []
    if kws:
        hay = (row.get("title") or "") + (row.get("content") or "")
        if not any(k in hay for k in kws):
            return False
    return True


# --------------------------------------------------------------------------
# 单源收集器
# --------------------------------------------------------------------------


@dataclass
class SourceOutcome:
    """一个源跑完的事实。字段名直接对应 `collect_tasks.stats.per_source`。"""

    key: str
    ok: bool = False
    collected: int = 0     # 源交出来的条数（含与库内重复的、含被条件滤掉的）
    filtered: int = 0      # 被 since/until/keywords 拦下的条数
    inserted: int = 0      # 真正落库的条数
    dup: int = 0           # 被 UNIQUE(url_hash) 拦下的条数
    error: str = ""
    ms: int = 0

    def as_dict(self) -> dict:
        return {"ok": self.ok, "collected": self.collected,
                "filtered": self.filtered, "inserted": self.inserted,
                "dup": self.dup, "error": self.error, "ms": self.ms}


class _Sink:
    """一场采集里某个源的收集缓冲。两条通路都调它。

    `limit` 的语义是**收集上限，不是采集上限**：只有 `safehoo` 的构造器有
    `max_items`（会真的早停），其余 23 个源没有这个概念，到了上限之后网络
    请求照跑，只是记录不再进来。这个区别写在这里，免得看页面的人以为
    「limit=20 能在 5 分钟内跑完 24 个源」。
    """

    def __init__(self, key: str, task_id: str, limit: Optional[int],
                 stop: Callable[[], bool], filters: Optional[dict] = None):
        self.key = key
        self.task_id = task_id
        self.limit = limit
        self._stop = stop
        self.filters = filters or {}
        self.rows: list[dict] = []
        self._lock = threading.Lock()
        self.capped = False
        self.filtered_out = 0

    def _accept(self, row: Optional[dict]) -> None:
        if row is None:
            return
        # 过滤放在计数之前：被条件拦下的记录不该占 limit 的名额，
        # 否则「上限 20 条」在一堆不相关文本里会一条都留不下。
        if not matches_filters(row, self.filters):
            self.filtered_out += 1
            return
        with self._lock:
            if self.limit is not None and len(self.rows) >= self.limit:
                self.capped = True
                return
            self.rows.append(row)

    # 通路 A
    def __call__(self, record: dict) -> None:
        self._accept(_row_from_sink_record(record, self.key, self.task_id))

    # 通路 B
    def feed(self, item: RawAccidentText) -> None:
        self._accept(_row_from_raw_item(item, self.key, self.task_id))

    @property
    def stop_requested(self) -> bool:
        return self.capped or self._stop()


# --------------------------------------------------------------------------
# 单源执行
# --------------------------------------------------------------------------


def _run_source(spec: SourceSpec, task_id: str, *,
                limit: Optional[int], tmp_root: Path,
                filters: Optional[dict] = None,
                per_source_timeout: int = SOURCE_TIMEOUT_S) -> SourceOutcome:
    """跑一个源：构造 → 挂 sink → collect → 收返回值 → 写库。

    异常一律收成 `SourceOutcome.error`，不让它掀掉整个任务——蓝图 01 §3.5：
    「超时记 `last_error` 并跳过该源，不中断任务」。
    """
    started = time.monotonic()
    out = SourceOutcome(key=spec.key)
    sink = _Sink(spec.key, task_id, limit, lambda: _is_cancelled(task_id),
                 filters)

    # 每个源一个独立临时目录：`data_dir` 传进去之后 PDF、详情 HTML 都落这里，
    # 任务结束即删。`data/<源名>/` 因此永远不出现（蓝图 01 §8 验收第 10 条）。
    data_dir = tmp_root / spec.key

    # 排队期间被取消：连站点都不要碰。放在构造之前——排队等线程池的时候
    # 用户点了取消，不该还发一轮请求出去。
    if _is_cancelled(task_id):
        out.ok = True
        out.error = "任务已取消，未启动"
        return out

    try:
        collector, _ = registry.build(spec.key, data_dir=data_dir)

        store = getattr(collector, "raw_store", None)
        if store is not None and hasattr(store, "attach"):
            store.attach(sink)      # 通路 A 生效；文件通路同时被掐掉
        else:
            logger.debug("[%s] 没有 raw_store，只靠 collect() 返回值", spec.key)

        # `safehoo` 是唯一原生支持条数上限的源（构造参数 `max_items`），
        # 能真早停就透传——省下的是网络时间，不只是内存。判据取构造签名
        # 而不是 `hasattr`：后者在刚赋完值之后必然为真，等于没判。
        if limit is not None:
            if "max_items" in inspect.signature(type(collector).__init__).parameters:
                collector.max_items = limit
            else:
                logger.debug("[%s] 不支持 max_items，limit 只在收集层生效", spec.key)

        ret = collector.collect()

        # 通路 B。`isinstance(ret, list)` 不是防御性写法，是必需的：
        # 9 个 store-backed 的源这里返回的是 stats dict（见模块 docstring）。
        if isinstance(ret, list):
            for item in ret:
                if isinstance(item, RawAccidentText):
                    sink.feed(item)
                if sink.stop_requested:
                    break
        elif ret is not None:
            logger.debug("[%s] collect() 返回 %s，非列表，按 store 通路处理",
                         spec.key, type(ret).__name__)

        out.collected = len(sink.rows) + sink.filtered_out
        out.filtered = sink.filtered_out

        if sink.rows:
            rows, assets_by_id = [], {}
            for raw in sink.rows:
                row = _fill(raw)
                found = row.pop("_assets", None)
                if found:
                    assets_by_id[row["raw_id"]] = found
                rows.append(row)

            with staging_db.write_tx() as con:
                # 先问清楚哪些 raw_id 是新的。`INSERT OR IGNORE` 之后
                # `total_changes` 只给总数，分不出「插进去的」和「被唯一键
                # 拦下的」，而附件只能给真正新进来的记录登记——否则每重跑
                # 一次采集，同一批 PDF 就再登记一遍。
                ids = [r["raw_id"] for r in rows]
                marks = ",".join("?" * len(ids))
                existing = {r[0] for r in con.execute(
                    f"SELECT raw_id FROM raw_accidents WHERE raw_id IN ({marks})",
                    ids)}

                before = con.total_changes
                con.executemany(_INSERT_SQL, rows)
                out.inserted = con.total_changes - before

                fresh = [(rid, a) for rid, lst in assets_by_id.items()
                         if rid not in existing for a in lst]
                if fresh:
                    con.executemany(
                        "INSERT INTO raw_accident_assets "
                        "(raw_id, asset_type, url, ocr_status) "
                        "VALUES (?, ?, ?, 'pending')",
                        [(rid, a["asset_type"], a["url"]) for rid, a in fresh])
            out.dup = out.collected - out.inserted

        out.ok = True

    except Exception as e:  # noqa: BLE001 —— 单源失败不许影响别的源
        out.ok = False
        out.error = f"{type(e).__name__}: {e}"[:500]
        out.collected = len(sink.rows) + sink.filtered_out
        out.filtered = sink.filtered_out
        logger.warning("[%s] 采集失败: %s", spec.key, out.error)
    finally:
        out.ms = int((time.monotonic() - started) * 1000)
        # 临时目录必删：这是「产出不散在文件系统」的最后一道保险。
        # henan_em / ln_em 会往这里下 PDF，不删就是几十 MB 残留。
        shutil.rmtree(data_dir, ignore_errors=True)

    return out


# --------------------------------------------------------------------------
# 任务级编排
# --------------------------------------------------------------------------


def _is_cancelled(task_id: str) -> bool:
    with _CANCEL_LOCK:
        return task_id in _CANCEL


def _update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with staging_db.write_tx() as con:
        con.execute(f"UPDATE collect_tasks SET {sets} WHERE task_id = :task_id",
                    {**fields, "task_id": task_id})


def _bump_source(spec: SourceSpec, out: SourceOutcome) -> None:
    """把单源结果写回 `collect_sources` 的健康度列。

    `total_raw` 用累加（`= total_raw + ?`）而不是覆盖：这一列是历史累计，
    一次任务只该往上加。`last_run_ok` / `last_error` 是「最近一次」的语义，
    覆盖才对。
    """
    with staging_db.write_tx() as con:
        con.execute(
            """
            UPDATE collect_sources
               SET last_run_at  = :now,
                   last_run_ok  = :ok,
                   last_error   = :err,
                   total_raw    = COALESCE(total_raw, 0) + :n
             WHERE source_key    = :key
            """,
            {"now": _now(), "ok": 1 if out.ok else 0,
             "err": out.error or None, "n": out.inserted, "key": spec.key},
        )


def create_task(source_keys: list[str], params: Optional[dict] = None,
                *, concurrency: int = DEFAULT_CONCURRENCY) -> str:
    """建任务行（`pending`），返回 task_id。不做采集。

    先落库再跑，是为了「重启 web 服务后进度不丢」（蓝图 01 §8 验收第 3 条）：
    进程挂了，`collect_tasks` 里那条 `running` 还在，`reconcile_stale_tasks()`
    下次启动能把它标成中断。
    """
    if not source_keys:
        raise ValueError("source_keys 不能为空")
    for k in source_keys:
        registry.get(k)          # 未登记就抛 KeyError，早失败好过跑一半才发现

    task_id = _new_id()
    p = normalize_params(params)
    p.setdefault("concurrency", concurrency)
    with staging_db.write_tx() as con:
        # 外键要求 collect_sources 先有行；同时保证源清单和代码同步
        registry.sync_to_db(con)
        con.execute(
            """
            INSERT INTO collect_tasks
                (task_id, source_keys, params, status, created_at, stats)
            VALUES (?, ?, ?, 'pending', ?, '{}')
            """,
            (task_id, json.dumps(source_keys, ensure_ascii=False),
             json.dumps(p, ensure_ascii=False), _now()),
        )
    return task_id


def run_task(task_id: str) -> dict:
    """同步跑一个已建的任务。返回最终 stats。

    每完成一个源就把 `stats` 写一次库，页面轮询 `GET /api/collect/tasks/<id>`
    因此能看到「跑完 5/24」这种中间态，而不是等 30 分钟拿一个结果。
    """
    task = staging_db.query_one(
        "SELECT * FROM collect_tasks WHERE task_id = ?", (task_id,))
    if task is None:
        raise KeyError(f"任务不存在: {task_id}")

    keys: list[str] = json.loads(task["source_keys"])
    params: dict = normalize_params(json.loads(task["params"] or "{}"))
    limit = params.get("limit") or None
    concurrency = int(params.get("concurrency") or DEFAULT_CONCURRENCY)

    # 建完就被取消的任务已经是 `cancelling`，别用 `running` 把取消意图盖掉——
    # 那样结尾会判成 `done`，用户点了取消却看到「已完成」。
    if task["status"] in ("pending", "running"):
        _update_task(task_id, status="running", started_at=_now())

    per_source: dict[str, dict] = {}
    tmp_root = Path(tempfile.mkdtemp(prefix=f"collect_{task_id}_"))
    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {}
            for key in keys:
                spec = registry.get(key)
                fut = pool.submit(_run_source, spec, task_id, limit=limit,
                                  tmp_root=tmp_root, filters=params)
                futures[fut] = spec
            for fut in futures:
                spec = futures[fut]
                try:
                    out = fut.result(timeout=SOURCE_TIMEOUT_S + 60)
                except Exception as e:  # noqa: BLE001 —— 超时或线程内异常
                    out = SourceOutcome(key=spec.key, ok=False,
                                        error=f"{type(e).__name__}: {e}"[:500])
                per_source[spec.key] = out.as_dict()
                _bump_source(spec, out)
                _update_task(task_id, stats=json.dumps(
                    {"per_source": per_source}, ensure_ascii=False))
                logger.info("[%s] %s 收 %d / 新增 %d / 重复 %d (%d ms)",
                            task_id, spec.key, out.collected, out.inserted,
                            out.dup, out.ms)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        with _CANCEL_LOCK:
            _CANCEL.discard(task_id)

    # 取消动作会把状态置成 `cancelling`（中间态：意图已表达、任务还在收尾）。
    # 跑完时据此定终态，否则 `done` 会盖掉用户刚点的取消。
    interrupted = staging_db.scalar(
        "SELECT status FROM collect_tasks WHERE task_id = ?", (task_id,))
    failed = [k for k, v in per_source.items() if not v["ok"]]
    if interrupted == "cancelling":
        final = "cancelled"
    elif failed and len(failed) == len(keys):
        final = "failed"       # 全军覆没才算失败，部分成功仍是 done
    else:
        final = "done"

    totals = {
        "per_source": per_source,
        "collected": sum(v["collected"] for v in per_source.values()),
        "filtered": sum(v["filtered"] for v in per_source.values()),
        "inserted": sum(v["inserted"] for v in per_source.values()),
        "dup": sum(v["dup"] for v in per_source.values()),
        "failed_sources": failed,
    }
    _update_task(task_id, status=final, finished_at=_now(),
                 stats=json.dumps(totals, ensure_ascii=False))
    return totals


def submit_task(source_keys: list[str], params: Optional[dict] = None,
                *, concurrency: int = DEFAULT_CONCURRENCY) -> str:
    """建任务并丢进线程池，立刻返回 task_id（API 走这个）。"""
    task_id = create_task(source_keys, params, concurrency=concurrency)
    _POOL.submit(run_task, task_id)
    return task_id


def cancel_task(task_id: str) -> bool:
    """请求取消。协作式：已提交给线程池的源跑完当前那一步才停。

    粒度**只能到源级**——24 个适配器都是一次性 `return` 整个列表，没有
    `yield` 点可供中断（蓝图 01 §3.4 的 `collect_items()` 设想因此不成立，
    见蓝图 §9 待决项）。所以取消后最坏要等当前这个源跑完。
    """
    row = staging_db.query_one(
        "SELECT status FROM collect_tasks WHERE task_id = ?", (task_id,))
    if row is None:
        return False
    if row["status"] in ("done", "failed", "cancelled"):
        return False
    with _CANCEL_LOCK:
        _CANCEL.add(task_id)
    _update_task(task_id, status="cancelling")
    return True


def reconcile_stale_tasks() -> int:
    """把上次进程留下的 `pending` / `running` / `cancelling` 任务标为中断。

    进程重启后，线程池里的任务全没了，但库里那几行还写着 `running`——页面
    会永远显示「进行中」。启动时跑一次，把它们改成 `failed` 并写明原因。
    返回处理的行数。
    """
    with staging_db.write_tx() as con:
        cur = con.execute(
            """
            UPDATE collect_tasks
               SET status = 'failed',
                   finished_at = ?,
                   error = COALESCE(error, '进程重启，任务中断')
             WHERE status IN ('pending', 'running', 'cancelling')
            """,
            (_now(),),
        )
        return cur.rowcount


def get_task(task_id: str) -> Optional[dict]:
    task = staging_db.query_one(
        "SELECT * FROM collect_tasks WHERE task_id = ?", (task_id,))
    if task is None:
        return None
    task["source_keys"] = json.loads(task["source_keys"] or "[]")
    task["params"] = json.loads(task["params"] or "{}")
    task["stats"] = json.loads(task["stats"] or "{}")
    return task


def list_tasks(limit: int = 50) -> list[dict]:
    rows = staging_db.query(
        "SELECT * FROM collect_tasks ORDER BY created_at DESC LIMIT ?", (limit,))
    for r in rows:
        r["source_keys"] = json.loads(r["source_keys"] or "[]")
        r["params"] = json.loads(r["params"] or "{}")
        r["stats"] = json.loads(r["stats"] or "{}")
    return rows
