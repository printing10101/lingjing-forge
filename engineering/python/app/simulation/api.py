"""Simulation API endpoints for voxel-based machining simulation.

Provides asynchronous execution endpoints for voxel cutting simulation,
supporting large-file simulation computation with background task processing.
Clients can submit simulation requests with tool parameters, G-code, and
stock geometry, then poll for results via task ID.

Configuration sourced from app.config.SimulationConfig:
    _MAX_STORE_SIZE: Maximum number of in-memory cached results.
    _MAX_STORE_AGE_SECONDS: Maximum validity period for cached results.

Example:
    POST /api/simulation/run - Synchronous simulation execution.
    POST /api/simulation/run/async - Asynchronous simulation submission.
    GET /api/simulation/status/{task_id} - Query task status and results.
    GET /api/simulation/output/{filename} - Download STL output file.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from app.utils.utils import sanitize_filename

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import Response
from fastapi.responses import StreamingResponse

from app.auth.dependencies import get_current_user
from app.config import config
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.core.error_taxonomy import (
    ManufacturingError,
    ErrorCategory,
)
from app.simulation.voxel_cutter import (
    VoxelSimulationResult,
    GeometryDiffer,
    DiffResult,
)
from app.simulation.toolpath_parser import ToolpathParser

logger = logging.getLogger(__name__)

# V3.0 拆分：Pydantic 模型 schemas.py，辅助函数 _helpers.py
from .schemas import (
    SimulationRequest,
    ConflictCheckRequest,
    ExportAnimationRequest,
    AutoDiffCompareRequest,
    FEMSolveRequest,
)
from ._helpers import (
    _validate_user_path,
    _get_store_lock,
    _run_simulation,
    _post_insert_cleanup,
    _build_response_data,
    _in_memory_store,
    _project_id_map,
    _completed_at_map,
    _active_tasks,
)

router = APIRouter(prefix="/api/simulation", tags=["Simulation"])

OUTPUT_DIR = Path(config.storage.output_dir) / "simulation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Allowed base directories for user-provided file path validation.
# These prevent path traversal attacks by restricting file access to
# known output and upload directories where files are legitimately stored.


@router.post("/run")
async def run_simulation(
    request: SimulationRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Run voxel cutting simulation synchronously.

    Accepts project ID, tool parameters, G-code input, and stock geometry.
    Executes the simulation in a worker thread (asyncio.to_thread) and
    returns the complete result including machined STL URL and collision
    detection data.

    Args:
        request: Simulation request parameters.

    Returns:
        Standard API response with simulation result data.

    Raises:
        MemoryError: If simulation exceeds available memory.
    """
    task_id = f"sim_{uuid.uuid4().hex[:12]}"

    if request.stock_stl_path:
        _validate_user_path(request.stock_stl_path, "stock_stl_path")
        stl_path = Path(request.stock_stl_path)
        if not stl_path.exists():
            source_path = None
            if request.source_file_path:
                _validate_user_path(request.source_file_path, "source_file_path")
                source_path = Path(request.source_file_path)
            if source_path is not None and not source_path.exists():
                logger.error(
                    "[Auto-generate STL] Both STL and source file not found: STL=%s, source=%s",
                    stl_path,
                    source_path,
                )
                return error(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message="STL file and source file both not found.",
                    detail={
                        "stl_path": str(stl_path),
                        "source_file_path": str(source_path),
                        "error_type": "STL_FILE_MISSING",
                    },
                    suggestion="Please generate the stock STL via STEP/DXF import first.",
                    severity="error",
                    recoverable=True,
                )

    try:
        start = time.perf_counter()
        result = await asyncio.to_thread(_run_simulation, task_id, request)
        elapsed = time.perf_counter() - start

        logger.info(
            "Simulation %s completed in %.2fs, collision=%s, removed=%d voxels",
            task_id,
            elapsed,
            result.collision.collided,
            result.removed_voxel_count,
        )

        # 修复 [并发安全]：在事件循环内触发异步 cleanup，统一淘汰过期/超额结果。
        await _post_insert_cleanup()

        response_data = _build_response_data(result)
        return success(
            data=response_data,
            message="Simulation completed.",
        )
    except MemoryError:
        logger.exception("Simulation %s failed: memory error", task_id)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="Insufficient memory. Increase voxel size or simplify the model.",
            detail={"task_id": task_id},
            recoverable=True,
        )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # 兜底捕获：仿真任务涉及网格运算、IO、序列化等多环节，
        # 任何未预期异常都需包装为统一错误响应以便上层处理；
        # 此处位于 API handler 入口，必须捕获所有异常以避免 5xx 直接抛给客户端。
        # 修复：避免 str(exc) 直接暴露内部异常详情，使用 safe_error_message 包装。
        safe = safe_error_message(exc, context=f"simulation.run[{task_id}]")
        logger.exception("Simulation %s failed: %s", task_id, exc)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={
                "task_id": task_id,
                "error_id": safe.get("error_id"),
                **({"error_type": type(exc).__name__} if safe.get("detail") else {}),
            },
            recoverable=True,
        )


@router.post("/run/async")
async def run_simulation_async(
    request: SimulationRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Start voxel cutting simulation asynchronously.

    Returns immediately with a task ID. The simulation runs in the
    background. Poll /api/simulation/status/{task_id} for progress
    and results.

    Args:
        request: Simulation request parameters.

    Returns:
        Standard API response with task_id and initial status.
    """
    task_id = f"sim_{uuid.uuid4().hex[:12]}"

    # Validate user-provided paths before scheduling the background task
    if request.stock_stl_path:
        _validate_user_path(request.stock_stl_path, "stock_stl_path")
    if request.source_file_path:
        _validate_user_path(request.source_file_path, "source_file_path")

    _in_memory_store[task_id] = VoxelSimulationResult(task_id=task_id)
    # 修复：异步任务在提交时立即预占 _in_memory_store 槽位，同时记录 project_id 映射。
    _project_id_map[task_id] = request.project_id

    async def _async_wrapper() -> None:
        try:
            await asyncio.to_thread(_run_simulation, task_id, request)
        except (KeyboardInterrupt, SystemExit):
            # 后台任务不响应中断信号，向上抛出
            raise
        except asyncio.CancelledError:
            # 修复 [资源清理]：客户端主动取消时显式释放 store 槽位，
            # 避免轮询接口看到 "running" 但实际任务已死。
            async with _get_store_lock():
                _in_memory_store.pop(task_id, None)
                _project_id_map.pop(task_id, None)
                _completed_at_map.pop(task_id, None)
            logger.info("Async simulation %s cancelled", task_id)
            raise
        except (ValueError, TypeError, KeyError, OSError) as exc:
            # 修复 [状态同步]：异常时仍记录完成时间戳但保持 duration_seconds == 0
            # 以便轮询端点将 status 识别为 failed；同时记录 error_id 供排障。
            async with _get_store_lock():
                _completed_at_map[task_id] = time.time()
            # 兜底捕获：后台任务线程内异常不应向上冒泡以免污染 FastAPI 事件循环，
            # 此处仅记录日志，由前端通过状态查询接口轮询获取结果。
            logger.exception("Async simulation %s failed: %s", task_id, exc)
        finally:
            # 修复 [任务生命周期]：无论任务成功/失败/取消，都清理活跃任务引用。
            _active_tasks.pop(task_id, None)
            # 触发异步清理（在事件循环内执行）。
            try:
                await _post_insert_cleanup()
            except (OSError, RuntimeError):
                logger.exception("Background cleanup failed for %s", task_id)

    # 修复：原实现 background_tasks.add_task(asyncio.create_task, _async_wrapper())
    # 存在严重问题——asyncio.create_task 需要运行中的事件循环，而 FastAPI 的
    # BackgroundTasks 在响应发送后才会执行，此时可能没有可用的循环上下文。
    # 正确做法是在当前请求协程中直接创建任务，由事件循环调度执行。
    # 同时将 task 引用保存到 _active_tasks，避免被 GC 提前回收导致中途取消。
    task = asyncio.create_task(_async_wrapper(), name=f"sim-{task_id}")
    _active_tasks[task_id] = task

    return success(
        data={"task_id": task_id, "status": "pending"},
        message="Simulation task submitted. Query /api/simulation/status/{task_id} for progress.",
    )


@router.get("/status/{task_id}")
async def get_simulation_status(
    task_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Query simulation task status and results.

    Args:
        task_id: The simulation task identifier.

    Returns:
        Standard API response with task status, progress, and result data.
    """
    result = _in_memory_store.get(task_id)

    if result is None:
        return success(
            data={
                "task_id": task_id,
                "status": "not_found",
                "progress": 0.0,
                "result": None,
            },
            message="Task not found or expired.",
        )

    is_complete = result.duration_seconds > 0 or result.voxel_count > 0
    status = "completed" if is_complete else "running"

    progress = 1.0 if is_complete else 0.5

    return success(
        data={
            "task_id": result.task_id,
            "status": status,
            "progress": progress,
            "result": (_build_response_data(result) if is_complete else None),
        },
        message="OK",
    )


@router.get("/output/{filename}")
async def get_simulation_output(
    filename: str,
    current_user: dict = Depends(get_current_user),
) -> Response:
    """Serve simulation output STL file.

    Args:
        filename: The STL filename to serve.

    Returns:
        Binary STL file stream for download.

    Raises:
        HTTPException: 400 if the file path is invalid (净化或验证失败);
                       404 if the STL file does not exist.

    [路径遍历修复] 增加了双重路径验证：
    1. 通过 sanitize_filename 拒绝包含路径分隔符或 ".." 的输入；
    2. 通过 resolve() + is_relative_to() 确保最终路径严格位于 OUTPUT_DIR 内。
    """
    # [路径遍历修复] 第一层：用户输入净化
    safe_name = sanitize_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="无效的文件名")

    # [路径遍历修复] 第二层：解析为绝对路径并验证在允许目录内
    allowed_dir = OUTPUT_DIR.resolve()
    file_path = (OUTPUT_DIR / safe_name).resolve()
    if not file_path.is_relative_to(allowed_dir):
        raise HTTPException(status_code=400, detail="无效的文件路径")

    # 保留原有的文件存在性检查
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="STL file not found.")

    return Response(
        content=file_path.read_bytes(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/history")
async def get_simulation_history(
    project_id: str | None = Query(default=None, description="Filter by project ID."),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of records."),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Query simulation history records.

    Args:
        project_id: Optional project ID filter.
        limit: Maximum number of records to return.

    Returns:
        List of simulation history records with metadata.
    """
    history = []
    # 修复：先按 project_id 过滤，再按完成时间倒序排序，最后截取 limit 条。
    # 旧实现 list(...items())[:limit] 顺序随机，limit 在过滤前应用会导致过滤失效。
    items = list(_in_memory_store.items())
    if project_id is not None:
        items = [(tid, r) for tid, r in items if _project_id_map.get(tid) == project_id]
    # 按完成时间倒序（最新优先）
    # 修复 [潜在崩溃]：原代码访问 r.completed_at.timestamp() 会触发 AttributeError，
    # 改用 _completed_at_map 单独维护时间戳。
    items.sort(
        key=lambda kv: _completed_at_map.get(kv[0], 0.0),
        reverse=True,
    )
    for task_id, result in items[:limit]:
        history.append(
            {
                "task_id": result.task_id,
                "project_id": _project_id_map.get(task_id, ""),
                "duration_seconds": result.duration_seconds,
                "collision_collided": result.collision.collided,
                "voxel_size": result.voxel_size,
                "segment_count": result.toolpath_segment_count,
            }
        )

    return success(
        data={
            "total": len(history),
            "items": history,
        },
        message="OK",
    )


@router.delete("/result/{task_id}")
async def delete_simulation_result(
    task_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Delete a simulation result from cache and disk.

    Removes the in-memory result entry and deletes the associated
    STL output file if present.

    Args:
        task_id: The simulation task identifier to delete.

    Returns:
        Standard API response confirming deletion.
    """
    if task_id in _in_memory_store:
        del _in_memory_store[task_id]
    # 修复 [资源清理]：删除结果时同步清理所有关联映射。
    _project_id_map.pop(task_id, None)
    _completed_at_map.pop(task_id, None)

    stl_file = OUTPUT_DIR / f"sim_result_{task_id}.stl"
    if stl_file.exists():
        stl_file.unlink()

    return success(message=f"Simulation result {task_id} deleted.")


@router.post("/check-conflict")
async def check_tool_slot_conflict(
    request: ConflictCheckRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Check tool-slot diameter compatibility.

    Validates whether the specified tool can physically fit within
    the given slot width for machining.

    Args:
        request: Tool-slot conflict check parameters.

    Returns:
        Standard API response with compatibility result.

    Raises:
        ManufacturingError: If tool diameter exceeds slot width.
    """
    tool_d = request.tool_diameter
    slot_w = request.slot_width

    if tool_d > slot_w:
        raise ManufacturingError(
            category=ErrorCategory.NO_SUITABLE_TOOL,
            detail=(
                f"Tool diameter ({tool_d}mm) exceeds slot width ({slot_w}mm). "
                f"Cannot enter the slot for machining. Material: {request.material}, "
                f"Operation: {request.operation}"
            ),
            suggestion=(
                f"Tool diameter ({tool_d}mm) exceeds slot width ({slot_w}mm) limit. "
                f"Suggested solutions: "
                f"1) Use a smaller tool (diameter <= {slot_w}mm); "
                f"2) Change to multi-pass or layered machining strategy; "
                f"3) Redesign the part to widen the slot to >= {tool_d}mm."
            ),
            recoverable=False,
        )

    return success(
        data={
            "compatible": True,
            "tool_diameter": tool_d,
            "slot_width": slot_w,
        },
        message="Tool and slot are compatible, no conflicts.",
    )


def _render_toolpath_animation(nc_code: str, fmt: str, tool_diameter: float) -> tuple["io.BytesIO", str, str]:
    """同步渲染刀具路径动画（GIF/MP4），供 asyncio.to_thread 在工作线程调用。

    性能：历史轨迹采用"增量历史层"——已经过的段按 50% 亮度逐段累积到常驻
    图层，每帧只拷贝图层再画当前亮段，总绘制量 O(段数) 而非 O(帧数×段数)。

    Raises:
        ValueError: G 代码无可识别运动段。
    """
    import io

    import numpy as np
    from PIL import Image, ImageDraw

    parser = ToolpathParser(controller_type="fanuc")
    segments = parser.parse_gcode(nc_code) if nc_code.strip() else []

    if not segments:
        raise ValueError("No valid toolpath segments found in G-code")

    def map_coord(x: float, y: float, z: float) -> tuple[int, int]:
        canvas_x = int(100 + (x + 100) * 3)
        canvas_y = int(500 - (z * 5))
        return canvas_x, canvas_y

    colors = {
        "rapid": (244, 67, 54),
        "linear": (76, 175, 80),
        "arc": (33, 150, 243),
        "dwell": (255, 193, 7),
    }

    def faded(color: tuple[int, int, int]) -> tuple[int, int, int]:
        return (int(color[0] * 0.5), int(color[1] * 0.5), int(color[2] * 0.5))

    num_frames = min(len(segments), 50)  # Limit to 50 frames for performance
    frame_indices = np.linspace(0, len(segments) - 1, num_frames, dtype=int)

    # 常驻历史图层：底色 + 毛坯框 + 已经过的段（50% 亮度）
    history = Image.new("RGB", (800, 600), color=(26, 26, 46))
    history_draw = ImageDraw.Draw(history)
    history_draw.rectangle([100, 100, 700, 500], outline=(100, 100, 100), width=2)

    frames: list = []
    drawn_upto = -1  # history 已包含 segments[0..drawn_upto]
    for idx in frame_indices:
        while drawn_upto < idx - 1:
            drawn_upto += 1
            prev_seg = segments[drawn_upto]
            history_draw.line(
                [map_coord(*prev_seg.start_point), map_coord(*prev_seg.end_point)],
                fill=faded(colors.get(prev_seg.type, (200, 200, 200))),
                width=2,
            )

        img = history.copy()
        draw = ImageDraw.Draw(img)

        segment = segments[idx]
        line_color = colors.get(segment.type, (200, 200, 200))

        # Draw current segment (bright)
        draw.line(
            [map_coord(*segment.start_point), map_coord(*segment.end_point)],
            fill=line_color,
            width=3,
        )

        # Draw tool position
        end_2d = map_coord(*segment.end_point)
        tool_radius = max(int(tool_diameter / 2), 1)
        draw.ellipse(
            [end_2d[0] - tool_radius, end_2d[1] - tool_radius, end_2d[0] + tool_radius, end_2d[1] + tool_radius],
            fill=(255, 255, 0),
            outline=(255, 200, 0),
            width=2,
        )

        # Add frame info
        draw.text((10, 10), f"Frame {idx + 1}/{num_frames}", fill=(200, 200, 200))
        draw.text((10, 30), f"Segment: {segment.type}", fill=line_color)

        frames.append(img)

    buffer = io.BytesIO()

    if fmt == "gif":
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=100,  # 100ms per frame
            loop=0,
        )
        media_type = "image/gif"
        filename = f"simulation_{uuid.uuid4().hex[:8]}.gif"
    else:  # mp4
        # For MP4, we'll use imageio if available, otherwise fallback to GIF
        try:
            import imageio

            frames_array = [np.array(frame) for frame in frames]
            # imageio.v2.mimsave 接受 fps 参数（v3 默认模块的 stub 不完整）
            imageio.v2.mimsave(buffer, frames_array, format="MP4", fps=10)  # type: ignore[call-overload]
            media_type = "video/mp4"
            filename = f"simulation_{uuid.uuid4().hex[:8]}.mp4"
        except ImportError:
            # Fallback to GIF if imageio not available
            frames[0].save(
                buffer,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=100,
                loop=0,
            )
            media_type = "image/gif"
            filename = f"simulation_{uuid.uuid4().hex[:8]}.gif"

    buffer.seek(0)
    return buffer, media_type, filename


@router.post("/export-animation")
async def export_simulation_animation(
    request: ExportAnimationRequest,
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """Export simulation animation as GIF or MP4.

    Generates a frame-by-frame animation of the machining simulation
    showing tool movement and material removal. Returns the animation
    file as a streaming download.

    Args:
        request: Animation export parameters.

    Returns:
        StreamingResponse with animation file content.

    Raises:
        HTTPException: 400 if animation generation fails.
    """
    try:
        # 渲染与编码为 CPU 密集型（PIL 逐帧绘制 + GIF/MP4 编码），
        # 必须放工作线程，避免阻塞事件循环（与 /run、auto-diff 同口径）
        buffer, media_type, filename = await asyncio.to_thread(
            _render_toolpath_animation,
            request.nc_code,
            request.format,
            request.tool_diameter,
        )

        return StreamingResponse(
            buffer,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )

    except Exception as e:
        logger.exception("Animation export failed: %s", e)
        raise HTTPException(
            status_code=400,
            detail="动画生成失败，请检查参数或稍后重试",
        ) from e


# Auto-Diff 几何比对（VERICUT 式残料 / 过切检测）

# 比对结果缓存（task_id -> DiffResult），与 _in_memory_store 同生命周期
_diff_result_store: dict[str, DiffResult] = {}


@router.post("/auto-diff/compare")
async def auto_diff_compare(
    request: AutoDiffCompareRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """比对设计模型与仿真切削结果，识别过切与残料。

    竞品对标：VERICUT Auto-Diff。接受设计 STL 与仿真结果 STL，
    体素化后逐体素异或，输出过切/残料体积、质心、最大深度、
    verdict（accept/warning/reject）以及偏差可视化 STL。

    Args:
        request: 比对请求参数。

    Returns:
        标准 API 响应，data 字段为 DiffResult 序列化结果。
    """
    # 路径校验：防止路径遍历
    try:
        design_path = _validate_user_path(request.design_stl_path, "design_stl_path")
        actual_path = _validate_user_path(request.actual_stl_path, "actual_stl_path")
    except HTTPException as exc:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(exc.detail),
            recoverable=True,
        )

    if not design_path.exists():
        return error(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"设计 STL 文件不存在: {design_path}",
            recoverable=True,
        )
    if not actual_path.exists():
        return error(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"仿真结果 STL 文件不存在: {actual_path}",
            recoverable=True,
        )

    # 构造 GeometryDiffer 实例（仅在调用时创建，避免 import 期开销）
    differ_kwargs: dict[str, Any] = {
        "voxel_size": request.voxel_size,
    }
    if request.gouge_warn_ratio is not None:
        differ_kwargs["gouge_warn_ratio"] = request.gouge_warn_ratio
    if request.gouge_reject_ratio is not None:
        differ_kwargs["gouge_reject_ratio"] = request.gouge_reject_ratio
    if request.leftover_warn_ratio is not None:
        differ_kwargs["leftover_warn_ratio"] = request.leftover_warn_ratio
    if request.leftover_reject_ratio is not None:
        differ_kwargs["leftover_reject_ratio"] = request.leftover_reject_ratio

    try:
        differ = GeometryDiffer(**differ_kwargs)
        result = await asyncio.to_thread(
            differ.compare,
            design_path,
            actual_path,
            OUTPUT_DIR,
            None,
            request.export_diff_stl,
        )
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        safe = safe_error_message(exc, context="auto_diff.compare")
        logger.exception("Auto-Diff 比对失败: %s", exc)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")},
            recoverable=True,
        )

    # 缓存结果
    _diff_result_store[result.task_id] = result
    # 限制缓存大小（保留最近 50 条）
    if len(_diff_result_store) > 50:
        oldest = next(iter(_diff_result_store))
        _diff_result_store.pop(oldest, None)

    return success(
        data=result.to_dict(),
        message=f"几何比对完成，判定：{result.verdict}",
    )


@router.get("/auto-diff/{task_id}")
async def get_auto_diff_result(
    task_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """按 task_id 查询 Auto-Diff 比对结果。

    Args:
        task_id: 比对任务 ID。

    Returns:
        标准 API 响应，data 字段为 DiffResult 序列化结果。
    """
    result = _diff_result_store.get(task_id)
    if result is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"未找到比对结果：{task_id}",
            recoverable=False,
        )
    return success(data=result.to_dict(), message="OK")


# FEM 求解（简化解析模型，教学演示级）


@router.post("/fem/solve")
async def fem_solve(
    request: FEMSolveRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """简化 FEM 求解（简支梁三点弯曲解析解）。

    真实计算（非写死数据，结果随输入参数变化）：
    - 最大弯曲应力 sigma_max = M*c / I，M = F*L/4（集中载荷跨中）
    - 最大挠度 delta = F*L^3 / (48*E*I)
    - 安全系数 n = yield_strength / sigma_max

    说明：该模型为教学演示级解析解，用于参数敏感性分析；
    精确有限元分析请使用专用 CAE 工具。
    """
    try:
        e_pa = request.elastic_modulus * 1e9  # GPa -> Pa
        l_m = request.beam_length * 1e-3  # mm -> m
        b_m = request.beam_width * 1e-3
        h_m = request.beam_height * 1e-3
        inertia = b_m * h_m**3 / 12.0
        if inertia <= 0:
            raise ValueError("试件截面惯性矩必须大于 0")

        bending_moment = request.load_force * l_m / 4.0
        sigma_max = bending_moment * (h_m / 2.0) / inertia / 1e6  # Pa -> MPa
        delta_mm = (request.load_force * l_m**3 / (48.0 * e_pa * inertia)) * 1e3  # m -> mm

        safety = request.yield_strength / sigma_max if sigma_max > 0 else float("inf")

        # 节点应力分布：沿梁长 11 个采样点，应力从两端 0 线性增至跨中最大值
        n_nodes = 11
        distribution = []
        for i in range(n_nodes):
            ratio = i / (n_nodes - 1)
            sigma = sigma_max * (1.0 - abs(2.0 * ratio - 1.0))
            distribution.append(
                {
                    "x": round(ratio * request.beam_length, 1),
                    "stress": round(sigma, 2),
                }
            )

        return success(
            data={
                "material": request.material,
                "mesh_type": request.mesh_type,
                "element_size": request.element_size,
                "adaptive_refinement": request.adaptive_refinement,
                "beam": {
                    "length": request.beam_length,
                    "width": request.beam_width,
                    "height": request.beam_height,
                },
                "load_force": request.load_force,
                "nodes": n_nodes,
                "max_stress": round(sigma_max, 2),
                "max_deflection": round(delta_mm, 4),
                "yield_strength": request.yield_strength,
                "safety_factor": round(safety, 2) if safety < 1e6 else 999.0,
                "status": "ok",
                "stress_distribution": distribution,
                "warning": "简化解析模型（三点弯曲），用于教学演示与参数敏感性分析，非完整有限元分析",
            },
            message="FEM 求解完成",
        )
    except (ValueError, ZeroDivisionError) as e:
        logger.warning("FEM 求解参数错误: %s", e)
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except Exception as e:
        safe = safe_error_message(e, context="simulation.fem_solve", fallback="FEM 求解失败，请检查参数")
        logger.error("[simulation.fem_solve] error_id=%s: %s", safe["error_id"], e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"])


# 仿真工厂闭环（Phase 3b 升级：① SUPCON Factory Agent 思路 API 化）


@router.post("/factory/closed-loop")
async def factory_closed_loop(
    n_parts: int = Query(5, ge=1, le=100, description="目标产量（件）"),
    max_ticks: int = Query(800, ge=10, le=10000, description="最大仿真 tick 数"),
    seed: int = Query(42, description="随机种子（确定性可复现）"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """运行仿真工厂闭环生产（感知→决策→执行→反馈），返回 NLDF 风格 KPI 评分。

    依赖 mcp_server 沙盒；不可用时返回 503。
    """
    try:
        from app.simulation.factory_bridge import run_factory_closed_loop

        report = await asyncio.to_thread(run_factory_closed_loop, n_parts, max_ticks, seed)
    except Exception as e:
        safe = safe_error_message(e, context="simulation.factory_closed_loop", fallback="仿真工厂闭环运行失败")
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"])

    if report is None:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="仿真工厂沙盒不可用（mcp_server 未安装/未在 sys.path）",
        )
    if "error" in report:
        return error(code=ErrorCode.INTERNAL_ERROR, message=report["error"])
    return success(data=report, message="仿真工厂闭环生产完成")


@router.get("/factory/demo-status")
async def factory_demo_status(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """返回仿真演示设备清单（Phase 2 demo registry）。"""
    try:
        from app.simulation.factory_bridge import get_factory_demo_status

        devices = await asyncio.to_thread(get_factory_demo_status)
    except Exception as e:
        safe = safe_error_message(e, context="simulation.factory_demo_status", fallback="获取仿真设备状态失败")
        return error(code=ErrorCode.INTERNAL_ERROR, message=safe["message"])
    if devices is None:
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="仿真工厂沙盒不可用（mcp_server 未安装/未在 sys.path）",
        )
    return success(data=devices, message="仿真设备清单")
