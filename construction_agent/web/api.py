"""JSON API 蓝图：展示层的数据端点。

对应蓝图 04「接口设计」一节。所有端点都是**只读**的——写路径在
`staging_db.py`（工作库）与后续的 promote 模块（并入最终库），
展示层拿到的句柄由 `db.connect_readonly()` 保证物理只读。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/overview` | 概览统计（totals / 五张分布 / CAICS 类目 / 省份分组） |
| GET | `/api/accidents` | 事故列表（筛选 / 排序 / 分页） |
| GET | `/api/accidents/<id>` | 单事故详情（字段 / 致因 / 图片 / 元信息，一次返回） |
| GET | `/api/images/stats` | 图片覆盖统计 |
| GET | `/api/filters` | 筛选器各字段的可选值与计数 |
| GET | `/api/causes/framework` | CAICS 体系定义（19 类 + 层归属 + 计数） |
| GET | `/api/causes/distribution` | 五层金字塔 + 19 类明细 + 证据等级分布 |
| GET | `/api/causes/cooccurrence` | 编码共现矩阵与 Top 对（去重口径） |
| GET | `/api/causes/timeline` | 每年 × 每层的覆盖事故数 |
| GET | `/api/province/audit` | 省份归一的逐条明细（数据质量核查用） |
| GET | `/api/health` | 库连通性与表行数自检 |

致因四端点与 `/api/accidents` **共用同一套筛选参数**（同一个 `ListParams`、
同一个 `build_where()`）——两页对「筛选了事故类型=坍塌」的理解必须一致，
否则用户从列表点进致因页会看到对不上的数字。

`/api/health` 是蓝图 07 中 M1 的启动自检项：库路径、九表行数、索引是否
齐备，一眼可判「跑起来的环境对不对」。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import accidents_query, causes, db, detail, images, overview, province

api_bp = Blueprint("api", __name__, url_prefix="/api")


# ══════════════════════════════════════════════════════════════
# 概览
# ══════════════════════════════════════════════════════════════


@api_bp.get("/overview")
def api_overview():
    """概览统计。按库指纹缓存，库更新即失效。"""
    return jsonify(overview.with_meta())


# ══════════════════════════════════════════════════════════════
# 列表
# ══════════════════════════════════════════════════════════════


@api_bp.get("/accidents")
def api_accidents():
    """事故列表。参数见 `accidents_query.ListParams`。"""
    params = accidents_query.ListParams.from_request(request.args)
    return jsonify(accidents_query.run(params))


@api_bp.get("/accidents/<accident_id>")
def api_accident_detail(accident_id: str):
    """单事故详情。一次返回全部内容，前端不再二次请求。

    找不到时返回 **404**（而不是 200 + 空对象）——「没有这条」与「这条
    什么都没有」是两件事，前端要能分开处理。
    """
    data = detail.build(accident_id)
    if data is None:
        return jsonify({
            "error": "accident_not_found",
            "message": f"事故 {accident_id} 不存在",
        }), 404
    return jsonify({"ok": True, "data": data})


# `/accidents/<id>/images` 曾在此。删于 2026-10-01：详情接口一次就返回
# `images`，蓝图为它写的定位是「画廊单独刷新或分页」，而前端从未调用（42 条
# 路由里唯一的孤儿），单事故实测最多 7 张、≥10 张的 0 个——分页也不成立。
# 它带来的实际成本是那段打标逻辑与 detail.py 逐字重复两份。取数已收敛到
# `images.for_accident_marked()`。


@api_bp.get("/images/stats")
def api_images_stats():
    """图片覆盖统计。蓝图 06 §4。

    覆盖率按三版合并口径算（559/2,623 ≈ 21.3%），并给出
    `accidents_with_images_legacy_only`：只有历史版本目录里有图、一张最终版
    都没有的事故数。截到 `imageV3` 时那批事故在界面上呈现为「无图片」，这个
    字段就是截断代价的直接度量。
    """
    return jsonify({"ok": True, "data": images.stats()})


@api_bp.get("/filters")
def api_filters():
    """筛选器的可选值。取值来自数据本身，新增枚举值无需改代码。"""
    return jsonify(accidents_query.filter_options())


# ══════════════════════════════════════════════════════════════
# 致因分析（蓝图 05 §4.2）
# ══════════════════════════════════════════════════════════════


@api_bp.get("/causes/framework")
def api_causes_framework():
    """CAICS 体系定义。页面初始化取一次。"""
    return jsonify({"ok": True, "data": causes.framework()})


@api_bp.get("/causes/distribution")
def api_causes_distribution():
    """五层金字塔 + 19 类明细 + 证据等级分布。与列表页共用同一套筛选参数。"""
    params = accidents_query.ListParams.from_request(request.args)
    return jsonify({
        "ok": True,
        "scope": causes.scope(params),
        "filter": params.normalized(),
        "data": causes.distribution(params),
    })


@api_bp.get("/causes/cooccurrence")
def api_causes_cooccurrence():
    """编码共现。`order` 取 `count`（默认）或 `lift`。

    去重口径——`count` 是**共现的事故数**，不是编码出现次数。默认按 count
    排序的理由（实测 lift 榜首是只有 24 条事故支撑的 C1+H4）写在
    `causes.cooccurrence()` 的 docstring 里。
    """
    params = accidents_query.ListParams.from_request(request.args)
    order = request.args.get("order", "count")
    if order not in ("count", "lift"):
        order = "count"
    try:
        top = int(request.args.get("top", 20))
    except (TypeError, ValueError):
        top = 20

    return jsonify({
        "ok": True,
        "data": causes.cooccurrence(params, top=top, order=order),
    })


@api_bp.get("/causes/timeline")
def api_causes_timeline():
    """每年 × 每层的覆盖事故数，含每年总事故数作分母。"""
    params = accidents_query.ListParams.from_request(request.args)
    return jsonify({"ok": True, "data": causes.timeline(params)})


# ══════════════════════════════════════════════════════════════
# 数据质量
# ══════════════════════════════════════════════════════════════


@api_bp.get("/province/audit")
def api_province_audit():
    """省份归一的逐条明细。

    `province` 列有 79 个原始取值，其中 47 个不是标准省级全称。
    本端点把它们按归一去向分三类列出——已归一的地址脏值不掩盖、
    境外记录可追溯、认不出来的显式暴露。
    """
    idx = province.get_index()
    rows = idx.audit()
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["count"]

    return jsonify({
        "rows": rows,
        "by_kind": by_kind,
        "summary": idx.summary(),
        "selfcheck": province.selfcheck(),
    })


@api_bp.get("/health")
def api_health():
    """库连通性与结构自检。启动时打开一次即可确认环境配置正确。"""
    path = db.resolve_db_path()
    if not path.exists():
        return jsonify({
            "ok": False,
            "db_path": str(path),
            "error": "库文件不存在",
            "hint": "用环境变量 CAIEMS_DB 指定，例如 "
                    "CAIEMS_DB=/home/suntianhao/construction_agent/data/caiems_v2.db",
        }), 503

    counts = {t: db.scalar(f"SELECT COUNT(*) FROM {t}", default=0) for t in db.list_tables()}
    indexes = [i["name"] for i in db.list_indexes()]

    # 展示层最依赖的三个索引。缺了不报错，但列表页会慢两个数量级
    # （实测：has_causes 筛选 1.8 ms → 3,541 ms）。
    expected = {"idx_cause_acc", "idx_acc_province", "idx_acc_date"}
    missing = sorted(expected - set(indexes))

    # `ok` 只说「库能查到、表在」。索引是性能问题不是可用性问题，
    # 单列 `indexes_ok`——否则一座刚建好的测试库会被判成不健康。
    return jsonify({
        "ok": True,
        "db_path": str(path),
        "tables": counts,
        "index_count": len(indexes),
        "indexes_ok": not missing,
        "missing_indexes": missing,
        "hint": (
            "缺索引会让列表筛选慢上千倍，执行："
            "python3 scripts/add_indexes.py"
        ) if missing else None,
    })


# ══════════════════════════════════════════════════════════════
# 错误处理
# ══════════════════════════════════════════════════════════════


@api_bp.errorhandler(db.DatabaseNotFoundError)
def _handle_db_missing(exc: db.DatabaseNotFoundError):
    """库不存在时给结构化错误，而不是 Flask 的 HTML 500 页。"""
    return jsonify({
        "error": "database_not_found",
        "message": str(exc),
    }), 503
