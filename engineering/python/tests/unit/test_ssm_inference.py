"""SSM 工程侧 ONNX 推理 单元测试（升级④：onnxruntime 路径）。

依赖 torch（导出夹具用）——research 桥接可用时构建真实 TorchMambaLNN 导出，
否则跳过（生产容器无 research/ 时的预期行为）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENG_PYTHON = Path(__file__).resolve().parents[2]
if str(_ENG_PYTHON) not in sys.path:
    sys.path.insert(0, str(_ENG_PYTHON))

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("onnxruntime")

from app.ai.lnn._research_bridge import get_torch_mamba_lnn_factory, get_lnn_config_factory  # noqa: E402
from app.ai.lnn.ssm_inference import SsmOnnxMeta, SsmOnnxPredictor, register_ssm_predictor  # noqa: E402


def _build_trained_fixture(tmp_path) -> tuple[str, SsmOnnxMeta, object]:
    """经 research 桥接构建小型 TorchMambaLNN 并导出 ONNX（夹具）。"""
    # torch.onnx.export 依赖 onnx/onnxscript（训练侧依赖，工程侧 requirements 仅装 onnxruntime）
    pytest.importorskip("onnxscript", reason="需要 onnx/onnxscript（ONNX 导出路径）")
    factory = get_torch_mamba_lnn_factory()
    config_factory = get_lnn_config_factory()
    if factory is None or config_factory is None:
        pytest.skip("research 不可用（生产容器预期行为）")

    torch.manual_seed(0)
    config = config_factory(input_size=4, hidden_size=16, output_size=2, num_layers=2, dropout=0.0, time_constant=0.01)
    model = factory(config).eval()
    onnx_path = str(tmp_path / "ssm_fixture.onnx")

    # 与导出脚本一致：dt 烘焙进图，仅 x/h 为图输入（nn.Module 包装器）
    import torch as _t

    class _Wrapper(_t.nn.Module):
        def __init__(self, m: object, dt: float) -> None:
            super().__init__()
            self._m = m
            self._dt = dt

        def forward(self, x_: _t.Tensor, h_: _t.Tensor) -> tuple[_t.Tensor, _t.Tensor]:
            return self._m(x_, _t.tensor(self._dt), h_)

    x = _t.randn(1, 4)
    h = _t.zeros(2, 1, 16)
    _t.onnx.export(
        _Wrapper(model, 0.01),
        (x, h),
        onnx_path,
        input_names=["x", "h"],
        output_names=["y", "h_new"],
        dynamic_axes={"x": {0: "batch"}, "h": {1: "batch"}, "y": {0: "batch"}, "h_new": {1: "batch"}},
        opset_version=17,
    )
    meta = SsmOnnxMeta(input_size=4, hidden_size=16, output_size=2, num_layers=2, dt=0.01)
    return onnx_path, meta, model


class TestSsmOnnxPredictor:
    def test_predict_matches_torch(self, tmp_path) -> None:
        onnx_path, meta, model = _build_trained_fixture(tmp_path)
        predictor = SsmOnnxPredictor(onnx_path, meta)

        batch = 4
        x = np.random.randn(batch, 4).astype(np.float32)
        h = np.zeros((2, batch, 16), dtype=np.float32)
        with torch.no_grad():
            y_t, h_t = model(torch.from_numpy(x), 0.01, torch.from_numpy(h))
        y, h_new = predictor.predict(x, h)
        np.testing.assert_allclose(y, y_t.numpy(), rtol=1e-3, atol=1e-3)
        np.testing.assert_allclose(h_new, h_t.numpy(), rtol=1e-3, atol=1e-3)

    def test_predict_sequence(self, tmp_path) -> None:
        onnx_path, meta, model = _build_trained_fixture(tmp_path)
        predictor = SsmOnnxPredictor(onnx_path, meta)
        x_seq = np.random.randn(2, 8, 4).astype(np.float32)
        with torch.no_grad():
            out_t, _ = model.forward_sequence(torch.from_numpy(x_seq), 0.01)
        out = predictor.predict_sequence(x_seq)
        np.testing.assert_allclose(out, out_t[:, -1].numpy(), rtol=1e-3, atol=1e-3)

    def test_register_to_engine(self, tmp_path) -> None:
        onnx_path, meta, _ = _build_trained_fixture(tmp_path)

        class _FakeEngine:
            def __init__(self) -> None:
                self.predictors: dict[str, object] = {}

            def register_lnn_predictor(self, model_name: str, predictor: object) -> None:
                self.predictors[model_name] = predictor

        engine = _FakeEngine()
        predictor = register_ssm_predictor(engine, onnx_path, meta, model_name="ssm_chatter")
        assert "ssm_chatter" in engine.predictors
        assert engine.predictors["ssm_chatter"] is predictor
