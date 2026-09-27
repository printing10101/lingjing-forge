"""LNN 系统端点（health / tasks / device）。

从 routes.py 拆分而来（P0-2.3 子路由拆分）。本模块承载健康检查、
任务列表、设备信息/状态/缓存清理等系统级端点，模块级状态
（model_registry / task_manager / _active_training_tasks 等）集中在
``dependencies.py``。

设备管理函数（detect_device 等）来自 ``app.ai.lnn.training.device_manager``，
通过 try/except 兼容旧路径，避免阶段2解耦改造后导入失败。
"""

import logging

from fastapi import APIRouter, Depends

from app.core.response import ErrorCode, error, success
from app.auth.permissions import require_permission
from app.core.api_response import api_response
from app.tasks.task_manager import TaskType

# P0#3 解耦: 通过 research_bridge 延迟导入。
_HAS_DEVICE_MANAGER = False
detect_device = None
get_available_devices = None
get_device_status = None
clear_gpu_memory = None


def _lazy_init_device_manager() -> bool:
    global _HAS_DEVICE_MANAGER, detect_device, get_available_devices
    global get_device_status, clear_gpu_memory
    if _HAS_DEVICE_MANAGER:
        return True
    try:
        from app.ai.lnn._research_bridge import (
            get_device_detect,
            get_available_devices_func,
            get_device_status_func,
            get_clear_gpu_memory_func,
        )

        detect_device = get_device_detect()
        get_available_devices = get_available_devices_func()
        get_device_status = get_device_status_func()
        clear_gpu_memory = get_clear_gpu_memory_func()
        _HAS_DEVICE_MANAGER = detect_device is not None
    except Exception:
        _HAS_DEVICE_MANAGER = False
    return _HAS_DEVICE_MANAGER


from app.api.v1.lnn.dependencies import (
    model_registry,
    task_manager,
    MAX_CONCURRENT_TRAINING_TASKS,
    _active_training_tasks,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
@api_response
async def health_check():
    """LNN 系统健康检查(包含持久层状态)"""
    model_count = len(model_registry.registry)
    active_tasks = len(_active_training_tasks)
    total_slots = MAX_CONCURRENT_TRAINING_TASKS

    from app.database.connection import check_db_health
    from app.services.redis_client import check_redis_health

    db_health = await check_db_health()
    redis_health = await check_redis_health()

    health_status = {
        "status": "healthy" if model_count > 0 else "degraded",
        "models_registered": model_count,
        "active_training_tasks": active_tasks,
        "available_training_slots": total_slots - active_tasks,
        "max_concurrent_tasks": total_slots,
        "persistence": {
            "postgres": db_health,
            "redis": redis_health,
        },
    }

    return success(data=health_status, message="Health check completed")


@router.get("/tasks")
@api_response
async def list_training_tasks():
    """列出所有训练任务"""
    tasks = await task_manager.list_tasks(task_type=TaskType.LNN_TRAINING, limit=200, offset=0)

    tasks_list = []
    for t in tasks:
        td = t.to_dict()
        tasks_list.append(
            {
                "task_id": t.job_id,
                "status": t.status.value,
                "progress": t.progress,
                "message": td.get("error", ""),
                "metrics": td.get("metrics"),
                "created_at": td.get("created_at_iso", ""),
                "duration_seconds": td.get("duration_seconds"),
            }
        )

    return success(
        data={"tasks": tasks_list, "total": len(tasks_list)},
        message="Training tasks retrieved",
    )


@router.get("/device/info")
@api_response
async def get_device_info():
    """返回系统中可用的计算设备信息"""
    # 首次访问时懒初始化设备管理桥接（模块加载时不调用，避免启动期依赖科研侧）
    _lazy_init_device_manager()
    # 设备管理模块未启用时直接返回错误，避免调用 None 抛 TypeError
    if not _HAS_DEVICE_MANAGER or get_available_devices is None or detect_device is None:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="device_manager 模块未启用，设备信息不可用",
        )

    import torch  # noqa: PLC0415 - 延迟导入（无 torch 环境降级）

    devices = get_available_devices()

    current_device, current_info = detect_device("auto")

    response_data = {
        "current_device": {
            "type": current_info.device_type,
            "index": current_info.device_index,
            "name": current_info.device_name,
            "total_memory_mb": current_info.total_memory_mb,
            "available_memory_mb": current_info.available_memory_mb,
            "cuda_version": current_info.cuda_version,
            "compute_capability": current_info.compute_capability,
            "gpu_count": current_info.gpu_count,
        },
        "available_devices": [d.to_dict() for d in devices],
        "torch_cuda_available": torch.cuda.is_available(),  # type: ignore[attr-defined]
        "torch_version": torch.__version__,  # type: ignore[attr-defined]
        "cuda_version": (
            torch.version.cuda if torch.cuda.is_available() else None  # type: ignore[attr-defined]
        ),
        "cudnn_version": (
            torch.backends.cudnn.version()  # type: ignore[attr-defined]
            if torch.cuda.is_available()  # type: ignore[attr-defined]
            else None
        ),
    }

    return success(data=response_data, message="Device info retrieved successfully")


@router.get("/device/status")
@api_response
async def get_device_status_endpoint():
    """返回当前设备利用率和温度等信息"""
    _lazy_init_device_manager()
    if not _HAS_DEVICE_MANAGER or detect_device is None or get_device_status is None:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="device_manager 模块未启用，设备状态不可用",
        )

    device, device_info = detect_device("auto")

    status = get_device_status(device)

    response_data = {
        "active_device": str(device),
        "device_info": device_info.to_dict(),
        "status": status,
    }

    if device.type == "cuda":
        import torch  # noqa: PLC0415 - 延迟导入（无 torch 环境降级）

        try:
            gpu_index = device.index if device.index is not None else 0
            response_data["gpu_status"] = {
                "total_memory_mb": round(
                    torch.cuda.get_device_properties(gpu_index).total_memory  # type: ignore[attr-defined]
                    / (1024**2),
                    2,
                ),
                "allocated_memory_mb": round(
                    torch.cuda.memory_allocated(gpu_index) / (1024**2),  # type: ignore[attr-defined]
                    2,
                ),
                "reserved_memory_mb": round(
                    torch.cuda.memory_reserved(gpu_index) / (1024**2),  # type: ignore[attr-defined]
                    2,
                ),
                "max_memory_mb": round(
                    torch.cuda.max_memory_allocated(gpu_index) / (1024**2),  # type: ignore[attr-defined]
                    2,
                ),
            }
        except Exception as e:  # noqa: BLE001
            # GPU 状态探测失败（驱动/上下文/线程环境问题）不阻断整个端点，
            # 降级为仅返回基础设备信息，并显式标注错误供排查。
            logger.warning("GPU status probe failed, degraded response: %s", e)
            response_data["gpu_status_error"] = str(e)

    return success(data=response_data, message="Device status retrieved successfully")


@router.post("/device/clear-cache", dependencies=[Depends(require_permission("lnn:write"))])
@api_response
async def clear_device_cache():
    """清空GPU缓存"""
    if not _HAS_DEVICE_MANAGER or clear_gpu_memory is None:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="device_manager 模块未启用，无法清理 GPU 缓存",
        )

    import torch  # noqa: PLC0415 - 延迟导入（无 torch 环境降级）

    if not torch.cuda.is_available():  # type: ignore[attr-defined]
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="No CUDA device available",
        )

    clear_gpu_memory(torch.device("cuda"))  # type: ignore[attr-defined]

    return success(
        data={"message": "GPU cache cleared successfully"},
        message="Device cache cleared",
    )
