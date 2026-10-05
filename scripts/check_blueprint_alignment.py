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

## 2026-10-05：两批假缺口，根因在两个不同的层

回归批跑（§2.5.90）先后报出**两批**「正文提及、代码没有、且没有否定词」，
**两批的根因完全不同**，各自修在判据的不同层。

### 第一批：字符类里的 `[` `]`

    /api/collect/sources]}   01_在线采集.md:12690
    /api/review/stats]}      01_在线采集.md:12691
    /api/overview]}          01_在线采集.md:12692

三条都在**注入式自证记录表**里，原文形如 `` `{a1: [GET /api/collect/sources]}` ``
——那是机器可读出口，路径后面紧跟 `]}`。而路径的字符类里写了 `[` 和 `]`，
于是 `]}` 被当成路径的一部分吃了进去，`/api/collect/sources]}` 在 real 里
自然查不到。**每写一条自证记录就多一条假缺口**。修法是把 `[` `]` 移出字符类
（`]` 成了终止符），3 条同时消失，退 0。

### 第二批：扫描面把核查记录当成了规格

修完第一批、写完 §2.5.90 那一节**之后再复跑，又红了**，换了一批：

    /api/old/legacy      01_在线采集.md:14923
    /api/x               01_在线采集.md:14944
    /api/x${suffix}      01_在线采集.md:14993

三条全是**那一节刚写进去的诊断举例**（「给一个不存在的接口写标题会判成幽灵」、
「`case_7` 核 `]` 之后的东西不被吃进路径」）。根因在**扫描面**：
`BLUEPRINT_MAX` 按**文件名**划线，前提是「文件名 = 文档角色」——而
`01_在线采集.md` 现在**同时是蓝图与核查记录**：`### 2.5.1` 到 `### 2.5.90`
共 **50 节**嵌在 `## 二、现状诊断` 之下（实测占 708–15022 行），
而 `## 三、目标架构`/`## 四、接口设计`/`## 五、页面结构与交互逻辑` 才是规格。
**判据的设计前提会过期**——这次是它当场发作。

修法：按**标题结构**排除核查记录区（`scan_lines`）——级别 ≤2 的标题离开该区、
级别 3 的标题看是否 `2.5.` 开头、级别 ≥4 沿用当前状态（是 2.5.x 的子标题）。
不猜语义、不写死节号。

**为什么这次改代码，而第一批时说过「不改」**：当时设想的修法是「01 号只取某几节」，
要写死节号（节号会增、标题会改，脆），而且**会把 01 号 §三/§四 那批真实接口一起
放过**。现在排除的是**子章节**、保留 §三/§四/§五，实测 **spec 少 0 条**——没有放过
任何真实接口。这一格是选这个方案的前提，所以 `--drift-test` 用 **D5/D6 一对**钉住它：
同一个探针注入**区外**必须报红（D5）、注入**区内**必须不报红（D6）。

### 这层巧合原来靠什么撑着

第一批之前之所以没出事，是**结构巧合**：只有**标题行**进 spec，而 §2.5.x 的标题是
`### 2.5.90 …`、不含路由；那些报告行落进 prose，而它们提的路径都是**真实路由**、
被 `set(prose) - set(real)` 滤掉了。巧合有两个破法：①某节的三级标题若写成
``### `GET /api/xxx` `` 就会被当**规格**；②报告里出现一个**不存在**的路径
（正如第二批），只要同页没有否定词就会被报成缺口。

## 判据边界（别把退 0 当免检）

- **`BLUEPRINT_MAX` 的前提（文件名 = 文档角色）已部分过期**，由 `NOTES_PREFIX`
  在文档内部补上一刀（见上节）。**`BLUEPRINT_MAX` 本身仍按文件名划线**——
  若将来别的蓝图文档（02–08）也开始混装核查记录，得同样处理。
- **`<int:accident_id>` 这种带类型标注的 Flask 变量段不被支持**：`:` 不在字符类里，
  会被截成 `/api/accidents/<int`、在 real 里查不到。实测 00–08 **0 处**使用这种写法
  （文档统一写 `<accident_id>`），故今天不发作。**刻意不加 `:`**——加了会引入另一种
  假阳性：路径后紧跟半角冒号（``见 `GET /api/x`: …``）时冒号同样会被吃进路径。
  两边的宽度都要写出来（§2.5.66），这里选的是**已知缺口 + 如实登记**。
- **`{` `}` `$` 保留在字符类里**：它们是 JS 模板占位符（`${id}`），保留**不会**
  引入假阳性（反引号不在字符类里，路径天然在反引号处终止），但也**不会**把它们
  归一成 `< >` —— 若将来文档改用 `${id}` 写路由，会报假缺口。同样登记不修。
- 判据只核**路径形状**，不核方法（spec 里同一条路径出现 `GET` 与 `POST` 两种
  写法时只留一条记录）；不核参数、不核响应（那些是 §2.5.80–83 的射程）。

用法：
    .venv/bin/python3 scripts/check_blueprint_alignment.py
    .venv/bin/python3 scripts/check_blueprint_alignment.py --self-test
    .venv/bin/python3 scripts/check_blueprint_alignment.py --drift-test

退出码：0 = 双向对齐（那两个自证开关同理：0 = 全部通过）；1 = 有缺口 / 自证失败。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_web_alignment import collect_routes, norm  # noqa: E402

# 000–08 是蓝图；09 是核查记录、10 是待执行清单，它们提到路由是在**报告**，
# 不是在规定，混进来会把核查结论本身当成规格。
BLUEPRINT_MAX = 8

# **但 01 号内部也混着核查记录**（2026-10-05 补）：它既是蓝图（§一 目标 / §三 目标
# 架构 / §四 接口设计 / §五 页面结构），又是 50 节核查记录的挂载点——`### 2.5.1`
# 到 `### 2.5.90` 嵌在 `## 二、现状诊断` 之下，实测占 708–15022 行。核查记录里
# 写「给一个不存在的接口写标题会判成幽灵」时的**举例路径**，会被当成「蓝图规定了
# 这个接口」⇒ 每写一条诊断举例就多一条假缺口。
#
# 识别只按**标题结构**（章节编号），不猜语义：级别 ≤2 的标题一律离开核查区，
# 级别 3 的标题看它是不是 `2.5.` 开头，级别 ≥4 沿用当前状态（是 2.5.x 的子标题）。
NOTES_PREFIX = "2.5."

#: 标题行：``(井号个数, 标题文本)``。带前导空白（md 允许至多 3 空格）但**不认
#: 引用块**（`> ### x` 的 `>` 先出现，不匹配）。
HEADING = re.compile(r"^(#{1,6})\s+(\S)")


def heading_kind(line: str) -> tuple[int, str] | None:
    """标题行返回 ``(级别, 标题文本)``，否则 ``None``。"""
    m = HEADING.match(line.lstrip())
    if not m:
        return None
    return len(m.group(1)), line.lstrip()[len(m.group(1)):].lstrip()


def scan_lines(lines: list[str]) -> list[tuple[str, bool]]:
    """给每行打上「是否处于核查记录区」的标记。

    抽成纯函数是为了 `--self-test` 能直接喂行列表验证状态转移——
    「排除生效」这件事不能只看最后那个退出码。
    """
    out = []
    in_notes = False
    for line in lines:
        hk = heading_kind(line)
        if hk is not None:
            lvl, title = hk
            if lvl <= 2:
                in_notes = False
            elif lvl == 3:
                in_notes = title.startswith(NOTES_PREFIX)
            # lvl >= 4：沿用当前状态（是 ### 2.5.x 的子标题，不是新的分区）
        out.append((line, in_notes))
    return out


# 正文提及路径时，同一行出现这些词说明是在说「已经没有了」或「旧的怎么错」。
RETIRED_WORDS = ("删除", "已删", "旧实现", "已废弃", "废弃", "原有", "移除", "不再")

# 路径的字符类。**方括号 `[` `]` 刻意不在其中**（2026-10-05 修正）：
# 本项目核查记录里有一种机器可读出口写作 `` `{a1: [GET /api/collect/sources]}` ``，
# 路径后面紧跟 `]}`；`[` `]` 一旦进字符类，`/api/collect/sources]}` 会整串被吃进
# 路径、在 real 里查不到，于是**每写一条注入式自证记录就多一条假缺口**（§2.5.90）。
# `{` `}` `$` 保留——它们是 JS 模板占位符（`${id}`），今天没有文档这么写，
# 但保留不会引入假阳性（反引号不在字符类里，路径天然在反引号处终止）。
# 见 `--self-test` 的 case_1/case_9。
CALL = re.compile(r"\b(GET|POST|PUT|DELETE)\s+(/api/[A-Za-z0-9_\-/<>.{}$]*)")


def parse_line(line: str) -> list[tuple[str, bool]]:
    """从一行里抽出 ``(归一化路径, 是否标题行)``。

    抽成纯函数是为了 `--self-test` 能直接喂字符串验证提取行为，
    不必造临时 md 文件。
    """
    is_heading = line.lstrip().startswith("#")
    out = []
    for m in CALL.finditer(line):
        path = norm(m.group(2).rstrip("。，、）)"))
        out.append((path, is_heading))
    return out


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

        核查记录区（`### 2.5.x`）整段跳过——见 `NOTES_PREFIX` 的注释。
    """
    spec: dict[str, list[str]] = {}
    prose: dict[str, list[str]] = {}

    for f in (blueprint_files() if files is None else files):
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, (line, in_notes) in enumerate(scan_lines(lines), 1):
            if in_notes:
                continue
            for path, is_heading in parse_line(line):
                slot = spec if is_heading else prose
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


def compare(
    spec: dict[str, list[str]],
    prose: dict[str, list[str]],
    real: dict,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """三路求差，返回 ``(幽灵, 未写进蓝图, 对照提及, 可疑)``。

    抽成纯函数是为了 `--drift-test` 能直接喂构造好的三份字典验判据，
    不必先造出真 md 文件——**判据能报红**这件事本身要被验证（§2.5.64）。
    """
    ghost = sorted(set(spec) - set(real))
    undocumented = sorted(set(real) - set(spec))
    retired, suspicious = classify_prose(prose, real, spec)
    return ghost, undocumented, retired, suspicious


def main() -> int:
    spec, prose = collect_doc_routes()
    real = collect_routes()

    ghost, undocumented, retired, suspicious = compare(spec, prose, real)

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

    # 机器可读出口：`--drift-test` 要断言「红的**恰好**是哪几条」，
    # 解析中文打印行太脆（§2.5.68 的教训）。
    print("MARKS " + json.dumps(
        {"ghost": ghost, "undocumented": undocumented,
         "retired": retired, "suspicious": suspicious},
        ensure_ascii=False))

    bad = len(ghost) + len(undocumented) + len(suspicious)
    if bad:
        print(f"✗ 蓝图与实现未对齐：{bad} 条待处理。")
        return 1

    print(f"✓ 蓝图 ↔ 实现双向对齐：{len(spec)} 条规定接口全部实现，"
          f"{len(real)} 条实际路由全部写进蓝图。")
    if retired:
        print(f"  （另有 {len(retired)} 条正文提及的已废弃路由，是刻意的对照说明）")
    return 0


# ── --self-test：提取行为本身的正反用例 ──────────────────────────────
# §2.5.90 立的规矩：**只有脚本有能力报 1，它的 0 才可信**。这条判据的结论
# 完全取决于「一行文本里的路径被提成了什么」——而这一步正是 2026-10-05 报出
# 3 条假缺口的地方（`[` `]` 进字符类，路径吃进了 `]}`）。所以自证不能只验
# 「最后退出码」，得逐条验提取出的**路径字符串**。
#
# 每条用例：输入行 → 期望的 `[(路径, 是否标题行)]`。
SELF_CASES: list[tuple[str, list[tuple[str, bool]]]] = [
    # case_1 本节的回归保护：机器可读出口里的路径后面紧跟 `]}`，
    # 它必须停在 `]` 前、且整个 `]}` 不得进路径。
    ("| 实现把 `total_raw` 包成字符串 | `{a1: [GET /api/collect/sources]}` |",
     [("/api/collect/sources", False)]),
    # case_2 标题行（反引号包裹）→ 进 spec
    ("### `GET /api/collect/sources`", [("/api/collect/sources", True)]),
    # case_3 同一行含中文说明，标题判定不受影响
    ("#### POST /api/review/items —— 提交一条审核", [("/api/review/items", True)]),
    # case_4 query string 要剥掉（norm 的职责）
    ("列表见 GET /api/accidents?severity=死亡&page=2", [("/api/accidents", False)]),
    # case_5 Flask 变量段归一成 `<>`（文档里用的是不带类型标注的形态，
    # 带 `<int:...>` 的写法今天 0 处使用，是已登记的缺口，见模块 docstring 边界）
    ("`GET /api/accidents/<accident_id>`", [("/api/accidents/<>", False)]),
    # case_6 中文句末标点要 rstrip（否则路径尾带 `。`、在 real 里查不到）
    ("调用 GET /api/health。", [("/api/health", False)]),
    # case_7 负例：`]` 是终止符，`]` 之后的字符一个都不许进路径
    ("见 GET /api/x]y 一段", [("/api/x", False)]),
    # case_8 一行两个接口（行首是反引号不是 `#`，两个都归正文）
    ("`GET /api/a` 与 `POST /api/b`", [("/api/a", False), ("/api/b", False)]),
    # case_9 占位符形态不被误吃成真实路径（保留 `{}` `$` 的代价说明）
    ("`GET /api/x/${id}`", [("/api/x/${id}", False)]),
    # case_10 没有任何接口的一行 → 空
    ("这一段只是说明文字，不含任何端点。", []),
]

#: 状态机用例：``(行列表, 期望的「是否处于核查记录区」序列)``。
#: 证明「排除核查记录」是按**标题结构**生效的，不是按行内容猜、也不是整份跳过。
SCAN_CASES: list[tuple[list[str], list[bool]]] = [
    # scan_1 区内的标题行与正文行都不扫（`#### ` 是 2.5.x 的子标题，沿用状态）
    (["## 二、现状诊断", "### 2.5.1 第一轮", "见 GET /api/a", "#### 子标题"],
     [False, True, True, True]),
    # scan_2 二级标题离开（§三/§四/§五 才是规格）
    (["### 2.5.9 第九轮", "## 三、目标架构", "### `GET /api/b`"],
     [True, False, False]),
    # scan_3 **同级**的非 2.5 标题也离开——不能只看井号个数
    (["### 2.5.9", "### 附录", "正文"], [True, False, False]),
    # scan_4 区外的四级标题只是普通子标题，不改变状态
    (["## 三、目标架构", "#### GET /api/c"], [False, False]),
    # scan_5 引用块里的 `### 2.5.x` 不算标题（md 语义），不会误触发进入
    (["## 三、目标架构", "> ### 2.5.2 假标题", "正文"], [False, False, False]),
    # scan_6 前缀必须带点：`2.50` 不是 `2.5.`（防将来编号扩到 2.50 时误判）
    (["### 2.50 将来的一节"], [False]),
]


def self_test() -> int:
    fails = 0
    print("  [parse_line 提取]")
    for n, (line, want) in enumerate(SELF_CASES, 1):
        got = parse_line(line)
        if got == want:
            print(f"  ✓ case_{n}")
        else:
            fails += 1
            print(f"  ✗ case_{n}  输入 {line!r}")
            print(f"      期望 {want}")
            print(f"      实得 {got}")
    print("  [scan_lines 状态机]")
    for n, (lines, want) in enumerate(SCAN_CASES, 1):
        got = [flag for _, flag in scan_lines(lines)]
        if got == want:
            print(f"  ✓ scan_{n}")
        else:
            fails += 1
            print(f"  ✗ scan_{n}  输入 {lines!r}")
            print(f"      期望 {want}")
            print(f"      实得 {got}")
    total = len(SELF_CASES) + len(SCAN_CASES)
    if fails:
        print(f"\n✗ --self-test：{total - fails}/{total} 通过，{fails} 条失败。")
        return 1
    print(f"\n✓ --self-test：{total}/{total} 通过。")
    return 0


# ── --drift-test：注入式自证，证明主检真的会报红 ─────────────────────
# `--self-test` 验的是「提取对不对」，`--drift-test` 验的是「判据会不会报红」，
# **两者不是同一件事**（§2.5.87 立）。一条永远返回 0 的判据，它的 self-test
# 可以全绿；只有往里面注入缺陷、断言红的**恰好**是哪几条，才证明这个 0 有意义。
#
# 前三例走**临时文档**（构造整份 md，含全部真实路由的标题行，故 undocumented
# 恒空、归因唯一）；第四例走**真实文件 + main() 全流程**——这是唯一能证明
# 「文件 → 解析 → 分类 → 退出码」整条链路活着的方式。
# 每项：``(名字, 注入文本, 期望的报红面, 期望被归入 retired 的那条)``。
# **「报红面」只含参与退出码的三路**——retired（对照提及）是文档该有的交代、
# 不进退出码，它单独用一个字段断言，免得「不报红」与「归错类」混为一谈。
DRIFT_DOC_CASES: list[tuple[str, str, dict[str, list[str]], str | None]] = [
    ("标题写一个不存在的接口 → 幽灵",
     "### `GET /api/zzz_probe`\n", {"ghost": ["/api/zzz_probe"]}, None),
    ("正文写一个不存在的接口、无否定词 → 可疑",
     "见 `GET /api/yyy_probe` 接口。\n", {"suspicious": ["/api/yyy_probe"]}, None),
    ("正文写一个不存在的接口、带否定词 → 对照提及（不报红）",
     "原有 `GET /api/www_probe`，2026-10-01 已删除。\n",
     {}, "/api/www_probe"),
    # 本节的回归保护：机器可读出口里的路径后面紧跟 `]}`。
    # 2026-10-05 之前这行会报一条假缺口（路径被提成 `/api/health]}`）。
    ("机器可读出口里的真实路径不算缺口",
     "| 注入 | `{a1: [GET /api/health]}` |\n", {}, None),
]


def _fake_blueprint(extra: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """造一份「写全了所有真实接口」的临时蓝图，再追加 `extra` 那几行。"""
    real = collect_routes()
    body = "".join(f"### `GET {p}`\n" for p in sorted(real)) + extra
    tmp = Path(tempfile.mkdtemp()) / "00_probe.md"
    tmp.write_text(body, encoding="utf-8")
    return collect_doc_routes(files=[tmp])


def _run_main() -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main()
    marks: dict = {}
    for line in buf.getvalue().splitlines():
        if line.startswith("MARKS "):
            marks = json.loads(line[len("MARKS "):])
    return rc, marks


#: 参与退出码的三路（retired 是对照提及、文档该有的交代，不进退出码）。
RED_KEYS = ("ghost", "undocumented", "suspicious")


def _red(marks: dict) -> dict:
    """只保留**参与退出码**的那几路——「红的恰好是哪几条」的左侧。"""
    return {k: v for k, v in marks.items() if k in RED_KEYS and v}


def drift_test() -> int:
    fails = 0

    # 基线：注入之前主检必须是绿的，否则下面的归因不成立
    base_rc, base_marks = _run_main()
    if base_rc != 0 or _red(base_marks):
        print(f"  ✗ 基线不是绿的（rc={base_rc}, red={_red(base_marks)}）——"
              f"先修好主检再跑自证")
        return 1
    print("  ✓ 基线：主检 rc=0、参与退出码的三路皆空")

    for n, (name, extra, want, want_retired) in enumerate(DRIFT_DOC_CASES, 1):
        spec, prose = _fake_blueprint(extra)
        ghost, undoc, retired, susp = compare(spec, prose, collect_routes())
        got = _red({"ghost": ghost, "undocumented": undoc, "suspicious": susp})
        got_retired = want_retired in retired if want_retired else None
        if got == want and got_retired in (None, True):
            print(f"  ✓ D{n} {name}")
        else:
            fails += 1
            print(f"  ✗ D{n} {name}\n      期望 红={want} retired含={want_retired}"
                  f"\n      实得 红={got} retired={retired}")

    # D5/D6 是一对：同一个探针、同一个文件，**只差注入位置**。
    # 区外（文件末尾，§五 之后）必须报红；区内（`### 2.5.x` 核查记录）必须不报红。
    # 配对的意义：单有 D5 时，「整份 01 号被放过」这种错误实现也能通过——那会让
    # D6 静默失效；单有 D6 时，「判据根本没扫 01 号」同样是绿的。
    # 两条一起，才证明「排除」是按标题结构精确生效的（§2.5.90）。
    tgt = ROOT / "docs" / "web" / "01_在线采集.md"
    orig = tgt.read_text(encoding="utf-8")
    try:
        # D5 区外：追加到文件末尾（§五 页面结构之后，不在任何 2.5.x 节里）
        tgt.write_text(orig + "\n\n> 探针段落：见 `GET /api/zzz_probe` 接口。\n",
                       encoding="utf-8")
        rc, marks = _run_main()
        want = {"suspicious": ["/api/zzz_probe"]}
        if rc == 1 and _red(marks) == want:
            print("  ✓ D5 真实 01 号**区外**注入 → 退出码 0→1，红的恰好是那一条")
        else:
            fails += 1
            print(f"  ✗ D5 真实 01 号区外注入\n      rc={rc}（期望 1）"
                  f"\n      期望 {want}\n      实得 {_red(marks)}")

        # D6 区内：插到第一个 `### 2.5.` 标题之后
        lines = orig.splitlines()
        idx = next((i for i, ln in enumerate(lines) if ln.startswith("### 2.5.")), None)
        if idx is None:
            fails += 1
            print("  ✗ D6 在 01 号里找不到 `### 2.5.` 核查记录区——用例前提不成立")
        else:
            lines.insert(idx + 1, "> 探针：见 `GET /api/zzz_probe_notes` 接口。")
            tgt.write_text("\n".join(lines) + "\n", encoding="utf-8")
            rc6, marks6 = _run_main()
            if rc6 == 0 and not _red(marks6):
                print("  ✓ D6 真实 01 号**核查记录区内**注入 → 被排除，退出码不变")
            else:
                fails += 1
                print(f"  ✗ D6 真实 01 号区内注入\n      rc={rc6}（期望 0）"
                      f"\n      期望 红={{}}\n      实得 {_red(marks6)}")
    finally:
        tgt.write_text(orig, encoding="utf-8")
        if tgt.read_text(encoding="utf-8") != orig:
            print("  ✗ 恢复失败：01_在线采集.md 与原文不一致！")
            fails += 1
        else:
            print("  ✓ D5/D6 现场已逐字恢复")

    total = len(DRIFT_DOC_CASES) + 3  # 基线 + D5 + D6
    if fails:
        print(f"\n✗ --drift-test：{total - fails}/{total} 通过，{fails} 条失败。")
        return 1
    print(f"\n✓ --drift-test：{total}/{total} 通过。")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--drift-test" in sys.argv:
        sys.exit(drift_test())
    sys.exit(main())
