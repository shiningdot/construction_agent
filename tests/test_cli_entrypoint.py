"""CLI 入口的理论守卫：旧链路命令必须被拦下。

主库 2,623 条数据全部由 S1–S4 CAICS 链路产出，而 `process` / `batch` /
`interactive` / `hfacs-encode` / `dataset` 走的是 V1 四维度 + HFACS-CS 6 层。
两套体系的字段对不上，旧链路的产出无法并入主库。

这不是"风格问题"，是**看起来能跑通、结果却接不上**的那类坑：`process`
会正常打印一份漂亮的结果表，没有任何迹象表明它跟主库没关系。所以拦在
入口，并且由测试守着——将来有人出于好心把 `block_legacy` 去掉，这里会红。
"""

from __future__ import annotations

import pytest

from construction_agent.main import (
    _LEGACY_COMMANDS,
    block_legacy,
    main,
)

LEGACY = ["process", "batch", "interactive", "hfacs-encode", "dataset"]
PRODUCTION = ["extract", "web"]


@pytest.mark.parametrize("command", LEGACY)
def test_legacy_command_is_blocked(command, capsys):
    """旧命令默认退出码 2，并且把「该用哪个」说清楚。

    只断言退出码不够——拦住却不说替代命令，用的人只会加 `--help` 找路，
    找不到还会以为是自己参数写错了。所以顺带断言提示里出现了 `extract`。
    """
    with pytest.raises(SystemExit) as exc:
        block_legacy(command)
    assert exc.value.code == 2
    assert "extract" in capsys.readouterr().out


@pytest.mark.parametrize("command", LEGACY)
def test_legacy_command_allowed_with_env(command, monkeypatch):
    """显式放行后不再拦（用于复核封存前的历史结果）。"""
    monkeypatch.setenv("CAIEMS_ALLOW_LEGACY", "1")
    block_legacy(command)          # 不抛即通过


@pytest.mark.parametrize("command", PRODUCTION)
def test_production_command_is_not_blocked(command):
    """生产链路的命令不该被自己的守卫拦住。"""
    block_legacy(command)


def test_unknown_command_is_ignored():
    """`argparse` 未给子命令时 command 为 None，不该在这里炸。"""
    block_legacy(None)


def test_main_dispatches_through_the_guard(monkeypatch):
    """`main()` 真的接了这道守卫，而不只是有了个没人调的函数。"""
    called = []
    monkeypatch.setattr(
        "construction_agent.main.cmd_process", lambda args: called.append(args))
    with pytest.raises(SystemExit) as exc:
        main(["process", "某工地发生坍塌事故"])
    assert exc.value.code == 2
    assert called == [], "旧命令被拦下了就不该再执行"


def test_legacy_table_covers_the_switches_that_exist():
    """表里每一项都要是真实存在的子命令名。

    名字写错（比如 `hfacs_encode` 写成下划线）的后果最阴：守卫看着还在，
    那条命令却因为查不到表项而被静默放行。
    """
    from construction_agent.main import parse_args

    for command in _LEGACY_COMMANDS:
        args = parse_args([command] + _MIN_ARGS.get(command, []))
        assert args.command == command
        if command not in PRODUCTION:
            assert command in LEGACY


# 让 parse_args 能走到底所需的最小位置参数（表里没有的不用给）
_MIN_ARGS = {
    "process": ["文本"],
    "batch": ["文件.txt"],
    "dataset": ["stats"],
    "hfacs-encode": [],
    "interactive": [],
}
