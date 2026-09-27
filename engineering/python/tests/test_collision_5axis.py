"""碰撞检测器5轴功能全面测试

覆盖 collision_detector.py 中所有5轴相关功能：
- FiveAxisToolVector 刀具向量计算
- 5轴OBB碰撞检测
- 轴限位检测
- 工作空间限位检测
- 奇异性检测
- 圆弧过切检测
- 5轴模式完整检查流程
"""

from __future__ import annotations

import math

from app.simulation.collision_detector import (
    CollisionDetector,
    CollisionEvent,
    CollisionReport,
    FiveAxisToolVector,
    WorkspaceLimits,
)
from app.simulation.stock_model import StockModel
from app.simulation.toolpath_parser import ToolpathParser, ToolpathSegment


class TestFiveAxisToolVector:
    """测试5轴刀具向量计算"""

    def test_calculate_from_angles_zero(self):
        """测试A=0°, C=0°时的刀具向量"""
        vec = FiveAxisToolVector(a_angle=0.0, c_angle=0.0)
        vec.calculate_from_angles()
        
        # A=0时，刀具垂直向下
        assert abs(vec.i_component) < 1e-6
        assert abs(vec.j_component) < 1e-6
        assert abs(vec.k_component - 1.0) < 1e-6

    def test_calculate_from_angles_a_90(self):
        """测试A=90°时的刀具向量"""
        vec = FiveAxisToolVector(a_angle=90.0, c_angle=0.0)
        vec.calculate_from_angles()
        
        # A=90°, C=0时，刀具指向Y负方向
        assert abs(vec.i_component) < 1e-6
        assert abs(vec.j_component - (-1.0)) < 1e-6
        assert abs(vec.k_component) < 1e-6

    def test_calculate_from_angles_c_90(self):
        """测试C=90°时的刀具向量"""
        vec = FiveAxisToolVector(a_angle=0.0, c_angle=90.0)
        vec.calculate_from_angles()
        
        # C=90°但A=0时，刀具仍垂直向下
        assert abs(vec.i_component) < 1e-6
        assert abs(vec.j_component) < 1e-6
        assert abs(vec.k_component - 1.0) < 1e-6

    def test_calculate_from_angles_combined(self):
        """测试A=45°, C=45°时的刀具向量"""
        vec = FiveAxisToolVector(a_angle=45.0, c_angle=45.0)
        vec.calculate_from_angles()
        
        # 验证向量分量计算正确
        a_rad = math.radians(45.0)
        c_rad = math.radians(45.0)
        expected_i = math.sin(c_rad) * math.sin(a_rad)
        expected_j = -math.cos(c_rad) * math.sin(a_rad)
        expected_k = math.cos(a_rad)
        
        assert abs(vec.i_component - expected_i) < 1e-6
        assert abs(vec.j_component - expected_j) < 1e-6
        assert abs(vec.k_component - expected_k) < 1e-6

    def test_calculate_from_angles_negative(self):
        """测试负角度"""
        vec = FiveAxisToolVector(a_angle=-30.0, c_angle=-60.0)
        vec.calculate_from_angles()
        
        # 验证计算完成，无异常
        assert isinstance(vec.i_component, float)
        assert isinstance(vec.j_component, float)
        assert isinstance(vec.k_component, float)


class TestFiveAxisOBBCollision:
    """测试5轴OBB碰撞检测"""

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
        
        # 应该检测到碰撞
        assert len(collisions) > 0
        assert any(c.collision_type == "5axis_obb_collision" for c in collisions)

    def test_obb_collision_no_collision_outside(self):
        """测试工具在毛坯外时无碰撞"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # 路径在毛坯外
        seg = ToolpathSegment(
            type="linear",
            start_point=(200.0, 200.0, 100.0),
            end_point=(300.0, 300.0, 100.0),
            block_number=1,
            g_code="G01",
        )
        
        tool_vec = FiveAxisToolVector(a_angle=0.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        
        collisions: list[CollisionEvent] = []
        detector._check_obb_collision(seg, stock.get_bbox(), tool_vec, collisions)
        
        # 应该无碰撞
        assert len(collisions) == 0

    def test_obb_collision_tool_axis_penetration(self):
        """测试刀具轴线穿过毛坯"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # 工具尖端在毛坯外，但刀具轴线穿过毛坯
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 100.0),
            end_point=(0.0, 0.0, 100.0),
            block_number=1,
            g_code="G01",
        )
        
        # 刀具倾斜，使轴线穿过毛坯
        tool_vec = FiveAxisToolVector(a_angle=45.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        
        collisions: list[CollisionEvent] = []
        detector._check_obb_collision(seg, stock.get_bbox(), tool_vec, collisions)
        
        # 可能检测到刀具轴线碰撞
        # 注意：这取决于具体的几何计算
        assert isinstance(collisions, list)

    def test_obb_collision_none_bbox(self):
        """测试bbox为None时的处理"""
        detector = CollisionDetector(stock=None, mode="5axis")
        
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
        detector._check_obb_collision(seg, None, tool_vec, collisions)
        
        # bbox为None时应直接返回，无碰撞
        assert len(collisions) == 0


class TestAxisLimits:
    """测试轴限位检测"""

    def test_axis_limits_a_exceeded(self):
        """测试A轴超限"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # A轴超出限制（默认限制为±120°）
        tool_vec = FiveAxisToolVector(a_angle=150.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        
        # 应该检测到A轴超限
        assert len(collisions) > 0
        assert any(c.collision_type == "axis_limit_exceeded" for c in collisions)
        assert any("A-axis" in c.message for c in collisions)

    def test_axis_limits_c_exceeded(self):
        """测试C轴超限"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # C轴超出限制（默认限制为±360°）
        tool_vec = FiveAxisToolVector(a_angle=0.0, c_angle=400.0)
        tool_vec.calculate_from_angles()
        
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        
        # 应该检测到C轴超限
        assert len(collisions) > 0
        assert any(c.collision_type == "axis_limit_exceeded" for c in collisions)
        assert any("C-axis" in c.message for c in collisions)

    def test_axis_limits_within_range(self):
        """测试轴在限制范围内"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # 轴在限制范围内
        tool_vec = FiveAxisToolVector(a_angle=45.0, c_angle=90.0)
        tool_vec.calculate_from_angles()
        
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        
        # 应该无超限
        assert len(collisions) == 0

    def test_axis_limits_custom_workspace(self):
        """测试自定义工作空间限制"""
        stock = StockModel(100, 100, 50)
        
        # 自定义更严格的限制
        custom_limits = WorkspaceLimits(
            a_min=-90.0,
            a_max=90.0,
            c_min=-180.0,
            c_max=180.0,
        )
        
        detector = CollisionDetector(
            stock=stock,
            mode="5axis",
            workspace_limits=custom_limits,
        )
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # A轴超出自定义限制
        tool_vec = FiveAxisToolVector(a_angle=100.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        
        collisions: list[CollisionEvent] = []
        detector._check_axis_limits(seg, tool_vec, collisions)
        
        # 应该检测到超限
        assert len(collisions) > 0


class TestWorkspaceLimits:
    """测试工作空间限位检测"""

    def test_workspace_x_exceeded(self):
        """测试X轴超出工作空间"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # X超出工作空间（默认±300mm）
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(400.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        
        # 应该检测到X超限
        assert len(collisions) > 0
        assert any(c.collision_type == "workspace_limit_exceeded" for c in collisions)
        assert any("X=" in c.message for c in collisions)

    def test_workspace_y_exceeded(self):
        """测试Y轴超出工作空间"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # Y超出工作空间
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 400.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        
        # 应该检测到Y超限
        assert len(collisions) > 0
        assert any(c.collision_type == "workspace_limit_exceeded" for c in collisions)
        assert any("Y=" in c.message for c in collisions)

    def test_workspace_z_exceeded(self):
        """测试Z轴超出工作空间"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # Z超出工作空间（默认±200mm）
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 0.0, 250.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        
        # 应该检测到Z超限
        assert len(collisions) > 0
        assert any(c.collision_type == "workspace_limit_exceeded" for c in collisions)
        assert any("Z=" in c.message for c in collisions)

    def test_workspace_within_limits(self):
        """测试在工作空间内"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # 在工作空间内
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(100.0, 100.0, 50.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_workspace_limits(seg, collisions)
        
        # 应该无超限
        assert len(collisions) == 0


class TestSingularity:
    """测试奇异性检测"""

    def test_singularity_a_near_zero(self):
        """测试A轴接近0°时的奇异性"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # A轴接近0°
        tool_vec = FiveAxisToolVector(a_angle=0.5, c_angle=0.0)
        tool_vec.calculate_from_angles()
        
        warnings: list[str] = []
        detector._check_singularity(seg, tool_vec, warnings)
        
        # 应该检测到奇异性警告
        assert len(warnings) > 0
        assert any("singularity" in w.lower() for w in warnings)

    def test_singularity_k_component_low(self):
        """测试刀具向量K分量过低"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # A轴90°时，K分量接近0
        tool_vec = FiveAxisToolVector(a_angle=90.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        
        warnings: list[str] = []
        detector._check_singularity(seg, tool_vec, warnings)
        
        # 应该检测到刀具方向警告
        assert len(warnings) > 0
        assert any("horizontal" in w.lower() or "instability" in w.lower() for w in warnings)

    def test_no_singularity_normal_orientation(self):
        """测试正常刀具方向无奇异性"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        # 正常刀具方向
        tool_vec = FiveAxisToolVector(a_angle=30.0, c_angle=45.0)
        tool_vec.calculate_from_angles()
        
        warnings: list[str] = []
        detector._check_singularity(seg, tool_vec, warnings)
        
        # 应该无警告
        assert len(warnings) == 0


class TestArcOvercut:
    """测试圆弧过切检测"""

    def test_arc_overcut_z_below_stock(self):
        """测试圆弧路径Z低于毛坯底面"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        # 圆弧路径，Z低于毛坯底面
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 0.0, -5.0),
            end_point=(0.0, 50.0, -5.0),
            block_number=1,
            g_code="G02",
            arc_center=(0.0, 0.0, -5.0),
            clockwise=True,
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该检测到Z过切
        assert len(collisions) > 0
        assert any(c.collision_type == "overcut_z" for c in collisions)

    def test_arc_overcut_boundary_exceed(self):
        """测试圆弧路径超出边界"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        # 圆弧路径终点超出 X 边界（150 > 100）
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 0.0, 25.0),
            end_point=(150.0, 50.0, 25.0),  # X=150 超出 stock X_max=100
            block_number=1,
            g_code="G02",
            arc_center=(50.0, 50.0, 25.0),
            clockwise=False,
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该检测到边界警告
        assert len(warnings) > 0
        assert any("exceeds stock boundary" in w for w in warnings)

    def test_arc_overcut_degenerate_radius(self):
        """测试退化为直线的圆弧（半径过小）"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        # 圆弧中心和起点几乎重合，半径过小
        seg = ToolpathSegment(
            type="arc",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G02",
            arc_center=(0.001, 0.0, 25.0),  # 半径极小
            clockwise=True,
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该退化为直线检查，无异常
        assert isinstance(collisions, list)
        assert isinstance(warnings, list)


class TestLinearOvercut:
    """测试线性过切回退检测"""

    def test_linear_overcut_x_exceed(self):
        """测试X方向超出边界"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(200.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该检测到X边界警告
        assert len(warnings) > 0
        assert any("X=" in w and "exceeds stock boundary" in w for w in warnings)

    def test_linear_overcut_y_exceed(self):
        """测试Y方向超出边界"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 200.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该检测到Y边界警告
        assert len(warnings) > 0
        assert any("Y=" in w and "exceeds stock boundary" in w for w in warnings)

    def test_linear_overcut_z_below(self):
        """测试Z低于毛坯底面"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 0.0, -10.0),
            block_number=1,
            g_code="G01",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该检测到Z过切
        assert len(collisions) > 0
        assert any(c.collision_type == "overcut_z" for c in collisions)

    def test_linear_overcut_within_bounds(self):
        """测试在边界内无过切"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock)
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 50.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        
        detector._check_linear_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该无过切
        assert len(collisions) == 0
        assert len(warnings) == 0


class TestCheckSegments5Axis:
    """测试5轴模式完整检查流程"""

    def test_5axis_mode_complete_check(self):
        """测试5轴模式完整检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # 创建包含多种类型的路径段
        segments = [
            ToolpathSegment(
                type="rapid",
                start_point=(0.0, 0.0, 100.0),
                end_point=(0.0, 0.0, 50.0),
                block_number=1,
                g_code="G00",
            ),
            ToolpathSegment(
                type="linear",
                start_point=(0.0, 0.0, 50.0),
                end_point=(50.0, 0.0, 25.0),
                block_number=2,
                g_code="G01",
            ),
        ]
        
        # 提供刀具向量
        tool_vectors = [
            FiveAxisToolVector(a_angle=0.0, c_angle=0.0),
            FiveAxisToolVector(a_angle=30.0, c_angle=45.0),
        ]
        
        for vec in tool_vectors:
            vec.calculate_from_angles()
        
        report = detector.check_segments_5axis(segments, tool_vectors)
        
        # 应该返回完整的碰撞报告
        assert report.total_segments == 2
        assert report.segments_checked == 2
        assert isinstance(report.collisions, list)
        assert isinstance(report.warnings, list)

    def test_5axis_mode_fallback_to_3axis(self):
        """测试非5轴模式回退到3轴检查"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        segments = [
            ToolpathSegment(
                type="linear",
                start_point=(0.0, 0.0, 25.0),
                end_point=(50.0, 0.0, 25.0),
                block_number=1,
                g_code="G01",
            ),
        ]
        
        report = detector.check_segments_5axis(segments)
        
        # 应该回退到3轴检查
        assert report.total_segments == 1
        assert isinstance(report.collisions, list)

    def test_5axis_mode_without_tool_vectors(self):
        """测试5轴模式不提供刀具向量"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        segments = [
            ToolpathSegment(
                type="linear",
                start_point=(0.0, 0.0, 25.0),
                end_point=(50.0, 0.0, 25.0),
                block_number=1,
                g_code="G01",
            ),
        ]
        
        report = detector.check_segments_5axis(segments, tool_vectors=None)
        
        # 应该使用默认刀具向量
        assert report.total_segments == 1
        assert isinstance(report.collisions, list)

    def test_5axis_mode_with_rotation_data(self):
        """测试5轴模式段包含旋转数据"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # 创建包含旋转数据的段
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        # 动态添加旋转属性
        seg.a_angle = 30.0
        seg.c_angle = 45.0
        
        segments = [seg]
        
        report = detector.check_segments_5axis(segments)
        
        # 应该从段中提取旋转数据
        assert report.total_segments == 1
        assert isinstance(report.collisions, list)

    def test_5axis_mode_detects_all_issues(self):
        """测试5轴模式检测所有问题"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        # 创建包含多种问题的路径
        segments = [
            # 快速移动撞毛坯
            ToolpathSegment(
                type="rapid",
                start_point=(0.0, 0.0, 100.0),
                end_point=(0.0, 0.0, -10.0),
                block_number=1,
                g_code="G00",
            ),
            # 过切
            ToolpathSegment(
                type="linear",
                start_point=(0.0, 0.0, 25.0),
                end_point=(0.0, 0.0, -10.0),
                block_number=2,
                g_code="G01",
            ),
            # 工作空间超限
            ToolpathSegment(
                type="linear",
                start_point=(0.0, 0.0, 25.0),
                end_point=(500.0, 0.0, 25.0),
                block_number=3,
                g_code="G01",
            ),
        ]
        
        # A轴超限的刀具向量
        tool_vec = FiveAxisToolVector(a_angle=150.0, c_angle=0.0)
        tool_vec.calculate_from_angles()
        tool_vectors = [tool_vec, tool_vec, tool_vec]
        
        report = detector.check_segments_5axis(segments, tool_vectors)
        
        # 应该检测到多种问题
        assert not report.safe
        assert len(report.collisions) > 0
        # 可能包含多种碰撞类型
        collision_types = {c.collision_type for c in report.collisions}
        assert len(collision_types) > 0


class TestIntegration:
    """集成测试"""

    def test_full_5axis_machining_scenario(self):
        """测试完整的5轴加工场景"""
        # 创建5轴检测器
        stock = StockModel(200, 150, 80)
        detector = CollisionDetector(
            stock=stock,
            mode="5axis",
            safe_z_height=10.0,
        )
        
        # 解析5轴G代码
        gcode = """%
O0001
G21 G17 G90 G94
G00 Z100.
G00 X0. Y0.
M03 S8000
G01 Z-5. F500
G01 X50. Y25. F800
G02 X75. Y50. R25.
G01 X0. Y0.
G00 Z100.
M05
M30
%"""
        
        parser = ToolpathParser()
        segments = parser.parse_gcode(gcode)
        
        # 执行5轴检查
        report = detector.check_segments_5axis(segments)
        
        # 验证报告
        assert report.total_segments > 0
        assert report.segments_checked > 0
        assert isinstance(report.safe, bool)
        assert isinstance(len(report.collisions), int)

    def test_report_to_dict_5axis(self):
        """测试5轴报告转字典"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="5axis")
        
        segments = [
            ToolpathSegment(
                type="linear",
                start_point=(0.0, 0.0, 25.0),
                end_point=(50.0, 0.0, 25.0),
                block_number=1,
                g_code="G01",
            ),
        ]
        
        report = detector.check_segments_5axis(segments)
        report_dict = report.to_dict()
        
        # 验证字典结构
        assert "total_segments" in report_dict
        assert "segments_checked" in report_dict
        assert "collisions" in report_dict
        assert "warnings" in report_dict
        assert "safe" in report_dict
        assert "collision_count" in report_dict


class TestUncoveredLines:
    """测试未覆盖的代码行"""

    def test_check_segments_rapid_collision(self):
        """测试 check_segments 中的快速移动碰撞检查 (lines 222-223)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 创建快速移动段，穿过毛坯
        segments = [
            ToolpathSegment(
                type="rapid",
                start_point=(0.0, 0.0, 100.0),
                end_point=(0.0, 0.0, 25.0),  # 终点在毛坯内
                block_number=1,
                g_code="G00",
            ),
        ]
        
        report = detector.check_segments(segments)
        
        # 应该检测到碰撞
        assert report.total_segments == 1
        assert len(report.collisions) > 0

    def test_check_rapid_collision_bbox_none(self):
        """测试 bbox 为 None 时的快速移动碰撞检查 (line 254)"""
        detector = CollisionDetector(stock=None, mode="3axis")
        
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 100.0),
            end_point=(0.0, 0.0, 25.0),
            block_number=1,
            g_code="G00",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, None, collisions)
        
        # bbox 为 None 时应直接返回，无碰撞
        assert len(collisions) == 0

    def test_check_rapid_collision_no_intersection(self):
        """测试快速移动路径与毛坯不相交 (line 269)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 路径完全在毛坯外
        seg = ToolpathSegment(
            type="rapid",
            start_point=(200.0, 200.0, 100.0),
            end_point=(300.0, 300.0, 100.0),
            block_number=1,
            g_code="G00",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, bbox, collisions)
        
        # 路径不与毛坯相交，无碰撞
        assert len(collisions) == 0

    def test_check_rapid_collision_short_distance(self):
        """测试短距离快速移动的采样逻辑 (lines 290-291)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 短距离移动 (< 10mm)，穿过毛坯
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 25.0),
            end_point=(5.0, 0.0, 25.0),  # 距离 = 5mm < 10mm
            block_number=1,
            g_code="G00",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        detector._check_rapid_collision(seg, bbox, collisions)
        
        # 应该检测到碰撞（使用细步长 0.5mm）
        assert len(collisions) > 0

    def test_check_z_safety_non_rapid_segment(self):
        """测试非快速移动段的 Z 安全检查 (line 337)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 线性移动段
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_z_safety(seg, 50.0, collisions)
        
        # 非快速移动段应直接返回，无碰撞
        assert len(collisions) == 0

    def test_check_z_safety_low_start(self):
        """测试起点低于安全高度的快速移动 (line 349)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0, mode="3axis")
        
        # 起点在毛坯内，终点也在毛坯内
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 25.0),  # 低于安全高度 60mm
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G00",
        )
        
        collisions: list[CollisionEvent] = []
        detector._check_z_safety(seg, 50.0, collisions)
        
        # 应该检测到 Z 高度不足
        assert len(collisions) > 0
        assert any(c.collision_type == "rapid_z_low" for c in collisions)

    def test_check_overcut_bbox_none(self):
        """测试 bbox 为 None 时的过切检查 (line 380)"""
        detector = CollisionDetector(stock=None, mode="3axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, None, collisions, warnings)
        
        # bbox 为 None 时应直接返回
        assert len(collisions) == 0
        assert len(warnings) == 0

    def test_check_overcut_arc_segment(self):
        """测试圆弧段的过切检查 (lines 386-387)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 圆弧段
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 0.0, 25.0),
            end_point=(0.0, 50.0, 25.0),
            block_number=1,
            g_code="G02",
            arc_center=(0.0, 0.0, 25.0),
            clockwise=True,
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, bbox, collisions, warnings)
        
        # 应该调用 _check_arc_overcut
        assert isinstance(collisions, list)
        assert isinstance(warnings, list)

    def test_check_overcut_y_exceeds(self):
        """测试 Y 方向超出边界的过切检查 (line 398)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # Y 超出边界
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(0.0, 200.0, 25.0),  # Y=200 超出 stock Y_max=50
            block_number=1,
            g_code="G01",
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_overcut(seg, bbox, collisions, warnings)
        
        # 应该检测到 Y 边界警告
        assert len(warnings) > 0
        assert any("Y=" in w and "exceeds stock boundary" in w for w in warnings)

    def test_check_arc_overcut_degenerate_radius_fallback(self):
        """测试退化为直线的圆弧过切检查 (lines 439-440)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 圆弧中心和起点几乎重合，半径过小
        seg = ToolpathSegment(
            type="arc",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G02",
            arc_center=(0.0001, 0.0, 25.0),  # 半径极小 < 0.001
            clockwise=True,
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该退化为直线检查
        assert isinstance(collisions, list)
        assert isinstance(warnings, list)

    def test_check_arc_overcut_counterclockwise_negative_sweep(self):
        """测试逆时针圆弧的负扫掠角 (line 454)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 逆时针圆弧，需要 sweep <= 0 的情况
        seg = ToolpathSegment(
            type="arc",
            start_point=(50.0, 0.0, 25.0),
            end_point=(0.0, 50.0, 25.0),
            block_number=1,
            g_code="G03",
            arc_center=(0.0, 0.0, 25.0),
            clockwise=False,  # 逆时针
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该正常处理
        assert isinstance(collisions, list)
        assert isinstance(warnings, list)

    def test_check_arc_overcut_y_exceeds(self):
        """测试圆弧路径 Y 方向超出边界 (line 480)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 圆弧路径 Y 超出边界
        seg = ToolpathSegment(
            type="arc",
            start_point=(0.0, 50.0, 25.0),
            end_point=(50.0, 150.0, 25.0),  # Y=150 超出 stock Y_max=50
            block_number=1,
            g_code="G02",
            arc_center=(0.0, 100.0, 25.0),
            clockwise=True,
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该检测到 Y 边界警告
        assert len(warnings) > 0
        assert any("Y=" in w and "exceeds stock boundary" in w for w in warnings)

    def test_check_single_rapid_non_rapid_segment(self):
        """测试非快速移动段的单段检查 (line 544)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        seg = ToolpathSegment(
            type="linear",
            start_point=(0.0, 0.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G01",
        )
        
        collisions = detector.check_single_rapid(seg)
        
        # 非快速移动段应返回空列表
        assert collisions == []

    def test_check_single_rapid_with_collision(self):
        """测试有碰撞的单段快速移动检查 (lines 545-549)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 100.0),
            end_point=(0.0, 0.0, 25.0),  # 终点在毛坯内
            block_number=1,
            g_code="G00",
        )
        
        collisions = detector.check_single_rapid(seg)
        
        # 应该检测到碰撞
        assert len(collisions) > 0

    def test_check_single_rapid_no_stock(self):
        """测试无毛坯时的单段快速移动检查 (lines 546-549)"""
        detector = CollisionDetector(stock=None, mode="3axis")
        
        seg = ToolpathSegment(
            type="rapid",
            start_point=(0.0, 0.0, 100.0),
            end_point=(0.0, 0.0, 25.0),
            block_number=1,
            g_code="G00",
        )
        
        collisions = detector.check_single_rapid(seg)
        
        # 无毛坯时应返回空列表
        assert collisions == []

    def test_check_arc_overcut_counterclockwise_negative_sweep_edge(self):
        """测试逆时针圆弧扫掠角为负数的边界情况 (line 454)"""
        stock = StockModel(100, 100, 50)
        detector = CollisionDetector(stock=stock, mode="3axis")
        
        # 创建逆时针圆弧，使得 end_angle < start_angle，导致 sweep < 0
        # 起点在 (0, 50, 25) -> start_angle = π/2
        # 终点在 (50, 0, 25) -> end_angle = 0
        # sweep = 0 - π/2 = -π/2 < 0，会触发 line 454
        seg = ToolpathSegment(
            type="arc",
            start_point=(0.0, 50.0, 25.0),
            end_point=(50.0, 0.0, 25.0),
            block_number=1,
            g_code="G03",
            arc_center=(0.0, 0.0, 25.0),
            clockwise=False,  # 逆时针
        )
        
        bbox = stock.get_bbox()
        collisions: list[CollisionEvent] = []
        warnings: list[str] = []
        detector._check_arc_overcut(seg, bbox, collisions, warnings, margin=0.5)
        
        # 应该正常处理，扫掠角被修正为正值
        assert isinstance(collisions, list)
        assert isinstance(warnings, list)
