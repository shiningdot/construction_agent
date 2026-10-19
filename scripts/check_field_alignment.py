#!/usr/bin/env python3
"""字段级对齐核查：前端读的属性 ↔ 后端返回的字段，外加反向的「表列 ↔ 详情分档」。

前几层验收判的都是「通不通」——路由对不对得上、页面打不打得开、渲染有没有
崩。它们都看不见一类静默错配：**后端返回 `death_count`、前端读 `deaths`**。
页面不报错、控制台干净、数字对账也全对，只是那一格永远显示「—」。

这个脚本把两侧的**名字**拿来对账：

- 后端侧：起 app 打真实请求，把 JSON 递归展平成字段路径（`items.0.death_count`）
- 前端侧：读 `static/*.js` 与 `templates/*.html`，抽出全部 `.属性` 读取

## 判据

前端读的每个名字，落到下面任何一层就放过：

1. **后端运行时返回过** —— 最强证据，名字确实在 JSON 里
2. **JS 语言 / DOM / 本项目 DOM 工具白名单** —— 前端本来就不读后端的地方：
   `.length`、`.classList`、`.dataset`，以及 `dom.js` 导出的 `el` / `num` / `blank`
3. **前端自己构造的键**（`FRONTEND_LOCAL`）—— 四五个，每个都注明了出处
4. **后端源码里作为标识符出现过** —— 覆盖「库是空的、端点上没有元素可展」的那批
   （采集 / 提取 / 审核三组的表在空工作库里是 0 行，返回 `[]` 展不出任何字段）

四层都落不到的，报出来人工看。**误报可接受、漏报不可接受**——所以第 4 层刻意
放宽（源码里提到过就算），宁可放过。

`--verbose` 会把每一层放过的名字全打出来，可以翻。

## 反方向那一半：`accidents` 的列 ↔ 详情页四档

上面那一半只查「前端读了什么」。缺口还有一种形态：**后端表里有值、页面上
从未出现**——前端不会去读一个后端没下发的名字，所以它读不出来，也就永远不会
被上面那一半抓到。

这一半查详情接口：`accidents` 的每一列，要么进 `CONTENT_TIERS` 四档、要么进
`META_FIELDS`、要么在 `DETAIL_NOT_SHOWN` 里写明为什么不展示。三者都不是就是缺口。

查出来过真问题：`project_type`、`construction_phase`、`collapse_subtype` 三列
有值率 100%，列表页有、概览页有筛选，唯独详情页看不到——蓝图 05 §2.1 的分档表
（43 个列名）漏列了它们，实现照抄分档表。**缺口是从文档抄进代码的**，所以这个
核查同时也盯着「分档表列全了没有」。

## 两个已知的假信号（写在这里免得下次又查一遍）

- **注释里的文件名**。`/* … overview.js 里还有一份… */` 会被当成 `.js` 属性读取。
  先剥注释。
- **赋全局那几行**。`global.num = num` 里的 `.num` 不是读取，是定义。排除掉
  「点号后面跟单个等号」的位置（`==` 不算，那是比较）。

## 用法

    .venv/bin/python3 scripts/check_field_alignment.py
    .venv/bin/python3 scripts/check_field_alignment.py --verbose

退出码 `0` = 没有未归类的读取、也没有未收进详情页的列；`1` = 两者任一存在（可直接接 CI）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402

WEB = ROOT / "construction_agent" / "web"

#: `accidents` 里详情接口**有意不展示**的列。每条都得有理由——没有理由的就是缺口。
DETAIL_NOT_SHOWN = {
    "raw_text": "不进 `fields`，在响应顶层单独下发（前端读它渲染原文）",
    "work_task_enum": "1.0 残留列，703 条编码在枚举表里根本不存在（蓝图 05 §2.3）",
}


def detail_tier_gaps(main_db: str) -> list[str]:
    """`accidents` 的列里，详情页「内容四档 + 元数据区」一个都没收的。

    这是**反方向**的一查。上面查的是「前端读了、后端没有的名字」，这里查
    「后端表里有、页面上永远不显示的值」。两类缺口的症状一模一样：不报错、
    不空白、控制台干净，只是那几列数据从来没人看见。

    这个方向查出过真问题：`project_type` 与 `construction_phase` 有值率 100%、
    列表页有这两列、概览页有这两维筛选，但蓝图 05 §2.1 的分档表（43 个列名）
    漏了它们——实现照抄分档表，详情页就一直看不到这两列。

    「有没有值」不参与判断：一列只要有值就该有归宿，至于进了哪一档、那一档
    折不折叠，是分档表的事。
    """
    import sqlite3

    # 延迟 import：本脚本其余部分只读文件与库，不必拉起整个 web 包
    from construction_agent.web.detail import CONTENT_TIERS, META_FIELDS

    covered = {n for _, _, names in CONTENT_TIERS for n in names} | set(META_FIELDS)
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(accidents)")]
    finally:
        con.close()
    return [c for c in cols if c not in covered and c not in DETAIL_NOT_SHOWN]

#: JS 语言与 DOM 自带的名字，以及本项目 `dom.js` 导出的工具。
#: 判断依据是「这个名字在前端是读浏览器对象，不是读后端 JSON」。
JS_AND_DOM = {
    # ── 语言内置 ──
    "length", "push", "pop", "map", "filter", "find", "foreach", "reduce",
    "join", "split", "slice", "splice", "concat", "includes", "indexof",
    "keys", "values", "entries", "sort", "reverse", "some", "every", "flat",
    "replace", "trim", "tolowercase", "touppercase", "substring", "substr",
    "padstart", "padend", "startswith", "endswith", "match", "test", "exec",
    "then", "catch", "finally", "stringify", "parse", "assign", "freeze",
    "isarray", "round", "floor", "ceil", "abs", "random", "tolocalestring",
    "tofixed", "toisostring", "getfullyear", "getmonth", "getdate", "gettime",
    # ── DOM / BOM ──
    "style", "classlist", "classname", "textcontent", "innerhtml", "innertext",
    "value", "checked", "disabled", "selected", "dataset", "children",
    "parentnode", "parentelement", "firstchild", "lastelementchild",
    "nextelementsibling", "previouselementsibling", "queryselector",
    "queryselectorall", "getelementbyid", "getattribute", "setattribute",
    "removeattribute", "hasattribute", "createelement", "createtextnode",
    "appendchild", "removechild", "insertbefore", "closest", "matches",
    "scrolltop", "scrollheight", "clientheight", "offsetheight", "offsettop",
    "offsetwidth", "clientwidth", "getboundingclientrect", "preventdefault",
    "stoppropagation", "currenttarget", "location", "href", "hash", "search",
    "hostname", "protocol", "reload", "naturalwidth", "complete", "files",
    "reset", "submit", "focus", "blur", "click", "onclick", "onchange",
    "oninput", "onsubmit", "body", "head", "searchparams", "append",
    "settimeout", "setinterval", "clearinterval", "cleartimeout", "nodevalue",
    "nodename", "tagname", "text", "html", "json", "blob", "formdata",
    "status", "statustext", "stack", "cause", "options", "pathname",
    "prototype", "remove", "toggle", "hidden", "open", "history", "win",
    "name", "type", "id", "src", "alt", "title", "url", "width", "height",
    # ── 本项目 dom.js 导出的全局（el/num/blank/clear/$/fetchJson）──
    "el", "num", "blank", "clear", "fetchjson", "loaddbchip",
    # ── 数据属性选择器 `[data-num]` 这类在前端内部的用法 ──
    "data",
}

#: 前端自己构造的键，不是后端字段。
#:
#: 这组要**尽量小**：加进来之前先确认它不是后端漏返的字段。加的时候注明出处，
#: 免得下一个人（或下一次的我）以为白名单可以随手加——`.dev_pct` 就是查过才进来的：
#: 它由 `u.deviation_pct` 改名而来，`extract.py:207` 后端确实返回 `deviation_pct`。
FRONTEND_LOCAL = {
    "on",        # overview.js 的 el() 内联副本：opts.on 是「事件名 → 回调」表
    "accent",    # overview.js 统计卡：c.accent 决定要不要加 is-accent
    "color",     # overview.js 图表：cfg.color(r) 是取色回调
    "dev_pct",   # extract.js usage()：后端 deviation_pct 的前端别名
}


def strip_noise(src: str) -> str:
    """去掉注释与字符串字面量。

    不去掉的话，`/* … overview.js … */` 会送进来一个 `.js`，`'xxx'` 里的点号
    也会。模板串要按跨行处理——它常用来拼 HTML，里面全是属性名。
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)              # 块注释
    src = re.sub(r"//[^\n]*", " ", src)                           # 行注释
    src = re.sub(r"`(?:\\.|[^`\\])*`", "''", src, flags=re.S)     # 模板串
    src = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", src)               # 单引号串
    src = re.sub(r'"(?:\\.|[^"\\\n])*"', "''", src)               # 双引号串
    return src


def attrs_in(src: str) -> set[str]:
    """从一份（已剥噪音的）JS/HTML 里抽出**读取**的属性名。

    两类要排除，都是踩过的坑：

    - **方法调用** `.map(` / `.then(` —— 那是调函数，不是取字段
    - **赋值左侧** `global.num = num` —— 那是定义。要排除单个等号，但 `==` 不能
      跟着被排掉：`if (a.count == b.count)` 里的 `a.count` 是货真价实的读取
    """
    got: set[str] = set()
    for m in re.finditer(r"\.([a-z][a-z0-9_]{1,30})\b", src):
        rest = src[m.end():m.end() + 3]
        if rest.lstrip()[:1] == "(":
            continue
        if re.match(r"\s*=(?!=)", rest):
            continue
        got.add(m.group(1))
    return got


def frontend_attrs() -> dict[str, set[str]]:
    """每个前端文件里读到的属性名。"""
    out: dict[str, set[str]] = {}
    files = sorted((WEB / "static").glob("*.js")) + sorted((WEB / "templates").glob("*.html"))
    for f in files:
        out[f.relative_to(ROOT).as_posix()] = attrs_in(
            strip_noise(f.read_text(encoding="utf-8")))
    return out


def flatten(obj, prefix: str, out: set[str], depth: int = 0) -> set[str]:
    if depth > 8:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out.add(p)
            flatten(v, p, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:5]:                                # 元素结构一致，取几个够
            flatten(v, prefix, out, depth + 1)
    return out


def empty_arrays(obj, prefix: str = "", out: set[str] | None = None,
                 depth: int = 0) -> set[str]:
    """JSON 里所有**空数组**的位置。

    这才是「元素字段核不到」的准确判据。早先写成「这个端点没让自己的键总数
    增加」——那样 `/api/review/queue` 会被误判：它明明返回了 `total`、`counts`
    这些键，只是 `items` 是空的，而 `total` 在别的端点里已经见过。
    """
    if out is None:
        out = set()
    if depth > 8:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            empty_arrays(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    elif isinstance(obj, list):
        if not obj:
            out.add(prefix)
        else:
            for v in obj[:5]:
                empty_arrays(v, prefix, out, depth + 1)
    return out


def backend_fields(main_db: str, staging_db: str) -> tuple[set[str], list[tuple[str, list[str]]]]:
    """打真实请求拿后端字段路径。返回 (路径集合, [(端点, 空数组位置)])。"""
    os.environ["CAIEMS_DB"] = main_db
    os.environ["CAIEMS_STAGING_DB"] = staging_db
    # 图片根跟着主仓库走，否则 `media_root()` 指向 worktree 的 data/（没有图片目录）
    os.environ.setdefault("CAIEMS_DATA_DIR", str(main_repo_root() / "data"))

    import sqlite3
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    repl = {
        "accident_id": con.execute("SELECT id FROM accidents LIMIT 1").fetchone()[0],
        "code": con.execute("SELECT code FROM caics_causes LIMIT 1").fetchone()[0],
        "task_id": "1", "filename": "x", "path": "x", "kind": "images",
    }
    con.close()

    from construction_agent.web.factory import create_app
    app = create_app()
    client = app.test_client()

    paths: set[str] = set()
    holes: list[tuple[str, list[str]]] = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        if "GET" not in (rule.methods or ()) or str(rule).startswith("/static"):
            continue
        path = re.sub(r"<(?:[^:>]+:)?(\w+)>",
                      lambda m: str(repl.get(m.group(1), "1")), str(rule))
        if "/media/" in path:                            # 图片走文件系统，不是 JSON
            continue
        r = client.get(path)
        if r.status_code != 200 or not r.is_json:
            continue
        body = r.get_json()
        paths |= flatten(body, "", set())
        blanks = sorted(empty_arrays(body))
        if blanks:
            holes.append((str(rule), blanks))
    return paths, holes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--main-db", default=None)
    ap.add_argument("--staging-db", default=None)
    ap.add_argument("--verbose", action="store_true", help="打印放过的每一类")
    args = ap.parse_args()

    main_db = args.main_db or main_db_path()
    staging_db = args.staging_db or str(main_repo_root() / "data" / "caiems_staging.db")

    back_paths, holes = backend_fields(main_db, staging_db)
    back_leaves = {p.split(".")[-1] for p in back_paths}

    # 后端源码里出现过的标识符——兜底：库空时端点上没有元素可展
    back_src = " ".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (ROOT / "construction_agent").rglob("*.py"))

    front = frontend_attrs()
    all_front = set().union(*front.values()) if front else set()

    passed_runtime = {a for a in all_front if a in back_leaves}
    passed_local = {a for a in all_front if a in FRONTEND_LOCAL}
    passed_dom = {a for a in all_front if a in JS_AND_DOM}
    passed_src, unclassified = set(), set()
    for a in all_front:
        if a in passed_runtime or a in passed_dom or a in passed_local:
            continue
        if re.search(rf"\b{a}\b", back_src):
            passed_src.add(a)
        else:
            unclassified.add(a)

    print(f"后端字段路径   {len(back_paths):>4} 条（叶子名 {len(back_leaves)} 个）")
    print(f"前端属性读取   {len(all_front):>4} 个（来自 {len(front)} 个文件）")
    print(f"  ├ 后端运行时返回过   {len(passed_runtime):>4}")
    print(f"  ├ JS/DOM 白名单      {len(passed_dom):>4}")
    print(f"  ├ 前端内部构造       {len(passed_local):>4}  {' '.join(sorted(passed_local))}")
    print(f"  └ 后端源码里出现过   {len(passed_src):>4}")
    print(f"未归类               {len(unclassified):>4}")

    gaps = detail_tier_gaps(main_db)
    print(f"详情页未收列         {len(gaps):>4}  （`accidents` 的列不在四档 + 元数据区里）")
    if gaps:
        for c in gaps:
            print(f"    {c}")
        print("  这些列的值后端有、页面上永远不显示。要么补进 `detail.CONTENT_TIERS`，")
        print("  要么进 `DETAIL_NOT_SHOWN` 并写清为什么不展示。")

    if holes:
        print(f"\n⚠ {len(holes)} 个端点的数组是空的，元素字段无从验证：")
        for path, blanks in holes:
            print(f"    {path:<38} {' '.join(blanks)}")
        print("  列在下面的名字**没有**被这一条兜住——它们能过是因为别处见过，")
        print("  不是因为在元素上核过。库里有数据后重跑才算数。")

    if args.verbose:
        for label, s in (("后端运行时返回过", passed_runtime),
                         ("JS/DOM 白名单", passed_dom),
                         ("前端内部构造", passed_local),
                         ("后端源码里出现过", passed_src)):
            print(f"\n── {label}（{len(s)}）\n   {' '.join(sorted(s))}")

    if unclassified:
        print(f"\n✗ {len(unclassified)} 个前端在读、后端三层都不认识：")
        for a in sorted(unclassified):
            where = [f.split("/")[-1] for f, s in front.items() if a in s]
            print(f"    {a:<24} 出现在 {', '.join(where)}")
        print("\n  逐个看：要么是后端该返回而没返回（真错配），"
              "要么该补进 JS_AND_DOM 白名单。")

    if unclassified or gaps:
        return 1

    print("\n✓ 前端读的每个名字都能归到「后端返回」「DOM 自带」「前端内部」「后端源码」"
          "四者之一；")
    print("  `accidents` 的每一列也都有归宿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
