"""仿真 API 辅助模块（V3.0 自 api.py 拆分）。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.utils.utils import validate_user_path
from app.config import config
from app.simulation.voxel_cutter import (
    VoxelCutter,
    VoxelSimulationResult,
    ToolModel,
)
from app.simulation.toolpath_parser import ToolpathParser, ToolpathSegment
from app.simulation.schemas import SimulationRequest, SimulationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/simulation", tags=["Simulation"])

OUTPUT_DIR = Path(config.storage.output_dir) / "simulation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Allowed base directories for user-provided file path validation.
# These prevent path traversal attacks by restricting file access to
# known output and upload directories where files are legitimately stored.

_OUTPUT_ROOT = Path(config.storage.output_dir).resolve()
_ALLOWED_STOCK_DIRS: list[Path] = [
    OUTPUT_DIR.resolve(),
    (_OUTPUT_ROOT / "step_import").resolve(),
    (_OUTPUT_ROOT / "step_import" / "_uploads").resolve(),
    (_OUTPUT_ROOT / "dxf_import").resolve(),
    (_OUTPUT_ROOT / "dxf_import" / "_uploads").resolve(),
    (_OUTPUT_ROOT / "projects").resolve(),
    (_OUTPUT_ROOT / "projects" / "_uploads").resolve(),
]


def _validate_user_path(user_path: str, field_name: str) -> Path:
    """Validate that a user-provided file path is within allowed directories.

    委托给统一的 ``app.utils.utils.validate_user_path`` 实现：解析路径为绝对
    路径并校验其位于预定义的允许目录之一内，消除任何目录遍历组件。

    Args:
        user_path: The raw path string from the user request.
        field_name: The field name for error reporting (e.g. "stock_stl_path").

    Returns:
        The resolved absolute Path if validation passes.

    Raises:
        HTTPException: 400 if the path is outside allowed directories.
    """
    try:
        return validate_user_path(
            user_path=user_path,
            allowed_base_dirs=_ALLOWED_STOCK_DIRS,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The path '{user_path}' for '{field_name}' is not allowed. "
                f"File must reside within a permitted output or upload directory."
            ),
        ) from exc


_in_memory_store: dict[str, VoxelSimulationResult] = {}
# 修复：原实现 get_simulation_history 接受 project_id 但完全未应用，导致过滤参数形同虚设。
# 这里使用一个独立的 task_id -> project_id 映射避免修改 VoxelSimulationResult 的字段
# （该类被多个模块使用，添加字段会引发级联修改）。
_project_id_map: dict[str, str] = {}
# 修复 [潜在崩溃]：VoxelSimulationResult 没有 completed_at 字段，但 _cleanup_store 和
# get_simulation_history 直接访问 r.completed_at.timestamp() 会在 store 超过容量或包含
# 完成结果时触发 AttributeError。使用独立 map 记录完成时间戳，避免侵入式修改 dataclass。
_completed_at_map: dict[str, float] = {}
# 修复 [任务生命周期]：记录每个活动的 asyncio.Task 引用，避免后台任务因 GC 被提前
# 取消；并支持在客户端主动取消时优雅回收资源。
_active_tasks: dict[str, "asyncio.Task[None]"] = {}
# 修复 [并发安全]：FastAPI 可并发处理多个仿真请求，使用 asyncio.Lock 保护共享 store
# 状态（添加/更新/清理），避免极端并发下 _in_memory_store 与三个关联 map 之间出现
# 短暂不一致（例如 cleanup 在 store 删除时 _completed_at_map 已被覆盖）。
_store_lock: "asyncio.Lock | None" = None
_MAX_STORE_SIZE = config.simulation.max_store_size
_MAX_STORE_AGE_SECONDS = config.simulation.max_store_age_seconds


def _get_store_lock() -> asyncio.Lock:
    """懒初始化 asyncio.Lock。

    在 FastAPI 启动后才有可绑定的事件循环，因此采用懒加载避免在 import 期
    实例化时绑定到错误的循环（uvicorn 重新载入场景下尤其重要）。
    """
    global _store_lock
    if _store_lock is None:
        _store_lock = asyncio.Lock()
    return _store_lock


async def _cleanup_store() -> None:
    """Remove expired and excess entries from the in-memory result store.

    Evicts the oldest results when the store exceeds _MAX_STORE_SIZE,
    and removes entries older than _MAX_STORE_AGE_SECONDS. 修复合并发：
    在持有 asyncio.Lock 的情况下统一清理 _in_memory_store 与三个关联 map，
    避免清理过程中其他协程插入/删除同一 key 导致字典大小判断错乱。
    """
    async with _get_store_lock():
        now = time.time()
        if len(_in_memory_store) > _MAX_STORE_SIZE:
            # 修复 [潜在崩溃]：原代码 kv[1].completed_at.timestamp() 会因 VoxelSimulationResult
            # 没有该字段而抛 AttributeError，导致清理彻底失败、内存无限增长。
            sorted_entries = sorted(
                _in_memory_store.items(),
                key=lambda kv: _completed_at_map.get(kv[0], 0.0),
            )
            for task_id, _ in sorted_entries[: len(_in_memory_store) - _MAX_STORE_SIZE]:
                _in_memory_store.pop(task_id, None)
                # 修复 [资源清理]：同步清理关联映射，避免孤儿键。
                _project_id_map.pop(task_id, None)
                _completed_at_map.pop(task_id, None)
                _active_tasks.pop(task_id, None)
        expired = [
            tid
            for tid in _in_memory_store
            if (_completed_at_map.get(tid) is not None) and (now - _completed_at_map[tid]) > _MAX_STORE_AGE_SECONDS
        ]
        for tid in expired:
            _in_memory_store.pop(tid, None)
            # 修复 [资源清理]：同步清理关联映射。
            _project_id_map.pop(tid, None)
            _completed_at_map.pop(tid, None)
            _active_tasks.pop(tid, None)


def _run_simulation(
    task_id: str,
    request: SimulationRequest,
) -> VoxelSimulationResult:
    """Execute voxel cutting simulation synchronously for background task use.

    Parses G-code into toolpath segments, constructs the tool model,
    loads or generates the stock STL, and runs the voxel-based simulation.

    Args:
        task_id: Unique identifier for the simulation task.
        request: Simulation request parameters.

    Returns:
        VoxelSimulationResult containing the machined model and collision data.
    """
    tool = ToolModel(
        diameter=request.tool_diameter,
        cutting_length=request.tool_length,
        tool_type=request.tool_type,
        corner_radius=request.tool_corner_radius,
    )

    segments: list[ToolpathSegment] = []
    if request.gcode.strip():
        parser = ToolpathParser(controller_type="fanuc")
        segments = parser.parse_gcode(request.gcode)

    if request.stock_stl_path:
        try:
            stock_stl_path = _validate_user_path(request.stock_stl_path, "stock_stl_path")
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
    else:
        stock_stl_path = _default_stock_stl()

    source_file_paths: list[Path] | None = None
    if request.source_file_path:
        try:
            source_path = _validate_user_path(request.source_file_path, "source_file_path")
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
        if source_path.exists():
            source_file_paths = [source_path]
        else:
            logger.warning(
                "[Auto-generate STL] Specified source file does not exist: %s",
                source_path,
            )

    cutter = VoxelCutter(voxel_size=request.voxel_size)
    result = cutter.run_simulation(
        stock_stl_path=stock_stl_path,
        tool=tool,
        segments=segments,
        output_dir=OUTPUT_DIR,
        safe_z_height=request.safe_z_height,
        task_id=task_id,
        source_file_paths=source_file_paths,
    )

    _in_memory_store[task_id] = result
    # 修复：记录 task_id -> project_id 映射，使 history 接口的过滤参数真正生效。
    _project_id_map[task_id] = request.project_id
    # 修复 [清理支持]：记录完成时间戳，使 _cleanup_store 能正确按时间淘汰。
    _completed_at_map[task_id] = time.time()
    # 修复 [并发安全]：cleanup 是异步且需要持锁，必须在事件循环中由外层
    # async 端点调用；同步函数内部仅做数据写入，将清理动作交给 _post_insert_cleanup。
    return result


async def _post_insert_cleanup() -> None:
    """在 store 写入后异步触发的清理动作（在事件循环内持锁执行）。"""
    await _cleanup_store()


def _build_response_data(result: VoxelSimulationResult) -> dict:
    """Build a structured API response dict from simulation results.

    Restructures the raw simulation result into the format expected by
    the API response schema, including collision details and simulation
    metrics.

    Args:
        result: The completed voxel simulation result.

    Returns:
        Dictionary formatted for API response with simulation_result,
        collision_details, and metadata sections.
    """
    base = SimulationResponse.from_result(result).model_dump()
    collision_positions = base.pop("collision_positions")
    collision_segment_indices = base.pop("collision_segment_indices")
    collision_severity = base.pop("collision_severity")
    return {
        **base,
        "collision_detected": base.pop("collision_collided"),
        "simulation_result": {
            "workpiece_stl_path": base.pop("stock_stl_url"),
            "voxel_count": base["voxel_count"],
            "removed_voxel_count": base["removed_voxel_count"],
            "voxel_size": base["voxel_size"],
            "original_bbox": base.pop("original_bbox"),
        },
        "collision_details": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "positions": collision_positions,
            "segment_indices": collision_segment_indices,
            "severity": collision_severity,
            "count": len(collision_positions),
        },
    }


def _default_stock_stl() -> Path:
    """Generate a default rectangular stock STL file.

    Creates a 150x100x40mm box if trimesh is available; otherwise
    returns an empty placeholder file.

    Returns:
        Path to the generated default STL file.
    """
    try:
        import trimesh
    except ImportError:
        fallback = OUTPUT_DIR / "_default_stock.stl"
        if not fallback.exists():
            fallback.write_bytes(b"")
        return fallback

    default_path = OUTPUT_DIR / "_default_stock.stl"
    if default_path.exists():
        return default_path

    box = trimesh.creation.box(extents=[150, 100, 40])
    box.apply_translation([0, 0, 20])
    box.export(str(default_path), file_type="stl")
    return default_path
