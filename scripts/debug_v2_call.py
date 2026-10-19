#!/usr/bin/env python3
"""调试: 单次 v2 调用并打印原始响应"""
import sys, json, sqlite3
from pathlib import Path
sys.path.insert(0, '/home/suntianhao/construction_agent')
from dotenv import load_dotenv
load_dotenv('/home/suntianhao/construction_agent/.env', override=True)
from anthropic import Anthropic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ablation_gatekeeping import PROMPTS, JSON_SPEC, USER_PREFIX, TWOSTEP_BODY, COVERAGE, extract_json

db = sqlite3.connect('/home/suntianhao/construction_agent/data/caiems_v2.db')
db.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
row = db.execute("SELECT id, title, raw_text FROM accidents WHERE raw_text IS NOT NULL AND length(raw_text)>200 LIMIT 1").fetchone()
db.close()
acc_id, title, text = row

system = f"你是建筑施工安全事故分析专家。使用CAICS框架按M→F→T→H→C因果链逐层分析。\n\n{TWOSTEP_BODY}\n\n{COVERAGE}\n\n{JSON_SPEC}"
client = Anthropic()
resp = client.messages.create(
    model='deepseek-v4-pro', max_tokens=16384, temperature=0.0,
    system=system, messages=[{'role': 'user', 'content': USER_PREFIX + text[:3500]}],
)
print('=== content blocks ===')
for i, blk in enumerate(resp.content):
    print(f'[block {i}] type={blk.type}')
    if hasattr(blk, 'text'):
        print('  text 前200字:', blk.text[:200])
    if hasattr(blk, 'thinking'):
        print('  thinking 前200字:', blk.thinking[:200])
raw = ''.join(blk.text for blk in resp.content if hasattr(blk, 'text'))
print()
print('=== raw 后 600 字 ===')
print(raw[-600:] if raw else '(无 text 块)')
print()
parsed = extract_json(raw)
print('extract_json 结果:', json.dumps(parsed, ensure_ascii=False)[:300] if parsed else None)
print('stop_reason:', resp.stop_reason)
print('usage:', resp.usage)
