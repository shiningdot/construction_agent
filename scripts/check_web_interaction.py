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

## 验的十七件事

1. 初始加载：分页器有总数，且是多页
2. **概览页筛选维度齐全**：`/api/filters` 给几维可筛，页面就渲染几维
3. **致因页筛选维度齐全**：同一判据，同一份 `/api/filters`
4. 勾一个省份 chip：总数 == 该 chip 自己标的条数
5. 列表真的只剩该省（抽前几行的省份列）
6. 再点一次取消：总数回到初始
7. 翻到第 2 页：页码变了、首行换了
8. **粘贴链接能复原**：把当前 hash 拼成 URL 新开一页，状态一致
9. **过期链接自愈**：URL 里带着库里已不存在的省份时，该值被清掉
10. 关键词搜索：总数变小，且结果标题确实含该词
11. 重置：回到初始
12. **详情页折叠能真点开**（§2.5.69）：逐个点 `summary`，开合与文字量都对
13. **「展开全文」按钮真点有效**：点前字数 + 按钮说还剩的 = 接口全文 = 点后字数
14. **图片灯箱**：点缩略图开、点「下一张」换图、按 Esc 关
15. **列表 → 详情 → 返回**：这条导航链走得通
16. **键盘路径**（§2.5.70）：Tab 真实遍历可达缩略图与折叠；Enter / Space 真开合
17. **灯箱键盘与焦点**：键盘能开灯箱；Tab 按多少次都出不去；Esc 后焦点归位
18. **批量条勾选链路**（§2.5.96）：勾选行 → 批量条出现 → 计数对 → 清除后消失

第 2、3 条是后来补的。原先 chips 只能按「第几个」定位，`/api/filters` 返回五维、
概览页只画三维这种缺口测不出来——那两维的值在接口里躺着、用户筛不到，页面上
没有任何症状。给 chips 容器加了 `data-key` 之后，两边可以直接对集合。

第 12–15 条也是后来补的（§2.5.69）。前十一条全在**列表页**，详情页一条没有；
而 §2.5.68 测折叠用的是 `page.evaluate("d.open = true")`——**程序式改 DOM 属性**，
不走路由 click 事件到浏览器默认行为的**真实链路**。两者不等价：中间若有 JS
`preventDefault`，程序式照样能开、用户真点开不了，而 §2.5.68 那条判据会报绿。
12–15 条一律走 `locator.click()`，走真实事件链。

第 16、17 条又是另一条链（§2.5.70）。12–15 条虽然走的是真事件，但走的是**鼠标**
事件链；键盘走「focus + keydown → 浏览器激活语义」，两条路不等价——一个
`<div onclick>` 鼠标点得开而键盘永远 Tab 不到，一段吃掉 keydown 的 JS 也能让
Enter 失效而鼠标照常。16、17 两条还管**焦点**：灯箱声明了 `aria-modal="true"` 却按一次
Tab 就跑到背景上（§2.5.70 实测到的真缺陷，已修），而类名与 `aria-*` 属性都看不出
这件事——只有问「焦点此刻在谁身上」才知道。

第 18 条（§2.5.96）补的是**批量条**：勾选行 → 批量条出现 → 计数对 → 清除后
消失。工作库 `raw_accidents` 实测 0 行、没有行可勾，故用 `page.route` 拦
`/api/collect/raw` 注入两行（字段名照抄后端真实 SELECT，不自己起名）。

用法：
    .venv/bin/python3 scripts/check_web_interaction.py

退出码 `0` = 十八条全过，`1` = 有不过的。
"""
from __future__ import annotations

import argparse
import json
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


#: 展开全文按钮的截断阈值，与 `accident.js` 的 `RAW_PREVIEW` 同值。
#: 判据不依赖它（判据是三点闭合），只在报错信息里用到，故允许它漂移。
RAW_PREVIEW = 5000


def pick_samples(main_db: str, staging_db: str) -> tuple[str, str]:
    """挑两条详情页样本：一条**图最多**、一条**正文最长**。

    ⚠️ **为什么是两条而不是一条**（2026-10-05 实测）：库里这两件事**没有交集**——
    `raw_text` 超过 5,000 字的 13 条**全部是文献轨**（PDF 电子书，`source_url`
    是本地文件路径）记录，图数**全为 0**；而有图的在线采集记录正文都在三位数
    长度。所以「展开全文按钮」与「图片灯箱」只能分别在两条记录上验。
    这不是数据缺陷，是数据形态——两条轨的记录本来就不是一个样子。

    有图的那条顺带要求有 CAICS 编码：详情页元素最全时（12 个折叠）测折叠最有效。
    """
    import sqlite3
    con_s = sqlite3.connect(f"file:{staging_db}?mode=ro", uri=True)
    try:
        by_img = [aid for aid, _ in con_s.execute(
            "SELECT accident_id, COUNT(*) c FROM image_manifest "
            "GROUP BY accident_id ORDER BY c DESC LIMIT 200")]
    finally:
        con_s.close()

    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    try:
        many_img = ""
        for aid in by_img:
            r = con.execute("SELECT LENGTH(raw_text) FROM accidents WHERE id=?",
                            (aid,)).fetchone()
            if not r:
                continue
            n = con.execute("SELECT COUNT(*) FROM caics_causes WHERE accident_id=?",
                            (aid,)).fetchone()[0]
            if r[0] and n:
                many_img = aid
                break
        row = con.execute(
            "SELECT id FROM accidents WHERE LENGTH(raw_text) > ? "
            "ORDER BY LENGTH(raw_text) DESC LIMIT 1", (RAW_PREVIEW,)).fetchone()
        long_text = row[0] if row else ""
    finally:
        con.close()

    assert many_img, "库里挑不出「有图 + 有编码 + 有正文」的事故"
    assert long_text, ("库里没有正文超过 "
                       f"{RAW_PREVIEW} 字的事故——「展开全文」按钮在真实数据下"
                       "不会出现，第 13 条判据无从验起")
    return many_img, long_text


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

            def chips_keys(pg):
                """页面上实际画出来的筛选维度（chips 容器的 `data-key`）。"""
                return sorted(pg.eval_on_selector_all(
                    "#filterChips .chips[data-key]",
                    "els => els.map(e => e.getAttribute('data-key'))"))

            def want_keys(pg):
                """后端给了哪几维可筛。

                两个页面都从 `/api/filters` 取可选值（概览页直接渲染它；
                致因页 `causes.js` 也把它读进 `optsCache`），所以判据共用一套，
                免得两处各写一遍、改一处漏一处。`/api/causes/distribution` 的
                `filter` 回显看着也能用，但那里的空数组意思是「当前没筛这一维」，
                与「这一维有没有可选值」是两回事，判不出前端漏渲染。

                判据里滤掉空列表的维度：那一维确实没值，前端不画是对的。
                `sort` 单独排除——它是个下拉（`#f_sort`），不是 chip 组。
                """
                opts = pg.request.get(base + "/api/filters").json()
                return sorted(k for k, v in opts.items()
                              if isinstance(v, list) and v and k != "sort")

            def dimension_case(name: str, pg):
                """后端给了几维筛选，页面就画几维。

                这条是在补一个**盲区**：原先 chips 只能按「第几个」定位，于是
                「`/api/filters` 返回五维、页面只渲染三维」这种缺口一直测不出来
                ——后两维的值在接口里躺着，用户却筛不到，页面上也没有任何症状。
                现在 chips 容器带 `data-key`，两边可以直接对集合。
                """
                want, got = want_keys(pg), chips_keys(pg)
                assert got == want, (
                    f"{name}：后端给了 {want} 这几维可筛，页面只画了 {got}"
                    f"——少的那几维在 /api/filters 里是有值的，用户却筛不到")
                return f"{name} {len(got)} 维：{' / '.join(got)}"

            step("筛选维度齐全", lambda: dimension_case("概览页", page))

            def s1c():
                pc = ctx.new_page()
                try:
                    pc.goto(base + "/causes", wait_until="networkidle", timeout=30000)
                    pc.wait_for_timeout(1200)
                    return dimension_case("致因页", pc)
                finally:
                    pc.close()
            step("致因页维度齐全", s1c)

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

            # ══════════ 详情页的真实点击（§2.5.69）══════════
            # 上面十一条全在列表页。详情页此前一条交互判据都没有——而它恰恰是
            # 内容藏得最深的一页：§2.5.68 量出首屏只覆盖 32.5% 的正文，其余
            # 全在折叠里。下面四条一律走 `locator.click()`（真实事件链），
            # 不用 `evaluate('e.click()')`，也不用 `d.open = true`。
            many_img, long_text = pick_samples(main_db, staging_db)

            def s9():
                """逐个真点 `summary`——不是 `d.open = true`。

                两者**不等价**：程序式改属性绕过了 click 事件到浏览器默认行为的
                链路，中间若有 JS `preventDefault`，程序式照样开、用户真点开不了
                （`check_2569` 的 A 段用注入证明了这一点）。判据两头都收：
                点一下必须翻状态，展开必须变长、收起必须变短。
                """
                pg = ctx.new_page()
                try:
                    pg.goto(f"{base}/accident/{many_img}", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(1800)
                    n = pg.locator("details").count()
                    assert n >= 2, f"详情页只有 {n} 个折叠，验不出什么"
                    bad = []
                    for i in range(n):
                        d = pg.locator("details").nth(i)
                        was = d.evaluate("d => d.open")
                        b4 = d.evaluate("d => d.innerText.trim().length")
                        d.locator("summary").first.click(timeout=5000)
                        pg.wait_for_timeout(180)
                        now = d.evaluate("d => d.open")
                        af = d.evaluate("d => d.innerText.trim().length")
                        if now == was:
                            bad.append(f"第 {i} 个点了没反应")
                        elif now and af <= b4:
                            bad.append(f"第 {i} 个展开后文字没变多（{b4}→{af}）")
                        elif not now and af >= b4:
                            bad.append(f"第 {i} 个收起后文字没变少（{b4}→{af}）")
                    assert not bad, "；".join(bad[:3])
                    return f"{n} 个折叠逐个真点：开合翻转、文字量两头都对"
                finally:
                    pg.close()
            step("详情页折叠能真点开", s9)

            def s10():
                """「展开全文」按钮：三点闭合，不硬编码那个 43,124。

                点前字数 + 按钮自称还剩的字数 == 接口给的全文长度 == 点后字数。
                三点里任意一处与另两处不符都能抓到（按钮谎报、截断点错位、
                点完没换成全文）。
                """
                pg = ctx.new_page()
                try:
                    pg.goto(f"{base}/accident/{long_text}", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(1800)
                    more = pg.locator("button.raw-more")
                    assert more.count() == 1, (
                        f"{long_text} 的正文长到该出现「展开全文」按钮了，"
                        f"实际 {more.count()} 个")
                    # ⚠️ 按钮在**闭合的 `<details>` 里**。`inner_text()` 对不可见
                    # 元素返回**空串**（实测，不是 N/A 也不是原文），必须用
                    # `text_content()`——用 `inner_text()` 这条判据会读不出字数，
                    # 报的是「按钮文字读不出剩余字数」而不是它的真实毛病。
                    label = more.first.text_content() or ""
                    m = re.search(r"还有\s*([\d,]+)\s*字", label)
                    assert m, f"按钮文字读不出剩余字数：{label!r}"
                    rest = int(m.group(1).replace(",", ""))
                    pre = pg.locator("pre.raw-text").first
                    b4 = pre.evaluate("e => e.textContent.length")
                    # 用户也得先展开外面的折叠才点得到它——这是真实路径的一部分
                    pg.locator("details").filter(has=more.first).first.evaluate(
                        "d => d.open = true")
                    pg.wait_for_timeout(300)
                    more.first.click(timeout=5000)
                    pg.wait_for_timeout(500)
                    af = pre.evaluate("e => e.textContent.length")
                    full = len(pg.request.get(
                        f"{base}/api/accidents/{long_text}").json()["data"]["raw_text"] or "")
                    assert b4 + rest == full == af, (
                        f"点前 {b4} + 按钮说还剩 {rest} = {b4 + rest}，"
                        f"接口全文 {full}，点后 {af}")
                    assert pg.locator("button.raw-more").count() == 0, \
                        "点了「展开全文」按钮还在"
                    return f"{b4} + {rest} = {af} 字，按钮已消失"
                finally:
                    pg.close()
            step("展开全文按钮", s10)

            def s11():
                """图片灯箱：点开 / 下一张 / Esc 关。

                顺带判 `aria-hidden` 跟着翻——灯箱是 `role="dialog"`，开了却
                留着 `aria-hidden="true"` 的话，读屏用户面前它等于不存在。
                """
                pg = ctx.new_page()
                try:
                    pg.goto(f"{base}/accident/{many_img}", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(2200)
                    k = pg.locator("button.img-tile").count()
                    assert k >= 2, f"只有 {k} 张图，验不出「下一张」"
                    # ⚠️ 灯箱是**懒建**的（`open()` 里那句 `if (!host) build()`）：
                    # 进页面时 DOM 里根本没有 `.lightbox` 这个节点，点第一张才建。
                    # 所以「点击前不存在」不是缺陷，是设计——判据写成「点击后没
                    # 建出来」才是验它。第一版把这两件事搞反了，实跑报了一次假红。
                    assert pg.locator(".lightbox").count() == 0, \
                        "还没点，灯箱节点就已经在 DOM 里了（懒建失效）"
                    pg.locator("button.img-tile").first.click(timeout=5000)
                    pg.wait_for_timeout(500)
                    lb = pg.locator(".lightbox")
                    assert lb.count() == 1, "点了缩略图，灯箱节点没建出来"
                    assert lb.evaluate("e => e.classList.contains('is-open')"), \
                        "点了缩略图灯箱没开"
                    assert lb.get_attribute("aria-hidden") == "false", \
                        "灯箱开了，但 aria-hidden 还是 true——读屏用户看不见"
                    src0 = pg.locator(".lb-img").evaluate("e => e.src")
                    pg.locator(".lb-next").first.click(timeout=5000)
                    pg.wait_for_timeout(400)
                    assert pg.locator(".lb-img").evaluate("e => e.src") != src0, \
                        "点了「下一张」图没换"
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(300)
                    assert not lb.evaluate("e => e.classList.contains('is-open')"), \
                        "按 Esc 灯箱没关"
                    return f"{k} 张图：点开 / 下一张 / Esc 都对"
                finally:
                    pg.close()
            step("图片灯箱", s11)

            def s12():
                """列表 → 详情 → 返回。这条链此前没验过：列表行是不是真链接、
                详情页的「返回列表」按下去回不回得来。

                返回控件是 `<a href="/">` + 一个 `history.back()` 的 handler
                （有历史时用它，好把列表的筛选与页码从 URL hash 里恢复）。
                从列表点进来 `history.length > 1`，走的正是 back 那条路。
                """
                pg = ctx.new_page()
                try:
                    pg.goto(base + "/", wait_until="networkidle", timeout=30000)
                    pg.wait_for_timeout(1500)
                    link = pg.locator("#gridBody a[href^='/accident/']").first
                    href = link.get_attribute("href")
                    link.click()
                    pg.wait_for_timeout(2200)
                    here = pg.evaluate("() => location.pathname")
                    assert "/accident/" in here, f"点列表行进不了详情页，现在在 {here}"
                    pg.locator("a.back-link").first.click()
                    pg.wait_for_timeout(1500)
                    back = pg.evaluate("() => location.pathname")
                    assert back.rstrip("/") in ("", "/index"), \
                        f"点「返回列表」没回到列表，现在在 {back}"
                    return f"{href} 进详情，返回回到列表"
                finally:
                    pg.close()
            step("列表→详情→返回", s12)

            def s13():
                """键盘路径：Tab 到不到得了、Enter/Space 开不开得动。

                §2.5.70。`locator.click()` 走的是**鼠标事件链**，键盘走
                「focus + keydown → 浏览器激活语义」——两条路不是一回事：一个
                `<div onclick>` 鼠标点得开、键盘永远 Tab 不到；一段吃掉 keydown
                的 JS 也能让 Enter 失效而鼠标照常。第 12–15 条全用鼠标，这一条
                补键盘那一半。

                可达性必须**真实 Tab 遍历**，不能拿 `.focus()` 代替——那等于
                先假设"已经能到"再去用。
                """
                pg = ctx.new_page()
                try:
                    pg.goto(f"{base}/accident/{many_img}", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(1500)
                    trail = []
                    for _ in range(30):
                        pg.keyboard.press("Tab")
                        trail.append(pg.evaluate(
                            "() => { const e = document.activeElement;"
                            " return e ? e.tagName + '.' + (e.className || '') : ''; }"))
                    assert any("img-tile" in t for t in trail), \
                        "Tab 遍历 30 次没到过任何缩略图——键盘用户打不开灯箱"
                    assert any(t.startswith("SUMMARY") for t in trail), \
                        "Tab 遍历 30 次没到过任何折叠 summary"

                    d = pg.locator("details").nth(1)
                    d.locator("summary").first.focus()
                    # ⚠️ 基准要取**前一次**的状态，不能取初始状态：`<details>`
                    # 是能来回翻转的，拿初始值当基准时「按 Space 把 True 翻回
                    # False」会被误判成"没翻转"（第一版就这么错的，报了一次假红）。
                    for key in ("Enter", "Space", "Enter"):
                        prev = d.evaluate("x => x.open")
                        pg.keyboard.press(key)
                        pg.wait_for_timeout(220)
                        now = d.evaluate("x => x.open")
                        if now == prev:
                            raise AssertionError(
                                f"summary 聚焦后按 {key} 没翻转（仍 {now}）——"
                                "键盘开不了/关不上折叠")

                    # 长文页：**闭合的 details 内部按规范不该被 Tab 到**，
                    # 所以「展开全文」按钮的键盘路径必须是「先 Enter 展开折叠、
                    # 再聚焦按钮、再 Space」。这条链本身就是判据。
                    pg.goto(f"{base}/accident/{long_text}", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(1500)
                    # ⚠️ 「看不看得见」要用 `is_visible()`，**不能用 `count()`**：
                    # 按钮在 DOM 里一直都在（只是被闭合的 `<details>` 藏起来），
                    # `count()` 恒为 1，拿它判可见性永远为假（第一版就这么错的，
                    # 报了一次假红）。同 §2.5.69 那对 `inner_text()` /
                    # `text_content()`——都是选到了测不出这件事的那个 API。
                    if pg.locator("button.raw-more").is_visible():
                        raise AssertionError("还没展开折叠，「展开全文」按钮就可见了")
                    idx = pg.evaluate(
                        "() => [...document.querySelectorAll('details')]"
                        ".findIndex(d => d.querySelector('pre.raw-text'))")
                    assert idx >= 0, "长文页找不到「原文全文」那个折叠"
                    # 顺带验这条键盘链的**前提**：闭合的 `<details>` 内部不进
                    # Tab 序，所以「展开全文」必须先展开折叠才够得着。
                    pg.locator("details").nth(idx).locator("summary").first.focus()
                    pg.keyboard.press("Tab")
                    here = pg.evaluate(
                        "() => { const e = document.activeElement;"
                        " return e ? (e.className || '') : ''; }")
                    if "raw-more" in here:
                        raise AssertionError(
                            "折叠还没展开，Tab 就落到了「展开全文」按钮上")
                    pg.locator("details").nth(idx).locator("summary").first.focus()
                    pg.keyboard.press("Enter")
                    pg.wait_for_timeout(280)
                    if not pg.locator("details").nth(idx).evaluate("x => x.open"):
                        raise AssertionError("长文页 summary 按 Enter 没展开")
                    more = pg.locator("button.raw-more")
                    assert more.count() == 1, \
                        "折叠展开后「展开全文」按钮没出现（或不止一个）"
                    pre = pg.locator("pre.raw-text").first
                    b4 = pre.evaluate("e => e.textContent.length")
                    more.first.focus()
                    pg.keyboard.press("Space")   # 按钮用 Space 激活，比 Enter 更典型
                    pg.wait_for_timeout(320)
                    af = pre.evaluate("e => e.textContent.length")
                    if af <= b4:
                        raise AssertionError(
                            f"聚焦「展开全文」按 Space 没展开（{b4}→{af}）")
                    return (f"Tab {len(trail)} 次可达缩略图与折叠；Enter/Space 真开合；"
                            f"长文 {b4}→{af} 字")
                finally:
                    pg.close()
            step("键盘路径", s13)

            def s14():
                """灯箱的焦点管理：`aria-modal="true"` 是**声明**，不是实现。

                §2.5.70 实测出两个真缺陷（已修）：①灯箱开着时按一次 Tab 焦点就
                跑到背景导航上——而屏幕阅读器会照着 aria-modal 把背景整个隐藏，
                于是用户以为对话框已关、实际焦点在背景里；②按 Esc 关闭后焦点掉到
                `<body>`，键盘用户要从页首重新 Tab 一遍才回得到画廊。

                判据不看类名、只看**焦点在哪**：Tab 按多少次都必须仍在灯箱内，
                关闭后焦点必须回到触发它的那张缩略图。
                """
                pg = ctx.new_page()
                try:
                    pg.goto(f"{base}/accident/{many_img}", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(1500)
                    tile = pg.locator("button.img-tile").nth(1)
                    tile.scroll_into_view_if_needed()
                    pg.wait_for_timeout(200)
                    tile.focus()
                    pg.keyboard.press("Enter")      # 键盘开，不是点
                    pg.wait_for_timeout(420)
                    lb = pg.locator(".lightbox").first
                    assert lb.evaluate("e => e.classList.contains('is-open')"), \
                        "缩略图聚焦后按 Enter 没打开灯箱——键盘开不了"

                    inside = ("() => { const a = document.activeElement;"
                              " const h = document.querySelector('.lightbox');"
                              " return !!(a && h && h.contains(a)); }")
                    assert pg.evaluate(inside), \
                        "灯箱开了，但焦点没移进灯箱（键盘用户还留在背后的页面上）"
                    for i in range(12):
                        pg.keyboard.press("Tab")
                        assert pg.evaluate(inside), \
                            f"第 {i + 1} 次 Tab 焦点跑出了灯箱——`aria-modal` 是空话"
                    for i in range(4):
                        pg.keyboard.press("Shift+Tab")
                        assert pg.evaluate(inside), \
                            f"第 {i + 1} 次 Shift+Tab 焦点跑出了灯箱"

                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(350)
                    assert not lb.evaluate("e => e.classList.contains('is-open')"), \
                        "按 Esc 灯箱没关"
                    who = pg.evaluate(
                        "() => { const e = document.activeElement;"
                        " return e ? e.tagName + '.' + (e.className || '') : ''; }")
                    assert "img-tile" in who, \
                        f"Esc 关闭后焦点没归还给触发它的缩略图，现在在 {who or '(空)'}"
                    return ("键盘开灯箱；Tab 12 次 + Shift+Tab 4 次都在灯箱内；"
                            "Esc 后焦点归位到缩略图")
                finally:
                    pg.close()
            step("灯箱键盘与焦点", s14)

            def s15():
                """批量条：勾选 → 出现 → 计数 → 清除（§2.5.96）。

                `CLAUDE.md` 与 `docs/web/01_在线采集.md` 各写着一句声明，
                说批量条这条链由本脚本管——此前**本脚本里并没有**这条判据，
                那两句是悬空的（§2.5.96 的 A 类判据抓出来的 2 条）。这一条
                把它兑现。

                ⚠️ 上面**不照抄那句声明的原话**：§2.5.96 的 A 类判据按固定
                句式扫全仓，**分不出"作出声明"与"逐字引述声明"**——首版
                docstring 抄了一遍，当场被判成一条自指的**新**声明（实测
                A 类 6 条 vs 登记 5 条）。凡引述一律改写成描述。

                ⚠️ 工作库 `raw_accidents` 实测 0 行（§2.5.86 记录），**没有
                行可勾**，故用 `page.route` 拦 `/api/collect/raw` 注入两行。
                注入行的字段名**照抄** `collect.py:335-340` 的真实 SELECT
                （`raw_id`/`source_key`/`content_preview`/`has_assets` …），
                不自己起名——否则测的是"前端容忍缺字段"，不是真实链路。
                本判据测的是**前端「勾选→出现→计数→清除」这条链**，**不是**
                "后端确实返回了行"（后者归 `check_2583` / `check_2584` 那一族）。

                ⚠️ 读计数文本一律用 `text_content()` 而**不是** `inner_text()`
                ——批量条默认带着 `hidden`，`innerText` 反映**渲染性**，闭合时
                恒返回空串（§2.5.69 立的账）。这里要的是"JS 往 DOM 里写了
                什么"，不是"用户此刻看不看得见"。
                """
                def row(rid, title):
                    return {
                        "raw_id": rid, "source_key": "mem", "task_id": None,
                        "title": title, "source_url": "https://example.invalid/",
                        "fetch_time": "2026-10-05 10:00:00",
                        "publish_date": "2026-10-01", "status": "new",
                        "gate_verdict": None, "gate_reason": None,
                        "gate_stage": None, "gate_confidence": None,
                        "ext_date": None, "ext_province": None, "ext_city": None,
                        "ext_deaths": None, "ext_injuries": None,
                        "ext_accident_type": None, "ext_severity": None,
                        "content_preview": "探针用的正文片段。", "content_len": 9,
                        "has_assets": False,
                    }

                payload = {
                    "ok": True, "available": True,
                    "meta": {"total": 2, "page": 1, "size": 50, "pages": 1},
                    "data": [row("probe-a", "探针记录甲"),
                             row("probe-b", "探针记录乙")],
                }
                pg = ctx.new_page()
                try:
                    # 正则而不是 glob：Playwright 的 `*` 不跨 `/`，但这条
                    # 判据不能靠"猜 glob 语义"成立，写死 URL 形态最稳。
                    pg.route(re.compile(r".*/api/collect/raw(\?.*)?$"),
                             lambda route: route.fulfill(
                                 status=200,
                                 content_type="application/json",
                                 body=json.dumps(payload, ensure_ascii=False)))
                    pg.goto(f"{base}/collect", wait_until="networkidle",
                            timeout=30000)
                    pg.wait_for_timeout(1200)

                    bar = pg.locator("#bulkBar")
                    count = pg.locator("#bulkCount")
                    boxes = pg.locator("#rawBody input[type=checkbox]")
                    assert boxes.count() == 2, \
                        (f"注入 2 行后列表应有 2 个勾选框，实测 {boxes.count()} 个"
                         "——拦截没生效或行没渲染出来，下面每格都失去意义")

                    def text() -> str:
                        return (count.text_content() or "").strip()

                    def shown() -> bool:
                        return not bar.evaluate("e => e.hidden")

                    # ① 起点：模板里那个容器带着 `hidden`，一行没勾就不该出现
                    # （这里**故意不写出它的选择器**：`check_2596_handoff.py`
                    # 拿那个 id 选择器当"这条声明兑现了没有"的探针，注释里
                    # 再抄一遍等于给它一把假钥匙——§2.5.70 的坑）。
                    if shown():
                        raise AssertionError("还没勾选，批量条就显示了")
                    # ⚠️ 计数初始**不是空串**而是「已选 0 条」——`renderBulkBar()`
                    # 在页面初始化时跑过一次，把 0 写进了 DOM（容器还 hidden 着，
                    # 用户看不见）。第一版把判据写成"应为空串"，报了一次假红：
                    # **判据要照着实际形态写，不能照着我以为的形态写**
                    # （§2.5.90：判据的设计前提也会错）。
                    # 真正要抓的是"一行没勾就报了个非零数"，故只否掉非 0。
                    m0 = re.search(r"已选\s*(\d+)\s*条", text())
                    if m0 and m0.group(1) != "0":
                        raise AssertionError(f"还没勾选，计数已写着「{text()}」")

                    # ② 勾第一行 → 出现 + 计数 1
                    boxes.nth(0).check()
                    pg.wait_for_timeout(200)
                    if not shown():
                        raise AssertionError("勾了 1 行，批量条没出现")
                    if text() != "已选 1 条":
                        raise AssertionError(
                            f"勾了 1 行，计数写着「{text()}」，应为「已选 1 条」")

                    # ③ 勾第二行 → 计数跟着走（**只测"出现过"会漏掉这一格**：
                    #    计数写死不更新时 ② 照样通过）
                    boxes.nth(1).check()
                    pg.wait_for_timeout(200)
                    if text() != "已选 2 条":
                        raise AssertionError(
                            f"勾了 2 行，计数写着「{text()}」，应为「已选 2 条」"
                            "——计数没跟着选择走")

                    # ④ 取消勾第二行 → 回到 1（反方向；`checked.delete` 那一支）
                    boxes.nth(1).uncheck()
                    pg.wait_for_timeout(200)
                    if text() != "已选 1 条" or not shown():
                        raise AssertionError(
                            f"取消勾 1 行后计数「{text()}」、显示={shown()}，"
                            "应为「已选 1 条」且仍显示")

                    # ⑤ 再取消勾第一行 → 清零即隐藏
                    boxes.nth(0).uncheck()
                    pg.wait_for_timeout(200)
                    if shown():
                        raise AssertionError(
                            f"全部取消勾选后批量条还在（计数「{text()}」）")

                    # ⑥ 「取消选择」按钮：条收起 + 勾选框一并复位（按钮走的是
                    #    另一条通路 `checked.clear(); loadRaw()`，与 ⑤ 的
                    #    checkbox 通路不是同一段代码）
                    boxes.nth(0).check()
                    boxes.nth(1).check()
                    pg.wait_for_timeout(200)
                    if text() != "已选 2 条":
                        raise AssertionError("清除按钮用例的前置没成立")
                    pg.locator("#bulkClear").click()
                    pg.wait_for_timeout(900)
                    if shown():
                        raise AssertionError("点了「取消选择」，批量条没收起")
                    left = pg.locator("#rawBody input[type=checkbox]:checked").count()
                    if left:
                        raise AssertionError(
                            f"点了「取消选择」，仍有 {left} 个勾选框是勾上的"
                            "——选择清空了但勾选框没复位")
                    return ("默认隐藏；勾 1/2 行计数跟走；逐个取消回到隐藏；"
                            "「取消选择」收起并复位勾选框")
                finally:
                    pg.close()
            step("批量条勾选链路", s15)

            browser.close()
    finally:
        server.shutdown()

    bad = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(bad)} 项，失败 {len(bad)} 项。")
    if bad:
        for name, _, err in bad:
            print(f"  ✗ {name}：{err}")
        return 1
    print("✓ 交互十八条全过：两页维度齐全 / 筛选 / 取消 / 翻页 / 分享链接 / "
          "过期链接自愈 / 搜索 / 重置；详情页折叠真点 / 展开全文 / 灯箱 / "
          "列表↔详情往返；键盘路径 / 灯箱焦点陷阱与归还；批量条勾选链路。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
