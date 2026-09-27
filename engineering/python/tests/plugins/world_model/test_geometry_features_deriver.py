"""``GeometryFeaturesDeriver`` 单元测试.

对应 ADR-020 思路 1 P0 数据解锁工具（几何部分）.

验收标准：
- 派生常量一致性（分桶容量和=32、复杂度上界=60）
- bbox 从 vertices 真实计算，None/无效时用 (0,0,0) 中性填充并标记 defaulted
- symmetry_score 平面法向平行/反平行对称对占比正确
- complexity_score 归一化到 [0, 1]，超过 60 截断为 1.0
- feature_vector 恰好 32 维，按 confidence 降序取 top-K，不足 zero-pad
- 物理归一化（area/radius）截断到 [0, 1]
- ``filter_reviewed=True`` 只保留 confirmed/edited 特征
- ``effective_params()`` 尊重工程师审核（edited 状态用 edited_params）
- ``DerivationResult`` 诊断字段（is_complete/completeness_ratio/to_dict）准确
- ``should_degrade`` 降级阈值判断正确
"""

from __future__ import annotations

import pytest

from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureReviewStatus,
    FeatureType,
)
from app.plugins.world_model.geometry_features_deriver import (
    AREA_NORM_MM2,
    COMPLEXITY_NORM_BOUND,
    CYLINDER_BUCKET_CAPACITY,
    CYLINDER_BUCKET_PARAMS,
    FEATURE_VECTOR_DIM,
    GeometryFeaturesDeriver,
    HOLE_BUCKET_CAPACITY,
    HOLE_BUCKET_PARAMS,
    PLANE_BUCKET_CAPACITY,
    PLANE_BUCKET_PARAMS,
    RADIUS_NORM_MM,
    SYMMETRY_PARALLEL_THRESHOLD,
)
from app.plugins.world_model.unified_state import GeometryFeatures


# 辅助构造函数


def make_plane(
    feature_id: str = "p",
    normal: list[float] | None = None,
    area_mm2: float = 100.0,
    confidence: float = 0.9,
    review_status: str = FeatureReviewStatus.CONFIRMED.value,
    edited_params: dict | None = None,
) -> ExtractedFeature:
    """构造平面特征."""
    if normal is None:
        normal = [0.0, 0.0, 1.0]
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type=FeatureType.PLANE.value,
        params={"normal": normal, "offset": 0.0, "area_mm2": area_mm2},
        confidence=confidence,
        review_status=review_status,
        edited_params=edited_params or {},
    )


def make_cylinder(
    feature_id: str = "c",
    radius_mm: float = 10.0,
    confidence: float = 0.85,
    review_status: str = FeatureReviewStatus.CONFIRMED.value,
) -> ExtractedFeature:
    """构造圆柱特征."""
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type=FeatureType.CYLINDER.value,
        params={
            "axis": [0.0, 0.0, 1.0],
            "center": [0.0, 0.0, 0.0],
            "radius_mm": radius_mm,
            "height_mm": 20.0,
        },
        confidence=confidence,
        review_status=review_status,
    )


def make_hole(
    feature_id: str = "h",
    radius_mm: float = 5.0,
    confidence: float = 0.8,
    review_status: str = FeatureReviewStatus.CONFIRMED.value,
) -> ExtractedFeature:
    """构造孔特征."""
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type=FeatureType.HOLE.value,
        params={
            "normal": [0.0, 0.0, 1.0],
            "center": [0.0, 0.0, 0.0],
            "radius_mm": radius_mm,
            "depth_mm": 10.0,
        },
        confidence=confidence,
        review_status=review_status,
    )


def make_boss(
    feature_id: str = "b",
    radius_mm: float = 8.0,
    confidence: float = 0.75,
) -> ExtractedFeature:
    """构造凸台特征（应归入 cylinder 桶）."""
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type=FeatureType.BOSS.value,
        params={
            "normal": [0.0, 0.0, 1.0],
            "center": [0.0, 0.0, 0.0],
            "radius_mm": radius_mm,
            "height_mm": 5.0,
        },
        confidence=confidence,
    )


def make_unknown(feature_id: str = "u") -> ExtractedFeature:
    """构造未分类特征（应被忽略）."""
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type=FeatureType.UNKNOWN.value,
        params={},
        confidence=0.3,
    )


# 1. 派生常量一致性


@pytest.mark.unit
@pytest.mark.plugins
class TestDerivationConstants:
    """派生常量一致性校验."""

    def test_feature_vector_dim_is_32(self):
        """feature_vector 固定维度为 32（与 GeometryEncoder.feature_dim 对齐）."""
        assert FEATURE_VECTOR_DIM == 32

    def test_bucket_capacity_sum_equals_dim(self):
        """分桶容量 × 参数数之和必须等于 32."""
        total = (
            PLANE_BUCKET_CAPACITY * PLANE_BUCKET_PARAMS
            + CYLINDER_BUCKET_CAPACITY * CYLINDER_BUCKET_PARAMS
            + HOLE_BUCKET_CAPACITY * HOLE_BUCKET_PARAMS
        )
        assert total == FEATURE_VECTOR_DIM

    def test_plane_bucket_layout(self):
        """plane 桶: 8 × 2 = 16 维."""
        assert PLANE_BUCKET_CAPACITY == 8
        assert PLANE_BUCKET_PARAMS == 2

    def test_cylinder_bucket_layout(self):
        """cylinder 桶: 4 × 2 = 8 维."""
        assert CYLINDER_BUCKET_CAPACITY == 4
        assert CYLINDER_BUCKET_PARAMS == 2

    def test_hole_bucket_layout(self):
        """hole 桶: 4 × 2 = 8 维."""
        assert HOLE_BUCKET_CAPACITY == 4
        assert HOLE_BUCKET_PARAMS == 2

    def test_complexity_norm_bound_is_60(self):
        """复杂度归一化上界为 60（plane_max 20 + cyl_max 10 + hole_max 30）."""
        assert COMPLEXITY_NORM_BOUND == 60

    def test_symmetry_threshold_is_0_95(self):
        """对称性阈值为 0.95."""
        assert SYMMETRY_PARALLEL_THRESHOLD == 0.95

    def test_physical_norm_scales_positive(self):
        """物理归一化尺度为正值."""
        assert AREA_NORM_MM2 > 0
        assert RADIUS_NORM_MM > 0


# 2. bbox_dimensions 派生


@pytest.mark.unit
@pytest.mark.plugins
class TestDeriveBbox:
    """bbox_dimensions 派生行为."""

    def test_bbox_from_complete_vertices(self):
        """完整 vertices 正确计算 per-axis max - min."""
        vertices = [[0, 0, 0], [10, 0, 0], [0, 20, 0], [0, 0, 30]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=vertices,
        )
        assert result.geometry.bbox_dimensions == (10.0, 20.0, 30.0)
        assert "bbox_dimensions" not in result.defaulted_fields

    def test_bbox_none_vertices_defaults_to_zero(self):
        """vertices=None 时 bbox 用 (0,0,0) 填充并标记 defaulted."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert result.geometry.bbox_dimensions == (0.0, 0.0, 0.0)
        assert "bbox_dimensions" in result.defaulted_fields
        assert result.is_complete is False

    def test_bbox_empty_list_defaults_to_zero(self):
        """空 vertices 列表时 bbox 用 (0,0,0) 填充."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=[],
        )
        assert result.geometry.bbox_dimensions == (0.0, 0.0, 0.0)
        assert "bbox_dimensions" in result.defaulted_fields

    def test_bbox_invalid_vertices_defaults_to_zero(self):
        """无效 vertices（含 None 元素 / 不足 3 维）跳过，全无效则 defaulted."""
        vertices = [None, [1, 2], "not_a_point", [0, 0, 0]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=vertices,
        )
        # 仅 [0,0,0] 有效，bbox=(0,0,0) 但非 defaulted（有 1 个有效点）
        assert result.geometry.bbox_dimensions == (0.0, 0.0, 0.0)
        assert "bbox_dimensions" not in result.defaulted_fields

    def test_bbox_all_invalid_vertices_defaults_to_zero(self):
        """全部无效 vertices 视同 None."""
        vertices = [None, [1, 2], "not_a_point"]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=vertices,
        )
        assert result.geometry.bbox_dimensions == (0.0, 0.0, 0.0)
        assert "bbox_dimensions" in result.defaulted_fields

    def test_bbox_negative_coordinates(self):
        """负坐标正确计算（max - min）."""
        vertices = [[-5, -5, -5], [5, 5, 5]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=vertices,
        )
        assert result.geometry.bbox_dimensions == (10.0, 10.0, 10.0)

    def test_bbox_float_coordinates(self):
        """浮点坐标正确计算."""
        vertices = [[1.5, 2.5, 3.5], [4.5, 6.5, 8.5]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=vertices,
        )
        assert result.geometry.bbox_dimensions == (3.0, 4.0, 5.0)


# 3. symmetry_score 派生


@pytest.mark.unit
@pytest.mark.plugins
class TestDeriveSymmetry:
    """symmetry_score 派生行为."""

    def test_symmetry_less_than_two_planes_returns_zero(self):
        """plane 数 < 2 时返回 0.0（无法判断对称性）."""
        features = [make_plane("p1", normal=[0, 0, 1])]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score == 0.0

    def test_symmetry_no_planes_returns_zero(self):
        """无 plane 特征时 symmetry_score=0.0."""
        features = [make_cylinder(), make_hole()]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score == 0.0

    def test_symmetry_parallel_planes_returns_one(self):
        """两个平行 plane（法向相同）→ symmetry_score=1.0."""
        features = [
            make_plane("p1", normal=[0, 0, 1]),
            make_plane("p2", normal=[0, 0, 1]),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score == pytest.approx(1.0)

    def test_symmetry_antiparallel_planes_returns_one(self):
        """两个反平行 plane（法向相反）→ |cos θ|=1 → symmetry_score=1.0.

        法向方向有歧义，对称性应取 |cos θ|.
        """
        features = [
            make_plane("p1", normal=[0, 0, 1]),
            make_plane("p2", normal=[0, 0, -1]),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score == pytest.approx(1.0)

    def test_symmetry_orthogonal_planes_returns_zero(self):
        """两个正交 plane（法向垂直）→ |cos θ|=0 → symmetry_score=0.0."""
        features = [
            make_plane("p1", normal=[0, 0, 1]),
            make_plane("p2", normal=[1, 0, 0]),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score == pytest.approx(0.0)

    def test_symmetry_partial_symmetry(self):
        """4 个 plane：2 对平行 + 2 个正交 → symmetry_score=1/3.

        平面对数：C(4,2)=6 对
        - p1=[0,0,1] vs p2=[0,0,1]: |cos|=1 
        - p1=[0,0,1] vs p3=[1,0,0]: |cos|=0 
        - p1=[0,0,1] vs p4=[1,0,0]: |cos|=0 
        - p2=[0,0,1] vs p3=[1,0,0]: |cos|=0 
        - p2=[0,0,1] vs p4=[1,0,0]: |cos|=0 
        - p3=[1,0,0] vs p4=[1,0,0]: |cos|=1 
        → 2/6 = 1/3
        """
        features = [
            make_plane("p1", normal=[0, 0, 1]),
            make_plane("p2", normal=[0, 0, 1]),
            make_plane("p3", normal=[1, 0, 0]),
            make_plane("p4", normal=[1, 0, 0]),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score == pytest.approx(1.0 / 3.0)

    def test_symmetry_zero_normal_vector_skipped(self):
        """零法向量被跳过（不影响其他对的对称性计算）."""
        features = [
            make_plane("p1", normal=[0, 0, 1]),
            make_plane("p2", normal=[0, 0, 0]),  # 零向量，跳过
            make_plane("p3", normal=[0, 0, 1]),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        # 只计算 p1 vs p3：|cos|=1 1/1 = 1.0
        assert result.geometry.symmetry_score == pytest.approx(1.0)

    def test_symmetry_score_bounded_to_one(self):
        """symmetry_score 不超过 1.0."""
        features = [
            make_plane("p1", normal=[0, 0, 1]),
            make_plane("p2", normal=[0, 0, 1]),
            make_plane("p3", normal=[0, 0, 1]),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.symmetry_score <= 1.0


# 4. complexity_score 派生


@pytest.mark.unit
@pytest.mark.plugins
class TestDeriveComplexity:
    """complexity_score 派生行为."""

    def test_complexity_empty_features_returns_zero(self):
        """无特征时 complexity_score=0.0."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert result.geometry.complexity_score == 0.0

    def test_complexity_single_plane(self):
        """1 个 plane → 1/60."""
        features = [make_plane()]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.complexity_score == pytest.approx(1.0 / 60.0)

    def test_complexity_mixed_types(self):
        """混合类型：2 plane + 1 cylinder + 1 hole + 1 boss = 5 → 5/60."""
        features = [
            make_plane("p1"),
            make_plane("p2"),
            make_cylinder("c1"),
            make_hole("h1"),
            make_boss("b1"),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.complexity_score == pytest.approx(5.0 / 60.0)

    def test_complexity_unknown_not_counted(self):
        """unknown 特征不计入复杂度."""
        features = [make_plane(), make_unknown(), make_unknown()]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        # 只算 1 个 plane 1/60
        assert result.geometry.complexity_score == pytest.approx(1.0 / 60.0)

    def test_complexity_overflow_truncated_to_one(self):
        """特征总数超过 60 时截断为 1.0."""
        # 70 个 plane
        features = [make_plane(f"p{i}") for i in range(70)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.complexity_score == 1.0

    def test_complexity_exactly_60_returns_one(self):
        """特征总数恰好 60 → complexity_score=1.0."""
        features = [make_plane(f"p{i}") for i in range(60)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.complexity_score == 1.0


# 5. feature_vector 派生


@pytest.mark.unit
@pytest.mark.plugins
class TestDeriveFeatureVector:
    """feature_vector 派生行为."""

    def test_feature_vector_always_32_dim(self):
        """feature_vector 恰好 32 维（空特征时全零）."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert len(result.geometry.feature_vector) == FEATURE_VECTOR_DIM
        assert all(v == 0.0 for v in result.geometry.feature_vector)

    def test_feature_vector_dim_with_mixed_features(self):
        """混合特征时 feature_vector 仍为 32 维."""
        features = [
            make_plane("p1", area_mm2=500.0, confidence=0.9),
            make_cylinder("c1", radius_mm=10.0, confidence=0.85),
            make_hole("h1", radius_mm=5.0, confidence=0.8),
            make_boss("b1", radius_mm=8.0, confidence=0.75),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert len(result.geometry.feature_vector) == FEATURE_VECTOR_DIM

    def test_feature_vector_plane_bucket_layout(self):
        """plane 桶前 16 维：(area_norm, confidence) × 8."""
        features = [make_plane("p1", area_mm2=500.0, confidence=0.9)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 第 0 位：area_norm = 500/10000 = 0.05
        assert vec[0] == pytest.approx(0.05)
        # 第 1 位：confidence = 0.9
        assert vec[1] == pytest.approx(0.9)
        # 第 2-15 位：zero-pad
        assert all(v == 0.0 for v in vec[2:16])

    def test_feature_vector_cylinder_bucket_layout(self):
        """cylinder 桶位 16-23：(radius_norm, confidence) × 4."""
        features = [make_cylinder("c1", radius_mm=10.0, confidence=0.85)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 位 16：radius_norm = 10/50 = 0.2
        assert vec[16] == pytest.approx(0.2)
        # 位 17：confidence = 0.85
        assert vec[17] == pytest.approx(0.85)
        # 位 18-23：zero-pad
        assert all(v == 0.0 for v in vec[18:24])

    def test_feature_vector_hole_bucket_layout(self):
        """hole 桶位 24-31：(radius_norm, confidence) × 4."""
        features = [make_hole("h1", radius_mm=5.0, confidence=0.8)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 位 24：radius_norm = 5/50 = 0.1
        assert vec[24] == pytest.approx(0.1)
        # 位 25：confidence = 0.8
        assert vec[25] == pytest.approx(0.8)
        # 位 26-31：zero-pad
        assert all(v == 0.0 for v in vec[26:32])

    def test_feature_vector_boss_goes_to_cylinder_bucket(self):
        """boss 特征归入 cylinder 桶（位 16-23）."""
        features = [make_boss("b1", radius_mm=8.0, confidence=0.75)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 位 16：radius_norm = 8/50 = 0.16
        assert vec[16] == pytest.approx(0.16)
        assert vec[17] == pytest.approx(0.75)
        # plane 桶全零
        assert all(v == 0.0 for v in vec[0:16])

    def test_feature_vector_unknown_ignored(self):
        """unknown 特征不出现在 feature_vector 任何桶."""
        features = [make_unknown()]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        assert len(vec) == FEATURE_VECTOR_DIM
        assert all(v == 0.0 for v in vec)

    def test_feature_vector_sorted_by_confidence_desc(self):
        """同桶内按 confidence 降序取 top-K."""
        features = [
            make_plane("p1", area_mm2=100.0, confidence=0.5),
            make_plane("p2", area_mm2=200.0, confidence=0.95),
            make_plane("p3", area_mm2=300.0, confidence=0.7),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # top-1 应是 p2 (confidence=0.95)
        assert vec[1] == pytest.approx(0.95)
        # top-2 应是 p3 (confidence=0.7)
        assert vec[3] == pytest.approx(0.7)
        # top-3 应是 p1 (confidence=0.5)
        assert vec[5] == pytest.approx(0.5)

    def test_feature_vector_plane_bucket_overflow_truncated(self):
        """plane 超 8 个时按 confidence 降序截断到 8."""
        # 10 个 plane，confidence 从 0.1 到 1.0
        features = [make_plane(f"p{i}", area_mm2=100.0 * i, confidence=i / 10.0) for i in range(1, 11)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 前 16 维（8 个 plane × 2 参数）应有值
        assert any(v != 0.0 for v in vec[0:16])
        # 取 top-8：confidence 0.3..1.0（共 8 个，i=3..10）
        # top-1 confidence=1.0 (i=10), area=1000
        assert vec[0] == pytest.approx(1000.0 / AREA_NORM_MM2)
        assert vec[1] == pytest.approx(1.0)
        # 第 8 个（top-8）confidence=0.3 (i=3), area=300
        assert vec[14] == pytest.approx(300.0 / AREA_NORM_MM2)
        assert vec[15] == pytest.approx(0.3)

    def test_feature_vector_cylinder_bucket_includes_boss(self):
        """cylinder+boss 合并后按 confidence 降序取 top-4."""
        features = [
            make_cylinder("c1", radius_mm=10.0, confidence=0.6),
            make_boss("b1", radius_mm=8.0, confidence=0.95),
            make_cylinder("c2", radius_mm=12.0, confidence=0.7),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # top-1: boss b1 (confidence=0.95, radius=8)
        assert vec[16] == pytest.approx(8.0 / RADIUS_NORM_MM)
        assert vec[17] == pytest.approx(0.95)
        # top-2: cylinder c2 (confidence=0.7, radius=12)
        assert vec[18] == pytest.approx(12.0 / RADIUS_NORM_MM)
        assert vec[19] == pytest.approx(0.7)
        # top-3: cylinder c1 (confidence=0.6, radius=10)
        assert vec[20] == pytest.approx(10.0 / RADIUS_NORM_MM)
        assert vec[21] == pytest.approx(0.6)
        # top-4: zero-pad
        assert vec[22] == 0.0
        assert vec[23] == 0.0

    def test_feature_vector_area_normalization_truncated_to_one(self):
        """area 超过归一化尺度时截断为 1.0."""
        # area = 20000 mm² > AREA_NORM_MM2=10000
        features = [make_plane("p1", area_mm2=20000.0, confidence=0.9)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.feature_vector[0] == 1.0

    def test_feature_vector_radius_normalization_truncated_to_one(self):
        """radius 超过归一化尺度时截断为 1.0."""
        # radius = 100 mm > RADIUS_NORM_MM=50
        features = [make_cylinder("c1", radius_mm=100.0, confidence=0.9)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.feature_vector[16] == 1.0

    def test_feature_vector_confidence_truncated_to_one(self):
        """confidence > 1.0 时截断为 1.0（防御性）."""
        features = [
            ExtractedFeature(
                feature_id="p1",
                feature_type=FeatureType.PLANE.value,
                params={"normal": [0, 0, 1], "offset": 0.0, "area_mm2": 100.0},
                confidence=1.5,  # 异常值
            ),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert result.geometry.feature_vector[1] == 1.0


# 6. filter_reviewed 过滤行为


@pytest.mark.unit
@pytest.mark.plugins
class TestFilterReviewed:
    """审核状态过滤行为."""

    def test_filter_reviewed_false_keeps_all(self):
        """filter_reviewed=False 保留全部特征（含 pending/rejected）."""
        features = [
            make_plane("p1", confidence=0.9, review_status=FeatureReviewStatus.PENDING.value),
            make_plane("p2", confidence=0.8, review_status=FeatureReviewStatus.CONFIRMED.value),
            make_plane("p3", confidence=0.7, review_status=FeatureReviewStatus.REJECTED.value),
            make_plane("p4", confidence=0.6, review_status=FeatureReviewStatus.EDITED.value),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
            filter_reviewed=False,
        )
        # 全部 4 个 plane 进入复杂度计算
        assert result.geometry.complexity_score == pytest.approx(4.0 / 60.0)

    def test_filter_reviewed_true_keeps_confirmed_and_edited(self):
        """filter_reviewed=True 只保留 confirmed + edited."""
        features = [
            make_plane("p1", confidence=0.9, review_status=FeatureReviewStatus.PENDING.value),
            make_plane("p2", confidence=0.8, review_status=FeatureReviewStatus.CONFIRMED.value),
            make_plane("p3", confidence=0.7, review_status=FeatureReviewStatus.REJECTED.value),
            make_plane("p4", confidence=0.6, review_status=FeatureReviewStatus.EDITED.value),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
            filter_reviewed=True,
        )
        # 只 2 个 plane（p2 + p4）进入复杂度计算
        assert result.geometry.complexity_score == pytest.approx(2.0 / 60.0)
        # 备注记录了过滤
        assert any("filter_reviewed" in note for note in result.derivation_notes)

    def test_filter_reviewed_true_all_filtered_notes(self):
        """filter_reviewed=True 过滤掉全部特征时记录备注."""
        features = [
            make_plane("p1", review_status=FeatureReviewStatus.PENDING.value),
            make_plane("p2", review_status=FeatureReviewStatus.REJECTED.value),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
            filter_reviewed=True,
        )
        assert result.geometry.complexity_score == 0.0
        assert any("filter_reviewed" in note for note in result.derivation_notes)

    def test_filter_reviewed_true_no_drop_no_note(self):
        """filter_reviewed=True 但无特征被过滤时不记录过滤备注."""
        features = [
            make_plane("p1", review_status=FeatureReviewStatus.CONFIRMED.value),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
            filter_reviewed=True,
        )
        assert not any("filter_reviewed" in note for note in result.derivation_notes)


# 7. DerivationResult 诊断


@pytest.mark.unit
@pytest.mark.plugins
class TestDerivationResultDiagnostics:
    """DerivationResult 诊断字段."""

    def test_is_complete_with_full_data(self):
        """有 vertices + 特征 → is_complete=True."""
        features = [make_plane()]
        vertices = [[0, 0, 0], [10, 0, 0], [0, 20, 0], [0, 0, 30]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=vertices,
        )
        assert result.is_complete is True
        assert len(result.defaulted_fields) == 0

    def test_is_complete_false_when_bbox_missing(self):
        """vertices=None → is_complete=False."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert result.is_complete is False
        assert "bbox_dimensions" in result.defaulted_fields

    def test_completeness_ratio_full(self):
        """完整派生 → completeness_ratio=1.0."""
        features = [make_plane()]
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=vertices,
        )
        assert result.completeness_ratio == 1.0

    def test_completeness_ratio_three_quarters_when_bbox_missing(self):
        """仅 bbox 缺失 → completeness_ratio=3/4=0.75."""
        # 其他 3 字段从 features 计算（即使 features 为空也是真实结果）
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[make_plane()],
            vertices=None,
        )
        assert result.completeness_ratio == 0.75

    def test_source_is_adr007_ransac(self):
        """source 固定为 'adr007_ransac'."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert result.source == "adr007_ransac"

    def test_to_dict_round_trip(self):
        """to_dict 序列化包含全部字段."""
        features = [make_plane(area_mm2=500.0, confidence=0.9)]
        vertices = [[0, 0, 0], [10, 0, 0], [0, 20, 0], [0, 0, 30]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=vertices,
        )
        d = result.to_dict()
        assert d["source"] == "adr007_ransac"
        assert d["is_complete"] is True
        assert d["completeness_ratio"] == 1.0
        assert d["geometry"]["bbox_dimensions"] == [10.0, 20.0, 30.0]
        assert len(d["geometry"]["feature_vector"]) == FEATURE_VECTOR_DIM
        assert d["defaulted_fields"] == []
        assert isinstance(d["derivation_notes"], list)

    def test_derivation_notes_record_bucket_overflow(self):
        """桶溢出时备注记录截断."""
        # 10 个 plane 超过 8 桶容量
        features = [make_plane(f"p{i}", confidence=0.5) for i in range(10)]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert any("plane" in note and "截断" in note for note in result.derivation_notes)

    def test_derivation_notes_record_empty_features(self):
        """空 features 时备注记录"零件无特征"."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert any("features 为空" in note for note in result.derivation_notes)


# 8. should_degrade 降级判断


@pytest.mark.unit
@pytest.mark.plugins
class TestShouldDegrade:
    """should_degrade 降级阈值判断."""

    def test_should_degrade_true_when_bbox_missing(self):
        """bbox 缺失 → should_degrade=True（DEGRADE_THRESHOLD=1）."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[make_plane()],
            vertices=None,
        )
        assert GeometryFeaturesDeriver.should_degrade(result) is True

    def test_should_degrade_false_when_complete(self):
        """完整派生 → should_degrade=False."""
        features = [make_plane()]
        vertices = [[0, 0, 0], [1, 0, 0]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=vertices,
        )
        assert GeometryFeaturesDeriver.should_degrade(result) is False

    def test_should_degrade_respects_custom_threshold(self):
        """自定义 threshold=2 时，1 个 defaulted 不降级."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[make_plane()],
            vertices=None,
        )
        # defaulted_fields = ["bbox_dimensions"]，长度 1
        assert GeometryFeaturesDeriver.should_degrade(result, threshold=2) is False

    def test_degrade_threshold_constant_is_one(self):
        """DEGRADE_THRESHOLD 常量为 1（bbox 缺失即降级）."""
        assert GeometryFeaturesDeriver.DEGRADE_THRESHOLD == 1


# 9. effective_params 尊重工程师审核


@pytest.mark.unit
@pytest.mark.plugins
class TestEffectiveParams:
    """effective_params 尊重工程师审核结果."""

    def test_edited_plane_uses_edited_params(self):
        """edited 状态的 plane 使用 edited_params 的 area."""
        features = [
            ExtractedFeature(
                feature_id="p1",
                feature_type=FeatureType.PLANE.value,
                params={"normal": [0, 0, 1], "offset": 0.0, "area_mm2": 100.0},
                confidence=0.9,
                review_status=FeatureReviewStatus.EDITED.value,
                edited_params={
                    "normal": [0, 0, 1],
                    "offset": 0.0,
                    "area_mm2": 500.0,
                },
            ),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 使用 edited_params.area_mm2=500 500/10000=0.05
        assert vec[0] == pytest.approx(0.05)

    def test_edited_cylinder_uses_edited_radius(self):
        """edited 状态的 cylinder 使用 edited_params 的 radius."""
        features = [
            ExtractedFeature(
                feature_id="c1",
                feature_type=FeatureType.CYLINDER.value,
                params={
                    "axis": [0, 0, 1],
                    "center": [0, 0, 0],
                    "radius_mm": 10.0,
                    "height_mm": 20.0,
                },
                confidence=0.85,
                review_status=FeatureReviewStatus.EDITED.value,
                edited_params={
                    "axis": [0, 0, 1],
                    "center": [0, 0, 0],
                    "radius_mm": 25.0,
                    "height_mm": 20.0,
                },
            ),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 使用 edited_params.radius_mm=25 25/50=0.5
        assert vec[16] == pytest.approx(0.5)

    def test_confirmed_plane_uses_original_params(self):
        """confirmed 状态使用原始 params."""
        features = [
            make_plane(
                "p1",
                area_mm2=300.0,
                confidence=0.9,
                review_status=FeatureReviewStatus.CONFIRMED.value,
            ),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        vec = result.geometry.feature_vector
        # 原始 area=300 300/10000=0.03
        assert vec[0] == pytest.approx(0.03)

    def test_edited_plane_normal_used_for_symmetry(self):
        """edited 状态的 plane 法向使用 edited_params（影响 symmetry 计算）."""
        features = [
            ExtractedFeature(
                feature_id="p1",
                feature_type=FeatureType.PLANE.value,
                params={"normal": [0, 0, 1], "offset": 0.0, "area_mm2": 100.0},
                confidence=0.9,
                review_status=FeatureReviewStatus.EDITED.value,
                edited_params={
                    "normal": [1, 0, 0],
                    "offset": 0.0,
                    "area_mm2": 100.0,
                },
            ),
            ExtractedFeature(
                feature_id="p2",
                feature_type=FeatureType.PLANE.value,
                params={"normal": [0, 0, 1], "offset": 0.0, "area_mm2": 100.0},
                confidence=0.9,
                review_status=FeatureReviewStatus.CONFIRMED.value,
            ),
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        # p1 edited 法向 [1,0,0]，p2 法向 [0,0,1]，正交 symmetry=0
        assert result.geometry.symmetry_score == pytest.approx(0.0)


# 10. 集成场景


@pytest.mark.unit
@pytest.mark.plugins
class TestIntegrationScenarios:
    """端到端集成场景."""

    def test_realistic_part_derivation(self):
        """真实零件派生：3 plane + 2 cylinder + 1 hole + 1 boss."""
        features = [
            make_plane("p1", normal=[0, 0, 1], area_mm2=5000.0, confidence=0.95),
            make_plane("p2", normal=[0, 0, 1], area_mm2=3000.0, confidence=0.88),
            make_plane("p3", normal=[1, 0, 0], area_mm2=2000.0, confidence=0.82),
            make_cylinder("c1", radius_mm=15.0, confidence=0.91),
            make_cylinder("c2", radius_mm=8.0, confidence=0.78),
            make_hole("h1", radius_mm=4.0, confidence=0.85),
            make_boss("b1", radius_mm=10.0, confidence=0.72),
        ]
        vertices = [
            [-50, -50, -25],
            [50, 50, 25],
            [50, -50, -25],
            [-50, 50, 25],
            [0, 0, 0],
        ]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=vertices,
        )

        # bbox = (100, 100, 50)
        assert result.geometry.bbox_dimensions == (100.0, 100.0, 50.0)
        # complexity = 7/60
        assert result.geometry.complexity_score == pytest.approx(7.0 / 60.0)
        # symmetry: p1 vs p2 平行（|cos|=1）, p1 vs p3 正交（0）, p2 vs p3 正交（0）
        #  1/3
        assert result.geometry.symmetry_score == pytest.approx(1.0 / 3.0)
        # feature_vector 32 维
        assert len(result.geometry.feature_vector) == FEATURE_VECTOR_DIM
        # 完整性
        assert result.is_complete is True
        assert result.completeness_ratio == 1.0
        assert GeometryFeaturesDeriver.should_degrade(result) is False

    def test_minimal_valid_part(self):
        """最小有效零件：1 plane + 4 vertices."""
        features = [make_plane("p1", area_mm2=100.0, confidence=0.9)]
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=vertices,
        )
        assert result.is_complete is True
        assert result.geometry.bbox_dimensions == (1.0, 1.0, 1.0)
        assert result.geometry.complexity_score == pytest.approx(1.0 / 60.0)

    def test_degraded_path_with_no_vertices(self):
        """vertices=None 时降级路径触发."""
        features = [make_plane()]
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=features,
            vertices=None,
        )
        assert GeometryFeaturesDeriver.should_degrade(result) is True
        assert result.is_complete is False
        # 但 feature_vector/symmetry/complexity 仍是真实计算结果
        assert len(result.geometry.feature_vector) == FEATURE_VECTOR_DIM
        assert result.geometry.complexity_score == pytest.approx(1.0 / 60.0)

    def test_geometry_features_dataclass_compatible(self):
        """派生结果可直接构造 GeometryFeatures dataclass."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[make_plane()],
            vertices=[[0, 0, 0], [1, 1, 1]],
        )
        # 验证返回的 geometry 是 GeometryFeatures 实例
        assert isinstance(result.geometry, GeometryFeatures)
        # 验证可访问全部 4 个字段
        assert hasattr(result.geometry, "bbox_dimensions")
        assert hasattr(result.geometry, "feature_vector")
        assert hasattr(result.geometry, "symmetry_score")
        assert hasattr(result.geometry, "complexity_score")

    def test_no_exception_on_empty_input(self):
        """空输入不抛异常（features=[] + vertices=None）."""
        result = GeometryFeaturesDeriver.from_feature_extraction(
            features=[],
            vertices=None,
        )
        assert result.geometry.bbox_dimensions == (0.0, 0.0, 0.0)
        assert result.geometry.symmetry_score == 0.0
        assert result.geometry.complexity_score == 0.0
        assert len(result.geometry.feature_vector) == FEATURE_VECTOR_DIM
        assert result.is_complete is False
