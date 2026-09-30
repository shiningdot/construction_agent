#!/usr/bin/env python3
"""六个页面的截图 + 前端渲染探针。

数值对账（条数、分布、与 SQL 相等）查不出「界面错乱 / 图表空白 / 图片裂开」
这一类问题——SQL 全对而页面是坏的，在对账里完全不可见。这个脚本补上这一维：
起真实服务、用无头 Chromium 逐页打开，然后报三件事。

- **破图**：`<img>` 加载完却 `naturalWidth == 0`
- **空白**：可见文字少于 200 字（整页没渲染出来）
- **控制台错误**：JS 抛异常，或资源 404

任何一项非空即退出码 1，可直接接进 CI。

数据接真货：最终库取**主仓库**的 `caiems_v2.db`（2,623 条），工作库与图片根也
默认指主仓库——在主仓库跑不用管，在 worktree 跑会自动指回主仓库（见
`_common.py`）。详情页的样本是从 `image_manifest` 里挑「图最多、且有 CAICS 编码」
的真实事故，字段齐、编码齐、图多，覆盖面最大。

用法：
    .venv/bin/python3 scripts/screenshot_web.py
    .venv/bin/python3 scripts/screenshot_web.py --out /tmp/shots
    .venv/bin/python3 scripts/screenshot_web.py --staging-db data/caiems_staging.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402
from _serve import point_at, serve                            # noqa: E402


def pick_sample(main_db: str, staging_db: Path) -> tuple[str | None, str]:
    """挑一个既有图、又有 CAICS 编码的真实事故。

    挑不出来时**要说清卡在哪一步**。三种情况的处理办法完全不同：工作库不存在
    要跑 `create_staging_db.py`，清单是旧口径要跑 `build_image_manifest.py`，
    都有图有编码但都没有编码则是数据本身如此。2026-10-01 实测就撞上第二种
    ——主仓库的工作库 `image_manifest` 停在旧口径，226 行的 `accident_id` 在
    最终库里一个都对不上，若只回一句「没找到」会被读成「没数据」。
    """
    if not staging_db.exists():
        return None, "（工作库不存在，详情页跳过）"
    con_s = sqlite3.connect(f"file:{staging_db}?mode=ro", uri=True)
    try:
        with_img = [r[0] for r in con_s.execute(
            "SELECT accident_id FROM image_manifest GROUP BY accident_id "
            "ORDER BY COUNT(*) DESC")]
    except sqlite3.Error as e:
        # 分两种：库是空壳（一张表都没有，跑 create_staging_db.py）vs 只是缺这张表
        n_tab = con_s.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if not n_tab:
            return None, ("（工作库是个空文件，一张表都没有——跑 "
                          "scripts/create_staging_db.py 建表，详情页跳过）")
        return None, f"（工作库缺 image_manifest 表：{e}，详情页跳过）"
    finally:
        con_s.close()

    if not with_img:
        return None, "（工作库 image_manifest 是空表，详情页跳过）"

    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        n_in_main = 0
        for aid in with_img:
            row = con.execute("SELECT title, severity FROM accidents WHERE id = ?",
                              (aid,)).fetchone()
            if not row:
                continue
            n_in_main += 1
            n = con.execute("SELECT COUNT(*) FROM caics_causes WHERE accident_id = ?",
                            (aid,)).fetchone()[0]
            if n:
                return aid, f"{row['title'][:28]}… / {row['severity']} / {n} 条编码"
    finally:
        con.close()

    if not n_in_main:
        return None, (f"（清单里 {len(with_img)} 个事故在最终库一个都对不上——清单是"
                      f"旧口径，跑 scripts/build_image_manifest.py 重建，详情页跳过）")
    return None, f"（{n_in_main} 个有图事故在最终库存在，但都还没 CAICS 编码，详情页跳过）"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "paper" / "figures"),
                    help="截图输出目录")
    ap.add_argument("--port", type=int, default=5099)
    ap.add_argument("--main-db", default=None, help="最终库（默认取主仓库的）")
    ap.add_argument("--staging-db", default=None,
                    help="工作库（默认取主仓库的 data/caiems_staging.db）")
    args = ap.parse_args()

    main_repo = main_repo_root()
    main_db = args.main_db or main_db_path()
    staging_db = (Path(args.staging_db) if args.staging_db
                  else main_repo / "data" / "caiems_staging.db")

    point_at(main_db, staging_db)

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    accident_id, info = pick_sample(main_db, staging_db)
    print(f"最终库   : {main_db}")
    print(f"工作库   : {staging_db}{'' if staging_db.exists() else '  ← 不存在'}")
    print(f"图片根   : {main_repo / 'data'}")
    print(f"样本事故 : {accident_id or '—'}  {info}\n")

    server, base = serve(args.port)

    pages = [
        ("/", "01_概览"),
        ("/collect", "02_采集"),
        ("/extract", "03_提取"),
        ("/review", "04_审核"),
        ("/causes", "05_致因分析"),
    ]
    if accident_id:
        pages.append((f"/accident/{accident_id}", "06_详情"))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright：.venv/bin/pip install playwright && "
              ".venv/bin/playwright install chromium", file=sys.stderr)
        return 2

    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        console_errors: list[str] = []
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        for route, name in pages:
            console_errors.clear()
            try:
                page.goto(base + route, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1800)       # 等图表与图片渲染
            except Exception as e:                # noqa: BLE001
                problems.append(f"{name} 打不开：{str(e)[:70]}")
                print(f"  ✗ {name:<12} 打不开")
                continue

            page.screenshot(path=str(out / f"{name}.png"), full_page=True)

            broken = page.evaluate(
                "() => Array.from(document.images)"
                ".filter(i => i.complete && i.naturalWidth === 0)"
                ".map(i => i.currentSrc || i.src)")
            text_len = page.evaluate("() => document.body.innerText.trim().length")
            n_img = page.evaluate("() => document.images.length")
            n_canvas = page.evaluate("() => document.querySelectorAll('canvas').length")
            n_svg = page.evaluate("() => document.querySelectorAll('svg').length")

            marks = []
            if broken:
                marks.append(f"破图 {len(broken)}")
                problems.append(f"{name} 有 {len(broken)} 张图裂开：{broken[:2]}")
            if text_len < 200:
                marks.append(f"文字仅 {text_len} 字")
                problems.append(f"{name} 页面几乎空白（可见文字 {text_len} 字）")
            if console_errors:
                marks.append(f"控制台错误 {len(console_errors)}")
                problems.append(f"{name} 控制台报错：{console_errors[0][:70]}")

            print(f"  {'⚠' if marks else '✓'} {name:<12} 文字 {text_len:>5} 字  "
                  f"img {n_img:>2}  canvas {n_canvas}  svg {n_svg}  "
                  f"{'  ⚠ ' + '、'.join(marks) if marks else ''}")

        browser.close()

    server.shutdown()
    print(f"\n截图 {len(pages)} 张 → {out}")
    if problems:
        print(f"\n✗ {len(problems)} 处问题：")
        for x in problems:
            print(f"    - {x}")
        return 1
    print("\n✓ 各页无破图、无空白、无控制台报错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
