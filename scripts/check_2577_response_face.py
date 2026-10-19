#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.77 结果集 → HTTP 响应：查出来的列，有几列真的发给了前端。

## 来路：链条上前一节刚补完，后一节还空着

§2.5.76 补的是「**库列 → 结果集**」（133 列里哪些真进过 `SELECT`），当时明写了
判据边界：「只判进没进结果集，**判不到渲染出来没有**」。§2.5.67 / §2.5.68 在链
条**另一头**（前端读不读这个名字、用户在浏览器里看不看得见）。中间这一节——

    库列 →〔§2.5.76〕→ 结果集 →〔**本节**〕→ HTTP 响应 →〔§2.5.67/68〕→ 用户

——**从来没有单独核过**：某列被 `SELECT` 出来了，`route` 层组装响应时会不会把它
丢掉？`check_api_contract.py` 管的是「蓝图写的字段，实现给没给」，方向相反；
`check_field_alignment.py` 管的是「前端读的名字，后端给没给」，落点在**前端**。
所以这一节的判据只能是「**结果集里的列名，有没有作为键出现在响应 JSON 里**」。

## 判据为什么只能落在"名字面"

先说清楚**没立**的那条判据：本来想立「**值面**」——取该列的真实值，看它有没有
出现在响应里（能抓「改了名、值照搬」）。实测下来它两头都不准：

* **会假绿**：`work_task` 与 `work_task_enum` 是**同一个命名空间**（都写 `WT04` /
  `WT12` 这种码），`work_task_enum` 的值很容易撞上响应里 `work_task` 的值——
  一条分不出「发了这一列」还是「发了兄弟列」的判据不是判据。
* **会假红**：`pipeline_metadata.value` 的值是一段 JSON 串，`extract.py:97-104`
  把它 `json.loads` 之后**只取 `version` 字段**放进响应——原串根本不该出现，
  值面会把它判成「没出口」，而它其实出口了。

所以只留**两层名字面/源码面**判据，值面明确不立，边界写在这里（§2.5.63／§2.5.66
的规矩：判据的宽度与强度同样决定结论的可信度，两边的宽度都要写出来）。

| 层 | 判据 | 强度 |
|---|---|---|
| ① 名字面 | 列名作为键出现在某条 JSON 200 响应里 | 强（运行时实测） |
| ② 源码面 | web 包里存在 `x["列名"]` 形式的读取点（AST，注释天然不算） | 中（证明"代码在读"，不证明"发出去"） |

出口 = ① 或 ②。两层都不成立 ⇒ 「查出来了却没发给前端」。

## 为什么按「列」判而**不**按「路由」判

按路由判会被**空集合**打出假阳性：`/api/accidents?severity=死亡&has_causes=1&page=2`
返回 0 条，`items` 为空 ⇒ 列表项的所有字段在那条路由上"都没进响应"，实测一次报
11 列。改成**列级**判定（「全站范围里，这一列有没有在任何一条响应里当过键」）后
这种假阳性自动消失——别的路由会给它作证。这**不是**放松判据：真不下发的列
（本节抓到的那一列）在任何路由上都拿不到键。

## 四段

    A. 名字面实观测：逐 (表,列) 判有没有响应键出口 + 判据的覆盖面
    B. 嫌疑集 ↔ 登记集（不重不漏）+ 每条的两层判据（可证伪）
    C. 注入式自证：改真实源码三例，断言**红的恰好是哪一项**
    D. 文档 ↔ 实测

口径：`app.test_client()`；与 §2.5.76 同一套代理与归属算法（`check_2576` 的
`_TOUCH` / `_ALIAS` / `_AGG` / `_all_cols` 直接复用，同一个解析逻辑不写第二遍）。
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_2576_column_exit as M                      # noqa: E402
from _serve import point_at                             # noqa: E402

DOC = ROOT / "docs" / "web" / "01_在线采集.md"
WEB = ROOT / "construction_agent" / "web"
DETAIL = WEB / "detail.py"
EXTRACT = WEB / "extract.py"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"

# ══════════════════════════════════════════════════════════════
# 登记面
#
# 硬编码是**故意的**：这两条是本节查出来的**结论**，实测面与它逐格相等才算通过。
# 哪天有人给 `work_task_enum` 加了读取点、或 `extract.py` 不再读 `value`，
# 差额会自己冒出来（C 段的用例 2/3 就是这两条的失败路径）。
# ══════════════════════════════════════════════════════════════

#: 名字面失守、但源码面有读取点 ⇒ 值经解析/派生后下发。
EXIT_BY_SOURCE = {
    ("pipeline_metadata", "value"):
        "`extract.py:96-104` 读到 `row[\"value\"]` 后按 key 分派："
        "`pipeline_version` 的值进响应的 `caics.version`，`*_enum_version` 的值 "
        "`json.loads` 后取 `version` 进 `enums[name]`——**原串不进响应**，"
        "所以名字面失守、源码面成立。",
}

#: 两层判据都不成立：查出来了、代码也不读它、响应里也没有。
NO_EXIT_AT_ALL = {
    ("accidents", "work_task_enum"):
        "1.0 旧体系残留列（`accidents_query.py:111-125` 有完整裁决：与 `work_task` "
        "两套编码、有值时 1,392 条互相矛盾、703 条的 `WT11–WT15` 在枚举表里根本不存在）。"
        "它能进结果集**只因详情页那条 `SELECT *`**，web 包里对它的读取点为零 —— "
        "**有意不下发**，不是漏发。",
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
                            "star": bool(M._STAR.search(str(sql)))})
            return cur

        def __getattr__(self, k):
            return getattr(self._con, k)

    wdb.connect_readonly = lambda db_path=None: TracedConn(orig(db_path))  # type: ignore


def keys_of(o, out: set[str]) -> None:
    """递归收集 JSON 里出现过的**所有键名**（含嵌套）。"""
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(str(k))
            keys_of(v, out)
    elif isinstance(o, list):
        for v in o:
            keys_of(v, out)


def _attrs(rec: dict, cols: dict, owners: dict) -> set[tuple[str, str]]:
    """把一条 SQL 结果集的列归属到 (表, 列)。与 §2.5.76 同一套算法。"""
    out: set[tuple[str, str]] = set()
    amap = {m.group(2).lower(): m.group(1).lower() for m in M._ALIAS.finditer(rec["sql"])
            if m.group(2).lower() not in M._RESERVED}
    touched = {m.group(1).lower() for m in M._TOUCH.finditer(rec["sql"])}
    for col in rec["cols"]:
        if M._AGG.search(col):
            continue
        if re.search(r"\bas\s+" + re.escape(col) + r"\b", rec["sql"], re.I):
            continue
        qual = {t for al, t in amap.items()
                if re.search(r"\b" + re.escape(al) + r"\." + re.escape(col) + r"\b",
                             rec["sql"], re.I)}
        if len(qual) == 1 and (t := next(iter(qual))) in cols:
            out.add((t, col))
            continue
        hit = [t for t in owners.get(col, []) if t in touched]
        if len(hit) == 1:
            out.add((hit[0], col))
    return out


def scan() -> dict:
    """跑一遍展示层，返回名字面统计。"""
    point_at(str(M.MAIN_DB), M.STAGING_DB)
    _install_proxy()

    from construction_agent.web.factory import create_app
    app = create_app()
    c = app.test_client()

    con0 = sqlite3.connect(f"file:{M.MAIN_DB}?mode=ro", uri=True)
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

    cols = M._all_cols()
    owners: dict[str, list[str]] = {}
    for t, cs in cols.items():
        for x in cs:
            owners.setdefault(x, []).append(t)

    pairs_all: set[tuple[str, str]] = set()      # 进过结果集的业务列（全部路由）
    pairs_json: set[tuple[str, str]] = set()     # 其中"本来有机会进响应"的（JSON 200）
    named: set[tuple[str, str]] = set()          # 名字面出口
    keys_all: set[str] = set()
    n_json = 0
    n_sql = 0
    for p in paths:
        RECORDS.clear()
        r = c.get(p)
        n_sql += len(RECORDS)
        is_json = r.mimetype == "application/json" and r.status_code == 200
        ks: set[str] = set()
        if is_json:
            n_json += 1
            keys_of(r.get_json(silent=True), ks)
            keys_all |= ks
        for rec in RECORDS:
            got = _attrs(rec, cols, owners)
            pairs_all |= got
            if is_json:
                pairs_json |= got
                named |= {(t, c2) for (t, c2) in got if c2 in ks}
    del c
    return {"pairs_all": pairs_all, "pairs_json": pairs_json, "named": named,
            "keys_all": keys_all, "n_paths": len(paths), "n_json": n_json,
            "n_sql": n_sql}


# ══════════════════════════════════════════════════════════════
# 源码面判据：`x["列名"]` 形式的读取点
# ══════════════════════════════════════════════════════════════

def read_points(col: str) -> list[str]:
    """web 包里所有 `x["列名"]` 下标读取点（AST：注释与字符串里的提及都不算）。

    判据的**接收者宽度**（§2.5.66 的规矩，两边都要写出来）：
    只认**下标**形态 `x["col"]`；`row.get("col")`、`getattr(row, "col")`、
    以及名单常量（`CONTENT_TIERS = [... "work_task" ...]`）**都不认**。
    收窄换来准确（不会把 `fields` 里某个同名键当成"在读这一列"），
    代价是覆盖变窄（换个写法就漏）。本节这两条的实测形态恰好是下标，
    所以够用；哪天有人改成 `.get()`，判据会把它判成"无读取点"——
    那是**报红**不是假绿，方向是安全的。
    """
    hits: list[str] = []
    for f in sorted(WEB.glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:                                # noqa: PERF203
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                s = node.slice
                if isinstance(s, ast.Constant) and s.value == col:
                    hits.append(f"{f.name}:{node.lineno}")
    return hits


def compare(measured: dict) -> tuple[list, list, list]:
    """返回（未登记、过期登记、判据不成立）。"""
    suspects = measured["pairs_json"] - measured["named"]
    registered = set(EXIT_BY_SOURCE) | set(NO_EXIT_AT_ALL)
    unregistered = sorted(f"{t}.{c}" for t, c in suspects - registered)
    stale = sorted(f"{t}.{c}" for t, c in registered - suspects)
    crit = []
    for (t, c2) in sorted(EXIT_BY_SOURCE):
        if not read_points(c2):
            crit.append(f"{t}.{c2} 源码面无读取点")
    for (t, c2) in sorted(NO_EXIT_AT_ALL):
        if read_points(c2):
            crit.append(f"{t}.{c2} 源码面出现读取点")
    return unregistered, stale, crit


# ══════════════════════════════════════════════════════════════
# A / B 段
# ══════════════════════════════════════════════════════════════

def part_a() -> dict:
    print("\n[A] 名字面实观测：结果集里的列有没有作为键进过响应")
    m = scan()
    print(f"  实打路由 {m['n_paths']} 条（JSON 200 的 {m['n_json']} 条）；"
          f"捕获主库 SQL {m['n_sql']} 条")
    print(f"  进过结果集的业务列（表,列）：{len(m['pairs_all'])} 对；"
          f"其中来自 JSON 200 路由（**本该有机会进响应**）的 {len(m['pairs_json'])} 对")
    only_html = sorted(m["pairs_all"] - m["pairs_json"])
    print(f"  只在非 JSON 路由的结果集里出现过的：{len(only_html)} 对"
          + (f"{only_html}" if only_html else "（判据的覆盖面：这类列天然拿不到响应键，"
                                              "若不为空必须逐条人工看）"))
    miss = sorted(m["pairs_json"] - m["named"])
    print(f"  名字面出口 {len(m['named'])} 对，失守 {len(miss)} 对：")
    for t, c2 in miss:
        print(f"      {t}.{c2}")
    biz = {c2 for _, c2 in m["pairs_json"]}
    print(f"  响应键面共 {len(m['keys_all'])} 个键，其中不属于结果集列名的 "
          f"{len(m['keys_all'] - biz)} 个"
          f"（派生字段：`*_label`、分页 `meta`、`tiers` 等 —— 本节不判它们，"
          f"§2.5.67/68 已覆盖那一头）")
    return m


def part_b(m: dict) -> None:
    print("\n[B] 嫌疑集 ↔ 登记集 + 每条的两层判据")
    unregistered, stale, crit = compare(m)
    suspects = sorted(m["pairs_json"] - m["named"])
    print(f"  嫌疑集 {len(suspects)} 对、登记集 {len(EXIT_BY_SOURCE) + len(NO_EXIT_AT_ALL)} 对")
    ok(not unregistered, "没有未登记的嫌疑列（查出来了却没发出去、又没写理由）",
       f"未登记 {unregistered}" if unregistered else "两列都在登记表上")
    ok(not stale, "没有过期登记（登记了却已有名字面出口）",
       f"过期 {stale}" if stale else "")
    print("  ── 每条登记都必须能被判据证伪（§2.5.67 的规矩）")
    for (t, c2), why in sorted(EXIT_BY_SOURCE.items()):
        hits = read_points(c2)
        ok(bool(hits), f"`{t}.{c2}` 源码面确有读取点（值经解析后下发）",
           f"{hits[:3]} —— {why}" if hits else "一个 `x[\"列名\"]` 都没找到")
    for (t, c2), why in sorted(NO_EXIT_AT_ALL.items()):
        hits = read_points(c2)
        ok(not hits, f"`{t}.{c2}` 源码面确无读取点（有意不下发）",
           f"实测 {hits}" if hits else why)
    ok(not crit, "两条登记的两层判据都成立", f"{crit}" if crit else "")
    print("  ── 判据自身的覆盖面")
    src = DETAIL.read_text(encoding="utf-8")
    ok('_SQL_ACCIDENT = "SELECT * FROM accidents WHERE id = ?"' in src,
       "`_SQL_ACCIDENT` 仍是 `SELECT *`（`work_task_enum` 能进结果集的前提；"
       "改成显式列清单后本节结论要重跑）")
    ok("CONTENT_TIERS" in src and "META_FIELDS" in src,
       "详情页仍走分档表组装 `fields` / `meta`（名字面失守的**机制**："
       "没进这两张表的列不进响应）")


# ── C 段：注入式自证 ────────────────────────────────────────────

_D_SET_FIELD = "            fields[n] = row.get(n)"
_D_SET_FIELD_X = ("            if n == \"work_task\":\n"
                  "                continue\n"
                  "            fields[n] = row.get(n)")
_E_ROWKEY = '            key, value = row["key"], row["value"]'
_E_ROWKEY_X = '            key, value = row["key"], row["key"]'
_D_DICTROW = "        row = dict(row)"
_D_DICTROW_X = '        row = dict(row)\n        _probe = row["work_task_enum"]  # noqa: F841'

INJECT = [
    # ① 名字面判据的失败路径：详情页不再把 `work_task` 放进 `fields`
    #    ⇒ 它失去唯一的名字面出口 ⇒ 必须以「未登记」转红。
    (DETAIL, _D_SET_FIELD, _D_SET_FIELD_X, {"unregistered": ["accidents.work_task"]}),
    # ② 源码面判据的失败路径：`extract.py` 不再读 `row["value"]`
    #    ⇒ `pipeline_metadata.value` 从「源码面出口」掉成「两层都不成立」
    #    ⇒ 它的判据（要求有读取点）转红。
    (EXTRACT, _E_ROWKEY, _E_ROWKEY_X,
     {"crit_fail": ["pipeline_metadata.value 源码面无读取点"]}),
    # ③ 反方向：给 `work_task_enum` 加一个下标读取点
    #    ⇒ 它的判据（要求零读取点）转红，提示「该列有出口了、登记表要更新」。
    (DETAIL, _D_DICTROW, _D_DICTROW_X,
     {"crit_fail": ["accidents.work_task_enum 源码面出现读取点"]}),
]


def _diff_subprocess() -> dict:
    r = subprocess.run([PY, str(Path(__file__).resolve()), "--diff"],
                       capture_output=True, text=True, timeout=900)
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    try:
        return json.loads(lines[-1])
    except Exception:                                        # noqa: BLE001
        print("    （--diff 没吐出 JSON；stdout 尾部：", lines[-3:],
              "stderr 尾部：", r.stderr.strip().splitlines()[-2:], "）")
        return {"unregistered": [], "stale": [], "crit_fail": [], "exit": 1,
                "broken": True}


def _empty(payload: dict) -> bool:
    return not (payload.get("unregistered") or payload.get("stale")
                or payload.get("crit_fail") or payload.get("broken"))


def part_c() -> int:
    print("\n[C] 注入式自证：红的是不是**恰好**那一项")
    base = _diff_subprocess()
    ok(not base.get("broken") and _empty(base),
       "基线：`--diff` 退出码 0 且三项皆空", f"实测 {base}")

    caught = 0
    for path, old, new, want in INJECT:
        src = path.read_text(encoding="utf-8")
        if old not in src:
            ok(False, f"注入锚点未命中（{path.name}）——锚点写错＝用例根本没跑")
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        try:
            got = _diff_subprocess()
        finally:
            path.write_text(src, encoding="utf-8")
        # 红的**恰好**是期望的那一项：其余两类必须为空
        actual = {k: v for k, v in got.items()
                  if k in ("unregistered", "stale", "crit_fail") and v}
        caught += actual == want
        ok(actual == want, f"注入 {path.name}：{old.strip()[:34]}… → 红的恰好 {want}",
           f"实测 {actual}")
    after = _diff_subprocess()
    ok(not after.get("broken") and _empty(after),
       "恢复后回到基线（try/finally 保证源码复原）")
    print(f"  drift: {caught}/{len(INJECT)} 转红")
    return 0 if caught == len(INJECT) else 1


# ── D 段：文档对账 ─────────────────────────────────────────────

DOC_NEEDLES = ["结果集里的列名，有几列真的发了出去"]


def part_d(m: dict) -> None:
    print("\n[D] 文档 ↔ 实测")
    txt = DOC.read_text(encoding="utf-8")
    for needle in DOC_NEEDLES:
        ok(needle in txt, f"01 号文档里有锚点「{needle[:24]}」")
    stat = {
        "结果集里的业务列": len(m["pairs_json"]),
        "名字面直接出口": len(m["named"]),
        "源码面出口": len(EXIT_BY_SOURCE),
        "全无出口": len(NO_EXIT_AT_ALL),
    }
    for label, n in stat.items():
        mm = re.search(r"\|\s*" + label + r"\s*\|\s*\*\*(\d+)\*\*\s*\|", txt)
        ok(bool(mm) and int(mm.group(1)) == n, f"文档表里「{label}」= {n}",
           f"文档写的是 {mm.group(1) if mm else '（没找到该行）'}")


def main() -> int:
    if "--diff" in sys.argv:
        m = scan()
        unregistered, stale, crit = compare(m)
        payload = {"unregistered": unregistered, "stale": stale, "crit_fail": crit}
        payload["exit"] = 1 if (unregistered or stale or crit) else 0
        print(json.dumps(payload, ensure_ascii=False))
        return payload["exit"]

    print("§2.5.77 结果集 → HTTP 响应：查出来的列有几列真的发了出去")
    print(f"  主库 {M.MAIN_DB}")
    m = part_a()
    part_b(m)
    rc = part_c()
    part_d(m)
    print("\n" + ("✅ 全部通过" if not FAILS and rc == 0
                  else f"🔴 {len(FAILS)} 项未通过"
                       + ("" if rc == 0 else " + C 段未全中")))
    for f in FAILS:
        print("   ·", f)
    return 1 if FAILS or rc else 0


if __name__ == "__main__":
    sys.exit(main())
