"""审核告警：把该用眼睛看的条目标出来。

为什么要在审核时提示，而不是靠审核员逐条读完
------------------------------------------------
暂存区动辄上百条，逐条读完不现实；但其中真正有问题的只是少数。告警的作用
是**把注意力引到可能有问题的那几条上**，其余零告警条目才敢开放批量通过。

两条设计原则（决定了下面每条规则的宽窄）
----------------------------------------

1. **读时算，不存表**。告警是「当前这批数据的函数」，不是数据本身的属性。
   将来阈值校准、规则增删，历史条目会立刻跟着变，不会留下一批用旧规则算出来
   的陈旧标记。代价是每次查询要重算——所以重活（计数、求和）都下推给 SQL，
   这里只做常数级判断。

2. **宁可窄，不可滥**。误报会让审核员对所有告警脱敏，那这套东西就白做了。
   凡规则本身有争议的（见 `type_mismatch`），一律取最窄的形态。

阈值（`MIN_TITLE_CHARS` 等）是初始猜测，蓝图 03 待决项 D 要求用真实暂存数据
跑一遍看命中率（目标区间 20%–40%）。过高说明规则太松失去筛选意义，过低说明
误报太多会被忽略。校准时应只改这里的常量。
"""

from __future__ import annotations

from typing import Any, Iterable

# ── 阈值（校准入口，改动只应发生在这几行） ──────────────────────────
MIN_TITLE_CHARS = 4      # 最终库 title 是 NOT NULL 且要可检索
MIN_TEXT_CHARS = 100     # 低于此长度的原文抽取质量会明显下降
QUALITY_SHIFT_RATIO = 0.5  # 超过半数编码的生效值与 LLM 自报不一致才算告警

# ── 告警码与中文说明 ───────────────────────────────────────────────
WARNING_LABELS: dict[str, str] = {
    "empty_title": "标题空或过短",
    "no_death": "无伤亡数字",
    "no_causes": "无致因编码",
    "cause_not_in_taxonomy": "出现未知编码",
    "short_text": "原文过短",
    "quality_shift": "证据等级与模型自报不符",
    "type_mismatch": "体裁与事故类型矛盾",
}

# 严重到必须先补数据才能并入的告警：并入时会直接拒绝
BLOCKING: frozenset[str] = frozenset({"empty_title", "no_causes"})


def label(code: str) -> str:
    return WARNING_LABELS.get(code, code)


def compute(row: dict[str, Any]) -> list[str]:
    """对一条已聚合的暂存行算告警。

    `row` 需要的键（由 `web/review.py` 的队列查询一次性带出，不在本函数里
    再查库）：

    `title` `deaths` `injuries` `accident_type` `text_type`
    `cause_count` `unknown_code_count` `quality_diff` `raw_len`
    """
    hits: list[str] = []

    title = (row.get("title") or "").strip()
    if len(title) < MIN_TITLE_CHARS:
        hits.append("empty_title")

    if not (row.get("deaths") or 0) and not (row.get("injuries") or 0):
        hits.append("no_death")

    if not (row.get("cause_count") or 0):
        hits.append("no_causes")

    if row.get("unknown_code_count"):
        hits.append("cause_not_in_taxonomy")

    if (row.get("raw_len") or 0) < MIN_TEXT_CHARS:
        hits.append("short_text")

    # 质量漂移：LLM 自报的证据等级与生效值（现算规则）不一致的编码超过半数。
    # 这个比例高说明两种判定方式在这条上分歧大，值得人看一眼再定夺。
    total = row.get("cause_count") or 0
    diff = row.get("quality_diff") or 0
    if total and diff / total > QUALITY_SHIFT_RATIO:
        hits.append("quality_shift")

    # 体裁与字段矛盾。
    #
    # 只取最窄的形态：统计汇总类文本（"2024 年全国建筑施工事故共 X 起"）本身
    # 不指向单起事故，若却抽出了具体事故类型与伤亡数字，说明管线要么把汇总里
    # 某一句当成了单起事故，要么 S1 判错了体裁。
    #
    # 为什么不把 regulatory（通报）/ training（培训）也算进来：通报文件里
    # 常常就带完整的事故描述与伤亡，那是正常的，算进来只会制造大批误报。
    if (row.get("text_type") == "aggregate"
            and (row.get("accident_type") or "").strip()
            and ((row.get("deaths") or 0) or (row.get("injuries") or 0))):
        hits.append("type_mismatch")

    return hits


def summarize(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按告警码统计命中条数，供漏斗与校准用。"""
    counter: dict[str, int] = {}
    for row in rows:
        for code in row.get("warnings") or ():
            counter[code] = counter.get(code, 0) + 1
    return [
        {"code": code, "label": label(code), "count": n}
        for code, n in sorted(counter.items(), key=lambda kv: -kv[1])
    ]


def is_clean(row: dict[str, Any]) -> bool:
    return not (row.get("warnings") or [])
