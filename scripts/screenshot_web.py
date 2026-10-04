#!/usr/bin/env python3
"""六个页面的截图 + 前端渲染探针。

数值对账（条数、分布、与 SQL 相等）查不出「界面错乱 / 图表空白 / 图片裂开」
这一类问题——SQL 全对而页面是坏的，在对账里完全不可见。这个脚本补上这一维：
起真实服务、用无头 Chromium 逐页打开，然后报三件事。

- **破图**：`<img>` 加载完却 `naturalWidth == 0`
- **空白**：可见文字少于 200 字（整页没渲染出来）
- **控制台错误**：JS 抛异常，或资源 404

## 折叠区要单独截（2026-10-05 补）

前三项全跑在**首屏**上，而详情页的内容大半藏在 `<details>` 折叠里：实测 **14 个
折叠、默认只开 1 个**，首屏可见文字 **1,096 字**，逐个点开是 **7,527 字**——也就是
说此前「文字 ≥ 200 字」这条判据，对详情页只看到了 **14.6%** 的正文，8 条 CAICS
致因的证据链、B/C/D 三档字段、处理信息、原文全文**从来没进过截图**。概览页有 1 个
折叠（5,187 → 6,606 字），致因页 0 个。

所以每页截两张：**首屏**（用户第一眼看到的，原样保留）＋ **全展开**（内容都在）。
并把两件事记成判据：展开后文字必须**变多**（一个闭合折叠都不贡献文字，说明里面
是空的）、每个折叠**各自**都要有非空文字（逐个报，不只看总量）。

另：`<details>` 里的图**不**受这条影响——详情页实测 9 张图全在不折叠的图片面板里，
`document.images` 一条不漏（2026-10-05 实测，登记于此免去重复排查）。

## 详情页还要证 `raw_text` 真的看得见（2026-10-05 补）

`check_field_alignment.py` 的 `DETAIL_NOT_SHOWN` 给 `raw_text` 的豁免理由是「前端读它
渲染原文」——那条判据扫的是**静态文件里有没有 `raw_text` 这个名字**（"读了"），核不到
"看得见"。两者的差距本轮实测过：`<details>` 闭合时 `pre.raw-text` **在 DOM 里、
`offsetParent` 非 null、`clientHeight` 有 235 px**，但 `checkVisibility()` 为假、
`innerText` 里没有它。前三个判据（含最朴素的 `querySelector(...) !== null`）**全部
假绿**，只有后两个能分辨。

所以详情页在全展开后加一条硬判据：`pre.raw-text` 存在、`checkVisibility()` 为真、
`innerText` 含它自己的前 40 字，且它的文字是 API 响应里 `raw_text` 的**前缀**（长文
默认截断在 5,000 字，是设计如此）。这四问合起来才叫"用户点开就能看到、看到的
就是库里的字节"。

任何一项非空即退出码 1，可直接接进 CI。

数据接真货：最终库取**主仓库**的 `caiems_v2.db`（2,623 条），工作库与图片根也
默认指主仓库——在主仓库跑不用管，在 worktree 跑会自动指回主仓库（见
`_common.py`）。

详情页的样本分两级挑（`pick_sample`）：先从 `image_manifest` 里挑「图最多、且有
CAICS 编码」的，字段齐、编码齐、图多，覆盖面最大；工作库不可用时**退回到从最终库
直接挑**，只要求有编码、有摘要、有伤亡、`collapse_subtype` 非哨兵——图片区会是空的，
但文字区、致因树、四档折叠都照常渲染，仍是有效核查。**详情页不许因为工作库没建好
就从核查里消失**：2026-10-01 实测就是这种情况，而当时详情页刚改过三列展示。

## 截图落在哪（2026-10-05 改）

默认输出目录是 **`docs/web/figures/`**，**不再是 `docs/paper/figures/`**。后者是
V1 归档论文（`docs/paper/eswa_paper.md`）的插图目录，装的是论文正文
`![](figures/…)` 直接引用的 `figN_*`；截图是 web 核查产物、不是论文插图，默认落
进去等于把两类产物混在一处——而且**每无参数跑一次就多写 8 张 PNG**（6 张首屏 +
2 张 `_全展开`，实测约 3 MB），往一个已归档的交付物目录里持续增量。

`check_2568_render_visibility.py` 早就为此显式传 `--out` 到临时目录、并在注释里
写明「否则每次核验都会往 `docs/paper/figures/` 里塞六张 PNG」——那是对这个默认值
的**一次性绕行**；这次改的是默认值本身，绕行也就不再必要（它仍可传 `--out`）。

这不是纯偏好：`build_v2_docs.py` 的 `FIGDIR` 指向 `docs/paper_v2/figures`、插图按
正文里的 `![](figures/figN_xxx.png)` 解析，所以往 `docs/paper/figures/` 写再多也
**不会**进 V2 论文——伤害不在论文，在这个目录被当成"什么都往里丢"的公共落点。

用法：
    .venv/bin/python3 scripts/screenshot_web.py
    .venv/bin/python3 scripts/screenshot_web.py --out /tmp/shots
    .venv/bin/python3 scripts/screenshot_web.py --staging-db data/caiems_staging.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402
from _serve import point_at, serve                            # noqa: E402


def pick_sample(main_db: str, staging_db: Path) -> tuple[str | None, str]:
    """挑一个详情页样本：优先「有图 + 有编码」的，退而求其次只要求有编码。

    **详情页不依赖工作库也能渲染**（图片区给空清单 + `images_available=false`），
    所以工作库不可用只是让样本少覆盖一个区域，不该让整页从核查里消失。原先是
    `_pick_with_images` 挑不出来就整个跳过详情页——2026-10-01 实测主仓库工作库是
    空壳，于是详情页成了**唯一一个从没被截过图的页面**，而这期间详情页恰恰改过
    三列展示。两级挑选修的就是这个：有图更好，没图也得截。
    """
    aid, note = _pick_with_images(main_db, staging_db)
    if aid:
        return aid, note
    aid, why = _pick_from_main(main_db)
    if aid:
        return aid, f"{why}　〔图片区为空：{note}〕"
    return None, f"{note}；主库里也没挑出样本：{why}"


def _pick_from_main(main_db: str) -> tuple[str | None, str]:
    """直接从最终库挑，不看工作库。

    条件按「能覆盖最多详情页区域」定：必须**有 CAICS 编码**（否则致因树整块是空
    的，等于没验），优先**非坍塌类填了真实子类**的坍塌事故（这样 `collapse_subtype`
    有值区与哨兵归一两条路径都过得到），再要有 `summary` 与 `deaths`。按编码数
    降序取第一条——编码多，致因区的层分布、多证据合并、证据三级都更容易碰上。
    """
    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT a.id, a.title, a.severity, COUNT(c.code) AS n"
            "  FROM accidents a JOIN caics_causes c ON c.accident_id = a.id"
            " WHERE a.summary IS NOT NULL AND TRIM(a.summary) <> ''"
            "   AND a.deaths IS NOT NULL"
            "   AND a.collapse_subtype IS NOT NULL"
            "   AND a.collapse_subtype <> '不适用'"
            " GROUP BY a.id ORDER BY n DESC LIMIT 1").fetchone()
    finally:
        con.close()
    if not row:
        return None, "没有「有编码 + 有摘要 + 有伤亡 + 坍塌子类非哨兵」的事故"
    return row["id"], (f"{(row['title'] or '')[:28]}… / {row['severity']} / "
                       f"{row['n']} 条编码　（主库直选）")


def _pick_with_images(main_db: str, staging_db: Path) -> tuple[str | None, str]:
    """挑一个既有图、又有 CAICS 编码的真实事故。

    挑不出来时**要说清卡在哪一步**。三种情况的处理办法完全不同：工作库不存在
    要跑 `create_staging_db.py`，清单是旧口径要跑 `build_image_manifest.py`，
    都有图有编码但都没有编码则是数据本身如此。2026-10-01 实测就撞上第二种
    ——主仓库的工作库 `image_manifest` 停在旧口径，226 行的 `accident_id` 在
    最终库里一个都对不上，若只回一句「没找到」会被读成「没数据」。
    """
    if not staging_db.exists():
        return None, "工作库不存在"
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
            return None, ("工作库是个空文件，一张表都没有——跑 "
                          "scripts/create_staging_db.py 建表")
        return None, f"工作库缺 image_manifest 表：{e}"
    finally:
        con_s.close()

    if not with_img:
        return None, "工作库 image_manifest 是空表"

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
                return aid, (f"{(row['title'] or '')[:28]}… / {row['severity']} / "
                             f"{n} 条编码　（清单里挑的）")
    finally:
        con.close()

    if not n_in_main:
        return None, (f"清单里 {len(with_img)} 个事故在最终库一个都对不上——清单是"
                      f"旧口径，跑 scripts/build_image_manifest.py 重建")
    return None, f"{n_in_main} 个有图事故在最终库存在，但都还没 CAICS 编码"


def _fold_state(page) -> dict:
    """折叠态：共几个 `<details>`、几个闭合、首屏文字量、**逐个**折叠的文字量。"""
    return page.evaluate("""() => {
      const ds = Array.from(document.querySelectorAll('details'));
      return {
        n: ds.length,
        closed: ds.filter(d => !d.open).length,
        text: document.body.innerText.trim().length,
        per: ds.map(d => ({open: d.open, len: d.innerText.trim().length})),
      };
    }""")


def _expand_all(page) -> dict:
    """点开每个 `<details>`，回文字总量与**逐个**折叠展开后的文字量。

    ⚠️ 「空折叠」不能用 `d.innerText.trim().length === 0` 判——`innerText`
    **包含 summary 自己的文字**（实测折叠态 10–29 字），那条判据永远不成立，
    是条空话（§2.5.64：多出来的用例是空话）。正确的判据是拿这里的结果跟
    `_fold_state` 的逐个值比：**闭合的折叠展开后必须变长**。
    """
    page.evaluate("() => document.querySelectorAll('details')"
                  ".forEach(d => d.open = true)")
    page.wait_for_timeout(900)
    return page.evaluate("""() => ({
      text: document.body.innerText.trim().length,
      per: Array.from(document.querySelectorAll('details'))
             .map(d => d.innerText.trim().length),
    })""")


def _raw_text_state(page) -> dict | None:
    """详情页 `pre.raw-text` 的**可见性**——不是"在不在 DOM 里"。

    五问一起看才叫"用户点开就能看到"：

    | 判据 | 折叠态 | 能分辨吗 |
    |---|---|---|
    | `querySelector('pre.raw-text') !== null` | true | ✗ 假绿 |
    | `pre.offsetParent !== null` | true | ✗ 假绿 |
    | `pre.clientHeight > 0` | 235 | ✗ 假绿 |
    | `pre.checkVisibility()` | **false** | ✓ |
    | `body.innerText` 含 pre 开头 | **false** | ✓ |

    前三个看起来都很合理，实测**全部在折叠态成立**（2026-10-05）。后两个是唯二
    能分辨的，所以判据用它们。`weak_*` 三个一并返回，好在输出里展示"它们确实
    分不出来"——只写结论、不留证据，下次没人知道为什么不用 `offsetParent`。
    """
    return page.evaluate("""() => {
      const pre = document.querySelector('pre.raw-text');
      if (!pre) return null;
      const t = pre.textContent;
      return {
        visible: pre.checkVisibility(),
        in_text: document.body.innerText.includes(t.slice(0, 40)),
        len: t.length,
        head: t.slice(0, 40),
        weak_offset_parent: pre.offsetParent !== null,
        weak_client_h: pre.clientHeight,
      };
    }""")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # ⚠️ 默认落点是 `docs/web/figures/`，**刻意不是 `docs/paper/figures/`**：
    # 那是 V1 归档论文的插图目录（`figN_*` 被正文 `![]()` 引用）。截图是 web
    # 核查产物，默认落进去会让每次无参数运行都往归档目录多写 8 张 PNG。
    # 改动这条会让 `check_2568_render_visibility.py` 的 C 段判据转红——那是设计。
    ap.add_argument("--out", default=str(ROOT / "docs" / "web" / "figures"),
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
    mark_map: dict[str, list[str]] = {}
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
            is_detail = route.startswith("/accident/")
            try:
                page.goto(base + route, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1800)       # 等图表与图片渲染
            except Exception as e:                # noqa: BLE001
                problems.append(f"{name} 打不开：{str(e)[:70]}")
                print(f"  ✗ {name:<12} 打不开")
                continue

            # ① 首屏（用户第一眼看到的，原样保留）
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            fold0 = _fold_state(page)
            text_first = fold0["text"]

            # ② 全展开（内容都在）——只有有关闭的折叠才多截一张
            fold1 = None
            if fold0["closed"]:
                fold1 = _expand_all(page)
                page.screenshot(path=str(out / f"{name}_全展开.png"), full_page=True)

            # 详情页的原文可见性——**不放进折叠分支里**：万一 `renderRaw` 哪天
            # 不再折叠，这条判据不该跟着一起消失（那时它反而更该跑）。
            raw = _raw_text_state(page) if is_detail else None

            broken = page.evaluate(
                "() => Array.from(document.images)"
                ".filter(i => i.complete && i.naturalWidth === 0)"
                ".map(i => i.currentSrc || i.src)")
            n_img = page.evaluate("() => document.images.length")
            n_canvas = page.evaluate("() => document.querySelectorAll('canvas').length")
            n_svg = page.evaluate("() => document.querySelectorAll('svg').length")

            marks = []
            if broken:
                marks.append(f"破图 {len(broken)}")
                problems.append(f"{name} 有 {len(broken)} 张图裂开：{broken[:2]}")
            if text_first < 200:
                marks.append(f"文字仅 {text_first} 字")
                problems.append(f"{name} 页面几乎空白（首屏可见文字 {text_first} 字）")
            if console_errors:
                marks.append(f"控制台错误 {len(console_errors)}")
                problems.append(f"{name} 控制台报错：{console_errors[0][:70]}")

            # 折叠区的判据：**闭合的**折叠展开后必须变长
            fold_note = ""
            if fold1 is not None:
                stalled = [i for i, b in enumerate(fold0["per"])
                           if not b["open"] and fold1["per"][i] <= b["len"]]
                grew = fold1["text"] - text_first
                fold_note = f"折叠 {fold0['closed']}/{fold0['n']} 展开 +{grew} 字"
                if stalled:
                    marks.append(f"折叠 {len(stalled)} 个没内容")
                    problems.append(
                        f"{name} 有 {len(stalled)} 个闭合折叠展开后一字未增"
                        f"（序号 {stalled}）——有壳没内容")
            elif fold0["n"] == 0:
                fold_note = "无折叠"

            # 详情页的 raw_text 可见性（结清 §2.5.67 的待决）
            if raw is not None:
                fold_note += (f"；raw_text {raw['len']} 字 "
                              f"可见={raw['visible']} 在正文里={raw['in_text']}")
                if not raw["visible"] or not raw["in_text"]:
                    marks.append("原文不可见")
                    problems.append(
                        f"{name} 的 `pre.raw-text` 展开后仍不可见"
                        f"（checkVisibility={raw['visible']}、"
                        f"innerText 含={raw['in_text']}）——"
                        f"注意 offsetParent={raw['weak_offset_parent']}、"
                        f"clientHeight={raw['weak_client_h']} 这两个弱判据"
                        f"这时都**成立**，别拿它们当可见性")
                api_raw = (page.request.get(
                    f"{base}/api/accidents/{accident_id}").json().get("raw_text") or "")
                if api_raw and not api_raw.startswith(raw["head"]):
                    marks.append("原文与接口不一致")
                    problems.append(
                        f"{name} 页面上的原文开头与接口的 raw_text 对不上："
                        f"页面={raw['head']!r} 接口={api_raw[:40]!r}")
            elif is_detail:
                marks.append("无 raw-text")
                problems.append(
                    f"{name} 页面上没有 `pre.raw-text`——样本有 raw_text 却没渲染，"
                    f"要么样本挑错了，要么 renderRaw 没跑")

            print(f"  {'⚠' if marks else '✓'} {name:<12} 文字 {text_first:>5} 字  "
                  f"img {n_img:>2}  canvas {n_canvas}  svg {n_svg}  "
                  f"{fold_note}  "
                  f"{'  ⚠ ' + '、'.join(marks) if marks else ''}")
            mark_map[name] = marks

        browser.close()

    server.shutdown()
    # 机器可读的判据汇总：注入式自证要断言「**红的恰好是哪几条**」（§2.5.62
    # 「红的集合恰好是两条」），靠解析上面那行中文输出得先处理 `⚠`/`、`/排版，
    # 脆。给一行干净的 JSON，人也能看。
    print("\nMARKS " + json.dumps(mark_map, ensure_ascii=False))
    print(f"\n截图 → {out}（每页首屏一张；有折叠的页另加一张 `_全展开`）")
    if problems:
        print(f"\n✗ {len(problems)} 处问题：")
        for x in problems:
            print(f"    - {x}")
        return 1
    print("\n✓ 各页无破图、无空白、无控制台报错；折叠区展开后有内容，"
          "详情页原文可见且与接口一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
