"""回填现有数据集的搜索元数据。

对 accidents.json (V1) 和 accidents_v2.json (V2) 中的每条记录，
用 SearchIntegrator 补充：
  - search_snippets: 搜索结果摘要
  - search_queries: 搜索查询
  - image_urls: 图片来源信息
  - 多来源 URL 合并

用法:
  # 回填 V1 数据集前 500 条（默认）
  python scripts/backfill_search.py

  # 回填 V2 数据集
  python scripts/backfill_search.py --dataset data/accidents_v2.json

  # 仅处理来源URL不足2条的记录
  python scripts/backfill_search.py --min-sources 2

  # 指定范围续传
  python scripts/backfill_search.py --start 100 --count 50
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

from construction_agent.search.search_integration import SearchIntegrator


def backfill(
    dataset_path: str = "data/accidents.json",
    start: int = 0,
    count: int | None = None,
    min_sources: int = 1,
    skip_existing: bool = True,
    delay: float = 0.5,
):
    """回填搜索元数据到数据集。

    Args:
        dataset_path: 数据集 JSON 文件路径
        start: 起始索引
        count: 处理条数（None = 到末尾）
        min_sources: 只有当 source_urls < min_sources 时才处理
        skip_existing: 跳过已有 search_snippets 的记录
        delay: 每次搜索间隔（秒）
    """
    path = Path(dataset_path)
    if not path.exists():
        logger.error(f"数据集不存在: {path}")
        return

    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    total = len(records)
    end = min(start + count, total) if count else total
    batch = records[start:end]

    logger.info(f"{'='*50}")
    logger.info(f"回填搜索元数据: {path.name}")
    logger.info(f"  总记录: {total} | 处理范围: {start}~{end-1} ({len(batch)}条)")
    logger.info(f"  min_sources: {min_sources} | skip_existing: {skip_existing}")

    integrator = SearchIntegrator()
    updated = 0
    skipped = 0
    failed = 0
    total_snippets = 0
    total_images = 0
    start_time = time.time()

    SAVE_INTERVAL = 10  # 每处理 N 条保存一次

    for i, record in enumerate(batch):
        global_idx = start + i
        title = record.get("title", "") or ""
        if not title:
            skipped += 1
            continue

        # 跳过条件
        if skip_existing and record.get("search_snippets"):
            skipped += 1
            continue
        if min_sources and len(record.get("source_urls", []) or []) >= min_sources:
            skipped += 1
            continue

        logger.info(f"[{global_idx}/{total}] {title[:40]}...")

        try:
            # 文本搜索
            keyword = title
            search_results, queries, snippets = integrator.search_text(keyword, max_results=5)

            # 图片搜索
            accident_id = record.get("id", "") or str(hash(title))[:8]
            img_results, local_paths, img_sources = integrator.search_images(
                keyword, accident_id, max_images=2
            )

            # 合并 source_urls
            existing_urls = set(record.get("source_urls", []) or [])
            for r in search_results:
                if r.url and r.url not in existing_urls:
                    record.setdefault("source_urls", []).append(r.url)
                    existing_urls.add(r.url)

            # 注入新字段
            record["search_queries"] = queries

            existing_snippets = record.get("search_snippets", []) or []
            existing_urls_in_snippets = {s.get("url", "") for s in existing_snippets if isinstance(s, dict)}
            for s in snippets:
                if s.get("url", "") not in existing_urls_in_snippets:
                    existing_snippets.append(s)
                    existing_urls_in_snippets.add(s.get("url", ""))
            record["search_snippets"] = existing_snippets

            existing_image_urls = record.get("image_urls", []) or []
            existing_thumbs = {u.get("thumb_url", "") for u in existing_image_urls if isinstance(u, dict)}
            for u in img_sources:
                if u.get("thumb_url", "") not in existing_thumbs:
                    existing_image_urls.append(u)
                    existing_thumbs.add(u.get("thumb_url", ""))
            record["image_urls"] = existing_image_urls

            # 合并图片路径
            existing_images = set(record.get("images", []) or [])
            for p in local_paths:
                if p not in existing_images:
                    record.setdefault("images", []).append(p)
                    existing_images.add(p)

            updated += 1
            total_snippets += len(snippets)
            total_images += len(local_paths)

            logger.info(f"  ✅ +{len(snippets)} snippets | +{len(local_paths)} images | "
                        f"现在 {len(record['source_urls'])} URLs")

            # 增量保存（每 SAVE_INTERVAL 条写一次磁盘）
            if updated % SAVE_INTERVAL == 0:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(records, f, indent=2, ensure_ascii=False)
                logger.info(f"  💾 增量保存完成 (已处理{updated}条)")

        except Exception as e:
            logger.error(f"  ❌ {type(e).__name__}: {e}")
            failed += 1

        time.sleep(delay)

    # 保存
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    logger.info(f"{'='*50}")
    logger.info(f"✅ 回填完成")
    logger.info(f"  处理: {updated}条 | 跳过: {skipped}条 | 失败: {failed}条")
    logger.info(f"  新增 snippets: {total_snippets}条 | 新增图片: {total_images}张")
    logger.info(f"  耗时: {elapsed/60:.1f}分")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="回填数据集搜索元数据")
    parser.add_argument("--dataset", default="data/accidents.json", help="数据集路径")
    parser.add_argument("--start", type=int, default=0, help="起始索引")
    parser.add_argument("--count", type=int, default=None, help="处理条数")
    parser.add_argument("--min-sources", type=int, default=1,
                        help="仅处理 source_urls 少于该值的记录 (默认1, 设0则全部处理)")
    parser.add_argument("--no-skip", action="store_true",
                        help="不跳过已有 search_snippets 的记录")
    parser.add_argument("--delay", type=float, default=0.5, help="搜索间隔秒数")
    args = parser.parse_args()

    backfill(
        dataset_path=args.dataset,
        start=args.start,
        count=args.count,
        min_sources=args.min_sources,
        skip_existing=not args.no_skip,
        delay=args.delay,
    )
