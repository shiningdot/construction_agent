#!/usr/bin/env python3
"""用恢复的专家GT重算六策略指标 + bootstrap CI"""
import json, sys, random
from pathlib import Path

# 本仓库根（脚本在 scripts/ 下，上溯一级），worktree 与主仓库都成立。
# 原先 `sys.path` 与 BASE 指向主仓库、GT 又指向 classifier-mod worktree，
# 两处都可能落空：主仓库下没有 experiments/experiment_6strategies（见
# CLAUDE.md），而写死的那个 worktree 一旦改名/清理就断。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from construction_agent.evaluation.experiment3.runner import compute_metrics

BASE = str(ROOT / 'experiments' / 'experiment_6strategies')
GT = json.load(open(str(ROOT / 'experiments' / 'ablation_gatekeeping'
                         / 'gt_recovered.json')))
gt_list = [GT[str(i)] for i in range(100)]

# 加载各策略结果（按 index 1-100 排序，统一解析到顶层 codes/accident_type/severity）
def load_results(name):
    d = json.load(open(f'{BASE}/S{name}_result/S{name}_results.json'))
    results = d['results'] if isinstance(d, dict) else d
    if isinstance(results, dict):
        results = list(results.values())
    results = sorted(results, key=lambda r: r.get('index', r.get('idx', 0)))
    norm = []
    for r in results:
        p = r.get('parsed', r)
        if isinstance(p, list):
            p = {'codes': p}
        if not isinstance(p, dict):
            p = {}
        norm.append({
            'codes': p.get('codes', []),
            'accident_type': p.get('accident_type', ''),
            'severity': p.get('severity', ''),
            'deaths': p.get('deaths'),
            'injuries': p.get('injuries'),
        })
    return norm

STRATS = {'0': 'S0', '1': 'S1', '2': 'S2', '3': 'S3', '4': 'S4', '5': 'S5'}
REPORTED = {'0': 0.628, '1': 0.737, '2': 0.630, '3': 0.643, '4': 0.676, '5': 0.488}

print('=== 六策略 Ave+ F1 复现（多数投票GT） vs 原报告 ===')
for k in sorted(STRATS):
    try:
        preds = load_results(k)
        # 对齐: 前100条
        preds = preds[:100]
        m = compute_metrics(preds, gt_list)
        rep = REPORTED[k]
        delta = m['ave_plus']['F1'] - rep
        flag = '✓' if abs(delta) < 0.03 else '⚠'
        print(f"  S{STRATS[k]}: Ave+F1 = {m['ave_plus']['F1']:.3f} (报告 {rep}) Δ={delta:+.3f} {flag} | "
              f"TypeAcc={m['type_acc']:.3f} SevAcc={m['sev_acc']:.3f}")
    except Exception as e:
        print(f'  S{STRATS[k]}: 加载失败 {str(e)[:60]}')

# Bootstrap CI (1000 次重采样)
print()
print('=== Bootstrap 95% CI (1,000 次重采样, Ave+ F1) ===')
random.seed(42)
for k in sorted(STRATS):
    preds = load_results(k)[:100]
    f1s = []
    for _ in range(1000):
        idx = [random.randint(0, 99) for _ in range(100)]
        gt_boot = [gt_list[i] for i in idx]
        pred_boot = [preds[i] for i in idx]
        m = compute_metrics(pred_boot, gt_boot)
        f1s.append(m['ave_plus']['F1'])
    f1s.sort()
    lo, hi = f1s[25], f1s[974]
    print(f"  S{STRATS[k]}: F1 = {sum(f1s)/1000:.3f} [95% CI {lo:.3f}, {hi:.3f}]")
