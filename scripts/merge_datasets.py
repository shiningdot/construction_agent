"""合并 V1 + V2 数据集为统一的 Web UI 展示数据集。

合并策略：
1. 按标题（归一化后）匹配记录
2. 重合记录以 V2 为基础（准确率更高），合并 V1 的补充信息
3. 非重合记录全部保留
4. 输出到 data/accidents_merged.json
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def norm_title(s: str) -> str:
    """归一化标题用于匹配。"""
    return re.sub(r'\s+', '', (s or "")).lower()


def merge_fields(base: dict, supplement: dict, fields: list[str]):
    """合并字段：从 supplement 补充 base 中缺失的值。"""
    for f in fields:
        bv = base.get(f)
        sv = supplement.get(f)
        if (not bv or bv in ("", [], 0, "0", "无法判断")) and sv:
            base[f] = sv


def merge_list_fields(base: dict, supplement: dict, fields: list[str]):
    """合并列表字段：去重合并。"""
    for f in fields:
        bv = base.get(f) or []
        sv = supplement.get(f) or []
        if isinstance(bv, list) and isinstance(sv, list):
            existing = set()
            for item in bv:
                if isinstance(item, dict):
                    existing.add(json.dumps(item, sort_keys=True, ensure_ascii=False))
                else:
                    existing.add(str(item))
            for item in sv:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, dict) else str(item)
                if key not in existing:
                    bv.append(item)
                    existing.add(key)
            base[f] = bv


def merge_datasets(
    v1_path: str = "data/accidents.json",
    v2_path: str = "data/accidents_v2.json",
    output_path: str = "data/accidents_merged.json",
    prefer_v2: bool = True,
):
    """合并 V1 和 V2 数据集。

    Args:
        v1_path: V1 数据集路径
        v2_path: V2 数据集路径
        output_path: 输出路径
        prefer_v2: 重合记录是否优先用 V2 版本
    """
    # 加载
    with open(v1_path, "r", encoding="utf-8") as f:
        v1_records = json.load(f)
    with open(v2_path, "r", encoding="utf-8") as f:
        v2_records = json.load(f)

    logger.info(f"V1: {len(v1_records)} 条 | V2: {len(v2_records)} 条")

    # 构建索引
    v2_by_title = {}
    for r in v2_records:
        t = norm_title(r.get("title", ""))
        if t:
            v2_by_title[t] = r

    v1_by_title = {}
    for r in v1_records:
        t = norm_title(r.get("title", ""))
        if t:
            v1_by_title[t] = r

    merged = []
    processed_v2_titles = set()

    # 需要合并的字段
    OVERWRITE_FIELDS = [
        "accident_type", "severity", "deaths", "injuries",
        "location", "city", "weather", "direct_cause", "contractor",
        "project_type", "construction_phase",
    ]
    LIST_MERGE_FIELDS = [
        "source_urls", "images", "search_snippets",
        "search_queries", "image_urls",
    ]

    # 以 V1 为基础遍历
    for v1_rec in v1_records:
        t = norm_title(v1_rec.get("title", ""))
        if not t:
            merged.append(v1_rec)
            continue

        if t in v2_by_title:
            # 重合记录
            v2_rec = v2_by_title[t]
            processed_v2_titles.add(t)

            if prefer_v2:
                base = dict(v2_rec)  # V2 为基础
                supplement = v1_rec
            else:
                base = dict(v1_rec)
                supplement = v2_rec

            # 合并字段
            merge_fields(base, supplement, OVERWRITE_FIELDS)
            merge_list_fields(base, supplement, LIST_MERGE_FIELDS)

            # 添加标记
            base["_source"] = "v1+v2"
            merged.append(base)
        else:
            # V1 独有
            v1_rec["_source"] = "v1"
            merged.append(v1_rec)

    # 添加 V2 独有的记录
    for v2_rec in v2_records:
        t = norm_title(v2_rec.get("title", ""))
        if t and t not in processed_v2_titles:
            v2_rec["_source"] = "v2"
            merged.append(v2_rec)

    # 写文件
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    v1_only = sum(1 for r in merged if r.get("_source") == "v1")
    v2_only = sum(1 for r in merged if r.get("_source") == "v2")
    both = sum(1 for r in merged if r.get("_source") == "v1+v2")
    with_snippets = sum(1 for r in merged if r.get("search_snippets") and len(r["search_snippets"]) > 0)
    with_images = sum(1 for r in merged if r.get("images") and len(r["images"]) > 0)
    with_multi_urls = sum(1 for r in merged if len(r.get("source_urls",[]) or []) > 1)

    logger.info(f"\n{'='*50}")
    logger.info(f"✅ 合并完成 → {output_path}")
    logger.info(f"  总记录: {len(merged)} 条")
    logger.info(f"  仅 V1: {v1_only} | 仅 V2: {v2_only} | 重合(合并): {both}")
    logger.info(f"  含搜索结果摘要: {with_snippets} 条")
    logger.info(f"  含图片: {with_images} 条")
    logger.info(f"  多来源URL: {with_multi_urls} 条")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/accidents_merged.json")
    args = parser.parse_args()
    merge_datasets(output_path=args.output)
