"""圆柱特征提取。

算法流程
========
1. 优先用 pyransac3d（专用于 3D 几何 RANSAC 的库）
2. pyransac3d 不可用时，用纯 numpy 实现 RANSAC 圆柱拟合
3. 迭代提取多个圆柱（每次提取一个，移除内点，再提取下一个）

纯 numpy RANSAC 圆柱拟合算法：
    1. 随机采样 2 个点
    2. 两点连线方向作为轴线候选
    3. 把所有点投影到垂直于轴线的 2D 平面上
    4. 在 2D 平面上拟合圆（Kåsa 代数法）
    5. 计算 3D 点到圆柱面的距离 = |dist_to_axis - radius|
    6. 统计内点数（距离 < threshold）
    7. 重复 max_trials 次，选内点最多的圆柱
    8. 用所有内点重新拟合

输出参数（存于 ExtractedFeature.params）：
    axis: [ax, ay, az]     单位轴线方向
    center: [cx, cy, cz]   轴线上一点（投影圆心反投影回 3D）
    radius_mm: float       半径
    height_mm: float        高度估计（内点沿轴线方向的跨度）
    inlier_count: int       内点数

置信度计算：
    confidence = inlier_count / total_vertex_count
    典型值 0.02-0.30（圆柱通常占总顶点较少比例）

工程告知（项目记忆硬约束）：
    圆柱拟合对噪声敏感，半径误差通常 5-15%。
    配合面圆柱（如轴承位 H7）不可用本模块输出值，
    必须经三坐标测量机或 cylindricity 检验。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.config import FeatureExtractionConfig
from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureReviewStatus,
    FeatureType,
)

logger = logging.getLogger(__name__)


# 条件导入：pyransac3d 可选，不可用时用纯 numpy 实现


def _try_import_pyransac3d() -> Any:
    """尝试导入 pyransac3d。

    Returns:
        pyransac3d 模块 或 None
    """
    try:
        import pyransac3d

        return pyransac3d
    except ImportError:
        logger.info(
            "pyransac3d 未安装，圆柱提取退化为纯 numpy RANSAC 实现。如需更稳定的拟合，请安装：pip install pyransac3d"
        )
        return None


# 结果数据类


@dataclass
class CylinderExtractionResult:
    """圆柱提取结果摘要。"""

    success: bool
    features: list[ExtractedFeature]
    method: str  # "pyransac3d" / "numpy_ransac" / "fallback_empty"
    total_vertex_count: int = 0
    extracted_count: int = 0
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "features": [f.to_dict() for f in self.features],
            "method": self.method,
            "total_vertex_count": self.total_vertex_count,
            "extracted_count": self.extracted_count,
            "error_message": self.error_message,
        }


# 圆柱提取器


class CylinderExtractor:
    """RANSAC 圆柱特征提取器。

    迭代提取多个圆柱：每次拟合一个 → 移除内点 → 拟合下一个。
    最多提取 ``cfg.cylinder_max_features`` 个圆柱。
    半径超出 [cylinder_min_radius_mm, cylinder_max_radius_mm] 的圆柱被丢弃。
    """

    def __init__(self, cfg: FeatureExtractionConfig) -> None:
        self._cfg = cfg
        self._pyransac3d = _try_import_pyransac3d()

    def extract(
        self,
        vertices: np.ndarray,
        faces: np.ndarray | None = None,
    ) -> CylinderExtractionResult:
        """从顶点数组提取多个圆柱特征。

        Args:
            vertices: (N, 3) 顶点坐标数组
            faces: (M, 3) 面片索引数组（可选，目前未使用）

        Returns:
            CylinderExtractionResult
        """
        if vertices is None or len(vertices) < 5:
            return CylinderExtractionResult(
                success=False,
                features=[],
                method="fallback_empty",
                error_message=f"顶点数不足 5（实际 {0 if vertices is None else len(vertices)}），无法拟合圆柱",
            )

        vertices = np.asarray(vertices, dtype=np.float64)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            return CylinderExtractionResult(
                success=False,
                features=[],
                method="fallback_empty",
                error_message=f"vertices 形状错误，期望 (N, 3)，实际 {vertices.shape}",
            )

        total_count = len(vertices)
        method = "pyransac3d" if self._pyransac3d is not None else "numpy_ransac"

        # 迭代提取多个圆柱
        remaining_mask = np.ones(total_count, dtype=bool)
        global_indices = np.arange(total_count)
        all_features: list[ExtractedFeature] = []

        for cyl_idx in range(self._cfg.cylinder_max_features):
            remaining_count = int(np.sum(remaining_mask))
            if remaining_count < self._cfg.cylinder_min_inliers:
                break

            remaining_vertices = vertices[remaining_mask]
            remaining_global_indices = global_indices[remaining_mask]

            cyl_params, inlier_local_mask = self._fit_single_cylinder(remaining_vertices)
            if cyl_params is None or inlier_local_mask is None:
                break

            inlier_count = int(np.sum(inlier_local_mask))
            if inlier_count < self._cfg.cylinder_min_inliers:
                break

            # 半径范围校验
            radius = cyl_params["radius_mm"]
            if radius < self._cfg.cylinder_min_radius_mm or radius > self._cfg.cylinder_max_radius_mm:
                logger.debug(
                    "圆柱半径 %.3fmm 超出范围 [%.3f, %.3f]，跳过",
                    radius,
                    self._cfg.cylinder_min_radius_mm,
                    self._cfg.cylinder_max_radius_mm,
                )
                # 移除这批内点，继续找下一个
                inlier_global_indices = remaining_global_indices[inlier_local_mask]
                inlier_global_mask = np.zeros(total_count, dtype=bool)
                inlier_global_mask[inlier_global_indices] = True
                remaining_mask = remaining_mask & (~inlier_global_mask)
                continue

            # 置信度
            confidence = float(inlier_count) / float(total_count)

            # 采样顶点索引
            inlier_global_indices = remaining_global_indices[inlier_local_mask]
            sample_size = min(100, len(inlier_global_indices))
            if sample_size > 0:
                sample_step = max(1, len(inlier_global_indices) // sample_size)
                sample_indices = inlier_global_indices[::sample_step][:sample_size].tolist()
            else:
                sample_indices = []

            feature = ExtractedFeature(
                feature_id=f"cyl_{uuid.uuid4().hex[:10]}",
                feature_type=FeatureType.CYLINDER.value,
                params={
                    "axis": cyl_params["axis"],
                    "center": cyl_params["center"],
                    "radius_mm": float(radius),
                    "height_mm": float(cyl_params["height_mm"]),
                    "inlier_count": inlier_count,
                },
                confidence=confidence,
                sample_vertex_indices=sample_indices,
                review_status=FeatureReviewStatus.PENDING.value,
            )
            all_features.append(feature)

            # 移除本次内点
            inlier_global_mask = np.zeros(total_count, dtype=bool)
            inlier_global_mask[inlier_global_indices] = True
            remaining_mask = remaining_mask & (~inlier_global_mask)

            logger.debug(
                "提取圆柱 #%d: radius=%.3fmm height=%.3fmm inliers=%d confidence=%.3f",
                cyl_idx,
                radius,
                cyl_params["height_mm"],
                inlier_count,
                confidence,
            )

        return CylinderExtractionResult(
            success=True,
            features=all_features,
            method=method,
            total_vertex_count=total_count,
            extracted_count=len(all_features),
        )

    def _fit_single_cylinder(
        self,
        vertices: np.ndarray,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        """拟合单个圆柱。

        Returns:
            (cyl_params, inlier_mask) 或 (None, None)
        """
        if self._pyransac3d is not None:
            return self._fit_cylinder_pyransac3d(vertices)
        return self._fit_cylinder_numpy(vertices)

    def _fit_cylinder_pyransac3d(
        self,
        vertices: np.ndarray,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        """用 pyransac3d 拟合圆柱。"""
        try:
            cyl = self._pyransac3d.cylinder()
            center, axis, radius, inlier_idx = cyl.fit(
                vertices,
                thresh=self._cfg.plane_ransac_threshold_mm,
                maxIteration=200,
            )
            inlier_mask = np.zeros(len(vertices), dtype=bool)
            inlier_mask[inlier_idx] = True

            # 计算高度（内点沿轴线方向的跨度）
            inlier_vertices = vertices[inlier_mask]
            if len(inlier_vertices) == 0:
                return None, None
            axis_arr = np.asarray(axis, dtype=np.float64)
            axis_arr = axis_arr / (np.linalg.norm(axis_arr) + 1e-10)
            projections = inlier_vertices @ axis_arr
            height_mm = float(projections.max() - projections.min())

            return (
                {
                    "axis": axis_arr.tolist(),
                    "center": list(center),
                    "radius_mm": float(radius),
                    "height_mm": height_mm,
                },
                inlier_mask,
            )
        except (ValueError, RuntimeError, IndexError) as e:
            logger.warning("pyransac3d 圆柱拟合失败: %s，退化为 numpy 实现", e)
            return self._fit_cylinder_numpy(vertices)

    def _fit_cylinder_numpy(
        self,
        vertices: np.ndarray,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        """纯 numpy RANSAC 圆柱拟合。

        算法：
        1. 随机采样 2 个点，连线方向作为轴线候选
        2. 把所有点投影到垂直于轴线的 2D 平面
        3. 在 2D 平面上拟合圆（Kåsa 代数法）
        4. 计算 3D 点到圆柱面的距离 = |dist_to_axis - radius|
        5. 统计内点（距离 < threshold）
        6. 重复 max_trials 次，选内点最多的圆柱
        """
        rng = np.random.default_rng(seed=42)
        n = len(vertices)
        threshold = self._cfg.plane_ransac_threshold_mm
        max_trials = 150

        best_inlier_count = 0
        best_inlier_mask: np.ndarray | None = None
        best_params: dict[str, Any] | None = None

        for _ in range(max_trials):
            if n < 2:
                break
            idx = rng.choice(n, 2, replace=False)
            p1, p2 = vertices[idx]
            axis = p2 - p1
            axis_norm = np.linalg.norm(axis)
            if axis_norm < 1e-10:
                continue
            axis = axis / axis_norm

            # 构造垂直于轴线的 2D 坐标系
            # 任取一个不平行于 axis 的向量
            if abs(axis[0]) < 0.9:
                ref = np.array([1.0, 0.0, 0.0])
            else:
                ref = np.array([0.0, 1.0, 0.0])
            u = np.cross(axis, ref)
            u = u / (np.linalg.norm(u) + 1e-10)
            v = np.cross(axis, u)

            # 投影到 2D 平面
            centered = vertices - vertices.mean(axis=0)
            coords_2d = np.column_stack([centered @ u, centered @ v])

            # 在 2D 上拟合圆（Kåsa 法）
            circle_params = _fit_circle_2d(coords_2d)
            if circle_params is None:
                continue
            cx_2d, cy_2d, radius = circle_params
            if radius < self._cfg.cylinder_min_radius_mm * 0.5:
                continue

            # 计算每个点到圆心的 2D 距离
            dist_to_center = np.sqrt((coords_2d[:, 0] - cx_2d) ** 2 + (coords_2d[:, 1] - cy_2d) ** 2)
            # 点到圆柱面的距离 = |dist_to_center - radius|
            dist_to_surface = np.abs(dist_to_center - radius)
            inlier_mask = dist_to_surface < threshold
            inlier_count = int(np.sum(inlier_mask))

            if inlier_count > best_inlier_count:
                best_inlier_count = inlier_count
                best_inlier_mask = inlier_mask
                # 反投影圆心到 3D
                center_3d = vertices.mean(axis=0) + cx_2d * u + cy_2d * v
                best_params = {
                    "axis": axis.tolist(),
                    "center": center_3d.tolist(),
                    "radius_mm": float(radius),
                    "height_mm": 0.0,  # 后面用内点计算
                }

        if best_inlier_mask is None or best_params is None or best_inlier_count < 3:
            return None, None

        # 计算高度（内点沿轴线方向的跨度）
        inlier_vertices = vertices[best_inlier_mask]
        axis_arr = np.asarray(best_params["axis"])
        projections = inlier_vertices @ axis_arr
        best_params["height_mm"] = float(projections.max() - projections.min())

        return best_params, best_inlier_mask


# 辅助函数：2D 圆拟合（Kåsa 代数法）


def _fit_circle_2d(points: np.ndarray) -> tuple[float, float, float] | None:
    """用 Kåsa 代数法拟合 2D 圆。

    圆方程: (x-cx)² + (y-cy)² = r²
    展开: x² + y² = 2*cx*x + 2*cy*y + (r² - cx² - cy²)
    令 A = 2*cx, B = 2*cy, C = r² - cx² - cy²
    则 x² + y² = A*x + B*y + C
    用最小二乘求解 [x, y, 1] [A, B, C]^T = x² + y²

    Args:
        points: (N, 2) 2D 点集

    Returns:
        (cx, cy, radius) 或 None
    """
    if len(points) < 3:
        return None

    x = points[:, 0]
    y = points[:, 1]
    # 构造线性方程组 A * [a, b, c]^T = z
    # 其中 a = 2*cx, b = 2*cy, c = r² - cx² - cy²
    A_mat = np.column_stack([x, y, np.ones_like(x)])
    z = x**2 + y**2

    try:
        # 最小二乘求解
        coef, _, _, _ = np.linalg.lstsq(A_mat, z, rcond=None)
        a, b, c = coef
        cx = a / 2
        cy = b / 2
        radius_sq = c + cx**2 + cy**2
        if radius_sq <= 0:
            return None
        radius = float(np.sqrt(radius_sq))
        return float(cx), float(cy), radius
    except (np.linalg.LinAlgError, ValueError):
        return None
