"""字段级对齐核查脚本（`scripts/check_field_alignment.py`）的判据测试。

脚本整个跑一遍要起 app 打全量请求，慢，不适合放进单元测试。这里只钉住**判据**
本身——剥噪音、抽属性读取、找空数组位置。这三条规则都是从真实噪音里一条条总结
出来的（注释里的文件名、赋全局那几行、空数组），改起来很容易回归。

三条改动的来由见 `docs/web/09_端到端对齐核查.md` §七。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_field_alignment", ROOT / "scripts" / "check_field_alignment.py")
assert _spec and _spec.loader
cfa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfa)


class Test剥噪音:
    def test_块注释里的文件名不算属性读取(self):
        """`/* … overview.js 里还有一份… */` 曾送进来一个假的 `.js`。"""
        src = "/* 这一份和 overview.js 里的等价 */\nvar a = b.count;"
        assert cfa.attrs_in(cfa.strip_noise(src)) == {"count"}

    def test_行注释不算(self):
        src = "var a = b.count;  // 见 overview.js 的说明\n"
        assert cfa.attrs_in(cfa.strip_noise(src)) == {"count"}

    def test_字符串里的点号不算(self):
        assert cfa.attrs_in(cfa.strip_noise("el('a', {text: 'x.y'});")) == set()

    def test_跨行模板串不算(self):
        """模板串常用来拼 HTML，里面全是属性名，且会跨行。"""
        src = "const t = `\n  <div class=\"x\">\n    ${a.count}\n  </div>\n`;"
        assert cfa.attrs_in(cfa.strip_noise(src)) == set()


class Test抽属性读取:
    def test_普通读取算(self):
        assert cfa.attrs_in("x = a.death_count;") == {"death_count"}

    def test_赋值左侧不算(self):
        """`global.num = num` 里的 `.num` 是定义，不是读取。"""
        assert cfa.attrs_in("global.num = num;") == set()

    def test_相等比较里的读取要算(self):
        """排除单等号时不能把 `==` 一起排掉——那是真读取。"""
        assert cfa.attrs_in("if (a.count == b.count) {}") == {"count"}

    def test_方法调用不算(self):
        assert cfa.attrs_in("list.map(fn); obj.then(cb);") == set()

    def test_数字字面量不算(self):
        assert cfa.attrs_in("var x = 1.5 + 2.5;") == set()


class Test找空数组:
    def test_空数组报出它的路径(self):
        got = cfa.empty_arrays({"data": [], "total": 0})
        assert got == {"data"}

    def test_嵌套空数组报全路径(self):
        got = cfa.empty_arrays({"a": {"b": []}})
        assert got == {"a.b"}

    def test_非空数组不报(self):
        got = cfa.empty_arrays({"data": [{"accident_id": "x"}]})
        assert got == set()

    def test_元素里的空数组照样报(self):
        """列表本身有元素，但元素的某个字段是空数组——那段元素字段也核不到。"""
        got = cfa.empty_arrays({"data": [{"images": [], "title": "x"}]})
        assert got == {"data.images"}

    def test_普通标量结构不报(self):
        assert cfa.empty_arrays({"a": 1, "b": "x", "c": None}) == set()


class Test展平:
    def test_路径到叶子(self):
        got = cfa.flatten({"a": {"b": {"c": 1}}}, "", set())
        assert got == {"a", "a.b", "a.b.c"}

    def test_数组元素按同结构展开(self):
        got = cfa.flatten({"items": [{"id": 1, "n": 2}]}, "", set())
        assert "items.id" in got and "items.n" in got
