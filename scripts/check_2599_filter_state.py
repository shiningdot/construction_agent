#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.99 概览页筛选状态机：hash ↔ state ↔ 请求 三个投影。

结 09 号 §十五「尚未机器化核查」里仍挂着的那条——概览页的**筛选状态机**
从没单独核过（`check_web_interaction.py` 覆盖的是主路径行为，不问三个投影
彼此是否同源）。

`overview.js` 把**同一批筛选键**写了三遍，分属三个投影：

| 投影 | 函数 | 可见性 |
|---|---|---|
| URL hash | `writeHash()` / `readHash()` | 用户可见（地址栏）、可分享 |
| 请求 query | `toQuery()` | 只在网络面板可见 |
| `state` | 夹在中间 | 不可见 |

三者可观测性依次递减，任一处少一个键，症状各不相同：`readHash` 少一个键
⇒ 分享链接打进去少一维（**静默**）；`toQuery` 少一个键 ⇒ 后端收不到、筛选
失效（**静默**）；`writeHash` 少一个键 ⇒ 刷新后条件丢失（**静默**）。
三个都是"页面照常渲染、数字照常返回"，不报错、不留日志——与 §2.5.82 的
GET query 面同一种病。

三段：
  A 静态  —— 四者键集同源（三投影 + 后端真读 + state 声明）、控件名单、
             重置清单、跨语言常量
  B 运行时 —— 真浏览器里种 hash、点按钮，抓真发出去的请求 URL 与 hash 回写
  C 登记面 —— 无控件的两个维度（`page` / `size`）各自有可证伪的去处
  D 文档对账
  E 自证   —— `--self-test`（判据正反用例）+ `--drift-test`（注入式）

用法：
  /home/suntianhao/construction_agent/.venv/bin/python3 scripts/check_2599_filter_state.py
  ... --static-only        # 只跑 A/C/D，不起服务（约 0.4s）
  ... --self-test          # 判据的正反用例
  ... --drift-test         # 往真实源码/文档注入漂移，断言退出码转 1
退出码 0 = 全部通过。
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from _common import main_db_path, main_repo_root          # noqa: E402
from _serve import point_at, serve                        # noqa: E402

import check_2582_query_params as C2582                   # noqa: E402
import check_2598_filter_wiring as C2598                  # noqa: E402

JS = ROOT / "construction_agent" / "web" / "static"
TPL = ROOT / "construction_agent" / "web" / "templates"
QUERY_PY = ROOT / "construction_agent" / "web" / "accidents_query.py"
PORT = 5170

# 概览页的路由是 `/`（base.html 导航「概览与检索」，§2.5.72 实测六页）。
PAGE = "/"
JSFILE = "overview.js"
TMPLFILE = "overview.html"

# 后端 endpoint 的真实名字。**别按文件名猜**——`accidents_query.py` 里那个
# `ListParams.from_request` 只是被 `api.api_accidents` 调用的转发点，endpoint
# 在 `api.py` 的 `@api_bp.get("/accidents")`（第一版探针按 `accidents.list_accidents`
# 去问，impl_keys 回「找不到函数」⇒ 报出"后端一个键都不读"的假缺陷）。
EP_ACCIDENTS = "api.api_accidents"

D01 = ROOT / "docs" / "web" / "01_在线采集.md"
D09 = ROOT / "docs" / "web" / "09_端到端对齐核查.md"

FAILS: list[str] = []


# ══════════════════════════════════════════════════════════════════
# 输出小工具（与 2597/2598 同款）
# ══════════════════════════════════════════════════════════════════
def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(("  ✓ " if cond else "  ✗ ") + label + (("  " + extra) if extra else ""))
    if not cond:
        FAILS.append(label)
    return cond


def note(label: str, extra: str = "") -> None:
    print("  · " + label + (("  " + extra) if extra else ""))


# ══════════════════════════════════════════════════════════════════
# 解析：三投影 / state 声明 / 重置清单 / 控件
# ══════════════════════════════════════════════════════════════════
def fn_body(js: str, name: str) -> str | None:
    """按大括号配平取函数体。**比 `.*?\\n  }` 这类边界正则稳**：那版遇到
    函数体内缩进恰为两格的块就会提前收尾。"""
    m = re.search(r"^\s*function\s+" + re.escape(name) + r"\s*\(", js, re.M)
    if not m:
        return None
    i = js.find("{", m.end())
    if i < 0:
        return None
    return C2582._brace_body(js, i)


def multi_keys_of(js: str) -> list[str]:
    m = re.search(r"const\s+MULTI_KEYS\s*=\s*\[(.*?)\];", js, re.S)
    if not m:
        return []
    return [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]


def proj_keys(body: str, multi: list[str]) -> set[str]:
    """一个投影函数体里"它管哪些筛选键"。

    字面键来自 `p.get('x')` / `p.set('x'`；多选维度走 `MULTI_KEYS.forEach`，
    按循环变量 `k` 追加（`p.append(k, v)`），故**必须**把 MULTI_KEYS 展开，
    否则三个投影都只报 7 个键、把它们实际管的 12 维漏掉 5 个。
    """
    ks = set(re.findall(r"p\.get\(\s*'([A-Za-z_]\w*)'\s*\)", body))
    ks |= set(re.findall(r"p\.set\(\s*'([A-Za-z_]\w*)'", body))
    if re.search(r"\bMULTI_KEYS\b", body):
        ks |= set(multi)
    return ks


def state_decl_keys(js: str) -> list[str]:
    """`const state = { … }` 声明的全部键（顺序保留）。"""
    m = re.search(r"const\s+state\s*=\s*\{", js)
    if not m:
        return []
    body = C2582._brace_body(js, js.index("{", m.end() - 1))
    return re.findall(r"^\s*([A-Za-z_]\w*)\s*:", body, re.M)


def handler_body(js: str, ctrl: str) -> str | None:
    """`$('<ctrl>').addEventListener(` 的 handler 体（配平取）。"""
    m = re.search(r"\$\(\s*['\"]" + re.escape(ctrl) + r"['\"]\s*\)\s*\.addEventListener\(",
                  js)
    if not m:
        return None
    i = js.find("{", m.end())
    if i < 0:
        return None
    return C2582._brace_body(js, i)


def reset_state_keys(js: str, multi: list[str]) -> set[str]:
    """重置按钮 handler 里**它重置了哪些 state 键**。

    与 2598 的 `reset_ids()` 不同：那版提的是 `$('f_x')` 控件 id，对概览页
    恒返回空集（概览页的重置写的是 `state.x = …`，一个控件 id 都没有）
    ——**照搬会在概览页上得到"重置清单为空"，进而得出"全部漏了"的假缺陷**。
    """
    body = handler_body(js, "resetBtn")
    if body is None:
        return set()
    keys = set(re.findall(r"state\.([A-Za-z_]\w*)", body))
    if re.search(r"state\s*\[\s*k\s*\]", body):     # MULTI_KEYS.forEach
        keys |= set(multi)
    return keys


def tpl_f_controls(html: str) -> set[str]:
    return set(re.findall(r'id="(f_[A-Za-z0-9_]+)"', html))


def fe_constants(js: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in re.finditer(r"const\s+(DEFAULT_SIZE|MAX_SIZE)\s*=\s*(\d+)\s*;", js):
        out[m.group(1)] = int(m.group(2))
    return out


def be_constants(src: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in re.finditer(r"^(DEFAULT_SIZE|MAX_SIZE)\s*=\s*(\d+)\s*$", src, re.M):
        out[m.group(1)] = int(m.group(2))
    return out


def fe_pager_next(js: str) -> str | None:
    """分页器上「下一页」按钮的**文案**——B4 要用它定位那个按钮。

    为什么不在判据里直接写 `'下一页'`：那是**同一份清单的第二个副本**
    （§2.5.73 家族）。改文案（比如本地化）时判据会找不到元素，而
    `locator.count() == 0` 这件事**恰恰长得像"分页器只有一页"**，
    于是判据会静默走进"跳过"分支、再也不报红——**解析失败降级成空集合**
    正是 §2.5.67 明令禁止的那一种失效方式。故文案从源码取；取不到返回
    None，由 B4 **判红**，不是跳过。

    锚点是 `btn(<文案>, meta.page + 1, …)`——「下一页」的**页码参数形态**
    （`+ 1`），不是按钮位置：`renderPager` 里第 0 个按钮是「上一页」、
    第 1 个是**页码 1**（§2.5.99 实测），按 `nth(1)` 定位取到的是"当前页"，
    点它本来就是 no-op —— 判据会变成一条恒定报红的假判据。
    """
    m = re.search(r"btn\(\s*'([^']*)'\s*,\s*meta\.page\s*\+\s*1\b", js)
    return m.group(1) if m else None


# ══════════════════════════════════════════════════════════════════
# 实测
# ══════════════════════════════════════════════════════════════════
def measure() -> dict:
    js = C2598.strip_js_comments((JS / JSFILE).read_text(encoding="utf-8"))
    html = (TPL / TMPLFILE).read_text(encoding="utf-8")
    multi = multi_keys_of(js)

    projs: dict[str, set[str]] = {}
    for fn in ("readHash", "writeHash", "toQuery"):
        body = fn_body(js, fn)
        projs[fn] = proj_keys(body or "", multi)

    from construction_agent.web.factory import create_app
    app = create_app()
    with app.test_client() as c:
        fj = c.get("/api/filters").get_json()
    filters = fj.get("data", fj)
    counted = {k for k, v in filters.items()
               if isinstance(v, list) and v and isinstance(v[0], dict)
               and "count" in v[0]}

    be_keys, be_note = C2582.impl_keys(EP_ACCIDENTS)

    # 表单 submit handler 里读的 `f_*`（与 syncFormFromState 那份清单对账）
    sub = (handler_body(js, "filters") or "")
    sub_f = set(re.findall(r"\$\(\s*['\"](f_[A-Za-z0-9_]+)['\"]\s*\)", sub))
    sync_body = fn_body(js, "syncFormFromState") or ""
    sync_f = set(re.findall(r"\$\(\s*['\"](f_[A-Za-z0-9_]+)['\"]\s*\)", sync_body))

    return {
        "js": js, "html": html, "multi": multi, "projs": projs,
        "filters": filters, "counted": counted,
        "be_keys": be_keys, "be_note": be_note,
        "state_keys": state_decl_keys(js),
        "reset_keys": reset_state_keys(js, multi),
        "tpl_f": tpl_f_controls(html),
        "sub_f": sub_f, "sync_f": sync_f,
        "fe_const": fe_constants(js),
        "be_const": be_constants(QUERY_PY.read_text(encoding="utf-8")),
        "pager_next": fe_pager_next(js),
        "filters_vals": {k: [x["value"] for x in v]
                         for k, v in filters.items()
                         if isinstance(v, list) and v and isinstance(v[0], dict)},
    }


# ══════════════════════════════════════════════════════════════════
# A 段：静态四者同源 + 控件名单 + 重置清单 + 跨语言常量
# ══════════════════════════════════════════════════════════════════
def part_a(m: dict) -> None:
    print("\n── A 段：静态（三投影 / state / 后端真读，四者键集同源）──")
    multi = set(m["multi"])
    allk = set(m["projs"]["readHash"])

    ok(len(multi) == 5 and m["projs"]["readHash"] == m["projs"]["writeHash"]
       == m["projs"]["toQuery"],
       "A1 三个投影（readHash / writeHash / toQuery）管的键集完全相同",
       f"{len(m['projs']['readHash'])} 个：{sorted(m['projs']['readHash'])}")
    if m["projs"]["readHash"] != allk:                       # 理论上不可能
        return

    ok(allk == set(m["state_keys"]),
       "A2 三投影键集 == `const state` 声明的键集",
       f"state {len(m['state_keys'])} 个；差集 "
       f"投影-{sorted(allk - set(m['state_keys']))} / "
       f"state-{sorted(set(m['state_keys']) - allk)}")

    be = set(m["be_keys"])
    ok(allk == be,
       f"A3 三投影键集 == 后端真读的键集（{EP_ACCIDENTS}，复用 §2.5.82 的 impl_keys）",
       f"后端 {len(be)} 个；差集 前端多-{sorted(allk - be)} / 后端多-{sorted(be - allk)}"
       + (f"  [{m['be_note']}]" if m["be_note"] else ""))

    ok(set(m["multi"]) == m["counted"],
       "A4 MULTI_KEYS == /api/filters 里「带 count 的那些键」"
       "（filters 另给一个 `sort` 下拉选项集，那不是多选维度）",
       f"MULTI_KEYS {len(multi)} 个 / filters 带 count 的 {len(m['counted'])} 个；"
       f"差集 {sorted(multi.symmetric_difference(m['counted']))}")

    ok(m["tpl_f"] == m["sync_f"] == m["sub_f"] and len(m["tpl_f"]) == 5,
       "A5 控件三处名单相等：模板 `f_*` == syncFormFromState == 表单 submit 读的",
       f"{sorted(m['tpl_f'])}")

    missing = set(m["state_keys"]) - m["reset_keys"]
    ok(not missing,
       "A6 「重置」handler 重置的 state 键集 == state 声明的键集"
       "（漏一个键 ⇒ 那个维度重置不掉，而 writeHash 又把非默认值写回 hash）",
       f"重置 {len(m['reset_keys'])} / 声明 {len(m['state_keys'])}"
       + (f"；**漏了 {sorted(missing)}**" if missing else ""))

    ok(m["fe_const"] == m["be_const"] == {"DEFAULT_SIZE": 50, "MAX_SIZE": 200},
       "A7 前端 DEFAULT_SIZE/MAX_SIZE == 后端 accidents_query.py 的同名常量"
       "（跨语言第二份副本，只能靠判据钉）",
       f"前端 {m['fe_const']} / 后端 {m['be_const']}")


# ══════════════════════════════════════════════════════════════════
# C 段：无控件的两个维度，各自要有可证伪的去处
# ══════════════════════════════════════════════════════════════════
def part_c(m: dict) -> None:
    print("\n── C 段：无控件的两个维度（登记面必须可证伪）──")
    js = m["js"]
    multi = m["multi"]
    allk = set(m["projs"]["readHash"])
    # 控件 id 是 `f_<键名>`，与 state 的键名**不在同一个命名空间**——做差集前
    # 必须剥前缀，否则差出全部 12 个（第一版就是这么错的：报"12 个维度全无控件"）。
    #
    # 12 个维度有**三个**控件来源，不是一个：
    #   ① 模板静态 `f_*`（5 个：q / year_from / year_to / sort / has_causes）
    #   ② JS 动态 chip（5 个多选维度，由 renderFilters 按 MULTI_KEYS 生成）
    #   ③ 无控件（page 归分页器、size 无 UI）
    # 把 ② 漏掉就会得出"5 个多选维度全无控件"的假缺陷——第一版正是如此。
    static = {c[2:] for c in m["tpl_f"]}
    no_ctrl = sorted(allk - static - set(multi))
    ok(no_ctrl == ["page", "size"],
       "C1 12 个维度的控件来源分三类，无控件的恰好是 page 与 size",
       f"静态 f_* {sorted(static)} / 动态 chip {sorted(multi)} / 无控件 {no_ctrl}")

    rf = fn_body(js, "renderFilters") or ""
    ok("MULTI_KEYS.forEach" in rf,
       "C1b 动态 chips 直接走 MULTI_KEYS（不是那份清单的字面副本）"
       " ⇒ 加一维不必改两处", "")

    body = fn_body(js, "renderPager") or ""
    ok("state.page" in body and "writeHash" in body,
       "C2 `page` 的去处在分页器：renderPager 会改 state.page 并 writeHash"
       "（所以它可以没有输入框）", "")

    writers = []
    for fn in ("syncFormFromState", "pruneUnknown", "loadList", "renderList",
               "toggleMulti"):
        b = fn_body(js, fn) or ""
        if re.search(r"state\.size\s*=", b):
            writers.append(fn)
    # `bindForm` 里允许有，但**只允许落在 resetBtn 的 handler 体内**：
    # 「重置」是归位、不是输入，size 仍然没有任何输入路径。
    reset_body = handler_body(js, "resetBtn") or ""
    in_bf = fn_body(js, "bindForm") or ""
    n_bf = len(re.findall(r"state\.size\s*=", in_bf))
    n_reset = len(re.findall(r"state\.size\s*=", reset_body))
    ok(not writers and n_bf == n_reset == 1,
       "C3 `size` 没有输入路径（写点只有 state 初值 / readHash / 「重置」归位三处）"
       " ⇒ 分享链接带进来的 size 只能靠「重置」纠正，漏它等于漏到底",
       f"其他函数里的写点 {writers}；bindForm 内 {n_bf} 处、其中重置 handler 内 {n_reset} 处")


# ══════════════════════════════════════════════════════════════════
# B 段：运行时真值面
# ══════════════════════════════════════════════════════════════════
def part_b(m: dict, args) -> None:
    print("\n── B 段：运行时真值面（种 hash / 点按钮，抓真请求与 hash 回写）──")
    # werkzeug 的访问日志会把本段的判据行淹掉（每个页面加载好几条 GET），
    # 而判据行才是要看的东西——静音它，只留下自己的输出。
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ok(False, "B0 playwright 可用",
           "需 .venv/bin/pip install playwright && .venv/bin/playwright install chromium")
        return

    vals = m["filters_vals"]
    multi = m["multi"]
    # 每个多选维度取它自己的第一个真实取值——**必须是真值**，否则
    # `pruneUnknown` 会把它当成过期链接清掉，B1 就量不到"hash→请求保真"。
    pick = {k: (vals.get(k) or ["?"])[0] for k in multi}
    if any(v == "?" for v in pick.values()):
        ok(False, "B1 取真值", f"/api/filters 里取不到多选维度的值：{pick}")
        return
    qs = [f"{k}={v}" for k, v in pick.items()]
    hash12 = ("q=%E9%9A%A7%E9%81%93&" + "&".join(qs)
              + "&year_from=2000&year_to=2020&has_causes=1"
                "&sort=date_asc&page=2&size=100")

    # 拼给 hash 的期望值（**不是**请求值：has_causes 两个投影编码不同）
    want_hash = dict(pick)
    want_hash.update({"q": "隧道", "year_from": "2000", "year_to": "2020",
                      "has_causes": "1", "sort": "date_asc",
                      "page": "2", "size": "100"})
    want_req = dict(want_hash)
    want_req["has_causes"] = "true"          # toQuery 用 'true'，writeHash 用 '1'

    server, base = serve(args.port)
    print("  服务：%s（端口 %d）" % (base, args.port))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})

            def grab(url_hash: str):
                """进页面 → 等网络静默 → 返回 (最后一个 /api/accidents 的 query, hash)。"""
                pg = ctx.new_page()
                seen: list[str] = []
                pg.on("request", lambda r, u=seen: u.append(r.url)
                      if "/api/accidents?" in r.url else None)
                pg.goto(base + PAGE + (("#" + url_hash) if url_hash else ""),
                        wait_until="networkidle", timeout=30000)
                pg.wait_for_timeout(900)
                q = ""
                if seen:
                    q = seen[-1].split("?", 1)[1]
                h = pg.evaluate("location.hash")
                return pg, q, h

            # ── B1 hash → 请求 保真 ──
            pg, q, _h = grab(hash12)
            from urllib.parse import parse_qs
            got = parse_qs(q, keep_blank_values=True)
            flat = {k: v[0] for k, v in got.items()}
            ok(set(flat) == set(m["be_keys"]),
               "B1a 12 维 hash 进去 ⇒ 请求恰好带后端读的那 12 个键",
               f"请求 {len(flat)} 个；差集 请求多-{sorted(set(flat) - set(m['be_keys']))}"
               f" / 少-{sorted(set(m['be_keys']) - set(flat))}")
            bad = {k: (flat.get(k), v) for k, v in want_req.items()
                   if flat.get(k) != v}
            ok(not bad, "B1b 每个键的值与 hash 里的一致（含 has_causes 的 '1'→'true' 换码）",
               f"不符 {bad}" if bad else f"has_causes hash='1' 请求='{flat.get('has_causes')}'")
            pg.close()

            # ── B5 默认态：hash 为空进、hash 仍为空（默认值省略生效） ──
            pg, q, h = grab("")
            ok(h in ("", "#"), "B5 无 hash 进页面 ⇒ hash 不被写脏（默认值省略）",
               f"hash={h!r} 请求 {len(parse_qs(q))} 键")
            pg.close()

            # ── B6 chips 的动态维度面（`data-key` 就是为此留的锚点）──
            pg, _q, _h = grab("")
            pg.wait_for_timeout(600)
            keys = pg.eval_on_selector_all(
                "[data-key]", "els => els.map(e => e.getAttribute('data-key'))")
            ok(sorted(set(keys)) == sorted(m["multi"]),
               "B6 页面上真渲染出的 chip 维度 == MULTI_KEYS（动态那份也对得上）",
               f"实测 {sorted(set(keys))}")
            pg.close()

            # ── B2 重置面 ──
            pg, _q, _h = grab("q=x&size=100&" + "&".join(qs))
            pg.click("#resetBtn")
            pg.wait_for_timeout(900)
            h = pg.evaluate("location.hash")
            left = {k: v for k, v in parse_qs(h.lstrip("#"),
                                              keep_blank_values=True).items()}
            ok(not left, "B2 点「重置」后 hash 里不残留任何筛选键"
               "（残留 = 用户以为清了、URL 里还带着，刷新即复发）",
               f"残留 {left}" if left else "hash 已清空")
            pg.close()

            # ── B3 prune 自愈（过期链接）──
            pg, _q, h = grab("province=__不存在的省__&q=%E9%9A%A7%E9%81%93")
            got3 = parse_qs(h.lstrip("#"), keep_blank_values=True)
            ok("province" not in got3 and got3.get("q") == ["隧道"],
               "B3 过期省份被 pruneUnknown 清掉、其余键保留（自愈后 hash 自洽）",
               f"hash={h!r}")
            pg.close()

            # ── B4 分页面（`page` 没有输入框，唯一的去处在分页器）──
            #
            # 第一版写的是 `nxt.nth(1)`（选择器 `#pager a, #pager button`），
            # 实跑报红。查下去**不是缺陷**：`renderPager` 的按钮顺序是
            # [上一页][页码 1][页码 2]…[下一页]，`nth(1)` 取到的是**页码 1**，
            # 而当前就在第 1 页 ⇒ `go(1)` ⇒ `state.page = 1` = 默认值 ⇒
            # `writeHash` 按设计省略 page ⇒ hash 为空。**判据取错了元素**，
            # 与 §2.5.87「名字/位置来自印象不来自 grep」同族。
            # 现按「下一页」该有的**页码参数形态**（`meta.page + 1`）从源码取
            # 文案定位（`fe_pager_next`），这样文案改了判据自动跟随；取不到
            # 时**判红**而不是走进 `else` 跳过——`locator.count() == 0` 长得
            # 跟"分页器只有一页"一模一样，静默降级正是 §2.5.67 禁止的。
            pg, _q, _h = grab("")
            pg.wait_for_timeout(600)
            nxt = m.get("pager_next")
            if not nxt:
                ok(False, "B4 能从源码取到「下一页」按钮的文案（取不到 ⇒ 判据失效，"
                   "不得降级成跳过）",
                   "renderPager 里没找到 `btn(<文案>, meta.page + 1, …)`")
            else:
                nb = pg.locator("#pager button", has_text=nxt)
                n = nb.count()
                if n != 1:
                    ok(False, "B4 `#pager` 里有且只有一个「下一页」按钮",
                       f"文案 {nxt!r} 命中 {n} 个")
                elif nb.is_disabled():
                    note(f"B4 分页器「下一页」disabled（当前页就是末页）⇒ 本条测不到；"
                         f"#pager={pg.inner_text('#pager')!r}")
                else:
                    nb.click()
                    pg.wait_for_timeout(900)
                    h = pg.evaluate("location.hash")
                    ok(parse_qs(h.lstrip("#")).get("page") == ["2"],
                       "B4 点「下一页」⇒ hash 里出现 page=2（`page` 无输入框，"
                       "去处在分页器；writeHash 只在 page>1 时才写）",
                       f"hash={h!r}")
            pg.close()

            browser.close()
    finally:
        server.shutdown()


# ══════════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════════
DOC_MUST = [
    # (文件, 该在文档里逐字出现的串, 说明)
    (D01, "§2.5.99", "01 号有本节"),
    (D09, "R41", "09 号维度号"),
]


def part_d(m: dict) -> None:
    print("\n── D 段：文档对账 ──")
    if not D01.exists() or not D09.exists():
        ok(False, "D1 两份文档存在", f"{D01.exists()} / {D09.exists()}")
        return
    t01 = D01.read_text(encoding="utf-8")
    t09 = D09.read_text(encoding="utf-8")
    ok("2.5.99" in t01, "D1 01 号含 §2.5.99", "")
    ok("2.5.99" in t09, "D2 09 号 §十五 含 §2.5.99", "")
    # 本节实测的四个数须在文档里出现（数字对账，防止"改了实现忘改文档"）
    for s, why in [("12", "四者键集 12 维"), ("5", "MULTI_KEYS 5 个")]:
        ok(s in t01 or s in t09, f"D3 实测值 {s} 已写进文档（{why}）", "")


# ══════════════════════════════════════════════════════════════════
# E 段：自证
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    """判据的正反用例——**退出码 0 只有在脚本有能力报 1 时才可信**（§2.5.64）。"""
    print("── E 段 --self-test：解析器与判据的正反用例 ──")
    bad = 0
    js = C2598.strip_js_comments((JS / JSFILE).read_text(encoding="utf-8"))
    multi = multi_keys_of(js)

    def eq(got, want, why):
        nonlocal bad
        if got != want:
            print(f"  ✗ {why}：得到 {got}，期望 {want}")
            bad += 1
        else:
            print(f"  ✓ {why}")

    eq(len(multi), 5, "MULTI_KEYS 解析出 5 个")
    eq(sorted(fn_body(js, "readHash") is not None for _ in [0]), [True],
       "fn_body 能取到 readHash（配平取体）")
    eq(fn_body(js, "不存在的函数"), None, "fn_body 对不存在的函数返回 None（不静默给空串）")

    b = fn_body(js, "toQuery")
    eq(proj_keys(b, multi), {"q", "year_from", "year_to", "has_causes",
                             "sort", "page", "size"} | set(multi),
       "proj_keys 展开 MULTI_KEYS（否则漏 5 个多选维度）")
    eq(proj_keys("const p = 1;", multi), set(), "proj_keys 对无 p.* 的体返回空集")

    eq(len(state_decl_keys(js)), 12, "state 声明解析出 12 个键")
    eq("size" in state_decl_keys(js), True, "state 声明含 size")

    rk = reset_state_keys(js, multi)
    eq(set(state_decl_keys(js)) - rk, set(),
       "重置清单已覆盖全部 state 键（§2.5.99 修的就是 size 那一个）")
    # 反例：把 size 从清单里去掉，判据必须能报出来
    broken = js.replace("state.size = DEFAULT_SIZE;", "", 1)
    eq(sorted(set(state_decl_keys(broken)) - reset_state_keys(broken, multi)),
       ["size"], "反例：抹掉 `state.size = DEFAULT_SIZE;` ⇒ 恰好报出 size")

    fe = fe_constants(js)
    eq(fe, {"DEFAULT_SIZE": 50, "MAX_SIZE": 200}, "前端两个常量解析正确")
    be = be_constants(QUERY_PY.read_text(encoding="utf-8"))
    eq(be, {"DEFAULT_SIZE": 50, "MAX_SIZE": 200}, "后端两个常量解析正确")
    eq(fe_constants("const DEFAULT_SIZE = 99;"), {"DEFAULT_SIZE": 99},
       "常量正则跟得上改值（不是硬编码 50）")

    eq(fe_pager_next(js), "下一页",
       "B4 的「下一页」文案从源码取（判据里不另写一份，§2.5.73）")
    eq(fe_pager_next(js.replace("btn('下一页', meta.page + 1",
                                "btn('Next page', meta.page + 1", 1)), "Next page",
       "反例：文案改名 ⇒ 解析器跟着走（否则 B4 找不到按钮、静默失效）")
    eq(fe_pager_next("const b = (t) => t;"), None,
       "反例：取不到文案返回 None（由 B4 判红，不降级成空集合，§2.5.67）")
    eq(fe_pager_next("btn(t, meta.page + 1, {})"), None,
       "反例：按钮文案是变量而非字面量 ⇒ 也返回 None（不猜）")

    print(("✓ self-test 全部通过" if not bad else f"✗ self-test {bad} 条失败"))
    return 1 if bad else 0


DRIFTS = [
    # (说明, 相对 ROOT 的路径, old, new, 期望转红的判据标签片段[, 跑哪段[, 要求恰好]])
    #
    # **两个注入面都要测**（§2.5.68）：前四条打的是静态面（A 段），最后一条
    # 打的是**运行时面**（B 段）——只测静态面的话，B 段判据改坏了不会有任何
    # 用例转红，它报绿就只是"没报错"。live 用例要真起服务 + 开浏览器，约 30s。
    ("A6 反例：重置清单里删掉 size", "construction_agent/web/static/overview.js",
     "state.size = DEFAULT_SIZE;", "", "A6"),
    ("A7 反例：前端 MAX_SIZE 改成 300",
     "construction_agent/web/static/overview.js",
     "const MAX_SIZE = 200;", "const MAX_SIZE = 300;", "A7"),
    ("A2 反例：state 声明里加一个投影不发的键",
     "construction_agent/web/static/overview.js",
     "    size: DEFAULT_SIZE,\n  };", "    size: DEFAULT_SIZE,\n    zzz: 1,\n  };",
     "A2"),
    ("A4 反例：MULTI_KEYS 少一维",
     "construction_agent/web/static/overview.js",
     "'province', 'severity', 'accident_type', 'project_type', 'construction_phase',",
     "'province', 'severity', 'accident_type', 'project_type',", "A4"),
    # live：抹掉 writeHash 里 page 的写入。B4 点「下一页」后 state.page=2 而
    # hash 里没有 page ⇒ 转红。其余 B 段判据不受影响（B1 走 toQuery、
    # B2/B3/B5 的状态里 page 停在 1，按设计本来就不写），故要求**恰好**一条红。
    ("B4 反例：writeHash 不再写 page（运行时面）",
     "construction_agent/web/static/overview.js",
     "    if (state.page > 1) p.set('page', String(state.page));\n", "",
     "B4", "b", True),
]


def drift_test() -> int:
    print("── E 段 --drift-test：往真实源码注入漂移，断言退出码转 1 ──")
    import contextlib
    import io
    bad = 0
    for item in DRIFTS:
        why, rel, old, new, want = item[:5]
        runner = item[5] if len(item) > 5 else "a"
        exact = item[6] if len(item) > 6 else False
        p = ROOT / rel
        src = p.read_text(encoding="utf-8")
        n = src.count(old)
        if n != 1:
            print(f"  ✗ {why}：锚点命中 {n} 次（须 1）——锚点写错＝用例根本没跑")
            bad += 1
            continue
        try:
            p.write_text(src.replace(old, new, 1), encoding="utf-8")
            global FAILS
            saved, FAILS = FAILS, []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mm = measure()
                if runner == "b":
                    part_b(mm, argparse.Namespace(port=PORT))
                else:
                    part_a(mm)
            reds = list(FAILS)
            FAILS = saved
            hit = [r for r in reds if r.startswith(want)]
            if not hit:
                print(f"  ✗ {why} → 未转红（红的集合 {reds}）")
                bad += 1
            elif exact and len(reds) != len(hit):
                print(f"  ✗ {why} → 转红 {hit}，但**还多红了 {len(reds) - len(hit)} 条**"
                      f"（须恰好 {len(hit)} 条，否则归因不唯一）：{reds}")
                bad += 1
            else:
                print(f"  ✓ {why} → 转红 {hit}"
                      + ("（恰好）" if exact else ""))
        finally:
            p.write_text(src, encoding="utf-8")
    print(("✓ drift-test 全部通过" if not bad else f"✗ drift-test {bad} 条失败"))
    return 1 if bad else 0


# ══════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--static-only", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--drift-test", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.drift_test:
        return drift_test()

    point_at(main_db_path(), main_repo_root() / "data" / "staging.db")
    print("§2.5.99 概览页筛选状态机（hash ↔ state ↔ 请求）")
    m = measure()
    part_a(m)
    part_c(m)
    if not args.static_only:
        part_b(m, args)
    part_d(m)

    print()
    if FAILS:
        print(f"✗ 未通过 {len(FAILS)} 条：")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
