"""页面蓝图：服务端只出壳，数据由前端拉 API。

蓝图 04 §4.2 定的分工——Jinja 渲染静态骨架与文案，列表、图表、筛选器
全部由 `static/overview.js` 通过 `/api/*` 填充。这样做的好处是筛选、
翻页、排序都只走一次 JSON 往返，不会整页刷新；代价是首屏必须等一次
JS 执行，对一个内网查询工具可以接受。

模板沿用 `templates/` 目录（与旧页面共存），但用 `base.html` 做统一骨架——
旧的三份模板各自重复了一份 header 与主题脚本，那是本次重建要消除的。
"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, send_file

from ..collector import executor as collect_executor
from . import images

views_bp = Blueprint("views", __name__)

# 进程重启后 `collect_tasks` 里会留下几行写着 `running` 的僵死任务（线程池
# 随进程没了）。清理只需要做一次，放在页面入口而不是 `create_app()`：工厂
# 会被测试反复调用几十次，每次都去写一次库既慢又脏。
_reconciled = False


@views_bp.get("/")
def overview_page():
    """概览与检索。蓝图 04 的落地页。"""
    return render_template("overview.html")


@views_bp.get("/collect")
def collect_page():
    """在线采集。蓝图 01 的落地页，对应流水线的阶段①。"""
    global _reconciled
    if not _reconciled:
        _reconciled = True
        try:
            n = collect_executor.reconcile_stale_tasks()
            if n:
                current_app.logger.info("清理了 %d 个中断的采集任务", n)
        except Exception as e:  # noqa: BLE001 —— 库没建时不该拦住页面
            current_app.logger.debug("跳过僵死任务清理: %s", e)
    return render_template("collect.html")


@views_bp.get("/accident/<accident_id>")
def accident_page(accident_id: str):
    """单事故详情。蓝图 05 §4.1。id 交给前端拉 API，页面本身只出壳。"""
    return render_template("accident.html", accident_id=accident_id)


@views_bp.get("/causes")
def causes_page():
    """致因分析。蓝图 05 §4.2。四张图各自拉一个端点，筛选条件放 URL hash。"""
    return render_template("causes.html")


@views_bp.get("/media/<path:rel_path>")
def media(rel_path: str):
    """事故图片。蓝图 06 §4。

    只服务 `imageV3/`（最终版）——`images/` 与 `imageV2/` 不给，三版里同一
    事故可能有重复图片，放出来读者没法判断哪张是最终采用的。越界与不存在
    都由 `images.resolve_media_path()` 挡掉，这里只负责 404 与缓存头。
    """
    path = images.resolve_media_path(rel_path)
    if path is None:
        abort(404)

    resp = send_file(path, conditional=True)
    # 图片文件本身不会变（改了就是换文件），可以长缓存。用 max-age 而不是
    # immutable：将来若同名替换，immutable 会让浏览器一年都不再回源。
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp
