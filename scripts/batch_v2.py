"""V2 全量批量处理脚本（含图片抓取）。

用 V2Pipeline（CoT + Few-shot）处理全部事故数据。
每次成功提取后，自动搜索并下载相关现场图片。
保存到独立文件 data/accidents_v2.json，与 V1 不冲突。
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S", force=True)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from construction_agent.versions import get_pipeline
from construction_agent.database.dataset_manager import DatasetManager
from construction_agent.models.accident import Accident
from construction_agent.search.image_search import ImageSearcher
from construction_agent.search.search_integration import SearchIntegrator


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0, help="起始行")
    parser.add_argument("--count", type=int, default=None, help="处理条数")
    parser.add_argument("--end", type=int, default=None, help="结束行(不含)")
    parser.add_argument("--csv", default="原始数据/data_STH.csv")
    parser.add_argument("--resume", action="store_true", help="跳过已处理的")
    parser.add_argument("--no-search", action="store_true", help="跳过联网文本搜索")
    parser.add_argument("--no-images", action="store_true", help="跳过图片抓取")
    args = parser.parse_args()

    # ── V2 独立数据集 ──
    v2_db = DatasetManager(filepath=Path("data/accidents_v2.json"))

    # ── 图片搜索器 ──
    image_searcher = ImageSearcher() if not args.no_images else None

    # ── 读取 CSV ──
    csv_path = Path(args.csv)
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    start = args.start
    end = min(args.end, total) if args.end else (min(start + args.count, total) if args.count else total)
    batch = rows[start:end]
    logger.info(f"CSV 共 {total} 条，处理 {start}~{end-1}（共 {len(batch)} 条）")
    logger.info(f"图片抓取: {'开启' if image_searcher else '跳过'}")

    # ── 续传：跳过已存在标题 ──
    existing = set()
    if args.resume:
        for r in v2_db.get_all():
            t = (r.get("title") or "").strip()
            if t:
                existing.add(t)
        logger.info(f"V2 数据集中已有 {len(existing)} 条")

    # ── V2 Pipeline ──
    p = get_pipeline("v2")
    if not p:
        logger.error("V2 Pipeline 未注册")
        sys.exit(1)
    logger.info(f"V2 Pipeline: {p.get_info()['description']}")

    # ── 运行 ──
    success = 0
    skipped = 0
    failed = 0
    images_downloaded = 0
    start_time = time.time()

    for i, row in enumerate(batch):
        global_idx = start + i
        seq = row.get("序列", "").strip()
        text = (row.get("爬虫") or "").strip()
        title_csv = (row.get("事故名") or "").strip()

        if not text:
            skipped += 1
            continue
        if args.resume and title_csv in existing:
            skipped += 1
            continue

        logger.info(f"[{global_idx}/{total}] 第{seq}条 「{title_csv[:30]}」({len(text)}字)...")

        # ── 搜索集成器（文本+图片） ──
        integrator = SearchIntegrator() if (not args.no_search or not args.no_images) else None
        extract_text = text

        try:
            # ── 文本搜索（在提取之前，增强上下文） ──
            if not args.no_search and integrator:
                logger.info(f"  🔍 联网搜索相关信息...")
                sr, queries, snippets = integrator.search_text(title_csv or text)
                if sr:
                    extract_text = integrator.build_enhanced_text(text, sr)
                    logger.info(f"    找到 {len(sr)} 条补充信息")
                # 暂存搜索元数据
                search_data = {
                    "search_results": sr, "search_queries": queries,
                    "snippets_meta": snippets,
                    "image_results": [], "image_local_paths": [], "image_sources": [],
                }
            else:
                search_data = None

            t0 = time.time()
            result = p.process_single(extract_text)
            elapsed = time.time() - t0

            if not result.success or not result.accident:
                logger.warning(f"  ❌ 失败({elapsed:.0f}s): {result.error_message}")
                failed += 1
                continue

            accident = result.accident
            # 注入标题和来源
            accident.title = title_csv or accident.title
            url = row.get("来源网址", "").strip()
            if url and url not in accident.source_urls:
                accident.source_urls.append(url)

            # ── 图片抓取 ──
            if not args.no_images and integrator and accident.title:
                try:
                    logger.info(f"  📷 搜索事故现场图片...")
                    img_results, local_paths, img_sources = integrator.search_images(
                        accident.title, str(accident.id), max_images=2
                    )
                    images_downloaded += len(local_paths)
                    if search_data:
                        search_data["image_results"] = img_results
                        search_data["image_local_paths"] = local_paths
                        search_data["image_sources"] = img_sources
                    else:
                        search_data = {
                            "search_results": [], "search_queries": [],
                            "snippets_meta": [],
                            "image_results": img_results,
                            "image_local_paths": local_paths,
                            "image_sources": img_sources,
                        }
                except Exception as img_e:
                    logger.warning(f"    📷 图片抓取失败: {img_e}")

            # ── 整合搜索元数据 ──
            if search_data and integrator:
                integrator.integrate_into_accident(
                    accident=accident,
                    search_results=search_data["search_results"],
                    search_queries=search_data["search_queries"],
                    snippets_meta=search_data["snippets_meta"],
                    image_sources=search_data["image_sources"],
                    image_local_paths=search_data["image_local_paths"],
                )

            v2_db.add(accident)
            success += 1

            cause_count = (len(accident.human_factors) + len(accident.machine_factors) +
                           len(accident.management_factors) + len(accident.environmental_factors))
            img_info = f" 📷{len(accident.images)}" if accident.images else ""
            logger.info(f"  ✅ {accident.accident_type:8s} {accident.severity:6s} "
                        f"死亡{accident.deaths} 致因{cause_count}条{img_info} ({elapsed:.0f}s)")

        except Exception as e:
            logger.error(f"  ❌ 异常: {e}")
            failed += 1

        time.sleep(0.3)

    # ── 报告 ──
    elapsed_total = time.time() - start_time
    logger.info(f"\n{'='*50}")
    logger.info(f"V2 全量处理完成")
    logger.info(f"  范围: {start}~{end-1}")
    logger.info(f"  成功: {success} / 跳过: {skipped} / 失败: {failed}")
    logger.info(f"  图片: {images_downloaded} 张")
    logger.info(f"  耗时: {elapsed_total/60:.0f} 分")
    logger.info(f"  V2 数据集: {v2_db.stats()['total']} 条")


if __name__ == "__main__":
    main()
