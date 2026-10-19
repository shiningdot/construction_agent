"""致因分析页（蓝图 05 §4.2）的四个聚合。

## 两个计数口径，分工在这里落地

`caics_causes` 对 `(accident_id, code)` 没有唯一约束（实测 278 组重复、616
行），所以「16,323」与「15,985」是两个不同的东西（判据见蓝图 05 §2.3）：

| 本模块的函数 | 口径 | 为什么 |
|---|---|---|
| `distribution` 的 `count` | 行数 | 与概览统计卡「16,323 条致因标注」对得上 |
| `distribution` 的 `accidents` | 去重 | 「多少条事故出现过这一类」是事故级陈述 |
| `cooccurrence` | **必须去重** | 见下 |
| `timeline` | 去重 | 同上 |
| `scope` | 两个都报 | 页面口径条上两个数字都要显示 |

**共现为什么必须去重**：文山州模架坍塌事故的 10 个编码各标了 3 次。直接
`JOIN caics_causes` 会让这条事故单独贡献 3×3=9 次 H1+M1 共现，全表 Top 1
从 1,821 虚高到 1,968。所以一律先 `SELECT DISTINCT accident_id, code` 再自连接。

## 筛选

复用 `accidents_query.build_where()`，与列表页**同一套条件翻译**——两页对
「筛选了事故类型=坍塌」的理解必须完全一致，否则用户从列表点进致因页会看到
对不上的数字。条件作用在 `accident_id IN (SELECT a.id FROM accidents a …)`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import accidents_query, db, overview

# 层的展示顺序固定，不随计数变化（理由同 overview.CAICS_LAYERS）
LAYER_LABELS: dict[str, str] = overview.LAYER_LABELS
LAYER_ORDER: tuple[str, ...] = tuple(code for code, _ in overview.CAICS_LAYERS)

# lift 排序的共现对数下限。lift 在小样本上方差极大：一对只共现过 2 次、
# 而两个编码本身都罕见时 lift 能到 8，但没有任何解读价值。10 条事故共现
# 是「看得见的结构」与「噪声」的分界。matrix 不受此限，仍给全部对数。
MIN_PAIR_COUNT = 10


# ══════════════════════════════════════════════════════════════
# 作用域
# ══════════════════════════════════════════════════════════════


def _scope(where: str) -> str:
    """筛选后的事故 id 子查询。`where` 来自 `build_where()`，已带 `a.` 前缀。"""
    return f"SELECT a.id FROM accidents a{where}"


def _in(sub: str) -> str:
    return f"accident_id IN ({sub})"


def _and(where: str, cond: str) -> str:
    """在可能为空的 WHERE 片段后追加一个条件。"""
    return f"{where} AND {cond}" if where else f" WHERE {cond}"


# ══════════════════════════════════════════════════════════════
# 口径条
# ══════════════════════════════════════════════════════════════


def scope(
    params: accidents_query.ListParams, db_path: str | Path | None = None
) -> dict[str, Any]:
    """当前筛选范围。页面常显，防止读者看图时忘记自己在看子集。"""
    where, args = accidents_query.build_where(params, db_path)
    sub = _scope(where)

    row = db.query_one(f"""
        SELECT
          (SELECT COUNT(*) FROM ({sub}))                                AS accidents,
          (SELECT COUNT(*) FROM caics_causes WHERE {_in(sub)})          AS causes,
          (SELECT COUNT(DISTINCT accident_id) FROM caics_causes
             WHERE {_in(sub)})                                          AS covered,
          (SELECT COUNT(*) FROM (SELECT DISTINCT accident_id, code
             FROM caics_causes WHERE {_in(sub)}))                       AS pairs
    """, [*args, *args, *args, *args], db_path=db_path) or {}

    accidents = row.get("accidents") or 0
    covered = row.get("covered") or 0
    return {
        "accidents": accidents,
        "causes": row.get("causes") or 0,
        "covered": covered,
        "pairs": row.get("pairs") or 0,
        # 无编码的事故不出现在任何条上，单独报出来——否则读者会拿
        # 「覆盖 2,618」去减「总计 2,623」，自己发现 5 条差额却不知为何
        "without_causes": accidents - covered,
        "filtered": bool(where),
    }


# ══════════════════════════════════════════════════════════════
# 分布
# ══════════════════════════════════════════════════════════════


def distribution(
    params: accidents_query.ListParams, db_path: str | Path | None = None
) -> dict[str, Any]:
    """五层金字塔 + 19 类明细 + 证据等级分布。一次返回，同屏渲染。"""
    where, args = accidents_query.build_where(params, db_path)
    sub = _scope(where)

    total_causes = db.scalar(
        f"SELECT COUNT(*) FROM caics_causes WHERE {_in(sub)}", args,
        default=0, db_path=db_path,
    ) or 0

    # 两个口径在同一个 GROUP BY 里取，不做两次全表扫描
    got_layer = {
        r["key"]: r for r in db.query(f"""
            SELECT substr(code, 1, 1) AS key,
                   COUNT(*) AS count,
                   COUNT(DISTINCT accident_id) AS accidents
            FROM caics_causes WHERE {_in(sub)} GROUP BY key
        """, args, db_path=db_path)
    }
    by_layer = [
        {
            "key": code,
            "label": LAYER_LABELS.get(code, code),
            "count": (got_layer.get(code) or {}).get("count", 0),
            "accidents": (got_layer.get(code) or {}).get("accidents", 0),
        }
        for code in LAYER_ORDER
    ]

    def _rank(c: dict[str, Any]) -> tuple[int, int]:
        return (LAYER_ORDER.index(c["layer"])
                if c["layer"] in LAYER_ORDER else 99, -c["count"])

    by_category = sorted(
        (
            {
                "code": r["code"],
                "name": r["name"],
                "layer": (r["code"] or "?")[:1],
                "layer_label": LAYER_LABELS.get((r["code"] or "?")[:1], "?"),
                "count": r["count"],
                "accidents": r["accidents"],
                "share": (r["count"] / total_causes) if total_causes else 0.0,
            }
            for r in db.query(f"""
                SELECT code, MAX(name) AS name,
                       COUNT(*) AS count,
                       COUNT(DISTINCT accident_id) AS accidents
                FROM caics_causes WHERE {_in(sub)}
                GROUP BY code
            """, args, db_path=db_path)
        ),
        key=_rank,
    )

    got_ev = {
        r["key"]: r for r in db.query(f"""
            SELECT evidence_quality AS key,
                   COUNT(*) AS count,
                   COUNT(DISTINCT accident_id) AS accidents
            FROM caics_causes WHERE {_in(sub)} GROUP BY key
        """, args, db_path=db_path)
    }
    evidence = [
        {
            "key": k,
            "label": overview.EVIDENCE_LABELS[k],
            "count": (got_ev.get(k) or {}).get("count", 0),
            "accidents": (got_ev.get(k) or {}).get("accidents", 0),
        }
        for k in overview.EVIDENCE_ORDER
    ]
    # 落在三级之外的取值单独报数，不并进任何一档——吞掉就再也发现不了
    unknown = sum(r["count"] for k, r in got_ev.items()
                  if k not in overview.EVIDENCE_LABELS)

    return {
        "by_layer": by_layer,
        "by_category": by_category,
        "evidence_quality": evidence,
        "evidence_unknown": unknown,
        "total_causes": total_causes,
    }


# ══════════════════════════════════════════════════════════════
# 共现
# ══════════════════════════════════════════════════════════════


def cooccurrence(
    params: accidents_query.ListParams,
    *,
    top: int = 20,
    order: str = "count",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """编码共现。**去重口径**——先 DISTINCT 再自连接，理由见模块注释。

    `lift` = 实际共现率 / 独立期望率 = `n · N / (n_x · n_y)`，用于剔除
    「M1 本身出现得多所以跟谁都共现多」的伪相关。

    ## 默认排序取 count 而非 lift（2026-09-30 实测后修正蓝图）

    蓝图 05 原定默认按 lift。实测 lift 榜首是 **C1 救援响应迟缓 + H4 应急
    行为失当 = 24 条事故**，lift 6.4——数字没错（这两个类别确实高度绑定），
    但首屏给一个只有 24 条事故支撑的对，读者拿不到这套体系的主线。

    count 榜首是 H1+M1 = 1,821，也正是论文关心的「人因 × 管理」主线。
    蓝图当初的担心（高频编码天然共现多）由**页面上 count 与 lift 两列并列**
    来化解——M1 的共现多是不是「因为它本身多」，读者对着 lift 一眼可判。

    所以：默认 `count` 看主结构，切到 `lift` 找「非主线但强绑定」的对。
    """
    where, args = accidents_query.build_where(params, db_path)
    sub = _scope(where)

    # 类目的事故级计数复用 distribution，不另算一遍——同一批数字在多处
    # 各算一遍正是本项目反复出现的失败模式
    dist = distribution(params, db_path=db_path)
    acc_of = {c["code"]: c["accidents"] for c in dist["by_category"]}
    name_of = {c["code"]: c["name"] for c in dist["by_category"]}
    codes = [c["code"] for c in dist["by_category"]]

    # N 取「有编码的事故数」而非全部事故：只有有编码的事故才可能共现，
    # 拿全部事故当分母会把所有 lift 整体压低，且压得没有意义
    n_covered = db.scalar(
        f"SELECT COUNT(DISTINCT accident_id) FROM caics_causes WHERE {_in(sub)}",
        args, default=0, db_path=db_path,
    ) or 0

    rows = db.query(f"""
        WITH scoped AS (
          SELECT DISTINCT accident_id, code FROM caics_causes WHERE {_in(sub)}
        )
        SELECT a.code AS x, b.code AS y, COUNT(*) AS n
        FROM scoped a JOIN scoped b
          ON a.accident_id = b.accident_id AND a.code < b.code
        GROUP BY a.code, b.code
    """, args, db_path=db_path)

    pairs: list[dict[str, Any]] = []
    for r in rows:
        nx, ny = acc_of.get(r["x"], 0), acc_of.get(r["y"], 0)
        pairs.append({
            "a": r["x"], "a_name": name_of.get(r["x"], r["x"]),
            "b": r["y"], "b_name": name_of.get(r["y"], r["y"]),
            "count": r["n"],
            "joint_share": (r["n"] / n_covered) if n_covered else 0.0,
            "lift": round((r["n"] * n_covered / (nx * ny)) if nx and ny else 0.0, 3),
        })

    key = "lift" if order == "lift" else "count"
    # 无论按哪个排序都先过 min_count 门槛：按 count 排序时低计数对本来
    # 就在末尾，按 lift 排序时它们会窜到榜首
    eligible = sorted(
        (p for p in pairs if p["count"] >= MIN_PAIR_COUNT),
        key=lambda p: (-p[key], -p["count"]),
    )
    top = max(1, min(100, top))

    # 矩阵给全量，不受 min_count 与 top 限制——热力图要的是整体形状，
    # 截断过的矩阵会让人以为角落的类别从不共现
    index = {c: i for i, c in enumerate(codes)}
    size = len(codes)
    matrix = [[0] * size for _ in range(size)]
    for p in pairs:
        i, j = index.get(p["a"]), index.get(p["b"])
        if i is None or j is None:
            continue
        matrix[i][j] = p["count"]
        matrix[j][i] = p["count"]

    return {
        "codes": codes,
        "names": [name_of.get(c, c) for c in codes],
        "layers": [c[:1] for c in codes],
        "pairs": eligible[:top],
        "pair_total": len(pairs),
        "scope": {"accidents": n_covered},
        "matrix": matrix,
        "sort": key,
        "filter": {"min_count": MIN_PAIR_COUNT},
    }


# ══════════════════════════════════════════════════════════════
# 年代趋势
# ══════════════════════════════════════════════════════════════


def timeline(
    params: accidents_query.ListParams, db_path: str | Path | None = None
) -> dict[str, Any]:
    """每年 × 每层的**覆盖事故数**，以及每年的总事故数。

    用覆盖事故数而非标注行数：趋势图回答的是「这一年有多少条事故涉及管理类
    致因」，不是「标注了多少条」。分母一并给出——1980 年代那几个点的占比
    波动到底是结构还是样本量，读者要能自己判断。

    **年份区间连续补零**：主库 1991–2004 是断档（没有事故记录）。
    若只返回有数据的年份，折线会直接从前一点连到后一点，读者看不出中间
    空了十四年——而这正是数据来源的历史特征，必须看得见。
    """
    where, args = accidents_query.build_where(params, db_path)

    total = {
        r["year"]: r["accidents"]
        for r in db.query(f"""
            SELECT substr(date, 1, 4) AS year, COUNT(*) AS accidents
            FROM accidents a{where} GROUP BY year ORDER BY year
        """, args, db_path=db_path)
        if r["year"]
    }

    by_layer: dict[str, dict[str, int]] = {}
    for r in db.query(f"""
        SELECT substr(a.date, 1, 4) AS year,
               substr(c.code, 1, 1) AS layer,
               COUNT(DISTINCT c.accident_id) AS accidents
        FROM caics_causes c JOIN accidents a ON a.id = c.accident_id
        {where} GROUP BY year, layer ORDER BY year
    """, args, db_path=db_path):
        if r["year"]:
            by_layer.setdefault(r["year"], {})[r["layer"]] = r["accidents"]

    # 无日期的事故落不到任何年份。实测全库 30 条（NULL 与空串都有），
    # 不报出来读者会拿它去和口径条上的事故总数相减，然后找不到差额
    all_accidents = db.scalar(
        f"SELECT COUNT(*) FROM accidents a{where}", args,
        default=0, db_path=db_path,
    ) or 0
    without_date = all_accidents - sum(total.values())

    if not total:
        return {"years": [], "accidents": [], "by_layer": [],
                "without_date": without_date}

    lo, hi = min(total), max(total)
    years = [f"{y:04d}" for y in range(int(lo), int(hi) + 1)]
    return {
        "years": years,
        "accidents": [total.get(y, 0) for y in years],
        "by_layer": [
            {
                "key": code,
                "label": LAYER_LABELS.get(code, code),
                "counts": [by_layer.get(y, {}).get(code, 0) for y in years],
            }
            for code in LAYER_ORDER
        ],
        "without_date": without_date,
    }


# ══════════════════════════════════════════════════════════════
# 体系定义
# ══════════════════════════════════════════════════════════════


def framework(db_path: str | Path | None = None) -> dict[str, Any]:
    """CAICS 体系定义（19 类 + 层归属 + 计数）。

    **直接复用概览的聚合结果**，不另算一遍：同一批数字在多处各算各的，正是
    本项目反复出现的失败模式（旧代码里曾有 6 份 `evidence_quality` 复制品）。
    页面初始化取一次，与概览页必然一致。

    ## 层内按**全库**计数降序，且不随筛选重排

    前端拿这份顺序当 19 类明细的骨架：筛选时条长变、位置不变。若按筛选后的
    计数重排，每改一次条件整个列表就洗一次牌，反而看不出「改条件后哪一类
    掉得最多」——而后者正是这张图要回答的。
    """
    data = overview.build(db_path)
    cat_of: dict[str, list[dict[str, Any]]] = {}
    for c in data["caics_catalog"]:
        cat_of.setdefault(c["layer"], []).append({
            "code": c["code"], "name": c["name"],
            "count": c["count"], "accidents": c["accidents"],
        })
    for items in cat_of.values():
        items.sort(key=lambda c: (-c["count"], c["code"]))

    return {
        "version": "caics_v1.0",
        "layers": [
            {
                "key": layer["key"],
                "label": layer["label"],
                "count": layer["count"],
                "categories": cat_of.get(layer["key"], []),
            }
            for layer in data["caics_layer"]
        ],
        "total": data["totals"]["causes"],
        "covered_accidents": data["totals"]["causes_covered"],
        "evidence_quality": data["evidence_quality"],
    }
