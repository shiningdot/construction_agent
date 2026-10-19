"""`scripts/` 下各脚本的公共路径解析。

要拿**主仓库**数据的有两组：四个 e2e 脚本（`e2e_verify_main` / `e2e_pipeline` /
`e2e_extract` / `e2e_click_m3`）要最终库，`screenshot_web` 要最终库 + 工作库 +
图片根。而它们可能在两处被运行：

- `.claude/worktrees/<name>/` —— 开发期，worktree 隔离
- 主仓库根 —— 用户实际执行的地方

原先四处都写死 `ROOT.parents[2] / "data" / "caiems_v2.db"`。这个层级只对
worktree 成立（`.claude/worktrees/<name>` 上溯三级正好是主仓库根）；在**主仓库
根**下执行时 `ROOT.parents[2]` 是 `/home`，默认值算成 `/home/data/caiems_v2.db`，
而脚本随后会用 `os.environ["CAIEMS_DB"] = MAIN_DB` 把它**强制**覆盖到 app 上，
于是必然起不来——用户会遇到的第一条命令就卡在这里。

### 为什么用 `.git` 的形态判主仓库，而不是「上溯找 data/caiems_v2.db」

按文件存在性上溯更直觉，但实测会踩坑：worktree 的 `data/` 里**确实有**一个
`caiems_v2.db`——它是 2026-09-30 在副本上验证 `add_indexes.py` 时留下的。上溯
第一步就命中了这个副本，而**副本是会与主库分叉的**。

⚠️（§2.5.94）那句"会分叉"**已经发生了**，见 `scripts/check_2594_data_replica.py`：
它实测两份库逐表行数（`image_files` / `image_manifest` 已各差若干行）。该节还查明
**本模块绕开了副本，但 `web/db.resolve_db_path()` 没有**——它按 `settings.DATA_DIR`
（`__file__` 相对）解析，于是 app 侧读的正是这个副本。故写"该脚本读主库"时必须
记住：**它驱动的 app 读的是另一份**，除非显式 `os.environ["CAIEMS_DB"] = …`。

`.git` 的形态是 git 自己的结构约定，不会因为谁往 data/ 里丢了个副本而改变：
**主仓库的 `.git` 是目录，worktree 的 `.git` 是指向 gitdir 的文件**。逐级上溯找
第一个 `.git` 是目录的位置，就是主仓库根。
"""
from __future__ import annotations

import os
from pathlib import Path

#: 本模块在 `scripts/` 下，上溯一级即**本仓库**（worktree 或主仓库）
REPO_ROOT = Path(__file__).resolve().parents[1]

DB_FILENAME = "caiems_v2.db"

#: 唯一可用的显式覆盖开关。**不是** `CAIEMS_DB`——这组脚本会用它覆盖后者
#: （各脚本都有 `os.environ["CAIEMS_DB"] = MAIN_DB`），只传 `CAIEMS_DB` 会被
#: 无声盖掉，看着像没生效。
ENV_MAIN_DB = "CAIEMS_MAIN_DB"


def main_repo_root() -> Path:
    """主仓库根。worktree 下上溯到主仓库，主仓库下即自身。"""
    p = REPO_ROOT
    while True:
        if (p / ".git").is_dir():        # worktree 的 .git 是文件，不会命中
            return p
        if p.parent == p:                # 到根了，没找到（非 git 环境）
            return REPO_ROOT
        p = p.parent


def main_db_path() -> str:
    """主仓库最终库的路径。

    解析顺序：环境变量 `CAIEMS_MAIN_DB` → 主仓库根下的 `data/caiems_v2.db`。
    """
    raw = os.environ.get(ENV_MAIN_DB)
    if raw:
        return str(Path(raw).expanduser().resolve())
    return str(main_repo_root() / "data" / DB_FILENAME)
