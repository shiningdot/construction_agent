"""提取模块测试。"""

from construction_agent.models.accident import (
    Accident,
    AccidentType,
    SeverityLevel,
    HumanFactor,
    MachineFactor,
    ManagementFactor,
    EnvironmentalFactor,
    AccidentExtractionResult,
)


class TestAccidentModel:
    """事故数据模型测试。"""

    def test_accident_creation(self):
        """测试创建事故记录。"""
        accident = Accident(
            title="深圳某工地坍塌事故",
            accident_type="坍塌",
            severity="较大事故",
            deaths=3,
            injuries=2,
            location="广东省深圳市",
            human_factors=[
                HumanFactor(category="违章操作", description="工人未按规程操作")
            ],
        )
        assert accident.title == "深圳某工地坍塌事故"
        assert accident.accident_type == "坍塌"
        assert accident.deaths == 3
        assert len(accident.human_factors) == 1

    def test_to_dict(self):
        """测试转字典。"""
        a = Accident(title="test", accident_type="坍塌", severity="一般事故", deaths=1, injuries=0)
        d = a.to_dict()
        assert d["title"] == "test"
        assert d["deaths"] == 1

    def test_to_flat_dict(self):
        """测试转扁平字典（CSV 用）。"""
        a = Accident(title="test", accident_type="坍塌", severity="一般事故", deaths=1, injuries=0)
        flat = a.to_flat_dict()
        assert "human_factors" in flat
        assert "management_factors" in flat

    def test_to_json(self):
        """测试 JSON 序列化。"""
        a = Accident(title="测试事故", accident_type="坍塌", severity="一般事故", deaths=1, injuries=0)
        json_str = a.to_json()
        assert "测试事故" in json_str
        assert "坍塌" in json_str


class TestAccidentExtractionResult:
    """提取结果模型测试。"""

    def test_to_accident(self):
        """测试提取结果转完整模型。"""
        result = AccidentExtractionResult(
            title="测试事故",
            accident_type="坍塌",
            severity="一般事故",
            deaths=1,
            injuries=0,
            human_factors=[
                {"category": "违章操作", "description": "违规施工"}
            ],
            summary="事故经过",
        )
        accident = result.to_accident(raw_input="用户输入的原始信息")
        assert isinstance(accident, Accident)
        assert accident.title == "测试事故"
        assert accident.raw_input == "用户输入的原始信息"
        assert len(accident.human_factors) == 1
        assert accident.human_factors[0].category == "违章操作"

    def test_to_accident_with_date(self):
        """测试带日期的转换。"""
        result = AccidentExtractionResult(
            title="带日期事故",
            date="2024-03-15",
            accident_type="坍塌",
            severity="较大事故",
            deaths=3,
            injuries=0,
        )
        accident = result.to_accident()
        assert accident.date is not None
        assert accident.date.isoformat() == "2024-03-15"

    def test_to_accident_invalid_date(self):
        """测试无效日期不会崩溃。"""
        result = AccidentExtractionResult(
            title="无效日期",
            date="not-a-date",
            accident_type="坍塌",
            severity="一般事故",
            deaths=1,
            injuries=0,
        )
        accident = result.to_accident()
        assert accident.date is None  # 无效日期返回 None
