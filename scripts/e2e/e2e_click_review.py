"""M4（阶段③审核）第三层验证：headless Chrome 真点击。

前两层验的是接口；这一层验的是**人在浏览器里点得通**：选择器的 class 对不对、
事件有没有绑上、点完 DOM 有没有真变、控制台干不干净。

对审核页这层尤其不能省——它的三处安全设计（批量通过只放行零告警、并入两段式、
退回必填原因）全部是**前端先行拦截 + 后端兜底**。后端那半第一层已验过，
前端这半只有真点才知道拦没拦住。

库用独立的 staging + final（并入会真写，不能指向主库）。
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 每次跑都另起一个临时目录：这几个脚本会真建库、真并入、真写备份，
# 复用同一个目录会让上一轮的库和备份混进这一轮，出了错也说不清是哪次留下的。
# 目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_e2e_"))
STAGING = TMP / "e2e_click_review_staging.db"
FINAL = TMP / "e2e_click_review_final.db"
for suffix in ("", "-wal", "-shm"):
    for base in (STAGING, FINAL):
        p = Path(str(base) + suffix)
        if p.exists():
            p.unlink()
os.environ["CAIEMS_STAGING_DB"] = str(STAGING)
os.environ["CAIEMS_DB"] = str(FINAL)

_spec = importlib.util.spec_from_file_location(
    "create_db_v2", ROOT / "scripts" / "create_db_v2.py")
create_db_v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create_db_v2)

from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.factory import create_app  # noqa: E402

PORT = 5096
BASE = f"http://127.0.0.1:{PORT}"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name
          + ("  " + str(extra) if extra else ""))


# ── 建库 ──────────────────────────────────────────────────

sdb.init_db()
con = sqlite3.connect(FINAL)
con.executescript(create_db_v2.SCHEMA)
con.commit()
con.close()

with sdb.write_tx() as c:
    c.execute("INSERT OR IGNORE INTO collect_sources (source_key, display_name, "
              "source_group, track) VALUES ('safehoo','安全管理网','national','network')")


def seed(stage_id, title, causes, **fields):
    row = {
        "title": title, "date": "2024-05-06", "location": "江苏省",
        "city": "南京市", "accident_type": "高处坠落", "severity": "一般事故",
        "deaths": 2, "injuries": 1, "project_type": "房屋建筑",
        "report_type": "事故调查报告", "summary": "工人未系安全带坠落。",
        "text_type": "single", "relevance_verdict": "construction",
        "relevance_confidence": "high",
    }
    row.update(fields)
    raw_id = "raw" + stage_id
    with sdb.write_tx() as c:
        c.execute("INSERT INTO raw_accidents (raw_id, source_key, source_url, "
                  "fetch_time, url_hash, title, content) VALUES (?,?,?,?,?,?,?)",
                  (raw_id, "safehoo", f"https://example.com/{stage_id}",
                   "2026-10-01T10:00:00", "h" + stage_id, title,
                   "某某建设集团承建的项目，工人在六层外脚手架作业时未系安全带，"
                   "失足坠落至地面。" * 6))
        cols = ["stage_id", "raw_id", "created_at", "review_status",
                "split_index"] + list(row.keys())
        vals = [stage_id, raw_id, "2026-10-01T10:00:00", "pending", 0] \
            + list(row.values())
        c.execute(f"INSERT INTO staging_accidents ({','.join(cols)}) "
                  f"VALUES ({','.join('?' * len(cols))})", vals)
        for i, (code, name) in enumerate(causes):
            c.execute("INSERT INTO staging_causes (stage_id, code, name, evidence, "
                      "inference, evidence_quality, sort_order) VALUES (?,?,?,?,?,?,?)",
                      (stage_id, code, name, "原文写到" + name,
                       "因为原文提到" + name + "，故归入该编码。", "direct_quote", i + 1))


CLEAN = [("M1", "现场监管缺失"), ("F1", "防护设施缺失"), ("H1", "未使用防护用品")]
seed("p1", "江苏省南京市某工地高处坠落事故", CLEAN)
seed("p2", "浙江省杭州市某项目脚手架坍塌事故", CLEAN[:2],
     location="浙江省", city="杭州市", accident_type="坍塌", deaths=3)
seed("w1", "", CLEAN[:1])          # 空标题 → empty_title
seed("w2", "某工地物体打击事故", [])  # 无编码 → no_causes

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
    # 回滚按钮会弹原生 confirm，不接管就会一直卡住
    page.on("dialog", lambda d: d.accept())

    def wait_visible(sel, timeout=8000):
        page.wait_for_selector(sel + ":not([hidden])", timeout=timeout)

    def wait_hidden(sel, timeout=8000):
        page.wait_for_function("s => !!document.querySelector(s).hidden",
                               arg=sel, timeout=timeout)

    print("\n=== ① 首屏 ===")
    resp = page.goto(BASE + "/review", wait_until="networkidle")
    check("HTTP 200", resp.status == 200, resp.status)
    check("导航高亮在「暂存审核」",
          page.inner_text("nav a.is-active").strip() == "暂存审核",
          page.inner_text("nav a.is-active").strip())
    page.wait_for_selector("#funnelRow .stat", timeout=8000)
    stats = page.locator("#funnelRow .stat").all_inner_texts()
    check("漏斗四段渲染出来", len(stats) == 4, stats)
    check("漏斗首段是暂存总数 4", "4" in stats[0], stats[0])
    check("漏斗末段有强调态（已并入）",
          page.locator("#funnelRow .stat.is-accent").count() == 1)
    check("状态摘要四态齐全",
          all(k in page.inner_text("#funnelMeta")
              for k in ("待审核", "已通过", "已修改", "已退回")),
          page.inner_text("#funnelMeta"))

    print("\n=== ② 告警 chips 与队列 ===")
    page.wait_for_selector("#warnChips .chip", timeout=8000)
    chips = page.locator("#warnChips .chip").all_inner_texts()
    check("chips 首位是「全部」", chips[0].startswith("全部"), chips)
    check("两个阻断级告警各一个 chip",
          sum(1 for c in chips if "标题" in c or "编码" in c) >= 2, chips)
    check("「全部」默认点亮", page.locator("#warnChips .chip.is-on").count() == 1)

    page.wait_for_selector("#qBody tr", timeout=8000)
    check("队列 4 条都在", page.locator("#qBody tr").count() == 4,
          page.locator("#qBody tr").count())
    headers = page.locator("#queuePanel thead th").all_inner_texts()
    check("表头 10 列", len(headers) == 10, headers)
    check("每行实打实 10 个单元格",
          page.locator("#qBody tr").nth(0).locator("td").count() == 10,
          page.locator("#qBody tr").nth(0).locator("td").count())
    check("空标题那条显示占位而不是空白",
          "（无标题）" in page.inner_text("#qBody"),
          page.inner_text("#qBody")[:120].replace("\n", " | "))
    check("零告警行打的是绿标",
          page.locator("#qBody .badge.is-ok").count() >= 2,
          page.locator("#qBody .badge.is-ok").count())
    check("阻断级告警打的是红标",
          page.locator("#qBody .warn-cell .badge.sev-1").count() == 2,
          page.locator("#qBody .warn-cell .badge.sev-1").count())
    check("分页信息显示共 4 条",
          "4" in page.inner_text("#qPager"), page.inner_text("#qPager"))

    print("\n=== ③ 筛选 ===")
    page.select_option("#f_warn", "none")
    page.click("#qFilters button[type=submit]")
    page.wait_for_timeout(700)
    check("零告警筛出 2 条", page.locator("#qBody tr").count() == 2,
          page.locator("#qBody tr").count())
    check("「跳到零告警」按钮变成「回到全部」",
          page.inner_text("#qClean") == "回到全部", page.inner_text("#qClean"))
    page.click("#qClean")
    page.wait_for_timeout(700)
    check("再点回到全部 4 条", page.locator("#qBody tr").count() == 4,
          page.locator("#qBody tr").count())

    page.fill("#f_q", "坍塌")
    page.click("#qFilters button[type=submit]")
    page.wait_for_timeout(700)
    check("关键词「坍塌」命中 1 条", page.locator("#qBody tr").count() == 1,
          page.locator("#qBody tr").count())
    page.click("#qReset")
    page.wait_for_timeout(700)
    check("重置回 4 条", page.locator("#qBody tr").count() == 4,
          page.locator("#qBody tr").count())

    print("\n=== ④ 勾选与选择条 ===")
    check("未勾选时选择条隐藏", page.locator("#bulkBar").is_hidden())
    page.locator("#qBody tr").nth(0).click()
    page.locator("#qBody tr").nth(1).click()
    page.wait_for_function("() => !document.querySelector('#bulkBar').hidden",
                           timeout=3000)
    check("点行本身就能勾上", page.locator("#qBody tr.is-picked").count() == 2)
    check("选择条报「已选 2 条」",
          "2" in page.inner_text("#bulkCount"), page.inner_text("#bulkCount"))
    check("提示条说明其中有几条零告警",
          "零告警" in page.inner_text("#bulkHint"),
          page.inner_text("#bulkHint"))
    page.locator("#qBody tr").nth(1).click()
    page.wait_for_timeout(200)
    check("再点一次取消勾选", page.locator("#qBody tr.is-picked").count() == 1)
    page.locator("#qBody tr").nth(1).click()
    page.wait_for_timeout(200)

    # 「全选本页 / 清空」走的是合成 change 事件，与真实点击方框不是一条路径
    page.click("#qAll")
    page.wait_for_timeout(250)
    check("全选本页把 4 条都勾上",
          page.locator("#qBody tr.is-picked").count() == 4,
          page.locator("#qBody tr.is-picked").count())
    page.click("#qAll")
    page.wait_for_timeout(250)
    check("再点一次全选不会把已选的翻掉",
          page.locator("#qBody tr.is-picked").count() == 4,
          page.locator("#qBody tr.is-picked").count())
    page.click("#qNone")
    page.wait_for_timeout(250)
    check("清空把 4 条都取消（不是反过来全勾上）",
          page.locator("#qBody tr.is-picked").count() == 0,
          page.locator("#qBody tr.is-picked").count())
    check("清空后选择条收起", page.locator("#bulkBar").is_hidden())

    print("\n=== ⑤ 抽屉：字段编辑 ===")
    page.locator("#qBody tr").nth(0).locator(".title-main").click()
    wait_visible("#drawer")
    page.wait_for_selector("#drawerBody .edit-box", timeout=8000)
    check("抽屉打开", page.locator("#drawer").is_visible())
    check("抽屉标题是事故标题",
          page.inner_text("#drawerTitle") == "江苏省南京市某工地高处坠落事故",
          page.inner_text("#drawerTitle"))
    check("31 个字段输入框平铺出来",
          page.locator("#drawerBody input[data-field]").count() == 31,
          page.locator("#drawerBody input[data-field]").count())
    check("字段带中文标签",
          "省（原始）" in page.inner_text("#drawerBody"))
    # 体裁给中文（审核人不该被逼着认 single/regulatory），相关性保留来源码原值
    check("闸门区体裁显示中文", "单个事故" in page.inner_text("#drawerBody"),
          page.inner_text("#drawerBody")[:200].replace("\n", " | "))
    check("闸门区给出相关性判定", "construction" in page.inner_text("#drawerBody"))
    # 原文在折叠的 <details> 里，inner_text 只算可见文本——先展开再断言
    page.click("#drawerBody .raw-box summary")
    page.wait_for_timeout(250)
    check("原文一并给出可对照",
          "未系安全带" in page.inner_text("#drawerBody"),
          page.inner_text("#drawerBody")[-160:].replace("\n", " | "))

    print("\n=== ⑥ 致因树 ===")
    page.wait_for_selector("#causeList .cause-row", timeout=8000)
    check("3 条编码都在", page.locator("#causeList .cause-row").count() == 3,
          page.locator("#causeList .cause-row").count())
    layers = page.locator("#causeList .cause-layer").all_inner_texts()
    check("按层分组且层序 M→F→H",
          [x[0] for x in layers] == ["M", "F", "H"], layers)
    check("层标题带中文名", "管理根基" in layers[0], layers[0])
    check("每条编码带证据等级下拉",
          page.locator("#causeList .ev-select").count() == 3)
    check("每条编码带证据与推断两个输入框",
          page.locator("#causeList .cause-row").nth(0).locator(
              "textarea.ev-text").count() == 2)
    check("编辑头报 3 条", "3 条" in page.inner_text(".cause-edit-box .edit-head"),
          page.inner_text(".cause-edit-box .edit-head"))

    page.locator("#causeList .cause-row").nth(2).locator("button").click()
    page.wait_for_timeout(250)
    check("删掉一条后剩 2 条", page.locator("#causeList .cause-row").count() == 2)
    check("计数跟着改成 2 条",
          "2 条" in page.inner_text(".cause-edit-box .edit-head"),
          page.inner_text(".cause-edit-box .edit-head"))

    print("\n=== ⑦ 退回必须先填原因（前端拦截）===")
    page.click("#actReject")
    page.wait_for_timeout(300)
    msg = page.inner_text("#drawerMsg")
    check("没填原因被前端拦下", "必须填写原因" in msg, msg)
    check("被拦下时抽屉不关、状态没变",
          page.locator("#drawer").is_visible()
          and sdb.scalar("SELECT review_status FROM staging_accidents "
                         "WHERE stage_id='p1'") == "pending")

    print("\n=== ⑧ 保存修改 ===")
    page.fill("#fld_city", "无锡市")
    page.fill("#fld_deaths", "5")
    page.click("#actSave")
    page.wait_for_function(
        "() => { const m = document.querySelector('#drawerMsg');"
        "return m && !m.hidden && /已保存/.test(m.innerText); }", timeout=8000)
    check("保存有回执", "已保存" in page.inner_text("#drawerMsg"),
          page.inner_text("#drawerMsg"))
    check("回执列出改了哪几个字段",
          "city" in page.inner_text("#drawerMsg")
          or "无锡" in page.inner_text("#drawerMsg"),
          page.inner_text("#drawerMsg"))
    row = sdb.query_one("SELECT city, deaths, review_status FROM staging_accidents "
                        "WHERE stage_id='p1'")
    check("改动真落库",
          row["city"] == "无锡市" and row["deaths"] == 5
          and row["review_status"] == "edited", dict(row))
    check("没碰过的字段没被一起覆盖（原文还在）",
          bool(sdb.scalar("SELECT summary FROM staging_accidents "
                          "WHERE stage_id='p1'")))
    cs = sdb.query("SELECT code FROM staging_causes WHERE stage_id='p1' "
                   "ORDER BY sort_order")
    check("致因树删掉的那条也真删了",
          [c["code"] for c in cs] == ["M1", "F1"], cs)

    page.click("#drawerClose")
    wait_hidden("#drawer")
    check("抽屉能关上", page.locator("#drawer").is_hidden())
    # 保存成功后会延迟 350ms 回读一次。用户若在这个窗口里点了关闭，
    # 抽屉不能被程序自己弹回来——那是跟人抢控制权。
    page.wait_for_timeout(700)
    check("回读不会把已关掉的抽屉重新弹开",
          page.locator("#drawer").is_hidden())

    print("\n=== ⑨ 批量通过：有告警的必须被拦下并说明 ===")
    # 上一步把 p1 保存成了 edited，它已掉出 pending 队列，
    # 此刻列表是 [p2 干净, w1 空标题, w2 无编码] → 应当只放行 1 条
    check("队列剩 3 条 pending", page.locator("#qBody tr").count() == 3,
          page.locator("#qBody tr").count())
    page.locator("#qBody tr").nth(0).click()
    page.locator("#qBody tr").nth(1).click()
    page.locator("#qBody tr").nth(2).click()
    page.wait_for_timeout(250)
    check("三条都勾上了", page.locator("#qBody tr.is-picked").count() == 3,
          page.locator("#qBody tr.is-picked").count())
    page.click("#bulkApprove")
    page.wait_for_function(
        "() => /通过 \\d+\\//.test(document.querySelector('#dbNotice').innerText)",
        timeout=8000)
    n = page.inner_text("#dbNotice")
    check("报出 通过/请求 两个数", "通过 1/3" in n, n)
    check("跳过的条数如实报出", "跳过 2 条" in n, n)
    check("跳过原因逐条写在明面上",
          "标题" in n and "致因" in n, n)
    check("notice 是警示色（不是成功色）",
          "notice-warn" in page.get_attribute("#dbNotice", "class"),
          page.get_attribute("#dbNotice", "class"))
    check("被跳过的仍是 pending",
          sdb.scalar("SELECT review_status FROM staging_accidents "
                     "WHERE stage_id='w1'") == "pending"
          and sdb.scalar("SELECT review_status FROM staging_accidents "
                         "WHERE stage_id='w2'") == "pending")
    check("零告警那条真通过了",
          sdb.scalar("SELECT review_status FROM staging_accidents "
                     "WHERE stage_id='p2'") == "approved",
          sdb.scalar("SELECT review_status FROM staging_accidents WHERE stage_id='p2'"))
    check("批量通过后选择条收起", page.locator("#bulkBar").is_hidden())

    print("\n=== ⑩ 并入预演 ===")
    page.select_option("#f_status", "all")
    page.click("#qFilters button[type=submit]")
    page.wait_for_timeout(800)
    check("切到全部后 4 条都在", page.locator("#qBody tr").count() == 4,
          page.locator("#qBody tr").count())
    # 按行号点不可靠（"全部"下顺序不定，点到有告警的会被预演整批拦下）。
    # 按告警单元格里的绿标勾，勾中的就是零告警那两条。
    # 合成 change 不翻转 checked，必须先自己置位（页面里也是这个坑）。
    page.evaluate("""() => {
      document.querySelectorAll('#qBody tr').forEach(tr => {
        const cell = tr.querySelector('.warn-cell');
        if (!cell || !cell.querySelector('.badge.is-ok')) return;
        const cb = tr.querySelector('input[type=checkbox]');
        if (cb && !cb.checked) {
          cb.checked = true;
          cb.dispatchEvent(new Event('change'));
        }
      });
    }""")
    page.wait_for_timeout(250)
    check("勾中的正是零告警那两条",
          page.locator("#qBody tr.is-picked").count() == 2,
          page.locator("#qBody tr.is-picked").count())

    page.click("#bulkPromote")
    wait_visible("#promoteModal")
    page.wait_for_function(
        "() => { const b = document.querySelector('#promoteBody');"
        "return b && !/正在预演/.test(b.innerText); }", timeout=8000)
    body = page.inner_text("#promoteBody")
    check("弹层弹出并给出预演结果", page.locator("#promoteModal").is_visible())
    check("预演写明新增/编码行数",
          "新增事故" in body and "写入编码行" in body, body[:200].replace("\n", " | "))
    check("预演说明会备份可回滚",
          "备份" in body and "回滚" in body, body[:200].replace("\n", " | "))
    check("预演到位后按钮解禁", not page.locator("#promoteRun").is_disabled())

    print("\n=== ⑪ 取消预演：最终库一字未动 ===")
    before = FINAL.read_bytes()
    page.click("#promoteCancel")
    wait_hidden("#promoteModal")
    check("取消后弹层关闭", page.locator("#promoteModal").is_hidden())
    check("取消后最终库字节完全一致", FINAL.read_bytes() == before)
    check("取消后 accidents 仍为空",
          sqlite3.connect(f"file:{FINAL}?mode=ro", uri=True).execute(
              "SELECT COUNT(*) FROM accidents").fetchone()[0] == 0)

    print("\n=== ⑫ 真并入 ===")
    page.click("#bulkPromote")
    wait_visible("#promoteModal")
    page.wait_for_function("() => !document.querySelector('#promoteRun').disabled",
                           timeout=8000)
    page.click("#promoteRun")
    wait_hidden("#promoteModal")
    page.wait_for_function(
        "() => /已并入 \\d+ 条/.test(document.querySelector('#dbNotice').innerText)",
        timeout=10000)
    n = page.inner_text("#dbNotice")
    check("并入回执写明条数与备份", "已并入 2 条" in n and "备份" in n, n)
    check("并入后选择条收起", page.locator("#bulkBar").is_hidden())
    cnt = sqlite3.connect(f"file:{FINAL}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM accidents").fetchone()[0]
    check("最终库真写进 2 条", cnt == 2, cnt)
    # 漏斗是 loadStats() 异步刷的，notice 却是在它之前同步写的——等漏斗自己
    # 到位再断言，别拿 notice 的出现当"漏斗也刷新了"的证据
    page.wait_for_function(
        "() => document.querySelectorAll('#funnelRow .stat')[3]"
        ".innerText.includes('2')", timeout=8000)
    funnel4 = page.locator("#funnelRow .stat").nth(3).inner_text()
    check("漏斗「已并入」跟着变", "2" in funnel4, funnel4)

    print("\n=== ⑬ 并入历史与回滚 ===")
    page.click("#histToggle")
    wait_visible("#histBox")
    page.wait_for_selector("#histBody tr td", timeout=8000)
    check("历史表列出 1 条", page.locator("#histBody tr").count() == 1,
          page.locator("#histBody tr").count())
    hist = page.inner_text("#histBody")
    check("历史显示「生效中」", "生效中" in hist, hist[:150])
    check("历史显示备份文件名", ".bak" in hist, hist[:150])

    page.click("#rollbackBtn")
    page.wait_for_function(
        "() => /已回滚/.test(document.querySelector('#rollbackHint').innerText)",
        timeout=10000)
    check("回滚有回执", "已回滚 2 条事故" in page.inner_text("#rollbackHint"),
          page.inner_text("#rollbackHint"))
    cnt = sqlite3.connect(f"file:{FINAL}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM accidents").fetchone()[0]
    check("回滚后最终库归零", cnt == 0, cnt)
    page.wait_for_timeout(600)
    check("历史行改标「已回滚」", "已回滚" in page.inner_text("#histBody"),
          page.inner_text("#histBody")[:150])

    print("\n=== ⑭ 键盘与脏输入 ===")
    page.locator("#qBody tr").nth(0).locator(".title-main").click()
    wait_visible("#drawer")
    page.keyboard.press("Escape")
    wait_hidden("#drawer")
    check("Esc 能关抽屉", page.locator("#drawer").is_hidden())

    # 直接往 select 里塞非法值，后端不能 500。语义是「退回该参数的默认值」：
    # review_status 的默认是 pending（不是"忽略条件"），所以这里应回到 2 条
    # pending，与 text_type 的默认（空=全部）是同一条规则的两种表现。
    page.evaluate("""() => {
      const s = document.querySelector('#f_status');
      const o = document.createElement('option');
      o.value = 'bogus'; o.selected = true; s.appendChild(o);
    }""")
    page.click("#qFilters button[type=submit]")
    page.wait_for_timeout(800)
    check("非法 review_status 不报错，退回默认的 pending",
          page.locator("#qBody tr").count() == 2,
          page.locator("#qBody tr").count())

    check("控制台干干净净", not console_errors, console_errors[:3])
    check("没有未捕获的 JS 异常", not page_errors, page_errors[:3])

    page.screenshot(path=str(TMP / "m4_review.png"), full_page=True)
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
