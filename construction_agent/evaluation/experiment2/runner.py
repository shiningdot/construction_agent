"""E2: Strategy × Model Interaction Experiment Runner.

Reuses E1's model_clients and metrics. Tests V2/V3/V4 strategies
across DeepSeek V4, Qwen 3.7, and GLM 5.2 on 50 stratified samples.
"""

from __future__ import annotations

import json, logging, time, re, random
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from construction_agent.evaluation.experiment1.model_clients import create_client
from construction_agent.evaluation.experiment1.config import CORE_MODELS, ModelConfig
from construction_agent.evaluation.experiment1.metrics import compute_all_metrics
from construction_agent.evaluation.experiment1.unified_prompt import SYSTEM_PROMPT as V2_SYSTEM_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("E2")

SILVER_STANDARD_PATH = Path("data/datasets/accidents_normalized.json")
E2_OUTPUT_DIR = Path("data/experiments/e2/results")
E2_SAMPLES_PATH = Path("data/experiments/e2/e2_samples.json")


# ── Strategy Definitions ──

def _parse_json_response(text: str) -> dict:
    if not text: return {"_error": "empty response", "_success": False}
    try:
        data = json.loads(text); data["_success"] = True; return data
    except json.JSONDecodeError: pass
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            data = json.loads(m.group(1)); data["_success"] = True; return data
        except json.JSONDecodeError: pass
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            data = json.loads(m.group(0)); data["_success"] = True; return data
        except json.JSONDecodeError: pass
    return {"_error": f"JSON parse failed: {text[:200]}", "_success": False}


V3_SYSTEM = """你是建筑施工安全分析专家。你的任务是从事故信息中提取结构化数据并进行致因分析。

## 分析步骤
1. 先识别事故中的关键致因线索（人的不安全行为、设备/物的不安全状态、管理缺陷、环境不利条件）
2. 对每个维度逐一判断是否存在该维度致因
3. 汇总为完整JSON输出

## 输出要求
必须输出纯JSON，字段使用英文名称：title, date, accident_time, location, city, accident_type, severity, deaths, injuries, direct_cause, contractor, summary, human_factors, machine_factors, management_factors, environmental_factors。
human_factors等致因数组的每项含 category 和 description 两个字段。
直接输出JSON，不要Markdown包裹。"""

V4_SYSTEM = """你是施工安全事故分析Agent。请按两步法处理：
Step 1: 扫描文本中的事故类型、伤亡数字和致因关键词
Step 2: 综合扫描结果，输出完整的结构化JSON

## 输出要求
必须输出纯JSON，字段使用英文名称：title, date, location, city, accident_type, severity, deaths, injuries, direct_cause, summary, human_factors(数组，每项含category和description), machine_factors(数组), management_factors(数组), environmental_factors(数组), hfacs_codes(数组)。
直接输出JSON，不要Markdown包裹。"""


def strategy_v2(text: str, cfg: ModelConfig) -> dict:
    client = create_client(cfg)
    resp = client.call(V2_SYSTEM_PROMPT, f"请分析以下建筑施工事故信息，提取结构化数据：\n\n{text}")
    if not resp["success"]: return {"_error": resp.get("error","unknown"), "_success": False}
    return _parse_json_response(resp["text"])


def strategy_v3(text: str, cfg: ModelConfig) -> dict:
    """V3: dimension-by-dimension analysis before JSON output."""
    client = create_client(cfg)
    resp = client.call(V3_SYSTEM, f"请逐一分析四个致因维度后输出完整JSON：\n\n{text}")
    if not resp["success"]: return {"_error": resp.get("error","unknown"), "_success": False}
    return _parse_json_response(resp["text"])


def strategy_v4(text: str, cfg: ModelConfig) -> dict:
    """V4: two-pass agentic - identify clues then extract."""
    client = create_client(cfg)
    resp = client.call(V4_SYSTEM, f"请分两步处理此事故文本：\n\n{text}")
    if not resp["success"]: return {"_error": resp.get("error","unknown"), "_success": False}
    return _parse_json_response(resp["text"])


# ── Runner ──

@dataclass
class E2Result:
    model: str; strategy: str
    predictions: list[dict]; ground_truth: list[dict]
    metrics: dict = field(default_factory=dict)
    latency_total: float = 0.0; api_calls: int = 0

STRATEGIES = {"V2": strategy_v2, "V3": strategy_v3, "V4": strategy_v4}


class E2Runner:
    def __init__(self, sample_size: int = 50):
        self.sample_size = sample_size

    def load_samples(self) -> list[dict]:
        if E2_SAMPLES_PATH.exists():
            return json.loads(E2_SAMPLES_PATH.read_text(encoding="utf-8"))
        ss = json.loads(SILVER_STANDARD_PATH.read_text(encoding="utf-8"))
        random.seed(42)
        by_type = defaultdict(list)
        for r in ss: by_type[r.get("accident_type","其他")].append(r)
        samples = []
        per = max(1, self.sample_size // len(by_type))
        for t, pool in by_type.items():
            samples.extend(random.sample(pool, min(per, len(pool))))
        remain = [r for r in ss if r not in samples]
        need = self.sample_size - len(samples)
        if need > 0 and remain: samples.extend(random.sample(remain, min(need, len(remain))))
        E2_SAMPLES_PATH.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"抽样: {len(samples)} 条")
        return samples

    def run_one(self, strat_name: str, strat_fn, model_cfg: ModelConfig, samples: list[dict]) -> E2Result:
        key = f"{model_cfg.name}_{strat_name}"
        logger.info(f"[{key}] 开始...")
        preds, total_t, calls = [], 0.0, 0
        for i, s in enumerate(samples):
            text = s.get("summary","") or s.get("raw_input","") or s.get("title","")
            t0 = time.time()
            try: r = strat_fn(text, model_cfg)
            except Exception as e: r = {"_error": str(e), "_success": False}
            preds.append(r); total_t += time.time()-t0; calls += 1
            if not r.get("_success"):
                logger.warning(f"  [{i+1}/{len(samples)}] fail: {str(r.get('_error',''))[:80]}")
        try: metrics = compute_all_metrics(preds, samples)
        except Exception as e: metrics = {"error": str(e)}
        rv = E2Result(model=model_cfg.name, strategy=strat_name, predictions=preds,
                       ground_truth=samples, metrics=metrics, latency_total=total_t, api_calls=calls)
        bf = metrics.get("basic_fields",{})
        cf = metrics.get("cause_dim_f1",{})
        logger.info(f"[{key}] Acc={bf.get('avg_accuracy',0):.3f} CauseF1={cf.get('avg',{}).get('f1',0):.3f}")
        return rv

    def run_all(self, models: list[str] = None) -> dict[str, E2Result]:
        samples = self.load_samples()
        all_m = {m.name: m for m in CORE_MODELS if m.enabled}
        if models:
            # Filter by model key OR name
            filtered = {}
            for name, mc in all_m.items():
                if mc.key in models or name in models:
                    filtered[name] = mc
            all_m = filtered
        E2_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        results = {}
        for mk, mc in all_m.items():
            for sn, sf in STRATEGIES.items():
                res = self.run_one(sn, sf, mc, samples)
                results[f"{mk}_{sn}"] = res
                data = {"model":res.model,"strategy":res.strategy,"sample_count":len(res.predictions),
                        "metrics":res.metrics,"latency_total":res.latency_total,"api_calls":res.api_calls}
                (E2_OUTPUT_DIR / f"{mk}_{sn}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="E2: Strategy × Model Interaction")
    p.add_argument("--models", nargs="*", default=None, help="Models (default: all)")
    p.add_argument("--sample-size", type=int, default=50)
    args = p.parse_args()
    runner = E2Runner(sample_size=args.sample_size)
    results = runner.run_all(models=args.models)
    print(f"\n✅ E2 完成: {len(results)} 条件")
    for k, r in results.items():
        bf = r.metrics.get("basic_fields",{})
        print(f"  {k}: Acc={bf.get('avg_accuracy',0):.3f}")
