"""TorchMambaLNN + 合成颤振数据 单元测试（Phase 3a：④ SSM 预测 backbone）。

运行（research/ 目录下）：pytest tests/test_torch_mamba_lnn.py -v
"""

from __future__ import annotations

import pytest
import torch

from datasets.synthetic_chatter import generate_chatter_dataset
from models.torch_base_lnn import LNNConfig
from models.torch_mamba_lnn import SSMCell, TorchMambaLNN


def _config(hidden: int = 16, layers: int = 2) -> LNNConfig:
    return LNNConfig(
        input_size=4,
        hidden_size=hidden,
        output_size=2,
        num_layers=layers,
        dropout=0.1,
        time_constant=0.01,
    )


class TestSyntheticData:
    def test_shapes_and_label_range(self) -> None:
        X, yi, yc, meta = generate_chatter_dataset(n_samples=64, seq_len=100, seed=7)
        assert X.shape == (64, 100, 4)
        assert yi.shape == (64, 1)
        assert yc.shape == (64, 1)
        assert float(yi.min()) >= 0.0 and float(yi.max()) <= 1.0
        assert set(torch.unique(yc).tolist()) <= {0.0, 1.0}
        assert 0.0 < meta.meta["chatter_ratio"] < 1.0

    def test_reproducible_with_seed(self) -> None:
        X1, _, _, _ = generate_chatter_dataset(n_samples=32, seq_len=50, seed=42)
        X2, _, _, _ = generate_chatter_dataset(n_samples=32, seq_len=50, seed=42)
        assert torch.equal(X1, X2)


class TestTorchMambaLNN:
    def test_forward_shapes(self) -> None:
        m = TorchMambaLNN(_config())
        X, _, _, _ = generate_chatter_dataset(n_samples=8, seq_len=50, seed=0)
        out, h = m.forward_sequence(X, 0.01)
        assert out.shape == (8, 50, 2)
        assert h.shape == (2, 8, 16)

    def test_step_interface(self) -> None:
        m = TorchMambaLNN(_config())
        X, _, _, _ = generate_chatter_dataset(n_samples=8, seq_len=50, seed=0)
        h0 = m.init_hidden(8)
        y, h1 = m(X[:, 0], 0.01, h0)
        assert y.shape == (8, 2)
        assert h1.shape == (2, 8, 16)

    def test_gradient_flow(self) -> None:
        m = TorchMambaLNN(_config())
        X, yi, yc, _ = generate_chatter_dataset(n_samples=8, seq_len=50, seed=0)
        out, _ = m.forward_sequence(X, 0.01)
        loss = torch.nn.functional.mse_loss(out[:, -1, :1], yi) + torch.nn.functional.binary_cross_entropy_with_logits(
            out[:, -1, 1], yc.squeeze(-1)
        )
        loss.backward()
        grads = [p.grad for p in m.parameters() if p.grad is not None]
        assert len(grads) == sum(1 for p in m.parameters() if p.requires_grad)
        for g in grads:
            assert torch.isfinite(g).all()

    def test_selective_and_lti(self) -> None:
        X, _, _, _ = generate_chatter_dataset(n_samples=8, seq_len=50, seed=0)
        for selective in (True, False):
            m = TorchMambaLNN(_config(), selective=selective)
            out, _ = m.forward_sequence(X, 0.01)
            assert out.shape == (8, 50, 2)

    def test_ssm_cell_handles_batch(self) -> None:
        cell = SSMCell(4, 16, selective=True)
        x = torch.randn(8, 4)
        h = torch.zeros(8, 16)
        y, h_new = cell.step(x, h, 0.01)
        assert y.shape == (8, 16)
        assert h_new.shape == (8, 16)
