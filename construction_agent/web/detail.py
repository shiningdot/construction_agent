"""单事故详情：`/api/accidents/<id>` 的全部聚合。

## 字段分档

按有值率分「内容四档 + 过程元数据一区」（蓝图 05 §2.1）。分档表**只在
本模块定义一次**，随响应下发给前端——服务端算 `available`、前端按同一个
定义渲染，两边不会漂移。

`deaths=0` / `injuries=0` **算有值**：语义是「报道未提及伤亡」，不是缺失
（蓝图 00 §3.5 的口径）。

## 同一编码的多条标注

`caics_causes` 对 `(accident_id, code)` **没有唯一约束**：实测 278 组有
重复、涉及 616 行（最极端的一组 4 行）。那是同一编码的多次标注，各带
独立的证据文本，不是脏数据。

详情页按 `code` 合并成一条，把多条证据并列在 `citations` 里——**不是去重
丢弃**，而是把「一条编码」与「一份证据」两个层次分开呈现。该编码的等级
取多条中**最强**的一条：「原文有直引」就是有直引，不因并存一条弱推断而降级。

## 证据文本缺失

`evidence` / `inference` 实测各有 8 / 11 条空串（两列均无 NULL）。缺失时
给出 `None`，前端显示「未提供证据文本」——**不得拿 `code` 的类名去冒充
证据**，那会把「无证据」伪装成「有证据」，直接损害证据三级分层的可信度。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import accidents_query, db, images, overview, province

LAYER_LABELS = overview.LAYER_LABELS
EVIDENCE_LABELS = overview.EVIDENCE_LABELS

# 层的展示顺序：按编码体系自身的层号，与概览页一致
LAYER_ORDER: dict[str, int] = {
    code: i for i, (code, _) in enumerate(overview.CAICS_LAYERS)
}

# 证据等级强→弱。合并同一 code 的多条标注时取最强的一条。
_QUALITY_RANK: dict[str, int] = {
    "direct_quote": 0,
    "indirect_infer": 1,
    "weak_infer": 2,
}

# 等级 → 页面文案（蓝图 05 §2.5 的三级展示语义）
QUALITY_TEXT: dict[str, str] = {
    "direct_quote": "原文直接表述",
    "indirect_infer": "由原文推断",
    "weak_infer": "原文未直接提及",
}

NO_EVIDENCE_TEXT = "未提供证据文本"


# ══════════════════════════════════════════════════════════════
# 字段分档
# ══════════════════════════════════════════════════════════════

CONTENT_TIERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("A", "必显", (
        "title", "date", "province", "city", "accident_location_detail",
        "accident_type", "severity", "deaths", "injuries",
        "summary", "direct_cause",
    )),
    ("B", "有则显示", (
        "project_name", "work_task", "accident_time", "worker_type",
        "publish_date", "district", "search_enriched_text",
    )),
    ("C", "折叠", (
        "material_involved", "contractor", "equipment_involved",
        "direct_economic_loss", "contractor_type",
    )),
    ("D", "高级折叠", (
        "trapped_count", "developer", "fall_height", "building_structure",
        "supervision_company", "weather",
    )),
)

# 过程元数据区（独立折叠面板「处理信息」）。`classification_verdict` 在这里
# 出现是有意的：它记录「这条数据是怎么进来的」（网络轨 / 文献轨 / LLM 仲裁），
# 是数据来源可追溯的抓手。
#
# `source_count` 下发但**前端不展示**：主库 2,623 条实测全部等于 1，是恒量
# 不是信息。留在响应里是因为它对 API 的其它消费者仍有意义（判断某条是否
# 来自多源合并），页面上摆出来只会让人以为「每条事故只有一个来源」。
META_FIELDS: tuple[str, ...] = (
    "id", "source_name", "source_url", "crawl_date",
    "report_type", "text_type",
    "classification_verdict", "classification_confidence",
    "source_count", "search_performed",
    "pipeline_version", "step1_duration_ms", "step2_duration_ms", "created_at",
)

# 枚举列 → `enum_definitions.enum_type`。中文名查表得到，前端不硬编码编码表。
ENUM_COLUMNS: dict[str, str] = {
    "work_task": "work_task",
    "equipment_involved": "equipment_involved",
    "material_involved": "material_involved",
    "worker_type": "worker_type",
    "building_structure": "building_structure",
}


def has_value(v: Any) -> bool:
    """有值 = 非 NULL 且非空串。`0` 与 `False` 算有值。"""
    if v is None:
        return False
    return str(v).strip() != ""


# ══════════════════════════════════════════════════════════════
# SQL
# ══════════════════════════════════════════════════════════════

_SQL_ACCIDENT = "SELECT * FROM accidents WHERE id = ?"

# `id` 收尾保证同 sort_order 下的顺序稳定（实测同一编码的多行 sort_order 相同）
_SQL_CAUSES = """
SELECT code, name, evidence, inference, evidence_quality, sort_order
FROM caics_causes
WHERE accident_id = ?
ORDER BY sort_order, code, id
"""



# ══════════════════════════════════════════════════════════════
# 组装
# ══════════════════════════════════════════════════════════════


def _label_of(column: str, code: Any, db_path: str | Path | None) -> str | None:
    """枚举列的中文名。查不到时回退编码本身，不写兜底词。"""
    if not has_value(code):
        return None
    code = str(code)
    return accidents_query.enum_names(ENUM_COLUMNS[column], db_path).get(code, code)


def _tiers(fields: dict[str, Any]) -> list[dict[str, Any]]:
    """各档的字段清单与「实际有值数」，前端据此决定折叠面板是否渲染。"""
    return [
        {
            "key": key,
            "label": label,
            "fields": list(names),
            "available": sum(1 for n in names if has_value(fields.get(n))),
            "total": len(names),
        }
        for key, label, names in CONTENT_TIERS
    ]


def _group_causes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 code 合并同一编码的多条标注，层内按 sort_order 排。"""
    grouped: dict[str, dict[str, Any]] = {}

    for r in rows:
        slot = grouped.get(r["code"])
        if slot is None:
            layer = (r["code"] or "?")[:1]
            slot = grouped[r["code"]] = {
                "code": r["code"],
                "name": r["name"],
                "layer": layer,
                "layer_label": LAYER_LABELS.get(layer, layer),
                "sort_order": r["sort_order"],
                "citations": [],
            }
        slot["citations"].append({
            "evidence": r["evidence"] if has_value(r["evidence"]) else None,
            "inference": r["inference"] if has_value(r["inference"]) else None,
            "evidence_quality": r["evidence_quality"],
            "quality_label": QUALITY_TEXT.get(
                r["evidence_quality"], r["evidence_quality"]
            ),
        })

    items = sorted(
        grouped.values(),
        key=lambda s: (
            LAYER_ORDER.get(s["layer"], 99),
            s["sort_order"] if s["sort_order"] is not None else 0,
            s["code"],
        ),
    )
    for s in items:
        best = min(
            s["citations"],
            key=lambda c: _QUALITY_RANK.get(c["evidence_quality"], 9),
        )
        s["evidence_quality"] = best["evidence_quality"]
        s["quality_label"] = best["quality_label"]
        s["citation_count"] = len(s["citations"])
    return items


def build(
    accident_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """返回单事故的完整详情。id 不存在时返回 `None`。"""
    with db.read_conn(db_path) as con:
        row = con.execute(_SQL_ACCIDENT, (accident_id,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        cause_rows = [dict(r) for r in con.execute(_SQL_CAUSES, (accident_id,))]

    fields: dict[str, Any] = {}
    for _, _, names in CONTENT_TIERS:
        for n in names:
            fields[n] = row.get(n)

    for column in ENUM_COLUMNS:
        fields[column + "_label"] = _label_of(column, fields.get(column), db_path)

    # 地区走与列表页同一套归一，详情页与列表页的省名不会漂移
    fields["province_key"] = province.classify(fields.get("province"))
    fields["province_label"] = province.label_of(fields["province_key"])

    meta = {k: row.get(k) for k in META_FIELDS}

    # 图片来自**工作库**的 image_manifest（蓝图 06），不是最终库的 image_files：
    # 后者的 local_path 是旧登记，与磁盘真源差 123 张 / 83 个事故，展示以工作库
    # 为准。工作库不可用时给空清单 + 标志位，前端据此区分「没有图」与「索引没重建」。
    image_items = images.for_accident_marked(accident_id)

    return {
        "id": row["id"],
        "fields": fields,
        "tiers": _tiers(fields),
        "causes": {
            "items": _group_causes(cause_rows),
            "distinct": len({c["code"] for c in cause_rows}),
            "citations": len(cause_rows),
        },
        "meta": meta,
        "images": image_items,
        "images_available": images.available(),
        "raw_text": row.get("raw_text"),
    }
