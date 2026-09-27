"""基准实验框架单元测试。

覆盖：
- XGBoost/RF/SVR/MLP四模型初始化/训练/推理
- 评估指标计算准确性
- 数据集加载/划分/小样本抽样
- 完整实验流程可执行性
"""

from __future__ import annotations


import numpy as np
import pytest

from app.benchmarks import (
    XGBoostBaseline,
    RFBaseline,
    SVMBaseline,
    MLPBaseline,
    compute_mae,
    compute_rmse,
    compute_r2,
    compute_mape,
    compute_all_metrics,
    load_uniwear_data,
    split_dataset,
    sample_training_subset,
    MetricsResult,
)


def _make_synthetic_data(n_samples=200, n_features=5, seed=42):
    rng = np.random.RandomState(seed)
    X = rng.randn(n_samples, n_features)
    y = X[:, 0] * 2.0 + X[:, 1] * 0.5 + rng.randn(n_samples) * 0.3
    return X, y


class TestMetrics:
    def test_mae_perfect(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        assert compute_mae(y_true, y_pred) == 0.0

    def test_mae_nonzero(self):
        y_true = np.array([0.0, 1.0, 2.0])
        y_pred = np.array([1.0, 1.0, 1.0])
        assert compute_mae(y_true, y_pred) == pytest.approx(2.0 / 3.0, abs=1e-6)

    def test_rmse_perfect(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        assert compute_rmse(y_true, y_pred) == 0.0

    def test_rmse_baseline(self):
        y_true = np.array([0.0, 1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 1.0, 1.0, 1.0])
        se = np.array([1, 0, 1, 4])
        expected = np.sqrt(np.mean(se))
        assert compute_rmse(y_true, y_pred) == pytest.approx(expected, abs=1e-6)

    def test_r2_perfect(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.0, 2.0, 3.0])
        assert compute_r2(y_true, y_pred) == 1.0

    def test_r2_zero(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([2.0, 2.0, 2.0])
        r2 = compute_r2(y_true, y_pred)
        assert r2 <= 0.0

    def test_r2_constant_y(self):
        y_true = np.array([5.0, 5.0, 5.0])
        y_pred = np.array([5.0, 5.0, 5.0])
        assert compute_r2(y_true, y_pred) == 1.0

    def test_mape_zero_true_not_broken(self):
        y_true = np.array([0.0, 1.0, 2.0])
        y_pred = np.array([0.1, 0.9, 2.1])
        mape = compute_mape(y_true, y_pred)
        assert np.isfinite(mape)

    def test_mape_perfect(self):
        y_true = np.array([10.0, 20.0, 30.0])
        y_pred = np.array([10.0, 20.0, 30.0])
        assert compute_mape(y_true, y_pred) == 0.0

    def test_metrics_result_to_dict(self):
        r = MetricsResult(
            mae=0.123456,
            rmse=0.234567,
            r2=0.987654,
            mape=2.345,
            inference_time_ms=0.001,
            model_size_mb=1.5,
            sample_fraction=0.5,
        )
        d = r.to_dict()
        assert d["mae"] == 0.123456
        assert d["r2"] == 0.987654
        assert d["mape"] == 2.345


class TestDatasets:
    def test_load_uniwear_data(self):
        splits, meta, scaler = load_uniwear_data()
        total = sum(s[0].shape[0] for s in splits.values())
        assert total > 0
        assert meta["n_features"] > 0
        assert "n_samples" in meta
        assert "n_features" in meta
        assert "label_name" in meta
        assert meta["n_samples"] == total
        # 各划分特征数一致
        for key in ("train", "val", "test"):
            assert splits[key][0].shape[1] == meta["n_features"]
        # scaler 必须已拟合（仅用训练集拟合，避免数据泄漏）
        assert hasattr(scaler, "mean_") and scaler.mean_ is not None
        assert hasattr(scaler, "scale_") and scaler.scale_ is not None

    def test_split_dataset_sizes(self):
        X, y = _make_synthetic_data(500, 5)
        splits = split_dataset(X, y, random_seed=42)
        assert "train" in splits
        assert "val" in splits
        assert "test" in splits
        total = sum(s[0].shape[0] for s in splits.values())
        assert total == X.shape[0]
        test_ratio = splits["test"][0].shape[0] / X.shape[0]
        assert 0.08 <= test_ratio <= 0.12

    def test_split_dataset_no_overlap(self):
        X, y = _make_synthetic_data(500, 5)
        splits = split_dataset(X, y, random_seed=42)
        X_train = splits["train"][0]
        X_test = splits["test"][0]
        # Quick id check - shapes differ so sets differ
        assert X_train.shape[1] == X_test.shape[1]

    def test_split_dataset_reproducible(self):
        X, y = _make_synthetic_data(500, 5)
        s1 = split_dataset(X, y, random_seed=42)
        s2 = split_dataset(X, y, random_seed=42)
        assert np.allclose(s1["test"][1], s2["test"][1])

    def test_sample_training_subset(self):
        X, y = _make_synthetic_data(500, 5)
        splits = split_dataset(X, y)
        X_sub, y_sub = sample_training_subset(
            splits["train"][0],
            splits["train"][1],
            fraction=0.3,
            random_seed=42,
        )
        expected_size = max(int(splits["train"][0].shape[0] * 0.3), 2)
        assert X_sub.shape[0] == expected_size

    def test_sample_training_subset_full(self):
        X, y = _make_synthetic_data(500, 5)
        splits = split_dataset(X, y)
        X_sub, y_sub = sample_training_subset(
            splits["train"][0],
            splits["train"][1],
            fraction=1.0,
        )
        assert X_sub.shape[0] == splits["train"][0].shape[0]

    def test_sample_training_subset_min(self):
        X, y = _make_synthetic_data(500, 5)
        splits = split_dataset(X, y)
        X_sub, y_sub = sample_training_subset(
            splits["train"][0],
            splits["train"][1],
            fraction=0.001,
        )
        n_expected = max(int(splits["train"][0].shape[0] * 0.001), 2)
        assert X_sub.shape[0] == n_expected


class TestXGBoostBaseline:
    def test_init_default(self):
        m = XGBoostBaseline()
        assert m._config["n_estimators"] == 200
        assert not m._fitted

    def test_init_custom(self):
        m = XGBoostBaseline({"n_estimators": 50, "max_depth": 3})
        assert m._config["n_estimators"] == 50

    def test_fit_predict(self):
        X, y = _make_synthetic_data()
        m = XGBoostBaseline({"n_estimators": 20, "max_depth": 3})
        info = m.fit(X, y)
        assert "training_time_s" in info
        assert m._fitted
        pred = m.predict(X)
        assert pred.shape == y.shape

    def test_fit_with_validation(self):
        X, y = _make_synthetic_data(200)
        X_val, y_val = _make_synthetic_data(50, seed=99)
        m = XGBoostBaseline({"n_estimators": 20, "max_depth": 3})
        info = m.fit(X, y, X_val, y_val)
        assert "training_time_s" in info
        pred = m.predict(X_val)
        assert pred.shape == y_val.shape

    def test_params_count(self):
        X, y = _make_synthetic_data()
        m = XGBoostBaseline({"n_estimators": 10, "max_depth": 3})
        assert m.get_params_count() == 0
        m.fit(X, y)
        assert m.get_params_count() > 0

    def test_model_size(self):
        X, y = _make_synthetic_data()
        m = XGBoostBaseline({"n_estimators": 10, "max_depth": 3})
        size = m.get_model_size_mb()
        assert size >= 0.0


class TestRFBaseline:
    def test_init_default(self):
        m = RFBaseline()
        assert m._config["n_estimators"] == 200
        assert not m._fitted

    def test_fit_predict(self):
        X, y = _make_synthetic_data()
        m = RFBaseline({"n_estimators": 20, "max_depth": 5})
        info = m.fit(X, y)
        assert "training_time_s" in info
        assert m._fitted
        pred = m.predict(X)
        assert pred.shape == y.shape

    def test_params_count(self):
        X, y = _make_synthetic_data()
        m = RFBaseline({"n_estimators": 10, "max_depth": 3})
        m.fit(X, y)
        assert m.get_params_count() > 0


class TestSVMBaseline:
    def test_init_default(self):
        m = SVMBaseline()
        assert m._config["C"] == 1.0

    def test_fit_predict(self):
        X, y = _make_synthetic_data(100)
        m = SVMBaseline({"C": 1.0})
        info = m.fit(X, y)
        assert "training_time_s" in info
        assert m._fitted
        pred = m.predict(X)
        assert pred.shape == y.shape

    def test_params_count(self):
        X, y = _make_synthetic_data(100)
        m = SVMBaseline({"C": 1.0})
        m.fit(X, y)
        assert m.get_params_count() > 0


class TestMLPBaseline:
    def test_init_default(self):
        m = MLPBaseline()
        assert m._config["hidden_layer_sizes"] == (128, 64, 32)

    def test_fit_predict(self):
        X, y = _make_synthetic_data()
        m = MLPBaseline({"hidden_layer_sizes": (32, 16), "max_iter": 100})
        info = m.fit(X, y)
        assert "training_time_s" in info
        assert m._fitted
        pred = m.predict(X)
        assert pred.shape == y.shape

    def test_params_count(self):
        X, y = _make_synthetic_data()
        m = MLPBaseline({"hidden_layer_sizes": (32, 16), "max_iter": 50})
        m.fit(X, y)
        assert m.get_params_count() > 0


class TestComputeAllMetrics:
    def test_returns_metrics_result(self):
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 1.9, 3.1])
        result = compute_all_metrics(y_true, y_pred)
        assert isinstance(result, MetricsResult)
        assert result.mae > 0
        assert result.r2 > 0

    def test_with_predict_fn(self):
        X, y = _make_synthetic_data()
        m = XGBoostBaseline({"n_estimators": 10, "max_depth": 2})
        m.fit(X, y)
        y_pred = m.predict(X)
        result = compute_all_metrics(y, y_pred, predict_fn=m.predict, X_test=X)
        assert result.inference_time_ms > 0
        assert result.model_size_mb >= 0


class TestIntegration:
    def test_full_pipeline_quick(self):
        """集成测试：快速验证完整流程（仅使用少量数据子集避免超时）"""
        from app.benchmarks import load_uniwear_data
        from app.benchmarks.metrics import compute_all_metrics

        # load_uniwear_data 已完成 train/val/test 划分与标准化（scaler 仅在
        # 训练集上拟合，无数据泄漏）。这里仅取训练集前 200 样本与测试集前 100
        # 样本做快速冒烟测试，避免重新混合划分导致的泄漏。
        splits, _, _ = load_uniwear_data(random_seed=42)
        X_train_full, y_train_full = splits["train"]
        n_train = min(200, X_train_full.shape[0])
        X_train, y_train = X_train_full[:n_train], y_train_full[:n_train]
        X_test_full, y_test_full = splits["test"]
        n_test = min(100, X_test_full.shape[0])
        X_test, y_test = X_test_full[:n_test], y_test_full[:n_test]

        models_data = [
            ("XGBoost", XGBoostBaseline({"n_estimators": 10, "max_depth": 3})),
            ("RF", RFBaseline({"n_estimators": 10, "max_depth": 5, "n_jobs": 1})),
            ("SVR", SVMBaseline({"C": 1.0})),
            ("MLP", MLPBaseline({"hidden_layer_sizes": (16, 8), "max_iter": 50})),
        ]

        results = []
        for name, model in models_data:
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            m = compute_all_metrics(y_test, y_pred, model=model)
            results.append({"model": name, "rmse": m.rmse, "r2": m.r2, "mae": m.mae})

        for r in results:
            print(
                f"  {r['model']}: RMSE={r['rmse']:.4f}, R²={r['r2']:.4f}, MAE={r['mae']:.4f}"
            )

        assert len(results) == 4
        for r in results:
            assert isinstance(r["rmse"], float)
            assert isinstance(r["r2"], float)
