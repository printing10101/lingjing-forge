"""D3（切参推荐与主轴限幅贯通）与 D4（体素欠切/切到位校验）回归测试。"""

from __future__ import annotations

import pytest

from app.cam_validation.voxel_validator import VoxelValidator
from app.config import CamValidationConfig
from app.cutting_parameters.recommender import CuttingParamRecommender
from app.process_planning.pipeline import ProcessPlanningPipeline


class TestD3CuttingParamsClosure:
    def test_recommender_clamps_spindle_and_rescales_feed_for_vmc850(self):
        """AL6061 + Ø5.5 理论转速远超 8000 rpm，必须受 vmc_850 上限限幅并同步重算进给。"""
        rec = CuttingParamRecommender().recommend(
            feature_id="HOLE_TEST",
            feature_type="hole",
            material_id="al_6061",
            precision_tier="standard",
            tool_diameter_mm=5.5,
            num_flutes=4,
            machine_type="vmc_850",
        )
        assert rec.spindle_speed_rpm <= 8000.0
        expected_feed = round(rec.spindle_speed_rpm * 4 * rec.feed_per_tooth_mm, 2)
        assert rec.feed_rate_mm_per_min == pytest.approx(expected_feed, rel=1e-3)
        assert any("限幅" in w for w in rec.warnings)

    def test_recommended_sf_flows_into_generated_gcode(self):
        """上游推荐并限幅后的 S/F 必须直接进入最终 G 代码，不再各自查表脱节。"""
        rec = CuttingParamRecommender().recommend(
            feature_id="HOLE_001",
            feature_type="hole",
            material_id="al_6061",
            precision_tier="standard",
            tool_diameter_mm=6.0,
            num_flutes=4,
            machine_type="vmc_850",
        )
        result = ProcessPlanningPipeline().run(
            part_description={
                "material": "al_6061",
                "part_type": "plate",
                "face_allowance_mm": 0.0,
                "overall_dimensions": {"length": 100.0, "width": 100.0},
                "holes": [
                    {
                        "id": "HOLE_001",
                        "type": "through_hole",
                        "position": [50.0, 50.0],
                        "diameter": 6.0,
                        "depth": 10.0,
                        "spindle_speed": rec.spindle_speed_rpm,
                        "feed_rate": rec.feed_rate_mm_per_min,
                        "params_source": "切削参数推荐器",
                    }
                ],
            },
            controller_type="fanuc_0i",
            safe_z=80.0,
            stock_top_z=10.0,
        )
        assert result.success and result.gcode_result is not None
        nc = result.gcode_result.program_text
        assert f"S{int(round(rec.spindle_speed_rpm))} M03" in nc
        assert f"F{rec.feed_rate_mm_per_min:.3f}" in nc
        assert "切削参数推荐器" in nc


class TestD4VoxelUndercutGate:
    def test_small_drill_on_large_hole_is_rejected_as_undercut(self):
        """Ø38.2 孔只用 Ø10 钻头在中心钻一刀，无碰撞但残留 >80%，必须判 FAIL。"""
        gcode = (
            "%\n"
            "O1000\n"
            "; T01 | 麻花钻 Ø10 | Φ10.0 | 钻孔\n"
            "G90 G21 G17 G40 G49 G80\n"
            "T01 M06\n"
            "G00 Z80.000\n"
            "G00 X50.000 Y50.000\n"
            "G98 G83 X50.000 Y50.000 Z-3.000 R13.000 Q5.000 F200.000\n"
            "G80\n"
            "M30\n"
            "%\n"
        )
        report = VoxelValidator(CamValidationConfig()).validate(
            gcode_text=gcode,
            controller_type="fanuc_0i",
            safe_z=80.0,
            stock_top_z=10.0,
            stock_length=100.0,
            stock_width=100.0,
            stock_height=10.0,
            tool_diameter_mm=10.0,
            breakthrough_tolerance_mm=3.0,
            expected_features=[
                {"id": "HOLE_001", "type": "hole", "x": 50.0, "y": 50.0, "diameter": 38.2, "depth": 10.0}
            ],
        )
        assert report.passed is False
        assert report.severity == "critical"
        assert report.undercut_count == 1
        assert report.undercut_features[0]["feature_id"] == "HOLE_001"
        assert report.undercut_features[0]["residual_ratio"] > 0.8

    def test_missing_hole_is_rejected_as_undercut(self):
        """程序漏掉第二个孔时，体素完成度闸门必须拦截。"""
        gcode = (
            "%\n"
            "O1000\n"
            "; T01 | 麻花钻 Ø10 | Φ10.0 | 钻孔\n"
            "G90 G21 G17 G40 G49 G80\n"
            "T01 M06\n"
            "G00 Z80.000\n"
            "G00 X25.000 Y50.000\n"
            "G98 G83 X25.000 Y50.000 Z-3.000 R13.000 Q5.000 F200.000\n"
            "G80\n"
            "M30\n"
            "%\n"
        )
        report = VoxelValidator(CamValidationConfig()).validate(
            gcode_text=gcode,
            controller_type="fanuc_0i",
            safe_z=80.0,
            stock_top_z=10.0,
            stock_length=100.0,
            stock_width=100.0,
            stock_height=10.0,
            tool_diameter_mm=10.0,
            breakthrough_tolerance_mm=3.0,
            expected_features=[
                {"id": "HOLE_001", "type": "hole", "x": 25.0, "y": 50.0, "diameter": 10.0, "depth": 10.0},
                {"id": "HOLE_002", "type": "hole", "x": 75.0, "y": 50.0, "diameter": 10.0, "depth": 10.0},
            ],
        )
        assert report.passed is False
        assert report.undercut_count == 1
        assert report.undercut_features[0]["feature_id"] == "HOLE_002"
        assert report.undercut_features[0]["residual_ratio"] > 0.9

    def test_multi_tool_program_switches_tool_diameter_and_passes(self):
        """多刀程序（T01 Ø6 + T02 Ø16）按各自直径去料，Ø16 孔完整切除并通过。"""
        gcode = (
            "%\n"
            "O1000\n"
            "; T01 | 麻花钻 Ø6 | Φ6.0 | 钻孔\n"
            "; T02 | 麻花钻 Ø16 | Φ16.0 | 钻孔\n"
            "G90 G21 G17 G40 G49 G80\n"
            "T01 M06\n"
            "G00 Z80.000\n"
            "G98 G83 X25.000 Y50.000 Z-3.000 R13.000 Q5.000 F200.000\n"
            "G80\n"
            "T02 M06\n"
            "G00 Z80.000\n"
            "G98 G83 X75.000 Y50.000 Z-3.000 R13.000 Q5.000 F200.000\n"
            "G80\n"
            "M30\n"
            "%\n"
        )
        report = VoxelValidator(CamValidationConfig()).validate(
            gcode_text=gcode,
            controller_type="fanuc_0i",
            safe_z=80.0,
            stock_top_z=10.0,
            stock_length=100.0,
            stock_width=100.0,
            stock_height=10.0,
            tool_diameter_mm=6.0,
            breakthrough_tolerance_mm=3.0,
            expected_features=[
                {"id": "HOLE_001", "type": "hole", "x": 25.0, "y": 50.0, "diameter": 6.0, "depth": 10.0},
                {"id": "HOLE_002", "type": "hole", "x": 75.0, "y": 50.0, "diameter": 16.0, "depth": 10.0},
            ],
        )
        assert report.passed is True
        assert report.undercut_count == 0
