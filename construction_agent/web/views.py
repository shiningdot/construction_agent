"""页面蓝图：服务端只出壳，数据由前端拉 API。

蓝图 04 §4.2 定的分工——Jinja 渲染静态骨架与文案，列表、图表、筛选器
全部由 `static/overview.js` 通过 `/api/*` 填充。这样做的好处是筛选、
翻页、排序都只走一次 JSON 往返，不会整页刷新；代价是首屏必须等一次
JS 执行，对一个内网查询工具可以接受。

模板沿用 `templates/` 目录（与旧页面共存），但用 `base.html` 做统一骨架——
旧的三份模板各自重复了一份 header 与主题脚本，那是本次重建要消除的。
"""

from __future__ import annotations

from flask import Blueprint, render_template

views_bp = Blueprint("views", __name__)


@views_bp.get("/")
def overview_page():
    """概览与检索。蓝图 04 的落地页。"""
    return render_template("overview.html")


@views_bp.get("/accident/<accident_id>")
def accident_page(accident_id: str):
    """单事故详情。蓝图 05 §4.1。id 交给前端拉 API，页面本身只出壳。"""
    return render_template("accident.html", accident_id=accident_id)
