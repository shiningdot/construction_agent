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

## 退出码

- `0` —— 探测跑完，**且至少有一个源抓到了数据**（结论已产出）
- `2` —— **一个源都没抓到**。这大概率不是「全网 24 站同时失效」，而是探测
  本身出了问题（没网、代理、sink 挂错）。跟 `build_image_manifest.py`
  「扫到 0 张退 2」同一族判据：两头都真的判据等于没有判据。
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

    def _accept(self, title, url, text) -> None:
        self.count += 1
        # 逐条落盘 + flush：父进程超时 kill 之后，已经收到的条目还能读出来。
        # 「120 秒收到 47 条」和「120 秒一条没有」是完全不同的两个结论。
        self._fh.write(json.dumps(
            {"title": (title or "")[:80], "url": url or "",
             "chars": len(text or "")}, ensure_ascii=False) + "\n")
        self._fh.flush()
        if self.count >= self.limit:
            raise _ProbeStop

    def __call__(self, record: dict) -> None:
        self._accept(record.get("title"), record.get("source_url"),
                     record.get("content"))

    def feed(self, item) -> None:
        self._accept(item.title, item.source_url, item.raw_text)

    def close(self) -> None:
        self._fh.close()


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
    try:
        sink = _Sink(work / f"{key}.jsonl", limit)
        data_dir = tmp_root / key
        data_dir.mkdir(parents=True, exist_ok=True)
        collector, _spec = registry.build(key, data_dir=data_dir)

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
    if res.get("timed_out"):
        return "超时"
    if res.get("error"):
        return "出错"
    return "有数据" if res["count"] else "空"


def _fmt_line(res: dict) -> str:
    st = _status_of(res)
    el = f"{res['elapsed']:.1f}s" if res["elapsed"] is not None else "—"
    chars = [r["chars"] for r in res["rows"]]
    med = _median(chars)
    listed = res.get("listed")
    extra = f"正文中位 {med} 字" if res["count"] else (res.get("error") or "")[:60]
    tail = "" if listed is None else f"列表 {listed}"
    return f"{res['key']:20s} {st:4s} {res['count']:2d} 条 {el:>7s}  {tail:>10s}  {extra}"


# 典型的一页容量。TRS 站的列表页多是 15 / 19 / 20 条一页。
_PAGE_SIZED = 25


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

        print("\n| 源 | 分组 | 结果 | 条数 | 列表 | 正文中位 | 耗时 | 通路 |")
        print("|---|---|---|---|---|---|---|---|")
        by_key = {s.key: s for s in specs}
        for r in results:
            spec = by_key.get(r["key"])
            chars = [x["chars"] for x in r["rows"]]
            el = f"{r['elapsed']:.1f}s" if r["elapsed"] is not None else "—"
            listed = r.get("listed")
            print(f"| `{r['key']}` | {spec.group if spec else '?'} | {_status_of(r)} "
                  f"| {r['count']} | {'—' if listed is None else listed} "
                  f"| {_median(chars)} | {el} | {r.get('note','')} |")

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

        if not hit:
            print("\n✗ 一个源都没抓到。这大概率不是「24 个站同时失效」，"
                  "而是探测本身出了问题：", file=sys.stderr)
            print("  先确认能上外网（curl -sI https://www.baidu.com），"
                  "再看上面每行的备注。", file=sys.stderr)
            return 2
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
