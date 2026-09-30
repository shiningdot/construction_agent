"""省份归一分组：把 79 个原始取值收敛为可筛选的确定集合。

## 为什么需要这一层

主库 `accidents.province` 实测有 **79 个不同取值**，直接 `SELECT DISTINCT`
喂给筛选器会得到 79 个选项——其中既有「四川省」这样的标准全称，也有
「黑龙江省鸡西市矿业学院电教楼工地」这样的地址级脏值，还有「印度」「香港」
这类境外记录。用户无法从中选出想要的省。

本模块把原始值收敛为 **内地 31 个省级单位 + 三个桶**，全部逻辑建立在
`utils/location.py` 既有的行政区划典之上，不另建一套映射表。

## 实测分布（2026-09-30，主库 2,623 条）

    内地 31 个省级单位      2,559 条    （2,544 条直接命中 + 15 条由地址归一而来）
    港澳台及境外              46 条    （28 个原始取值）
    无法归一                   4 条
    空值                      14 条
    ─────────────────────────────────
    合计                   2,623 条

那 15 条「地址级脏值」能被 `location.py` 的 `extract_province()` 认出来
（``鄂州市五里墩建设工地`` → 湖北省、``太原市`` → 山西省、``西藏`` → 西藏自治区），
所以它们**计入所属省份的筛选结果**——用户按「四川省」筛选时应该看到全部
192 条，而不是 191 条。这些脏值本身在 `audit()` 里完整列出，不掩盖问题。

## 与筛选器的约定

`/api/accidents` 的 `province` 参数接受两类值，可多选：

1. **省级全称**（如 ``四川省``）—— 命中该省全部记录，含由地址归一而来的脏值
2. **桶哨兵**：``__overseas__`` / ``__unnormalized__`` / ``__empty__``

各键命中数之和**恒等于** `accidents` 总行数。这个不变量由 `summary()`
自检并在 `/api/overview` 中回显——「分别计数、不掩盖问题」是蓝图 04
第 2.4 节的明确要求。

## 为什么筛选条件要反向展开

归一逻辑在 Python 侧，SQL 侧无法复刻。但归一的**值域是有限集合**，所以
可以反向展开：``四川省`` → ``province IN ('四川省', '四川省成都市温江县国土局和审计局住宅楼工地')``。

展开表由 `build_index()` 对全表做一次 `GROUP BY province` 现算，因此
**新数据入库后自动生效**，无需维护硬编码清单。索引按库文件的
(mtime, size) 缓存，库更新即失效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..utils.location import (
    AUTONOMOUS_REGIONS,
    MUNICIPALITIES,
    PROVINCES,
    SAR,
    extract_province,
)
from . import db

# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════

GROUP_STANDARD = "standard"
GROUP_OVERSEAS = "overseas"
GROUP_UNNORMALIZED = "unnormalized"
GROUP_EMPTY = "empty"

SENTINEL_OVERSEAS = "__overseas__"
SENTINEL_UNNORMALIZED = "__unnormalized__"
SENTINEL_EMPTY = "__empty__"

SENTINELS: tuple[str, ...] = (SENTINEL_OVERSEAS, SENTINEL_UNNORMALIZED, SENTINEL_EMPTY)

BUCKET_LABELS: dict[str, str] = {
    SENTINEL_OVERSEAS: "境外及港澳台",
    SENTINEL_UNNORMALIZED: "未归一",
    SENTINEL_EMPTY: "未标注",
}

# 港澳台：在 location.py 的典里是标准省级单位，但对本项目的筛选语义而言
# 属于「境外及港澳台」组。故单独列出，不混进内地 31 个。
HMT: frozenset[str] = frozenset({PROVINCES["台湾"], SAR["香港"], SAR["澳门"]})

# 内地 31 个省级单位，按行政区划惯用顺序（华北→东北→华东→中南→西南→西北）。
# 这个顺序只影响前端下拉框的排列，集合本身由下面的断言与 location.py 对齐。
MAINLAND_ORDER: tuple[str, ...] = (
    "北京市", "天津市", "河北省", "山西省", "内蒙古自治区",
    "辽宁省", "吉林省", "黑龙江省",
    "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省",
    "重庆市", "四川省", "贵州省", "云南省", "西藏自治区",
    "陕西省", "甘肃省", "青海省", "宁夏回族自治区", "新疆维吾尔自治区",
)

_DERIVED_ALL: frozenset[str] = frozenset(
    set(PROVINCES.values()) | set(AUTONOMOUS_REGIONS.values()) | set(MUNICIPALITIES.values())
)

assert set(MAINLAND_ORDER) == (_DERIVED_ALL - HMT), (
    "MAINLAND_ORDER 与 location.py 的行政区划典不一致——"
    "要么典改了，要么这里的清单写错了。筛选器会静默漏数据，必须立刻修。"
)

MAINLAND: frozenset[str] = frozenset(MAINLAND_ORDER)

# 境外国家/地区关键词。只在 `extract_province()` **认不出来**时启用兜底：
# 「香港」「台湾省」等会被典直接命中，走不到这里。
#
# 前半段是本批数据实测出现的 23 个；后半段是预防性覆盖，避免下一批采集
# 到新国家时掉进「未归一」桶而看不出原因。
OVERSEAS_HINTS: tuple[str, ...] = (
    # ── 实测出现 ──
    "印度", "韩国", "西班牙", "美国", "海地", "阿尔巴尼亚", "越南",
    "蒙古", "菲律宾", "肯尼亚", "罗马尼亚", "洪都拉斯", "泰国", "法国",
    "新加坡", "摩洛哥", "巴基斯坦", "尼泊尔", "孟加拉", "乌干达",
    "塞内加尔", "太子港", "印控克什米尔",
    # ── 预防性覆盖 ──
    "日本", "德国", "英国", "俄罗斯", "意大利", "加拿大", "澳大利亚",
    "马来西亚", "印度尼西亚", "缅甸", "柬埔寨", "老挝", "斯里兰卡",
    "土耳其", "伊朗", "伊拉克", "沙特", "以色列", "埃及", "南非",
    "巴西", "墨西哥", "阿根廷", "智利", "秘鲁", "新西兰", "乌克兰",
    "波兰", "荷兰", "比利时", "瑞士", "瑞典", "挪威", "丹麦", "希腊",
)


# ══════════════════════════════════════════════════════════════
# 归一
# ══════════════════════════════════════════════════════════════


def _looks_overseas(value: str) -> bool:
    """关键词兜底判定。value 已被 `extract_province()` 判为认不出来。"""
    return any(hint in value for hint in OVERSEAS_HINTS)


def classify(raw: Any) -> str:
    """把一个原始 province 值映射为筛选键。

    Returns:
        省级全称（如 ``四川省``）或桶哨兵（``__overseas__`` 等）。
        注意港澳台返回的是其全称（``香港特别行政区``），分组由
        `group_of()` 决定——保持「键 = 归一结果」这一单一语义，
        避免调用方在两处各判一次。
    """
    if raw is None:
        return SENTINEL_EMPTY
    value = str(raw).strip()
    if not value:
        return SENTINEL_EMPTY

    canonical = extract_province(value)
    if canonical:
        # 港澳台在 location.py 里是标准省级单位，但本项目把三者与境外归为
        # 同一组。这里直接返回哨兵而非其全称——否则「香港特别行政区」(7 条)
        # 和「台湾省」(2 条) 会成为两个独立选项，用户点「境外及港澳台」
        # 只看到 37 条，与概览卡片上的 46 条对不上。
        return SENTINEL_OVERSEAS if canonical in HMT else canonical

    if _looks_overseas(value):
        return SENTINEL_OVERSEAS

    return SENTINEL_UNNORMALIZED


def group_of(key: str) -> str:
    """筛选键 → 分组名。"""
    if key == SENTINEL_OVERSEAS:
        return GROUP_OVERSEAS
    if key == SENTINEL_UNNORMALIZED:
        return GROUP_UNNORMALIZED
    if key == SENTINEL_EMPTY:
        return GROUP_EMPTY
    return GROUP_OVERSEAS if key in HMT else GROUP_STANDARD


def label_of(key: str) -> str:
    """筛选键 → 展示名。"""
    return BUCKET_LABELS.get(key, key)


# ══════════════════════════════════════════════════════════════
# 反向展开索引
# ══════════════════════════════════════════════════════════════


@dataclass
class ProvinceIndex:
    """筛选键 → (条数, 原始值清单) 的双向表。"""

    counts: dict[str, int] = field(default_factory=dict)
    raws: dict[str, list[str]] = field(default_factory=dict)
    raw_counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def hit_count(self, key: str) -> int:
        return self.counts.get(key, 0)

    def raw_values(self, key: str) -> list[str]:
        """该键对应的原始值清单。空值桶返回空列表（SQL 侧用 IS NULL 特判）。"""
        return self.raws.get(key, [])

    def summary(self) -> dict[str, Any]:
        """按分组汇总，用于 `/api/overview` 的 `province_group` 字段。"""
        by_group: dict[str, int] = {}
        for key, n in self.counts.items():
            g = group_of(key)
            by_group[g] = by_group.get(g, 0) + n

        standard = [
            {"value": p, "label": p, "count": self.counts.get(p, 0)}
            for p in MAINLAND_ORDER
            if self.counts.get(p, 0) > 0
        ]
        buckets = [
            {
                "value": s,
                "label": BUCKET_LABELS[s],
                "count": self.counts.get(s, 0),
                "group": group_of(s),
            }
            for s in SENTINELS
        ]
        return {
            "standard": standard,
            "buckets": buckets,
            "by_group": {
                GROUP_STANDARD: by_group.get(GROUP_STANDARD, 0),
                GROUP_OVERSEAS: by_group.get(GROUP_OVERSEAS, 0),
                GROUP_UNNORMALIZED: by_group.get(GROUP_UNNORMALIZED, 0),
                GROUP_EMPTY: by_group.get(GROUP_EMPTY, 0),
            },
            "distinct_raw_values": len(self.raw_counts),
            "total": self.total,
        }

    def audit(self) -> list[dict[str, Any]]:
        """列出全部**非标准原始值**及其归一结果，供数据质量核查。

        「不掩盖问题」的落点：那些被成功归一的地址级脏值虽然在筛选时
        计入所属省份，但仍然出现在这里，可以逐条追查。
        """
        rows: list[dict[str, Any]] = []
        for raw, n in self.raw_counts.items():
            key = classify(raw)
            if key == raw and key in MAINLAND:
                continue  # 本来就是标准全称，无需审计

            if key in MAINLAND:
                kind = "renormalized"   # 地址级脏值，已归入某个省
            elif key == SENTINEL_OVERSEAS:
                kind = "overseas"
            else:
                kind = "unanchored"     # 认不出来，落进未归一桶

            rows.append({
                "raw": raw,
                "count": n,
                "resolved": key,
                "group": group_of(key),
                "kind": kind,
            })
        rows.sort(key=lambda r: (-r["count"], r["raw"]))
        return rows


def build_index(db_path: str | Path | None = None) -> ProvinceIndex:
    """对全表做一次 GROUP BY，构建反向展开表。"""
    rows = db.query(
        "SELECT province AS p, COUNT(*) AS c FROM accidents GROUP BY province",
        db_path=db_path,
    )

    idx = ProvinceIndex()
    for r in rows:
        raw = r["p"]
        n = r["c"]
        key = classify(raw)
        idx.counts[key] = idx.counts.get(key, 0) + n
        idx.total += n
        if raw is None or str(raw).strip() == "":
            continue  # 空值桶的 SQL 条件由 IS NULL 表达，不需要原始值
        idx.raws.setdefault(key, []).append(str(raw))
        idx.raw_counts[str(raw)] = n
    return idx


_INDEX_CACHE: dict[tuple, ProvinceIndex] = {}


def get_index(db_path: str | Path | None = None, *, refresh: bool = False) -> ProvinceIndex:
    """取索引（按库指纹缓存）。库未变动时零额外查询。"""
    fp = db.db_fingerprint(db_path)
    if refresh or fp not in _INDEX_CACHE:
        _INDEX_CACHE.clear()  # 只保留最新一份，避免长跑进程里堆积
        _INDEX_CACHE[fp] = build_index(db_path)
    return _INDEX_CACHE[fp]


def reset_cache() -> None:
    """清空缓存。测试切换库文件时调用。"""
    _INDEX_CACHE.clear()


# ══════════════════════════════════════════════════════════════
# SQL 条件翻译
# ══════════════════════════════════════════════════════════════


def filter_clause(
    keys: Sequence[str], db_path: str | Path | None = None
) -> tuple[str, list[str]]:
    """把筛选键列表翻译成 SQL 条件与参数。

    Returns:
        ``(sql_fragment, params)``。sql_fragment 形如
        ``province IN (?,?) OR province IS NULL``，调用方需自行加括号后
        与其它条件 AND。未知键**静默忽略**（前端传了过期的键不该让页面崩），
        但调用方应把实际生效的键回显在响应的 meta 里。

        无有效键时返回 ``("", [])``，调用方据此不加条件。
    """
    if not keys:
        return "", []

    idx = get_index(db_path)
    parts: list[str] = []
    params: list[str] = []

    for key in keys:
        if key == SENTINEL_EMPTY:
            parts.append("(province IS NULL OR TRIM(province) = '')")
            continue

        raws = idx.raw_values(key)
        if not raws:
            continue  # 未知键，或该省份在本库无记录
        placeholders = ",".join("?" * len(raws))
        parts.append(f"province IN ({placeholders})")
        params.extend(raws)

    if not parts:
        return "", []
    return "(" + " OR ".join(parts) + ")", params


def options(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """筛选器下拉框的数据源：内地省份 + 三个桶，均带计数。"""
    idx = get_index(db_path)
    out = [
        {"value": p, "label": p, "group": GROUP_STANDARD, "count": idx.hit_count(p)}
        for p in MAINLAND_ORDER
        if idx.hit_count(p) > 0
    ]
    out.extend(
        {
            "value": s,
            "label": BUCKET_LABELS[s],
            "group": group_of(s),
            "count": idx.hit_count(s),
        }
        for s in SENTINELS
        if idx.hit_count(s) > 0
    )
    return out


def selfcheck(db_path: str | Path | None = None) -> dict[str, Any]:
    """验证「各键计数之和 = 总行数」。

    这个不变量一旦破坏，说明归一层漏掉了某个取值——筛选器的计数会
    与实际列表条数对不上。`/api/overview` 每次返回前都会跑一次。
    """
    idx = get_index(db_path)
    counted = sum(idx.counts.values())
    return {
        "ok": counted == idx.total,
        "counted": counted,
        "total": idx.total,
        "drift": counted - idx.total,
    }
