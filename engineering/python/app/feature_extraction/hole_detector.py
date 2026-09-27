"""孔/凸台检测。

算法流程
========
1. 在已检测到的平面内点上找圆形边界（孔/凸台的外周）
2. 若未传入 plane_features，则在全部顶点的主成分 2D 投影上找圆环
3. RANSAC 圆环拟合（与圆柱拟合的区别：圆柱拟合的是「侧面」，
   孔/凸台拟合的是「端面圆周」）
4. 根据圆心区域相对平面的高度偏移判定 HOLE（凹陷）或 BOSS（凸起）

RANSAC 圆环拟合算法（2D）：
    1. 随机采样 3 点
    2. 用 Kåsa 代数法计算外接圆方程
    3. 计算所有点到圆周的距离 = |dist_to_center - radius|
    4. 内点 = 距离 < threshold
    5. 重复 max_trials 次，选内点最多的圆
    6. 用所有内点重新拟合圆

HOLE vs BOSS 判定：
    取圆心附近（半径 < 0.5 * hole_radius）的内点，
    计算它们沿平面法向量方向的平均偏移：
    - offset < -threshold  → HOLE（凹陷）
    - offset > +threshold  → BOSS（凸起）
    - |offset| <= threshold → 默认 HOLE（无法判断方向）

输出参数（存于 ExtractedFeature.params）：
    hole:
        normal: [nx, ny, nz]   所在平面法向量
        center: [cx, cy, cz]   圆心 3D 坐标
        radius_mm: float       孔半径
        depth_mm: float         孔深估计（圆心区域凹陷量，负值表示反向）
    boss:
        normal: [nx, ny, nz]
        center: [cx, cy, cz]
        radius_mm: float
        height_mm: float       凸起高度

置信度计算：
    confidence = inlier_count / total_vertex_count
    典型值 0.01-0.10（孔/凸台通常占总顶点很少比例）

工程告知（项目记忆硬约束）：
    孔/凸台检测对 mesh 拓扑质量敏感，重建噪声会导致圆心位置漂移。
    螺纹孔 / 锥孔 / 沉头孔等复杂孔型不可由本模块识别，
    必须经工程师审核 + 三坐标测量机或塞规验证。
    螺纹孔（M3/M4/M5 等）的螺纹底径需查标准表，不可用本模块输出值。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.config import FeatureExtractionConfig
from app.feature_extraction._feature_classifier import (
    FEATURE_HOLE,
    classify_hole_or_boss_deep,
)
from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureReviewStatus,
    FeatureType,
)

logger = logging.getLogger(__name__)


# 结果数据类


@dataclass
class HoleDetectionResult:
    """孔/凸台检测结果摘要。"""

    success: bool
    features: list[ExtractedFeature]
    method: str  # "plane_based" / "global_pca" / "fallback_empty"
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


# 孔/凸台检测器


class HoleDetector:
    """孔/凸台特征检测器。

    优先在已检测平面内点上找圆环；未传入平面时退化为全局主成分 2D 投影。
    最多检测 ``cfg.hole_max_features`` 个孔/凸台。
    半径超出 [hole_min_radius_mm, hole_max_radius_mm] 的圆环被丢弃。
    """

    def __init__(self, cfg: FeatureExtractionConfig) -> None:
        self._cfg = cfg

    def detect(
        self,
        vertices: np.ndarray,
        plane_features: list[ExtractedFeature] | None = None,
    ) -> HoleDetectionResult:
        """从顶点数组检测孔/凸台特征。

        Args:
            vertices: (N, 3) 顶点坐标数组
            plane_features: 已检测平面特征列表（来自 PlaneExtractor）。
                若提供，将在每个平面内点上找圆环；否则在全局主成分 2D 投影上找。

        Returns:
            HoleDetectionResult
        """
        if vertices is None or len(vertices) < 5:
            return HoleDetectionResult(
                success=False,
                features=[],
                method="fallback_empty",
                error_message=f"顶点数不足 5（实际 {0 if vertices is None else len(vertices)}），无法检测孔",
            )

        vertices = np.asarray(vertices, dtype=np.float64)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            return HoleDetectionResult(
                success=False,
                features=[],
                method="fallback_empty",
                error_message=f"vertices 形状错误，期望 (N, 3)，实际 {vertices.shape}",
            )

        total_count = len(vertices)
        all_features: list[ExtractedFeature] = []
        used_global_indices: set[int] = set()

        # 路径 A：基于已检测平面找孔
        if plane_features:
            method = "plane_based"
            for plane in plane_features:
                if len(all_features) >= self._cfg.hole_max_features:
                    break

                # 取平面内点
                sample_indices = plane.sample_vertex_indices
                if len(sample_indices) < self._cfg.cylinder_min_inliers:
                    continue

                # 过滤已使用的索引
                available_indices = [i for i in sample_indices if i not in used_global_indices]
                if len(available_indices) < self._cfg.cylinder_min_inliers:
                    continue

                plane_vertices = vertices[available_indices]
                normal = np.asarray(plane.params["normal"], dtype=np.float64)
                normal = normal / (np.linalg.norm(normal) + 1e-10)

                holes = self._detect_holes_in_plane(
                    plane_vertices,
                    normal,
                    available_indices,
                    total_count,
                )
                for h in holes:
                    if len(all_features) >= self._cfg.hole_max_features:
                        break
                    all_features.append(h)
                    # 标记使用的全局索引
                    for idx in h.sample_vertex_indices:
                        used_global_indices.add(idx)
        else:
            # 路径 B：全局主成分 2D 投影
            method = "global_pca"
            holes = self._detect_holes_global_pca(vertices, total_count)
            all_features.extend(holes[: self._cfg.hole_max_features])

        return HoleDetectionResult(
            success=True,
            features=all_features,
            method=method,
            total_vertex_count=total_count,
            extracted_count=len(all_features),
        )

    def _detect_holes_in_plane(
        self,
        plane_vertices: np.ndarray,
        normal: np.ndarray,
        global_indices: list[int],
        total_count: int,
    ) -> list[ExtractedFeature]:
        """在单个平面的内点上检测孔/凸台。

        Args:
            plane_vertices: (M, 3) 平面内点
            normal: 平面法向量（单位向量）
            global_indices: 这些点在原 mesh 中的全局索引
            total_count: 原始 mesh 总顶点数（用于置信度）

        Returns:
            检测到的孔/凸台特征列表
        """
        if len(plane_vertices) < 5:
            return []

        # 构造平面 2D 坐标系
        u, v = _build_plane_basis(normal)
        centroid = plane_vertices.mean(axis=0)
        centered = plane_vertices - centroid
        coords_2d = np.column_stack([centered @ u, centered @ v])

        # 迭代 RANSAC 圆环拟合
        remaining_mask = np.ones(len(plane_vertices), dtype=bool)
        features: list[ExtractedFeature] = []

        for _ in range(self._cfg.hole_max_features):
            if int(np.sum(remaining_mask)) < self._cfg.cylinder_min_inliers:
                break

            remaining_2d = coords_2d[remaining_mask]
            remaining_local_indices = np.where(remaining_mask)[0]

            result = _ransac_circle_ring(remaining_2d, self._cfg)
            if result is None:
                break

            cx_2d, cy_2d, radius, inlier_local_mask = result
            inlier_count = int(np.sum(inlier_local_mask))
            if inlier_count < self._cfg.cylinder_min_inliers:
                break

            # 半径范围校验
            if radius < self._cfg.hole_min_radius_mm or radius > self._cfg.hole_max_radius_mm:
                # 移除这批内点，继续找下一个
                remaining_mask[remaining_local_indices[inlier_local_mask]] = False
                continue

            # 反投影圆心到 3D
            center_3d = centroid + cx_2d * u + cy_2d * v

            # 判定 HOLE 还是 BOSS
            feature_type, offset_value = self._classify_hole_or_boss(
                plane_vertices[remaining_local_indices[inlier_local_mask]],
                coords_2d[remaining_local_indices[inlier_local_mask]],
                plane_vertices,
                coords_2d,
                cx_2d,
                cy_2d,
                radius,
                normal,
            )

            # 采样顶点索引
            inlier_global_local = remaining_local_indices[inlier_local_mask]
            inlier_global_idx = [global_indices[i] for i in inlier_global_local]
            sample_size = min(100, len(inlier_global_idx))
            if sample_size > 0:
                step = max(1, len(inlier_global_idx) // sample_size)
                sample_indices = inlier_global_idx[::step][:sample_size]
            else:
                sample_indices = []

            # 置信度
            confidence = float(inlier_count) / float(total_count)

            if feature_type == FeatureType.HOLE:
                params = {
                    "normal": normal.tolist(),
                    "center": center_3d.tolist(),
                    "radius_mm": float(radius),
                    "depth_mm": float(abs(offset_value)),
                    "inlier_count": inlier_count,
                }
                feature_id = f"hole_{uuid.uuid4().hex[:10]}"
            else:
                params = {
                    "normal": normal.tolist(),
                    "center": center_3d.tolist(),
                    "radius_mm": float(radius),
                    "height_mm": float(abs(offset_value)),
                    "inlier_count": inlier_count,
                }
                feature_id = f"boss_{uuid.uuid4().hex[:10]}"

            feature = ExtractedFeature(
                feature_id=feature_id,
                feature_type=feature_type.value,
                params=params,
                confidence=confidence,
                sample_vertex_indices=sample_indices,
                review_status=FeatureReviewStatus.PENDING.value,
            )
            features.append(feature)

            # 移除本次内点
            remaining_mask[remaining_local_indices[inlier_local_mask]] = False

            logger.debug(
                "检测到 %s: radius=%.3fmm offset=%.3fmm inliers=%d confidence=%.3f",
                feature_type.value,
                radius,
                offset_value,
                inlier_count,
                confidence,
            )

        return features

    def _detect_holes_global_pca(
        self,
        vertices: np.ndarray,
        total_count: int,
    ) -> list[ExtractedFeature]:
        """未传入平面时，在全局主成分 2D 投影上找圆环。

        警告：此方法准确度低于平面内检测，仅在无平面可用时使用。
        """
        centroid = vertices.mean(axis=0)
        centered = vertices - centroid

        try:
            _, _, vh = np.linalg.svd(centered, full_matrices=False)
        except np.linalg.LinAlgError:
            return []

        # 第三主成分作为法向量
        normal = vh[-1]
        normal = normal / (np.linalg.norm(normal) + 1e-10)

        # 注：2D 投影坐标（vh[0]/vh[1] 主轴）由下游平面检测逻辑重新计算
        global_indices = list(range(len(vertices)))

        # 直接复用平面内检测逻辑（用一个虚拟平面）
        return self._detect_holes_in_plane(
            vertices,
            normal,
            global_indices,
            total_count,
        )

    def _classify_hole_or_boss(
        self,
        inlier_3d: np.ndarray,
        inlier_2d: np.ndarray,
        all_plane_3d: np.ndarray,
        all_plane_2d: np.ndarray,
        cx_2d: float,
        cy_2d: float,
        radius: float,
        normal: np.ndarray,
    ) -> tuple[FeatureType, float]:
        """判定圆环是 HOLE 还是 BOSS。

        算法：
            1. 找到圆心附近（距圆心 < 0.3 * radius）的所有平面内点
            2. 计算这些点沿法向量方向的偏移
            3. 偏移 < -threshold → HOLE（凹陷）
            4. 偏移 > +threshold → BOSS（凸起）
            5. |偏移| <= threshold → 默认 HOLE（无法判定方向）

        Args:
            inlier_3d: 圆环内点 3D 坐标
            inlier_2d: 圆环内点 2D 坐标
            all_plane_3d: 平面所有顶点 3D
            all_plane_2d: 平面所有顶点 2D
            cx_2d, cy_2d: 圆心 2D
            radius: 半径
            normal: 平面法向量

        Returns:
            (FeatureType, offset_value)
            offset_value > 0 表示凸起，< 0 表示凹陷
        """
        # 圆心附近的点
        dist_to_center = np.sqrt((all_plane_2d[:, 0] - cx_2d) ** 2 + (all_plane_2d[:, 1] - cy_2d) ** 2)
        center_zone_mask = dist_to_center < (0.3 * radius)
        if int(np.sum(center_zone_mask)) < 3:
            # 圆心附近无点，无法判定，默认 HOLE
            return FeatureType.HOLE, 0.0

        # 圆心附近点沿法向量方向的偏移
        # 用平面所有点的平均 z（沿法向量方向）作为基准
        plane_z = all_plane_3d @ normal
        center_z = inlier_3d @ normal
        # 基准：用圆环内点的平均 z（因为圆环本身在平面上）
        reference_z = float(np.mean(center_z))
        center_zone_z = plane_z[center_zone_mask]
        offset = float(np.mean(center_zone_z) - reference_z)

        # 判定阈值：用 RANSAC 阈值（×2，与既有行为一致）
        threshold = self._cfg.plane_ransac_threshold_mm * 2.0

        # 分类判定委托给纯 Python 白盒逻辑（_feature_classifier），
        # 与既有 HOLE/BOSS 判定规则逐字节一致（防回归）。
        feature_type_str, offset_v = classify_hole_or_boss_deep(
            offset,
            threshold,
            default_type=FEATURE_HOLE,
        )
        ftype = FeatureType.HOLE if feature_type_str == FEATURE_HOLE else FeatureType.BOSS
        return ftype, offset_v


# 辅助函数


def _build_plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """根据法向量构造平面的 2D 坐标基 (u, v)。

    Args:
        normal: (3,) 单位法向量

    Returns:
        (u, v) 两个正交单位向量，与 normal 构成右手坐标系
    """
    # 任取一个不平行于 normal 的向量
    if abs(normal[0]) < 0.9:
        ref = np.array([1.0, 0.0, 0.0])
    else:
        ref = np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, ref)
    u = u / (np.linalg.norm(u) + 1e-10)
    v = np.cross(normal, u)
    return u, v


def _ransac_circle_ring(
    points_2d: np.ndarray,
    cfg: FeatureExtractionConfig,
) -> tuple[float, float, float, np.ndarray] | None:
    """RANSAC 2D 圆环拟合（找圆周上的点）。

    与 Kåsa 圆拟合的区别：
    - Kåsa 法：拟合圆内所有点（找最大内切圆）
    - 圆环法：找落在圆周上的点（适合 mesh 上的孔/凸台边界）

    Args:
        points_2d: (N, 2) 2D 点集
        cfg: 配置（用于读取 threshold / min_radius / max_radius）

    Returns:
        (cx, cy, radius, inlier_mask) 或 None
    """
    rng = np.random.default_rng(seed=42)
    n = len(points_2d)
    if n < 3:
        return None

    threshold = cfg.plane_ransac_threshold_mm
    max_trials = 200
    min_radius = cfg.hole_min_radius_mm
    max_radius = cfg.hole_max_radius_mm

    best_inlier_count = 0
    best_result: tuple[float, float, float, np.ndarray] | None = None

    for _ in range(max_trials):
        idx = rng.choice(n, 3, replace=False)
        p1, p2, p3 = points_2d[idx]

        # 三点定圆
        circle = _circle_from_three_points(p1, p2, p3)
        if circle is None:
            continue
        cx, cy, radius = circle
        if radius < min_radius * 0.3 or radius > max_radius * 3.0:
            continue

        # 计算所有点到圆周的距离
        dist_to_center = np.sqrt((points_2d[:, 0] - cx) ** 2 + (points_2d[:, 1] - cy) ** 2)
        dist_to_ring = np.abs(dist_to_center - radius)
        inlier_mask = dist_to_ring < threshold
        inlier_count = int(np.sum(inlier_mask))

        if inlier_count > best_inlier_count:
            best_inlier_count = inlier_count
            best_result = (cx, cy, radius, inlier_mask)

    if best_result is None or best_inlier_count < 3:
        return None

    # 用所有内点重新拟合圆（Kåsa 法）
    cx, cy, radius, inlier_mask = best_result
    refined = _fit_circle_kasa(points_2d[inlier_mask])
    if refined is not None:
        rcx, rcy, rradius = refined
        # 重新计算内点
        dist_to_center = np.sqrt((points_2d[:, 0] - rcx) ** 2 + (points_2d[:, 1] - rcy) ** 2)
        dist_to_ring = np.abs(dist_to_center - rradius)
        refined_mask = dist_to_ring < threshold
        if int(np.sum(refined_mask)) >= best_inlier_count * 0.5:
            return rcx, rcy, rradius, refined_mask

    return best_result


def _circle_from_three_points(
    p1: np.ndarray,
    p2: np.ndarray,
    p3: np.ndarray,
) -> tuple[float, float, float] | None:
    """三点定圆。

    Args:
        p1, p2, p3: 三个 2D 点

    Returns:
        (cx, cy, radius) 或 None（三点共线）
    """
    # 用行列式法计算外接圆
    ax, ay = p1
    bx, by = p2
    cx, cy = p3

    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return None

    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    radius = float(np.sqrt((ax - ux) ** 2 + (ay - uy) ** 2))
    return float(ux), float(uy), radius


def _fit_circle_kasa(
    points_2d: np.ndarray,
) -> tuple[float, float, float] | None:
    """Kåsa 代数法拟合 2D 圆。

    与 cylinder_extractor 中的 _fit_circle_2d 相同算法，
    此处为模块自包含实现以避免跨模块依赖。

    Args:
        points_2d: (N, 2) 2D 点集

    Returns:
        (cx, cy, radius) 或 None
    """
    if len(points_2d) < 3:
        return None

    x = points_2d[:, 0]
    y = points_2d[:, 1]
    A_mat = np.column_stack([x, y, np.ones_like(x)])
    z = x**2 + y**2

    try:
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
