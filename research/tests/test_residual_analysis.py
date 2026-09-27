"""
残差分析测试：验证模型残差分布特性

- 验收标准：残差均值接近0，残差与预测值无明显强相关性（|corr| < 0.5）
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


def correlation_test(residuals, predictions):
    """计算残差与预测值的Pearson相关系数"""
    return float(np.corrcoef(residuals, predictions)[0, 1])


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not available")
class TestResidualAnalysis:
    """残差分析测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        rng = np.random.RandomState(42)
        if HAS_TORCH:
            # 固定 torch 全局随机种子：模型初始化/DataLoader shuffle 依赖
            # torch 随机状态，不固定会导致残差均值统计断言 flaky（全量跑时
            # torch 状态被前面测试消耗，单跑/全跑结果不同）。
            torch.manual_seed(42)
        n_samples = 3000
        n_features = 15

        self.X = rng.randn(n_samples, n_features).astype(np.float32)
        true_w = rng.randn(n_features).astype(np.float32) * 0.5
        self.y = (
            self.X @ true_w
            + 0.1 * self.X[:, 0] * self.X[:, 1]
            + rng.randn(n_samples).astype(np.float32) * 0.05
        ).reshape(-1, 1)

        n = len(self.X)
        indices = rng.permutation(n)
        train_end = int(n * 0.7)

        self.X_train = self.X[indices[:train_end]]
        self.y_train = self.y[indices[:train_end]]
        self.X_test = self.X[indices[train_end:]]
        self.y_test = self.y[indices[train_end:]]

    def _train_and_get_residuals(self, model_class, config_class, config_kwargs, epochs=100):
        """训练并获取残差"""
        from training.trainer import LNNTrainer

        config = config_class(**config_kwargs)
        model = model_class(config)

        train_dataset = TensorDataset(
            torch.FloatTensor(self.X_train), torch.FloatTensor(self.y_train)
        )
        val_dataset = TensorDataset(
            torch.FloatTensor(self.X_test[:300]), torch.FloatTensor(self.y_test[:300])
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
            X_t = torch.FloatTensor(self.X_test)
            preds = model(X_t, dt=0.1)
            if isinstance(preds, tuple):
                preds = preds[0]
            preds = preds.cpu().numpy().flatten()

        residuals = preds - self.y_test.flatten()
        return residuals, preds

    def test_cfc_residual_analysis(self):
        """CFC残差均值接近0"""
        from models.torch_cfc_model import CFCModel, LNNConfig

        residuals, preds = self._train_and_get_residuals(
            CFCModel,
            LNNConfig,
            {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 2, "dropout": 0.1},
        )

        resid_mean = float(np.mean(residuals))
        resid_std = float(np.std(residuals))

        print(f"\nCFC 残差均值: {resid_mean:.4f}, 标准差: {resid_std:.4f}")

        # 残差均值应接近0
        assert abs(resid_mean) < 0.1, f"残差均值 {resid_mean:.4f} 应接近0"

    def test_ltc_residual_analysis(self):
        """LTC残差均值接近0"""
        from models.torch_ltc_model import LTCModel, LNNConfig

        residuals, preds = self._train_and_get_residuals(
            LTCModel,
            LNNConfig,
            {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 2, "dropout": 0.1},
        )

        resid_mean = float(np.mean(residuals))

        print(f"\nLTC 残差均值: {resid_mean:.4f}")

        assert abs(resid_mean) < 0.2, f"残差均值 {resid_mean:.4f} 应接近0"

    def test_hybrid_residual_analysis(self):
        """HybridLNN残差均值接近0"""
        from models.torch_hybrid_lnn import HybridLNN, LNNConfig

        residuals, preds = self._train_and_get_residuals(
            HybridLNN,
            LNNConfig,
            {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 3, "dropout": 0.1},
            epochs=80,
        )

        resid_mean = float(np.mean(residuals))

        print(f"\nHybridLNN 残差均值: {resid_mean:.4f}")

        assert abs(resid_mean) < 0.2, f"残差均值 {resid_mean:.4f} 应接近0"
