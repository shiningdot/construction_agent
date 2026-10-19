#!/usr/bin/env python3
"""ESWA 编号引用转换: 作者-年份 → [n] 数字编号式
产出 docs/paper/eswa_paper_numbered.md（投稿版引用格式）"""
import re, json

SRC = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper.md'
OUT = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/eswa_paper_numbered.md'

paper = open(SRC, encoding='utf-8').read()
body, refs = paper.split('## References')
body_keep = body
_, appendix = refs.split('## Appendix')

# ── 引用键定义（引用串 → 参考条目） ──
CITES = {
    'Kong and Ahn, 2024': 'Kong, F., Ahn, S. (2024). Use of Knowledge Graphs for Construction Safety Management: A Systematic Literature Review. Information, 15(7), 390. https://doi.org/10.3390/info15070390',
    'Togan et al., 2025': 'Togan, V., Mostofi, F., Tokdemir, O.B., Kadioglu, F. (2025). Efficient Management of Safety Documents Using Text-Based Analytics to Extract Safety Attributes From Construction Accident Reports. IEEE Access, 13, 99758-99777. https://doi.org/10.1109/ACCESS.2025.3576442',
    'Devlin et al., 2019': 'Devlin, J., Chang, M.-W., Lee, K., Toutanova, K. (2019). BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding. In Proceedings of NAACL-HLT 2019, 4171-4186.',
    'Brown et al., 2020': 'Brown, T., Mann, B., Ryder, N., et al. (2020). Language Models are Few-Shot Learners. Advances in Neural Information Processing Systems, 33, 1877-1901.',
    'OpenAI, 2023': 'OpenAI (2023). GPT-4 Technical Report. arXiv:2303.08774.',
    'Ahmadi et al., 2024': 'Ahmadi, E., Muley, S., Wang, C. (2024). Automatic Construction Accident Report Analysis Using Large Language Models (LLMs). Journal of Intelligent Construction, 3(1). https://doi.org/10.26599/jic.2024.9180039',
    'Kim and Han, 2024': 'Kim, D., Han, S. (2024). Systemic Analysis of Fall Accidents Caused by Worker Inattention at Construction Sites: An Integrated Approach using HFACS and LLM. KIBIM Magazine, 14(4), 23-34. https://doi.org/10.13161/kibim.2024.14.4.023',
    'Liu et al., 2025a': 'Liu, Q., Li, F., Ng, K.K.H., Han, J., Feng, S. (2025). Accident investigation via LLMs reasoning: HFACS-guided Chain-of-Thoughts enhance general aviation safety. Expert Systems with Applications, 269, 126422. https://doi.org/10.1016/j.eswa.2025.126422',
    'Liu and Luo, 2025': 'Liu, Q., Luo, X. (2025). Large language model-based knowledge graph construction of unstructured accident reports to improve construction safety. EG-ICE 2025. https://doi.org/10.17868/strath.00093269',
    'Rafindadi et al., 2023': 'Rafindadi, A.D., Shafiq, N., Othman, I., et al. (2023). Data mining of the essential causes of different types of fatal construction accidents. Heliyon, 9(2), e13389. https://doi.org/10.1016/j.heliyon.2023.e13389',
    'Onuchukwu et al., 2024': 'Onuchukwu, I.S., Gholizadeh, P., Liko, G., Esmaeili, B. (2024). Nature of Occupational Incidents among Roofing Contractors: A Data Mining Approach. Buildings, 14(3), 595. https://doi.org/10.3390/buildings14030595',
    'Ayhan and Tokdemir, 2019': 'Ayhan, B.U., Tokdemir, O.B. (2019). Predicting the outcome of construction incidents. Safety Science, 113, 91-104. https://doi.org/10.1016/j.ssci.2018.11.001',
    'Zhang, Hou, et al., 2025a': 'Zhang, L., Hou, Y., Ren, F. (2025). AIR Agent - A GPT-Based Subway Construction Accident Investigation Report Analysis Chatbot. Buildings, 15(4), 527. https://doi.org/10.3390/buildings15040527',
    'Liu, Ding, et al., 2025b': 'Liu, Q., Ding, Y., Luo, X. (2025). Automated knowledge graph-based risk assessment for fall-from-height accidents in construction. Automation in Construction, 179, 106482. https://doi.org/10.1016/j.autcon.2025.106482',
    'Shappell and Wiegmann, 2000': 'Shappell, S.A., Wiegmann, D.A. (2000). The Human Factors Analysis and Classification System-HFACS. Report DOT/FAA/AM-00/7, Office of Aviation Medicine, Federal Aviation Administration.',
    'Wei et al., 2022': 'Wei, J., Wang, X., Schuurmans, D., et al. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models. Advances in Neural Information Processing Systems, 35, 24824-24837.',
    'Kojima et al., 2022': 'Kojima, T., Gu, S.S., Reid, M., Matsuo, Y., Iwasawa, Y. (2022). Large Language Models are Zero-Shot Reasoners. Advances in Neural Information Processing Systems, 35, 22199-22213.',
    'Wang et al., 2023': 'Wang, X., Wei, J., Schuurmans, D., et al. (2023). Self-Consistency Improves Chain of Thought Reasoning in Language Models. In Proceedings of ICLR 2023.',
    'Zhou et al., 2023': 'Zhou, D., Schaerli, N., Hou, L., et al. (2023). Least-to-Most Prompting Enables Complex Reasoning in Large Language Models. In Proceedings of ICLR 2023.',
    'Huang et al., 2026': 'Huang, Y., Yan, R., Zhang, Z. (2026). Automated knowledge extraction from marine accident reports using large language models: Graph construction and evaluation. Ocean & Coastal Management, 272, 108015. https://doi.org/10.1016/j.ocecoaman.2025.108015',
    'Jiang et al., 2026': 'Jiang, Y., Nie, B., Wang, J., Peng, P., Wang, L. (2026). Large language model-driven knowledge graph and intelligent analysis for coal mine accidents. Measurement, 277, 121558. https://doi.org/10.1016/j.measurement.2026.121558',
    'Yan et al., 2025': 'Yan, Y., Li, B., Lodewijks, G. (2025). UAV Accident Forensics via HFACS-LLM Reasoning: Low-Altitude Safety Insights. Drones, 9(10), 704. https://doi.org/10.3390/drones9100704',
    'Ahmadi et al., 2025': 'Ahmadi, A., Sharif, S., Banad, Y. (2025). Improving Aviation Safety Analysis: Automated HFACS Classification Using Reinforcement Learning with Group Relative Policy Optimization. arXiv:2508.21201.',
    'Zhang, Wei, et al., 2025b': 'Zhang, J., Zhang, W., Wei, Q., Zhong, H., Miao, Y. (2025). GRAG-ProSafe QAS: Graph retrieval-augmented generation for process production safety intelligent question and answer system. Expert Systems with Applications. https://doi.org/10.1016/j.eswa.2025.129947',
    'Yang and Park, 2025': 'Yang, J., Park, S. (2025). LLM-Assisted Knowledge Graph Construction for Managing Road Accident Information From News Articles. IEEE Access, 13, 217846-217865. https://doi.org/10.1109/ACCESS.2025.3648842',
    'Shi et al., 2025': 'Shi, D., Gan, S., Zurada, J., Guan, J., Wang, F., Weichbroth, P. (2025). A multi-model approach to construction site safety: Fault trees, Bayesian networks, and ontology reasoning. Expert Systems with Applications, 288, 127817. https://doi.org/10.1016/j.eswa.2025.127817',
    'Xiao et al., 2026': 'Xiao, X., Chen, P., Qi, B., et al. (2026). KRAIL: A knowledge-driven framework for human reliability analysis integrating IDHEAS-DATA and large language models. Reliability Engineering & System Safety, 265, 111585. https://doi.org/10.1016/j.ress.2025.111585',
    'Xuecai et al., 2024': 'Xuecai, X., Gui, F., Shifei, S., et al. (2024). Accident case data-accident causation model driven safety training method. Process Safety and Environmental Protection, 182, 1208-1226. https://doi.org/10.1016/j.psep.2023.12.042',
}

# ── 按首次出现位置排序编号（括号形式或叙事形式均可） ──
order = []
for key in CITES:
    m = re.match(r'^(.*), (\d{4}[ab]?)$', key)
    if not m:
        continue
    name, year = m.group(1), m.group(2)
    pos = body_keep.find(f'({name}, {year})')   # 括号形式
    if pos < 0:
        pos = body_keep.find(f'{name} ({year})')  # 叙事形式
    if pos < 0:
        pos = body_keep.find(f'{name}, {year}')  # 多引用组内裸形式
    if pos >= 0:
        order.append((pos, key))
order.sort()
numbering = {key: i + 1 for i, (_, key) in enumerate(order)}
print(f'正文引用键: {len(order)}/28')

# 反向替换：从长串到短串避免冲突（注意 Liu 2025a/b 等）
sorted_keys = sorted(CITES, key=len, reverse=True)
text = body_keep
for key in sorted_keys:
    n = numbering[key]
    # 叙事形式: "Key (Year)" 中 key 以 ", YYYY" 结尾 → 提取名字部分与年份
    m = re.match(r'^(.*), (\d{4}[ab]?)$', key)
    if not m:
        continue
    name, year = m.group(1), m.group(2)
    # 括号形式
    text = text.replace(f'({key})', f'[{n}]')
    # 叙事形式
    text = text.replace(f'{name} ({year})', f'{name} [{n}]')
    # 多引用组内裸形式
    text = text.replace(f'{name}, {year}', f'[{n}]')

# 多引用组: (A, 2000; B, 2020) 已在单替换后变为 ( [x]; [y] ) 或残留
def fix_groups(t):
    def repl(m):
        inner = m.group(1)
        nums = re.findall(r'\[(\d+)\]', inner)
        return '[' + ', '.join(nums) + ']'
    t = re.sub(r'\((\s*\[\d+\](?:;\s*\[\d+\])+\s*)\)', repl, t)
    return t
text = fix_groups(text)

# ── 参考列表编号化 ──
ref_lines = [f'[{numbering[key]}] {CITES[key]}' for _, key in order]
missing = [k for k in CITES if k not in numbering]
if missing:
    print('警告: 未被正文引用的键:', missing)
ref_block = '\n\n'.join(ref_lines)

out = text + '## References\n\n' + ref_block + '\n\n## Appendix' + appendix
out = out.replace('# CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction\n',
                  '# CAIEMS: An LLM-Driven Pipeline for Construction Accident Information Extraction, Causal Coding, and Online Database Construction\n')
open(OUT, 'w', encoding='utf-8').write(out)
print(f'已生成: {OUT}')
print(f'编号引用: {len(order)} 条')
