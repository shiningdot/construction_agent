#!/usr/bin/env python3
"""端到端对齐核查：路由维度 + 页面维度。

用法：
    .venv/bin/python3 scripts/check_web_alignment.py

项目要求「每个 web 部分要实现端到端的完美对齐」，落到代码上有四件不能有的事。

**路由维度**（静态分析，不需要数据库）

- **孤儿端点**：后端实现了、前端从不调用——数据层有、展示层缺，用户够不到
- **幽灵调用**：前端在请求、后端没有这条路由——点了就 404

**页面维度**（起 app 打真实请求）

- 打不开的页面（蓝图 07 验收第 1 条：全部页面 200 无 500）
- 没有任何入口的页面——从哪儿都点不到，它等于不存在

页面维度必须真跑起来才判得了：导航写的是 `url_for('views.xxx_page')`，源码里
没有 `/collect` 这样的字面量，静态分析在这里什么都找不到。同理，列表页的详情
链接是 JS 拼的（`href: '/accident/' + id`），只数服务端 HTML 里的 href 会把详情页
误判成「谁也链不到」——所以入口要算两路：渲染后的 href ∪ 该页加载的 JS 里的
路径字面量。

## 为什么不用「静态段顺序查找」

第一版判据是把路由的静态段按序在前端源码里找，段落在别处偶然出现就算
「有调用」。`/start`、`/cancel`、`/rerun` 这类后缀在按钮文字、状态映射表里
到处都是，于是 42 条路由报出 0 条孤儿——**而真有一条**（`/accidents/<id>/images`），
最后是手工 grep 才挖出来的。判据太松，结论就是假的。

这里改用**路径形状匹配**：变量段与 query string 在两侧都归一成 `<>`，再做集合
运算。难点在前端拼接——`'/api/extract/tasks/' + t.task_id + '/start'` 在源码里
是三个字面量夹两个表达式，所以要从 `/api` 那串起贪心地吃后续片段。

退出码：0 = 完全对齐；1 = 存在孤儿或幽灵（可直接接进 CI）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "construction_agent" / "web"

# 蓝图自带 url_prefix（各文件 Blueprint(...)），装饰器里写的是蓝图内相对路径。
# 比对必须用完整路径，否则 41 条会全部对不上，报出一大片假孤儿。
PREFIX = {"api_bp": "/api", "collect_bp": "/api/collect",
          "extract_bp": "/api/extract", "review_bp": "/api/review"}


def norm(p: str) -> str:
    """变量段与 query string 都抹平，只留路径形状。"""
    p = p.split("?")[0]                      # `/api/collect/raw?limit=30` → 去 query
    p = re.sub(r"<[^>]+>", "<>", p)          # Flask 变量段
    p = re.sub(r"<[^>]*>", "<>", p)          # 前端拼接占位
    return p.rstrip("/") or "/"


def collect_routes() -> dict[str, list[tuple[str, str, str]]]:
    routes: dict[str, list[tuple[str, str, str]]] = {}
    for f in sorted(WEB.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r'@(\w+)\.(get|post|put|delete)\(\s*"([^"]+)"', src):
            bp, method, path = m.group(1), m.group(2).upper(), m.group(3)
            if bp == "views_bp":
                continue                     # 页面路由由浏览器直接访问，不是 API
            full = PREFIX.get(bp, "") + path
            routes.setdefault(norm(full), []).append((method, f.name, full))
    return routes


# 拼接续接：` + <表达式> + '字面量'`。表达式只认常见的标识符/属性/调用形状，
# 免得把 `+ 1` 之类的无关运算也吃进来。
TAIL_MORE = re.compile(r"\s*\+\s*[A-Za-z_$][\w.$\[\]()]*\s*\+\s*(['\"])([^'\"]*)\1")
# 拼接收尾：` + <表达式>` 后面没再有字面量。只在路径以 `/` 结尾时补占位——
# `send('...tasks/' + t.task_id, 'DELETE')` 就是这种形态。
TAIL_END = re.compile(r"\s*\+\s*[A-Za-z_$][\w.$\[\]()]*")


def collect_calls() -> dict[str, list[str]]:
    files = sorted(list((WEB / "static").glob("*.js"))
                   + list((WEB / "templates").glob("*.html")))
    calls: dict[str, list[str]] = {}

    for f in files:
        text = f.read_text(encoding="utf-8")

        for m in re.finditer(r"['\"](/api/[^'\"]*)['\"]", text):
            path, pos = m.group(1), m.end()
            while True:
                t = TAIL_MORE.match(text, pos)
                if t:
                    path += "<>" + t.group(2)
                    pos = t.end()
                    continue
                t = TAIL_END.match(text, pos)
                if t and path.endswith("/"):
                    path += "<>"
                    pos = t.end()
                break
            line = text.count("\n", 0, m.start()) + 1
            calls.setdefault(norm(path), []).append(f"{f.name}:{line}")

        # 模板字符串写法：`/api/x/${y}`
        for m in re.finditer(r"`(/api/[^`]*)`", text):
            line = text.count("\n", 0, m.start()) + 1
            calls.setdefault(norm(re.sub(r"\$\{[^}]*\}", "<>", m.group(1))),
                             []).append(f"{f.name}:{line}(模板串)")
    return calls


def report_routes() -> list[str]:
    """路由维度。返回问题清单（空 = 对齐）。"""
    routes, calls = collect_routes(), collect_calls()
    print(f"后端 API 路由 {sum(len(v) for v in routes.values())} 条"
          f"（{len(routes)} 种路径形状）")
    print(f"前端调用点   {sum(len(v) for v in calls.values())} 处"
          f"（{len(calls)} 种路径形状）\n")

    orphans = sorted(set(routes) - set(calls))
    ghosts = sorted(set(calls) - set(routes))

    if orphans:
        print("✗ 后端有、前端无调用（孤儿端点）：")
        for p in orphans:
            for method, fname, raw in routes[p]:
                print(f"    {method:<6} {raw:<38} ({fname})")
    else:
        print("✓ 后端每条路由都有前端调用点")

    if ghosts:
        print("\n✗ 前端在调、后端无此路由（幽灵调用）：")
        for p in ghosts:
            print(f"    {p:<38} ← {', '.join(calls[p][:3])}")
    else:
        print("✓ 前端每个调用点都有后端路由")

    return [f"孤儿端点 {p}" for p in orphans] + [f"幽灵调用 {p}" for p in ghosts]


# ══════════════════════════════════════════════════════════════
# 页面维度
# ══════════════════════════════════════════════════════════════

# 导航链接是 `url_for('views.xxx_page')` 渲染出来的，源码里没有 `/collect`
# 这样的字面量——**页面维度必须起 app 打真实请求**，静态分析在这里无效。
JS_LITERAL = re.compile(r"""['"`](/[\w/-]+/)(?=['"`]|\s*\+)""")


def _normalize_link(href: str) -> str | None:
    h = href.split("#")[0].split("?")[0]
    if h.startswith("http") or h.startswith("/static") or not h.startswith("/"):
        return None
    h = h.rstrip("/") or "/"
    return "/accident/<accident_id>" if h.startswith("/accident/") else h


def report_pages() -> list[str]:
    """页面维度：可访问性 + 可达性。需要能连上最终库取一个真实 accident_id。"""
    sys.path.insert(0, str(ROOT))
    try:
        from construction_agent.web.factory import create_app
        app = create_app()
    except Exception as e:                    # noqa: BLE001
        print(f"（跳过页面维度：起不了 app —— {e}）")
        return []

    client = app.test_client()
    accident_id = None
    try:
        from construction_agent.web import db as rodb
        con = rodb.connect_readonly()
        try:
            row = con.execute("SELECT id FROM accidents LIMIT 1").fetchone()
            accident_id = row[0] if row else None
        finally:
            con.close()
    except Exception:                         # noqa: BLE001
        pass

    pages: list[tuple[str, str]] = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        path = str(rule)
        if (path.startswith(("/api", "/media", "/static"))
                or "GET" not in rule.methods):
            continue
        if "<accident_id>" in path and not accident_id:
            continue                          # 库里没数据，这一页没法打
        pages.append((path, path.replace("<accident_id>", accident_id or "")))

    print("\n── 页面维度 ──")
    problems: list[str] = []
    html_of: dict[str, str] = {}
    for raw, url in pages:
        r = client.get(url)
        ok = r.status_code == 200
        print(f"  {'✓' if ok else '✗'} {r.status_code}  {raw}")
        if not ok:
            problems.append(f"页面 {raw} 返回 {r.status_code}")
        else:
            html_of[raw] = r.get_data(as_text=True)

    # 可达性：入口 = 渲染后 HTML 的 href ∪ 该页加载的 JS 里的路径字面量。
    # 少了 JS 这一路，列表页的详情链接（`href: '/accident/' + id`）就看不见，
    # 详情页会被误判成「谁也链不到」。
    def outgoing(html: str) -> set[str]:
        outs = {_normalize_link(m.group(1))
                for m in re.finditer(r'href="([^"]+)"', html)}
        names = {m.group(1) for m in
                 re.finditer(r'<script[^>]+src="[^"]*/([\w.-]+\.js)"', html)}
        for name in names:
            f = WEB / "static" / name
            if f.exists():
                outs |= {_normalize_link(m.group(1) + "\x00")
                         for m in JS_LITERAL.finditer(
                             f.read_text(encoding="utf-8"))}
        return {o for o in outs if o}

    inbound: dict[str, set[str]] = {p: set() for p, _ in pages}
    for src, html in html_of.items():
        for dst in outgoing(html):
            if dst in inbound and dst != src:
                inbound[dst].add(src)

    for p, _ in pages:
        n = len(inbound[p])
        print(f"  {'✓' if n else '✗'} {n:>2} 个入口  {p}")
        if not n:
            problems.append(f"页面 {p} 没有任何入口")
    return problems


def main() -> int:
    problems = report_routes()
    print()
    problems += report_pages()

    if problems:
        print(f"\n✗ 共 {len(problems)} 处不对齐：")
        for p in problems:
            print(f"    - {p}")
        return 1
    print("\n两侧完全对齐（路由 + 页面）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
