#!/usr/bin/env python3
"""修复论文文件中错位的章节 banner 注释（重排后 banner 附着到了错误章节）"""
import re

def fix_banners(path, is_zh):
    with open(path, encoding='utf-8') as f:
        text = f.read()
    # 删除所有 banner 行
    lines = [ln for ln in text.split('\n')
             if not ln.strip().startswith('<!-- =') and not ln.strip().startswith('<!-- 第')
             and not ln.strip().startswith('<!-- Chapter')]
    text = '\n'.join(lines)
    # 按章节名重建 banner
    names = ({1: '引言', 2: '相关工作', 3: '系统设计', 4: '实验评估', 5: '讨论', 6: '结论'}
             if is_zh else
             {1: 'Introduction', 2: 'Related Work', 3: 'System Design',
              4: 'Experimental Evaluation', 5: 'Discussion', 6: 'Conclusion'})

    def banner(n):
        inner = ('第 %d 章：%s' % (n, names[n])) if is_zh else ('Chapter %d: %s' % (n, names[n]))
        return ('<!-- ' + '=' * 60 + ' -->\n'
                '<!-- ' + inner + ' -->\n'
                '<!-- ' + '=' * 60 + ' -->\n')

    for n in range(6, 0, -1):
        m = re.search(r'^## %d\. ' % n, text, re.MULTILINE)
        if m and '<!--' not in text[max(0, m.start() - 150):m.start()]:
            text = text[:m.start()] + banner(n) + text[m.start():]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('%s: OK' % path)

fix_banners('/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper.md', False)
fix_banners('/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper_zh.md', True)
