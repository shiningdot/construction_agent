"""M2 在线采集的回归测试（执行器 + 源登记表）。

**为什么全部用假采集器**：真跑 24 个源要访问 24 个政府站点，测试既慢又
不该打外部网络。真实的适配器行为已经由 `scripts/` 下的试跑脚本覆盖，这里
只测**执行器的编排逻辑**——两条产出通路、去重、落库、状态流转、取消。

数据库用 `tmp_path` 下的空库，不碰 `data/caiems_staging.db`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from construction_agent.collector import executor, registry
from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.web import staging_db as sdb

# 用一个真实存在的 source_key，这样 `collect_sources` 的外键与累加列
# 走的是真实代码路径（假 key 会因为外键失败，测不到东西）。
KEY = "safehoo"


@pytest.fixture
def staging(tmp_path, monkeypatch):
    """空工作库 + 隔离的环境变量。"""
    monkeypatch.setenv("CAIEMS_STAGING_DB", str(tmp_path / "staging.db"))
    sdb.init_db()
    return tmp_path


class FakeStore:
    """模拟 `RawAccidentStore`：只保留 executeor 真正用到的那一个方法。"""

    def __init__(self):
        self.sink = None
        self.kept: list[dict] = []

    def attach(self, sink):
        self.sink = sink
        return self

    def add(self, record: dict):
        """真实 `add()` 的行为：去重后 append，再交给 sink。"""
        self.kept.append(record)
        if self.sink is not None:
            self.sink(record)
        return record.get("id")


def _record(url: str, title: str = "某地发生事故", **extra) -> dict:
    import hashlib
    rid = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return {"id": rid, "title": title, "content": "正文" * 50,
            "source_url": url, "source_name": KEY,
            "crawl_date": "2026-09-30T00:00:00+00:00",
            "publish_date": "2026-09-29", "status": "new",
            "filter_reason": "", "pdf_urls": [url + ".pdf"], **extra}


class StoreCollector(BaseCollector):
    """通路 A：走 store（21 个源的样子），且 `collect()` 返回 stats 字典
    （9 个 store-backed 非 TRS 源的真实形态）。"""

    seen_data_dir: str | None = None

    def __init__(self, records, data_dir=None):
        self._records = records
        self.raw_store = FakeStore()
        self.data_dir = data_dir
        StoreCollector.seen_data_dir = str(data_dir) if data_dir else None
        if data_dir:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            (Path(data_dir) / "报告.pdf").write_bytes(b"%PDF-1.4 fake")

    def source_name(self):
        return KEY

    def collect(self):
        for r in self._records:
            self.raw_store.add(r)
        return {"collected": len(self._records), "review": 0}


class ReturnCollector(BaseCollector):
    """通路 B：不挂 store，靠 `collect()` 返回值（mohurd 那三个源的样子）。"""

    def __init__(self, items, data_dir=None):
        self._items = items
        self.data_dir = data_dir

    def source_name(self):
        return KEY

    def collect(self):
        return self._items


class BothCollector(BaseCollector):
    """通路 C：又写 store 又返回列表（safehoo 的真实形态）。"""

    def __init__(self, records, data_dir=None):
        self._records = records
        self.raw_store = FakeStore()
        self.data_dir = data_dir

    def source_name(self):
        return KEY

    def collect(self):
        for r in self._records:
            self.raw_store.add(r)
        # 同一条事故，第二次从返回值出来 —— 库里的 UNIQUE(url_hash) 该拦住它
        return [RawAccidentText(title=r["title"], raw_text=r["content"],
                                source_url=r["source_url"], source_name=KEY)
                for r in self._records]


def _patch_build(monkeypatch, factory):
    def fake_build(key, *, data_dir=None):
        assert key == KEY, key
        return factory(data_dir=data_dir), registry.get(key)
    monkeypatch.setattr(registry, "build", fake_build)


def _run(factory, monkeypatch, **task_params):
    _patch_build(monkeypatch, factory)
    task_id = executor.create_task([KEY], task_params or None)
    return task_id, executor.run_task(task_id)


def _raw_count() -> int:
    return sdb.scalar("SELECT COUNT(*) FROM raw_accidents", default=0)


# ══════════════════════════════════════════════════════════════
# 两条产出通路
# ══════════════════════════════════════════════════════════════


def test_通路A_走store的记录落库(staging, monkeypatch):
    recs = [_record(f"https://example.com/a{i}") for i in range(3)]
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    assert _raw_count() == 3
    assert stats["inserted"] == 3 and stats["dup"] == 0
    row = sdb.query_one("SELECT * FROM raw_accidents WHERE source_url = ?",
                        ("https://example.com/a0",))
    assert row["source_key"] == KEY
    assert row["status"] == "new"
    assert row["publish_date"] == "2026-09-29"
    # 通路 A 拿不到 HTML，留空而不是编一个
    assert row["raw_html"] == ""
    # 能被标准列吸收的字段走列；收不下的（如 pdf_urls）进 extra
    assert json.loads(row["extra"])["pdf_urls"] == ["https://example.com/a0.pdf"]


def test_通路B_返回值的记录落库(staging, monkeypatch):
    items = [
        RawAccidentText(title=f"事故{i}", raw_text="正文" * 30,
                        raw_html="<p>原始</p>", source_url=f"https://x.gov/{i}",
                        source_name=KEY, fetch_time="2026-09-30T01:00:00+00:00",
                        extracted_city="南京市", extracted_deaths=2,
                        confidence_overall=0.8)
        for i in range(2)
    ]
    _, stats = _run(lambda **kw: ReturnCollector(items, **kw), monkeypatch)

    assert _raw_count() == 2
    assert stats["per_source"][KEY]["ok"] is True
    row = sdb.query_one("SELECT * FROM raw_accidents WHERE source_url = ?",
                        ("https://x.gov/0",))
    assert row["raw_html"] == "<p>原始</p>"
    assert row["ext_city"] == "南京市"
    assert row["ext_deaths"] == 2
    assert row["fingerprint"], "通路 B 该算出事故指纹"
    assert row["confidence_overall"] == 0.8


def test_通路C_同一URL只落一条(staging, monkeypatch):
    """safehoo 的真实形态：store 与返回值都给出同一条，靠 UNIQUE 拦住。"""
    recs = [_record(f"https://safehoo.com/n{i}") for i in range(4)]
    _, stats = _run(lambda **kw: BothCollector(recs, **kw), monkeypatch)

    assert _raw_count() == 4, "8 条产出（4+4）里该有 4 条被 UNIQUE(url_hash) 拦下"
    assert stats["collected"] == 8
    assert stats["inserted"] == 4
    assert stats["dup"] == 4


def test_collect返回stats字典不被当成列表(staging, monkeypatch):
    """类型标注写的是 list，实际返回 dict —— 不能因此崩。"""
    recs = [_record("https://example.com/z")]
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)
    assert stats["per_source"][KEY]["ok"] is True
    assert _raw_count() == 1


# ══════════════════════════════════════════════════════════════
# 隔离与清理
# ══════════════════════════════════════════════════════════════


def test_data_dir落在临时目录且跑完即删(staging, monkeypatch):
    recs = [_record("https://example.com/1")]
    _, _ = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    seen = Path(StoreCollector.seen_data_dir)
    assert "collect_" in str(seen), f"临时目录名不对: {seen}"
    assert not seen.exists(), "任务结束该把临时目录删干净（PDF 会堆在里面）"


def test_不往settings_DATA_DIR写下源目录(staging, monkeypatch, tmp_path):
    from construction_agent.config import settings
    target = Path(settings.DATA_DIR) / KEY
    before = target.exists()
    recs = [_record("https://example.com/2")]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)
    assert target.exists() == before, "data/<源名>/ 不该因为一次采集而出现"


# ══════════════════════════════════════════════════════════════
# 任务状态与统计
# ══════════════════════════════════════════════════════════════


def test_任务状态流转与stats落库(staging, monkeypatch):
    recs = [_record(f"https://example.com/t{i}") for i in range(2)]
    task_id, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    task = executor.get_task(task_id)
    assert task["status"] == "done"
    assert task["started_at"] and task["finished_at"]
    assert task["source_keys"] == [KEY]
    assert task["stats"]["per_source"][KEY]["inserted"] == 2
    assert stats["failed_sources"] == []


def test_单源失败不影响任务完成(staging, monkeypatch):
    def boom(data_dir=None):
        raise RuntimeError("站点拒绝连接")

    _patch_build(monkeypatch, boom)
    task_id = executor.create_task([KEY])
    stats = executor.run_task(task_id)

    assert stats["per_source"][KEY]["ok"] is False
    assert "站点拒绝连接" in stats["per_source"][KEY]["error"]
    # 全部源都失败才叫 failed
    assert executor.get_task(task_id)["status"] == "failed"
    # 失败也要写回健康度，否则页面还显示上次的成功
    row = sdb.query_one("SELECT * FROM collect_sources WHERE source_key = ?", (KEY,))
    assert row["last_run_ok"] == 0
    assert "站点拒绝连接" in row["last_error"]


def test_collect_sources累计与最近状态(staging, monkeypatch):
    recs = [_record(f"https://example.com/c{i}") for i in range(3)]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)
    _run(lambda **kw: StoreCollector([_record("https://example.com/c9")],
                                     **kw), monkeypatch)

    row = sdb.query_one("SELECT * FROM collect_sources WHERE source_key = ?", (KEY,))
    assert row["total_raw"] == 4, "total_raw 该累加（3 + 1），不是覆盖"
    assert row["last_run_ok"] == 1
    assert row["last_error"] is None
    assert row["last_run_at"]


def test_跨任务同URL被拦(staging, monkeypatch):
    recs = [_record("https://example.com/same")]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    assert _raw_count() == 1
    assert stats["inserted"] == 0 and stats["dup"] == 1


# ══════════════════════════════════════════════════════════════
# 附件登记
# ══════════════════════════════════════════════════════════════


def test_附件URL登记成待处理资产(staging, monkeypatch):
    """采集期只登记 URL，不下载——下载与 OCR 是阶段②的事。

    省级站的通报常常把正文挂在 PDF 附件上，页面本身只有一行标题。不登记
    就等于把正文丢了。
    """
    recs = [_record(f"https://example.com/a{i}") for i in range(2)]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    rows = sdb.query("SELECT raw_id, asset_type, url, ocr_status "
                     "FROM raw_accident_assets ORDER BY raw_id")
    assert len(rows) == 2
    assert all(r["asset_type"] == "pdf" for r in rows)
    assert all(r["ocr_status"] == "pending" for r in rows), "等着阶段②去下载 OCR"
    assert all(r["url"].endswith(".pdf") for r in rows)


def test_重跑不重复登记附件(staging, monkeypatch):
    """第二次采集同一条被 UNIQUE 拦下，附件也不该再登记一遍。"""
    recs = [_record("https://example.com/same")]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    n = sdb.scalar("SELECT COUNT(*) FROM raw_accident_assets", default=0)
    assert n == 1, f"附件被重复登记了 {n} 次"


def test_附件键名与元素形状都认(staging, monkeypatch):
    """pdf_urls / image_urls / attachments 三种键、字符串与字典两种元素。"""
    recs = [_record("https://example.com/multi",
                    image_urls="https://example.com/m.png",
                    attachments=[{"url": "https://example.com/doc.docx"},
                                 "https://example.com/raw.pdf"])]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    rows = sdb.query("SELECT asset_type, url FROM raw_accident_assets ORDER BY id")
    assert [(r["asset_type"], r["url"]) for r in rows] == [
        ("pdf", "https://example.com/multi.pdf"),
        ("image", "https://example.com/m.png"),
        ("attachment", "https://example.com/doc.docx"),
        ("attachment", "https://example.com/raw.pdf"),
    ]


def test_认不出的键不当附件(staging, monkeypatch):
    """宁可不登记，也不要把普通链接当附件去下载。"""
    recs = [_record("https://example.com/x", pdf_urls=[],
                    related_links=["https://example.com/other"])]
    _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    assert sdb.scalar("SELECT COUNT(*) FROM raw_accident_assets", default=0) == 0


def test_没有URL的记录直接丢(staging, monkeypatch):
    """`raw_id` / `url_hash` 都由 URL 算出，没有 URL 就没有主键。"""
    recs = [_record("https://example.com/ok"),
            {"id": "nourl", "title": "无 URL 的记录", "content": "正文"}]
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch)

    assert _raw_count() == 1
    assert stats["collected"] == 1, "丢掉的不算收集数，否则 dup 会算错"


def test_limit只截收集不截采集(staging, monkeypatch):
    recs = [_record(f"https://example.com/l{i}") for i in range(10)]
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch, limit=3)

    assert _raw_count() == 3
    # 源本身没有 limit 概念（只有 safehoo 有 max_items），所以它照跑 10 条，
    # 是收集层把后 7 条丢了。这个数字就是那条限制的证据。
    assert stats["per_source"][KEY]["collected"] == 3


# ══════════════════════════════════════════════════════════════
# 采集后过滤（since / until / keywords）
# ══════════════════════════════════════════════════════════════


def test_keywords过滤掉不相关文本(staging, monkeypatch):
    recs = [
        _record("https://example.com/k1", title="某工地坍塌事故"),
        _record("https://example.com/k2", title="某工地坠落事故"),
        _record("https://example.com/k3", title="脚手架坍塌致2死"),
    ]
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch,
                    keywords="坍塌")

    assert _raw_count() == 2
    assert stats["filtered"] == 1
    assert stats["per_source"][KEY]["collected"] == 3, "collected 含被滤掉的"


def test_日期过滤但保留提不出日期的记录(staging, monkeypatch):
    recs = [
        _record("https://example.com/d1", publish_date="2026-05-01"),
        _record("https://example.com/d2", publish_date="2025-01-01"),
        _record("https://example.com/d3", publish_date=""),      # 提不出日期
    ]
    _, stats = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch,
                    since="2026-01-01")

    assert _raw_count() == 2, "2025 那条该被拦，无日期的必须留下"
    assert stats["filtered"] == 1


def test_过滤发生在limit之前(staging, monkeypatch):
    """被条件拦下的条目不占 limit 名额，否则上限会被无关文本吃光。"""
    recs = ([_record(f"https://example.com/n{i}", title="防汛演练")
             for i in range(6)]
            + [_record(f"https://example.com/h{i}", title="高处坠落事故")
               for i in range(4)])
    _, _ = _run(lambda **kw: StoreCollector(recs, **kw), monkeypatch,
                keywords="坠落", limit=3)

    rows = sdb.query("SELECT title FROM raw_accidents")
    assert len(rows) == 3
    assert all("坠落" in r["title"] for r in rows)


def test_params归一化(staging):
    p = executor.normalize_params({"keywords": "坍塌， 坠落 ,", "limit": "20",
                                   "since": "2026-01-01T00:00:00"})
    assert p["keywords"] == ["坍塌", "坠落"]
    assert p["limit"] == 20
    assert p["since"] == "2026-01-01"

    p = executor.normalize_params({"keywords": None, "limit": "abc"})
    assert p["keywords"] == [] and p["limit"] is None


# ══════════════════════════════════════════════════════════════
# 取消与中断恢复
# ══════════════════════════════════════════════════════════════


def test_取消置中间态且不被done盖掉(staging, monkeypatch):
    recs = [_record("https://example.com/x")]
    _patch_build(monkeypatch, lambda **kw: StoreCollector(recs, **kw))
    task_id = executor.create_task([KEY])

    assert executor.cancel_task(task_id) is True
    assert executor.get_task(task_id)["status"] == "cancelling"

    executor.run_task(task_id)
    assert executor.get_task(task_id)["status"] == "cancelled"

    # 终态任务不可再取消
    assert executor.cancel_task(task_id) is False


def test_已取消的任务不启动新源(staging, monkeypatch):
    started = []

    def factory(data_dir=None):
        started.append(1)
        return StoreCollector([], data_dir=data_dir)

    _patch_build(monkeypatch, factory)
    task_id = executor.create_task([KEY])
    executor.cancel_task(task_id)
    executor.run_task(task_id)
    assert started == [], "排队期间被取消的任务不该再去打站点"


def test_reconcile把僵死任务标为中断(staging):
    with sdb.write_tx() as con:
        con.execute(
            "INSERT INTO collect_tasks (task_id, source_keys, status, created_at) "
            "VALUES ('stale1', '[\"safehoo\"]', 'running', '2026-01-01T00:00:00')")
        con.execute(
            "INSERT INTO collect_tasks (task_id, source_keys, status, created_at) "
            "VALUES ('done1', '[\"safehoo\"]', 'done', '2026-01-01T00:00:00')")

    assert executor.reconcile_stale_tasks() == 1
    assert executor.get_task("stale1")["status"] == "failed"
    assert "中断" in executor.get_task("stale1")["error"]
    assert executor.get_task("done1")["status"] == "done", "已完成的不许被改"


# ══════════════════════════════════════════════════════════════
# 源登记表
# ══════════════════════════════════════════════════════════════


def test_24个源全部登记且分组齐全(staging):
    assert len(registry.SOURCES) == 24
    groups = {k: len(v) for k, v in registry.by_group().items()}
    assert groups == {"province": 18, "national": 2, "media": 3, "open_data": 1}


def test_未登记的源直接报错(staging):
    with pytest.raises(KeyError):
        executor.create_task(["not_a_source"])
    with pytest.raises(KeyError):
        registry.get("not_a_source")


def test_sync_to_db幂等且不动运行期状态(staging):
    with sdb.write_tx() as con:
        assert registry.sync_to_db(con) == 24
    with sdb.write_tx() as con:
        con.execute("UPDATE collect_sources SET is_active = 0, total_raw = 5 "
                    "WHERE source_key = ?", (KEY,))
    with sdb.write_tx() as con:
        registry.sync_to_db(con)
    row = sdb.query_one("SELECT * FROM collect_sources WHERE source_key = ?", (KEY,))
    assert row["is_active"] == 0 and row["total_raw"] == 5
    assert row["source_group"] == "media", "元信息该被刷新"
