"""TorchMambaLNN → ONNX 导出（Phase 3a 升级：④ 工程侧接入）。

契约（与 engineering/python/app/ai/lnn/ssm_inference.py 对齐）：
- 输入名：``x`` (B, input_size) float32、``h`` (num_layers, B, hidden_size) float32
- 输出名：``y`` (B, output_size) float32、``h_new`` (num_layers, B, hidden_size) float32
- ``dt`` 烘焙进图（默认 0.01），推理端不再传 dt
- batch 维度动态（dynamic_axes）

用法（research/ 目录下）：
    python experiments/05_SSM预测backbone/export_onnx.py \
        --checkpoint output/ssm_smoke/torch_mamba_lnn.pt \
        --out output/ssm_smoke/torch_mamba_lnn.onnx
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

_RESEARCH_ROOT = Path(__file__).resolve().parents[2]
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from models.torch_base_lnn import LNNConfig  # noqa: E402
from models.torch_mamba_lnn import TorchMambaLNN  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ssm-export")

DEFAULT_DT = 0.01


class _StepWrapper(nn.Module):
    """把 (x, h, dt) 的三参单步接口包装为 (x, h) 两参（dt 烘焙为常量）。"""

    def __init__(self, model: TorchMambaLNN, dt: float = DEFAULT_DT) -> None:
        super().__init__()
        self.model = model
        self.dt = dt

    def forward(self, x: torch.Tensor, h: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self.model.step(x, h, self.dt)


def export_ssm_onnx(
    model: TorchMambaLNN,
    out_path: str | Path,
    dt: float = DEFAULT_DT,
    example_batch: int = 1,
) -> dict[str, Any]:
    """把 TorchMambaLNN 导出为单步 ONNX（契约见模块 docstring）。

    Args:
        model: 已加载权重的 TorchMambaLNN。
        out_path: 输出 .onnx 路径。
        dt: 烘焙时间步长。
        example_batch: 示例 batch 大小（导出时用）。

    Returns:
        meta dict（input_size/hidden_size/output_size/num_layers/dt），
        同时写入 {out_path}.meta.json。
    """
    model.eval()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wrapper = _StepWrapper(model, dt)
    x = torch.randn(example_batch, model.config.input_size)
    h = torch.zeros(model.config.num_layers, example_batch, model.config.hidden_size)

    torch.onnx.export(
        wrapper,
        (x, h),
        str(out_path),
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

    meta = {
        "input_size": model.config.input_size,
        "hidden_size": model.config.hidden_size,
        "output_size": model.config.output_size,
        "num_layers": model.config.num_layers,
        "dt": dt,
    }
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("ONNX 已导出: %s（meta=%s）", out_path, meta)
    return meta


def verify_numerical(
    model: TorchMambaLNN,
    onnx_path: str | Path,
    dt: float = DEFAULT_DT,
    batch: int = 3,
    tol: float = 1e-4,
) -> float:
    """torch 单步 vs onnxruntime 单步 数值一致性检查，返回最大绝对误差。"""
    import onnxruntime as ort

    model.eval()
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    with torch.no_grad():
        x_t = torch.randn(batch, model.config.input_size)
        h_t = torch.randn(model.config.num_layers, batch, model.config.hidden_size)
        y_t, h_new_t = model.step(x_t, h_t, dt)

    y_o, h_o = session.run(None, {"x": x_t.numpy(), "h": h_t.numpy()})
    max_err = max(
        float(abs(y_t.numpy() - y_o).max()),
        float(abs(h_new_t.numpy() - h_o).max()),
    )
    logger.info("数值一致性: max_err=%.2e（tol=%.0e）", max_err, tol)
    assert max_err < tol, f"ONNX 数值偏差过大: {max_err} >= {tol}"
    return max_err


def main() -> None:
    parser = argparse.ArgumentParser(description="TorchMambaLNN → ONNX 导出")
    parser.add_argument("--checkpoint", required=True, help="训练 checkpoint (.pt)")
    parser.add_argument("--out", default="output/ssm_smoke/torch_mamba_lnn.onnx")
    parser.add_argument("--dt", type=float, default=DEFAULT_DT)
    parser.add_argument("--skip-verify", action="store_true")
    args = parser.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    config = LNNConfig(**ckpt["config"])
    model = TorchMambaLNN(config)
    model.load_state_dict(ckpt["state_dict"])
    logger.info("已加载 checkpoint: %s（model=%s）", args.checkpoint, ckpt.get("model_name"))

    meta = export_ssm_onnx(model, args.out, dt=args.dt)
    logger.info("meta: %s", meta)
    if not args.skip_verify:
        verify_numerical(model, args.out, dt=args.dt)


if __name__ == "__main__":
    main()
