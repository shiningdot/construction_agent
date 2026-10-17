"""生成「CAICS 致因标注 · 专家审核」单文件网页。

## 用途

给几位专家逐条复核 300 条事故的致因编码。每页展示：

- 事故原文（本地 PDF 提取的正文，运行时不联网）
- 现有 GT 的 19 类勾选，可逐类修改、标记存疑、写备注
- 模型参考：默认「汇总支持度」（每类被 24 格中的多少格选中），
  可切到「按策略 / 按模型」看具体编码

## 为什么参考面板不默认铺开 24 格

模型输出会锚定专家判断（anchoring）。默认只给**聚合支持度**，专家需要主动
切换才能看到具体某格，能在保留参考价值的同时降低锚定。

## 审核结果

纯前端保存（localStorage，按专家姓名隔离）+ 导出 JSON，格式：

    {expert, schema:"caics-expert-review-v1", items:[{accident_id, base_gt_codes,
     reviewed_codes, uncertain, changed_codes, note, status, reviewed_at}]}

用法：
    python3 scripts/golden_s1/build_expert_review.py
    python3 scripts/golden_s1/build_expert_review.py --out /path/to/审核.html
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/golden_s1"))

from eval_matrix import ALL_CODES, LABELS, STRATEGIES, load_cause_gt, load_preds  # noqa: E402

GOLD = ROOT / "experiments/golden_standard"
GTM = GOLD / "gt_manual"
TEMPLATE = ROOT / "scripts/golden_s1/expert_review_template.html"
DEFAULT_OUT = GOLD / "expert_review" / "专家标注审核.html"

CODE_NAMES = {
    "M1": "现场监管缺失", "M2": "隐患排查失效", "M3": "安全培训不足",
    "M4": "制度规程漏洞", "M5": "市场行为违规",
    "F1": "临边洞口防护缺失", "F2": "支撑体系失稳", "F3": "设备安全装置失效",
    "F4": "个体防护未使用",
    "T1": "设备机具故障", "T2": "物料状态异常", "T3": "地质环境突变", "T4": "气象水文触发",
    "H1": "违反操作规程", "H2": "违规指挥决策", "H3": "风险辨识失败", "H4": "应急行为失当",
    "C1": "救援响应迟缓", "C2": "信息迟报瞒报",
}
LAYERS = [
    ["M", "管理根基", [f"M{i}" for i in range(1, 6)]],
    ["F", "防线失效", [f"F{i}" for i in range(1, 5)]],
    ["T", "触发条件", [f"T{i}" for i in range(1, 5)]],
    ["H", "人的行为", [f"H{i}" for i in range(1, 5)]],
    ["C", "后果放大", ["C1", "C2"]],
]
STRATEGY_LABELS = {
    "S0": "S0′ 零样本基线", "S1": "S1′ 少样本（3 例）", "S2": "S2′ 逐层推理",
    "S3": "S3′ 计划与求解", "S4": "S4′ CAICS-CoT", "S5": "S5′ CAICS-CoT+",
}
STRATEGY_SHORT = {s: s + "′" for s in STRATEGIES}
MODEL_LABELS = {"glm5.2": "GLM-5.2", "flash": "DeepSeek Flash",
                "hy3": "混元 hy3", "pro": "DeepSeek Pro"}
MODEL_SHORT = {"glm5.2": "GLM", "flash": "Flash", "hy3": "hy3", "pro": "Pro"}


def load_texts() -> dict[str, dict]:
    out = {}
    for line in (GOLD / "golden_candidates.jsonl").open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["id"]] = r
    return out


def load_basic() -> dict[str, dict]:
    out = {}
    for line in (GTM / "gt_draft.jsonl").open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["id"]] = r.get("basic_features") or {}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="输出路径；默认按策略组合命名")
    ap.add_argument("--strategies", default=",".join(STRATEGIES),
                    help="逗号分隔，只包含这些策略的参考结果（如 S0,S1,S2,S3）")
    args = ap.parse_args()

    strategies = [s.strip().upper() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in strategies if s not in STRATEGIES]
    if unknown:
        sys.exit(f"未知策略：{unknown}；可选 {STRATEGIES}")
    if not strategies:
        sys.exit("--strategies 不能为空")
    if not args.out:
        suffix = "" if strategies == STRATEGIES else "_" + "-".join(strategies)
        args.out = str(GOLD / "expert_review" / f"专家标注审核{suffix}.html")

    cause_gt = load_cause_gt()
    texts = load_texts()
    basic = load_basic()

    n_cells = len(strategies) * len(LABELS)
    preds: dict[tuple[str, str], dict[str, dict]] = {}
    for s in strategies:
        for lb in LABELS:
            preds[(s, lb)] = load_preds(s, lb)

    ids = [i for i in cause_gt if i in texts]
    missing_text = [i for i in cause_gt if i not in texts]
    if missing_text:
        print(f"⚠️  {len(missing_text)} 条 GT 找不到原文，已跳过：{missing_text[:5]}")

    accidents = []
    for idx, aid in enumerate(ids, 1):
        t = texts[aid]
        bf = basic.get(aid, {})
        gt = [c for c in ALL_CODES if c in cause_gt[aid]]

        pred = {}
        support = {c: 0 for c in ALL_CODES}
        for s in strategies:
            for lb in LABELS:
                p = preds[(s, lb)].get(aid)
                codes = [c for c in ((p or {}).get("codes") or []) if c in ALL_CODES]
                pred[f"{s}|{lb}"] = codes
                for c in codes:
                    support[c] += 1
        # 分歧度：至少有模型选、至少有不选的类别数（0–19）
        disagreement = sum(1 for c in ALL_CODES if 0 < support[c] < n_cells)

        accidents.append({
            "id": aid,
            "idx": idx,
            "title": t.get("title") or "",
            "source": t.get("source_name") or "",
            "text": t.get("raw_text") or "",
            "type": bf.get("accident_type") or "未知",
            "severity": bf.get("severity") or "",
            "deaths": bf.get("deaths"),
            "gtCodes": gt,
            "pred": pred,
            "support": support,
            "disagreement": disagreement,
        })

    payload = {
        "meta": {
            "codes": ALL_CODES,
            "codeNames": CODE_NAMES,
            "layers": LAYERS,
            "strategies": strategies,
            "models": LABELS,
            "nCells": n_cells,
            "variant": "all" if strategies == STRATEGIES else "-".join(strategies),
            "strategyLabels": STRATEGY_LABELS,
            "strategyShort": STRATEGY_SHORT,
            "modelLabels": MODEL_LABELS,
            "modelShort": MODEL_SHORT,
        },
        "accidents": accidents,
    }

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, 9)
    b64 = base64.b64encode(gz).decode("ascii")
    print(f"事故 {len(accidents)} 条 | 正文合计 {sum(len(a['text']) for a in accidents)/1e4:.1f} 万字")
    print(f"数据：明文 {len(raw)/1e6:.2f} MB → gzip {len(gz)/1e6:.2f} MB → base64 {len(b64)/1e6:.2f} MB")

    html = TEMPLATE.read_text(encoding="utf-8")
    if "__DATA_B64__" not in html:
        sys.exit(f"模板缺少 __DATA_B64__ 占位符：{TEMPLATE}")
    html = html.replace("__DATA_B64__", b64)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已写入 {out}（{out.stat().st_size/1e6:.2f} MB）")

    # 自检
    back = json.loads(gzip.decompress(base64.b64decode(b64)))
    assert len(back["accidents"]) == len(accidents)
    assert all(len(a["pred"]) == n_cells for a in back["accidents"])
    print(f"自检通过：{len(back['accidents'])} 条 × {n_cells} 格参考"
          f"（{','.join(strategies)}）")


if __name__ == "__main__":
    main()
