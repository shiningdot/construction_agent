"""S3 提取器的 tool schema ↔ 结果 dataclass 一致性测试。

**为什么需要这组测试**（2026-09-30 补）：
`Step1BasicExtractor.extract()` 在 `_normalize_result()` 之后执行
`Step1Result(**normalized)`——用 `**` 展开。这意味着只要 tool schema 里
多出一个 dataclass 没有的字段，整条提取就会抛 TypeError 崩掉；
反过来，dataclass 有而 schema 没有的字段，则永远是默认值（静默缺失）。

本文件把这条隐式契约固化成断言。补 `direct_cause` 时正是踩这个坑：
schema 加了字段但 dataclass 没加，会直接崩。

另含一项「代码 ↔ 数据库」检查：S3 的 31 个字段必须都能在
`staging_accidents` 里找到落点，否则并入最终库时必然错位。
该检查依赖真实数据库，无库时自动跳过。
"""

from __future__ import annotations

import dataclasses

import pytest

from construction_agent.extraction.step1_basic_extractor import (
    STEP1_SYSTEM_PROMPT,
    STEP1_TOOL,
    Step1Result,
)

# 蓝图 docs/web/02_审核与提取.md 第 2.2 节声明的字段数（补 direct_cause 后）
EXPECTED_FIELD_COUNT = 31

# 详情页 A 档必显字段中唯一一个只由 S3 产出的内容列。
# 旧链路 claude_extractor.py:114 产出它，S1–S4 曾完全缺失，2026-09-30 补回。
REGRESSION_FIELDS = ("direct_cause",)


def _schema_properties() -> dict:
    return STEP1_TOOL["input_schema"]["properties"]


def _dataclass_fields() -> set[str]:
    """Step1Result 的公有字段名（下划线开头的是内部元数据，不进 schema）。"""
    return {f.name for f in dataclasses.fields(Step1Result) if not f.name.startswith("_")}


class TestSchemaDataclassContract:
    """`Step1Result(**normalized)` 的安全前提。"""

    def test_schema_字段数与蓝图一致(self):
        props = _schema_properties()
        assert len(props) == EXPECTED_FIELD_COUNT, (
            f"tool schema 实测 {len(props)} 个字段，蓝图声明 {EXPECTED_FIELD_COUNT} 个。"
            "改字段数时须同步更新 docs/web/02_审核与提取.md"
        )

    def test_dataclass_字段数与蓝图一致(self):
        assert len(_dataclass_fields()) == EXPECTED_FIELD_COUNT

    def test_schema_不多出_dataclass_没有的字段(self):
        """多出即崩溃：extract() 会执行 Step1Result(**normalized)。"""
        extra = set(_schema_properties()) - _dataclass_fields()
        assert not extra, (
            f"tool schema 有而 Step1Result 没有的字段: {sorted(extra)}。"
            "这会让 Step1Result(**normalized) 抛 TypeError"
        )

    def test_dataclass_不多出_schema_没有的字段(self):
        """多出即静默缺失：该字段永远拿不到 LLM 的返回值。"""
        extra = _dataclass_fields() - set(_schema_properties())
        assert not extra, f"Step1Result 有而 tool schema 没有的字段: {sorted(extra)}"

    def test_required_是_properties_子集(self):
        required = set(STEP1_TOOL["input_schema"].get("required", []))
        assert required <= set(_schema_properties()), (
            f"required 里的字段不在 properties 中: {sorted(required - set(_schema_properties()))}"
        )

    @pytest.mark.parametrize("field", REGRESSION_FIELDS)
    def test_关键字段三处齐全(self, field: str):
        """schema 有、dataclass 有、prompt 提到——三处缺一即失效。"""
        assert field in _schema_properties(), f"{field} 不在 tool schema"
        assert field in _dataclass_fields(), f"{field} 不在 Step1Result"
        assert field in STEP1_SYSTEM_PROMPT, f"{field} 未写进 STEP1_SYSTEM_PROMPT"


class TestStep1ResultBehavior:
    """结果对象本身的行为。"""

    def test_可用全默认值构造(self):
        """所有字段都可缺省——LLM 少返回一个字段不该导致崩溃。

        默认值有四种形态（2026-09-30 实测）：
          `""` 多数文本字段 ｜ `0` 计数 ｜ `None` 14 个 Optional 字段
          ｜ **哨兵字符串** 6 个（见下一条测试）
        这里只要求「构造得出来」，不强行统一默认值写法——统一默认值
        会牵动落库时的 NULL / 空串 / 哨兵值之别，是独立决策。
        """
        r = Step1Result()  # 构造本身不抛异常即为通过
        assert r.title == "" and r.deaths == 0

    def test_哨兵默认值清单未变(self):
        """6 个字段用「哨兵字符串」而非空值作默认。

        这不是 bug，但对**落库口径**有直接影响：若 S3 走默认值就落库，
        `weather` 会写成 `'未提及'` 而不是 NULL。主库实测该列 2,415 条 NULL、
        仅 46 条 `'未提及'` → 说明落库层当时做了归一。

        本测试锁定这份清单：改动其中任何一项，落库映射层与蓝图的
        「有值率」分档都要重新核对。
        """
        r = Step1Result()
        sentinels = {
            f.name: getattr(r, f.name)
            for f in dataclasses.fields(Step1Result)
            if not f.name.startswith("_")
            and getattr(r, f.name) not in ("", 0, None)
        }
        assert sentinels == {
            "weather": "未提及",
            "project_type": "其他/未知",
            "construction_phase": "未知",
            "contractor_type": "未提及",
            "collapse_subtype": "不适用",
            "report_type": "新闻报道",
        }, f"哨兵默认值有变动：{sentinels}。请同步核对落库映射层与蓝图 00 的字段分档"

    def test_构造时可接收_schema_的全部字段(self):
        """模拟 LLM 返回「每个字段都给值」的极端情况。"""
        payload = {k: "" for k in _schema_properties()}
        payload["deaths"] = 3
        payload["injuries"] = 1
        r = Step1Result(**payload)
        assert r.deaths == 3

    def test_to_dict_覆盖全部公有字段(self):
        d = Step1Result().to_dict()
        missing = _dataclass_fields() - set(d)
        assert not missing, f"to_dict() 漏掉字段: {sorted(missing)}"

    def test_to_dict_含_direct_cause(self):
        r = Step1Result(direct_cause="沟槽未按规定放坡致边坡坍塌")
        assert r.to_dict()["direct_cause"] == "沟槽未按规定放坡致边坡坍塌"


class TestStagingSchemaAlignment:
    """S3 字段 ↔ 工作库 staging_accidents 的落点检查。

    依赖真实数据库（系统 python 无 dotenv，故本组检查用 sqlite3 裸连，
    不 import 项目的 db 层）。无库或未建表时跳过。
    """

    @staticmethod
    def _staging_path():
        import os
        from pathlib import Path

        env = os.environ.get("CAIEMS_STAGING")
        if env:
            return Path(env).expanduser()
        # 默认与 construction_agent/web/staging_db.py 的解析规则一致
        return Path(__file__).resolve().parents[1] / "data" / "caiems_staging.db"

    def _stage_columns(self) -> set[str]:
        import sqlite3

        path = self._staging_path()
        if not path.exists():
            pytest.skip(f"工作库不存在: {path}（先跑 scripts/create_staging_db.py）")
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "staging_accidents" not in tables:
                pytest.skip("staging_accidents 表不存在")
            return {d[1] for d in con.execute("PRAGMA table_info(staging_accidents)")}
        finally:
            con.close()

    def test_S3_全部字段在_staging_accidents_有落点(self):
        cols = self._stage_columns()
        missing = set(_schema_properties()) - cols
        assert not missing, f"staging_accidents 缺少 S3 字段: {sorted(missing)}"
