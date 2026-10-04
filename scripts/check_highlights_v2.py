#!/usr/bin/env python3
"""V2 Highlights 合规校验：Elsevier 必交独立文件的两项硬约束。

Highlights 是 Elsevier 的必交独立文件（不并入正文），带两个硬性格式约束：
**3–5 条**、**每条不超过 85 个字符（含空格）**——投稿系统会直接拒收超限项，
而且这两条靠肉眼数很容易出错。

本脚本同时复用另两个核验脚本的判定：
  - 禁用措辞（check_forbidden_v2.iter_hits）——Highlights 单独送到审稿人
    眼前，比正文更容易出现"没有依据的漂亮话"；
  - 数字一致性（check_numbers_v2.CHECKLIST）——引用的数字必须已在正文
    统一值清单上，否则 Highlight 与正文会脱节。

数字提取沿用 check_numbers_v2.py B 段的规则：1–2 位整数视为结构性噪声
（否则 "2×2 消融" 的 2 会被当成实验数据）。

用法：python3 scripts/check_highlights_v2.py
退出码 0 = 条数 / 字符数 / 禁用措辞 / 数字四项全合规。
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_forbidden_v2 as F  # noqa: E402
import check_numbers_v2 as N  # noqa: E402

BASE = HERE.parent / 'docs' / 'paper_v2'
SRC = BASE / 'highlights.md'
COUNT_RANGE = (3, 5)
CHAR_LIMIT = 85


def main():
    if not SRC.exists():
        print(f'⚠ 未找到 {SRC}（Highlights 尚未起草）')
        return 1
    text = SRC.read_text(encoding='utf-8')
    en_block = text.split('## 中文对照')[0]
    items = re.findall(r'^\d+\.\s+(.+)$', en_block, re.M)

    bad = 0
    print('A. 条数与字符数')
    print(f'{"#":<3}{"字符":>5}  状态  Highlight')
    print('-' * 74)
    for i, s in enumerate(items, 1):
        n = len(s)
        ok = n <= CHAR_LIMIT
        bad += not ok
        print(f'{i:<3}{n:>5}  {"✅" if ok else "❌ 超 85"}  {s}')
    lo, hi = COUNT_RANGE
    cnt_ok = lo <= len(items) <= hi
    bad += not cnt_ok
    print(f'\n  条数 {len(items)}（要求 {lo}–{hi}）  '
          f'{"✅" if cnt_ok else "❌"}')

    print('\nB. 禁用措辞（复用 check_forbidden_v2 判定）')
    hits = [(zh, k) for s in items for k, zh, *_ in F.iter_hits(s, 'EN')]
    if hits:
        bad += len(hits)
        for zh, kind in hits:
            print(f'  ❌ 「{zh}」  {kind}')
        print(f'  → {len(hits)} 处命中')
    else:
        print('  ✅ 无')

    print('\nC. 数字是否已在正文统一值清单上')
    known = {v.replace(',', '') for vals in N.CHECKLIST.values() for v in vals}
    nums = set()
    for s in items:
        for m in re.finditer(r'\d[\d,]*', s):
            t = m.group(0).replace(',', '')
            if re.fullmatch(r'\d{1,2}', t):   # 结构性噪声（如 "2×2"）
                continue
            nums.add(t)
    unknown = sorted(nums - known)
    print(f'  正文清单上的数字：{sorted(nums)}')
    if unknown:
        bad += len(unknown)
        print(f'  ❌ 不在清单上：{unknown}（须与正文统一值同步）')
    else:
        print('  ✅ 全部已登记')

    print('\n' + '=' * 74)
    if bad:
        print(f'❌ {bad} 项待处置')
        return 1
    print('✅ Highlights 四项全合规（条数 / 字符数 / 禁用措辞 / 数字）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
