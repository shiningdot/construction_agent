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

from flask import Flask

from ..config import settings
from .api import api_bp
from .views import views_bp

WEB_DIR = Path(__file__).resolve().parent


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
    app.register_blueprint(views_bp)

    return app
