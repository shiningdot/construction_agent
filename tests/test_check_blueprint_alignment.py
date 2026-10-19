"""蓝图 ↔ 实现核查脚本（`scripts/check_blueprint_alignment.py`）的判据测试。

脚本跑一遍要解析全部蓝图 + 全部 web 源码，不适合整跑。这里钉住**判据**本身：
标题行算规定 / 正文行算提及、否定词把对照提及分出来、判断用完整行、只认蓝图文件。

这几条都是从真实误报里总结的（`/api/process` 那条被截断误伤过），改起来容易回归。
来由见 `docs/web/09_端到端对齐核查.md` §九。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_blueprint_alignment",
    ROOT / "scripts" / "check_blueprint_alignment.py")
assert _spec and _spec.loader
cba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cba)


@pytest.fixture
def md(tmp_path):
    """写一个临时蓝图文件，返回 (路径, 写入函数)。"""
    p = tmp_path / "0X_临时.md"

    def write(text: str) -> list[Path]:
        p.write_text(text, encoding="utf-8")
        return [p]

    return write


class Test标题与正文分档:
    def test_标题行算规定(self, md):
        spec, prose = cba.collect_doc_routes(md("### `GET /api/overview`\n"))
        assert list(spec) == ["/api/overview"]
        assert prose == {}

    def test_正文行算提及(self, md):
        spec, prose = cba.collect_doc_routes(md("页面加载时打 GET /api/filters 取一次。\n"))
        assert spec == {}
        assert list(prose) == ["/api/filters"]

    def test_变量段归一成同一形状(self, md):
        """蓝图写 `<id>`、实现写 `<accident_id>`，归一后要能对上。"""
        spec, _ = cba.collect_doc_routes(
            md("### `GET /api/accidents/<accident_id>/images`\n"))
        assert list(spec) == ["/api/accidents/<>/images"]

    def test_不留行尾标点(self, md):
        _, prose = cba.collect_doc_routes(md("见 GET /api/overview。下一句。\n"))
        assert list(prose) == ["/api/overview"]


class Test对照提及:
    def test_带否定词算对照提及(self):
        prose = {"/api/process": ["02.md:23  旧实现 POST /api/process 同步调用"]}
        retired, suspicious = cba.classify_prose(prose, real={}, spec={})
        assert retired == ["/api/process"] and suspicious == []

    def test_没有否定词算可疑(self):
        prose = {"/api/foo": ["04.md:9  调 POST /api/foo 提交"]}
        retired, suspicious = cba.classify_prose(prose, real={}, spec={})
        assert suspicious == ["/api/foo"] and retired == []

    def test_一处带否定词不足以给另一处背书(self):
        """同一条路径出现两次，只有一次说了「删除」——仍算可疑。

        否则一条路径只要在某一页被交代过一次「已删」，另一页的中性提及
        就再也报不出来了。
        """
        prose = {"/api/foo": ["06.md:283 原有一个 GET /api/foo，已删除",
                              "09.md:12  前端调用 GET /api/foo"]}
        retired, suspicious = cba.classify_prose(prose, real={}, spec={})
        assert suspicious == ["/api/foo"] and retired == []

    def test_已注册的不进可疑(self):
        prose = {"/api/overview": ["04.md:227  变化都触发 GET /api/overview"]}
        retired, suspicious = cba.classify_prose(
            prose, real={"/api/overview": [("GET", "x", "/api/overview")]}, spec={})
        assert (retired, suspicious) == ([], [])


class Test完整行:
    def test_否定词在长行末尾也能认出来(self, md):
        """`/api/process` 那行 90 字开外才出现「旧实现」。

        判据若用截断后的行文本，「旧实现」根本不在字符串里，一条正常的对照提及
        会被报成缺口。存完整行、显示才截断。
        """
        long_line = (
            "单条全链路耗时 40–50 秒（实测：字段提取 16–19 s + 致因编码 21–30 s）。"
            "这不是可以塞进 HTTP 请求的量级——必须异步任务 + 轮询。"
            "旧实现 `POST /api/process` 同步调用，必然超时。"
        )
        assert len(long_line) > 90, "这一条要验的就是超长行，先保证它够长"

        spec, prose = cba.collect_doc_routes(md(long_line))
        assert len(prose["/api/process"][0]) > 90, "存进来的行被截断了"
        retired, _ = cba.classify_prose(prose, real={}, spec=spec)
        assert retired == ["/api/process"]


class Test只认蓝图:
    def test_核查记录与待办不算蓝图(self):
        """09 是核查报告、10 是待执行清单，它们提到路由是在报告不是在规定。"""
        names = {p.name for p in cba.blueprint_files()}
        assert not {n for n in names if n.startswith(("09_", "10_"))}
        assert names, "至少要找到一份蓝图"

    def test_真实蓝图与实现双向对齐(self, capsys):
        """契约测试：这条红了说明有接口实现了没写进蓝图，或蓝图写了没实现。

        修法不是放宽判据——是补蓝图或补实现，让两边一致。
        """
        assert cba.main() == 0
