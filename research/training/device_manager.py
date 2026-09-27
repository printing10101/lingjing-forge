"""设备管理模块。

提供 GPU 自动检测、设备优先级管理、CPU 回退及批量大小/工作线程数推荐。
"""

import os
import logging
import torch
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class DeviceInfo:
    """计算设备硬件信息容器。

    Attributes:
        device_type: 设备类型 ("cuda" 或 "cpu")。
        device_index: 设备索引。
        device_name: 设备名称。
        total_memory_mb: 总显存（MB）。
        available_memory_mb: 可用显存（MB）。
        cuda_version: CUDA 版本。
        compute_capability: GPU 计算能力。
        gpu_count: GPU 数量。
    """

    def __init__(
        self,
        device_type: str,
        device_index: int = 0,
        device_name: str = "CPU",
        total_memory_mb: float = 0.0,
        available_memory_mb: float = 0.0,
        cuda_version: str = "",
        compute_capability: str = "",
        gpu_count: int = 1,
    ):
        self.device_type = device_type
        self.device_index = device_index
        self.device_name = device_name
        self.total_memory_mb = total_memory_mb
        self.available_memory_mb = available_memory_mb
        self.cuda_version = cuda_version
        self.compute_capability = compute_capability
        self.gpu_count = gpu_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_type": self.device_type,
            "device_index": self.device_index,
            "device_name": self.device_name,
            "total_memory_mb": round(self.total_memory_mb, 2),
            "available_memory_mb": round(self.available_memory_mb, 2),
            "cuda_version": self.cuda_version,
            "compute_capability": self.compute_capability,
            "gpu_count": self.gpu_count,
        }


def detect_device(device_preference: str = "auto") -> tuple[torch.device, DeviceInfo]:
    """
    Detect and select the best available device for training.

    Args:
        device_preference: Device preference - 'auto', 'gpu', or 'cpu'

    Returns:
        Tuple of (torch.device, DeviceInfo)
    """
    env_device = os.environ.get("LNN_TRAINING_DEVICE", "").lower()
    if env_device and env_device in ["auto", "gpu", "cuda", "cpu"]:
        device_preference = "cuda" if env_device == "gpu" else env_device

    if device_preference == "cpu":
        device = torch.device("cpu")
        info = DeviceInfo(device_type="cpu")
        logger.info("Using CPU for training (user preference)")
        return device, info

    if torch.cuda.is_available():
        gpu_count = torch.cuda.device_count()
        gpu_index = 0
        device = torch.device(f"cuda:{gpu_index}")

        props = torch.cuda.get_device_properties(gpu_index)
        total_mem_mb = props.total_memory / (1024**2)
        allocated_mem_mb = torch.cuda.memory_allocated(gpu_index) / (1024**2)
        torch.cuda.memory_reserved(gpu_index) / (1024**2)
        available_mem_mb = total_mem_mb - allocated_mem_mb

        info = DeviceInfo(
            device_type="cuda",
            device_index=gpu_index,
            device_name=props.name,
            total_memory_mb=total_mem_mb,
            available_memory_mb=available_mem_mb,
            cuda_version=torch.version.cuda or "",
            compute_capability=f"{props.major}.{props.minor}",
            gpu_count=gpu_count,
        )

        logger.info(
            f"Using GPU for training: {props.name} "
            f"(VRAM: {total_mem_mb:.0f}MB, CUDA: {torch.version.cuda})"
        )
        return device, info
    else:
        device = torch.device("cpu")
        info = DeviceInfo(device_type="cpu")
        logger.info("CUDA not available, falling back to CPU for training")
        return device, info


def get_available_devices() -> List[DeviceInfo]:
    """
    Get information about all available compute devices.

    Returns:
        List of DeviceInfo objects for all available devices
    """
    devices = []

    devices.append(
        DeviceInfo(
            device_type="cpu",
            device_name="CPU",
            gpu_count=1,
        )
    )

    if torch.cuda.is_available():
        gpu_count = torch.cuda.device_count()
        for i in range(gpu_count):
            props = torch.cuda.get_device_properties(i)
            total_mem_mb = props.total_memory / (1024**2)

            devices.append(
                DeviceInfo(
                    device_type="cuda",
                    device_index=i,
                    device_name=props.name,
                    total_memory_mb=total_mem_mb,
                    available_memory_mb=total_mem_mb,
                    cuda_version=torch.version.cuda or "",
                    compute_capability=f"{props.major}.{props.minor}",
                    gpu_count=gpu_count,
                )
            )

    return devices


def get_device_status(device: torch.device) -> Dict[str, Any]:
    """
    Get current device status including utilization and temperature.

    Args:
        device: PyTorch device to query

    Returns:
        Dictionary with device status information
    """
    status = {
        "device_type": device.type,
        "device_str": str(device),
    }

    if device.type == "cuda" and torch.cuda.is_available():
        gpu_index = device.index if device.index is not None else 0

        status.update(
            {
                "gpu_name": torch.cuda.get_device_properties(gpu_index).name,
                "total_memory_mb": round(
                    torch.cuda.get_device_properties(gpu_index).total_memory
                    / (1024**2),
                    2,
                ),
                "allocated_memory_mb": round(
                    torch.cuda.memory_allocated(gpu_index) / (1024**2), 2
                ),
                "reserved_memory_mb": round(
                    torch.cuda.memory_reserved(gpu_index) / (1024**2), 2
                ),
                "max_memory_mb": round(
                    torch.cuda.max_memory_allocated(gpu_index) / (1024**2), 2
                ),
                "cuda_version": torch.version.cuda or "",
                "gpu_count": torch.cuda.device_count(),
                "is_available": True,
            }
        )

        try:
            temp = torch.cuda.temperature(gpu_index)
            status["temperature_celsius"] = temp
        except (RuntimeError, AttributeError, OSError, ModuleNotFoundError):
            # GPU 温度查询可能在驱动未就绪/CUDA 不可用/缺少 pynvml 时失败，回退为 None
            status["temperature_celsius"] = None

        try:
            utilization = torch.cuda.utilization(gpu_index)
            status["utilization_percent"] = utilization
        except (RuntimeError, AttributeError, OSError, ModuleNotFoundError):
            # GPU 利用率查询同上
            status["utilization_percent"] = None
    else:
        import psutil

        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()

        status.update(
            {
                "cpu_percent": cpu_percent,
                "total_memory_mb": round(mem.total / (1024**2), 2),
                "available_memory_mb": round(mem.available / (1024**2), 2),
                "memory_percent": mem.percent,
                "is_available": True,
            }
        )

    return status


def get_optimal_batch_size(device: torch.device, default_batch_size: int = 32) -> int:
    """
    Calculate optimal batch size based on device capabilities.

    Args:
        device: Target training device
        default_batch_size: Default batch size for CPU

    Returns:
        Recommended batch size
    """
    if device.type == "cuda" and torch.cuda.is_available():
        gpu_index = device.index if device.index is not None else 0
        total_mem_mb = torch.cuda.get_device_properties(gpu_index).total_memory / (
            1024**2
        )

        if total_mem_mb >= 16000:
            return default_batch_size * 4
        elif total_mem_mb >= 8000:
            return default_batch_size * 2
        else:
            return default_batch_size
    else:
        return default_batch_size


def get_optimal_num_workers() -> int:
    """
    Calculate optimal number of DataLoader workers based on CPU cores.

    Returns:
        Recommended number of workers
    """
    import multiprocessing

    cpu_count = multiprocessing.cpu_count()

    if cpu_count <= 2:
        return 0
    elif cpu_count <= 4:
        return 2
    elif cpu_count <= 8:
        return 4
    else:
        return min(8, cpu_count // 2)


def clear_gpu_memory(device: torch.device) -> None:
    """
    Clear unused GPU memory to prevent fragmentation.

    Args:
        device: Target device to clear
    """
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        logger.debug("GPU memory cleared")


def check_gpu_memory_safe(threshold_percent: float = 90.0) -> bool:
    """
    Check if GPU memory usage is within safe limits.

    Args:
        threshold_percent: Maximum safe memory usage percentage

    Returns:
        True if memory usage is safe, False otherwise
    """
    if not torch.cuda.is_available():
        return True

    gpu_index = 0
    total_mem = torch.cuda.get_device_properties(gpu_index).total_memory
    allocated_mem = torch.cuda.memory_allocated(gpu_index)

    usage_percent = (allocated_mem / total_mem) * 100
    return usage_percent < threshold_percent
