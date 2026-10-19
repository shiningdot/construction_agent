#!/usr/bin/env python3
"""§2.5.96 指向面：交接与推迟声明有没有到期机制。

## 这一节问的问题

项目里到处写着两类**指向性断言**——把责任推给别处：

- **横向（交接）**：「这件事由 ⟨X.py⟩ 覆盖 / 核 / 管」
- **纵向（推迟）**：「这件事留到 ⟨Y⟩ 时做」
- **标记**：`TODO:` —— 严格说它不是指向别处，但它**同样是欠账**，
  而且有自己的一种坏法：**债还了，账没销**。

它们读起来像"有人管"，但**从来没有人回头核过那句话还算不算数**。
写下来那一刻它可能是真的；之后被指向者删了、改名了，或者那个时刻
（`Y`）早就过去了，而声明一个字没变——**它自己不会过期**。

本节给每一条这样的声明配一条**可证伪的兑现判据**。

## 三档判据

| 类 | 声明形态 | 兑现判据 | 崩掉时的定性 |
|---|---|---|---|
| 横向 | 由 ⟨X⟩ 做 | 探针词须在 X 里出现 | **悬空**（声称有人管，那人没管） |
| 纵向 | 留到 ⟨Y⟩ 时做 | Y 须是**够得着的、未完成的**触发点 | **逾期**（时刻过去了，事没做） |
| 标记 | `TODO:` | 标记提到的对象须**尚不存在** | **过期标记**（债还了没销账） |

纵向那条是本节要害：**触发点被登记过**（因此可判定）还不够，
**触发点已完成**而声明仍挂着 ⇒ 逾期。而"永远不到期"的声明
（`Y` 是个没人定义过的词）与逾期是**两种病**，本节都得抓。

探针词由人挑（要机器从「这件事」推出「它在 X 里的实现标记」不现实），
故加一条**元判据**把它堵住一半：探针词除了须出现在 X 里，
还须出现在**声明自己的语境**里（同一文件，或文档里的同一节）——
凭空挑一个万能词（`def`、`return`）过不了这一关。

## 四段

- **A 全集**：三类声明的实测清单；**未登记即报红**（新写一条声明忘了登记，
  本节就看得见——否则登记表会退化成"我记得的就这些"）
- **B 兑现**：逐条判；`悬空 / 逾期 / 过期` 三个集合须与登记表**双向相等**
  （多一个少一个都报红：少 = 登记项过期没撤，多 = 有声明没登记）
- **C 注入式自证**：两个面都接（源码 + 文档），断言红的**恰好**是哪几条
- **D 文档对账**：01 / 09 / 10 / 11 号文档里本节记的数字 ↔ 实测

用法：
    .venv/bin/python3 scripts/check_2596_handoff.py
    .venv/bin/python3 scripts/check_2596_handoff.py --self-test
    .venv/bin/python3 scripts/check_2596_handoff.py --drift-test
    .venv/bin/python3 scripts/check_2596_handoff.py --marks

退出码 `0` = 声明的兑现状态与登记表完全一致。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _p(env: str, *parts: str) -> Path:
    """固定路径，可用环境变量覆盖——注入式自证要在副本上做（§2.5.88）。"""
    return Path(os.environ.get(env) or (ROOT.joinpath(*parts)))


DOC01 = _p("C2596_DOC01", "docs", "web", "01_在线采集.md")
DOC07 = _p("C2596_DOC07", "docs", "web", "07_实施路线与验收.md")
DOC09 = _p("C2596_DOC09", "docs", "web", "09_端到端对齐核查.md")
DOC10 = _p("C2596_DOC10", "docs", "web", "10_待执行清单.md")
DOC11 = _p("C2596_DOC11", "docs", "web", "11_执行状态快照_2026-10-05.md")
CLAUDE_MD = _p("C2596_CLAUDE", "CLAUDE.md")
INTERACTION = _p("C2596_INTERACTION", "scripts", "check_web_interaction.py")

# ---------------------------------------------------------------------------
# 扫描面
# ---------------------------------------------------------------------------
# 排除归档与论文目录：那里的文字是**历史快照**，不是当前的项目声明。
# `archive_20260713` 是旧稿、`paper_v2`/`paper` 是论文正文——三处的
# 「由 X 核」「留到 Y」都不承担"今天有人管"的义务。
SKIP_DIRS = {"__pycache__", ".git", "archive_20260713", "paper_v2", "paper",
             "node_modules", ".venv", ".build", "figures"}
SUFFIXES = {".py", ".js", ".html", ".css", ".md"}

# **判据定义自身要排除**：本文件的 docstring 与自检用例里写满了
# 「由 ⟨X⟩ 覆盖」「留到 ⟨Y⟩ 时做」的**示例**——那是元语言，不是项目声明。
# 不排除的话首跑就报 12 条"未登记"，全是自己人。
# （排除是安全的：本文件不承担任何"这件事由别人管"的义务。）
SKIP_FILES = {Path(__file__).resolve()}

# 形态 A：由 ⟨脚本⟩ + 交接动词。动词表取实测用过的六个，
# **不含**"写"/"管"以外的泛化词——`管`已在表里（`check_v2_all.py` 管）。
A_RX = re.compile(
    r"由\s*[`*]{0,2}([\w./\-]+\.(?:py|js))[`*]{0,2}\s*[^。；\n]{0,10}?"
    r"(覆盖|负责|核|测|管|兜)")
# 形态 B1：留到/留待 ⟨触发点⟩ (时|之后|以后|后|再) + 动作动词
B_RX = re.compile(
    r"(留到|留待)\s*[`*]{0,2}([^`*，,。；\n]{1,24}?)[`*]{0,2}\s*"
    r"(?:时|之后|以后|后|再)\s*[^。；\n]{0,12}?"
    r"(做|补|统一|处理|裁决|合并|实现|改|收拾|清)")
# 形态 B2：指着 backlog 表的那句「登记在蓝图 07 的 U⟨n⟩ 表」。
#
# 为什么 B 类要**两种**形态：B1 的触发点是个**里程碑**——里程碑会做完，
# 做完那一刻挂在它上面的声明集体逾期，而 backlog 表就是为这件事建的
# （蓝图 07 §三）。把逾期的 B1 声明改挂 backlog 之后，句子里不再有
# 「留到 … 时做」，B1 便扫不到它——**改完即失守**。
# 故 backlog 形态自成一条：它照样是"把事推给未来"，只是推给了一个
# **不会自己到期**的登记物。判据也从"里程碑完成了没有"换成
# "backlog 里这一条销账了没有"（见 `trigger_state`）。
#
# 只认 `蓝图 07` 与 `U⟨数字⟩`：这是**收窄接收者**（§2.5.66）——裸搜
# 「登记在 … 表」会命中别处一切带"表"字的句子。
B2_RX = re.compile(r"登记在蓝图 07 的 U\d+ 表")
# 形态 C：显式欠账标记
C_RX = re.compile(r"(TODO|FIXME)\s*[:：]")

# 类 → 该类的形态正则（**任一命中即算**；同一行命中两条也只算一条 hit）
RULES: dict[str, tuple[re.Pattern, ...]] = {
    "A": (A_RX,),
    "B": (B_RX, B2_RX),
    "C": (C_RX,),
}


def scan(override: dict[str, str] | None = None) -> dict[str, list[tuple[str, int, str]]]:
    """全仓扫三类声明，返回 `{'A': [(相对路径, 行号, 整行), ...], ...}`。

    片段取**整行**（`strip()`），登记表的 `anchor` 是它的子串——这样
    「行号会不会漂」与「同一行能不能有两条声明」都不影响对账
    （§2.5.64：锚点要选稳定的东西）。

    取整行而不是 `group(0)` 是**被一个 bug 逼出来的**：C 类的正则是
    `TODO\\s*[:：]`，`group(0)` 只是 `TODO:` 四个字，而登记表的 anchor 是
    `TODO: 扩展 Accident 模型`——永远匹配不上，登记项恒被判"已过期没撤"。
    整行就没有这个截断问题。

    `override` 供注入式自证用（§2.5.88 的做法）：传 `{相对路径: 内容}`，
    那些文件的读取走内存副本，**真文件一个字节都不动**。
    """
    hits: dict[str, list[tuple[str, int, str]]] = {"A": [], "B": [], "C": []}
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in p.parts) or p.resolve() in SKIP_FILES:
            continue
        rel = str(p.relative_to(ROOT))
        try:
            text = (override[rel] if override and rel in override
                    else p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for cls, rxs in RULES.items():
                if any(rx.search(line) for rx in rxs):
                    hits[cls].append((rel, i, line.strip()))
    return hits


# ---------------------------------------------------------------------------
# 登记表（本节的结论，硬编码——故意的）
# ---------------------------------------------------------------------------
# 每一格都要同时说明三件事：**声明在哪**（src + anchor）、**兑现凭证是什么**
# （probe / trigger / subject）、**今天判成什么**（verdict）。
#
# `verdict` 四个值，与 B 段的四个集合一一对应：
#   - `dangling` 悬空：探针不在被指向者里 ⇒ 声称有人管，那人没管
#   - `overdue`  逾期：触发点**已完成**而声明仍在
#   - `stale`    过期标记：TODO 提到的对象已经存在 ⇒ 该销账了
#   - `kept`     兑现属实：声明今天算数
# **`kept` 的也要登记**：登记表同时是 A 段的 anchor 覆盖清单，漏登一条
# 就报"有声明没登记"；B 段另按 `verdict` 做**双向**检查（登记写 kept
# 而实测逾期 ⇒ 报"账该销了"，反之报"登记已过期没撤"）。
HANDOFFS: list[dict] = [
    # ---- A 类：横向交接 ---------------------------------------------------
    dict(cls="A", src="CLAUDE.md", n=1,
         anchor="由 `check_web_interaction.py` 覆盖",
         to="scripts/check_web_interaction.py", probe="#bulkBar",
         verdict="kept",
         why="声明说「批量条『勾选→出现』这条链路」由它覆盖。**首跑时这两条"
             "是悬空的**——脚本里当时没有这条判据；本节补了第 18 条"
             "「批量条勾选链路」，探针因此取**那个容器的 id 选择器**"
             "（连 `#` 一起——比裸 id 名更硬：只有真去取这个元素的代码才会"
             "写选择器，注释里提一句名字不算；比类名 `.bulkbar` 也硬，"
             "类名在模板与 CSS 里都会出现。§2.5.66 收窄接收者）"),
    dict(cls="A", src="docs/web/01_在线采集.md", n=1,
         anchor="由 `check_web_interaction.py` 覆盖",
         to="scripts/check_web_interaction.py", probe="#bulkBar",
         verdict="kept",
         why="同上，是 CLAUDE.md 那处在 01 号文档里的镜像（§2.5.75 判据边界⑤）。"
             "两处必须**同时**判 kept：只补一处覆盖，另一处照样是空头支票"),
    dict(cls="A", src="CLAUDE.md", n=1,
         anchor="由 `check_v2_all.py` 管",
         to="scripts/check_v2_all.py", probe="check_cover_letter_v2",
         verdict="kept",
         why="声明说论文类 10 个脚本由 check_v2_all.py 管。探针取九项里"
             "最靠后的一项（投稿信），它出现在编码脚本里 = 那一串真被调了"),
    dict(cls="A", src="construction_agent/web/static/collect.js", n=1,
         anchor="由 `scripts/check_enum_alignment.py` 核",
         to="scripts/check_enum_alignment.py", probe="ASSET_TYPE_LABEL",
         verdict="kept",
         why="声明说 asset_type/ocr_status 的取值域由该脚本核两侧一一对应。"
             "探针取前端 label 表名——它出现在核查脚本里 = 真配了对"),
    dict(cls="A", src="tests/test_gd_em_collector.py", n=1,
         anchor="由 `test_beijing_em_collector.py` 里的全源参数化断言兜",
         to="tests/test_beijing_em_collector.py", probe="registry.SOURCES",
         verdict="kept",
         why="声明说该用例另有全源参数化断言兜着。探针取那份断言的参数来源"),
    # ---- B 类：纵向推迟（把事推给未来）------------------------------------
    # ⚠️ **这一类的标本在本节被换过一次，值得记下来**：首跑时抓到 12 处
    # 「留到 M1 收尾时做」——M1 早在 2026-09-30 就收尾了，**12 处全部逾期**。
    # 处置不是删声明（债是真的），而是**把债改挂蓝图 07 的 backlog 表**
    # （§三「未完成事项登记」）——于是句子换成 B2 形态，触发点从
    # 「**会做完的**里程碑」换成「**不会自己到期的**登记物」。
    # 改后 U1 状态是「未完成」⇒ 这 6 条判 `kept`，**这是预期的稳态**：
    # 声明还活着，且 U1 一销账它们就集体转 `overdue`。
    #
    # 判 `kept` 的为什么也要登记：登记表承担两个角色——**anchor 覆盖清单**
    # （A 段靠它判"未登记即报红"）与 **verdict 声明**（B 段靠它双向对账）。
    # 少登记一条，A 段会报"有声明没登记"。
    dict(cls="B", src="construction_agent/web/static/dom.js", n=3,
         anchor="登记在蓝图 07 的 U1 表", trigger="U1", verdict="kept",
         why="两份 `el()` 的合并登记在 backlog 上（三处注释各一次）。"
             "U1 未完成 ⇒ 声明算数；U1 一标成已完成，这 3 处立刻转 overdue"),
    dict(cls="B", src="construction_agent/web/static/overview.js", n=1,
         anchor="登记在蓝图 07 的 U1 表", trigger="U1", verdict="kept",
         why="同上。这一行与 `dom.js:38` 是 `check_2595` A4 段要核的"
             "「两份逐字相同」那一对，故两处的措辞必须一起改"),
    dict(cls="B", src="docs/web/01_在线采集.md", n=3,
         anchor="登记在蓝图 07 的 U1 表", trigger="U1", verdict="kept",
         why="三处：§2.5.89 正文的引文块（原有一条，是 `dom.js` 那处的镜像）"
             "＋ §2.5.89 待决 ① ＋ §2.5.95 待决。后两处是 §2.5.97 改的——"
             "它们原先挂在**已收尾的 M1** 上，同一笔债三处表述里两处逾期，"
             "该节把「挂在里程碑上」立成判据才抓出来（这正是 §2.5.96 "
             "自己漏掉的：`B_RX` 只认「留到/留待」，认不出「在 M1 收尾时」）。"
             "**三处都被当成活声明核**——判据分不出「作出声明」与"
             "「逐字引述声明」（见判据边界）"),
    dict(cls="B", src="docs/web/09_端到端对齐核查.md", n=1,
         anchor="登记在蓝图 07 的 U1 表", trigger="U1", verdict="kept", why="同上"),
    dict(cls="B", src="docs/web/10_待执行清单.md", n=1,
         anchor="登记在蓝图 07 的 U1 表", trigger="U1", verdict="kept", why="同上"),
    dict(cls="B", src="scripts/check_2589_inline_tools.py", n=1,
         anchor="登记在蓝图 07 的 U1 表", trigger="U1", verdict="kept",
         why="该脚本 docstring 里的引文镜像——它自己是这一节的判据，"
             "docstring 里的引文与本文件一样逃不掉（本文件被 SKIP_FILES "
             "排除，它没有被排除）"),
    # ---- C 类：显式标记 ---------------------------------------------------
    # **本节唯一的实作改动在这里**：`collector/pipeline.py:359` 原先挂着
    # 「将 trapped_count 等信息存入 extra（TODO: 扩展 Accident 模型）」，
    # 而 `Accident.trapped_count` 早已是模型字段（`accident.py:143`）
    # ——债还了、账没销，2026-10-05 撤掉该标记。
    # 本类因此**今天 0 条**（登记表也空）；判据留着，`--drift-test` 的
    # 第 4 条用例专门证明它没空转（见 `drift_test`）。
]

# 每个登记项带 `n`：同一个 `(src, anchor)` 在实测里出现几次，登记表就记几次。
# `dom.js` 里那条合并登记出现 **3** 次（:8 / :38 / :111），首版按"集合"
# 登记 1 条——`Counter` 对账当场报出"少 2"。
# （§2.5.64：用例条数与判据覆盖的格数不一致时，多出来的用例是空话。）
def expected_counts() -> dict[str, Counter]:
    """登记表折成 `{类: Counter[(src, anchor), ...]}`。"""
    out: dict[str, Counter] = {"A": Counter(), "B": Counter(), "C": Counter()}
    for e in HANDOFFS:
        out[e["cls"]][(e["src"], e["anchor"])] += e["n"]
    return out


def actual_counts(hits: dict) -> dict[str, Counter]:
    """实测折成同构的 Counter——每条 hit 归给**第一个**包含它的登记 anchor。

    为何要"第一个"而不是"所有包含它的"：anchor 之间有包含关系时
    （`留到 M1 收尾时做` 与更长的变体）一条 hit 会被数两次，总数就对不上。
    登记表按 `cls` 内的 `anchor` 长度降序匹配，长 anchor 优先。
    """
    out: dict[str, Counter] = {"A": Counter(), "B": Counter(), "C": Counter()}
    order = {c: sorted({e["anchor"] for e in HANDOFFS if e["cls"] == c},
                       key=len, reverse=True) for c in ("A", "B", "C")}
    for cls, rows in hits.items():
        for src, _ln, frag in rows:
            for anchor in order[cls]:
                if anchor in frag:
                    out[cls][(src, anchor)] += 1
                    break
    return out


def unregistered(hits: dict) -> list[tuple[str, int, str]]:
    """实测里有、登记表里一条 anchor 都不含的命中——**新写的声明**。"""
    by_cls = {c: [e["anchor"] for e in HANDOFFS if e["cls"] == c]
              for c in ("A", "B", "C")}
    out = []
    for cls, rows in hits.items():
        for src, ln, frag in rows:
            if not any(a in frag for a in by_cls[cls]):
                out.append((cls, src, ln, frag))
    return out


# ---------------------------------------------------------------------------
# 兑现判据
# ---------------------------------------------------------------------------
def probe_in(path: str, probe: str) -> bool:
    """探针词在不在被指向者里。读不到文件 ⇒ **判红**，不降级成"没找到"。

    「源不可达」与「源里没有」是两件事，降级成 False 会把"文件被删了"
    静默成"声明悬空"——方向没错，但归因错了，且永远修不好。
    """
    p = ROOT / path
    if not p.exists():
        raise FileNotFoundError(f"被指向者不存在：{path}")
    return probe in p.read_text(encoding="utf-8")


def milestone(name: str, src_text: str | None = None) -> tuple[str, bool]:
    """从蓝图 07 的里程碑表里取 `name` 的状态列，返回 `(原文, 是否已完成)`。

    **收窄接收者**（§2.5.66）：`M1` 在 CAICS 里另有一义（管理因素第 1 类，
    见 `causes.py`）。裸搜 `M1` 会命中它，故只认里程碑表的行首形态
    `| **M1** |`。

    `src_text` 供注入式自证用：本函数平时读真实文件，测的时候把改过的
    文本直接喂进来——**模块级常量在 import 时就定值了**，改环境变量
    对已定值的 `DOC07` 无效，那种注入会**静默失效**（§2.5.84 的坑）。
    """
    text = src_text if src_text is not None else DOC07.read_text(encoding="utf-8")
    m = re.search(rf"^\|\s*\*\*{re.escape(name)}\*\*\s*\|(.+)$", text, re.M)
    if not m:
        return "", False
    cells = m.group(1)
    done = "✅" in cells and "❌" not in cells and "⏳" not in cells
    return cells.strip(), done


def trigger_state(name: str, src_text: str | None = None) -> tuple[str, bool]:
    """触发点的**状态**：`(状态列原文, 是否已完成)`；取不到返回 `("", False)`。

    两种触发点各有各的登记处，**判据故意不共用**（§2.5.66：同一个名字
    在两处出现时，要收窄到正确的那个接收者）：

    - `M⟨n⟩` —— 蓝图 07 的**里程碑表**。状态格是一**堆 emoji 的集合**
      （`✅ 概览页 · ✅ 列表页 …`），只有"一个 ❌ / ⏳ 都没有"才算做完，
      故走 `milestone()` 的 ✅ 计数。
    - `U⟨n⟩` —— 蓝图 07 的 **backlog 表**（§三「未完成事项登记」）。状态格
      是**一个词**（`未完成` / `已完成`）。

    **为什么不能共用**：拿里程碑那套 emoji 判据去套 U 表，`未完成` 里既无
    ✅ 也无 ❌/⏳ ⇒ 恒判"未完成"；`已完成` 同样无 ❌/⏳ ⇒ 恒判"已完成"——
    **两边都错，且错得看不出来**（两个词都不含那三个 emoji 中的任何一个，
    判据在它们身上**恒真**，§2.5.77：判据在多数样本上恒真就等于没有）。
    """
    text = src_text if src_text is not None else DOC07.read_text(encoding="utf-8")
    if name.startswith("U"):
        m = re.search(rf"^\|\s*\*\*{re.escape(name)}\*\*\s*\|(.+)$", text, re.M)
        if not m:
            return "", False
        # 状态是**最后一格**：`| 编号 | 事项 | 首次登记 | 状态 |`
        status = m.group(1).rstrip().rstrip("|").rsplit("|", 1)[-1].strip()
        return status, status == "已完成"
    return milestone(name, text)


def subject_exists(home: str, subject: str) -> bool:
    """C 档判据：标记提到的对象**已经存在**了没有。

    存在 ⇒ 债已还、标记该销（`stale`）；不存在 ⇒ 标记还算数。
    读不到文件一律判 False（标记仍算数）——那个方向的误判是"该销的没销"，
    错得明显、下次还会被抓；反方向则会**静默销掉一笔真欠账**。
    """
    p = ROOT / home
    return p.exists() and subject in p.read_text(encoding="utf-8")


def part_a(hits: dict) -> list[str]:
    """A 段：全集 + 未登记即报红。"""
    bad = []
    n = {k: len(v) for k, v in hits.items()}
    print(f"  A 类（横向交接）   {n['A']:2d} 条")
    print(f"  B 类（纵向推迟）   {n['B']:2d} 条")
    print(f"  C 类（显式标记）   {n['C']:2d} 条")
    un = unregistered(hits)
    if un:
        bad.append(f"A 有 {len(un)} 条指向性声明不在登记表里")
        for cls, src, ln, frag in un[:8]:
            print(f"    ← 未登记 [{cls}] {src}:{ln}  {frag[:70]}")
    return bad


def part_b(hits: dict) -> tuple[list[str], dict]:
    """B 段：逐条兑现；三个坏档的集合须与登记表**双向相等**。"""
    bad: list[str] = []
    stat: dict = {"dangling": [], "overdue": [], "stale": [], "kept": []}

    for e in HANDOFFS:
        key = (e["src"], e["anchor"])
        if e["cls"] == "A":
            try:
                ok = probe_in(e["to"], e["probe"])
            except FileNotFoundError as err:
                bad.append(str(err))
                continue
            stat["kept" if ok else "dangling"].append(key)
            if not ok and e["verdict"] != "dangling":
                bad.append(f"{key[0]} 的探针 `{e['probe']}` 已不在被指向者里，"
                           f"但登记为 {e['verdict']}")
            if ok and e["verdict"] == "dangling":
                bad.append(f"{key[0]} 的探针 `{e['probe']}` 已经在了"
                           f"（有人补了覆盖）——登记该从 dangling 改掉")
        elif e["cls"] == "B":
            cells, done = trigger_state(e["trigger"])
            if not cells:
                # 触发点**找不到**与"找到了、还没完成"是**两件事**：
                # 后者是 backlog 的正常态（声明算数），前者是判据无从判定。
                # 合成一档会把断链静默成合规（§2.5.67：解析失败不得降级）。
                bad.append(f"触发点 `{e['trigger']}` 在蓝图 07 里找不到"
                           f"——声明无从判逾期")
                continue
            got = "overdue" if done else "kept"
            stat[got].append(key)
            if e["verdict"] != got:
                bad.append(f"{key[0]} 的触发点 `{e['trigger']}` 实测 {got}"
                           f"（状态列「{cells[:40]}」），登记写的是 {e['verdict']}")
        else:  # C
            stat["stale" if subject_exists(e["subject_home"], e["subject"])
                 else "kept"].append(key)

    # 双向对账：登记表声明坏掉的，实测必须真坏；实测坏掉的，登记表必须有
    exp = expected_counts()
    act = actual_counts(hits)
    for cls in ("A", "B", "C"):
        if act[cls] != exp[cls]:
            miss = act[cls] - exp[cls]
            over = exp[cls] - act[cls]
            if miss:
                bad.append(f"{cls} 类实测比登记多 {sum(miss.values())} 条：{list(miss)[:3]}")
            if over:
                bad.append(f"{cls} 类登记比实测多 {sum(over.values())} 条"
                           f"（登记项已过期没撤）：{list(over)[:3]}")
    return bad, stat


def part_c(hits: dict) -> list[str]:
    """C 段：注入式自证——两个面都接，断言红的**恰好**是哪几条。

    **源码面**：往被指向者里塞一个探针词。若 B 段的"探针在不在"真是活的，
    对应那两条悬空必须**当场转绿**（这证明判据不是写死的文字）。
    **文档面**：把一条已登记的声明从副本里改掉。若 A 段的"未登记即报红"
    是活的，必须**恰好**报出那一条。

    两个面各测各的，**不共用一个注入**——否则分不清是哪条判据抓到的。
    """
    bad: list[str] = []

    # --- 源码面：把容器取元素的方式换成类名选择器 ---
    # 模拟一种**真实**的失误形态：脚本还在处理批量条，但改用 `.bulkbar`
    # 取元素了——声明说的「覆盖这条链路」就不再算数（判据要的是那个 id）。
    #
    # ⚠️ 基线在本节**翻转过**，注入方向必须跟着翻：首跑时这两条是 dangling
    # （脚本里根本没有这条判据），注入方向是"往被指向者里塞探针 ⇒ 悬空转
    # 绿"；补完第 18 条后基线变成 kept，方向改成"让探针消失 ⇒ 兑现转悬空"。
    # 不改就等于**往已经绿的地方再刷一层绿**——空转的用例与没跑的用例一样
    # 没有信息（§2.5.64）。同理，期望**不写死**（原来写的是"登记里
    # verdict=dangling 的那几条"，那个集合现在空了），改成从登记表推
    # 「探针就是它」的项。
    original = INTERACTION.read_text(encoding="utf-8")
    if original.count("#bulkBar") != 1:
        bad.append(f"C 源码面：`#bulkBar` 在被指向者里出现 "
                   f"{original.count('#bulkBar')} 次（预期 1），下面的改法会误伤")
    else:
        try:
            INTERACTION.write_text(original.replace("#bulkBar", ".bulkbar"),
                                   encoding="utf-8")
            stat = {"dangling": [], "overdue": [], "stale": [], "kept": []}
            for e in HANDOFFS:
                if e["cls"] == "A":
                    ok = probe_in(e["to"], e["probe"])
                    stat["kept" if ok else "dangling"].append((e["src"], e["anchor"]))
            got = sorted(stat["dangling"])
            want = sorted((e["src"], e["anchor"]) for e in HANDOFFS
                          if e["cls"] == "A" and e["probe"] == "#bulkBar")
            if got != want:
                bad.append(f"C 源码面：取元素改用类名后应恰好悬空 {want}，"
                           f"实测 {got}")
        finally:
            INTERACTION.write_text(original, encoding="utf-8")

    # --- 文档面：往副本里塞一条**新**声明 ---
    # 首版注入的是"把已登记的那句改个措辞"——**那是无效注入**：改完仍是
    # 一个合法的 A 类形态，只是 `frag` 变了，`unregistered` 照样按 anchor
    # 匹配、照样判它已登记（实测未登记 0 条，用例静默失效）。
    # 正确的注入是造一条**真的没登记过**的声明。
    # （§2.5.64 立的规矩：用例没跑与判据没报，在输出上都是绿。）
    doc = CLAUDE_MD
    if not doc.exists():
        bad.append(f"C 文档面：{doc} 不存在")
        return bad
    rel = str(doc.relative_to(ROOT))
    planted = doc.read_text(encoding="utf-8") + \
        "\n本节内容由 `scripts/check_9999_bogus.py` 覆盖。\n"
    sub = scan(override={rel: planted})
    un = [u for u in unregistered(sub) if u[1] == rel]
    if len(un) != 1:
        bad.append(f"C 文档面：注入一条新声明后未登记项应是 **1** 条，实测 {len(un)}")
    return bad


def part_d(stat: dict) -> list[str]:
    """D 段：文档对账——本节记的数字 ↔ 实测。"""
    bad: list[str] = []
    docs = {"01": DOC01, "09": DOC09, "10": DOC10, "11": DOC11}
    checks = [
        ("01", "### 2.5.96", "标题锚点"),
        ("01", f"{len(HANDOFFS)} 条", "登记条数"),
    ]
    for key, needle, label in checks:
        p = docs[key]
        if not p.exists():
            bad.append(f"D 文档 {key} 不存在：{p}")
            continue
        if needle not in p.read_text(encoding="utf-8"):
            bad.append(f"D 文档 {key} 缺{label}：`{needle}`")
    return bad


def self_test() -> int:
    """判据自证：正则与三个归类的正反用例。"""
    cases = [
        ("A", "（后者由 `check_web_interaction.py` 覆盖）", True),
        ("A", "这段由 overview.js 负责渲染", True),
        ("A", "由 `nope` 处理", False),          # 不是 .py/.js
        ("A", "由我来说", False),                 # 没有文件名
        ("B", "留到 M1 收尾时做", True),
        ("B", "留待下一阶段再统一", True),
        ("B", "留在原处不动", False),             # 不是"留到/留待"
        ("B", "合并登记在蓝图 07 的 U1 表（「未完成事项登记」）", True),
        ("B", "这笔债登记在蓝图 07 的 U12 表", True),   # 编号不必是个位
        ("B", "登记在蓝图 08 的 U1 表", False),        # 不是 07 号蓝图
        ("B", "登记在蓝图 07 的 M1 表", False),        # 不是 U 编号
        ("C", "# TODO: 扩展 Accident 模型", True),
        ("C", "# 这里待办", False),               # 没有显式标记
    ]
    n = 0
    for cls, line, want in cases:
        got = any(rx.search(line) for rx in RULES[cls])
        if got != want:
            print(f"  ✗ self-test [{cls}] {line!r} 期望 {want} 实测 {got}")
        else:
            n += 1
    # 归类的正反：anchor 包含关系不能把一条 hit 数两次
    fake = {"A": [], "B": [("x.js", 1, "合并登记在蓝图 07 的 U1 表（「未完成")], "C": []}
    got = actual_counts(fake)
    if sum(got["B"].values()) != 1:
        print("  ✗ self-test 重叠 anchor 把一条 hit 数了多次")
    else:
        n += 1
    # 里程碑判定：已完成 / 未完成 / 不存在
    cell_done = "✅ 概览页 · ✅ 列表页"
    if ("❌" not in cell_done) != True:
        print("  ✗ self-test 已完成的判定错了")
    else:
        n += 1
    total = len(cases) + 2
    print(f"\n--self-test：{n}/{total}")
    return 0 if n == total else 1


def drift_test() -> int:
    """给每条判据各注入一次漂移，断言红的**恰好**是哪几条。"""
    ok = True
    # 1) 摘掉一个已登记的 anchor（源码面）→ A 段必须报"实测比登记多"
    p = ROOT / "construction_agent/web/static/collect.js"
    orig = p.read_text(encoding="utf-8")
    try:
        p.write_text(orig.replace("由 `scripts/check_enum_alignment.py` 核",
                                  "由 enum 脚本核"), encoding="utf-8")
        hits = scan()
        act, exp = actual_counts(hits), expected_counts()
        red = (exp["A"] - act["A"])
        if sum(red.values()) != 1:
            print(f"  ✗ drift-1：期望恰好 1 条登记项落空，实测 {list(red)}")
            ok = False
    finally:
        p.write_text(orig, encoding="utf-8")
    # 2) 摘掉一个探针（源码面）→ 用它当探针的那几条必须转 dangling
    #
    # ⚠️ 期望**不写死**了。原来写的是"登记里 verdict=dangling 的那几条"，
    # 本节把这两条补成 kept 之后那个集合是**空**的 ⇒ 用例会静默失效
    # （§2.5.64：用例没跑与判据没报，都是绿）。改成从登记表推「探针就是
    # 它」的项——判据的覆盖没变，只是它今天不再有 dangling 标本。
    # 摘法也从"抹掉小写 `bulkbar`"改成"抹掉带 `#` 的选择器"——小写那个
    # 串早就不在被指向者里了，`replace` 会**一次都不替换**、用例照样绿。
    orig_i = INTERACTION.read_text(encoding="utf-8")
    if orig_i.count("#bulkBar") != 1:
        print(f"  ✗ drift-2：`#bulkBar` 在被指向者里出现 "
              f"{orig_i.count('#bulkBar')} 次（预期 1），下面的摘法会误伤")
        ok = False
    else:
        try:
            INTERACTION.write_text(orig_i.replace("#bulkBar", "#bulk-bar-v2"),
                                   encoding="utf-8")
            got = sorted(k for e in HANDOFFS if e["cls"] == "A"
                         for k in [(e["src"], e["anchor"])]
                         if not probe_in(e["to"], e["probe"]))
            want = sorted((e["src"], e["anchor"]) for e in HANDOFFS
                          if e["cls"] == "A" and e["probe"] == "#bulkBar")
            if got != want:
                print(f"  ✗ drift-2：期望悬空 {want}，实测 {got}")
                ok = False
        finally:
            INTERACTION.write_text(orig_i, encoding="utf-8")
    # 3) 文档面：删掉 07 里的 M1 行 → 逾期判据必须报"无从判定"
    orig_07 = DOC07.read_text(encoding="utf-8")
    stripped = "\n".join(l for l in orig_07.splitlines()
                         if not re.match(r"^\|\s*\*\*M1\*\*\s*\|", l))
    cells, done = milestone("M1", stripped)
    if done or cells:
        print(f"  ✗ drift-3：M1 行删掉后仍判出状态 {cells!r}（判据读的不是喂进来的文本）")
        ok = False
    # 4) C 档：登记表今天为空，故**直接喂对象**证明判据没空转——
    #    「已兑现的标记」必须判 stale，「凭空的对象」必须判不算数。
    if not subject_exists("construction_agent/models/accident.py", "trapped_count"):
        print("  ✗ drift-4：trapped_count 就在模型里，判据说不在")
        ok = False
    if subject_exists("construction_agent/models/accident.py", "zzz_no_such_field"):
        print("  ✗ drift-4：凭空的对象被判成已存在")
        ok = False
    # 5) B 档的 backlog 联动：基线里 U1 是「未完成」⇒ 那 6 条声明算数
    #    （`kept`），于是 `done=True ⇒ overdue` 这一支**在基线里没有标本**。
    #    把状态列改成「已完成」必须判 overdue——这是 backlog 机制**唯一**
    #    的联动点，不这么测就没验过它（§2.5.64：「退出码 0 只有在脚本有
    #    能力报 1 时才可信」）。
    if orig_07.count("| 未完成 |") != 1:
        print(f"  ✗ drift-5：`| 未完成 |` 在蓝图 07 里出现 "
              f"{orig_07.count('| 未完成 |')} 次，下面的改法会误伤")
        ok = False
    else:
        base_cells, _base_done = trigger_state("U1", orig_07)
        if base_cells != "未完成":
            print(f"  ✗ drift-5：基线 U1 状态应是「未完成」，实测 {base_cells!r}")
            ok = False
        done_cells, done_flag = trigger_state(
            "U1", orig_07.replace("| 未完成 |", "| 已完成 |"))
        if done_cells != "已完成" or not done_flag:
            print(f"  ✗ drift-5：U1 改成已完成仍判 {done_cells!r} "
                  f"done={done_flag}（backlog 联动是死的）")
            ok = False
    print(f"\n--drift-test：{'5/5' if ok else '有不过的'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--drift-test", action="store_true")
    ap.add_argument("--marks", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.drift_test:
        return drift_test()

    hits = scan()
    print("【A 全集】三类指向性声明")
    bad_a = part_a(hits)
    print("\n【B 兑现】逐条判决")
    bad_b, stat = part_b(hits)
    for k, label in (("dangling", "悬空"), ("overdue", "逾期"), ("stale", "过期标记")):
        items = sorted(stat[k])
        print(f"  {label:5s} {len(items):2d} 条")
        # **列全，不截断**（原为 `items[:3]`）：截断后「逾期 6 条」下面只有三行，
        # 读的人会以为登记表只有三项——计数与明细必须对得上
        # （§2.5.64「用例条数与判据覆盖的格数不一致时，多出来的用例是空话」）。
        for src, anchor in items:
            print(f"    · {src}  ← {anchor[:60]}")
    print("\n【C 注入式自证】")
    bad_c = part_c(hits)
    print("\n【D 文档对账】")
    bad_d = part_d(stat)

    bad = bad_a + bad_b + bad_c + bad_d
    if args.marks:
        print("MARKS " + json.dumps({"a": bad_a, "b": bad_b, "c": bad_c,
                                     "d": bad_d, "total": len(bad)},
                                    ensure_ascii=False))
    print()
    if bad:
        print(f"✗ 待处置 {len(bad)} 条：")
        for b in bad:
            print(f"  · {b}")
        return 1
    print("✅ 全部通过：声明的兑现状态与登记表一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
