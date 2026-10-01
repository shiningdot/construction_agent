"""`collect()` 的两种返回形态，以及「注解说实话」这道静态闸门（第三十一轮）。

**背景**：15 个 `collect()` 实现分两类——5 个返回 `list[RawAccidentText]`
（产出在返回值里），9 个 store-backed 的源返回 `_make_stats()` 统计字典
（产出走 `raw_store`）。而**这 9 个的注解一律写着 `-> list[RawAccidentText]`**，
是错的；6 个的 docstring 甚至已经写着「返回统计字典」——**同一个函数里
注解与 docstring 互相矛盾**。

注解不影响运行，但它是契约。下游稍一疏忽就会**静默出错**：`len(dict)`
不报错、给的是键数（8），`for x in dict` 给的是键名字符串，`x.raw_text`
抛 `AttributeError` 被 `except Exception` 吞成 `logger.debug`——默认 INFO
级别下**一个字都看不见**。`pipeline.py` 撞的正是这个，实测日志长这样：

    Step 1 采集: 8 条原始文本
    Step 2 清洗: 0 条有效文本（过滤 8 条）

**看起来像清洗环节把 8 条全滤掉了**——去查清洗规则、去调最短长度阈值，
永远查不到真因。

本文件两组：
  A. 行为——真跑 `CollectorPipeline`，撞 dict 时不许出假条数、必须留 warning；
  B. 静态——注解与返回形态必须一致，新增源也拦得住。

**不联网**：假 collector，请求一次都不发。
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.base import (  # noqa: E402
    BaseCollector, CollectResult, RawAccidentText)

SOURCES_DIR = ROOT / "construction_agent" / "collector" / "sources"

# 照抄 `hebei_em._make_stats` 的形状（8 个键）——第三十轮实测该源 110 条列表
STATS_HEBEI = {"total_records": 110, "total_crawled": 23, "pending_review": 5,
               "filtered_out": 62, "cached_total": 23, "cached_new": 23,
               "cached_review": 5, "duration_seconds": 41.7}

GOOD_TEXT = ("某某工程“3·12”高处坠落事故调查报告。经查，作业人员未系安全绳，"
             "在脚手架上作业时坠落。") * 4


class _FakeDict(BaseCollector):
    """`collect()` 返回 stats dict——9 个 store-backed 源的真形态。"""

    def source_name(self) -> str:
        return "fake_dict_source"

    def collect(self) -> CollectResult:
        return dict(STATS_HEBEI)


class _FakeList(BaseCollector):
    """对照组：返回真列表——`mohurd` 那一形态。"""

    def source_name(self) -> str:
        return "fake_list_source"

    def collect(self) -> list[RawAccidentText]:
        return [RawAccidentText(title="某某工程高坠事故调查报告",
                                source="fake_list_source",
                                source_url="http://example.invalid/a",
                                raw_text=GOOD_TEXT)]


def _run(cls, tmp_path):
    """真跑 `CollectorPipeline.run()`。数据集落 tmp，不碰 `data/`。"""
    from construction_agent.collector.pipeline import CollectorPipeline

    p = CollectorPipeline(dataset_path=tmp_path / "ds.json")
    p.register_collector(cls())
    return p.run()


# --------------------------------------------------------------------------
# A 组：行为
# --------------------------------------------------------------------------


def test_撞到统计字典时不出假条数(tmp_path, caplog):
    """**核心断言**：`total_collected` 必须是 0，不是 `len(统计字典)`。

    改前这里是 8（那个字典恰好 8 个键），日志写「采集: 8 条原始文本」，
    而实际一条文本都没有。真源标称 110 条时，这个数字更没意义——它只是
    键的个数，与采集量毫无关系。
    """
    with caplog.at_level(logging.INFO):
        stats = _run(_FakeDict, tmp_path)

    assert stats["total_collected"] == 0, (
        f"又把统计字典的键数当条数了：{stats['total_collected']}")
    assert stats["total_after_clean"] == 0
    assert stats["added"] == 0
    assert stats["by_source"]["fake_dict_source"]["collected"] == 0


def test_撞到统计字典必须留一条warning(tmp_path, caplog):
    """**可见性**是这个测试的重点。

    改前那条路只有 `logger.debug("清洗失败: 'str' object has no attribute
    'raw_text'")`——用户跑 `run_collect.py` 时默认 INFO 级别，**一个字都看不到**，
    只看到「过滤 8 条」，会以为是清洗规则太严。
    """
    with caplog.at_level(logging.INFO):
        _run(_FakeDict, tmp_path)

    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warns, "撞到统计字典却一条 warning 都没留（改前正是如此）"
    text = " ".join(r.getMessage() for r in warns)
    assert "fake_dict_source" in text, "warning 里没说是哪个源"
    assert "统计字典" in text, f"warning 没点明原因：{text}"
    # 不许把键数当条数报出去
    assert "条数不是 8" in text, f"warning 应显式否认那个数字：{text}"


def test_返回列表的源照常处理(tmp_path, caplog):
    """对照组。没有这一条，上面两条可能只是在断言一个恒空的东西。"""
    with caplog.at_level(logging.INFO):
        stats = _run(_FakeList, tmp_path)

    assert stats["total_collected"] == 1
    assert stats["total_after_clean"] == 1
    assert stats["added"] == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_判据抓得住旧写法():
    """判据自检——否则上面两条可能测的是一条恒真的路径。

    这里直接复现改前 `pipeline.py` Step 1 的两行：`len(dict)` **不报错**，
    给 8；`for item in dict` 给键名字符串，取 `.raw_text` 才炸，而那个异常
    被 `except Exception` 吞掉。
    """
    d = dict(STATS_HEBEI)
    assert len(d) == 8, "前提变了：这个统计字典不再是 8 个键"
    assert list(d)[0] == "total_records", "遍历拿到的是键名，不是条目"

    with pytest.raises(AttributeError):
        for item in d:                    # 改前就是这个循环
            _ = item.raw_text             # noqa: B018


# --------------------------------------------------------------------------
# B 组：静态闸门——注解必须说实话
# --------------------------------------------------------------------------


def _collect_returns(tree: ast.Module) -> list[str] | None:
    """取 `collect()` 方法体里全部 `return` 表达式的源码形态。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "collect":
            continue
        return [ast.unparse(sub.value) if sub.value is not None else ""
                for sub in ast.walk(node)
                if isinstance(sub, ast.Return)]
    return None


def _annotation(tree: ast.Module) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "collect":
            return ast.unparse(node.returns) if node.returns else ""
    return None


def test_基类契约写着两种形态():
    """`BaseCollector.collect` 的注解必须能表达「也可能是统计字典」。

    这条盯的是**基类**——新写源的人第一个读的就是它。它若只写
    `list[RawAccidentText]`，照抄的人自然以为只有一种形态。
    """
    ann = _annotation(ast.parse(
        (ROOT / "construction_agent" / "collector" / "base.py").read_text("utf-8")))
    assert ann is not None, "`base.py` 里没有 `collect` 方法了？"
    assert "CollectResult" in ann, (
        f"基类 `collect()` 的注解是 {ann!r}，没有表达第二种形态")


def test_CollectResult确实能表达两种形态():
    """别名本身别写成半截——比如只剩 list 那一支。"""
    from typing import get_args, get_origin
    args = get_args(CollectResult)
    assert len(args) == 2, f"`CollectResult` 应当正好两支：{args}"
    assert get_origin(args[0]) is list, f"第一支应是 list，实际 {args[0]!r}"
    assert get_origin(args[1]) is dict, f"第二支应是 dict，实际 {args[1]!r}"


@pytest.mark.parametrize("name", sorted(
    p.stem for p in SOURCES_DIR.glob("*.py")))
def test_每个源的注解与返回形态一致(name):
    """**这道闸门挡的是「注解与 docstring 互相矛盾」那类写法。**

    单向判据（能确定的那一侧）：`return` 表达式里出现了 `_make_stats(`
    或字典字面量，注解就必须是 `CollectResult`。反过来（返回 `results`
    这种标识符）静态判断不了，不判——宁可漏，不要误报。
    """
    tree = ast.parse((SOURCES_DIR / f"{name}.py").read_text("utf-8"))
    rets = _collect_returns(tree)
    if rets is None:
        pytest.skip(f"{name}.py 里没有 collect 方法")
    ann = _annotation(tree) or ""

    returns_dict = any("_make_stats(" in r or r.strip().startswith("{")
                       for r in rets)
    if not returns_dict:
        return

    assert "CollectResult" in ann, (
        f"{name}.py 的 `collect()` 返回统计字典，注解却是 {ann!r}——"
        f"注解与实现不符（返回表达式：{rets[:2]}）"
    )


def test_静态闸门抓得住旧写法():
    """判据自检：拿第二十九轮那版 `beijing_em` 的注解形状喂进去，必须红。"""
    old = ast.parse(
        "class C:\n"
        "    def collect(self) -> list[RawAccidentText]:\n"
        "        return self._make_stats(1.0)\n")
    rets = _collect_returns(old) or []
    assert any("_make_stats(" in r for r in rets)
    ann = _annotation(old) or ""
    assert "CollectResult" not in ann, (
        "前提变了：这条自检样本本身已经带上了新注解")
