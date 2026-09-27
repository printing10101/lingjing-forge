"""ADR-007 RANSAC 特征 → GeometryFeatures 派生器.

ADR-020 思路 1 的 P0 数据解锁工具（几何部分）。将 ADR-007 几何特征提取的
变长 ``ExtractedFeature`` 列表 + mesh vertices 派生为固定的 ``GeometryFeatures``，
让融合架构在不伪造数据的前提下获得真实几何输入.

设计边界（与 ADR-020 §1.3 一致）
--------------------------------
- **真实派生，非伪造**：bbox/symmetry/complexity/feature_vector 全部从
  ADR-007 真实产出计算，不创造任何新数据
- **变长 → 固定 32 维**：feature_vector 采用分桶 + top-K + zero-pad 策略，
  与 ``GeometryEncoder.feature_dim=32`` 对齐
- **物理尺度归一化**：area/radius 用典型 CNC 零件尺度归一化（工程合理，
  非凭空创造数据），截断到 [0, 1]
- **审核状态感知**：使用 ``effective_params()`` 尊重工程师审核结果
  （edited 状态用 edited_params）
- **缺失显式标记**：vertices=None 时 bbox 用 (0,0,0) 填充并标记
- **纯 Python 实现**：无 numpy/torch 硬依赖，与 ``DynamicsStateBridge``
  风格一致，保证可测试性

派生路径表（与 ADR-020 §1.3 一致）
+--------------------+---------------------------+--------------------------------+
| GeometryFeatures   | ADR-007 派生源            | 派生方式                       |
+--------------------+---------------------------+--------------------------------+
| bbox_dimensions    | mesh vertices             | per-axis max - min             |
| symmetry_score     | plane normals             | 法向夹角对称对占比             |
| complexity_score   | plane/cyl/hole/boss 计数  | min(total/60, 1.0)             |
| feature_vector     | plane/cyl/hole params     | 分桶 top-K + 物理归一化 (32维) |
+--------------------+---------------------------+--------------------------------+

feature_vector 分桶设计（32 维）
--------------------------------
- plane 桶: 8 特征 × 2 参数 (area_mm2_norm, confidence) = 16 维
- cylinder 桶: 4 特征 × 2 参数 (radius_mm_norm, confidence) = 8 维
  （boss 归入 cylinder 桶，外圆柱与内圆柱几何相似）
- hole 桶: 4 特征 × 2 参数 (radius_mm_norm, confidence) = 8 维
- 合计 16 + 8 + 8 = 32 维
- 各桶按 confidence 降序取 top-K，不足 zero-pad

对应 ADR：ADR-020 思路 1 / ADR-007 几何特征辅助提取
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Sequence

from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureReviewStatus,
    FeatureType,
)
from app.plugins.world_model.unified_state import GeometryFeatures

logger = logging.getLogger(__name__)


# 派生常量

FEATURE_VECTOR_DIM: int = 32
"""feature_vector 固定维度，与 ``GeometryEncoder.feature_dim`` 对齐."""

# 分桶配置：plane 16维 + cylinder 8维 + hole 8维 = 32
PLANE_BUCKET_CAPACITY: int = 8
PLANE_BUCKET_PARAMS: int = 2  # area_mm2_norm, confidence
CYLINDER_BUCKET_CAPACITY: int = 4
CYLINDER_BUCKET_PARAMS: int = 2  # radius_mm_norm, confidence
HOLE_BUCKET_CAPACITY: int = 4
HOLE_BUCKET_PARAMS: int = 2  # radius_mm_norm, confidence

assert (
    PLANE_BUCKET_CAPACITY * PLANE_BUCKET_PARAMS
    + CYLINDER_BUCKET_CAPACITY * CYLINDER_BUCKET_PARAMS
    + HOLE_BUCKET_CAPACITY * HOLE_BUCKET_PARAMS
) == FEATURE_VECTOR_DIM, "分桶容量之和必须等于 FEATURE_VECTOR_DIM"

# 复杂度归一化上界：plane_max(20) + cylinder_max(10) + hole_max(30) = 60
# （与 FeatureExtractionConfig 默认值对齐）
COMPLEXITY_NORM_BOUND: int = 60

# 对称性判定阈值：|cos θ| > 0.95 视为对称对（平面法向平行或反平行）
SYMMETRY_PARALLEL_THRESHOLD: float = 0.95

# 物理尺度归一化（典型 CNC 零件尺度，工程合理非伪造）
AREA_NORM_MM2: float = 10000.0
"""平面面积归一化尺度. 10000 mm² = 100 cm²，典型 CNC 零件平面面积上限."""
RADIUS_NORM_MM: float = 50.0
"""圆柱/孔半径归一化尺度. 50 mm，典型 CNC 零件圆柱/孔半径上限."""


# 派生结果


@dataclass
class DerivationResult:
    """``GeometryFeaturesDeriver`` 派生结果.

    同时返回派生后的 ``GeometryFeatures`` 与完整性诊断信息，让调用方
    （``WorldModelService`` / ``WorldModelPlugin``）决策是否走融合路径.

    Attributes
    ----------
    geometry : GeometryFeatures
        派生后的几何特征. 缺失字段用中性值填充.
    defaulted_fields : list[str]
        数据源缺失导致用中性值填充的字段（如 vertices=None 时的
        ``bbox_dimensions``）. 不包含"真实计算结果为零"的字段
        （如无特征时 feature_vector 全零是真实结果）.
    derivation_notes : list[str]
        派生过程中的备注（降级、截断、桶溢出、审核过滤等），供 MLflow 追踪.
    source : str
        数据来源标记，固定为 ``"adr007_ransac"``.
    """

    geometry: GeometryFeatures
    defaulted_fields: list[str] = field(default_factory=list)
    derivation_notes: list[str] = field(default_factory=list)
    source: str = "adr007_ransac"

    @property
    def is_complete(self) -> bool:
        """是否所有字段都有真实数据源（无中性值填充）."""
        return len(self.defaulted_fields) == 0

    @property
    def completeness_ratio(self) -> float:
        """完整性比例 = 真实数据源字段数 / 4.

        4 个字段：bbox_dimensions / feature_vector / symmetry_score /
        complexity_score. 其中只有 bbox_dimensions 在 vertices=None 时
        会被标记为 defaulted（其他字段从 features 计算，即使结果为零也是
        真实计算）.
        """
        real_field_count = 4 - len(self.defaulted_fields)
        return real_field_count / 4

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（供 MLflow 记录与日志输出）."""
        geo = self.geometry
        return {
            "geometry": {
                "bbox_dimensions": list(geo.bbox_dimensions),
                "feature_vector": list(geo.feature_vector),
                "symmetry_score": geo.symmetry_score,
                "complexity_score": geo.complexity_score,
            },
            "defaulted_fields": list(self.defaulted_fields),
            "derivation_notes": list(self.derivation_notes),
            "source": self.source,
            "is_complete": self.is_complete,
            "completeness_ratio": self.completeness_ratio,
        }


# 派生器


class GeometryFeaturesDeriver:
    """ADR-007 RANSAC 特征 → ``GeometryFeatures`` 派生器.

    所有方法均为类方法或静态方法，无状态，线程安全.

    使用示例
    --------
    >>> from app.feature_extraction.feature_store import ExtractedFeature
    >>> features = [
    ...     ExtractedFeature(
    ...         feature_id="p1",
    ...         feature_type="plane",
    ...         params={"normal": [0, 0, 1], "offset": 0.0, "area_mm2": 500.0},
    ...         confidence=0.95,
    ...     ),
    ... ]
    >>> vertices = [[0, 0, 0], [10, 0, 0], [0, 20, 0], [0, 0, 30]]
    >>> result = GeometryFeaturesDeriver.from_feature_extraction(features, vertices)
    >>> result.geometry.bbox_dimensions
    (10.0, 20.0, 30.0)
    >>> result.geometry.complexity_score
    0.016666...
    """

    DEFAULT_BBOX: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """vertices 缺失时 bbox 的中性填充值. (0,0,0) 不会引入虚假尺度信号."""

    DEGRADE_THRESHOLD: int = 1
    """降级阈值. ``defaulted_fields`` 数量 >= 此值时 ``should_degrade`` 为 True.

    取 1 的理由：bbox_dimensions 是几何最基本的尺度信号，若 vertices 缺失
    则 bbox 为 (0,0,0)，融合 embedding 会学到错误的尺度表示，应降级到
    传统路径. 其他字段（feature_vector/symmetry/complexity）从 features
    计算，即使 features 为空也是真实结果（零件确实无特征），不视为 defaulted.
    """

    # 主入口

    @classmethod
    def from_feature_extraction(
        cls,
        features: list[ExtractedFeature],
        vertices: Sequence[Sequence[float]] | Any | None = None,
        filter_reviewed: bool = False,
    ) -> DerivationResult:
        """从 ADR-007 特征提取结果派生 ``GeometryFeatures``.

        Parameters
        ----------
        features : list[ExtractedFeature]
            ADR-007 ``FeatureExtractionPipeline`` 产出的特征列表
            （plane/cylinder/hole/boss/unknown）.
        vertices : array-like, optional
            mesh 顶点数组，形状 (N, 3). 用于派生 bbox_dimensions.
            接受 list[list[float]] / tuple / numpy ndarray.
            None 时 bbox 用 (0,0,0) 填充并标记 defaulted.
        filter_reviewed : bool
            True 时只保留 ``confirmed`` / ``edited`` 状态的特征
            （与 ADR-007 导出 ``confirmed_features.json`` 的过滤规则一致）.
            False 时用全部特征（适用于审核前的早期预测场景）.

        Returns
        -------
        DerivationResult
            包含 ``GeometryFeatures`` 与完整性诊断. 不抛异常.

        Notes
        -----
        本方法 **不伪造数据**：
        - bbox 从真实 vertices 计算，vertices=None 时用 (0,0,0) 中性值填充
          并显式标记在 ``defaulted_fields``
        - feature_vector 从真实特征 params 计算，无特征时全零（真实结果）
        - symmetry/complexity 从真实特征统计计算
        - 调用方应检查 ``is_complete`` / ``should_degrade``，决定是否走融合路径
        """
        notes: list[str] = []
        defaulted: list[str] = []

        # 审核状态过滤
        working_features = list(features)
        if filter_reviewed:
            kept = [
                f
                for f in working_features
                if f.review_status in (FeatureReviewStatus.CONFIRMED.value, FeatureReviewStatus.EDITED.value)
            ]
            dropped = len(working_features) - len(kept)
            if dropped > 0:
                notes.append(f"filter_reviewed=True: 过滤掉 {dropped} 条未审核特征")
            working_features = kept

        # 1. bbox_dimensions
        bbox, bbox_defaulted = cls._derive_bbox(vertices)
        if bbox_defaulted:
            defaulted.append("bbox_dimensions")
            notes.append("vertices 为空或无效，bbox_dimensions 用 (0,0,0) 填充")

        # 2. 统计特征类型 + 收集 plane normals
        type_counts: dict[str, int] = {}
        plane_normals: list[list[float]] = []
        for f in working_features:
            type_counts[f.feature_type] = type_counts.get(f.feature_type, 0) + 1
            if f.feature_type == FeatureType.PLANE.value:
                params = f.effective_params()
                normal = params.get("normal")
                if isinstance(normal, (list, tuple)) and len(normal) >= 3:
                    try:
                        plane_normals.append([float(normal[0]), float(normal[1]), float(normal[2])])
                    except (TypeError, ValueError):
                        pass

        # 3. symmetry_score
        symmetry = cls._derive_symmetry(plane_normals)
        if len(plane_normals) < 2:
            notes.append(f"plane 特征数 {len(plane_normals)} < 2，symmetry_score=0.0（无法判断对称性，非默认填充）")

        # 4. complexity_score
        complexity = cls._derive_complexity(type_counts)

        # 5. feature_vector
        feature_vector = cls._derive_feature_vector(working_features)
        if not working_features:
            notes.append("features 为空，feature_vector 全零（真实结果：零件无特征）")

        # 桶溢出备注
        plane_count = type_counts.get(FeatureType.PLANE.value, 0)
        cyl_count = type_counts.get(FeatureType.CYLINDER.value, 0) + type_counts.get(FeatureType.BOSS.value, 0)
        hole_count = type_counts.get(FeatureType.HOLE.value, 0)
        if plane_count > PLANE_BUCKET_CAPACITY:
            notes.append(f"plane 特征 {plane_count} 超过桶容量 {PLANE_BUCKET_CAPACITY}，按 confidence 降序截断")
        if cyl_count > CYLINDER_BUCKET_CAPACITY:
            notes.append(
                f"cylinder+boss 特征 {cyl_count} 超过桶容量 {CYLINDER_BUCKET_CAPACITY}，按 confidence 降序截断"
            )
        if hole_count > HOLE_BUCKET_CAPACITY:
            notes.append(f"hole 特征 {hole_count} 超过桶容量 {HOLE_BUCKET_CAPACITY}，按 confidence 降序截断")

        geometry = GeometryFeatures(
            bbox_dimensions=bbox,
            feature_vector=feature_vector,
            symmetry_score=symmetry,
            complexity_score=complexity,
        )

        return DerivationResult(
            geometry=geometry,
            defaulted_fields=defaulted,
            derivation_notes=notes,
            source="adr007_ransac",
        )

    # 子派生器

    @classmethod
    def _derive_bbox(
        cls,
        vertices: Sequence[Sequence[float]] | Any | None,
    ) -> tuple[tuple[float, float, float], bool]:
        """从 mesh vertices 派生 bbox_dimensions.

        单个无效元素（None / 不足 3 维 / 不可转 float 的类型）会被跳过，
        不影响其他有效顶点. 仅当 vertices 本身为 None / 不可迭代 / 全部
        元素无效时才返回 defaulted=True.

        Returns
        -------
        (bbox, defaulted)
            bbox: (length, width, height) mm. defaulted=True 表示 vertices
            无有效顶点，bbox 为 (0,0,0) 中性值.
        """
        if vertices is None:
            return cls.DEFAULT_BBOX, True

        pts: list[tuple[float, float, float]] = []
        try:
            for v in vertices:
                try:
                    if v is None or len(v) < 3:
                        continue
                    pts.append((float(v[0]), float(v[1]), float(v[2])))
                except (TypeError, ValueError, IndexError):
                    continue
        except TypeError:
            # vertices 本身不可迭代
            return cls.DEFAULT_BBOX, True

        if not pts:
            return cls.DEFAULT_BBOX, True

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        bbox = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
        return bbox, False

    @classmethod
    def _derive_symmetry(cls, plane_normals: list[list[float]]) -> float:
        """从 plane 法向量派生 symmetry_score.

        算法：对所有平面法向量两两计算 |cos θ|（绝对值，因法向方向有歧义），
        统计 |cos θ| > SYMMETRY_PARALLEL_THRESHOLD (0.95) 的对数占比.
        对称平面越多，零件对称性越高.

        Parameters
        ----------
        plane_normals : list[list[float]]
            平面法向量列表，每个为 [nx, ny, nz].

        Returns
        -------
        float
            symmetry_score ∈ [0, 1]. plane 数 < 2 时返回 0.0（无法判断）.
        """
        n = len(plane_normals)
        if n < 2:
            return 0.0

        symmetric_pairs = 0
        total_pairs = 0
        for i in range(n):
            ni = plane_normals[i]
            norm_i = math.sqrt(ni[0] ** 2 + ni[1] ** 2 + ni[2] ** 2)
            if norm_i < 1e-9:
                continue
            for j in range(i + 1, n):
                nj = plane_normals[j]
                norm_j = math.sqrt(nj[0] ** 2 + nj[1] ** 2 + nj[2] ** 2)
                if norm_j < 1e-9:
                    continue
                dot = ni[0] * nj[0] + ni[1] * nj[1] + ni[2] * nj[2]
                cos_abs = abs(dot / (norm_i * norm_j))
                total_pairs += 1
                if cos_abs > SYMMETRY_PARALLEL_THRESHOLD:
                    symmetric_pairs += 1

        if total_pairs == 0:
            return 0.0
        return min(symmetric_pairs / total_pairs, 1.0)

    @classmethod
    def _derive_complexity(cls, type_counts: dict[str, int]) -> float:
        """从特征计数派生 complexity_score.

        公式：min(total / COMPLEXITY_NORM_BOUND, 1.0)
        其中 total = plane + cylinder + hole + boss 计数，
        COMPLEXITY_NORM_BOUND=60（与 FeatureExtractionConfig 默认上限对齐）.

        unknown 特征不计入（无法判断复杂度贡献）.
        """
        total = sum(
            type_counts.get(t, 0)
            for t in (
                FeatureType.PLANE.value,
                FeatureType.CYLINDER.value,
                FeatureType.HOLE.value,
                FeatureType.BOSS.value,
            )
        )
        return min(total / COMPLEXITY_NORM_BOUND, 1.0)

    @classmethod
    def _derive_feature_vector(
        cls,
        features: list[ExtractedFeature],
    ) -> list[float]:
        """从变长特征列表派生固定 32 维 feature_vector.

        分桶策略（与模块 docstring 一致）：
        - plane 桶: 8 特征 × (area_mm2_norm, confidence) = 16 维
        - cylinder 桶: 4 特征 × (radius_mm_norm, confidence) = 8 维
          （boss 归入 cylinder 桶）
        - hole 桶: 4 特征 × (radius_mm_norm, confidence) = 8 维

        各桶按 confidence 降序取 top-K，不足 zero-pad.
        unknown 特征忽略（无几何参数）.
        """
        planes = [f for f in features if f.feature_type == FeatureType.PLANE.value]
        cylinders = [f for f in features if f.feature_type in (FeatureType.CYLINDER.value, FeatureType.BOSS.value)]
        holes = [f for f in features if f.feature_type == FeatureType.HOLE.value]

        # 按 confidence 降序
        planes.sort(key=lambda f: f.confidence, reverse=True)
        cylinders.sort(key=lambda f: f.confidence, reverse=True)
        holes.sort(key=lambda f: f.confidence, reverse=True)

        vec: list[float] = []

        # plane 桶：(area_mm2_norm, confidence) × 8
        for f in planes[:PLANE_BUCKET_CAPACITY]:
            params = f.effective_params()
            area = float(params.get("area_mm2", 0.0))
            vec.append(min(max(area / AREA_NORM_MM2, 0.0), 1.0))
            vec.append(min(max(float(f.confidence), 0.0), 1.0))
        while len(vec) < PLANE_BUCKET_CAPACITY * PLANE_BUCKET_PARAMS:
            vec.append(0.0)

        # cylinder 桶（含 boss）：(radius_mm_norm, confidence) × 4
        cylinder_start = PLANE_BUCKET_CAPACITY * PLANE_BUCKET_PARAMS
        for f in cylinders[:CYLINDER_BUCKET_CAPACITY]:
            params = f.effective_params()
            radius = float(params.get("radius_mm", 0.0))
            vec.append(min(max(radius / RADIUS_NORM_MM, 0.0), 1.0))
            vec.append(min(max(float(f.confidence), 0.0), 1.0))
        while len(vec) < cylinder_start + CYLINDER_BUCKET_CAPACITY * CYLINDER_BUCKET_PARAMS:
            vec.append(0.0)

        # hole 桶：(radius_mm_norm, confidence) × 4
        hole_start = cylinder_start + CYLINDER_BUCKET_CAPACITY * CYLINDER_BUCKET_PARAMS
        for f in holes[:HOLE_BUCKET_CAPACITY]:
            params = f.effective_params()
            radius = float(params.get("radius_mm", 0.0))
            vec.append(min(max(radius / RADIUS_NORM_MM, 0.0), 1.0))
            vec.append(min(max(float(f.confidence), 0.0), 1.0))
        while len(vec) < hole_start + HOLE_BUCKET_CAPACITY * HOLE_BUCKET_PARAMS:
            vec.append(0.0)

        # 防御性：确保恰好 32 维
        if len(vec) != FEATURE_VECTOR_DIM:
            # 理论上不会走到这里（分桶容量已 assert），但防御性截断/补零
            if len(vec) > FEATURE_VECTOR_DIM:
                vec = vec[:FEATURE_VECTOR_DIM]
            else:
                vec.extend([0.0] * (FEATURE_VECTOR_DIM - len(vec)))

        return vec

    # 降级判断

    @staticmethod
    def should_degrade(
        result: DerivationResult,
        threshold: int | None = None,
    ) -> bool:
        """判断是否应降级到传统路径.

        Parameters
        ----------
        result : DerivationResult
            ``from_feature_extraction`` 的返回值.
        threshold : int, optional
            降级阈值. None 时使用 ``DEGRADE_THRESHOLD`` (1).

        Returns
        -------
        bool
            True 表示 ``defaulted_fields`` 数量 >= 阈值，应降级.
        """
        thresh = GeometryFeaturesDeriver.DEGRADE_THRESHOLD if threshold is None else threshold
        return len(result.defaulted_fields) >= thresh


__all__ = [
    "FEATURE_VECTOR_DIM",
    "PLANE_BUCKET_CAPACITY",
    "CYLINDER_BUCKET_CAPACITY",
    "HOLE_BUCKET_CAPACITY",
    "COMPLEXITY_NORM_BOUND",
    "SYMMETRY_PARALLEL_THRESHOLD",
    "AREA_NORM_MM2",
    "RADIUS_NORM_MM",
    "DerivationResult",
    "GeometryFeaturesDeriver",
]
