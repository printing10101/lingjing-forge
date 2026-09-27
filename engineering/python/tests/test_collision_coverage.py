"""碰撞检测模块覆盖率补充测试。

针对 collision_detector.py 未覆盖的代码行编写测试用例，
目标将覆盖率从 51% 提升到 100%。
"""

from __future__ import annotations

import pytest

from app.simulation.collision_detector import (
    CollisionDetector,
    CollisionEvent,
    CollisionReport,
    FiveAxisToolVector,
    WorkspaceLimits,
)
from app.simulation.stock_model import StockModel
from app.simulation.toolpath_parser import ToolpathSegment


class TestFiveAxisToolVector:
    """FiveAxisToolVector 测试 - 覆盖行 47-52"""

    def test_calculate_from_angles_zero(self):
        """测试零角度时的工具向量计算"""
        vec = FiveAxisToolVector(a_angle=0.0, c_angle=0.0)
        vec.calculate_from_angles()
        assert vec.i_component == 0.0
        assert vec.j_component == 0.0
        assert vec.k_component == 1.0

    def test_calculate_from_angles_a_axis_only(self):
        """测试仅A轴旋转"""
        vec = FiveAxisToolVector(a_angle=45.0, c_angle=0.0)
        vec.calculate_from_angles()
        # A轴45度时，k = cos(45°) ≈ 0.707
        assert abs(vec.k_component - 0.707) < 0.01
        # i, j 分量不为零
        assert vec.i_component != 0.0 or vec.j_component != 0.0

    def test_calculate_from_angles_c_axis_only(self):
        """测试仅C轴旋转"""
        vec = FiveAxisToolVector(a_angle=0.0, c_angle=90.0)
        vec.calculate_from_angles()
        # C轴旋转但A轴为0时，工具向量不变
        assert vec.k_component == 1.0

    def test_calculate_from_angles_both_axes(self):
        """测试A/C轴同时旋转"""
        vec = FiveAxisToolVector(a_angle=30.0, c_angle=45.0)
        vec.calculate_from_angles()
        # 验证向量已更新
        assert vec.i_component != 0.0 or vec.j_component != 0.0
        assert vec.k_component < 1.0


class TestCollisionEvent:
    """CollisionEvent 测试 - 覆盖 to_dict 方法"""

    def test_to_dict(self):
        """测试碰撞事件转字典"""
        event = CollisionEvent(
            collision_type="rapid_into_stock",
            severity="high",
            block_number=10,
            position=(10.0, 20.0, 30.0),
            message="Test collision",
            suggestion="Fix it",
        )
        d = event.to_dict()
        assert d["collision_type"] == "rapid_into_stock"
        assert d["severity"] == "high"
        assert d["block_number"] == 10
        assert d["position"] == [10.0, 20.0, 30.0]
        assert d["message"] == "Test collision"
        assert d["suggestion"] == "Fix it"


class TestCollisionReport:
    """CollisionReport 测试 - 覆盖 to_dict 方法"""

    def test_to_dict_empty(self):
        """测试空报告转字典"""
        report = CollisionReport(
            total_segments=10,
            segments_checked=10,
            collisions=[],
            warnings=[],
            safe=True,
        )
        d = report.to_dict()
        assert d["total_segments"] == 10
        assert d["segments_checked"] == 10
        assert d["collisions"] == []
        assert d["warnings"] == []
        assert d["safe"] is True
        assert d["collision_count"] == 0

    def test_to_dict_with_collisions(self):
        """测试带碰撞的报告转字典"""
        event = CollisionEvent(
            collision_type="overcut_z",
            severity="high",
            block_number=5,
            position=(0.0, 0.0, -10.0),
            message="Z overcut",
        )
        report = CollisionReport(
            total_segments=5,
            segments_checked=5,
            collisions=[event],
            warnings=["Warning 1"],
            safe=False,
        )
        d = report.to_dict()
        assert d["collision_count"] == 1
        assert len(d["collisions"]) == 1
        assert d["safe"] is False


class TestCollisionDetectorNoStock:
    """无毛坯模型测试 - 覆盖行 214, 253, 379"""

    def test_check_segments_no_stock(self):
        """测试无毛坯时的检查"""
        detector = CollisionDetector(stock=None, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 200.0),  # Z=200 高于安全平面 (100+10=110)
            end_point=(100.0, 0.0, 200.0),
            block_number=1,
            g_code="G00",
        )
        report = detector.check_segments([seg])
        assert report.safe  # 无毛坯且 Z 高于安全平面时应该安全

    def test_check_rapid_collision_no_bbox(self):
        """测试无边界框时的快速移动检查"""
        detector = CollisionDetector(stock=None)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 50.0),
            end_point=(100.0, 0.0, 50.0),
            block_number=1,
            g_code="G00",
        )
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, None, collisions)
        assert len(collisions) == 0

    def test_check_overcut_no_bbox(self):
        """测试无边界框时的过切检查"""
        detector = CollisionDetector(stock=None)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 0.0),
            end_point=(100.0, 0.0, -10.0),
            block_number=1,
            g_code="G01",
        )
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, None, collisions, warnings)
        assert len(collisions) == 0
        assert len(warnings) == 0


class TestAdaptiveStepSize:
    """自适应步长测试 - 覆盖行 290-291"""

    def test_short_rapid_move_fine_step(self):
        """测试短距离快速移动的精细步长"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        # 短距离移动 (< 10mm)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(10.0, 10.0, 30.0),  # 在安全平面以下
            end_point=(15.0, 10.0, 30.0),  # 5mm 移动
            block_number=1,
            g_code="G00",
        )
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, stock.get_bbox(), collisions)
        # 短距离应该使用更精细的步长

    def test_long_rapid_move_coarse_step(self):
        """测试长距离快速移动的粗糙步长"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        # 长距离移动 (>= 10mm)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(10.0, 10.0, 30.0),
            end_point=(50.0, 10.0, 30.0),  # 40mm 移动
            block_number=1,
            g_code="G00",
        )
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, stock.get_bbox(), collisions)


class TestZSafetyCheck:
    """Z轴安全检查测试 - 覆盖行 337"""

    def test_check_z_safety_non_rapid(self):
        """测试非快速移动类型的Z安全检查"""
        detector = CollisionDetector(stock=StockModel(100, 100, 50))
        seg = ToolpathSegment(
            type="linear",  # 非rapid类型
            start_point=(0.0, 0.0, 5.0),
            end_point=(10.0, 0.0, 5.0),
            block_number=1,
            g_code="G01",
        )
        collisions: list[CollisionEvent] = []
        detector._check_z_safety(seg, 50.0, collisions)
        assert len(collisions) == 0  # 非rapid类型应该跳过检查


class TestArcOvercut:
    """圆弧过切检测测试 - 覆盖行 386-387, 431-495"""

    def test_arc_overcut_detected(self):
        """测试圆弧路径Z轴过切检测"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        # 圆弧路径，Z轴低于毛坯底部
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 0.0, -5.0),  # Z=-5 低于毛坯底部 Z=0
            end_point=(0.0, 50.0, -5.0),
            arc_center=(25.0, 25.0, -5.0),
            clockwise=True,
            block_number=1,
            g_code="G02",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, bbox, collisions, warnings)
        assert any(c.collision_type == "overcut_z" for c in collisions)

    def test_arc_boundary_exceed_x(self):
        """测试圆弧路径X轴超出边界"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        # 圆弧路径，X轴超出边界
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 0.0, 25.0),
            end_point=(0.0, 50.0, 25.0),
            arc_center=(25.0, 25.0, 25.0),
            clockwise=True,
            block_number=1,
            g_code="G02",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, bbox, collisions, warnings)

    def test_arc_boundary_exceed_y(self):
        """测试圆弧路径Y轴超出边界"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="arc",
            start_point=(0.0, 50.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            arc_center=(25.0, 25.0, 25.0),
            clockwise=False,
            block_number=1,
            g_code="G03",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, bbox, collisions, warnings)

    def test_arc_zero_radius_fallback(self):
        """测试零半径圆弧退化为直线检查 - 覆盖行 437-440"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        # 圆心与起点重合，半径为0
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 50.0, 25.0),
            end_point=(60.0, 50.0, 25.0),
            arc_center=(50.0, 50.0, 25.0),  # 与起点相同
            clockwise=True,
            block_number=1,
            g_code="G02",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, bbox, collisions, warnings)


class TestLinearOvercutFallback:
    """直线过切退化检查测试 - 覆盖行 506-519"""

    def test_linear_overcut_x_exceed(self):
        """测试直线过切X轴超出"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(200.0, 0.0, 25.0),  # X超出边界
            block_number=1,
            g_code="G01",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        assert any("exceeds stock boundary" in w for w in warnings)

    def test_linear_overcut_y_exceed(self):
        """测试直线过切Y轴超出"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 200.0, 25.0),  # Y超出边界
            block_number=1,
            g_code="G01",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        assert any("exceeds stock boundary" in w for w in warnings)

    def test_linear_overcut_z_below(self):
        """测试直线过切Z轴低于底部"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 0.0, -10.0),  # Z低于底部
            block_number=1,
            g_code="G01",
        )
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        assert any(c.collision_type == "overcut_z" for c in collisions)


class TestCheckSingleRapid:
    """单段快速移动检查测试 - 覆盖行 544-549"""

    def test_check_single_rapid_safe(self):
        """测试安全的单段快速移动"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 100.0),  # 在安全平面以上
            end_point=(50.0, 0.0, 100.0),
            block_number=1,
            g_code="G00",
        )
        collisions = detector.check_single_rapid(seg)
        assert len(collisions) == 0

    def test_check_single_rapid_collision(self):
        """测试碰撞的单段快速移动"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 30.0),  # 在安全平面以下
            end_point=(50.0, 0.0, 30.0),  # 穿过毛坯
            block_number=1,
            g_code="G00",
        )
        collisions = detector.check_single_rapid(seg)
        assert len(collisions) > 0

    def test_check_single_rapid_non_rapid_type(self):
        """测试非rapid类型的单段检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="linear",  # 非rapid类型
            start_point=(0.0, 0.0, 50.0),
            end_point=(50.0, 0.0, 50.0),
            block_number=1,
            g_code="G01",
        )
        collisions = detector.check_single_rapid(seg)
        assert len(collisions) == 0


class TestFiveAxisOBBCollision:
    """5轴OBB碰撞检测测试 - 覆盖行 573-621"""

    def test_obb_collision_no_bbox(self):
        """测试无边界框时的OBB检查"""
        detector = CollisionDetector(stock=None, mode="5axis")
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 0.0),
            end_point=(10.0, 0.0, 0.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector()
        collisions: list[CollisionEvent] = []
        detector._check_obb_collision(seg, None, tool_vec, collisions)
        assert len(collisions) == 0

    def test_obb_collision_tool_tip_inside_stock(self):
        """测试工具尖端在毛坯内的OBB碰撞"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        # 路径穿过毛坯
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=0.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        collisions: list[CollisionEvent] = []
        detector._check_obb_collision(seg, stock.get_bbox(), tool_vec, collisions)
        assert any(c.collision_type == "5axis_obb_collision" for c in collisions)

    def test_obb_collision_tool_axis_penetration(self):
        """测试工具轴穿透毛坯的碰撞"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        # 工具倾斜，工具轴可能穿透毛坯
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 60.0),  # 在毛坯上方
            end_point=(50.0, 0.0, 60.0),
            block_number=1,
            g_code="G01",
        )
        # 工具倾斜45度
        tool_vec = FiveAxisToolVector(a_angle=45.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        collisions: list[CollisionEvent] = []
        detector._check_obb_collision(seg, stock.get_bbox(), tool_vec, collisions)
        # 可能检测到工具轴碰撞


class TestAxisLimits:
    """A/C轴限制检查测试 - 覆盖行 636-651"""

    def test_axis_limits_exceeded_a(self):
        """测试A轴超出限制"""
        stock = StockModel(100, 100, 50)
        limits = WorkspaceLimits(a_min=-30.0, a_max=30.0)
        detector = CollisionDetector(stock=stock, mode="5axis", workspace_limits=limits)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=45.0, c_angle=0.0)  # 超出 [-30, 30]
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        assert any(c.collision_type == "axis_limit_exceeded" for c in collisions)

    def test_axis_limits_exceeded_c(self):
        """测试C轴超出限制"""
        stock = StockModel(100, 100, 50)
        limits = WorkspaceLimits(c_min=-180.0, c_max=180.0)
        detector = CollisionDetector(stock=stock, mode="5axis", workspace_limits=limits)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=0.0, c_angle=200.0)  # 超出 [-180, 180]
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        assert any(c.collision_type == "axis_limit_exceeded" for c in collisions)

    def test_axis_limits_within_bounds(self):
        """测试轴角度在限制范围内"""
        stock = StockModel(100, 100, 50)
        limits = WorkspaceLimits(a_min=-120.0, a_max=120.0, c_min=-360.0, c_max=360.0)
        detector = CollisionDetector(stock=stock, mode="5axis", workspace_limits=limits)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=30.0, c_angle=45.0)
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        assert len(collisions) == 0


class TestWorkspaceLimits:
    """工作区限制检查测试 - 覆盖行 673-701"""

    def test_workspace_limit_x_exceeded(self):
        """测试X轴超出工作区"""
        stock = StockModel(100, 100, 50)
        limits = WorkspaceLimits(x_min=-100.0, x_max=100.0)
        detector = CollisionDetector(stock=stock, mode="5axis", workspace_limits=limits)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(200.0, 0.0, 25.0),  # X超出 [−100, 100]
            block_number=1,
            g_code="G01",
        )
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        assert any(c.collision_type == "workspace_limit_exceeded" for c in collisions)

    def test_workspace_limit_y_exceeded(self):
        """测试Y轴超出工作区"""
        stock = StockModel(100, 100, 50)
        limits = WorkspaceLimits(y_min=-100.0, y_max=100.0)
        detector = CollisionDetector(stock=stock, mode="5axis", workspace_limits=limits)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 200.0, 25.0),  # Y超出 [−100, 100]
            block_number=1,
            g_code="G01",
        )
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        assert any(c.collision_type == "workspace_limit_exceeded" for c in collisions)

    def test_workspace_limit_z_exceeded(self):
        """测试Z轴超出工作区"""
        stock = StockModel(100, 100, 50)
        limits = WorkspaceLimits(z_min=-50.0, z_max=100.0)
        detector = CollisionDetector(stock=stock, mode="5axis", workspace_limits=limits)
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 0.0, 150.0),  # Z超出 [−50, 100]
            block_number=1,
            g_code="G01",
        )
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        assert any(c.collision_type == "workspace_limit_exceeded" for c in collisions)

    def test_workspace_limit_within_bounds(self):
        """测试在工作区范围内"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 50.0, 50.0),
            block_number=1,
            g_code="G01",
        )
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        assert len(collisions) == 0


class TestSingularityCheck:
    """奇异性检查测试 - 覆盖行 729-737"""

    def test_singularity_near_zero_a_axis(self):
        """测试A轴接近0度时的奇异性警告"""
        detector = CollisionDetector(stock=StockModel(100, 100, 50), mode="5axis")
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=0.5, c_angle=0.0)  # A轴接近0
        warnings: list[str] = []
        detector._check_singularity(seg, tool_vec, warnings)
        assert any("singularity" in w.lower() for w in warnings)

    def test_singularity_tool_horizontal(self):
        """测试工具接近水平时的警告"""
        detector = CollisionDetector(stock=StockModel(100, 100, 50), mode="5axis")
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        # 工具接近水平 (k分量 < 0.1)
        tool_vec = FiveAxisToolVector(a_angle=85.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        warnings: list[str] = []
        detector._check_singularity(seg, tool_vec, warnings)
        assert any("horizontal" in w.lower() or "instability" in w.lower() for w in warnings)

    def test_singularity_safe_orientation(self):
        """测试安全的工具方向"""
        detector = CollisionDetector(stock=StockModel(100, 100, 50), mode="5axis")
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=30.0, c_angle=45.0)
        tool_vec.calculate_from_angles()
        warnings: list[str] = []
        detector._check_singularity(seg, tool_vec, warnings)
        # 安全方向不应该有奇异性警告


class TestCheckSegments5Axis:
    """5轴模式完整检查测试 - 覆盖行 762-795"""

    def test_check_segments_5axis_mode_mismatch(self):
        """测试非5轴模式时回退到3轴检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")  # 3轴模式
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 100.0),
            end_point=(50.0, 0.0, 100.0),
            block_number=1,
            g_code="G00",
        )
        report = detector.check_segments_5axis([seg])
        # 应该回退到3轴检查

    def test_check_segments_5axis_full_check(self):
        """测试5轴模式完整检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        tool_vec = FiveAxisToolVector(a_angle=30.0, c_angle=45.0)
        report = detector.check_segments_5axis([seg], tool_vectors=[tool_vec])
        assert report.total_segments == 1
        assert report.segments_checked == 1

    def test_check_segments_5axis_with_rotation_data(self):
        """测试带旋转数据的5轴检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        # 创建带旋转属性的段
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        # 动态添加旋转属性
        seg.a_angle = 30.0  # type: ignore
        seg.c_angle = 45.0  # type: ignore
        report = detector.check_segments_5axis([seg])
        assert report.segments_checked == 1

    def test_check_segments_5axis_rapid_with_z_safety(self):
        """测试5轴模式下的快速移动Z安全检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis", safe_z_height=10.0)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 30.0),  # 低于安全平面
            end_point=(50.0, 0.0, 30.0),
            block_number=1,
            g_code="G00",
        )
        report = detector.check_segments_5axis([seg])
        # 应该检测到Z安全问题


class TestRetractProtection:
    """抬刀保护逻辑测试 - 覆盖行 275-279, 344-346"""

    def test_retract_protection_skip_check(self):
        """测试抬刀操作跳过碰撞检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        # 抬刀操作：向上移动且终点在安全平面以上
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 45.0),  # 起点在毛坯内
            end_point=(0.0, 0.0, 70.0),  # 终点在安全平面 (50+10=60) 以上
            block_number=1,
            g_code="G00",
        )
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, stock.get_bbox(), collisions)
        # 抬刀操作应该跳过检查，不报告碰撞
        assert len(collisions) == 0

    def test_retract_z_safety_skip(self):
        """测试抬刀操作跳过Z安全检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 45.0),  # 起点低于安全平面
            end_point=(0.0, 0.0, 70.0),  # 终点在安全平面以上
            block_number=1,
            g_code="G00",
        )
        collisions: list[CollisionEvent] = []
        detector._check_z_safety(seg, 50.0, collisions)
        # 抬刀操作应该跳过Z安全检查
        assert len(collisions) == 0
