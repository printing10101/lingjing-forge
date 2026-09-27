"""统一评分服务测试（自进化 M0 ·「一个分数」出口）。

覆盖：语法维度（硬门禁/错误码透出）、几何维度（注入桩 + 真实体素
仿真）、物理维度（注入桩/仿真失败降级）、复合分权重归一、EVAL_INCOMPLETE、
判分器永不抛异常。
"""

import pytest

from app.cam_validation.voxel_validator import VoxelValidationReport
from app.evaluation import (
    EvaluationService,
    GcodeEvaluationRequest,
    evaluate_gcode,
)
from app.gcode_generation.safety_validator import SafetyValidator
from app.process_planning.sim_integration import SimulationResult

VALID_GCODE = "O1000\nG01 X10 F800\nM30"
INVALID_GCODE = "G01 X10 F-5\nM30"  # 负进给（error），无程序号（warning）

# 体素真实验证所需的毛坯/安全高度
_STOCK = dict(safe_z=10.0, stock_top_z=5.0, stock_length=20.0, stock_width=20.0, stock_height=5.0)


def _voxel_report(passed=True, severity="none") -> VoxelValidationReport:
    return VoxelValidationReport(
        passed=passed,
        engine="python",
        voxel_size_mm=1.0,
        total_segments=4,
        cutting_segments=2,
        collision_count=0 if passed else 2,
        collision_blocks=[3] if not passed else [],
        collision_positions=[[1.0, 1.0, -0.5]] if not passed else [],
        severity=severity,
        removed_voxel_count=10,
        voxel_count=100,
        duration_seconds=0.01,
        warnings=[],
    )


class _FakeVoxel:
    def __init__(self, report: VoxelValidationReport):
        self.report = report
        self.calls: list[dict] = []

    def validate(self, **kwargs):
        self.calls.append(kwargs)
        return self.report


class _FakeSim:
    def __init__(self, result: SimulationResult | Exception):
        self.result = result
        self.calls: list[dict] = []

    def run_simulation(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# ---------------------------------------------------------------------------
# 语法维度（唯一必评维度，硬门禁）
# ---------------------------------------------------------------------------


class TestSyntaxDimension:
    def test_valid_program_passes(self):
        report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE))
        assert report.passed is True
        assert report.composite_score == 100.0
        syntax = report.dimension("syntax")
        assert syntax.available and syntax.passed and syntax.score == 100.0
        assert report.failure_classes == []

    def test_negative_feed_hard_fails_with_error_code(self):
        report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text=INVALID_GCODE))
        assert report.passed is False
        syntax = report.dimension("syntax")
        assert syntax.passed is False
        assert syntax.score < 100.0
        assert "NEGATIVE_FEED" in report.failure_classes

    def test_missing_program_end(self):
        report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text="G01 X10 F800"))
        assert report.passed is False
        assert "NO_PROGRAM_END" in report.failure_classes

    def test_empty_program_hard_fails(self):
        report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text=""))
        assert report.passed is False
        assert "EMPTY_PROGRAM" in report.failure_classes


# ---------------------------------------------------------------------------
# 几何维度（注入桩）
# ---------------------------------------------------------------------------


class TestGeometryDimension:
    def test_passed_geometry(self):
        svc = EvaluationService(voxel_validator=_FakeVoxel(_voxel_report(passed=True)))
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, **_STOCK))
        geometry = report.dimension("geometry")
        assert geometry.available and geometry.passed and geometry.score == 100.0
        assert report.passed is True

    def test_warning_collision_scores_60_and_hard_fails(self):
        svc = EvaluationService(voxel_validator=_FakeVoxel(_voxel_report(passed=False, severity="warning")))
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, **_STOCK))
        geometry = report.dimension("geometry")
        assert geometry.available and geometry.passed is False
        assert geometry.score == 60.0
        assert report.passed is False
        assert "GEOMETRY_COLLISION" in report.failure_classes

    def test_critical_collision_scores_20(self):
        svc = EvaluationService(voxel_validator=_FakeVoxel(_voxel_report(passed=False, severity="critical")))
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, **_STOCK))
        assert report.dimension("geometry").score == 20.0

    def test_missing_stock_inputs_skips_dimension(self):
        report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE))
        geometry = report.dimension("geometry")
        assert geometry.available is False
        assert "缺少几何输入" in geometry.skip_reason
        # 复合分权重归一：仅语法 → 仍 100
        assert report.composite_score == 100.0
        assert "EVAL_INCOMPLETE" not in report.failure_classes

    def test_voxel_error_is_captured_not_raised(self):
        svc = EvaluationService(voxel_validator=_FakeVoxel(_voxel_report(passed=True)))

        def _boom(**kwargs):
            raise RuntimeError("voxel down")

        svc._voxel_validator = type("_V", (), {"validate": staticmethod(_boom)})()
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, **_STOCK))
        geometry = report.dimension("geometry")
        assert geometry.status == "error"
        assert "voxel down" in geometry.skip_reason
        assert "EVAL_INCOMPLETE" in report.failure_classes
        # 几何 error 不阻断硬门禁（与生产口径一致：文本安全是硬门禁）
        assert report.passed is True


# ---------------------------------------------------------------------------
# 物理维度（注入桩）
# ---------------------------------------------------------------------------


_CUTTING = {"spindle_rpm": 6000, "feed_rate": 800, "depth_of_cut": 2.0}


class TestPhysicsDimension:
    def test_physics_score_in_composite(self):
        sim = _FakeSim(SimulationResult(status="success", passed=True, score=88.0))
        svc = EvaluationService(simulator=sim)
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, cutting_params=dict(_CUTTING)))
        physics = report.dimension("physics")
        assert physics.available and physics.passed and physics.score == 88.0
        # 语法 100×0.5 + 物理 88×0.2，权重归一 /0.7
        assert report.composite_score == pytest.approx((100 * 0.5 + 88 * 0.2) / 0.7)
        assert report.passed is True

    def test_physics_fail_is_advisory_not_hard_gate(self):
        sim = _FakeSim(SimulationResult(status="success", passed=False, score=40.0))
        svc = EvaluationService(simulator=sim)
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, cutting_params=dict(_CUTTING)))
        assert report.passed is True  # 物理仅决策辅助
        assert "PHYSICS_NOT_RECOMMENDED" in report.failure_classes

    def test_sim_timeout_maps_to_eval_incomplete(self):
        sim = _FakeSim(SimulationResult(status="timeout", error_message="仿真超时"))
        svc = EvaluationService(simulator=sim)
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, cutting_params=dict(_CUTTING)))
        physics = report.dimension("physics")
        assert physics.status == "error"
        assert "仿真超时" in physics.skip_reason
        assert "EVAL_INCOMPLETE" in report.failure_classes

    def test_simulator_raise_is_captured(self):
        svc = EvaluationService(simulator=_FakeSim(RuntimeError("sim down")))
        report = svc.evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE, cutting_params=dict(_CUTTING)))
        assert report.dimension("physics").status == "error"
        assert report.passed is True  # 不阻断硬门禁
        assert report.composite_score == 100.0  # 权重归一到语法

    def test_missing_cutting_params_skips(self):
        report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text=VALID_GCODE))
        physics = report.dimension("physics")
        assert physics.available is False
        assert "缺少切削参数" in physics.skip_reason


# ---------------------------------------------------------------------------
# 真实体素仿真（集成路径：VoxelValidator + ToolpathParser + 体素内核）
# ---------------------------------------------------------------------------


class TestRealVoxel:
    def _service(self):
        from app.cam_validation.voxel_validator import VoxelValidator
        from app.config.cam_validation import CamValidationConfig

        return EvaluationService(voxel_validator=VoxelValidator(CamValidationConfig()))

    def test_valid_program_geometry_passes(self):
        gcode = "O1000\nG90 G54\nG00 X0 Y0\nG43 Z10. H01\nG01 Z1. F100\nG01 X5.\nG00 Z10.\nM30"
        report = self._service().evaluate(
            GcodeEvaluationRequest(gcode_text=gcode, controller_type="fanuc", **_STOCK)
        )
        assert report.passed is True
        geometry = report.dimension("geometry")
        assert geometry.available and geometry.passed
        assert geometry.detail["collision_count"] == 0

    def test_overcut_program_geometry_fails(self):
        gcode = "O1000\nG90 G54\nG00 X0 Y0\nG43 Z10. H01\nG01 Z-1. F100\nG01 X5.\nG00 Z10.\nM30"
        report = self._service().evaluate(
            GcodeEvaluationRequest(gcode_text=gcode, controller_type="fanuc", **_STOCK)
        )
        assert report.passed is False
        geometry = report.dimension("geometry")
        assert geometry.available and geometry.passed is False
        assert geometry.detail["collision_count"] > 0
        assert "GEOMETRY_COLLISION" in report.failure_classes


# ---------------------------------------------------------------------------
# 便捷入口与口径一致性
# ---------------------------------------------------------------------------


def test_module_level_entry_uses_global_service():
    report = evaluate_gcode(GcodeEvaluationRequest(gcode_text=VALID_GCODE))
    assert report.passed is True
    assert {d.name for d in report.dimensions} == {"syntax", "geometry", "physics"}


def test_syntax_scoring_matches_safety_validator_semantics():
    """语法分必须与 SafetyValidator 口径一致（同一校验器、不同权重换算）。"""
    report = EvaluationService().evaluate(GcodeEvaluationRequest(gcode_text=INVALID_GCODE))
    direct = SafetyValidator().validate_gcode_text(INVALID_GCODE)
    expected = max(0.0, 100.0 - 15.0 * len(direct.errors) - 2.0 * len(direct.warnings))
    assert report.dimension("syntax").score == expected
