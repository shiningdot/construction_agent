#!/usr/bin/env python3
"""截取 CAIEMS Web 平台界面截图（Fig.8 素材）"""
from playwright.sync_api import sync_playwright

OUT = '/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/docs/paper/figures'
pages = [
    ('/', 'web_home'),
    ('/dataset', 'web_dataset'),
    ('/causes', 'web_causes'),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    for route, name in pages:
        try:
            page.goto(f'http://127.0.0.1:5000{route}', wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(2500)  # 等图表渲染
            page.screenshot(path=f'{OUT}/{name}.png', full_page=False)
            print(f'  ✓ {route} → {name}.png')
        except Exception as e:
            print(f'  ✗ {route}: {str(e)[:80]}')
    browser.close()
print('完成')
