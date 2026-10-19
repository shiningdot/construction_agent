#!/usr/bin/env python3
"""Stage 2.5 参考文献 DOI 核验（stdlib urllib 版）"""
import urllib.request, urllib.error, time, re, json

DOIS = {
    'Ahmadi 2024': '10.26599/jic.2024.9180039',
    'Ayhan 2019': '10.1016/j.ssci.2018.11.001',
    'Huang 2026': '10.1016/j.ocecoaman.2025.108015',
    'Jiang 2026': '10.1016/j.measurement.2026.121558',
    'Kim&Han 2024': '10.13161/kibim.2024.14.4.023',
    'Kong 2024': '10.3390/info15070390',
    'Liu,Ding 2025': '10.1016/j.autcon.2025.106482',
    'Liu,Li 2025': '10.1016/j.eswa.2025.126422',
    'Liu&Luo 2025': '10.17868/strath.00093269',
    'Onuchukwu 2024': '10.3390/buildings14030595',
    'Rafindadi 2023': '10.1016/j.heliyon.2023.e13389',
    'Shi 2025': '10.1016/j.eswa.2025.127817',
    'Togan 2025': '10.1109/ACCESS.2025.3576442',
    'Xiao 2026': '10.1016/j.ress.2025.111585',
    'Yan 2025': '10.3390/drones9100704',
    'Yang&Park 2025': '10.1109/ACCESS.2025.3648842',
    'Zhang,Hou 2025': '10.3390/buildings15040527',
    'Zhang,Wei 2025': '10.1016/j.eswa.2025.129947',
}

ARXIVS = {
    'Ahmadi(GRPO) 2025': '2508.21201',
    'Brown(GPT-3) 2020': '2005.14165',
    'Devlin(BERT) 2019': '1810.04805',
    'OpenAI(GPT-4) 2023': '2303.08774',
    'Wei(CoT) 2022': '2201.11903',
    'Kojima 2022': '2205.11916',
    'Zhou 2023': '2205.10625',
    'Wang 2023': '2203.11171',
}


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'CAIEMS-paper-check/1.0 (mailto:check@example.org)'})
    return urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')


ok = fail = 0
print('=== CrossRef DOI 核验 ===')
for name, doi in DOIS.items():
    try:
        data = json.loads(get(f'https://api.crossref.org/works/{doi}'))
        title = (data['message'].get('title') or ['?'])[0][:70]
        print(f'  OK {name}: [{title}]')
        ok += 1
    except Exception as e:
        print(f'  FAIL {name}: {str(e)[:70]}')
        fail += 1
    time.sleep(0.5)

print()
print('=== arXiv ID 核验 ===')
for name, aid in ARXIVS.items():
    try:
        xml = get(f'https://export.arxiv.org/api/query?id_list={aid}')
        m = re.search(r'<entry>.*?<title>(.*?)</title>', xml, re.S)
        title = re.sub(r'\s+', ' ', m.group(1)).strip()[:70] if m else '?'
        print(f'  OK {name}: [{title}]')
        ok += 1
    except Exception as e:
        print(f'  FAIL {name}: {str(e)[:70]}')
        fail += 1
    time.sleep(0.6)

print(f'\n=== DOI/arXiv 核验: {ok} 通过, {fail} 失败 ===')
