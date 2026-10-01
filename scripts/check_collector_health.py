#!/usr/bin/env python3
"""24 个采集源的**动态**健康检查：现在还真能抓到数据吗？

蓝图 `01_在线采集.md` 待决项 B 记着这条缺口：

> 站点大面积失效（22 个源声明的目录全部不存在）｜M2 抓不到数据｜**静态健康度
> 清单已出**（24/24 import 成功、全部符合 `BaseCollector` 契约、12 个共用一个
> TRS 基类）；**动态试跑未做**，按清单决定开发优先级。

静态检查只能证明「代码还在」，证明不了「站点还在」。本脚本对每个源真发一次
请求，把「能不能抓到」变成实测数字。

## 怎么跑

    .venv/bin/python3 scripts/check_collector_health.py
    .venv/bin/python3 scripts/check_collector_health.py --only hebei_em,safehoo
    .venv/bin/python3 scripts/check_collector_health.py --limit 5 --timeout 180

默认跑全部 24 个源，每个源最多收 3 条，单源 120 秒。

## 四条口径，先看这里

1. **不写任何库。** 只读探测：`registry.build(key, data_dir=<tmp>)` 之后挂一个
   只记账的 sink，不碰 `raw_accidents`，也不碰 `data/<源名>/`。探测完临时目录
   整个删掉。
2. **探测的超时不是生产的超时。** `executor.SOURCE_TIMEOUT_S` 是 **30 分钟**，
   因为它要把一个源翻到底；本脚本给 120 秒，因为只要证明「列表页还能出条目」。
   两者不可互推：某源 120 秒没跑完，不代表生产里也会超时。
3. **`limit` 是收集上限，不是采集上限。** 只有 `safehoo` 的构造器有
   `max_items`（真早停），其余源的网络请求照跑。为了不把 24 个源串行翻一遍，
   sink 收满 N 条后抛 `ProbeStop(BaseException)` 强制中断——继承
   `BaseException` 是为了穿过源内部的 `except Exception` 兜底，否则会被就地
   吞掉、早停失效。实测 `hebei_em` 靠它从 77 秒降到 14 秒。
4. **「有正文」这一列不是「正文抓到了」。** 它能证明的只是「收到的 content
   里有一段非标题的文本」。通路 A 的 `content` 是源自己拼的（标题 + 来源 +
   正文预览），长度天然大于 0。要看具体内容，跑 `--samples`。
5. **「闭环」那一栏只在没早停时才有意义。** 它核的是
   `列表条目数 == 收录 + 审核 + 排除`，回答的是上面「条数」栏答不了的问题：
   列表 110 条、只收到 3 条，中间那 107 条**是初筛排除掉的，还是压根没处理**？
   但早停会在任意一条上截断循环，此时等式两边停在同一个位置，差额恰好是
   「还没来得及处理的条目」——恒等式**必然不成立**，而且这个不成立没有信息量。
   所以探针对**列表本身 ≤ 60 条的源不早停**（跑完也不慢，见 `_FULL_LIST_MAX`），
   让这一栏真能出结论；更长的源照旧早停，这一栏显示 `早停`。另有三种显示
   ——`空表`（列表 0 条，`0 == 0 + 0 + 0` 恒真）、`—`（6 个源根本没记这几个数：
   `esafety` / `mem` / `mohurd` / `news_aggregator` / `open_gov` / `safehoo`）
   ——**全都表示「不适用」，不是「有问题」**。三者混着数，就会得出
   「十几个源闭环失败」这种自己吓自己的结论。
6. **「空」要分三种。** 站点通了、栏目本来就空 / 列表页拿到了却解析不出条目 /
   DNS 挂了、TLS 谈不成、被 412 拦——这三件事在 `_total_listed` 上都是 0，
   日志里都只有一行 WARNING，采集任务都报「成功」。

   2026-10-01 实测 `jl_em`（DNS）、`jx_em`（SSL 老式重协商）、`hub_em`（412）
   三个源在旧表上逐格相同；第三十七轮把 `_fetch_failed` 加进 TRS 基类（只数
   重试耗尽，404/410 不算），「结果」栏据此拆出 `没连上`。**但 `jx_em` 的 TLS
   修好之后变成 `net_failed = 0` + `listed = 0`，与「栏目本来就空」又逐格相同**
   ——它实际是列表页 200 / 271,906 字节（SPA 壳），修好一层只是把它从一种
   「看不出区别」挪到另一种。

   第三十八轮再加 `_list_pages_ok`（列表页正文真取到了几页），拆出 `解析空`。
   **已知的不可分**：真·空栏目（站点通了、那个栏目确实零条）也落在 `解析空`，
   两者都要人去看页面；这一格至少把「压根没取到页面」排除了。
   **只覆盖 12 个 TRS 源**，其余源仍只有 `空` 一种。

## 退出码

- `0` —— 探测跑完，**且至少有一个源抓到了数据**（结论已产出）
- `2` —— **全量探测里一个源都没抓到**。这大概率不是「全网 24 站同时失效」，
  而是探测本身出了问题（没网、代理、sink 挂错）。跟 `build_image_manifest.py`
  「扫到 0 张退 2」同一族判据：两头都真的判据等于没有判据。
  **`--only` 时这条不适用**（退 0）——见 `_exit_code`。
- `1` —— 脚本自身跑不动（源清单拿不到、结果目录写不进去）

**注意退 0 不表示「源都健康」。** 「18 个源失效」是真实的探测结果、不是脚本
的故障，所以不退非 0——落在表格的最后一行小结里。要拿它做门禁的话，读
`--json` 导出的报告自己判。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from construction_agent.collector import registry as registry  # noqa: E402

# 单源超时。**故意比 `executor.SOURCE_TIMEOUT_S`（30 分钟）短得多**——
# 见模块 docstring 第 2 条。探测要回答的是「列表页还出不出条目」，
# 不是「翻到底要多久」。
DEFAULT_TIMEOUT_S = 120

# 同时探测几个源。4 是保守值：对单个站仍是串行（采集器自带 delay），
# 只是别让 24 个站排成一队。生产执行器的并发度是可配的，这里固定死，
# 免得探测脚本自己成了流量源。
CONCURRENCY = 4

DEFAULT_LIMIT = 3

# 列表条目数不超过这个值就**不早停**——把源跑到底，好让「闭环」那一栏出结论。
# 60 的来由是实测的候选比例：这类源大约 15% 的列表条目能过初筛（`tj_em` 列表
# 15 条出 3 条、`ln_em` 20 条出 2 条），跑到底十几秒；再长一档就是 84 / 94 /
# 110 / 193 / 472，跑到底要几分钟，不值得为探测付这个代价。
_FULL_LIST_MAX = 60

# 无论如何都要停的硬上限。`_FULL_LIST_MAX` 依赖「列表条目数在进循环之前就
# 写好了」这个前提（两族实现都满足），可这个前提一旦不成立就再没有东西能拦住
# 探针——加一个不依赖任何前提的兜底。
_HARD_STOP = 200


def _median(xs: list[int]) -> int:
    if not xs:
        return 0
    ys = sorted(xs)
    return ys[len(ys) // 2]


# --------------------------------------------------------------------------
# 子进程侧：探一个源
# --------------------------------------------------------------------------


class _ProbeStop(BaseException):
    """收够条数就中止本轮采集。

    继承 `BaseException` 不是笔误：采集器里到处是 `except Exception`（单条
    失败不许掀掉整轮），抛普通异常会被就地吞掉，早停失效、网络请求照跑。
    """


class _Sink:
    """只记账的 sink，与 `executor._Sink` 同形：两条通路都实现。

    键名两套，别弄混——
    - 通路 A（`RawAccidentStore.add()` 交出来的 record）：正文在 `content`
    - 通路 B（`RawAccidentText`）：正文在 `raw_text`

    本文件第一版两条路都读 `raw_text`，于是 9 个 store-backed 的源全部报
    「正文 0 字」。那不是源的问题，是取错了键。
    """

    def __init__(self, path: Path, limit: int):
        self._fh = path.open("w", encoding="utf-8")
        self.limit = limit
        self.count = 0
        # 挂上采集器，好在早停之前先看一眼「列表到底有多长」——见 `_short_list()`。
        # 允许为 None：build 失败时没有采集器可挂。
        self.watch = None

    def _short_list(self) -> bool:
        """列表本身就很短时别早停：把源跑到底，「闭环」那一栏才算得出来。

        列表条目数在 `collect()` 抓完列表、进循环**之前**就写好了（两族实现
        都如此），所以这里的每一条 `_accept` 都读得到。属性名沿用读「列表」
        栏的那三个——只认一个名字的话，另外一族会永远退回早停。

        读不到就按「不短」处理、照旧早停：**宁可这一栏算不出来，也不要赌
        一个可能很长的源**。
        """
        if self.watch is None:
            return False
        for attr in ("_total_listed", "_total_list", "_total_records"):
            v = getattr(self.watch, attr, None)
            if isinstance(v, int):
                return v <= _FULL_LIST_MAX
        return False

    def _accept(self, title, url, text) -> None:
        self.count += 1
        # 逐条落盘 + flush：父进程超时 kill 之后，已经收到的条目还能读出来。
        # 「120 秒收到 47 条」和「120 秒一条没有」是完全不同的两个结论。
        self._fh.write(json.dumps(
            {"title": (title or "")[:80], "url": url or "",
             "chars": len(text or "")}, ensure_ascii=False) + "\n")
        self._fh.flush()
        if self.count >= _HARD_STOP:
            raise _ProbeStop
        if self.count >= self.limit and not self._short_list():
            raise _ProbeStop

    def __call__(self, record: dict) -> None:
        self._accept(record.get("title"), record.get("source_url"),
                     record.get("content"))

    def feed(self, item) -> None:
        self._accept(item.title, item.source_url, item.raw_text)

    def close(self) -> None:
        self._fh.close()


def _closure_of(collector) -> dict | None:
    """把「列表 → 四个去向」的账读出来；读不齐就返回 None。

    两套命名，**语义对齐之后才能相加**：

    | 族 | 列表 | 收录 | 审核 | 排除 |
    |---|---|---|---|---|
    | TRS 基类（12 个源） | `_total_listed` | `_collected` | `_review` | `_filtered_title` + `_filtered_classify` |
    | 独立实现（6 个源） | `_total_list` / `_total_records` | `_collected` | `_review_count` | `_filtered_out` |

    TRS 把「排除」拆成初筛与分类两段，独立实现合成一个 `_filtered_out`——
    **加起来才是同一个口径**。少加一个，12 个源的差额会恰好等于初筛排除数，
    看起来就像是「丢了这么多条」。

    返回值里的 `sum` 是三个去向之和，`listed - sum` 就是差额。

    读不齐（含 `collector` 为 None）时返回 None，这一栏显示 `—`。6 个源落在
    这里——它们压根没记这几个数，**不是「闭环失败」**。
    """
    if collector is None:
        return None

    def pick(*names):
        for n in names:
            v = getattr(collector, n, None)
            if isinstance(v, int):
                return v
        return None

    listed = pick("_total_listed", "_total_list", "_total_records")
    col = pick("_collected")
    rev = pick("_review_count", "_review")
    fo = pick("_filtered_out")
    if fo is None:
        ft = pick("_filtered_title")
        fc = pick("_filtered_classify")
        filt = None if (ft is None and fc is None) else (ft or 0) + (fc or 0)
    else:
        filt = fo
    if listed is None or col is None or rev is None or filt is None:
        return None
    return {"listed": listed, "collected": col, "review": rev,
            "filtered": filt, "sum": col + rev + filt}


def _probe_child(key: str, work: Path, limit: int, tmp_root: Path) -> None:
    """在子进程里探一个源。结果写 `<work>/<key>.jsonl`，异常写 `<key>.err`。

    **全程不写库**：只 `build` + 挂 sink，`executor` 那条写 `raw_accidents`
    的路径根本没进来。
    """
    import inspect

    work, tmp_root = Path(work), Path(tmp_root)   # 跨进程传的是 str
    started = time.monotonic()
    note = ""
    sink = None
    collector = None
    early = False
    try:
        sink = _Sink(work / f"{key}.jsonl", limit)
        data_dir = tmp_root / key
        data_dir.mkdir(parents=True, exist_ok=True)
        collector, _spec = registry.build(key, data_dir=data_dir)
        # 早停之前先知道列表有多长——`_short_list()` 靠它决定「值不值得跑到底」。
        sink.watch = collector

        store = getattr(collector, "raw_store", None)
        if store is not None and hasattr(store, "attach"):
            store.attach(sink)
            note = "通路A"

        # 唯一原生支持早停的源（构造参数 `max_items`）。判据取构造签名而不是
        # `hasattr`——刚赋完值之后 hasattr 必然为真，等于没判。
        if "max_items" in inspect.signature(type(collector).__init__).parameters:
            collector.max_items = limit
            note = (note + "+max_items").lstrip("+")

        try:
            ret = collector.collect()
            if isinstance(ret, list):
                note = (note + "+通路B").lstrip("+")
                for item in ret:
                    sink.feed(item)
        except _ProbeStop:
            early = True
            note = (note + "+早停").lstrip("+")
    except Exception as e:  # noqa: BLE001 —— 探测脚本不挑异常类型
        (work / f"{key}.err").write_text(f"{type(e).__name__}: {e}",
                                         encoding="utf-8")
    finally:
        if sink is not None:
            sink.close()
    # **列表条目数**——本轮新加的判据。它测的是「这个源在列表页上一共列了多少篇」，
    # 与「收到了几条」是两件事：早停会让后者恒等于 `--limit`，看不出列表有多长。
    # 2026-10-01 第五轮就是栽在这上面：`shaanxi_em` / `sh_em` 都只抓了列表第一页
    # （真实 472 / 94 条，抓到 20 / 15 条），而「有数据」这一栏把它们判绿，
    # 因为「这个站本来就少」听起来完全合理。有这一栏，**列表只有一页就露出来了**。
    # 三个属性名，因为采集器有两套实现：TRS 基类用 `_total_listed`，
    # 独立实现各写各的（`henan_em` 用 `_total_list`，`beijing_em` 用
    # `_total_records`）。**只认一个名字，读不到的源就显示 `—`**——
    # 而 `—` 和「这个源没有列表」看起来一样，等于判据对它们失灵。
    listed = None
    for attr in ("_total_listed", "_total_list", "_total_records"):
        v = getattr(collector, attr, None)
        if isinstance(v, int):
            listed = v
            break
    if listed is not None:
        (work / f"{key}.listed").write_text(str(listed), encoding="utf-8")
    # **闭环**——「列表条目数 == 收录 + 审核 + 排除」。上面「列表」栏只说得出
    # 「列表里有多少篇」，说不出「没进库的那些去哪了」；这一栏补的就是那一段。
    # 早停时不算（见 `_Sink._short_list()` 与模块 docstring 第 5 条），
    # 但**照样写盘**——`early_stop` 这个标志要传回父进程，好让表格显示「早停」
    # 而不是一个看起来像差额的数字。
    closure = _closure_of(collector)
    if closure is not None:
        closure["early_stop"] = early
        # 网络层失败次数。`_total_listed == 0` 时它是唯一能把「栏目本来就空」
        # 和「压根没连上」分开的东西——见 `_status_of`。
        nf = getattr(collector, "_fetch_failed", None)
        closure["net_failed"] = nf if isinstance(nf, int) else None
        # 取到正文的列表页个数。`listed == 0` 时它把「页面拿到了但解析不出」
        # 与「压根没取到页面」分开——见 `_status_of`。
        lp = getattr(collector, "_list_pages_ok", None)
        closure["list_pages_ok"] = lp if isinstance(lp, int) else None
        (work / f"{key}.closure").write_text(
            json.dumps(closure), encoding="utf-8")
    (work / f"{key}.elapsed").write_text(f"{time.monotonic() - started:.1f}",
                                         encoding="utf-8")
    if note:
        (work / f"{key}.note").write_text(note, encoding="utf-8")


# --------------------------------------------------------------------------
# 父进程侧：调度 + 汇总
# --------------------------------------------------------------------------


def _read_result(key: str, work: Path) -> dict:
    rows = []
    p = work / f"{key}.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass        # 被 kill 时最后一行可能写了一半
    out: dict = {"key": key, "rows": rows, "count": len(rows)}
    err = work / f"{key}.err"
    out["error"] = err.read_text(encoding="utf-8").strip() if err.exists() else ""
    el = work / f"{key}.elapsed"
    out["elapsed"] = float(el.read_text()) if el.exists() else None
    nt = work / f"{key}.note"
    out["note"] = nt.read_text() if nt.exists() else ""
    lst = work / f"{key}.listed"
    out["listed"] = int(lst.read_text()) if lst.exists() else None
    cl = work / f"{key}.closure"
    if cl.exists():
        try:
            out["closure"] = json.loads(cl.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out["closure"] = None   # 被 kill 时可能写了一半
    else:
        out["closure"] = None
    return out


def _run_all(specs, work: Path, tmp_root: Path, limit: int, timeout: int) -> list:
    pending = list(specs)
    running: list[dict] = []
    results: list[dict] = []
    ctx = mp.get_context("fork")
    total = len(specs)
    done = 0

    def launch(spec):
        p = ctx.Process(target=_probe_child,
                        args=(spec.key, str(work), limit, str(tmp_root)),
                        name=f"probe-{spec.key}", daemon=True)
        p.start()
        return {"proc": p, "key": spec.key,
                "deadline": time.monotonic() + timeout}

    try:
        while pending or running:
            while pending and len(running) < CONCURRENCY:
                running.append(launch(pending.pop(0)))

            time.sleep(0.25)
            for item in list(running):
                proc = item["proc"]
                if proc.is_alive() and time.monotonic() <= item["deadline"]:
                    continue
                killed = False
                if proc.is_alive():
                    proc.terminate()
                    proc.join(5)
                    if proc.is_alive():
                        proc.kill()
                        proc.join(5)
                    killed = True
                else:
                    proc.join()
                res = _read_result(item["key"], work)
                res["timed_out"] = killed
                # 子进程自己崩了（不是被我们 kill 的）必须报成「出错」。
                # 漏掉这一步，探测脚本自己的 bug 会伪装成「这个源没数据」——
                # 父进程只看结果文件，文件不存在和「没抓到」长得一模一样。
                if not killed and proc.exitcode not in (0, None) \
                        and not res["error"]:
                    res["error"] = f"子进程异常退出（exitcode={proc.exitcode}）"
                results.append(res)
                running.remove(item)
                done += 1
                print(f"  [{done:2d}/{total}] {_fmt_line(res)}", flush=True)
    except KeyboardInterrupt:
        for item in running:
            item["proc"].kill()
        print("\n· 被中断，已杀掉在跑的子进程", file=sys.stderr)
        raise
    return results


def _status_of(res: dict) -> str:
    """四种取值变成六种：`空` 被拆成「没连上」与「解析空」。

    2026-10-01 实测：`jl_em`（DNS 解析失败）、`jx_em`（SSL 老式重协商被禁）、
    `hub_em`（HTTP 412 反爬）三个源，三种毫不相干的故障，在旧表上逐格相同。
    它们和「站点通了、栏目确实没内容」在 `_total_listed` 上都是 0——不拆开，
    这一栏就答不了「这个源还活着吗」。

    第三十七轮只拆出了「没连上」。**第三十八轮的全源快照又造出一个反例**：
    `jx_em` 的 TLS 修好之后变成 `net_failed = 0` + `listed = 0`——与「站点
    通了、栏目确实没内容」**逐格相同**，而它的真相是列表页 HTTP 200 /
    271,906 字节（SPA 壳，49 条全在 JSON 接口里）。修好一层只是把一个源从
    一种「看不出区别」挪到另一种「看不出区别」。

    判据用 `_list_pages_ok`：**列表页正文真取到了几页**。取到了却一条都没
    解析出来，问题就在解析这一层（「列表页形态变了」优先于「栏目真的空了」），
    而不是在网络或站点存活上。

    **已知的不可分**：真·空栏目（站点通了、那个栏目确实零条）也会落到
    `解析空`。两者都要人去看页面。这一格至少把「压根没取到页面」排除了。
    """
    if res.get("timed_out"):
        return "超时"
    if res.get("error"):
        return "出错"
    if res["count"]:
        return "有数据"
    c = res.get("closure") or {}
    if c.get("net_failed"):
        return "没连上"
    if c.get("list_pages_ok") and res.get("listed") == 0:
        return "解析空"
    return "空"


def _closure_cell(res: dict) -> str:
    """「闭环」一格的字面量。

    三种取值，含义完全不同，**不能混着看**：

    - `—`       读不齐计数器（6 个源根本没记这几个数），或子进程被 kill 了
                ——不适用
    - `早停`     列表超过 `_FULL_LIST_MAX`，探针按设计早停了——不适用
    - `空表`     列表解析出 0 条，`0 == 0 + 0 + 0`——**恒真，同样不适用**
    - `✓` / `差 N` **跑到底之后的真实对账结果**。只有这两个是结论。

    最后那条是这一栏存在的理由：`差 N` 意味着列表里的条目进了循环、却没有
    落到任何一个去向（三个计数器在循环里逐条累加，正常情况必然相等）。

    `空表` 是补上的一个洞：这一栏刚加出来时，任何完全抓不到的源都会得到
    `✓`——列表 0、收录 0、审核 0、排除 0。一个恒真的判据和恒假的判据一样
    没用，所以这里不给它打勾。
    """
    c = res.get("closure")
    if not c or "sum" not in c:
        return "—"
    if c.get("early_stop"):
        return "早停"
    if c["listed"] == 0:
        return "空表"
    d = c["listed"] - c["sum"]
    return "✓" if d == 0 else f"差 {d}"


def _count_cell(res: dict) -> str:
    """「条数」一格的字面量。

    这个数在两类源上**含义不同**，不打标记就没法横向比：

    - 列表 ≤ `_FULL_LIST_MAX` 的源不早停 → 这是**跑到底之后的真实候选数**
    - 更长的源早停 → 这是 `--limit`，**不是抓到了这么多**

    带 `*` 的是后者。拿 `cq_em` 的 13 和 `sh_em` 的 3 直接比是错的：
    `sh_em` 列表 94 条，只是按设计停在了 3。
    """
    n = str(res["count"])
    return n + "*" if "早停" in (res.get("note") or "") else n


def _fmt_line(res: dict) -> str:
    st = _status_of(res)
    el = f"{res['elapsed']:.1f}s" if res["elapsed"] is not None else "—"
    chars = [r["chars"] for r in res["rows"]]
    med = _median(chars)
    listed = res.get("listed")
    extra = f"正文中位 {med} 字" if res["count"] else (res.get("error") or "")[:60]
    tail = "" if listed is None else f"列表 {listed}"
    clo = _closure_cell(res)
    clo = "" if clo == "—" else f"闭环 {clo}"
    return (f"{res['key']:20s} {st:4s} {_count_cell(res):>3s} 条 {el:>7s}  "
            f"{tail:>10s}  {clo:>8s}  {extra}")


# 典型的一页容量。TRS 站的列表页多是 15 / 19 / 20 条一页。
_PAGE_SIZED = 25


def _print_closure_hint(results: list) -> None:
    """「闭环」这一栏的读法；跑到底却对不上账的源单独列出来。

    **只有 `✓` 和 `差 N` 是结论。** `早停` 与 `—` 都是「这次没算出结论」，
    不是「有问题」——三者混着数，就会得出「十几个源闭环失败」这种自己吓
    自己的结论。这正是环形判据的老毛病换了个地方：判据的取值域里混进了
    「不适用」，而它和「失败」看起来一样。
    """
    judged = [r for r in results
              if (r.get("closure") or {}).get("sum") is not None
              and r["closure"].get("early_stop") is False
              and r["closure"]["listed"] > 0]
    bad = [r for r in judged if r["closure"]["listed"] != r["closure"]["sum"]]
    held = [r for r in results
            if (r.get("closure") or {}).get("early_stop") is True]
    empty = [r for r in results
             if (r.get("closure") or {}).get("sum") is not None
             and r["closure"]["listed"] == 0
             and r["closure"].get("early_stop") is False]
    blank = [r for r in results
             if not (r.get("closure") or {}).get("sum") is not None]

    if bad:
        print(f"\n⚠ {len(bad)} 个源跑到底后账对不上（列表 ≠ 收录+审核+排除）：")
        for r in bad:
            c = r["closure"]
            print(f"  `{r['key']}`  列表 {c['listed']} ≠ 收录 {c['collected']} "
                  f"+ 审核 {c['review']} + 排除 {c['filtered']} = {c['sum']}"
                  f"  （差 {c['listed'] - c['sum']}）")
        print("  三个计数器在循环里是逐条累加的，正常情况必然相等——出现差额"
              "意味着有列表条目进了循环、却没落到任何一个去向。")

    print(f"\n> 闭环：判定 {len(judged)} 个"
          f"（`✓` {len(judged) - len(bad)}、`差 N` {len(bad)}）、"
          f"早停未判定 {len(held)} 个、空表未判定 {len(empty)} 个、"
          f"无此判据 {len(blank)} 个。")
    if held:
        print(f"> 早停的是列表超过 {_FULL_LIST_MAX} 条的源"
              f"——**不适用，不是有问题**。")
    if empty:
        keys = "、".join(f"`{r['key']}`" for r in empty)
        print(f"> 空表的是列表解析出 0 条的源（{keys}）——`0 == 0 + 0 + 0` 恒真，"
              f"这一栏对它们没有信息量。**它们的问题不在「账对不对」，在"
              f"「为什么一条都没有」**，看「结果」栏的 `空` / `解析空` / `没连上`"
              f"三者的区别。")
    if blank:
        print("> 无此判据的源没记这几个计数器，同样不是「闭环失败」。")


def _print_parse_empty_hint(results: list) -> None:
    """「列表页拿到了、却一条都没解析出来」的源单独列出来。

    这是第三十八轮新增的一格。它比 `没连上` 更值得看：`没连上` 是网络/站点
    层面的问题，人一眼知道要等或要绕；`解析空` 说明**站点活着、页面拿到了，
    是我们的判据在这个页面上一条都对不上**——几乎总是「列表页形态变了」
    （改版、换成 SPA、条目渲染进 JSON），而不是「栏目真的空了」。

    区分靠 `_list_pages_ok`（取到正文的列表页数）。**已知不可分**：真·空栏目
    也落在这一格，所以给出的动作是「去看一眼页面」，不是「一定有 bug」。
    """
    pe = [r for r in results
          if (r.get("closure") or {}).get("list_pages_ok")
          and r.get("listed") == 0]
    if not pe:
        return
    for r in pe:
        c = r["closure"]
        print(f"\n⚠ `{r['key']}`：列表页取到正文 {c['list_pages_ok']} 页、"
              f"解析出 0 条——**站点是活的，是判据对不上页面**。")
    print("> 这一格与「栏目本来就空」在计数器上不可分（两者都是 pages_ok≥1 且 "
          "listed=0），要人打开列表页看一眼才能定。")
    print("> 若是改版：优先怀疑列表页换成 SPA / 条目进了 JSON 接口，"
          "而不是真的没内容。")


def _print_paging_hint(results: list) -> None:
    """挑出「列表条目数只有一页量级」的源。

    2026-10-01 第五轮：`shaanxi_em` 与 `sh_em` **都只抓了列表第一页**
    （真实 472 条 / 94 条）。当时健康检查只有「条数 / 正文中位」，两个源都是
    「有数据」且正文中位是全 24 源里最高的两个，判绿；唯一异常是条数少，
    而「这个站建站晚、事故通报本来就少」听起来完全合理。这一栏为那种情形而设。

    **这只是提示，不是结论**：单页源本来就该落在这里。要点在于——
    带分页的站若也落在这里，先怀疑分页没生效，而不是先相信「它只有一页」。
    """
    sized = [r for r in results if (r.get("listed") or 0) and r["listed"] <= _PAGE_SIZED]
    if not sized:
        return
    keys = "、".join(f"`{r['key']}`({r['listed']})" for r in sized)
    print(f"\n> 列表条目数在「一页量级」（≤{_PAGE_SIZED}）的源：{keys}")
    print("> 单页源落在这里属正常。**两个方向都值得看一眼**：")
    print("> ① 配了 `list_page_pattern` 却仍只有一页量级 → 分页 URL 可能没拼对；")
    print("> ② **没配** `list_page_pattern` → 「单页」可能只是一句没查过的注释。")
    print(">    去页面源码里找分页线索：`index_N.html`、`pagenav_N`、")
    print(">    `createPageHTML(总数, 页数, 当前索引, 文件名, 扩展名)`——最后这个")
    print(">    直接把总页数写在参数里。判据里少一维，那一维就不会报警。")


def _exit_code(results: list, only: str | None) -> int:
    """退出码判据。见模块 docstring「退出码」那一节。

    **`--only` 时「一个源都没抓到」不适用这条判据。** 它是为「跑全量却全灭」
    设计的——那说明探测本身坏了（没网、代理、sink 挂错）。限定几个源时，若
    挑的正好是已知有问题的源（排查时就是这么挑的），「0 个抓到」是**预期
    结果**。2026-10-01 第三十八轮实测：`--only jx_em,hub_em,jl_em,sc_em,
    esafety` 五个全灭 → 退 2 并打印「探测本身出了问题」，而它其实是这次探测
    最成功的一次（五个源各自的三格判据全对）。

    与「闭环」那一栏同族：判据的取值域里混进了「不适用」，而它和「失败」
    看起来一模一样。
    """
    if any(r["count"] for r in results):
        return 0
    return 0 if only else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="只探这几个源（逗号分隔的 source_key）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"每个源最多收几条（默认 {DEFAULT_LIMIT}）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help=f"单源秒数上限（默认 {DEFAULT_TIMEOUT_S}）")
    ap.add_argument("--json", help="把结果另存为 JSON（默认只打印）")
    ap.add_argument("--samples", type=int, default=0,
                    help="每个源额外打印前 N 条样例")
    args = ap.parse_args()

    try:
        specs = registry.all_sources()
    except Exception as e:  # noqa: BLE001
        print(f"✗ 取不到采集源清单：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        known = {s.key for s in specs}
        unknown = want - known
        if unknown:
            print(f"✗ 未登记的源：{sorted(unknown)}", file=sys.stderr)
            return 1
        specs = [s for s in specs if s.key in want]

    print(f"探测 {len(specs)} 个源，每源最多 {args.limit} 条、"
          f"上限 {args.timeout}s、并发 {CONCURRENCY}")
    print("口径：不写任何库；探测超时 ≠ 生产超时（生产 30 分钟，见 docstring）\n")

    work = Path(tempfile.mkdtemp(prefix="collector-health-"))
    tmp_root = work / "data"
    try:
        results = _run_all(specs, work, tmp_root, args.limit, args.timeout)
        if not results:
            print("✗ 没有任何结果", file=sys.stderr)
            return 1

        order = {s.key: i for i, s in enumerate(specs)}
        results.sort(key=lambda r: order.get(r["key"], 999))

        print("\n| 源 | 分组 | 结果 | 条数 | 列表 | 闭环 | 正文中位 | 耗时 | 通路 |")
        print("|---|---|---|---|---|---|---|---|---|")
        by_key = {s.key: s for s in specs}
        for r in results:
            spec = by_key.get(r["key"])
            chars = [x["chars"] for x in r["rows"]]
            el = f"{r['elapsed']:.1f}s" if r["elapsed"] is not None else "—"
            listed = r.get("listed")
            print(f"| `{r['key']}` | {spec.group if spec else '?'} | {_status_of(r)} "
                  f"| {_count_cell(r)} | {'—' if listed is None else listed} "
                  f"| {_closure_cell(r)} "
                  f"| {_median(chars)} | {el} | {r.get('note','')} |")
        print("\n> `条数` 带 `*` = 被早停截断，等于 `--limit`，**不是抓到了这么多**；"
              "不带 = 列表本就短，跑到底的真实候选数。两者不要横向比。")

        _print_closure_hint(results)
        _print_parse_empty_hint(results)
        _print_paging_hint(results)

        if args.samples:
            print()
            for r in results:
                if not r["rows"]:
                    continue
                print(f"**{r['key']}**")
                for row in r["rows"][:args.samples]:
                    print(f"  - {row['chars']:>6d} 字  {row['title']}")
                    print(f"    {row['url']}")

        hit = [r for r in results if r["count"]]
        err = [r for r in results if r.get("error")]
        to = [r for r in results if r.get("timed_out")]
        print(f"\n小结：{len(hit)}/{len(results)} 个源抓到了数据"
              f"（出错 {len(err)}、超时 {len(to)}）")

        if args.json:
            Path(args.json).write_text(json.dumps(
                {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "timeout_s": args.timeout, "limit": args.limit,
                 "results": [{k: v for k, v in r.items() if k != "rows"}
                             | {"samples": r["rows"]} for r in results]},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"报告：{args.json}")

        code = _exit_code(results, args.only)
        if code == 2:
            print("\n✗ 一个源都没抓到。这大概率不是「24 个站同时失效」，"
                  "而是探测本身出了问题：", file=sys.stderr)
            print("  先确认能上外网（curl -sI https://www.baidu.com），"
                  "再看上面每行的备注。", file=sys.stderr)
        elif not hit and args.only:
            print(f"\n注：本次是 `--only` 限定探测（{len(results)} 个源），"
                  f"「一个源都没抓到就判探测故障」这条不适用——"
                  f"排查时挑的本来就是已知有问题的源。")
        return code
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
