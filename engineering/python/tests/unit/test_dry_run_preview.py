"""G代码dry-run预览功能单元测试

测试目标：验证dry_run_preview方法的完整性和正确性
覆盖范围：
- 刀具路径摘要生成
- 加工时间估算
- 刀具使用统计
- 碰撞风险提示
- 断点位置标记
- 警告信息生成
- 空工序规划处理
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, MagicMock

from app.process_planning.gcode_generator import GCodeGenerator
from app.process_planning.operation_sequencer import OperationPlan, Operation


class TestDryRunPreview:
    """dry_run_preview方法测试套件"""

    @pytest.fixture
    def generator(self):
        """GCodeGenerator实例"""
        return GCodeGenerator()

    @pytest.fixture
    def sample_operation_plan(self):
        """示例工序规划"""
        operations = [
            Operation(
                seq=1,
                name="OP01-粗铣平面",
                feature_name="plane_top",
                machining_method="粗铣",
                surface="top_surface",
                tolerance_grade="IT10",
                tool_type="endmill_D10",
                cutting_params={
                    "start_x": 0.0,
                    "start_y": 0.0,
                    "depth": 5.0,
                },
                estimated_time_min=2.5,
            ),
            Operation(
                seq=2,
                name="OP02-精铣平面",
                feature_name="plane_top",
                machining_method="精铣",
                surface="top_surface",
                tolerance_grade="IT7",
                tool_type="endmill_D10",
                cutting_params={
                    "start_x": 0.0,
                    "start_y": 0.0,
                    "depth": 5.0,
                },
                estimated_time_min=3.0,
            ),
            Operation(
                seq=3,
                name="OP03-钻孔",
                feature_name="hole_01",
                machining_method="钻孔",
                surface="top_surface",
                tolerance_grade="IT8",
                tool_type="drill_D8",
                cutting_params={
                    "start_x": 50.0,
                    "start_y": 50.0,
                    "depth": 20.0,
                },
                estimated_time_min=1.5,
            ),
        ]
        
        plan = OperationPlan()
        plan.operations = operations
        plan.estimated_time_min = 7.0  # 总时间
        return plan

    @pytest.fixture
    def deep_cavity_operation_plan(self):
        """深腔加工工序规划（用于测试碰撞风险）"""
        operations = [
            Operation(
                seq=1,
                name="OP01-深腔加工",
                feature_name="cavity_deep",
                machining_method="型腔铣",
                surface="cavity_surface",
                tolerance_grade="IT9",
                tool_type="endmill_D12",
                cutting_params={
                    "start_x": 100.0,
                    "start_y": 100.0,
                    "depth": 60.0,  # 深度>50mm，应触发深腔警告
                },
                estimated_time_min=10.0,
            ),
        ]
        
        plan = OperationPlan()
        plan.operations = operations
        plan.estimated_time_min = 10.0
        return plan

    @pytest.fixture
    def long_rapid_move_plan(self):
        """长距离快速移动工序规划（用于测试碰撞风险）"""
        operations = [
            Operation(
                seq=1,
                name="OP01-长距离移动",
                feature_name="feature_01",
                machining_method="铣削",
                surface="surface_01",
                tolerance_grade="IT10",
                tool_type="endmill_D10",
                cutting_params={
                    "start_x": 0.0,
                    "start_y": 0.0,
                    "depth": 150.0,  # 移动距离>100mm
                },
                estimated_time_min=5.0,
            ),
        ]
        
        plan = OperationPlan()
        plan.operations = operations
        plan.estimated_time_min = 5.0
        return plan

    def test_dry_run_preview_basic_structure(self, generator, sample_operation_plan):
        """测试dry_run_preview返回基本结构"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
            controller_type="fanuc_0i",
            material_name="45#钢",
            program_number=1000,
            safe_z=50.0,
        )

        # 验证返回结构
        assert isinstance(result, dict)
        assert "controller_type" in result
        assert "material" in result
        assert "program_number" in result
        assert "safe_z" in result
        assert "tool_path_summary" in result
        assert "time_estimation" in result
        assert "tool_usage" in result
        assert "collision_risks" in result
        assert "checkpoint_positions" in result
        assert "warnings" in result

    def test_dry_run_preview_controller_and_material(self, generator, sample_operation_plan):
        """测试控制器类型和材料信息"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
            controller_type="siemens_840d",
            material_name="6061-T6铝合金",
            program_number=2000,
            safe_z=60.0,
        )

        assert result["controller_type"] == "siemens_840d"
        assert result["material"] == "6061-T6铝合金"
        assert result["program_number"] == 2000
        assert result["safe_z"] == 60.0

    def test_dry_run_preview_tool_path_summary(self, generator, sample_operation_plan):
        """测试刀具路径摘要"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
            controller_type="fanuc_0i",
            safe_z=50.0,
        )

        tool_path_summary = result["tool_path_summary"]
        assert len(tool_path_summary) == 3  # 3个工序

        # 验证第一个工序
        path1 = tool_path_summary[0]
        assert path1["op_seq"] == 1
        assert path1["op_name"] == "OP01-粗铣平面"
        assert path1["tool_type"] == "endmill_D10"
        assert path1["start_pos"]["z"] == 50.0  # 从安全高度开始
        assert path1["end_pos"]["z"] == 45.0  # 毛坯顶面 50 - 深度 5
        assert path1["travel_distance"] == 5.0  # 50 - 45 = 5
        assert path1["machining_method"] == "粗铣"

    def test_dry_run_preview_time_estimation(self, generator, sample_operation_plan):
        """测试加工时间估算"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
            controller_type="fanuc_0i",
        )

        time_estimation = result["time_estimation"]
        assert "machining_time_min" in time_estimation
        assert "tool_change_time_min" in time_estimation
        assert "total_time_min" in time_estimation
        assert "operation_count" in time_estimation
        assert "tool_change_count" in time_estimation

        # 验证工序数量
        assert time_estimation["operation_count"] == 3
        
        # 验证换刀次数（endmill_D10和drill_D8两种刀具）
        assert time_estimation["tool_change_count"] == 2
        
        # 验证换刀时间（2次 × 1.5分钟 = 3.0分钟）
        assert time_estimation["tool_change_time_min"] == 3.0
        
        # 验证总时间（7.0 + 3.0 = 10.0分钟）
        assert time_estimation["total_time_min"] == 10.0

    def test_dry_run_preview_tool_usage(self, generator, sample_operation_plan):
        """测试刀具使用统计"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
            controller_type="fanuc_0i",
        )

        tool_usage = result["tool_usage"]
        assert "endmill_D10" in tool_usage
        assert "drill_D8" in tool_usage

        # 验证endmill_D10使用统计
        endmill_stats = tool_usage["endmill_D10"]
        assert endmill_stats["usage_count"] == 2  # 粗铣和精铣
        assert "粗铣" in endmill_stats["methods"]
        assert "精铣" in endmill_stats["methods"]
        assert "plane_top" in endmill_stats["features"]

        # 验证drill_D8使用统计
        drill_stats = tool_usage["drill_D8"]
        assert drill_stats["usage_count"] == 1
        assert "钻孔" in drill_stats["methods"]
        assert "hole_01" in drill_stats["features"]

    def test_dry_run_preview_collision_risks_deep_cavity(self, generator, deep_cavity_operation_plan):
        """测试深腔加工碰撞风险提示"""
        result = generator.dry_run_preview(
            operation_plan=deep_cavity_operation_plan,
            controller_type="fanuc_0i",
        )

        collision_risks = result["collision_risks"]
        assert len(collision_risks) > 0

        # 验证深腔风险
        deep_cavity_risk = next(
            (r for r in collision_risks if r["risk_type"] == "deep_cavity"),
            None
        )
        assert deep_cavity_risk is not None
        assert deep_cavity_risk["op_seq"] == 1
        assert deep_cavity_risk["severity"] == "medium"
        assert "深腔加工" in deep_cavity_risk["description"]
        assert "60.0mm" in deep_cavity_risk["description"]

    def test_dry_run_preview_collision_risks_long_rapid_move(self, generator, long_rapid_move_plan):
        """测试长距离快速移动碰撞风险提示"""
        result = generator.dry_run_preview(
            operation_plan=long_rapid_move_plan,
            controller_type="fanuc_0i",
        )

        collision_risks = result["collision_risks"]
        assert len(collision_risks) > 0

        # 验证长距离移动风险
        long_move_risk = next(
            (r for r in collision_risks if r["risk_type"] == "long_rapid_move"),
            None
        )
        assert long_move_risk is not None
        assert long_move_risk["op_seq"] == 1
        assert long_move_risk["severity"] == "low"
        assert "长距离快速移动" in long_move_risk["description"]

    def test_dry_run_preview_checkpoint_positions(self, generator, sample_operation_plan):
        """测试断点位置标记"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
            controller_type="fanuc_0i",
        )

        checkpoints = result["checkpoint_positions"]
        assert len(checkpoints) == 3  # 3个工序

        # 验证第一个断点
        cp1 = checkpoints[0]
        assert cp1["checkpoint_id"] == "CP001"
        assert cp1["op_index"] == 0
        assert cp1["op_name"] == "OP01-粗铣平面"
        assert cp1["feature_name"] == "plane_top"
        assert cp1["estimated_line"] == 100  # 1 * 100

        # 验证第二个断点
        cp2 = checkpoints[1]
        assert cp2["checkpoint_id"] == "CP002"
        assert cp2["op_index"] == 1
        assert cp2["estimated_line"] == 200

    def test_dry_run_preview_warnings_tool_changes(self, generator):
        """测试刀具更换次数过多警告"""
        # 创建12个不同刀具的工序
        operations = []
        for i in range(12):
            op = Operation(
                seq=i + 1,
                name=f"OP{i+1:02d}",
                feature_name=f"feature_{i}",
                machining_method="铣削",
                surface=f"surface_{i}",
                tolerance_grade="IT10",
                tool_type=f"tool_{i}",  # 每个工序使用不同刀具
                cutting_params={"depth": 5.0},
                estimated_time_min=1.0,
            )
            operations.append(op)

        plan = OperationPlan()
        plan.operations = operations
        plan.estimated_time_min = 12.0

        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )

        warnings = result["warnings"]
        tool_change_warning = next(
            (w for w in warnings if "刀具更换次数较多" in w),
            None
        )
        assert tool_change_warning is not None
        assert "12次" in tool_change_warning

    def test_dry_run_preview_warnings_long_time(self, generator):
        """测试加工时间过长警告"""
        operations = [
            Operation(
                seq=1,
                name="OP01",
                feature_name="feature_01",
                machining_method="铣削",
                surface="surface_01",
                tolerance_grade="IT10",
                tool_type="tool_01",
                cutting_params={"depth": 5.0},
                estimated_time_min=70.0,  # 超过60分钟
            ),
        ]

        plan = OperationPlan()
        plan.operations = operations
        plan.estimated_time_min = 70.0

        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )

        warnings = result["warnings"]
        time_warning = next(
            (w for w in warnings if "预估加工时间较长" in w),
            None
        )
        assert time_warning is not None
        assert "70.0分钟" in time_warning

    def test_dry_run_preview_warnings_collision_risks(self, generator, deep_cavity_operation_plan):
        """测试碰撞风险警告"""
        result = generator.dry_run_preview(
            operation_plan=deep_cavity_operation_plan,
            controller_type="fanuc_0i",
        )

        warnings = result["warnings"]
        collision_warning = next(
            (w for w in warnings if "碰撞风险" in w),
            None
        )
        assert collision_warning is not None

    def test_dry_run_preview_empty_operation_plan(self, generator):
        """测试空工序规划处理"""
        plan = OperationPlan()
        plan.operations = []

        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )

        # 验证返回结构完整
        assert isinstance(result, dict)
        assert "tool_path_summary" in result
        assert len(result["tool_path_summary"]) == 0

        # 验证警告信息
        warnings = result["warnings"]
        empty_warning = next(
            (w for w in warnings if "工序规划结果为空" in w),
            None
        )
        assert empty_warning is not None

    def test_dry_run_preview_none_operation_plan(self, generator):
        """测试None工序规划处理"""
        result = generator.dry_run_preview(
            operation_plan=None,
            controller_type="fanuc_0i",
        )

        # 验证返回结构完整
        assert isinstance(result, dict)
        assert len(result["tool_path_summary"]) == 0

        # 验证警告信息
        warnings = result["warnings"]
        empty_warning = next(
            (w for w in warnings if "工序规划结果为空" in w),
            None
        )
        assert empty_warning is not None

    def test_dry_run_preview_default_parameters(self, generator, sample_operation_plan):
        """测试默认参数"""
        result = generator.dry_run_preview(
            operation_plan=sample_operation_plan,
        )

        # 验证默认值（safe_z 默认 80.0：安全高度高于毛坯顶面 50.0）
        assert result["controller_type"] == "fanuc_0i"
        assert result["material"] == "45#钢"
        assert result["program_number"] == 1000
        assert result["safe_z"] == 80.0

    def test_dry_run_preview_multiple_features_same_tool(self, generator):
        """测试同一刀具加工多个特征"""
        operations = [
            Operation(
                seq=1,
                name="OP01-铣平面A",
                feature_name="plane_A",
                machining_method="铣削",
                surface="surface_A",
                tolerance_grade="IT10",
                tool_type="endmill_D10",
                cutting_params={"depth": 5.0},
                estimated_time_min=2.0,
            ),
            Operation(
                seq=2,
                name="OP02-铣平面B",
                feature_name="plane_B",
                machining_method="铣削",
                surface="surface_B",
                tolerance_grade="IT10",
                tool_type="endmill_D10",
                cutting_params={"depth": 5.0},
                estimated_time_min=2.0,
            ),
        ]

        plan = OperationPlan()
        plan.operations = operations
        plan.estimated_time_min = 4.0

        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )

        # 验证刀具统计
        tool_usage = result["tool_usage"]
        assert "endmill_D10" in tool_usage
        assert tool_usage["endmill_D10"]["usage_count"] == 2
        assert "plane_A" in tool_usage["endmill_D10"]["features"]
        assert "plane_B" in tool_usage["endmill_D10"]["features"]
