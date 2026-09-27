"""训练集导出适配器（exporters.py）与权重桥接（weight_bridge.py）单测."""

from __future__ import annotations

import numpy as np
import pytest

from app.training.weight_bridge import (
    fit_standardization,
    fold_standardization,
    numpy_mirror_forward,
    regression_metrics,
)

pytestmark = pytest.mark.unit


def _random_mlp(layer_dims: list[int], seed: int = 0):
    rng = np.random.default_rng(seed)
    weights = [rng.standard_normal((layer_dims[i], layer_dims[i + 1])) * 0.5 for i in range(len(layer_dims) - 1)]
    biases = [rng.standard_normal((d,)) * 0.1 for d in layer_dims[1:]]
    return weights, biases


class TestFoldStandardization:
    def test_folded_forward_equals_manual_pipeline(self):
        """折叠前（手动标准化→前向→反标准化）与折叠后（原始输入直推）必须等价。"""
        layer_dims = [4, 8, 8, 1]
        weights, biases = _random_mlp(layer_dims)
        rng = np.random.default_rng(1)
        x = rng.standard_normal((50, 4)) * np.array([1000.0, 100.0, 1.0, 1.0]) + np.array([4000.0, 400.0, 1.0, 2.0])

        x_mean, x_std, y_mean, y_std = fit_standardization(x, x[:, 0] * 2 + 5)
        y = x[:, 0] * 2 + 5

        # 手动管线：标准化 → 前向 → 反标准化
        xs = (x - x_mean) / x_std
        y_std_pred = numpy_mirror_forward(xs, weights, biases).reshape(-1) * y_std + y_mean

        # 折叠后：原始输入直推
        fw, fb = fold_standardization(weights, biases, x_mean, x_std, y_mean, y_std)
        y_folded = numpy_mirror_forward(x, fw, fb).reshape(-1)

        np.testing.assert_allclose(y_folded, y_std_pred, rtol=1e-10, atol=1e-8)

    def test_zero_std_column_rejected(self):
        weights, biases = _random_mlp([3, 4, 1])
        with pytest.raises(ValueError, match="标准差非正"):
            fold_standardization(weights, biases, np.zeros(3), np.zeros(3), 0.0, 1.0)

    def test_empty_weights_rejected(self):
        with pytest.raises(ValueError, match="权重层为空"):
            fold_standardization([], [], np.zeros(1), np.ones(1), 0.0, 1.0)

    def test_constant_target_std_fallback(self):
        x = np.ones((10, 2)) * 3.0
        x_mean, x_std, y_mean, y_std = fit_standardization(x, np.full(10, 7.0))
        assert np.all(x_std == 1.0)  # 常数列回退 1
        assert y_std == 1.0 and y_mean == 7.0


class TestMirrorForward:
    def test_matches_relu_mlp_semantics(self):
        """与「ReLU 隐层 + 线性输出」的逐层语义一致（负值被截断）。"""
        w = [np.array([[1.0], [1.0]]), np.array([[1.0], [2.0]])]  # (2,1),(1,2)... 展示形状灵活性
        w = [np.eye(2), np.array([[1.0], [1.0]])]
        b = [np.array([0.0, -10.0]), np.array([0.5])]
        x = np.array([[1.0, 2.0]])
        # h1 = relu(x - [0,10]) = [1,0]; y = [1,0]@[ [1],[1] ] + 0.5 = 1.5
        assert numpy_mirror_forward(x, w, b)[0, 0] == 1.5


class TestRegressionMetrics:
    def test_perfect_prediction(self):
        y = np.array([1.0, 2.0, 3.0])
        m = regression_metrics(y, y)
        assert m["r2"] == pytest.approx(1.0)
        assert m["mae"] == 0.0 and m["mape_percent"] == 0.0

    def test_known_values(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0])
        y_pred = np.array([2.0, 3.0, 4.0, 5.0])
        m = regression_metrics(y_true, y_pred)
        # SS_res=4, SS_tot=5 → R²=0.2
        assert m["r2"] == pytest.approx(0.2)
        assert m["mae"] == pytest.approx(1.0)
        assert m["max_abs_error"] == pytest.approx(1.0)

    def test_constant_target_r2_nan(self):
        m = regression_metrics(np.ones(5), np.ones(5) + 1)
        assert np.isnan(m["r2"])

    def test_shape_mismatch_rejected(self):
        with pytest.raises(ValueError, match="形状不一致"):
            regression_metrics(np.ones(3), np.ones(4))
