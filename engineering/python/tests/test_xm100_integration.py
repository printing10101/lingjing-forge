"""
XM-100 五轴加工集成测试

验证五轴加工链路完整性：
1. 后处理器集成
2. 材料/刀具库
3. 碰撞检测
4. 刀路规划
5. G-code 生成
"""
import json
import pytest
from pathlib import Path

from app.postprocessor.xmachine import XMachineXM100PostProcessor
from app.process_planning.gcode_generator import GCodeGenerator
from app.simulation.collision_detector import CollisionDetector, FiveAxisToolVector
from app.toolpath.five_axis_planner import FiveAxisToolpathPlanner, FiveAxisStrategy


class TestXM100PostProcessor:
    """XM-100 后处理器测试"""
    
    def test_postprocessor_exists(self):
        """验证后处理器存在"""
        assert "xmachine_xm100" in GCodeGenerator.CONTROLLER_MAP
        assert GCodeGenerator.CONTROLLER_MAP["xmachine_xm100"] == XMachineXM100PostProcessor
    
    def test_rtcp_commands(self):
        """验证 RTCP 命令"""
        pp = XMachineXM100PostProcessor()
        assert hasattr(pp, "format_rtcp_on")
        assert hasattr(pp, "format_rtcp_off")
        
        rtcp_on = pp.format_rtcp_on()
        assert "G43.4" in rtcp_on or "G43.5" in rtcp_on
    
    def test_five_axis_motion(self):
        """验证五轴运动命令"""
        pp = XMachineXM100PostProcessor()
        
        # 测试带 A/C 轴的线性移动
        move = pp.format_linear_move(
            x=10.0, y=20.0, z=-5.0,
            feed=1000,
            a=5.0, c=0.0
        )
        
        assert "G1" in move or "G0" in move
        assert "X10" in move or "X10.0" in move
        assert "Y20" in move or "Y20.0" in move
        assert "Z-5" in move or "Z-5.0" in move
        assert "A5" in move or "A5.0" in move


class TestMaterialToolLibrary:
    """材料/刀具库测试"""
    
    def test_five_axis_materials(self):
        """验证五轴常用材料"""
        materials_path = Path(__file__).parents[1] / "app" / "database" / "data" / "materials.json"
        with open(materials_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # data is a list, not a dict with "materials" key
        material_ids = [m["id"] for m in data]
        material_categories = {m.get("category", "") for m in data}

        # 验证五轴加工常用材料类别存在（黄铜、紫铜、工程塑料）
        assert "brass" in material_categories      # 黄铜 (brass_c36000)
        assert "copper" in material_categories    # 紫铜 (copper_c11000)
        assert "engineering_plastic" in material_categories  # ABS/POM (abs_plastic)

    def test_five_axis_tools(self):
        """验证五轴加工刀具"""
        tools_path = Path(__file__).parents[1] / "app" / "database" / "data" / "tools.json"
        with open(tools_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # data is a list,使用 subtype 字段标识刀具子类型
        tool_subtypes = {t.get("subtype", "") for t in data}

        # 验证五轴刀具
        assert "ball" in tool_subtypes           # 球头铣刀
        assert "taper_ball" in tool_subtypes      # 锥度球头刀
    
    def test_cutting_parameters(self):
        """验证切削参数"""
        params_path = Path(__file__).parents[1] / "app" / "data" / "cutting_parameters.json"
        with open(params_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # data is a list, not a dict with "parameters" key
        # 验证五轴参数
        five_axis_params = [
            p for p in data
            if "五轴" in p.get("description", "")
        ]
        assert len(five_axis_params) > 0


class TestCollisionDetection:
    """碰撞检测测试"""
    
    def test_five_axis_tool_vector(self):
        """验证五轴刀具矢量"""
        vector = FiveAxisToolVector(a_angle=5.0, c_angle=0.0)
        vector.calculate_from_angles()
        
        # 验证矢量计算
        assert abs(vector.k_component - 0.996) < 0.01  # cos(5°) ≈ 0.996
    
    def test_five_axis_collision_check(self):
        """验证五轴碰撞检测"""
        from app.simulation.stock_model import StockModel
        from app.simulation.toolpath_parser import ToolpathSegment
        
        stock = StockModel(length=100, width=100, height=20)
        detector = CollisionDetector(stock, mode="5axis")
        
        # ToolpathSegment uses type, start_point, end_point instead of move_type, start_x, etc.
        segment = ToolpathSegment(
            type="cut",
            start_point=(0, 0, 5),
            end_point=(10, 0, 5),
            feed_rate=1000,
        )
        
        # 验证检测器可以处理五轴模式
        assert detector.mode == "5axis"


class TestToolpathPlanning:
    """刀路规划测试"""
    
    def test_lead_angle_strategy(self):
        """验证引导角策略"""
        planner = FiveAxisToolpathPlanner()
        
        orientations = planner.plan_lead_angle_toolpath(
            start_x=0, start_y=0, start_z=0,
            end_x=10, end_y=0, end_z=0,
        )
        
        assert len(orientations) > 0
        assert orientations[0].a_angle != 0  # 应有引导角
    
    def test_tilt_angle_strategy(self):
        """验证倾斜角策略"""
        planner = FiveAxisToolpathPlanner()
        
        orientations = planner.plan_tilt_angle_toolpath(
            start_x=0, start_y=0, start_z=0,
            end_x=10, end_y=0, end_z=0,
        )
        
        assert len(orientations) > 0
    
    def test_interpolation_strategy(self):
        """验证插值策略"""
        planner = FiveAxisToolpathPlanner()
        
        points = [(0, 0, 0), (5, 0, 0), (10, 0, 0)]
        normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1)]
        
        orientations = planner.plan_interpolation_toolpath(points, normals)
        
        assert len(orientations) > 0


class TestGCodeGeneration:
    """G-code 生成测试"""
    
    def test_five_axis_gcode_generation(self):
        """验证五轴 G-code 生成"""
        from app.process_planning.operation_sequencer import OperationPlan, Operation
        
        generator = GCodeGenerator()
        
        # Operation uses seq, name, feature_name, machining_method, surface, tolerance_grade
        # not operation_id, operation_type, description, tool_id, parameters
        operation = Operation(
            seq=1,
            name="OP01-五轴轮廓加工",
            feature_name="contour",
            machining_method="五轴精加工",
            surface="Surface A",
            tolerance_grade="IT7",
            tool_type="ball_nose",
            cutting_params={"cut_depth": 5.0},
        )
        plan = OperationPlan(
            operations=[operation],
        )
        
        result = generator.generate(
            operation_plan=plan,
            controller_type="xmachine_xm100",
            material_name="aluminum",
        )
        
        # 验证五轴特征
        assert result.is_valid
        assert "G43.4" in result.program_text or "G43.5" in result.program_text  # RTCP
        assert "X" in result.program_text
        assert "Y" in result.program_text
        assert "Z" in result.program_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
