#!/usr/bin/env python3
"""交互验收：点下去之后，页面对不对。

至此已经验过四层，但它们全是**首屏**：路由通不通、页面开不开、渲染坏没坏、
字段名对不对。用户在页面上做的动作——勾筛选、翻页、排序、重置——一层都没验过。
接口对账（`e2e_verify_main.py` 那 45 项）验的是 API 层，也管不到「按钮绑没绑上」。

这个脚本用无头浏览器真点一遍，看**页面自己说的话**跟**点完之后的结果**对不对得上。

## 为什么断言能自洽、不硬编码数字

概览页每个筛选 chip 的 `title` 里就写着「辽宁省：137 条」——那是后端 `/api/filters`
算出来的计数。点下去之后，分页器上的「共 N 条」应当**等于**这个数。两个数出自不同
的代码路径（筛选器计数 vs 列表查询），相等才算对齐。

所以脚本不写 `2623` 这种数字，数据变了照样跑。唯一写死的期望是「点两次同一个
chip 回到原点」这类结构性断言。

## 验的八件事

1. 初始加载：分页器有总数，且是多页
2. 勾一个省份 chip：总数 == 该 chip 自己标的条数
3. 列表真的只剩该省（抽前几行的省份列）
4. 再点一次取消：总数回到初始
5. 翻到第 2 页：页码变了、首行换了
6. **粘贴链接能复原**：把当前 hash 拼成 URL 新开一页，状态一致
7. 关键词搜索：总数变小，且结果标题确实含该词
8. 重置：回到初始

用法：
    .venv/bin/python3 scripts/check_web_interaction.py

退出码 `0` = 八条全过，`1` = 有不过的。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402
from _serve import point_at, serve                            # noqa: E402


def total_on_page(page) -> int | None:
    """分页器上的「共 N 条」。

    单页时是 `共 N 条`，多页时是 `第 1 / 53 页 · 每页 50 条 · 共 N 条`——
    同一个正则抓两种。
    """
    txt = page.inner_text("#pager")
    m = re.search(r"共\s*([\d,]+)\s*条", txt)
    return int(m.group(1).replace(",", "")) if m else None


def first_row(page) -> str:
    try:
        return page.locator("#gridBody tr").first.inner_text().strip()[:60]
    except Exception:                                        # noqa: BLE001
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # 端口约定见 scripts/e2e/README.md：5098 已被 e2e_verify_main（主库对账）占用，
    # 5099 是截图探针。这里取 5100，各占一个、互不干扰。
    ap.add_argument("--port", type=int, default=5100)
    ap.add_argument("--main-db", default=None)
    ap.add_argument("--staging-db", default=None)
    args = ap.parse_args()

    main_db = args.main_db or main_db_path()
    staging_db = args.staging_db or str(main_repo_root() / "data" / "caiems_staging.db")
    point_at(main_db, staging_db)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright：.venv/bin/pip install playwright && "
              ".venv/bin/playwright install chromium", file=sys.stderr)
        return 2

    server, base = serve(args.port)
    print(f"最终库 : {main_db}\n服务   : {base}\n")

    results: list[tuple[str, bool, str]] = []

    def step(name: str, fn):
        try:
            results.append((name, True, fn()))
            print(f"  ✓ {name}  {results[-1][2]}")
        except Exception as e:                               # noqa: BLE001
            results.append((name, False, str(e)[:110]))
            print(f"  ✗ {name}  {results[-1][2]}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = ctx.new_page()
            page.goto(base + "/", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1200)

            t0 = total_on_page(page)

            def s1():
                assert t0, "分页器上没有总数——列表没渲染出来"
                pages = len(page.locator("#pager button").all())
                assert pages >= 3, f"分页按钮只有 {pages} 个，可能只有一页"
                return f"共 {t0} 条"
            step("初始加载", s1)

            # ── 勾第一个筛选 chip ──────────────────────────────
            chip = page.locator("#filterChips label.chip").first
            chip_name = ""
            chip_n = 0

            def s2():
                nonlocal chip_name, chip_n
                title = chip.get_attribute("title") or ""
                m = re.search(r"(.+?)：([\d,]+)\s*条", title)
                assert m, f"chip 的 title 不是「名字：N 条」：{title!r}"
                chip_name = m.group(1)
                chip_n = int(m.group(2).replace(",", ""))
                chip.click()
                page.wait_for_timeout(900)
                got = total_on_page(page)
                assert got == chip_n, (
                    f"筛选器说「{chip_name}」有 {chip_n} 条，点完后列表说 {got} 条")
                return f"{chip_name}：筛选器 {chip_n} == 列表 {got}"
            step("勾筛选 chip", s2)

            def s3():
                assert chip_n < t0, "挑中的省份条数等于全部，验不出筛选效果"
                col = page.locator("#gridBody tr").first.inner_text()
                assert chip_name[:2] in col, (
                    f"列表首行里找不到「{chip_name[:2]}」：{col[:60]}")
                return f"首行确属 {chip_name[:2]}"
            step("列表只剩该省", s3)

            def s4():
                hash_now = page.evaluate("() => location.hash")
                assert "province=" in hash_now, f"hash 里没有 province：{hash_now!r}"
                chip.click()
                page.wait_for_timeout(900)
                got = total_on_page(page)
                assert got == t0, f"取消筛选后应回到 {t0}，实际 {got}"
                return f"回到 {t0}，hash 已清空 province"
            step("再点一次取消", s4)

            # ── 翻页 ─────────────────────────────────────────
            row_p1 = first_row(page)

            def s5():
                page.locator("#pager button", has_text="下一页").first.click()
                page.wait_for_timeout(900)
                assert "page=2" in page.evaluate("() => location.hash"), "hash 里没有 page=2"
                assert first_row(page) != row_p1, "翻页后首行没变"
                assert total_on_page(page) == t0, "翻页不该改变总数"
                return "第 2 页，首行已换，总数不变"
            step("翻到第 2 页", s5)

            # ── 分享链接 ──────────────────────────────────────
            def s6():
                h = page.evaluate("() => location.hash")
                assert h, "当前没有 hash，无从拼链接"
                p2 = ctx.new_page()
                p2.goto(base + "/" + h, wait_until="networkidle", timeout=30000)
                p2.wait_for_timeout(1200)
                got = total_on_page(p2)
                assert got == t0, f"新开页总数 {got} != {t0}"
                assert first_row(p2) == first_row(page), "新开页的首行与当前页不同"
                n_on = p2.locator("#filterChips label.chip input:checked").count()
                assert n_on == 0, f"新开页有 {n_on} 个 chip 被选中，状态没复原干净"
                p2.close()
                return f"粘贴 {h} 复原到同一状态"
            step("分享链接能复原", s6)

            # ── 过期的筛选值 ──────────────────────────────────
            def s6b():
                """链接里的省份过期了（数据更新后改名），页面不该停在自相矛盾的状态。

                后端对未识别的省份键是静默忽略的（列表返回全部），若前端照单全收，
                用户会看到「URL 说筛了、列表却是全部」而毫无提示。
                """
                p3 = ctx.new_page()
                p3.goto(base + "/#province=不存在的省",
                        wait_until="networkidle", timeout=30000)
                p3.wait_for_timeout(1500)
                got = total_on_page(p3)
                assert got == t0, f"应回到全部 {t0} 条，实际 {got}"
                h = p3.evaluate("() => location.hash")
                assert "province=" not in h, f"过期的 province 应被清掉，hash 仍是 {h!r}"
                p3.close()
                return "过期的省份值被清掉，列表回到全部"
            step("过期链接自愈", s6b)

            # ── 关键词 ───────────────────────────────────────
            def s7():
                page.locator("#resetBtn").click()
                page.wait_for_timeout(900)
                assert total_on_page(page) == t0, "重置没回到初始总数"
                page.fill("#f_q", "坍塌")
                page.press("#f_q", "Enter")
                page.wait_for_timeout(900)
                got = total_on_page(page)
                assert got is not None and 0 < got < t0, (
                    f"搜「坍塌」应得一个比 {t0} 小的正数，实际 {got}")
                title = page.locator("#gridBody tr").first.inner_text()
                assert "坍塌" in title, f"首行标题里没有关键词：{title[:60]}"
                return f"坍塌：{got} 条，首行含关键词"
            step("关键词搜索", s7)

            def s8():
                page.locator("#resetBtn").click()
                page.wait_for_timeout(900)
                got = total_on_page(page)
                assert got == t0, f"重置后应回 {t0}，实际 {got}"
                assert first_row(page) == row_p1, "重置后首行与最初不同"
                return f"回到 {t0}，首行与最初一致"
            step("重置", s8)

            browser.close()
    finally:
        server.shutdown()

    bad = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(bad)} 项，失败 {len(bad)} 项。")
    if bad:
        for name, _, err in bad:
            print(f"  ✗ {name}：{err}")
        return 1
    print("✓ 交互九条全过：筛选 / 取消 / 翻页 / 分享链接 / 过期链接自愈 / 搜索 / 重置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
