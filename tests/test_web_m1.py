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

from construction_agent.web import accidents_query, db, detail, overview, province
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
    project_type TEXT, construction_phase TEXT,
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
    "deaths,injuries,trapped_count,project_type,construction_phase,source_name,"
    "summary,direct_cause,work_task"
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
         source_name="safehoo", direct_cause="边坡未按规定放坡",
         work_task="WT01"),
    dict(id="a2", title="成都高处坠落事故", date="2023-05-05", province="四川省",
         accident_type="高处坠落", severity="一般事故", deaths=0, injuries=1,
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
def dup_client(tiny_db):
    """在 tiny_db 上追加「同一编码多条标注」与图片记录。

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
    con.execute(
        "INSERT INTO image_analyses (accident_id,local_path,source_url,"
        "phase1_scene_type,phase1_damage,phase2_consistency) "
        "VALUES ('a1','data/imageV3/a1/1.jpg','http://example.invalid/1.jpg',"
        "'坍塌现场','结构破坏','一致')"
    )
    con.execute(
        "INSERT INTO image_analyses (accident_id,local_path,phase1_scene_type) "
        "VALUES ('a1','data/imageV3/a1/2.jpg','救援现场')"
    )
    con.commit()
    con.close()
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
        assert a["total"] == 11
        # a1 有值 8 项：title/date/province/type/severity/deaths/injuries/direct_cause
        # 缺 3 项：city / accident_location_detail / summary
        assert a["available"] == 8
        d = tiers[3]
        assert "trapped_count" in d["fields"]
        assert "weather" in d["fields"]

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
                   ["data"]["images"]) == 2
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
