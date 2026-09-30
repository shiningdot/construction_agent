"""蓝图 02 §8 验收第 5 条：提取任务被中途杀掉后，能不能接着跑、接着跑会不会重复。

> 任务运行中杀进程，重启后已完成条目的 `staging_accidents` **不丢不坏**；
> 任务状态转 `failed`，**可续跑且不重复**（`UNIQUE(raw_id, split_index)`）

与 `e2e_restart_collect.py` 同族（都跨进程），但多验一件事：**续跑**。采集那边
断了就断了，重起一个任务即可；提取这边每一条都是真金白银的 LLM 调用，「已经跑过
的别再跑一遍」不是优化，是不重复烧钱。所以本脚本的重头戏在第 ④ 步。

**为什么必须跨进程**：`execute_task` 开头只在 `pending`/`running` 时置位，重跑一个
`failed` 任务时状态机怎么走、`/api/extract/pending` 在**另一个进程**里数得对不对，
同进程里都验不到。

**假 pipeline 假在哪，真在哪**：`_pipeline` 被换成假的（不碰 LLM），但
`run_one` / `_stage_row` / `UNIQUE(raw_id, split_index)` / `inserted` 分支
**全是真的**——「不重复」这四个字就落在 `inserted == 0` 那一支上，不真跑就验不到。
假 pipeline 在标题含「4 号」的那条上停住并长睡，父进程正在此时发 SIGKILL。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/_common
PY = sys.executable

from _common import main_db_path  # noqa: E402

#: 要杀进程，绝不能用主仓库那份工作库。主库只被**读**（取 CAICS 类名与枚举口径）。
TMP = Path(tempfile.mkdtemp(prefix="caiems_restart_ex_"))
DB = TMP / "restart_extract.db"
MAIN_DB = main_db_path()
os.environ["CAIEMS_STAGING_DB"] = str(DB)
os.environ["CAIEMS_DB"] = MAIN_DB

#: 6 条原文。第 4 条是「被杀的时点」——假 pipeline 跑到它就停住。
RAW_IDS = [f"rr{i}" for i in range(1, 7)]
HALT_TITLE = "重启探针 4 号"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + ("  " + str(extra) if extra else ""))


# ── 子进程共用的头部：假 pipeline ─────────────────────────────────
#
# 与 `e2e_extract.py` 的 FakePipe 是两份，不是一份——那份要造 S2 拦下 / S3 失败
# 等各种分支（它验的是闸门），这份只需要一条成功路径 + 一个可控的停车点。
# 抽成共享模块反而要把两套分支揉在一起，增益不抵复杂度。

_COMMON = '''
import json
import os
import sys
import time
sys.path.insert(0, __ROOT__)
sys.path.insert(0, __SCRIPTS__)

from construction_agent.collector.llm_classifier import (
    ClassificationResult, ClassificationVerdict, TextType, TextTypeResult)
from construction_agent.extraction import staging_executor as ex
from construction_agent.extraction.caics_pipeline import (
    STATUS_COMPLETED, PipelineOutcome, StageRecord)
from construction_agent.extraction.step1_basic_extractor import Step1Result
from construction_agent.extraction.step2_caics_encoder import CauseItem, Step2Result


class FakePipe:
    """真 pipeline 的接口面：run_once 返回 PipelineOutcome。"""

    calls = 0        # 进程内计数：这一趟真调了几次「模型」

    class _C:
        _model = "fake-model"

    def __init__(self):
        self.extractor = self._C()
        self.encoder = self._C()

    def run_once(self, title, text, publish_date="", source_id="", source_name=""):
        FakePipe.calls += 1
        # 停车点**只在被杀的那一趟生效**（`HALT_ON_HIT`）。续跑那一趟里
        # 第 4 条还会再来一次——条件要是只看标题，续跑也会停在这里等 300 秒，
        # 于是「跑不完」被误读成产品缺陷，而其实只是探针自己没长记性。
        if __HALT__ in title and os.environ.get("HALT_ON_HIT") == "1":
            # 停在这里长睡，父进程正是在这段时间里发 SIGKILL。
            # 此时前 3 条已各自提交过事务，这一条一行都还没写。
            print("READY", flush=True)
            time.sleep(300)

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
            construction_phase="主体结构", summary="某工地浇筑时支撑体系失稳坍塌。",
            contractor="某建设集团有限公司")
        b.tokens_in, b.tokens_out = 8000, 500
        c = Step2Result(
            primary_cause="模板支撑体系失稳导致坍塌",
            cause_chain="M1(监管缺失)→H1(违规操作)→F2(支撑失稳)",
            causes=[
                CauseItem("M1", "现场监管缺失", "监理单位未对违规堆放物料进行制止",
                          "监理未履行监督职责，表明现场监管环节失效"),
                CauseItem("H1", "违反操作规程", "未按专项施工方案搭设支撑体系",
                          "施工方违反方案要求，属于典型的违规操作"),
            ])
        c.tokens_in, c.tokens_out = 15000, 900
        out = PipelineOutcome(source_id=source_id, title=title,
                              publish_date=publish_date)
        out.text_type, out.classification, out.basic, out.causes = tt, cl, b, c
        out.records += [StageRecord("S1", True, 900, "ok"),
                        StageRecord("S2", True, 500, "ok"),
                        StageRecord("S3", True, 17000, "ok"),
                        StageRecord("S4", True, 24000, "ok")]
        out.status = STATUS_COMPLETED
        return out


ex._pipeline = lambda: FakePipe()


def wait_done(tid, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        t = ex.get_task(tid)
        if t and t["status"] in ("done", "failed", "cancelled"):
            return t
        time.sleep(0.2)
    raise TimeoutError("任务 %s 没在 %d 秒内跑完" % (tid, timeout))
'''

#: ① 建任务并跑起来，跑到第 4 条时自己停住（不返回，等被杀）
_RUN = '''
r = ex.create_task(__IDS__, concurrency=1)
print("TASK=" + r["task_id"], flush=True)
ex.start_task(r["task_id"])
while True:
    time.sleep(0.5)
'''

#: ② 新进程打开页面，触发 `_reconcile_once`
_RESTART = '''
from construction_agent.web.factory import create_app
app = create_app({"TESTING": True})
b = app.test_client().get("/extract")
print("PAGE_STATUS=%d" % b.status_code, flush=True)
'''

#: ③ 续跑：先看队列剩几条，再补跑，最后全量重跑一遍看会不会重复
_RESUME = '''
from construction_agent.web.factory import create_app
app = create_app({"TESTING": True})
c = app.test_client()

pend = c.get("/api/extract/pending").get_json()["data"]
rest = [r["raw_id"] for r in pend["items"]]
print("PENDING=" + json.dumps(rest), flush=True)

if rest:
    r = ex.create_task(rest, concurrency=1)
    ex.start_task(r["task_id"])
    t = wait_done(r["task_id"])
    print("RESUME_STATUS=" + t["status"], flush=True)

# 全量重跑：把 6 条**全部**再送一遍。已提取过的那些该被
# UNIQUE(raw_id, split_index) 拦下，一行都不该多出来。
r2 = ex.create_task(__IDS__, concurrency=1)
ex.start_task(r2["task_id"])
t2 = wait_done(r2["task_id"])
print("FULL_STATUS=" + t2["status"], flush=True)
print("FULL_STATS=" + json.dumps(t2.get("stats") or {}, ensure_ascii=False), flush=True)
print("MODEL_CALLS=%d" % FakePipe.calls, flush=True)
print("RESUME_DONE", flush=True)
'''


def _code(body: str) -> str:
    return (body.replace("__ROOT__", repr(str(ROOT)))
                .replace("__SCRIPTS__", repr(str(ROOT / "scripts")))
                .replace("__HALT__", repr(HALT_TITLE))
                .replace("__IDS__", repr(RAW_IDS)))


def spawn(body: str, *, halt: bool = False) -> tuple[subprocess.Popen, list[str]]:
    """`halt=True` 的那一趟才会停在第 4 条上等被杀，别的趟次一路跑完。"""
    env = {**os.environ, "CAIEMS_STAGING_DB": str(DB), "CAIEMS_DB": str(MAIN_DB)}
    if halt:
        env["HALT_ON_HIT"] = "1"
    proc = subprocess.Popen(
        [PY, "-c", _code(_COMMON + body)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(ROOT), env=env,
    )
    lines: list[str] = []

    def _reader():
        assert proc.stdout is not None
        for ln in proc.stdout:
            lines.append(ln.rstrip())

    threading.Thread(target=_reader, daemon=True).start()
    return proc, lines


def wait_for(lines: list[str], prefix: str, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ln in list(lines):
            if ln.startswith(prefix):
                return ln
        time.sleep(0.1)
    return None


def snapshot() -> dict:
    """库外视角：暂存行、每条的 stage_id、raw 的生命周期回填。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        n_stage = con.execute("SELECT COUNT(*) FROM staging_accidents").fetchone()[0]
        stages = {r["raw_id"]: r["stage_id"] for r in con.execute(
            "SELECT raw_id, stage_id FROM staging_accidents ORDER BY raw_id")}
        n_cause = con.execute("SELECT COUNT(*) FROM staging_causes").fetchone()[0]
        promoted = {r["raw_id"] for r in con.execute(
            "SELECT raw_id FROM raw_accidents WHERE status = 'promoted'")}
        task = con.execute(
            "SELECT status, error FROM extract_tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    return {"n_stage": n_stage, "stages": stages, "n_cause": n_cause,
            "promoted": promoted, "task": dict(task) if task else None,
            "integrity": integrity}


def main() -> int:
    print(f"临时工作库：{DB}\n主库（只读）：{MAIN_DB}")

    sys.path.insert(0, str(ROOT))
    from construction_agent.web import staging_db as sdb
    built = sdb.init_db(DB)
    check("工作库建表成功", len(built["tables"]) == 11,
          f"{len(built['tables'])} 张表，schema {built['schema_version']}")
    check("主库可读（否则提取会静默判不出编码）", Path(MAIN_DB).exists(), MAIN_DB)

    # ── 种 6 条已确认相关的原文 ──
    with sdb.write_tx() as con:
        con.execute("INSERT INTO collect_sources (source_key, display_name, "
                    "source_group, track) VALUES ('safehoo','安全管理网','national','network')")
        for i, rid in enumerate(RAW_IDS, start=1):
            title = f"重启探针 {i} 号工地坍塌事故"
            con.execute(
                "INSERT INTO raw_accidents (raw_id, source_key, title, content, "
                "source_url, publish_date, fetch_time, url_hash, gate_verdict, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,'gated')",
                (rid, "safehoo", title,
                 f"{title}。经调查，事故起因为现场管理缺失。" * 6,
                 f"https://www.safehoo.com/News/{rid}.shtml", "2026-03-11",
                 f"2026-09-30T02:0{i}:00Z", f"h_{rid}", "confirmed"))

    # ── ① 跑起来，在第 4 条上停住，然后杀 ──
    print("\n① 起进程 A，单并发跑 6 条，第 4 条时它会停下等被杀")
    proc_a, lines_a = spawn(_RUN, halt=True)
    tid = wait_for(lines_a, "TASK=", timeout=90)
    check("任务已建", tid is not None, tid or f"子进程输出：{lines_a}")
    ready = wait_for(lines_a, "READY", timeout=120)
    check("前 3 条已各写完一笔事务、第 4 条卡在模型调用里", ready is not None,
          "" if ready else f"子进程输出：{lines_a}")
    if ready is None:
        proc_a.kill()
        print("\n子进程 A 没能走到可杀的时点，后面的断言全部无意义，就此打住。")
        return 1

    before = snapshot()
    check("杀之前状态是 running", (before["task"] or {}).get("status") == "running",
          str(before["task"]))
    check("杀之前暂存区恰好 3 条", before["n_stage"] == 3, f"实际 {before['n_stage']}")
    check("杀之前恰好 3 条原文被回填成 promoted",
          before["promoted"] == set(RAW_IDS[:3]), sorted(before["promoted"]))

    proc_a.kill()
    proc_a.wait(timeout=30)
    print(f"  · 已 SIGKILL 进程 A（pid {proc_a.pid}）")

    # ── ② 杀完立刻验 ──
    print("\n② 杀完立刻验库")
    after_kill = snapshot()
    check("库完整", after_kill["integrity"] == "ok", after_kill["integrity"])
    check("已完成条目不丢", after_kill["n_stage"] == 3, f"实际 {after_kill['n_stage']}")
    check("已完成条目逐条完好（stage_id 未变）",
          after_kill["stages"] == before["stages"])
    check("在第 4 条卡住的那条没有留下半截记录",
          "rr4" not in after_kill["stages"], sorted(after_kill["stages"]))
    check("致因行与暂存行同步（没多也没少）",
          after_kill["n_cause"] == before["n_cause"], f"{after_kill['n_cause']} 条")
    check("任务状态仍是 running（等着被清理的那一行）",
          (after_kill["task"] or {}).get("status") == "running",
          str(after_kill["task"]))

    # ── ③ 新进程打开页面，触发清理 ──
    print("\n③ 起进程 B（新进程，_reconciled 归零），访问 /extract")
    proc_b, lines_b = spawn(_RESTART)
    hit = wait_for(lines_b, "PAGE_STATUS=", timeout=90)
    proc_b.wait(timeout=30)
    check("重启后 /extract 仍然 200", hit == "PAGE_STATUS=200",
          hit or f"子进程输出：{lines_b}")

    after_restart = snapshot()
    check("僵死任务被标为 failed",
          (after_restart["task"] or {}).get("status") == "failed",
          str(after_restart["task"]))
    err = (after_restart["task"] or {}).get("error") or ""
    check("失败原因写明了「已完成条目不受影响」", "不受影响" in err, err)
    check("重启没有把已完成条目弄丢", after_restart["n_stage"] == 3,
          f"实际 {after_restart['n_stage']}")

    # ── ④ 续跑：这一条是提取侧独有的 ──
    print("\n④ 起进程 C：看队列剩几条 → 补跑 → 再全量重跑一遍看会不会重复")
    proc_c, lines_c = spawn(_RESUME)
    pend = wait_for(lines_c, "PENDING=", timeout=90)
    # 比**集合**不比顺序：队列按 `fetch_time DESC` 排，那是我造的种子数据决定的，
    # 写死顺序等于把探针的种子顺序当成了产品契约
    got = json.loads(pend.split("=", 1)[1]) if pend else None
    check("待提取队列恰好剩第 4–6 条（已提取的没被重新列出来）",
          got is not None and sorted(got) == ["rr4", "rr5", "rr6"],
          pend or f"输出：{lines_c}")

    done = wait_for(lines_c, "RESUME_DONE", timeout=180)
    if done is None:
        proc_c.kill()
        print(f"\n子进程 C 没跑完，后面的断言全部无意义。它的输出：{lines_c}")
        return 1
    proc_c.wait(timeout=30)
    print("  · 子进程 C 输出：" + " | ".join(lines_c))

    calls = wait_for(lines_c, "MODEL_CALLS=", timeout=10)
    check("续跑补 3 条 + 全量重送 6 条 = 9 次「模型调用」"
          "（**数据**不重复，但已跑过的条目仍会再调一次模型）",
          calls == "MODEL_CALLS=9", calls or "没拿到调用次数")

    after_resume = snapshot()
    check("补跑后 6 条原文各有一条暂存行（不重不漏）",
          after_resume["n_stage"] == 6, f"实际 {after_resume['n_stage']}")
    check("全量重跑没有多出任何一行（UNIQUE 拦住了）",
          after_resume["n_stage"] == 6, f"实际 {after_resume['n_stage']}（期望仍是 6）")
    check("前 3 条的 stage_id 自始至终没变（重跑走的是 inserted=0 那一支）",
          all(after_resume["stages"][k] == before["stages"][k]
              for k in after_resume["stages"] if k in before["stages"]),
          "逐条相等" if all(after_resume["stages"][k] == before["stages"][k]
                            for k in after_resume["stages"] if k in before["stages"])
          else f"{before['stages']} → {after_resume['stages']}")
    check("致因行也恰好每条的份数（重跑没挂第二份）",
          after_resume["n_cause"] == 12, f"实际 {after_resume['n_cause']}（6 条 × 2）")
    check("库完整性仍通过", after_resume["integrity"] == "ok")

    print(f"\n任务 {(tid or 'TASK=').split('=', 1)[-1]}：running（被杀）→ failed（重启后清理）"
          f" → 由新任务补完，暂存区 3 → 6 条")
    print("全量重跑：6 条原文再送一遍 → 暂存区仍是 6 条，stage_id 一个没变")

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项。现场留在 {TMP}")
    if FAIL:
        for name in FAIL:
            print(f"  ✗ {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
