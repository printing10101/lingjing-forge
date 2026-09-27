"""LNN 训练/模型/设备管理的契约接口 (Protocol/ABC)。

本模块定义 engineering 侧消费的接口，不包含任何 PyTorch 导入。
research/ 中的具体实现通过鸭子类型满足这些 Protocol。

目的 (P0#3 解耦):
  - engineering 包只依赖 contracts.py（零 PyTorch 依赖）
  - 生产容器（onnxruntime only）可以加载此模块而无需 torch
  - 训练功能在开发/研究环境中通过 research_bridge 延迟加载

使用方式::

    from app.ai.lnn.contracts import LNNConfigProtocol
    from app.ai.lnn._research_bridge import get_lnn_config
    cfg = get_lnn_config(hidden_size=64)  # 返回满足 LNNConfigProtocol 的对象
"""

from __future__ import annotations

from typing import Any, Protocol
from typing_extensions import runtime_checkable


# LNN 模型配置


@runtime_checkable
class LNNConfigProtocol(Protocol):
    """LNN 模型配置协议。

    与 ``research.models.torch_base_lnn.LNNConfig`` 保持结构兼容。
    """

    input_dim: int
    hidden_size: int
    output_dim: int
    num_layers: int
    learning_rate: float
    batch_size: int
    epochs: int

    def to_dict(self) -> dict[str, Any]: ...


# LNN 训练器


@runtime_checkable
class LNNTrainerProtocol(Protocol):
    """LNN 模型训练器协议。"""

    def __init__(self, config: LNNConfigProtocol, model: Any, **kwargs: Any) -> None: ...

    def train(
        self,
        train_loader: Any,
        val_loader: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


# 设备管理


class DeviceInfo(Protocol):
    """设备信息协议。"""

    device_type: str
    device_name: str


def detect_device(force_cpu: bool = False) -> DeviceInfo:
    """检测可用设备（CPU/CUDA/MPS）。原生实现在 research_bridge 中。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


def get_optimal_batch_size(device: DeviceInfo, default: int = 32) -> int:
    """根据设备计算最优 batch size。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


def get_optimal_num_workers(device: DeviceInfo, default: int = 4) -> int:
    """根据设备计算最优 DataLoader workers。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


def get_device_status() -> dict[str, Any]:
    """获取设备状态摘要。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


def get_available_devices() -> list[DeviceInfo]:
    """列出所有可用设备。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


def clear_gpu_memory() -> None:
    """清理 GPU 显存缓存。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


# 实验追踪 (MLflow)


@runtime_checkable
class ExperimentTrackerProtocol(Protocol):
    """实验追踪器协议（MLflow 包装）。"""

    def start_run(self, run_name: str, **kwargs: Any) -> Any: ...

    def log_params(self, params: dict[str, Any]) -> None: ...

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None: ...

    def log_model(self, model: Any, artifact_path: str, **kwargs: Any) -> None: ...

    def end_run(self) -> None: ...


# 量化


@runtime_checkable
class QuantizerProtocol(Protocol):
    """模型量化器协议。"""

    def quantize(self, model: Any, calibration_data: Any | None = None) -> Any: ...

    def get_model_size_bytes(self, model: Any) -> int: ...


# 可复现性


def set_global_seed(seed: int = 42) -> None:
    """设置全局随机种子以确保实验可复现。"""
    raise NotImplementedError("该函数由 research_bridge 延迟导入提供，生产环境不应直接调用")


# 类型别名（简化导入）

ConfigType = LNNConfigProtocol
TrainerType = LNNTrainerProtocol
TrackerType = ExperimentTrackerProtocol
