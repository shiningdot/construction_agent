#!/usr/bin/env python3
"""查询计划核查：列表与致因分析的筛选，有没有真的下推到索引。

## 为什么查这个

蓝图 07 §四 M1 的验收条目写着「列表筛选下推到 SQL，`EXPLAIN QUERY PLAN` 显示走
索引」。这条**不能靠读源码判定**——SQL 是 `build_where` 拼出来的，拼得对不对、
SQLite 有没有真用上索引，是两件事。跑一次 `EXPLAIN QUERY PLAN` 才知道。

## 做法：抓真跑出来的 SQL，不照抄源码

给 `db.query` / `db.scalar` 打补丁记下每条 SQL，再对它们逐条 EXPLAIN。**不照抄
源码里的拼法**——那种写法在 `run()` 改了之后不会跟着改，查的是上一版的 SQL。

## 判据：加了筛选之后，计划里有没有 `SEARCH`

验收条目的语言就是 `SEARCH x USING INDEX i (col=?)`——`SCAN` 不是那个意思，无论
带不带 `USING`（`SCAN t USING COVERING INDEX x` 是全**索引**扫描，扫全部索引项）。

**只看带筛选的用例**（用例名是脚本自己构造的，不看 SQL 文本——子查询里也有 WHERE，
按文本判会把排序用例误伤）。致因分析页与排序用例只打印计划不计分：前者是必然的
全量聚合，后者没有筛选条件。

判据写废过两版，都是**设计错**不是实现 bug，一并记在 `classify` 的 docstring 里：
按表名匹配（别名让判据恒真，挖洞验不出）、按 `WHERE` 子串判有无筛选（子查询的
WHERE 骗出九条假阳性）。

## 两处已知豁免（写死，且带理由）

- **关键词 `instr(...)`**：没有 FTS 索引，`instr` 只能逐行算。2,623 条下代价可接受；
  要快得上 FTS5，那是另一件事
- **年份 `substr(a.date,1,4)`**：**故意**不改成 `date >= '2015-01-01'` 的范围比较。
  实测（2026-10-01，见下）范围口径快 11 倍（0.012 ms vs 0.138 ms），但**会算错**：

  | | substr 口径 | 范围口径 |
  |---|---|---|
  | `'2008'`（纯年份，2 条） | 算 2008 ✓ | **丢**（`'2008' < '2008-01-01'`） |
  | `'1986-01'`（年月，8 条） | 算 1986 ✓ | **算成 1985**（`'1986-01' < '1986-01-01'`） |

  0.126 ms 的差别没人感知得到，3 条记录归错年份是实打实的数据错。**这一处不改是
  决定，不是遗漏**；哪天有人"顺手优化"成范围比较，这个脚本要能拦住他

## 用法

    .venv/bin/python3 scripts/check_query_plan.py
    .venv/bin/python3 scripts/check_query_plan.py --db <path>

退出码 `0` = 所有带筛选的查询都走了索引（豁免项除外）；`1` = 有筛选没走索引。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: (SQL 里的片段, 为什么可以接受)。**窄匹配**：只认这些具体写法，不认「凡是扫」。
#: 写宽了等于没有判据。
EXEMPT: tuple[tuple[str, str], ...] = (
    ("instr(", "关键词没有 FTS 索引，只能逐行算；2,623 条下可接受"),
    ("substr(a.date, 1, 4)", "年份故意用 substr —— 改成范围比较会算错 3 条记录的年份"),
    ("substr(date, 1, 4)", "同上（致因分析页的时间线聚合）"),
    ("EXISTS (SELECT 1 FROM caics_causes",
     "「有编码 / 无编码」筛选走 EXISTS 相关子查询，主表侧没有可索引的列可用；"
     "且有编码的占 2,618/2,623，扫过去本就接近最优"),
    ("GROUP BY province",
     "筛选器的省份计数（`ListParams.normalized()`）：要给出每个省各多少条就得遍历"
     "所有分组，索引帮不上；它走的是 idx_acc_province 的覆盖索引，已是最优"),
)


def classify(case: str, sql: str, details: list[str]) -> tuple[str, str]:
    """给一条 SQL 的查询计划定级：`("ok"|"exempt"|"bad"|"skip", 说明)`。

    ## 判据：**加了筛选之后，计划里有没有 `SEARCH`**

    直接对着验收条目问——「筛选下推到 SQL，`EXPLAIN QUERY PLAN` 显示走索引」。
    `SEARCH x USING INDEX i (col=?)` 就是这句验收的语言；`SCAN` 不是。

    ## 前两版是怎么写废的（都不是实现 bug，是判据设计错）

    **第一版按表名匹配**计划里的 `SCAN accidents`。恒真：展示层的 SQL 全都
    `FROM accidents a`，计划里写的是别名 `SCAN a USING …`，一条也匹配不上，于是
    「条件没走上索引」永远是空集——连挖两个洞都验不出差别（一个把筛选列改成
    `lower(col) IN`，一个清空豁免表，跑出来一模一样）。

    **第二版改成「带 WHERE 就不许出现 `SCAN`」**，靠 `" WHERE " in sql` 判有无筛选。
    过严且仍是错的：子查询里也有 `WHERE`（`(SELECT COUNT(*) FROM caics_causes c
    WHERE c.accident_id = a.id)`），于是**排序用例**（压根没筛选）被判成「带 WHERE
    却是扫描」，九条假阳性。

    现在不看 SQL 文本，**看用例名**——「这个用例到底有没有加筛选条件」是脚本自己
    构造的，不会被子查询、别名、函数包裹骗到。

    ## 为什么致因分析页与无筛选用例不计分

    致因分析是三张**全量聚合**（分布、共现、时间线）：按 `substr(code,1,1)` 分组就得
    把 16,323 行过一遍，`SCAN` 是必然。排序用例没有筛选条件，`SCAN a USING INDEX
    idx_acc_date` 配 `ORDER BY date` 是**好计划**（索引有序，取前 20 条即停）。
    两者都不是「筛选下推」的验收对象，只打印计划供人看。
    """
    if case.startswith("致因") or case.startswith("排序") or case == "列表·无筛选":
        return "skip", " / ".join(d for d in details if "SCAN" in d or "SEARCH" in d)

    search = [d for d in details if d.startswith("SEARCH ")]
    if search:
        return "ok", " / ".join(search)

    for frag, why in EXEMPT:
        if frag in sql:
            return "exempt", why
    return "bad", ("加了筛选却仍是扫描（条件没走上索引）："
                   + " / ".join(d for d in details if d.startswith("SCAN ")))


def collect() -> list[tuple[str, str, list, list[str]]]:
    """跑一遍展示层的查询，返回 [(用例, SQL, 参数, 计划)]。"""
    import construction_agent.web.accidents_query as aq
    import construction_agent.web.causes as cz
    import construction_agent.web.db as db

    seen: list[tuple[str, str, list]] = []
    oq, os_ = db.query, db.scalar

    def spy_q(sql, args=(), **kw):
        seen.append((" ".join(sql.split()), list(args), _case[0]))
        return oq(sql, args, **kw)

    def spy_s(sql, args=(), **kw):
        seen.append((" ".join(sql.split()), list(args), _case[0]))
        return os_(sql, args, **kw)

    db.query, db.scalar = spy_q, spy_s
    _case = ["?"]

    con = sqlite3.connect(f"file:{_db_path}?mode=ro", uri=True)
    cases: list[tuple[str, object]] = []
    for name, kw in (
        ("列表·无筛选", {}),
        ("列表·省份", {"province": ["江苏省"]}),
        ("列表·省份多选", {"province": ["江苏省", "浙江省"]}),
        ("列表·严重程度", {"severity": ["较大事故"]}),
        ("列表·事故类型", {"accident_type": ["高处坠落"]}),
        ("列表·项目类型", {"project_type": ["房屋建筑"]}),
        ("列表·施工阶段", {"construction_phase": ["主体结构"]}),
        ("列表·关键词", {"q": "坍塌"}),
        ("列表·年份区间", {"year_from": 2015, "year_to": 2020}),
        ("列表·有编码", {"has_causes": True}),
        ("列表·组合四条", {"province": ["江苏省"], "severity": ["较大事故"],
                           "year_from": 2015, "has_causes": True}),
    ):
        cases.append((name, lambda kw=kw: aq.run(aq.ListParams(**kw), db_path=_db_path)))
    for s in sorted(aq.SORTS):
        cases.append((f"排序·{s}",
                      lambda s=s: aq.run(aq.ListParams(sort=s), db_path=_db_path)))
    cases += [
        ("致因·distribution", lambda: cz.distribution(aq.ListParams(), db_path=_db_path)),
        ("致因·cooccurrence", lambda: cz.cooccurrence(aq.ListParams(), db_path=_db_path)),
        ("致因·scope", lambda: cz.scope(aq.ListParams(), db_path=_db_path)),
    ]
    if hasattr(cz, "timeline"):
        cases.append(("致因·timeline",
                      lambda: cz.timeline(aq.ListParams(), db_path=_db_path)))

    out: list[tuple[str, str, list, list[str]]] = []
    for name, fn in cases:
        _case[0] = name
        seen.clear()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            out.append((name, f"（用例跑不动：{type(exc).__name__}: {exc}）", [], []))
            continue
        for sql, args, case_name in seen:
            try:
                plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)]
            except sqlite3.Error as exc:
                plan = [f"EXPLAIN 失败：{exc}"]
            out.append((case_name, sql, args, plan))
    con.close()
    return out


_db_path = ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", help="库文件路径（默认按 CAIEMS_DB > settings.DATA_DIR 解析）")
    args = ap.parse_args()

    global _db_path
    from construction_agent.web.db import resolve_db_path
    _db_path = str(Path(args.db).expanduser().resolve() if args.db else resolve_db_path())
    if not Path(_db_path).exists():
        print(f"✗ 库文件不存在：{_db_path}", file=sys.stderr)
        return 2

    rows = collect()
    bad: list[str] = []
    by_case: dict[str, list[tuple[str, str]]] = {}
    for case, sql, _args, plan in rows:
        level, why = classify(case, sql, plan)
        by_case.setdefault(case, []).append((level, why))
        if level == "bad":
            bad.append(f"{case}：{why}\n      {sql[:140]}")

    marks = {"ok": "✓", "exempt": "·", "bad": "✗", "skip": "·"}
    for case, items in by_case.items():
        worst = "bad" if any(lv == "bad" for lv, _ in items) else (
            "exempt" if any(lv == "exempt" for lv, _ in items) else
            "skip" if all(lv == "skip" for lv, _ in items) else "ok")
        print(f"{marks[worst]} {case}")
        if worst == "skip":
            # 只打印、不计分的用例合并成一行：一个用例有好几条 SQL，逐条铺开
            # （排序那 6 个各 3 条）会把真正的信号淹在计划行里
            merged: list[str] = []
            for _lv, why in items:
                merged += [d for d in why.split(" / ") if d and d not in merged]
            print(f"    · {' / '.join(merged)}")
            continue
        for lv, why in items:
            if lv != "ok":
                print(f"    {marks[lv]} {why}")
    print(f"\n{len(rows)} 条查询，{len(by_case)} 个用例。")

    if bad:
        print(f"\n✗ {len(bad)} 条筛选没走索引：")
        for b in bad:
            print(f"    - {b}")
        print("\n  若是新增的筛选维度，给它建索引（脚本：scripts/add_indexes.py）；")
        print("  若是把某个表达式改成了函数包裹列，先确认改回去不会算错数据——")
        print("  本文件顶部的年份那一条就是这么来的。")
        return 1

    n_exempt = sum(1 for items in by_case.values()
                   for lv, _ in items if lv == "exempt")
    print(f"\n✓ 带筛选的查询全部走了索引；{n_exempt} 条命中豁免"
          f"（见本文件 `EXEMPT`，每条都带理由——理由要经得起看，不能当垃圾篓）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
