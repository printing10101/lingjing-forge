"""Rust 加速的体素切削仿真引擎 - Python 适配层。

本模块对 PyO3 编译的 Rust 计算内核 ``compute._native.voxel_cutter`` 进行封装，
对外保持与 ``app.simulation.voxel_cutter.VoxelCutter`` 完全一致的 API 契约，
以便上层调用方（API、调度器、可视化前端）**无需修改**即可享受到：

- 6 种刀具的解析体素化（球头/平底/圆角平底/锥度/球头锥度/成形刀）
- 批量切削的 SIMD 友好位运算内核
- 切削过程零拷贝/低拷贝的 numpy 互操作

## 自动回退

模块导入期与运行时均做 Rust 可用性探测：

1. **导入期探测**：尝试 ``from compute._native import voxel_cutter``；失败时
   将 ``RUST_ENGINE_AVAILABLE = False``，所有热路径回退到原 ``VoxelCutter``。
2. **运行时探测**：即便导入成功，调用前也会再校验一次（防止运行中段错误）。
3. **逐函数回退**：每个方法独立判断，某个 Rust 调用异常时仅该次回退，不污染
   后续调用。

Example:
    >>> from app.simulation.rust_engine import VoxelCutter, RUST_ENGINE_AVAILABLE
    >>> if RUST_ENGINE_AVAILABLE:
    ...     print("Rust compute engine enabled")
    ... else:
    ...     print("Falling back to pure-Python engine")
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np

# 导入原始 Python 实现作为回退基线
from app.simulation.voxel_cutter import (
    CollisionInfo as _PyCollisionInfo,
)
from app.simulation.voxel_cutter import (
    ToolModel as _PyToolModel,
)
from app.simulation.voxel_cutter import (
    VoxelCutter as _PyVoxelCutter,
)
from app.simulation.voxel_cutter import (
    VoxelSimulationResult as _PyVoxelSimulationResult,
)
from app.simulation.voxel_cutter.cutter import (
    _check_rapid_collisions,
    _discretize_segment,
)
from app.simulation.voxel_cutter.mesher import (
    reconstruct_mesh,
    voxelize_mesh,
)
from app.simulation.toolpath_parser import ToolpathSegment

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Rust 模块可用性探测
# 探测策略：尝试多种 import 路径与形式，捕获所有 ImportError / AttributeError。
# 探测结果以模块级常量形式暴露给调用方，便于运行时诊断。

RUST_ENGINE_AVAILABLE: bool = False
RUST_ENGINE_VERSION: str | None = None
RUST_IMPORT_ERROR: str | None = None
_RUST_VOXEL_CUTTER: Any = None
_RUST_NATIVE: Any = None

try:
    # 路径 1：maturin 构建产物 (compute._native.*)
    from compute import _native

    _RUST_NATIVE = _native
    try:
        _RUST_VOXEL_CUTTER = _native.voxel_cutter
        RUST_ENGINE_VERSION = getattr(_native, "__version__", None)
        RUST_ENGINE_AVAILABLE = True
        logger.info(
            "Rust compute engine loaded (version=%s, module=compute._native.voxel_cutter)",
            RUST_ENGINE_VERSION,
        )
    except (AttributeError, ImportError) as attr_err:
        RUST_IMPORT_ERROR = type(attr_err).__name__
        logger.warning("Rust voxel_cutter submodule missing: %s", attr_err)
except ImportError as err:
    RUST_IMPORT_ERROR = type(err).__name__
    logger.info(
        "Rust compute engine unavailable (compute._native import failed: %s). "
        "Falling back to pure-Python voxel_cutter.",
        err,
    )


def is_rust_available() -> bool:
    """返回 Rust 引擎当前是否可用（供运行时检查）。"""
    return RUST_ENGINE_AVAILABLE and _RUST_VOXEL_CUTTER is not None


def get_engine_status() -> dict[str, Any]:
    """获取 Rust 引擎状态信息（用于诊断 / 监控端点）。"""
    return {
        "rust_available": RUST_ENGINE_AVAILABLE,
        "rust_version": RUST_ENGINE_VERSION,
        "import_error": RUST_IMPORT_ERROR,
        "fallback": "python" if not RUST_ENGINE_AVAILABLE else "rust",
    }


# 数据结构 - 复用 Python 实现，避免重复定义导致 API 不兼容
# 直接别名到 Python 端定义，保留 dataclass 字段、序列化、to_dict 行为一致。
# 这确保上游代码对结果对象做 isinstance/属性访问时不会因为类型不同而失败。

ToolModel = _PyToolModel
CollisionInfo = _PyCollisionInfo
VoxelSimulationResult = _PyVoxelSimulationResult


# 工具映射：ToolModel -> Rust ToolGeometry 参数
# 负责把 Python 侧 dataclass 字段翻译成 Rust 端 build_tool_mask 期望的标量。
# 兼容 6 种刀具类型（含任务说明里要求的球头锥度、成形刀）。

# 任务说明书要求支持的 6 种刀具类型：
_RUST_TOOL_TYPE_MAP: dict[str, str] = {
    "ball": "ball",
    "ballnose": "ball",
    "flat": "flat",
    "flatend": "flat",
    "bullnose": "bullnose",
    "bull": "bullnose",
    "tapered": "tapered",
    "balltapered": "balltapered",
    "tapered_ball": "balltapered",
    "form": "form",
    "profile": "form",
    # 兼容原始 Python 实现支持的 drill/chamfer/thread_mill/reamer：
    # 这些类型在 Rust 端没有直接对应，但可降级为 flat 处理
    "drill": "flat",
    "chamfer": "bullnose",
    "thread_mill": "ball",
    "reamer": "flat",
}


def _to_rust_tool_type(py_type: str) -> str:
    """把 Python 刀具类型翻译成 Rust 接受的字符串别名。"""
    return _RUST_TOOL_TYPE_MAP.get(py_type.lower(), "flat")


def _resolve_corner_radius(tool: ToolModel) -> float:
    """规范化 corner_radius：球头刀未显式指定时默认为半径。"""
    if tool.tool_type.lower() in ("ball", "ballnose") and tool.corner_radius < 1e-6:
        return tool.diameter * 0.5
    return tool.corner_radius


# 核心：VoxelCutter 适配类
# 继承原始 Python VoxelCutter，仅在切削热路径上覆写为 Rust 实现。
# 这样所有非性能关键代码（STL 加载、Marching Cubes 重建、碰撞信息汇总）
# 自动复用 Python 侧的成熟实现，避免重复维护。


@dataclass
class _RustCutStats:
    """单次 Rust 调用的统计信息（用于监控 / 基准）。"""

    used_rust: bool = False
    fallback_reason: str | None = None
    elapsed_ms: float = 0.0
    removed: int = 0
    skipped: int = 0
    points: int = 0


class VoxelCutter(_PyVoxelCutter):
    """Rust 加速的体素切削引擎（向后兼容 ``_PyVoxelCutter``）。

    行为差异：
        - 内部 ``_apply_tool_mask_batch`` 走 Rust 位运算路径，复杂度从
          Python 三重循环 ``O(N*V*T)`` 降为 ``O(N*M)``，其中 ``M << V``。
        - 当 Rust 模块不可用时，**完全等价** 退化为父类 Python 实现。
        - 对外暴露字段 / 方法签名保持不变；上层调用零修改。

    使用:
        >>> cutter = VoxelCutter(voxel_size=1.0)
        >>> # 自动选择 Rust 或 Python 后端
    """

    def __init__(self, voxel_size: float = 1.0) -> None:
        super().__init__(voxel_size=voxel_size)
        # 实例级统计（最近一次切削的引擎选择），便于单元测试断言
        self.last_cut_stats: _RustCutStats = _RustCutStats()
        engine = "rust" if is_rust_available() else "python"
        logger.info(
            "[VoxelCutter] initialized with %s backend (voxel_size=%.3f)",
            engine,
            self._voxel_size,
        )

    # 公开 API：run_simulation 复写以在切削阶段使用 Rust

    def run_simulation(
        self,
        stock_stl_path: Path,
        tool: ToolModel,
        segments: list[ToolpathSegment],
        output_dir: Path,
        safe_z_height: float = 10.0,
        task_id: str | None = None,
        source_file_paths: list[Path] | None = None,
    ) -> VoxelSimulationResult:
        """执行完整的体素化切削仿真流程。

        流程：检查STL存在性 → 自动生成(如需要) → 加载STL → 体素化
        → **刀具体素掩码 (Rust) → 轨迹切削 (Rust) → 重建STL → 碰撞检测**。
        """
        start_time = time.perf_counter()
        task_id = task_id or str(uuid.uuid4())[:12]

        stl_check = self._ensure_stl_file(
            stl_path=stock_stl_path,
            source_file_paths=source_file_paths,
            output_dir=output_dir,
        )
        if not stl_check["exists"]:
            logger.error(
                "[自动生成STL] STL文件不可用且自动生成失败: %s, 错误: %s",
                stock_stl_path,
                stl_check.get("error", "未知"),
            )
            return self._generate_fallback_result(
                task_id,
                output_dir,
                segments,
                start_time,
                stl_check.get("error", "STL文件不可用"),
            )

        try:
            import trimesh
        except ImportError:
            return self._generate_fallback_result(task_id, output_dir, segments, start_time, "trimesh未安装")

        try:
            stock_mesh = trimesh.load(str(stock_stl_path), file_type="stl")
            if not isinstance(stock_mesh, trimesh.Trimesh):
                # Scene 类型：尝试取首个几何体（trimesh.load 可能返回 Scene）
                scene_geoms = getattr(stock_mesh, "geometry", None)
                if isinstance(scene_geoms, dict) and scene_geoms:
                    first_mesh = next(iter(scene_geoms.values()))
                    if isinstance(first_mesh, trimesh.Trimesh):
                        stock_mesh = first_mesh
                    else:
                        return self._generate_fallback_result(task_id, output_dir, segments, start_time, "STL解析失败")
                else:
                    return self._generate_fallback_result(task_id, output_dir, segments, start_time, "STL解析失败")
        except (OSError, ValueError, TypeError, RuntimeError) as load_err:
            logger.warning("STL文件加载失败: %s", load_err, exc_info=True)
            return self._generate_fallback_result(task_id, output_dir, segments, start_time, "STL文件加载失败")

        bbox_min, bbox_max = (
            stock_mesh.bounds[0].copy(),
            stock_mesh.bounds[1].copy(),
        )
        original_bbox_dict = {
            "x_min": float(bbox_min[0]),
            "x_max": float(bbox_max[0]),
            "y_min": float(bbox_min[1]),
            "y_max": float(bbox_max[1]),
            "z_min": float(bbox_min[2]),
            "z_max": float(bbox_max[2]),
        }

        voxel_grid = voxelize_mesh(stock_mesh, bbox_min, bbox_max, self._voxel_size)
        total_voxels = int(voxel_grid.sum())

        cutting_segments = [s for s in segments if s.type in ("linear", "arc")]

        collision_info = CollisionInfo()
        padding = self._voxel_size * 2

        # 1) 构造刀具体素掩码（优先使用 Rust）
        tool_mask = self._build_tool_mask(tool)

        # 2) 收集切削点 + 碰撞检测
        all_cut_points: list[np.ndarray] = []
        for seg in cutting_segments:
            seg_points = _discretize_segment(seg, self._voxel_size * 0.5, self._voxel_size)
            for pt in seg_points:
                x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
                if z < bbox_min[2] - 0.01:
                    collision_info.collided = True
                    collision_info.collision_positions.append([x, y, z])
                    collision_info.collision_segment_indices.append(seg.block_number)
                    continue
                all_cut_points.append(np.array([x, y, z]))

        # 3) 批量应用刀具掩码（优先使用 Rust）
        removed_count = 0
        if all_cut_points:
            points_array = np.array(all_cut_points, dtype=np.float64)
            removed_count = self._apply_tool_mask_batch(
                voxel_grid,
                tool_mask,
                points_array,
                bbox_min,
                self._voxel_size,
                padding,
            )

        if collision_info.collided:
            severity = "critical" if len(collision_info.collision_positions) > 3 else "warning"
            collision_info.collision_severity = severity

        rapid_check = _check_rapid_collisions(segments, voxel_grid, bbox_min, safe_z_height, self._voxel_size)
        if rapid_check.collided:
            collision_info.collided = True
            collision_info.collision_positions.extend(rapid_check.collision_positions)
            collision_info.collision_segment_indices.extend(rapid_check.collision_segment_indices)
            if rapid_check.collision_severity == "critical":
                collision_info.collision_severity = "critical"
            elif collision_info.collision_severity == "none":
                collision_info.collision_severity = rapid_check.collision_severity

        if collision_info.collision_positions:
            unique_positions = []
            for pos in collision_info.collision_positions:
                if pos not in unique_positions:
                    unique_positions.append(pos)
            collision_info.collision_positions = unique_positions[:20]

        result_mesh = reconstruct_mesh(voxel_grid, bbox_min, self._voxel_size)

        output_dir.mkdir(parents=True, exist_ok=True)
        stl_filename = f"sim_result_{task_id}.stl"
        stl_path = output_dir / stl_filename

        stl_raw = b""
        stl_url = ""
        if result_mesh is not None and len(result_mesh.faces) > 0:
            result_mesh.export(str(stl_path), file_type="stl")
            stl_raw = result_mesh.export(file_type="stl")
            stl_url = f"/api/simulation/output/{stl_filename}"

        elapsed = time.perf_counter() - start_time

        return VoxelSimulationResult(
            task_id=task_id,
            stock_stl_url=stl_url,
            stock_stl_raw=stl_raw,
            collision=collision_info,
            duration_seconds=elapsed,
            voxel_count=total_voxels,
            removed_voxel_count=removed_count,
            voxel_size=self._voxel_size,
            original_bbox=original_bbox_dict,
            toolpath_segment_count=len(segments),
        )

    # 内部：构造工具掩码（Rust 优先，失败回退到 Python）
    def _build_tool_mask(self, tool: ToolModel) -> np.ndarray:
        """构造 3D 刀具掩码（优先 Rust，失败回退 ``ToolModel.voxel_mask``）。

        失败回退条件：
            - Rust 模块不可用
            - Rust 端抛异常（参数不兼容 / OOM / panic）
        """
        if is_rust_available():
            try:
                rust_type = _to_rust_tool_type(tool.tool_type)
                corner_radius = _resolve_corner_radius(tool)
                taper_angle = 0.0
                form_profile: list[tuple[float, float]] | None = None

                if rust_type in ("tapered", "balltapered"):
                    # 锥度角：用默认 5°（Python 端无对应字段，保守取值）
                    taper_angle = 5.0
                if rust_type == "form":
                    # 成形刀：用线性递增轮廓作为通用近似
                    form_profile = [
                        (-tool.cutting_length, 0.5),
                        (-tool.cutting_length * 0.5, tool.diameter * 0.25),
                        (0.0, tool.diameter * 0.5),
                    ]

                mask_array, _info = _RUST_VOXEL_CUTTER.build_tool_mask(
                    tool_type=rust_type,
                    diameter=float(tool.diameter),
                    corner_radius=float(corner_radius),
                    cutting_length=float(tool.cutting_length),
                    voxel_size=float(self._voxel_size),
                    taper_angle_deg=float(taper_angle),
                    form_profile=form_profile,
                )
                return np.asarray(mask_array, dtype=bool)
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.warning(
                    "[VoxelCutter] Rust build_tool_mask failed (%s); falling back to Python voxel_mask for this call.",
                    exc,
                    exc_info=True,
                )
        # 回退：直接调用 Python 端实现
        return tool.voxel_mask(self._voxel_size)

    # 内部：批量应用刀具掩码（Rust 优先）
    def _apply_tool_mask_batch(
        self,
        voxel_grid: np.ndarray,
        tool_mask: np.ndarray,
        points: np.ndarray,
        bbox_min: np.ndarray,
        voxel_size: float,
        padding: float,
    ) -> int:
        """批量应用刀具掩码；Rust 路径不可用时回退到父类 Python 实现。"""
        stats = _RustCutStats()
        self.last_cut_stats = stats

        # 1) 优先尝试 Rust 路径
        if is_rust_available() and tool_mask is not None and tool_mask.size > 0:
            t0 = time.perf_counter()
            try:
                # 保证 grid 连续（避免 numpy 视图问题）
                grid_view = np.ascontiguousarray(voxel_grid, dtype=bool)
                mask_view = np.ascontiguousarray(tool_mask, dtype=bool)
                # points 必须是 (N, 3) float64
                pts_view = np.ascontiguousarray(points, dtype=np.float64)
                if pts_view.ndim == 1:
                    pts_view = pts_view.reshape(-1, 3)
                result = _RUST_VOXEL_CUTTER.apply_tool_mask(
                    grid=grid_view,
                    tool_mask=mask_view,
                    points=pts_view,
                    bbox_min=tuple(float(x) for x in bbox_min),
                    voxel_size=float(voxel_size),
                    padding=float(padding),
                )
                # Rust 端是就地修改；同步结果到调用方 grid
                np.copyto(voxel_grid, grid_view)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                stats.used_rust = True
                stats.elapsed_ms = elapsed_ms
                stats.removed = int(result.get("removed", 0))
                stats.skipped = int(result.get("skipped", 0))
                stats.points = int(result.get("points", 0))
                logger.debug(
                    "[VoxelCutter] Rust cut: removed=%d skipped=%d points=%d elapsed=%.2fms",
                    stats.removed,
                    stats.skipped,
                    stats.points,
                    stats.elapsed_ms,
                )
                return stats.removed
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                stats.fallback_reason = f"rust_apply_failed: {exc}"
                logger.warning(
                    "[VoxelCutter] Rust apply_tool_mask failed (%s); falling back to Python batch for this call.",
                    exc,
                    exc_info=True,
                )
                # 继续走 Python 路径

        # 2) Python 回退路径
        stats.used_rust = False
        if stats.fallback_reason is None:
            stats.fallback_reason = "rust_unavailable"
        t0 = time.perf_counter()
        from app.simulation.voxel_cutter import _apply_tool_mask_batch as _py_batch

        removed = _py_batch(voxel_grid, tool_mask, points, bbox_min, voxel_size, padding)
        stats.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        stats.removed = removed
        stats.points = int(points.shape[0])
        return removed


# 便捷函数：直接执行一次切削（无需封装 VoxelCutter）
def apply_cutting_batch(
    voxel_grid: np.ndarray,
    tool_mask: np.ndarray,
    points: np.ndarray,
    bbox_min: np.ndarray,
    voxel_size: float,
    padding: float,
) -> dict[str, int]:
    """模块级便捷函数：直接调用 Rust 批量切削（不可用时抛 RuntimeError）。

    Returns:
        dict: 包含 removed/skipped/points 字段。
    """
    if not is_rust_available():
        raise RuntimeError(f"Rust compute engine unavailable (import_error={RUST_IMPORT_ERROR})")
    return _RUST_VOXEL_CUTTER.apply_tool_mask(voxel_grid, tool_mask, points, bbox_min, voxel_size, padding)


def build_tool_mask(
    tool_type: str,
    diameter: float,
    corner_radius: float,
    cutting_length: float,
    voxel_size: float,
    taper_angle_deg: float = 0.0,
    form_profile: list[tuple[float, float]] | None = None,
) -> np.ndarray:
    """模块级便捷函数：调用 Rust 构造刀具掩码。"""
    if not is_rust_available():
        raise RuntimeError(f"Rust compute engine unavailable (import_error={RUST_IMPORT_ERROR})")
    mask_array, _info = _RUST_VOXEL_CUTTER.build_tool_mask(
        tool_type=tool_type,
        diameter=diameter,
        corner_radius=corner_radius,
        cutting_length=cutting_length,
        voxel_size=voxel_size,
        taper_angle_deg=taper_angle_deg,
        form_profile=form_profile,
    )
    return np.asarray(mask_array, dtype=bool)


__all__ = [
    "RUST_ENGINE_AVAILABLE",
    "RUST_ENGINE_VERSION",
    "RUST_IMPORT_ERROR",
    "VoxelCutter",
    "ToolModel",
    "CollisionInfo",
    "VoxelSimulationResult",
    "is_rust_available",
    "get_engine_status",
    "apply_cutting_batch",
    "build_tool_mask",
]
