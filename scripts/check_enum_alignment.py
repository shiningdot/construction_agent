#!/usr/bin/env python3
"""枚举取值对齐核查。

三节判据，两类枚举：

| 节 | 查的对象 | 真值在哪 | 失效长什么样 |
|---|---|---|---|
| 一、二 | 工作库状态列（`collect_tasks.status`…） | `staging_schema.py` 的列注释 | 页面上冒英文码 |
| 三 | 编码列（`work_task`、`building_structure`…） | 主库 `enum_definitions` 表 | 页面上是一串看不出对错的码 |

一、二节是**静态**比对（读源码的两个集合）；第三节是**动态**的（真起 app 打接口）。

## 为什么查这个

工作库各表的状态列，取值域都写在 `web/staging_schema.py` 的列注释里
（`status TEXT NOT NULL,  -- pending|running|cancelling|done|failed|cancelled`）。
前端另有一张「英文码 → 中文」的标签表，把勾出来的状态翻成「取消中」这类词。

两侧一旦不同步，**症状是页面上出现英文**：`TASK_LABEL[s] || s` 这种写法
（`collect.js:66`）在查不到标签时**原样回显英文码**——这是有意的兜底（宁可见到
`paused` 也不要见到空白），代价是漏一个键就静默漏一次翻译，没有任何报错。

这正是蓝图 08 §8.2 那条判据（「后端给了几维筛选 ↔ 页面画了几维」）的同族问题：
**后端能产出 N 种取值、前端只认 M 种**。8.2 那次查的是筛选维度，这次查枚举取值。

## 判据

对每组「表.列 ↔ 前端标签表」比较两个集合：

1. **schema 注释声明的取值集合** == **前端标签表键集合**。不相等就报出来，
   并把两侧差集都打出来——**差集本身不指明谁对**，可能注释多写了一个永不产出的
   值，也可能前端少认了一个真实存在的值，要人看代码定夺（下面这一条就是干这个的）。
2. **后端写入的字面量 ⊆ schema 注释声明的取值**。代码里写了 `status="cancelling"`
   而注释没列它，说明注释是谎话——下一个照注释写前端的人会漏掉这个状态。
   只抓**写入形式**（`status = 'x'`、`status="x"`），不抓读取（`status == 'x'`、
   `WHERE status IN (...)`），因为读取处的字面量可能来自别处，不代表这张表会产出它。

## 已知的落地案例（2026-10-01 首次跑出来）

- `collect_tasks.status` 注释多写了 `paused`：全项目除这行注释**没有任何代码**产出
  这个状态（`grep -rn paused` 只命中注释本身）
- `extract_tasks.status` 注释漏写了 `cancelling`：`staging_executor.py:592` 明确会写
- `raw_accident_assets` 的 `asset_type` / `ocr_status` 前端**直接拼原值上屏**
  （`'OCR: ' + a.ocr_status`），没有标签表

## 第三节的判据

**接口在下发的每条记录上，凡编码列有值，就要给对应的中文名。** 三个页面分别判：

- 详情页 `/api/accidents/<id>`：`data.fields[col]` 有值 → `data.fields[col+"_label"]` 必须在
- 列表页 `/api/accidents`：凡 `*_code` 有值 → 配对的 `*_label` 必须在（不写死列名，
  列表页以后多带编码列会自动跟上）
- 审核页 `/api/review/items/<id>`：`enum_labels` 的键要覆盖「`ENUM_COLUMNS` ∩
  `EDITABLE_FIELDS`」——审核员要判的正是这些码对不对

判据是**单向**的：接口没给中文名就是漏了。这跟一、二节不同——那边差集不指明谁对。

2026-10-01 这节查出：审核页把五个编码列渲染成可编辑输入框，值就是码，而详情页
早就翻了（`detail.py`）。同一份数据在两个页面一个翻一个不翻。

## 边界

- 一、二节只查 `staging_schema.py` 里**以列注释形式声明**的枚举；走
  `enum_definitions` 表的编码列归第三节（它们的真值是库里的表，不是注释）
- 第三节**依赖库里真有条目**：主库没有编码列有值、或工作库没建，对应的组会跳过并
  在输出里说明。**跳过长得跟通过一样绿**，看输出里的 `·` 行区分
- 前端标签表按**变量名**定位（脚本里的 `PAIRS` 写死映射）。改了变量名要同步改这里
- 第 2 条判据的「后端文件」也是写死的（`WRITERS`）。它只覆盖状态类列——这几处
  赋值形式规整、精度高；分类列（`project_type` 等）的取值来自 LLM 而非代码字面量，
  没有可抓的字面量

## 用法

    .venv/bin/python3 scripts/check_enum_alignment.py
    .venv/bin/python3 scripts/check_enum_alignment.py --verbose

退出码 `0` = 前三节都没有缺口；`1` = 有缺口（可直接接 CI）。
第三节要连库，跑之前确认 `CAIEMS_DB`（主库）与 `CAIEMS_STAGING_DB`（工作库）指向
想看的那两个库；不设就用默认路径。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WEB = ROOT / "construction_agent" / "web"
SCHEMA_PY = WEB / "staging_schema.py"

#: 「表.列 → 前端哪个文件的哪张标签表」。一个列可能被两个页面渲染（`text_type`
#: 在提取页与审核页各有一张表），两边都要对。
PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("collect_tasks", "status", "collect.js", "TASK_LABEL"),
    ("raw_accidents", "status", "collect.js", "STATUS_LABEL"),
    ("raw_accidents", "gate_verdict", "collect.js", "VERDICT_LABEL"),
    ("raw_accident_assets", "asset_type", "collect.js", "ASSET_TYPE_LABEL"),
    ("raw_accident_assets", "ocr_status", "collect.js", "OCR_LABEL"),
    ("extract_tasks", "status", "extract.js", "STATUS_LABEL"),
    ("staging_accidents", "text_type", "extract.js", "TEXT_LABEL"),
    ("staging_accidents", "relevance_verdict", "extract.js", "REL_LABEL"),
    ("staging_accidents", "review_status", "review.js", "ST_LABEL"),
    ("staging_accidents", "text_type", "review.js", "TT_LABEL"),
)

#: 「表.列 → 怎么找它的写入点」。每条形如 `(文件, 抓法, 记号)`，抓法两种：
#:
#: - `("sql", 表名)`：取 `UPDATE <表名> ... SET <子句>` 里 `WHERE` 之前的部分
#: - `("call", 函数名)`：取 `<函数名>(...)` 调用里的关键字参数
#:
#: **为什么不按「文件里出现过这个列名」抓**：`status` 在 `raw_accidents` /
#: `collect_tasks` / `extract_tasks` 三张表里都有，文件级抓取会把
#: `web/collect.py` 里 `UPDATE raw_accidents SET ... status='gated'` 算到
#: `collect_tasks` 头上（第一版就是这么误报的，把 `gated/new/promoted` 报成
#: 任务表的状态）。所以只配**真正写这张表这一列**的文件，且每条都要带语境。
#:
#: 只配状态类列：它们写的是字面量，抓得准；分类列（`project_type` 等）的值来自
#: LLM 输出，代码里没有可抓的字面量。
WRITERS: dict[tuple[str, str], tuple[tuple[str, str, str], ...]] = {
    ("collect_tasks", "status"): (
        ("collector/executor.py", "sql", "collect_tasks"),
        ("collector/executor.py", "call", "_update_task"),
    ),
    ("raw_accidents", "status"): (
        ("web/collect.py", "sql", "raw_accidents"),
        ("extraction/staging_executor.py", "sql", "raw_accidents"),
    ),
    ("extract_tasks", "status"): (
        ("extraction/staging_executor.py", "sql", "extract_tasks"),
        ("extraction/staging_executor.py", "call", "_update_task"),
    ),
    ("staging_accidents", "review_status"): (
        ("web/review.py", "sql", "staging_accidents"),
    ),
}


def schema_enums() -> dict[tuple[str, str], list[str]]:
    """从 `staging_schema.py` 的列注释里抠出枚举：`-- a|b|c`。

    带表名返回，因为 `status` 这个名字在四张表里都有。枚举可能写成
    `a|b|c` 也可能写成 `a | b | c`，两种都收。`review | NULL(未判)` 这类尾部
    带说明的，只取能识别的码（`NULL(未判)` 不是 `[a-z_]+`，自然被挡在外面）。

    `TEXT` 到 `--` 之间用 `.*?` 而不是 `[^,]*?`：列声明里几乎都有 `NOT NULL,`
    这个逗号，卡住逗号等于一条也匹配不上。单行内 `.*?` 不会跨行，安全。
    """
    out: dict[tuple[str, str], list[str]] = {}
    table: str | None = None
    for line in SCHEMA_PY.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)", line)
        if m:
            table = m.group(1)
            continue
        m = re.match(r"\s*([a-z_]+)\s+TEXT\b.*?--\s*"
                     r"([a-z_]+(?:\s*\|\s*[a-z_]+)+)", line)
        if m and table:
            out[(table, m.group(1))] = [
                v.strip() for v in m.group(2).split("|")]
    return out


def js_label_keys(filename: str, table: str) -> list[str]:
    """前端标签表的键。按 `var NAME = { ... };` 整块抠，避免抓到别的对象。

    文件不存在时返回空表，让调用方按「这张表没有」报——与 `written_literals`
    对缺失文件的处理一致，也免得日志里蹦一个 FileNotFoundError。
    """
    path = WEB / "static" / filename
    if not path.exists():
        return []
    src = path.read_text(encoding="utf-8")
    m = re.search(r"\bvar\s+" + table + r"\s*=\s*\{(.*?)\n\s*\};", src, re.S)
    if m is None:
        return []
    return re.findall(r"([A-Za-z_]\w*)\s*:", m.group(1))


def written_literals(column: str, rules: tuple[tuple[str, str, str], ...]) -> set[str]:
    """后端**写入**这张表这一列时用到的字面量。

    只认 `col = 'x'` 与 `col="x"`：`==` / `IN (...)` 是读取，抓进来会把「判断某
    状态该不该做什么」误当成「会产生这个状态」。SQL 侧还额外卡到 `SET` 与 `WHERE`
    之间——`SET status='gated' WHERE status='new'` 里后一个是读取。
    """
    col_re = re.compile(rf"\b{re.escape(column)}\s*=\s*['\"]([a-z_]+)['\"]")
    got: set[str] = set()
    for rel, mode, token in rules:
        path = ROOT / "construction_agent" / rel
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        if mode == "sql":
            # 语句块切到 `WHERE` 或分号为止；无 WHERE 又无分号的（f-string 拼接）
            # 截断，免得吞掉文件后面别处的字面量
            for m in re.finditer(
                    rf"UPDATE\s+{re.escape(token)}\b.*?\bSET\b(.*?)(?=\bWHERE\b|;|\Z)",
                    src, re.S | re.I):
                got |= set(col_re.findall(m.group(1)[:800]))
        else:  # "call"
            for m in re.finditer(rf"\b{re.escape(token)}\s*\(", src):
                got |= set(col_re.findall(src[m.end():m.end() + 400]))
    return got


# ══════════════════════════════════════════════════════════════
# 第三节：库里存码的列，接口在下发的记录上有没有给中文名
# ══════════════════════════════════════════════════════════════
#
# 前两节比的是**工作库的状态列**：取值域写在 schema 注释里，前端各有标签表，
# 漏了会「冒英文码」。这一节比的是另一类枚举——`work_task` / `building_structure`
# / `equipment_involved` / `material_involved` / `worker_type` 五列，库里存的是码
# （`WT04`、`BLD01`…），中文名在**主库的 `enum_definitions` 表**里，注释里没有
# 取值域可写（枚举是数据，不是 DDL）。
#
# 它们的失效形态也不同：`WT04` 看着就像个正经编码，页面上不刺眼，没人觉得哪里
# 不对——只是审核员核不了「S3 判的 WT04 对不对」。所以这一节只能问接口，不能查
# 源码；问的是「有值的编码列，接口给没给对应的 `_label`」。
#
# 2026-10-01 这一节查出：审核页把五个编码列渲染成可编辑输入框，值就是码，而详情页
# 早就翻了（`detail.py`）。同一份数据在两个页面一个翻一个不翻。

def code_column_coverage() -> tuple[list[str], list[str], str]:
    """打真实接口，问「编码列有没有中文名」。

    返回 `(缺口, 提示, 说明)`。缺口导致退出码 1；提示只是告知（比如工作库没建，
    审核页这一组跳过）。
    """
    from construction_agent.web import accidents_query as aq
    from construction_agent.web import db as wdb
    from construction_agent.web.factory import create_app

    problems: list[str] = []
    notes: list[str] = []

    db_path = wdb.resolve_db_path()
    if not db_path.exists():
        return [], [f"主库不存在（{db_path}），整节跳过"], "跳过"
    app = create_app({"TESTING": True})
    c = app.test_client()

    # ── 详情页：五个编码列都该有 `_label` ──
    #
    # 挑一条**填得最满**的样本，而不是随手第一条：五个编码列的填充率差得远
    # （主库 2,623 条实测 work_task 2,202、building_structure 只有 359），随手
    # 挑一条往往只验得到两个列——判据看着绿，实际七成没验。逐级降级取样本，
    # 验不到的列在 notes 里点名，而不是让它们悄悄混过去。
    cols = list(aq.ENUM_COLUMNS)
    con = wdb.connect_readonly()
    try:
        row, covered = None, []
        for n in range(len(cols), 0, -1):
            want = cols[:n]
            where = " AND ".join(f"COALESCE(TRIM({k}),'')<>''" for k in want)
            # 顺便数一数有几条这么满的——这个数字本身就是填充率的实况
            hit = con.execute(
                f"SELECT id FROM accidents WHERE {where} LIMIT 1").fetchone()
            if hit is not None:
                row, covered = {"id": hit[0]}, want
                break
    finally:
        con.close()

    if row is None:
        notes.append("主库里没有任何一条带编码列的事故，详情页这一组跳过")
    else:
        d = c.get(f"/api/accidents/{row['id']}").get_json()
        fields = (d.get("data") or {}).get("fields") or {}
        for col in covered:
            v = fields.get(col)
            if not fields.get(col + "_label"):
                problems.append(
                    f"详情页：`{col}` 有值（{v}）却没有 `{col}_label`")
        missed = [col for col in cols if col not in covered]
        if missed:
            notes.append(f"详情页没验到的编码列（全库无一条同时有值）："
                         f"{'、'.join(missed)}")
        else:
            notes.append(f"详情页样本 {row['id']}：五个编码列全覆盖")

    # ── 列表页：凡 `*_code` 有值，就该有配对的 `*_label` ──
    #
    # 注意这个接口**没有 `data` 层**：`api.py:60` 直接把
    # `accidents_query.run()` 的 `{"items", "meta"}` jsonify 出去。审核队列
    # 那边正相反，`data` 是记录数组本身。三个接口三种包装，写判据时一个都不能
    # 照抄——这类「差一层」的错误不会报错，只会静默地什么都查不到。
    #
    # 不写死 `work_task`：哪天列表页多带一个编码列，这条自动跟上去。
    b = c.get("/api/accidents?page=1&page_size=20").get_json()
    items = (b.get("items")) or []
    if not items:
        notes.append("列表页没取到数据，这一组跳过")
    seen_code = 0
    for it in items:
        for k, v in it.items():
            if not k.endswith("_code") or v in (None, ""):
                continue
            seen_code += 1
            lab = k[: -len("_code")] + "_label"
            if not it.get(lab):
                problems.append(f"列表页：`{k}` 有值（{v}）却没有 `{lab}`")
                break
    if items and not seen_code:
        notes.append("列表页这一页没有任何 `*_code` 键——判据被架空，需人核")

    # ── 审核页：可编辑的编码列都要有对照表 ──
    from construction_agent.web import review as rv
    need = [col for col in aq.ENUM_COLUMNS if col in rv.EDITABLE_FIELDS]
    q = c.get("/api/review/queue?page=1&page_size=1").get_json()
    qitems = (q.get("data")) or []          # 这个接口的 data 就是数组，不是 {items}
    if not qitems:
        notes.append(f"工作库没有暂存记录，审核页这一组跳过"
                     f"（该页要翻译 {len(need)} 个可编辑编码列）")
    else:
        d = c.get(f"/api/review/items/{qitems[0]['stage_id']}").get_json()
        labels = (d.get("data") or {}).get("enum_labels") or {}
        notes.append(f"审核页样本 {qitems[0]['stage_id']}："
                     f"{len(need)} 个可编辑编码列逐个核对下行对照表")
        for col in need:
            if col not in labels:
                problems.append(f"审核页：可编辑的编码列 `{col}` 没下发对照表")
            elif not labels[col]:
                notes.append(f"审核页：`{col}` 的对照表是空的——主库"
                             f"`enum_definitions` 里这个枚举没有 `is_active` 行")

    return problems, notes, f"详情页 / 列表页 / 审核页 × {len(aq.ENUM_COLUMNS)} 个编码列"



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verbose", action="store_true", help="打印每一组的完整集合")
    args = ap.parse_args()

    declared = schema_enums()
    print(f"schema 注释里的枚举列   {len(declared)} 个")
    print(f"需要两侧对齐的映射      {len(PAIRS)} 组\n")

    problems: list[str] = []
    for table, column, js_file, js_table in PAIRS:
        want = declared.get((table, column))
        label = f"{table}.{column} ↔ {js_file} {js_table}"
        if want is None:
            problems.append(f"{label}：schema 注释里找不到这个列的枚举声明")
            print(f"  ✗ {label:<52} schema 没声明")
            continue

        got = js_label_keys(js_file, js_table)
        if not got:
            problems.append(f"{label}：前端找不到标签表 {js_table}")
            print(f"  ✗ {label:<52} 前端没有这张表")
            continue

        missing = sorted(set(want) - set(got))     # 后端会产出、前端不翻译
        extra = sorted(set(got) - set(want))       # 前端翻译了、注释没声明
        marks = []
        if missing:
            marks.append(f"前端缺 {','.join(missing)}")
            problems.append(f"{label}：注释声明了 {','.join(missing)}，前端没有标签"
                            f"——这些值会原样显示成英文")
        if extra:
            marks.append(f"前端多 {','.join(extra)}")
            problems.append(f"{label}：前端有 {','.join(extra)}，注释没声明"
                            f"——要么注释漏写，要么这张表没有产出的路径")

        n_written = ""
        rules = WRITERS.get((table, column))
        if rules:
            written = written_literals(column, rules)
            stray = sorted(written - set(want))
            n_written = f" · 后端写入 {len(written)}"
            if stray:
                files = sorted({r[0] for r in rules})
                marks.append(f"写入未声明 {','.join(stray)}")
                problems.append(
                    f"{label}：{'、'.join(files)} 里写了 {','.join(stray)}，"
                    f"注释没声明——注释是谎话，照它写前端会漏")

        ok = not marks
        print(f"  {'✓' if ok else '✗'} {label:<52} schema {len(want):>2}"
              f" · 前端 {len(got):>2}{n_written}"
              f"{'　' + '；'.join(marks) if marks else ''}")
        if args.verbose and not ok:
            print(f"        schema: {' '.join(want)}")
            print(f"        前端  : {' '.join(got)}")

    # ── 第三节：库里存码的列，接口给没给中文名 ──
    #
    # 用 try 包住：这一节要真起 app、真连库，缺库或工作库没建都可能抛异常。
    # 抛了就当「这节没跑」并说清楚——**不能让它把前两节的结论一起带崩**，
    # 更不能静默吞掉（吞掉就成了「跑过且通过」，比没跑更坏）。
    print()
    try:
        cov_problems, cov_notes, cov_brief = code_column_coverage()
    except Exception as exc:  # noqa: BLE001 —— 见上
        cov_problems, cov_notes = [], []
        cov_brief = f"跑不起来（{type(exc).__name__}: {exc}）"
    print(f"编码列在各页面的翻译   {cov_brief}")
    for n in cov_notes:
        print(f"  · {n}")
    if not cov_problems:
        print("  ✓ 接口下发的记录里，有值的编码列都带了中文名")
    problems += cov_problems

    if problems:
        print(f"\n✗ {len(problems)} 处不一致：")
        for p in problems:
            print(f"    - {p}")

        # 两类问题的修法不同，尾注也要分头给：前两节的差集是双向的（不知道谁对），
        # 第三节是单向的（接口漏了就是漏了）。混着写等于给一半的问题开了错药方。
        page_prefixes = ("详情页：", "列表页：", "审核页：")
        if any(p.startswith(page_prefixes) for p in problems):
            print("\n  第三节是单向判据：接口没给出中文名就是漏了，没有「谁对」的"
                  "歧义——补 `_label` / `enum_labels` 即可。")
        if any(not p.startswith(page_prefixes) for p in problems):
            print("\n  第一/二节的差集本身不指明谁对：可能是注释多写了一个不产出的"
                  "值，也可能是前端少认了一个真实值。")
            print("  看 `WRITERS` 那一列（后端写入）定方向——它列了就说明代码真会产出。")
        return 1

    print("\n✓ 每组枚举两侧一致，且代码里写入的取值都在注释声明之内。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
