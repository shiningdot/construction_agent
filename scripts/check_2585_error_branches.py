#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.85 GET 错误分支的真值面：把 §2.5.84 的 B4 从「静态推断」坐实成「实测」。

## 来路

§2.5.84 给静态面配了独立真值（真打 28 条只读 GET 路由），但它**只打了快乐路径**
——合法样本参数 → 200。于是它留下 5 条无法结案的读，进 B4「分支差异」档：

| 调用点 | 读的键 | §2.5.84 的处置 |
|---|---|---|
| `extract.js:631` `/api/extract/results` | 顶层 `hint` | 静态面说"别的分支里有" ⇒ 不判红 |
| `review.js:237` `/api/review/queue` | 顶层 `hint` | 同上 |
| `extract.js:219` `/api/extract/pending` | 顶层 `available` | 同上 |
| `extract.js:219` `/api/extract/pending` | 顶层 `hint` | 同上 |
| `review.js:139` `/api/review/stats` | `data.hint` | 同上 |

**"别的分支里应该有"本身是个推断，没被任何一次真实响应验证过**——这与 §2.5.67
那条"例外白名单只写理由、脚本从不复核"是同一个形状：**理由写得对不代表理由成立，
更不代表它明天还成立**。本节把每一个分支真打一遍，让"有"变成"在哪一条上实测有"。

## 怎么造出错误分支

关键前提（本节实测确认）：`db.resolve_db_path()` 与 `staging_db.resolve_staging_path()`
都是**每次调用读 `os.environ`**，没有模块级缓存。所以同一个进程里改环境变量再打
请求就能切换库形态——**不必像 §2.5.84 的注入自证那样另起进程**。`try/finally`
恢复现场。

九种场景：

| 场景 | 造法 | 想看的 |
|---|---|---|
| `happy` | 真库 + 真 id | 基线（= §2.5.84 的真值面） |
| `final_gone` | `CAIEMS_DB` → 不存在的路径 | 最终库 503 |
| `final_zero` | `CAIEMS_DB` → 0 字节文件 | 空壳库走哪一档 |
| `final_blank` | `CAIEMS_DB` → 合法 SQLite、0 张表 | 同上 |
| `staging_gone` | `CAIEMS_STAGING_DB` → 不存在的路径 | 工作库 `available:false` |
| `staging_blank` | `CAIEMS_STAGING_DB` → 0 表库 | 同上（另一档 hint） |
| `staging_notdb` | `CAIEMS_STAGING_DB` → 非 SQLite 文件 | `not_a_database` 一档 |
| `no_id` | 真库 + 假 id | 404 |
| `bad_param` | 真库 + `?page=abc&limit=-1&…` | 400 或静默降级 |

库形态文件一律建在**临时目录**，绝不在主仓库 `data/` 下落地（那里已有一次误建
探针文件的待办）。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 快乐路径下读不到的键，必须在**某个错误分支**的真响应里出现 | 前端读了任何真响应里都没有的键 ⇒ 死读（静默 `undefined`） | 红 |
| A2 | 读某个库的 GET 路由，在该库不可用场景下必须给出可识别的不可用标记 | 库坏了却照常 200 带正常数据（静默降级） | 红 |
| A3 | 闭式：场景 × 路由的组合数 = 解析出 JSON 的 + 没解析出的 | 静默跳过的组合（§2.5.67） | 红 |
| B1 | 状态码矩阵（每条路由 × 每个场景） | ——（基线） | 定量 |
| B2 | 错误分支**独有**的键路径（相对快乐路径） | "只能靠分支兑现"的键清单 | 定量 |
| B3 | 错误场景下仍 200 且无任何不可用标记的 (场景, 路由) | 静态判据看不见的那些 | 定量 |
| B4 | 每个场景能打出 JSON 的路由数 | ——（覆盖面的量化） | 定量 |

**A1 是本节对 §2.5.84 的正面回答**：B4 那 5 条到底成不成立。**A2 是本节自己新
立的**：库不可用是个"该报错的状态"，而"该报错却没报"只有真打才发现得了——
静态面里 `except` 分支写没写、写对没写对，读代码是看不出来的。

## A1 的第一版会假绿

首版把 A1 写成"这些读的键名在错误分支响应里出现过没有"，**只要键名出现就算过**。
实测这是假绿：`hint` 在 `/api/collect/sources` 的**快乐路径**里就有（值为 `null`），
于是 `extract.js:631` 读 `/api/extract/results` 的 `hint` 会被别条路由的快乐路径
救活。判据必须**绑定到调用点自己的那条路由**上（§2.5.66「判据必须收窄接收者」
的又一形态）。改后按 `(调用点, 路由)` 配对核，不跨路由借据。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _common import main_db_path, main_repo_root             # noqa: E402
from _serve import point_at                                  # noqa: E402
import check_2583_get_response as G                          # noqa: E402
import check_2584_live_keys as K                             # noqa: E402

WEB = ROOT / "construction_agent" / "web"
STATIC = WEB / "static"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
MAIN_DB = main_db_path()
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

#: 锚点句用**本节独有**的整行标题（§2.5.83 的教训：与别节同族的措辞会让
#: "锚点被删"用例恒真）。
DOC_NEEDLES = ["### 2.5.85 展示层（续）：GET 错误分支的真值面——**把 B4 的分支差异实测坐实**"]

#: 读工作库的路由前缀。**这是给 B 段做交叉验证用的猜想**，不是判据本身——
#: 脚本会用 `staging_gone` 场景**实测**哪些路由真读工作库（打出 `available:false`），
#: 再拿实测集合与本猜想比对，不一致就报出来。猜想错了是发现，不是错误。
STAGING_PREFIXES = ("/api/collect/", "/api/extract/", "/api/review/")

#: 共享封装读的可选键（§2.5.83 的 `wrap_keys`）。这些是**进了豁免名单**的读：
#: 「实现只在出错时才返回它们」——本节要**验证这句豁免陈述**。
WRAP = {"message", "ok"}


# ══════════════════════════════════════════════════════════════════
# 库形态与场景
# ══════════════════════════════════════════════════════════════════
class Shapes:
    """三种"坏库"形态，落在临时目录里。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.gone = root / "gone.db"                 # 父目录都不建 ⇒ 文件不存在
        self.zero = root / "zero.db"                 # 0 字节
        self.blank = root / "blank.db"               # 合法 SQLite、0 张表
        self.notdb = root / "notdb.db"               # 不是 SQLite 库
        self.zero.write_bytes(b"")
        for p in (self.blank,):
            if p.exists():
                p.unlink()
            sqlite3.connect(p).close()
        self.notdb.write_text("这不是一个 SQLite 数据库文件。\n" * 8,
                              encoding="utf-8")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


#: 场景表：`(场景名, 中文, env 覆盖, 参数风格)`。
#: 参数风格 `real` = 按变量名造真参数（复用 §2.5.84）；`fake` = 全填假 id；
#: `bad` = 真参数 + 一串非法 query。
SCENES = [
    ("happy",        "快乐路径",          {},                          "real"),
    ("final_gone",   "最终库·文件不存在",   {"__final__": "gone"},        "real"),
    ("final_zero",   "最终库·0 字节",      {"__final__": "zero"},        "real"),
    ("final_blank",  "最终库·空库",        {"__final__": "blank"},       "real"),
    ("final_notdb",  "最终库·非 SQLite",   {"__final__": "notdb"},       "real"),
    ("staging_gone", "工作库·文件不存在",   {"__staging__": "gone"},      "real"),
    ("staging_blank", "工作库·空库",       {"__staging__": "blank"},     "real"),
    ("staging_notdb", "工作库·非 SQLite",  {"__staging__": "notdb"},     "real"),
    ("no_id",        "id 不存在",         {},                          "fake"),
    ("bad_param",    "参数非法",          {},                          "bad"),
]

#: 非法 query 串（`bad_param` 场景）。挑的都是 §2.5.82 登记过、后端**真的读**的参数，
#: 且都被 `int()` 包过——正确实现要么 400、要么静默降级成默认值，两者都算合法，
#: 但不能崩 500。
BAD_Q = ("?page=abc&size=-1&limit=0&n=xyz&year_from=zzz&year_to=1e9"
         "&min_count=nan&top=∞&threshold=")


@contextmanager
def _env(over: dict, shapes: Shapes):
    """临时改 `CAIEMS_DB` / `CAIEMS_STAGING_DB`，退出时**逐字**恢复。"""
    keys = {"__final__": "CAIEMS_DB", "__staging__": "CAIEMS_STAGING_DB"}
    saved = {k: os.environ.get(k) for k in set(keys.values())}
    try:
        for alias, shape in over.items():
            os.environ[keys[alias]] = str(getattr(shapes, shape))
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _url(rule: str, style: str, ids: dict) -> str | None:
    """按参数风格把 `<var>` 换成实参；`None` = 本场景造不出参数。"""
    url = rule
    for var in re.findall(r"<([^>]*)>", rule):
        v = "__no_such_2585__" if style == "fake" else ids.get(var)
        if v is None:
            return None
        url = url.replace(f"<{var}>", str(v))
    if style == "bad":
        url += BAD_Q
    return url


def _avail_of(j) -> object:
    """响应里的「可用性」值：顶层 `available` 优先，其次 `data.available`。

    **两种形状都是合法的**，不是谁写错了：`unavailable_payload` 把 `available`
    放在顶层，而 `review.funnel_payload()` 把它放在 `data` 里（`/api/review/stats`
    的 `data.hint` 是同一个来路）。判据要认这两种，但要**分别记账**——把
    `data.available` 误当顶层来判，会让「顶层没有」被读成「没有标记」。
    """
    if not isinstance(j, dict):
        return None
    if "available" in j:
        return j["available"]
    d = j.get("data")
    if isinstance(d, dict) and "available" in d:
        return d["available"]
    return None


def _err_of(j) -> str:
    """响应里的 `error` 码（顶层优先，其次 `data.error`）。"""
    if not isinstance(j, dict):
        return ""
    if isinstance(j.get("error"), str):
        return j["error"]
    d = j.get("data")
    if isinstance(d, dict) and isinstance(d.get("error"), str):
        return d["error"]
    return ""


def sweep(app, shapes: Shapes) -> tuple[dict, list]:
    """→ `({(scene, rule): 记录}, 组合清单)`。记录里 `paths=None` 表示非 JSON。"""
    c = app.test_client()
    ids = K._sample_ids()
    rules = sorted({str(r) for r in app.url_map.iter_rules() if "GET" in r.methods})
    rules = [r for r in rules if r.startswith("/api/") and "<path:" not in r]

    out: dict = {}
    combos: list = []
    for scene, _label, over, style in SCENES:
        with _env(over, shapes):
            for rule in rules:
                combos.append((scene, rule))
                url = _url(rule, style, ids)
                if url is None:
                    out[(scene, rule)] = {"status": None, "paths": None,
                                          "empty": set(), "top": [], "url": "",
                                          "avail": None, "err": "", "raw": ""}
                    continue
                r = c.get(url)
                j = r.get_json(silent=True)
                rec = {"status": r.status_code, "url": url, "top": [],
                       "paths": None, "empty": set(), "raw": "",
                       "avail": None, "err": ""}
                if j is None:
                    rec["raw"] = r.get_data(as_text=True)[:120]
                else:
                    got: set = set()
                    emp: set = set()
                    K.walk(j, "", got, emp)
                    rec["paths"] = got
                    rec["empty"] = emp
                    rec["top"] = sorted(j.keys()) if isinstance(j, dict) else []
                    rec["avail"] = _avail_of(j)
                    rec["err"] = _err_of(j)
                out[(scene, rule)] = rec
    return out, combos


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════
def misreads(rep) -> list[dict]:
    """快乐路径下**读不到**的前端读：§2.5.84 的 B4 ∪ `wrap_keys` 豁免的那批。

    判据要收窄到 `(调用点, 路由)` 配对——见模块 docstring 末尾记的那个假绿。
    """
    out: list[dict] = []
    nolive: list[dict] = []
    seen: set = set()
    for h in rep["fe"]:
        # 快乐路径**打不出**真响应的路由：那些读既不进本节、也不进 §2.5.84 的
        # A1（它已经归 B5），**但必须单独记账**——否则它们会被本节的 A1 判成
        # "十个场景都没有"，而真相是"这条路由一次都没打到"（§2.5.67：判红与
        # "我没比"必须分开说）。首版正是栽在这里：77 条里有 70 条是这个来路。
        if not h["rule"] or h["rule"] not in rep["live"] \
                or rep["live"].get(h["rule"]) is None:
            if h["rule"]:
                nolive.append({"file": h["file"], "line": h["line"],
                               "rule": h["rule"], "n": len(h["paths"])})
            continue
        got = rep["live"].get(h["rule"]) or set()
        # `trees` 的键是 **endpoint**（不是 rule）——首版拿 rule 去取，取到 `{}`，
        # 于是 `static_says_maybe(路径, {})` 对任何路径都返回 False（"静态确证
        # 这个键不在这里"），九条待坐实的读被静默滤成两条。§2.5.65「解析失败
        # 不得静默降级成空集合」的又一形态：**空树与"树里没有"在判据眼里是
        # 两件事**，取错了 key 会让判据变成"我没比"。
        tree = rep["trees"].get(rep["eps"].get(h["rule"])) or {}
        for p in sorted(h["paths"]):
            tail = p.rsplit(".", 1)[-1]
            wrapped = tail in WRAP
            if p in got or p + "[]" in got:
                continue
            if (not wrapped) and not K.static_says_maybe(p, tree):
                # 两面都说没有 ⇒ §2.5.84 判红的真缺陷，不进本节（A1=0 是它的前提）
                continue
            key = (h["file"], h["line"], h["rule"], p)
            if key in seen:
                continue
            seen.add(key)
            out.append({"file": h["file"], "line": h["line"], "rule": h["rule"],
                        "path": p, "wrapped": wrapped})
    return out, nolive


def test_a1(mis, face) -> tuple[list, list]:
    """A1：每条读必须在**它自己那条路由**的某个场景里真出现。"""
    reds, conf = [], []
    for m in mis:
        where = []
        for (scene, rule), rec in face.items():
            if rule != m["rule"] or rec["paths"] is None:
                continue
            if m["path"] in rec["paths"] or m["path"] + "[]" in rec["paths"]:
                where.append(scene)
        if where:
            conf.append({**m, "scenes": sorted(where)})
        else:
            reds.append(f"{m['file']}:{m['line']} {m['rule']} 读 `{m['path']}`——"
                        f"快乐路径没有它，而{len(SCENES)} 个场景打下来**也没有**")
    return reds, conf


def _unavail_marker(rec) -> bool:
    """这份响应有没有"我不可用"的标记。"""
    if rec["status"] == 503:
        return True
    if rec["paths"] is None:
        return False
    for p in ("available", "ok"):
        if p in rec["paths"]:
            return True
    return "error" in rec["paths"]


def test_a2(face) -> tuple[list, dict]:
    """A2：**主数据源**不可用时，路由必须给出可识别的不可用标记。

    首版把"读某库"判成"响应内容在库坏掉时变了样"，于是 `/api/accidents/<id>`
    被判为"读工作库"并报红——**实测它确实读**（详情页挂了工作库侧的图片清单），
    但那是**可选降级**不是缺陷。**"读到了什么"与"以它为主"是两件事**：判据的
    接收者要收窄到"工作库是它主数据源"的那批（`STAGING_PREFIXES`），
    跨库读取另列出来供裁决（A2b），不混进红集合。

    A2a：前缀内的路由在 `staging_gone` 下必须有 `available is False` 或 503。
    A2b：前缀**外**却实测读了工作库的路由 ⇒ 列出来（跨库读取，可能是正确的
         可选降级，也可能是设计外耦合）。
    A2c：`final_*` 场景下 503 的路由必须都是读最终库的（反过来：读最终库的
         路由在 `final_gone` 下必须 503）。
    """
    rules = sorted({r for _s, r in face})
    happy = {r: face[("happy", r)] for r in rules}

    def changed(r, scene) -> bool:
        a, b = happy[r], face[(scene, r)]
        if b["paths"] is None or a["paths"] is None:
            return False
        if b["status"] != a["status"] or b["top"] != a["top"]:
            return True
        return b["paths"] != a["paths"]

    staging_scenes = ("staging_gone", "staging_blank", "staging_notdb")
    final_scenes = ("final_gone", "final_zero", "final_blank", "final_notdb")
    all_bad = final_scenes + staging_scenes

    reads_staging = {r for r in rules if any(changed(r, s) for s in staging_scenes)}
    degraded = {r for r in rules
                if any(face[(s, r)]["avail"] is False for s in staging_scenes)}
    reads_final = {r for r in rules if any(changed(r, s) for s in final_scenes)}

    def marked(rec: dict) -> bool:
        """这条响应有没有"我读不到库"的可识别表达。

        三条通道**都要认**——只认 `available` 会把走 503 出口那批全判成假红：
          · `available is False`  —— 装订成 payload 的那条（`unavailable_payload`）
          · `status == 503`       —— errorhandler 那条（`_on_db_unavailable`）
          · `error` 非空          —— 两条通道共有的那格
        判据的宽度直接决定结论的强度：第一版只认 `available`，报了 14 条假红。
        """
        return rec["avail"] is False or rec["status"] == 503 or bool(rec["err"])

    def lib_of(scene: str) -> str:
        return "final" if scene.startswith("final_") else "staging"

    # 「这条路由以哪个库为**主数据源**」也用实测定，不用前缀猜：
    #   以某库为主 ⟺ 该库不可用时它**给出了标记**（它自己声明了这个依赖）。
    # 反过来说，某库坏了它内容变、却不给标记，而它在**另一个**库上是有标记的——
    # 那这个库对它就只是**可选数据源**（有则显示、无则略过），是设计不是缺陷。
    owns: dict = {}
    for r in rules:
        for s in all_bad:
            if marked(face[(s, r)]):
                owns.setdefault(r, set()).add(lib_of(s))

    reds: list[str] = []
    silent: list[str] = []
    contradict: list[str] = []
    optional: list[str] = []

    for r in rules:
        for s in all_bad:
            rec = face[(s, r)]
            # ① `paths is None` = 这条路由本场景没样本可打（工作库无该 id）。
            #    **"没打到"与"没有标记"必须分开说**——否则报出一串假红。
            if rec["paths"] is None or not changed(r, s):
                continue
            if marked(rec):
                continue
            # ② 可选降级：主数据源（另一个库）在时它给了标记，说明它分得清主次。
            if owns.get(r) and lib_of(s) not in owns[r]:
                optional.append(f"{s} {r}")
                continue
            # ③ 自相矛盾：内容随库变、无标记，却**明说** `available: True`。
            if rec["avail"] is True:
                contradict.append(
                    f"{s} {r}：内容随库变、却仍报 `available: True`")
                continue
            # ④ 剩下的是真静默：没标记、也没说可用。
            reds.append(f"{r} 在「{s}」下响应内容变了（说明它读了那个库），"
                        f"但 HTTP {rec['status']} 且无 `available`/`error` 标记")
            silent.append(f"{s} {r}")

    # A2a（形态一致性）：**同一个库的不同坏法必须同样对待**。
    # 一个库有两种坏法（文件不在 = `database_not_found`；文件在但不是库 =
    # `not_a_database`），实现很容易只处理其中一种。判据不恒真——实测里
    # `unavailable_payload` 与 errorhandler 两条通道确实分岔（200 vs 503），
    # 但都给了标记，故本条为绿。
    for r in rules:
        for lib, scenes in (("final", final_scenes), ("staging", staging_scenes)):
            got = [s for s in scenes if marked(face[(s, r)])]
            if not got or len(got) == len(scenes):
                continue
            miss = [s for s in scenes if s not in got]
            reds.append(f"{r} 的「{lib}库」坏法处理不一致："
                        f"{got} 给了标记，{miss} 没给")
            silent.extend(f"{s} {r}" for s in miss)

    expect_staging = {r for r in rules if r.startswith(STAGING_PREFIXES)}
    cross = {
        "前缀内的路由": sorted(expect_staging),
        "实测读工作库（内容随它变）": sorted(reads_staging),
        "实测打 available:false": sorted(degraded),
        "实测读最终库（内容随它变）": sorted(reads_final),
        "跨库读取（前缀外却读了工作库）": sorted(reads_staging - expect_staging),
        "前缀内但未测到读工作库": sorted(expect_staging - reads_staging),
        "以最终库为主（最终库坏则报错）": sorted(r for r, v in owns.items() if "final" in v),
        "以工作库为主（工作库坏则报错）": sorted(r for r, v in owns.items() if "staging" in v),
    }
    return reds, cross, silent, contradict, optional


def test_a3(face, combos) -> tuple[list, dict]:
    """A3：**非 JSON 的每一格都必须是有意为之**。

    闭式（`解析 + 无样本 + 非 JSON = 总格数`）是三类对 combos 的**划分**，数学上
    恒真——它值得打印，但**它不是判据**（§2.5.77：「判据在多数样本上恒真就等于
    没有」）。真正的判据是它的下一句：非 JSON 只允许两种形态——接不住的 503
    默认页、与 404 页；出现别的状态码（比如某路由自己炸成 500）就是真缺陷。

    这条判据实测为绿（nonjson = 0，即 280 格里**一格非 JSON 都没有**），
    绿得可信与否由 C 段注入证明：注入一个 500 必须让它转红。
    """
    parsed = [k for k in combos if face[k]["paths"] is not None]
    nosample = [k for k in combos if face[k]["status"] is None]
    nonjson = [k for k in combos if face[k]["paths"] is None and face[k]["status"] is not None]
    reds = []
    if len(parsed) + len(nosample) + len(nonjson) != len(combos):
        reds.append(f"闭式不成立：{len(parsed)}+{len(nosample)}+{len(nonjson)}"
                    f" ≠ {len(combos)}")
    allowed = {503, 404}
    for s, r in nonjson:
        st = face[(s, r)]["status"]
        if st not in allowed:
            reds.append(f"{r} 在「{s}」下返回非 JSON 且状态码 {st}"
                        f"（只允许 {sorted(allowed)}）")
    return reds, {"parsed": len(parsed), "nosample": len(nosample),
                  "nonjson": len(nonjson), "total": len(combos),
                  "nonjson_list": sorted(f"{s} {r} → HTTP {face[(s, r)]['status']}"
                                         for s, r in nonjson)}


# ══════════════════════════════════════════════════════════════════
# 段
# ══════════════════════════════════════════════════════════════════
FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return cond


def part_a(face, combos) -> dict:
    print("═══ A. 实测 ═══")
    rules = sorted({r for _s, r in face})
    per_scene = {}
    for scene, label, _o, _st in SCENES:
        got = [r for r in rules if face[(scene, r)]["paths"] is not None]
        per_scene[scene] = got
        codes = sorted({face[(scene, r)]["status"] for r in got})
        print(f"  {label:16s} 打出 JSON {len(got):2d}/{len(rules)} 条"
              f"  状态码 {codes}")
    return per_scene


def part_b(rep, face, combos, per_scene) -> dict:
    print("\n═══ B. 判据 ═══")
    mis, nolive = misreads(rep)
    print(f"  快乐路径下读不到的键 {len(mis)} 条"
          f"（§2.5.84 的 B4 + 共享封装豁免）")
    print(f"  另：快乐路径就打不出真响应的调用点 {len(nolive)} 处"
          f"（名下 {sum(x['n'] for x in nolive)} 条路径，核不了、不判红）")
    a1_reds, conf = test_a1(mis, face)
    ok(not a1_reds, "A1 每条读都在某个错误分支上实测出现",
       f"{len(conf)}/{len(mis)}")
    for r in a1_reds:
        print(f"      ✗ {r}")

    a2_reds, cross, silent, contradict, optional = test_a2(face)
    ok(not a2_reds, "A2 读了某库的路由在该库不可用时都给出了可识别标记")
    for r in a2_reds:
        print(f"      ✗ {r}")
    print(f"  · 可选降级 {len(optional)} 例（内容随库变、但主数据源不是它——设计如此）")
    for x in optional[:6]:
        print(f"      · {x}")
    print(f"  · 自相矛盾 {len(contradict)} 例（无标记却报 available:True，供裁决）")
    for x in contradict[:6]:
        print(f"      ! {x}")

    a3_reds, a3 = test_a3(face, combos)
    ok(not a3_reds, "A3 非 JSON 的每一格都是有意为之（503/404）",
       f"闭式 {a3['parsed']} 解析 + {a3['nosample']} 无样本"
       f" + {a3['nonjson']} 非 JSON = {a3['total']}")
    for r in a3_reds:
        print(f"      ✗ {r}")

    print("\n  ── 库归属（实测，不靠前缀硬编码）──")
    for k, v in cross.items():
        print(f"    {k}（{len(v)}）：{v if len(v) <= 8 else v[:8] + ['…']}")

    # 定量
    happy = {r: face[("happy", r)]["paths"] for r in {r for _s, r in face}}
    only_err: dict = {}
    for r in sorted(happy):
        add = set()
        for scene, _l, _o, _st in SCENES:
            if scene == "happy":
                continue
            got = face[(scene, r)]["paths"]
            if got is None or happy[r] is None:
                continue
            add |= (got - happy[r])
        if add:
            only_err[r] = sorted(add)

    print(f"\n  定量：错误分支**独有**的键路径出现在 {len(only_err)} 条路由上，"
          f"合计 {sum(len(v) for v in only_err.values())} 条")
    for r, ks in sorted(only_err.items(), key=lambda x: -len(x[1]))[:8]:
        print(f"    · {r}  +{len(ks)}  {ks[:6]}{' …' if len(ks) > 6 else ''}")
    print(f"\n  静默降级（内容随库变、却无可识别标记）{len(silent)} 例：")
    for x in silent[:12]:
        print(f"    · {x}")

    return {"a1_reds": a1_reds, "a2_reds": a2_reds, "a3": a3, "mis": mis,
            "conf": conf, "cross": cross, "only_err": only_err,
            "silent": silent, "contradict": contradict, "optional": optional,
            "per_scene": per_scene,
            "reds": a1_reds + a2_reds + a3_reds}


# ══════════════════════════════════════════════════════════════════
# C. 注入式自证
# ══════════════════════════════════════════════════════════════════
def _child_marks(*args) -> dict:
    """另起进程跑 `--marks`，拿机器可读的红集合。"""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--marks", *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    for line in (r.stdout or "").splitlines():
        if line.startswith("MARKS "):
            return json.loads(line[6:])
    raise RuntimeError(f"子进程没打出 MARKS：stdout={r.stdout[-400:]!r} "
                       f"stderr={r.stderr[-400:]!r}")


def _inject_reds(edits: list, *args) -> tuple[set, str]:
    """改真实文件 → 跑子进程 → `finally` 恢复。返回 `(红集合, 备注)`。

    `edits` 是 `[(文件名, old, new), …]`，**允许多个文件多处**。
    单点注入表达不了「同一个响应键有两个出口」：`/api/health` 的 `message`
    在最终库坏时一分为二——文件不存在 / 非 SQLite 走 `dberr.describe()`，
    0 字节 / 空库走 `api.py` 里 `missing_tables` 分支硬编码的那行 return。
    只掐一处，那条读照样有落点，A1 恒绿（**注入要覆盖判据真正依赖的每一格**）。
    """
    saved: list[tuple[Path, str]] = []
    for fname, old, new in edits:
        path = DOC if fname == "01_在线采集.md" else WEB / fname
        src = path.read_text(encoding="utf-8")
        if old not in src:
            for p, s in saved:          # 前面几处已写下去，须先恢复再报错
                p.write_text(s, encoding="utf-8")
            return set(), f"锚点未命中（{fname}）：{old!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        saved.append((path, src))
    try:
        marks = _child_marks(*args)
        return set(marks.get("names") or []), ""
    finally:
        for p, s in saved:
            p.write_text(s, encoding="utf-8")


# 注入用例：(标签, 期望转红的那一条判据 label, 注入面, [(文件名, old, new), …], 说明)
#
# ⚠️ **首版四例全部静默失效**：它们只往响应里**加**了一个新键
# （`__probe_nohint__` / `hint_`），而 A1/A2/A3 三条判据**都不依赖"多没多一个键"**
# ——注入跑了，退出码却分毫未动，打印出来还是一句"已跑"。这正是 §2.5.64
# 「用例条数与判据覆盖的格数不一致时，多出来的用例是空话」：**注入必须打在
# 判据真正依赖的那一格上**，并且断言"红的恰好是它"。
INJECTS: list = [
    ("C1", "A1 每条读都在某个错误分支上实测出现", "源码面",
     [("dberr.py",
       '    return {"error": code, "message": message, "hint": _hint(code, kind, p, what)}',
       '    return {"error": code, "hint": _hint(code, kind, p, what)}'),
      ("api.py",
       '''            "message": f"最终库缺少必需表：{'、'.join(missing_tables)}",\n''',
       "")],
     "掐掉 `/api/health` 的 `message` ⇒ `dom.js:110` / `overview.js:145` 那两条读"
     "（tail 是 `message`，属 `WRAP`，**无条件进 `mis`**）落空 ⇒ 打 A1。\n"
     "**这个键有两个出口，必须同时掐两处**（实测坐实）：文件不存在 / 非 SQLite "
     "走 `dberr.describe()`，0 字节 / 空库走 `api.py` 里 `missing_tables` 分支"
     "硬编码的那行 return——只改前者时，`final_zero` / `final_blank` 两个场景"
     "照样带 `message`，落点还在，A1 恒绿（首版正是栽在这里）。\n"
     "`error` / `hint` 与 503 都还在 ⇒ 不打 A2；状态码仍是 503 ⇒ 不打 A3。\n"
     "**靶子要挑 `mis` 里「无条件入选」的那几条**：改 `funnel_payload()` 那类注入会"
     "让静态面也说「确证没有」，于是该读**被移出 `mis`**——判据的输入被改了，"
     "而不是判据被打了（也实测过：`mis` 7→6，A1 照绿）"),
    ("C2", "A2 读了某库的路由在该库不可用时都给出了可识别标记", "源码面",
     [("staging_db.py",
       '        "available": False,\n'
       '        "data": [] if data is None else data,\n'
       '        **probe(missing=missing),',
       '        "data": [] if data is None else data,\n'
       '        "hint": probe(missing=missing)["hint"],')],
     "`unavailable_payload` 去掉 `available` 与 `error`、**只留 `hint`** ⇒ 11 条"
     "纯工作库路由在该库坏时既无标记、也不自称可用 ⇒ 打 A2；`hint` 还在 ⇒ 不打 A1。\n"
     "**首版锚点少写了一行**（`**probe(missing=missing),` 没吃掉）⇒ `error` 仍在 ⇒ "
     "`marked()` 为真 ⇒ 注入静默失效（§2.5.64「锚点写错 = 用例根本没跑」）"),
    ("C3", "A3 非 JSON 的每一格都是有意为之（503/404）", "源码面",
     [("extract.py",
       '    return jsonify({"ok": True, "available": True, "data": taxonomy_payload()})',
       '    return "boom", 500')],
     "让 `/api/extract/taxonomy` 炸成 500 非 JSON ⇒ 打 A3；它在 A2 里因"
     "`paths is None` 被提前跳过 ⇒ 不打 A2"),
]


def part_c(rep) -> dict:
    print("\n═══ C. 注入式自证 ═══")
    base = _child_marks()
    base_names = set(base.get("names") or [])
    print(f"  基线（未注入）：红 {len(base_names)} 条，"
          f"坐实 {base.get('n_conf')} 条待坐实的读")
    out = {"base": base}
    ok(not base_names, "基线必须是绿的（有红就没有可归因的基线）")
    if base_names:
        for n in sorted(base_names):
            print(f"      ✗ {n}")
        return out

    # C4 的期望标签要从 D 段自己算出来（它是动态数字），故在这里拼。
    doc_label = f"文档锚点：{_doc_needles(rep)[0][0]}"
    cases = list(INJECTS) + [
        ("C4", doc_label, "文档面",
         [("01_在线采集.md", "\n### 2.5.85 ", "\n### 2.5.85x ")],
         "改文档里的本节标题 ⇒ 打 D 段那一条锚点；正文与源码都不动"),
    ]

    for tag, want, face, edits, why in cases:
        names, note = _inject_reds(edits, "--marks")
        if note:
            # 「锚点未命中」与「判据报警」在输出上都是红——必须分开说（§2.5.67）
            ok(False, f"{tag} 注入锚点未命中", note)
            out[tag] = {"reds": [], "note": note}
            continue
        got = set(names)
        # **只断言本次注入所在的那个面**（§2.5.68：两个注入面都要测，但要分开测）。
        #
        # 源码面注入改的是**响应键**，而 D 段那几条数字锚点（「待坐实的读 N 条」
        # 「错误分支独有 N 条」）正是从响应面统计出来的 ⇒ 它们**必然跟着变**，
        # 那不是"归因不唯一"，而是**文档对账在正确地报警**（源码改了数、文档
        # 没跟）。所以源码面用例断言"**源码面**红的恰好是那一条"，把附带打红的
        # 文档锚点如实打印出来；文档面用例反过来断言文档面。
        doc_reds = {n for n in got if n.startswith("文档锚点")}
        src_reds = got - doc_reds
        inner = doc_reds if face == "文档面" else src_reds
        ok(inner == {want}, f"{tag} 注入后{face}红的**恰好**是那一条",
           f"期望「{want}」，实测 {sorted(inner) if inner else '无红'}"
           + (f"（附带打红文档锚点 {len(doc_reds)} 条）"
              if face == "源码面" and doc_reds else ""))
        if inner != {want}:
            print(f"      · 说明：{why}")
        out[tag] = {"reds": sorted(got), "want": want, "face": face, "note": ""}
    return out


# ══════════════════════════════════════════════════════════════════
# D. 文档对账
# ══════════════════════════════════════════════════════════════════
def _doc_needles(rep) -> list[tuple[str, str]]:
    """本节要核的文档锚点：每条 = `(标签, 该在文档里出现的整句片段)`。"""
    out = [(n, n) for n in DOC_NEEDLES]
    a3 = rep["a3"]
    out.append(("A3 闭式", f"{a3['parsed']} 解析 + {a3['nosample']} 无样本 + "
                          f"{a3['nonjson']} 非 JSON = {a3['total']}"))
    out.append(("待坐实的读条数", f"待坐实的读 **{len(rep['mis'])}** 条"))
    out.append(("错误分支独有键", f"错误分支独有 {sum(len(v) for v in rep['only_err'].values())} 条"))
    return out


def part_d(rep) -> None:
    print("\n═══ D. 文档对账 ═══")
    src = DOC.read_text(encoding="utf-8")
    for label, needle in _doc_needles(rep):
        hit = needle in src
        print(f"  {'✓' if hit else '✗'} {label}")
        if not hit:
            FAILED.append(f"文档锚点：{label}")


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════
def collect() -> dict:
    point_at(str(MAIN_DB), str(STAGING_DB))
    from construction_agent.web.factory import create_app

    app = create_app()
    tmp = Path(tempfile.mkdtemp(prefix="c2585_"))
    shapes = Shapes(tmp)
    try:
        face, combos = sweep(app, shapes)
    finally:
        shapes.cleanup()
    rep = K.run()
    per_scene = part_a(face, combos)
    return {"face": face, "combos": combos, "rep": rep, "per_scene": per_scene}


def run() -> dict:
    rep = collect()
    b = part_b(rep["rep"], rep["face"], rep["combos"], rep["per_scene"])
    return {**rep, **b}


def main() -> int:
    if "--marks" in sys.argv:
        try:
            r = run()
            # D 段也要进 MARKS——否则"文档面"的注入在子进程里根本看不见
            # （§2.5.68：两个注入面都要测）。
            part_d(r)
        except Exception as exc:            # noqa: BLE001 —— 子进程要给出可读的失败
            print(f"MARKS {json.dumps({'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}")
            return 1
        print("MARKS " + json.dumps(
            {"names": FAILED, "n_conf": len(r["conf"]),
             "n_mis": len(r["mis"])}, ensure_ascii=False))
        return 0

    print("§2.5.85 GET 错误分支的真值面\n")
    rep = run()
    part_c(rep)
    part_d(rep)
    print("\n" + "═" * 60)
    if FAILED:
        print(f"❌ 有 {len(FAILED)} 项未通过：")
        for f in FAILED:
            print(f"  · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
