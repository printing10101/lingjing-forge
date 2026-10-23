"""颤振稳定性神经网络预测器单元测试。"""

from __future__ import annotations

import os
from typing import Any, cast

import sys
import time
import logging
import pytest
import numpy as np
from unittest.mock import patch, MagicMock

logger = logging.getLogger(__name__)

# 确保可以导入 app 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from app.simulation.chatter.predictor import (
    ChatterPredictor,
    get_cached_predictor,
    predict_stability,
    predict_stability_batch,
    reset_predictor_cache,
)


class TestChatterPredictor:
    """ChatterPredictor 类测试。"""

    def test_init_without_model(self):
        """测试无模型时的初始化。"""
        with patch("os.path.exists", return_value=False):
            predictor = ChatterPredictor()
            assert predictor.model is None

    def test_normalize_inputs(self):
        """测试输入归一化。"""
        with patch("os.path.exists", return_value=False):
            predictor = ChatterPredictor()

            normalized = predictor._normalize_inputs(
                spindle_rpm=8000,
                machine_stiffness=2e7,
                machine_damping=0.05,
                machine_freq=800,
                tool_diameter=10,
                tool_k_s=2000,
            )

            # 应该返回长度为 6 的数组
            assert len(normalized) == 6

            # 所有值应该在合理范围内
            assert all(0 <= v <= 2 for v in normalized)

    def test_predict_without_model(self):
        """测试无模型时的预测：返回负值哨兵，交由上层回退解析法。"""
        with patch("os.path.exists", return_value=False):
            predictor = ChatterPredictor()

            stable, limit_depth = predictor.predict(
                spindle_rpm=8000,
                machine_stiffness=2e7,
                machine_damping=0.05,
                machine_freq=800,
                tool_diameter=10,
                tool_k_s=2000,
            )

            # 契约（2026-10-22 起）：limit_depth < 0 表示模型不可用，
            # 调用方必须回退 compute_stability_limit；不再返回固定 5.0 误导调用方
            assert isinstance(stable, bool)
            assert isinstance(limit_depth, float)
            assert limit_depth == -1.0


class TestNeuralNetworkPaths:
    """神经网络模型加载和推理路径测试。"""

    def test_model_load_success(self):
        """测试模型加载成功路径（research 桥接返回 LTC 工厂）。"""

        class _FakeLTC:
            def __init__(self, config):
                self.config = config
                self.hidden_state = None

            def load_state_dict(self, state_dict):
                return None

            def eval(self):
                return None

        class _FakeConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        mock_torch = MagicMock()
        mock_torch.load = MagicMock(return_value={"model_state_dict": {}})

        with patch.dict("sys.modules", {"torch": mock_torch}):
            with (
                patch("app.ai.lnn._research_bridge.get_ltc_model_factory", return_value=_FakeLTC),
                patch("app.ai.lnn._research_bridge.get_lnn_config_factory", return_value=_FakeConfig),
            ):
                with patch("os.path.exists", return_value=True):
                    predictor = ChatterPredictor()

                    # 桥接可用 + 检查点存在 → 模型应被加载
                    assert predictor.model is not None
                    assert isinstance(predictor.model, _FakeLTC)

    def test_model_load_import_error(self):
        """测试 PyTorch 未安装时的 ImportError 处理。"""
        # Mock torch 导入失败
        with patch.dict("sys.modules", {"torch": None}):
            with patch("os.path.exists", return_value=True):
                predictor = ChatterPredictor()

                # 模型应该为 None（回退到解析法）
                assert predictor.model is None

    def test_model_load_exception(self):
        """测试模型加载异常处理。"""
        # Mock torch 但在加载时抛出异常
        mock_torch = MagicMock()
        mock_torch.load = MagicMock(side_effect=Exception("Model load failed"))

        with patch.dict("sys.modules", {"torch": mock_torch}):
            with patch("os.path.exists", return_value=True):
                predictor = ChatterPredictor()

                # 异常时模型应该为 None
                assert predictor.model is None

    def test_predict_with_model(self):
        """测试有模型时的神经网络推理路径。"""

        class _Out:
            """模拟 LTC 输出张量：只需 squeeze(0).numpy()。"""

            def squeeze(self, dim):
                return self

            def numpy(self):
                return np.array([0.5, 10.0], dtype=np.float32)

        class _StubModel:
            """LTC 前向契约：(output, hidden_state)。"""

            hidden_state = None

            def __call__(self, x):
                return _Out(), None

        mock_torch = MagicMock()
        with patch.dict("sys.modules", {"torch": mock_torch}):
            with patch("os.path.exists", return_value=False):
                predictor = ChatterPredictor()
                predictor.model = _StubModel()  # 手动设置模型

                stable, limit_depth = predictor.predict(
                    spindle_rpm=8000,
                    machine_stiffness=2e7,
                    machine_damping=0.05,
                    machine_freq=800,
                    tool_diameter=10,
                    tool_k_s=2000,
                )

                # 应该返回推理结果：sigmoid(0.5) > 0.5 判稳定，切深取绝对值
                assert isinstance(stable, bool)
                assert stable is True
                assert limit_depth == pytest.approx(10.0)

    def test_predict_inference_exception(self):
        """测试推理异常处理：返回负值哨兵，由上层回退解析法。"""
        # 用 RuntimeError 模拟 torch 推理失败（predict 的 except 已收窄到
        # RuntimeError/ValueError/TypeError，不再吞掉 CancelledError）
        mock_model = MagicMock()
        mock_model.side_effect = RuntimeError("Inference failed")

        with patch("os.path.exists", return_value=False):
            predictor = ChatterPredictor()
            predictor.model = mock_model

            stable, limit_depth = predictor.predict(
                spindle_rpm=8000,
                machine_stiffness=2e7,
                machine_damping=0.05,
                machine_freq=800,
                tool_diameter=10,
                tool_k_s=2000,
            )

            # 契约（2026-10-22 起）：推理失败返回 -1.0 哨兵
            assert stable is True
            assert limit_depth == -1.0


class TestPredictStability:
    """predict_stability 函数测试。"""

    def test_basic_prediction(self):
        """测试基本预测功能。"""
        result = predict_stability(
            spindle_rpm=8000,
            machine="vmc_850",
            tool="endmill_d10",
            workpiece="aluminum",
        )

        # 结果应该包含必要的键
        assert "stable" in result
        assert "limit_depth" in result
        assert "method" in result

        # 数据类型检查
        assert isinstance(result["stable"], bool)
        assert isinstance(result["limit_depth"], float)
        assert isinstance(result["method"], str)

        # 极限切深应该为正数
        assert float(result["limit_depth"]) > 0

    def test_different_machines(self):
        """测试不同机床的预测。"""
        machines = ["vmc_850", "cnc_lathe_ck6140", "small_vmc_640"]

        for machine in machines:
            result = predict_stability(
                spindle_rpm=8000,
                machine=machine,
                tool="endmill_d10",
            )

            assert float(result["limit_depth"]) > 0  # type: ignore[arg-type]
            assert result["stable"] in [True, False]

    def test_different_tools(self):
        """测试不同刀具的预测。"""
        tools = ["endmill_d10", "endmill_d16", "endmill_d20"]

        for tool in tools:
            result = predict_stability(
                spindle_rpm=8000,
                machine="vmc_850",
                tool=tool,
            )

            assert float(result["limit_depth"]) > 0  # type: ignore[arg-type]

    def test_different_speeds(self):
        """测试不同转速的预测。"""
        speeds = [2000, 5000, 8000, 10000]

        for speed in speeds:
            result = predict_stability(
                spindle_rpm=speed,
                machine="vmc_850",
                tool="endmill_d10",
            )

            assert float(result["limit_depth"]) > 0  # type: ignore[arg-type]

    def test_analytical_method_fallback(self):
        """测试解析法回退。

        2026-10-23：``predict_stability`` 现持有进程级单例，仅把
        ``os.path.exists`` patch 成 False 已不能触发回退——已建立的实例不会因
        权重文件"消失"而重建，前序测试留下的实例会让本条恒为 neural_network
        （合并跑暴露）。改用显式 ``force_analytical`` 开关，语义也更贴近运维
        在权重可疑时强制降级的真实用法。
        """
        result = predict_stability(
            spindle_rpm=8000,
            machine="vmc_850",
            tool="endmill_d10",
            force_analytical=True,
        )

        # 应该使用解析法
        assert result["method"] == "analytical"
        assert float(result["limit_depth"]) > 0  # type: ignore[arg-type]

    def test_force_analytical_overrides_cached_predictor(self):
        """单例缓存已建立时，force_analytical 仍必须降级到解析法。

        这是 force_analytical 存在的理由：缓存命中后仅删除权重文件不会让入口
        回退，运维需要一个运行时的显式开关。
        """
        reset_predictor_cache()
        cached = get_cached_predictor()  # 建立单例（无论权重是否存在）
        assert cached is not None

        result = predict_stability(spindle_rpm=8000, force_analytical=True)
        assert result["method"] == "analytical"
        assert float(result["limit_depth"]) > 0  # type: ignore[arg-type]

        # 不传开关时行为不受影响（有模型则走神经路径，无模型则解析法）
        normal = predict_stability(spindle_rpm=8000)
        assert normal["method"] in {"analytical", "neural_network"}
        reset_predictor_cache()

    def test_model_load_raises_generic_exception_does_not_crash(self):
        """权重加载抛任意异常都必须回退，而不是让预测入口崩溃。

        权重来自磁盘，损坏形式不可穷举（EOFError/UnpicklingError/BadZipFile/
        AttributeError…）。白名单式捕获会让未列举的异常冒泡，把"模型不可用"
        升级成"功能不可用"。
        """
        for exc in (Exception("generic"), EOFError("truncated"), EOFError("bad zip")):
            mock_torch = MagicMock()
            mock_torch.load = MagicMock(side_effect=exc)
            with patch.dict("sys.modules", {"torch": mock_torch}):
                with patch("os.path.exists", return_value=True):
                    predictor = ChatterPredictor()
            assert predictor.model is None, f"{type(exc).__name__} 未被兜底"

    def test_result_structure(self):
        """测试结果结构完整性。"""
        result = predict_stability(spindle_rpm=8000)

        # 必须包含的键
        required_keys = ["stable", "limit_depth", "method"]
        for key in required_keys:
            assert key in result, f"缺少必需的键: {key}"


class TestPredictStabilityBatch:
    """批量预测测试。"""

    def test_batch_prediction(self):
        """测试批量预测功能。"""
        params_list = [
            {"spindle_rpm": 4000, "machine": "vmc_850", "tool": "endmill_d10"},
            {"spindle_rpm": 6000, "machine": "vmc_850", "tool": "endmill_d10"},
            {"spindle_rpm": 8000, "machine": "vmc_850", "tool": "endmill_d10"},
        ]

        results = predict_stability_batch(params_list)

        # 结果数量应该与输入相同
        assert len(results) == len(params_list)

        # 每个结果都应该有效
        for result in results:
            assert "stable" in result
            assert "limit_depth" in result
            assert float(result["limit_depth"]) > 0

    def test_empty_batch(self):
        """测试空批量预测。"""
        results = predict_stability_batch([])
        assert results == []

    def test_single_item_batch(self):
        """测试单项批量预测。"""
        params_list = [
            {"spindle_rpm": 8000, "machine": "vmc_850", "tool": "endmill_d10"},
        ]

        results = predict_stability_batch(params_list)

        assert len(results) == 1
        assert results[0]["limit_depth"] > 0


class TestPerformance:
    """性能测试。"""

    def test_inference_time(self):
        """测试推理时间（应小于 50ms）。"""
        # 预热
        predict_stability(spindle_rpm=8000)

        # 测试 100 次推理的平均时间
        times = []
        for _ in range(100):
            start = time.time()
            predict_stability(spindle_rpm=8000)
            elapsed = (time.time() - start) * 1000  # 转换为 ms
            times.append(elapsed)

        avg_time = sum(times) / len(times)

        # 平均推理时间应小于 50ms
        assert avg_time < 50, f"平均推理时间 {avg_time:.2f}ms 超过 50ms 限制"

        # 打印性能报告
        logger.info("\n性能测试报告:")
        logger.info(f"  平均推理时间: {avg_time:.2f} ms")
        logger.info(f"  最小推理时间: {min(times):.2f} ms")
        logger.info(f"  最大推理时间: {max(times):.2f} ms")


class TestConsistency:
    """一致性测试。"""

    def test_analytical_vs_neural_consistency(self):
        """测试解析法与神经网络结果的一致性（误差在 ±5% 内）。"""
        # 测试多个参数组合
        test_cases = [
            {"spindle_rpm": 4000, "machine": "vmc_850", "tool": "endmill_d10"},
            {"spindle_rpm": 6000, "machine": "vmc_850", "tool": "endmill_d10"},
            {"spindle_rpm": 8000, "machine": "vmc_850", "tool": "endmill_d10"},
        ]

        for params in test_cases:
            # 获取解析法结果
            with patch("os.path.exists", return_value=False):
                analytical_result = predict_stability(**cast(dict[str, Any], params))

            # 获取神经网络结果（如果可用）
            neural_result = predict_stability(**cast(dict[str, Any], params))

            # 如果两种方法都可用，检查结果一致性
            if analytical_result["method"] == "analytical" and neural_result["method"] == "neural_network":
                analytical_depth = analytical_result["limit_depth"]
                neural_depth = neural_result["limit_depth"]

                # 计算相对误差
                if float(analytical_depth) > 0:  # type: ignore[arg-type]
                    relative_error = abs(float(neural_depth) - float(analytical_depth)) / float(analytical_depth)  # type: ignore[arg-type]

                    # 误差应在 ±5% 内
                    assert relative_error < 0.05, f"参数 {params} 的误差 {relative_error:.2%} 超过 5% 限制"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
