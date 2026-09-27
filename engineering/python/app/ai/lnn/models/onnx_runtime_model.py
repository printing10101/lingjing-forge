"""ONNX 权重的注册表模型适配器（onnxruntime 后端，无 torch 依赖）。

让 ``ModelRegistry`` / ``LNNModelRegistry`` 能按权重文件扩展名分发加载：
``.npz`` 走既有 NumPy 模型类，``.onnx`` 走本适配器。适配器与
``BaseLNNModel`` 保持最小同构接口（build/forward/predict/get_model_info），
使注册表的加载与预测路径无需感知后端差异。

导出端约定（与 research/scripts/export_ssm_onnx.py 的验证方式一致）：
图输入为单输入特征矩阵 ``float32 (batch, input_dim)``，输出为
``(batch, output_dim)``。预测路径数值与训练侧 torch 前向逐元素一致
（parity 由导出脚本在导出时验证，并记录进 manifest）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class OnnxLNNModel:
    """以 onnxruntime 执行 LNN 权重的模型适配器。

    Attributes:
        model_name: 模型名称。
        input_dim: 输入维度（缺省时从 ONNX 图输入形状推导）。
        output_dim: 输出维度（缺省时从 ONNX 图输出形状推导）。
        is_trained: 恒为 True——ONNX 工件只能由训练管线产出。
    """

    def __init__(
        self,
        model_name: str,
        input_dim: int | None = None,
        output_dim: int | None = None,
        device: str = "cpu",
        **kwargs: Any,
    ):
        self.model_name = model_name
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.device = device
        self.is_trained = True
        self.config = kwargs
        self._session: Any = None
        self._input_name: str | None = None
        self._output_names: list[str] = []
        self._onnx_path: str | None = None

    # ── BaseLNNModel 同构接口 ──────────────────────────────────────────

    def build(self) -> None:
        """兼容接口：ONNX 会话在 load 时创建，此处无需构建。"""

    def forward(self, x: np.ndarray) -> np.ndarray:
        return self.predict(x)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """执行 ONNX 推理，返回 ``(batch, output_dim)``。"""
        if self._session is None:
            raise RuntimeError(f"ONNX 模型 '{self.model_name}' 尚未加载权重文件。请先调用 load(path) 加载 .onnx 工件。")
        arr = np.asarray(x, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        outputs = self._session.run(self._output_names, {self._input_name: arr})
        first = outputs[0]
        if self.output_dim is not None and first.ndim > 1 and first.shape[1] != self.output_dim:
            raise ValueError(f"ONNX 输出维度不符：期望 {self.output_dim}，实际 {first.shape[1]}（{self._onnx_path}）")
        return first

    def get_model_info(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "input_dim": self.input_dim,
            "output_dim": self.output_dim,
            "device": self.device,
            "is_trained": self.is_trained,
            "backend": "onnxruntime",
            "onnx_path": self._onnx_path,
            "config": self.config,
        }

    def save(self, path: str) -> None:
        raise NotImplementedError("ONNX 适配器不支持 save：请从训练侧重新导出 .onnx 工件")

    def load(self, path: str) -> None:
        """加载 .onnx 工件并创建 onnxruntime 会话（懒依赖，缺失时给出指引）。"""
        try:
            import onnxruntime as ort
        except ImportError as e:  # pragma: no cover - 环境缺依赖
            raise RuntimeError(f"加载 ONNX 模型需要 onnxruntime（工程侧已内置依赖）。当前环境导入失败：{e}") from e

        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = 1  # 桌面单请求场景：小模型低延迟优先
        self._session = ort.InferenceSession(str(path), sess_options=sess_options, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]

        if self.input_dim is None:
            shape = self._session.get_inputs()[0].shape
            self.input_dim = int(shape[1]) if len(shape) > 1 and isinstance(shape[1], int) else None
        if self.output_dim is None:
            shape = self._session.get_outputs()[0].shape
            self.output_dim = int(shape[1]) if len(shape) > 1 and isinstance(shape[1], int) else None

        self._onnx_path = str(Path(path))
        logger.info("ONNX 模型已加载: %s (input=%s)", path, self.input_dim)
