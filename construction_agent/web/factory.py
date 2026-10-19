"""应用工厂：新展示层的 Flask 入口。

## 与旧 `app.py` 的关系

旧 `web/app.py` 在模块级创建 `app = Flask(...)`，注册了 34 个路由，其中
23 个返回空、2 个 500 崩溃（实测），根因是读 JSON 快照而非 SQLite 主库。
本工厂建的是**替换品**，M1–M5 逐个模块落地，全部就绪后再切换
`main.py web` 的入口。

在那之前两套并存：旧入口不动，新页面走 `create_app()`。

## 用工厂而非模块级单例

1. 测试可以在同一进程里反复建应用、换配置（`CAIEMS_DB` 指向不同库文件）
2. 没有「导入即副作用」——旧 app.py 在导入时就 `mkdir`，测试里会污染工作目录
3. 将来若要加 gunicorn 多 worker，工厂是必需的形态
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from ..config import settings
from .api import api_bp
from .collect import collect_bp
from .extract import extract_bp
from .review import review_bp
from .views import views_bp

WEB_DIR = Path(__file__).resolve().parent

#: 可信 Host。本应用是**本机工具**（`FLASK_HOST` 默认 `127.0.0.1`），不该接受
#: 任意 Host 头。不设它的话，浏览器里任意页面对 `127.0.0.1` 的请求都可能被
#: 「DNS rebinding」打成**同源**——那是唯一能让外站**读到**本机接口内容的路子
#: （同源策略拦的是「读」，不拦「发」）。端口由 Werkzeug 自动剥离，不必写进来；
#: `127.0.0.1.evil.com` 这类后缀伪装也挡得住（实测）。
TRUSTED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]

#: 会改状态的方法
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def create_app(config: dict[str, Any] | None = None) -> Flask:
    """建立展示层应用。

    Args:
        config: 可选的 Flask 配置覆盖，用于测试（如 ``TESTING=True``、
            ``SERVER_NAME``）。
    """
    app = Flask(
        __name__,
        template_folder=str(WEB_DIR / "templates"),
        static_folder=str(WEB_DIR / "static"),
    )
    app.secret_key = settings.FLASK_SECRET_KEY

    # 响应体里的中文不转成 \uXXXX：这是给人看的接口，
    # curl 出来直接可读，省掉每次都要 json.tool 的麻烦
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    if config:
        app.config.update(config)

    app.register_blueprint(api_bp)
    app.register_blueprint(collect_bp)
    app.register_blueprint(extract_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(views_bp)

    # ── 可达面：谁能把请求送到这些路由 ────────────────────────────────
    # **不能用 `setdefault`**：Flask 的默认配置里本来就有 `TRUSTED_HOSTS: None`
    # 这个键，`setdefault` 于是永远不生效（实测踩过，值是 None 而看着像设上了）。
    # 判据改成「调用方有没有显式传」——显式传 None 即「自认在可信网络里、主动
    # 关掉这一层」，不传则用上面那张表。
    if "TRUSTED_HOSTS" not in (config or {}):
        app.config["TRUSTED_HOSTS"] = TRUSTED_HOSTS

    @app.before_request
    def _same_origin_guard():
        """跨源写请求一律拒掉。

        **为什么要有**：`fetch` 与 HTML 表单能把请求**发**到任意源（同源策略拦的
        是「读」不是「发」），所以浏览器里任意一个页面都能让它替自己 POST 本机的
        写路由。本应用**没有身份校验**（唯一的保护是绑在 127.0.0.1），这一层是
        防「浏览器被利用」，不是防「本机以外的客户端」。

        判据用现成的 `Origin` 头——浏览器对每个 POST 都会带它，比维护一张 CSRF
        token 表轻得多，且不需要前端配合。

        两条边界（明写，别把它读强了）：
        - **不带 `Origin` 的请求放行**（`curl`、`requests`、Flask 测试客户端都不
          带）。它挡的是浏览器发起的跨源写，不是「本机以外的客户端」。
        - 同源判定用 `request.host_url`，而它取自 `Host` 头 ⇒ **这一条依赖上面
          的 `TRUSTED_HOSTS`**。两条必须同时在场：只留 `Origin` 校验，攻击者可以用
          DNS rebinding 把 Host 伪装成 `127.0.0.1` 从而「同源」；只留
          `TRUSTED_HOSTS`，跨源表单写照样能打到不需要 body 的路由。
        """
        if request.method not in UNSAFE_METHODS:
            return None
        origin = request.headers.get("Origin")
        if origin is None or origin == request.host_url.rstrip("/"):
            return None
        return jsonify({
            "ok": False,
            "message": "跨源写请求被拒（Origin 与服务端不一致）",
        }), 403

    return app
