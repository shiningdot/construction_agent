"""三方互异的格子里，可由规则机械判定的部分。

只覆盖两类真正"规则可判"的字段，判不了的（事故类型/坍塌子类/工程类型等需要读原文）
一律返回 None，留给人工。

规则依据：
- accident_time 合法值必须是 HH:MM。三方中只有一个给出合法时刻 → 采用它（另两方只是没找到）；
  三方都无合法值 → 置空（如库内把"下午"填进了时刻字段）。
- city 的取值应是地级市/自治州/地区/盟级名称。剔除脱敏占位符（××县）、县级名称（…县）、
  以及空值；在剩下的里优先自治州/地区/盟，同级取信息最全的写法（如"文山壮族苗族自治州"）。
"""
from __future__ import annotations

import re

TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{2})\s*$")
EMPTY = {None, "", "未知", "未提及", "null", "不详"}


def time_valid(v):
    m = TIME_RE.match(str(v if v is not None else ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if 0 <= h <= 23 and 0 <= mi <= 59 else None


def city_valid(v):
    s = str(v if v is not None else "").strip()
    if not s or s in EMPTY:
        return None
    if re.search(r"[×xX?？]|未知|不详", s):          # 脱敏占位符 / 无效标记
        return None
    if re.search(r"(自治州|地区|盟|市|州)$", s):       # 地级市 / 自治州 / 地区 / 盟
        return s
    return None                                      # 县级（…县）不当地级市用


def resolve(field: str, cands: list):
    """返回 (值, 说明)。判不了返回 (None, None)。值 None 表示应置空。"""
    vals = [c for c in cands if c is not None and str(c).strip() not in EMPTY]

    if field == "accident_time":
        good = {t for t in (time_valid(v) for v in vals) if t}
        if len(good) == 1:
            return good.pop(), f"三方中唯一合法时刻（原候选 {[_s(v) for v in cands]}）"
        if not good:
            return None, f"三方均无合法 HH:MM 值（原候选 {[_s(v) for v in cands]}），置空"
        return None, None                            # 多个不同时刻 → 仍需人工

    if field == "city":
        good = {c for c in (city_valid(v) for v in vals) if c}
        if good:
            pick = max(good, key=lambda s: (bool(re.search(r"自治州|地区|盟", s)), len(s)))
            return pick, f"取规范地级名称（原候选 {[_s(v) for v in cands]}）"
        return None, f"三方均非有效地级市/州名（原候选 {[_s(v) for v in cands]}），置空"

    return None, None


def _s(v):
    return "空" if (v is None or str(v).strip() == "") else str(v)


if __name__ == "__main__":
    for f, c in [("accident_time", [None, None, "下午"]),
                 ("accident_time", ["07:00", None, None]),
                 ("accident_time", ["07:00", "08:00", None]),
                 ("city", ["文山州", "文山壮族苗族自治州", "文山市"]),
                 ("city", [None, None, "××县"]),
                 ("accident_type", ["起重伤害", "高处坠落", "坍塌"])]:
        print(f"{f:16s} {c} -> {resolve(f, c)}")
