"""给核查脚本起一个本地服务。

`screenshot_web.py`（看界面坏没坏）与 `check_web_interaction.py`（看点了之后
对不对）都要「把 app 起在某个端口上，用无头浏览器打开」。这段逻辑原先在
screenshot 里内联，第二个使用者出现时抽出来。

与 `_common.py` 的分工：那个管**路径怎么找**，这个管**服务怎么起**。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from _common import main_repo_root


def point_at(main_db: str, staging_db: Path | str) -> None:
    """把 app 的三个数据路径显式指过去。

    三者的默认值都从 `settings.DATA_DIR` 派生，而它跟着**被导入的包**走——在
    worktree 下运行时会指向 worktree 自己的 `data/`，那里既没有库（`.gitignore`
    忽略了）也没有图片目录。所以三个都得设，少一个就有一块是空的：

    | 环境变量 | 不设的后果 |
    |---|---|
    | `CAIEMS_DB` | 概览/详情/致因全空，因为读不到最终库 |
    | `CAIEMS_STAGING_DB` | 采集/提取/审核三页显示 `available=false` |
    | `CAIEMS_DATA_DIR` | 详情页图片全 404（2026-10-01 实测：7 张图 7 个 404） |
    """
    os.environ["CAIEMS_DB"] = str(main_db)
    os.environ["CAIEMS_STAGING_DB"] = str(staging_db)
    os.environ["CAIEMS_DATA_DIR"] = str(main_repo_root() / "data")


def serve(port: int):
    """起一个后台线程的服务，返回 `(server, base_url)`。

    调用方负责在结束时 `server.shutdown()`。用 werkzeug 的 `make_server` 而不是
    `app.run()`：后者会装信号处理器，只能在主线程跑，而这里要在脚本里一边跑服务
    一边驱动浏览器。
    """
    from werkzeug.serving import make_server

    from construction_agent.web.factory import create_app

    app = create_app()
    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"
