"""子进程的输出**归谁**——`_probe_child` 的捕获 + 父进程的补前缀。

修的是一个把我骗了两次的读数陷阱（`docs/web/01_在线采集.md` §2.5.56）：
四个源并发跑、子进程 stdout 直通终端，于是**不带源名前缀的行**（搜索层的
`百度搜索「…」…`、基类的 `未找到 div.…`）只能按「挨着哪条 `[N/24]`」
来猜归属——而 `[N/24]` 是完成顺序，不是排列顺序。实测就把百度拦截行
归给了 `open_gov`，而 `open_gov.py` 里 `baidu` 一处都没有。

本文件测三件事：
  A `_probe_child` 真把输出关进 `<key>.stdout`（含 `logging` 走的那条路）
  B 关不住的情形（被 terminate）也留得住已写出的行——所以用行缓冲文件
  C 父进程补前缀：不缺、不重、缩进对，且**不串源**
"""
from __future__ import annotations

import importlib.util
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "health_output", _ROOT / "scripts" / "check_collector_health.py")
health = importlib.util.module_from_spec(_spec)
sys.modules["health_output"] = health
_spec.loader.exec_module(health)

# 真实发生过的那两行：一行带前缀、一行不带。归属的两种处境各取一个。
PLAIN = "百度搜索「工地坍塌 事故 通报」返回的是拦截页，不是结果页"
PREFIXED = "[hub_em] HTTP 412 且是 JS 挑战页，重试无用"

# 输出块相对 `[N/24]` 汇总行的缩进（与脚本里的 `_BLOCK_INDENT` 同一个值）。
_INDENT = " " * 10


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


class _FakeCollector:
    """只实现 `collect()`——`_probe_child_inner` 要的其它东西都取默认。

    构造签名里没有 `max_items`，所以「唯一原生支持早停的源」那一支会跳过；
    没有 `raw_store`，所以通路 A 不挂。
    """

    def __init__(self, lines=(), sleep=0.0):
        self._lines = lines
        self._sleep = sleep

    def collect(self):
        for ln in self._lines:
            print(ln)
        logging.getLogger("probe.fake").warning(PLAIN)
        sys.stdout.flush()
        if self._sleep:
            time.sleep(self._sleep)
        return []


@pytest.fixture
def fake_build(monkeypatch):
    """把 `registry.build` 换掉。monkeypatch 在 fork 前改 → 子进程继承。"""
    def _install(collector):
        monkeypatch.setattr(health.registry, "build",
                            lambda key, data_dir=None: (collector, None))
    return _install


@pytest.fixture
def bare_root_logger():
    """去掉 root 上的 handler，还原「健康检查进程里没有 logging 配置」这一实况。

    pytest 自己会往 root 挂捕获 handler，而**有 handler 时 `lastResort`
    就不生效**——不还原的话，这条测试测的是「pytest 的配置」而不是脚本的。
    """
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    try:
        yield
    finally:
        root.handlers = saved


def _stdout_of(work: Path, key: str) -> str:
    p = work / f"{key}.stdout"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# --------------------------------------------------------------------------
# A 捕获
# --------------------------------------------------------------------------


def test_子进程的print和logging都落进自己的文件(
        tmp_path, fake_build, bare_root_logger):
    fake_build(_FakeCollector(lines=[PREFIXED]))
    work = tmp_path / "work"
    work.mkdir()
    health._probe_child("hub_em", work, 3, tmp_path / "tmp_root")
    text = _stdout_of(work, "hub_em")
    assert PREFIXED in text, "带前缀的 print 没进 <key>.stdout"
    assert PLAIN in text, \
        "logging 走的那条路没进 <key>.stdout——lastResort 用的是 sys.stderr 吧？"


def test_捕获期间不漏到外层stdout(tmp_path, fake_build, bare_root_logger, capsys):
    """**这是本修法的全部意义**：漏出去那几行就又变成「挨着谁算谁的」。"""
    fake_build(_FakeCollector(lines=[PREFIXED]))
    work = tmp_path / "work"
    work.mkdir()
    health._probe_child("hub_em", work, 3, tmp_path / "tmp_root")
    leaked = capsys.readouterr()
    assert PREFIXED not in leaked.out and PREFIXED not in leaked.err
    assert PLAIN not in leaked.out and PLAIN not in leaked.err


def test_已经装了StreamHandler也要抓得住(tmp_path, fake_build):
    """只改 `sys.stdout/stderr` 抓不住**构造时绑死流**的 handler。

    零请求实测过：装了 `StreamHandler` 之后重定向抓到的是空串。
    所以 `_CaptureStreams` 要连 root 上的 handler 一起改道。
    """
    h = logging.StreamHandler()
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = [h]
    try:
        fake_build(_FakeCollector(lines=[]))
        work = tmp_path / "work"
        work.mkdir()
        health._probe_child("hub_em", work, 3, tmp_path / "tmp_root")
        assert PLAIN in _stdout_of(work, "hub_em"), \
            "root 上有 StreamHandler 时漏了——handler 没跟着改道"
    finally:
        root.handlers = saved


def test_捕获结束后把stdout还回去(tmp_path, fake_build, bare_root_logger):
    """宿主进程的 `sys.stdout` 被换掉不还，后面所有 print 都会消失。"""
    fake_build(_FakeCollector(lines=[]))
    work = tmp_path / "work"
    work.mkdir()
    before = sys.stdout
    health._probe_child("hub_em", work, 3, tmp_path / "tmp_root")
    assert sys.stdout is before


def test_输出文件打不开也不放弃探测(tmp_path, fake_build, bare_root_logger,
                            capsys):
    """落不了盘就直通。**不把日志设施问题升级成「这个源没数据」。**

    构造的是「work 目录在、但 `<key>.stdout` 这个位置建不出文件」——
    拿一个同名目录占位。若改成 work 目录本身不存在，内层连 `<key>.err`
    都写不了、探测本来就无从记账，测的就不是这条回退路径了。
    """
    fake_build(_FakeCollector(lines=[PREFIXED]))
    work = tmp_path / "work"
    (work / "hub_em.stdout").mkdir(parents=True)     # 目录占住文件名 → open 失败
    health._probe_child("hub_em", work, 3, tmp_path / "t")
    assert PREFIXED in capsys.readouterr().out, "没有直通，行被静默丢弃了"
    assert (work / "hub_em.elapsed").exists(), "回退路径没把探测跑完"


# --------------------------------------------------------------------------
# B 被 terminate 也留得住
# --------------------------------------------------------------------------


def test_超时的源也留得住已写出的行(tmp_path, fake_build):
    """用行缓冲文件而不是 `StringIO` 的全部理由。

    SIGTERM 不走 `finally`，`StringIO` 里攒的行一行都带不走——**恰恰是最
    需要日志的那次探测（超时）最拿不到日志**。
    """
    fake_build(_FakeCollector(lines=[PLAIN], sleep=60))
    work = tmp_path / "work"
    work.mkdir()
    ctx = mp.get_context("fork")          # 与 `_run_all` 同一个上下文
    p = ctx.Process(target=health._probe_child,
                    args=("news_aggregator", str(work), 3, str(tmp_path / "t")))
    p.start()
    try:
        deadline = time.monotonic() + 15
        cap = work / "news_aggregator.stdout"
        while time.monotonic() < deadline and not (
                cap.exists() and PLAIN in cap.read_text(encoding="utf-8")):
            time.sleep(0.1)
        assert cap.exists() and PLAIN in cap.read_text(encoding="utf-8"), \
            "terminate 之前那一行就没落盘——不是行缓冲？"
        p.terminate()
        p.join(5)
        assert not p.is_alive()
        assert PLAIN in cap.read_text(encoding="utf-8"), \
            "被 terminate 之后行没了——说明它攒在内存里"
    finally:
        if p.is_alive():
            p.kill()
            p.join(5)


# --------------------------------------------------------------------------
# C 父进程补前缀
# --------------------------------------------------------------------------


def test_不带前缀的行补上源名():
    got = health._attr_line("news_aggregator", PLAIN)
    assert got == " " * 10 + f"[news_aggregator] {PLAIN}"


def test_已经带前缀的行不重复补():
    got = health._attr_line("hub_em", PREFIXED)
    assert got.count("[hub_em]") == 1, f"补重了：{got!r}"
    assert got == " " * 10 + PREFIXED


def test_缩进一致所以两块并排也看得出归属():
    a = health._attr_line("hub_em", PREFIXED)
    b = health._attr_line("hub_em", PLAIN)
    assert len(a) - len(a.lstrip()) == len(b) - len(b.lstrip())
    # 补完之后两种行形如 `          [hub_em] …`——**肉眼看不到「原本带不带前缀」
     # 的差别**，这正是要的效果：读者不需要知道哪些行本来就有前缀。
    assert a.lstrip().startswith("[hub_em] ") and b.lstrip().startswith("[hub_em] ")


def test_空行被丢掉而不是占一行(tmp_path):
    (tmp_path / "x.stdout").write_text("a\n\n   \nb\n", encoding="utf-8")
    assert health._read_captured("x", tmp_path) == ["a", "b"]


def test_没有输出文件时给空表不抛(tmp_path):
    assert health._read_captured("没跑过的源", tmp_path) == []


def test_乱码字节不让整份日志读不出来(tmp_path):
    (tmp_path / "x.stdout").write_bytes(b"ok\n\xff\xfe bad\n")
    got = health._read_captured("x", tmp_path)
    assert got[0] == "ok" and len(got) == 2


def test_每个源的输出只来自自己那份文件(tmp_path):
    """**归属是结构性的**：父进程只读 `<key>.stdout`，物理上串不了。

    旧输出里没有这层隔离——四路输出写的是同一个终端。
    """
    (tmp_path / "a.stdout").write_text(f"{PLAIN}\n", encoding="utf-8")
    (tmp_path / "b.stdout").write_text(f"{PREFIXED}\n", encoding="utf-8")
    assert health._read_captured("a", tmp_path) == [PLAIN]
    assert health._read_captured("b", tmp_path) == [PREFIXED]


def test_汇总行在前输出块在后(tmp_path, capsys):
    (tmp_path / "hub_em.stdout").write_text(f"{PREFIXED}\n{PLAIN}\n",
                                            encoding="utf-8")
    res = {"key": "hub_em", "rows": [], "count": 0, "error": "",
           "elapsed": 0.2, "note": "", "listed": 0, "closure": None}
    health._print_result_block(res, tmp_path, 6, 24)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("  [ 6/24] hub_em"), lines[0]
    assert lines[1] == health._attr_line("hub_em", PREFIXED)
    assert lines[2] == health._attr_line("hub_em", PLAIN)
    assert len(lines) == 3


# --------------------------------------------------------------------------
# D 端到端：真跑 `_run_all`（两个源并发）
# --------------------------------------------------------------------------


class _Spec:
    def __init__(self, key):
        self.key = key
        self.group = "province"


def test_并发两源的输出各自成块不串行(tmp_path, monkeypatch, bare_root_logger,
                             capsys):
    """**真跑调度器**——直接调 `_print_result_block` 的测试抓不到
    「`_run_all` 忘了用它」这一种回归。

    两个源同时跑，各自打一行**不带前缀**的话。断言：两行都在，都带上了
    自己的源名，且各自紧跟在**自己那条汇总行**之后；**没有任何一行裸着
    出去**——这正是修法要消掉的东西。
    """
    key_a, key_b = "hub_em", "open_gov"
    lines = {key_a: "百度搜索「建筑施工 安全事故 死亡」返回的是拦截页",
             key_b: "未找到 div.zfxxgk_zdgkc"}

    class _C:
        def __init__(self, line):
            self._line = line

        def collect(self):
            print(self._line)
            time.sleep(0.05)          # 让两个子进程真的重叠
            return []

    monkeypatch.setattr(health.registry, "build",
                        lambda key, data_dir=None: (_C(lines[key]), None))
    work = tmp_path / "work"
    work.mkdir()
    health._run_all([_Spec(key_a), _Spec(key_b)], work, tmp_path / "t", 3, 30)

    out = capsys.readouterr().out.splitlines()
    for key in (key_a, key_b):
        idx = [i for i, l in enumerate(out)
               if l.startswith("  [") and f"] {key} " in l]
        assert len(idx) == 1, f"{key} 的汇总行不是恰好一条：{out}"
        body = out[idx[0] + 1] if idx[0] + 1 < len(out) else ""
        assert body.startswith(_INDENT + f"[{key}] "), \
            f"{key} 的下一行不是它自己的输出块：{body!r}"
    bare = [l for l in out if l in lines.values()]
    assert not bare, f"有行没带源名就出来了：{bare}"


# --------------------------------------------------------------------------
# E 机制本身的守卫
# --------------------------------------------------------------------------


def test_健康检查进程里root_logger没有挂handler():
    """捕获靠的是 `logging.lastResort` 现读 `sys.stderr`——**root 一旦挂了
    handler，这条路就绕开了**，输出会沉默地漏回终端。

    **必须在真子进程里断言，不能在 pytest 进程里断言**：pytest 的 logging
    插件自己会往 root 挂捕获 handler（实测 5 个），在本进程里 `== []` 恒假。
    第一版就是那么写的，报出来的是「pytest 的配置」而不是脚本的处境。

    将来谁在导入链里加了 `logging.basicConfig()`，这条会先炸。
    """
    import subprocess
    code = (
        "import importlib.util, logging, sys\n"
        f"spec = importlib.util.spec_from_file_location('h', {str(_ROOT / 'scripts' / 'check_collector_health.py')!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['h'] = m\n"
        "spec.loader.exec_module(m)\n"
        "print(len(logging.getLogger().handlers))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "0", \
        f"健康检查进程里 root 挂了 handler：{out.stdout.strip()} 个"
