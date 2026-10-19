"""M2 在线采集的第三层验证：headless Chrome 真点击。

前两层（Flask test client、真 HTTP + urllib）验的是接口对不对；这一层验的是
**人在浏览器里点得通**——勾源、开始采集、任务卡自己走完、开抽屉、批量裁决、
筛选翻页。前面两层全绿也可能栽在这里：接口 200、数据正确、页面却是空的。

采集页此前是三层里唯一没有第三层的（`e2e_collect.py` 只到第二层）。补上它，
三页（采集 / 提取 / 审核）的点击层就齐了。

不碰外网——`registry.build` 被换成返回假采集器的版本，但**两条产出通路、落库、
判定、统计全是真的**，工作库也是真的 sqlite 文件。

假数据特意凑到 58 条（safehoo 55 + mohurd 3）：`#f_size` 最小的选项是 50
（collect.html），少于 51 条就只有一页，`renderPager` 直接 return，翻页这件事
在第三层就无从点起——而「点下一页」恰恰是只有第三层才验得到的。

顺序上刻意先验「工作库还没建」的那一段：用户第一次打开 /collect 时正好是这个
状态，页面不能 500，提示条要出现，源清单必须照样出 24 条。
"""

import json
import os
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 每次跑另起临时目录：这几条脚本会真建库、真落数据，复用同一目录会让上一轮的
# 库混进这一轮。目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_"))
DB = TMP / "e2e_click_collect.db"
os.environ["CAIEMS_STAGING_DB"] = str(DB)
if DB.exists():
    DB.unlink()

from construction_agent.collector import registry                      # noqa: E402
from construction_agent.collector.base import BaseCollector, RawAccidentText  # noqa: E402
from construction_agent.web import staging_db as sdb                   # noqa: E402
from construction_agent.web.factory import create_app                  # noqa: E402

PORT = 5092
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


# ── 假采集器（与 e2e_collect.py 同源） ─────────────────────

class FakeStore:
    def __init__(self):
        self.sink = None

    def attach(self, sink):
        self.sink = sink
        return self

    def add(self, record):
        if self.sink is not None:
            self.sink(record)
        return record.get("id")


_BASE_TITLES = [
    ("南京市某工地脚手架坍塌致3人死亡", "2026-03-11", 3, 2),
    ("苏州市某厂房高处坠落事故", "2026-03-12", 1, 0),
    ("徐州市某项目塔吊倾覆事故通报", "2026-02-28", 2, 5),
    ("无锡市某工地触电事故调查结果", "2025-12-04", 1, 1),
    ("常州市某项目模板支撑体系坍塌", "2026-01-19", 0, 4),
]

# 凑到 55 条，是为了让「下一页」按钮真的出现：`#f_size` 最小的选项是 50
# （collect.html），少于 51 条就只有一页，`renderPager` 直接 return，
# 翻页这件事在第三层就无从点起。第 5 条起标题加「续报」后缀保唯一。
TITLES = []
for _i in range(55):
    _t, _d, _dead, _hurt = _BASE_TITLES[_i % len(_BASE_TITLES)]
    if _i >= len(_BASE_TITLES):
        _t = f"{_t}（第 {_i} 次续报）"
    TITLES.append((_t, _d, _dead, _hurt))


class StoreSource(BaseCollector):
    """通路 A：写 store。safehoo 那类源的真实形态。"""

    def __init__(self, data_dir=None, key="safehoo"):
        self._key = key
        self.data_dir = data_dir
        self.raw_store = FakeStore()

    def source_name(self):
        return self._key

    def collect(self):
        for i, (title, day, dead, hurt) in enumerate(TITLES):
            url = f"https://www.safehoo.com/NewsSpecial/Build/{self._key}{i}.shtml"
            self.raw_store.add({
                "id": f"rec{self._key}{i}",
                "title": title,
                "content": f"{title}。经调查，事故起因为现场管理缺失、防护措施不到位。" * 4,
                "source_url": url,
                "source_name": self._key,
                "crawl_date": "2026-09-30T02:00:00+00:00",
                "publish_date": day,
                "status": "new",
                "deaths": dead,
                "injuries": hurt,
                "pdf_urls": [url.replace(".shtml", ".pdf")],
            })
        return {"collected": len(TITLES)}


class ReturnSource(BaseCollector):
    """通路 B：返回值。mohurd 那类源的真实形态。"""

    def __init__(self, data_dir=None):
        self.data_dir = data_dir

    def source_name(self):
        return "mohurd"

    def collect(self):
        return [
            RawAccidentText(
                id=f"mohurd-{i}",
                source="mohurd",
                title=f"住建部通报：某地建筑施工事故 {i}",
                raw_text="住房和城乡建设部通报，该起事故造成人员伤亡。" * 5,
                raw_html=f"<html><body><p>通报第 {i} 号</p></body></html>",
                source_url=f"https://www.mohurd.gov.cn/tongbao/{i}.html",
                source_name="住房和城乡建设部",
                fetch_time="2026-09-30T02:05:00+00:00",
                extracted_date=date(2026, 3, 1),
                extracted_city="北京市",
                extracted_province="北京市",
                extracted_deaths=1,
                confidence_overall=0.82,
            )
            for i in range(3)
        ]


def fake_build(key, *, data_dir=None):
    spec = registry.get(key)
    if key == "safehoo":
        return StoreSource(data_dir=data_dir), spec
    if key == "mohurd":
        return ReturnSource(data_dir=data_dir), spec
    raise RuntimeError(f"端到端脚本没为 {key} 准备假采集器")


registry.build = fake_build

# ── 起服务（此时工作库还不存在） ──────────────────────────

app = create_app({"TESTING": False})
from werkzeug.serving import make_server                            # noqa: E402

server = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.6)

# ── 开浏览器 ──────────────────────────────────────────────

from playwright.sync_api import sync_playwright                     # noqa: E402

console_errors, page_errors = [], []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    page.on("console", lambda m: console_errors.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))

    # 源清单每行第一列是 checkbox，但表里还夹着分组标题行（.group-row）
    SRC_PICK = "#srcBody tr:not(.group-row) td.col-pick input"
    RAW_ROW = "#rawBody tr:has(td.col-pick)"

    def src_rows() -> int:
        return page.locator(SRC_PICK).count()

    def raw_rows() -> int:
        return page.locator(RAW_ROW).count()

    def raw_text() -> str:
        return page.inner_text("#rawBody")

    print("\n=== ① 首屏：工作库还没建 ===")
    resp = page.goto(BASE + "/collect", wait_until="networkidle", timeout=30000)
    check("页面 200，不是 500", resp.status == 200, resp.status)
    check("提示条出现（库未建要说出来）",
          page.locator("#dbNotice").is_visible())
    notice_text = page.inner_text("#dbNotice")
    check("提示条有实质内容", len(notice_text.strip()) > 8, notice_text[:60])
    check("源清单照样出 24 行", src_rows() == 24, src_rows())
    check("源计数文案在", "24" in page.inner_text("#srcCount"),
          page.inner_text("#srcCount"))
    check("任务区是空态不是错误页",
          "还没有采集任务" in page.inner_text("#taskList"))
    check("原始数据区是空态（说清是库没建，不是没数据）",
          "工作库未建立" in page.inner_text("#rawPanel"),
          page.inner_text("#rawPanel")[:60])

    print("\n=== ② 建库后重载：提示条该消失 ===")
    sdb.init_db()
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(700)
    check("提示条已隐藏", not page.locator("#dbNotice").is_visible())
    check("源清单仍是 24 行", src_rows() == 24, src_rows())

    print("\n=== ③ 勾选两个源 ===")
    # 按 source_key 精确勾，不能用 `.first`：源清单 24 行按分组排版，第一行是
    # beijing_em 这类**没有准备假采集器**的省站，勾了它那一格直接跑成失败。
    keys = page.locator("#srcBody tr:not(.group-row) .src-key")
    idx = {}
    for i in range(keys.count()):
        idx[keys.nth(i).inner_text().strip()] = i
    check("源清单里有 safehoo 与 mohurd",
          "safehoo" in idx and "mohurd" in idx, sorted(idx)[:6])

    page.locator(SRC_PICK).nth(idx["safehoo"]).check()
    page.wait_for_timeout(200)
    picked_one = page.inner_text("#pickedCount")
    check("勾一个后计数说话", "1" in picked_one, picked_one)

    page.locator(SRC_PICK).nth(idx["mohurd"]).check()
    page.wait_for_timeout(200)
    picked_two = page.inner_text("#pickedCount")
    check("勾两个后计数变 2", "2" in picked_two, picked_two)

    print("\n=== ④ 开始采集 ===")
    # 刻意不填关键词：填了会让关键字过滤把部分记录挡在库外，这一层要验的是
    # 「58 条都进得来」。关键词筛选在第 ⑪ 段用列表上的 #f_q 验。
    page.click("#startBtn")
    page.wait_for_selector("#taskList .task-card", timeout=20000)
    check("任务卡出现", page.locator("#taskList .task-card").count() == 1)
    card_cls = page.get_attribute("#taskList .task-card", "class")
    check("卡片带状态 class", "task-card is-" in card_cls, card_cls)
    check("卡片上是刚勾的两个源",
          page.locator("#taskList .task-sources li").count() == 2,
          page.locator("#taskList .task-sources li").count())

    print("\n=== ⑤ 不点刷新，任务卡自己走完 ===")
    # 采集页只在有活动任务时轮询（setTimeout 递归）。这里刻意不碰「刷新」按钮，
    # 验的就是那条轮询链路是否真的在跑。
    page.wait_for_selector("#taskList .task-card.is-done", timeout=40000)
    totals = page.inner_text("#taskList .task-totals")
    check("状态徽章变成「已完成」", "已完成" in page.inner_text("#taskList"))
    check("totals 写明入库 58 条", "入库 58" in totals, totals)
    check("totals 写明源 2/2", "源 2/2" in totals, totals)
    # 源级徽章用的是 is-ok（`srcStateBadge`：完成 → `badge('完成', 'is-ok')`），
    # 不是卡片的 is-done——两者同名不同义，看代码确认过
    check("两个源都标「完成」",
          page.locator("#taskList .task-sources .badge.is-ok").count() == 2,
          page.locator("#taskList .task-sources .badge.is-ok").count())
    check("完成后卡片上的操作换成「删除」",
          page.locator("#taskList .task-card button",
                       has_text="删除").count() == 1)

    print("\n=== ⑥ 原始数据表：任务跑完自己刷出来 ===")
    # 这一条是本轮修复的回归点。此前轮询到任务结束就停了，没人去取原始数据，
    # 用户看到的是任务卡写着「入库 58」而下方的表空着——接口全对、数据真进了库，
    # 只是没人去取。修法见 collect.js 的 loadTasks（活动任务清空时刷新三块）。
    page.wait_for_selector(RAW_ROW, timeout=15000)
    # 默认每页 50（collect.html 的 selected），58 条正好分两页
    check("没点任何东西，表格自己出来了（通路 A 55 + 通路 B 3，首页 50 行）",
          raw_rows() == 50, raw_rows())
    check("计数文案报总数 58", "58" in page.inner_text("#rawCount"),
          page.inner_text("#rawCount"))
    # 列表里只有标题行、没有正文块：正文那串在列表上找不到（要看得开抽屉）。
    # 注意不能拿它当「预览出现了」的证据——列表列的预览只在**没有标题**时才顶替
    # （collect.js rawRow），这批假数据都有标题，预览本就不该出现。
    check("列表不铺正文（正文要开抽屉才看得到）",
          "经调查，事故起因为现场管理缺失" not in raw_text())
    check("两条通路都在表里",
          "安全管理网" in raw_text() and "住房和城乡建设部" in raw_text())
    check("未判定徽章显示「未判定」", "未判定" in raw_text())

    print("\n=== ⑦ 漏斗 ===")
    funnel = page.inner_text("#funnelPanel")
    check("漏斗区有内容（不是骨架屏）", "骨架" not in funnel, funnel[:50])
    check("漏斗第一段报原始 58", "58" in funnel, funnel[:120])

    print("\n=== ⑧ 点「查看」开抽屉 ===")
    page.locator(RAW_ROW).first.locator("button", has_text="查看").click()
    page.wait_for_selector("#drawer:not([hidden])", timeout=8000)
    # 抽屉的显隐是同步翻的（collect.js openDrawer 先 `hidden = false` 再 fetch），
    # 所以「可见」不等于「内容到了」——等正文块真出现再读。
    page.wait_for_selector("#drawerBody .raw-text", timeout=8000)
    check("抽屉打开", page.locator("#drawer").is_visible())
    body = page.inner_text("#drawerBody")
    # 不拿某一条记录的专属句子当准：首页第一行是哪个源由后端排序决定，
    # 今天排到 safehoo、明天排到 mohurd，断言就会无谓地翻脸。这里判的是
    # 「正文块真有正文」，内容对不对由上面列表那批断言去管。
    raw_body = page.inner_text("#drawerBody .raw-text")
    check("抽屉里有正文（不是一个字）", len(raw_body.strip()) > 50,
          raw_body[:60])
    check("抽屉标题栏在", page.locator("#drawerBody .drawer-title")
          .inner_text().strip() != "")
    check("抽屉里有来源链接", "mohurd" in body or "safehoo" in body
          or "https://" in body, body[:80])
    page.click("#drawerClose")
    page.wait_for_function("() => document.querySelector('#drawer').hidden",
                           timeout=6000)
    check("抽屉能关上", not page.locator("#drawer").is_visible())

    print("\n=== ⑨ 批量裁决：标为不相关，再退回待判 ===")
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.accept("初步排除")))
    page.locator(RAW_ROW).nth(0).locator("input[type=checkbox]").check()
    page.locator(RAW_ROW).nth(1).locator("input[type=checkbox]").check()
    page.wait_for_timeout(300)
    check("批量条浮出", page.locator("#bulkBar").is_visible())
    bulk = page.inner_text("#bulkCount")
    check("批量条报选中 2 条", "2" in bulk, bulk)

    page.click('#bulkBar [data-verdict="rejected"]')
    page.wait_for_timeout(1200)
    check("裁决后批量条收起", not page.locator("#bulkBar").is_visible())
    check("提示条报告更新条数", "已更新 2 条" in page.inner_text("#taskHint"),
          page.inner_text("#taskHint"))
    check("徽章变成「不相关（人工）」", "不相关（人工）" in raw_text())
    check("理由落进备注列", "初步排除" in raw_text())

    # 「退回待判」在后端是**撤销判定**（collect.py:436 把 gate_verdict 置 NULL），
    # 不是记一个「待人工」的结论。所以退回之后徽章应当回到「未判定」，
    # 备注也该被清掉。这里先制造出非未判定的状态再退回，才验得到东西——
    # 直接对没判定过的行点退回，断言「显示未判定」是恒真的。
    page.locator(RAW_ROW).nth(0).locator("input[type=checkbox]").check()
    page.locator(RAW_ROW).nth(1).locator("input[type=checkbox]").check()
    page.wait_for_timeout(300)
    page.click('#bulkBar [data-verdict="review"]')
    page.wait_for_timeout(1200)
    check("退回不弹理由框（撤销没什么可写的）", len(dialogs) == 1, dialogs[:2])
    # 首页 50 行此刻应当全是「未判定」，且「不相关」一个不剩
    check("退回后首页整页回到「未判定」",
          raw_text().count("未判定") == 50 and "不相关" not in raw_text(),
          raw_text().count("未判定"))
    check("退回后备注被清掉", "初步排除" not in raw_text())

    print("\n=== ⑩ 批量裁决（标为相关，会弹 prompt） ===")
    # 另一条路：标为相关要先问理由。playwright 默认会把 dialog 关掉，
    # 那样 gate() 拿到 null 直接 return——页面看起来「点了没反应」。
    page.once("dialog", lambda d: (dialogs.append(d.message), d.accept("闸门复核")))
    page.locator(RAW_ROW).nth(0).locator("input[type=checkbox]").check()
    page.wait_for_timeout(300)
    page.click('#bulkBar [data-verdict="confirmed"]')
    page.wait_for_timeout(1200)
    check("弹了理由框并拿到输入", len(dialogs) == 2, dialogs[1:])
    check("徽章变成「相关（人工）」", "相关（人工）" in raw_text())
    check("漏斗跟着刷新（不再是初始那行）",
          page.inner_text("#funnelPanel").strip() != "")

    print("\n=== ⑪ 筛选与重置 ===")
    # 筛选是**表单 submit**（collect.js `$('rawFilters').addEventListener('submit')`），
    # 改控件的值不会自己查一次——必须点「查询」。
    def query():
        page.click('#rawFilters button[type="submit"]')
        page.wait_for_timeout(800)

    page.select_option("#f_source", "mohurd")
    query()
    check("按源筛出 mohurd 的 3 条", raw_rows() == 3, raw_rows())

    page.click("#rawReset")
    page.wait_for_timeout(800)
    check("重置回 58 条", raw_rows() == 50 and "58" in page.inner_text("#rawCount"),
          page.inner_text("#rawCount"))

    page.select_option("#f_verdict", "confirmed")
    query()
    check("按「相关」筛出刚才那 1 条", raw_rows() == 1, raw_rows())

    page.click("#rawReset")
    page.wait_for_timeout(800)
    page.fill("#f_q", "住建部通报")
    query()
    check("关键词「住建部通报」筛出 3 条", raw_rows() == 3, raw_rows())

    page.click("#rawReset")
    page.wait_for_timeout(800)
    check("重置后回到首页 50 行", raw_rows() == 50, raw_rows())

    print("\n=== ⑫ 分页 ===")
    # 每页最小 50（collect.html 没有更小的选项），所以造假数据时特意凑了 58 条
    # ——少于 51 条 `renderPager` 直接 return（collect.js:616），翻页按钮不出现。
    pager = page.inner_text("#rawPager")
    check("分页器报第 1 / 2 页", "1 / 2" in pager, pager)
    page.locator("#rawPager button", has_text="下一页").first.click()
    page.wait_for_timeout(800)
    check("第 2 页剩 8 行", raw_rows() == 8, raw_rows())
    check("分页器跟着走到第 2 页", "2 / 2" in page.inner_text("#rawPager"),
          page.inner_text("#rawPager"))
    page.locator("#rawPager button", has_text="上一页").first.click()
    page.wait_for_timeout(800)
    check("点回上一页又是 50 行", raw_rows() == 50, raw_rows())

    print("\n=== ⑬ 脏输入与边界 ===")
    page.evaluate("() => { window.location.hash = '#page=999'; }")
    page.wait_for_timeout(900)
    check("越界页码不白屏（表还在）",
          page.locator("#rawGrid").is_visible())
    check("控制台干干净净", not console_errors, console_errors[:3])
    check("没有未捕获的 JS 异常", not page_errors, page_errors[:3])

    browser.close()

server.shutdown()

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  -", f)
    print("控制台错误：", console_errors[:5])
    print("JS 异常：", page_errors[:5])
    sys.exit(1)
print("✓ 采集页第三层（headless 真点击）全过。")
