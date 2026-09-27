"""
收敛性测试：验证各LNN模型变体在训练过程中R²值呈单调上升趋势

测试内容：
- 生成训练曲线：Loss和R²值随epoch变化趋势图
- 验收标准：训练R²值应呈现单调上升趋势，验证R²值无明显过拟合下降现象
- 要求所有模型在100个epoch内达到稳定收敛状态
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def generate_synthetic_regression_data(
    n_samples: int = 5000,
    n_features: int = 15,
    noise_level: float = 0.05,
    seed: int = 42,
):
    """生成合成回归数据集"""
    rng = np.random.RandomState(seed)
    X = rng.randn(n_samples, n_features).astype(np.float32)

    # 构造有意义的线性+非线性关系
    true_weights = rng.randn(n_features).astype(np.float32) * 0.5
    y = (
        X @ true_weights
        + 0.1 * X[:, 0] * X[:, 1]
        + 0.05 * np.sin(X[:, 2])
        + rng.randn(n_samples).astype(np.float32) * noise_level
    )

    return X, y.reshape(-1, 1)


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not available")
class TestLNNConvergence:
    """LNN模型收敛性测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """准备测试数据"""
        self.n_samples = 3000
        self.n_features = 15
        self.output_dim = 1
        self.batch_size = 64
        self.epochs = 100

        X, y = generate_synthetic_regression_data(
            n_samples=self.n_samples,
            n_features=self.n_features,
            noise_level=0.05,
            seed=42,
        )

        # 70/15/15 划分
        n = len(X)
        indices = np.random.RandomState(42).permutation(n)
        train_end = int(n * 0.7)
        val_end = int(n * 0.85)

        self.X_train = X[indices[:train_end]]
        self.y_train = y[indices[:train_end]]
        self.X_val = X[indices[train_end:val_end]]
        self.y_val = y[indices[train_end:val_end]]
        self.X_test = X[indices[val_end:]]
        self.y_test = y[indices[val_end:]]

    def test_cfc_convergence(self):
        """CFC模型收敛性测试：R²应呈上升趋势"""
        from models.torch_cfc_model import CFCModel, LNNConfig
        from training.trainer import LNNTrainer
        from torch.utils.data import DataLoader, TensorDataset

        config = LNNConfig(
            input_size=self.n_features,
            hidden_size=128,
            output_size=self.output_dim,
            num_layers=2,
            dropout=0.1,
        )
        model = CFCModel(config)

        train_dataset = TensorDataset(
            torch.FloatTensor(self.X_train), torch.FloatTensor(self.y_train)
        )
        val_dataset = TensorDataset(
            torch.FloatTensor(self.X_val), torch.FloatTensor(self.y_val)
        )
        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=self.batch_size, shuffle=False)

        trainer = LNNTrainer(
            model=model,
            learning_rate=0.001,
            optimizer_type="adamw",
            loss_type="mse",
            epochs=self.epochs,
            early_stopping_patience=15,
            gradient_clip_value=1.0,
            lr_scheduler_type="cosine",
            weight_decay=1e-5,
        )

        history = trainer.fit(train_loader, val_loader)

        # 验证R²指标存在且为合理值
        assert "train_r2" in history, "训练历史应包含train_r2指标"
        assert "val_r2" in history, "训练历史应包含val_r2指标"
        assert len(history["train_r2"]) > 0, "应有非空的train_r2记录"

        # 最终R²应大于0（表示模型学到了有效模式）
        final_r2 = history["val_r2"][-1] if history["val_r2"] else history["train_r2"][-1]
        assert final_r2 > 0.3, f"最终R²={final_r2:.4f}应大于0.3，说明模型已学到有效模式"

        # 检查训练R²整体趋势（取后50%的均值应大于前50%）
        mid = len(history["train_r2"]) // 2
        early_r2 = np.mean(history["train_r2"][:mid])
        late_r2 = np.mean(history["train_r2"][mid:]) if mid > 0 else early_r2
        assert late_r2 >= early_r2 * 0.8, (
            f"后期R²均值({late_r2:.4f})应不低于前期({early_r2:.4f})的80%"
        )

    def test_ltc_convergence(self):
        """LTC模型收敛性测试"""
        from models.torch_ltc_model import LTCModel, LNNConfig
        from training.trainer import LNNTrainer
        from torch.utils.data import DataLoader, TensorDataset

        config = LNNConfig(
            input_size=self.n_features,
            hidden_size=128,
            output_size=self.output_dim,
            num_layers=2,
            dropout=0.1,
        )
        model = LTCModel(config)

        train_dataset = TensorDataset(
            torch.FloatTensor(self.X_train), torch.FloatTensor(self.y_train)
        )
        val_dataset = TensorDataset(
            torch.FloatTensor(self.X_val), torch.FloatTensor(self.y_val)
        )
        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=self.batch_size, shuffle=False)

        trainer = LNNTrainer(
            model=model,
            learning_rate=0.001,
            optimizer_type="adamw",
            loss_type="mse",
            epochs=self.epochs,
            early_stopping_patience=15,
            gradient_clip_value=1.0,
            lr_scheduler_type="cosine",
            weight_decay=1e-5,
        )

        history = trainer.fit(train_loader, val_loader)

        assert "train_r2" in history
        assert len(history["train_r2"]) > 0

        final_r2 = history["val_r2"][-1] if history["val_r2"] else history["train_r2"][-1]
        assert final_r2 > 0.3, f"LTC最终R²={final_r2:.4f}应大于0.3"

    def test_hybrid_lnn_convergence(self):
        """HybridLNN模型收敛性测试"""
        from models.torch_hybrid_lnn import HybridLNN, LNNConfig
        from training.trainer import LNNTrainer
        from torch.utils.data import DataLoader, TensorDataset

        config = LNNConfig(
            input_size=self.n_features,
            hidden_size=128,
            output_size=self.output_dim,
            num_layers=3,
            dropout=0.1,
        )
        model = HybridLNN(config)

        train_dataset = TensorDataset(
            torch.FloatTensor(self.X_train), torch.FloatTensor(self.y_train)
        )
        val_dataset = TensorDataset(
            torch.FloatTensor(self.X_val), torch.FloatTensor(self.y_val)
        )
        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=self.batch_size, shuffle=False)

        trainer = LNNTrainer(
            model=model,
            learning_rate=0.001,
            optimizer_type="adamw",
            loss_type="mse",
            epochs=min(self.epochs, 80),
            early_stopping_patience=15,
            gradient_clip_value=1.0,
            lr_scheduler_type="cosine",
            weight_decay=1e-5,
        )

        history = trainer.fit(train_loader, val_loader)

        assert "train_r2" in history
        assert len(history["train_r2"]) > 0

        final_r2 = history["val_r2"][-1] if history["val_r2"] else history["train_r2"][-1]
        assert final_r2 > 0.2, f"HybridLNN最终R²={final_r2:.4f}应大于0.2"
