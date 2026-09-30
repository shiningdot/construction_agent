"""蓝图 01 §8 验收第 5 条：任务运行中杀进程，重启后状态与数据各自如何。

> 任务运行中杀进程（`kill -9`），重启后该任务状态为 `failed`，已入库的
> `raw_accidents` **不丢不坏**（事务保证）

**这条为什么必须跨进程跑**：pytest 里的 `test_reconcile把僵死任务标为中断` 是
直接往库里插一条 `running` 行再调函数——它验的是「函数改得对不对」，验不到
「进程真被杀掉时事务会怎样」「新进程会不会真的去调那个函数」。两个问题都只有
起真进程、真发 SIGKILL 才看得见。

**为什么两个子进程而不是一个**：`web/views.py` 的 `_reconcile_once` 是**进程级**
一次（模块级 `_reconciled` 标志）。同进程里再打一次 `/collect` 什么也不会发生，
也正是这个标志让「重启后有清理、重启前没有」这句话有了内容。

**假采集器假在哪，真在哪**：`_run_source` 被换掉（不碰外网），换进来的东西
**照样用真的 `_fill` / `_row_from_raw_item` / `staging_db.write_tx()` 写库**，
只是分两笔写：

- 第一笔写完**提交** → 这 3 行是「已入库的 `raw_accidents`」，杀进程后必须还在
- 第二笔写进去**不提交**，然后长睡 → 父进程正在此时发 SIGKILL。这 3 行必须
  **一行都不落地**——「事务保证」四个字说的就是这件事，不构造这个场景就验不到

如果连第二笔也事先提交，整条验收就退化成「数一数行数」，随便怎么写都能过。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable

#: 本脚本要杀进程，绝不能用主仓库那份工作库。每次跑另起临时目录；
#: 目录不主动删——跑挂了正好留着查现场。
TMP = Path(tempfile.mkdtemp(prefix="caiems_restart_"))
DB = TMP / "restart_staging.db"
os.environ["CAIEMS_STAGING_DB"] = str(DB)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + ("  " + str(extra) if extra else ""))


# ── 两个子进程的代码 ─────────────────────────────────────────────
#
# 用字符串传给 `python -c` 而不是落成临时文件：这两个脚本只在本文件里有意义，
# 落盘出去就成了「多出来的、没人知道该不该改的脚本」。

_PROBE = """
import sys
import time
sys.path.insert(0, __ROOT__)
from construction_agent.collector.base import RawAccidentText
from construction_agent.collector import executor
from construction_agent.collector.executor import SourceOutcome, _fill, _row_from_raw_item
from construction_agent.web import staging_db


def _rows(spec, task_id, start):
    out = []
    for i in range(3):
        n = start + i
        item = RawAccidentText(
            source_url="https://restart-probe.invalid/a/%d" % n,
            title="重启探针事故 %d" % n,
            raw_text="第 %d 条正文，用于验证杀进程后已入库的记录不丢不坏。" % n,
        )
        row = _fill(_row_from_raw_item(item, spec.key, task_id))
        row.pop("_assets", None)      # `_` 前缀的键不是列，与真实路径同样处理
        out.append(row)
    return out


def fake_run_source(spec, task_id, *, limit=None, tmp_root=None, filters=None,
                    per_source_timeout=0):
    # 第一笔：写完就提交 —— 这三行是「已入库的 raw_accidents」
    with staging_db.write_tx() as con:
        con.executemany(executor._INSERT_SQL, _rows(spec, task_id, 0))
    print("READY", flush=True)

    # 第二笔：写进去但**不提交**，然后长睡。父进程正是在这段时间里发 SIGKILL。
    # 事务没提交就随进程一起回滚，这 3 行一行都不该落地。
    with staging_db.write_tx() as con:
        con.executemany(executor._INSERT_SQL, _rows(spec, task_id, 3))
        time.sleep(300)
    return SourceOutcome(key=spec.key, ok=True)


executor._run_source = fake_run_source
task_id = executor.create_task(["beijing_em"])
print("TASK=" + task_id, flush=True)
executor.run_task(task_id)
"""

_RESTART = """
import sys
sys.path.insert(0, __ROOT__)
from construction_agent.web.factory import create_app

app = create_app({"TESTING": True})
body = app.test_client().get("/collect")
print("PAGE_STATUS=%d" % body.status_code, flush=True)
"""


def _code(template: str) -> str:
    return template.replace("__ROOT__", repr(str(ROOT)))


def spawn(template: str) -> tuple[subprocess.Popen, list[str]]:
    """起一个真子进程，异步收它的 stdout（不读会把管道塞满，子进程会卡住）。"""
    proc = subprocess.Popen(
        [PY, "-c", _code(template)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(ROOT),
        env={**os.environ, "CAIEMS_STAGING_DB": str(DB)},
    )
    lines: list[str] = []

    def _reader():
        assert proc.stdout is not None
        for ln in proc.stdout:
            lines.append(ln.rstrip())

    threading.Thread(target=_reader, daemon=True).start()
    return proc, lines


def wait_for(lines: list[str], prefix: str, timeout: float) -> str | None:
    """等某一行出现。返回该行，超时返回 None。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ln in list(lines):
            if ln.startswith(prefix):
                return ln
        time.sleep(0.1)
    return None


def read_state(label: str) -> dict:
    """从库外看现场：文件、完整性、行数、任务状态。"""
    files = {p.name: p.stat().st_size for p in sorted(TMP.glob("restart_staging.db*"))}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        n_raw = con.execute("SELECT COUNT(*) FROM raw_accidents").fetchone()[0]
        rows = con.execute(
            "SELECT raw_id, source_key, title, content, source_url, status "
            "FROM raw_accidents ORDER BY title").fetchall()
        task = con.execute(
            "SELECT status, error, finished_at FROM collect_tasks ORDER BY created_at "
            "DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return {"files": files, "integrity": integrity, "n_raw": n_raw,
            "rows": rows, "task": task, "label": label}


def main() -> int:
    print(f"临时工作库：{DB}")

    # ── 建表并确认起点 ──
    sys.path.insert(0, str(ROOT))
    from construction_agent.web import staging_db
    built = staging_db.init_db(DB)
    check("工作库建表成功", len(built["tables"]) == 11,
          f"{len(built['tables'])} 张表，schema {built['schema_version']}")

    print("\n① 起进程 A，跑一个真任务，在第二个事务悬挂时杀它")
    proc_a, lines_a = spawn(_PROBE)
    tid_line = wait_for(lines_a, "TASK=", timeout=60)
    check("任务已建", tid_line is not None, tid_line or f"子进程输出：{lines_a}")
    ready = wait_for(lines_a, "READY", timeout=90)
    check("前 3 行已提交、第二笔事务已悬挂", ready is not None,
          "" if ready else f"子进程输出：{lines_a}")

    if ready is None:
        proc_a.kill()
        print("\n子进程 A 没能走到可杀的时点，后面的断言全部无意义，就此打住。")
        return 1

    # 任务此刻应处于 running；确认后再杀，否则杀的可能是个已经结束的进程
    before = read_state("kill 前")
    check("杀之前任务状态是 running", (before["task"] or [None])[0] == "running",
          str(before["task"]))
    check("杀之前已提交 3 行（第二笔的 3 行还看不见）", before["n_raw"] == 3,
          f"实际 {before['n_raw']}")

    proc_a.kill()                     # SIGKILL，不给收尾机会
    proc_a.wait(timeout=30)
    print(f"  · 已 SIGKILL 进程 A（pid {proc_a.pid}）")

    # ── ② 杀完立刻看库：事务保证在不在 ──
    print("\n② 杀完立刻验库（这一步没有任何代码替我们收尾）")
    after_kill = read_state("kill 后")
    print(f"  · 库文件：{after_kill['files']}"
          "（`-wal` 里可能还留着未提交的帧，靠它恢复才是这一步的意义）")
    check("库仍可打开且完整性检查通过", after_kill["integrity"] == "ok",
          after_kill["integrity"])
    check("已提交的 3 行不丢", after_kill["n_raw"] == 3, f"实际 {after_kill['n_raw']}")
    check("悬挂事务的 3 行一行都没落地", after_kill["n_raw"] == 3,
          f"实际 {after_kill['n_raw']}（期望 3，不是 6）")
    check("落地的正是已提交的那 3 条（不是悬挂事务里的第 4–6 条）",
          {r[2] for r in after_kill["rows"]}
          == {"重启探针事故 0", "重启探针事故 1", "重启探针事故 2"},
          str(sorted(r[2] for r in after_kill["rows"])))
    check("已入库的记录逐列完好",
          before["rows"] == after_kill["rows"],
          "杀前杀后逐列相等" if before["rows"] == after_kill["rows"]
          else f"{before['rows']} != {after_kill['rows']}")
    check("任务状态仍是 running（进程没来得及收尾，这正是要清理的那一行）",
          (after_kill["task"] or [None])[0] == "running", str(after_kill["task"]))

    # ── ③ 起新进程，靠「打开页面」触发清理 ──
    print("\n③ 起进程 B（新进程，_reconciled 标志归零），访问 /collect")
    proc_b, lines_b = spawn(_RESTART)
    hit = wait_for(lines_b, "PAGE_STATUS=", timeout=90)
    proc_b.wait(timeout=30)
    check("重启后 /collect 仍然 200（页面没被僵死任务拖垮）",
          hit == "PAGE_STATUS=200", hit or f"子进程输出：{lines_b}")

    after_restart = read_state("重启后")
    check("僵死任务被标为 failed", (after_restart["task"] or [None])[0] == "failed",
          str(after_restart["task"]))
    check("失败原因写明了是进程重启",
          "进程重启" in (after_restart["task"][1] or ""),
          str((after_restart["task"] or [None, None])[1]))
    check("重启没有把数据弄丢", after_restart["n_raw"] == 3,
          f"实际 {after_restart['n_raw']}")
    check("重启没有把数据弄坏", after_restart["rows"] == after_kill["rows"])
    check("重启后完整性检查仍通过", after_restart["integrity"] == "ok",
          after_restart["integrity"])

    the_id = (tid_line or "TASK=").split("=", 1)[-1]
    print(f"\n任务 {the_id}：running（被杀）→ failed（重启后由页面入口清理）")
    print(f"raw_accidents：0 → 3（提交的）→ 3（+3 行悬挂事务随进程回滚）→ 3（重启后不变）")

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项。现场留在 {TMP}")
    if FAIL:
        for name in FAIL:
            print(f"  ✗ {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
