"""M3 第三层验证：headless Chrome 真点击。

前两层（Flask test client、真 HTTP + urllib）验的是接口对不对；这一层验的是
**人在浏览器里点得通**——按钮的 selector 对不对、事件有没有绑上、点击后 DOM
有没有真的变、控制台有没有报错。前面两层全绿也可能栽在这里（比如 POST 被
dom.js 的 fetchJson 吃掉当 GET 发出去，接口层完全看不出来）。

假 pipeline 与 e2e_extract.py 同源，但库文件与端口独立，互不干扰。
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 每次跑另起临时目录：这几个脚本会真建库、真并入、真写备份，复用同一目录
# 会让上一轮的库和备份混进这一轮。目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_"))
DB = TMP / "e2e_click_m3.db"
# 主库只被**读**（取 CAICS 类名与枚举口径），不被写。路径仍要显式给出：
# worktree 里的 data/ 没有 caiems_v2.db，默认成相对路径会让提取静默拿到
# 空的分类体系——不报错，只是所有编码都判不出来。
MAIN_DB = os.environ.get("CAIEMS_MAIN_DB",
                         str(ROOT.parents[2] / "data" / "caiems_v2.db"))
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()
os.environ["CAIEMS_STAGING_DB"] = str(DB)
os.environ["CAIEMS_DB"] = MAIN_DB

from construction_agent.collector.llm_classifier import (  # noqa: E402
    ClassificationResult, ClassificationVerdict, TextType, TextTypeResult,
)
from construction_agent.extraction import staging_executor as ex  # noqa: E402
from construction_agent.extraction.caics_pipeline import (  # noqa: E402
    STATUS_COMPLETED, PipelineOutcome, StageRecord,
)
from construction_agent.extraction.step1_basic_extractor import Step1Result  # noqa: E402
from construction_agent.extraction.step2_caics_encoder import (  # noqa: E402
    CauseItem, Step2Result,
)
from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.factory import create_app  # noqa: E402

PORT = 5094
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


class FakePipe:
    class _C:
        _model = "fake-model"

    classifier = _C()

    def __init__(self):
        self.extractor = self._C()
        self.encoder = self._C()

    def run_once(self, title, text, publish_date="", source_id="", source_name=""):
        out = PipelineOutcome(source_id=source_id, title=title,
                              publish_date=publish_date)
        # 跑得慢一点，好让「任务卡正在跑」这个中间态被点到
        time.sleep(0.8)
        tt = TextTypeResult(text_type=TextType.SINGLE, confidence="high",
                            reason="单起事故报道")
        tt.tokens_in, tt.tokens_out = 300, 60
        cl = ClassificationResult(
            item_id=source_id, item_title=title, is_construction=True,
            verdict=ClassificationVerdict.CONFIRMED_CONSTRUCTION,
            confidence="high", reason="建筑施工事故")
        cl.tokens_in, cl.tokens_out = 700, 90
        b = Step1Result(
            title=title, date=publish_date, location="江苏省", city="南京市",
            accident_type="坍塌", severity="较大事故", deaths=3, injuries=2,
            direct_cause="模板支撑体系失稳", project_type="房屋建筑",
            construction_phase="主体结构",
            summary="某工地浇筑时支撑体系失稳坍塌。",
            contractor="某建设集团有限公司")
        b.tokens_in, b.tokens_out = 8000, 500
        c = Step2Result(
            primary_cause="模板支撑体系失稳导致坍塌",
            cause_chain="M1(监管缺失)→F2(支撑失稳)→H1(违规操作)",
            causes=[
                CauseItem("M1", "现场监管缺失", "监理单位未对违规堆放物料进行制止",
                          "监理未履行监督职责，表明现场监管环节失效"),
                CauseItem("F2", "支撑体系失稳", "支撑立杆间距超方案要求",
                          "原文直接写到立杆间距，属直接引用"),
                CauseItem("H1", "违反操作规程", "原文未直接提及，但从整体坍塌可推断",
                          "施工方违反方案要求，属违规操作"),
            ])
        c.tokens_in, c.tokens_out = 15000, 900
        out.text_type, out.classification, out.basic, out.causes = tt, cl, b, c
        out.records += [StageRecord("S1", True, 900, "ok"),
                        StageRecord("S2", True, 500, "ok"),
                        StageRecord("S3", True, 17000, "ok"),
                        StageRecord("S4", True, 24000, "ok")]
        out.status = STATUS_COMPLETED
        return out


ex._pipeline = lambda: FakePipe()

# ── 建工作库 + 造 3 条已确认原文 ─────────────────────────────
sdb.init_db()
with sdb.write_tx() as con:
    con.execute("INSERT INTO collect_sources (source_key, display_name, "
                "source_group, track) VALUES ('safehoo','安全管理网','national','network')")
    for i in ("r1", "r2", "r3"):
        con.execute(
            "INSERT INTO raw_accidents (raw_id, source_key, title, content, "
            "source_url, fetch_time, url_hash, gate_verdict, status) "
            "VALUES (?,?,?,?,?,?,?,?, 'gated')",
            (i, "safehoo", f"江苏省南京市某工地坍塌事故（{i}）", "正文" * 80,
             f"https://x/{i}", "2026-09-30T00:00:00Z", f"h{i}", "confirmed"))

app = create_app({"TESTING": False})
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", PORT, app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(0.6)

# ── 开浏览器 ──────────────────────────────────────────────
from playwright.sync_api import sync_playwright  # noqa: E402

console_errors, page_errors = [], []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: console_errors.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))

    def wait_hidden(sel, timeout=6000):
        """wait_for_selector(sel) 默认要求元素 visible，而 [hidden] 恰恰永远
        不可见——所以判「关掉了」得看 hidden 属性本身，不能用选择器等。"""
        page.wait_for_function("s => !!document.querySelector(s).hidden",
                               arg=sel, timeout=timeout)

    def wait_visible(sel, timeout=6000):
        # 判「露出来了」交给 playwright 自己的可见性判定（看 bounding box），
        # 别用 offsetParent——弹层是 position:fixed，fixed 元素的 offsetParent
        # 恒为 null，拿它当判据会把已经打开的弹层判成没开
        page.wait_for_selector(sel + ":not([hidden])", timeout=timeout)

    print("\n=== ① 首屏：页面能开、数据能拉 ===")
    resp = page.goto(BASE + "/extract", wait_until="networkidle")
    check("HTTP 200", resp.status == 200, resp.status)
    check("导航高亮在「在线提取」",
          page.inner_text("nav a.is-active").strip() == "在线提取",
          page.inner_text("nav a.is-active").strip())

    # 理论版本区块由 /api/extract/taxonomy 填充
    page.wait_for_selector("#taxChips .chip", timeout=8000)
    chips = page.locator("#taxChips .chip").all_inner_texts()
    check("理论版本 chips 渲染出来", len(chips) >= 4, chips[:5])
    check("版本号显示 caics_v1.0",
          any("caics_v1.0" in c for c in chips), chips)

    page.click("#taxToggle")
    page.wait_for_selector("#taxBody tr", timeout=5000)
    rows = page.locator("#taxBody tr").count()
    check("展开后 19 类表列出 19 行", rows == 19, rows)
    layer_badges = page.locator("#taxBody .layer-cell").count()
    check("层标记每层一个（共 5 层）", layer_badges == 5, layer_badges)
    span_sum = page.evaluate(
        "() => Array.from(document.querySelectorAll('#taxBody .layer-cell'))"
        ".reduce((a, c) => a + Number(c.getAttribute('rowspan')), 0)")
    check("各层 rowspan 之和 = 19（19 类一个不漏）", span_sum == 19, span_sum)
    layers = page.locator("#taxBody .layer-cell").all_inner_texts()
    check("五层顺序 M/F/T/H/C",
          [x[0] for x in layers] == ["M", "F", "T", "H", "C"], layers)
    page.click("#taxToggle")

    print("\n=== ② 待提取队列 ===")
    page.wait_for_selector("#pendBody tr", timeout=8000)
    pend_rows = page.locator("#pendBody tr").count()
    check("3 条待提取原文都在列表里", pend_rows == 3, pend_rows)
    check("队列计数不是占位符",
          "3" in page.inner_text("#pendCount"), page.inner_text("#pendCount"))

    print("\n=== ③ 勾选 → 选择条浮出 ===")
    check("未勾选时选择条是隐藏的", page.locator("#bulkBar").is_hidden())
    page.locator("#pendBody tr").nth(0).click()
    page.locator("#pendBody tr").nth(1).click()
    page.wait_for_function("() => !document.querySelector('#bulkBar').hidden",
                           timeout=3000)
    check("点行本身就能勾上（不用去够小方框）", page.locator(
        "#pendBody tr.is-picked").count() == 2,
        page.locator("#pendBody tr.is-picked").count())
    check("选择条报「已选 2 条」",
          "2" in page.inner_text("#bulkCount"), page.inner_text("#bulkCount"))
    check("复选框跟着行状态同步",
          page.locator("#pendBody tr").nth(1).locator(
              "input[type=checkbox]").is_checked())
    # 再点一次应取消勾选
    page.locator("#pendBody tr").nth(1).click()
    page.wait_for_timeout(150)
    check("再点一次取消勾选", page.locator("#pendBody tr.is-picked").count() == 1)
    page.locator("#pendBody tr").nth(1).click()
    page.wait_for_timeout(150)

    print("\n=== ④ 建任务 → 成本确认弹层 ===")
    page.click("#bulkPlan")
    wait_visible("#costModal")
    # 弹出瞬间是「正在估算…」，按钮 disabled；等真数据回来再断言
    page.wait_for_function(
        "() => !/正在估算/.test(document.querySelector('#costBody').innerText)",
        timeout=8000)
    body = page.inner_text("#costBody")
    check("弹层弹出且带预估", page.locator("#costModal").is_visible())
    check("弹层写明了条数", "2" in body, body[:160].replace("\n", " | "))
    check("弹层给了 token 预估", "token" in body.lower(), body[:200])
    check("弹层说明非结算依据",
          "非结算依据" in body or "估算" in body, body[:200])
    check("估算到位后按钮解禁", not page.locator("#costRun").is_disabled())

    print("\n=== ⑤ 取消弹层：一毛钱不花 ===")
    page.click("#costCancel")
    wait_hidden("#costModal")
    check("取消后弹层关闭", page.locator("#costModal").is_hidden())
    check("取消后一条任务都没建",
          page.locator("#taskList .task-card").count() == 0,
          page.locator("#taskList .task-card").count())
    check("取消后暂存区仍空",
          sdb.scalar("SELECT COUNT(*) FROM staging_accidents") == 0)

    print("\n=== ⑥ 确认执行 → 任务卡出现并走完 ===")
    page.click("#bulkPlan")
    wait_visible("#costModal")
    page.wait_for_function(
        "() => !document.querySelector('#costRun').disabled", timeout=8000)
    page.click("#costRun")
    wait_hidden("#costModal")
    page.wait_for_selector("#taskList .task-card", timeout=8000)
    check("任务卡出现", page.locator("#taskList .task-card").count() == 1)
    check("任务卡有状态徽章",
          page.locator("#taskList .task-card .badge").count() >= 1,
          page.inner_text("#taskList .task-card")[:80].replace("\n", " | "))
    check("任务卡有进度条",
          page.locator("#taskList .task-card .task-progress-fill").count() == 1)

    # 等它跑完：假 pipeline 每条 0.8s，两条 1.6s
    page.wait_for_function(
        "() => { const c = document.querySelector('#taskList .task-card');"
        "return c && c.className.includes('is-done'); }", timeout=30000)
    card_txt = page.inner_text("#taskList .task-card")
    check("跑完后任务卡进 done 态", "is-done" in page.get_attribute(
        "#taskList .task-card", "class"), page.get_attribute(
        "#taskList .task-card", "class"))
    check("任务卡显示 2/2", "2/2" in card_txt or "100%" in card_txt, card_txt[:120].replace("\n", " | "))
    check("勾选条已收起（任务提交后不再挂着）",
          page.locator("#bulkBar").is_hidden())

    print("\n=== ⑦ 暂存结果表 ===")
    # 跑完任务后结果表自己会刷出来，不需要用户手点「查询」
    page.wait_for_function(
        "() => document.querySelectorAll('#resBody tr').length === 2"
        " && document.querySelector('#resBody tr td').getAttribute('colspan') === null",
        timeout=12000)
    res_rows = page.locator("#resBody tr").count()
    check("任务跑完结果表自动刷出 2 行（不用手点查询）", res_rows == 2, res_rows)
    headers = page.locator("#resultsPanel thead th").all_inner_texts()
    check("表头 11 列（含入库时间）", len(headers) == 11, headers)
    check("表头有「入库时间」", "入库时间" in headers, headers)
    first_row_cells = page.locator("#resBody tr").nth(0).locator("td")
    check("每行实打实 11 个单元格", first_row_cells.count() == 11,
          first_row_cells.count())
    check("入库时间有值（不是空列）",
          first_row_cells.nth(9).inner_text().strip() not in ("", "—"),
          first_row_cells.nth(9).inner_text())
    check("编码数显示为 3", "3" in first_row_cells.nth(6).inner_text(),
          first_row_cells.nth(6).inner_text())
    check("S3/S4 耗时显示为秒",
          first_row_cells.nth(7).inner_text().endswith("s"),
          first_row_cells.nth(7).inner_text())

    print("\n=== ⑧ 点开抽屉看证据链 ===")
    page.locator("#resBody tr").nth(0).click()
    wait_visible("#drawer")
    page.wait_for_selector("#drawerBody .cause-item", timeout=8000)
    drawer_txt = page.inner_text("#drawerBody")
    check("抽屉打开", page.locator("#drawer").is_visible())
    check("抽屉里有闸门判定",
          "闸门" in drawer_txt or "类型" in drawer_txt, drawer_txt[:150].replace("\n", " | "))
    check("抽屉里有三级证据标签",
          "直引" in drawer_txt, [l for l in ("直引", "推断", "弱推断")
                                 if l in drawer_txt])
    check("证据链 3 条都在", page.locator("#drawerBody .cause-item").count() == 3,
          page.locator("#drawerBody .cause-item").count())
    codes = page.locator("#drawerBody .cause-name").all_inner_texts()
    check("证据链按层序排（M → F → H）",
          codes == ["现场监管缺失", "支撑体系失稳", "违反操作规程"], codes)
    chips = page.locator("#drawerBody .cause-item .chip").all_inner_texts()
    check("每条都带层级 chip", sum(1 for c in chips
                                   if c[:2] in ("M1", "F2", "H1")) >= 3, chips[:6])
    check("每条都带五层配色 class",
          page.locator("#drawerBody .cause-item .chip[class*='layer-']").count() >= 3)
    check("原文一并给出可对照",
          "正文" in drawer_txt, drawer_txt[-200:].replace("\n", " | "))

    # 抽屉里的「重跑」是覆盖式的
    page.click("#rerunBtn")
    page.wait_for_timeout(3000)
    check("重跑后抽屉仍可用（没崩）", page.locator("#drawerBody").is_visible())
    check("重跑是覆盖不是新增（结果仍 2 行）",
          page.locator("#resBody tr").count() == 2,
          page.locator("#resBody tr").count())

    page.click("#drawerClose")
    wait_hidden("#drawer")
    check("抽屉能关上", page.locator("#drawer").is_hidden())

    print("\n=== ⑨ 筛选与分页 ===")
    page.select_option("#f_text", "single")
    page.click("#resFilters button[type=submit]")
    page.wait_for_timeout(900)
    check("按「单个事故」筛出 2 条",
          page.locator("#resBody tr").count() == 2,
          page.locator("#resBody tr").count())

    page.select_option("#f_text", "regulatory")
    page.click("#resFilters button[type=submit]")
    page.wait_for_timeout(900)
    txt = page.inner_text("#resBody")
    check("按「通报文件」筛空并给出空态文案",
          "没有记录" in txt or "还没有" in txt, txt[:80])

    page.click("#f_q")
    page.fill("#f_q", "坍塌")
    page.select_option("#f_text", "")
    page.click("#resFilters button[type=submit]")
    page.wait_for_timeout(900)
    check("关键词「坍塌」命中 2 条",
          page.locator("#resBody tr").count() == 2,
          page.locator("#resBody tr").count())

    page.click("#resReset")
    page.wait_for_timeout(900)
    check("重置回全量 2 条", page.locator("#resBody tr").count() == 2,
          page.locator("#resBody tr").count())

    print("\n=== ⑩ 脏输入与边界 ===")
    # 直接在输入框里塞非法 text_type，后端应忽略而不是 500
    page.evaluate("""() => {
      const f = document.querySelector('#resFilters');
      const i = document.createElement('input');
      i.type='hidden'; i.id='f_text'; i.value='bogus';
      const old = document.getElementById('f_text'); old.remove();
      f.appendChild(i);
    }""")
    page.click("#resFilters button[type=submit]")
    page.wait_for_timeout(900)
    check("非法筛选值不报错，返回全量",
          page.locator("#resBody tr").count() == 2,
          page.locator("#resBody tr").count())
    check("控制台干干净净", not console_errors, console_errors[:3])
    check("没有未捕获的 JS 异常", not page_errors, page_errors[:3])

    page.screenshot(path=str(TMP / "m3_extract.png"), full_page=True)
    browser.close()

server.shutdown()

print("\n" + "=" * 52)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print("  -", f)
if console_errors:
    print("控制台错误：", console_errors[:5])
if page_errors:
    print("JS 异常：", page_errors[:5])
sys.exit(1 if FAIL else 0)
