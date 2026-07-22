"""
实验一排程器：分层抽样 → 多模型 API 调用 → 结果保存
"""

import json
import os
import random
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from ..experiment1.config import (
    CORE_MODELS, SAMPLE_SIZE, GROUND_TRUTH_PATH, EXPERIMENT_DIR,
)
from ..experiment1.model_clients import create_client
from ..experiment1.unified_prompt import SYSTEM_PROMPT, build_user_message


class Experiment1Runner:
    def __init__(self, models: Optional[List[str]] = None, sample_size: int = SAMPLE_SIZE):
        self.all_models = [m for m in CORE_MODELS if m.enabled]
        self.models = [m for m in self.all_models if m.key in models] if models else self.all_models
        self.sample_size = sample_size
        self.sample = []
        self.ground_truth = []
        self.results = {}
        os.makedirs(EXPERIMENT_DIR, exist_ok=True)

    def load_ground_truth(self) -> List[Dict]:
        with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("records", data)

    def stratified_sample(self, records: List[Dict]) -> List[Dict]:
        valid = [r for r in records if len((r.get("raw_input") or r.get("description") or "")) > 20]
        buckets = defaultdict(list)
        for r in valid:
            buckets[r.get("accident_type", "未知")].append(r)

        sampled = []
        types = sorted(buckets.keys())
        min_per = max(1, min(3, self.sample_size // len(types)))
        for t in types:
            n = min(max(min_per, int(self.sample_size * len(buckets[t]) / len(valid))), len(buckets[t]))
            sampled.extend(random.sample(buckets[t], n))

        if len(sampled) < self.sample_size:
            ids = {s.get("id") for s in sampled}
            remaining = [r for r in valid if r.get("id") not in ids]
            random.shuffle(remaining)
            sampled.extend(remaining[:self.sample_size - len(sampled)])

        random.shuffle(sampled)
        sampled = sampled[:self.sample_size]
        print(f"分层抽样: {len(sampled)} 条, 覆盖 {len(types)} 种类型")
        return sampled

    def extract_json(self, text: str) -> Optional[Dict]:
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s:e + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def run_model(self, model_config) -> List[Dict]:
        client = create_client(model_config)
        records = []
        for i, gt in enumerate(tqdm(self.ground_truth, desc=model_config.name)):
            text = gt.get("raw_input") or gt.get("description") or ""
            if len(text.strip()) < 10:
                records.append({"index": i, "success": False, "model_key": model_config.key, "error": "文本过短"})
                continue
            result = client.call(SYSTEM_PROMPT, build_user_message(text))
            if not result["success"]:
                records.append({"index": i, "success": False, "model_key": model_config.key, "error": result["error"], "latency": result["latency"]})
                continue
            records.append({
                "index": i, "success": True, "model_key": model_config.key,
                "latency": result["latency"], "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "total_tokens": result["total_tokens"], "cost_usd": result["cost_usd"],
                "parsed": self.extract_json(result["text"]),
            })
        return records

    def run_all(self) -> Dict:
        records = self.load_ground_truth()
        self.sample = self.stratified_sample(records)
        self.ground_truth = self.sample
        for m in self.models:
            r = self.run_model(m)
            self.results[m.key] = {"config": {"name": m.name, "key": m.key, "model_id": m.model_id, "provider": m.provider}, "records": r}
        return self.results

    def save(self, prefix: str = "experiment1") -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = {
            "experiment": "experiment1_multi_model", "timestamp": ts,
            "sample_size": len(self.sample), "models": {},
        }
        for mk, mr in self.results.items():
            out["models"][mk] = {
                "config": mr["config"],
                "summary": {
                    "total": len(mr["records"]),
                    "success": sum(1 for r in mr["records"] if r["success"]),
                    "total_cost": sum(r.get("cost_usd", 0) for r in mr["records"]),
                    "avg_latency": sum(r.get("latency", 0) for r in mr["records"] if r["success"]) / max(1, sum(1 for r in mr["records"] if r["success"])),
                },
                "parsed_records": [{"index": r["index"], "success": r["success"],
                    "error": r.get("error"), "latency": r.get("latency"),
                    "tokens": r.get("total_tokens"), "cost_usd": r.get("cost_usd"),
                    "extracted": r.get("parsed")} for r in mr["records"]],
            }
        out["ground_truth"] = [{"index": i, "id": g.get("id"), "title": g.get("title"),
            "accident_type": g.get("accident_type"), "severity": g.get("severity"),
            "deaths": g.get("deaths"), "injuries": g.get("injuries"),
            "location": g.get("location"), "city": g.get("city"),
            "human_factors": g.get("human_factors"), "machine_factors": g.get("machine_factors"),
            "management_factors": g.get("management_factors"), "environmental_factors": g.get("environmental_factors"),
            "project_type": g.get("project_type"), "direct_cause": g.get("direct_cause"),
            "construction_phase": g.get("construction_phase")} for i, g in enumerate(self.ground_truth)]
        p = os.path.join(EXPERIMENT_DIR, f"{prefix}_{ts}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存: {p}")
        return p


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE)
    args = parser.parse_args()
    r = Experiment1Runner(models=args.models, sample_size=args.sample_size)
    r.run_all()
    r.save()
