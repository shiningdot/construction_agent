"""§2.5.94 数据副本的适用范围：web 与核查脚本读的是**哪一个库**。

## 这一节要回答什么

§2.5.91 查的是「**核查副本的适用范围**」——核查脚本核的是哪一份 `web/` 源码，
把「生产侧 md5 ↔ 核查侧 md5」的分叉登记成表。那一节管的是**代码副本**。

本节问同一个问题的**下一半**：

> 数据呢？

因为本项目在同一台机器上有**两套库路径解析规则**，而它们今天给出的答案是
**两个不同的文件**。

## 两套规则（实测）

| 规则 | 谁在用 | 解析依据 | 落点 |
|---|---|---|---|
| `_common.main_db_path()` | `scripts/` | **主仓库根**（靠 `.git` 是目录还是文件判） | `main/data/caiems_v2.db` |
| `web/db.resolve_db_path()` | `construction_agent/web/` | `settings.DATA_DIR`，而它是 `__file__` 相对 | **本 checkout** 的 `data/caiems_v2.db` |

同一个进程、同一次运行里：

```
main_repo_root()      /home/suntianhao/construction_agent
main_db_path()        /home/suntianhao/construction_agent/data/caiems_v2.db
web.resolve_db_path() .../worktrees/classifier-mod/data/caiems_v2.db
```

**这不是配置错了**，恰恰相反：`web/` 用 `__file__` 相对路径是**正确的**
——用户从主仓库启动 web 时，它就该读主仓库的库。问题出在另一半：

**worktree 的 `data/` 里有一份本不该存在的库。**

它是 2026-09-30 在副本上验证 `add_indexes.py` 时留下的（旁证：
`data/caiems_v2.db.bak.before_indexes_20260930_223404`）。CLAUDE.md 的双目录
结构表里，worktree 一栏只写「论文稿件、论文脚本、消融实验数据」——**数据库
不在其中**。它是**未声明的产物**。

## 这个隐患前人早就写下了，只是没人守

`scripts/_common.py` 的模块 docstring 里逐字写着：

> 按文件存在性上溯更直觉，但实测会踩坑：worktree 的 `data/` 里**确实有**一个
> `caiems_v2.db`……上溯第一步就命中了这个副本，而**副本是会与主库分叉的**。

于是 `_common` 改用 `.git` 的形态判主仓库，**绕开了**这个副本。

**但 `web/db.resolve_db_path()` 没有得到同款处理**——它照样命中副本。

所以这是一个**做了一半的修复**：脚本侧绕开了，app 侧没有。本项目「第二份副本」
家族（§2.5.66 跨副本 `config.py`、§2.5.73 配色在 JS 里、§2.5.75 布局常量、
§2.5.89 `el()` 键集、§2.5.91 `web/` **代码**副本）的**第六次发作**——而且这次是
**数据**不是代码。

## 已经分了叉（逐表实测）

| 库 | 表 | 本 checkout | 主仓库 | 差 |
|---|---|---|---|---|
| `caiems_v2.db` | `image_files` | 1,042 | 1,108 | −66 |
| `caiems_staging.db` | `image_manifest` | 693 | 1,771 | −1,078 |

其余表行数**逐表全同**（`accidents` 2623 / `caics_causes` 16323 / …），
`date` 范围、`severity` 分布、`evidence_quality` 分布、来源数、`pipeline_version`
关键值**逐项全同**。方向也一致：本 checkout 是**旧的快照**（两处都更少）。

## 后果：同一个名字，两个数，两个判据都是绿的

```
check_2565 断言 image_files = 1,108   ← 它的 DB = main_db_path() ⇒ 主仓库
/api/health 报   image_files = 1,042   ← 它走 create_app()  ⇒ 本 checkout
```

**两个都对，因为问的是两个库。** 更醒目的一处在图片：

```
/api/images/stats  files 693  coverage 15.55%   ← 本 checkout 的 staging
§2.5.71 登记基线    1,771 行 / 有图事故 805 / 30.7%  ← 主仓库的 staging
```

## 混用脚本的姿态（本节最要紧的一张名单）

18 个脚本用 `create_app()`（⇒ web 解析规则），14 个**同时**用
`_common.main_db_path()`（⇒ 另一条规则）。也就是说它们在**一次运行里同时碰到
两个库**。

而「让两者一致」这件事**是每个脚本自己写一行才有的**：

```python
os.environ["CAIEMS_DB"] = str(main_db)      # 各脚本自己写
```

`CAIEMS_DB` 是 app 侧唯一能改落点的开关（`db.py:ENV_DB_PATH`）。**写了就一致，
不写就分叉**——没有任何隐式机制。实测：14 个里 4 个写了、10 个没写。

⚠️ **今天这 10 个都没有实际损害**，因为它们的判据全在**名字面／路径面**
（哪些列进过结果集、响应体的键树长什么样），不看行数值。这是「**结构缺陷成立、
实际未触发**」（§2.5.64 立的规矩）——本节的判据因此**只登记姿态、不判它们红**。

## 另一个开关：`CAIEMS_MAIN_DB` 只管一半

`_common.ENV_MAIN_DB = "CAIEMS_MAIN_DB"` 只改**脚本侧**的落点，对 app 侧
**毫无影响**。所以「设一个环境变量让两边一致」是做不到的——A3 段把这件事
写成判据。

另有一处更细的：`web/db.py` 的第三档读 `settings.DATABASE_URL`，而 `settings`
是**模块级实例**，`DATABASE_URL` 在**import 时**读一次。所以
`os.environ["DATABASE_URL"] = ...` 在运行时改**无效**，而 `CAIEMS_DB` 是
**每次调用读**、运行时改**有效**。两个"覆盖开关"生效时机不同，A3 段一并钉住。

## 顺带查出的：`web/db.resolve_db_path()` 的第三档是死代码

`resolve_db_path()` 的 docstring 写着三档优先级，但第二档**永远为真**
（`config.py:64` 的 `DATABASE_URL` 有默认值 `f"sqlite:///{DATA_DIR}/caiems_v2.db"`）
⇒ 第三档 `return (settings.DATA_DIR / DB_FILENAME).resolve()` **到不了**。
两档算出来又是同一个路径 ⇒ **无实际损害**（「结构缺陷成立、实际未触发」，
§2.5.64）。**这件事是注入自证逼出来的**：I1 的锚点首版就锚在第三档那行，
实测红的集合是**空**——锚在死分支上、注入等于没做，与"判据没报"长得一模一样
（§2.5.89）。处置两处：①锚点改到**函数入口**（与走哪一档无关）；②新增 **A5**
判据，把"第三档被遮蔽且两档同值"钉成**会过期的记录**。

## 本节改了什么

只改**描述**，不改数据、不改解析规则：

1. `scripts/_common.py` 的 docstring——它写着「28 MB，比主库还大，主库 26 MB」，
   而实测**今天反过来了**（本 checkout 27.2 MiB / 主仓库 27.4 MiB）。隐患陈述
   成立、尺寸数字已过期。**删掉字节数，留下患**（尺寸是响应式的活值，写死必漂）。
2. `scripts/check_2584_live_keys.py` 的 docstring——「有数据的只有
   `image_manifest` 1,771 行」这句的 1,771 来自**主仓库**，而该脚本驱动的是
   **本 checkout** 的 app（693 行）。补上这个限定。
3. 本节文档与各索引。

**不自己裁决** worktree 那两份库怎么处置（同步？删除？还是让
`web/db.resolve_db_path()` 也走 `main_repo_root()`）——属作者裁决。

## 判据边界

① 只核「**读的是哪一个文件**」，核不到「读得对不对」；
② 两副本合一（在主仓库里跑本脚本）时本节**无对象**，A 段会报红并说明——
   那是「该撤登记」的提示，**不是判据坏了**（§2.5.91 立的同款规矩）；
③ 行数方向判据（`n_local <= n_main`）只能抓「本 checkout 领先」，
   抓不到「两边各自被写过、只是本 checkout 恰好不领先」；
④ 差异表集合是**登记式**的——新表出现差异会报红，那是「该重新裁决」；
⑤ 差异的**量**只打印不判红（主库会长，量必变；§2.5.75：写死的固定值
   本身可能就是响应式的）；
⑥ 本节**不起服务**（用 test_client，约 3 秒）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root  # noqa: E402

WDB = ROOT / "construction_agent" / "web" / "db.py"
SDB = ROOT / "construction_agent" / "web" / "staging_db.py"
IMG = ROOT / "construction_agent" / "web" / "images.py"
COMMON = ROOT / "scripts" / "_common.py"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
DOC09 = ROOT / "docs" / "web" / "09_端到端对齐核查.md"
DOC10 = ROOT / "docs" / "web" / "10_待执行清单.md"
DOC11 = ROOT / "docs" / "web" / "11_执行状态快照_2026-10-05.md"
README = ROOT / "scripts" / "e2e" / "README.md"

V2_NAME = "caiems_v2.db"
ST_NAME = "caiems_staging.db"

#: 本 checkout 的两份库（**字面路径**，不由任何 resolver 派生——否则注入
#: resolver 时"期望值"会跟着变，判据恒绿）
LOCAL_V2 = ROOT / "data" / V2_NAME
LOCAL_ST = ROOT / "data" / ST_NAME

#: 登记：两套解析规则**应当**分叉。双向核——声明分叉则实测须分叉；
#: 哪天有人把 web 侧也改成 `main_repo_root()`，这里会红，提示「该重判本节」。
EXPECT_SPLIT = {"v2": True, "staging": True}

#: 登记：哪几张表有行数差。只登记**表名**不登记量（量是响应式的活值）。
KNOWN_DIFF = {"v2": {"image_files"}, "staging": {"image_manifest"}}

#: 登记：14 个混用脚本里**显式把 app 指向主库**的那几个。
#: 只断言 `KNOWN_EXPLICIT ⊆ 实测`（登记的须仍在，过期即红）；
#: 实测里**多出来的**只提示不判红（新脚本不必先登记才能写）。
KNOWN_EXPLICIT = {
    "_serve.py",
    "check_api_contract.py",
    "check_field_alignment.py",
    "check_2592_confirm_level.py",
}

#: 源码面注入用
#
# ⚠️ `V2_HEAD` 是**函数入口**而不是某个 `return` 行——2026-10-05 实测踩过一次：
# 首版锚在第三档 `return (settings.DATA_DIR / DB_FILENAME).resolve()`，而那一档
# **永远到不了**（见 part_a 的 A5），注入等于没做、红的集合是**空**，与"判据没报"
# 长得一模一样（§2.5.89）。锚在入口则**与走哪一档无关**，且锚点不唯一时 `_swap`
# 会当场报错而不是静默失效。
V2_HEAD = "    raw = os.environ.get(ENV_DB_PATH)\n    if raw:\n"
ST_TAIL = '    return (settings.DATA_DIR / STAGING_DB_FILENAME).resolve()'

def inject_ret(target: Path) -> str:
    """注入用的返回行。

    **写绝对字面量，不写 `main_repo_root()`**——`web/db.py` 没有 import 它，
    注入后会是 `NameError`、把 app 直接打崩，红的就不是"读错库"而是"崩了"，
    归因不唯一。
    """
    return f"    return Path({str(target)!r}).resolve()"


# ══════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════
def ok(cond: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)


def info(text: str) -> None:
    print(f"    {text}")


def con(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def tables(p: Path) -> list[str]:
    with con(p) as c:
        return [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def cols(p: Path) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    with con(p) as c:
        for t in tables(p):
            out[t] = tuple(r[1] for r in c.execute(f'PRAGMA table_info("{t}")'))
    return out


def counts(p: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    with con(p) as c:
        for t in tables(p):
            if t == "sqlite_sequence":
                continue
            out[t] = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    return out


def fe_files() -> list[Path]:
    return sorted((ROOT / "scripts").glob("*.py"))


# ── 判据本体抽成纯函数：主检与 --self-test 调同一份（§2.5.86：自测要真的
#    锻炼判据，不能另写一遍意思相近的表达式） ──────────────────────────
def ahead_tables(n_local: dict, n_main: dict) -> set:
    """本 checkout **领先**主仓库的表（不该有；有=有人在 worktree 里写过库）。"""
    return {t for t in set(n_local) | set(n_main)
            if (n_local.get(t) or 0) > (n_main.get(t) or 0)}


def diff_tables(n_local: dict, n_main: dict) -> set:
    """两份库行数不同的表。"""
    return {t for t in set(n_local) | set(n_main)
            if n_local.get(t) != n_main.get(t)}


def split_ok(app_p: Path, main_p: Path, want_split: bool) -> bool:
    """登记式的分叉断言：声明与实测须一致。"""
    return (Path(app_p) != Path(main_p)) == want_split


def diff_registry_ok(diff: set, known: set) -> tuple[bool, set, set]:
    """差异表集合 ↔ 登记，**双向**。返回 (是否通过, 新增, 已消失)。"""
    new, gone = diff - known, known - diff
    return (not new and not gone), new, gone


def tier_shadowed(url: str | None, tier2: Path | None, tier3: Path) -> list[str]:
    """A5 的判据本体（纯函数，便于自测）：第三档被第二档遮蔽、且两档同值。

    返回**不通过的理由列表**（空列表＝通过）。两条理由分开记，是为了让输出
    能指出**是哪一半**不成立——两半要防的是两件不同的事：`url` 为空 ⇒ 第三档
    复活（措辞与注入锚点都该重判）；两档分歧 ⇒ 同一份配置给出两个路径。
    """
    why: list[str] = []
    if not url:
        why.append("二档为空 ⇒ 三档复活，本条的措辞与 I1 的锚点都该重判")
    elif tier2 != tier3:
        why.append(f"两档分歧：二档 {tier2} ≠ 三档 {tier3}")
    return why


# ══════════════════════════════════════════════════════════════════
# A 解析面
# ══════════════════════════════════════════════════════════════════
def resolvers() -> dict[str, object]:
    from construction_agent.web import db as wdb, staging_db as sdb
    return {
        "main_root": main_repo_root(),
        "main_v2": Path(main_db_path()),
        "main_st": main_repo_root() / "data" / ST_NAME,
        "app_v2": Path(wdb.resolve_db_path()),
        "app_st": Path(sdb.resolve_staging_path()),
        "wdb": wdb, "sdb": sdb,
    }


def part_a(rv: dict) -> list[str]:
    print("\n── A 解析面：两套规则各落在哪个文件 ──")
    fails = []
    mv2, mv2m = rv["app_v2"], rv["main_v2"]
    mst, mstm = rv["app_st"], rv["main_st"]
    info(f"脚本侧 main_db_path()      {mv2m}")
    info(f"app  侧 resolve_db_path()  {mv2}")
    info(f"脚本侧 main repo staging   {mstm}")
    info(f"app  侧 resolve_staging()  {mst}")

    # A1 / A2 —— 登记式双向
    for tag, app_p, main_p in (("v2", mv2, mv2m), ("staging", mst, mstm)):
        want = EXPECT_SPLIT[tag]
        lab = "A1" if tag == "v2" else "A2"
        if not ok(split_ok(app_p, main_p, want),
                  f"{lab} [{tag}] 两套规则{'应当分叉' if want else '应当同源'}",
                  f"实测{'分叉' if app_p != main_p else '同源'}"):
            fails.append(lab)
            if want:
                info("   两副本合一 ⇒ 本节无对象。若这是有意修复，请翻转 "
                     "EXPECT_SPLIT 并重判本节；不是判据坏了。")

    # A3 —— 覆盖开关的效力与生效时机
    wdb = rv["wdb"]
    saved = {k: os.environ.get(k) for k in
             ("CAIEMS_DB", "CAIEMS_MAIN_DB", "DATABASE_URL")}
    try:
        # (a) CAIEMS_DB 能令两边一致 —— 证明"机制存在"
        os.environ["CAIEMS_DB"] = str(mv2m)
        same = Path(wdb.resolve_db_path()) == mv2m
        # (b) 但 main_db_path() 不受它影响
        script_same = Path(main_db_path()) == mv2m
        # (c) CAIEMS_MAIN_DB 只动脚本侧、app 侧纹丝不动
        del os.environ["CAIEMS_DB"]
        os.environ["CAIEMS_MAIN_DB"] = "/tmp/__probe_2594__.db"
        app_before = Path(wdb.resolve_db_path())
        script_moved = Path(main_db_path()) == Path("/tmp/__probe_2594__.db")
        del os.environ["CAIEMS_MAIN_DB"]
        # (d) DATABASE_URL 运行时改无效（settings 在 import 时已定型）
        os.environ["DATABASE_URL"] = f"sqlite:///{mv2m}"
        app_after_url = Path(wdb.resolve_db_path())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    if not ok(same and script_same, "A3a CAIEMS_DB 是唯一能让两边一致的开关",
              f"设后 app={same} 脚本={script_same}"):
        fails.append("A3a")
    if not ok(app_before == mv2 and script_moved,
              "A3b CAIEMS_MAIN_DB 只动脚本侧、app 侧不受影响",
              f"脚本侧动了={script_moved} app 侧仍={app_before.name if app_before == mv2 else app_before}"):
        fails.append("A3b")
    if not ok(app_after_url == mv2,
              "A3c DATABASE_URL 运行时改无效（settings 在 import 时定型）",
              f"改后 app 侧 = {app_after_url}"):
        fails.append("A3c")

    # A5 —— `web/db.resolve_db_path()` 的**第三档是死代码**（2026-10-05 注入自证时发现）
    #
    # 函数 docstring 写着三档优先级（`CAIEMS_DB` → `settings.DATABASE_URL` →
    # `settings.DATA_DIR / DB_FILENAME`），但 `config.py:64` 是
    #   `DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/caiems_v2.db")`
    # ——**总有值** ⇒ 第二档 `if url:` 恒真、第三档**永远到不了**。而第二档的默认值
    # 与第三档**算出来是同一个路径** ⇒ **无实际损害**（「结构缺陷成立、实际未触发」，
    # §2.5.64 立的规矩）。顺带：`DATABASE_URL` 的默认值由 `DATA_DIR` 拼出、`DATA_DIR`
    # 又是 `__file__` 相对 ⇒ 这两档把**同一条规则写了两遍**（§2.5.66 那个家族）。
    #
    # 为什么值得单列：I1 的注入锚点首版就选的是第三档那行，实测**红的集合是空**——
    # 锚点落在死分支上、注入等于没做，与"判据没报"长得一模一样（§2.5.89）。这条
    # 判据把"第三档到不了"钉成一条**会过期的记录**：将来若有人把默认值改空
    # （第三档复活）或改成别的路径（两档分歧），A5 转红＝**该重新裁决**，不是数据坏了。
    from construction_agent.config import settings as _st
    url = getattr(_st, "DATABASE_URL", None)
    tier3 = (_st.DATA_DIR / wdb.DB_FILENAME).resolve()
    tier2 = Path(url.split("///", 1)[-1]).expanduser().resolve() if url else None
    why = tier_shadowed(url, tier2, tier3)
    if not ok(not why,
              "A5 三档被二档遮蔽（死代码）、且两档同值（无实际损害）",
              "；".join(why) if why else f"二档 == 三档 == {tier3}"):
        fails.append("A5")

    # A4 —— 读取点覆盖（定量打印 + 登记子集）
    #
    # 两档口径**必须分开说**（§2.5.66：收窄换来准确、代价是覆盖变窄）：
    #   强形态 = `environ["CAIEMS_DB"] = …` 直接赋值 ⇒ 计入登记、双向核
    #   弱形态 = 文件里**提及** `CAIEMS_DB` ⇒ 只打印
    # 弱形态包含强形态，且会把"只写进 docstring 的表格""用字典间接切库"也算进来。
    # 后者是真的：`check_2585` 靠 `os.environ[keys[alias]] = …` 切库，强形态正则
    # 认不出（§2.5.87：判据不认识的形态必须有名有姓，不能当成"没有"）。
    #
    # **排除自己**：本脚本 A3 段会临时设 `CAIEMS_DB` 做探针，那是自指；
    # 不排除的话名单里会凭空多出"把 app 指向主库的本脚本"这一条。
    self_name = Path(__file__).name
    users_script, users_app, strong, weak = set(), set(), set(), set()
    pat_strong = re.compile(r'environ\[["\']CAIEMS_DB["\']\]\s*\+?=|'
                            r'environ\.setdefault\(\s*["\']CAIEMS_DB["\']')
    for f in fe_files():
        if f.name == self_name:
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bmain_db_path\b|\bmain_repo_root\b", src):
            users_script.add(f.name)
        if re.search(r"\bcreate_app\b", src):
            users_app.add(f.name)
        if "CAIEMS_DB" in src:
            weak.add(f.name)
        if pat_strong.search(src):
            strong.add(f.name)
    both = users_script & users_app
    info(f"用 _common 主库解析的脚本 {len(users_script)} 个 ｜ "
         f"用 create_app() 的 {len(users_app)} 个 ｜ 两者都用 {len(both)} 个")
    info(f"  强形态（直接给 CAIEMS_DB 赋值）{len(strong & both)} 个：{sorted(strong & both)}")
    info(f"  弱形态（文件里提及 CAIEMS_DB）{len(weak & both)} 个：{sorted(weak & both)}")
    info(f"  两者都不的 {len(both - weak)} 个（app 侧读本 checkout）：{sorted(both - weak)}")
    if not ok(bool(both), "A4 元判据：存在同时用两套解析的脚本",
              f"交集 {len(both)} 个" + ("" if both else " ⇒ 本节无对象")):
        fails.append("A4")
    stale = KNOWN_EXPLICIT - both
    if not ok(not stale, "A4b 登记的「显式指库」脚本仍在交集中（过期即红）",
              f"过期 {sorted(stale)}" if stale else f"登记 {len(KNOWN_EXPLICIT)} 个全部仍在"):
        fails.append("A4b")
    return fails


# ══════════════════════════════════════════════════════════════════
# B 副本面
# ══════════════════════════════════════════════════════════════════
def compare(tag: str, loc: Path, main: Path) -> list[str]:
    """两份库的 schema / 行数 / 方向。返回三类结果。"""
    print(f"\n  ── {tag}：{loc.name} ──")
    lc, mc = cols(loc), cols(main)
    n_local, n_main = counts(loc), counts(main)
    print(f"    {loc}  {loc.stat().st_size:,} B  mtime={_mt(loc)}")
    print(f"    {main}  {main.stat().st_size:,} B  mtime={_mt(main)}")

    diff = diff_tables(n_local, n_main)
    ahead = ahead_tables(n_local, n_main)
    for t in sorted(diff):
        info(f"  差 {t:22s} 本 checkout {n_local.get(t)!s:>7s} / 主仓库 {n_main.get(t)!s:>7s}")
    if not diff:
        info("  （逐表行数全同）")

    return [lc == mc, ahead, diff]


def _mt(p: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def part_b(rv: dict) -> list[str]:
    print("\n── B 副本面：两份库的 schema / 行数 / 方向 ──")
    fails = []
    for tag, loc, main in (("v2", LOCAL_V2, rv["main_v2"]),
                           ("staging", LOCAL_ST, rv["main_st"])):
        same_schema, ahead, diff = compare(tag, loc, main)
        lab = "B1" if tag == "v2" else "B1s"
        if not ok(same_schema, f"{lab} [{tag}] 表集合与逐表列集合相同（schema 未分叉）"):
            fails.append(lab)
        lab2 = "B2" if tag == "v2" else "B2s"
        if not ok(not ahead, f"{lab2} [{tag}] 行数方向：本 checkout 不领先主仓库",
                  f"领先的表 {sorted(ahead)}" if ahead else "无领先"):
            fails.append(lab2)
        lab3 = "B3" if tag == "v2" else "B3s"
        good, new, gone = diff_registry_ok(diff, KNOWN_DIFF[tag])
        if not ok(good, f"{lab3} [{tag}] 差异表集合与登记一致",
                  f"新增 {sorted(new)} ／ 已消失 {sorted(gone)}"
                  if (new or gone) else f"登记 {sorted(KNOWN_DIFF[tag])} 全部命中"):
            fails.append(lab3)
    return fails


# ══════════════════════════════════════════════════════════════════
# C 后果面（运行时真值）
# ══════════════════════════════════════════════════════════════════
def run_app() -> dict:
    from construction_agent.web.factory import create_app
    app = create_app()
    c = app.test_client()

    def get(p: str):
        r = c.get(p)
        try:
            return r.status_code, r.get_json()
        except Exception:
            return r.status_code, None

    st, health = get("/api/health")
    st2, imgs = get("/api/images/stats")
    return {"health": health, "images": imgs,
            "health_status": st, "images_status": st2}


def part_c(rv: dict, app_data: dict) -> list[str]:
    print("\n── C 后果面：app 报的数 == 它实际解析到的那份库 ──")
    fails = []

    # C3 先做闭式：C 段看到的库路径 == A 段解析出的路径（防段间环境泄漏）
    from construction_agent.web import db as wdb, staging_db as sdb
    now_v2, now_st = Path(wdb.resolve_db_path()), Path(sdb.resolve_staging_path())
    if not ok(now_v2 == rv["app_v2"] and now_st == rv["app_st"],
              "C3 闭式：C 段解析到的库 == A 段解析到的库（无段间环境泄漏）",
              f"v2 {now_v2.name} ／ st {now_st.name}"):
        fails.append("C3")

    n_local = counts(LOCAL_V2)
    n_main = counts(rv["main_v2"])

    print("\n  ── /api/health 报的表行数 vs 两份库 ──")
    h = app_data["health"] or {}
    ht = ((h.get("data") or {}).get("tables") or h.get("tables") or {})
    if not ht:
        info(f"  未取到 tables（HTTP {app_data['health_status']}）")
    keys_local = set(n_local)
    same_keys = set(ht) == keys_local
    bad = {k: (ht.get(k), n_local.get(k)) for k in ht
           if k in n_local and ht.get(k) != n_local[k]}
    diff_main = {k: (n_local.get(k), n_main.get(k)) for k in keys_local
                 if n_local.get(k) != n_main.get(k)}
    info(f"  两份库行数不同的表：{ {k: v for k, v in sorted(diff_main.items())} }")
    if not ok(bool(ht) and same_keys and not bad,
              "C1 [v2] /api/health 的逐表行数 == 本 checkout 库（键集合同、值全同）",
              f"键集合相同={same_keys} 值不符={bad or '无'}"):
        fails.append("C1")
    if diff_main:
        info("  ⚠ 上表若从主仓库启动会是：" + " ／ ".join(
            f"{k}={v[1]}" for k, v in sorted(diff_main.items())))

    print("\n  ── /api/images/stats vs 两份 staging 的 image_manifest ──")
    n_local_st = counts(LOCAL_ST)
    n_main_st = counts(rv["main_st"])
    imgs = app_data["images"] or {}
    d = imgs.get("data") or imgs
    files = d.get("files")
    analyzed = d.get("analyzed")
    with con(LOCAL_ST) as c:
        loc_files = c.execute("SELECT COUNT(*) FROM image_manifest").fetchone()[0]
        loc_an = c.execute("SELECT COALESCE(SUM(COALESCE(analyzed,0)),0) "
                           "FROM image_manifest").fetchone()[0]
    info(f"  本 checkout image_manifest {loc_files} 行 ／ 主仓库 "
         f"{n_main_st.get('image_manifest')} 行")
    info(f"  端点报 files={files} analyzed={analyzed} coverage={d.get('coverage')} "
         f"state={d.get('state')}")
    if not ok(files == loc_files and analyzed == loc_an,
              "C2 [staging] /api/images/stats 的 files/analyzed == 本 checkout 工作库",
              f"期望 {loc_files}/{loc_an}，实得 {files}/{analyzed}"):
        fails.append("C2")
    return fails


# ══════════════════════════════════════════════════════════════════
# D 文档对账
# ══════════════════════════════════════════════════════════════════
def part_d() -> list[str]:
    print("\n── D 文档对账 ──")
    fails = []
    doc = DOC.read_text(encoding="utf-8")
    if not ok(bool(re.search(r"^### 2\.5\.94 ", doc, re.M)),
              "D1 01 号文档有 §2.5.94 小节标题"):
        fails.append("D1")

    common = COMMON.read_text(encoding="utf-8")
    if not ok("分叉" in common and re.search(r"worktree 的 `data/` 里\*\*确实有\*\*", common),
              "D2 _common.py 仍留存患陈述（副本会与主库分叉）"):
        fails.append("D2")
    if not ok(not re.search(r"\b28 MB\b|\b26 MB\b", common),
              "D3 _common.py 不再写死字节尺寸（尺寸是响应式的活值）"):
        fails.append("D3")

    # 出处说明是**紧跟在那一行之后的一段**，不写在同一行里——判据取窗口而不是
    # 单行（否则只能把说明硬塞进原句，读起来像病句）。窗口收窄到 8 行，且要求
    # 同时出现「主仓库」与「本 checkout」两个词：只有那段专门写的说明才两者兼有，
    # 别的段落救不活它（§2.5.63：判据的宽度决定结论的强度）。
    c84 = (ROOT / "scripts" / "check_2584_live_keys.py").read_text(encoding="utf-8")
    lines84 = c84.splitlines()
    prem = [i for i, ln in enumerate(lines84) if "有数据的只有" in ln]
    win = "\n".join(lines84[prem[0]:prem[0] + 8]) if len(prem) == 1 else ""
    if not ok(len(prem) == 1 and "主仓库" in win and "本 checkout" in win,
              "D4 check_2584 的 1,771 标明出处（主仓库，非本 checkout）",
              f"命中 {len(prem)} 行" + (f"；窗口内 主仓库={'主仓库' in win} "
                                       f"本checkout={'本 checkout' in win}" if prem else "")):
        fails.append("D4")

    d09 = DOC09.read_text(encoding="utf-8")
    if not ok("2.5.94" in d09, "D5 09 号 §十五 维度表含本期维度"):
        fails.append("D5")
    d10 = DOC10.read_text(encoding="utf-8")
    if not ok("2.5.94" in d10, "D6 10 号清单含本期小节"):
        fails.append("D6")
    d11 = DOC11.read_text(encoding="utf-8")
    if not ok("2.5.94" in d11, "D7 11 号快照含本期"):
        fails.append("D7")
    rd = README.read_text(encoding="utf-8")
    if not ok("check_2594_data_replica.py" in rd,
              "D8 e2e/README 只读脚本表含本脚本"):
        fails.append("D8")
    return fails


# ══════════════════════════════════════════════════════════════════
# 主检
# ══════════════════════════════════════════════════════════════════
def collect() -> list[str]:
    rv = resolvers()
    fails = []
    fails += part_a(rv)
    fails += part_b(rv)
    fails += part_c(rv, run_app())
    fails += part_d()
    return fails


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--inject-test" in sys.argv:
        return inject_test()
    if "--marks" in sys.argv:
        fails = collect()
        print("MARKS " + json.dumps(sorted(fails)))
        return 1 if fails else 0

    print("§2.5.94 数据副本的适用范围：web 与核查脚本读的是哪一个库")
    fails = collect()
    print("\n" + "═" * 62)
    if fails:
        print(f"✗ 待处置 {len(fails)} 条：{fails}")
        return 1
    print("✅ 全部通过")
    return 0


# ══════════════════════════════════════════════════════════════════
# 注入式自证
# ══════════════════════════════════════════════════════════════════
def _marks() -> set[str]:
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--marks"],
                       capture_output=True, text=True, cwd=str(ROOT))
    lines = [x for x in r.stdout.splitlines() if x.startswith("MARKS ")]
    if not lines:
        raise RuntimeError("子进程没给出 MARKS 行：\n"
                           + r.stdout[-1500:] + "\n" + r.stderr[-1500:])
    return set(json.loads(lines[-1][len("MARKS "):]))


def _swap(path: Path, old: str, new: str):
    src = path.read_text(encoding="utf-8")
    if src.count(old) != 1:
        raise RuntimeError(f"锚点在 {path.name} 里出现 {src.count(old)} 次，须恰好 1 次")
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def inject_test() -> int:
    base = _marks()
    print(f"基线红集合：{sorted(base) or '（空）'}")
    cases = [
        ("I1 在 web/db.resolve_db_path 入口短路 → 指主仓库",
         WDB, V2_HEAD, inject_ret(Path(main_db_path())) + "\n" + V2_HEAD, {"A1", "C1"}),
        ("I2 改 staging_db.resolve_staging_path 的末行 → 指主仓库",
         SDB, ST_TAIL, inject_ret(main_repo_root() / "data" / ST_NAME), {"A2", "C2"}),
        ("I3 /api/images/stats 的 COUNT(*) → COUNT(*)+1",
         IMG, "SELECT COUNT(*) AS files", "SELECT COUNT(*) + 1 AS files", {"C2"}),
        ("I4 删掉 01 号文档的 §2.5.94 标题（文档面）",
         DOC, "### 2.5.94 ", "### 2.5.94x ", {"D1"}),
    ]
    bad = 0
    for label, path, old, new, want in cases:
        orig = path.read_text(encoding="utf-8")
        try:
            _swap(path, old, new)
            got = _marks()
        finally:
            path.write_text(orig, encoding="utf-8")
        good = (got == want)
        print(f"  {'✓' if good else '✗'} {label}\n      期望 {sorted(want)} ／ 实得 {sorted(got)}")
        bad += 0 if good else 1
    print(f"\n{'✅' if not bad else '✗'} 注入自证 {len(cases) - bad}/{len(cases)}")
    return 1 if bad else 0


# ══════════════════════════════════════════════════════════════════
# 判据自测（判据本身的正反用例）
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    """判据本体的**正反用例**——调的是主检同一份纯函数，不是另写一遍意思
    相近的表达式（§2.5.86：自测要真的锻炼判据）。"""
    cases: list[tuple[str, bool, bool]] = []

    def chk(name: str, cond: bool, want: bool) -> None:
        cases.append((name, cond, want))

    eq = {"a": 5, "b": 9}

    # ── ahead_tables：本 checkout 不得领先 ──
    chk("行数全等 → 无领先", ahead_tables(eq, eq) == set(), True)
    chk("本 checkout 少 66 → 无领先", ahead_tables({"a": 1042}, {"a": 1108}) == set(), True)
    chk("本 checkout 多 → 领先（须报红）",
        ahead_tables({"a": 1108}, {"a": 1042}) == {"a"}, True)
    chk("领先的是**子集**而非全部", ahead_tables({"a": 9, "b": 1}, {"a": 5, "b": 9}) == {"a"}, True)
    chk("缺表的表视作 0", ahead_tables({"a": 1}, {}) == {"a"}, True)

    # ── diff_tables：差异面 ──
    chk("值不同 → 进差异集合", diff_tables({"a": 1}, {"a": 2}) == {"a"}, True)
    chk("值相同 → 不入", diff_tables(eq, eq) == set(), True)
    chk("只有一边有的表 → 算差异", diff_tables({"a": 1}, {}) == {"a"}, True)

    # ── diff_registry_ok：双向登记 ──
    chk("差异 == 登记 → 过", diff_registry_ok({"a"}, {"a"})[0], True)
    chk("出现未登记的新差异 → 红", not diff_registry_ok({"a", "b"}, {"a"})[0], True)
    chk("登记的表差异消失 → 红", not diff_registry_ok(set(), {"a"})[0], True)
    chk("归因：新增与已消失分别报出",
        diff_registry_ok({"b"}, {"a"})[1:] == ({"b"}, {"a"}), True)

    # ── split_ok：登记式分叉，**双向** ──
    chk("声明分叉 + 实测分叉 → 过", split_ok(Path("/x"), Path("/y"), True), True)
    chk("声明分叉 + 实测同源 → 红（两副本合一，本节无对象）",
        not split_ok(Path("/x"), Path("/x"), True), True)
    chk("声明同源 + 实测同源 → 过", split_ok(Path("/x"), Path("/x"), False), True)
    chk("声明同源 + 实测分叉 → 红", not split_ok(Path("/x"), Path("/y"), False), True)

    # ── tier_shadowed：第三档被遮蔽且两档同值（返回理由列表，空＝过） ──
    x = Path("/x/caiems_v2.db")
    chk("二档有值 + 两档同路径 → 过", tier_shadowed("sqlite:////x/caiems_v2.db", x, x) == [], True)
    chk("二档为空 → 红（三档复活）", bool(tier_shadowed(None, None, x)), True)
    chk("空串二档 → 红（`if url:` 为假）", bool(tier_shadowed("", None, x)), True)
    chk("两档分歧 → 红", bool(tier_shadowed("sqlite:////y/db", Path("/y/db"), x)), True)
    chk("裸路径写法也能解析（无 `///`）",
        tier_shadowed("/x/caiems_v2.db", x, x) == [], True)
    chk("理由**只报成立的那一半**（同值时不得报分歧）",
        tier_shadowed("sqlite:////x/caiems_v2.db", x, x) == [], True)

    bad = 0
    for name, got, want in cases:
        if got != want:
            print(f"  ✗ {name}（得 {got}，期望 {want}）")
            bad += 1
    print(f"{'✅' if not bad else '✗'} 判据自测 {len(cases) - bad}/{len(cases)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
