#!/usr/bin/env python3
"""图片补采：为缺图事故重跑图片采集管线。

━━ 背景 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
主库 2,623 条事故中，只有 476 条（18.1%）在 image_files 里有图片登记，
剩下的详情页显示「无现场图片」。其中最容易补齐的一批是：
    已经做过搜索增强（source_urls 有记录）、但图片为 0 的事故。

以齐齐哈尔体育馆坍塌事故（ae16d12d2c7f）为例：它有 23 条源 URL
（新华网图片频道 / 澎湃 / 央视 / 央广网 / 网易 …），2026-07-23 做过
搜索增强，但 data/images/ae16d12d2c7f/ 目录从未建立 —— 图片采集
这一步没有真正执行过。实测这 23 个页面里 21 个可达，能提取 163 张
图片候选，其中确含新华社的救援现场航拍。

━━ 管线（复用现有组件，不新增依赖）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    source_urls → 抓页面 → <img> 提取 → 规则过滤（关键词/尺寸/体积）
      → 下载 → CLIP 语义过滤（剔除 logo/二维码/图标/宣传图）
      → data/images/{accident_id}/ → 登记 image_files 表

抓取与下载复用 construction_agent.extraction.image_collector.ImageCollector，
语义过滤复用同一模块的 clip_filter。本脚本只负责「选目标 + 编排 + 落库」。

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 列出候选（不下载、不写库）
    python3 scripts/backfill_images.py --list

    # 试运行：抓取与过滤都跑，但不落盘、不写库
    python3 scripts/backfill_images.py --accident-id ae16d12d2c7f --dry-run

    # 补单个事故（在 worktree 里跑，落 worktree 的隔离副本）
    python3 scripts/backfill_images.py --accident-id ae16d12d2c7f

    # 补主仓库（--data-dir 同时决定图片落点与默认主库）
    python3 scripts/backfill_images.py --accident-id ae16d12d2c7f \
        --data-dir /home/suntianhao/construction_agent/data

    # 补全部「有源 URL 但无图」的事故（按死亡人数从高到低）
    python3 scripts/backfill_images.py --all-missing --limit 34 \
        --data-dir /home/suntianhao/construction_agent/data

━━ 注意 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
补采只往 data/images/{accident_id}/ 新增文件、往 image_files 新增行，
不删除、不改写既有数据，可重复执行（同名文件按 URL 哈希去重）。
展示层读的是工作库的 image_manifest 表，补采后需重建清单：
    python3 scripts/build_image_manifest.py
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from construction_agent.config import settings  # noqa: E402

# 抓取与过滤复用主管线组件；IMAGE_DIR 是模块级常量，改它即可改落点
import construction_agent.extraction.image_collector as ic_mod  # noqa: E402
from construction_agent.extraction.image_collector import ImageCollector  # noqa: E402


def resolve_db(explicit: str | None) -> Path:
    """定位主库。默认走 settings.DATA_DIR（在 worktree 里即 worktree 的副本）。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(settings.DATA_DIR) / "caiems_v2.db"


def select_targets(con: sqlite3.Connection, args) -> list[tuple[str, str, int, int]]:
    """挑出待补采的事故，返回 (id, title, deaths, n_urls)，按死亡人数降序。

    选目标的依据是可执行性，而不是「图片少」：
    没有 source_urls 的事故无从抓取，不在本脚本射程内。
    """
    if args.accident_id:
        rows = []
        for aid in args.accident_id:
            r = con.execute(
                "SELECT a.id, a.title, COALESCE(a.deaths, 0), "
                "       (SELECT COUNT(*) FROM source_urls s WHERE s.accident_id = a.id) "
                "FROM accidents a WHERE a.id = ?", (aid,)).fetchone()
            if r is None:
                print(f"  ✗ {aid} 不在 accidents 表里")
                continue
            rows.append(r)
        return rows

    # --all-missing：有源 URL、但 image_files 里没有任何图片登记
    sql = """
        SELECT a.id, a.title, COALESCE(a.deaths, 0),
               (SELECT COUNT(*) FROM source_urls s WHERE s.accident_id = a.id) AS n_urls
        FROM accidents a
        WHERE a.id IN (SELECT DISTINCT accident_id FROM source_urls)
          AND a.id NOT IN (SELECT DISTINCT accident_id FROM image_files
                           WHERE accident_id IS NOT NULL)
        ORDER BY deaths DESC, n_urls DESC
    """
    rows = con.execute(sql).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    return rows


def register_images(db: Path, aid: str, records: list[dict]) -> int:
    """把新采到的图片登记进 image_files。

    按 (accident_id, local_path) 去重，避免重复执行时插重复行。
    """
    if not records:
        return 0
    con = sqlite3.connect(str(db))
    try:
        con.execute("PRAGMA foreign_keys = ON")
        existing = {r[0] for r in con.execute(
            "SELECT local_path FROM image_files WHERE accident_id = ?", (aid,))}
        now = datetime.now().isoformat(timespec="seconds")
        new = [r for r in records if r["local_path"] not in existing]
        con.executemany(
            "INSERT INTO image_files "
            "(accident_id, local_path, source_url, source_page_url, "
            " file_size, width, height, mime_type, download_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?)",
            [(aid, r["local_path"], r["source_url"], r["source_page_url"],
              r["file_size"], r["width"], r["height"], r["mime_type"], now)
             for r in new])
        con.commit()
        return len(new)
    finally:
        con.close()


def probe_size(path: Path) -> tuple[int | None, int | None, str | None]:
    """读宽高与 MIME。读失败时返回 None，不让它中断补采。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            mime = Image.MIME.get(im.format or "", None)
        return w, h, mime
    except Exception:
        return None, None, None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="为缺图事故重跑图片采集管线",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accident-id", action="append", default=[],
                    help="指定事故 id，可重复；与 --all-missing 二选一")
    ap.add_argument("--all-missing", action="store_true",
                    help="全部「有源 URL 但无图」的事故，按死亡人数降序")
    ap.add_argument("--limit", type=int, default=None,
                    help="配合 --all-missing，最多处理前 N 条")
    ap.add_argument("--list", action="store_true",
                    help="只列出候选，不抓取")
    ap.add_argument("--max-images", type=int, default=8,
                    help="每个事故最多保留几张（默认 8）")
    ap.add_argument("--max-pages", type=int, default=10,
                    help="每个事故最多访问几个页面（默认 10）")
    ap.add_argument("--dry-run", action="store_true",
                    help="抓取与过滤照跑，但不落盘、不写库")
    ap.add_argument("--no-clip", action="store_true",
                    help="跳过 CLIP 语义过滤（只做规则过滤）")
    ap.add_argument("--db", default=None, help="主库路径（默认 settings.DATA_DIR/caiems_v2.db）")
    ap.add_argument("--data-dir", default=None,
                    help="数据根目录，决定图片落点与默认主库位置"
                         "（默认 settings.DATA_DIR；在 worktree 里跑时它会指向"
                         " worktree 自己的残缺副本，要写主仓库需显式指定）")
    args = ap.parse_args()

    if not args.accident_id and not args.all_missing:
        ap.error("需要 --accident-id 或 --all-missing")

    data_dir = (Path(args.data_dir).expanduser().resolve()
                if args.data_dir else Path(settings.DATA_DIR))
    db = resolve_db(args.db) if args.db else data_dir / "caiems_v2.db"
    if not db.is_file():
        print(f"✗ 主库不存在: {db}")
        return 2

    images_dir = data_dir / "images"
    reject_dir = images_dir / "_rejected"

    # 把 ImageCollector 的落点从默认的 cwd 相对路径改到 DATA_DIR 下
    ic_mod.IMAGE_DIR = str(images_dir)
    ic_mod.REJECT_DIR = str(reject_dir)

    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    targets = select_targets(con, args)
    con.close()

    if not targets:
        print("没有待补采的事故。")
        return 0

    print("=" * 74)
    print(f"主库 {db}")
    print(f"图片落点 {images_dir}")
    print(f"待补采 {len(targets)} 个事故" + ("（试运行）" if args.dry_run else ""))
    print("=" * 74)
    for aid, title, deaths, n_urls in targets:
        print(f"  {aid}  死{deaths:>3}  url{n_urls:>3}  {str(title)[:44]}")
    print()

    if args.list:
        return 0

    # 关掉 VLM 分析：补采只关心「有没有图」，逐张 VLM 又慢又要 API 额度
    if args.no_clip:
        os.environ["CLIP_FILTER_ENABLED"] = "0"
    collector = ImageCollector()
    collector.analyzer._enabled = False
    print(f"CLIP 语义过滤: {'关闭' if args.no_clip else '开启'}\n")

    total_new_files, total_new_rows = 0, 0
    summary = []

    for aid, title, deaths, n_urls in targets:
        urls = []
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        urls = [r[0] for r in con.execute(
            "SELECT url FROM source_urls WHERE accident_id = ? ORDER BY id", (aid,))]
        con.close()

        if not urls:
            print(f"── {aid} 无源 URL，跳过")
            continue

        print(f"── {aid}  {str(title)[:40]}  （{len(urls)} 个源页面）")
        try:
            collected = collector.collect_for_accident(
                accident_id=aid, urls=urls, accident_context=None,
                max_images=args.max_images, max_pages=args.max_pages)
        except Exception as e:
            print(f"   ✗ 采集异常: {type(e).__name__}: {e}")
            summary.append((aid, 0, 0, f"异常 {type(e).__name__}"))
            continue

        if not collected:
            print("   → 未取得任何图片")
            summary.append((aid, 0, 0, "无产出"))
            continue

        records = []
        aid_dir = images_dir / aid
        for img in collected:
            p = Path(img.local_path)
            w, h, mime = probe_size(p)
            records.append({
                "local_path": f"images/{aid}/{p.name}",
                "source_url": img.url,
                "source_page_url": img.page_url,
                "file_size": img.file_size,
                "width": w, "height": h, "mime_type": mime,
            })
            print(f"   ✓ {p.name:<46} {img.file_size:>9,}B  {w}x{h}")

        if args.dry_run:
            # 试运行：把刚写下的文件删掉，保持磁盘干净
            for img in collected:
                try:
                    Path(img.local_path).unlink()
                except OSError:
                    pass
            if aid_dir.is_dir() and not any(aid_dir.iterdir()):
                aid_dir.rmdir()
            print(f"   → 试运行，{len(records)} 张已回滚，未写库")
            summary.append((aid, len(records), 0, "试运行"))
            continue

        n_rows = register_images(db, aid, records)
        total_new_files += len(collected)
        total_new_rows += n_rows
        print(f"   → 落盘 {len(collected)} 张，登记 {n_rows} 行")
        summary.append((aid, len(collected), n_rows, "已补采"))

    print()
    print("=" * 74)
    print(f"合计：落盘 {total_new_files} 张，登记 {total_new_rows} 行")
    print("=" * 74)
    for aid, n_file, n_row, note in summary:
        print(f"  {aid}  文件{n_file:>3}  登记{n_row:>3}  {note}")

    if not args.dry_run and total_new_files:
        print()
        print("下一步：重建展示层清单，图片才会出现在网页上")
        print("    python3 scripts/build_image_manifest.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
