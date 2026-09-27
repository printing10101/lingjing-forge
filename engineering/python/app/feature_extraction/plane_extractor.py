"""平面特征提取（RANSAC）。

算法流程
========
1. 对所有顶点迭代提取多个平面（每次提取一个，移除内点，再提取下一个）
2. 每次提取用 RANSAC 拟合平面方程 ax + by + cz + d = 0
3. 优先用 sklearn.linear_model.RANSACRegressor（成熟稳定）
4. sklearn 不可用时退化为纯 numpy 实现（保证桌面轻量环境可用）
5. 每个平面特征包含：法向量 / 偏移量 / 面积估计 / 内点数 / 置信度

输出参数（存于 ExtractedFeature.params）：
    normal: [nx, ny, nz]   单位法向量
    offset: float          平面到原点的有向距离（normal · x = offset）
    area_mm2: float        平面面积估计（内点凸包面积，仅参考）
    inlier_count: int      内点数

置信度计算：
    confidence = inlier_count / total_vertex_count
    典型值 0.05-0.50（一个平面通常占总顶点 5%-50%）

工程告知（项目记忆硬约束）：
    本模块输出的是「算法建议的平面」，工程师必须审核后才能进入阶段 3。
    mesh 未标定时 area_mm2 为无量纲值，仅可用于可视化。
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


# 条件导入：sklearn 可选，不可用时用纯 numpy 实现


def _try_import_sklearn() -> tuple[Any, Any]:
    """尝试导入 sklearn 的 RANSACRegressor 和 LinearRegression。

    Returns:
        (RANSACRegressor, LinearRegression) 或 (None, None)
    """
    try:
        from sklearn.linear_model import LinearRegression, RANSACRegressor

        return RANSACRegressor, LinearRegression
    except ImportError:
        logger.info(
            "sklearn 未安装，平面提取退化为纯 numpy RANSAC 实现。如需更稳定的拟合，请安装：pip install scikit-learn"
        )
        return None, None


# 结果数据类


@dataclass
class PlaneExtractionResult:
    """平面提取结果摘要。"""

    success: bool
    features: list[ExtractedFeature]
    method: str  # "sklearn_ransac" / "numpy_ransac" / "fallback_empty"
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


# 平面提取器


class PlaneExtractor:
    """RANSAC 平面特征提取器。

    迭代提取多个平面：每次拟合一个平面 → 移除内点 → 拟合下一个。
    最多提取 ``cfg.plane_max_features`` 个平面。
    """

    def __init__(self, cfg: FeatureExtractionConfig) -> None:
        self._cfg = cfg
        self._sklearn_ransac, self._sklearn_linear = _try_import_sklearn()

    def extract(
        self,
        vertices: np.ndarray,
        faces: np.ndarray | None = None,
    ) -> PlaneExtractionResult:
        """从顶点数组提取多个平面特征。

        Args:
            vertices: (N, 3) 顶点坐标数组
            faces: (M, 3) 面片索引数组（可选，目前未使用，保留接口）

        Returns:
            PlaneExtractionResult
        """
        if vertices is None or len(vertices) < 3:
            return PlaneExtractionResult(
                success=False,
                features=[],
                method="fallback_empty",
                error_message=f"顶点数不足 3（实际 {0 if vertices is None else len(vertices)}），无法拟合平面",
            )

        vertices = np.asarray(vertices, dtype=np.float64)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            return PlaneExtractionResult(
                success=False,
                features=[],
                method="fallback_empty",
                error_message=f"vertices 形状错误，期望 (N, 3)，实际 {vertices.shape}",
            )

        total_count = len(vertices)
        method = "sklearn_ransac" if self._sklearn_ransac is not None else "numpy_ransac"

        # 迭代提取多个平面
        remaining_mask = np.ones(total_count, dtype=bool)
        global_indices = np.arange(total_count)
        all_features: list[ExtractedFeature] = []

        for plane_idx in range(self._cfg.plane_max_features):
            remaining_count = int(np.sum(remaining_mask))
            if remaining_count < self._cfg.plane_min_inliers:
                logger.debug(
                    "平面提取停止：剩余顶点 %d < min_inliers %d",
                    remaining_count,
                    self._cfg.plane_min_inliers,
                )
                break

            remaining_vertices = vertices[remaining_mask]
            remaining_global_indices = global_indices[remaining_mask]

            # 拟合单个平面
            plane_params, inlier_local_mask = self._fit_single_plane(remaining_vertices)
            if plane_params is None or inlier_local_mask is None:
                break

            inlier_count = int(np.sum(inlier_local_mask))
            if inlier_count < self._cfg.plane_min_inliers:
                logger.debug(
                    "平面提取停止：内点数 %d < min_inliers %d",
                    inlier_count,
                    self._cfg.plane_min_inliers,
                )
                break

            # 估计平面面积（内点凸包面积）
            inlier_vertices = remaining_vertices[inlier_local_mask]
            area_mm2 = _estimate_plane_area(inlier_vertices)

            # 置信度 = 内点数 / 总顶点数
            confidence = float(inlier_count) / float(total_count)

            # 采样顶点索引（用于前端高亮，最多取 100 个）
            inlier_global_indices = remaining_global_indices[inlier_local_mask]
            sample_size = min(100, len(inlier_global_indices))
            if sample_size > 0:
                sample_step = max(1, len(inlier_global_indices) // sample_size)
                sample_indices = inlier_global_indices[::sample_step][:sample_size].tolist()
            else:
                sample_indices = []

            feature = ExtractedFeature(
                feature_id=f"plane_{uuid.uuid4().hex[:10]}",
                feature_type=FeatureType.PLANE.value,
                params={
                    "normal": plane_params["normal"],
                    "offset": float(plane_params["offset"]),
                    "area_mm2": float(area_mm2),
                    "inlier_count": inlier_count,
                },
                confidence=confidence,
                sample_vertex_indices=sample_indices,
                review_status=FeatureReviewStatus.PENDING.value,
            )
            all_features.append(feature)

            # 从剩余顶点中移除本次内点
            inlier_global_mask = np.zeros(total_count, dtype=bool)
            inlier_global_mask[inlier_global_indices] = True
            remaining_mask = remaining_mask & (~inlier_global_mask)

            logger.debug(
                "提取平面 #%d: normal=%s offset=%.4f inliers=%d confidence=%.3f",
                plane_idx,
                plane_params["normal"],
                plane_params["offset"],
                inlier_count,
                confidence,
            )

        return PlaneExtractionResult(
            success=True,
            features=all_features,
            method=method,
            total_vertex_count=total_count,
            extracted_count=len(all_features),
        )

    def _fit_single_plane(
        self,
        vertices: np.ndarray,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        """拟合单个平面。

        Returns:
            (plane_params, inlier_mask) 或 (None, None)
            plane_params: {"normal": [nx,ny,nz], "offset": float}
            inlier_mask: bool 数组，标记哪些顶点是内点
        """
        if self._sklearn_ransac is not None:
            return self._fit_plane_sklearn(vertices)
        return self._fit_plane_numpy(vertices)

    def _fit_plane_sklearn(
        self,
        vertices: np.ndarray,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        """用 sklearn RANSACRegressor 拟合平面。

        平面方程: z = ax + by + c，法向量 (-a, -b, 1) / norm
        """
        RANSACRegressor, LinearRegression = self._sklearn_ransac, self._sklearn_linear
        assert RANSACRegressor is not None and LinearRegression is not None

        # 用 X, Y 预测 Z
        X = vertices[:, :2]  # (N, 2)
        z = vertices[:, 2]  # (N,)

        try:
            ransac = RANSACRegressor(
                estimator=LinearRegression(),
                residual_threshold=self._cfg.plane_ransac_threshold_mm,
                random_state=42,
                max_trials=200,
            )
            ransac.fit(X, z)
        except ValueError as e:
            logger.warning("sklearn RANSAC 拟合失败: %s，退化为 numpy 实现", e)
            return self._fit_plane_numpy(vertices)

        inlier_mask = ransac.inlier_mask_
        if inlier_mask is None:
            return self._fit_plane_numpy(vertices)

        # 从拟合系数恢复平面方程
        # z = a*x + b*y + c => a*x + b*y - z + c = 0
        # 法向量 (-a, -b, 1)
        a, b = ransac.estimator_.coef_
        normal = np.array([-a, -b, 1.0])
        norm = np.linalg.norm(normal)
        if norm < 1e-10:
            return self._fit_plane_numpy(vertices)
        normal = normal / norm
        # offset = normal · 点 = (-a/norm)*x + (-b/norm)*y + (1/norm)*z
        # 用内点质心计算 offset
        inlier_vertices = vertices[inlier_mask]
        centroid = inlier_vertices.mean(axis=0)
        offset = float(np.dot(normal, centroid))

        return {"normal": normal.tolist(), "offset": offset}, inlier_mask

    def _fit_plane_numpy(
        self,
        vertices: np.ndarray,
    ) -> tuple[dict[str, Any] | None, np.ndarray | None]:
        """纯 numpy 实现的 RANSAC 平面拟合。

        算法：
        1. 随机采样 3 个点
        2. 计算平面方程 ax + by + cz + d = 0
        3. 统计内点数（距离 < threshold）
        4. 重复 max_trials 次，选内点最多的平面
        5. 用所有内点 SVD 重新拟合
        """
        rng = np.random.default_rng(seed=42)
        n = len(vertices)
        threshold = self._cfg.plane_ransac_threshold_mm
        max_trials = 200

        best_inlier_count = 0
        best_inlier_mask: np.ndarray | None = None

        for _ in range(max_trials):
            if n < 3:
                break
            idx = rng.choice(n, 3, replace=False)
            p1, p2, p3 = vertices[idx]

            # 计算法向量
            v1 = p2 - p1
            v2 = p3 - p1
            normal = np.cross(v1, v2)
            norm = np.linalg.norm(normal)
            if norm < 1e-10:
                continue
            normal = normal / norm
            d = -np.dot(normal, p1)

            # 计算所有点到平面的距离
            distances = np.abs(vertices @ normal + d)
            inlier_mask = distances < threshold
            inlier_count = int(np.sum(inlier_mask))

            if inlier_count > best_inlier_count:
                best_inlier_count = inlier_count
                best_inlier_mask = inlier_mask

        if best_inlier_mask is None or best_inlier_count < 3:
            return None, None

        # 用所有内点 SVD 重新拟合平面
        inlier_vertices = vertices[best_inlier_mask]
        centroid = inlier_vertices.mean(axis=0)
        centered = inlier_vertices - centroid
        try:
            _, _, vh = np.linalg.svd(centered, full_matrices=False)
            normal = vh[-1]
            # 统一法向量方向：指向远离原点
            if np.dot(normal, centroid) < 0:
                normal = -normal
            offset = float(np.dot(normal, centroid))
            return {"normal": normal.tolist(), "offset": offset}, best_inlier_mask
        except np.linalg.LinAlgError as e:
            logger.warning("SVD 平面拟合失败: %s", e)
            return None, None


# 辅助函数


def _estimate_plane_area(inlier_vertices: np.ndarray) -> float:
    """估计平面面积（内点凸包面积）。

    使用顶点在平面上的 2D 凸包面积作为估计。
    对于噪声较大的 mesh，此估计仅作参考。

    Args:
        inlier_vertices: (M, 3) 内点顶点

    Returns:
        面积（mm²），mesh 未标定时为无量纲值
    """
    if len(inlier_vertices) < 3:
        return 0.0

    # 用 SVD 把内点投影到最佳 2D 平面
    centroid = inlier_vertices.mean(axis=0)
    centered = inlier_vertices - centroid
    try:
        # 右奇异向量（vh 的行）是主成分方向，投影到最佳 2D 平面。
        # 修复：原用 u[:, :2]（左奇异向量），语义错误且对 M>3 会维度不匹配抛 ValueError。
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        coords_2d = centered @ vh.T[:, :2]
    except (np.linalg.LinAlgError, ValueError):
        return 0.0

    # 计算 2D 凸包面积
    try:
        from scipy.spatial import ConvexHull

        hull = ConvexHull(coords_2d)
        return float(hull.volume)  # 2D 中 volume 就是面积
    except (ImportError, ValueError, IndexError):
        # scipy 不可用或凸包失败，用包围盒面积作为粗略估计
        if len(coords_2d) < 3:
            return 0.0
        min_xy = coords_2d.min(axis=0)
        max_xy = coords_2d.max(axis=0)
        bbox_area = float((max_xy[0] - min_xy[0]) * (max_xy[1] - min_xy[1]))
        return bbox_area
