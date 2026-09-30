#!/usr/bin/env python3
"""枚举取值对齐核查：schema 注释声明的取值 ↔ 前端标签表的键。

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

## 边界

- 只查 `staging_schema.py` 里**以列注释形式声明**的枚举。`work_task`
  （WT01–WT99）这类走 `enum_definitions` 表的编码不在此列——它们的真值是库里的
  表，不是注释
- 前端标签表按**变量名**定位（脚本里的 `PAIRS` 写死映射）。改了变量名要同步改这里
- 第 2 条判据的「后端文件」也是写死的（`WRITERS`）。它只覆盖状态类列——这几处
  赋值形式规整、精度高；分类列（`project_type` 等）的取值来自 LLM 而非代码字面量，
  没有可抓的字面量

## 用法

    .venv/bin/python3 scripts/check_enum_alignment.py
    .venv/bin/python3 scripts/check_enum_alignment.py --verbose

退出码 `0` = 每组两侧一致、且写入值都在注释里；`1` = 有不一致（可直接接 CI）。
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

    if problems:
        print(f"\n✗ {len(problems)} 处不一致：")
        for p in problems:
            print(f"    - {p}")
        print("\n  差集本身不指明谁对：可能是注释多写了一个不产出的值，也可能是前端"
              "少认了一个真实值。")
        print("  看 `WRITERS` 那一列（后端写入）定方向——它列了就说明代码真会产出。")
        return 1

    print("\n✓ 每组枚举两侧一致，且代码里写入的取值都在注释声明之内。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
