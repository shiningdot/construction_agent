#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.88 坏掉的时候，用户看到的是什么：错误提示的**真可见性**与**真准确性**。

## 来路

§2.5.87 核的是「catch 里有没有写进 DOM」——**源码层面的三个动作**，并自己登记了两条
边界（原文）：

> ②B2 认的"可见落地"是源码层面的三个动作，写进一个 `display:none` 的容器、或写进一个
> **根本不存在的 id**（`$('x')` 返回 null 时直接抛）都算绿——**真正的可见性要 DOM 断言**
> （§2.5.68 那条路的延伸，本节没做）

本节就做这一格。**判据对象从静态源码换成运行时的浏览器**：把两个库分别打坏，真加载
六个页面，看**用户屏幕上到底出现了什么**。

## 为什么必须真打

§2.5.87 静态扫出的那些 `catch` 与 `available:false` 分支，**在快乐路径上一条都不会执行**。
"代码里写得对"与"那条分支真的跑得通"是两件事——`$('dbNotice')` 返回 null 时
`box.hidden = false` 会当场抛 TypeError（而快乐路径下永远走不到这句）。

## 两个库要**分别**打坏

2026-10-05 侦察发现（v1 探针的教训）：`/collect` `/extract` `/review` 读写的是**工作库**
`caiems_staging.db`，只把最终库打坏，这三页**毫无反应**。反过来详情页不读工作库。
故场景是 **两库 × 三种坏法**，不是一维的"库坏了"。

## 判据

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 有失败请求的格，页面 `innerText` 的**基线差集**里必须有实质新行 | 静默降级成空面板（用户以为是"本来就没有数据"） | 红 |
| A2 | `pageerror` 为 0、且**代码写的** `console.error` 为 0 | `$('x')` 目标不存在时当场抛（§2.5.87 边界②点名的形态） | 红 |
| A3 | 同一页面，**`error` 码集合不同**的两格，告示文本**不得逐字相同** | "后端分了两种故障、前端显示成同一句话"（有 hint 参数却不用） | 红 |
| A4 | 每格的（后端 message/hint ↔ 页面新行）并排打印 | "文案与事实不符"这类只能人看的 | 观察 |

**A1 为什么用 `innerText` 而不是 `textContent`**：`innerText` 反映**渲染性**——
`display:none` 的内容不在里面、折叠的 `<details>` 里的也不在（§2.5.68 实测）。所以
用它就是在核"**用户真读得到**"，边界②说的"写进一个 `display:none` 的容器"这一半
**被它天然覆盖**；另一半（写进不存在的 id）由 A2 抓。

**A3 为什么是"码不同则文本必须不同"**：后端对「文件不存在」与「文件在但不是库」
给了两个不同的 `error` 码，并各配了一句**下一步动作不同**的 hint。前端若把它们显示
成同一句话，说明它**没消费这个诊断**。反过来，若两种故障的 `error` 码相同（后端自己
就没区分），前端合并**是合理的**、判据自动放行——**这就是豁免规则的复核方式**：
不写"我觉得可以合并"，而是问"后端的码一样吗"（§2.5.67：例外白名单不能只写理由）。

**A2 为什么要过滤 `console.error`**：浏览器对 4xx/5xx 的**资源加载失败**会自动打一条
`Failed to load resource: … 503`，那是浏览器的、不是代码写的。判据要的是后者。

## 边界（判据核不到什么）

- A1 只核"**出现了新文本**"，核不到新文本**说得对不对**（那由 A3 部分覆盖 + A4 登记）；
- A1 的基线差集是**该页面自己**的 happy 文本，故渲染里任何与故障无关的抖动
  （时间戳等）都会进差集——实测详情页在 staging 场景下有 2~3 行这样的抖动，
  故 A1 只在**该格真有失败请求**时才判；
- A3 只核"两种故障的告示是否不同"，核不到**各自说得对不对**；
- A4 是**观察项不判红**：把两栏并排摆出来供裁决，不假装判得了"事实不符"；
- 只覆盖**页面加载**这一刻（用户点按钮触发的失败不在射程内）；
- 只覆盖**无头 Chromium** 的渲染（`innerText` 的可见性判定跟随它的排版引擎）。

## 安全

坏库一律建在 `tempfile.mkdtemp()`，**绝不在主仓库 `data/` 下落地**（那里已有一次
误建探针文件的待办）。`CAIEMS_DB` / `CAIEMS_STAGING_DB` 在 `try/finally` 里恢复。

用法：
    .venv/bin/python3 scripts/check_2588_error_notice.py
    .venv/bin/python3 scripts/check_2588_error_notice.py --self-test
    .venv/bin/python3 scripts/check_2588_error_notice.py --drift-test

退出码 `0` = 三段全绿。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root               # noqa: E402
from _serve import point_at, serve                             # noqa: E402

#: 要真加载的六个页面。前两个与详情页读**最终库**，中间三个读**工作库**。
PAGES = ["/", "/causes", "/collect", "/extract", "/review"]

#: 场景 = `(名字, CAIEMS_DB, CAIEMS_STAGING_DB)`。
#: `main` / `stg` 两个占位符在 `_scenes()` 里换成真实路径。
SCENES = [
    ("happy",         "main", "stg"),
    ("final_gone",    "gone", "stg"),
    ("final_blank",   "blank", "stg"),
    ("staging_gone",  "main", "gone"),
    ("staging_blank", "main", "blank"),
    ("staging_notdb", "main", "notdb"),
]

#: 只比较**同一个库**的两种坏法，且**只比读那个库的页面**。
#: 首版没限定页面，于是 `final_gone` vs `final_blank` 被拿去比 `/collect`——那页读的是
#: **工作库**，对最终库怎么坏本来就不该有反应，两格的告示自然相同、被判红（越库比较）。
PAGE_DB = {
    "final": ["/", "/causes"],                       # + `/accident/<id>`，见 pagedb()
    "staging": ["/collect", "/extract", "/review"],
}
PAIRS = [("final_gone", "final_blank", "final"),
         ("staging_gone", "staging_blank", "staging"),
         ("staging_blank", "staging_notdb", "staging")]


def pagedb(rep: dict, which: str) -> list[str]:
    """`which` 这个库的坏法会波及哪些页面（详情页动态拼 id）。"""
    return [p for p in PAGE_DB[which] if p in rep["pages"]] + \
           [p for p in rep["pages"] if p.startswith("/accident/")
            and which == "final"]

#: 浏览器自己打的、不是代码写的。判据要的是**代码**写的那条。
BROWSER_NOISE = re.compile(r"Failed to load resource|net::ERR_|favicon", re.I)


# ══════════════════════════════════════════════════════════════════
# 采集
# ══════════════════════════════════════════════════════════════════
def _scenes(tmp: Path) -> tuple[list, dict]:
    """→ `(场景表, 路径表)`。坏库一律在这里现造，全部落在临时目录。"""
    gone = tmp / "nope" / "caiems_v2.db"                    # 父目录都不存在
    blank = tmp / "blank.db"                                # 合法 SQLite、0 张表
    sqlite3.connect(blank).close()
    notdb = tmp / "notdb.txt"                               # 非 SQLite
    notdb.write_text("this is not a database\n", encoding="utf-8")

    paths = {"main": str(main_db_path()),
             "stg": str(main_repo_root() / "data" / "caiems_staging.db"),
             "gone": str(gone), "blank": str(blank), "notdb": str(notdb)}
    return [(n, paths[a], paths[b]) for n, a, b in SCENES], paths


def _sample_id() -> str:
    """详情页要一个真 id。只读、不写。"""
    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT id FROM accidents LIMIT 1").fetchone()
    finally:
        con.close()
    return row[0]


_RESP_JS = """() => {
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length) return;              // 只取叶子，避免祖先重复
    // ⚠ 必须过 `checkVisibility()`：页内 `el.innerText` 对**不可渲染**的元素会回退到
    // `textContent`（与 Playwright 的 `locator.inner_text()` 行为不同——那个对不可见
    // 元素返回空串）。首版漏了这一句，把 `<noscript>` 的**源码文本**收进了告示里。
    if (!el.checkVisibility || !el.checkVisibility()) return;
    const t = (el.innerText || '').trim();
    if (t) out.push(t);
  });
  return out;
}"""

#: 告示容器自身的状态：**填了内容吗 / 用户看得见吗**。
#:
#: A1 判的是「**页面**上有没有新增的可见信息」，不是「**那条**告示可不可见」。C3 注入
#: 实测把这点摊开了：给 `#dbNotice` 加 `display:none` 之后 A1 照旧绿——因为 `/collect`
#: 上别的卡片仍各自报了自己的错（「漏斗加载失败：…」），**页面确实有新增可见信息**。
#: 那不算 A1 失灵，是两条判据问的不是同一个问题，所以要单独探一格（A1′）。
_NOTICE_JS = """() => {
  const n = document.getElementById('dbNotice');
  if (!n) return null;
  const t = (n.textContent || '').trim();
  // `checkVisibility()` 对 `display:none` 与 `hidden` 属性都返回 false——正是要的。
  return {has: t.length > 0, visible: !!(n.checkVisibility && n.checkVisibility()),
          text: t.slice(0, 160)};
}"""


def collect(port: int = 5110) -> dict:
    """跑 场景 × 页面，逐格记：失败响应、控制台、`innerText`、叶子文本。

    `innerText` 走**渲染后**的值（`display:none` 与折叠内容不在里面）——这正是 A1
    要的"用户真读得到"。另存一份**叶子文本**（`_RESP_JS`），供 A3 取"告示文本"。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:                                      # pragma: no cover
        print("需要 playwright：.venv/bin/pip install playwright && "
              ".venv/bin/playwright install chromium", file=sys.stderr)
        raise SystemExit(2) from None

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    tmp = Path(tempfile.mkdtemp(prefix="chk2588_"))
    scenes, paths = _scenes(tmp)
    point_at(paths["main"], paths["stg"])
    server, base = serve(port)
    page_list = PAGES + [f"/accident/{_sample_id()}"]
    rep: dict = {"cells": {}, "scenes": [s[0] for s in scenes],
                 "pages": page_list, "tmp": str(tmp), "ok": True}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
            for sname, mdb, sdb in scenes:
                os.environ["CAIEMS_DB"] = mdb
                os.environ["CAIEMS_STAGING_DB"] = sdb
                for pg in page_list:
                    rep["cells"][(sname, pg)] = _one(ctx, base, pg)
    finally:
        server.shutdown()
        os.environ["CAIEMS_DB"] = paths["main"]
        os.environ["CAIEMS_STAGING_DB"] = paths["stg"]
    return rep


def _one(ctx, base: str, pg: str) -> dict:
    """一格：一次页面加载。"""
    page = ctx.new_page()
    errs: list[str] = []
    fails: list[dict] = []
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))

    def on_con(m):
        if m.type == "error" and not BROWSER_NOISE.search(m.text):
            errs.append("console.error: " + m.text[:200])
    page.on("console", on_con)

    def on_resp(r):
        if not r.url.startswith(base + "/api/"):
            return
        try:
            j = r.json()
        except Exception:                                    # noqa: BLE001
            return
        if not isinstance(j, dict):
            return
        d = j.get("data")
        dd = d if isinstance(d, dict) else {}            # `data` 也可能是 list

        def dig(k):
            v = j.get(k)
            return v if v is not None else dd.get(k)
        # ⚠ 只认**显式布尔假**。首版写成 `j.get("ok") is None` ⇒ 把所有**没有 `ok`
        # 键的正常响应**都算成降级，"有失败请求的格"虚高到 29/36。
        degraded = dig("available") is False or dig("ok") is False
        if r.status >= 400 or degraded:
            fails.append({"status": r.status, "url": r.url.replace(base, ""),
                          "code": str(dig("error")
                                      or ("degraded_%d" % r.status
                                          if degraded else "http_%d" % r.status)),
                          "message": str(dig("message") or "")[:200],
                          "hint": str(dig("hint") or "")[:240]})
    page.on("response", on_resp)

    try:
        page.goto(base + pg, wait_until="networkidle", timeout=25000)
        loaded = True
    except Exception as e:                                   # noqa: BLE001
        errs.append("goto: " + str(e)[:120])
        loaded = False
    page.wait_for_timeout(1200)
    whole = page.evaluate("document.body ? document.body.innerText : ''")
    leaves = page.evaluate(_RESP_JS)
    # ⚠ 判据取**展开态**。概览页那 1 个 `<details>` 默认闭合，`innerText` **不含**折叠
    # 里的内容（§2.5.68 实测：概览页 5,187 → 6,606 字）。首版用折叠态，于是
    # `/api/images/stats` 失败导致「现场图片」卡消失这件事**判据根本看不见**，
    # 报出三条"有失败请求却无变化"的误报。本节测的是「用户点开后能读到什么」，
    # 「首屏默认能看到什么」是 §2.5.68 的课题。
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    page.wait_for_timeout(150)
    leaves_open = page.evaluate(_RESP_JS)
    whole_open = page.evaluate("document.body ? document.body.innerText : ''")
    nfold = page.evaluate("document.querySelectorAll('details').length")
    notice_box = page.evaluate(_NOTICE_JS)
    page.close()
    return {"loaded": loaded, "fails": fails, "errs": errs, "nfold": nfold,
            "text": whole, "leaves": leaves,
            "text_open": whole_open, "leaves_open": leaves_open,
            "notice": notice_box}


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════
#: "实质新行"：长度够、且不是纯数字/百分比/日期之类的排版噪声。
_MEANINGFUL = re.compile(r"[一-鿿]{2,}|[A-Za-z]{4,}")


def new_lines(rep: dict, sname: str, pg: str) -> list[str]:
    """该格**展开态** `innerText` 相对**本页 happy 基线**的新行（去掉噪声）。"""
    base = rep["cells"].get(("happy", pg), {}).get("text_open", "")
    cur = rep["cells"].get((sname, pg), {}).get("text_open", "")
    if sname == "happy":
        return []
    out = []
    for ln in cur.splitlines():
        ln = ln.strip()
        if not ln or ln in base:
            continue
        if _MEANINGFUL.search(ln):
            out.append(ln)
    return out


def _leafset(rep: dict, sname: str, pg: str, key: str = "leaves_open") -> set[str]:
    out = set()
    for t in rep["cells"].get((sname, pg), {}).get(key, []):
        t = re.sub(r"\s+", " ", t).strip()
        if t and _MEANINGFUL.search(t):
            out.add(t)
    return out


def diff_leaves(rep: dict, sname: str, pg: str) -> tuple[list[str], list[str]]:
    """→ `(新增的叶子, 消失的叶子)`，都以本页 happy 基线为参照。

    用叶子而不是整页文本：整页文本里混着渲染抖动（计数格式、时间戳），两格永远
    不会逐字相同，A3 的"逐字相同"判据就恒绿了；而且"消失"只有落在叶子上才指得清。
    """
    if sname == "happy":
        return [], []
    base = _leafset(rep, "happy", pg)
    cur = _leafset(rep, sname, pg)
    return sorted(cur - base), sorted(base - cur)


def notice_text(rep: dict, sname: str, pg: str) -> str:
    """该格的**告示文本**：相对基线**新增**的叶子文本，规范化后拼接。"""
    added, _ = diff_leaves(rep, sname, pg)
    return " ⏐ ".join(added)


def codes_of(rep: dict, sname: str, pg: str) -> list[str]:
    """该格失败响应的 `error` 码集合（排序去重）。"""
    return sorted({f["code"] for f in rep["cells"][(sname, pg)]["fails"]})


#: A1 的**例外清单**：有明文理由**不产生告示**的失败。
#:
#: §2.5.67 的规矩——例外不能只写理由、不验证。每条例外都配一个**可证伪的复核**：
#: 这里复核的是「那张卡**确实消失**了，而不是显示 0／旧值」。若哪天前端改成在
#: 不可用时显示「0 张图」，复核立刻失败、例外不成立，那一格会被 A1 抓成红。
A1_EXEMPT = [
    {"url": "/api/images/stats", "pages": ["/"], "marker": "现场图片",
     "why": "overview.js:657-669 明文：工作库不可用时整张「现场图片」卡不出现，"
            "而不是显示 0——「没有图片索引」与「一张图都没有」是两件事，"
            "混在一起会谎报采集完整度。"},
]


def exempted(rep: dict, sname: str, pg: str) -> bool:
    """该格的失败是否**全部**落在例外清单里，**且每条例外的复核都通过**。"""
    fails = rep["cells"][(sname, pg)]["fails"]
    if not fails:
        return False
    base, cur = _leafset(rep, "happy", pg), _leafset(rep, sname, pg)
    for f in fails:
        rule = next((r for r in A1_EXEMPT
                     if r["url"] in f["url"] and pg in r["pages"]), None)
        if rule is None:
            return False                 # 有一条没被覆盖 ⇒ 整格不豁免
        m = rule["marker"]
        if not (any(m in t for t in base) and not any(m in t for t in cur)):
            return False                 # 复核失败：卡没消失 ⇒ 例外不成立
    return True


def test_a1(rep) -> list[tuple[str, str, int]]:
    """A1：有失败请求的格，页面必须出现**新增的可见告示**——除非整格被例外清单覆盖。

    这条判据的宽度是**两轮实测**修出来的：
    - 首版写"必须有新文本"，把 `/api/images/stats` 那种「整张卡不出现」
      （`overview.js:657-669` 的明文设计）判成静默 ⇒ **误报**；
    - 第二版放宽成"有新文本**或**内容消失"，误报没了，却**放过了 `display:none`**：
      告示写进了 DOM 但不可见时，`added` 为空、内容消失却能满足判据——而
      **那正是 §2.5.87 边界②点名的形态**（"写进一个 `display:none` 的容器…都算绿"）。
      C3 注入（给 `#dbNotice` 加 `display:none`）就是用来盯住这一格的。

    所以判据回到"必须有新增"，被豁免的那一类改由**显式的、可复核的**例外清单承担
    （`A1_EXEMPT`），而不是靠放宽判据来吞掉。
    """
    bad = []
    for sname in rep["scenes"]:
        if sname == "happy":
            continue
        for pg in rep["pages"]:
            c = rep["cells"][(sname, pg)]
            if not c["fails"]:
                continue                    # 该页在这个场景下确实没有失败，不适用
            if new_lines(rep, sname, pg):
                continue                    # 有新增的可见告示
            if exempted(rep, sname, pg):
                continue                    # 整格失败都有明文理由，且复核通过
            bad.append((sname, pg, len(c["fails"])))
    return bad


def test_a1p(rep) -> list[tuple[str, str, str]]:
    """A1′：**告示容器**（`#dbNotice`）填了内容却不可见 ⇒ 用户读不到。

    这是 C3 注入逼出来的一条。A1 与 A1′ 问的**不是同一个问题**：
      A1 ：「这一格，用户**有没有**被告知什么」——看整页有没有新增可见文本；
      A1′：「**专门用来告知的那个容器**，用户**看得见吗**」——只看那一个元素。
    C3 把 `#dbNotice` 藏起来后 A1 照旧绿（页面上别的卡片各自报了自己的错，确实有新增
    可见信息），A1′ 转红——两条各自守住一半，缺一条就漏掉「告示写了但看不见」这个
    §2.5.87 边界②点名的形态。

    ⚠ 判据**只对"填了内容"的格判**：`happy` 下 `#dbNotice` 是 `hidden` 且文本为空，
    那是正常的未使用态，不该报红。
    """
    bad = []
    for sname in rep["scenes"]:
        for pg in rep["pages"]:
            n = rep["cells"][(sname, pg)].get("notice")
            if n and n["has"] and not n["visible"]:
                bad.append((sname, pg, n["text"][:60]))
    return bad


def test_a2(rep) -> list[tuple[str, str, str]]:
    """A2：无未捕获异常、无代码写的 console.error。"""
    bad = []
    for sname in rep["scenes"]:
        for pg in rep["pages"]:
            for e in rep["cells"][(sname, pg)]["errs"]:
                bad.append((sname, pg, e))
    return bad


def test_a3(rep) -> list[tuple[str, str, str]]:
    """A3：`error` 码不同 ⇒ 告示文本必须不同。→ `[(页面, 场景A, 场景B)]`"""
    bad = []
    for a, b, which in PAIRS:
        for pg in pagedb(rep, which):
            ca, cb = codes_of(rep, a, pg), codes_of(rep, b, pg)
            if not ca or not cb or ca == cb:
                continue                 # 后端自己没区分，前端合并合理
            ta, tb = notice_text(rep, a, pg), notice_text(rep, b, pg)
            if not ta or not tb:
                continue                 # 没告示的格由 A1 管，这里不管
            if ta == tb:
                bad.append((pg, a, b))
    return bad


def part_a(rep) -> dict:
    a1, a1p, a2, a3 = test_a1(rep), test_a1p(rep), test_a2(rep), test_a3(rep)
    print("A 段 实测（六场景 × 六页面）")
    nfail = sum(1 for s in rep["scenes"] for p in rep["pages"]
                if rep["cells"][(s, p)]["fails"])
    print(f"  有失败请求的格 {nfail} / {len(rep['scenes']) * len(rep['pages'])}")
    for sname, pg, n in a1:
        print(f"  ✗ A1 {sname} {pg}：有 {n} 个失败请求，"
              f"但页面**完全照旧**（无新增、无消失）——静默降级")
    for sname, pg, t in a1p:
        print(f"  ✗ A1′ {sname} {pg}：告示容器 `#dbNotice` 写了内容却**不可见**"
              f"（`checkVisibility()` 为假）——用户读不到：{t!r}")
    for sname, pg, e in a2:
        print(f"  ✗ A2 {sname} {pg}：{e}")
    for pg, a, b in a3:
        ca, cb = codes_of(rep, a, pg), codes_of(rep, b, pg)
        print(f"  ✗ A3 {pg}：{a}{ca} 与 {b}{cb} 的告示**逐字相同**——"
              f"后端分了两种故障，前端显示成同一句话")
        print(f"      告示原文：{notice_text(rep, a, pg)[:160]!r}")
    if not (a1 or a1p or a2 or a3):
        print("  ✅ A1/A1′/A2/A3 全绿")
    return {"a1": a1, "a1p": a1p, "a2": a2, "a3": a3, "nfail": nfail}


def part_a1b(rep) -> list[tuple[str, str, list[str]]]:
    """A1b：**内容消失型**降级——失败导致页面某块内容不再出现，且没有任何说明。

    这一档**不判红**（`overview.js:657-669` 那种「整张卡不出现、而不是显示 0」是有
    明文理由的设计），但必须**逐格登记**：用户在此之前看到过那张卡、之后没了，
    而页面上没有一个字解释为什么。裁决权在作者，判据只负责把它摆出来。

    复核该设计理由的方式也在这里：若消失的是**卡**（标记词整条不见），说明是"移除"；
    若卡还在、值却变成了 `0`／旧值，那是**谎报**，会在 A4 的对照里露出来。
    """
    out = []
    for sname in rep["scenes"]:
        if sname == "happy":
            continue
        for pg in rep["pages"]:
            if not rep["cells"][(sname, pg)]["fails"]:
                continue
            added, gone = diff_leaves(rep, sname, pg)
            if gone and not added:
                out.append((sname, pg, gone))
    return out


def part_a4(rep) -> None:
    """A4：观察项——把后端说的与页面显示的并排摆出来。"""
    print("\nA4 段 观察（后端 message/hint ↔ 页面新增行，**不判红**）")
    for sname in rep["scenes"]:
        if sname == "happy":
            continue
        for pg in rep["pages"]:
            c = rep["cells"][(sname, pg)]
            if not c["fails"]:
                continue
            print(f"\n  ── {sname} {pg}")
            for f in c["fails"][:4]:
                print(f"     后端 [{f['code']}] {f['url']}")
                if f["message"]:
                    print(f"          message: {f['message'][:100]}")
                if f["hint"]:
                    print(f"          hint: {f['hint'][:110]}")
            for ln in new_lines(rep, sname, pg)[:3]:
                print(f"     页面 ▸ {ln[:110]}")
            _, gone = diff_leaves(rep, sname, pg)
            for ln in gone[:2]:
                print(f"     页面 ✖ 消失: {ln[:100]}")


# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# B 段：静态面（判据在不在、位置在哪里）
# ══════════════════════════════════════════════════════════════════
STATIC = ROOT / "construction_agent" / "web" / "static"
TPLDIR = ROOT / "construction_agent" / "web" / "templates"

#: **恒等三元**：`X ? A : B` 里 A 与 B 是同一个字符串字面量 ⇒ 两条分支产出同一个值，
#: X 读没读结论都一样。`(hint ? '' : '')` 正是这个形态——参数在、读取动作在、
#: **结论不变**，静态读代码的人会以为 hint 参与了渲染。
#:
#: 为什么用这条而不是"查形参有没有被用"：后者要么漏（值经实参转手给别的形参，如
#: `notice(d.hint || '兜底')`）、要么误报（形参名不叫 hint）。恒等三元是**坏味道本身**，
#: 与变量叫什么名字无关，且能定位到行。
#: **宽度**：它只抓"两条分支是**同一个字符串字面量**"这一种恒等形态；`X ? '' : undefined`、
#: `X ? f() : f()` 这类抓不到（登记在边界里）。
_TERN = re.compile(
    r"\b([A-Za-z_$][\w$.]*)\s*\?\s*([\"'])([^\"']*)\2\s*:\s*\2([^\"']*)\2")


def identity_ternaries(src: str) -> list[tuple[int, str]]:
    """→ `[(行号, 原文)]`。**纯函数**，`--self-test` 直接调它。"""
    out = []
    for m in _TERN.finditer(src):
        if m.group(3) == m.group(4):
            out.append((src[:m.start()].count("\n") + 1, m.group(0)))
    return out


def strip_js_comments(src: str) -> str:
    """把 `//…` 与 `/*…*/` 换成**等长空白**，行号不变。

    必须剥：说明这条坏味道的注释里**必然**会写出 `(hint ? '' : '')` 这个例子
    （本文件、`collect.js` 的修复注释里就有），不剥就会把注释当代码报假红。
    **宽度**：字符串里的 `//`（如 URL 字面量）也会被当成注释起点——实测
    `static/*.js` 里的 API 路径都是单斜杠（`/api/...`），故不触发；
    若将来出现 `'http://…'` 字面量，该行会被误剥、**可能漏报**，登记在边界里。
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def part_b() -> list[str]:
    """B 段：判据**在源码里的位置**（A 段是运行时的，这一档是静态的，两者互补）。"""
    bad: list[str] = []
    print("\nB 段 静态面")
    n_js = hits = 0
    for f in sorted(STATIC.glob("*.js")):
        n_js += 1
        for ln, txt in identity_ternaries(
                strip_js_comments(f.read_text(encoding="utf-8"))):
            hits += 1
            bad.append(f"B1 {f.name}:{ln} 恒等三元 `{txt}`——两条分支同一个值，"
                       f"参数等于没用")
    print(f"  B1 扫描 {n_js} 个 .js，恒等三元 {hits} 处" +
          ("" if hits else "（0 = 没有「假使用」）"))
    for m in bad[:20]:
        print(f"    ✗ {m}")

    # B2 判据在位：告示元素有落点、调用点确实拿到了后端的诊断
    tpl = "\n".join(p.read_text(encoding="utf-8") for p in
                    sorted(TPLDIR.glob("*.html")))
    if 'id="dbNotice"' not in tpl:
        bad.append("B2 模板里找不到 #dbNotice——告示没有落点")
    n_call = 0
    for f in sorted(STATIC.glob("*.js")):
        s = f.read_text(encoding="utf-8")
        # 只要 `.hint`（属性访问）：裸 `hint` 是**形参转手**（如 `blank('—', hint)`），
        # 那是"原报告未提及"的提示语，不是后端诊断。首版没写这个点，把 `blank`、
        # `statCell`、`el` 一概算了进来，"诊断从后端来"这句话就成了空话。
        for m in re.finditer(r"\b(\w+)\s*\(\s*([^()]*\.\s*hint\b[^()]*)\)", s):
            n_call += 1
            print(f"    · {f.name}: {m.group(1)}({m.group(2).strip()[:60]})")
    if n_call == 0:
        bad.append("B2 没有任何调用点把后端的 `.hint` 传下去——"
                   "那后端的诊断就没人消费")
    print(f"  B2 告示落点 {'✓' if 'id=\"dbNotice\"' in tpl else '✗'}；"
          f"传 `hint` 的调用点 {n_call} 处")
    return bad, n_call


# ══════════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════════
DOC = ROOT / "docs" / "web" / "01_在线采集.md"

#: 锚点句用**本节独有**的整行标题（§2.5.83 的教训：与别节同族的措辞会让
#: 「锚点被删」用例恒真）。
DOC_TITLE = ("### 2.5.88 展示层（续）：错误提示的真可见性"
             "——把库打坏，看用户屏幕上有什么")


def part_d(rep: dict, n_call: int, a1b: list) -> list[str]:
    """D 段：文档里写的数 == 本节实测的数。

    **核值而不只核锚点**（§2.5.67 立）：文档那句「有失败请求的格 24 / 36」若与实测
    不符，必报红——否则文档改错了没人知道。

    ⚠ 选的这几项都是**不随注入变**的：`/accident/<id>` 的折叠数只由模板结构决定、
    调用点数只由源码决定、「失败格数」只由**请求**失败与否决定（C 段四例改的全是前端
    渲染与模板样式，请求照发、照样失败）。所以它们能进判据而不与 C 段串味。
    **`A1b 的格数`反过来会随注入变**（C2 让 chip 文案变空 ⇒ 概览页多一条"内容消失"），
    故它只打印、不判红——判据与用例互相污染的时候，先保用例的归因唯一。
    """
    bad: list[str] = []
    print("\nD 段 文档对账（`docs/web/01_在线采集.md`）")
    txt = DOC.read_text(encoding="utf-8")
    nfail = sum(1 for c in rep["cells"].values() if c["fails"])
    ncells = len(rep["cells"])
    nfold_acc = rep["cells"][("happy", rep["pages"][-1])]["nfold"]
    wants = [
        ("本节标题锚点", DOC_TITLE),
        ("场景 × 页面积", f"六场景 × 六页面 = {ncells} 格"),
        ("失败格数", f"有失败请求的格 {nfail} / {ncells}"),
        ("详情页折叠数", f"`/accident/<id>`=**{nfold_acc}**"),
        ("传 `hint` 的调用点", f"传 `hint` 的调用点共 **{n_call} 处**"),
    ]
    for label, s in wants:
        hit = s in txt
        print(f"  {'✓' if hit else '✗'} {label}：`{s}`")
        if not hit:
            bad.append(f"D 文档锚点缺失（{label}）：{s}")
    if not bad:
        print("  ✅ 五项锚点全部命中（**值**来自本次实测，不是写死的期望）")
    print(f"  · 观察（不判红）：A1b 登记 {len(a1b)} 格——该数会随 C 段注入变，"
          f"故不进判据")
    return bad


# ══════════════════════════════════════════════════════════════════
# 自检：判据本身的正反用例
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    """`--self-test`：一个**只会报绿**的判据和没有判据是一回事（§2.5.87）。

    每条用例都给**期望的命中数**，正反两侧都要有——只测正例的话，一条恒不命中的
    正则也会全绿。恒等三元那条尤其：它必须能在 `hint ? '' : ''` 上报 1，
    又必须在 `d.hint || '兜底'` 上报 0（否则 review.js 那种正确写法会被误伤）。
    """
    cases = [
        # (说明, 源码, 期望命中数)
        ("原始缺陷形态", "var s = ' 然后刷新本页。' + (hint ? '' : '') ;", 1),
        ("正确写法：**或**兜底", "n.text = ' ' + (hint || '本页读写的是 x。');", 0),
        ("正确写法：三元但两支不同", "n.text = hint ? '甲' : '乙';", 0),
        ("另一条恒等：单引号内容相同", "n.text = (msg ? 'x' : 'x');", 1),
        ("双引号恒等", 'n.text = (msg ? "" : "");', 1),
        ("属性路径也抓", "n.text = (a.b.c ? '' : '');", 1),
        ("不同引号不算（保守）", "n.text = (m ? '' : \"\");", 0),
        ("正常三元非字面量", "n.text = (x ? f() : g());", 0),
        ("跨行", "n.text = (hint\n  ? ''\n  : '');", 1),
        ("注释里的**不算**（剥注释后）",
         "// hint ? '' : ''\nn.text = 'x';", 0),
        ("块注释里的**不算**",
         "/* (hint ? '' : '') */\nn.text = 'x';", 0),
        ("剥注释后行号仍准",
         "// 说明\n// 说明\nn.text = (h ? '' : '');", 1),
    ]
    bad = 0
    print("自检 恒等三元判据")
    for why, src, want in cases:
        got = len(identity_ternaries(strip_js_comments(src)))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：期望 {want} 实测 {got}")

    # `_MEANINGFUL`：A1/A3 都靠它滤掉渲染抖动
    mcases = [("中文两字以上", "工作库不可用", True),
              ("英文四字母以上", "error", True),
              ("单个汉字", "图", False),
              ("纯数字", "1,771", False),
              ("纯数字与符号", "30.7%", False),
              ("短英文", "ok", False)]
    print("自检 实质文本判据 _MEANINGFUL")
    for why, s, want in mcases:
        got = bool(_MEANINGFUL.search(s))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：期望 {want} 实测 {got}")

    # 浏览器噪声过滤：A2 靠它把"浏览器自动报的 503"与"代码写的"分开
    ncases = [("浏览器自动报的资源 503",
               "Failed to load resource: the server responded with a status of "
               "503 (SERVICE UNAVAILABLE)", True),
              ("代码自己报的", "TypeError: Cannot set properties of null", False),
              ("favicon", "GET http://x/favicon.ico net::ERR_ABORTED", True)]
    print("自检 浏览器噪声过滤 BROWSER_NOISE")
    for why, s, want in ncases:
        got = bool(BROWSER_NOISE.search(s))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {why}：期望 {want} 实测 {got}")

    # A1′ 的三态：填了但不可见（红）／填了且可见（绿）／没填（绿——`happy` 下
    # `#dbNotice` 就是 `hidden` 且空的正常未使用态，**不能**因为它不可见就报红）。
    pcase = {"cells": {
        ("happy", "/collect"): {"notice": {"has": False, "visible": False, "text": ""}},
        ("staging_gone", "/collect"): {"notice": {"has": True, "visible": True, "text": "工作库不可用。"}},
        ("staging_blank", "/collect"): {"notice": {"has": True, "visible": False, "text": "工作库不可用。"}},
        ("staging_gone", "/"): {"notice": None},         # 页面没有该容器
        ("happy", "/"): {"notice": None},
        ("staging_blank", "/"): {"notice": None}},
        "scenes": ["happy", "staging_gone", "staging_blank"],
        "pages": ["/collect", "/"]}
    print("自检 A1′ 告示容器可见性 test_a1p")
    got = test_a1p(pcase)
    want = [("staging_blank", "/collect", "工作库不可用。")]
    ok = got == want
    bad += 0 if ok else 1
    print(f"  {'✓' if ok else '✗'} 三态（填了不可见红／填了可见绿／没填绿）："
          f"期望 {want} 实测 {got}")

    print(f"\n{'✅ 自检全部通过（%d 条）' % (len(cases) + len(mcases) + len(ncases) + 1)}"
          if not bad else f"\n❌ 自检 {bad} 条不过")
    return 1 if bad else 0


# ══════════════════════════════════════════════════════════════════
# C 段：注入式自证——「退出码 0」只有在判据有能力报 1 时才可信（§2.5.68）
#
# 五例（源码/模板面 4 + **文档面 1**），**注入面与被打的判据都不重样**（§2.5.68：只测
# 一个面时，别的面改坏了不会有任何用例转红，等于那几个面没接失败路径）：
#   C1a 静态面 B1 + 运行时 A3：把 `extract.js` 的 hint 透传改回**恒等三元**
#       `(hint ? '' : '')`——那正是 §2.5.88 修掉的原始形态。一例打红两条是**预期内
#       的**：两条看的是同一个缺陷的两个侧面（源码形态 / 运行时后果），不是判据串味
#       ——串味会表现为**无关的格**跟着红。
#   C1b 只打 A3（**复刻另一处缺陷的形态**）：把 span 换成**写死的长文案**，语法干净、
#       没有恒等三元。这正是 `extract.js` 修前的样子——**B1 天生抓不到**（hint 只是
#       没被用，源码里没有坏味道），而 A3 抓得到（三种故障显示同一句话）。C1a 与 C1b
#       合起来才说明「B1 不是 A3 的替代品」：同一处缺陷，一处 B1 能抓、一处只有 A3 能抓。
#   C2  代码面打 A1：`dom.js` 把库故障徽标的文案写成空串 ⇒ 元素在、DOM 在、源码在，
#       就是没字。打红的是**六格**（`final_gone` 与 `final_blank` × 三页共用 dom.js），
#       不是一格——探针实测那六格里 `new_lines` 恰好只有 `['数据库不可用']` 一条，
#       即 chip 是它们**唯一**的告示。
#   C3  模板面打 A1′ + A3（**可见性**）：给 `collect.html` 的 `#dbNotice` 加
#       `style="display:none"`——告示照旧 `hidden=false`、照旧写进 DOM、文本照旧在
#       里面，变的只有「用户看得见吗」。**这正是 §2.5.87 边界②点名的形态**（「写进一个
#       `display:none` 的容器…都算绿」）。
#       ⚠ 首版把这例的期望写成 A1（"有失败请求的格必须有新增可见告示"）——**实测不红**，
#       一度以为判据有盲区。探针查明**A1 是对的**：`/collect` 上别的卡片仍各自报了自己
#       的错（「漏斗加载失败：…」），页面**确实有新增可见信息**。A1 与 A1′ 问的不是同一
#       个问题——A1 管「用户**有没有**被告知」，A1′ 管「**那条**告示看不看得见」。故为此
#       单独立了 A1′（见 `test_a1p`）。A3 则因可见文本退化（三场景告示变同一句）而转红，
#       两条红都是「藏掉容器」的直接后果、不是串味。只改 `collect.html` 一个模板，故
#       `/review` `/extract` 不受影响 ⇒ 归因唯一。
#       ⚠ 「恰好」是**双向**的：首版 a1p 只写了 `['/collect']` **一格**，实测红了**三格**
#       （三种工作库坏法都会在 `/collect` 上填 `#dbNotice`）——**期望写窄同样被判红**，
#       这正是双向断言的价值。三格都对、判据没错，要改的是期望本身。
#   C4  **文档面**打 D 段：把本节标题锚点改名 ⇒ `d=['文档锚点缺失']`。前四例全在改
#       源码/模板，**碰不到 D 段的文档判据**——只测源码面时，文档被改坏了不会有任何
#       用例转红（§2.5.68 立、§2.5.71 复立）。注入的是 `01_在线采集.md`，故 `file`
#       是 `DOC` 而不是 `STATIC`/`TPLDIR`。
# ══════════════════════════════════════════════════════════════════
INJECTIONS = [
    {"name": "C1a extract.js 的 hint 透传改回恒等三元（B1 + A3）",
     "file": STATIC / "extract.js",
     "old": "text: ' ' + (hint ||",
     "new": "text: ' ' + (hint ? '' : '') || (",
     "expect": {"a3": ["/extract"]},
     "expect_sub": {"b1": ["extract.js"]}},
    {"name": "C1b extract.js 的 span 改回**写死文案**（只 A3，B1 抓不到）",
     "file": STATIC / "extract.js",
     "old": "text: ' ' + (hint ||",
     "new": "text: ' 本页读写的是 caiems_staging.db，它还不存在。"
            "在项目根目录执行：' + (",
     "expect": {"a3": ["/extract"]}},
    {"name": "C2 dom.js 的库故障徽标文案写成空串（A1 ×6 格）",
     "file": STATIC / "dom.js",
     "old": "text.textContent = '数据库不可用';",
     "new": "text.textContent = '';",
     "expect": {"a1": ["final_gone /collect", "final_gone /extract",
                       "final_gone /review", "final_blank /collect",
                       "final_blank /extract", "final_blank /review"]}},
    {"name": "C3 告示容器加 display:none（A1′ + A3，可见性）",
     "file": TPLDIR / "collect.html",
     "old": 'id="dbNotice" hidden',
     "new": 'id="dbNotice" style="display:none" hidden',
     "expect": {"a1p": ["staging_gone /collect", "staging_blank /collect",
                        "staging_notdb /collect"],
                "a3": ["/collect"]}},
    # ⚠ **文档面**必须有自己的用例（§2.5.68 立、§2.5.71 复立）：只测源码面时，
    # 文档判据（D 段）被改坏了不会有任何用例转红——那 D 段就是一段没人验的装饰。
    # 注入只动标题、不动其余四项，故 `d` 恰有一条。
    {"name": "C4 文档标题锚点被改名（D 段）",
     "file": DOC,
     "old": "错误提示的真可见性——把库打坏",
     "new": "错误提示的假可见性——把库打坏",
     "expect_sub": {"d": ["文档锚点缺失"]}},
]


def _marks_of_child() -> dict:
    """跑一次**完整主检**（子进程，判据一条不绕），只取 MARKS 行。"""
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--no-a4"],
                       capture_output=True, text=True, timeout=1800)
    for ln in r.stdout.splitlines():
        if ln.startswith("MARKS "):
            return json.loads(ln[6:])
    raise RuntimeError("子进程没给出 MARKS 行（退出码 %d）\n--stdout 尾--\n%s"
                       "\n--stderr 尾--\n%s"
                       % (r.returncode, r.stdout[-800:], r.stderr[-800:]))


#: 会被 MARKS 报出来的档。`total` 是合计、不参与逐档判定。
_MARK_KEYS = ("a1", "a1p", "a2", "a3", "b1", "b2", "d")


def _judge_marks(m: dict, inj: dict) -> list[str]:
    """断言「红的**恰好**是哪几条」（§2.5.68）。

    只断言"该红的红了"太弱——**多红的那些同样是缺陷**（判据串味：一个注入打红无关的
    判据，说明判据认的不是它声称在认的东西）。所以判法逐档两分：
      · 档在 `expect` 里 ⇒ 与期望**集合相等**（顺序无关）；
      · 档在 `expect_sub` 里 ⇒ 非空，且每条都至少含一个给定子串（用于带行号的档，
        如 `b1` 的 `extract.js:146`）；
      · 其余档 ⇒ **必须为空**（这就是"恰好"两个字的落点）。
    """
    problems = []
    want_exact = inj.get("expect", {})
    want_sub = inj.get("expect_sub", {})
    for k in _MARK_KEYS:
        got = m.get(k, [])
        if k in want_exact:
            want = want_exact[k]
            if sorted(got) != sorted(want):
                problems.append(f"{k}: 期望恰为 {want}，实测 {got}")
        elif k in want_sub:
            subs = want_sub[k]
            if not got:
                problems.append(f"{k}: 期望非空（须含 {subs} 中任一子串），实测为空")
            for g in got:
                if not any(s in g for s in subs):
                    problems.append(f"{k}: {g!r} 不含 {subs} 中任一子串")
        elif got:
            problems.append(f"{k}: 期望为空（该档与这个注入无关），实测 {got}")
    if not m.get("total"):
        problems.append("total 为 0——注入根本没打红任何判据（用例失效）")
    return problems


def drift_test() -> int:
    base = _marks_of_child()
    print("基线 MARKS " + json.dumps(base, ensure_ascii=False))
    if base["total"]:
        print("❌ 基线就不干净，注入自证无从谈起（先修基线）")
        return 1

    bad = 0
    for inj in INJECTIONS:
        p: Path = inj["file"]
        src = p.read_text(encoding="utf-8")
        n = src.count(inj["old"])
        if n != 1:
            # 锚点写错 = 用例根本没跑（§2.5.64）。首版这种失败曾在输出上
            # 与「判据报警」长得一模一样，故这里单独报出次数。
            print(f"✗ {inj['name']}\n    锚点在 {p.name} 里出现 {n} 次（须恰好 1 次）"
                  f"——**锚点未命中，用例根本没跑**")
            bad += 1
            continue
        print(f"\n── {inj['name']}  注入 {p.relative_to(ROOT)}")
        try:
            p.write_text(src.replace(inj["old"], inj["new"], 1), encoding="utf-8")
            m = _marks_of_child()
        finally:
            p.write_text(src, encoding="utf-8")
        problems = _judge_marks(m, inj)
        print(f"  {'✓' if not problems else '✗'} 注入后 MARKS "
              + json.dumps(m, ensure_ascii=False))
        for pr in problems:
            print(f"    {pr}")
        bad += 0 if not problems else 1
    # 恢复后回基线：确认上面那些红**是注入引起的**，不是环境抖动（§2.5.86：
    # 「没打到」与「没有标记」必须分开说——这条就是区分两者的那一格）。
    back = _marks_of_child()
    print("\n恢复后 MARKS " + json.dumps(back, ensure_ascii=False))
    if back != base:
        print("✗ 恢复后没回到基线——上面的红可能不是注入引起的")
        bad += 1

    print(f"\n{'✅ 漂移自证全部通过（%d 例）' % len(INJECTIONS) if not bad
           else '❌ 漂移自证 %d 处不过' % bad}")
    return 1 if bad else 0


def run(port: int = 5110) -> dict:
    rep = collect(port)
    print(f"临时目录 : {rep['tmp']}")
    print(f"场景     : {' / '.join(rep['scenes'])}")
    print(f"页面     : {' / '.join(rep['pages'])}")
    print("折叠     : " + " ".join(
        "%s=%d" % (p, rep["cells"][("happy", p)]["nfold"]) for p in rep["pages"]) + "\n")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=5110)
    ap.add_argument("--no-a4", action="store_true", help="跳过 A4 观察段（输出短些）")
    ap.add_argument("--self-test", action="store_true",
                    help="只跑判据自身的正反用例")
    ap.add_argument("--drift-test", action="store_true",
                    help="往源码/文档注入漂移，断言退出码转 1")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.drift_test:
        return drift_test()

    rep = run(args.port)
    res = part_a(rep)
    a1b = part_a1b(rep)
    print("\nA1b 段 登记（**内容消失型**降级，不判红；"
          "消失的是「有明文理由不出卡」还是「谎报」由 A4 对照）")
    if not a1b:
        print("  无")
    for sname, pg, gone in a1b:
        print(f"  · {sname} {pg}：{len(gone)} 条内容不再出现，且无任何说明")
        for g in gone[:2]:
            print(f"      消失 ▸ {g[:100]}")
    if not args.no_a4:
        part_a4(rep)
    bblock, n_call = part_b()
    dblock = part_d(rep, n_call, a1b)

    bad = (len(res["a1"]) + len(res["a1p"]) + len(res["a2"]) + len(res["a3"])
           + len(bblock) + len(dblock))
    # 机器可读的结论行：注入式自证要断言「红的**恰好**是哪几条」，解析中文输出脆
    # （§2.5.68 的教训）。`--drift-test` 只认这一行。
    marks = {"a1": ["%s %s" % (s, p) for s, p, _ in res["a1"]],
             "a1p": ["%s %s" % (s, p) for s, p, _ in res["a1p"]],
             "a2": ["%s %s" % (s, p) for s, p, _ in res["a2"]],
             "a3": [p for p, _, _ in res["a3"]],
             "b1": [m.split()[1] for m in bblock if m.startswith("B1 ")],
             "b2": [m.split()[1] if len(m.split()) > 1 else m
                    for m in bblock if m.startswith("B2 ")],
             "d": list(dblock),
             "total": bad}
    print("MARKS " + json.dumps(marks, ensure_ascii=False))
    print(f"\n{'═' * 60}")
    print(f"A1 静默 {len(res['a1'])} ／ A1′ 告示不可见 {len(res['a1p'])} "
          f"／ A2 未捕获异常 {len(res['a2'])} "
          f"／ A3 后端分了几种前端显示几种 {len(res['a3'])} ／ "
          f"B1 恒等三元 {len(bblock)} ／ D 文档锚点 {len(dblock)} ／ "
          f"A1b 登记 {len(a1b)}（不计错）")
    print("✅ 全部通过" if not bad else f"❌ {bad} 项不过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
