#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文侧待裁决项账本 —— 把《修订对账记录》里散落的「待决」汇总成一张会过期的表。

为什么要这个脚本
----------------
`docs/paper_v2/修订对账记录.md` 自第十五节起，每一节末尾都写着「待处置（须作者
裁决）」。它们读起来像待办，但**是自然语言**：没有标识符、没有状态字段、写下
那一刻为真、之后不会自我更新。已有四处实测证明这句话的代价：

  * §14.6#2 称「Cover letter 的 V2 版尚未起草」，而 `cover_letter_v2.md` 早已存在；
  * §16.7#1 称 `evaluation/experiment1|2|3` 只剩 `__pycache__`，而 17 个 `.py`
    已回收到位——**且同一笔债在 §28.6#1 没被标结清**，两处状态互相矛盾；
  * §17.7#2 建议「补留 CLIP 运行记录」，而磁盘 `imageV3/` 目录本身就是清单；
  * §19.7#1「图 5/6 数值手抄」已由第二十二节改为现读，条目里挂着删除线。

这与 web 侧 §2.5.97 的 `check_2597_pending_ledger.py` 是同一个病，只是落在论文侧。

判据的分工（本脚本唯一的设计要点）
----------------------------------
每条待决登记四态之一，**只有 open / done 两态配探针**：

  open    仍待裁决（含技术遗留）。探针 = 「原始前提今天**仍成立**」= True。
  done    已兑现、但可能没销账。探针 = 「原始前提**已消失**」= False。
  decide  真·作者裁决项（内容取舍）。**不许配探针**——裁决项没有事实前提，
          给它挂探针就是给主观判断伪造一个客观判据。
  note    结论性观察 / 后果说明。**不许配探针**，同上。

判决只有两种会红：

  open + 探针 False ⇒ `STALE-CLOSED`   「前提消失了，该销账」
  done + 探针 True  ⇒ `STALE-REOPENED` 「前提复活了，登记过期」

元判据 M1/M2 钉死上面那条分工；M3/M4 防漏登记——§2.5.97 的教训是「清单转录型
脚本必须核对条目总数，不能只看退出码」。

不起服务、不写任何库：主库经 `scripts._common.main_db_path()` 派生后以 `mode=ro` 打开。

环境变量（供 --drift-test 把判据指向副本，真文件一字不动）：
  C38_SRC  覆盖《修订对账记录》路径
  C38_DOC  覆盖《待裁决项总账》路径
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from _common import main_db_path  # noqa: E402

SRC = Path(os.environ.get("C38_SRC") or ROOT / "docs" / "paper_v2" / "修订对账记录.md")
DOC = Path(os.environ.get("C38_DOC") or ROOT / "docs" / "paper_v2" / "待裁决项总账.md")
PAPER = ROOT / "docs" / "paper_v2"
SCRIPTS = ROOT / "scripts"
_DB = Path(main_db_path())
MAIN = _DB.parent.parent                 # 主仓库根（主库在 <main>/data/ 下）
IMAGEV3 = _DB.parent / "imageV3"         # CLIP 过滤产物目录（表 4 / 图 1 的源）

# 占位符：中英两种写法都要认（实测 EN 写 [Supervisor name]、ZH 写 [导师姓名]）
SUP_RE = re.compile(r"\[Supervisor name\]|\[导师姓名\]")
SUP_FILES = {
    "EN": PAPER / "eswa_paper_v2.md",
    "ZH": PAPER / "eswa_paper_v2_zh.md",
    "CL2": PAPER / "cover_letter_v2.md",
    "CL1": ROOT / "docs" / "paper" / "cover_letter.md",   # V1 存档件
}


# ============================================================ A 段：切分
HEAD = re.compile(r"^###\s+(\d+\.\d+)\s+(.*)$")
SEC_END = re.compile(r"^#{1,3} ")
NUM_ITEM = re.compile(r"^\s*\d+\.\s+\S")
DASH_ITEM = re.compile(r"^\s*[-*]\s+\S")


def cut_doc(text: str) -> list[dict]:
    """从《修订对账记录》切出所有「待决」小节，附其编号条目。

    命中条件：小节标题含「待」或「作者」（§37.7 的标题「三条作者裁决项」无「待」
    字，只靠「待」会漏——实测漏过一整节）。
    条目形态两种：编号行 `1. ` 与 `-` 列表项；**`-` 形态只在整节无编号条目时启用**，
    否则正文里的普通项目符号会被误收成待决。
    """
    lines = text.splitlines()
    secs: list[dict] = []
    cur: dict | None = None
    for i, s in enumerate(lines):
        m = HEAD.match(s)
        if m:
            if cur:
                cur["end"] = i
                secs.append(cur)
            cur = {"num": m.group(1), "title": m.group(2).strip(),
                   "start": i, "body": []}
            continue
        if cur is not None:
            if SEC_END.match(s):
                cur["end"] = i
                secs.append(cur)
                cur = None
            else:
                cur["body"].append(s)
    if cur:
        cur["end"] = len(lines)
        secs.append(cur)

    out = []
    for s in secs:
        if "待" not in s["title"] and "作者" not in s["title"]:
            continue
        nums = [b for b in s["body"] if NUM_ITEM.match(b)]
        dashes = [b for b in s["body"] if DASH_ITEM.match(b)]
        items = nums if nums else dashes
        kind = "num" if nums else "dash"
        keys = [f"{s['num']}#{k + 1}" for k in range(len(items))]
        out.append({"num": s["num"], "title": s["title"], "start": s["start"],
                    "end": s["end"], "kind": kind, "items": items, "keys": keys})
    return out


def cut_keys() -> tuple[list[str], list[dict]]:
    secs = cut_doc(SRC.read_text(encoding="utf-8"))
    keys = [k for s in secs for k in s["keys"]]
    return keys, secs


# ============================================================ B 段：登记面
# (key, section, state, probe, note)
#   state ∈ open / done / decide / note   —— open/done 必须配探针，decide/note 不许配
LEDGER: list[tuple[str, str, str, str | None, str]] = [
    # ---- §6.5 遗留问题（`-` 列表形态）----
    ("6.5#1", "6.5", "open", "zero_code", "5 条零编码事故：3 条真事故可补编码、2 条非事故文档可移出"),
    ("6.5#2", "6.5", "note", None, "无独立手工采集文件——结论性观察，无事可做"),
    # ---- §7.3 本轮新增待办（`-` 列表形态）----
    ("7.3#1", "7.3", "decide", None, "表 10 末行 2.86pt 溢出：第十三节已判可接受，属作者追认"),
    # ---- §14.6 ----
    ("14.6#1", "14.6", "decide", None, "五条 Highlights 的取舍与措辞"),
    ("14.6#2", "14.6", "done", "cover_v2", "Cover letter V2 已起草"),
    ("14.6#3", "14.6", "open", "supervisor", "导师真实姓名替换占位符"),
    # ---- §15.5 ----
    ("15.5#1", "15.5", "decide", None, "投稿信三条契合理由的表述"),
    ("15.5#2", "15.5", "open", "reviewer_list", "推荐审稿人名单（尚无文件）"),
    ("15.5#3", "15.5", "open", "supervisor", "导师姓名（与 14.6#3 同一笔债）"),
    ("15.5#4", "15.5", "done", "v1_archived", "V1 投稿信标『存档，勿投』"),
    # ---- §16.7 ----
    ("16.7#1", "16.7", "done", "eval_py", "evaluation/experiment1|2|3 源已回收到位"),
    ("16.7#2", "16.7", "done", "t7_json", "六策略产物已复制进本 worktree"),
    ("16.7#3", "16.7", "decide", None, "表 7 两列用哪一批（与 25.6#2 / 26.7#1 同一笔债）"),
    ("16.7#4", "16.7", "note", None, "表 7 复现口径已改动过一版——后果说明"),
    # ---- §17.7 ----
    ("17.7#1", "17.7", "decide", None, "表 3 的 2.9%→2.8% 请过目"),
    ("17.7#2", "17.7", "done", "imagev3", "CLIP 226 的源已查明（磁盘目录即清单）"),
    ("17.7#3", "17.7", "decide", None, "表 1 年代列口径（与 27.6#1 同一件事）"),
    # ---- §18.6 ----
    ("18.6#1", "18.6", "decide", None, "表 2 关键词列表述强度（可选微调表注）"),
    ("18.6#2", "18.6", "note", None, "hfacs.py 旧映射不在范围——范围声明"),
    # ---- §19.7 ----
    ("19.7#1", "19.7", "done", "fig_hardcode", "图 4/5/6 数值已改为现读"),
    ("19.7#2", "19.7", "note", None, "与 18.6#2 重复——旧体系不在范围"),
    # ---- §20.7 ----
    ("20.7#1", "20.7", "done", "tex_log", "TeX 编译日志已落盘"),
    ("20.7#2", "20.7", "note", None, "drift 未覆盖 TeX/PDF 的内容漂移——判据边界"),
    # ---- §21.6 ----
    ("21.6#1", "21.6", "note", None, "同上（20.7#2 的重写）"),
    ("21.6#2", "21.6", "note", None, "Missing character 的中文字形检测有天然上限"),
    # ---- §22.6 ----
    ("22.6#1", "22.6", "note", None, "图 4 的 cost.total 键名有误导性——不宜在定稿期动"),
    ("22.6#2", "22.6", "open", "gen_fig_wt", "gen_paper_figures.py 的 WT 仍由 __file__ 推导"),
    # ---- §23.6 ----
    ("23.6#1", "23.6", "note", None, "PDF 端『缺 token』无法区分真缺与提取不到"),
    ("23.6#2", "23.6", "note", None, ".tex 的段落文本仍无核验"),
    # ---- §24.6 ----
    ("24.6#1", "24.6", "note", None, "与 23.6#1 重复"),
    ("24.6#2", "24.6", "note", None, "PDF 其它维度仍无失败路径"),
    ("24.6#3", "24.6", "note", None, "garbage=4, clean=True 的副作用未穷尽"),
    # ---- §25.6 ----
    ("25.6#1", "25.6", "open", "image_gap", "主库 image_files 少登记 15 条 imageV3 记录"),
    ("25.6#2", "25.6", "decide", None, "表 7 两列仍无源（与 26.7#1 同一笔债）"),
    ("25.6#3", "25.6", "decide", None, "CLIP 阈值与模型版本无权重快照哈希"),
    # ---- §26.7 ----
    ("26.7#1", "26.7", "decide", None, "表 7 两列三选一（建议①用落盘批重算）"),
    ("26.7#2", "26.7", "note", None, "若选①则 S0/S3/S4 编码率与六格耗时都要改——后果说明"),
    ("26.7#3", "26.7", "note", None, "V1 那批 run 的产物建议不必再找"),
    # ---- §27.6 ----
    ("27.6#1", "27.6", "decide", None, "表头 Accident period → Era block"),
    ("27.6#2", "27.6", "note", None, "改表头则四格式需重出——后果说明"),
    ("27.6#3", "27.6", "decide", None, "各来源的实测跨度是否要在论文里出现"),
    # ---- §28.6 ----
    ("28.6#1", "28.6", "done", "eval_py", "（与 16.7#1 同一笔债，此处旧描述已被实测推翻）"),
    ("28.6#2", "28.6", "open", "t7_fallback", "回退路径 _T7_FALLBACK 只为兼容未同步的旧检出"),
    # ---- §37.7 ----
    ("37.7#1", "37.7", "decide", None, "正文 where page structure permits 与代码不符"),
    ("37.7#2", "37.7", "decide", None, "B2 文案 per collector 的口径"),
    ("37.7#3", "37.7", "decide", None, "B3 entirely serial 的时态"),
]

# 探针与登记面之间必须是**集合相等**（§2.5.74：过期登记同样报红）
EXPECT_TOTAL = 46
EXPECT_STATE = {"open": 7, "done": 8, "decide": 15, "note": 16}
EXPECT_SECS = 18


class ProbeError(RuntimeError):
    """源不可达 —— 报红，不得降级成 False（§2.5.67）。"""


def _read(p: Path) -> str:
    if not p.exists():
        raise ProbeError(f"文件不存在：{p}")
    return p.read_text(encoding="utf-8")


# ------------------------------------------------ 13 个探针（返回「原始前提是否仍成立」）
def p_zero_code():
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM accidents a WHERE NOT EXISTS "
            "(SELECT 1 FROM caics_causes c WHERE c.accident_id = a.id)").fetchone()[0]
    finally:
        con.close()
    return n > 0, f"零编码事故 {n} 条"


def p_supervisor():
    tot, parts = 0, []
    for nm, p in SUP_FILES.items():
        t = p.read_text(encoding="utf-8") if p.exists() else ""
        k = len(SUP_RE.findall(t))
        tot += k
        parts.append(f"{nm} {k}")
    return tot > 0, f"占位符合计 {tot} 处（{' / '.join(parts)}）"


def p_reviewer_list():
    hits = [str(p.relative_to(PAPER)) for p in PAPER.rglob("*")
            if p.is_file() and re.search(r"review|审稿|推荐", p.name, re.I)]
    return not hits, f"匹配文件 {len(hits)} 个"


def p_eval_py():
    cnt = {d: len(list((ROOT / "construction_agent" / "evaluation" / d).glob("*.py")))
           for d in ("experiment1", "experiment2", "experiment3")}
    n = sum(cnt.values())
    # 主仓库那一侧（本会话不可写）——只作观察，不参与判定
    obs = {}
    for d in ("experiment1", "experiment2", "experiment3"):
        q = MAIN / "construction_agent" / "evaluation" / d
        obs[d] = len(list(q.glob("*.py"))) if q.exists() else -1
    return n == 0, (f"本 worktree .py 合计 {n} 个 {cnt}；主仓库侧 {obs}（-1＝目录不存在，仅观察）")


def p_t7_json():
    base = ROOT / "experiments" / "experiment_6strategies"
    n = len(list(base.glob("S*_result/*.json"))) if base.exists() else 0
    return n == 0, f"本地 S*_result/*.json {n} 个"


def p_imagev3():
    if not IMAGEV3.exists():
        raise ProbeError(f"imageV3 目录不存在：{IMAGEV3}")
    n = len([p for p in IMAGEV3.rglob("*") if p.is_file()])   # 必须递归（顶层 0 个文件）
    return n == 0, f"磁盘递归文件 {n} 个"


def p_tex_log():
    d = PAPER / ".build"
    n = len(list(d.glob("*.log"))) if d.exists() else 0
    return n == 0, f".build/*.log {n} 个"


def p_cover_v2():
    p = PAPER / "cover_letter_v2.md"
    return not p.exists(), f"cover_letter_v2.md 存在={p.exists()}"


def p_v1_archived():
    t = _read(ROOT / "docs" / "paper" / "cover_letter.md")[:400]
    return "存档，勿投" not in t, f"开头 400 字含『存档，勿投』={'存档，勿投' in t}"


def p_fig_hardcode():
    src = _read(SCRIPTS / "gen_paper_figures.py")
    fns = ["ablation_f1", "agreement_ci", "expert_band", "expert_n", "sampling_n"]
    missing = [f for f in fns if f"def {f}(" not in src]
    return bool(missing), f"现读函数缺 {len(missing)} 个 {missing}"


def p_gen_fig_wt():
    src = _read(SCRIPTS / "gen_paper_figures.py")
    has_env = bool(re.search(r"(?m)^WT\s*=.*os\.environ", src))
    return not has_env, f"WT 定义行含 os.environ={has_env}"


def p_image_gap():
    if not IMAGEV3.exists():
        raise ProbeError(f"imageV3 目录不存在：{IMAGEV3}")
    disk = len([p for p in IMAGEV3.rglob("*") if p.is_file()])
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    try:
        db = con.execute("SELECT COUNT(*) FROM image_files "
                         "WHERE local_path LIKE 'imageV3%'").fetchone()[0]
    finally:
        con.close()
    return db < disk, f"库登记 {db} < 磁盘 {disk}"


def p_t7_fallback():
    src = _read(SCRIPTS / "check_tables_v2.py")
    has = bool(re.search(r"\b_T7_FALLBACK\b", src))
    return has, f"源码含 \\b_T7_FALLBACK\\b={has}"


PROBES = {
    "zero_code": (p_zero_code, "库里仍存在零编码事故"),
    "supervisor": (p_supervisor, "论文/投稿信仍存在导师占位符"),
    "reviewer_list": (p_reviewer_list, "仍没有推荐审稿人名单文件"),
    "eval_py": (p_eval_py, "evaluation 源仍只剩 .pyc"),
    "t7_json": (p_t7_json, "六策略产物仍不在本 worktree"),
    "imagev3": (p_imagev3, "CLIP 图片仍无源可核（磁盘目录为空）"),
    "tex_log": (p_tex_log, "TeX 编译日志仍未落盘"),
    "cover_v2": (p_cover_v2, "Cover letter V2 仍未起草"),
    "v1_archived": (p_v1_archived, "V1 投稿信仍未标『存档，勿投』"),
    "fig_hardcode": (p_fig_hardcode, "图内数值仍手抄（现读函数不全在）"),
    "gen_fig_wt": (p_gen_fig_wt, "gen_paper_figures.py 的 WT 仍无环境变量覆盖"),
    "image_gap": (p_image_gap, "主库 imageV3 登记数仍少于磁盘"),
    "t7_fallback": (p_t7_fallback, "check_tables_v2.py 仍保留 _T7_FALLBACK 回退路径"),
}


# ============================================================ 判决
RED_OPEN, RED_DONE = "STALE-CLOSED", "STALE-REOPENED"


def verdict(state: str, probe_val: bool | None) -> str:
    """返回判决串；只有 STALE-* 两种会红。"""
    if state == "open":
        return "open-confirmed" if probe_val else RED_OPEN
    if state == "done":
        return "done-confirmed" if probe_val is False else RED_DONE
    return "no-probe"          # decide / note：只登记不判定


def run_probes() -> tuple[dict[str, tuple[bool, str]], list[str]]:
    vals, errs = {}, []
    for name, (fn, _desc) in PROBES.items():
        try:
            vals[name] = fn()
        except ProbeError as e:
            errs.append(f"{name}: {e}")
            vals[name] = (False, f"源不可达：{e}")
    return vals, errs


# ============================================================ 元判据
def meta_checks(cut_key_list: list[str], cut_secs: list[dict]) -> tuple[list[str], list[str]]:
    """M1/M2 探针分工；M3 双向键集；M4 总数与节数。返回 (错, 计数说明)。"""
    errs, msgs = [], []
    reg_keys = [r[0] for r in LEDGER]
    reg_secs = sorted({r[1] for r in LEDGER}, key=lambda s: [int(x) for x in s.split(".")])

    # ⚠ 以下错误串的**首 token 就是红键**（`main()` 用 `_key_of()` 取首 token）。
    # 塌成一个 `M1`/`M3` 会让「红的恰好是哪几条」失去分辨力：改标题（该节整批
    # 切不到）与插一条编号（多出一个键）是两种性质完全不同的违规，塌成同一个键
    # 后两者的红集合一模一样（§2.5.68 只断言退出码太弱 / §2.5.93 归因不唯一）。
    # 故条目级违规一律**逐条报、首 token 用条目键**。

    # M1/M2：探针分工
    for key, sec, state, probe, _note in LEDGER:
        if state in ("open", "done") and probe is None:
            errs.append(f"{key} M1 违反：{state} 未配探针")
        if state in ("decide", "note") and probe is not None:
            errs.append(f"{key} M2 违反：{state} 不该配探针（{probe}）")
        if probe is not None and probe not in PROBES:
            errs.append(f"{key} M2 违反：引用了未定义的探针 {probe!r}")

    # M3：登记面 ↔ 实测面（双向）
    for k in reg_keys:
        if k not in cut_key_list:
            errs.append(f"{k} M3 登记了却切不到")
    for k in cut_key_list:
        if k not in reg_keys:
            errs.append(f"{k} M3 切到了却没登记")

    # M4：总数 / 状态分布 / 节数（⚠ 五条**各自独立编号**——它们会由同一次注入
    # 连带触发，写成一个 `M4` 就分不清是哪一条整体判据出问题了）
    if len(LEDGER) != EXPECT_TOTAL:
        errs.append(f"M4a 登记条目 {len(LEDGER)} ≠ 期望 {EXPECT_TOTAL}")
    if len(cut_key_list) != EXPECT_TOTAL:
        errs.append(f"M4b 实测切分 {len(cut_key_list)} ≠ 期望 {EXPECT_TOTAL}")
    tally = {}
    for _k, _s, st, _p, _n in LEDGER:
        tally[st] = tally.get(st, 0) + 1
    if tally != EXPECT_STATE:
        errs.append(f"M4c 状态分布 {tally} ≠ 期望 {EXPECT_STATE}")
    if len(reg_secs) != EXPECT_SECS:
        errs.append(f"M4d 小节数 {len(reg_secs)} ≠ 期望 {EXPECT_SECS}")
    # 账本内的 section 必须都真的被切出来（防「节号写错、条目还在」）
    cut_sec_set = {s["num"] for s in cut_secs}
    bad = [s for s in reg_secs if s not in cut_sec_set]
    if bad:
        errs.append(f"M4e 登记的小节切不到：{bad}")

    msgs.append(f"登记 {len(LEDGER)} 条 / 实测切分 {len(cut_key_list)} 条")
    msgs.append(f"状态分布 {tally}")
    msgs.append(f"小节 {len(reg_secs)} 个：{reg_secs}")
    return errs, msgs


# ============================================================ D 段：文档对账
DOC_ANCHORS = [
    ("条目总数", "**46 条**"),
    ("四态分布", "open 7 / done 8 / decide 15 / note 16"),
    ("小节数", "**18 个**待决小节"),
    ("探针数", "**13 个**探针"),
    ("已兑现未销账", "**8 条**"),
    ("真裁决项", "**15 条**"),
]


def doc_checks() -> tuple[list[str], list[str]]:
    errs, msgs = [], []
    if not DOC.exists():
        errs.append(f"D0 总账文档不存在：{DOC}")
        return errs, msgs
    t = DOC.read_text(encoding="utf-8")
    # ⚠ 锚点逐个编号（D1..D6）。全写 D1 的话多个锚点未命中会塌成一个键，
    # 「红的恰好是哪几条」这条断言就失去分辨力（§2.5.68）。
    for i, (name, anchor) in enumerate(DOC_ANCHORS, 1):
        if anchor not in t:
            errs.append(f"D{i} 锚点未命中：{name} → {anchor!r}")
    msgs.append(f"总账文档 {DOC.name}（{len(t)} 字符）核 {len(DOC_ANCHORS)} 个锚点")
    return errs, msgs


# ============================================================ 主检
def _key_of(err: str) -> str:
    """从错误串取稳定标识（首 token，如 M1 / D0 / D3）。

    §2.5.68：只断言「退出码非 0」太弱——红的必须能精确到哪一条判据，
    漂移用例才分得清「谁抓到的」。"""
    return err.split(maxsplit=1)[0] if err.strip() else "?"


def main() -> int:
    args = set(sys.argv[1:])
    quiet = "--quiet" in args
    show_marks = "--marks" in args          # ⚠ 不与 quiet 共用开关（§2.5.101）

    def say(s: str) -> None:
        if not quiet:
            print(s)

    def add_red(k: str) -> None:
        if k not in reds:
            reds.append(k)

    cut_key_list, cut_secs = cut_keys()
    probe_vals, probe_errs = run_probes()

    reds: list[str] = []
    say("=" * 78)
    say("A/B 段：账本 ↔ 实测切分面")
    say("=" * 78)
    meta_errs, meta_msgs = meta_checks(cut_key_list, cut_secs)
    for m in meta_msgs:
        say("  " + m)
    if probe_errs:
        # ⚠ 源不可达必须判红，不得降级成「探针返回 False」——那样
        # 「源没了」与「前提真的不成立了」在输出上一模一样（§2.5.67）。
        say(f"  🔴 探针源不可达 {len(probe_errs)}：{probe_errs}")
        for e in probe_errs:
            add_red("PE:" + e.split(":", 1)[0])

    say("")
    say("C 段：逐条判决")
    for key, sec, state, probe, note in LEDGER:
        pv, detail = (probe_vals.get(probe) if probe else (None, "（不配探针）"))
        v = verdict(state, pv)
        bad = v in (RED_OPEN, RED_DONE)
        if probe is None and state in ("open", "done"):
            bad = True                     # M1 违反也计入红
            detail = "未配探针"
        if probe is not None and state in ("decide", "note"):
            bad = True                     # M2 违反也计入红
        if bad:
            reds.append(key)
        flag = "🔴" if bad else "  "
        say(f"  {flag} [{state:<6}] {key:<8} {v:<15} {note}")
        say(f"          └ 探针 {probe or '—'}：{detail}")

    say("")
    say("M 段：元判据")
    for e in meta_errs:
        say("  🔴 " + e)
        add_red(_key_of(e))
    if not meta_errs:
        say("  ✓ M1/M2 探针分工、M3 双向键集、M4 总数与节数 全通过")

    say("")
    say("D 段：文档对账")
    d_errs, d_msgs = doc_checks()
    for m in d_msgs:
        say("  " + m)
    for e in d_errs:
        say("  🔴 " + e)
        add_red(_key_of(e))
    if not d_errs:
        say("  ✓ 锚点全中")

    say("")
    say("=" * 78)
    say(f"合计待处置：{len(reds)} 条" + (f" → {reds}" if reds else ""))
    say("=" * 78)

    if show_marks:
        print("MARKS " + json.dumps({"reds": sorted(reds), "n": len(LEDGER)},
                                    ensure_ascii=False))
    return 1 if reds else 0


# ============================================================ 自证
def _chk(name: str, cond: bool, fails: list[str]) -> None:
    print(("  ✓ " if cond else "  ✗ ") + name)
    _CHK_N.append(name)
    if not cond:
        fails.append(name)


def _mk(text: str) -> list[dict]:
    """用临时文件跑切分（复用 cut_doc 的纯文本接口）。"""
    return cut_doc(text)


# ⚠ 用例台账：分母由它算，**不写死**。写死时「全绿恰好凑上写死的数」会让
# 计数偏差永远不显形——首版分母写 16、实际 19 条，输出 16/16 看着没问题
# （§2.5.64 恒真判据 / §2.5.97 清单转录型脚本必须核对条目总数）。
_CHK_N: list[str] = []


def self_test() -> int:
    _CHK_N.clear()
    fails: list[str] = []
    print("== 切分器 ==")
    t_num = ("### 9.1 待处置\n\n1. 第一条\n2. 第二条\n\n### 9.2 别的\n")
    _chk("编号行被收", [s["num"] for s in _mk(t_num)] == ["9.1"], fails)
    _chk("编号条目计数=2", _mk(t_num)[0]["keys"] == ["9.1#1", "9.1#2"], fails)

    t_dash = "### 9.3 待作者决定\n\n- 甲\n- 乙\n"
    _chk("`-` 行在无编号时被收", _mk(t_dash)[0]["kind"] == "dash", fails)
    _chk("`-` 条目计数=2", len(_mk(t_dash)[0]["keys"]) == 2, fails)

    t_mix = "### 9.4 待处置\n\n1. 甲\n\n- 这不是待决，是正文项目符号\n"
    _chk("有编号时 `-` 行不被收", _mk(t_mix)[0]["kind"] == "num"
         and len(_mk(t_mix)[0]["keys"]) == 1, fails)

    t_none = "### 9.5 已处置\n\n1. 甲\n"
    _chk("标题不含『待』『作者』的节不收", _mk(t_none) == [], fails)

    t_sec = "### 9.6 待处置\n\n1. 甲\n\n## 十、正文\n\n2. 这不是条目\n"
    _chk("父级标题终止小节", len(_mk(t_sec)[0]["keys"]) == 1, fails)

    print("== 判决 ==")
    _chk("open + True ⇒ 绿", verdict("open", True) == "open-confirmed", fails)
    _chk("open + False ⇒ STALE-CLOSED", verdict("open", False) == RED_OPEN, fails)
    _chk("done + False ⇒ 绿", verdict("done", False) == "done-confirmed", fails)
    _chk("done + True ⇒ STALE-REOPENED", verdict("done", True) == RED_DONE, fails)
    _chk("decide 恒不判红", verdict("decide", None) == "no-probe", fails)
    _chk("note 恒不判红", verdict("note", None) == "no-probe", fails)

    print("== 元判据（用合成 LEDGER）==")
    global LEDGER
    keep = LEDGER
    try:
        LEDGER = [("9.1#1", "9.1", "open", None, "x")]
        e, _ = meta_checks(["9.1#1"], [{"num": "9.1"}])
        _chk("M1：open 未配探针 ⇒ 报错",
             any("M1 违反" in s for s in e), fails)
        # ⚠ 首 token 必须是**条目键**——塌成 `M1` 会让「红的恰好是哪几条」失去分辨力
        _chk("M1 红键是条目键（不是 M1）",
             _key_of(next(s for s in e if "M1 违反" in s)) == "9.1#1", fails)

        LEDGER = [("9.1#1", "9.1", "decide", "cover_v2", "x")]
        e, _ = meta_checks(["9.1#1"], [{"num": "9.1"}])
        _chk("M2：decide 配了探针 ⇒ 报错",
             any("M2 违反" in s for s in e), fails)

        LEDGER = [("9.1#1", "9.1", "open", "no_such_probe", "x")]
        e, _ = meta_checks(["9.1#1"], [{"num": "9.1"}])
        _chk("M2 红键是条目键（未定义的探针）",
             _key_of(next(s for s in e if "未定义的探针" in s)) == "9.1#1", fails)

        LEDGER = [("9.1#1", "9.1", "note", "cover_v2", "x")]
        e, _ = meta_checks(["9.1#1"], [{"num": "9.1"}])
        _chk("M2：note 配了探针 ⇒ 报错",
             any("M2 违反" in s for s in e), fails)

        LEDGER = [("9.1#1", "9.1", "open", "cover_v2", "x")]
        e, _ = meta_checks(["9.1#2"], [{"num": "9.1"}])
        _chk("M3：登记了却切不到 ⇒ 报错",
             any("登记了却切不到" in s for s in e), fails)
        # ⚠ 塌成一条 `M3` 的话，「改标题（整节切不到）」与「插一条编号（多出一个键）」
        # 这两种性质不同的违规红集合一模一样（§2.5.93 归因不唯一）
        _chk("M3 红键是条目键（登记侧）",
             _key_of(next(s for s in e if "登记了却切不到" in s)) == "9.1#1", fails)

        LEDGER = [("9.1#1", "9.1", "open", "cover_v2", "x")]
        e, _ = meta_checks(["9.1#1", "9.1#2"], [{"num": "9.1"}])
        _chk("M3：切到了却没登记 ⇒ 报错",
             any("切到了却没登记" in s for s in e), fails)
        _chk("M3 红键是条目键（切分侧）",
             _key_of(next(s for s in e if "切到了却没登记" in s)) == "9.1#2", fails)

        LEDGER = [("9.1#1", "9.99", "open", "cover_v2", "x")]
        e, _ = meta_checks(["9.1#1"], [{"num": "9.1"}])
        _chk("M4e：登记的小节切不到 ⇒ 报错",
             any("登记的小节切不到" in s for s in e), fails)

        # ⚠ M4 五条**各自独立编号**（M4a..M4e）：它们会由同一次注入连带触发，
        # 塌成一个 `M4` 就分不清是哪一条整体判据出问题了。
        LEDGER = [("9.1#1", "9.1", "open", "cover_v2", "x")]
        e, _ = meta_checks(["9.9#1"], [{"num": "9.9"}])
        ks = {_key_of(s) for s in e}
        _chk("M4 五条各自独立编号（不塌成一个 M4）",
             {"M4a", "M4b", "M4c", "M4d", "M4e"} <= ks, fails)
    finally:
        LEDGER = keep

    print(f"\n--self-test: {len(_CHK_N) - len(fails)}/{len(_CHK_N)}")
    return 1 if fails else 0


# ============================================================ 漂移自证
def _child(env_extra: dict, arg: str = "--marks") -> tuple[int, list[str]]:
    env = dict(os.environ)
    env.update(env_extra)
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), arg],
                       capture_output=True, text=True, env=env, cwd=str(ROOT))
    reds = []
    for line in (r.stdout or "").splitlines():
        if line.startswith("MARKS "):
            reds = json.loads(line[6:]).get("reds", [])
    return r.returncode, reds


def _inject(path: Path, old: str, new: str, count: int | None = None) -> str:
    """在真实文件里注入替换，返回原文（调用方负责 finally 恢复）。

    ⚠ count=None ⇒ 全部替换。默认值**不能**写成 0——`str.replace(old,new,0)`
    是「替换 0 次」，会让注入静默不发生，而输出与「判据没报」长得一模一样（§2.5.89）。
    """
    orig = path.read_text(encoding="utf-8")
    if count is None:
        n = orig.count(old)
        mod = orig.replace(old, new)
    else:
        n = min(count, orig.count(old))
        mod = orig.replace(old, new, count)
    assert mod != orig, f"注入未生效（锚点未命中）：{path} / {old!r}"
    path.write_text(mod, encoding="utf-8")
    return orig


def drift_test() -> int:
    print("== 基准前置断言 ==")
    rc0, reds0 = _child({})
    print(f"  基线退出码 {rc0}，红集合 {reds0}")
    if rc0 != 0 or reds0:
        print("  🔴 基线不绿，drift 用例失去分辨力")
        return 1

    fails: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="c38_"))
    try:
        # ---- C1 源码面：check_tables_v2.py 的 _T7_FALLBACK 改名 ⇒ 28.6#2 转红
        p = SCRIPTS / "check_tables_v2.py"
        orig = _inject(p, "_T7_FALLBACK", "_T7_FALLBACK_X")
        try:
            rc, reds = _child({})
            _chk("C1 `_T7_FALLBACK` 改名 ⇒ 红恰好 ['28.6#2']",
                 rc == 1 and reds == ["28.6#2"], fails)
        finally:
            p.write_text(orig, encoding="utf-8")

        # ---- C2 源码面：gen_paper_figures.py 的 WT 加环境变量 ⇒ 22.6#2 转红
        p = SCRIPTS / "gen_paper_figures.py"
        old = "WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))"
        new = ("WT = os.environ.get('CAIEMS_WT', "
               "os.path.dirname(os.path.dirname(os.path.abspath(__file__))))")
        orig = _inject(p, old, new, count=1)
        try:
            rc, reds = _child({})
            _chk("C2 `WT` 接环境变量 ⇒ 红恰好 ['22.6#2']",
                 rc == 1 and reds == ["22.6#2"], fails)
        finally:
            p.write_text(orig, encoding="utf-8")

        # ---- C3 源码面（文档源）：修订对账记录的节标题改字 ⇒ 该节两条转红
        alt = tmp / "src_c3.md"
        shutil.copy2(SRC, alt)
        t = alt.read_text(encoding="utf-8")
        t2 = t.replace("### 20.7 待处置", "### 20.7 已办结", 1)
        assert t2 != t, "C3 锚点未命中"
        alt.write_text(t2, encoding="utf-8")
        rc, reds = _child({"C38_SRC": str(alt)})
        _chk("C3 源文档 §20.7 标题改字 ⇒ 红恰好 ['20.7#1','20.7#2','M4b','M4e']",
             rc == 1 and reds == ["20.7#1", "20.7#2", "M4b", "M4e"], fails)

        # ---- C4 文档源：往 §24.6 插一条编号 ⇒ 切分多一条、登记面缺一条
        alt = tmp / "src_c4.md"
        shutil.copy2(SRC, alt)
        t = alt.read_text(encoding="utf-8")
        lines = t.splitlines()
        idx = None
        for i, s in enumerate(lines):
            if s.startswith("### 24.6"):
                idx = i
                break
        assert idx is not None, "C4 找不到 §24.6"
        j = idx + 1
        while j < len(lines) and not DASH_ITEM.match(lines[j]) and not NUM_ITEM.match(lines[j]):
            if SEC_END.match(lines[j]) and j > idx:
                break
            j += 1
        lines.insert(j, "4. 这是一条新插入的待决（漂移注入）")
        alt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rc, reds = _child({"C38_SRC": str(alt)})
        _chk("C4 §24.6 多一条编号 ⇒ 红恰好 ['24.6#4','M4b']",
             rc == 1 and reds == ["24.6#4", "M4b"], fails)

        # ---- C5 文档面：总账文档锚点被改 ⇒ D1 报错
        dl = tmp / "doc_c5.md"
        shutil.copy2(DOC, dl)
        t = dl.read_text(encoding="utf-8")
        t2 = t.replace("**46 条**", "**45 条**", 1)
        assert t2 != t, "C5 锚点未命中"
        dl.write_text(t2, encoding="utf-8")
        rc, reds = _child({"C38_DOC": str(dl)})
        _chk("C5 总账文档条目数被改 ⇒ 红恰好 ['D1']",
             rc == 1 and reds == ["D1"], fails)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n--drift-test: {5 - len(fails)}/5")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--drift-test" in sys.argv:
        sys.exit(drift_test())
    sys.exit(main())
