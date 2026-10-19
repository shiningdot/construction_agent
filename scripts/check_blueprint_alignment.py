#!/usr/bin/env python3
"""蓝图 ↔ 实现：文档规定的接口，与代码里真正注册的路由对不对得上。

`check_web_alignment.py` 管的是「路由 ↔ 前端调用」，`check_field_alignment.py`
管「前端读的名字 ↔ 后端返回的字段」。这一维问的是更前面的一环：**蓝图写的接口，
实现了吗？实现了的接口，写进蓝图了吗？**

## 判据：只有标题算「规定」

蓝图里出现 `/api/xxx` 的地方有两种，语义完全不同：

1. **接口设计章节的标题**——`### `GET /api/collect/sources``。这是**规格**，
   规定了要实现的接口形状。
2. **正文里的一笔带过**——可能是解释某个设计时举的例子，也可能是**在说旧实现
   怎么错**。例如蓝图 02 写「旧实现 `POST /api/process`（`web/app.py:326-350`）
   同步调用，40 秒的请求必然超时」，那是在陈述**已废弃**的东西；蓝图 06 写
   「原有一个 `GET /api/accidents/<id>/images` 供画廊单独刷新，2026-10-01 删除」，
   是在记录**删掉**了什么。

把正文一并算作「规定」，这两条就会报成幽灵接口——而它们恰恰是文档最该有的那种
交代。所以判据只取标题；正文提及单独列出来，并**用否定词过滤**（删除 / 旧实现 /
原有 / 已废弃 / 移除 / 不再），把「对照提及」与「漏写」分开。

反过来也一样：只按标题判，会漏掉「实现了但蓝图没写」的接口。这类不是文档瑕疵的
小事——蓝图是后续会话唯一的施工依据，没写进去的端点下一轮就可能被当成冗余删掉。
所以两个方向都报，两个方向都要求为 0。

## 实测（2026-10-01）

第一版只按标题判，报出幽灵 0 条、未写进蓝图 **6** 条：

    /api/extract/pending    页面②待提取队列的数据源，蓝图里只有正文描述
    /api/extract/estimate   确认弹层的预估，蓝图里只有一段 JSON 样例
    /api/review/history     并入流水，蓝图 03 只有 stats 没有 history
    /api/filters            筛选器可选值，蓝图 04 §2.4 讲了语义没立接口
    /api/health             库连通性自检，蓝图 00 完全没有
    /api/province/audit     省份归一审计，蓝图 04 正文提了一次

逐条读实现核实后补进对应蓝图（都是真实功能，不是实现多写），双向清零。
正文提及未注册的只剩上面那两条对照提及。

用法：
    .venv/bin/python3 scripts/check_blueprint_alignment.py

退出码：0 = 双向对齐；1 = 有缺口。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_web_alignment import collect_routes, norm  # noqa: E402

# 000–08 是蓝图；09 是核查记录、10 是待执行清单，它们提到路由是在**报告**，
# 不是在规定，混进来会把核查结论本身当成规格。
BLUEPRINT_MAX = 8

# 正文提及路径时，同一行出现这些词说明是在说「已经没有了」或「旧的怎么错」。
RETIRED_WORDS = ("删除", "已删", "旧实现", "已废弃", "废弃", "原有", "移除", "不再")


def blueprint_files() -> list[Path]:
    out = []
    for p in sorted((ROOT / "docs" / "web").glob("[0-9][0-9]_*.md")):
        if int(p.name[:2]) <= BLUEPRINT_MAX:
            out.append(p)
    return out


def collect_doc_routes(
    files: list[Path] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """蓝图里的路由，分「标题行」与「正文行」两档。

    Returns:
        ``(spec, prose)``——spec 是**规定**的接口（标题行），prose 是正文提及。
        两者都保留**完整行**便于回溯；截断只发生在打印时。

        （第一版在这里就 `[:90]` 截断了，结果 `/api/process` 那行末尾的「旧实现」
        正好落在 90 字之外，否定词过滤看不见它，一条对照提及被报成了缺口。
        判断用完整文本、显示才截断，这个分寸不能图省事。）
    """
    import re

    call = re.compile(r"\b(GET|POST|PUT|DELETE)\s+(/api/[A-Za-z0-9_\-/<>.{}$\[\]]*)")
    spec: dict[str, list[str]] = {}
    prose: dict[str, list[str]] = {}

    for f in (blueprint_files() if files is None else files):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in call.finditer(line):
                path = norm(m.group(2).rstrip("。，、）)"))
                slot = spec if line.lstrip().startswith("#") else prose
                slot.setdefault(path, []).append(f"{f.name}:{i}  {line.strip()}")
    return spec, prose


def classify_prose(
    prose: dict[str, list[str]], real: dict, spec: dict
) -> tuple[list[str], list[str]]:
    """正文提及、但没注册的路径，分成「对照提及」与「可疑」。

    同一条路径在正文里可能出现多次，**每一次都带否定词**才算对照提及——
    出现过一次「删除」不能给同页另一处的中性提及背书。
    """
    retired: list[str] = []
    suspicious: list[str] = []
    for p in sorted(set(prose) - set(real) - set(spec)):
        locs = prose[p]
        (retired if all(any(w in l for w in RETIRED_WORDS) for l in locs)
         else suspicious).append(p)
    return retired, suspicious


def main() -> int:
    spec, prose = collect_doc_routes()
    real = collect_routes()

    ghost = sorted(set(spec) - set(real))
    undocumented = sorted(set(real) - set(spec))

    print(f"蓝图标题规定的接口 {len(spec)} 条")
    print(f"代码实际注册的路由 {len(real)} 条\n")

    if ghost:
        print(f"✗ 蓝图规定了、代码没有（幽灵接口）{len(ghost)} 条：")
        for p in ghost:
            print(f"    {p:46s} {spec[p][0][:120]}")
        print()

    if undocumented:
        print(f"✗ 代码注册了、蓝图没写 {len(undocumented)} 条：")
        for p in undocumented:
            print(f"    {p:46s} {real[p][0][1]}  ({real[p][0][2]})")
        print()

    # 正文提及但没注册：带否定词的属「对照提及」，是文档该有的交代，不算缺口
    retired, suspicious = classify_prose(prose, real, spec)

    if retired:
        print(f"正文提及、代码没有，但带否定词（对照提及，正常）{len(retired)} 条：")
        for p in retired:
            print(f"    {p:46s} {prose[p][0][:120]}")
        print()

    if suspicious:
        print(f"⚠ 正文提及、代码没有，且没有否定词 {len(suspicious)} 条——"
              f"查清是漏写还是有意保留：")
        for p in suspicious:
            print(f"    {p:46s} {prose[p][0][:120]}")
        print()

    bad = len(ghost) + len(undocumented) + len(suspicious)
    if bad:
        print(f"✗ 蓝图与实现未对齐：{bad} 条待处理。")
        return 1

    print(f"✓ 蓝图 ↔ 实现双向对齐：{len(spec)} 条规定接口全部实现，"
          f"{len(real)} 条实际路由全部写进蓝图。")
    if retired:
        print(f"  （另有 {len(retired)} 条正文提及的已废弃路由，是刻意的对照说明）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
