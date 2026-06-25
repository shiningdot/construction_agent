"""Flask Web 应用。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import io
import csv as csv_module

from flask import Flask, jsonify, render_template, request, send_from_directory, Response

from construction_agent.config import settings
from construction_agent.database import DatasetManager
from construction_agent.extraction import ExtractionPipeline
from construction_agent.models.accident import Accident
from construction_agent.output.json_formatter import JSONFormatter
from construction_agent.output.csv_formatter import CSVFormatter

logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = settings.FLASK_SECRET_KEY

# 全局实例
pipeline = ExtractionPipeline()
db = DatasetManager()


# ── 图片服务 ──

@app.route("/images/<path:filename>")
def serve_image(filename):
    """提供本地图片文件服务。"""
    images_dir = settings.DATA_DIR / "images"
    return send_from_directory(str(images_dir), filename)


# ── 页面路由 ──

@app.route("/")
def index():
    """首页。"""
    return render_template("index.html")


@app.route("/dataset")
def dataset_page():
    """数据集页面。"""
    records = db.get_all()
    stats = db.stats()

    # 计算可用的年份和月份选项（用于前端筛选）
    years: set[str] = set()
    months: set[str] = set()
    for r in records:
        d = r.get("date", "") or ""
        if len(d) >= 7:
            years.add(d[:4])
            months.add(d[5:7])
    year_options = sorted(years, reverse=True)
    month_options = sorted(months)

    # 事故类型选项：按出现频率降序，"其他"固定最后
    type_counts = stats["by_type"]
    type_options = sorted(
        (k for k in type_counts if k != "其他"),
        key=lambda t: -type_counts[t],
    ) + (["其他"] if "其他" in type_counts else [])

    # 地点（省份）选项
    locations: set[str] = set()
    for r in records:
        loc = (r.get("location") or "").strip()
        if loc:
            locations.add(loc)
    location_options = sorted(locations)

    return render_template(
        "dataset.html",
        records=records,
        stats=stats,
        year_options=year_options,
        month_options=month_options,
        type_options=type_options,
        location_options=location_options,
    )


@app.route("/causes")
def causes_page():
    """致因分析页面。"""
    return render_template("causes.html")


@app.route("/api/causes/stats", methods=["GET"])
def api_causes_stats():
    """获取 HFACS-CS 致因统计数据。"""
    from construction_agent.hfacs import HfacsTaxonomy
    from collections import Counter

    taxonomy = HfacsTaxonomy()
    taxonomy.load()
    records = db.get_all()

    # 各级别计数
    level_counts = Counter()
    category_counts = Counter()
    # V1 四维度填充计数
    dim_counts = {}
    for dim in ["human_factors", "machine_factors", "management_factors", "environmental_factors"]:
        filled = sum(1 for r in records if r.get(dim) not in (None, "", [], 0))
        dim_counts[dim] = {"filled": filled, "total": len(records)}

    # HFACS 映射统计
    for r in records:
        for v1_field in ["human_factors", "machine_factors", "management_factors", "environmental_factors"]:
            factors = r.get(v1_field) or []
            if isinstance(factors, list):
                for f in factors:
                    if isinstance(f, dict):
                        cat = f.get("category", "").strip()
                        desc = f.get("description", "").strip()
                        if cat:
                            hfacs_results = taxonomy.classify(cat, desc)
                            for code, lname, cname in hfacs_results:
                                level_num = {
                                    "O": 1, "S": 2, "P": 3, "A": 4, "W": 5, "E": 6
                                }.get(code[0], 0)
                                if level_num:
                                    level_counts[level_num] += 1
                                category_counts[f"{code}:{cname}"] += 1

    level_names = {1:"组织因素", 2:"不安全监督", 3:"不安全行为前提条件",
                   4:"不安全行为", 5:"施工现场条件", 6:"物理环境"}

    return jsonify({
        "level_stats": [
            {"level": lvl, "name_cn": level_names.get(lvl, ""),
             "count": level_counts.get(lvl, 0)}
            for lvl in sorted(level_names.keys())
        ],
        "category_stats": [
            {"code": c.split(":")[0], "name": c.split(":")[1], "count": n}
            for c, n in category_counts.most_common(30)
        ],
        "dimension_fill_rates": {
            "human_factors": {"name_cn": "人的因素", "filled": dim_counts["human_factors"]["filled"], "total": dim_counts["human_factors"]["total"]},
            "machine_factors": {"name_cn": "设备因素", "filled": dim_counts["machine_factors"]["filled"], "total": dim_counts["machine_factors"]["total"]},
            "management_factors": {"name_cn": "管理因素", "filled": dim_counts["management_factors"]["filled"], "total": dim_counts["management_factors"]["total"]},
            "environmental_factors": {"name_cn": "环境因素", "filled": dim_counts["environmental_factors"]["filled"], "total": dim_counts["environmental_factors"]["total"]},
        },
        "total_records": len(records),
    })


@app.route("/api/causes/5m", methods=["GET"])
def api_causes_5m():
    """获取 5M 归一化致因统计。"""
    from collections import Counter

    records = db.get_all()

    dim_counts = Counter()
    cat_counts = Counter()
    record_counts = Counter()

    for r in records:
        five_m = r.get("cause_5m", {})
        record_dims = set()
        for dim, items in five_m.items():
            for item in items:
                dim_counts[dim] += 1
                cat_counts[f"{dim}::{item['standard_category']}"] += 1
                record_dims.add(dim)
        for d in record_dims:
            record_counts[d] += 1

    dim_labels = {"Man": "人员因素", "Machine": "设备因素", "Material": "物料因素",
                  "Method": "方法管理", "Environment": "环境因素"}

    return jsonify({
        "dimension_stats": [
            {"dim": d, "name_cn": dim_labels.get(d, d),
             "cause_count": dim_counts.get(d, 0),
             "record_count": record_counts.get(d, 0)}
            for d in ["Man", "Machine", "Material", "Method", "Environment"]
        ],
        "category_stats": [
            {"code": c.split("::")[0], "dim": c.split("::")[0],
             "category": c.split("::", 1)[1], "count": n}
            for c, n in cat_counts.most_common(50)
        ],
        "total_normalized": sum(1 for r in records if r.get("cause_5m")),
    })


@app.route("/api/causes/framework", methods=["GET"])
def api_causes_framework():
    """获取完整 HFACS-CS 框架（含每类定义+示例）。"""
    from construction_agent.hfacs import HfacsTaxonomy
    taxonomy = HfacsTaxonomy()
    taxonomy.load()

    with open("data/hfacs_cs_taxonomy.json", "r", encoding="utf-8") as f:
        raw = json.load(f)

    from collections import Counter
    category_counts = Counter()
    category_examples = {}

    records = db.get_all()
    for r in records:
        for v1_field in ["human_factors", "machine_factors", "management_factors", "environmental_factors"]:
            factors = r.get(v1_field) or []
            if isinstance(factors, list):
                for f in factors:
                    if isinstance(f, dict):
                        cat = f.get("category", "").strip()
                        desc = f.get("description", "").strip()
                        if cat:
                            hfacs_results = taxonomy.classify(cat, desc)
                            for code, lname, cname in hfacs_results:
                                category_counts[code] += 1
                                if code not in category_examples:
                                    category_examples[code] = []
                                if len(category_examples[code]) < 3:
                                    category_examples[code].append({
                                        "title": (r.get("title") or "")[:40],
                                        "v1_category": cat,
                                        "description": desc[:120],
                                    })

    levels = []
    for ldata in raw["levels"]:
        subcategories = []
        for cdata in ldata["subcategories"]:
            code = cdata["code"]
            subcategories.append({
                "code": code,
                "name_cn": cdata["name_cn"],
                "keywords": cdata["keywords"],
                "count": category_counts.get(code, 0),
                "examples": category_examples.get(code, []),
            })
        levels.append({
            "level": ldata["level"],
            "name": ldata["name"],
            "name_cn": ldata["name_cn"],
            "description": ldata["description"],
            "subcategories": sorted(subcategories, key=lambda x: -x["count"]),
        })

    return jsonify({
        "levels": levels,
        "v1_mapping": raw.get("v1_mapping", {}),
        "data_coverage": raw.get("data_coverage", {}),
        "limitations": raw.get("limitations", []),
    })


@app.route("/api/causes/detail", methods=["GET"])
def api_causes_detail():
    """获取逐条致因详情。"""
    from construction_agent.hfacs import HfacsTaxonomy
    taxonomy = HfacsTaxonomy()
    taxonomy.load()
    records = db.get_all()

    # 分页
    page = request.args.get("page", 1, type=int)
    per_page = 20
    start = (page - 1) * per_page
    end = start + per_page

    # 只取有致因的记录
    items = []
    for r in records:
        causes = []
        for v1_field, dim_name in [
            ("human_factors", "人的因素"), ("machine_factors", "设备因素"),
            ("management_factors", "管理因素"), ("environmental_factors", "环境因素"),
        ]:
            factors = r.get(v1_field) or []
            if isinstance(factors, list):
                for f in factors:
                    if isinstance(f, dict):
                        cat = f.get("category", "").strip()
                        desc = f.get("description", "").strip()
                        if cat:
                            hfacs_results = taxonomy.classify(cat, desc)
                            codes = [f"{code}:{cname}" for code, lname, cname in hfacs_results]
                            causes.append({
                                "dimension": dim_name,
                                "v1_category": cat,
                                "description": desc[:100],
                                "hfacs_codes": codes,
                            })
        if causes:
            items.append({
                "id": r.get("id", ""),
                "title": (r.get("title") or "")[:50],
                "date": r.get("date", ""),
                "accident_type": r.get("accident_type", ""),
                "causes": causes,
            })

    total = len(items)
    page_items = items[start:end]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": page_items,
    })


# ── API 路由 ──

@app.route("/api/process", methods=["POST"])
def api_process():
    """处理事故信息。"""
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "缺少 text 字段"}), 400

    text = data["text"].strip()
    if not text:
        return jsonify({"error": "内容不能为空"}), 400

    try:
        accident = pipeline.process(text)
        if not accident:
            return jsonify({"error": "提取失败，请检查 API Key 配置"}), 500

        # 保存到数据集
        db.add(accident)
        return jsonify({
            "success": True,
            "accident": accident.to_dict(),
        })
    except Exception as e:
        logger.error(f"处理异常: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/dataset", methods=["GET"])
def api_get_dataset():
    """获取数据集。"""
    records = db.get_all()
    return jsonify({
        "total": len(records),
        "records": records,
    })


@app.route("/api/dataset/stats", methods=["GET"])
def api_dataset_stats():
    """获取数据集统计。"""
    return jsonify(db.stats())


@app.route("/api/dataset/search", methods=["GET"])
def api_search_dataset():
    """搜索数据集。"""
    keyword = request.args.get("q", "")
    if not keyword:
        return jsonify({"records": db.get_all()})
    results = db.search(keyword)
    return jsonify({"records": results, "total": len(results)})


@app.route("/api/dataset/export", methods=["POST"])
def api_export_dataset():
    """导出数据集（浏览器下载）。"""
    data = request.get_json() or {}
    fmt = data.get("format", "json")

    records = db.get_all()
    accidents = [Accident(**r) for r in records]

    if fmt == "csv":
        fieldnames = CSVFormatter.get_fieldnames()
        output = io.StringIO()
        writer = csv_module.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for a in accidents:
            writer.writerow(a.to_flat_dict())
        content = output.getvalue()
        return Response(
            "﻿" + content,  # UTF-8 BOM 防止 Excel 中文乱码
            mimetype="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": "attachment; filename=accidents_export.csv"},
        )
    else:
        content = JSONFormatter.format_batch(accidents)
        return Response(
            content,
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=accidents_export.json"},
        )


@app.route("/api/dataset/clear", methods=["POST"])
def api_clear_dataset():
    """清空数据集。"""
    db.clear()
    return jsonify({"success": True})


# ── V2.5 HFACS 编码 API ──

@app.route("/api/hfacs/v25/stats", methods=["GET"])
def api_hfacs_v25_stats():
    """HFACS-CS 编码统计（层级分布 + 编码分布）。"""
    records = db.get_all()
    from collections import Counter

    level_counts: Counter[int] = Counter()
    code_counts: Counter[str] = Counter()
    dim_counts: Counter[str] = Counter()
    total_with_hfacs = 0

    for r in records:
        details = r.get("hfacs_details") or []
        if not details:
            continue
        total_with_hfacs += 1
        for d in details:
            code = d.get("code", "")
            level = d.get("level", 0)
            dim = d.get("source_dimension", "")
            if code:
                code_counts[code] += 1
            if level:
                level_counts[level] += 1
            if dim:
                dim_counts[dim] += 1

    level_names = {
        1: "组织因素", 2: "不安全监督", 3: "不安全行为的前提条件",
        4: "不安全行为", 5: "施工现场条件", 6: "物理环境",
    }

    return jsonify({
        "total_records": len(records),
        "total_with_hfacs": total_with_hfacs,
        "level_stats": [
            {"level": lvl, "name_cn": level_names.get(lvl, ""), "count": level_counts.get(lvl, 0)}
            for lvl in sorted(level_names)
        ],
        "code_stats": [
            {"code": code, "count": count}
            for code, count in code_counts.most_common()
        ],
        "dimension_stats": [
            {"dim": dim, "count": dim_counts.get(dim, 0)}
            for dim in ["human", "machine", "management", "environmental"]
        ],
    })


@app.route("/api/hfacs/v25/records", methods=["GET"])
def api_hfacs_v25_records():
    """返回有 HFACS 编码的事故列表（分页）。"""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    records = db.get_all()

    items = []
    for r in records:
        codes = r.get("hfacs_codes") or []
        if not codes:
            continue
        items.append({
            "id": r.get("id", ""),
            "title": (r.get("title") or "")[:60],
            "date": r.get("date", ""),
            "accident_type": r.get("accident_type", ""),
            "severity": r.get("severity", ""),
            "hfacs_codes": codes,
            "hfacs_count": len(codes),
            "hfacs_details": (r.get("hfacs_details") or [])[:8],
            "encoding_version": r.get("hfacs_encoding_version", ""),
        })

    total = len(items)
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": page_items,
    })


# ── 自动创建 static 目录（如需要） ──
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
