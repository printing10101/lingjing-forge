"""SSM 工程侧推理封装（升级④：onnxruntime，无 torch 依赖）。

把 research 训练导出的 TorchMambaLNN ONNX（单步接口 forward(x, dt, h)）
封装为工程侧可用的预测器，并通过 ``register_ssm_predictor`` 挂到
``HybridInferenceEngine.register_lnn_predictor``（生产 onnxruntime 路径）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["SsmOnnxPredictor", "SsmOnnxMeta", "register_ssm_predictor"]


@dataclass
class SsmOnnxMeta:
    """ONNX 模型静态形状元数据（从导出端带出）。"""

    input_size: int
    hidden_size: int
    output_size: int
    num_layers: int
    dt: float = 0.01


class SsmOnnxPredictor:
    """SSM 单步预测器（onnxruntime 后端）。

    ``predict(x, h) -> (y, h_new)``：一次状态空间更新；
    隐藏状态由调用方持有并在序列上迭代（流式兼容，与 LNN 单步契约一致）。
    """

    def __init__(self, onnx_path: str | Path, meta: SsmOnnxMeta) -> None:
        try:
            import onnxruntime as ort  # noqa: PLC0415 - 延迟导入保持轻依赖
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("onnxruntime 未安装，无法使用 SSM 工程侧推理") from e

        self.meta = meta
        self._session = ort.InferenceSession(
            str(onnx_path),
            providers=["CPUExecutionProvider"],
        )
        logger.info("SSM ONNX 会话已加载: %s（dt=%.3f 烘焙于图内）", onnx_path, meta.dt)

    def predict(
        self,
        x: np.ndarray,
        h: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """单步状态空间更新。

        Args:
            x: (B, input_size) float32
            h: (num_layers, B, hidden_size) float32

        Returns:
            (y (B, output_size), h_new (num_layers, B, hidden_size))
        """
        x = np.asarray(x, dtype=np.float32)
        h = np.asarray(h, dtype=np.float32)
        y, h_new = self._session.run(
            None,
            {
                "x": x,
                "h": h,
            },
        )
        return y, h_new

    def predict_sequence(self, x_seq: np.ndarray) -> np.ndarray:
        """序列推理：从零状态扫描，返回末步输出 (B, output_size)。

        Args:
            x_seq: (B, T, input_size) float32

        Returns:
            (B, output_size) 末步预测
        """
        batch, time, _ = x_seq.shape
        h = np.zeros((self.meta.num_layers, batch, self.meta.hidden_size), dtype=np.float32)
        y = np.zeros((batch, self.meta.output_size), dtype=np.float32)
        for t in range(time):
            y, h = self.predict(x_seq[:, t], h)
        return y


def register_ssm_predictor(
    engine: Any,
    onnx_path: str | Path,
    meta: SsmOnnxMeta,
    model_name: str = "ssm_chatter",
) -> SsmOnnxPredictor:
    """实例化 SsmOnnxPredictor 并注册到 HybridInferenceEngine。

    Args:
        engine: 具备 ``register_lnn_predictor(model_name, predictor)`` 的引擎实例。
        onnx_path: ONNX 文件路径。
        meta: 模型静态形状元数据。
        model_name: 注册名（默认 ssm_chatter）。

    Returns:
        已注册的 SsmOnnxPredictor。
    """
    predictor = SsmOnnxPredictor(onnx_path, meta)
    engine.register_lnn_predictor(model_name, predictor)
    logger.info("SSM 预测器已注册到引擎: %s", model_name)
    return predictor
