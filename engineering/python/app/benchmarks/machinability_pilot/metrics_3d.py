"""S3 几何相似度指标（口径对齐 Text2CAD-Bench：单位盒归一化 + 采样 Chamfer + 体素 IoU）。

偏差声明：Text2CAD-Bench 用 256³ 体素，pilot 默认 64³（速度考虑），
分辨率是显式参数并记录进 run manifest，论文口径需在扩基准阶段统一。
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

CHAMFER_SAMPLES = 30_000
VOXEL_RESOLUTION = 64
CHAMFER_SCALE = 1e3  # Text2CAD-Bench 口径：CD ×10³

# 归一化后最长边恰为 1.0；留上下容差，用于挡住"忘记归一化"而不是挑剔归一化方式
_UNIT_EXTENT_LO = 0.5
_UNIT_EXTENT_HI = 1.5


class GeometryNotNormalizedError(ValueError):
    """传入网格未做单位盒归一化。"""


def _require_unit_box(mesh: trimesh.Trimesh, fn_name: str) -> None:
    """断言输入已过 :func:`normalize_to_unit_box`，否则抛指名道姓的错误。

    为什么必须有这道守卫（实测教训）：把 70 mm 的原始叶片网格直接喂进来时，
    ``voxel_iou`` 的 pitch = 1/64 会被当成 0.0156 mm 用，trimesh 的 subdivide
    体素化在 ``max_iter=10`` 处抛 ``ValueError: max_iter exceeded!``——报错信息
    与真实原因（漏了归一化）毫无关系，且只在粗网格大尺寸件上出现，用长方体
    原语写的测试抓不到。Chamfer 更隐蔽：不报错，直接给出量纲错误的数。
    """
    extent = float(mesh.extents.max())
    if not _UNIT_EXTENT_LO < extent <= _UNIT_EXTENT_HI:
        raise GeometryNotNormalizedError(
            f"[归一化缺失] {fn_name} 只接受单位盒网格，实际最长边 {extent:.4g}。"
            "建议操作：先调用 normalize_to_unit_box(mesh) 再传入本函数。"
        )


def load_mesh(path: str | trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh") if not isinstance(path, trimesh.Trimesh) else path
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"无法加载有效 mesh: {path}")
    return mesh


def normalize_to_unit_box(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """包围盒中心平移到原点，按最大边长缩放到单位盒（Text2CAD-Bench 口径）。"""
    mesh = mesh.copy()
    scale = 1.0 / float(max(mesh.extents.max(), 1e-9))
    mesh.apply_translation(-(mesh.bounds[0] + mesh.bounds[1]) / 2.0)
    mesh.apply_scale(scale)
    return mesh


def chamfer_distance(
    mesh_a: trimesh.Trimesh,
    mesh_b: trimesh.Trimesh,
    samples: int = CHAMFER_SAMPLES,
    seed: int = 0,
) -> float:
    """对称 Chamfer 距离：双向最近邻均值，单位盒坐标 ×1e3。"""
    _require_unit_box(mesh_a, "chamfer_distance")
    _require_unit_box(mesh_b, "chamfer_distance")
    rng_a = np.random.default_rng(seed)
    rng_b = np.random.default_rng(seed)
    pa, _ = trimesh.sample.sample_surface(mesh_a, samples, seed=rng_a)
    pb, _ = trimesh.sample.sample_surface(mesh_b, samples, seed=rng_b)
    dist_ab = cKDTree(pb).query(pa)[0].mean()
    dist_ba = cKDTree(pa).query(pb)[0].mean()
    return float((dist_ab + dist_ba) * 0.5 * CHAMFER_SCALE)


def _filled_matrix(mesh: trimesh.Trimesh, pitch: float) -> tuple[np.ndarray, np.ndarray, bool]:
    """体素占位矩阵 + 网格原点。fill 失败（非水密）时退化为表面体素并打标。"""
    grid = mesh.voxelized(pitch)
    try:
        grid = grid.fill()
        return grid.matrix.astype(bool), np.asarray(grid.transform)[:3, 3], True
    except (ValueError, RuntimeError, IndexError):
        return grid.matrix.astype(bool), np.asarray(grid.transform)[:3, 3], False


def voxel_iou(
    mesh_a: trimesh.Trimesh, mesh_b: trimesh.Trimesh, resolution: int = VOXEL_RESOLUTION
) -> tuple[float, bool]:
    """单位盒内体素占位 IoU，返回 (iou, 两网格是否都成功实体填充)。"""
    _require_unit_box(mesh_a, "voxel_iou")
    _require_unit_box(mesh_b, "voxel_iou")
    pitch = 1.0 / float(resolution)
    ma, origin_a, filled_a = _filled_matrix(mesh_a, pitch)
    mb, origin_b, filled_b = _filled_matrix(mesh_b, pitch)

    # 对齐两套网格索引：ma 单元 i 的世界坐标是 origin_a + i*pitch，
    # mb 单元 j 落在 ma 坐标系中的下标为 j + shift，其中 shift = (origin_b-origin_a)/pitch
    shift = np.rint((origin_b - origin_a) / pitch).astype(int)
    low = np.minimum(0, shift)
    high = np.maximum(np.asarray(ma.shape), shift + np.asarray(mb.shape))
    shape = tuple((high - low).tolist())

    def place(m: np.ndarray, offset: np.ndarray) -> np.ndarray:
        """把 m（其 [0,0,0] 单元位于公共坐标 offset 处）放入画布，越界部分裁掉。"""
        canvas = np.zeros(shape, dtype=bool)
        dst_lo = np.maximum(offset, 0)
        dst_hi = np.minimum(offset + np.asarray(m.shape), np.asarray(shape))
        src_lo = dst_lo - offset
        src_hi = dst_hi - offset
        canvas[tuple(slice(int(lo), int(hi)) for lo, hi in zip(dst_lo, dst_hi))] = m[
            tuple(slice(int(lo), int(hi)) for lo, hi in zip(src_lo, src_hi))
        ]
        return canvas

    pa = place(ma, -low)
    pb = place(mb, shift - low)
    union = int(np.logical_or(pa, pb).sum())
    iou = float(np.logical_and(pa, pb).sum() / union) if union else 0.0
    return iou, bool(filled_a and filled_b)
