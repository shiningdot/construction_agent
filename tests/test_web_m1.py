"""M1 展示模块（概览与检索）回归测试。

蓝图 07 给 M1 定的验收项是「逐项对账的数字表」，那些数字要在**主库**上
验证（见文件末尾的 `TestMainDbParity`）。但主库不进版本控制、内容还会随
采集变化，拿它当唯一断言源会让测试变脆。

所以主体用一座**自洽的小库**：10 条事故，覆盖全部省份分组、证据三级、
有/无编码、年份断档。每个断言都对着手算的期望值，不依赖外部数据。
主库对账作为补充，库不在就 skip。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from construction_agent.config import settings
from construction_agent.web import (
    accidents_query, causes, db, detail, images, overview, province,
)
from construction_agent.web import staging_db as sdb
from construction_agent.web.factory import create_app

# ══════════════════════════════════════════════════════════════
# 测试库
# ══════════════════════════════════════════════════════════════

SCHEMA = """
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
    enum_type TEXT, code TEXT, name TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE image_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id TEXT, local_path TEXT, source_url TEXT,
    phase1_scene_type TEXT, phase1_objects TEXT, phase1_damage TEXT,
    phase2_consistency TEXT, phase2_confidence REAL
);
"""

ACCIDENT_COLS = (
    "id,title,date,accident_time,province,city,district,accident_type,severity,"
    "deaths,injuries,trapped_count,project_type,construction_phase,"
    "collapse_subtype,source_name,summary,direct_cause,work_task"
)

# 10 条，覆盖：标准省（含跨记录同省）、地址级脏值、境外两种写法、
# 认不出来的值、NULL、空串、无日期。
#
# work_task 分布：a1/a2 = WT01（同码两条，验证枚举名复用）、a3 = WT02、
# a4 = WT77（**不在枚举表**，验证回退显示编码本身而非兜底词）、
# a5–a10 留空（验证 16% 缺失时不显示成「未标注」）。
ACCIDENTS: list[dict] = [
    dict(id="a1", title="四川某工地坍塌事故", date="2024-01-01", province="四川省",
         accident_type="坍塌", severity="重大事故", deaths=16, injuries=39,
         project_type="房屋建筑", construction_phase="主体结构",
         collapse_subtype="脚手架坍塌",
         source_name="safehoo", direct_cause="边坡未按规定放坡",
         work_task="WT01"),
    # a2 是**非坍塌**事故，`collapse_subtype` 按 step1 的约定填哨兵「不适用」——
    # 详情页必须把它读成缺失，否则这一栏会显示「不适用」且把 C 档顶成满值。
    dict(id="a2", title="成都高处坠落事故", date="2023-05-05", province="四川省",
         accident_type="高处坠落", severity="一般事故", deaths=0, injuries=1,
         collapse_subtype="不适用",
         source_name="safehoo", work_task="WT01"),
    dict(id="a3", title="广东脚手架坍塌", date="2022-03-03", province="广东省",
         accident_type="坍塌", severity="较大事故", deaths=3, injuries=2,
         source_name="esafety", work_task="WT02"),
    dict(id="a4", title="鄂州触电事故", date="2021-07-07", province="鄂州市五里墩建设工地",
         accident_type="触电", severity="一般事故", deaths=1, injuries=0,
         source_name="esafety", work_task="WT77"),
    dict(id="a5", title="印度桥梁垮塌", date="2020-02-02", province="印度",
         accident_type="坍塌", severity="重大事故", deaths=5, injuries=7,
         source_name="safehoo"),
    dict(id="a6", title="香港工地火灾", date="2019-09-09", province="香港",
         accident_type="火灾爆炸", severity="一般事故", deaths=2, injuries=0,
         source_name="safehoo"),
    dict(id="a7", title="某地一般事故", date="2018-01-01", province="中国",
         accident_type="其他", severity="一般事故", deaths=1, injuries=0,
         source_name="mem"),
    dict(id="a8", title="无省份无日期事故", province=None,
         accident_type="坍塌", severity="一般事故", deaths=0, injuries=0,
         source_name="mem"),
    dict(id="a9", title="无省份有日期事故", date="2017-01-01", province="",
         accident_type="其他", severity="", deaths=None, injuries=2,
         source_name="mem"),
    dict(id="a10", title="鸡西矿业学院事故", date="1990-01-01",
         province="黑龙江省鸡西市矿业学院电教楼工地",
         accident_type="坍塌", severity="一般事故", deaths=1, injuries=0,
         source_name="safehoo"),
]

# 6 条编码，覆盖 5 条事故；证据三级各有。
# `evidence` / `inference` 留空是有意的——实测主库两列各有 8 / 11 条空串，
# 详情页必须显示「未提供证据文本」而不能拿类名冒充证据。
# sort_order 取层级顺序，验证层内排序而非按 code 字母序。
CAUSES: list[tuple[str, str, str, str, int]] = [
    ("a1", "M1", "现场监管缺失", "direct_quote", 8),
    ("a1", "H1", "违反操作规程", "indirect_infer", 20),
    ("a2", "F4", "个体防护未使用", "direct_quote", 11),
    ("a4", "T1", "设备机具故障", "weak_infer", 15),
    ("a5", "M2", "隐患排查失效", "direct_quote", 9),
    ("a10", "H3", "风险辨识失败", "direct_quote", 22),
]

# 工序枚举：3 条在用 + 1 条停用。停用那条用来验证 `is_active=0` 不会被查到名字。
ENUMS: list[tuple[str, str, str, int]] = [
    ("work_task", "WT01", "地基与基础工程", 1),
    ("work_task", "WT02", "主体结构工程", 1),
    ("work_task", "WT99", "其他", 1),
    ("work_task", "WT09", "拆除工程", 0),
]


def _insert(con: sqlite3.Connection, table: str, cols: str, rows: list[dict]) -> None:
    names = [c.strip() for c in cols.split(",")]
    sql = f"INSERT INTO {table} ({cols}) VALUES ({','.join(':' + n for n in names)})"
    for r in rows:
        full = {n: None for n in names}
        full.update(r)
        con.execute(sql, full)


@pytest.fixture
def tiny_db(tmp_path, monkeypatch) -> Path:
    """建一座 10 条事故的小库，并把展示层指向它。"""
    path = tmp_path / "tiny.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    _insert(con, "accidents", ACCIDENT_COLS, ACCIDENTS)
    con.executemany(
        "INSERT INTO caics_causes (accident_id,code,name,evidence_quality,sort_order)"
        " VALUES (?,?,?,?,?)",
        CAUSES,
    )
    con.executemany(
        "INSERT INTO enum_definitions (enum_type,code,name,is_active) VALUES (?,?,?,?)",
        ENUMS,
    )
    con.commit()
    con.close()

    monkeypatch.setenv(db.ENV_DB_PATH, str(path))
    # 隔离工作库：默认路径指向 settings.DATA_DIR，在跑过重建脚本的机器上
    # 那是**真实**工作库，图片类断言会读到项目数据而不是测试数据
    monkeypatch.setenv(sdb.ENV_STAGING_DB, str(tmp_path / "no-staging.db"))
    province.reset_cache()
    overview.reset_cache()
    accidents_query.reset_cache()
    yield path
    province.reset_cache()
    overview.reset_cache()
    accidents_query.reset_cache()


@pytest.fixture
def client(tiny_db):
    return create_app({"TESTING": True}).test_client()


# ══════════════════════════════════════════════════════════════
# 省份归一
# ══════════════════════════════════════════════════════════════


class TestProvinceClassify:

    def test_标准全称原样返回(self):
        assert province.classify("四川省") == "四川省"

    def test_地址级脏值归到所属省份(self):
        assert province.classify("鄂州市五里墩建设工地") == "湖北省"
        assert province.classify("黑龙江省鸡西市矿业学院电教楼工地") == "黑龙江省"

    def test_港澳台并入境外桶(self):
        """港澳台在 location.py 里是标准省级单位，本项目归入境外组。

        若不做这一步，「香港特别行政区」会成为一个独立选项，
        与概览卡片上的「境外及港澳台」计数对不上。
        """
        assert province.classify("香港") == province.SENTINEL_OVERSEAS
        assert province.classify("香港特别行政区") == province.SENTINEL_OVERSEAS
        assert province.classify("台湾省") == province.SENTINEL_OVERSEAS

    def test_境外国家落入境外桶(self):
        assert province.classify("印度") == province.SENTINEL_OVERSEAS
        assert province.classify("蒙古国") == province.SENTINEL_OVERSEAS

    def test_认不出来的值单独成桶(self):
        assert province.classify("中国") == province.SENTINEL_UNNORMALIZED
        assert province.classify("宁波世纪大道和北外环交界处") == province.SENTINEL_UNNORMALIZED

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_空值落入空桶(self, raw):
        assert province.classify(raw) == province.SENTINEL_EMPTY

    def test_内地清单与行政区划典一致(self):
        """MAINLAND_ORDER 是硬编码的展示顺序，必须与 location.py 对齐。"""
        from construction_agent.utils.location import (
            AUTONOMOUS_REGIONS, MUNICIPALITIES, PROVINCES, SAR,
        )

        derived = (
            set(PROVINCES.values()) | set(AUTONOMOUS_REGIONS.values())
            | set(MUNICIPALITIES.values())
        )
        expected = derived - {PROVINCES["台湾"], SAR["香港"], SAR["澳门"]}
        assert set(province.MAINLAND_ORDER) == expected
        assert len(province.MAINLAND_ORDER) == 31


class TestProvinceIndex:

    def test_分组计数与总数自洽(self, tiny_db):
        idx = province.get_index()
        assert idx.total == 10
        assert idx.counts["四川省"] == 2
        assert idx.counts["湖北省"] == 1          # a4 由地址归一
        assert idx.counts["黑龙江省"] == 1        # a10 由地址归一
        assert idx.counts[province.SENTINEL_OVERSEAS] == 2   # 印度 + 香港
        assert idx.counts[province.SENTINEL_UNNORMALIZED] == 1
        assert idx.counts[province.SENTINEL_EMPTY] == 2      # NULL + 空串

    def test_自检不变量成立(self, tiny_db):
        chk = province.selfcheck()
        assert chk["ok"] is True
        assert chk["counted"] == chk["total"] == 10

    def test_反向展开含全部原始写法(self, tiny_db):
        idx = province.get_index()
        assert sorted(idx.raw_values("四川省")) == ["四川省"]
        assert sorted(idx.raw_values("湖北省")) == ["鄂州市五里墩建设工地"]
        assert sorted(idx.raw_values(province.SENTINEL_OVERSEAS)) == ["印度", "香港"]

    def test_空桶不需要原始值(self, tiny_db):
        assert province.get_index().raw_values(province.SENTINEL_EMPTY) == []

    def test_审计列出非标准值并分类(self, tiny_db):
        rows = {r["raw"]: r for r in province.get_index().audit()}
        assert "四川省" not in rows, "标准全称不该出现在审计里"
        assert rows["鄂州市五里墩建设工地"]["kind"] == "renormalized"
        assert rows["鄂州市五里墩建设工地"]["resolved"] == "湖北省"
        assert rows["印度"]["kind"] == "overseas"
        assert rows["中国"]["kind"] == "unanchored"

    def test_缓存按库指纹失效(self, tiny_db, tmp_path):
        first = province.get_index()
        assert first.total == 10
        province.reset_cache()
        assert province.get_index() is not first, "重置后应重建"

    def test_未知键被忽略而不是报错(self, tiny_db):
        sql, params = province.filter_clause(["不存在的省"])
        assert sql == "" and params == []

    def test_known_keys_translate_to_placeholders(self, tiny_db):
        sql, params = province.filter_clause(["四川省", province.SENTINEL_EMPTY])
        assert "province IN (?)" in sql
        assert "TRIM(province) = ''" in sql
        assert params == ["四川省"]

    def test_生效键剔除库外的省份(self, tiny_db):
        """回显给前端的条件必须是**真进了 WHERE** 的那批。

        `filter_clause` 对未知键静默忽略（列表照常返回全部），若 meta 照抄传入值，
        响应就在说「我按这个省筛了」——2026-10-01 实测到这一幕。
        """
        assert province.effective_keys([]) == []
        assert province.effective_keys(["不存在的省"]) == []
        assert province.effective_keys(["四川省", "不存在的省"]) == ["四川省"]

    def test_空值桶总生效(self, tiny_db):
        """`__empty__` 筛的是 IS NULL OR TRIM=''，与库里有几条无关——
        `raw_values` 对它返回空列表，不能拿它当判据。"""
        assert province.effective_keys([province.SENTINEL_EMPTY, "不存在的省"]) == [
            province.SENTINEL_EMPTY
        ]

    def test_生效键与过滤条件不漂(self, tiny_db):
        """`effective_keys` 是与 `filter_clause` 配套的另一半，两边判断必须一致：
        说了生效的键就得真产出 SQL 片段，没产出的就不该被回显。"""
        for keys in (
            [],
            ["四川省"],
            ["不存在的省"],
            [province.SENTINEL_EMPTY],
            ["四川省", "不存在的省", province.SENTINEL_EMPTY],
        ):
            sql, _ = province.filter_clause(keys)
            assert bool(sql) == bool(province.effective_keys(keys)), keys


# ══════════════════════════════════════════════════════════════
# 概览
# ══════════════════════════════════════════════════════════════


class TestOverview:

    def test_总数与伤亡口径(self, tiny_db):
        t = overview.build()["totals"]
        assert t["accidents"] == 10
        assert t["deaths"] == 29          # 16+0+3+1+5+2+1+0+0+1，None 被 SUM 忽略
        assert t["injuries"] == 51        # 39+1+2+0+7+0+0+0+2+0
        assert t["deaths_recorded"] == 7  # deaths > 0 的事故数
        assert t["injuries_recorded"] == 5
        assert t["casualty_any"] == 9  # a9 只有受伤、无死亡，也算涉伤亡
        assert t["causes"] == 6
        assert t["causes_covered"] == 5

    def test_证据三级按固定顺序(self, tiny_db):
        ev = overview.build()["evidence_quality"]
        assert [e["key"] for e in ev] == list(overview.EVIDENCE_ORDER)
        assert [e["count"] for e in ev] == [4, 1, 1]
        assert ev[0]["label"] == "直接引用"

    def test_空字符串等级不并入一般事故(self, tiny_db):
        sev = {r["key"]: r["count"] for r in overview.build()["severity"]}
        assert sev[overview.UNLABELED] == 1
        assert sev["一般事故"] == 6

    def test_五层按编码体系顺序(self, tiny_db):
        layers = overview.build()["caics_layer"]
        assert [x["key"] for x in layers] == ["M", "F", "T", "H", "C"]
        got = {x["key"]: x["count"] for x in layers}
        assert got == {"M": 2, "F": 1, "T": 1, "H": 2, "C": 0}
        assert layers[0]["label"] == "管理因素"

    def test_年份断档补零(self, tiny_db):
        year = overview.build()["year"]
        assert year[0]["key"] == "1990"
        assert year[-1]["key"] == "2024"
        assert len(year) == 35, "1990–2024 每个年份都要有点，缺的补 0"
        gaps = [y for y in year if 1991 <= int(y["key"]) <= 2016]
        assert all(y["count"] == 0 for y in gaps), "断档年份应为 0 而非缺席"

    def test_无日期记录单独计数(self, tiny_db):
        assert overview.build()["data_quality"]["year_unlabeled"] == 1

    def test_catalog_按编码给中文类名(self, tiny_db):
        cat = {c["code"]: c for c in overview.build()["caics_catalog"]}
        assert cat["M1"]["name"] == "现场监管缺失"
        assert cat["M1"]["layer_label"] == "管理因素"
        assert cat["M1"]["count"] == 1              # a1 的一条 M1
        assert cat["M2"]["accidents"] == 1

    def test_省份分组随概览一并返回(self, tiny_db):
        pg = overview.build()["province_group"]
        assert pg["by_group"]["standard"] == 5
        assert pg["by_group"]["overseas"] == 2
        assert pg["by_group"]["unnormalized"] == 1
        assert pg["by_group"]["empty"] == 2


# ══════════════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════════════


class TestListParams:

    def test_默认值(self):
        p = accidents_query.ListParams.from_request({})
        assert (p.page, p.size, p.sort) == (1, 50, "date_desc")
        assert p.province == [] and p.has_causes is None

    def test_非法排序退回默认(self):
        p = accidents_query.ListParams.from_request({"sort": "id; DROP TABLE accidents"})
        assert p.sort == "date_desc"

    @pytest.mark.parametrize("raw,expected", [
        ("-5", 50), ("0", 50), ("abc", 50), ("", 50), ("99999", 200), ("20", 20),
    ])
    def test_size_边界(self, raw, expected):
        assert accidents_query.ListParams.from_request({"size": raw}).size == expected

    def test_page_非正数归到第一页(self):
        assert accidents_query.ListParams.from_request({"page": "-3"}).page == 1

    def test_多值两种写法等价(self):
        a = accidents_query.ListParams.from_request({"severity": "重大事故,较大事故"})
        b = accidents_query.ListParams.from_request({"severity": ["重大事故", "较大事故"]})
        assert a.severity == b.severity == ["重大事故", "较大事故"]

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("1", True), ("yes", True),
        ("false", False), ("0", False), ("", None), ("maybe", None),
    ])
    def test_has_causes_解析(self, raw, expected):
        kw = {"has_causes": raw} if raw else {}
        assert accidents_query.ListParams.from_request(kw).has_causes is expected


class TestQuery:
    """每个查询跑两遍：一次直接 SQL，一次走 `run()`，两边必须相等。

    这样断言的是「翻译层没丢条件」，而不是我自己手算的期望值。
    """

    @staticmethod
    def _direct(where: str, params: list = ()) -> int:
        return db.scalar(f"SELECT COUNT(*) FROM accidents a{where}", params, default=0)

    def test_无筛选返回全部(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams())
        assert r["meta"]["total"] == 10
        assert len(r["items"]) == 10
        assert r["meta"]["pages"] == 1

    def test_按省筛选含归一脏值(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(province=["湖北省"]))
        assert r["meta"]["total"] == 1
        assert r["items"][0]["id"] == "a4"
        assert r["items"][0]["province_label"] == "湖北省"
        assert r["items"][0]["province_raw"] == "鄂州市五里墩建设工地"

    def test_标准省份不返回原始值(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(province=["四川省"]))
        assert "province_raw" not in r["items"][0]

    def test_境外桶(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(
            province=[province.SENTINEL_OVERSEAS]))
        assert r["meta"]["total"] == 2
        assert {i["id"] for i in r["items"]} == {"a5", "a6"}

    def test_空桶(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(
            province=[province.SENTINEL_EMPTY]))
        assert {i["id"] for i in r["items"]} == {"a8", "a9"}

    def test_多省并联(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(province=["四川省", "广东省"]))
        assert r["meta"]["total"] == 3

    def test_has_causes_两条路径(self, tiny_db):
        yes = accidents_query.run(accidents_query.ListParams(has_causes=True))
        no = accidents_query.run(accidents_query.ListParams(has_causes=False))
        assert yes["meta"]["total"] == 5
        assert no["meta"]["total"] == 5
        assert yes["meta"]["total"] + no["meta"]["total"] == 10
        assert all(i["cause_count"] > 0 for i in yes["items"])
        assert all(i["cause_count"] == 0 for i in no["items"])

    def test_关键词命中标题与直接原因(self, tiny_db):
        p = accidents_query.ListParams(q="坍塌")
        ids = {i["id"] for i in accidents_query.run(p)["items"]}
        assert "a1" in ids and "a3" in ids          # 标题
        p2 = accidents_query.ListParams(q="放坡")
        assert {i["id"] for i in accidents_query.run(p2)["items"]} == {"a1"}  # direct_cause

    def test_关键词里的通配符按字面处理(self, tiny_db):
        """`instr` 而非 `LIKE`：`%` 不该变成「匹配任意字符」。"""
        r = accidents_query.run(accidents_query.ListParams(q="%"))
        assert r["meta"]["total"] == 0

    def test_年份区间(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(year_from=2018, year_to=2021))
        assert {i["id"] for i in r["items"]} == {"a4", "a5", "a6", "a7"}

    def test_等级多选与空值哨兵(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(
            severity=["重大事故", accidents_query.SENTINEL_EMPTY]))
        assert {i["id"] for i in r["items"]} == {"a1", "a5", "a9"}

    def test_分页切片不重不漏(self, tiny_db):
        seen: list[str] = []
        for page in (1, 2, 3):
            r = accidents_query.run(accidents_query.ListParams(page=page, size=4))
            seen.extend(i["id"] for i in r["items"])
        assert len(seen) == 10
        assert len(set(seen)) == 10, "同一条不该在两页都出现"

    def test_超范围页返回空列表而非报错(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(page=9999))
        assert r["items"] == []
        assert r["meta"]["total"] == 10, "总数仍应正确"

    def test_排序白名单全部可执行(self, tiny_db):
        for key in accidents_query.SORTS:
            r = accidents_query.run(accidents_query.ListParams(sort=key, size=3))
            assert len(r["items"]) == 3, f"{key} 排序失败"

    def test_死亡排序方向正确(self, tiny_db):
        desc = accidents_query.run(accidents_query.ListParams(sort="deaths_desc", size=3))
        vals = [i["deaths"] or 0 for i in desc["items"]]
        assert vals == sorted(vals, reverse=True)
        assert desc["items"][0]["id"] == "a1"

    def test_等级排序按严重程度而非字面(self, tiny_db):
        r = accidents_query.run(accidents_query.ListParams(sort="severity_desc", size=2))
        assert r["items"][0]["severity"] == "重大事故"

    def test_条件翻译与直接_SQL_等价(self, tiny_db):
        p = accidents_query.ListParams(
            q="事故", province=["四川省"], severity=["一般事故"], has_causes=True)
        where, args = accidents_query.build_where(p)
        assert accidents_query.run(p)["meta"]["total"] == self._direct(where, args)

    def test_空条件不产生_WHERE(self, tiny_db):
        where, args = accidents_query.build_where(accidents_query.ListParams())
        assert where == "" and args == []

    def test_筛选回显与实际条件一致(self, tiny_db):
        p = accidents_query.ListParams(province=["四川省"], severity=["一般事故"])
        meta = accidents_query.run(p)["meta"]
        assert meta["filters"]["province"] == ["四川省"]
        assert meta["filters"]["severity"] == ["一般事故"]

    def test_筛选选项来自数据本身(self, tiny_db):
        opts = accidents_query.filter_options()
        sev = {o["value"]: o["count"] for o in opts["severity"]}
        assert sev["一般事故"] == 6
        assert sev[accidents_query.SENTINEL_EMPTY] == 1
        assert opts["province"][0]["group"] == "standard"
        assert len(opts["sort"]) == len(accidents_query.SORTS)


# ══════════════════════════════════════════════════════════════
# HTTP 层
# ══════════════════════════════════════════════════════════════


class TestWorkTask:
    """工序列：只读 `accidents.work_task`，中文名查 `enum_definitions`。

    这是 2026-09-30 的裁决，判据写在 `accidents_query.py` 的模块注释里：
    `work_task_enum` 是 1.0 旧体系残留列（1,538 条里 703 条编码已废弃），
    展示与并入一律不得使用。
    """

    def test_列表返回编码与中文名(self, tiny_db):
        out = accidents_query.run(accidents_query.ListParams())
        by_id = {it["id"]: it for it in out["items"]}
        assert by_id["a1"]["work_task_code"] == "WT01"
        assert by_id["a1"]["work_task_label"] == "地基与基础工程"
        assert by_id["a3"]["work_task_label"] == "主体结构工程"
        # a1/a2 同码：枚举名整页查一次，不该出现一条有名、一条没有
        assert by_id["a2"]["work_task_label"] == "地基与基础工程"

    def test_无值时不写兜底词(self, tiny_db):
        """主库缺失 16%，显示成「未标注」会让读者误以为它是一个类别。"""
        out = accidents_query.run(accidents_query.ListParams())
        by_id = {it["id"]: it for it in out["items"]}
        assert by_id["a5"]["work_task_code"] is None
        assert by_id["a5"]["work_task_label"] is None

    def test_编码不在枚举表时回退显示编码本身(self, tiny_db):
        """异常编码要自己冒出来，不能被一个兜底词盖住。"""
        out = accidents_query.run(accidents_query.ListParams())
        by_id = {it["id"]: it for it in out["items"]}
        assert by_id["a4"]["work_task_code"] == "WT77"
        assert by_id["a4"]["work_task_label"] == "WT77"

    def test_停用的枚举不给名字(self, tiny_db):
        """`is_active=0` 的编码按「查不到」处理，同样走回退路径。"""
        names = accidents_query.enum_names("work_task")
        assert "WT09" not in names
        assert names["WT99"] == "其他"

    def test_枚举名按库指纹缓存(self, tiny_db):
        first = accidents_query.enum_names("work_task")
        assert accidents_query.enum_names("work_task") is first


# ══════════════════════════════════════════════════════════════
# 详情页（蓝图 05）
# ══════════════════════════════════════════════════════════════


@pytest.fixture
def tiny_staging(tmp_path, monkeypatch) -> Path:
    """建一座小工作库，并把展示层的图片数据源指过去。

    图片**不在最终库**：`image_files.local_path` 三版混存（`imageV2/` 550 +
    `images/` 276 + `imageV3/` 211）。展示层因此改读工作库的
    `image_manifest`——那份清单以文件系统为真源（蓝图 06 §3），测试也就
    需要有工作库。

    三版目录都造出来，是因为**三版都服务**：`local_path` 逐条 stat 命中
    1,037/1,037，只放 `imageV3/` 会让 366 个有图事故一张图都看不到
    （`docs/web/08_图片资产复核与更正.md`）。`/media` 的路径校验必须能真的
    把文件送出去，而不是只测到 404 分支。
    """
    path = tmp_path / "staging.db"
    sdb.init_db(path)
    monkeypatch.setenv(sdb.ENV_STAGING_DB, str(path))

    data_dir = tmp_path / "data"
    v3 = data_dir / "imageV3" / "a1"
    v3.mkdir(parents=True)
    (v3 / "1.jpg").write_bytes(b"\xff\xd8\xff\xe0-fake-jpeg-one")
    (v3 / "2.jpg").write_bytes(b"\xff\xd8\xff\xe0-fake-jpeg-two")
    # 旧两版也建上：它们要能被服务（`/media` 的白名单含三版），不是当反例
    for vdir in ("images", "imageV2"):
        d = data_dir / vdir / "a1"
        d.mkdir(parents=True)
        (d / "old.jpg").write_bytes(b"\xff\xd8\xff\xe0-fake-jpeg-old")

    # images.media_root() 读 settings.DATA_DIR，不指过来会去项目的真实 data/
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)
    yield path


@pytest.fixture
def dup_client(tiny_db, tiny_staging):
    """追加「同一编码多条标注」（最终库）与图片清单（工作库）。

    单独开一个 fixture 是为了不动共用的 `ACCIDENTS` / `CAUSES`——概览与
    列表的断言依赖那两组的计数。`caics_causes` 对 `(accident_id, code)`
    无唯一约束是主库的实测事实（278 组重复、616 行），这里复现它。
    """
    con = sqlite3.connect(tiny_db)
    con.executemany(
        "INSERT INTO caics_causes (accident_id,code,name,evidence,inference,"
        "evidence_quality,sort_order) VALUES (?,?,?,?,?,?,?)",
        [
            # a1 的 M1 第二条：等级更弱、证据文本是空串（实测 8 条这种情况）
            ("a1", "M1", "现场监管缺失", "", "", "weak_infer", 8),
            # a1 的 H1 第二条：等级更强且带证据，验证合并取最强
            ("a1", "H1", "违反操作规程", "工人未系安全带", "现场照片可见",
             "direct_quote", 20),
            # 中间层，验证层分组顺序与层内 sort_order 排序
            ("a1", "F2", "防护设施缺失", "临边无护栏", "", "direct_quote", 12),
        ],
    )
    con.commit()
    con.close()

    stg = sqlite3.connect(tiny_staging)
    stg.executemany(
        "INSERT INTO image_manifest (accident_id, rel_path, filename, ext, "
        "bytes, sha1, source_url, phase1_scene_type, phase2_consistency, "
        "analyzed, version, manifest_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # VLM 的原始返回里带 Markdown 强调标记（主库实测形如
            # `**坍塌 / 土方坍塌**`），展示前要剥掉
            ("a1", "imageV3/a1/1.jpg", "1.jpg", "jpg", 1024, "sha-one",
             "http://example.invalid/1.jpg", "**坍塌现场**", "一致", 1,
             "v3", "2026-09-30 12:00:00"),
            ("a1", "imageV3/a1/2.jpg", "2.jpg", "jpg", 512, "sha-two",
             None, None, None, 0, "v3", "2026-09-30 12:00:00"),
            # 历史版本的图。主库 476 个有图事故里 57 个跨版本都有图，只造
            # v3 会把排序与角标这两条路径整个漏掉。哈希用独立值，免得搅进
            # 「多事故共用」的判定。
            ("a1", "imageV2/a1/old.jpg", "old.jpg", "jpg", 256, "sha-old",
             None, None, None, 0, "v2", "2026-09-30 12:00:00"),
        ],
    )
    stg.commit()
    stg.close()

    accidents_query.reset_cache()
    return create_app({"TESTING": True}).test_client()


class TestDetail:
    """详情聚合。接口契约：`detail.build()` 的返回值即前端唯一数据源。"""

    def test_不存在的_id_返回_404(self, client):
        """404 而不是 200 + 空对象——「没有这条」与「这条什么都没有」是两件事。"""
        r = client.get("/api/accidents/zzz")
        assert r.status_code == 404
        assert r.get_json()["error"] == "accident_not_found"

    def test_详情页路由出壳(self, client):
        r = client.get("/accident/a1")
        assert r.status_code == 200
        assert b'data-accident-id="a1"' in r.data

    def test_层分组且层内按_sort_order_排(self, dup_client):
        d = dup_client.get("/api/accidents/a1").get_json()["data"]
        assert [c["code"] for c in d["causes"]["items"]] == ["M1", "F2", "H1"]
        assert [c["layer"] for c in d["causes"]["items"]] == ["M", "F", "H"]
        assert d["causes"]["items"][1]["layer_label"] == "设施设备"

    def test_同一编码多条标注并列不丢弃(self, dup_client):
        d = dup_client.get("/api/accidents/a1").get_json()["data"]
        m1 = d["causes"]["items"][0]
        assert m1["code"] == "M1"
        assert m1["citation_count"] == 2
        assert len(m1["citations"]) == 2

    def test_合并后证据等级取最强(self, dup_client):
        """并存一条弱推断不该把「原文有直引」降级。"""
        by_code = {c["code"]: c
                   for c in dup_client.get("/api/accidents/a1").get_json()["data"]
                   ["causes"]["items"]}
        assert by_code["M1"]["evidence_quality"] == "direct_quote"
        assert by_code["H1"]["evidence_quality"] == "direct_quote"
        assert by_code["H1"]["quality_label"] == "原文直接表述"

    def test_空证据给_None_不拿类名冒充(self, dup_client):
        by_code = {c["code"]: c
                   for c in dup_client.get("/api/accidents/a1").get_json()["data"]
                   ["causes"]["items"]}
        # M1 两条的证据列都是空串（主库实测 8 / 11 条这种情况）→ 一律给 None
        assert [c["evidence"] for c in by_code["M1"]["citations"]] == [None, None]
        # H1 一条空、一条有文本：并列展示，各归各，不互相顶替
        evs = [c["evidence"] for c in by_code["H1"]["citations"]]
        assert None in evs
        assert "工人未系安全带" in evs

    def test_计数口径两个层次(self, dup_client):
        """`citations` 是标注行数，`distinct` 是事故×类别覆盖数。"""
        c = dup_client.get("/api/accidents/a1").get_json()["data"]["causes"]
        assert c["citations"] == 5   # M1×2 + H1×2 + F2×1
        assert c["distinct"] == 3

    def test_无编码事故不报错(self, client):
        c = client.get("/api/accidents/a8").get_json()["data"]["causes"]
        assert c["items"] == []
        assert c["distinct"] == 0
        assert c["citations"] == 0

    def test_分档带可用数且_D_档含被困人数(self, client):
        tiers = client.get("/api/accidents/a1").get_json()["data"]["tiers"]
        assert [t["key"] for t in tiers] == ["A", "B", "C", "D"]
        a = tiers[0]
        assert a["total"] == 13
        # a1 有值 10 项：title/date/province/type/project_type/construction_phase/
        # severity/deaths/injuries/direct_cause
        # 缺 3 项：city / accident_location_detail / summary
        assert a["available"] == 10
        d = tiers[3]
        assert "trapped_count" in d["fields"]
        assert "weather" in d["fields"]

    def test_三类核心分类列都在_A_档(self, client):
        """`project_type` / `construction_phase` 有值率 100% 却曾不在任何档里。

        根因是蓝图 05 §2.1 的分档表漏列（表里 43 个列名、标题写 48 列），实现
        照抄表格。这一条护住补档结果，`scripts/check_field_alignment.py` 另有
        一条按 `accidents` 全列核的判据。
        """
        a = client.get("/api/accidents/a1").get_json()["data"]["tiers"][0]
        assert "project_type" in a["fields"]
        assert "construction_phase" in a["fields"]

    def test_坍塌子类的哨兵值读成缺失(self, client):
        """「不适用」是 step1 定的哨兵，不是数据。

        `collapse_subtype` 非坍塌类填「不适用」（`step1_basic_extractor.py`），
        主库 2,623 行里 1,320 行是这个值。原样下发的话，触电、坠落这些事故会各
        显示一行「坍塌子类：不适用」，C 档的 `available` 也被顶成满值。
        """
        c = client.get("/api/accidents/a1").get_json()["data"]      # 坍塌
        assert c["fields"]["collapse_subtype"] == "脚手架坍塌"
        c = client.get("/api/accidents/a2").get_json()["data"]      # 高处坠落
        assert c["fields"]["collapse_subtype"] is None
        tiers = {t["key"]: t for t in c["tiers"]}
        assert "collapse_subtype" in tiers["C"]["fields"]           # 在档里，只是没值

    def test_空值与零可区分(self, client):
        """a2 的 `deaths=0` 算有值。

        语义是「报道未提及伤亡」而非「这一栏没填」——若把 0 判成缺失，
        主库 2,280 条记录过死亡人数的数据会被系统性地误判（蓝图 00 §3.5）。
        a2 实际有值 7 项（含 deaths=0），缺 city / accident_location_detail /
        summary / direct_cause 4 项。
        """
        tiers = client.get("/api/accidents/a2").get_json()["data"]["tiers"]
        assert tiers[0]["available"] == 7

    def test_省份走与列表页同一套归一(self, client):
        d = client.get("/api/accidents/a4").get_json()["data"]["fields"]
        # 原始脏值保留，归一结果另存——脏值不掩盖、可追溯
        assert d["province"] == "鄂州市五里墩建设工地"
        assert d["province_label"] == "湖北省"

    def test_工序列给中文名(self, client):
        f = client.get("/api/accidents/a1").get_json()["data"]["fields"]
        assert f["work_task"] == "WT01"
        assert f["work_task_label"] == "地基与基础工程"

    def test_编码不在枚举表时回退编码本身(self, client):
        f = client.get("/api/accidents/a4").get_json()["data"]["fields"]
        assert f["work_task_label"] == "WT77"

    def test_图片按事故过滤(self, dup_client, client):
        assert len(dup_client.get("/api/accidents/a1").get_json()
                   ["data"]["images"]) == 3
        assert client.get("/api/accidents/a2").get_json()["data"]["images"] == []

    def test_元信息含分类判定路径(self, client):
        m = client.get("/api/accidents/a1").get_json()["data"]["meta"]
        assert set(m) == {"id", "source_name", "source_url", "crawl_date",
                          "report_type", "text_type", "classification_verdict",
                          "classification_confidence", "source_count",
                          "search_performed", "pipeline_version",
                          "step1_duration_ms", "step2_duration_ms", "created_at"}
        assert m["id"] == "a1"
        assert m["source_name"] == "safehoo"

    def test_来源链接不外泄到内容字段(self, client):
        """`source_url` 只在元信息区出现，内容分档里没有它。"""
        d = client.get("/api/accidents/a1").get_json()["data"]
        all_fields = [f for t in d["tiers"] for f in t["fields"]]
        assert "source_url" not in all_fields


# ══════════════════════════════════════════════════════════════
# 图片（蓝图 06）
# ══════════════════════════════════════════════════════════════


class TestImages:
    """图片来自工作库的 `image_manifest`，不是最终库的 `image_files`。

    `image_files.local_path` 三版混存，按它拼 URL 会大面积 404——这条是
    整块图片展示重做的起因，也是下面「不服务旧版目录」那条断言的由来。
    """

    def test_media_把文件送出去(self, dup_client):
        r = dup_client.get("/media/imageV3/a1/1.jpg")
        assert r.status_code == 200
        assert r.data.startswith(b"\xff\xd8"), "返回的不是图片内容"
        assert "max-age=86400" in r.headers["Cache-Control"]

    def test_media_服务三版目录(self, dup_client):
        """`images/`、`imageV2/`、`imageV3/` 都服务。

        「只放最终版」的原始理由是躲开跨版本重复图，但实测真重复只有 19
        组 / 38 张，代价是 725 张图、366 个有图事故看不到图。版本差异改由
        角标呈现，不再靠隐藏。
        """
        for vdir, name in (("images", "old.jpg"), ("imageV2", "old.jpg"),
                           ("imageV3", "1.jpg")):
            r = dup_client.get(f"/media/{vdir}/a1/{name}")
            assert r.status_code == 200, vdir
            assert r.data.startswith(b"\xff\xd8"), vdir

    def test_media_挡住目录穿越(self, dup_client):
        """路径校验两道关：第一段必须在版本白名单内，且 realpath 后仍在
        某个版本目录之内。`..` 一律直接拒——`imageV3/../imageV2/x` 这种
        「绕出去又落回白名单目录」的写法，只靠 realpath 是放行的。"""
        for p in ("imageV3/../imageV2/a1/old.jpg",
                  "imageV3/a1/../../imageV2/a1/old.jpg",
                  "imageV3/%2e%2e/imageV2/a1/old.jpg",
                  "imageV3//etc/passwd",
                  "etc/passwd",
                  "../data/imageV3/a1/1.jpg"):
            assert dup_client.get("/media/" + p).status_code == 404, p

    def test_media_不存在返回_404(self, dup_client):
        assert dup_client.get("/media/imageV3/a1/nope.jpg").status_code == 404

    def test_图片清单带_url_与分析标记(self, dup_client):
        """走**详情接口**取图——那是前端真正用的那条路。

        原先走 `/api/accidents/<id>/images`，那条画廊专用端点已删（42 条路由
        里唯一的孤儿），测试跟着它走，测的就不是真实路径了。
        """
        d = dup_client.get("/api/accidents/a1").get_json()["data"]
        assert len(d["images"]) == 3
        assert d["images_available"] is True
        assert d["images_state"] == "ready"
        first = d["images"][0]
        assert first["url"] == "/media/imageV3/a1/1.jpg"
        assert first["analyzed"] is True
        assert first["phase2_consistency"] == "一致"
        assert d["images"][1]["analyzed"] is False

    def test_图片按版本排序且带角标(self, dup_client):
        """最终版排最前，历史版本在后，每条带展示用角标。

        排序不是小事：同一事故跨版本都有图时（主库 57 个），读者先看到的
        必须是最终采用的那一版。
        """
        items = dup_client.get("/api/accidents/a1").get_json()["data"]["images"]
        assert [it["version"] for it in items] == ["v3", "v3", "v2"]
        assert [it["version_badge"] for it in items] == ["最终版", "最终版", "历史版本"]
        assert items[2]["url"] == "/media/imageV2/a1/old.jpg"

    def test_vlm_文本剥掉_markdown_强调(self, dup_client):
        """VLM 返回里夹 `**` 强调标记，那是模型的输出格式不是字段语义。

        括号里的补充说明（主库实测有「实为人工拆除作业被误报为坍塌」这种）
        是分析结论的一部分，剥标记时不能连它一起丢。
        """
        d = dup_client.get("/api/accidents/a1").get_json()["data"]
        assert d["images"][0]["phase1_scene_type"] == "坍塌现场"

        assert images.clean_vlm_text("**坍塌**") == "坍塌"
        assert images.clean_vlm_text("__一致__") == "一致"
        assert images.clean_vlm_text(None) == ""
        assert (images.clean_vlm_text("**其他**（或疑似**火灾/坍塌**类事故）")
                == "其他（或疑似火灾/坍塌类事故）")

    def test_共用图片的判据是_3_条事故(self, dup_client):
        """同一张图配给多条事故时打标，阈值取 3。

        三版合并后实测 951 条里 356 条（37.4%）出现在多个事故目录。92 组里
        48 组只有 2 条——那可能真的是两条事故共用一张图（同一工程的分支
        事故、转载配图）；覆盖 ≥3 条的 44 组，事故还按地域聚堆（武汉一批、
        广州一批），来源 URL 里带 `n.sinaimg.cn/default/`，那才是图床返回
        的默认占位图。

        **打标不隐藏**——数据里就是这样，藏起来反而让人以为每条事故都有
        独立的现场照片。
        """
        def add(accident_id):
            con = sqlite3.connect(sdb.resolve_staging_path())
            con.execute(
                "INSERT INTO image_manifest (accident_id, rel_path, filename, "
                "ext, bytes, sha1, analyzed, version, manifest_at) VALUES "
                "(?,?,?,'jpg',10,'sha-one',0,'v3','x')",
                (accident_id, f"imageV3/{accident_id}/9.jpg", "9.jpg"))
            con.commit()
            con.close()

        def shared():
            d = dup_client.get("/api/accidents/a1").get_json()["data"]
            return [it["shared"] for it in d["images"]]

        # 三条：v3 的 1.jpg / 2.jpg，v2 的 old.jpg（哈希独立，不参与判定）
        assert shared() == [False, False, False], "sha-one 一开始只有 a1 一条"

        add("a2")
        assert shared() == [False, False, False], "2 条事故共用不打标"

        add("a3")
        assert shared() == [True, False, False], "3 条事故共用才打标"

    def test_工作库不可用时降级不报错(self, client):
        """工作库不存在时给空清单 + available=false。

        「这条事故没有图片」与「图片索引还没重建」是两件事，前端要能分开
        处理——前者是数据事实，后者要去跑 `build_image_manifest.py`。

        第四十轮把它再分了一层：这次 `client` 的工作库路径指向
        `tmp_path/no-staging.db`（**文件根本不存在**），报的是
        `unavailable` 而不是 `empty`——两者的下一步动作不同（先跑
        `create_staging_db.py` 还是直接跑 `build_image_manifest.py`）。
        """
        d = client.get("/api/accidents/a1").get_json()["data"]
        assert d["images"] == []
        assert d["images_available"] is False
        assert d["images_state"] == "unavailable"

    @pytest.mark.parametrize("state,as_bool", [
        ("ready", True), ("empty", False), ("unavailable", False)])
    def test_详情接口把三态投影成布尔与state(self, tiny_db, monkeypatch,
                                              state, as_bool):
        """`detail.py` 只调一次 `availability()`，由同一个值派生两个字段。

        分两次调会让工作库的状态在两条语句之间漂移（比如清单脚本正好跑到
        一半），响应里两个字段就自相矛盾了。这条把「同源」钉住。
        """
        monkeypatch.setattr(images, "availability", lambda: state)
        c = create_app({"TESTING": True}).test_client()

        d = c.get("/api/accidents/a1").get_json()["data"]
        assert d["images_state"] == state
        assert d["images_available"] is as_bool

    def test_图片统计(self, dup_client):
        d = dup_client.get("/api/images/stats").get_json()["data"]
        assert d["files"] == 3                     # 三版合并口径，不截到 v3
        assert d["accidents_with_images"] == 1
        assert d["analyzed"] == 1                  # 只有 v3 的 1.jpg 有结论
        assert d["accidents_total"] == 10          # tiny 库的事故总数
        assert d["coverage"] == pytest.approx(0.1)
        # a1 有 v3 的图，所以它不是「只靠历史版本」——这个计数得是 0 而不是
        # 把 a1 也算进去
        assert d["accidents_with_images_legacy_only"] == 0
        assert d["by_ext"] == [{"ext": "jpg", "count": 3}]
        assert d["available"] is True


class TestApi:

    def test_页面与静态资源可访问(self, client):
        for path in ("/", "/accident/a1", "/static/app.css", "/static/dom.js",
                     "/static/overview.js", "/static/accident.js"):
            assert client.get(path).status_code == 200, path

    def test_health_报出库路径与表(self, client):
        body = client.get("/api/health").get_json()
        assert body["ok"] is True, "库能查到就算健康，索引缺失不算不健康"
        assert body["tables"]["accidents"] == 10
        assert body["tables"]["caics_causes"] == 6

    def test_health_缺索引时给出提示(self, client):
        body = client.get("/api/health").get_json()
        assert body["indexes_ok"] is False, "小库没建索引"
        assert "idx_cause_acc" in body["missing_indexes"]
        assert "add_indexes.py" in body["hint"]

    def test_overview_结构完整(self, client):
        body = client.get("/api/overview").get_json()
        for key in ("totals", "evidence_quality", "severity", "accident_type",
                    "year", "source", "caics_layer", "caics_catalog",
                    "province_group", "data_quality", "meta"):
            assert key in body, key

    def test_accidents_带分页元信息(self, client):
        body = client.get("/api/accidents?size=3").get_json()
        assert body["meta"] == {
            "total": 10, "page": 1, "size": 3, "pages": 4,
            "sort": "date_desc", "filters": body["meta"]["filters"],
            "has_more": True,
        }
        assert len(body["items"]) == 3

    def test_accidents_参数透传(self, client):
        body = client.get("/api/accidents?province=__overseas__").get_json()
        assert body["meta"]["total"] == 2

    def test_filters_含四类选项(self, client):
        body = client.get("/api/filters").get_json()
        assert set(body) == {"province", "severity", "accident_type",
                             "project_type", "construction_phase", "sort"}

    def test_province_audit_列出非标准值(self, client):
        body = client.get("/api/province/audit").get_json()
        raws = {r["raw"] for r in body["rows"]}
        assert "鄂州市五里墩建设工地" in raws
        assert "四川省" not in raws
        assert body["selfcheck"]["ok"] is True

    def test_中文不被转义(self, client):
        raw = client.get("/api/accidents?size=1").data.decode("utf-8")
        assert "四川省" in raw
        assert "\\u56db" not in raw

    def test_库不存在返回_503_而非_500(self, monkeypatch, tmp_path):
        monkeypatch.setenv(db.ENV_DB_PATH, str(tmp_path / "nope.db"))
        province.reset_cache()
        overview.reset_cache()
        r = create_app({"TESTING": True}).test_client().get("/api/overview")
        assert r.status_code == 503
        assert r.get_json()["error"] == "database_not_found"

    def test_只读连接拒绝写操作(self, tiny_db):
        con = db.connect_readonly()
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO accidents (id) VALUES ('x')")
        con.close()

    def test_工作库缺_promote_log_时审核端点不崩(self, dup_client):
        """早期版本建的工作库没有 `promote_log`（实测：schema 停在 v1.0）。

        审核队列与漏斗统计都要读它，缺表等价于「从没并入过任何东西」，应当
        返回空集。这两条都在页面主路径上，不该为一张空表把整页打成 500 ——
        2026-10-01 实测确实崩过：`/api/review/queue` 与 `/api/review/stats`
        双双 `OperationalError: no such table: promote_log`。
        """
        con = sqlite3.connect(sdb.resolve_staging_path())
        con.execute("DROP TABLE IF EXISTS promote_log")
        con.commit()
        con.close()

        for path in ("/api/review/queue", "/api/review/stats",
                     "/api/review/history"):
            assert dup_client.get(path).status_code == 200, path


# ══════════════════════════════════════════════════════════════
# 致因分析页（蓝图 05 §4.2）
# ══════════════════════════════════════════════════════════════


class TestCauses:
    """小库 10 条事故 / 6 条编码（覆盖 5 条事故、5 个类别）。

    编码分布刻意造成：M1 与 H1 落在同一事故（唯一一对共现）、
    三级证据各有、一个类别两次出现（M 层 2 条）。共现对数 1 远低于
    `MIN_PAIR_COUNT`，正好验证门槛生效时 `pairs` 为空但矩阵仍有值。
    """

    def test_framework_按层给出类目(self, client):
        d = client.get("/api/causes/framework").get_json()["data"]
        assert [l["key"] for l in d["layers"]] == ["M", "F", "T", "H", "C"]
        assert [l["count"] for l in d["layers"]] == [2, 1, 1, 2, 0]
        assert d["total"] == 6
        assert d["covered_accidents"] == 5

    def test_framework_层计数等于类目计数之和(self, client):
        d = client.get("/api/causes/framework").get_json()["data"]
        for layer in d["layers"]:
            assert layer["count"] == sum(c["count"] for c in layer["categories"])

    def test_framework_类目含名称与计数(self, client):
        """页面拿它当 19 类明细的骨架，缺 name 就只能显示编码。"""
        d = client.get("/api/causes/framework").get_json()["data"]
        codes = {c["code"] for l in d["layers"] for c in l["categories"]}
        assert codes == {"M1", "M2", "F4", "T1", "H1", "H3"}
        for layer in d["layers"]:
            for c in layer["categories"]:
                assert c["name"], c
                assert c["count"] >= c["accidents"]

    def test_scope_两个口径都报(self, client):
        d = client.get("/api/causes/distribution").get_json()
        assert d["scope"] == {
            "accidents": 10, "causes": 6, "covered": 5, "pairs": 6,
            "without_causes": 5, "filtered": False,
        }

    def test_层序固定不随计数变化(self, client):
        """M/F/T/H/C 是体系的层序，不是排序结果。

        小库里 C 层计数为 0，若按计数排序它会被排到最前或最后并被
        「优化」掉——层序一旦随数据变化，读者就没法把它和论文里的
        金字塔对上。
        """
        by_layer = client.get("/api/causes/distribution").get_json()["data"]["by_layer"]
        assert [l["key"] for l in by_layer] == ["M", "F", "T", "H", "C"]
        assert by_layer[-1]["count"] == 0, "计数为 0 的层也要出现"

    def test_证据三级各有且无未归类(self, client):
        d = client.get("/api/causes/distribution").get_json()["data"]
        assert [e["count"] for e in d["evidence_quality"]] == [4, 1, 1]
        assert d["evidence_unknown"] == 0

    def test_类目计数与直接_SQL_对账(self, client, tiny_db):
        d = client.get("/api/causes/distribution").get_json()["data"]
        rows = db.query(
            "SELECT code, COUNT(*) AS n, COUNT(DISTINCT accident_id) AS a "
            "FROM caics_causes GROUP BY code"
        )
        got = {c["code"]: (c["count"], c["accidents"]) for c in d["by_category"]}
        assert got == {r["code"]: (r["n"], r["a"]) for r in rows}

    def test_类目占比合计为_1(self, client):
        d = client.get("/api/causes/distribution").get_json()["data"]
        assert d["total_causes"] == 6
        assert abs(sum(c["share"] for c in d["by_category"]) - 1.0) < 1e-9

    def test_筛选后收窄且各图同口径(self, client):
        d = client.get("/api/causes/distribution?accident_type=坍塌").get_json()
        assert d["scope"] == {
            "accidents": 5, "causes": 4, "covered": 3, "pairs": 4,
            "without_causes": 2, "filtered": True,
        }
        assert sum(c["count"] for c in d["data"]["by_category"]) == d["scope"]["causes"]

    def test_共现低于门槛时列表为空但矩阵仍给全量(self, client):
        """`min_count` 只截列表，不截矩阵。

        矩阵被截断会让人以为角落的类别从不共现——那是没数据，不是
        没有关系。
        """
        d = client.get("/api/causes/cooccurrence").get_json()["data"]
        assert d["pair_total"] == 1
        assert d["pairs"] == [], "唯一一对只出现 1 次，低于门槛"
        assert d["filter"]["min_count"] == 10
        assert len(d["matrix"]) == 6
        assert len(d["matrix"][0]) == 6

    def test_共现矩阵对称且对角为零(self, client):
        d = client.get("/api/causes/cooccurrence").get_json()["data"]
        n = len(d["matrix"])
        assert all(d["matrix"][i][j] == d["matrix"][j][i]
                   for i in range(n) for j in range(n))
        assert all(d["matrix"][i][i] == 0 for i in range(n)), "对角是自己与自己"

    def test_共现默认按_count_排序(self, client):
        """实测 lift 榜首是只有 24 条事故支撑的 C1+H4，首屏拿不到主线。"""
        assert client.get("/api/causes/cooccurrence").get_json()["data"]["sort"] == "count"
        assert client.get(
            "/api/causes/cooccurrence?order=lift"
        ).get_json()["data"]["sort"] == "lift"

    def test_共现非法排序参数退回默认(self, client):
        d = client.get("/api/causes/cooccurrence?order=乱写").get_json()["data"]
        assert d["sort"] == "count"

    def test_共现_top_参数被夹在合法区间(self, client):
        for raw, want in (("0", 1), ("9999", 100), ("abc", 20)):
            d = client.get(f"/api/causes/cooccurrence?top={raw}").get_json()["data"]
            assert len(d["pairs"]) <= want

    def test_timeline_年份连续且补零(self, client):
        """1990 与 1991 之间没有记录也要出现，否则折线会直接连过去。"""
        d = client.get("/api/causes/timeline").get_json()["data"]
        assert d["years"][0] == "1990" and d["years"][-1] == "2024"
        assert len(d["years"]) == 35
        assert all(int(d["years"][i + 1]) == int(d["years"][i]) + 1
                   for i in range(len(d["years"]) - 1))
        assert d["accidents"][1] == 0, "1991 年无记录，补零"

    def test_timeline_报出无日期的条数(self, client):
        """无日期的事故落不到任何年份。

        不报出来，读者会拿它去和口径条上的事故总数相减然后找不到差额——
        小库里就是 a8 那一条。
        """
        d = client.get("/api/causes/timeline").get_json()["data"]
        assert d["without_date"] == 1
        assert sum(d["accidents"]) + d["without_date"] == 10

    def test_timeline_按层给覆盖事故数(self, client):
        d = client.get("/api/causes/timeline").get_json()["data"]
        assert [l["key"] for l in d["by_layer"]] == ["M", "F", "T", "H", "C"]
        for layer in d["by_layer"]:
            assert len(layer["counts"]) == len(d["years"])
        # 每层每年的事故数不可能超过当年的全部事故数
        assert all(layer["counts"][i] <= d["accidents"][i]
                   for layer in d["by_layer"] for i in range(len(d["years"])))

    def test_筛选_multi_值可重复传参(self, client):
        d = client.get(
            "/api/causes/distribution?accident_type=坍塌&accident_type=触电"
        ).get_json()
        # 坍塌 5 条 + 触电 1 条（a4，有一编码）
        assert d["scope"]["accidents"] == 6
        assert d["scope"]["causes"] == 5

    def test_零结果不崩且各图给空数组(self, client):
        """筛选到空集时四张图必须给出可渲染的空结构，而不是 None 或多一个键。"""
        q = "?accident_type=根本不存在的类型"
        dist = client.get("/api/causes/distribution" + q).get_json()
        assert dist["scope"]["accidents"] == 0
        assert [l["count"] for l in dist["data"]["by_layer"]] == [0] * 5
        assert dist["data"]["by_category"] == []
        assert dist["data"]["total_causes"] == 0, "分母为 0 时占比不能算出一堆 NaN"
        assert all(e["count"] == 0 for e in dist["data"]["evidence_quality"])

        tl = client.get("/api/causes/timeline" + q).get_json()["data"]
        assert tl == {"years": [], "accidents": [], "by_layer": [], "without_date": 0}

        co = client.get("/api/causes/cooccurrence" + q).get_json()["data"]
        assert co["pairs"] == [] and co["codes"] == [] and co["matrix"] == []

    def test_致因页与静态资源可访问(self, client):
        for path in ("/causes", "/static/causes.js", "/static/dom.js"):
            assert client.get(path).status_code == 200, path
        html = client.get("/causes").data.decode("utf-8")
        assert 'id="causes"' in html
        assert "causes.js" in html

    def test_两页导航互链(self, client):
        html = client.get("/causes").data.decode("utf-8")
        assert 'href="/"' in html
        home = client.get("/").data.decode("utf-8")
        assert 'href="/causes"' in home

    def test_致因页不引外部资源(self, client):
        """内网与外网断开时页面必须完整可用（蓝图 05 验收 13）。

        只认真正会发起请求的写法。`causes.js` 里的
        `http://www.w3.org/2000/svg` 是 SVG 命名空间标识符，不联网，
        按整串 "http://" 搜会误报。
        """
        for path in ("/causes", "/"):
            html = client.get(path).data.decode("utf-8")
            assert 'src="http' not in html, path
            assert 'href="http' not in html, path
        css = client.get("/static/app.css").data.decode("utf-8")
        assert "url(http" not in css and "@import" not in css

    def test_页面文案不含禁用措辞(self, client):
        """共现是关联不是因果（蓝图 05 待决项 B / CLAUDE.md 禁用措辞）。"""
        js = client.get("/static/causes.js").data.decode("utf-8")
        for word in ("导致", "因果", "引起", "证明", "专家级", "替代人工"):
            assert word not in js, word


# ══════════════════════════════════════════════════════════════
# 静态资源里踩过的坑
# ══════════════════════════════════════════════════════════════


class TestStaticAssets:
    """这些是浏览器布局行为，pytest 里跑不到渲染，只能按约定钉住。

    两条都在本轮的端到端验证里真实发生过，不是预防性写法。
    """

    def test_列表容器在宽屏下不做滚动容器(self, client):
        """sticky 表头的包含块，就是离它最近的滚动容器。

        `.table-wrap` 一旦写上 overflow-x，它就成了滚动容器：表头既不跟随
        视口滚动（sticky 白设），又被 `top:57px` 从容器顶再往下推 57px，
        压住首行数据。实测表头落在 938–977、首行落在 920–983，标题的中心点
        被表头吃掉 —— 列表里的标题因此点不进详情页。表格最小需要 738px，
        宽屏本来就装得下；窄屏在 `@media (max-width: 790px)` 里才切回滚动
        容器，并同时把表头改回 static（两者不能在同一个元素上并存）。
        """
        css = client.get("/static/app.css").data.decode("utf-8")
        assert ".table-wrap {" in css, "样式表里找不到 .table-wrap 规则"

        parts = css.split(".table-wrap {")
        default_body = parts[1].split("}", 1)[0]
        assert "overflow" not in default_body, (
            "默认规则里出现 overflow 会把 .table-wrap 变成滚动容器，"
            "表头将被下推 57px 并压住首行"
        )

        assert len(parts) >= 3, "窄屏断点里少了 .table-wrap 的横向滚动规则"
        narrow_body = parts[2].split("}", 1)[0]
        assert "overflow-x: auto" in narrow_body

        # 用 rindex 而不是 split：这段断点名在上面的注释里也出现过一次，
        # 从第一次出现处切会切到注释中间。
        i = css.rindex("@media (max-width: 790px)")
        assert "position: static" in css[i:], (
            "窄屏切到滚动容器后必须把表头改回 static，否则又是同一个错位"
        )

    def test_列表标题是指向详情页的链接(self, client):
        """蓝图 05 的三条用户路径之一：列表 → 详情 → 返回。

        用 <a href> 而不是点击事件：中键新开、右键复制链接、浏览器前进
        后退都跟着能用，而详情页的返回按钮正靠 history.back() 回到带筛选
        状态的列表。
        """
        js = client.get("/static/overview.js").data.decode("utf-8")
        assert "title-main" in js
        assert "'/accident/'" in js, "标题没有拼成详情页链接"


# ══════════════════════════════════════════════════════════════
# 主库对账（库不在则跳过）
# ══════════════════════════════════════════════════════════════

MAIN_DB = Path("/home/suntianhao/construction_agent/data/caiems_v2.db")

pytestmark_main = pytest.mark.skipif(
    not MAIN_DB.exists(), reason="主库不在本机"
)


@pytest.fixture
def main_db(monkeypatch):
    monkeypatch.setenv(db.ENV_DB_PATH, str(MAIN_DB))
    province.reset_cache()
    overview.reset_cache()
    yield MAIN_DB
    province.reset_cache()
    overview.reset_cache()


@pytestmark_main
class TestMainDbParity:
    """蓝图 07 的 M1 验收数字。数字来自 2026-09-30 的实测快照。"""

    def test_总数(self, main_db):
        assert overview.build()["totals"] == {
            "accidents": 2623, "deaths": 5894, "injuries": 5796,
            "deaths_recorded": 2280, "injuries_recorded": 1227,
            "casualty_any": 2474, "causes": 16323, "causes_covered": 2618,
        }

    def test_证据三级与论文对账一致(self, main_db):
        ev = {e["key"]: e["count"] for e in overview.build()["evidence_quality"]}
        assert ev == {"direct_quote": 14784, "indirect_infer": 1117, "weak_infer": 422}

    def test_五层分布(self, main_db):
        got = {x["key"]: x["count"] for x in overview.build()["caics_layer"]}
        assert got == {"M": 7077, "H": 4723, "F": 2799, "T": 1533, "C": 191}

    def test_省份分组(self, main_db):
        pg = overview.build()["province_group"]
        assert pg["by_group"] == {
            "standard": 2559, "overseas": 46, "unnormalized": 4, "empty": 14,
        }
        assert len(pg["standard"]) == 31
        assert pg["distinct_raw_values"] == 78

    def test_归一自检不变量(self, main_db):
        assert province.selfcheck()["ok"] is True

    def test_十九类齐全(self, main_db):
        cat = overview.build()["caics_catalog"]
        assert len(cat) == 19
        assert {c["layer"] for c in cat} == {"M", "F", "T", "H", "C"}

    def test_列表分页(self, main_db):
        r = accidents_query.run(accidents_query.ListParams())
        assert r["meta"]["total"] == 2623
        assert r["meta"]["pages"] == 53
        assert len(r["items"]) == 50

    def test_境外桶命中数(self, main_db):
        r = accidents_query.run(accidents_query.ListParams(province=["__overseas__"]))
        assert r["meta"]["total"] == 46

    def test_无编码的事故(self, main_db):
        r = accidents_query.run(accidents_query.ListParams(has_causes=False))
        assert r["meta"]["total"] == 5

    def test_年份序列可见断档(self, main_db):
        year = overview.build()["year"]
        assert len(year) == 49
        assert all(y["count"] == 0 for y in year if 1991 <= int(y["key"]) <= 2004)

    def test_工序编码全部在枚举表(self, main_db):
        """`work_task` 的取值必须全部落在当前枚举体系内。

        只要有一个编码不在 `enum_definitions` 里，就说明旧体系的值混了
        进来——这正是 `work_task_enum` 列的问题（703 条 WT11–WT15）。
        """
        names = accidents_query.enum_names("work_task")
        assert len(names) == 11
        rows = db.query(
            "SELECT DISTINCT work_task AS v FROM accidents "
            "WHERE work_task IS NOT NULL AND TRIM(work_task) != ''"
        )
        assert rows, "主库工序列不该是空的"
        assert sorted(r["v"] for r in rows if r["v"] not in names) == []

    def test_详情抽样与直接_SQL_对账(self, main_db):
        """逐条 build 与直接查库对账。

        `LIMIT 40` 固定按 id 取，不是随机抽样——随机抽样在 CI 上会偶发
        通过、偶发失败，问题被埋掉。前 40 条已覆盖重复标注、无证据、
        无编码等各形态（实测这条 SQL 命中的行里 22 组有重复编码）。
        """
        ids = [r["id"] for r in db.query(
            "SELECT id FROM accidents ORDER BY id LIMIT 40"
        )]
        assert len(ids) == 40
        for aid in ids:
            got = detail.build(aid)
            n_rows = db.scalar(
                "SELECT COUNT(*) FROM caics_causes WHERE accident_id = ?",
                (aid,), default=0,
            )
            n_distinct = db.scalar(
                "SELECT COUNT(DISTINCT code) FROM caics_causes WHERE accident_id = ?",
                (aid,), default=0,
            )
            assert got["causes"]["citations"] == n_rows, aid
            assert got["causes"]["distinct"] == n_distinct, aid
            assert sum(c["citation_count"] for c in got["causes"]["items"]) == n_rows, aid

    def test_计数两个口径的关系(self, main_db):
        """16,323 行 vs 15,985 个 `(accident_id, code)`。

        差 338 = 616 行重复 − 278 组。两个数字都对，只是口径不同：
        论文与概览页统计卡用**行数**（措辞「16,323 条致因标注」），
        事故级分析（共现、每事故编码数）必须用**去重口径**。
        """
        rows = db.scalar("SELECT COUNT(*) FROM caics_causes", default=0)
        pairs = db.scalar(
            "SELECT COUNT(*) FROM (SELECT DISTINCT accident_id, code FROM caics_causes)",
            default=0,
        )
        dup_rows = db.scalar(
            "SELECT COALESCE(SUM(n), 0) FROM ("
            "SELECT COUNT(*) AS n FROM caics_causes "
            "GROUP BY accident_id, code HAVING COUNT(*) > 1)", default=0,
        )
        dup_groups = db.scalar(
            "SELECT COUNT(*) FROM (SELECT 1 FROM caics_causes "
            "GROUP BY accident_id, code HAVING COUNT(*) > 1)", default=0,
        )
        assert (rows, pairs) == (16323, 15985)
        assert rows - pairs == dup_rows - dup_groups == 338

    def test_共现用去重口径(self, main_db):
        """H1+M1 按行数 1,968、按事故级去重 1,821。

        概览页的统计卡是行数口径，共现矩阵若照抄行数就会把「同一事故
        里同一对编码被标了两次」算成两次共现。
        """
        by_rows = db.scalar(
            "SELECT COUNT(*) FROM caics_causes a JOIN caics_causes b "
            "ON a.accident_id = b.accident_id "
            "WHERE a.code = 'H1' AND b.code = 'M1'", default=0,
        )
        by_pairs = db.scalar(
            "SELECT COUNT(*) FROM (SELECT DISTINCT accident_id, code FROM caics_causes) a "
            "JOIN (SELECT DISTINCT accident_id, code FROM caics_causes) b "
            "ON a.accident_id = b.accident_id "
            "WHERE a.code = 'H1' AND b.code = 'M1'", default=0,
        )
        assert by_rows == 1968
        assert by_pairs == 1821

    def test_工序覆盖与缺失(self, main_db):
        """有值 2,202 条 / 缺 421 条（16%）。缺失是留白，不写成「未标注」。"""
        total = db.scalar("SELECT COUNT(*) FROM accidents")
        filled = db.scalar(
            "SELECT COUNT(*) FROM accidents "
            "WHERE work_task IS NOT NULL AND TRIM(work_task) != ''"
        )
        assert (total, filled) == (2623, 2202)
        assert total - filled == 421

    # ── 致因分析页（蓝图 05 §4.2）──────────────────────────────

    def test_causes_体系与直接_SQL_逐项对账(self, main_db):
        d = causes.framework()
        assert len(d["layers"]) == 5
        assert d["total"] == 16323
        assert d["covered_accidents"] == 2618
        raw = {(r["code"], r["name"], r["count"]) for r in db.query(
            "SELECT code, MAX(name) AS name, COUNT(*) AS count "
            "FROM caics_causes GROUP BY code"
        )}
        got = {(c["code"], c["name"], c["count"])
               for layer in d["layers"] for c in layer["categories"]}
        assert got == raw

    def test_causes_层计数与概览一致(self, main_db):
        """同一批数字不在两处各算一遍——这正是旧代码 6 份副本的老毛病。"""
        fw = {l["key"]: l["count"] for l in causes.framework()["layers"]}
        ov = {l["key"]: l["count"] for l in overview.build()["caics_layer"]}
        assert fw == ov == {"M": 7077, "H": 4723, "F": 2799, "T": 1533, "C": 191}

    def test_causes_类目按层内计数降序(self, main_db):
        """层内顺序是页面上 19 类明细的固定骨架。

        若顺序由 `caics_catalog` 的原始顺序（按 code 排）决定，页面上会
        出现「M3 的安全培训不足 1,200 排在 M4 的制度规程漏洞 1,390 之前」——
        读者会以为条形图排序坏了。
        """
        for layer in causes.framework()["layers"]:
            counts = [c["count"] for c in layer["categories"]]
            assert counts == sorted(counts, reverse=True), layer["key"]

    def test_causes_分布两个口径分列(self, main_db):
        d = causes.distribution(accidents_query.ListParams())
        assert d["total_causes"] == 16323
        assert sum(c["count"] for c in d["by_category"]) == 16323, "行数口径"
        assert sum(c["accidents"] for c in d["by_category"]) == 15985, "去重口径"
        assert all(c["accidents"] <= c["count"] for c in d["by_category"])

    def test_causes_证据三级与论文一致(self, main_db):
        d = causes.distribution(accidents_query.ListParams())
        assert [e["count"] for e in d["evidence_quality"]] == [14784, 1117, 422]
        assert d["evidence_unknown"] == 0

    def test_causes_共现取去重口径(self, main_db):
        """行数口径 1,968、事故级 1,821。差 147 来自同一事故同一对码标多次。"""
        co = causes.cooccurrence(accidents_query.ListParams(), top=1)
        assert (co["pairs"][0]["a"], co["pairs"][0]["b"], co["pairs"][0]["count"]) \
            == ("H1", "M1", 1821)
        assert co["sort"] == "count", "默认按共现事故数，不是 lift"

    def test_causes_共现矩阵给全量(self, main_db):
        co = causes.cooccurrence(accidents_query.ListParams(), top=1)
        n = len(co["codes"])
        assert n == 19 and len(co["matrix"]) == n
        assert co["pair_total"] > len(co["pairs"]), "矩阵不受 top 与门槛限制"
        assert max(max(row) for row in co["matrix"]) == 1821

    def test_causes_年代趋势四十九年且报出无日期(self, main_db):
        t = causes.timeline(accidents_query.ListParams())
        assert len(t["years"]) == 49
        assert t["years"][0] == "1978" and t["years"][-1] == "2026"
        assert sum(t["accidents"]) == 2593
        assert t["without_date"] == 30
        assert sum(t["accidents"]) + t["without_date"] == 2623, "要和事故总数对得上"
        assert all(t["accidents"][t["years"].index(y)] == 0
                   for y in t["years"] if 1991 <= int(y) <= 2004)
