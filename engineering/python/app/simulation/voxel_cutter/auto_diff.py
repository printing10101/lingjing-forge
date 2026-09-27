"""Auto-Diff 几何比对验证（VERICUT 式残料 / 过切检测）。

竞品分析识别的核心补强点：VERICUT 的三层瀑布验证（几何 → 力学 → 参数优化）中，
几何层 Auto-Diff 是最基础也最关键的一环——比对"设计模型"与"仿真切削结果"，
自动识别两类偏差：

    Gouge（过切）：实际切除了设计模型中仍应存在的材料 → 切多了
        等价于：design_grid 为 True 但 actual_grid 为 False
        严重度：高（破坏工件尺寸，可能报废）

    Leftover（残料）：实际仍残留设计模型中已不存在的材料 → 切少了
        等价于：actual_grid 为 True 但 design_grid 为 False
        严重度：中（需补加工，影响效率与表面质量）

算法流程：
    1. 加载设计 STL（目标工件）→ 体素化 → design_grid
    2. 加载仿真结果 STL（VoxelCutter 切削后工件）→ 体素化 → actual_grid
    3. 统一坐标系与包围盒（取两者 bbox 并集，避免边界体素丢失）
    4. 逐体素异或：diff_grid = design_grid XOR actual_grid
       - gouge_grid = design_grid & ~actual_grid
       - leftover_grid = actual_grid & ~design_grid
    5. 统计体积（体素数 × 单体素体积）、质心、包围盒、最大偏差深度
    6. Marching Cubes 重建偏差网格（可选，用于前端可视化）
    7. 生成 Verdict：accept / warning / reject

设计说明：
    不修改现有 VoxelCutter，作为独立的几何验证层。
    复用 mesher.voxelize_mesh 与 reconstruct_mesh，保证体素分辨率一致性。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.simulation.voxel_cutter.mesher import reconstruct_mesh

logger = logging.getLogger(__name__)


# ── 严重度阈值（基于体积占比，可由调用方覆盖） ──────────────────────────
DEFAULT_GOUGE_REJECT_RATIO = 0.001  # 过切体积 > 设计体积 0.1% → reject
DEFAULT_GOUGE_WARN_RATIO = 0.0001  # 过切体积 > 0.01% → warning
DEFAULT_LEFTOVER_REJECT_RATIO = 0.05  # 残料体积 > 设计体积 5% → reject
DEFAULT_LEFTOVER_WARN_RATIO = 0.01  # 残料体积 > 1% → warning


@dataclass
class DiffRegion:
    """单类偏差区域统计。

    Attributes:
        kind: 偏差类型 - "gouge"（过切）/ "leftover"（残料）
        voxel_count: 偏差体素数
        volume_mm3: 偏差体积（mm³）
        ratio: 偏差体积 / 设计体积
        centroid: 偏差区域质心 [x, y, z]（mm）
        bbox_min: 偏差区域包围盒最小点 [x, y, z]
        bbox_max: 偏差区域包围盒最大点 [x, y, z]
        max_depth_mm: 最大偏差深度（mm），过切=切入设计模型的最大深度，
                      残料=残留材料高出设计表面的最大高度
        severity: 严重度 - "none" / "warning" / "critical"
        sample_positions: 采样偏差位置列表（最多 50 个，用于前端标注）
    """

    kind: str = "gouge"
    voxel_count: int = 0
    volume_mm3: float = 0.0
    ratio: float = 0.0
    centroid: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    bbox_min: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    bbox_max: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    max_depth_mm: float = 0.0
    severity: str = "none"
    sample_positions: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "voxel_count": self.voxel_count,
            "volume_mm3": round(self.volume_mm3, 4),
            "ratio": round(self.ratio, 6),
            "centroid": [round(c, 3) for c in self.centroid],
            "bbox_min": [round(c, 3) for c in self.bbox_min],
            "bbox_max": [round(c, 3) for c in self.bbox_max],
            "max_depth_mm": round(self.max_depth_mm, 4),
            "severity": self.severity,
            "sample_positions": self.sample_positions,
        }


@dataclass
class DiffResult:
    """几何比对完整结果。

    Attributes:
        task_id: 比对任务唯一标识
        verdict: 总体判定 - "accept" / "warning" / "reject"
        gouge: 过切区域统计
        leftover: 残料区域统计
        design_volume_mm3: 设计模型体积（mm³）
        actual_volume_mm3: 仿真结果体积（mm³）
        voxel_size: 体素分辨率（mm）
        duration_seconds: 比对耗时（秒）
        diff_stl_url: 偏差可视化 STL 的 URL（可选）
        diff_stl_raw: 偏差可视化 STL 二进制数据（可选）
        summary: 人类可读的结论摘要
    """

    task_id: str = ""
    verdict: str = "accept"
    gouge: DiffRegion = field(default_factory=lambda: DiffRegion(kind="gouge"))
    leftover: DiffRegion = field(default_factory=lambda: DiffRegion(kind="leftover"))
    design_volume_mm3: float = 0.0
    actual_volume_mm3: float = 0.0
    voxel_size: float = 1.0
    duration_seconds: float = 0.0
    diff_stl_url: str = ""
    diff_stl_raw: bytes = b""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verdict": self.verdict,
            "gouge": self.gouge.to_dict(),
            "leftover": self.leftover.to_dict(),
            "design_volume_mm3": round(self.design_volume_mm3, 4),
            "actual_volume_mm3": round(self.actual_volume_mm3, 4),
            "voxel_size": self.voxel_size,
            "duration_seconds": round(self.duration_seconds, 3),
            "diff_stl_url": self.diff_stl_url,
            "summary": self.summary,
        }


class GeometryDiffer:
    """几何比对引擎（VERICUT Auto-Diff 落地）。

    使用统一的体素网格对齐设计模型与仿真结果，逐体素异或识别偏差区域。
    """

    def __init__(
        self,
        voxel_size: float = 0.5,
        gouge_warn_ratio: float = DEFAULT_GOUGE_WARN_RATIO,
        gouge_reject_ratio: float = DEFAULT_GOUGE_REJECT_RATIO,
        leftover_warn_ratio: float = DEFAULT_LEFTOVER_WARN_RATIO,
        leftover_reject_ratio: float = DEFAULT_LEFTOVER_REJECT_RATIO,
    ) -> None:
        """初始化几何比对引擎。

        Args:
            voxel_size: 体素分辨率（mm）。越小越精确但越慢。推荐 0.2-1.0。
            gouge_warn_ratio: 过切告警阈值（体积占比）
            gouge_reject_ratio: 过切拒收阈值（体积占比）
            leftover_warn_ratio: 残料告警阈值（体积占比）
            leftover_reject_ratio: 残料拒收阈值（体积占比）
        """
        self._voxel_size = max(voxel_size, 0.1)
        self._gouge_warn = gouge_warn_ratio
        self._gouge_reject = gouge_reject_ratio
        self._leftover_warn = leftover_warn_ratio
        self._leftover_reject = leftover_reject_ratio

    def compare(
        self,
        design_stl_path: Path,
        actual_stl_path: Path,
        output_dir: Path | None = None,
        task_id: str | None = None,
        export_diff_stl: bool = True,
    ) -> DiffResult:
        """比对设计模型与仿真切削结果。

        Args:
            design_stl_path: 设计模型（目标工件）STL 路径
            actual_stl_path: 仿真切削结果 STL 路径（VoxelCutter 输出）
            output_dir: 偏差 STL 输出目录（None 则不导出）
            task_id: 任务 ID（None 自动生成）
            export_diff_stl: 是否导出偏差可视化 STL

        Returns:
            DiffResult 完整比对结果
        """
        start_time = time.perf_counter()
        task_id = task_id or str(uuid.uuid4())[:12]

        try:
            import trimesh
        except ImportError as e:
            return self._error_result(task_id, start_time, f"trimesh 未安装: {e}")

        # ── 1. 加载两个 STL ──────────────────────────────────────
        try:
            design_mesh = trimesh.load(str(design_stl_path), file_type="stl")
            actual_mesh = trimesh.load(str(actual_stl_path), file_type="stl")
            if not isinstance(design_mesh, trimesh.Trimesh):
                return self._error_result(task_id, start_time, "设计 STL 解析失败：非 Trimesh")
            if not isinstance(actual_mesh, trimesh.Trimesh):
                return self._error_result(task_id, start_time, "仿真结果 STL 解析失败：非 Trimesh")
        except (OSError, ValueError, TypeError, RuntimeError, AttributeError) as e:
            # AttributeError: trimesh 4.x 对不存在/损坏文件可能抛 AttributeError
            # （如 "'str' object has no attribute 'tell'"），需纳入降级路径
            return self._error_result(task_id, start_time, f"STL 加载失败: {e}")

        # ── 2. 统一坐标系：取两者 bbox 并集 ──────────────────────
        # 避免某个模型的部分体素落在另一模型的 bbox 之外被丢失
        design_bbox_min = design_mesh.bounds[0]
        design_bbox_max = design_mesh.bounds[1]
        actual_bbox_min = actual_mesh.bounds[0]
        actual_bbox_max = actual_mesh.bounds[1]

        union_bbox_min = np.minimum(design_bbox_min, actual_bbox_min)
        union_bbox_max = np.maximum(design_bbox_max, actual_bbox_max)

        # ── 3. 体素化（统一 bbox，保证网格尺寸一致） ─────────────
        # 注意：voxelize_mesh 内部会用 bbox 计算 padding，但实际返回的
        # grid 尺寸取决于 trimesh 的 voxelize 方法。为保证两者对齐，
        # 我们改用自定义的统一网格体素化。
        design_grid = self._voxelize_unified(design_mesh, union_bbox_min, union_bbox_max)
        actual_grid = self._voxelize_unified(actual_mesh, union_bbox_min, union_bbox_max)

        # ── 4. 委托 compare_grids 完成 grid 级比对 ──────────────
        return self.compare_grids(
            design_grid=design_grid,
            actual_grid=actual_grid,
            bbox_min=union_bbox_min,
            output_dir=output_dir,
            task_id=task_id,
            export_diff_stl=export_diff_stl,
            start_time=start_time,
        )

    def compare_grids(
        self,
        design_grid: np.ndarray,
        actual_grid: np.ndarray,
        bbox_min: np.ndarray,
        output_dir: Path | None = None,
        task_id: str | None = None,
        export_diff_stl: bool = True,
        start_time: float | None = None,
    ) -> DiffResult:
        """直接基于体素网格比对（VoxelCutter 集成入口）。

        当 VoxelCutter 已持有切削后的体素网格时，应优先调用本方法而非
        :meth:`compare`，避免 ``grid → STL → grid`` 的双重 Marching Cubes
        重建与重新体素化引入的数值误差（典型场景下边界体素损失可达 5%）。

        Args:
            design_grid: 设计模型体素网格（bool 三维数组）
            actual_grid: 仿真结果体素网格（bool 三维数组，VoxelCutter 输出）
            bbox_min: 网格世界坐标包围盒最小点 [x, y, z]（mm）
            output_dir: 偏差 STL 输出目录（None 则不导出）
            task_id: 任务 ID（None 自动生成）
            export_diff_stl: 是否导出偏差可视化 STL
            start_time: 外部传入的起始时间戳（None 则内部重新计时）

        Returns:
            DiffResult 完整比对结果
        """
        if start_time is None:
            start_time = time.perf_counter()
        task_id = task_id or str(uuid.uuid4())[:12]

        # 形状对齐校验
        if design_grid.shape != actual_grid.shape:
            min_shape = np.minimum(design_grid.shape, actual_grid.shape)
            design_grid = design_grid[: min_shape[0], : min_shape[1], : min_shape[2]]
            actual_grid = actual_grid[: min_shape[0], : min_shape[1], : min_shape[2]]
            logger.warning("设计/仿真体素网格形状不一致，已裁剪到 %s", tuple(min_shape))

        # ── 计算偏差区域 ─────────────────────────────────────
        gouge_grid = design_grid & ~actual_grid  # 设计有但实际没有 → 过切
        leftover_grid = actual_grid & ~design_grid  # 实际有但设计没有 → 残料

        voxel_volume = self._voxel_size**3
        design_volume = float(design_grid.sum()) * voxel_volume
        actual_volume = float(actual_grid.sum()) * voxel_volume

        # ── 统计偏差区域 ─────────────────────────────────────
        gouge = self._summarize_region(gouge_grid, "gouge", design_volume, bbox_min)
        leftover = self._summarize_region(leftover_grid, "leftover", design_volume, bbox_min)

        # ── 判定 verdict ────────────────────────────────────
        verdict = self._compute_verdict(gouge, leftover)

        # ── 导出偏差 STL（可选） ─────────────────────────────
        diff_stl_url = ""
        diff_stl_raw = b""
        if export_diff_stl and output_dir is not None:
            diff_stl_url, diff_stl_raw = self._export_diff_stl(gouge_grid, leftover_grid, bbox_min, output_dir, task_id)

        elapsed = time.perf_counter() - start_time
        summary = self._build_summary(verdict, gouge, leftover, design_volume, actual_volume)

        return DiffResult(
            task_id=task_id,
            verdict=verdict,
            gouge=gouge,
            leftover=leftover,
            design_volume_mm3=design_volume,
            actual_volume_mm3=actual_volume,
            voxel_size=self._voxel_size,
            duration_seconds=elapsed,
            diff_stl_url=diff_stl_url,
            diff_stl_raw=diff_stl_raw,
            summary=summary,
        )

    # 内部实现

    def _voxelize_unified(
        self,
        mesh: "trimesh.Trimesh",  # type: ignore[name-defined]  # noqa: F821  # trimesh 可选依赖延迟导入，字符串注解不求值
        bbox_min: np.ndarray,
        bbox_max: np.ndarray,
    ) -> np.ndarray:
        """在统一 bbox 下体素化网格，保证两个模型网格尺寸一致。

        相比 mesher.voxelize_mesh，这里强制使用传入的 bbox 计算网格尺寸，
        避免 trimesh 内部 voxelize 返回不一致的 matrix。
        """
        padding = self._voxel_size * 2
        extents = bbox_max - bbox_min + padding * 2
        nx = max(1, int(np.ceil(extents[0] / self._voxel_size)))
        ny = max(1, int(np.ceil(extents[1] / self._voxel_size)))
        nz = max(1, int(np.ceil(extents[2] / self._voxel_size)))

        # 优先尝试 trimesh 的 voxelize（更快）
        try:
            from trimesh.voxel import creation as voxel_creation

            voxel_obj = voxel_creation.voxelize(mesh, pitch=self._voxel_size, method="subdivide")
            matrix = voxel_obj.matrix.astype(bool)
            # 裁剪/填充到统一尺寸
            return self._resize_grid(matrix, (nx, ny, nz))
        except (ImportError, ModuleNotFoundError, ValueError, TypeError, RuntimeError) as e:
            logger.debug("trimesh voxelize 失败，回退到 contains: %s", e)

        # 回退：使用 mesh.contains 逐点判断
        return self._voxelize_contains_unified(mesh, bbox_min, padding, nx, ny, nz)

    def _resize_grid(self, grid: np.ndarray, target_shape: tuple[int, int, int]) -> np.ndarray:
        """将体素网格裁剪或填充到目标形状。"""
        src_shape = grid.shape
        result = np.zeros(target_shape, dtype=bool)
        min_shape = tuple(min(s, t) for s, t in zip(src_shape, target_shape))
        result[: min_shape[0], : min_shape[1], : min_shape[2]] = grid[: min_shape[0], : min_shape[1], : min_shape[2]]
        return result

    def _voxelize_contains_unified(
        self,
        mesh: "trimesh.Trimesh",  # type: ignore[name-defined]  # noqa: F821  # trimesh 可选依赖延迟导入，字符串注解不求值
        bbox_min: np.ndarray,
        padding: float,
        nx: int,
        ny: int,
        nz: int,
    ) -> np.ndarray:
        """使用 mesh.contains 在统一网格上体素化。"""
        grid = np.zeros((nx, ny, nz), dtype=bool)
        # 预生成所有体素中心点
        xs = bbox_min[0] - padding + (np.arange(nx) + 0.5) * self._voxel_size
        ys = bbox_min[1] - padding + (np.arange(ny) + 0.5) * self._voxel_size
        zs = bbox_min[2] - padding + (np.arange(nz) + 0.5) * self._voxel_size

        # 分批处理，避免一次性占用过多内存
        points_xy = np.array(np.meshgrid(xs, ys, indexing="ij")).reshape(2, -1).T  # (nx*ny, 2)

        for iz in range(nz):
            z = zs[iz]
            pts = np.column_stack([points_xy, np.full(len(points_xy), z)])
            try:
                inside = mesh.contains(pts)
            except (ValueError, RuntimeError, TypeError) as e:
                logger.debug("contains 调用失败 (z=%s): %s", z, e)
                continue
            inside_grid = inside.reshape(nx, ny)
            grid[:, :, iz] = inside_grid

        return grid

    def _summarize_region(
        self,
        region_grid: np.ndarray,
        kind: str,
        design_volume: float,
        bbox_min: np.ndarray,
    ) -> DiffRegion:
        """统计单个偏差区域。"""
        region_voxel_count = int(region_grid.sum())
        if region_voxel_count == 0:
            return DiffRegion(kind=kind)

        voxel_volume = self._voxel_size**3
        region_volume = region_voxel_count * voxel_volume
        ratio = region_volume / design_volume if design_volume > 0 else 0.0

        # 质心
        active_indices = np.argwhere(region_grid)
        centroid_idx = active_indices.mean(axis=0)
        padding = self._voxel_size * 2
        centroid_world = bbox_min - padding + (centroid_idx + 0.5) * self._voxel_size

        # 包围盒
        min_idx = active_indices.min(axis=0)
        max_idx = active_indices.max(axis=0)
        bbox_min_world = bbox_min - padding + min_idx * self._voxel_size
        bbox_max_world = bbox_min - padding + (max_idx + 1) * self._voxel_size

        # 最大偏差深度：沿偏差主方向的最大连续体素数 × voxel_size
        # 对 gouge：沿 Z 轴（切削深度方向）的最大连续深度
        # 对 leftover：沿 Z 轴（残留高度方向）的最大连续高度
        max_depth = self._compute_max_depth(region_grid) * self._voxel_size

        # 采样位置（最多 50 个，均匀采样）
        if len(active_indices) > 50:
            step = len(active_indices) // 50
            sampled = active_indices[::step][:50]
        else:
            sampled = active_indices
        sample_positions = (bbox_min - padding + (sampled + 0.5) * self._voxel_size).tolist()

        # 严重度判定
        if kind == "gouge":
            if ratio >= self._gouge_reject:
                severity = "critical"
            elif ratio >= self._gouge_warn:
                severity = "warning"
            else:
                severity = "none"
        else:  # leftover
            if ratio >= self._leftover_reject:
                severity = "critical"
            elif ratio >= self._leftover_warn:
                severity = "warning"
            else:
                severity = "none"

        return DiffRegion(
            kind=kind,
            voxel_count=region_voxel_count,
            volume_mm3=region_volume,
            ratio=ratio,
            centroid=centroid_world.tolist(),
            bbox_min=bbox_min_world.tolist(),
            bbox_max=bbox_max_world.tolist(),
            max_depth_mm=max_depth,
            severity=severity,
            sample_positions=sample_positions,
        )

    def _compute_max_depth(self, region_grid: np.ndarray) -> int:
        """计算沿 Z 轴的最大连续体素深度（严格最长连续段）。

        用于估计过切的最大切入深度或残料的最大残留高度。

        算法说明：
            旧实现用 ``region_grid.sum(axis=2).max()`` 近似——当偏差区域
            在某列内存在断续（如 Z=5..7 与 Z=10..14 都是 True，中间 Z=8..9
            为 False）时，旧实现返回 7（总 True 数），但真实最大连续深度
            应为 5（Z=10..14）。这会高估过切深度，误导工艺决策。

            本实现用纯向量化 cumsum + maximum.accumulate 技巧计算每列沿 Z 轴
            的最长连续 True 段，复杂度 O(N)：

            1. ``cum_arr = np.cumsum(arr_int, axis=-1)``：累积 True 计数
            2. ``cum_at_false = np.where(~padded, cum_arr, 0)``：仅在 False 位置保留 cum_arr
            3. ``cum_at_last_false = np.maximum.accumulate(cum_at_false, axis=-1)``：
               每个位置"最近的 False 的 cum_arr 值"
            4. ``reset_cum = cum_arr - cum_at_last_false``：从最近 False 之后的 True 数
            5. ``max_run_per_column = reset_cum.max(axis=-1)``：每列最长段
            6. 返回全局最大值
        """
        if region_grid.size == 0 or not region_grid.any():
            return 0

        # 把 Z 轴（axis=2）移到最后一维，统一处理
        grid_T = np.swapaxes(region_grid, 2, -1)
        n = grid_T.shape[-1]
        if n == 0:
            return 0

        # 前后补 False，保证边界段也能被正确终止
        shape_with_pad = grid_T.shape[:-1] + (n + 2,)
        padded = np.zeros(shape_with_pad, dtype=bool)
        padded[..., 1:-1] = grid_T

        arr_int = padded.astype(np.int32)
        cum_arr = np.cumsum(arr_int, axis=-1)
        # 仅在 False 位置保留 cum_arr，其他位置置 0
        cum_at_false = np.where(~padded, cum_arr, 0)
        # 沿最后一维取每个位置"最近 False 的 cum_arr 值"
        cum_at_last_false = np.maximum.accumulate(cum_at_false, axis=-1)
        # 从最近 False 重置后的 True 累计 = 段内偏移
        reset_cum = cum_arr - cum_at_last_false

        # 每列（x,y）沿 Z 的最长连续 True 段
        max_run_per_column = reset_cum.max(axis=-1)
        return int(max_run_per_column.max())

    def _compute_verdict(self, gouge: DiffRegion, leftover: DiffRegion) -> str:
        """根据 gouge 与 leftover 的严重度计算总体判定。"""
        if gouge.severity == "critical" or leftover.severity == "critical":
            return "reject"
        if gouge.severity == "warning" or leftover.severity == "warning":
            return "warning"
        return "accept"

    def _export_diff_stl(
        self,
        gouge_grid: np.ndarray,
        leftover_grid: np.ndarray,
        bbox_min: np.ndarray,
        output_dir: Path,
        task_id: str,
    ) -> tuple[str, bytes]:
        """导出偏差可视化 STL（gouge + leftover 合并，用于前端着色）。"""
        try:
            import trimesh  # noqa: F401  # 探测导入：确认 trimesh 可用，不可用时返回空
        except ImportError:
            return "", b""

        # 合并网格（前端通过顶点颜色区分 gouge/leftover，这里仅导出几何）
        combined_grid = gouge_grid | leftover_grid
        if not combined_grid.any():
            return "", b""

        diff_mesh = reconstruct_mesh(combined_grid, bbox_min, self._voxel_size)
        if diff_mesh is None or len(diff_mesh.faces) == 0:
            return "", b""

        output_dir.mkdir(parents=True, exist_ok=True)
        stl_filename = f"auto_diff_{task_id}.stl"
        stl_path = output_dir / stl_filename
        try:
            diff_mesh.export(str(stl_path), file_type="stl")
            stl_raw = diff_mesh.export(file_type="stl")
            url = f"/api/simulation/output/{stl_filename}"
            return url, stl_raw
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("偏差 STL 导出失败: %s", e)
            return "", b""

    def _build_summary(
        self,
        verdict: str,
        gouge: DiffRegion,
        leftover: DiffRegion,
        design_volume: float,
        actual_volume: float,
    ) -> str:
        """生成人类可读的结论摘要。"""
        if verdict == "accept":
            return (
                f"几何比对通过：过切 {gouge.volume_mm3:.2f} mm³ "
                f"({gouge.ratio * 100:.4f}%)，残料 {leftover.volume_mm3:.2f} mm³ "
                f"({leftover.ratio * 100:.4f}%)，均在容差范围内。"
            )
        if verdict == "warning":
            parts = []
            if gouge.severity == "warning":
                parts.append(
                    f"检测到过切 {gouge.volume_mm3:.2f} mm³ "
                    f"({gouge.ratio * 100:.4f}%)，最大深度 {gouge.max_depth_mm:.3f} mm"
                )
            if leftover.severity == "warning":
                parts.append(
                    f"检测到残料 {leftover.volume_mm3:.2f} mm³ "
                    f"({leftover.ratio * 100:.4f}%)，最大高度 {leftover.max_depth_mm:.3f} mm"
                )
            return "几何比对告警：" + "；".join(parts) + "。建议复核刀路。"
        # reject
        parts = []
        if gouge.severity == "critical":
            parts.append(
                f"严重过切 {gouge.volume_mm3:.2f} mm³ "
                f"({gouge.ratio * 100:.4f}%)，最大深度 {gouge.max_depth_mm:.3f} mm，"
                f"可能导致工件报废"
            )
        if leftover.severity == "critical":
            parts.append(
                f"严重残料 {leftover.volume_mm3:.2f} mm³ "
                f"({leftover.ratio * 100:.4f}%)，最大高度 {leftover.max_depth_mm:.3f} mm，"
                f"需补加工"
            )
        return "几何比对拒收：" + "；".join(parts) + "。"

    def _error_result(self, task_id: str, start_time: float, message: str) -> DiffResult:
        """生成错误降级结果。"""
        elapsed = time.perf_counter() - start_time
        result = DiffResult(
            task_id=task_id,
            verdict="reject",
            voxel_size=self._voxel_size,
            duration_seconds=elapsed,
            summary=f"几何比对失败：{message}",
        )
        # 标记为异常
        result.gouge.severity = "critical"
        result.leftover.severity = "critical"
        return result


__all__ = [
    "DiffRegion",
    "DiffResult",
    "GeometryDiffer",
    "DEFAULT_GOUGE_REJECT_RATIO",
    "DEFAULT_GOUGE_WARN_RATIO",
    "DEFAULT_LEFTOVER_REJECT_RATIO",
    "DEFAULT_LEFTOVER_WARN_RATIO",
]
