"""generator_adapter 单元测试。

覆盖阶段 6 适配器的纯逻辑：OperationPlan 反序列化、安全裕度计算、
特征↔工序映射、基于 checkpoints 的特征行范围切分、G 代码行提取，以及
adapt() 主流程（mock GCodeGenerator）。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.chatter_prediction._types import FeatureChatterResult
from app.gcode_generation.generator_adapter import (
    GeneratorAdapter,
    GeneratorAdapterError,
    load_operation_plan,
)
from app.gcode_generation.gcode_store import OperationPlanLoadError
from app.process_planning.gcode_generator import GCodeResult
from app.process_planning.operation_sequencer import Operation, OperationPlan

pytestmark = pytest.mark.unit


def _feature(feature_id="f1", stable=True, axial=1.0, limit=2.0, **kw) -> FeatureChatterResult:
    return FeatureChatterResult(
        feature_id=feature_id,
        feature_type=kw.get("feature_type", "plane"),
        material_id=kw.get("material_id", "steel"),
        spindle_rpm=kw.get("spindle_rpm", 2000.0),
        axial_depth_mm=axial,
        limit_depth_mm=limit,
        stable=stable,
        stability_margin=kw.get("stability_margin", 0.5),
        method=kw.get("method", "analytical"),
        ltc_active=kw.get("ltc_active", False),
    )


def _operation(seq=1, feature_name="f1", **kw) -> Operation:
    return Operation(
        seq=seq,
        name=kw.get("name", f"op{seq}"),
        feature_name=feature_name,
        machining_method=kw.get("machining_method", "平面铣削"),
        surface=kw.get("surface", "top"),
        tolerance_grade=kw.get("tolerance_grade", "IT8"),
        tool_type=kw.get("tool_type", "end_mill"),
    )


def _plan(*ops) -> OperationPlan:
    return OperationPlan(operations=list(ops), estimated_time_min=10.0)


def _gcode_result(**kw) -> GCodeResult:
    return GCodeResult(
        program_text=kw.get("program_text", "L0\nL1\nL2\nL3"),
        controller_type=kw.get("controller_type", "fanuc_0i"),
        program_number=kw.get("program_number", 1000),
        total_lines=kw.get("total_lines", 4),
        errors=list(kw.get("errors", [])),
        checkpoints=list(kw.get("checkpoints", [])),
    )


# load_operation_plan


def _op_plan_dict(**kw) -> dict:
    return {
        "operations": [
            {
                "seq": 1,
                "name": "op1",
                "feature_name": "f1",
                "machining_method": "平面铣削",
                "surface": "top",
                "tolerance_grade": "IT8",
                "tool_type": "end_mill",
                "estimated_time_min": 5.0,
            }
        ],
        "setups": [{"name": "setup1", "surface": "top"}],
        "estimated_time_min": 10.0,
        "face_change_count": 1,
    }


def _write_plan(tmp_path, data) -> str:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


class TestLoadOperationPlan:
    def test_load_success(self, tmp_path):
        path = _write_plan(tmp_path, _op_plan_dict())
        plan = load_operation_plan(path)
        assert len(plan.operations) == 1
        assert plan.operations[0].feature_name == "f1"
        assert plan.operations[0].machining_method == "平面铣削"
        assert len(plan.setups) == 1
        assert plan.setups[0].name == "setup1"
        assert plan.estimated_time_min == 10.0
        assert plan.face_change_count == 1

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(OperationPlanLoadError, match="不存在"):
            load_operation_plan(str(tmp_path / "nope.json"))

    def test_load_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{oops", encoding="utf-8")
        with pytest.raises(OperationPlanLoadError, match="格式错误"):
            load_operation_plan(str(p))

    def test_load_top_level_not_dict(self, tmp_path):
        path = _write_plan(tmp_path, [1, 2, 3])
        with pytest.raises(OperationPlanLoadError, match="顶层必须是 dict"):
            load_operation_plan(path)

    def test_load_operations_not_list(self, tmp_path):
        path = _write_plan(tmp_path, {"operations": "oops"})
        with pytest.raises(OperationPlanLoadError, match="必须是列表"):
            load_operation_plan(path)

    def test_load_operation_missing_field(self, tmp_path):
        data = _op_plan_dict()
        del data["operations"][0]["feature_name"]
        path = _write_plan(tmp_path, data)
        with pytest.raises(OperationPlanLoadError, match="缺少必填字段"):
            load_operation_plan(path)

    def test_load_empty_operations(self, tmp_path):
        path = _write_plan(tmp_path, {"operations": []})
        with pytest.raises(OperationPlanLoadError, match="为空"):
            load_operation_plan(path)

    def test_load_skips_invalid_setup(self, tmp_path):
        data = _op_plan_dict()
        data["setups"] = [{"name": "s1"}]  # 缺 surface → 跳过
        path = _write_plan(tmp_path, data)
        plan = load_operation_plan(path)
        assert plan.setups == []


# _compute_safety_margin


class TestComputeSafetyMargin:
    def test_normal_ratio(self):
        assert GeneratorAdapter._compute_safety_margin(1.0, 2.0) == 0.5

    def test_limit_zero_returns_neg_one(self):
        assert GeneratorAdapter._compute_safety_margin(1.0, 0.0) == -1.0

    def test_limit_negative_returns_neg_one(self):
        assert GeneratorAdapter._compute_safety_margin(1.0, -2.0) == -1.0


# _build_feature_operation_map


class TestBuildFeatureOperationMap:
    def test_maps_feature_to_op_indices(self):
        plan = _plan(
            _operation(seq=1, feature_name="f1"),
            _operation(seq=2, feature_name="f2"),
            _operation(seq=3, feature_name="f1"),
        )
        mapping = GeneratorAdapter._build_feature_operation_map(plan)
        assert mapping == {"f1": [0, 2], "f2": [1]}

    def test_empty_plan(self):
        assert GeneratorAdapter._build_feature_operation_map(_plan()) == {}


# _build_feature_line_ranges


class TestBuildFeatureLineRanges:
    def test_no_checkpoints_returns_empty(self):
        result = _gcode_result(checkpoints=[])
        assert GeneratorAdapter._build_feature_line_ranges(result, _plan()) == {}

    def test_two_features_split(self):
        result = _gcode_result(
            total_lines=4,
            checkpoints=[
                {"op_index": 0, "feature_name": "f1", "line_number": 0},
                {"op_index": 1, "feature_name": "f2", "line_number": 2},
            ],
        )
        ranges = GeneratorAdapter._build_feature_line_ranges(result, _plan())
        assert ranges["f1"] == (0, 1)
        assert ranges["f2"] == (2, 3)

    def test_merges_multiple_checkpoints_of_same_feature(self):
        result = _gcode_result(
            total_lines=6,
            checkpoints=[
                {"op_index": 0, "feature_name": "f1", "line_number": 1},
                {"op_index": 1, "feature_name": "f2", "line_number": 3},
                {"op_index": 2, "feature_name": "f1", "line_number": 4},
            ],
        )
        ranges = GeneratorAdapter._build_feature_line_ranges(result, _plan())
        # f1 合并为 [1, 5]，f2 为 [3, 3]（下一个 checkpoint 是 f1 的 line=4 end=3）
        assert ranges["f1"] == (1, 5)
        assert ranges["f2"] == (3, 3)


# _extract_feature_gcode_lines


class TestExtractFeatureGcodeLines:
    def test_zero_range_returns_empty(self):
        assert GeneratorAdapter._extract_feature_gcode_lines(["a", "b"], (0, 0)) == []

    def test_empty_program_returns_empty(self):
        assert GeneratorAdapter._extract_feature_gcode_lines([], (0, 1)) == []

    def test_normal_extract(self):
        lines = ["L0", "L1", "L2", "L3"]
        assert GeneratorAdapter._extract_feature_gcode_lines(lines, (1, 2)) == ["L1", "L2"]

    def test_out_of_range_clamped(self):
        lines = ["L0", "L1"]
        assert GeneratorAdapter._extract_feature_gcode_lines(lines, (0, 99)) == ["L0", "L1"]


# adapt


class TestAdapt:
    def test_empty_chatter_results_raises(self):
        adapter = GeneratorAdapter()
        with pytest.raises(GeneratorAdapterError, match="为空"):
            adapter.adapt(_plan(_operation()), [])

    def test_unstable_feature_appends_error(self):
        fake_result = _gcode_result(total_lines=3, checkpoints=[])
        with patch("app.gcode_generation.generator_adapter.GCodeGenerator") as MockGen:
            MockGen.return_value.generate.return_value = fake_result
            adapter = GeneratorAdapter()
            base, features = adapter.adapt(
                _plan(_operation(feature_name="f1")),
                [_feature("f1", stable=False, axial=1.5, limit=1.0)],
            )
        assert any("不稳定" in e for e in base.errors)
        assert len(features) == 1
        assert features[0].stable is False

    def test_safety_margin_warning(self):
        fake_result = _gcode_result(total_lines=3, checkpoints=[])
        with patch("app.gcode_generation.generator_adapter.GCodeGenerator") as MockGen:
            MockGen.return_value.generate.return_value = fake_result
            adapter = GeneratorAdapter()
            _, features = adapter.adapt(
                _plan(_operation(feature_name="f1")),
                [_feature("f1", stable=True, axial=1.8, limit=2.0)],  # 1.8 > 0.8*2.0=1.6
            )
        assert features[0].warning
        assert "安全裕度不足" in features[0].warning
        assert features[0].safety_margin_ratio == pytest.approx(0.9)

    def test_feature_line_splitting(self):
        fake_result = _gcode_result(
            program_text="L0\nL1\nL2\nL3",
            total_lines=4,
            checkpoints=[
                {"op_index": 0, "feature_name": "f1", "line_number": 0},
                {"op_index": 1, "feature_name": "f2", "line_number": 2},
            ],
        )
        with patch("app.gcode_generation.generator_adapter.GCodeGenerator") as MockGen:
            MockGen.return_value.generate.return_value = fake_result
            adapter = GeneratorAdapter()
            _, features = adapter.adapt(
                _plan(_operation(feature_name="f1"), _operation(feature_name="f2")),
                [_feature("f1", stable=True), _feature("f2", stable=True)],
            )
        assert features[0].line_range == (0, 1)
        assert features[0].gcode_lines == ["L0", "L1"]
        assert features[1].line_range == (2, 3)
        assert features[1].gcode_lines == ["L2", "L3"]

    def test_generate_value_error_wrapped(self):
        with patch("app.gcode_generation.generator_adapter.GCodeGenerator") as MockGen:
            MockGen.return_value.generate.side_effect = ValueError("boom")
            adapter = GeneratorAdapter()
            with pytest.raises(GeneratorAdapterError, match="失败"):
                adapter.adapt(_plan(_operation()), [_feature("f1")])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
