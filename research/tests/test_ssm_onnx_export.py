"""TorchMambaLNN ONNX 导出 + onnxruntime 一致性 单元测试（升级④）。

运行（research/ 目录下）：pytest tests/test_ssm_onnx_export.py -v
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from models.torch_base_lnn import LNNConfig
from models.torch_mamba_lnn import TorchMambaLNN
from scripts.export_ssm_onnx import export_ssm_onnx, load_model_from_checkpoint, verify_with_onnxruntime


class TestOnnxExport:
    def test_export_and_verify(self, tmp_path) -> None:
        torch.manual_seed(0)
        model = TorchMambaLNN(
            LNNConfig(input_size=4, hidden_size=16, output_size=2, num_layers=2, dropout=0.0, time_constant=0.01)
        ).eval()
        out = tmp_path / "ssm.onnx"
        export_ssm_onnx(model, str(out), dt=0.01)
        assert out.exists() and out.stat().st_size > 0
        # onnxruntime 与 torch 前向逐批一致
        verify_with_onnxruntime(str(out), model, dt=0.01, rtol=1e-3, atol=1e-3)

    def test_export_lti_variant(self, tmp_path) -> None:
        torch.manual_seed(1)
        model = TorchMambaLNN(
            LNNConfig(input_size=4, hidden_size=8, output_size=1, num_layers=1, dropout=0.0, time_constant=0.01),
            selective=False,
        ).eval()
        out = tmp_path / "ssm_lti.onnx"
        export_ssm_onnx(model, str(out), dt=0.01)
        verify_with_onnxruntime(str(out), model, dt=0.01)

    def test_onnx_sequence_matches_torch(self, tmp_path) -> None:
        """多步序列：onnx 单步循环 == torch 单步循环。"""
        import onnxruntime as ort

        torch.manual_seed(2)
        model = TorchMambaLNN(
            LNNConfig(input_size=4, hidden_size=16, output_size=2, num_layers=2, dropout=0.0, time_constant=0.01)
        ).eval()
        out = tmp_path / "seq.onnx"
        export_ssm_onnx(model, str(out), dt=0.01)
        sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])

        batch, time = 3, 10
        x_seq = torch.randn(batch, time, 4)
        dt = 0.01

        with torch.no_grad():
            y_t, h_t = model.forward_sequence(x_seq, dt)

        h = np.zeros((2, batch, 16), dtype=np.float32)
        ys = []
        for t in range(time):
            y, h = sess.run(None, {"x": x_seq[:, t].numpy().astype(np.float32), "h": h})
            ys.append(y)
        y_onnx = np.stack(ys, axis=1)
        np.testing.assert_allclose(y_onnx, y_t.numpy(), rtol=1e-3, atol=1e-3)
        np.testing.assert_allclose(h, h_t.numpy(), rtol=1e-3, atol=1e-3)


class TestCheckpointLoad:
    def test_load_saved_checkpoint(self) -> None:
        """从训练产出的 checkpoint 重建模型（存在性保护测试）。"""
        from pathlib import Path

        ckpt = Path("output/ssm_smoke/torch_mamba_lnn.pt")
        if not ckpt.exists():
            pytest.skip("训练 checkpoint 不存在（未运行 train_ssm_smoke）")
        model = load_model_from_checkpoint(str(ckpt))
        assert model.model_name == "TorchMambaLNN"
        x = torch.randn(2, model.config.input_size)
        y, h = model(x, 0.01, model.init_hidden(2))
        assert y.shape == (2, model.config.output_size)
