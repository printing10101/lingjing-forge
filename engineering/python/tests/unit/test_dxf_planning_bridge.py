"""DXF→规划器特征桥测试（2026-09 P1 功能断层修复）。

覆盖：
- ``_normalize_dxf_output``：StageResult 明细 → 规划器兼容特征列表，
  通孔深度以板厚推断
- ``_build_part_description``：显式输入优先于 DXF 桥接特征
- ``_aggregate_cutting_parameters``：键名归一化（规划器体系 → 标准键）
- **端到端验收**：真实 DXF（ezdxf 生成带孔图元）→ dxf_to_gcode 全链
  → 安全校验通过、G 代码含 M30（修复前此链必然"工序规划结果为空"）
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace

import pytest

from app.agent.orchestrator import AgentOrchestrator

pytestmark = pytest.mark.asyncio


def _fake_parse_result(holes_detail: list[dict], planes_detail: list[dict] | None = None, height: float = 12.0):
    return SimpleNamespace(
        features=SimpleNamespace(
            success=True,
            error="",
            summary={
                "hole_count": len(holes_detail),
                "overall_length": 100.0,
                "overall_width": 80.0,
                "overall_height": height,
                "holes_detail": holes_detail,
                "planes_detail": planes_detail or [],
            },
        )
    )


class TestNormalizeDxfOutput:
    def test_hole_mapping_with_through_hole_depth_inference(self):
        orch = AgentOrchestrator(trace_log_dir=tempfile.mkdtemp(), memory=False)
        parse_result = _fake_parse_result(
            [
                {
                    "hole_id": "H01",
                    "center_x": 20.0,
                    "center_y": 30.0,
                    "diameter": 8.5,
                    "depth": 0.0,  # 通孔未标注 → 板厚推断
                    "hole_type": "through_hole",
                    "tolerance_grade": "IT7",
                    "surface": "A",
                }
            ]
        )
        features, metadata = orch._normalize_dxf_output(parse_result)
        assert metadata["hole_count"] == 1
        hole = features[0]
        assert hole["type"] == "hole"
        assert hole["id"] == "H01"
        assert hole["position"] == {"x": 20.0, "y": 30.0}
        assert hole["diameter"] == 8.5
        assert hole["depth"] == 12.0  # 板厚推断
        assert hole["tolerance_grade"] == "IT7"

    def test_blind_hole_keeps_depth_and_plane_mapping(self):
        orch = AgentOrchestrator(trace_log_dir=tempfile.mkdtemp(), memory=False)
        parse_result = _fake_parse_result(
            [{"hole_id": "H01", "center_x": 1.0, "center_y": 2.0, "diameter": 6.0,
              "depth": 9.0, "hole_type": "blind_hole"}],
            [{"plane_id": "P01", "center_x": 50.0, "center_y": 40.0,
              "length": 100.0, "width": 80.0, "surface": "A"}],
        )
        features, metadata = orch._normalize_dxf_output(parse_result)
        assert features[0]["depth"] == 9.0  # 盲孔保留标注深度
        assert features[1]["type"] == "plane"
        assert features[1]["length"] == 100.0
        assert metadata["plane_count"] == 1

    def test_stage_result_shape_no_longer_leaks(self):
        """回归：features 必须是规范化 list[dict]，不得再是 StageResult 对象。"""
        orch = AgentOrchestrator(trace_log_dir=tempfile.mkdtemp(), memory=False)
        features, _ = orch._normalize_dxf_output(_fake_parse_result([]))
        assert isinstance(features, list)


class TestBuildPartDescription:
    def test_explicit_input_holes_win(self, tmp_path):
        orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "t"), memory=False)
        context = {
            "input": {"material_name": "45#钢", "holes": [{"id": "X1", "type": "through_hole"}]},
            "dxf_parse": {"features": [{"type": "hole", "id": "H01"}]},
        }
        part = orch._build_part_description(context["input"], context)
        assert part["holes"][0]["id"] == "X1"
        # 材料名归一化："45#钢" → "45钢"（知识库命名形态）
        assert part["material"] == "45钢"

    def test_falls_back_to_dxf_features(self, tmp_path):
        orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "t"), memory=False)
        context = {
            "input": {"material_name": "6061铝合金"},
            "dxf_parse": {"features": [{"type": "hole", "id": "H01", "diameter": 8.0}]},
        }
        part = orch._build_part_description({}, context)
        assert part["holes"][0]["id"] == "H01"
        assert part["material"] == "6061铝合金"


class TestAggregateCuttingParameters:
    def test_key_alias_normalization(self, tmp_path):
        orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "t"), memory=False)
        ops = [
            {"cutting_params": {"spindle_speed": 3000, "feed_rate": 200, "depth": 5.0}},
            {"cutting_params": {"rpm": 9999}},  # 已有标准键，不覆盖
        ]
        params = orch._aggregate_cutting_parameters(ops)
        assert params["spindle_rpm"] == 3000
        assert params["feed_rate_mm_per_min"] == 200
        assert params["depth_of_cut_mm"] == 5.0
        assert "rpm" not in params


# ---------------------------------------------------------------------------
# 端到端验收：真实 DXF → 全链 → 安全校验通过
# ---------------------------------------------------------------------------


def _make_hole_dxf(path: str) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 80), (0, 80)], close=True, dxfattribs={"layer": "OUTLINE"})
    for i, (x, y, r) in enumerate([(25, 40, 4.25), (75, 40, 4.25)]):
        msp.add_circle((x, y), r, dxfattribs={"layer": "HOLES"})
        # 直径标注（FeatureExtractor 通过关联尺寸标注提取直径）
        dim = msp.add_diameter_dim(
            center=(x, y), radius=r, angle=0,
            dxfattribs={"layer": "DIM"},
        )
        dim.render()
    doc.saveas(path)


async def test_end_to_end_real_dxf_produces_valid_gcode(tmp_path):
    """验收：修复前此链在真实 DXF 上必然失败（工序规划结果为空）。"""
    dxf_path = os.path.join(str(tmp_path), "plate.dxf")
    _make_hole_dxf(dxf_path)

    orch = AgentOrchestrator(trace_log_dir=str(tmp_path / "traces"), memory=False)
    result = await orch.execute_pipeline(
        "dxf_to_gcode",
        {"dxf_path": dxf_path, "material_name": "45#钢"},
    )
    d = result.to_dict()
    step_by_name = {}
    for s in d["steps"]:
        step_by_name.setdefault(s["step_name"].split("#")[0], s)

    assert step_by_name["dxf_parse"]["status"] == "completed"
    assert step_by_name["parameter_recommend"]["status"] == "completed"
    pr_out = step_by_name["parameter_recommend"]["output"]
    assert pr_out.get("operations"), "规划器应产出非空工序（桥接前为空）"

    assert d["success"] is True, f"端到端失败: {d.get('fallback_reason')}"
    gen_out = step_by_name["gcode_generate"]["output"]
    gcode = gen_out.get("gcode", "")
    assert gcode, "应产出非空 G 代码"
    assert "M30" in gcode
    assert step_by_name["validate_safety"]["output"].get("safety_valid") is True
