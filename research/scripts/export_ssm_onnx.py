"""TorchMambaLNN → ONNX 导出 + onnxruntime 验证（升级④：接工程侧）。

用法（research/ 目录下）：
    python scripts/export_ssm_onnx.py [--checkpoint output/ssm_smoke/torch_mamba_lnn.pt] [--out output/ssm_smoke/torch_mamba_lnn.onnx]

导出的是 BaseLNN 单步接口 ``forward(x, dt, h) -> (y, h_new)``，
与工程侧 onnxruntime 推理（SsmOnnxPredictor）直接对接；状态在调用方维护。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import torch

_RESEARCH_ROOT = Path(__file__).resolve().parents[2]
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from models.torch_base_lnn import LNNConfig  # noqa: E402
from models.torch_mamba_lnn import TorchMambaLNN  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ssm-onnx")


def load_model_from_checkpoint(checkpoint_path: str) -> TorchMambaLNN:
    ckpt = torch.load(checkpoint_path, map_location="cpu")
    cfg_dict = ckpt["config"]
    config = LNNConfig(
        input_size=int(cfg_dict["input_size"]),
        hidden_size=int(cfg_dict["hidden_size"]),
        output_size=int(cfg_dict["output_size"]),
        num_layers=int(cfg_dict.get("num_layers", 1)),
        dropout=float(cfg_dict.get("dropout", 0.0)),
        time_constant=float(cfg_dict.get("time_constant", 1.0)),
    )
    model = TorchMambaLNN(config, selective=bool(cfg_dict.get("selective", True)))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model


class _SsmStepWrapper(torch.nn.Module):
    """ONNX 导出包装器：把 dt 作为内部常量烘焙进图。

    torch.onnx.export 要求首参为 nn.Module（torch 2.7 对纯函数导出会
    在内部调用 .modules()）；dt 作为内部常量 → 图输入仅 x/h。
    """

    def __init__(self, model: TorchMambaLNN, dt: float) -> None:
        super().__init__()
        self._model = model
        self._dt = dt

    def forward(self, x: torch.Tensor, h: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self._model(x, torch.tensor(self._dt), h)


def export_ssm_onnx(
    model: TorchMambaLNN,
    out_path: str,
    dt: float = 0.01,
) -> None:
    """导出单步模型 forward(x, dt, h) → (y, h_new)，dt 烘焙为图内常量。

    注意：torch.onnx.export 会把标量 dt 常量折叠进图（selective 模式本就不使用
    dt；LTI 模式 float(dt) 也使其成为常量），故 ONNX 图输入仅 ``x``/``h``，
    时间步长以 ``dt`` 参数固定于导出时，运行时由 ``SsmOnnxPredictor`` 维护。

    Args:
        model: TorchMambaLNN（eval 态）。
        out_path: 输出 .onnx 路径。
        dt: 烘焙进图的基线时间步长。
    """
    config = model.config
    batch = 1
    x = torch.randn(batch, config.input_size)
    h = torch.zeros(config.num_layers, batch, config.hidden_size)

    wrapper = _SsmStepWrapper(model, dt)
    torch.onnx.export(
        wrapper,
        (x, h),
        out_path,
        input_names=["x", "h"],
        output_names=["y", "h_new"],
        dynamic_axes={
            "x": {0: "batch"},
            "h": {1: "batch"},
            "y": {0: "batch"},
            "h_new": {1: "batch"},
        },
        opset_version=17,
    )
    logger.info("ONNX 已导出（dt=%.3f 烘焙进图）: %s", dt, out_path)


def verify_with_onnxruntime(onnx_path: str, model: TorchMambaLNN, dt: float = 0.01, rtol: float = 1e-3, atol: float = 1e-3) -> None:
    """onnxruntime 推理与 torch 前向逐批对比（数值一致性验证）。"""
    import numpy as np
    import onnxruntime as ort

    config = model.config
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])

    batch = 4
    x = torch.randn(batch, config.input_size)
    h = torch.zeros(config.num_layers, batch, config.hidden_size)

    with torch.no_grad():
        y_torch, h_torch = model(x, dt, h)

    y_ort, h_ort = sess.run(None, {
        "x": x.numpy().astype(np.float32),
        "h": h.numpy().astype(np.float32),
    })
    np.testing.assert_allclose(y_ort, y_torch.numpy(), rtol=rtol, atol=atol)
    np.testing.assert_allclose(h_ort, h_torch.numpy(), rtol=rtol, atol=atol)
    logger.info("onnxruntime 与 torch 前向一致（batch=%d, rtol=%.0e）", batch, rtol)


def main() -> None:
    parser = argparse.ArgumentParser(description="TorchMambaLNN ONNX 导出")
    parser.add_argument("--checkpoint", default="output/ssm_smoke/torch_mamba_lnn.pt")
    parser.add_argument("--out", default="output/ssm_smoke/torch_mamba_lnn.onnx")
    parser.add_argument("--dt", type=float, default=0.01)
    args = parser.parse_args()

    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.exists():
        logger.error("checkpoint 不存在: %s（请先运行 train_ssm_smoke.py）", ckpt_path)
        raise SystemExit(1)

    model = load_model_from_checkpoint(str(ckpt_path))
    logger.info("加载模型: %s | 参数量 %d", model.model_name, model.get_info()["total_parameters"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    export_ssm_onnx(model, str(out), dt=args.dt)
    verify_with_onnxruntime(str(out), model, dt=args.dt)


if __name__ == "__main__":
    main()
