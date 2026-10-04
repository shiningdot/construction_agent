#!/usr/bin/env python3
"""V2 核验一键入口：正文四类 + 表格 + 图 + 两份投稿要件。

改稿流程第 5 步要依次跑多个脚本，容易漏跑一两个——尤其只改了一句话、
觉得"跟数字无关"的时候。这个入口按序执行全部八项并汇总，漏跑一处不会
静默通过。

分项各自的判据见其文件头：
  check_citations_v2.py      文献 ↔ 正文双向（孤立文献 / 悬空引用）
  check_xref_v2.py           图表与章节交叉引用 + 中英段落对等
  check_numbers_v2.py        数字对账清单 ↔ 正文（50 项统一值）
  check_forbidden_v2.py      导师要求删除的 7 类禁用措辞（含 --self-test 自证）
  check_tables_v2.py         九张表逐格 ↔ 源数据（含 --self-test 自证）
  check_figures_v2.py        六张图内数值 ↔ 源数据（含 --drift-test 自证）
  check_highlights_v2.py     Highlights：条数 / 字符数 / 措辞 / 数字有据
  check_cover_letter_v2.py   投稿信：数字有据 / 措辞 / 标题一致

前四项管"正文与自己的另一份记录对不对"，表与图两项管"正文与源数据对不对"，
末两项管"投稿要件与正文对不对"。**检查不到的部分不会被这行绿盖住**：
表格项会把表 7 无源可核的两列显式列出来，图项会把图 1 的 226 列出来，
而不是跳过。

用法：python3 scripts/check_v2_all.py [--quiet]
退出码 0 = 八项全绿；非 0 = 有分项未通过（汇总行列出）。
"""
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
CHECKS = [
    ('引用', 'check_citations_v2.py'),
    ('结构', 'check_xref_v2.py'),
    ('数字', 'check_numbers_v2.py'),
    ('措辞', 'check_forbidden_v2.py'),
    ('表格', 'check_tables_v2.py'),
    ('图', 'check_figures_v2.py'),
    ('Highlights', 'check_highlights_v2.py'),
    ('投稿信', 'check_cover_letter_v2.py'),
]


def main(quiet=False):
    results = []
    for label, name in CHECKS:
        path = SCRIPTS / name
        if not path.exists():
            results.append((label, name, None, 0.0, f'脚本不存在：{path}'))
            continue
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(path)],
                              capture_output=True, text=True)
        dur = time.time() - t0
        # 汇总行取"末尾往前第一个非缩进非空行"——不能按 ✅/❌ 前缀找：
        # check_xref_v2.py 的结论行写作"合计待处置：0"，而中间过程行
        # （如"✅ 无标题的段落数出入 ≥2"）反而带 ✅，按前缀找会抓错。
        # 也不能取最后一行：check_forbidden_v2.py 末尾是缩进的投稿提醒。
        out = (proc.stdout or proc.stderr).strip().split('\n')
        summary = next((l.strip() for l in reversed(out)
                        if l.strip() and not l.startswith((' ', '\t'))),
                       '(无输出)')
        results.append((label, name, proc.returncode, dur, summary))

    if not quiet:
        for label, name, code, dur, summary in results:
            mark = '✅' if code == 0 else ('❌' if code is not None else '⚠')
            print(f'{mark} {label}  {name:<24}{dur:5.1f}s  {summary}')

    print('\n' + '=' * 72)
    bad = [(l, n, c) for l, n, c, _, _ in results if c != 0]
    total = sum(d for *_, d, _ in results)
    if bad:
        print(f'❌ {len(bad)}/{len(CHECKS)} 项未通过（共 {total:.1f}s）：')
        for label, name, code in bad:
            print(f'   {label}  {name}  退出码 {code}')
        return 1
    print(f'✅ {len(CHECKS)} 项核验全绿（共 {total:.1f}s）')
    return 0


if __name__ == '__main__':
    sys.exit(main('--quiet' in sys.argv))
