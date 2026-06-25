"""V2.5 HFACS-CS 全量回填脚本。

对数据集中所有记录，使用 HfacsEncoder 进行 HFACS-CS 编码。
支持断点续传和增量保存。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

from construction_agent.versions.v2_5.encoder import HfacsEncoder

CHECKPOINT_FILE = Path("data/.hfacs_v25_checkpoint.json")
SAVE_INTERVAL = 25
DELAY = 0.3


def load_records(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_records(records: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def load_checkpoint() -> set[str]:
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_checkpoint(ids: set[str]):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(sorted(ids), f, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="V2.5 HFACS-CS 全量回填")
    parser.add_argument("--input", default="data/accidents_merged.json")
    parser.add_argument("--output", default="data/accidents_merged.json")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--method", choices=["llm", "keyword", "hybrid"], default="llm")
    parser.add_argument("--resume", action="store_true", help="断点续传")
    parser.add_argument("--force-keyword", action="store_true", help="使用关键词编码（零API成本）")
    parser.add_argument("--test", type=int, default=None, help="仅测试前 N 条")
    args = parser.parse_args()

    records = load_records(args.input)
    logger.info(f"加载数据集: {len(records)} 条")

    # 断点续传
    processed_ids = load_checkpoint() if args.resume else set()
    if processed_ids:
        logger.info(f"断点续传: {len(processed_ids)} 条已完成")

    method = "keyword" if args.force_keyword else args.method
    encoder = HfacsEncoder()

    end = args.test or (min(args.start + args.count, len(records)) if args.count else len(records))
    batch = records[args.start:end]
    logger.info(f"处理范围: {args.start}~{end-1} ({len(batch)}条), 方法={method}")

    stats = {"encoded": 0, "skipped": 0, "errors": 0, "codes_total": 0, "tokens": 0}

    for i, record in enumerate(batch):
        global_idx = args.start + i
        rid = record.get("id", f"idx_{global_idx}")
        title = (record.get("title") or "")[:50]

        if rid in processed_ids:
            stats["skipped"] += 1
            continue

        # 收集致因
        cause_dims = {}
        for dim in ["human_factors", "machine_factors", "management_factors", "environmental_factors"]:
            factors = record.get(dim) or []
            cause_dims[dim] = [f for f in factors if isinstance(f, dict) and f.get("category", "").strip()]

        total_causes = sum(len(v) for v in cause_dims.values())
        if total_causes == 0:
            record["hfacs_codes"] = []
            record["hfacs_details"] = []
            record["hfacs_encoding_version"] = f"v2.5_{method}_v1"
            processed_ids.add(rid)
            stats["skipped"] += 1
            continue

        t0 = time.time()
        try:
            codes, details = encoder.encode(
                title=record.get("title", ""),
                summary=record.get("summary", ""),
                human_factors=cause_dims["human_factors"],
                machine_factors=cause_dims["machine_factors"],
                management_factors=cause_dims["management_factors"],
                environmental_factors=cause_dims["environmental_factors"],
                method=method,
            )
            record["hfacs_codes"] = codes
            record["hfacs_details"] = [d.model_dump() for d in details]
            record["hfacs_encoding_version"] = f"v2.5_{method}_v1"
            processed_ids.add(rid)
            stats["encoded"] += 1
            stats["codes_total"] += len(codes)
            elapsed = time.time() - t0
            logger.info(
                f"[{global_idx}/{len(records)}] {title[:40]} | "
                f"{total_causes}致因→{len(codes)}编码 ({elapsed:.1f}s)"
            )
        except Exception as e:
            logger.error(f"[{global_idx}] {title} 编码异常: {e}")
            stats["errors"] += 1
            processed_ids.add(rid)  # 避免反复重试

        # 增量保存
        if (stats["encoded"] + stats["errors"]) % SAVE_INTERVAL == 0:
            save_records(records, args.output)
            save_checkpoint(processed_ids)
            _log_progress(stats, len(batch))

        time.sleep(DELAY)

    # 最终保存
    save_records(records, args.output)
    save_checkpoint(processed_ids)
    _log_progress(stats, len(batch), final=True)


def _log_progress(stats: dict, total: int, final: bool = False):
    tag = "✅ 完成" if final else "💾 保存"
    logger.info(
        f"{tag}: 编码{stats['encoded']} 跳过{stats['skipped']} "
        f"错误{stats['errors']}/{total} | "
        f"共{stats['codes_total']}个编码"
    )


if __name__ == "__main__":
    main()
