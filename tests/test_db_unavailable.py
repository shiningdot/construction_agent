"""最终库不可用的来路分级（第四十一轮）。

## 这一轮在修什么

`web/db.py` 的前置检查是 `path.exists()`，而 **文件存在不等于库可用**：
0 字节文件是一个合法存在的文件（SQLite 打开它不报错，只当成空库），
「建了文件、一条 DDL 都没执行」的库更是如此。它们要到第一条 SQL 才
报 `no such table: accidents`，而 `api.py` 的 errorhandler 当时只挂了
`DatabaseNotFoundError`（= 库文件不存在），于是异常一路冒到 Flask 默认
500 页。

2026-10-01 实测（造七种最终库、各起干净子进程打 28 个端点，脚本见
`docs/web/01_在线采集.md` §2.5.41）：展示层 11 个端点里 **10 个直接崩**，
只有 `/api/health` 例外——而它自己判的 `path.exists()` 也是错的，空壳库
报 `ok: true` + `tables: {}`，顶栏徽标照常显示并报「缺 3 个索引」，把
用户引去跑 `add_indexes.py`（**指错方向**：那个库连 `accidents` 表都没有）。

修法三条：`db.diagnose()` 按来路分级、`api_bp` 的 errorhandler 扩到
`sqlite3.Error`、`/api/health` 的 `ok` 判据换成「必需表都在」。

## 测试分层

- A 组：`db.diagnose()` —— 纯函数，五条来路逐条断言
- B 组：`db.missing_tables()` —— 判据本身
- C 组：七种库形态 × 端到端 —— HTTP 层的行为
- D 组：`/api/health` 的 `ok` 判据
- E 组：契约 —— 既有字段名与错误码没被改名
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from construction_agent.web import accidents_query, db, overview, province
from construction_agent.web import staging_db as sdb
from construction_agent.web.factory import create_app

# 列齐到能让 `overview` / `accidents_query` 跑通的规模（复制自
# `test_web_m1.SCHEMA`，那边多一张 `image_analyses` 是给图片断言用的）
_FULL_DDL = """
CREATE TABLE accidents (
    id TEXT PRIMARY KEY, title TEXT, date TEXT, accident_time TEXT,
    province TEXT, city TEXT, district TEXT,
    accident_type TEXT, severity TEXT,
    deaths INTEGER, injuries INTEGER, trapped_count INTEGER,
    project_type TEXT, construction_phase TEXT, collapse_subtype TEXT,
    source_name TEXT, summary TEXT, direct_cause TEXT,
    work_task TEXT
);
CREATE TABLE caics_causes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT, code TEXT, name TEXT, evidence TEXT,
    inference TEXT, evidence_quality TEXT, sort_order INTEGER
);
CREATE TABLE enum_definitions (
    enum_type TEXT, code TEXT, name TEXT, is_active INTEGER DEFAULT 1
);
"""

#: 八种形态。前六种对外应当是「不可用」，后两种是正常态。
KINDS = ["missing", "emptyfile", "notables", "garbage", "partial",
         "partialcols", "emptytable", "full"]

#: 形态 → 期望的错误码。**这张表就是本轮的结论**：改前它只有两种取值
#: （不可用 → 503 `database_not_found`，或者干脆没有取值 → 500）。
EXPECTED_CODE = {
    "missing": "database_not_found",
    "emptyfile": "schema_mismatch",
    "notables": "schema_mismatch",
    "garbage": "not_a_database",
    "partial": "schema_mismatch",
    "partialcols": "schema_mismatch",
}


def _make_db(root: Path, kind: str) -> Path:
    """造一种形态的最终库，返回路径。"""
    path = root / f"{kind}.db"

    if kind == "missing":
        return path                                  # 不创建

    if kind == "emptyfile":
        path.write_bytes(b"")
        return path

    if kind == "notables":
        sqlite3.connect(str(path)).close()           # 建文件，一条 DDL 都不执行
        return path

    if kind == "garbage":
        path.write_bytes(b"this is not a sqlite file\n" * 40)
        return path

    if kind == "partial":
        # 一张表都没建全：`REQUIRED_TABLES` 里另外两张缺失。accidents 的列
        # 是齐的——这样触发的是 `no such table`，与下一个场景分开
        con = sqlite3.connect(str(path))
        con.executescript(_FULL_DDL.split("CREATE TABLE caics_causes")[0])
        con.commit()
        con.close()
        return path

    if kind == "partialcols":
        # 表名三张齐全、**列**不齐。`/api/health` 的判据只看表名，查不出这一种
        # ——这是判据的已知边界，不是本轮要修的（见文件末尾 TestHealthOkJudge
        # 的 `test_列不齐时health查不出来`），但 `/api/overview` 会撞上
        # `no such column`，必须被 errorhandler 接住
        con = sqlite3.connect(str(path))
        con.executescript(_FULL_DDL)
        con.execute("DROP TABLE accidents")
        con.execute("CREATE TABLE accidents (id TEXT PRIMARY KEY, title TEXT)")
        con.commit()
        con.close()
        return path

    # emptytable / full 共用完整 schema，区别只在后者有数据
    con = sqlite3.connect(str(path))
    con.executescript(_FULL_DDL)
    if kind == "full":
        con.execute("INSERT INTO accidents (id, title, date, province, severity,"
                    " deaths, injuries) VALUES ('a1','示例','2024-01-01',"
                    "'四川省','一般事故',1,2)")
        con.execute("INSERT INTO caics_causes (accident_id, code, name,"
                    " evidence_quality, sort_order) VALUES ('a1','M01','管理',"
                    "'direct_quote',1)")
        con.execute("INSERT INTO enum_definitions (enum_type, code, name)"
                    " VALUES ('severity','一般事故','一般事故')")
    con.commit()
    con.close()
    return path


@pytest.fixture
def point_at(tmp_path, monkeypatch):
    """把展示层指向指定的库。用法：`point_at(kind)` → 返回路径。"""
    monkeypatch.setenv(sdb.ENV_STAGING_DB, str(tmp_path / "no-staging.db"))

    def _point(kind: str) -> Path:
        path = _make_db(tmp_path, kind)
        monkeypatch.setenv(db.ENV_DB_PATH, str(path))
        # 三个缓存都按库指纹失效；不显式清会在换了库路径后拿到上一次的结果
        province.reset_cache()
        overview.reset_cache()
        accidents_query.reset_cache()
        return path

    return _point


# ══════════════════════════════════════════════════════════════
# A 组：diagnose 纯函数
# ══════════════════════════════════════════════════════════════


class TestDiagnose:

    def test_库文件不存在(self, tmp_path):
        info = db.diagnose(db.DatabaseNotFoundError(), tmp_path / "nope.db")
        assert info["error"] == "database_not_found"
        assert "不存在" in info["message"]
        assert db.ENV_DB_PATH in info["hint"], "提示里要给出下一步动作"

    def test_不是sqlite文件(self, tmp_path):
        info = db.diagnose(
            sqlite3.DatabaseError("file is not a database"), tmp_path / "x.db")
        assert info["error"] == "not_a_database"
        assert "SQLite" in info["message"]

    def test_缺表(self, tmp_path):
        info = db.diagnose(
            sqlite3.OperationalError("no such table: accidents"), tmp_path / "x.db")
        assert info["error"] == "schema_mismatch"
        assert "`accidents`" in info["message"], "缺哪张要原样带出来"
        assert "create_db_v2.py" in info["hint"]

    def test_缺列与缺表同一档(self, tmp_path):
        """SQLite 说的是两句不同的话，但**下一步动作相同**（跑建表脚本）。
        实测这一种不罕见：`partialcols` 场景就落在它上面。"""
        info = db.diagnose(
            sqlite3.OperationalError("no such column: deaths"), tmp_path / "x.db")
        assert info["error"] == "schema_mismatch"
        assert "`deaths`" in info["message"]
        assert "列" in info["message"], "消息要说清是列不是表"

    def test_表名保持原大小写(self, tmp_path):
        """判据要在大写保留的消息上搜——在小写串上搜会把表名也降级。"""
        info = db.diagnose(
            sqlite3.OperationalError("no such table: Caics_Causes"), tmp_path / "x.db")
        assert "`Caics_Causes`" in info["message"]

    def test_认不出的异常有兜底(self, tmp_path):
        info = db.diagnose(sqlite3.OperationalError("disk I/O error"), tmp_path / "x.db")
        assert info["error"] == "database_error"
        assert "disk I/O error" in info["message"], "原始消息要留着供排查"
        assert "integrity_check" in info["hint"]

    def test_五条来路互不相同(self, tmp_path):
        """**判据自检**：任意两条来路的 error 码都不能相同。"""
        p = tmp_path / "x.db"
        codes = [
            db.diagnose(db.DatabaseNotFoundError(), p)["error"],
            db.diagnose(sqlite3.DatabaseError("file is not a database"), p)["error"],
            db.diagnose(sqlite3.OperationalError("no such table: t"), p)["error"],
            db.diagnose(sqlite3.OperationalError("disk I/O error"), p)["error"],
        ]
        assert len(set(codes)) == len(codes), f"有来路合流了：{codes}"


# ══════════════════════════════════════════════════════════════
# B 组：missing_tables 判据
# ══════════════════════════════════════════════════════════════


class TestMissingTables:

    def test_空壳库三张全缺(self, tmp_path):
        path = _make_db(tmp_path, "notables")
        assert db.missing_tables(path) == set(db.REQUIRED_TABLES)

    def test_半建库只报缺的那几张(self, tmp_path):
        """`partial` 里有 `accidents`——它不该出现在缺失清单里。"""
        path = _make_db(tmp_path, "partial")
        assert db.missing_tables(path) == {"caics_causes", "enum_definitions"}

    def test_表齐全时为空集(self, tmp_path):
        assert db.missing_tables(_make_db(tmp_path, "emptytable")) == set()

    def test_读不动时让它抛而不吞(self, tmp_path):
        """「读不动」与「缺表」的下一步动作不同，不能合成一个返回值。"""
        with pytest.raises(sqlite3.Error):
            db.missing_tables(_make_db(tmp_path, "garbage"))

    def test_必需表只含内容表(self):
        """把九张全列上，刚建好还没导数据的库会被判成坏库。"""
        assert db.REQUIRED_TABLES == {"accidents", "caics_causes", "enum_definitions"}


# ══════════════════════════════════════════════════════════════
# C 组：七种形态 × 端到端
# ══════════════════════════════════════════════════════════════

#: 展示层的库端点。**不含** `/api/health`（判据单独测）与 `/api/images/stats`
#: （它对最终库的依赖是**条件性**的：只有工作库可用、且清单非空时才会去查
#: `accidents` 表，否则在读工作库那一步就早退了——见 `TestImagesStats`）。
LIB_ENDPOINTS = [
    "/api/overview", "/api/accidents", "/api/causes/framework",
    "/api/causes/distribution", "/api/causes/cooccurrence",
    "/api/causes/timeline", "/api/filters", "/api/province/audit",
]

UNUSABLE = list(EXPECTED_CODE)


class TestEndpointsOnUnusableDb:

    @pytest.mark.parametrize("kind", UNUSABLE)
    @pytest.mark.parametrize("endpoint", LIB_ENDPOINTS)
    def test_库不可用时绝不崩(self, point_at, kind, endpoint):
        """**这一轮真正要保证的是「不崩」，不是「一律 503」。**

        「库不可用」不是一个整体状态，是**逐端点**的：`partial`（缺
        `caics_causes`）下 `/api/filters` 只查 `accidents` 与
        `enum_definitions`，它照常工作是对的；`/api/causes/framework` 要那张
        表，它就 503。改前的毛病是第三种可能——未捕获的
        `sqlite3.OperationalError` 冒成 Flask 的 500 页。"""
        point_at(kind)
        r = create_app({"TESTING": True}).test_client().get(endpoint)
        assert r.status_code in (200, 503), f"{kind} {endpoint} → {r.status_code}"
        if r.status_code == 503:
            body = r.get_json()
            assert body["error"] and body["message"] and body["hint"], "空消息等于没说"

    def test_缺哪张表决定哪些端点还能用(self, point_at):
        """分档的用处在这里。`partial` 有 `accidents` 与 `enum_definitions`、
        缺 `caics_causes`——所以「只要前两张」的端点照常工作，「要致因表」的
        端点明确说缺了什么，而不是一起哑掉或一起崩。

        **一个端点的数据依赖不看它叫什么名字，看它的 SQL**：这一条最初写的是
        「`/api/accidents` 只查 accidents，它该是 200」，实测是 503——因为
        `accidents_query.LIST_COLUMNS`（`:103`）里挂着
        `(SELECT COUNT(*) FROM caics_causes ...) AS cause_count`，列表页每行
        都要显示编码条数。真正只查 `accidents` + `enum_definitions` 的是
        `/api/filters`（`:152` 读 `enum_definitions` 的缺省枚举值）。"""
        point_at("partial")
        c = create_app({"TESTING": True}).test_client()

        assert c.get("/api/filters").status_code == 200, "它只查 accidents 与 enum_definitions"
        assert c.get("/api/province/audit").status_code == 200, "它只查 accidents"

        r = c.get("/api/accidents")
        assert r.status_code == 503, "列表的 SELECT 里有 cause_count 子查询"
        assert r.get_json()["error"] == "schema_mismatch"

        r = c.get("/api/causes/framework")
        assert r.status_code == 503, "它整张表都要"
        assert r.get_json()["error"] == "schema_mismatch"

    @pytest.mark.parametrize("kind", UNUSABLE)
    def test_错误码按下一步动作分档(self, point_at, kind):
        """同一个「库不可用」改前只有两种表现：503 `database_not_found`，
        或者压根没有表现（500）。现在按**下一步动作**分五档。

        「缺表」与「缺列」合成 `schema_mismatch` 是故意的——SQLite 说的是两句
        不同的话，但下一步动作相同（跑建表脚本）。而 `not_a_database` 单独一档，
        因为它是**路径指错了**，跑建表脚本会把错误固化下来。
        """
        point_at(kind)
        c = create_app({"TESTING": True}).test_client()
        assert c.get("/api/overview").get_json()["error"] == EXPECTED_CODE[kind]

    @pytest.mark.parametrize("kind", ["emptytable", "full"])
    def test_库能用时端点照常(self, point_at, kind):
        """**后两档不许被误伤**：表齐全的库（哪怕 0 行）是正常态，不是故障。"""
        point_at(kind)
        c = create_app({"TESTING": True}).test_client()
        for endpoint in LIB_ENDPOINTS:
            assert c.get(endpoint).status_code == 200, f"{kind} {endpoint}"

    def test_库不可用时其它蓝图不受影响(self, point_at):
        """`collect_bp` / `extract_bp` / `review_bp` 读**工作库**，各有自己的
        `_unavailable()`，不该被最终库的状态牵连。"""
        point_at("notables")
        c = create_app({"TESTING": True}).test_client()
        for endpoint in ("/api/collect/sources", "/api/extract/taxonomy",
                         "/api/review/queue"):
            assert c.get(endpoint).status_code == 200, endpoint

    def test_页面路由不受影响(self, point_at):
        """页面全是 `render_template`，数据由前端打 `/api/*` 取——服务端渲染
        换成前端取数的直接收益，别改回去。"""
        point_at("notables")
        c = create_app({"TESTING": True}).test_client()
        for path in ("/", "/accidents", "/causes", "/collect", "/extract", "/review"):
            assert c.get(path).status_code in (200, 404), path


# ══════════════════════════════════════════════════════════════
# C′ 组：/api/images/stats 对最终库的条件性依赖
# ══════════════════════════════════════════════════════════════

_MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS image_manifest (
    accident_id TEXT, rel_path TEXT, filename TEXT, ext TEXT, bytes INTEGER,
    sha1 TEXT, source_url TEXT, source_page_url TEXT, width INTEGER,
    height INTEGER, version TEXT, analyzed INTEGER, manifest_at TEXT)
"""


class TestImagesStats:
    """这个端点**要不要碰最终库，取决于工作库**：`stats()` 在有清单时要算
    覆盖率（除以事故总数），清单为空或工作库不可用就在读工作库那一步早退了。

    所以它不在上面的 `LIB_ENDPOINTS` 清单里——但两条路径都得守住：改前在工作
    库可用的环境下，它在空壳最终库上是会崩的（实测 `OperationalError`）。"""

    def test_工作库可用时才会去查最终库(self, tmp_path, monkeypatch):
        staging = tmp_path / "staging.db"
        con = sqlite3.connect(staging)
        con.execute("CREATE TABLE raw_accidents (id TEXT PRIMARY KEY)")
        con.execute(_MANIFEST_DDL)
        con.execute("INSERT INTO image_manifest (accident_id, rel_path, version,"
                    " analyzed) VALUES ('a1','imageV3/a1/x.jpg','v3',0)")
        con.commit()
        con.close()

        bad = tmp_path / "bad.db"
        sqlite3.connect(str(bad)).close()            # 建了文件、零张表

        monkeypatch.setenv(sdb.ENV_STAGING_DB, str(staging))
        monkeypatch.setenv(db.ENV_DB_PATH, str(bad))
        province.reset_cache()
        overview.reset_cache()
        accidents_query.reset_cache()

        r = create_app({"TESTING": True}).test_client().get("/api/images/stats")
        assert r.status_code == 503, "改前这里是未捕获的 OperationalError"
        assert r.get_json()["error"] == "schema_mismatch"

    def test_工作库不可用时早退不碰最终库(self, point_at):
        """工作库不可用时它是**正常降级**（`state: unavailable`），不该被最终库
        的状态牵连——这条路径是 200 而不是 503，是设计如此。

        注意这一条与上一条的差别：**最终库不可用不是整体状态**。上一条里工作库
        能用，于是它走去查最终库、撞上 `no such table` → 503；这一条里工作库
        先不可用，就在读工作库那步早退了，最终库是什么样根本没被问到。"""
        point_at("notables")
        r = create_app({"TESTING": True}).test_client().get("/api/images/stats")
        assert r.status_code == 200
        assert r.get_json()["data"]["state"] == "unavailable"

    @pytest.fixture
    def staging_with_manifest(self, tmp_path, monkeypatch):
        """工作库可用、清单有一行——`stats()` 会走到「查最终库」那一步。"""
        staging = tmp_path / "staging.db"
        con = sqlite3.connect(staging)
        con.execute("CREATE TABLE raw_accidents (id TEXT PRIMARY KEY)")
        con.execute(_MANIFEST_DDL)
        con.execute("INSERT INTO image_manifest (accident_id, rel_path, version,"
                    " analyzed) VALUES ('a1','imageV3/a1/x.jpg','v3',0)")
        con.commit()
        con.close()
        monkeypatch.setenv(sdb.ENV_STAGING_DB, str(staging))
        return staging

    @pytest.mark.parametrize("kind", ["partial", "partialcols", "emptytable"])
    def test_只依赖accidents的最终库形态照常工作(
            self, tmp_path, monkeypatch, staging_with_manifest, kind):
        """**八种最终库形态里只有四种让它 503，另外四种是 200。**

        `stats()` 在最终库那一侧只执行一条 SQL：`SELECT COUNT(*) FROM
        accidents`（`images.py` 的 `stats()` 里那一行）。所以「缺
        `caics_causes`」（`partial`）与「列不齐」（`partialcols`）都拦不住
        它——`accidents` 表在就够了。

        这条断言同时钉住一件更容易被改坏的事：将来给 `stats()` 加一个要读
        `caics_causes` 的字段（比如「有编码的图片事故数」），`partial` 会
        **静默**从 200 变成 503；有这条断言，改的人当场看得见。

        上一轮记的未决说「工作库可用、最终库缺表时 `images.stats()` 会 503
        而不是返回 `accidents_total: 0`」——实测（2026-10-05，八形态 × 两
        工作库状态）那只对**零张表**的库成立（`notables`，见上一条测试）。
        「缺表」这个词在八形态里对应四种不同的库，它们对这个端点的意义不同。
        """
        # 不用 `point_at`：那个 fixture 在本体里把工作库指到不存在的路径，
        # 与 `staging_with_manifest` 互相覆盖。
        monkeypatch.setenv(db.ENV_DB_PATH, str(_make_db(tmp_path, kind)))
        province.reset_cache()
        overview.reset_cache()
        accidents_query.reset_cache()

        r = create_app({"TESTING": True}).test_client().get("/api/images/stats")
        assert r.status_code == 200, f"{kind}：这个端点只查 accidents 一张表"
        assert r.get_json()["data"]["state"] == "ready"

    @pytest.mark.parametrize("kind,code", [
        ("missing", "database_not_found"),
        ("emptyfile", "schema_mismatch"),
        ("notables", "schema_mismatch"),
        ("garbage", "not_a_database"),
    ])
    def test_最终库打不开时这个端点也503(
            self, tmp_path, monkeypatch, staging_with_manifest, kind, code):
        """**八种最终库形态里，四种让它 503、四种是 200**（200 的那组见上一条）。

        分界不在「有没有数据」，在**那条 SQL 能不能执行**：这四种连库都打不开
        或一张表都没有（`db.connect_readonly()` 就拦下了），上一条那四种过了这
        一步、只是 `stats()` 恰好不碰它们缺的那张表。

        与上一条**成对**：本轮实测表（01 §2.5.42 第二节）是 8 格，两条测试合起
        来守满 8 格——文档里量过的，测试里要守得住，否则改动会悄悄改掉没人看的
        那几格。上一条原来的手写版只覆盖 `notables` 一种，这里补全。
        """
        monkeypatch.setenv(db.ENV_DB_PATH, str(_make_db(tmp_path, kind)))
        province.reset_cache()
        overview.reset_cache()
        accidents_query.reset_cache()

        r = create_app({"TESTING": True}).test_client().get("/api/images/stats")
        assert r.status_code == 503, f"{kind}：这个库里没有可用的 accidents 表"
        assert r.get_json()["error"] == code


# ══════════════════════════════════════════════════════════════
# D 组：/api/health 的 ok 判据
# ══════════════════════════════════════════════════════════════


class TestHealthOkJudge:

    @pytest.mark.parametrize("kind", ["emptyfile", "notables", "partial"])
    def test_空壳库不再报健康(self, point_at, kind):
        """改前这三种都报 `ok: true` + `tables: {}`，徽标显示「NaN 条」并把
        用户引去跑 `add_indexes.py`——**指错方向**。"""
        point_at(kind)
        r = create_app({"TESTING": True}).test_client().get("/api/health")
        assert r.status_code == 503
        body = r.get_json()
        assert body["ok"] is False
        assert body["missing_tables"], "缺哪几张要说出来"

    def test_空壳库不返回索引栏(self, point_at):
        """表都不在，谈索引没有意义。返回空数组等于把「不适用」伪装成
        「没有」——本项目已栽过四次的那个坑。缺这个键，前端忘了判 `ok`
        会抛异常掉进 catch 显示「数据库不可用」，**失败方向是安全的**。"""
        point_at("notables")
        body = create_app({"TESTING": True}).test_client().get("/api/health").get_json()
        assert "missing_indexes" not in body

    def test_表齐全但零行算健康(self, point_at):
        """**不是故障**：刚建完库、还没导入数据的正常态（与第四十轮工作库的
        `empty` 同档）。"""
        point_at("emptytable")
        body = create_app({"TESTING": True}).test_client().get("/api/health").get_json()
        assert body["ok"] is True
        assert body["tables"]["accidents"] == 0

    def test_半建库报出缺的那两张而不是全部(self, point_at):
        point_at("partial")
        body = create_app({"TESTING": True}).test_client().get("/api/health").get_json()
        assert sorted(body["missing_tables"]) == ["caics_causes", "enum_definitions"]

    @pytest.mark.parametrize("kind", ["garbage", "missing"])
    def test_读不动的库仍带ok与db_path(self, point_at, kind):
        """**回改验证里发现的一条测试缺口**（R6 撤回后全绿）。

        `/api/health` 有三条不可用来路，但只有两条会带 `ok: False`：

          - 文件不存在 → 显式分支，带
          - 文件在但读不动 → `try/except sqlite3.Error` 分支，带
          - 文件在、表缺   → 必需表判据分支，带

        第三类由 `test_空壳库不再报健康` 守着。而第二类那段 `try/except` 撤回
        后测试**仍全绿**——因为 `api_bp` 上挂着宽泛的 errorhandler，异常冒上去
        照样是 503 + 同样的 `error`/`message`/`hint` 三键。差别只在**少了
        `ok` 与 `db_path`**：`/api/health` 是给运维用的诊断入口，它自己少了
        这两个键，前端 `loadDbChip` 那条 `h.ok === false` 兜底就失灵，徽标会
        落到「NaN 条」上去。所以这一条守的不是状态码，是**响应形状**。

        `missing` 加进来一起守：文件不存在那条来路本来就走显式分支，
        它不是这段 `try/except` 的职责——两条来路都要带 `ok`。"""
        point_at(kind)
        r = create_app({"TESTING": True}).test_client().get("/api/health")
        assert r.status_code == 503
        body = r.get_json()
        assert body["ok"] is False, "health 自己的响应里必须有 ok"
        assert body["db_path"], "运维要知道它指的是哪个文件"
        assert body["error"] and body["hint"]

    def test_列不齐时health查不出来(self, point_at):
        """**判据的已知边界，不是本轮要修的。** `/api/health` 的 `ok` 只看表名
        （`REQUIRED_TABLES`），表名齐全但列不齐的库它报健康；那样的库要等
        `/api/overview` 撞上 `no such column` 才暴露（由 errorhandler 接住）。

        为什么不补上：查列要维护一份列清单，而那份清单**必然会漂移**——
        `db.table_columns()` 自己的 docstring 就记着「`create_db_v2.py` 的
        SCHEMA 常量注释 55 列 / SCHEMA 52 列 / 真实库 48 列」。拿一份会漂移的
        真相当判据，正是本项目栽过四次的那个坑。

        列不齐的库也不在正常运维路径上：建表脚本一次建全，能造出这种库的只有
        「别的程序建了同名表」或「建表脚本跑到一半」。
        """
        point_at("partialcols")
        c = create_app({"TESTING": True}).test_client()
        assert c.get("/api/health").get_json()["ok"] is True, "判据只看表名"
        r = c.get("/api/overview")
        assert r.status_code == 503, "查不出来不等于可以崩"
        assert r.get_json()["error"] == "schema_mismatch"

    def test_正常库照常报行数(self, point_at):
        point_at("full")
        body = create_app({"TESTING": True}).test_client().get("/api/health").get_json()
        assert body["ok"] is True
        assert body["tables"]["accidents"] == 1
        assert "missing_indexes" in body, "有用的库仍要给索引提示"


# ══════════════════════════════════════════════════════════════
# E 组：契约未被改名
# ══════════════════════════════════════════════════════════════


class TestContract:

    def test_库不存在的错误码没变(self, point_at):
        """`error` 与 `message` 是蓝图 00 §4.4 里写死的对外契约，只能加不能改。"""
        point_at("missing")
        r = create_app({"TESTING": True}).test_client().get("/api/overview")
        assert r.status_code == 503
        assert r.get_json()["error"] == "database_not_found"

    def test_错误体是平铺的三键(self, point_at):
        """`error` 是字符串不是对象（蓝图 00 §4.4 的既有形状）。"""
        point_at("garbage")
        body = create_app({"TESTING": True}).test_client().get(
            "/api/overview").get_json()
        assert isinstance(body["error"], str)
        assert isinstance(body["message"], str)
        assert set(body) == {"error", "message", "hint"}
