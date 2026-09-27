"""SafetyValidator 单元测试（Phase 1b：⑤ NumCraft 思路——统一多层安全校验）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_safety_validator.py -v --no-cov
"""

from __future__ import annotations

import pytest

from app.agent.orchestrator import AgentOrchestrator, OrchestratorMode
from app.chatter_prediction._types import FeatureChatterResult
from app.gcode_generation.safety_validator import (
    ERR_AXIS_TRAVEL_EXCEEDED,
    ERR_CUTTING_DEPTH_EXCEEDS_LIMIT,
    ERR_EMPTY_PROGRAM,
    ERR_INVALID_DEPTH,
    ERR_NEGATIVE_FEED,
    ERR_NO_PROGRAM_END,
    ERR_SPINDLE_OUT_OF_RANGE,
    WARN_INVALID_CUTTING_FORCE_COEFF,
    WARN_MISSING_LIMIT_DEPTH,
    WARN_MISSING_TOOL,
    WARN_SAFETY_MARGIN_INSUFFICIENT,
    WARN_UNKNOWN_G_M_CODE,
    WARN_UNSTABLE_FEATURE,
    SafetyValidator,
)


def _make_feat(
    *,
    rpm: float = 8000.0,
    axial: float = 1.0,
    limit: float = 2.0,
    stable: bool = True,
    tool_id: str = "T01-Φ10-endmill",
    ks: float = 1800.0,
) -> FeatureChatterResult:
    return FeatureChatterResult(
        feature_id="F1",
        feature_type="平面铣",
        material_id="45#钢",
        spindle_rpm=rpm,
        axial_depth_mm=axial,
        limit_depth_mm=limit,
        stable=stable,
        stability_margin=0.5,
        method="neural_network",
        ltc_active=True,
        tool_id=tool_id,
        cutting_force_coeff=ks,
    )


class TestFeatureValidation:
    def setup_method(self) -> None:
        self.validator = SafetyValidator()

    def test_valid_feature_passes(self) -> None:
        report = self.validator.validate_features([_make_feat()])
        assert report.is_valid, report.summary()
        assert report.warnings == []

    def test_spindle_out_of_range_error_with_clamp(self) -> None:
        report = self.validator.validate_features([_make_feat(rpm=50000)])
        assert not report.is_valid
        assert ERR_SPINDLE_OUT_OF_RANGE in report.error_codes
        issue = report.errors[0]
        assert issue.recommended == 24000.0  # clamp 到默认上限

    def test_cutting_depth_exceeds_limit_error(self) -> None:
        report = self.validator.validate_features([_make_feat(axial=3.0, limit=2.0)])
        assert not report.is_valid
        assert ERR_CUTTING_DEPTH_EXCEEDS_LIMIT in report.error_codes

    def test_margin_warning_not_error(self) -> None:
        report = self.validator.validate_features([_make_feat(axial=1.7, limit=2.0)])  # 0.85 > 0.8
        assert report.is_valid
        assert WARN_SAFETY_MARGIN_INSUFFICIENT in report.warning_codes

    def test_invalid_depth_error(self) -> None:
        report = self.validator.validate_features([_make_feat(axial=0.0)])
        assert not report.is_valid
        assert ERR_INVALID_DEPTH in report.error_codes

    def test_missing_limit_warning(self) -> None:
        report = self.validator.validate_features([_make_feat(limit=0.0)])
        assert report.is_valid
        assert WARN_MISSING_LIMIT_DEPTH in report.warning_codes

    def test_unstable_feature_warning(self) -> None:
        report = self.validator.validate_features([_make_feat(stable=False)])
        assert WARN_UNSTABLE_FEATURE in report.warning_codes

    def test_missing_tool_warning(self) -> None:
        report = self.validator.validate_features([_make_feat(tool_id="")])
        assert WARN_MISSING_TOOL in report.warning_codes

    def test_negative_ks_warning(self) -> None:
        report = self.validator.validate_features([_make_feat(ks=-1.0)])
        assert WARN_INVALID_CUTTING_FORCE_COEFF in report.warning_codes

    def test_clamped_parameters(self) -> None:
        clamped = self.validator.get_clamped_parameters(_make_feat(rpm=99999))
        assert clamped["spindle_rpm"] == 24000.0


class TestGCodeValidation:
    def setup_method(self) -> None:
        self.validator = SafetyValidator()

    def test_empty_program_error(self) -> None:
        report = self.validator.validate_gcode_text("")
        assert not report.is_valid
        assert ERR_EMPTY_PROGRAM in report.error_codes

    def test_missing_m30_error(self) -> None:
        gcode = "O1000\nG90 G54\nM3 S8000\nG0 X0 Y0 Z50\nG1 F500 Z-1"
        report = self.validator.validate_gcode_text(gcode, "fanuc_0i")
        assert not report.is_valid
        assert ERR_NO_PROGRAM_END in report.error_codes

    def test_negative_feed_error(self) -> None:
        gcode = "O1000\nG1 F-500 X10"
        report = self.validator.validate_gcode_text(gcode, "fanuc_0i")
        assert not report.is_valid
        assert ERR_NEGATIVE_FEED in report.error_codes

    def test_unknown_gcode_warning(self) -> None:
        gcode = "O1000\nG999 X10\nM30"
        report = self.validator.validate_gcode_text(gcode, "fanuc_0i")
        assert report.is_valid
        assert WARN_UNKNOWN_G_M_CODE in report.warning_codes

    def test_valid_fanuc_program_passes(self) -> None:
        gcode = (
            "O1000\n"
            "G90 G21 G17 G94 G54\n"
            "G0 X-10 Y-10 Z80\n"
            "M3 S8000 M8\n"
            "G1 Z50 F1000\n"
            "G1 X10 Y10 F2000\n"
            "G0 Z80\n"
            "M5 M9\n"
            "M30"
        )
        report = self.validator.validate_gcode_text(gcode, "fanuc_0i")
        assert report.is_valid, report.summary()


class TestValidateAll:
    def test_axis_travel_error(self) -> None:
        validator = SafetyValidator()
        report = validator.validate_all(
            chatter_results=[_make_feat()],
            gcode_text="O1000\nM30",
            safe_z=9999.0,  # 超出 Z 软限位
        )
        assert not report.is_valid
        assert ERR_AXIS_TRAVEL_EXCEEDED in report.error_codes

    def test_aggregates_feature_and_gcode(self) -> None:
        validator = SafetyValidator()
        report = validator.validate_all(
            chatter_results=[_make_feat(rpm=50000), _make_feat(axial=3.0, limit=2.0)],
            gcode_text="",
        )
        assert ERR_SPINDLE_OUT_OF_RANGE in report.error_codes
        assert ERR_CUTTING_DEPTH_EXCEEDS_LIMIT in report.error_codes


class TestOrchestratorStep:
    def test_dxf_to_gcode_chain_includes_safety(self) -> None:
        orch = AgentOrchestrator()
        steps = orch._get_pipeline_steps("dxf_to_gcode", {}, OrchestratorMode.SEQUENTIAL)
        names = [name for name, _ in steps]
        assert "validate_safety" in names
        assert names[-1] == "validate_safety"

    @pytest.mark.asyncio
    async def test_safety_step_rejects_bad_gcode(self) -> None:
        """W1.1 新契约：校验不通过不再抛异常中断管线，而是返回结构化失败
        供修复闭环规划修复动作（预算耗尽后由编排器标记 FAILED 转人工）。"""
        orch = AgentOrchestrator()
        out = await orch._step_validate_safety(
            {"gcode": "O1000\nG1 F500", "controller_type": "fanuc_0i"}, {}
        )
        assert out["safety_valid"] is False
        assert out["status"] == "validation_failed"
        assert "safety_report" in out

    @pytest.mark.asyncio
    async def test_safety_step_accepts_good_gcode(self) -> None:
        orch = AgentOrchestrator()
        out = await orch._step_validate_safety(
            {"gcode": "O1000\nG90 G54\nG0 X0 Y0 Z80\nM3 S8000\nM30", "controller_type": "fanuc_0i"},
            {},
        )
        assert out["safety_valid"] is True
        assert out["warning_count"] >= 0
