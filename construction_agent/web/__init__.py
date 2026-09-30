"""Web 模块（新展示层）。读主库 `caiems_v2.db`，走 S1–S4 CAICS 口径。

## 导入约定

从具体子模块导入，别指望本包再转出一道口子：

    from construction_agent.web import db, province, overview, accidents_query
    from construction_agent.web.factory import create_app

## 旧 `app.py` 已封存（2026-10-01）

这里原先有一段 PEP 562 的 `__getattr__`，把 `from construction_agent.web
import app` 转到旧 `web/app.py`，只为不打断 `main.py web` 那条旧入口。现在
入口本身已经切到 `create_app()`，转接层随之删除。

删掉它不只是清理：旧应用在**导入时**就 `mkdir`、建 `ExtractionPipeline`
单例、加载 HFACS 分类文件——只要转接层还在，任何一个"我只想取个 db 模块"
的导入都可能把它连根拔起。现在 `from construction_agent.web import app`
会干脆地 `ImportError`，而不是悄悄给一个读 JSON 快照、23 个端点返回空的
应用。**报错比给错东西好**。
"""

from __future__ import annotations

__all__: list[str] = []
