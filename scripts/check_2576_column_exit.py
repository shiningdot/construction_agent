#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.76 展示层对主库的读取面审计：133 列里哪些真的进过 `SELECT`。

## 来路

`construction_agent/web/db.py:41-48` 的 `REQUIRED_TABLES` 注释里写着一句话，
**从来没有核过**：

> `source_urls` / `search_logs` / `processing_log` / `image_files` /
> `image_analyses` 这些展示层一次都没查过

这句话是「只需三张内容表」这个结论的理由。理由没核过，结论就是悬空的。
本节把它测成事实，顺带补上它没说的另一半：**另外三张表里也有列从未进过结果集**
（`caics_causes` 1 列、`enum_definitions` 9 列、`pipeline_metadata` 1 列）。

## 判据怎么立：实测，不 grep

不 grep 源码。裸子串判据在本项目已发作三次（§2.5.63 的 `INSTR(col,'\\t')` 命中了
LaTeX 命令名 `\\times`；§2.5.66 的 `.base_url` 命中了别的类的同名字段；§2.5.75 的
`"bottom: 18px" not in css` 命中了 `.layer-group` 的 `margin-bottom`）。

改成**实测真跑出来的 SQL**：`web/db.py` 的 `connect_readonly` 是主库的**唯一读入口**
（`read_conn` / `query` / `query_one` / `scalar` 四个全部从它派生），用代理对象把它包住，
记下每条 SQL 的原文与 `cursor.description`，再用 `app.test_client()` 走遍全部 GET 路由
（含参数变体与真实 accident id）。

判读只认 `cursor.description` —— **它不可能撒谎**：列没被 `SELECT` 就不可能出现在
结果集里。代价是它**看不见约束面**（`WHERE` / `JOIN` / `ORDER BY` 用到的列不进结果集），
所以另记一份「SQL 文本面」当**弱判据**。两边的宽度都写在输出里。

## 五档分类（每一列恰好属于一档）

| 档 | 含义 | 证据强度 |
|---|---|---|
| `read` | 被**显式列清单**选出过 | 强（结果集） |
| `star` | **只**因 `SELECT *` 被带回 | 强，但说明「出口是顺带的」 |
| `filter` | 只出现在 `WHERE` / `JOIN` / `ORDER BY` 里 | 弱（SQL 文本） |
| `zero` | 有值、却没有任何出口 | ——（本节要解释的就是它） |
| `empty` | 值全空（§2.5.65 已定性为「有名无实」） | 自动免检 |

`accidents` 的出口面由 §2.5.67 核过（`check_field_alignment.py` 的 `CONTENT_TIERS` /
`META_FIELDS` / `DETAIL_NOT_SHOWN` + 前端读取面），本节只记一条它没记的事实：
**48 列全部进过结果集，其中 32 列只由 `SELECT *` 带回、从未被显式选出**。
引已有凭据而不是重做一遍（§2.5.52 立的规矩）。

## 四段

    A. 实测读取面：逐表逐列分类 + 剔除的聚合列/别名数（判据的边界要写出来）
    B. 登记面 ↔ 实测面逐格对账 + 元判据（不重不漏）+ 判据自身的覆盖面自证
    C. 注入式自证：改真实源码三例，断言**红的恰好是哪一个列**（双向：收紧与放宽）
    D. 文档 ↔ 实测

口径：`app.test_client()`，不起浏览器。**为什么够**：每个 XHR 也只能打到已注册的路由，
而这里把**全部** GET 路由都打了一遍；真浏览器那一轮的实测对照（154 条 SQL vs
test_client 51 条）**结果集列名集合完全相同**，见 §2.5.76 第七节。
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root          # noqa: E402
from _serve import point_at                               # noqa: E402

DOC = ROOT / "docs" / "web" / "01_在线采集.md"
WEB = ROOT / "construction_agent" / "web"
DETAIL = WEB / "detail.py"
AQUERY = WEB / "accidents_query.py"
MANIFEST = ROOT / "scripts" / "build_image_manifest.py"
ENUMS = ROOT / "docs" / "enums"
FA = ROOT / "scripts" / "check_field_alignment.py"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"

MAIN_DB = main_db_path()
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

# 结果列里的聚合/派生列（`COUNT(*)`、`COUNT(DISTINCT accident_id)`、`substr(...)`）
# 不是列，剔除；带 `AS 名字` 的是别名，剔除。这两条剔除是判据的**边界**，
# 不是细节：不剔就会把 `COUNT(DISTINCT accident_id)` 当成「读了 accident_id」，
# 把 `evidence_quality AS key` 当成「读了 key」。
_AGG = re.compile(r"^[A-Za-z_]+\s*\(|^substr\s*\(", re.I)
_TOUCH = re.compile(r"\b(?:from|join)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.I)
_STAR = re.compile(r"\bselect\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?\*", re.I)
# `FROM/JOIN <表> [AS] <别名>` —— 有别名时按 `别名.列` 精确归属，
# 否则列表页那条 `SELECT a.id … FROM accidents a, (… caics_causes c …)`
# 里的 `id` 会因「两张表都有 id」被判歧义而**丢掉**（实测就是这样：
# 不加别名解析时 `accidents.id` 会掉进 `star` 档，是假话）。
_ALIAS = re.compile(r"\b(?:from|join)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+"
                    r"(?:as\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.I)
_RESERVED = {"on", "where", "group", "order", "limit", "left", "inner", "join",
             "having", "as", "and", "or", "using", "select", "union", "not"}

# ══════════════════════════════════════════════════════════════
# 登记面
#
# 硬编码是**故意的**：「登记」这件事的意义就在于有人写过它。跑出来的实测面与它
# 逐格相等才算通过；哪天有人收紧了某条 SELECT，差额会自己冒出来。
# ══════════════════════════════════════════════════════════════

#: `accidents` 被**显式**选出的列（`accidents_query.py:98-106` 的 `LIST_COLUMNS`
#: 与 `accidents_query.py:439` 的分布查询）。
ACC_EXPLICIT = [
    "id", "title", "source_name", "date", "accident_time", "province", "city",
    "district", "project_type", "construction_phase", "work_task",
    "accident_type", "severity", "deaths", "injuries", "trapped_count",
]

#: `accidents` **只**由 `SELECT *` 带回、从未被显式选出的列。
#: 它们能到前端，靠的是 `detail.py:129` 的 `SELECT *` —— **顺带**，不是指名。
#: 32 列里包含 `raw_text`（详情页正文原文）、`summary`、`direct_cause`、
#: `work_task_enum` —— 全是页面上真在显示的东西。
ACC_STAR_ONLY = [
    "source_url", "crawl_date", "publish_date", "accident_location_detail",
    "weather", "project_name", "building_structure", "developer", "contractor",
    "contractor_type", "supervision_company", "direct_economic_loss",
    "collapse_subtype", "fall_height", "equipment_involved", "material_involved",
    "worker_type", "direct_cause", "report_type", "text_type",
    "classification_verdict", "classification_confidence", "source_count",
    "summary", "raw_text", "search_enriched_text", "search_performed",
    "pipeline_version", "step1_duration_ms", "step2_duration_ms", "created_at",
    "work_task_enum",
]

#: 其余八张表里**进过结果集**的列。
READ = {
    "caics_causes": ["code", "name", "evidence", "inference",
                     "evidence_quality", "sort_order"],
    "enum_definitions": ["code", "name"],
    "pipeline_metadata": ["key", "value"],
}

#: 只在 `WHERE` / `JOIN` / `ORDER BY` 里出现的列（约束面，弱判据）。
FILTER = {
    "caics_causes": {
        "accident_id": "`WHERE accident_id = ?` 与关联子查询的 JOIN 条件",
        # 🔴 第一版把 `id` 登记成"零出口"（理由：自增主键，展示层不需要行号），
        # 实测却是 `filter`——`_SQL_CAUSES` 的 `ORDER BY sort_order, code, id`
        # 拿它当**并列键**。这不是"主键所以没人读"，是"读了、但只用于排序"。
        # 登记与实测不符的第一次暴露，正是本节要防的事。
        "id": "`ORDER BY sort_order, code, id` 的并列键（三键排序，防同序抖动）",
    },
    "enum_definitions": {"enum_type": "`WHERE enum_type = ?`（取某一类枚举）",
                         "is_active": "`AND is_active = 1`（只取启用行）"},
}

#: 整表零出口。键 = 表名，值 = (类别, 判据函数名, 理由)。
NO_EXIT_TABLES = {
    "image_files": (
        "materialized", "crit_materialized",
        "主库这份是**上游原始登记**；展示层的图片数据走工作库 `image_manifest`"
        "（1,771 行 / 18 列），而那张表正是 `build_image_manifest.py` 从本表与 "
        "`image_analyses` **物化**出来的（脚本 :438 取 `width, height`）。",
    ),
    "image_analyses": (
        "materialized", "crit_materialized",
        "同上：本表的 `phase1_scene_type` / `phase2_consistency` / `phase2_confidence` / "
        "`vlm_model` 被 `build_image_manifest.py:445-447` 抄进工作库 `image_manifest`，"
        "前端读的是那一份。",
    ),
    "processing_log": (
        "pipeline_audit", "crit_pipeline_audit",
        "整表记的是**跑批过程**（分类判定、各步耗时、字段数），不是事故的属性。"
        "展示层的需求是「事故长什么样」，不是「哪次跑批花了多久」。它在展示层唯一的"
        "出现是 `/api/health` 里 `for t in list_tables()` 的通用计数模板。",
    ),
    "search_logs": (
        "pipeline_audit", "crit_pipeline_audit",
        "同上：搜索增强的留痕（查询词、结果数、同源片段）。§2.5.64 已查明 1,636 行里 "
        "97.4% 毫无收获，这更说明它是**诊断材料**而不是展示内容。",
    ),
    "source_urls": (
        "no_producer", "crit_no_producer",
        "这张表是给**多源归并**准备的，而多源归并未发生（§2.5.65 实测："
        "`accidents.source_count` 2,623/2,623 恒 `'1'`，580 行只对应 45 个事故）。"
        "上游没跑通 ⇒ 没有出口。",
    ),
}

#: 表本身有出口、个别列没有。键 = (表, 列)，值 = (类别, 判据函数名, 理由)。
NO_EXIT_COLUMNS = {
    ("enum_definitions", "id"): ("auto", "crit_auto", "自增主键"),
    ("enum_definitions", "sort_order"): ("auto", "crit_auto", "库内排序键"),
    ("enum_definitions", "created_at"): ("auto", "crit_auto", "写入时间"),
    ("enum_definitions", "updated_at"): ("auto", "crit_auto", "更新时间"),
    ("pipeline_metadata", "created_at"): ("auto", "crit_auto", "写入时间"),
    ("enum_definitions", "definition"): (
        "editorial", "crit_editorial",
        "枚举的**操作定义明文**（57 条全有值）——标注侧/论文侧的口径记录；"
        "展示层只把 `code` 翻成中文名。",
    ),
    ("enum_definitions", "decision_rule"): (
        "editorial", "crit_editorial", "规则式判定口径（11 条有值）",
    ),
    ("enum_definitions", "version"): (
        "editorial", "crit_editorial", "口径版本号（`2.0-llm` 等）",
    ),
    ("enum_definitions", "sample_count"): (
        "editorial", "crit_editorial", "建表时该类的样本量",
    ),
    ("enum_definitions", "parent_code"): (
        "editorial", "crit_editorial", "层级父码（11 条有值）",
    ),
}

#: `build_image_manifest.py` 从主库抄进工作库的列（物化判据要核这个）。
MATERIALIZED = {
    "image_files": ["width", "height"],
    "image_analyses": ["phase1_scene_type", "phase2_consistency",
                       "phase2_confidence", "vlm_model"],
}

FAILS: list[str] = []
RECORDS: list[dict] = []


def ok(cond: bool, what: str, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '🔴'} {what}" + (f"  —— {detail}" if detail else ""))
    if not cond:
        FAILS.append(what)
    return bool(cond)


# ══════════════════════════════════════════════════════════════
# 测量
# ══════════════════════════════════════════════════════════════

def _ro(path=None):
    return sqlite3.connect(f"file:{path or MAIN_DB}?mode=ro", uri=True)


def _tables(con) -> list[str]:
    return sorted(r[0] for r in con.execute(
        "select name from sqlite_master where type='table' "
        "and name not like 'sqlite_%'"))


def _all_cols() -> dict[str, list[str]]:
    con = _ro()
    out = {t: [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
           for t in _tables(con)}
    con.close()
    return out


def _staging_cols() -> dict[str, list[str]]:
    if not STAGING_DB.exists():
        return {}
    con = _ro(STAGING_DB)
    try:
        return {r[0]: [x[1] for x in con.execute(f'PRAGMA table_info("{r[0]}")')]
                for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def _install_proxy():
    """把 `connect_readonly` 换成代理。必须在 `create_app()` **之前**调用。"""
    from construction_agent.web import db as wdb
    orig = wdb.connect_readonly

    class TracedConn:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, params=()):
            cur = self._con.execute(sql, params)
            try:
                cols = [d[0] for d in (cur.description or [])]
            except Exception:                              # noqa: BLE001
                cols = []
            RECORDS.append({"sql": " ".join(str(sql).split()), "cols": cols,
                            "star": bool(_STAR.search(str(sql)))})
            return cur

        def __getattr__(self, k):
            return getattr(self._con, k)

    wdb.connect_readonly = lambda db_path=None: TracedConn(orig(db_path))


def scan() -> tuple[dict, dict, dict, int, int, int]:
    """跑一遍展示层，返回 (逐列分类面, 全库列, 各表 SQL, 剔除聚合数, 剔除别名数, 路由数)。"""
    point_at(str(MAIN_DB), STAGING_DB)
    _install_proxy()

    from construction_agent.web.factory import create_app
    app = create_app()
    c = app.test_client()
    # aid 直接查库，**不走 `/api/accidents`**：注入式自证会把某条 SELECT 改窄，
    # 那种情况下 API 可能 500、`get_json()` 返回 None，于是整个 `--diff` 崩在
    # `None["items"]` 上、被外层当成"没有不一致"（实测踩过：注入 enum 那条时
    # 子进程抛 TypeError，用例静默退化成"全绿"）。
    con0 = _ro()
    aid = con0.execute("SELECT id FROM accidents LIMIT 1").fetchone()[0]
    con0.close()

    paths = []
    for rule in sorted({str(r) for r in app.url_map.iter_rules() if "GET" in r.methods}):
        if rule.startswith("/static/") or "<path:" in rule:
            continue
        paths.append(re.sub(r"<[^>]*>", aid, rule))
    paths += [
        "/api/accidents?sort=deaths&order=desc&per_page=50",
        "/api/accidents?severity=%E6%AD%BB%E4%BA%A1&has_causes=1&page=2",
        "/api/causes/cooccurrence?order=accidents&top=5",
        "/api/causes/distribution?layer=F",
        f"/accident/{aid}",
    ]
    for p in paths:
        c.get(p)
    n_paths = len(paths)
    del c

    cols = _all_cols()
    owners: dict[str, list[str]] = {}
    for t, cs in cols.items():
        for x in cs:
            owners.setdefault(x, []).append(t)

    tsql: dict[str, list[dict]] = {t: [] for t in cols}
    for r in RECORDS:
        for m in _TOUCH.finditer(r["sql"]):
            t = m.group(1).lower()
            if t in tsql and r not in tsql[t]:
                tsql[t].append(r)

    face: dict[tuple[str, str], str] = {}
    n_agg = n_alias = n_qual = n_ambig = 0
    for r in RECORDS:
        touched = {m.group(1).lower() for m in _TOUCH.finditer(r["sql"])}
        amap = {m.group(2).lower(): m.group(1).lower()
                for m in _ALIAS.finditer(r["sql"]) if m.group(2).lower() not in _RESERVED}
        for col in r["cols"]:
            if _AGG.search(col):
                n_agg += 1
                continue
            if re.search(r"\bas\s+" + re.escape(col) + r"\b", r["sql"], re.I):
                n_alias += 1
                continue
            # ① 优先按限定名归属（`a.id` → accidents）：精确，且能救回跨表同名列
            qual = {t for al, t in amap.items()
                    if re.search(r"\b" + re.escape(al) + r"\." + re.escape(col) + r"\b",
                                 r["sql"], re.I)}
            if len(qual) == 1 and (t := next(iter(qual))) in cols:
                k = (t, col)
                n_qual += 1
            else:
                # ② 退回落差法：该列的主人们 ∩ 这条 SQL 触及的表
                hit = [t for t in owners.get(col, []) if t in touched]
                if len(hit) != 1:
                    n_ambig += 1
                    continue                   # 歧义 / 跨表同名，不计证据
                k = (hit[0], col)
            if r["star"] and face.get(k) != "read":
                face[k] = "star"
            else:
                face[k] = "read"

    con = _ro()
    full: dict[tuple[str, str], str] = {}
    for t, cs in cols.items():
        text = "\n".join(r["sql"] for r in tsql.get(t, []))
        for col in cs:
            got = face.get((t, col))
            if got:
                full[(t, col)] = got
            elif re.search(r"\b" + re.escape(col) + r"\b", text, re.I):
                full[(t, col)] = "filter"
            else:
                n = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{col}" '
                                f'IS NOT NULL AND "{col}" != \'\'').fetchone()[0]
                full[(t, col)] = "zero" if n else "empty"
    con.close()
    scan.attr = (n_agg, n_alias, n_qual, n_ambig)      # type: ignore[attr-defined]
    return full, cols, tsql, n_agg, n_alias, n_paths


# ══════════════════════════════════════════════════════════════
# 各档的判据（可证伪，且**不重复**实测面）
# ══════════════════════════════════════════════════════════════

def crit_materialized(table: str) -> tuple[bool, str]:
    """物化判据：抄它的脚本还在、抄的列在工作库副本里。

    三处**互不相同**的来源（构建脚本 / 工作库 / 主库），任一处变动都会转红——
    这不是把实测面再说一遍。工作库不在时报红，不静默降级。
    """
    src = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
    if f"FROM {table}" not in src:
        return False, f"`build_image_manifest.py` 里找不到 `FROM {table}`"
    wk = _staging_cols()
    if not wk:
        return False, f"工作库不可达（{STAGING_DB}）——判据的源缺失，不得降级为通过"
    if "image_manifest" not in wk:
        return False, "工作库没有 `image_manifest` 表（拷贝目标不在）"
    miss = [c for c in MATERIALIZED[table] if c not in wk["image_manifest"]]
    if miss:
        return False, f"工作库 `image_manifest` 缺列 {miss}"
    return True, f"物化链成立：`{table}` → `image_manifest`，抄了 {MATERIALIZED[table]}"


def _frontend_blob() -> str:
    out = []
    for sub in ("templates", "static"):
        d = WEB / sub
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file() and f.suffix in (".html", ".js", ".css"):
                    out.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(out)


def crit_pipeline_audit(table: str) -> tuple[bool, str]:
    """审计表的判据：**它独有的列名**不得出现在前端。

    后端不供、前端也不期待 ⇒ 无出口是设计，不是遗漏。

    🔴 **「独有」的接收者必须收窄到「两个库的全部表」**（§2.5.63/66/75 的同一条教训，
    这里一次抓出 6 个假阳性）：第一版只排除了**工作库**的同名列，于是
    `pipeline_version`（`accidents` 也有）、`classification_verdict`（同）、
    `step1_duration_ms`（同）、`search_performed`（同）、`source_name`（同）、
    `text_type`（同）全被当成本表独有，而前端当然提到了它们——只是指**另一张表的
    同名列**。正确的「独有」= 主库其余 8 张表 + 工作库全部表里都没有第二个主人。
    收窄换来准确、代价是覆盖变窄：这条判据**问不了「名字被别人占用」的那部分**，
    两边的宽度都在这里写明。
    """
    con = _ro()
    mine = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    con.close()
    other_main = {c for t, cs in _all_cols().items() if t != table for c in cs}
    wk = {c for cs in _staging_cols().values() for c in cs}
    uniq = [c for c in mine if c not in other_main and c not in wk]
    dropped = sorted(c for c in mine if c not in uniq)
    front = _frontend_blob()
    hits = sorted(c for c in uniq if re.search(r"\b" + re.escape(c) + r"\b", front))
    if hits:
        return False, f"{table} 独有列被前端提到了：{hits}"
    return True, (f"{table} 的 {len(uniq)} 个独有列名在前端 0 命中"
                  f"（另有 {len(dropped)} 个因在两库里有同名主人被排除：{dropped}）")


def crit_no_producer(table: str) -> tuple[bool, str]:
    """无人消费的判据：`accidents.source_count` 仍须是单值。

    这张表的前提是「一个事故能有多个来源」。多源归并一跑通，它就**该**有出口、
    登记须重审 —— 判据取自**另一张表**（`accidents`），与实测面无关。
    """
    con = _ro()
    vals = con.execute("SELECT source_count, COUNT(*) FROM accidents "
                       "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    con.close()
    if len(vals) != 1:
        return False, f"`source_count` 已经有 {len(vals)} 个取值 {vals[:4]}"
    return True, (f"`source_count` 仍是单值 {vals[0][0]!r}（{vals[0][1]} 行）"
                  f"⇒ 归并未发生，本表无人消费是**因果**而非遗漏")


def crit_auto(table: str, col: str) -> tuple[bool, str]:
    """自动列的判据：DDL 说它是主键，或列名在时间戳/排序键白名单里。"""
    con = _ro()
    info = {r[1]: r for r in con.execute(f'PRAGMA table_info("{table}")')}
    con.close()
    row = info.get(col)
    if row is None:
        return False, "列不存在"
    if row[5]:
        return True, f"`{col}` 是 `{row[2]}` PRIMARY KEY"
    if col not in {"created_at", "updated_at", "sort_order", "id"}:
        return False, f"`{col}` 既非主键也不在自动列白名单里（类型 {row[2]}）"
    return True, f"`{col}` ∈ 自动列白名单（{row[2]}，由写入逻辑保证）"


def crit_editorial(table: str, col: str) -> tuple[bool, str]:
    """口径记录列的判据：**口径另有出口**。

    库里这份是标注侧的记录；只要每个 `enum_type` 至少有一条 `definition` 的开头能在
    `docs/enums/` 找到对等物，就说明「展示层不读它」不等于「信息没处看」。

    判据**只核有对等物、不核逐条一致**（`work_task` 实测 11 条定义里只有 1 条命中——
    那几版措辞本来就不同）——宽度的边界写在这里。
    """
    if not ENUMS.exists():
        return False, "`docs/enums/` 不存在"
    blob = "\n".join(f.read_text(encoding="utf-8", errors="ignore")
                     for f in ENUMS.glob("*") if f.is_file())
    con = _ro()
    miss, cov = [], []
    for (et,) in con.execute("SELECT DISTINCT enum_type FROM enum_definitions ORDER BY 1"):
        rows = [r[0] for r in con.execute(
            "SELECT definition FROM enum_definitions WHERE enum_type=? "
            "AND definition IS NOT NULL AND definition != ''", (et,))]
        hit = sum(1 for d in rows if d[:14] in blob)
        cov.append(f"{et} {hit}/{len(rows)}")
        if hit == 0:
            miss.append(et)
    con.close()
    if miss:
        return False, f"这些 enum_type 在 docs/enums 找不到任何对等定义：{miss}"
    return True, ("每个 enum_type 都至少有一条定义在 `docs/enums/` 有对等物"
                  "（覆盖率 " + "、".join(cov) + "）")


CRITS = {
    "crit_materialized": lambda t, c=None: crit_materialized(t),
    "crit_pipeline_audit": lambda t, c=None: crit_pipeline_audit(t),
    "crit_no_producer": lambda t, c=None: crit_no_producer(t),
    "crit_auto": lambda t, c: crit_auto(t, c),
    "crit_editorial": lambda t, c: crit_editorial(t, c),
}


# ══════════════════════════════════════════════════════════════
# 登记面 ↔ 实测面
# ══════════════════════════════════════════════════════════════

def compare(face: dict, cols: dict) -> tuple[list[str], dict]:
    """返回 (不一致的 `表.列` 列表, 统计)。列表为空 = 全绿。"""
    bad: list[str] = []

    for (t, c), got in sorted(face.items()):
        key = f"{t}.{c}"
        if got == "empty":
            continue                       # 值全空 → 自动免检（§2.5.65 已定性）
        if t == "accidents":
            want = "read" if c in ACC_EXPLICIT else (
                "star" if c in ACC_STAR_ONLY else None)
            if want is None or got != want:
                bad.append(key)
            continue
        if c in READ.get(t, []):
            if got not in ("read", "star"):
                bad.append(key)
            continue
        if c in FILTER.get(t, {}):
            if got != "filter":
                bad.append(key)
            continue
        if t in NO_EXIT_TABLES or (t, c) in NO_EXIT_COLUMNS:
            if got != "zero":
                bad.append(key)
            continue
        bad.append(key)                    # 未登记

    # 元判据的另一半：登记了、实测却根本没这个列（防"登记不存在的列"）
    real = {(t, c) for t, cs in cols.items() for c in cs}
    reg = ([(("accidents"), c) for c in ACC_EXPLICIT + ACC_STAR_ONLY]
           + [(t, c) for (t, c) in NO_EXIT_COLUMNS]
           + [(t, c) for t, cs in READ.items() for c in cs]
           + [(t, c) for t, m in FILTER.items() for c in m])
    for pair in reg:
        if pair not in real:
            bad.append(f"{pair[0]}.{pair[1]}")

    stat = {"总列数": len(real),
            "read": sum(1 for v in face.values() if v == "read"),
            "star": sum(1 for v in face.values() if v == "star"),
            "filter": sum(1 for v in face.values() if v == "filter"),
            "zero": sum(1 for v in face.values() if v == "zero"),
            "empty": sum(1 for v in face.values() if v == "empty")}
    return sorted(set(bad)), stat


# ══════════════════════════════════════════════════════════════
# A / B 段
# ══════════════════════════════════════════════════════════════

def part_a() -> tuple[dict, dict, dict]:
    print("\n[A] 实测读取面")
    face, cols, tsql, n_agg, n_alias, n_paths = scan()
    print(f"  实打路由 {n_paths} 条，捕获主库 SQL {len(RECORDS)} 条")
    n_qual, n_ambig = getattr(scan, "attr", (0, 0, 0, 0))[2:]
    print(f"  归属：限定名命中 {n_qual} 次、回落主人∩触及表 {len(RECORDS)} 条 SQL；"
          f"剔除聚合/派生列 {n_agg} 次、别名列 {n_alias} 次、**歧义丢弃 {n_ambig} 次**"
          f" —— 这三条剔除是判据的边界：不剔就会把 `COUNT(DISTINCT accident_id)` "
          f"当成「读了 accident_id」、把 `evidence_quality AS key` 当成「读了 key」；"
          f"不做限定名归属（`a.id` → accidents）则列表页那条 JOIN 里的 `id` "
          f"会因两表同名被判歧义而**丢掉**")
    for t in sorted(cols):
        cnt = Counter(face[(t, c)] for c in cols[t])
        print(f"  {t:18s} {len(cols[t]):3d} 列  " + "  ".join(
            f"{k}={cnt[k]}" for k in ("read", "star", "filter", "zero", "empty")
            if cnt.get(k)))
    star_tabs = sorted({m.group(1).lower() for r in RECORDS if r["star"]
                        for m in _TOUCH.finditer(r["sql"]) if m.group(1).lower() in cols})
    print(f"  执行过 `SELECT *` 的表：{star_tabs}")
    return face, cols, tsql


def part_b(face: dict, cols: dict, tsql: dict) -> None:
    print("\n[B] 登记面 ↔ 实测面 + 判据自身的覆盖面")
    bad, stat = compare(face, cols)
    print("  统计：" + "  ".join(f"{k} {v}" for k, v in stat.items()))
    ok(not bad, "全部列逐格与登记面一致", "" if not bad else f"不一致 {bad}")

    print("  ── 零出口的每一条都必须有可证伪判据"
          "（§2.5.67 的规矩：只写理由不验证＝永久豁免）")
    for t, (kind, fn, why) in sorted(NO_EXIT_TABLES.items()):
        good, detail = CRITS[fn](t)
        ok(good, f"整表零出口 `{t}`（{kind}）的判据成立", detail)
    for (t, c), (kind, fn, why) in sorted(NO_EXIT_COLUMNS.items()):
        good, detail = CRITS[fn](t, c)
        ok(good, f"零出口列 `{t}.{c}`（{kind}）的判据成立", detail)

    print("  ── 约束面：列确实出现在触及该表的 SQL 里，且**从未进结果集**")
    for t, m in sorted(FILTER.items()):
        text = "\n".join(r["sql"] for r in tsql.get(t, []))
        for c, why in sorted(m.items()):
            ok(bool(re.search(r"\b" + re.escape(c) + r"\b", text, re.I)),
               f"`{t}.{c}` 出现在触及该表的 SQL 文本里", why)
            ok(face.get((t, c)) not in ("read", "star"),
               f"`{t}.{c}` 从未进结果集（否则它不是约束面）")

    print("  ── 元判据：登记表与实测面**不重不漏**")
    # 「整表零出口」= 该表**没有一列**进过结果集（`zero` 与 `empty` 都算"没出口"，
    # 因为 `empty` 只是"连值都没有"这一更强的形态）。两处修正都是被实测逼出来的：
    # ① 第一版写成"有 zero 列的表"，把 `enum_definitions`、`pipeline_metadata`
    #    也算进来（它们各有 9/1 列零出口，但表本身有出口）——2 条假红；
    # ② 第二版只认 `zero`，又把 `image_analyses`/`processing_log`/`search_logs`
    #    漏掉（它们各有一个**全空列** `source_url`/`error_message`/`error`）
    #    ——3 条假红。
    zero_tabs = {t for t, cs in cols.items()
                 if all(face[(t, c)] in ("zero", "empty") for c in cs)}
    ok(zero_tabs == set(NO_EXIT_TABLES),
       "整表零出口的表集合 = `NO_EXIT_TABLES` 的键", f"实测 {sorted(zero_tabs)}")
    zero_cols = {(t, c) for (t, c), v in face.items()
                 if v == "zero" and t not in NO_EXIT_TABLES}
    ok(zero_cols == set(NO_EXIT_COLUMNS),
       "逐列零出口的集合 = `NO_EXIT_COLUMNS` 的键（防漏登记与多登记）",
       f"实测 {len(zero_cols)} 条")

    print("  ── 判据自身的覆盖面（代理补丁失效会静默漏掉整张表）")
    direct = []
    for f in sorted(WEB.glob("*.py")):
        if f.name == "db.py":
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "") == ".db":
                for a in node.names:
                    if a.name in {"connect_readonly", "read_conn", "query",
                                  "query_one", "scalar"}:
                        direct.append(f"{f.name}:{node.lineno} {a.name}")
    ok(not direct, "web 包里没有模块直接 `from .db import <读入口>`（否则代理补丁"
       "静默失效、整张表会漏出读取面）",
       f"实测直接导入 {direct}" if direct else "全部走 `db.<name>` 属性访问")
    src = DETAIL.read_text(encoding="utf-8")
    ok('_SQL_ACCIDENT = "SELECT * FROM accidents WHERE id = ?"' in src,
       "`_SQL_ACCIDENT` 仍是 `SELECT *`（`star` 这一档的前提；改成显式列清单后"
       "必须重跑并重新登记）")
    fa = FA.read_text(encoding="utf-8") if FA.exists() else ""
    ok("DETAIL_NOT_SHOWN" in fa and '"raw_text"' in fa and '"work_task_enum"' in fa,
       "§2.5.67 对 `accidents` 出口面的结论仍然在位（引已有凭据，不重做一遍）")


# ── C 段：注入式自证 ────────────────────────────────────────────

_SQL_STAR = '_SQL_ACCIDENT = "SELECT * FROM accidents WHERE id = ?"'
_SQL_NARROW = '_SQL_ACCIDENT = "SELECT id, title FROM accidents WHERE id = ?"'
_ENUM_SEL = '"SELECT code, name FROM enum_definitions "'

INJECT = [
    # ① 把详情页那条 `SELECT *` 收成两列 ⇒ 红的**恰好**是那 32 个
    #    "只靠它出门"的列。这条用例同时证明：`accidents` 的 32 列出口是**顺带的**，
    #    改一行查询就全断（而不是"32 列另有出处"）。
    (DETAIL, _SQL_STAR, _SQL_NARROW,
     {f"accidents.{c}" for c in ACC_STAR_ONLY}),
    # ② 收紧：枚举查询少选一列 ⇒ 恰好 `name` 转红
    (AQUERY, _ENUM_SEL, '"SELECT code FROM enum_definitions "',
     {"enum_definitions.name"}),
    # ③ 反方向（放宽）：同一处多选两列 ⇒ 恰好那两个"零出口列"转红
    (AQUERY, _ENUM_SEL,
     '"SELECT code, name, definition, version FROM enum_definitions "',
     {"enum_definitions.definition", "enum_definitions.version"}),
]


def _diff_subprocess() -> tuple[set[str], int]:
    r = subprocess.run([PY, str(Path(__file__).resolve()), "--diff"],
                       capture_output=True, text=True, timeout=900)
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    try:
        payload = json.loads(lines[-1])
    except Exception:                                        # noqa: BLE001
        print("    （--diff 没吐出 JSON；stdout 尾部：", lines[-3:],
              "stderr 尾部：", r.stderr.strip().splitlines()[-2:], "）")
        return set(), 1
    return set(payload["mismatch"]), payload["exit"]


def part_c() -> int:
    print("\n[C] 注入式自证：红的是不是**恰好**那一个列")
    base, rc = _diff_subprocess()
    ok(rc == 0 and not base, "基线：`--diff` 退出码 0 且无不一致",
       f"实测 mismatch={sorted(base)}")
    caught = 0
    for path, old, new, want in INJECT:
        src = path.read_text(encoding="utf-8")
        if old not in src:
            ok(False, f"注入锚点未命中（{path.name}）——锚点写错＝用例根本没跑")
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        try:
            hit, _ = _diff_subprocess()
        finally:
            path.write_text(src, encoding="utf-8")
        caught += hit == want
        ok(hit == want, f"注入 {path.name}：{old[:36]}… → 红的恰好是 {sorted(want)}",
           f"实测 {sorted(hit)}")
    after, rc2 = _diff_subprocess()
    ok(rc2 == 0 and not after, "恢复后回到基线（try/finally 保证源码复原）")
    print(f"  drift: {caught}/{len(INJECT)} 转红")
    return 0 if caught == len(INJECT) else 1


# ── D 段：文档对账 ─────────────────────────────────────────────

DOC_NEEDLES = ["**133 列**里真的进过结果集的是"]


def part_d(stat: dict) -> None:
    print("\n[D] 文档 ↔ 实测")
    txt = DOC.read_text(encoding="utf-8")
    for needle in DOC_NEEDLES:
        ok(needle in txt, f"01 号文档里有锚点「{needle[:26]}」")
    for label, n in (("零出口", stat["zero"]), ("无值列", stat["empty"]),
                     ("约束面", stat["filter"]), ("读取面", stat["read"]),
                     ("仅 star", stat["star"]), ("总列数", stat["总列数"])):
        m = re.search(r"\|\s*" + label + r"\s*\|\s*\*\*(\d+)\*\*\s*\|", txt)
        ok(bool(m) and int(m.group(1)) == n, f"文档表里「{label}」= {n}",
           f"文档写的是 {m.group(1) if m else '（没找到该行）'}")


def main() -> int:
    if "--diff" in sys.argv:
        face, cols, _t, _a, _b, _p = scan()
        bad, _ = compare(face, cols)
        print(json.dumps({"mismatch": bad, "exit": 1 if bad else 0}))
        return 1 if bad else 0

    print("§2.5.76 展示层对主库的读取面审计")
    print(f"  主库 {MAIN_DB}")
    face, cols, tsql = part_a()
    part_b(face, cols, tsql)
    rc = part_c()
    _, stat = compare(face, cols)
    part_d(stat)
    print("\n" + ("✅ 全部通过" if not FAILS and rc == 0
                  else f"🔴 {len(FAILS)} 项未通过"
                       + ("" if rc == 0 else " + C 段未全中")))
    for f in FAILS:
        print("   ·", f)
    return 1 if FAILS or rc else 0


if __name__ == "__main__":
    sys.exit(main())
