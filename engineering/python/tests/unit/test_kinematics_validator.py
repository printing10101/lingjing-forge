"""KinematicsValidator（K001-K009 程序级运动学校验）单元测试。

背景：kinematics 模块此前零测试（2026-09 文档口径核对时发现），而它是
阶段 7"程序安全三道闸"的第一道闸，K001-K009 每个检查项都需要回归锚点。
机床画像用显式构造的小行程 profile，断言不依赖 machines.json 的内容。
"""

from __future__ import annotations

import json

import pytest

from app.simulation.kinematics.machine import (
    AxisLimits,
    MachineProfile,
    load_profile,
    profile_from_machine_dict,
)
from app.simulation.kinematics.validator import KinematicsValidator

pytestmark = [pytest.mark.unit]


@pytest.fixture
def validator() -> KinematicsValidator:
    profile = MachineProfile(
        machine_id="test_vmc",
        name="测试用三轴立加",
        axes={
            "X": AxisLimits(0.0, 850.0),
            "Y": AxisLimits(0.0, 500.0),
            "Z": AxisLimits(0.0, 500.0),
        },
        min_spindle_rpm=50.0,
        max_spindle_rpm=8000.0,
        max_cutting_feed=5000.0,
        tool_count=24,
    )
    return KinematicsValidator(profile=profile)


GOOD_PROGRAM = """\
G90
T01 M06
M03 S4000
G00 X50 Y50 Z20
G01 Z5 F200
X100 Y80
G00 Z30
M30
"""


def codes_of(validator: KinematicsValidator, text: str, **kwargs) -> list[str]:
    report = validator.validate(text, **kwargs)
    return [issue.code for issue in report.issues]


class TestValidProgram:
    def test_good_program_passes_clean(self, validator: KinematicsValidator):
        report = validator.validate(GOOD_PROGRAM)

        assert report.passed is True
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.move_count == 4
        assert report.lines_processed == 8
        # 首个运动的起点是初始位 (0,0,0)，也计入包络
        assert report.axis_extent["X"] == [0.0, 100.0]
        assert report.axis_extent["Y"] == [0.0, 80.0]
        assert report.axis_extent["Z"] == [0.0, 30.0]

    def test_report_to_dict_is_json_safe(self, validator: KinematicsValidator):
        report = validator.validate(GOOD_PROGRAM)
        payload = report.to_dict()

        assert payload["passed"] is True
        assert payload["machine_id"] == "test_vmc"
        # 必须能直接序列化（cam_report 落盘依赖）
        json.dumps(payload, ensure_ascii=False)


class TestTravelAndOffset:
    def test_k001_axis_over_travel(self, validator: KinematicsValidator):
        # 把超程快移放到最后一条运动：超程后机床停在该位置，其后每条
        # 运动都会再报一次 K001，故用末位运动保证"恰好 1 条"的断言清晰
        program = GOOD_PROGRAM.replace("G00 Z30", "G00 Z30\nG00 X900")
        report = validator.validate(program)

        assert report.passed is False
        k001 = [i for i in report.issues if i.code == "K001"]
        assert len(k001) == 1
        assert k001[0].line_no == 8
        assert "X" in k001[0].message

    def test_work_offset_shifts_machine_coords(self, validator: KinematicsValidator):
        # 程序坐标全部在行程内，但装夹偏移 +800 后 X 超程
        report = validator.validate(GOOD_PROGRAM, work_offset=(800.0, 0.0, 0.0))

        assert report.passed is False
        assert any(i.code == "K001" and "X" in i.message for i in report.issues)


class TestStateSemantics:
    def test_k002_cutting_without_spindle(self, validator: KinematicsValidator):
        program = GOOD_PROGRAM.replace("M03 S4000\n", "")
        assert "K002" in codes_of(validator, program)

    def test_k003_cutting_without_feed(self, validator: KinematicsValidator):
        program = GOOD_PROGRAM.replace("G01 Z5 F200", "G01 Z5")
        assert "K003" in codes_of(validator, program)

    def test_k004_feed_over_machine_limit(self, validator: KinematicsValidator):
        program = GOOD_PROGRAM.replace("F200", "F6000")
        assert "K004" in codes_of(validator, program)

    def test_k005_spindle_over_limit_is_program_level(self, validator: KinematicsValidator):
        report = validator.validate("G90\nS9000\nM30")

        assert report.passed is False
        k005 = [i for i in report.issues if i.code == "K005"]
        assert len(k005) == 1
        assert k005[0].line_no == 0  # 程序级问题，不定位到行

    def test_k007_cutting_without_tool(self, validator: KinematicsValidator):
        program = GOOD_PROGRAM.replace("T01 M06\n", "")
        assert "K007" in codes_of(validator, program)

    def test_k008_tool_beyond_magazine(self, validator: KinematicsValidator):
        report = validator.validate(GOOD_PROGRAM.replace("T01 M06", "T99 M06"))

        k008 = [i for i in report.issues if i.code == "K008"]
        assert len(k008) == 1
        assert "T99" in k008[0].message

    def test_k009_missing_end_is_warning_only(self, validator: KinematicsValidator):
        program = GOOD_PROGRAM.replace("M30\n", "")
        report = validator.validate(program)

        # 仅 warning 级不应判不通过
        assert report.passed is True
        assert report.warning_count == 1
        assert report.issues[0].code == "K009"
        assert report.issues[0].severity == "warning"


class TestRapidBelowStock:
    def test_k006_rapid_plunge_below_stock_top(self, validator: KinematicsValidator):
        program = "G90\nT01 M06\nM03 S4000\nG00 X50 Y50 Z20\nG00 Z5\nM30"
        report = validator.validate(program, stock_top_z=10.0)

        assert report.passed is False
        k006 = [i for i in report.issues if i.code == "K006"]
        assert len(k006) == 1
        assert "低于毛坯顶面" in k006[0].message

    def test_k006_lateral_rapid_below_stock_top(self, validator: KinematicsValidator):
        # G01 切入到 Z5 后在毛坯下方横向快移
        program = "G90\nT01 M06\nM03 S4000\nG00 X50 Y50 Z20\nG01 Z5 F200\nG00 X100 Y80\nM30"
        report = validator.validate(program, stock_top_z=10.0)

        k006 = [i for i in report.issues if i.code == "K006"]
        assert len(k006) == 1
        assert "横向" in k006[0].message

    def test_k006_skipped_without_stock_top(self, validator: KinematicsValidator):
        # 未提供 stock_top_z 时跳过 K006（不误报）
        program = "G90\nT01 M06\nM03 S4000\nG00 X50 Y50 Z20\nG00 Z5\nM30"
        report = validator.validate(program)

        assert not any(i.code == "K006" for i in report.issues)


class TestMachineProfile:
    def test_profile_from_machine_dict_maps_fields(self):
        profile = profile_from_machine_dict(
            {
                "id": "vmc_650",
                "name": "测试机床",
                "travel_xyz_mm": [650, 450, 500],
                "spindle_speed_rpm": [60, 12000],
                "feed_cutting_max_mmmin": 4000,
                "tool_changer_capacity": 16,
            }
        )

        assert profile.machine_id == "vmc_650"
        assert profile.axes["X"].max_mm == 650.0
        assert profile.axes["Z"].contains(500.0) is True
        assert profile.axes["Z"].contains(500.1) is False
        assert profile.max_spindle_rpm == 12000.0
        assert profile.tool_count == 16

    def test_profile_rejects_missing_id_and_bad_travel(self):
        with pytest.raises(ValueError, match="id"):
            profile_from_machine_dict({"travel_xyz_mm": [1, 1, 1]})
        with pytest.raises(ValueError, match="travel_xyz_mm"):
            profile_from_machine_dict({"id": "m1"})
        with pytest.raises(ValueError, match="非法"):
            profile_from_machine_dict({"id": "m1", "travel_xyz_mm": [650, 0, 500]})

    def test_load_profile_falls_back_to_builtin(self):
        profile = load_profile("no_such_machine__for_test_only")

        assert profile.machine_id == "vmc_850_builtin"
        assert set(profile.axes) == {"X", "Y", "Z"}
