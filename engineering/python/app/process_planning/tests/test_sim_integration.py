"""仿真集成模块单元测试。

测试 SimulationIntegration 类的功能和与工艺规划流水线的集成。
"""

import pytest
from unittest.mock import Mock, patch
from app.process_planning.sim_integration import (
    SimulationIntegration,
    SimulationResult,
    run_simulation_for_operation,
)


class TestSimulationResult:
    """测试 SimulationResult 数据类。"""

    def test_default_values(self):
        """测试默认值初始化。"""
        result = SimulationResult()
        assert result.status == "not_run"
        assert result.passed is False
        assert result.score == 0.0
        assert result.recommendation == "not_recommended"
        assert result.cutting_force is None
        assert result.chatter_stability is None
        assert result.duration_ms == 0.0
        assert result.error_message == ""

    def test_to_dict(self):
        """测试转换为字典。"""
        result = SimulationResult(
            status="success",
            passed=True,
            score=85.5,
            recommendation="recommended",
            cutting_force={"Fx": 300, "Fy": 250, "Fz": 400, "method": "pinn"},
            chatter_stability={"stable": True, "limit_depth": 3.5, "method": "neural_network"},
            duration_ms=125.5,
            error_message="",
        )

        result_dict = result.to_dict()

        assert result_dict["status"] == "success"
        assert result_dict["passed"] is True
        assert result_dict["score"] == 85.5
        assert result_dict["recommendation"] == "recommended"
        assert result_dict["cutting_force"]["Fx"] == 300
        assert result_dict["chatter_stability"]["stable"] is True
        assert result_dict["duration_ms"] == 125.5
        assert result_dict["error_message"] == ""


class TestSimulationIntegration:
    """测试 SimulationIntegration 类。"""

    def test_init_default_timeout(self):
        """测试默认超时时间初始化。"""
        simulator = SimulationIntegration()
        assert simulator.timeout_seconds == 5.0

    def test_init_custom_timeout(self):
        """测试自定义超时时间初始化。"""
        simulator = SimulationIntegration(timeout_seconds=10.0)
        assert simulator.timeout_seconds == 10.0

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_run_simulation_success(self, mock_stability, mock_force):
        """测试成功运行仿真。"""
        # 模拟切削力预测返回
        mock_force.return_value = {
            "Fx": 300.0,
            "Fy": 250.0,
            "Fz": 400.0,
            "method": "pinn",
        }

        # 模拟颤振稳定性预测返回
        mock_stability.return_value = {
            "stable": True,
            "limit_depth": 3.5,
            "method": "neural_network",
        }

        simulator = SimulationIntegration()
        result = simulator.run_simulation(
            material="45steel",
            tool="endmill_d10",
            spindle_rpm=8000,
            feed_rate=1200,
            depth_of_cut=2.0,
        )

        assert result.status == "success"
        assert result.passed is True
        assert result.score > 0
        assert result.recommendation in ["recommended", "acceptable"]
        assert result.cutting_force is not None
        assert result.chatter_stability is not None
        assert result.duration_ms > 0

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_run_simulation_force_exceeded(self, mock_stability, mock_force):
        """测试切削力超过阈值。"""
        # 模拟切削力超过阈值
        mock_force.return_value = {
            "Fx": 600.0,  # 超过 500N 阈值
            "Fy": 500.0,  # 超过 400N 阈值
            "Fz": 700.0,  # 超过 600N 阈值
            "method": "pinn",
        }

        mock_stability.return_value = {
            "stable": True,
            "limit_depth": 3.5,
            "method": "neural_network",
        }

        simulator = SimulationIntegration()
        result = simulator.run_simulation(
            material="45steel",
            tool="endmill_d10",
            spindle_rpm=8000,
            feed_rate=1200,
            depth_of_cut=2.0,
        )

        assert result.status == "success"
        assert result.passed is False  # 切削力超限，应标记为不通过
        assert result.recommendation == "not_recommended"

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_run_simulation_chatter_unstable(self, mock_stability, mock_force):
        """测试颤振不稳定。"""
        mock_force.return_value = {
            "Fx": 300.0,
            "Fy": 250.0,
            "Fz": 400.0,
            "method": "pinn",
        }

        # 模拟颤振不稳定
        mock_stability.return_value = {
            "stable": False,
            "limit_depth": 1.5,  # 极限切深小于实际切深 2.0
            "method": "neural_network",
        }

        simulator = SimulationIntegration()
        result = simulator.run_simulation(
            material="45steel",
            tool="endmill_d10",
            spindle_rpm=8000,
            feed_rate=1200,
            depth_of_cut=2.0,
        )

        assert result.status == "success"
        assert result.passed is False  # 颤振不稳定，应标记为不通过
        assert result.recommendation == "not_recommended"

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_run_simulation_exception(self, mock_stability, mock_force):
        """测试仿真过程中发生异常。"""
        mock_force.side_effect = Exception("仿真服务错误")

        simulator = SimulationIntegration()
        result = simulator.run_simulation(
            material="45steel",
            tool="endmill_d10",
            spindle_rpm=8000,
            feed_rate=1200,
            depth_of_cut=2.0,
        )

        assert result.status == "failed"
        assert result.passed is False
        assert result.recommendation == "not_recommended"
        assert "error_message" in result.to_dict()

    def test_calculate_score_high_score(self):
        """测试高分计算（切削力和颤振都在安全范围）。"""
        simulator = SimulationIntegration()

        force_result = {
            "Fx": 200.0,
            "Fy": 150.0,
            "Fz": 300.0,
        }

        chatter_result = {
            "stable": True,
            "limit_depth": 4.0,  # 极限切深是实际切深的 2 倍
        }

        score = simulator._calculate_score(force_result, chatter_result, depth_of_cut=2.0)

        assert score >= 80  # 应该得到高分

    def test_calculate_score_low_score(self):
        """测试低分计算（切削力超限或颤振不稳定）。"""
        simulator = SimulationIntegration()

        force_result = {
            "Fx": 600.0,  # 超限
            "Fy": 500.0,  # 超限
            "Fz": 700.0,  # 超限
        }

        chatter_result = {
            "stable": False,
            "limit_depth": 1.0,
        }

        score = simulator._calculate_score(force_result, chatter_result, depth_of_cut=2.0)

        assert score < 60  # 应该得到低分

    def test_evaluate_pass_all_conditions_met(self):
        """测试所有条件满足时通过评估。"""
        simulator = SimulationIntegration()

        force_result = {
            "Fx": 300.0,
            "Fy": 250.0,
            "Fz": 400.0,
        }

        chatter_result = {
            "stable": True,
            "limit_depth": 3.5,
        }

        passed = simulator._evaluate_pass(force_result, chatter_result, depth_of_cut=2.0)

        assert passed is True

    def test_evaluate_pass_force_exceeded(self):
        """测试切削力超限时不通过。"""
        simulator = SimulationIntegration()

        force_result = {
            "Fx": 600.0,  # 超过阈值 500N
            "Fy": 250.0,
            "Fz": 400.0,
        }

        chatter_result = {
            "stable": True,
            "limit_depth": 3.5,
        }

        passed = simulator._evaluate_pass(force_result, chatter_result, depth_of_cut=2.0)

        assert passed is False

    def test_evaluate_pass_chatter_unstable(self):
        """测试颤振不稳定时不通过。"""
        simulator = SimulationIntegration()

        force_result = {
            "Fx": 300.0,
            "Fy": 250.0,
            "Fz": 400.0,
        }

        chatter_result = {
            "stable": False,
            "limit_depth": 1.5,  # 小于实际切深 2.0
        }

        passed = simulator._evaluate_pass(force_result, chatter_result, depth_of_cut=2.0)

        assert passed is False

    def test_generate_recommendation_recommended(self):
        """测试生成推荐级别 - recommended。"""
        simulator = SimulationIntegration()

        recommendation = simulator._generate_recommendation(passed=True, score=85.0)
        assert recommendation == "recommended"

    def test_generate_recommendation_acceptable(self):
        """测试生成推荐级别 - acceptable。"""
        simulator = SimulationIntegration()

        recommendation = simulator._generate_recommendation(passed=True, score=70.0)
        assert recommendation == "acceptable"

    def test_generate_recommendation_not_recommended_low_score(self):
        """测试生成推荐级别 - not_recommended（低分）。"""
        simulator = SimulationIntegration()

        recommendation = simulator._generate_recommendation(passed=True, score=50.0)
        assert recommendation == "not_recommended"

    def test_generate_recommendation_not_recommended_failed(self):
        """测试生成推荐级别 - not_recommended（未通过）。"""
        simulator = SimulationIntegration()

        recommendation = simulator._generate_recommendation(passed=False, score=85.0)
        assert recommendation == "not_recommended"


class TestRunSimulationForOperation:
    """测试 run_simulation_for_operation 便捷函数。"""

    @patch("app.process_planning.sim_integration.SimulationIntegration")
    def test_run_simulation_for_operation(self, mock_sim_class):
        """测试为工序运行仿真。"""
        # 模拟仿真器
        mock_simulator = Mock()
        mock_sim_class.return_value = mock_simulator

        mock_result = SimulationResult(
            status="success",
            passed=True,
            score=85.0,
            recommendation="recommended",
        )
        mock_simulator.run_simulation.return_value = mock_result

        operation = {
            "tool": "endmill_d10",
            "spindle_rpm": 8000,
            "feed_rate": 1200,
            "depth_of_cut": 2.0,
            "machine": "vmc_850",
        }

        result = run_simulation_for_operation(
            operation=operation,
            material="45steel",
            timeout_seconds=5.0,
        )

        assert result.status == "success"
        assert result.passed is True
        assert result.score == 85.0
        assert result.recommendation == "recommended"


class TestPipelineIntegration:
    """测试与工艺规划流水线的集成。"""

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_pipeline_includes_simulation(self, mock_stability, mock_force):
        """测试流水线结果包含仿真数据。"""
        from app.process_planning.pipeline import plan_process

        # 模拟仿真返回
        mock_force.return_value = {
            "Fx": 300.0,
            "Fy": 250.0,
            "Fz": 400.0,
            "method": "pinn",
        }

        mock_stability.return_value = {
            "stable": True,
            "limit_depth": 3.5,
            "method": "neural_network",
        }

        # 运行工艺规划
        plan = plan_process(
            feature="pocket_cavity",
            material="45steel",
            tool="endmill_d10",
        )

        # 验证仿真数据存在
        assert "simulation" in plan
        assert "score" in plan["simulation"]
        assert "passed" in plan["simulation"]
        assert "recommendation" in plan["simulation"]
        assert "status" in plan["simulation"]

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_pipeline_simulation_not_recommended(self, mock_stability, mock_force):
        """测试流水线中仿真不通过时标记为不推荐。"""
        from app.process_planning.pipeline import plan_process

        # 模拟仿真失败
        mock_force.return_value = {
            "Fx": 600.0,  # 超限
            "Fy": 500.0,
            "Fz": 700.0,
            "method": "pinn",
        }

        mock_stability.return_value = {
            "stable": False,
            "limit_depth": 1.0,
            "method": "neural_network",
        }

        plan = plan_process(
            feature="pocket_cavity",
            material="45steel",
            tool="endmill_d10",
        )

        # 验证仿真未通过且标记为不推荐
        assert "simulation" in plan
        assert plan["simulation"]["passed"] is False
        assert plan["simulation"]["recommendation"] == "not_recommended"

    @patch("app.process_planning.sim_integration.predict_cutting_force")
    @patch("app.process_planning.sim_integration.predict_stability")
    def test_pipeline_simulation_failure_graceful(self, mock_stability, mock_force):
        """测试流水线中仿真失败时仍能返回基础方案。"""
        from app.process_planning.pipeline import plan_process

        # 模拟仿真服务异常
        mock_force.side_effect = Exception("仿真服务不可用")

        plan = plan_process(
            feature="pocket_cavity",
            material="45steel",
            tool="endmill_d10",
        )

        # 验证流水线仍然成功返回（降级处理）
        assert plan is not None
        assert "success" in plan
        # 仿真失败不应阻断主流程，仿真状态为 failed 或 not_run（流水线提前终止）
        if "simulation" in plan:
            assert plan["simulation"]["status"] in ("failed", "not_run")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
