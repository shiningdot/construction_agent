#!/usr/bin/env python3
"""原样本 F1 级消融: 用专家导出中的原始 100 条文本补跑 v1/v2a/v2b
(v2 = 既有 S4 结果，无需重跑)
产出: experiments/ablation_original/{cell}_checkpoint.json
"""
import json, os, sys, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 本仓库根（脚本在 scripts/ 下，上溯一级），worktree 与主仓库都成立。
# 原先两处 sys.path 与 OUT_DIR 写死 classifier-mod worktree。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from _common import main_repo_root      # noqa: E402 —— 须在 sys.path 之后
from dotenv import load_dotenv
# `.env` 的真源在主仓库根（worktree 里不一定有；config.py 亦按此上溯查找）
load_dotenv(str(main_repo_root() / '.env'), override=True)
from anthropic import Anthropic
from ablation_gatekeeping import (PROMPTS, JSON_SPEC, USER_PREFIX, CHECKS_BODY,
                                  TWOSTEP_BODY, CONSERVATIVE, COVERAGE,
                                  extract_json, norm_codes)

OUT_DIR = str(ROOT / 'experiments' / 'ablation_original')
os.makedirs(OUT_DIR, exist_ok=True)
MODEL = 'deepseek-v4-pro'
MAX_TOKENS = 16384
TEMPERATURE = 0.0
WORKERS = 4
MAX_RETRY = 1
CELLS = ['v1', 'v2a', 'v2b']

# ── 从专家导出恢复原始 100 条文本 ──
def load_original_texts():
    d = json.load(open('/home/suntianhao/TEM/expertA.json'))
    texts = []
    for task in d:
        t = task.get('data', {}).get('text', '')
        texts.append(t)
    return texts

def call_llm(client, system, user_text):
    for attempt in range(MAX_RETRY + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                system=system,
                messages=[{'role': 'user', 'content': user_text}],
            )
            raw = ''
            for blk in resp.content:
                if hasattr(blk, 'text'):
                    raw += blk.text
            return raw, None
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(3)
            else:
                return None, str(e)[:120]

def run_cell(cell, texts, checkpoint_path):
    done = {}
    if os.path.exists(checkpoint_path):
        done = {d['idx']: d for d in json.load(open(checkpoint_path))}
    client = Anthropic()
    spec = PROMPTS[cell]
    system = f"你是建筑施工安全事故分析专家。使用CAICS框架按M→F→T→H→C因果链逐层分析。\n\n{spec['body']}\n\n{spec['policy']}\n\n{JSON_SPEC}"

    def process(i, text):
        if i in done:
            return None
        out, err = call_llm(client, system, USER_PREFIX + text[:3500])
        parsed = extract_json(out) if out else None
        if isinstance(parsed, list):
            parsed = {'codes': parsed}
        raw_codes = parsed.get('codes', []) if parsed else []
        codes = norm_codes(raw_codes)
        return {'idx': i, 'codes': codes, 'n_codes': len(codes),
                'parse_error': not bool(parsed), 'api_error': err}

    results = list(done.values())
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(process, i, t): i for i, t in enumerate(texts)}
        for k, fut in enumerate(as_completed(futs)):
            r = fut.result()
            if r:
                results.append(r)
                if (k + 1) % 20 == 0:
                    json.dump(results, open(checkpoint_path, 'w'), ensure_ascii=False)
                    print(f'  [{cell}] {len(results)}/100', flush=True)
    json.dump(results, open(checkpoint_path, 'w'), ensure_ascii=False)
    return results

if __name__ == '__main__':
    texts = load_original_texts()
    print(f'原始文本: {len(texts)} 条', flush=True)
    for cell in CELLS:
        cp = f'{OUT_DIR}/{cell}_checkpoint.json'
        t0 = time.time()
        results = run_cell(cell, texts, cp)
        n = len(results)
        avg = sum(r['n_codes'] for r in results) / n if n else 0
        print(f'  {cell}: {n}/100 平均编码 {avg:.2f} 耗时 {time.time()-t0:.0f}s', flush=True)
    print('完成', flush=True)
