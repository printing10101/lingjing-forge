"""
预测分布测试：生成预测值与真实值散点图

验收标准：数据点应紧密分布在 y=x 对角线附近，相关系数 > 0.85
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


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not available")
class TestPredictionDistribution:
    """预测分布测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """准备数据"""
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

        n = len(self.X)
        indices = rng.permutation(n)
        train_end = int(n * 0.7)

        self.X_train = self.X[indices[:train_end]]
        self.y_train = self.y[indices[:train_end]]
        self.X_test = self.X[indices[train_end:]]
        self.y_test = self.y[indices[train_end:]]

    def _train_and_predict(self, model_class, config_class, config_kwargs, epochs=100):
        """训练模型并返回预测结果"""
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
            return preds.cpu().numpy().flatten(), self.y_test.flatten()

    def test_cfc_prediction_correlation(self):
        """CFC模型预测-真实值相关性 > 0.85"""
        from models.torch_cfc_model import CFCModel, LNNConfig

        preds, y_true = self._train_and_predict(
            CFCModel,
            LNNConfig,
            {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 2, "dropout": 0.1},
        )

        corr = np.corrcoef(preds, y_true)[0, 1]
        print(f"\nCFC 预测-真实值相关系数: {corr:.4f}")
        assert corr > 0.85, f"相关系数 {corr:.4f} 应 > 0.85"

    def test_ltc_prediction_correlation(self):
        """LTC模型预测-真实值相关性 > 0.85"""
        from models.torch_ltc_model import LTCModel, LNNConfig

        preds, y_true = self._train_and_predict(
            LTCModel,
            LNNConfig,
            {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 2, "dropout": 0.1},
        )

        corr = np.corrcoef(preds, y_true)[0, 1]
        print(f"\nLTC 预测-真实值相关系数: {corr:.4f}")
        assert corr > 0.85, f"相关系数 {corr:.4f} 应 > 0.85"

    def test_hybrid_prediction_correlation(self):
        """HybridLNN模型预测-真实值相关性 > 0.85"""
        from models.torch_hybrid_lnn import HybridLNN, LNNConfig

        preds, y_true = self._train_and_predict(
            HybridLNN,
            LNNConfig,
            {"input_size": 15, "hidden_size": 128, "output_size": 1, "num_layers": 3, "dropout": 0.1},
            epochs=80,
        )

        corr = np.corrcoef(preds, y_true)[0, 1]
        print(f"\nHybridLNN 预测-真实值相关系数: {corr:.4f}")
        assert corr > 0.85, f"相关系数 {corr:.4f} 应 > 0.85"
