"""TorchMambaLNN → ONNX 导出（Phase 3a 工程侧落地：④ SSM 进产品链路）。

导出**单步契约**（BaseLNN 风格）而非整条序列：
    step(x (B,F), h (L,B,H), dt) → (y (B,out), h_new (L,B,H))
好处：① 与 LNN 液态/流式推理语义一致（逐时间步扫描，携带状态）；
② ONNX 图简洁（无静态时间轴展开）；③ 工程侧 onnxruntime 循环 T 步即可。

用法（research/ 下）：
    python experiments/05_SSM预测backbone/export_ssm_onnx.py
产物：output/ssm_smoke/torch_mamba_lnn.onnx（含导出后 onnxruntime 一致性自检）
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import torch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("export-ssm-onnx")

_RESEARCH_ROOT = Path(__file__).resolve().parents[2]
if str(_RESEARCH_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_RESEARCH_ROOT))

from models.torch_base_lnn import LNNConfig  # noqa: E402
from models.torch_mamba_lnn import TorchMambaLNN  # noqa: E402


def load_model(checkpoint: Path, device: torch.device = torch.device("cpu")) -> TorchMambaLNN:
    ckpt = torch.load(checkpoint, map_location=device)
    cfg_dict = ckpt["config"]
    config = LNNConfig(
        input_size=int(cfg_dict["input_size"]),
        hidden_size=int(cfg_dict["hidden_size"]),
        output_size=int(cfg_dict["output_size"]),
        num_layers=int(cfg_dict["num_layers"]),
        dropout=float(cfg_dict.get("dropout", 0.0)),
        time_constant=float(cfg_dict.get("time_constant", 0.01)),
    )
    model = TorchMambaLNN(config)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model


class _StepWrapper(torch.nn.Module):
    """包装 model.step 为可导出的 nn.Module（torch.onnx 不支持绑定方法）。"""

    def __init__(self, model: TorchMambaLNN) -> None:
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor, h: torch.Tensor, dt: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self.model.step(x, h, float(dt))


def export(checkpoint: Path, out_path: Path, opset: int = 17) -> Path:
    model = load_model(checkpoint)
    n_layers = model.num_layers
    hidden = model.config.hidden_size
    in_size = model.config.input_size
    out_size = model.config.output_size

    wrapper = _StepWrapper(model).eval()
    x = torch.randn(1, in_size)
    h = torch.zeros(n_layers, 1, hidden)
    dt = torch.tensor(0.01)

    torch.onnx.export(
        wrapper,
        (x, h, dt),
        str(out_path),
        input_names=["x", "h", "dt"],
        output_names=["y", "h_new"],
        dynamic_axes={"x": {0: "batch"}, "h": {1: "batch"}, "y": {0: "batch"}, "h_new": {1: "batch"}},
        opset_version=opset,
    )
    logger.info("ONNX 已导出: %s", out_path)

    # 一致性自检：PyTorch vs onnxruntime
    import onnxruntime as ort

    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    with torch.no_grad():
        y_t, h_t = model.step(x, h, 0.01)
    y_o, h_o = sess.run(None, {"x": x.numpy(), "h": h.numpy(), "dt": np.float32(0.01)})
    max_diff_y = float(np.abs(y_t.numpy() - y_o).max())
    max_diff_h = float(np.abs(h_t.numpy() - h_o).max())
    logger.info("一致性自检: max|Δy|=%g max|Δh|=%g", max_diff_y, max_diff_h)
    assert max_diff_y < 1e-3 and max_diff_h < 1e-3, "PyTorch 与 ONNX 输出不一致"
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="TorchMambaLNN → ONNX 导出")
    parser.add_argument("--checkpoint", default="output/ssm_smoke/torch_mamba_lnn.pt")
    parser.add_argument("--out", default="output/ssm_smoke/torch_mamba_lnn.onnx")
    args = parser.parse_args()
    export(Path(args.checkpoint), Path(args.out))
    logger.info("完成: %s", args.out)


if __name__ == "__main__":
    main()
