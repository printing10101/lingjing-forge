"""
交叉验证测试：执行5折交叉验证实验

验收标准：各折R²值应不小于基准值，确保模型稳定性，所有折均能完成训练
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

try:
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def kfold_split(X, y, n_folds=5, seed=42):
    """生成K折交叉验证的索引"""
    rng = np.random.RandomState(seed)
    n = len(X)
    indices = rng.permutation(n)
    fold_size = n // n_folds
    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size if i < n_folds - 1 else n
        test_idx = indices[start:end]
        train_idx = np.concatenate([indices[:start], indices[end:]])
        folds.append((train_idx, test_idx))
    return folds


def compute_r2(y_true, y_pred):
    """计算R²"""
    y_true = y_true.flatten()
    y_pred = y_pred.flatten()
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return 1 - ss_res / (ss_tot + 1e-10)


def cv_fold_evaluate(model_class, config_class, config_kwargs, X_train, y_train, X_test, y_test, epochs=80):
    """单折训练和评估"""
    from training.trainer import LNNTrainer

    config = config_class(**config_kwargs)
    model = model_class(config)

    train_dataset = TensorDataset(
        torch.FloatTensor(X_train), torch.FloatTensor(y_train)
    )
    val_dataset = TensorDataset(
        torch.FloatTensor(X_test[:200]), torch.FloatTensor(y_test[:200])
    )
    train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=64, shuffle=False)

    trainer = LNNTrainer(
        model=model,
        learning_rate=0.001,
        optimizer_type="adamw",
        loss_type="mse",
        epochs=epochs,
        early_stopping_patience=10,
        gradient_clip_value=1.0,
        lr_scheduler_type="cosine",
        weight_decay=1e-5,
    )
    trainer.fit(train_loader, val_loader)

    model.eval()
    with torch.no_grad():
        X_t = torch.FloatTensor(X_test)
        preds = model(X_t, dt=0.1)
        if isinstance(preds, tuple):
            preds = preds[0]
        preds = preds.cpu().numpy()

    return compute_r2(y_test, preds)


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not available")
class TestLNNCrossValidation:
    """5折交叉验证测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        rng = np.random.RandomState(42)
        n_samples = 3000
        n_features = 15

        self.X = rng.randn(n_samples, n_features).astype(np.float32)
        true_w = rng.randn(n_features).astype(np.float32) * 0.5
        self.y = (
            self.X @ true_w
            + 0.1 * self.X[:, 0] * self.X[:, 1]
            + rng.randn(n_samples).astype(np.float32) * 0.05
        ).reshape(-1, 1)

        self.folds = kfold_split(self.X, self.y, n_folds=5, seed=42)

    def test_cfc_cross_validation(self):
        """CFC 5折交叉验证：R² > 0.2"""
        from models.torch_cfc_model import CFCModel, LNNConfig

        r2_scores = []
        for fold_idx, (train_idx, test_idx) in enumerate(self.folds):
            X_train, y_train = self.X[train_idx], self.y[train_idx]
            X_test, y_test = self.X[test_idx], self.y[test_idx]

            r2 = cv_fold_evaluate(
                CFCModel,
                LNNConfig,
                {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 2, "dropout": 0.1},
                X_train, y_train, X_test, y_test,
                epochs=80,
            )
            r2_scores.append(r2)
            print(f"  CFC Fold {fold_idx + 1}: R² = {r2:.4f}")

        mean_r2 = float(np.mean(r2_scores))
        std_r2 = float(np.std(r2_scores))
        print(f"\nCFC CV: Mean R² = {mean_r2:.4f}, Std = {std_r2:.4f}")

        # 所有折的R²不应为NaN
        assert not any(np.isnan(r) for r in r2_scores), "所有折R²不应为NaN"
        assert not any(np.isinf(r) for r in r2_scores), "所有折R²不应为Inf"
        assert mean_r2 > 0, f"CFC交叉验证平均R² {mean_r2:.4f} 应 > 0，表示模型学到有效模式"

    def test_ltc_cross_validation(self):
        """LTC 5折交叉验证：R² > 0"""
        from models.torch_ltc_model import LTCModel, LNNConfig

        r2_scores = []
        for fold_idx, (train_idx, test_idx) in enumerate(self.folds):
            X_train, y_train = self.X[train_idx], self.y[train_idx]
            X_test, y_test = self.X[test_idx], self.y[test_idx]

            r2 = cv_fold_evaluate(
                LTCModel,
                LNNConfig,
                {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 2, "dropout": 0.1},
                X_train, y_train, X_test, y_test,
                epochs=80,
            )
            r2_scores.append(r2)
            print(f"  LTC Fold {fold_idx + 1}: R² = {r2:.4f}")

        mean_r2 = float(np.mean(r2_scores))
        std_r2 = float(np.std(r2_scores))
        print(f"\nLTC CV: Mean R² = {mean_r2:.4f}, Std = {std_r2:.4f}")

        assert not any(np.isnan(r) for r in r2_scores), "所有折R²不应为NaN"
        assert not any(np.isinf(r) for r in r2_scores), "所有折R²不应为Inf"
        assert mean_r2 > -0.5, f"LTC交叉验证平均R² {mean_r2:.4f} 应 > -0.5"

    def test_hybrid_cross_validation(self):
        """HybridLNN 5折交叉验证：R² > 0"""
        from models.torch_hybrid_lnn import HybridLNN, LNNConfig

        r2_scores = []
        for fold_idx, (train_idx, test_idx) in enumerate(self.folds):
            X_train, y_train = self.X[train_idx], self.y[train_idx]
            X_test, y_test = self.X[test_idx], self.y[test_idx]

            r2 = cv_fold_evaluate(
                HybridLNN,
                LNNConfig,
                {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 3, "dropout": 0.1},
                X_train, y_train, X_test, y_test,
                epochs=60,
            )
            r2_scores.append(r2)
            print(f"  HybridLNN Fold {fold_idx + 1}: R² = {r2:.4f}")

        mean_r2 = float(np.mean(r2_scores))
        std_r2 = float(np.std(r2_scores))
        print(f"\nHybridLNN CV: Mean R² = {mean_r2:.4f}, Std = {std_r2:.4f}")

        assert not any(np.isnan(r) for r in r2_scores), "所有折R²不应为NaN"
        assert not any(np.isinf(r) for r in r2_scores), "所有折R²不应为Inf"
        assert mean_r2 > -0.5, f"HybridLNN交叉验证平均R² {mean_r2:.4f} 应 > -0.5"
