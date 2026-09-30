"""概览统计：`/api/overview` 的全部聚合。

## 职责边界

本模块只做**只读聚合**，不含任何筛选参数——筛选属于 `accidents_query.py`。
所有 SQL 常量集中在本文件顶部，便于与蓝图 04 的「数据来源」小节的数字
逐项对账（验收要求：概览页三级证据数须为 14,784 / 1,117 / 422）。

## 实测基准（2026-09-30，主库 2,623 条）

    totals          accidents 2,623  deaths 5,894  injuries 5,796
                    deaths_recorded 2,280  injuries_recorded 1,227
                    casualty_any 2,474  causes 16,323  causes_covered 2,618
    evidence        direct_quote 14,784 / indirect_infer 1,117 / weak_infer 422
    caics_layer     M 7,077 > H 4,723 > F 2,799 > T 1,533 > C 191
    year            1978–2026（35 个有值年份，1991–2004 断档，与论文一致）
    source          19 个来源，safehoo 1,247 为首

## 两处口径说明

1. **`deaths_recorded` 不是 `COUNT(deaths)`**。`deaths` 列为 0 的行有 343 条，
   语义是「报道未提及伤亡」而非「确认零伤亡」。故用 `deaths > 0` 计数，
   与论文的「有记录的事故数」口径一致。`SUM(deaths)` 只用 `COALESCE` 兜住
   全表 NULL 的边界情况。

2. **年份序列补零**。1991–2004 在主库中没有记录——这是采集断档，不是零事故。
   补零后前端能直接看出断档位置；若只返回有值的年份，图上会把这些年
   连成直线，等于盖住了数据本身的缺口。无日期或格式异常的行单独计入
   `unlabeled`，不混进序列。

## 缓存

按 `db.db_fingerprint()` 缓存整份结果。库一更新（补跑采集、并入暂存数据）
指纹立即变化，缓存自动失效——不需要重启服务。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, province

UNLABELED = "未标注"

# CAICS 五层。顺序按编码体系自身的层号（M→F→T→H→C），不随数据量变化——
# 按计数排序每次数据更新都会让图例换位，读者无法对照两次的截图。
# 标签为本项目统一中文名。
CAICS_LAYERS: tuple[tuple[str, str], ...] = (
    ("M", "管理因素"),
    ("F", "设施设备"),
    ("T", "技术工艺"),
    ("H", "人因"),
    ("C", "合规"),
)
LAYER_LABELS: dict[str, str] = dict(CAICS_LAYERS)

# 证据三级。键与 `caics_causes.evidence_quality` 的实测取值一一对应，
# 标签措辞遵循 CLAUDE.md 的既定决策（不写「证明」「全部编码有证据」）。
EVIDENCE_LABELS: dict[str, str] = {
    "direct_quote": "直接引用",
    "indirect_infer": "间接推断",
    "weak_infer": "弱推断",
}
EVIDENCE_ORDER: tuple[str, ...] = ("direct_quote", "indirect_infer", "weak_infer")


# ══════════════════════════════════════════════════════════════
# SQL
# ══════════════════════════════════════════════════════════════

_SQL_TOTALS = """
SELECT
    COUNT(*)                                                     AS accidents,
    COALESCE(SUM(deaths), 0)                                     AS deaths,
    COALESCE(SUM(injuries), 0)                                   AS injuries,
    SUM(CASE WHEN deaths   > 0 THEN 1 ELSE 0 END)                AS deaths_recorded,
    SUM(CASE WHEN injuries > 0 THEN 1 ELSE 0 END)                AS injuries_recorded,
    SUM(CASE WHEN deaths > 0 OR injuries > 0 THEN 1 ELSE 0 END)  AS casualty_any
FROM accidents
"""

_SQL_CAUSE_TOTALS = """
SELECT COUNT(*) AS causes, COUNT(DISTINCT accident_id) AS causes_covered
FROM caics_causes
"""

# 三张分布图共用同一形状：空值统一落到「未标注」，避免前端各自兜底。
_SQL_SEVERITY = f"""
SELECT CASE WHEN severity IS NULL OR TRIM(severity) = '' THEN '{UNLABELED}'
            ELSE severity END AS key,
       COUNT(*) AS count
FROM accidents
GROUP BY 1
ORDER BY count DESC, key
"""

_SQL_ACCIDENT_TYPE = f"""
SELECT CASE WHEN accident_type IS NULL OR TRIM(accident_type) = '' THEN '{UNLABELED}'
            ELSE accident_type END AS key,
       COUNT(*) AS count
FROM accidents
GROUP BY 1
ORDER BY count DESC, key
"""

_SQL_SOURCE = f"""
SELECT CASE WHEN source_name IS NULL OR TRIM(source_name) = '' THEN '{UNLABELED}'
            ELSE source_name END AS key,
       COUNT(*) AS count
FROM accidents
GROUP BY 1
ORDER BY count DESC, key
"""

_SQL_YEAR = """
SELECT CASE WHEN date IS NULL OR TRIM(date) = '' THEN '' ELSE substr(date, 1, 4) END AS key,
       COUNT(*) AS count
FROM accidents
GROUP BY 1
"""

_SQL_CAICS_LAYER = """
SELECT substr(code, 1, 1) AS key, COUNT(*) AS count
FROM caics_causes
GROUP BY 1
"""

_SQL_EVIDENCE = """
SELECT evidence_quality AS key, COUNT(*) AS count
FROM caics_causes
GROUP BY 1
"""

# CAICS 类目清单直接从数据查表得到——类名由 `caics_causes.name` 自带，
# 不需要硬编码，也不需要那份缺失的 taxonomy JSON（旧 web 500 的根源）。
_SQL_CAICS_CATALOG = """
SELECT substr(code, 1, 1) AS layer, code, name, COUNT(*) AS count,
       COUNT(DISTINCT accident_id) AS accidents
FROM caics_causes
GROUP BY code, name
ORDER BY code
"""


# ══════════════════════════════════════════════════════════════
# 单表聚合
# ══════════════════════════════════════════════════════════════


def _rows(con, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(sql, params)]


def _plain(con, sql: str) -> list[dict[str, Any]]:
    """原样返回 key/count 序列。"""
    return _rows(con, sql)


def _year_series(con) -> tuple[list[dict[str, Any]], int]:
    """年份序列（补零）+ 无法归入序列的条数。

    补零让 1991–2004 的采集断档在图上可见，而不是被连成直线掩盖掉。
    """
    valid: dict[str, int] = {}
    unlabeled = 0
    for r in _rows(con, _SQL_YEAR):
        key = r["key"] or ""
        if re.fullmatch(r"\d{4}", key):
            valid[key] = valid.get(key, 0) + r["count"]
        else:
            unlabeled += r["count"]

    if not valid:
        return [], unlabeled

    lo, hi = int(min(valid)), int(max(valid))
    series = [
        {"key": f"{y:04d}", "count": valid.get(f"{y:04d}", 0)}
        for y in range(lo, hi + 1)
    ]
    return series, unlabeled


def _layers(con) -> list[dict[str, Any]]:
    """五层分布，按 CAICS_LAYERS 顺序重排并补中文名。"""
    got = {r["key"]: r["count"] for r in _rows(con, _SQL_CAICS_LAYER)}
    return [
        {"key": code, "label": label, "count": got.get(code, 0)}
        for code, label in CAICS_LAYERS
    ]


def _evidence(con) -> tuple[list[dict[str, Any]], int]:
    """证据三级分布，按固定顺序重排；未知取值归入 unlabeled 计数。"""
    got = {r["key"]: r["count"] for r in _rows(con, _SQL_EVIDENCE)}
    known = [
        {
            "key": k,
            "label": EVIDENCE_LABELS[k],
            "count": got.pop(k, 0),
        }
        for k in EVIDENCE_ORDER
    ]
    return known, sum(got.values())


def _catalog(con) -> list[dict[str, Any]]:
    """19 类清单：层、编码、中文类名、编码数、覆盖事故数。"""
    out = []
    for r in _rows(con, _SQL_CAICS_CATALOG):
        out.append({
            "layer": r["layer"],
            "layer_label": LAYER_LABELS.get(r["layer"], r["layer"]),
            "code": r["code"],
            "name": r["name"],
            "count": r["count"],
            "accidents": r["accidents"],
        })
    return out


# ══════════════════════════════════════════════════════════════
# 组装
# ══════════════════════════════════════════════════════════════


def _build_uncached(db_path: str | Path | None) -> dict[str, Any]:
    with db.read_conn(db_path) as con:
        totals = dict(con.execute(_SQL_TOTALS).fetchone())
        totals.update(dict(con.execute(_SQL_CAUSE_TOTALS).fetchone()))

        year_series, year_unlabeled = _year_series(con)
        evidence, evidence_other = _evidence(con)

        data: dict[str, Any] = {
            "totals": totals,
            "evidence_quality": evidence,
            "severity": _plain(con, _SQL_SEVERITY),
            "accident_type": _plain(con, _SQL_ACCIDENT_TYPE),
            "year": year_series,
            "source": _plain(con, _SQL_SOURCE),
            "caics_layer": _layers(con),
            "caics_catalog": _catalog(con),
            "province_group": province.get_index(db_path).summary(),
        }

    # 数据质量问题显式回显，不藏在日志里
    data["data_quality"] = {
        "year_unlabeled": year_unlabeled,
        "evidence_unknown": evidence_other,
        "province": province.selfcheck(db_path),
    }
    return data


_CACHE: dict[tuple, dict[str, Any]] = {}


def build(db_path: str | Path | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """构建概览数据（按库指纹缓存）。"""
    fp = db.db_fingerprint(db_path)
    if refresh or fp not in _CACHE:
        _CACHE.clear()
        _CACHE[fp] = _build_uncached(db_path)
    return _CACHE[fp]


def reset_cache() -> None:
    """清空缓存。测试切换库文件时调用。"""
    _CACHE.clear()


def with_meta(db_path: str | Path | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """概览数据 + 元信息，`/api/overview` 的最终响应体。"""
    payload = dict(build(db_path, refresh=refresh))
    payload["meta"] = {
        "db_path": str(db.resolve_db_path() if db_path is None else db_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return payload
