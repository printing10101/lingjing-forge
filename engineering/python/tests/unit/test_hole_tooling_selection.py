"""按孔径选刀与超径阻断的回归测试。

背景：真实零件试跑（NEMA 23 安装板，2026-11-18）暴露出三个孔（Ø38.2 / Ø5.5 / Ø6.6）
共用一把 Ø10 麻花钻，因为工序层从不把孔径写进 ``cutting_params["tool_diameter"]``，
G 代码层只能吃到 fallback 10.0。

本测试锁定修复后的三条不变量：
1. 孔径进 ``tool_diameter``，且不同孔径成为不同刀具（不同 T 号）；
2. 孔径超过一次钻削上限时标记 ``tool_feasible=False``，G 代码层拒绝生成并报错；
3. 刀具清单里的直径必须可读（不能是 N/A）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_hole_tooling_selection.py -q --no-cov
"""

from __future__ import annotations

import pytest

from app.process_planning._feature_code_mixin import _material_category
from app.process_planning.gcode_generator import GCodeGenerator
from app.process_planning.operation_sequencer import (
    MAX_DRILL_DIAMETER_MM,
    Operation,
    OperationSequencer,
    OperationPlan,
)
from app.process_planning.feature_dependency import MachiningFeature


def _hole(name: str, diameter: float, x: float = 10.0, y: float = 10.0) -> MachiningFeature:
    return MachiningFeature(
        name=name,
        type="through_hole",
        geometric_type="cylinder",
        tolerance_grade="IT8",
        surface="A",
        dimensions={"diameter": diameter, "depth": 10.0, "position_x": x, "position_y": y},
    )


def _plan_with(*features: MachiningFeature) -> OperationPlan:
    return OperationSequencer().plan_operations(list(features), material="45#钢", part_type="plate")


class TestPerHoleToolDiameter:
    def test_each_diameter_gets_its_own_tool(self) -> None:
        plan = _plan_with(_hole("H1", 5.5), _hole("H2", 6.6))
        tools = {op.tool_type for op in plan.operations}
        assert len(tools) == 2, f"不同孔径不应共用一把刀: {tools}"
        diameters = sorted(op.cutting_params["tool_diameter"] for op in plan.operations)
        assert diameters == [5.5, 6.6]

    def test_tool_diameter_equals_hole_diameter(self) -> None:
        plan = _plan_with(_hole("H1", 8.0))
        assert plan.operations[0].cutting_params["tool_diameter"] == 8.0
        assert "Ø8" in plan.operations[0].tool_type

    def test_milling_operation_carries_library_tool(self) -> None:
        """铣削工序必须带刀具库里的 φ10 立铣刀直径，否则刀具清单又是 N/A。"""
        plane = MachiningFeature(
            name="基准面A",
            type="plane_surface",
            geometric_type="plane",
            surface="A",
            dimensions={"length": 100.0, "width": 100.0, "center_x": 50.0, "center_y": 50.0},
        )
        plan = _plan_with(plane)
        assert plan.operations[0].cutting_params["tool_diameter"] == 10.0


class TestOversizedHole:
    def test_oversized_hole_switches_to_bore_milling(self) -> None:
        """孔径超过钻削上限 → 改走螺旋铣孔（立铣刀环切），而不是硬钻。"""
        plan = _plan_with(_hole("H1", MAX_DRILL_DIAMETER_MM + 18.2, x=70.0, y=70.0))
        op = plan.operations[0]
        assert op.machining_method == "螺旋铣孔"
        assert op.cutting_params["tool_diameter"] == 10.0
        contour = op.cutting_params["geometry"]["contour"]
        assert len(contour) >= 16
        # 轮廓半径 = 孔径/2
        radius = ((contour[0][0] - 70.0) ** 2 + (contour[0][1] - 70.0) ** 2) ** 0.5
        assert abs(radius - (MAX_DRILL_DIAMETER_MM + 18.2) / 2.0) < 0.01

    def test_oversized_hole_generates_milling_not_drill_cycle(self) -> None:
        plan = _plan_with(_hole("H1", 38.2, x=70.0, y=70.0))
        result = GCodeGenerator().generate(plan, controller_type="fanuc_0i")
        program = result.program_text
        assert result.is_valid, result.errors
        assert "螺旋铣孔" in program
        # 大孔位置不得出现钻削固定循环
        assert "X70.000 Y70.000" not in program
        assert "pocket_contour_parallel" in program

    def test_infeasible_tool_is_blocked_at_gcode_layer(self) -> None:
        """安全网：外部计划显式标记 tool_feasible=False 时，拒绝出代码并报错。"""
        plan = OperationPlan(
            operations=[
                Operation(
                    seq=1,
                    name="OP01-H1",
                    feature_name="H1",
                    machining_method="钻孔",
                    surface="A",
                    tolerance_grade="IT8",
                    tool_type="麻花钻",
                    cutting_params={
                        "geometry": {"x": 70.0, "y": 70.0, "z_depth": 3.0},
                        "tool_feasible": False,
                        "tool_note": "孔径 Ø38.2 超过一次钻削上限 Ø20.0",
                    },
                )
            ]
        )
        result = GCodeGenerator().generate(plan, controller_type="fanuc_0i")
        assert result.errors
        assert not result.is_valid
        assert "[阻断]" in result.program_text

    def test_blocked_tool_not_listed(self) -> None:
        plan = _plan_with(_hole("H1", 5.5), _hole("H2", 6.6))
        result = GCodeGenerator().generate(plan, controller_type="fanuc_0i")
        summary = [ln for ln in result.program_text.splitlines() if ln.startswith("; T0") and "|" in ln]
        assert summary, "刀具清单不应为空"
        assert all("N/A" not in ln for ln in summary), f"刀具清单仍有未知直径: {summary}"
        assert any("Φ5.5" in ln for ln in summary)
        assert any("Φ6.6" in ln for ln in summary)


class TestMaterialCategory:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("45#钢", "steel"),
            ("铝合金", "aluminum"),
            ("AL6061", "aluminum"),
            ("al_7075", "aluminum"),
            ("304不锈钢", "stainless"),
            ("TC4钛合金", "titanium"),
            ("HT250铸铁", "cast_iron"),
            ("", "steel"),
        ],
    )
    def test_normalize(self, raw: str, expected: str) -> None:
        assert _material_category(raw) == expected
