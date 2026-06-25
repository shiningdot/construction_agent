"""日期标准化工具。

将各种常见中文日期格式归一化为 ISO 格式（YYYY-MM-DD）。
"""

from __future__ import annotations

import re
from typing import Optional


def normalize_date(date_str: Optional[str]) -> Optional[str]:
    """将各种常见日期格式归一化为 YYYY-MM-DD。

    支持的输入格式：
    - ISO: 2024-03-15, 2024-3-15, 2024/03/15, 2024.03.15
    - 中文: 2024年3月15日, 2024年03月15日
    - 中英混合: 2024年3月15号, 2024-03-15日
    - 年月: 2024年3月, 2024-03, 2024/03
    - 年: 2024年

    Args:
        date_str: 原始日期字符串

    Returns:
        YYYY-MM-DD 格式字符串；只能识别到年月则返回 YYYY-MM；只能识别到年则返回 YYYY；
        无法识别返回 None
    """
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()

    # 移除多余空格和常见噪音
    date_str = re.sub(r'\s+', '', date_str)

    # ── 正则模式匹配（按精确度降序）──

    # 1. ISO 格式连字符: 2024-03-15 或 2024-3-15
    m = re.match(r'^(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})$', date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 2. 中文格式: 2024年3月15日 或 2024年03月15日
    m = re.match(r'^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:期)?$', date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 3. 中文格式带"号": 2024年3月15号
    m = re.match(r'^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*号$', date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 4. ISO 年月: 2024-03 或 2024/03 或 2024年3月
    m = re.match(r'^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*月?$', date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"

    # 5. 只含年月日数字且连续: 20240315
    m = re.match(r'^(\d{4})(\d{2})(\d{2})$', date_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 6. 只有年份: 2024年 或 2024
    m = re.match(r'^(\d{4})\s*年?$', date_str)
    if m:
        return m.group(1)

    # 7. 月日式（补当前年份）: 3月15日, 03-15
    # 这种尽量不补年份，让 LLM 去处理，这里只兜底
    m = re.match(r'^(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?$', date_str)
    if m:
        return None  # 缺少年份，不猜测

    return None


def validate_date(date_str: Optional[str]) -> bool:
    """检查日期字符串是否为有效 ISO 格式。"""
    if not date_str:
        return False
    # 完整日期: YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        try:
            import datetime
            datetime.date.fromisoformat(date_str)
            return True
        except ValueError:
            return False
    # 年月: YYYY-MM
    if re.match(r'^\d{4}-\d{2}$', date_str):
        try:
            import datetime
            datetime.datetime.strptime(date_str, '%Y-%m')
            return True
        except ValueError:
            return False
    # 年: YYYY
    if re.match(r'^\d{4}$', date_str):
        return True
    return False


def parse_date_to_dateobj(date_str: Optional[str]) -> Optional[object]:
    """将日期字符串解析为 datetime.date 对象。

    完整日期（YYYY-MM-DD）→ date
    年月（YYYY-MM）→ date（day=1）
    年（YYYY）→ date（month=1, day=1）
    失败 → None
    """
    import datetime

    normalized = normalize_date(date_str)
    if not normalized:
        return None

    # 完整日期
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', normalized)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # 年月
    m = re.match(r'^(\d{4})-(\d{2})$', normalized)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None

    # 年
    m = re.match(r'^(\d{4})$', normalized)
    if m:
        try:
            return datetime.date(int(m.group(1)), 1, 1)
        except ValueError:
            return None

    return None
