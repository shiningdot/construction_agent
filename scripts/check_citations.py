#!/usr/bin/env python3
"""Phase 5a 引用合规校验 v2：逐条核对 27 条参考文献是否在正文被引用"""
import re

PATH = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper.md'
text = open(PATH, encoding='utf-8').read()
body = text.split('## References')[0]

# 27 条参考文献（首作者, 年份, 正文中应出现的引用形式）
refs = [
    ('Ahmadi', '2025', 'Ahmadi et al. (2025)'),
    ('Ahmadi', '2024', 'Ahmadi et al. (2024)'),
    ('Ayhan', '2019', 'Ayhan and Tokdemir (2019)'),
    ('Brown', '2020', 'Brown et al., 2020'),
    ('Devlin', '2019', 'Devlin et al., 2019'),
    ('Huang', '2026', 'Huang et al. (2026)'),
    ('Jiang', '2026', 'Jiang et al. (2026)'),
    ('Kim', '2024', 'Kim and Han (2024)'),
    ('Kojima', '2022', 'Kojima et al., 2022'),
    ('Kong', '2024', 'Kong and Ahn, 2024'),
    ('Liu', '2025a', 'Liu, Ding, et al. (2025)'),
    ('Liu', '2025b', 'Liu, Li, et al. (2025)'),
    ('Liu', '2025c', 'Liu et al. (2025)'),
    ('LiuLuo', '2025', 'Liu and Luo, 2025'),
    ('Onuchukwu', '2024', 'Onuchukwu et al., 2024'),
    ('OpenAI', '2023', 'OpenAI, 2023'),
    ('Rafindadi', '2023', 'Rafindadi et al., 2023'),
    ('Shappell', '2000', 'Shappell and Wiegmann, 2000'),
    ('Shi', '2025', 'Shi et al. (2025)'),
    ('Togan', '2025', 'Togan et al., 2025'),
    ('Wang', '2023', 'Wang et al., 2023'),
    ('Wei', '2022', 'Wei et al., 2022'),
    ('Xiao', '2026', 'Xiao et al. (2026)'),
    ('Yan', '2025', 'Yan et al. (2025)'),
    ('Yang', '2025', 'Yang and Park (2025)'),
    ('Zhang', '2025a', 'Zhang, Hou, et al. (2025)'),
    ('Zhang', '2025b', 'Zhang, Wei, et al. (2025)'),
    ('Zhou', '2023', 'Zhou et al., 2023'),
]

print('=== 逐条核对 ===')
problems = []
for key, year, expected in refs:
    # 检查参考文献列表条目存在
    ref_section = text.split('## References')[1]
    n_ref = ref_section.count(key + ',')
    # 检查正文引用存在
    n_body = body.count(expected)
    status = []
    if n_ref == 0:
        status.append('参考文献缺失')
    if n_body == 0:
        status.append('正文未引用')
    flag = '✓' if not status else '✗ ' + '; '.join(status)
    print(f'  {flag} {expected}  (正文{n_body}处, 文献{n_ref}条)')
    if status:
        problems.append((expected, status))

print()
print('问题总数:', len(problems))
if not problems:
    print('✅ 引用双向核对通过')
