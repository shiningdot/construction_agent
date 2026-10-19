"""
零样本 vs 少样本消融实验

对比两种提示策略：
1. 零样本：仅"请输出JSON格式"（无schema/无示例）
2. 少样本：完整System Prompt + JSON Schema + 3个示例

对每个模型只跑 20 条，作为消融分析。
"""

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from construction_agent.evaluation.experiment1.experiment_runner import Experiment1Runner
from construction_agent.evaluation.experiment1.config import CORE_MODELS
from construction_agent.evaluation.experiment1.model_clients import create_client
from construction_agent.evaluation.experiment1.unified_prompt import build_user_message
from construction_agent.evaluation.experiment1.metrics import compute_all_metrics, CORE_FIELDS

# ============================================================
# 零样本 System Prompt（极简）
# ============================================================

ZERO_SHOT_PROMPT = """你是一个建筑施工事故信息提取专家。请从以下事故描述文本中提取结构化信息，并以 JSON 格式输出。只输出 JSON，不要加其他文字。"""


# ============================================================
# 少样本 System Prompt（完整版，从 unified_prompt 复用）
# ============================================================

from construction_agent.evaluation.experiment1.unified_prompt import SYSTEM_PROMPT as FEW_SHOT_PROMPT


def run_ablation(models=None, sample_size=20):
    """运行零样本 vs 少样本对比"""
    # 1. 加载并抽样（不使用 run_all，避免提前跑模型）
    runner = Experiment1Runner(models=models, sample_size=sample_size)
    records = runner.load_ground_truth()
    runner.sample = runner.stratified_sample(records)
    runner.ground_truth = runner.sample
    ground_truth = runner.ground_truth

    results = {"zero_shot": {}, "few_shot": {}, "ablation_gain": {}}

    for model_config in runner.models:
        client = create_client(model_config)
        print(f"\n{'='*50}")
        print(f"模型: {model_config.name}")
        print(f"{'='*50}")

        for shot_type, prompt in [("few_shot", FEW_SHOT_PROMPT), ("zero_shot", ZERO_SHOT_PROMPT)]:
            records = []
            for i, gt in enumerate(ground_truth):
                text = gt.get("raw_input") or gt.get("description") or ""
                if len(text.strip()) < 10:
                    continue
                result = client.call(prompt, build_user_message(text))
                if result["success"]:
                    parsed = runner.extract_json(result["text"])
                    records.append({
                        "parsed": parsed,
                        "latency": result["latency"],
                        "tokens": result["total_tokens"],
                        "cost": result["cost_usd"],
                    })

            # 计算指标
            preds = [r["parsed"] or {} for r in records]
            aligned_gt = ground_truth[:len(preds)]
            metrics = compute_all_metrics(preds, aligned_gt)

            avg_cost = sum(r["cost"] for r in records) / max(len(records), 1)
            avg_lat = sum(r["latency"] for r in records) / max(len(records), 1)

            results[shot_type][model_config.key] = {
                "name": model_config.name,
                "metrics": metrics,
                "total_cost": sum(r["cost"] for r in records),
                "avg_cost": avg_cost,
                "avg_latency": round(avg_lat, 1),
                "success": len(records),
            }

            bf = metrics["basic_fields"]
            cf = metrics.get("cause_dim_f1", {}).get("avg", {}) or {"precision": 0, "recall": 0, "f1": 0}
            print(f"  {shot_type:10s}: Acc={bf['avg_accuracy']}%  Prec={cf['precision']}%  "
                  f"综合={metrics['overall_score']}  cost=${avg_cost:.4f}")

        # 计算消融增益
        fs = results["few_shot"][model_config.key]["metrics"]
        zs = results["zero_shot"][model_config.key]["metrics"]
        zs_avg = zs.get("cause_dim_f1", {}).get("avg", {}) or {"precision": 0}
        fs_avg = fs.get("cause_dim_f1", {}).get("avg", {}) or {"precision": 0}
        results["ablation_gain"][model_config.key] = {
            "acc_gain": round(fs["basic_fields"]["avg_accuracy"] - zs["basic_fields"]["avg_accuracy"], 1),
            "prec_gain": round(fs_avg.get("precision", 0) - zs_avg.get("precision", 0), 1),
        }

    return results


if __name__ == "__main__":
    # 跑 DeepSeek + Qwen 两个模型即可证明消融效果（GLM/Kimi 结果类似但更慢）
    results = run_ablation(models=['deepseek_v4', 'qwen3'])

    # 打印摘要
    print("\n\n============ 零样本 vs 少样本消融分析 ============")
    print(f"{'模型':15s} | {'零样本Acc':10s} {'少样本Acc':10s} {'增益':8s} | {'零样本Prec':10s} {'少样本Prec':10s} {'增益':8s}")
    print("-" * 80)
    for mk, cfg in results["few_shot"].items():
        zs = results["zero_shot"][mk]
        gain = results["ablation_gain"][mk]
        zs_acc = zs["metrics"]["basic_fields"]["avg_accuracy"]
        fs_acc = cfg["metrics"]["basic_fields"]["avg_accuracy"]
        zs_cf = zs["metrics"].get("cause_dim_f1", {}).get("avg", {}) or {"precision": 0}
        fs_cf = cfg["metrics"].get("cause_dim_f1", {}).get("avg", {}) or {"precision": 0}
        zs_prec = zs_cf.get("precision", 0)
        fs_prec = fs_cf.get("precision", 0)
        print(f"{cfg['name']:15s} | {zs_acc:5.1f}%      {fs_acc:5.1f}%      {gain['acc_gain']:+5.1f}% | "
              f"{zs_prec:5.1f}%      {fs_prec:5.1f}%      {gain['prec_gain']:+5.1f}%")

    # 保存结果
    out_path = "/home/suntianhao/construction_agent/experiments/experiment1/ablation_zeroshot.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
