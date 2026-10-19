"""M3 暂存审核的回归测试（告警规则 + 队列 + 单条审核 + 并入/回滚）。

**为什么全部落在临时库上**：并入是唯一会写最终库的操作，而最终库里装着
2,623 条已发表的论文数据。这里的一次误写就是一次数据事故，所以测试一律
用 `tmp_path` 下自建的两个库，绝不碰 `data/caiems_v2.db`。

最终库的表结构直接取 `scripts/create_db_v2.py` 的 `SCHEMA`——那份已经是
「以真库为准」校准过的定义，测试跟着它走，建表脚本再漂移时这里会一起红。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from construction_agent.review import promote as pm
from construction_agent.review import warnings as wn
from construction_agent.web import staging_db as sdb

ROOT = Path(__file__).resolve().parents[1]

# 建表定义的唯一来源在 scripts/ 下，不以包的形式 import（scripts 不是包）
_spec = importlib.util.spec_from_file_location(
    "create_db_v2", ROOT / "scripts" / "create_db_v2.py")
create_db_v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create_db_v2)

SOURCE_KEY = "safehoo"


# ══════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的工作库 + 最终库，都不在项目 data/ 下。"""
    staging = tmp_path / "staging.db"
    final = tmp_path / "final.db"
    monkeypatch.setenv("CAIEMS_STAGING_DB", str(staging))
    monkeypatch.setenv("CAIEMS_DB", str(final))

    sdb.init_db()
    con = sqlite3.connect(final)
    con.executescript(create_db_v2.SCHEMA)
    con.commit()
    con.close()

    with sdb.write_tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO collect_sources (source_key, display_name, "
            "source_group, track) VALUES (?,?,?,?)",
            (SOURCE_KEY, "安全管理网", "national", "network"))
    return tmp_path


@pytest.fixture
def client(env):
    from construction_agent.web.factory import create_app
    return create_app({"TESTING": True}).test_client()


def final_path(env) -> Path:
    return env / "final.db"


def seed(stage_id: str, *, causes=(("H1", "违章操作"),),
         review_status: str = "pending", with_raw: bool = True, **fields):
    """灌一条暂存记录。默认可并入（必填字段齐全、零阻断告警）。"""
    row = {
        "title": "某工地发生高处坠落事故致 2 人死亡",
        "date": "2024-05-06",
        "location": "江苏省",
        "city": "南京市",
        "accident_type": "高处坠落",
        "severity": "一般事故",
        "deaths": 2,
        "injuries": 0,
        "project_type": "房屋建筑",
        "report_type": "事故调查报告",
        "summary": "工人未系安全带，从脚手架坠落。",
        "text_type": "single",
        "relevance_verdict": "construction",
        "relevance_confidence": "high",
    }
    row.update(fields)

    raw_id = "raw" + stage_id
    with sdb.write_tx() as con:
        if with_raw:
            con.execute(
                "INSERT INTO raw_accidents (raw_id, source_key, source_url, "
                "fetch_time, url_hash, title, content) VALUES (?,?,?,?,?,?,?)",
                (raw_id, SOURCE_KEY, f"https://example.com/{stage_id}",
                 "2026-10-01T10:00:00", hashlib.md5(raw_id.encode()).hexdigest(),
                 row["title"], "原文正文。" * 60))
        cols = ["stage_id", "raw_id", "created_at", "review_status",
                "split_index"] + list(row.keys())
        vals = [stage_id, raw_id if with_raw else None,
                "2026-10-01T10:00:00", review_status, 0] + list(row.values())
        con.execute(
            f"INSERT INTO staging_accidents ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})", vals)
        for i, (code, name) in enumerate(causes or ()):
            con.execute(
                "INSERT INTO staging_causes (stage_id, code, name, evidence, "
                "inference, evidence_quality, sort_order) VALUES (?,?,?,?,?,?,?)",
                (stage_id, code, name, "原文提到" + name, "推断：" + name,
                 "direct_quote", i + 1))
    return stage_id


def seed_clean(stage_id: str, **kw):
    return seed(stage_id, **kw)


# ══════════════════════════════════════════════════════════════════════
# A. 告警规则（纯函数）
# ══════════════════════════════════════════════════════════════════════


def _warn_row(**kw):
    base = {"title": "某工地坠落事故", "deaths": 2, "injuries": 0,
            "accident_type": "高处坠落", "text_type": "single",
            "cause_count": 3, "unknown_code_count": 0, "quality_diff": 0,
            "raw_len": 500}
    base.update(kw)
    return base


def test_零告警的干净行():
    assert wn.compute(_warn_row()) == []


@pytest.mark.parametrize("field,value,code", [
    ("title", "", "empty_title"),
    ("title", "短", "empty_title"),
    ("deaths", 0, "no_death"),
    ("cause_count", 0, "no_causes"),
    ("unknown_code_count", 1, "cause_not_in_taxonomy"),
    ("raw_len", 30, "short_text"),
])
def test_单条规则各自命中(field, value, code):
    row = _warn_row(**{field: value})
    if field == "deaths":
        row["injuries"] = 0        # 只有死亡与受伤都空才算 no_death
    assert code in wn.compute(row)


def test_受伤不为零时不报无伤亡():
    assert "no_death" not in wn.compute(_warn_row(deaths=0, injuries=3))


def test_质量漂移按比例判定():
    # 3 条里 1 条不一致（1/3）不算；2 条不一致（2/3）才算
    assert "quality_shift" not in wn.compute(_warn_row(quality_diff=1))
    assert "quality_shift" in wn.compute(_warn_row(quality_diff=2))


def test_体裁矛盾只认统计汇总():
    # aggregate + 有类型 + 有伤亡 → 命中
    assert "type_mismatch" in wn.compute(
        _warn_row(text_type="aggregate"))
    # 通报类常带完整事故描述，算进来只会制造大批误报
    assert "type_mismatch" not in wn.compute(
        _warn_row(text_type="regulatory"))


def test_阻断级告警只有两个():
    # 这两个直接对应"并进去就是坏数据"，其余都是提醒
    assert wn.BLOCKING == frozenset({"empty_title", "no_causes"})


# ══════════════════════════════════════════════════════════════════════
# B. 队列与漏斗
# ══════════════════════════════════════════════════════════════════════


def test_工作库未建时优雅降级(tmp_path, monkeypatch):
    """库文件都没有时页面要能开，返回 available=false 而不是 500。"""
    monkeypatch.setenv("CAIEMS_STAGING_DB", str(tmp_path / "none.db"))
    monkeypatch.setenv("CAIEMS_DB", str(tmp_path / "final.db"))
    from construction_agent.web.factory import create_app
    c = create_app({"TESTING": True}).test_client()
    body = c.get("/api/review/queue").get_json()
    assert body["available"] is False and body["hint"]


def test_队列counts与直接SQL聚合逐项一致(client, env):
    for i, st in enumerate(["pending", "pending", "approved", "rejected"]):
        seed(f"clean{i}", review_status=st)
    seed("warn0", title="", review_status="pending")      # empty_title
    seed("warn1", causes=(), review_status="pending")     # no_causes

    counts = client.get("/api/review/queue").get_json()["counts"]

    for st in ("pending", "approved", "edited", "rejected"):
        direct = sdb.scalar(
            "SELECT COUNT(*) FROM staging_accidents WHERE review_status = ?",
            (st,), default=0)
        assert counts[st] == direct, st

    assert counts["total"] == sdb.scalar(
        "SELECT COUNT(*) FROM staging_accidents", default=0)
    assert counts["with_warnings"] == 2
    assert counts["clean"] == counts["total"] - counts["with_warnings"]


def test_告警筛选只留命中的(client, env):
    seed("clean0")
    seed("warn0", causes=())
    body = client.get("/api/review/queue?review_status=all&warnings=none").get_json()
    assert [r["stage_id"] for r in body["data"]] == ["clean0"]
    body = client.get("/api/review/queue?review_status=all&warnings=no_causes").get_json()
    assert [r["stage_id"] for r in body["data"]] == ["warn0"]


def test_by_warning计数与逐条计算相符(client, env):
    seed("a", causes=())
    seed("b", causes=(), title="")
    seed("c")
    body = client.get("/api/review/queue").get_json()
    by = {w["code"]: w["count"] for w in body["by_warning"]}
    assert by.get("no_causes") == 2
    assert by.get("empty_title") == 1

    # 与独立算一遍的结果对齐
    rows = sdb.query("SELECT stage_id, title FROM staging_accidents")
    manual = sum(1 for r in rows if not (r["title"] or "").strip())
    assert by.get("empty_title", 0) == manual


def test_分页与页面大小(client, env):
    for i in range(7):
        seed(f"s{i}")
    body = client.get("/api/review/queue?size=3&page=2").get_json()
    assert len(body["data"]) == 3
    assert body["meta"] == {"total": 7, "page": 2, "size": 3, "pages": 3,
                            "scan_truncated": False}


def test_漏斗四格(client, env):
    seed("a")
    seed("b")
    d = client.get("/api/review/stats").get_json()["data"]
    assert d["available"] is True
    assert [f["stage"] for f in d["funnel"]] == \
        ["暂存总数", "零告警", "审核通过", "已并入"]
    assert d["funnel"][0]["count"] == 2


# ══════════════════════════════════════════════════════════════════════
# C. 单条详情
# ══════════════════════════════════════════════════════════════════════


def test_可编辑白名单正好等于S3字段段(env):
    """白名单少一列 = 那列再也改不了；多一列 = 可能改到不该改的列。

    真库 58 列 = 31 个 S3 字段 + 27 个结构/闸门/过程/审核列。这条断言会在
    schema 加列而白名单忘了跟的时候失败。
    """
    from construction_agent.web.review import EDITABLE_FIELDS
    cols = {r["name"] for r in sdb.query("PRAGMA table_info(staging_accidents)")}
    assert len(cols) == 58
    assert set(EDITABLE_FIELDS) <= cols
    assert len(cols) - len(EDITABLE_FIELDS) == 27


def test_详情返回全部可编辑字段(client, env):
    seed("a")
    d = client.get("/api/review/items/a").get_json()["data"]
    assert set(d["editable"]) == set(d["fields"].keys())
    # 字段值真的取到了，而不是一片 None（初稿正是栽在这上面）
    assert d["fields"]["title"].startswith("某工地")
    assert d["fields"]["deaths"] == 2
    assert d["fields"]["city"] == "南京市"
    assert d["raw"]["content"]
    assert [c["code"] for c in d["causes"]] == ["H1"]
    assert d["causes"][0]["evidence_quality_label"] == "直引"


def test_详情下发编码列的中文名对照表(client, env):
    """审核员要判的是「S3 判的 `WT04` 对不对」，只给码等于让人先背下 11 个 WT 码。

    接口一次下发五个编码列（`accidents_query.ENUM_COLUMNS`）的对照表，键必须齐：
    少给一列，那一列就退回裸码，而**页面上没有任何迹象**——不报错、不空白，只是
    审核员看不懂。详情页早就翻了（`detail.py`），此前审核页没翻。
    """
    from construction_agent.web import accidents_query

    seed("a", work_task="WT04", material_involved="MAT02、MAT07")
    d = client.get("/api/review/items/a").get_json()["data"]
    assert set(d["enum_labels"]) == set(accidents_query.ENUM_COLUMNS)

    con = sqlite3.connect(final_path(env))
    con.executemany(
        "INSERT INTO enum_definitions (enum_type,code,name,definition,is_active)"
        " VALUES (?,?,?,?,?)",
        [("work_task", "WT04", "模板与脚手架工程", "模板、支撑、脚手架作业", 1),
         ("material_involved", "MAT02", "钢筋", "钢筋及制品", 1),
         # 停用的枚举不下发——它是迁移遗留，不该出现在审核员的候选里
         ("material_involved", "MAT07", "旧版材料类", "停用", 0)])
    con.commit()
    con.close()
    accidents_query.reset_cache()   # 枚举名按库指纹缓存，灌完数据要清

    d = client.get("/api/review/items/a").get_json()["data"]
    assert d["enum_labels"]["work_task"]["WT04"] == "模板与脚手架工程"
    assert d["enum_labels"]["material_involved"]["MAT02"] == "钢筋"
    assert "MAT07" not in d["enum_labels"]["material_involved"]
    # 没灌数据的枚举给空表而不是缺键——前端按「有没有这张表」判断这一列要不要提示
    assert d["enum_labels"]["worker_type"] == {}


def test_详情404(client, env):
    assert client.get("/api/review/items/nope").status_code == 404


def test_详情标出证据等级不符(client, env):
    seed("a")
    with sdb.write_tx() as con:
        con.execute("UPDATE staging_causes SET evidence_quality_llm = 'weak_infer' "
                    "WHERE stage_id = 'a'")
    d = client.get("/api/review/items/a").get_json()["data"]
    assert d["causes"][0]["mismatch"] is True
    assert d["causes"][0]["evidence_quality"] == "direct_quote"


# ══════════════════════════════════════════════════════════════════════
# D. 单条审核
# ══════════════════════════════════════════════════════════════════════


def test_通过(client, env):
    seed("a")
    r = client.post("/api/review/items/a", json={"action": "approve"}).get_json()
    assert r["data"]["review_status"] == "approved"
    assert sdb.scalar("SELECT review_status FROM staging_accidents "
                      "WHERE stage_id='a'") == "approved"


def test_退回必须写原因(client, env):
    seed("a")
    r = client.post("/api/review/items/a",
                    json={"action": "reject", "note": "  "})
    assert r.status_code == 400
    assert "原因" in r.get_json()["message"]
    assert sdb.scalar("SELECT review_status FROM staging_accidents "
                      "WHERE stage_id='a'") == "pending"


def test_退回带原因(client, env):
    seed("a")
    client.post("/api/review/items/a", json={"action": "reject", "note": "非施工"})
    row = sdb.query_one("SELECT review_status, review_note FROM staging_accidents "
                        "WHERE stage_id='a'")
    assert row["review_status"] == "rejected"
    assert row["review_note"] == "非施工"


def test_修改字段与致因树(client, env):
    seed("a", causes=(("H1", "违章操作"), ("M2", "制度缺失")))
    r = client.post("/api/review/items/a", json={
        "action": "edit", "note": "订正伤亡",
        "edits": {
            "deaths": "3", "city": "苏州市",
            "causes": [
                {"code": "M2", "name": "制度缺失", "evidence": "e",
                 "inference": "i", "evidence_quality": "weak_infer"},
            ],
        },
    }).get_json()
    assert r["data"]["review_status"] == "edited"
    row = sdb.query_one("SELECT deaths, city, review_status FROM staging_accidents "
                        "WHERE stage_id='a'")
    assert row["deaths"] == 3 and row["city"] == "苏州市"
    assert row["review_status"] == "edited"

    causes = sdb.query("SELECT code, sort_order FROM staging_causes "
                       "WHERE stage_id='a' ORDER BY sort_order")
    assert [c["code"] for c in causes] == ["M2"]
    # 人工增删后重排为连续序号，不留空洞
    assert [c["sort_order"] for c in causes] == [1]


def test_修改留空洞时会被重排(client, env):
    seed("a", causes=(("H1", "a"), ("H2", "b"), ("H3", "c")))
    client.post("/api/review/items/a", json={
        "action": "edit",
        "edits": {"causes": [
            {"code": "H3", "name": "c"}, {"code": "H1", "name": "a"}]},
    })
    orders = [r["sort_order"] for r in sdb.query(
        "SELECT sort_order FROM staging_causes WHERE stage_id='a' "
        "ORDER BY sort_order")]
    assert orders == [1, 2]


def test_空值写成NULL而不是空串(client, env):
    seed("a")
    client.post("/api/review/items/a", json={
        "action": "edit", "edits": {"weather": "  "}})
    assert sdb.scalar("SELECT weather FROM staging_accidents "
                      "WHERE stage_id='a'") is None


def test_数字字段脏输入不炸(client, env):
    seed("a")
    client.post("/api/review/items/a", json={
        "action": "edit", "edits": {"deaths": "两人"}})
    assert sdb.scalar("SELECT deaths FROM staging_accidents "
                      "WHERE stage_id='a'") is None


def test_没有改动时报错(client, env):
    seed("a")
    r = client.post("/api/review/items/a", json={"action": "edit", "edits": {}})
    assert r.status_code == 400


def test_未知action被拒(client, env):
    seed("a")
    assert client.post("/api/review/items/a",
                       json={"action": "delete"}).status_code == 400


def test_不可编辑的列改不动(client, env):
    seed("a")
    client.post("/api/review/items/a", json={
        "action": "edit",
        "edits": {"stage_id": "hacked", "review_status": "approved",
                  "text_type": "other"}})
    row = sdb.query_one("SELECT stage_id, text_type FROM staging_accidents "
                        "WHERE stage_id='a'")
    assert row["stage_id"] == "a" and row["text_type"] == "single"


# ══════════════════════════════════════════════════════════════════════
# E. 批量通过
# ══════════════════════════════════════════════════════════════════════


def test_批量通过只放行零告警(client, env):
    seed("clean0")
    seed("clean1")
    seed("bad", title="")        # empty_title 阻断
    seed("bad2", causes=())      # no_causes 阻断
    r = client.post("/api/review/batch-approve",
                    json={"stage_ids": ["clean0", "clean1", "bad", "bad2"]})
    d = r.get_json()["data"]
    assert d["approved"] == 2
    assert sorted(d["stage_ids"]) == ["clean0", "clean1"]
    assert {s["stage_id"] for s in d["skipped"]} == {"bad", "bad2"}
    # 跳过的必须说明原因，不能静默
    for s in d["skipped"]:
        assert s["reason"]
    assert sdb.scalar("SELECT review_status FROM staging_accidents "
                      "WHERE stage_id='bad'") == "pending"


def test_批量通过跳过不存在的记录(client, env):
    seed("a")
    d = client.post("/api/review/batch-approve",
                    json={"stage_ids": ["a", "ghost"]}).get_json()["data"]
    assert d["approved"] == 1
    assert d["skipped"][0]["stage_id"] == "ghost"


def test_批量通过空数组被拒(client, env):
    assert client.post("/api/review/batch-approve",
                       json={"stage_ids": []}).status_code == 400


# ══════════════════════════════════════════════════════════════════════
# F. 并入
# ══════════════════════════════════════════════════════════════════════


def _approve(client, *ids):
    client.post("/api/review/batch-approve", json={"stage_ids": list(ids)})


def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_预演不写任何东西(client, env):
    """验收第 3 条：dry_run 前后最终库文件哈希不变。"""
    seed("a")
    _approve(client, "a")
    before = _file_hash(final_path(env))

    d = client.post("/api/review/promote",
                    json={"stage_ids": ["a"], "dry_run": True}).get_json()["data"]

    assert d["mode"] == "dry_run" and d["ready"] is True
    assert d["plan"]["accidents_insert"] == 1
    assert d["plan"]["causes_total"] == 1
    assert d["confirm_token"]
    assert _file_hash(final_path(env)) == before
    con = sqlite3.connect(final_path(env))
    assert con.execute("SELECT COUNT(*) FROM accidents").fetchone()[0] == 0
    con.close()


def test_没带令牌的并入被拒(client, env):
    """验收第 4 条：不带 dry_run 的 promote 缺 confirm_token → 拒绝。"""
    seed("a")
    _approve(client, "a")
    r = client.post("/api/review/promote", json={"stage_ids": ["a"]})
    assert r.status_code == 409
    assert "令牌" in r.get_json()["message"]
    con = sqlite3.connect(final_path(env))
    assert con.execute("SELECT COUNT(*) FROM accidents").fetchone()[0] == 0
    con.close()


def test_令牌与选择集绑定(client, env):
    """改了选择却沿用旧令牌 = 往论文数据里混入没预览过的东西，必须拦住。"""
    seed("a")
    seed("b")
    _approve(client, "a", "b")
    token = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]

    r = client.post("/api/review/promote", json={
        "stage_ids": ["a", "b"], "dry_run": False, "confirm_token": token})
    assert r.status_code == 409
    assert "选择集" in r.get_json()["message"]


def test_令牌一次性(client, env):
    seed("a")
    _approve(client, "a")
    token = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]

    ok = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": False, "confirm_token": token})
    assert ok.status_code == 200

    again = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": False, "confirm_token": token})
    assert again.status_code == 409


def test_未通过审核的不能并入(client, env):
    seed("a")       # 还是 pending
    d = client.post("/api/review/promote",
                    json={"stage_ids": ["a"], "dry_run": True}).get_json()["data"]
    assert d["ready"] is False
    assert "审核状态" in d["plan"]["conflicts"][0]["reason"]


def test_必填为空时列出冲突而不是整批失败(client, env):
    seed("a")
    seed("b", summary="")
    _approve(client, "a", "b")
    d = client.post("/api/review/promote",
                    json={"stage_ids": ["a", "b"], "dry_run": True}).get_json()["data"]
    assert d["ready"] is False
    assert len(d["plan"]["conflicts"]) == 1
    assert d["plan"]["conflicts"][0]["stage_id"] == "b"
    assert "摘要" in d["plan"]["conflicts"][0]["reason"]
    # a 本身没问题，不该被 b 连坐
    assert d["plan"]["accidents_insert"] == 1


def test_同批内重复指向同一事故会被拦(client, env):
    seed("a")
    seed("b")
    # 同一 URL 被抓两次 → 两条暂存行映射到同一个 accident_id。
    # split_index 也得跟着错开，否则先撞上 (raw_id, split_index) 的唯一约束
    with sdb.write_tx() as con:
        con.execute("UPDATE staging_accidents SET raw_id = "
                    "(SELECT raw_id FROM staging_accidents WHERE stage_id='a'), "
                    "split_index = 1 WHERE stage_id='b'")
    _approve(client, "a", "b")
    d = client.post("/api/review/promote",
                    json={"stage_ids": ["a", "b"], "dry_run": True}).get_json()["data"]
    assert d["ready"] is False
    assert any("互相覆盖" in c["reason"] for c in d["plan"]["conflicts"])


def test_正常并入写入两表并留痕(client, env):
    seed("a", causes=(("H1", "违章操作"), ("M2", "制度缺失")))
    _approve(client, "a")

    token = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]
    d = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": False,
        "confirm_token": token}).get_json()["data"]

    assert d["mode"] == "executed"
    assert d["promoted"] == 1 and d["causes_written"] == 2
    assert Path(d["backup"]).exists()

    con = sqlite3.connect(final_path(env))
    con.row_factory = sqlite3.Row
    acc = con.execute("SELECT * FROM accidents").fetchone()
    assert acc["id"] == "rawa"
    assert acc["province"] == "江苏省"            # 改名映射：location → province
    assert acc["classification_verdict"] == "construction"
    assert acc["pipeline_version"] == pm.PIPELINE_VERSION
    assert acc["raw_text"]
    causes = con.execute("SELECT code FROM caics_causes ORDER BY sort_order").fetchall()
    con.close()
    assert [c["code"] for c in causes] == ["H1", "M2"]

    hist = client.get("/api/review/history").get_json()["data"]
    assert hist[0]["promoted"] == 1 and hist[0]["rolled_back_at"] is None


def test_重复并入同一事故不产生重复编码(client, env):
    """历史教训：主表 OR REPLACE 而子表裸 INSERT，重跑一次多一批重复行。"""
    seed("a", causes=(("H1", "违章操作"),))
    for _ in range(2):
        _approve(client, "a")
        token = client.post("/api/review/promote", json={
            "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]
        client.post("/api/review/promote", json={
            "stage_ids": ["a"], "dry_run": False, "confirm_token": token})

    con = sqlite3.connect(final_path(env))
    assert con.execute("SELECT COUNT(*) FROM accidents").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM caics_causes").fetchone()[0] == 1
    con.close()


def test_并入时外键开关是打开的(client, env):
    """1,041 行孤儿的成因就是「表声明了外键 + 连接没开 PRAGMA」。"""
    con = pm._connect_writable(final_path(env))
    try:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        con.close()


def test_并入失败时最终库保持原样(client, env):
    """规则 1：主表子表同事务。子表写入失败时，主表那行不能留下。

    用触发器注入失败，而不是 patch `sqlite3.Connection.executemany`——
    后者是 C 扩展里的不可变类型，属性根本改不了。
    """
    seed("a")
    _approve(client, "a")

    con = sqlite3.connect(final_path(env))
    con.executescript(
        "CREATE TRIGGER boom BEFORE INSERT ON caics_causes "
        "BEGIN SELECT RAISE(ABORT, '注入的失败'); END;")
    con.commit()
    con.close()

    token = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]
    r = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": False, "confirm_token": token})
    assert r.status_code == 409
    assert "回滚" in r.get_json()["message"]

    con = sqlite3.connect(final_path(env))
    assert con.execute("SELECT COUNT(*) FROM accidents").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM caics_causes").fetchone()[0] == 0
    con.close()


# ══════════════════════════════════════════════════════════════════════
# G. 回滚
# ══════════════════════════════════════════════════════════════════════


def test_回滚撤销最近一次并入(client, env):
    seed("a", causes=(("H1", "违章操作"),))
    _approve(client, "a")
    token = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]
    client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": False, "confirm_token": token})

    # §2.5.92：回滚是 L2 写操作，必须指名 promote_log 里「生效中」那条的 id
    promote_id = client.get("/api/review/history").get_json()["data"][0]["id"]
    d = client.post(
        "/api/review/rollback", json={"promote_id": promote_id}
    ).get_json()["data"]
    assert d["removed_accidents"] == 1 and d["removed_causes"] == 1

    con = sqlite3.connect(final_path(env))
    assert con.execute("SELECT COUNT(*) FROM accidents").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM caics_causes").fetchone()[0] == 0
    con.close()

    hist = client.get("/api/review/history").get_json()["data"]
    assert hist[0]["rolled_back_at"] is not None


def test_没有可回滚的并入时报错(client, env):
    r = client.post("/api/review/rollback", json={})
    assert r.status_code == 409
    assert "回滚" in r.get_json()["message"]


def test_回滚后已并入计数归零(client, env):
    seed("a")
    _approve(client, "a")
    token = client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": True}).get_json()["data"]["confirm_token"]
    client.post("/api/review/promote", json={
        "stage_ids": ["a"], "dry_run": False, "confirm_token": token})
    assert client.get("/api/review/stats").get_json()["data"]["funnel"][3]["count"] == 1

    promote_id = client.get("/api/review/history").get_json()["data"][0]["id"]
    client.post("/api/review/rollback", json={"promote_id": promote_id})
    assert client.get("/api/review/stats").get_json()["data"]["funnel"][3]["count"] == 0


# ══════════════════════════════════════════════════════════════════════
# H. 页面
# ══════════════════════════════════════════════════════════════════════


def test_审核页可打开(client, env):
    r = client.get("/review")
    assert r.status_code == 200
    assert "暂存审核" in r.get_data(as_text=True)


def test_并入计划快照能存能读(env):
    """promote_plans 的 plan 列是 JSON 快照，中文要能原样往返。"""
    seed("a")
    ids = ["a"]
    result = pm.plan(ids)
    tok = pm.issue_token(ids, result)
    row = sdb.query_one("SELECT stage_ids, plan FROM promote_plans WHERE token = ?",
                        (tok["confirm_token"],))
    assert json.loads(row["stage_ids"]) == ids
    assert json.loads(row["plan"])["accidents_insert"] == 1
