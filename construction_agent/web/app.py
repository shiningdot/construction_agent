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


# ── 数据采集管理页面路由 ──

@app.route("/collect")
def collect_page():
    """数据采集管理页面。"""
    return render_template("collect.html")


# ── 数据采集管理 API ──

import threading
import time as _time_module
from collections import defaultdict

# 采集任务状态存储（内存）
_collect_tasks: dict[str, dict] = {}
_collect_lock = threading.Lock()

# 数据源配置存储路径
SOURCES_CONFIG_PATH = settings.DATA_DIR / "collector_sources.json"


def _load_sources_config() -> list[dict]:
    """加载数据源配置。"""
    if SOURCES_CONFIG_PATH.exists():
        try:
            return json.loads(SOURCES_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_sources_config(sources: list[dict]):
    """保存数据源配置。"""
    SOURCES_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_CONFIG_PATH.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.route("/api/collector/sources", methods=["GET"])
def api_collector_sources():
    """获取所有数据源列表。"""
    sources = _load_sources_config()
    # 补充状态信息
    for s in sources:
        name = s.get("name", "")
        if name in _collect_tasks:
            task = _collect_tasks[name]
            s["status"] = task.get("status", "idle")
        else:
            s.setdefault("status", "idle")
        s.setdefault("lastCollect", None)
        s.setdefault("count", 0)
    return jsonify({"sources": sources})


@app.route("/api/collector/sources", methods=["POST"])
def api_collector_add_source():
    """添加数据源。"""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "缺少 name 字段"}), 400

    sources = _load_sources_config()
    # 检查重复
    names = {s.get("name") for s in sources}
    if data["name"] in names:
        return jsonify({"error": f"数据源 '{data['name']}' 已存在"}), 409

    sources.append({
        "name": data.get("name", ""),
        "label": data.get("label", data.get("name", "")),
        "url": data.get("url", ""),
        "type": data.get("type", "web"),
        "desc": data.get("desc", ""),
        "delay": data.get("delay", 3),
    })
    _save_sources_config(sources)
    logger.info(f"已添加数据源: {data['name']}")
    return jsonify({"success": True})


@app.route("/api/collector/sources/<name>", methods=["DELETE"])
def api_collector_delete_source(name):
    """删除数据源。"""
    sources = _load_sources_config()
    sources = [s for s in sources if s.get("name") != name]
    _save_sources_config(sources)
    logger.info(f"已删除数据源: {name}")
    return jsonify({"success": True})


@app.route("/api/collector/sources/sync", methods=["POST"])
def api_collector_sync_sources():
    """同步数据源列表（批量更新状态）。"""
    data = request.get_json()
    if data and "sources" in data:
        _save_sources_config(data["sources"])
    return jsonify({"success": True})


@app.route("/api/collector/start", methods=["POST"])
def api_collector_start():
    """启动采集任务（异步）。"""
    data = request.get_json() or {}
    source_names = data.get("sources", [])
    retry_count = data.get("retry_count", 3)
    retry_delay = data.get("retry_delay", 5)
    crawl_options = data.get("crawl_options", {})  # GUI 传来的爬取配置

    if not source_names:
        return jsonify({"error": "未指定数据源"}), 400

    # 生成任务 ID
    task_id = f"task_{int(_time_module.time())}"
    sources_config = _load_sources_config()

    # 初始化任务状态
    task_sources = {}
    for name in source_names:
        cfg = next((s for s in sources_config if s.get("name") == name), {})
        task_sources[name] = {
            "status": "running",
            "progress": 0,
            "collected": 0,
            "success": 0,
            "failed": 0,
            "retries": 0,
            "added": 0,
            "errors": [],
        }

    with _collect_lock:
        _collect_tasks[task_id] = {
            "sources": task_sources,
            "finished": False,
            "total_added": 0,
            "total_errors": 0,
            "total_duplicates": 0,
            "logs": [],
        }

    # 异步执行采集
    thread = threading.Thread(
        target=_run_collection,
        args=(task_id, source_names, sources_config, retry_count, retry_delay, crawl_options),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "sources": list(task_sources.keys())})


def _run_collection(task_id: str, source_names: list[str],
                    sources_config: list[dict], retry_count: int, retry_delay: int,
                    crawl_options: dict | None = None):
    """在后台线程中执行采集。"""
    from construction_agent.collector.pipeline import CollectorPipeline
    from construction_agent.collector.scripts.run_collect import _register, COLLECTOR_REGISTRY

    _register()
    crawl_opts = crawl_options or {}

    def _add_log(level: str, message: str):
        """向任务日志添加条目。"""
        with _collect_lock:
            if task_id in _collect_tasks:
                _collect_tasks[task_id]["logs"].append({
                    "time": _time_module.strftime("%H:%M:%S"),
                    "level": level,
                    "message": message,
                })
                if len(_collect_tasks[task_id]["logs"]) > 100:
                    _collect_tasks[task_id]["logs"] = \
                        _collect_tasks[task_id]["logs"][-100:]

    _add_log("info", f"采集任务启动: {len(source_names)} 个数据源, "
             f"页范围={crawl_opts.get('start_page',1)}-{crawl_opts.get('end_page',0) or '末页'}, "
             f"最多{crawl_opts.get('max_items',0) or '不限'}条")

    for name in source_names:
        cfg = next((s for s in sources_config if s.get("name") == name), {})
        _add_log("info", f"开始采集: {cfg.get('label', name)}")

        # 更新进度：启动
        with _collect_lock:
            if task_id in _collect_tasks:
                _collect_tasks[task_id]["sources"][name]["progress"] = 5

        try:
            # 为 safehoo 使用新采集器 API
            if name == "safehoo":
                from construction_agent.collector.sources.safehoo import SafehooCollector as Sc
                collector = Sc(
                    start_page=crawl_opts.get("start_page", 1),
                    end_page=crawl_opts.get("end_page", 0),
                    max_items=crawl_opts.get("max_items", 0),
                    delay=crawl_opts.get("delay", 2.0),
                )
            elif name in COLLECTOR_REGISTRY:
                _, factory = COLLECTOR_REGISTRY[name]
                collector = factory()
                # 动态设置爬取参数
                for attr in ['start_page', 'end_page', 'max_items']:
                    if crawl_opts.get(attr) and hasattr(collector, attr):
                        setattr(collector, attr, crawl_opts[attr])
                if crawl_opts.get("delay") and hasattr(collector, '_delay'):
                    collector._delay = crawl_opts["delay"]
            else:
                _add_log("warn", f"{name}: 采集器未注册")
                with _collect_lock:
                    if task_id in _collect_tasks:
                        _collect_tasks[task_id]["sources"][name].update({"status": "failed", "progress": 100})
                continue

            # 更新进度
            with _collect_lock:
                if task_id in _collect_tasks:
                    _collect_tasks[task_id]["sources"][name]["progress"] = 10

            # ── 执行采集 ──
            result = collector.collect()

            # 处理返回值: dict (新API) 或 list (旧API)
            if isinstance(result, dict):
                # 新 SafehooCollector API: 返回统计 dict，数据已在 RawAccidentStore
                total = result.get("total_crawled", 0)
                filtered = result.get("filtered_out", 0)
                cached = result.get("cached_total", 0)
                errors = result.get("errors", 0)

                _add_log("info", f"{cfg.get('label', name)}: 采集 {total} 条建筑事故, 过滤 {filtered} 条非建筑, 缓存 {cached} 条")
                if errors:
                    _add_log("warn", f"{cfg.get('label', name)}: {errors} 个请求失败")

                with _collect_lock:
                    if task_id in _collect_tasks:
                        _collect_tasks[task_id]["sources"][name].update({
                            "status": "done",
                            "progress": 100,
                            "collected": total,
                            "success": cached,
                            "added": cached,
                            "filtered": filtered,
                        })
                        _collect_tasks[task_id]["total_added"] += cached

                _add_log("success", f"{cfg.get('label', name)}: 完成! 缓存 {cached} 条原始数据到 data/raw_accidents.json")

            elif isinstance(result, list):
                # 旧 API: 返回 RawAccidentText 列表
                collected_count = len(result)
                _add_log("info", f"{cfg.get('label', name)}: 采集到 {collected_count} 条原始数据")

                with _collect_lock:
                    if task_id in _collect_tasks:
                        _collect_tasks[task_id]["sources"][name].update({
                            "collected": collected_count,
                            "progress": 50,
                        })

                if result:
                    pipeline = CollectorPipeline(enable_llm=False)
                    pipeline.register_collector(collector)
                    added_count = 0
                    for item in result:
                        try:
                            pipeline.cleaner.clean(item.raw_text or "", is_html=bool(getattr(item, 'raw_html', None)))
                            pipeline.field_extractor.extract(item)
                            check = pipeline.deduplicator.check(item)
                            if not check["is_duplicate"]:
                                accident = pipeline._raw_to_accident(item)
                                if accident:
                                    pipeline.dataset.add(accident)
                                    added_count += 1
                        except Exception:
                            pass

                    with _collect_lock:
                        if task_id in _collect_tasks:
                            _collect_tasks[task_id]["sources"][name].update({
                                "progress": 90,
                                "success": added_count,
                                "added": added_count,
                            })
                            _collect_tasks[task_id]["total_added"] += added_count

                with _collect_lock:
                    if task_id in _collect_tasks:
                        _collect_tasks[task_id]["sources"][name].update({
                            "status": "done",
                            "progress": 100,
                        })

                _add_log("success", f"{cfg.get('label', name)}: 完成! 新增 {added_count if result else 0} 条")

            else:
                _add_log("warn", f"{cfg.get('label', name)}: 未知返回值类型 {type(result)}")
                with _collect_lock:
                    if task_id in _collect_tasks:
                        _collect_tasks[task_id]["sources"][name].update({"status": "done", "progress": 100})

        except Exception as e:
            _add_log("error", f"{cfg.get('label', name)}: 采集失败 - {str(e)[:200]}")
            with _collect_lock:
                if task_id in _collect_tasks:
                    _collect_tasks[task_id]["sources"][name].update({
                        "status": "failed",
                        "progress": 100,
                        "errors": [str(e)[:500]],
                    })
                    _collect_tasks[task_id]["total_errors"] += 1

    # 全部完成
    with _collect_lock:
        if task_id in _collect_tasks:
            _collect_tasks[task_id]["finished"] = True
    _add_log("success", "全部采集任务完成")


@app.route("/api/collector/status", methods=["GET"])
def api_collector_status():
    """获取采集任务状态（轮询）。"""
    task_id = request.args.get("task_id", "")
    if not task_id or task_id not in _collect_tasks:
        return jsonify({"finished": True, "sources": {}, "total_added": 0, "logs": []})

    with _collect_lock:
        task = _collect_tasks[task_id]
        # 收集新增日志
        logs = task.get("logs", [])[-20:]  # 只返回最近20条
        # 清空已读取的日志
        task["logs"] = task.get("logs", [])[-20:]

        return jsonify({
            "finished": task.get("finished", False),
            "sources": {k: {kk: vv for kk, vv in v.items() if kk != "errors"}
                       for k, v in task.get("sources", {}).items()},
            "total_added": task.get("total_added", 0),
            "total_errors": task.get("total_errors", 0),
            "total_duplicates": task.get("total_duplicates", 0),
            "logs": logs,
        })


@app.route("/api/collector/stats", methods=["GET"])
def api_collector_stats():
    """获取采集统计信息。"""
    db_stats = db.stats()
    return jsonify({
        "dataset_total": db_stats.get("total", 0),
        "total_deaths": db_stats.get("total_deaths", 0),
        "total_injuries": db_stats.get("total_injuries", 0),
        "by_type": db_stats.get("by_type", {}),
    })


@app.route("/api/collector/upload", methods=["POST"])
def api_collector_upload():
    """手动上传数据文件并导入。"""
    if "file" not in request.files:
        return jsonify({"success": False, "error": "未选择文件"}), 400

    file = request.files["file"]
    source_name = request.form.get("source", "manual_upload")

    if file.filename == "":
        return jsonify({"success": False, "error": "文件名为空"}), 400

    # 保存上传文件
    upload_dir = settings.DATA_DIR / "uploaded"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 安全文件名
    safe_name = file.filename.rsplit(".", 1)[0][:50]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "csv"
    save_path = upload_dir / f"{safe_name}_{int(_time_module.time())}.{ext}"
    file.save(str(save_path))

    # 解析导入
    imported = 0
    try:
        if ext == "json":
            data = json.loads(save_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("data", data.get("records", [data]))
            if not isinstance(data, list):
                data = [data]
        elif ext == "csv":
            import csv as csv_mod
            with open(save_path, "r", encoding="utf-8-sig") as f:
                reader = csv_mod.DictReader(f)
                data = list(reader)
        elif ext in ("xlsx", "xls"):
            try:
                import pandas as pd
                df = pd.read_excel(save_path)
                data = df.to_dict(orient="records")
            except ImportError:
                return jsonify({"success": False, "error": "需要安装 pandas + openpyxl"}), 500
        else:
            return jsonify({"success": False, "error": f"不支持的格式: {ext}"}), 400

        # 通过 CollectorPipeline 导入
        from construction_agent.collector.sources.academic_import import AcademicDatasetImporter
        importer = AcademicDatasetImporter()

        for item in data:
            try:
                raw = importer._generic_item_to_raw(item, source_file=source_name)
                if raw and raw.title:
                    raw.source = source_name
                    from construction_agent.collector.pipeline import CollectorPipeline
                    pipeline_obj = CollectorPipeline(enable_llm=False)
                    pipeline_obj.field_extractor.extract(raw)
                    result = pipeline_obj.deduplicator.check(raw)
                    if not result["is_duplicate"]:
                        accident = pipeline_obj._raw_to_accident(raw)
                        if accident:
                            pipeline_obj.dataset.add(accident)
                            imported += 1
            except Exception:
                continue

    except Exception as e:
        logger.error(f"上传文件解析失败: {e}")
        return jsonify({"success": False, "error": f"文件解析失败: {str(e)}"}), 500

    return jsonify({"success": True, "imported": imported})


# ── 原始数据集 API ──

@app.route("/api/collector/raw-data", methods=["GET"])
def api_collector_raw_data():
    """获取原始缓存数据，支持分页。

    Query params:
        page: 页码（1-based，默认1）
        per_page: 每页条数（默认100，最大500）
        status: 筛选状态 (new/processed/filtered_out，默认全部)
    """
    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()
    items = store.get_all()
    stats = store.stats()

    # 筛选：状态
    status_filter = request.args.get("status", "")
    if status_filter:
        items = [i for i in items if i.get("status") == status_filter]
    # 筛选：来源
    source_filter = request.args.get("source", "")
    if source_filter:
        items = [i for i in items if i.get("source_name") == source_filter]
    # 搜索：关键词
    keyword = request.args.get("keyword", "")
    if keyword:
        items = [i for i in items
                 if keyword.lower() in (i.get("title", "") + i.get("content", "")).lower()]

    # 排序（按采集时间倒序）
    sorted_items = sorted(items, key=lambda x: x.get("crawl_date", ""), reverse=True)

    # 分页
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 100, type=int), 500)
    start = (page - 1) * per_page
    paged = sorted_items[start:start + per_page]

    return jsonify({
        "stats": stats,
        "total": len(sorted_items),
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (len(sorted_items) + per_page - 1) // per_page),
        "items": paged,
    })


@app.route("/api/collector/raw-data/import", methods=["POST"])
def api_collector_raw_import():
    """将原始缓存中的记录导入到正式数据集。

    Body:
        ids: 要导入的记录ID列表（空列表或"all" = 导入全部 new 状态记录）
        import_all: true = 导入全部 new 状态记录
    """
    data = request.get_json() or {}
    import_all = data.get("import_all", False)
    record_ids = set(data.get("ids", []))

    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()

    # 确定要导入的记录
    if import_all or (not record_ids and not import_all and data.get("ids") == []):
        # 未指定 ids 时也导入全部
        pass

    to_import = []
    for item in store.get_new():
        if import_all or not record_ids:
            to_import.append(item)
        elif item.get("id") in record_ids:
            to_import.append(item)

    if not to_import:
        return jsonify({"success": True, "imported": 0, "message": "无待导入记录"})

    imported = 0
    failed = 0
    for item in to_import:
        try:
            pipeline_obj = ExtractionPipeline()
            accident = pipeline_obj.process(
                f"{item.get('title', '')}\n\n{item.get('content', '')}",
                enable_search=False,
                source=item.get("source_name", "safehoo"),
            )
            if accident:
                db.add(accident)
                imported += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"导入失败 {item.get('id')}: {e}")
            failed += 1

    # 标记为已处理
    store.mark_processed([i["id"] for i in to_import])

    return jsonify({"success": True, "imported": imported, "failed": failed, "total": len(to_import)})


@app.route("/api/collector/raw-data/detail", methods=["GET"])
def api_collector_raw_detail():
    """获取单条原始数据的完整详情。"""
    rid = request.args.get("id", "")
    if not rid:
        return jsonify({"error": "缺少 id"}), 400

    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()
    for item in store.get_all():
        if item.get("id") == rid:
            return jsonify(item)

    return jsonify({"error": "未找到"}), 404


@app.route("/api/collector/raw-data/export", methods=["GET"])
def api_collector_raw_export():
    """导出原始缓存数据（JSON 或 CSV）。"""
    fmt = request.args.get("format", "json")
    ids_str = request.args.get("ids", "")

    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()

    items = store.get_all()
    if ids_str:
        selected = set(ids_str.split(","))
        items = [i for i in items if i.get("id") in selected]

    if fmt == "csv":
        output = io.StringIO()
        writer = csv_module.DictWriter(output, fieldnames=[
            "id", "title", "content", "source_url", "source_name",
            "crawl_date", "publish_date", "status"
        ])
        writer.writeheader()
        for item in items:
            writer.writerow({k: str(item.get(k, ""))[:500] for k in writer.fieldnames})
        content = output.getvalue()
        return Response(
            "﻿" + content,
            mimetype="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": "attachment; filename=raw_accidents.csv"},
        )
    else:
        return Response(
            json.dumps(items, ensure_ascii=False, indent=2, default=str),
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=raw_accidents.json"},
        )


@app.route("/api/collector/raw-data/clear", methods=["POST"])
def api_collector_raw_clear():
    """清空原始缓存。"""
    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()
    store.clear()
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════
# 边缘案例人工审核 API
# ══════════════════════════════════════════════════════

@app.route("/api/collector/review", methods=["GET"])
def api_collector_review_list():
    """获取待审核列表。"""
    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()
    items = store.get_review()
    # 按采集时间倒序
    items.sort(key=lambda r: r.get("crawl_date", ""), reverse=True)
    return jsonify(items)


@app.route("/api/collector/review/classify", methods=["POST"])
def api_collector_review_classify():
    """单条确认/拒绝。

    Body: {"id": "xxx", "classification": "confirmed_construction|confirmed_non_construction", "note": ""}
    """
    from construction_agent.collector.stores import RawAccidentStore
    data = request.get_json() or {}
    record_id = data.get("id", "")
    classification = data.get("classification", "")
    note = data.get("note", "")

    if not record_id or classification not in (
        "confirmed_construction", "confirmed_non_construction"
    ):
        return jsonify({"success": False, "error": "参数无效"}), 400

    store = RawAccidentStore()
    ok = store.classify(record_id, classification, note)
    return jsonify({"success": ok})


@app.route("/api/collector/review/batch", methods=["POST"])
def api_collector_review_batch():
    """批量确认/拒绝。

    Body: {"ids": ["xxx", "yyy"], "classification": "confirmed_construction|confirmed_non_construction"}
    """
    from construction_agent.collector.stores import RawAccidentStore
    data = request.get_json() or {}
    ids = data.get("ids", [])
    classification = data.get("classification", "")

    if not ids or classification not in (
        "confirmed_construction", "confirmed_non_construction"
    ):
        return jsonify({"success": False, "error": "参数无效"}), 400

    store = RawAccidentStore()
    count = store.batch_classify(ids, classification)
    return jsonify({"success": True, "count": count})


@app.route("/api/collector/review/stats", methods=["GET"])
def api_collector_review_stats():
    """审核统计。"""
    from construction_agent.collector.stores import RawAccidentStore
    store = RawAccidentStore()
    s = store.stats()
    return jsonify({
        "pending": s.get("review", 0),
        "confirmed": s.get("confirmed_construction", 0),
        "rejected": s.get("confirmed_non_construction", 0),
        "total": s.get("total", 0),
    })


# ── 自动创建 static 目录（如需要） ──
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
