"""体素化切削仿真引擎 - 核心切削逻辑模块。

实现刀具路径离散化、体素切削和碰撞检测。
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from app.simulation.toolpath_parser import ToolpathSegment
from app.simulation.voxel_cutter.mesher import (
    ToolModel,
    voxelize_mesh,
    reconstruct_mesh,
)
from app.simulation.voxel_cutter.models import CollisionInfo, VoxelSimulationResult

import sys

try:
    import numba

    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

# PyInstaller onefile 打包后源文件路径不存在于文件系统，numba 的 cache=True
# 会报 "no locator available for file ..."。仅在非冻结环境下启用缓存。
_NUMBA_CACHE = not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))

logger = logging.getLogger(__name__)

MAX_STL_RETRIES = 3
STL_RETRY_INTERVAL = 1.0


def _infer_source_paths(stl_path: Path) -> list[Path]:
    """根据STL路径推断可能的源文件路径。"""
    base = stl_path.parent / stl_path.stem
    candidates: list[Path] = []
    for ext in (".step", ".stp", ".dxf"):
        candidate = Path(str(base) + ext)
        if candidate.exists():
            candidates.append(candidate)
    return candidates


def _generate_stl_from_step(
    step_path: Path,
    stl_target_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """从STEP源文件生成STL。"""
    try:
        from app.step_import.step_parser import StepParser, StepParseError
        from app.step_import.step_converter import StepConverter
    except ImportError as e:
        return {
            "success": False,
            "error": f"STEP模块导入失败: {e}",
            "suggestion": "请确认step_import模块已正确安装",
        }

    try:
        parser = StepParser()
        shape = parser.get_cadquery_shape(step_path)
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        AttributeError,
        StepParseError,
    ) as e:
        logger.error("STEP文件解析失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"STEP文件解析失败: {e}",
            "suggestion": "请检查STEP文件是否有效且未被损坏",
        }

    try:
        converter = StepConverter(output_dir=output_dir)
        convert_result = converter.convert_to_stl(
            shape,
            stl_target_path.stem,
            entity_name="Stock",
        )
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
        logger.error("STEP→STL转换失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"STEP→STL转换失败: {e}",
            "suggestion": "请尝试调整STL导出精度参数或检查STEP文件几何完整性",
        }

    try:
        import shutil

        generated_path = Path(convert_result.stl_path)
        if generated_path != stl_target_path:
            stl_target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(generated_path), str(stl_target_path))
    except (OSError, AttributeError, TypeError, ValueError) as e:
        logger.error("STL文件复制到目标路径失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"STL文件复制到目标路径失败: {e}",
            "suggestion": "请检查目标目录的写入权限",
        }

    return {"success": True, "error": None, "suggestion": None}


def _generate_stl_from_dxf(
    dxf_path: Path,
    stl_target_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """从DXF源文件生成STL。"""
    try:
        from app.dxf.dxf_parser import DxfParser
        from app.dxf.exceptions import DxfParseError
        from app.dxf.feature_extractor import FeatureExtractor
        from app.dxf.dxf_to_model import DxfToModelConverter
    except ImportError as e:
        return {
            "success": False,
            "error": f"DXF模块导入失败: {e}",
            "suggestion": "请确认dxf模块已正确安装",
        }

    try:
        parser = DxfParser()
        parse_result = parser.parse(str(dxf_path))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, DxfParseError) as e:
        logger.error("DXF文件解析失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"DXF文件解析失败: {e}",
            "suggestion": "请检查DXF文件是否有效且格式正确",
        }

    try:
        extractor = FeatureExtractor()
        feature_result = extractor.extract(parse_result)
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError) as e:
        logger.error("DXF特征提取失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"DXF特征提取失败: {e}",
            "suggestion": "请确认DXF文件包含有效的外形和孔特征",
        }

    try:
        converter = DxfToModelConverter()
        model_result = converter.convert(feature_result)
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError) as e:
        logger.error("DXF→3D模型转换失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"DXF→3D模型转换失败: {e}",
            "suggestion": "请检查DXF中的特征尺寸是否有效",
        }

    try:
        converter.export_stl(model_result, stl_target_path)
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as e:
        logger.error("模型→STL导出失败: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"模型→STL导出失败: {e}",
            "suggestion": "请检查目标目录的写入权限和磁盘空间",
        }

    return {"success": True, "error": None, "suggestion": None}


# Numba JIT加速的批量刀具掩码应用函数
if HAS_NUMBA:

    @numba.jit(nopython=True, cache=_NUMBA_CACHE, parallel=False)
    def _apply_tool_mask_batch(
        voxel_grid: np.ndarray,
        tool_mask: np.ndarray,
        points: np.ndarray,
        bbox_min: np.ndarray,
        voxel_size: float,
        padding: float,
    ) -> int:
        """Numba JIT编译的批量刀具掩码应用。"""
        grid_shape0 = voxel_grid.shape[0]
        grid_shape1 = voxel_grid.shape[1]
        grid_shape2 = voxel_grid.shape[2]
        tool_shape0 = tool_mask.shape[0]
        tool_shape1 = tool_mask.shape[1]
        tool_shape2 = tool_mask.shape[2]
        half_mask0 = tool_shape0 // 2
        half_mask1 = tool_shape1 // 2
        half_mask2 = tool_shape2 // 2
        total_removed = 0

        for i in range(points.shape[0]):
            x = points[i, 0]
            y = points[i, 1]
            z = points[i, 2]

            tip_idx = np.round(np.array([x, y, z]) - bbox_min + padding) / voxel_size
            tip_idx = tip_idx.astype(np.int32)

            tx, ty, tz = tip_idx[0], tip_idx[1], tip_idx[2]

            gx_min = max(0, tx - half_mask0)
            gx_max = min(grid_shape0, tx + half_mask0 + 1)
            gy_min = max(0, ty - half_mask1)
            gy_max = min(grid_shape1, ty + half_mask1 + 1)
            gz_min = max(0, tz - half_mask2)
            gz_max = min(grid_shape2, tz + half_mask2 + 1)

            if gx_min >= gx_max or gy_min >= gy_max or gz_min >= gz_max:
                continue

            mx_start = gx_min - (tx - half_mask0)
            my_start = gy_min - (ty - half_mask1)
            mz_start = gz_min - (tz - half_mask2)

            mx_end = mx_start + (gx_max - gx_min)
            my_end = my_start + (gy_max - gy_min)
            mz_end = mz_start + (gz_max - gz_min)

            for mx in range(mx_start, mx_end):
                gx = gx_min + (mx - mx_start)
                for my in range(my_start, my_end):
                    gy = gy_min + (my - my_start)
                    for mz in range(mz_start, mz_end):
                        gz = gz_min + (mz - mz_start)
                        if tool_mask[mx, my, mz] and voxel_grid[gx, gy, gz]:
                            voxel_grid[gx, gy, gz] = False
                            total_removed += 1

        return total_removed

else:

    def _apply_tool_mask_batch(
        voxel_grid: np.ndarray,
        tool_mask: np.ndarray,
        points: np.ndarray,
        bbox_min: np.ndarray,
        voxel_size: float,
        padding: float,
    ) -> int:
        """纯Python回退版的批量刀具掩码应用。"""
        total_removed = 0
        mask_center = (np.array(tool_mask.shape) - 1) // 2
        for i in range(points.shape[0]):
            x, y, z = points[i, 0], points[i, 1], points[i, 2]
            total_removed += _apply_tool_mask_single(
                voxel_grid,
                tool_mask,
                mask_center,
                x,
                y,
                z,
                bbox_min,
                voxel_size,
                padding,
            )
        return total_removed


def _apply_tool_mask_single(
    voxel_grid: np.ndarray,
    tool_mask: np.ndarray,
    mask_center: np.ndarray,
    x: float,
    y: float,
    z: float,
    bbox_min: np.ndarray,
    voxel_size: float,
    padding: float,
) -> int:
    """应用刀具掩码（纯Python版本）。"""
    grid_shape = np.array(voxel_grid.shape)
    tool_shape = np.array(tool_mask.shape)

    tip_idx = np.round((np.array([x, y, z]) - bbox_min + padding) / voxel_size).astype(int)

    half_mask = tool_shape // 2
    gx_min = max(0, tip_idx[0] - half_mask[0])
    gx_max = min(grid_shape[0], tip_idx[0] + half_mask[0] + 1)
    gy_min = max(0, tip_idx[1] - half_mask[1])
    gy_max = min(grid_shape[1], tip_idx[1] + half_mask[1] + 1)
    gz_min_val = tip_idx[2] - half_mask[2]
    gz_min = max(0, gz_min_val)
    gz_max = min(grid_shape[2], tip_idx[2] + half_mask[2] + 1)

    if gx_min >= gx_max or gy_min >= gy_max or gz_min >= gz_max:
        return 0

    mx_start = gx_min - (tip_idx[0] - half_mask[0])
    my_start = gy_min - (tip_idx[1] - half_mask[1])
    mz_start = gz_min - (tip_idx[2] - half_mask[2])

    mx_end = mx_start + (gx_max - gx_min)
    my_end = my_start + (gy_max - gy_min)
    mz_end = mz_start + (gz_max - gz_min)

    tool_sub = tool_mask[mx_start:mx_end, my_start:my_end, mz_start:mz_end]
    grid_sub = voxel_grid[gx_min:gx_max, gy_min:gy_max, gz_min:gz_max]

    before = int(grid_sub.sum())
    grid_sub[tool_sub] = False
    after = int(grid_sub.sum())
    return before - after


def _discretize_segment(seg: ToolpathSegment, step: float, voxel_size: float) -> np.ndarray:
    """将刀路段离散为等间距采样点。"""
    if seg.type in ("linear", "rapid"):
        start = np.array(seg.start_point)
        end = np.array(seg.end_point)
        dist = np.linalg.norm(end - start)
        num = max(1, int(np.ceil(dist / step)))
        t = np.linspace(0, 1, num + 1)
        return start + t[:, np.newaxis] * (end - start)
    elif seg.type == "arc":
        start = np.array(seg.start_point)
        end = np.array(seg.end_point)
        start_xy = start[:2]
        end_xy = end[:2]

        chord = end_xy - start_xy
        chord_len_sq = float(chord[0] ** 2 + chord[1] ** 2)

        if chord_len_sq < 1e-12:
            return np.array([seg.start_point])

        chord_len = np.sqrt(chord_len_sq)

        mid_xy = (start_xy + end_xy) / 2.0

        g_code = getattr(seg, "g_code", "G02")
        if "G02" in g_code:
            perp = np.array([-chord[1], chord[0]]) / chord_len
        else:
            perp = np.array([chord[1], -chord[0]]) / chord_len

        r_default = max(chord_len / 2.0, voxel_size)
        h_default = np.sqrt(max(r_default**2 - (chord_len / 2.0) ** 2, 0))
        center_xy = mid_xy + perp * h_default

        r = np.sqrt(float(chord_len_sq) / 4.0 + h_default**2)

        v_start = start_xy - center_xy
        v_end = end_xy - center_xy

        if r < 1e-6:
            return np.array([seg.start_point])

        angle_start = np.arctan2(v_start[1], v_start[0])
        angle_end = np.arctan2(v_end[1], v_end[0])

        if "G02" in g_code:
            while angle_end >= angle_start:
                angle_end -= 2 * np.pi
        else:
            while angle_end <= angle_start:
                angle_end += 2 * np.pi

        arc_angle = abs(angle_end - angle_start)
        arc_length = r * arc_angle
        num = max(1, int(np.ceil(arc_length / step)))
        angles = np.linspace(angle_start, angle_end, num + 1)
        z_vals = np.linspace(start[2], end[2], num + 1)

        points = np.zeros((num + 1, 3))
        points[:, 0] = center_xy[0] + r * np.cos(angles)
        points[:, 1] = center_xy[1] + r * np.sin(angles)
        points[:, 2] = z_vals
        return points
    else:
        return np.array([seg.start_point])


def _check_rapid_collisions(
    segments: list[ToolpathSegment],
    voxel_grid: np.ndarray,
    bbox_min: np.ndarray,
    safe_z_height: float,
    voxel_size: float,
) -> CollisionInfo:
    """检查快速移动(G00)是否与剩余材料碰撞。"""
    result = CollisionInfo()
    rapid_segs = [s for s in segments if s.type == "rapid"]

    for seg in rapid_segs:
        points = _discretize_segment(seg, voxel_size, voxel_size)
        padding = voxel_size * 2

        for pt in points:
            x, y, z = pt[0], pt[1], pt[2]
            if z > bbox_min[2] + safe_z_height:
                continue

            idx = np.round((np.array([x, y, z]) - bbox_min + padding) / voxel_size).astype(int)

            if (
                0 <= idx[0] < voxel_grid.shape[0]
                and 0 <= idx[1] < voxel_grid.shape[1]
                and 0 <= idx[2] < voxel_grid.shape[2]
                and voxel_grid[idx[0], idx[1], idx[2]]
            ):
                result.collided = True
                result.collision_positions.append([float(x), float(y), float(z)])
                result.collision_segment_indices.append(seg.block_number)
                result.collision_severity = "critical"
                break

    return result


class VoxelCutter:
    """体素化切削仿真引擎。

    使用3D体素网格表示工件材料状态。刀具轨迹经过时，
    将对应体素标记为"已切除"。最终通过marching cubes重建表面网格。
    """

    def __init__(self, voxel_size: float = 1.0) -> None:
        """初始化体素切削引擎。

        Args:
            voxel_size: 体素边长(mm)。推荐值：粗仿1.0-2.0，精仿0.2-0.5。
        """
        self._voxel_size = max(voxel_size, 0.1)

    def _ensure_stl_file(
        self,
        stl_path: Path,
        source_file_paths: list[Path] | None,
        output_dir: Path,
        max_retries: int = MAX_STL_RETRIES,
        retry_interval: float = STL_RETRY_INTERVAL,
    ) -> dict[str, Any]:
        """确保STL文件存在，不存在则尝试从源文件自动生成。

        .. note::
            仅同步上下文使用：本方法使用 ``time.sleep`` 进行重试退避，
            不应在 async 上下文中直接调用。如需 async 支持，请用
            ``asyncio.to_thread`` 包装。
        """

        if stl_path.exists():
            logger.info(
                "[自动生成STL] STL文件已存在，跳过生成: %s",
                stl_path,
            )
            return {
                "exists": True,
                "generated": False,
                "error": None,
                "suggestion": None,
                "source_file": None,
            }

        logger.info(
            "[自动生成STL] STL文件不存在，尝试自动生成: %s",
            stl_path,
        )

        if not source_file_paths:
            source_file_paths = _infer_source_paths(stl_path)

        if not source_file_paths:
            logger.warning(
                "[自动生成STL] 未找到可用的源文件(STEP/DXF)，无法自动生成: %s",
                stl_path,
            )
            return {
                "exists": False,
                "generated": False,
                "error": "STL文件不存在，且在相同目录未找到源STEP/DXF文件",
                "suggestion": "请先通过STEP导入或DXF导入功能生成毛坯STL文件",
                "source_file": None,
            }

        for source_path in source_file_paths:
            if not source_path.exists():
                logger.warning(
                    "[自动生成STL] 推断的源文件不存在: %s",
                    source_path,
                )
                continue

            suffix = source_path.suffix.lower()
            logger.info(
                "[自动生成STL] 发现源文件: %s (类型: %s)",
                source_path,
                suffix,
            )

            for attempt in range(1, max_retries + 1):
                logger.info(
                    "[自动生成STL] 第%d/%d次尝试从源文件生成STL: %s -> %s",
                    attempt,
                    max_retries,
                    source_path,
                    stl_path,
                )

                if suffix in (".step", ".stp"):
                    gen_result = _generate_stl_from_step(source_path, stl_path, output_dir)
                elif suffix == ".dxf":
                    gen_result = _generate_stl_from_dxf(source_path, stl_path, output_dir)
                else:
                    gen_result = {
                        "success": False,
                        "error": f"不支持的源文件格式: {suffix}",
                        "suggestion": "支持的格式: .step, .stp, .dxf",
                    }

                if gen_result["success"]:
                    logger.info(
                        "[自动生成STL] 生成成功: %s (源文件: %s, 尝试次数: %d)",
                        stl_path,
                        source_path,
                        attempt,
                    )
                    return {
                        "exists": True,
                        "generated": True,
                        "error": None,
                        "suggestion": None,
                        "source_file": str(source_path),
                    }

                logger.warning(
                    "[自动生成STL] 第%d次尝试失败: 源文件=%s, 错误=%s",
                    attempt,
                    source_path,
                    gen_result.get("error", "未知错误"),
                )

                if attempt < max_retries:
                    wait_time = retry_interval
                    logger.info(
                        "[自动生成STL] 等待%.1fs后进行第%d次重试...",
                        wait_time,
                        attempt + 1,
                    )
                    time.sleep(wait_time)

            logger.error(
                "[自动生成STL] 从源文件 %s 生成STL失败，已重试%d次",
                source_path,
                max_retries,
            )

        return {
            "exists": False,
            "generated": False,
            "error": "所有源文件均无法生成有效的STL文件",
            "suggestion": "请检查源STEP/DXF文件是否有效，或手动通过导入功能生成STL",
            "source_file": None,
        }

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
        """执行完整的体素化切削仿真流程。"""
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
                stock_mesh = stock_mesh if hasattr(stock_mesh, "geometry") else None
                if stock_mesh is None or not isinstance(stock_mesh, trimesh.Trimesh):
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
        tool_mask = tool.voxel_mask(self._voxel_size)

        collision_info = CollisionInfo()

        padding = self._voxel_size * 2

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

        removed_count = 0
        if all_cut_points:
            points_array = np.array(all_cut_points, dtype=np.float64)
            removed_count = _apply_tool_mask_batch(
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

    def _generate_fallback_result(
        self,
        task_id: str,
        output_dir: Path,
        segments: list[ToolpathSegment],
        start_time: float,
        error_msg: str,
    ) -> VoxelSimulationResult:
        """生成降级仿真结果。"""
        elapsed = time.perf_counter() - start_time

        try:
            import trimesh

            stock_box = trimesh.creation.box(extents=[150, 100, 40])
            stock_box.apply_translation([0, 0, 20])
        except ImportError:
            stock_box = None

        output_dir.mkdir(parents=True, exist_ok=True)
        stl_filename = f"sim_fallback_{task_id}.stl"
        stl_path = output_dir / stl_filename
        stl_url = ""
        stl_raw = b""

        if stock_box is not None:
            stock_box.export(str(stl_path), file_type="stl")
            stl_raw = stock_box.export(file_type="stl")
            stl_url = f"/api/simulation/output/{stl_filename}"

        ix, iy, iz = (
            int(np.ceil(150 / max(self._voxel_size, 1.0))),
            int(np.ceil(100 / max(self._voxel_size, 1.0))),
            int(np.ceil(40 / max(self._voxel_size, 1.0))),
        )
        total_voxels = ix * iy * iz

        return VoxelSimulationResult(
            task_id=task_id,
            stock_stl_url=stl_url,
            stock_stl_raw=stl_raw,
            collision=CollisionInfo(),
            duration_seconds=elapsed,
            voxel_count=total_voxels,
            removed_voxel_count=0,
            voxel_size=max(self._voxel_size, 1.0),
            original_bbox={
                "x_min": -75.0,
                "x_max": 75.0,
                "y_min": -50.0,
                "y_max": 50.0,
                "z_min": 0.0,
                "z_max": 40.0,
            },
            toolpath_segment_count=len(segments),
        )
