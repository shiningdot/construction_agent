#!/usr/bin/env python3
"""§2.5.97 作者裁决项的账本：散落的待决有没有总账、有没有到期机制。

本节命题
    01 号文档里 19 个小节写了「待决」/「属作者裁决」。其中**两种**形态机器认得出：
      ① 小节型  `#### 八、待决` + `1. 2. 3.`
      ② 行内型  `**待决（属作者裁决）**：` + `①②③`
      ③ 隐性    正文里散落的「属作者裁决」「另属未决」——**本扫描器不认**，
                属已登记的覆盖缺口（01 号 §2.5.97 §一 表末与 §七 各记一次）
    没有任何地方能回答「一共多少条、哪些最老、哪些前提已经变了」。
    到期的判据只有写它的那一段知道，而写它的那一轮已经过去了。

四段
    A 全集   把 19 节切成 61 条，逐条编号 `2.5.xx#n`，标 open/closed
    B 闭式   每条 open 待决的「到期机制」= 一条**可自动复核的前提**。
             本节不人工给 61 条分类，而是让机器扫（C 段）——扫得到的进
             `PROBED`，扫不到的进 `BARE` 并**如实列出**。那份清单就是
             「有多少条待决今天没有任何到期机制」的量化答案。
    C 探针   六类前提各自实测：跨节引用 / 里程碑 / 文件行号 / 库表 / 计数 / backlog
    D 对账   本节写进 01 / 09 号文档的数字 ↔ 本次实测。
             **正例**（`MUST`）管"新值在场"，**反例**（`FORBID`）管"旧值不残留"。
             只有正例时，"改了文档一半"照样绿——本节自己就出过一次：
             01 号 §2.5.97 写作时记的是 57 条 / 50 open / 18 节，而账本实际
             量出 61 / 54 / 19，两套数字在同一节里并存了一轮没人发现。

判据设计（承 §2.5.67）
    登记「核不了」是一种**有据的登记**，不是豁免——必须写明为什么。
    `BARE` 不判红：它是**现状**不是缺陷，本节的结论就写在它上面。
    判红的是**自相矛盾**：引用了一个已全部结清的节却说它「仍未处置」。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 两个源都可用环境变量指向副本——`--self-proof` 要往副本里注入病、不能动真文档
DOC = Path(os.environ.get("C2597_DOC") or ROOT / "docs" / "web" / "01_在线采集.md")
ROADMAP = Path(os.environ.get("C2597_ROADMAP") or ROOT / "docs" / "web" / "07_实施路线与验收.md")

# ============================================================ A 段：切分
# 节号：早期写作 `### 2.5.30`、§2.5.36 起带 § 前缀，两种都要认
SEC = re.compile(r"^#{2,4} *§?(\d+\.\d+(?:\.\d+)?) *")
HDR = re.compile(r"^#{2,6} *[一二三四五六七八九十]+、待决")
INL = re.compile(r"\*\*待决（属作者裁决）\*\*")
ANY_H = re.compile(r"^#{2,6} ")

# 切点：行首编号（点号后**必须有空白**，挡 `0.95**` 这类续行）
#       或圈码（行首，或紧跟强标点——挡 `待决 ①的三份` 这类条内引用）
HEAD = re.compile(r"(?m)^[ \t>*_#]{0,6}(?:\d+\.\s+|[①②③④⑤⑥⑦⑧⑨⑩])\s*")
MID = re.compile(r"(?<=[：:；;。])\s*[①②③④⑤⑥⑦⑧⑨⑩]")
# 段内小标题（`**两条方法论（本节新增）**：`）＝新块，不是待决条
SUB = re.compile(r"^\*\*[^*]{2,40}\*\*[：:]?$")
JUNK = re.compile(r"[：:；;，,。、\s]*")

CLOSED = re.compile(r"~~|已结（§2\.5\.|已补（§2\.5\.|已处置|已回收|已修（§2\.5\.")
# 条内出现这些词＝这条在讲某件事「还没做」
PENDING_WORD = re.compile(r"未处置|仍未|待裁|待决|未决|未做|要不要|是否")

# 句子边界（P1 用）：只认强标点与换行。**逗号与破折号不算**——一条待决里
# 「§2.5.68 的规矩」与「要不要保留」常在同一句、由逗号相连，切在逗号上
# 等于没切。弱标点切分是另一回事（那会切得太碎、把同句的语义切开）。
SENT_BREAK = re.compile(r"[。；;！？\n]")


def sentence_at(text: str, pos: int) -> str:
    """取 `text[pos]` 所在的那一句（以强标点/换行为界）。

    P1 的**接收者**必须收窄到这一句：待决条自身几乎必然带「要不要」「是否」
    这类未决语气，若拿整条去搜，就会出现「本条在问自己的事」被判成
    「本条宣称某个**别的**节未处置」——§2.5.99 待决 2 真踩过这一格
    （§2.5.66「判据必须收窄接收者」的统一形态）。
    """
    start = 0
    for m in SENT_BREAK.finditer(text, 0, pos):
        start = m.end()
    m = SENT_BREAK.search(text, pos)
    return text[start:m.start() if m else len(text)]


def cut_items(text: str) -> list[tuple[int, str]]:
    """把一段待决正文切成条，返回 [(字符偏移, 文本)]。"""
    pos = sorted({0} | {m.start() for m in HEAD.finditer(text)}
                 | {m.start() for m in MID.finditer(text)})
    pos.append(len(text))
    out: list[tuple[int, str]] = []
    for a, b in zip(pos, pos[1:]):
        seg = re.sub(r"^[#>*_ ]*", "", text[a:b].strip()).strip()
        if not seg or JUNK.fullmatch(seg):
            continue                      # 段首残文（标记行被清掉后剩的标点）
        if seg.startswith("待决（属作者裁决）"):
            continue
        if re.match(r"^[一二三四五六七八九十]+、待决", seg):
            continue                      # 小节标题自身成了第 1 条
        out.append((a, seg))
    return out


def split_doc() -> list[dict]:
    """切出全部待决条。§2.5.59 的「待决」是引用他人既有裁决，不是本节待决。"""
    lines = DOC.read_text(encoding="utf-8").split("\n")
    sec, spans = "?", []
    for i, l in enumerate(lines):
        m = SEC.match(l)
        if m:
            sec = m.group(1)
        if HDR.match(l) and sec != "2.5.59":
            spans.append((i, sec, "HDR"))
        elif INL.search(l):
            spans.append((i, sec, "INL"))

    items: list[dict] = []
    for i, s, kind in spans:
        end = i + 1
        if kind == "HDR":
            while end < len(lines) and not ANY_H.match(lines[end]):
                # 段内小标题＝新块（「两条方法论」那类）。**HDR 型也必须断**——
                # 原先只有 INL 型断，于是「#### 九、待决」后面的方法论两条编号
                # 被当成待决条吃进账本（§2.5.97 自己撞上的）。
                if SUB.match(lines[end].strip()):
                    break
                end += 1
        else:
            blank = 0
            while end < len(lines) and not ANY_H.match(lines[end]):
                if SUB.match(lines[end].strip()):
                    break                 # 段内小标题＝新块（「两条方法论」那类）
                if lines[end].strip() == "":
                    blank += 1
                    if blank >= 2:
                        break
                else:
                    blank = 0
                end += 1
        body = "\n".join(lines[i:end]).replace("**待决（属作者裁决）**", "")
        offs, acc = [], 0
        for j in range(i, end):
            offs.append(acc)
            acc += len(lines[j]) + 1
        for n, (a, seg) in enumerate(cut_items(body), 1):
            ln = i + 1 + max(k for k, o in enumerate(offs) if o <= a)
            items.append({
                "key": f"{s}#{n}", "sec": s, "n": n, "line": ln, "kind": kind,
                "text": seg, "closed": bool(CLOSED.search(seg)),
            })
    return items


# ============================================================ C 段：五类探针
XREF = re.compile(r"§2\.5\.(\d+)")
ALREADY = lambda n: re.compile(rf"已[结补修放]（§2\.5\.{n}")   # 「→ 已结（§2.5.71）」
MS_REF = re.compile(r"\bM([1-4])\b")
# 把待决**挂**在一个里程碑上（而不只是提到它）
MS_HANG = re.compile(r"收尾时|属 ?M\d|留到 ?M\d|待 ?M\d|M\d ?收尾的")
FILE_REF = re.compile(r"`?([A-Za-z_][\w./-]*\.(?:py|js|css|html|md|json))`?(?::(\d+))?")
TABLE_REF = re.compile(r"`(accidents|caics_causes|enum_definitions|image_analyses|"
                       r"image_files|source_urls|processing_log|search_logs|"
                       r"pipeline_metadata|raw_accidents|staging_accidents|"
                       r"promote_log|collect_sources|image_manifest|staging_metadata)`")
COUNT_REF = re.compile(r"(\d+)\s*(?:个)?提交|领先\s*(\d+)")
# 指向蓝图 07 的 U 表（backlog）——§2.5.96 立的那张表，这里复用不重解（§2.5.76）
U_REF = re.compile(r"蓝图 07 的 U(\d+) 表")


def u_done(num: str) -> bool | None:
    """蓝图 07 的 Un 表行是否已收尾（状态列不含「未完成」）。查不到返回 None。"""
    if not ROADMAP.exists():
        return None
    for l in ROADMAP.read_text(encoding="utf-8").split("\n"):
        if re.match(rf"^\|\s*\*\*U{num}\*\*\s*\|", l):
            return "未完成" not in l
    return None


def ms_done(num: str) -> bool | None:
    """蓝图 07 里程碑表里 Mn 是否已收尾（行内无 ⬜ / 未完成）。查不到返回 None。"""
    if not ROADMAP.exists():
        return None
    for l in ROADMAP.read_text(encoding="utf-8").split("\n"):
        if re.match(rf"^\|\s*\*\*M{num}\*\*\s*\|", l):
            return ("⬜" not in l) and ("未完成" not in l)
    return None


def probe(item: dict, sec_open: dict[str, int]) -> tuple[list[str], list[str]]:
    """返回 (命中的探针类, 发现的矛盾)。矛盾＝红。"""
    t, hits, bad = item["text"], [], []

    # P1 跨节待决引用：引用的节若已全部结清，而**本句**还说它「未处置」 ⇒ 过期陈述
    # 判据的接收者收窄到「引用所在的那一句」（见 sentence_at）；hits 仍按**引用次数**
    # 计（P5 口径一致：命中 ≠ 判红），只有 bad 收窄。
    for m in XREF.finditer(t):
        tgt = "2.5." + m.group(1)
        if tgt == item["sec"] or tgt not in sec_open:
            continue
        hits.append("P1")
        sent = sentence_at(t, m.start())
        if sec_open[tgt] == 0 and not ALREADY(m.group(1)).search(sent) \
                and PENDING_WORD.search(sent):
            bad.append(f"P1 引用 §{tgt}（该节 0 条待决未结）却仍称其未处置")

    # P2 里程碑引用：把待决挂在已收尾的里程碑上 ⇒ 触发点过期（§2.5.96 同源）
    for m in MS_REF.finditer(t):
        hits.append("P2")
        done = ms_done(m.group(1))
        if done and MS_HANG.search(t):
            bad.append(f"P2 挂在已收尾的 M{m.group(1)} 上（触发点已过期）")
        break

    # P3 文件与行号前提：文件须存在（行号只登记不判红——会随编辑漂移）
    # 收窄到**含目录分隔符**的名字（§2.5.66）：裸 `db.py` 可能靠运气撞上
    # 某个同名文件，「存在」就不再说得清「哪一份存在」。
    for m in FILE_REF.finditer(t):
        rel = m.group(1)
        if "/" not in rel:
            continue
        if not any((ROOT / p / rel).exists() for p in
                   ("", "scripts", "construction_agent", "construction_agent/web",
                    "docs/web", "construction_agent/evaluation")):
            continue
        hits.append("P3")
        break

    # P4 库表前提：条内点名的表须真在库里（库不可达 ⇒ 不降级成「查过没问题」）
    if TABLE_REF.search(t):
        hits.append("P4")

    # P5 计数前提：会随提交/编辑变化的计数
    if COUNT_REF.search(t):
        hits.append("P5")

    # P6 backlog 指向：挂在蓝图 07 的 U 表上 ⇒ 表行一收尾就该回来销账
    for m in U_REF.finditer(t):
        hits.append("P6")
        if u_done(m.group(1)):
            bad.append(f"P6 指向蓝图 07 的 U{m.group(1)} 表，而该行已收尾 ⇒ 该回来销账")
        break

    return sorted(set(hits)), bad


def git_counts() -> tuple[int, int] | None:
    """(worktree 领先, 主仓库领先)；不在 git 环境或分支不存在时返回 None。"""
    try:
        a = subprocess.run(["git", "rev-list", "--count", "sync-from-worktree..HEAD"],
                           cwd=ROOT, capture_output=True, text=True, timeout=20)
        b = subprocess.run(["git", "rev-list", "--count", "HEAD..sync-from-worktree"],
                           cwd=ROOT, capture_output=True, text=True, timeout=20)
        if a.returncode or b.returncode:
            return None
        return int(a.stdout.strip()), int(b.stdout.strip())
    except Exception:
        return None


# ============================================================ D 段：文档对账
# 本节把实测值写进了两份文档，那两处数字从写下那一刻起就会过期。
# 判据两端同源（都来自本次实测），故能抓"文档没跟上账本"这一类漂移。
ALIGN = Path(os.environ.get("C2597_ALIGN")
             or ROOT / "docs" / "web" / "09_端到端对齐核查.md")


def _norm(s: str) -> str:
    """折叠全部空白再比对。

    文档为了排版会在数字之间换行，逐字符比对会把「换行位置变了」误报成
    「数字变了」（§2.5.68 同族：判据要盯住语义那一格，不要盯住无关的形态）。
    """
    return re.sub(r"\s+", "", s)


def _tally_str(tally: dict[str, int]) -> str:
    return " · ".join(f"{k} {v}" for k, v in sorted(tally.items()))


def doc_checks(m: dict, tally: dict, hits_total: int) -> list[str]:
    """D 段：本节写进 01 / 09 号文档的数字 ↔ 本次实测。返回红的项 id。

    **正例 `MUST`**：每个实测值都必须在文档里出现；
    **反例 `FORBID`**：**上一代数字不得残留**——两代并存时读者分不清哪个是真的，
    而正例判据对此完全无感（旧值在场时新值照样命中）。反例是本节唯一能抓住
    「只改了文档一半」的判据（§2.5.88「加一处时别处的期望集合要跟着走」同族）。
    两条方向相反、互为失败路径，故都能被自证用例单独打红。
    """
    items, opens = m["items"], m["open"]
    closed, nsec = items - opens, m["sec"]
    probed, bare, pct = m["probed"], m["bare"], m["pct"]
    ts = _tally_str(tally)

    MUST: list[tuple[str, Path, str]] = [
        ("D1:01", DOC, f"#### 二、实测：{items} 条，{opens} 条仍在等裁决"),
        ("D2:01", DOC,
         f"切成条目 **{items}** 条（`open` **{opens}** / `closed` **{closed}**）"
         f"，分布在 **{nsec} 个节**："),
        ("D3:01", DOC, f"（合计） | **{items}**"),
        ("D4:01", DOC, f"命中 **{hits_total}** 次、落在 **{probed} 条**上，"
                       f"分档 `{ts}`；**{probed} / {opens}** 有可自动复核前提，"
                       f"其余 **{bare}** 条进"),
        ("D5:01", DOC, f"**{bare} 条（{pct}%）今天没有任何到期机制**"),
        ("D6:01", DOC, f"| A 切分 | {items} 条与逐字符人工核对吻合 |"),
        ("D7:09", ALIGN, f"**{items} 条**（`open` {opens} / `closed` {closed}）"),
        ("D8:09", ALIGN, f"分布在 **{nsec} 个节**"),
        ("D9:09", ALIGN, f"**{probed} 条**有可自动复核前提、**{bare} 条**进"),
        ("D10:09", ALIGN, f"命中分档 `{ts}`"),
    ]
    # 反例：写作时的上一代数字。每一条都取自 01 号文档里**只出现过一次**的形态
    # （实测过：裸 `57 条`/`34 条` 在 1 MB 文档里各有 5–6 处，拿来当反例等于误报）。
    FORBID: list[tuple[str, Path, str]] = [
        ("F1:01", DOC, "**18 个节**"),
        ("F2:01", DOC, "（合计） | **57**"),
        ("F3:01", DOC, "**16 / 50**"),
        ("F4:01", DOC, "**34 条（68%）今天没有任何到期机制**"),
        # 第二代（2026-10-05，§2.5.96 待决段加了一处跨节引用 ⇒ probed 19→20）。
        # 判据边界第 4 条：FORBID 要随每一代手工更新，否则反例退化成
        # 正例的复制品（旧值在场时新值照样命中，正例对此完全无感）。
        ("F7:01", DOC, "**35 条（65%）今天没有任何到期机制**"),
        ("F8:01", DOC, "命中 **24** 次"),
        ("F9:01", DOC, "**19 / 54**"),
        # 第三代（2026-10-05，§2.5.98 新增 3 条待决 ⇒ items 61→64 / open 54→57 /
        # probed 20→21 / bare 34→36 / hits 25→26 / P1 16→17）。**pct 恰好不动**
        # ——34/54 与 36/57 同为 63%，故第三代的 BARE 句只能**整句**登记，
        # 只比百分比会与当代数字撞车（那样反例就退化成"恒不命中"的摆设）。
        ("F10:01", DOC, "命中 **25** 次"),
        ("F11:01", DOC, "**20 / 54**"),
        ("F12:01", DOC, "**34 条（63%）今天没有任何到期机制**"),
        # 第四代（2026-10-05，§2.5.99 新增 3 条待决 ⇒ items 64→67 / open 57→60 /
        # nsec 20→21 / probed 21→23 / bare 36→37 / hits 26→28 / P1 17→19）。
        # **这一代 `pct` 也动了**（36/57 与 37/60 并不同百分点：63% → 62%），
        # 故 BARE 句仍整句登记；`F17` 收的是节数那一格（D8 盯 21，它盯 20）。
        ("F13:01", DOC, "命中 **26** 次"),
        ("F14:01", DOC, "**21 / 57**"),
        ("F15:01", DOC, "**36 条（63%）今天没有任何到期机制**"),
        ("F16:01", DOC, "实测：64 条，57 条仍在等裁决"),
        ("F17:01", DOC, "分布在 **20 个节**"),
        # 第五代（2026-10-05，§2.5.100 / §2.5.101 各新增 3 条待决 ⇒
        # items 67→73 / open 60→66 / nsec 21→23 / hits 28→31 / probed 23→26 /
        # bare 37→40 / P1 19→22）。**这一代 `pct` 也动了**（37/60 = 62% → 40/66 = 61%），
        # 故 BARE 句仍**整句**登记；`F22` 收的是节数那一格（D8 盯 23，它盯 22）。
        ("F18:01", DOC, "命中 **29** 次"),
        ("F19:01", DOC, "**24 / 63**"),
        ("F20:01", DOC, "**39 条（62%）今天没有任何到期机制**"),
        ("F21:01", DOC, "实测：70 条，63 条仍在等裁决"),
        ("F22:01", DOC, "分布在 **22 个节**"),
        ("F5:01", DOC, "57** 条"),
        ("F6:01", DOC, "50 条仍在等裁决"),
    ]
    cache: dict[Path, str] = {}

    def body(p: Path) -> str:
        if p not in cache:
            cache[p] = _norm(p.read_text(encoding="utf-8")) if p.exists() else ""
        return cache[p]

    red: list[str] = []
    for tag, p, needle in MUST:
        if not p.exists():
            red.append(f"{tag} 源不可达（{p.name}）")     # 不降级成"通过"（§2.5.67）
        elif _norm(needle) not in body(p):
            red.append(f"{tag} 文档缺实测值：{needle[:48]}")
    for tag, p, stale in FORBID:
        if p.exists() and _norm(stale) in body(p):
            red.append(f"{tag} 上一代数字残留：{stale}")
    return red


# ============================================================ 自证
def self_test() -> int:
    """切分器与探针的正反用例。"""
    cases: list[tuple[str, object, object]] = []
    # 切分：行首编号 / 圈码 / 条内引用不切 / 行首小数不切 / 段首残文不切
    cases.append(("切-两编号", [s for _, s in cut_items("1. 甲\n2. 乙")], ["1. 甲", "2. 乙"]))
    cases.append(("切-行内圈码", [s for _, s in cut_items("：①甲。②乙")],
                  ["①甲。", "②乙"]))
    cases.append(("切-条内引用不切", [s for _, s in cut_items("1. 见 §2.5.88 待决 ①的三份")],
                  ["1. 见 §2.5.88 待决 ①的三份"]))
    cases.append(("切-行首小数不切", len(cut_items("1. 甲\n0.95**——续行")), 1))
    cases.append(("切-段首标点丢弃", cut_items("：\n1. 甲"), [(2, "1. 甲")]))
    # 探针：P1 过期 vs 未过期
    so = {"2.5.68": 0, "2.5.69": 1}
    _, bad = probe({"sec": "2.5.69", "text": "§2.5.68 的两条待决**仍未处置**"}, so)
    cases.append(("P1-过期陈述报红", len(bad), 1))
    _, bad = probe({"sec": "2.5.69", "text": "§2.5.69 仍有一条待决"}, so)
    cases.append(("P1-本节自引不报", len(bad), 0))
    _, bad = probe({"sec": "2.5.69", "text": "§2.5.68 已结（§2.5.68）"}, so)
    cases.append(("P1-已结引用不报", len(bad), 0))
    # 探针：P1 的**接收者收窄到引用所在那一句**（§2.5.66 的统一形态；§2.5.99
    # 待决 2 真踩过——条目自己在问「要不要保留」，句尾才引 §2.5.68，整条搜
    # 「要不要」会把「本条的未决语气」误判成「本条称 §2.5.68 未处置」）。
    _, bad = probe({"sec": "2.5.69",
                    "text": "要不要保留它。倾向保留（§2.5.68 的规矩）。"}, so)
    cases.append(("P1-未决语气不在引句不报", len(bad), 0))
    _, bad = probe({"sec": "2.5.69",
                    "text": "要不要保留它。它**仍未处置**（§2.5.68）。"}, so)
    cases.append(("P1-引句真称未处置仍报", len(bad), 1))
    # 反向自证：「收窄」不是「放松成不报」——上面两条一正一反，缺一条就分不清
    cases.append(("P1-取句函数只切强标点", sentence_at("甲，乙；丙。丁", 4), "丙"))
    cases.append(("P1-取句函数不切逗号", sentence_at("甲，乙", 2), "甲，乙"))
    # 探针：P3 认得出文件、不把裸词当文件
    h, _ = probe({"sec": "2.5.69", "text": "见 `construction_agent/web/db.py:257`"}, so)
    cases.append(("P3-文件命中", "P3" in h, True))
    h, _ = probe({"sec": "2.5.69", "text": "见 `db.py` 那个文件"}, so)
    cases.append(("P3-路径带目录才算", "P3" in h, False))
    # 探针：P6 指向蓝图 07 的 U 表（U1 当前「未完成」⇒ 绿；收尾后应转红）。
    # 用例故意**不写**「登记在……」那个声明前缀——§2.5.96 的扫描分不出
    # 「作出声明」与「逐字引述声明」，测试字面量会被它当成一条真声明扫走
    # （该节待决 2 记过的坑）。这里测的是 U_REF 认不认得出指向，认得出即可。
    h, bad = probe({"sec": "2.5.89", "text": "见蓝图 07 的 U1 表"}, so)
    cases.append(("P6-U 表命中", "P6" in h, True))
    cases.append(("P6-U 表未收尾不报红", len(bad), 0))

    ok = 0
    for name, got, want in cases:
        good = got == want
        ok += good
        print(f"  {'✓' if good else '✗'} {name}: {got!r}" + ("" if good else f" ≠ {want!r}"))
    print(f"自证 {ok}/{len(cases)}")
    return 0 if ok == len(cases) else 1


# ============================================================ 注入式自证
def self_proof() -> int:
    """往**副本**注入五种病，断言红的**恰好**是哪几条（§2.5.68）。

    五例各打一类判据，且**只改被测判据看得见的那一处**——归因才唯一：
      P2  把 §2.5.89 待决 ① 的 U 表指向改回「在 M1 收尾时」
      P1  把 §2.5.69 待决 ③ 的划掉标记与「已结」字样抹掉
      P6  把蓝图 07 的 U1 行标成已收尾
      D 正例  把 01 号里那处命中次数改小 ⇒ 只有 D4 失守
      D 反例  把上一代节数塞回 01 号 ⇒ 只有 F1 失守（旧值残留）
    """
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d01 = Path(td) / "01.md"
        d07 = Path(td) / "07.md"
        shutil.copy2(DOC, d01)
        shutil.copy2(ROADMAP, d07)

        base = subprocess.run([sys.executable, __file__], capture_output=True,
                              text=True, timeout=180,
                              env={**os.environ, "C2597_DOC": str(d01),
                                   "C2597_ROADMAP": str(d07)})
        # 两条通道都须在基线上为空——只查 BADS 时，D 段已经红了也照样开跑，
        # 于是"注入后的红集合恰好是哪几条"这条断言从一开始就不成立。
        for chan in ("BADS", "DOCREDS"):
            if not re.search(rf"^{chan} \[\]$", base.stdout, re.M):
                print(f"✗ 基线就不干净（{chan} 非空），自证无意义：")
                print(base.stdout[-1200:])
                return 1
        print("  基线 ✓ 两条通道的红集合均为空")

        # 第 5 元是**断言通道**：前三条打 B 段（条目级矛盾），后两条打 D 段
        # （文档级失配）。两条通道的红集合都可机读——`BADS` / `DOCREDS`。
        # D 段是**方向相反的两半**，故要两例各打一次：一例让**正例**失守
        # （新值被改掉），一例让**反例**失守（旧值被塞回来）。只测其中之一，
        # 另一半就是"没接过失败路径的判据"（§2.5.62 立的规矩）。
        cases: list[tuple[str, Path, str, str, str, str]] = [
            # 锚点必须逐字等于文档里的真文本（否则替换落空），但**不能以连续
            # 字面量写出来**：`check_2596_handoff.py` 扫的是**源码文本**，会把
            # 这里的引述当成一条真声明（§2.5.96 待决 2 记的坑，这是第二次撞上）。
            # 拆成相邻两个字面量，运行时自动拼接、语义分毫不差，而源码里不再
            # 出现该短语的连续形态——**引述一律描述而非复述**在夹具上的落地。
            ("P2 U 指向改回已收尾的里程碑", d01,
             "①两份 `el` 的合并**登记在蓝图 07 的 U"
             "1 表**——取并集",
             "①两份 `el` 的合并在 M1 收尾时**取并集**",
             "[('2.5.89#1', 'P2')]", "BADS"),
            # 注入必须把该条的**两重已结标记一起清掉**：`~~划掉~~` 与
            #「→ 已结（§2.5.71）」任一存在，`CLOSED` 就判它已结、条目根本不进
            # C 段——只去划线会**静默失效**（红集合为空，看着像"判据不灵"，
            # 其实是用例没跑到）。§2.5.64「锚点写错＝用例根本没跑」的同一形态。
            ("P1 已结的跨节陈述恢复成未处置", d01,
             "3. ~~§2.5.68 的两条待决**仍未处置**：截图默认输出目录是否另开"
             " `docs/web/figures/`。~~\n"
             "   **→ 2026-10-05 已结（§2.5.71）**：默认值已改到 `docs/web/figures/`，"
             "并配了两条行为性判据。",
             "3. §2.5.68 的两条待决**仍未处置**：截图默认输出目录是否另开"
             " `docs/web/figures/`。",
             "[('2.5.69#3', 'P1')]", "BADS"),
            ("P6 U1 表标成已收尾", d07, "未完成", "✅ 已收尾",
             "[('2.5.89#1', 'P6'), ('2.5.95#1', 'P6')]", "BADS"),
            # —— D 段两例（文档面）——
            # 正例：把 01 号副本里那处命中次数改掉 ⇒ 只有 D4 失守。
            # 锚点里的 31 是**当前实测值**（账本每次跑都会打印同一个数）；
            # 若哪天探针分档变了，这条会因"锚点一次都没命中"而**响亮地**失败，
            # 不会静默失效（§2.5.64：锚点写错＝用例根本没跑）。**已经真发生过四次**：
            # 24→25、25→26、26→28、28→31 四次换代，锚点每次还写着上一代的数，
            # `--self-proof` 都当场抛 `AssertionError: 锚点一次都没命中`
            # ——**这比静默失效好，但说明锚点是要跟着每一代数字走的**
            # （§2.5.90：判据的设计前提会过期）。
            #
            # **一律改大、不改小**：改小的方向迟早撞上上一代登记值（24、25、26、29
            # 都已在 `FORBID`：F8:01 / F10:01 / F13:01 / F18:01），届时会同时打红
            # `D4:01` 与那条 `FORBID`，红集合两条 ⇒ 归因不唯一，这一例就不再证明
            # "只有 D4 灵敏度"。改大则新值必然不在名单上（名单里全是更小的历代值）。
            # 注入用例的取值因此**被反例名单约束**：名单越全，可选值越少。
            ("D 正例：01 号的命中次数被改掉", d01,
             "命中 **31** 次", "命中 **32** 次", "['D4:01']", "DOCREDS"),
            # 反例：把上一代口径**追加**进 01 号副本 ⇒ 上一代数字残留被判红。
            # **不能"改写现有那句"**：那句正被 MUST 的 D2 盯着，改写后 D2 也失守、
            # 红集合变成 `['D2:01', 'F1:01']`，归因不唯一（本轮实跑抓到，期望
            # 本是 `['F1:01']`）。追加法让 D2 仍绿、只有 F1 转红 ⇒ 两条方向相反的
            # 判据（MUST 管新值在场 / FORBID 管旧值不残留）**互为独立通道**。
            ("D 反例：01 号里塞回上一代的节数", d01,
             "在同一节里并存了一整轮没人发现。",
             "在同一节里并存了一整轮没人发现。（上一代口径：节数为 **18 个节**。）",
             "['F1:01']", "DOCREDS"),
        ]
        ok = 0
        for name, target, old, new, want, chan in cases:
            keep = target.read_text(encoding="utf-8")
            try:
                s = keep
                if name.startswith("P6"):
                    # 只改 U1 那一行的状态列，不碰 M1–M4
                    s = re.sub(r"(\| \*\*U1\*\* \|[^\n]*?)未完成", r"\1✅ 已收尾", s, count=1)
                    assert s != keep, "U1 行没匹配上"
                else:
                    # 命中数可以 >1（同一段文本在两节各写一遍），但**不能是 0**
                    # ——0 次＝锚点写错＝用例根本没跑（§2.5.64）
                    assert s.count(old) >= 1, "锚点一次都没命中"
                    s = s.replace(old, new)
                target.write_text(s, encoding="utf-8")
                r = subprocess.run([sys.executable, __file__], capture_output=True,
                                   text=True, timeout=180,
                                   env={**os.environ, "C2597_DOC": str(d01),
                                        "C2597_ROADMAP": str(d07)})
                got = re.search(rf"^{chan} (.+)$", r.stdout, re.M)
                got = got.group(1) if got else "<无输出>"
                hit = got == want
                ok += hit
                print(f"  {'✓' if hit else '✗'} {name}: {got}"
                      + ("" if hit else f" ≠ {want}"))
            finally:
                target.write_text(keep, encoding="utf-8")
        print(f"自证 {ok}/{len(cases)}")
        return 0 if ok == len(cases) else 1


# ============================================================ 主检
def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--self-proof" in sys.argv:
        return self_proof()

    items = split_doc()
    opens = [i for i in items if not i["closed"]]
    # 待决节**全体**入表（含 open 数为 0 的）——只登记 open>0 的节，
    # 「引用的那一节已全部结清」这个最该报红的场景会因查不到键而静默跳过
    sec_open: dict[str, int] = {i["sec"]: 0 for i in items}
    for i in opens:
        sec_open[i["sec"]] += 1

    print(f"待决条 {len(items)} 条（open {len(opens)} / closed {len(items) - len(opens)}）")
    by_sec: dict[str, int] = {}
    for i in items:
        by_sec[i["sec"]] = by_sec.get(i["sec"], 0) + 1
    print("  各节条数：" + "  ".join(f"§{s} {n}" for s, n in sorted(by_sec.items())))

    probed, bare, bads = [], [], []
    print("\n" + "=" * 74 + "\nC 段 逐条扫「到期机制」\n" + "=" * 74)
    for it in opens:
        hits, bad = probe(it, sec_open)
        if hits:
            probed.append((it, hits))
        else:
            bare.append(it)
        for b in bad:
            bads.append((it, b))

    print(f"有可自动复核前提 {len(probed)} 条 / 无前提 {len(bare)} 条 "
          f"/ 合计 {len(probed) + len(bare)}")
    print("\n命中的探针分档：")
    tally: dict[str, int] = {}
    for _, hs in probed:
        for h in hs:
            tally[h] = tally.get(h, 0) + 1
    print("  " + _tally_str(tally))
    print(f"\n★ 无任何到期机制 {len(bare)} 条（本节结论，不判红）：")
    for it in bare:
        print(f"  · §{it['key']} L{it['line']}  {it['text'][:70]}")

    print("\n" + "=" * 74 + "\nB 段 矛盾与闭式\n" + "=" * 74)
    if bads:
        for it, b in bads:
            print(f"  ✗ §{it['key']} L{it['line']}  {b}")
    else:
        print("  ✓ 无自相矛盾的引用")
    if len(bare) == 0:
        print("  ✗ 无前提清单为空 —— 与「散落待决没有到期机制」的命题不符，判据可能失效")
        bare_problem = True
    else:
        bare_problem = False
    g = git_counts()
    if g:
        n = COUNT_REF.findall(  # 账本里写死的提交数须与实测一致
            "".join(i["text"] for i in opens))
        print(f"  提示 实测提交数：worktree 领先 {g[0]}、主仓库领先 {g[1]}"
              f"（账本内有 {len(n)} 处计数声明）")
    else:
        print("  提示 取不到 git 计数（跳过，不降级成通过）")

    marks = {"items": len(items), "open": len(opens), "probed": len(probed),
             "bare": len(bare), "bad": len(bads)}
    print(f"\nMARKS {marks}")
    # 机器可读的红集合：自证要断言「红的**恰好**是哪几条」（§2.5.68），
    # 解析中文输出行太脆（§2.5.69 踩过）
    print(f"BADS {sorted((it['key'], b.split()[0]) for it, b in bads)}")

    print("\n" + "=" * 74 + "\nD 段 文档对账（本节写出去的数字 ↔ 本次实测）\n" + "=" * 74)
    docred = doc_checks(
        {"items": len(items), "open": len(opens), "sec": len(by_sec),
         "probed": len(probed), "bare": len(bare),
         "pct": round(len(bare) * 100 / len(opens))},
        tally, sum(tally.values()))
    if docred:
        for d in docred:
            print(f"  ✗ {d}")
    else:
        print("  ✓ 01 / 09 号文档里的数字与本次实测一致，且无上一代数字残留")
    print(f"DOCREDS {sorted(d.split()[0] for d in docred)}")

    rc = 0
    if bads or bare_problem or docred:
        rc = 1
    print(f"\n合计待处置：{len(bads)} 处矛盾 / {len(docred)} 处文档失配"
          + ("" if rc == 0 else "  ⇒ 退出码 1"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
