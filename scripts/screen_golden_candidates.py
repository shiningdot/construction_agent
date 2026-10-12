#!/usr/bin/env python3
"""Golden Standard 候选事故筛选器（CAICS 致因标注标准答案的取样前端）。

目标：从 data/caiems_v2.db 中筛出「原始文本详细、事故信息丰富、致因多」的优质事故，
作为 CAICS 5 层 19 类（M1-M5/F1-F4/T1-T4/H1-H4/C1-C2）致因标注的标准答案来源。

筛选逻辑（四层闸门 + 打分）：

  A. 来源池     report_type ∈ {事故调查报告, 政府通报, 教材案例}
                —— 排除新闻报道（记者稿无官方原因认定）
  B. 原因认定   文本必须含「直接原因/间接原因/主要原因/事故原因/原因分析」
                —— 没有原因认定段落 = 无法做致因标注，硬性淘汰
  C. 文本洁净   剔除目录/页码等版式行后再算碎片率，识别扫描件 OCR 交错
                另剔除乱码字符、非中文占比过高的记录
  D. 单事故完整 剔除「一条记录塞多起事故」的合并块
  打分：显式原因结构 0.30 + 跨层关键词覆盖 0.25 + 篇幅 0.15 + 字段填充 0.15 + 原因层级种类 0.15

产出三档：
  T1_core  长报告、文本洁净、原因结构完整  —— 主力标准答案来源
  T2_short 有显式原因标签但正文偏短（800 例式条目）—— 补充来源
  T3_hold  不达标（含 esafety 扫描件 OCR 交错 / 截断者）—— 需按 source_url 重抓原文

用法：
    python3 scripts/screen_golden_candidates.py --diagnose
    python3 scripts/screen_golden_candidates.py --select --top 300 --out experiments/golden_standard
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

DB = Path("/home/suntianhao/construction_agent/data/caiems_v2.db")

CJK = re.compile(r"[一-鿿]")
GARBAGE = re.compile(r"[�\u0000-\u0008\u000b\u000c\u000e-\u001f]")

# ── A 层 ───────────────────────────────────────────────────────
REPORT_TYPES = {"事故调查报告", "政府通报", "教材案例"}

# 来源分管道：book = 书籍 PDF 文本层（完整）；web_pdf = 政府网页正文（常被截断）；
# web_html = 门户站转载（扫描件 OCR，交错严重）
SOURCE_CLASS = {
    "建筑施工安全事故案例分析(住建部)": "book",
    "建筑施工安全事故案例分析(住建部扫描版)": "book",
    "建筑施工生产安全事故案例分析(住建部)": "book",
    "建筑施工重大伤亡事故800例(樊锡仁)": "book",
    "esafety": "web_html",
    "safehoo": "web_html",
}
DEFAULT_CLASS = "web_pdf"

# ── C 层：版式噪声行（目录 / 页码 / 抓取元信息 / 网页壳），算碎片率前剔除 ──
BOILER = re.compile(
    r"(^\s*—?\s*\d{1,3}\s*—?\s*$)"      # 页码 — 1 —
    r"|(……)|(\.{4,})"                    # 目录点线
    r"|(^\s*目\s*$)|(^\s*录\s*$)"         # 目录二字独占行
    r"|(^\s*正文\s*[:：]?\s*$)"
    r"|(^\[?本地\s*[:：])|(^PDF\s*[:：])|(^信息来源)|(^https?://)"
    # 网页壳（爬虫把导航/工具栏写进了正文）
    r"|(^\s*(标题|关键字|摘要|全文|内容|打印|关闭|分享|字体|收藏|上一篇|下一篇|返回顶部|相关链接)\s*$)"
    r"|(您当前的位置)|(^\s*首\s*页)|(^\s*新\s*闻\s*中\s*心\s*$)|(^\s*政\s*务\s*服\s*务\s*$)"
    r"|(^\s*首页\s*[>›])"
)

# 书籍版权页 / 前言，混入正文的整本导入行
BOOKMATTER = re.compile(r"(图书在版编目|中国建筑工业出版社|ISBN\s*9|定价\s*[:：]|印张\s*[:：]|字数\s*[:：])")

MIN_LEN_T1 = 1200   # T1 长篇门槛
MIN_LEN_T2 = 500    # T2 最低门槛（低于此则「文本详细」不成立）
MAX_FRAG_RATIO = 0.18   # 版式行剔除后的碎片率上限
MIN_CJK_RATIO = 0.60
TRUNC_MARK = re.compile(r"[（(]\s*共\s*(\d+)\s*字\s*[)）]")   # 网页端「(共N字)」截断标记

# ── D 层：合并块 ───────────────────────────────────────────────
# 事故边界标记：(正则, 捕获组序号) —— 用「不同编号」计数，避免同篇页眉/交叉引用被误判
CASE_MARKERS = [
    (re.compile(r"##+\s*(\d{2,4})\s*事故"), 1),                       # 800例：## 396 事故
    (re.compile(r"案例\s*([一二三四五六七八九十百\d]+)"), 1),          # 住建部：案例十六
    (re.compile(r"##+\s*(\d{1,2}\.\d{1,2}\.\d{1,2})\s*案例"), 1),     # 住建部：## 5.1.16 案例
]
# 每起事故一条的正文标记。注意 800 例里有「发生简况:」和「事故发生简况:」两种写法，
# 只匹配带「事故」前缀的会漏掉前者，导致多起事故合并块被放行。
BRIEF_MARK = re.compile(r"发生(?:事故)?[简高]况\s*[:：]")
MIN_TAIL_AFTER_2ND = 300   # 第二起事故后面至少这么多字才算「真的合并了」

# ── B / 打分 ───────────────────────────────────────────────────
CAUSE_HEAD = re.compile(r"(直接原因|间接原因|主要原因|事故原因|原因分析|其他可能排除因素)")
ENUM_ITEM = re.compile(r"(?:[（(]\s*\d{1,2}\s*[)）]|\b\d{1,2}\s*[)）.、]|[一二三四五六七八九十]{1,2}是)")
LAYER_KEYWORDS = {
    "M": ["监理", "监督", "检查", "隐患", "整改", "培训", "交底", "教育", "方案", "制度",
          "预案", "资质", "分包", "转包", "挂靠", "赶工", "工期", "主体责任", "管理"],
    "F": ["防护", "栏杆", "安全网", "洞口", "临边", "支撑", "支架", "脚手架", "模板",
          "限位", "防坠", "安全装置", "安全带", "安全帽"],
    "T": ["断裂", "故障", "失灵", "超载", "堆载", "失稳", "涌水", "沉降", "流沙",
          "地质", "暴雨", "大风", "降雨", "浸水", "劣质", "材料"],
    "H": ["违规", "违章", "擅自", "未按", "冒险", "强令", "欠缺", "疏忽", "盲目施救",
          "安全意识", "无证", "未经"],
    "C": ["救援", "施救", "迟报", "瞒报", "漏报", "未及时上报", "抢救"],
}

FIELD_COLS = [
    "date", "province", "city", "accident_time", "accident_location_detail", "weather",
    "project_name", "project_type", "building_structure", "construction_phase", "work_task",
    "developer", "contractor", "supervision_company", "accident_type", "severity",
    "deaths", "injuries", "direct_economic_loss", "equipment_involved", "material_involved",
    "worker_type", "direct_cause",
]


def body_lines(t: str) -> list[str]:
    return [l.strip() for l in t.split("\n") if l.strip() and not BOILER.search(l.strip())]


def text_metrics(t: str) -> dict:
    n = len(t)
    nzn = len(re.sub(r"\s", "", t)) or 1
    L = body_lines(t)
    short = sum(1 for l in L if len(l) < 8)
    ascii_p = len(re.findall(r"[,;:()\[\]]", t))
    fw_p = len(re.findall(r"[，；：（）【】]", t))
    return {
        "len": n,
        "cjk_ratio": round(len(CJK.findall(t)) / nzn, 3),
        "frag_ratio": round(short / max(1, len(L)), 3),
        "punct_mix": round(ascii_p / max(1, ascii_p + fw_p), 3),
        "n_lines": len(L),
        "garbage": len(GARBAGE.findall(t)),
    }


def count_multi_accidents(t: str) -> int:
    """估算记录里含几起事故：按不同案例编号计数；编号不同且其后有实质正文才算合并。"""
    best = 0
    for pat, gi in CASE_MARKERS:
        hits = [(m.group(gi), m.end()) for m in pat.finditer(t)]
        if not hits:
            continue
        uniq = {}
        for cid, end in hits:
            uniq.setdefault(cid, end)          # 同编号只留首次
        if len(uniq) <= 1:
            best = max(best, len(uniq))
            continue
        ends = sorted(uniq.values())
        # 第一个之后的内容长度；若第二起后面正文够多，才算真合并
        if len(t) - ends[1] >= MIN_TAIL_AFTER_2ND:
            best = max(best, len(uniq))
        else:
            best = max(best, 1)
    # 800 例另有每起一条「发生单位」
    n_unit = len(re.findall(r"发生单位\s*[:：]", t))
    if n_unit:
        best = max(best, n_unit)

    # 「发生简况 / 事故发生简况」是 800 例里每起事故一条的字段标签：出现 ≥2 次即多起事故。
    # 实测第二起可能很短（214~294 字），故不再要求尾部长度。
    n_brief = len(BRIEF_MARK.findall(t))
    if n_brief >= 2:
        best = max(best, n_brief)
    return best


def cause_section(t: str) -> str:
    m = CAUSE_HEAD.search(t)
    if not m:
        return ""
    seg = t[m.start(): m.start() + 6000]
    cut = re.search(r"(事故教训|事故处理|防范措施|整改措施|对事故.{0,4}处理|责任认定|处理建议)", seg)
    return seg[: cut.start()] if cut else seg


def richness(t: str) -> dict:
    heads = CAUSE_HEAD.findall(t)
    sec = cause_section(t)
    # 原因段落里长度≥8 的陈述句条数 —— 「致因多」的直接度量
    clauses = [s for s in re.split(r"[。；;!！?？\n]", sec) if len(s.strip()) >= 8]
    return {
        "head_variety": len(set(heads)),
        "enum_in_sec": len(ENUM_ITEM.findall(sec)),
        "cause_clauses": len(clauses),
        "layer_cov": {L: sum(1 for kw in kws if kw in t) for L, kws in LAYER_KEYWORDS.items()},
    }


def field_fill(row) -> int:
    n = 0
    for c in FIELD_COLS:
        v = row[c]
        if v is None:
            continue
        if isinstance(v, str) and v.strip() in ("", "未知", "无", "不详"):
            continue
        if isinstance(v, (int, float)) and v == 0:
            continue
        n += 1
    return n


def load_rows(con):
    con.row_factory = sqlite3.Row
    cols = ", ".join(["id", "title", "source_name", "source_url", "report_type", "raw_text"]
                     + FIELD_COLS)
    return con.execute(f"SELECT {cols} FROM accidents").fetchall()


def assess(row) -> dict:
    t = row["raw_text"] or ""
    m = text_metrics(t)
    rich = richness(t)
    ff = field_fill(row)
    multi = count_multi_accidents(t)
    layers_hit = sum(1 for v in rich["layer_cov"].values() if v >= 2)
    has_cause = rich["head_variety"] >= 1
    src_class = SOURCE_CLASS.get(row["source_name"], DEFAULT_CLASS)

    # 截断：网页端给出「(共N字)」且我们拿到的明显不足
    tm = TRUNC_MARK.search(t)
    trunc_ratio = None
    truncated = False
    if tm:
        orig = int(tm.group(1))
        trunc_ratio = round(m["len"] / orig, 3)
        truncated = trunc_ratio < 0.75
    book_matter = bool(BOOKMATTER.search(t))

    fails = []
    if row["report_type"] not in REPORT_TYPES:
        fails.append("A_report_type")
    if not has_cause:
        fails.append("B_no_cause_section")
    if m["frag_ratio"] > MAX_FRAG_RATIO:
        fails.append("C_fragment")
    if m["cjk_ratio"] < MIN_CJK_RATIO:
        fails.append("C_cjk")
    if m["garbage"] > 0:
        fails.append("C_garbage")
    if multi > 1:
        fails.append("D_merged")
    if book_matter:
        fails.append("D_book_matter")
    if m["len"] < MIN_LEN_T2:
        fails.append("E_too_short")
    if truncated:
        fails.append("F_truncated")
    if src_class != "book":
        fails.append("G_web_source")   # 网页来源整体降级：截断/OCR 风险高

    if fails:
        tier = "T3_hold"
    elif m["len"] >= MIN_LEN_T1 and rich["head_variety"] >= 2:
        tier = "T1_core"
    else:
        tier = "T2_short"

    score = (0.30 * min(1.0, rich["cause_clauses"] / 10)
             + 0.20 * min(1.0, layers_hit / 5)
             + 0.15 * min(1.0, m["len"] / 4000)
             + 0.15 * min(1.0, ff / 18)
             + 0.10 * min(1.0, rich["head_variety"] / 3)
             + 0.10 * min(1.0, rich["enum_in_sec"] / 12))

    return {
        "id": row["id"], "title": row["title"], "source_name": row["source_name"],
        "source_url": row["source_url"], "report_type": row["report_type"],
        "source_class": src_class, "tier": tier, "fails": fails, "multi_acc": multi,
        "truncated": truncated, "trunc_ratio": trunc_ratio, "book_matter": book_matter,
        "accident_type": row["accident_type"], "severity": row["severity"],
        "deaths": row["deaths"], "injuries": row["injuries"],
        "enum_in_sec": rich["enum_in_sec"], "head_variety": rich["head_variety"],
        "cause_clauses": rich["cause_clauses"],
        "layers_hit": layers_hit, "fields_filled": ff,
        "score": round(score, 4), **m,
    }


def dedup_key(title: str) -> str:
    t = re.sub(r"[\s“”\"'·,，。.：:；;（）()\[\]【】—\-–_]", "", title or "")
    t = re.sub(r"^(事故标题|原始报道|案例[一二三四五六七八九十百\d]+)", "", t)
    return t[:16]


def dedup(cands: list[dict]):
    best, dropped = {}, []
    for c in sorted(cands, key=lambda x: -x["score"]):
        k = dedup_key(c["title"])
        if k and k in best:
            dropped.append(c)
        else:
            best[k] = c
    return list(best.values()), dropped


def stratified_select(cands: list[dict], total: int, min_per_type: int = 6) -> list[dict]:
    by_type = defaultdict(list)
    for c in sorted(cands, key=lambda x: -x["score"]):
        by_type[c["accident_type"] or "未知"].append(c)
    chosen, seen = [], set()
    for _, items in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        for c in items[:min_per_type]:
            chosen.append(c); seen.add(c["id"])
    rest = [c for c in sorted(cands, key=lambda x: -x["score"]) if c["id"] not in seen]
    chosen.extend(rest[: max(0, total - len(chosen))])
    return sorted(chosen, key=lambda x: -x["score"])[:total]


def diagnose(rows):
    scored = [assess(r) for r in rows]
    print(f"总记录 {len(rows)}")
    print("\n=== 闸门淘汰数（可叠加）===")
    for k, v in Counter(f for s in scored for f in s["fails"]).most_common():
        print(f"  {k:22s} {v}")
    print("\n=== 分档 ===")
    for k, v in Counter(s["tier"] for s in scored).most_common():
        print(f"  {k:12s} {v}")
    print("\n=== 各来源通过率 ===")
    print(f"  {'来源':44s} {'总数':>5s} {'T1':>5s} {'T2':>5s} {'T3':>5s} {'通过率':>7s}")
    by_src = defaultdict(Counter)
    for s in scored:
        by_src[s["source_name"]][s["tier"]] += 1
    for src, c in sorted(by_src.items(), key=lambda kv: -sum(kv[1].values())):
        tot = sum(c.values())
        ok = c["T1_core"] + c["T2_short"]
        print(f"  {src:44s} {tot:5d} {c['T1_core']:5d} {c['T2_short']:5d} {c['T3_hold']:5d} {ok/tot*100:6.1f}%")
    for tier in ("T1_core", "T2_short"):
        sub = [s for s in scored if s["tier"] == tier]
        if not sub:
            continue
        print(f"\n--- {tier} ({len(sub)}) ---")
        for k in ["score", "len", "cause_clauses", "enum_in_sec", "layers_hit",
                  "fields_filled", "frag_ratio"]:
            v = [s[k] for s in sub]
            print(f"    {k:13s} min={min(v):8.2f} med={st.median(v):8.2f} max={max(v):8.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--out", default="experiments/golden_standard")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = load_rows(con)
    scored = [assess(r) for r in rows]

    if args.diagnose or not args.select:
        diagnose(rows)
        return

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pool = [s for s in scored if s["tier"] in ("T1_core", "T2_short")]
    kept, dropped = dedup(pool)
    print(f"候选池 {len(pool)} → 去重后 {len(kept)}（剔除重复 {len(dropped)}）")

    t1 = [c for c in kept if c["tier"] == "T1_core"]
    t2 = [c for c in kept if c["tier"] == "T2_short"]
    sel1 = stratified_select(t1, min(len(t1), int(args.top * 0.65)))
    sel2 = stratified_select(t2, args.top - len(sel1))
    selected = sorted(sel1 + sel2, key=lambda x: -x["score"])
    print(f"入选 {len(selected)}（T1 {len(sel1)} + T2 {len(sel2)}）")

    raw_by_id = {r["id"]: r["raw_text"] for r in rows}
    with (out / "golden_candidates.jsonl").open("w", encoding="utf-8") as f:
        for c in selected:
            f.write(json.dumps({**c, "raw_text": raw_by_id[c["id"]]}, ensure_ascii=False) + "\n")

    keys = [k for k in selected[0] if k not in ("fails",)]
    with (out / "golden_candidates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for c in selected:
            w.writerow({k: v for k, v in c.items() if k in keys})

    (out / "screening_all_scored.json").write_text(
        json.dumps(scored, ensure_ascii=False, indent=1), encoding="utf-8")

    # 网页来源中「最值得重抓」的一批：有原因段、文本较洁净、篇幅够
    recoverable = [s for s in scored
                   if s["source_class"] != "book" and s["head_variety"] >= 1
                   and s["frag_ratio"] <= MAX_FRAG_RATIO and s["len"] >= MIN_LEN_T1]
    recoverable.sort(key=lambda s: -s["score"])
    with (out / "tier3_web_recoverable.csv").open("w", newline="", encoding="utf-8") as f:
        cols = ["id", "title", "source_name", "source_url", "len", "trunc_ratio",
                "frag_ratio", "cause_clauses", "score"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in recoverable:
            w.writerow({k: s[k] for k in cols})

    L = ["# Golden Standard 候选筛选报告", "",
         f"- 源库 `data/caiems_v2.db`：{len(rows)} 条",
         f"- 候选池（T1+T2）：{len(pool)} → 去重后 {len(kept)} → **入选 {len(selected)}**", "",
         "## 入选构成", "", "| 来源 | 条数 |", "|---|---|"]
    for k, v in Counter(c["source_name"] for c in selected).most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "| 事故类型 | 条数 |", "|---|---|"]
    for k, v in Counter(c["accident_type"] for c in selected).most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "| 档次 | 条数 |", "|---|---|"]
    for k, v in Counter(c["tier"] for c in selected).most_common():
        L.append(f"| {k} | {v} |")
    L += ["", "| 主要原因层覆盖（层内关键词≥2） | 条数 |", "|---|---|"]
    for k, v in sorted(Counter(c["layers_hit"] for c in selected).items()):
        L.append(f"| {k} 层 | {v} |")
    L += ["", f"- 网页来源待重抓清单：{len(recoverable)} 条 → `tier3_web_recoverable.csv`", ""]
    (out / "SCREENING_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print(f"产出 → {out}/")


if __name__ == "__main__":
    main()
