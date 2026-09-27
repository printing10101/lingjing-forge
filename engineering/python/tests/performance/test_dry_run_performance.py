

"""Dry-Run预览功能性能测试

测试目标：
- 验证大数据量下的dry-run预览性能
- 测量不同工序数量下的响应时间
- 验证内存使用是否在合理范围内
- 确保性能瓶颈可识别

测试场景：
1. 小规模（10道工序）
2. 中规模（50道工序）
3. 大规模（100道工序）
4. 超大规模（200道工序）- 压力测试
"""

import pytest
import time
import tracemalloc

from app.process_planning.gcode_generator import GCodeGenerator
from app.process_planning.operation_sequencer import OperationPlan, Operation

pytestmark = pytest.mark.skip_ci









class TestDryRunPerformance:
    """Dry-Run预览性能测试套件"""
    
    @pytest.fixture
    def generator(self):
        """创建GCodeGenerator实例"""
        return GCodeGenerator()
    
    def _create_operation_plan(self, num_operations: int) -> OperationPlan:
        """创建指定工序数量的OperationPlan
        
        Args:
            num_operations: 工序数量
            
        Returns:
            OperationPlan: 包含指定数量工序的计划
        """
        operations = []
        for i in range(1, num_operations + 1):
            op = Operation(
                seq=i,
                name=f"OP{i:02d}-测试工序",
                feature_name=f"特征{i}",
                machining_method="铣削" if i % 2 == 0 else "钻孔",
                surface="A",
                tolerance_grade="IT7",
                tool_type="立铣刀D10" if i % 2 == 0 else "麻花钻D8",
                cutting_params={
                    "start_x": float(i * 10),
                    "start_y": float(i * 5),
                    "depth": 5.0 + (i % 3) * 2.0,
                    "feed_rate": 100.0,
                    "spindle_speed": 1000,
                },
                estimated_time_min=2.0 + (i % 5) * 0.5,
            )
            operations.append(op)
        
        return OperationPlan(
            operations=operations,
            setups=[],
            estimated_time_min=sum(op.estimated_time_min for op in operations),
            face_change_count=0,
            fixture_recommendations=[],
        )
    
    def test_small_scale_performance(self, generator):
        """小规模性能测试：10道工序"""
        plan = self._create_operation_plan(10)
        
        start_time = time.time()
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        elapsed = time.time() - start_time
        
        # 验证性能指标
        assert elapsed < 0.1, f"小规模测试响应时间过长: {elapsed:.3f}s"
        
        # 验证结果完整性
        assert len(result["tool_path_summary"]) == 10
        assert "time_estimation" in result
        assert "collision_risks" in result
        
        print(f"\n小规模测试（10工序）: {elapsed*1000:.2f}ms")
    
    def test_medium_scale_performance(self, generator):
        """中规模性能测试：50道工序"""
        plan = self._create_operation_plan(50)
        
        start_time = time.time()
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        elapsed = time.time() - start_time
        
        # 验证性能指标（允许更长时间）
        assert elapsed < 0.5, f"中规模测试响应时间过长: {elapsed:.3f}s"
        
        # 验证结果完整性
        assert len(result["tool_path_summary"]) == 50
        
        print(f"\n中规模测试（50工序）: {elapsed*1000:.2f}ms")
    
    def test_large_scale_performance(self, generator):
        """大规模性能测试：100道工序"""
        plan = self._create_operation_plan(100)
        
        start_time = time.time()
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        elapsed = time.time() - start_time
        
        # 验证性能指标
        assert elapsed < 1.0, f"大规模测试响应时间过长: {elapsed:.3f}s"
        
        # 验证结果完整性
        assert len(result["tool_path_summary"]) == 100
        
        print(f"\n大规模测试（100工序）: {elapsed*1000:.2f}ms")
    
    def test_ultra_large_scale_stress(self, generator):
        """超大规模压力测试：200道工序"""
        plan = self._create_operation_plan(200)
        
        start_time = time.time()
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        elapsed = time.time() - start_time
        
        # 压力测试，记录时间但不强制限制
        print(f"\n超大规模测试（200工序）: {elapsed*1000:.2f}ms")
        
        # 验证结果完整性
        assert len(result["tool_path_summary"]) == 200
        
        # 验证不会超时（5秒内完成）
        assert elapsed < 5.0, f"压力测试超时: {elapsed:.3f}s"
    
    def test_memory_usage(self, generator):
        """内存使用测试"""
        tracemalloc.start()
        
        plan = self._create_operation_plan(100)
        
        # 记录初始内存
        current, peak = tracemalloc.get_traced_memory()
        
        # 执行预览
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        
        # 记录峰值内存
        current_after, peak_after = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        memory_increase = peak_after - peak
        
        # 验证内存增长在合理范围内（< 50MB）
        assert memory_increase < 50 * 1024 * 1024, \
            f"内存增长过大: {memory_increase / 1024 / 1024:.2f}MB"
        
        print("\n内存使用测试（100工序）:")
        print(f"  峰值内存增长: {memory_increase / 1024:.2f}KB")
    
    def test_multiple_controllers_performance(self, generator):
        """多控制器类型性能测试"""
        plan = self._create_operation_plan(50)
        controllers = ["fanuc_0i", "siemens_840d", "heidenhain_tnc640"]
        
        results = {}
        for controller in controllers:
            start_time = time.time()
            result = generator.dry_run_preview(
                operation_plan=plan,
                controller_type=controller,
            )
            elapsed = time.time() - start_time
            results[controller] = elapsed
            
            assert elapsed < 0.5, f"{controller}响应时间过长: {elapsed:.3f}s"
        
        print("\n多控制器性能测试（50工序）:")
        for ctrl, time_cost in results.items():
            print(f"  {ctrl}: {time_cost*1000:.2f}ms")
    
    def test_collision_detection_performance(self, generator):
        """碰撞检测性能测试"""
        # 创建包含潜在碰撞风险的工序计划
        plan = self._create_operation_plan(50)
        
        start_time = time.time()
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        elapsed = time.time() - start_time
        
        # 验证碰撞检测不会显著影响性能
        assert elapsed < 0.5, f"碰撞检测性能问题: {elapsed:.3f}s"
        
        # 验证碰撞风险列表存在
        assert "collision_risks" in result
        assert isinstance(result["collision_risks"], list)
        
        print(f"\n碰撞检测性能测试（50工序）: {elapsed*1000:.2f}ms")
        print(f"  检测到 {len(result['collision_risks'])} 个潜在风险")


class TestDNCPerformance:
    """DNC传输性能测试"""
    
    @pytest.fixture
    def generator(self):
        return GCodeGenerator()
    
    def _create_operation_plan(self, num_operations: int) -> OperationPlan:
        operations = []
        for i in range(1, num_operations + 1):
            op = Operation(
                seq=i,
                name=f"OP{i:02d}",
                feature_name=f"特征{i}",
                machining_method="铣削" if i % 2 == 0 else "钻孔",
                surface="A",
                tolerance_grade="IT7",
                cutting_params={"depth": 5.0},
                estimated_time_min=2.0,
            )
            operations.append(op)
        return OperationPlan(operations=operations)
    
    def test_large_gcode_preview_performance(self, generator):
        """大型G代码预览性能测试（替代G代码生成）"""
        plan = self._create_operation_plan(100)
        
        start_time = time.time()
        result = generator.dry_run_preview(
            operation_plan=plan,
            controller_type="fanuc_0i",
        )
        elapsed = time.time() - start_time
        
        # 验证预览时间
        assert elapsed < 1.0, f"大型预览时间过长: {elapsed:.3f}s"
        
        # 验证结果完整性
        assert len(result["tool_path_summary"]) == 100
        
        print("\n大型G代码预览测试（100工序）:")
        print(f"  预览时间: {elapsed*1000:.2f}ms")
        print(f"  工序数量: {len(result['tool_path_summary'])}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
