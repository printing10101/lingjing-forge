"""加工特征提取模块。

基于DXF解析结果中的几何实体和尺寸标注，识别加工特征。
实现孔特征和平面特征的自动提取算法。

特征识别策略：
1. 孔特征：关联圆实体与尺寸标注 → 提取直径 → 推断深度
2. 平面特征：检测矩形轮廓 → 提取长宽尺寸

输出与process_planning模块的MachiningFeature格式完全兼容。

本模块为门面：实现已拆分至 _dxf_feature_models / _dimension_mixin / _plane_mixin / _helpers。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.dxf.exceptions import DxfFeatureError
from app.dxf._dxf_feature_models import (  # noqa: F401
    PROXIMITY_THRESHOLD,
    RECTANGLE_ANGLE_TOLERANCE,
    RECTANGLE_LENGTH_TOLERANCE,
    FeatureExtractionResult,
    HoleFeatureInfo,
    PlaneFeatureInfo,
)
from app.dxf._dimension_mixin import _DimensionMixin
from app.dxf._helpers import (  # noqa: F401
    extract_tolerance_from_text,
    is_counterbore_text,
)
from app.dxf._plane_mixin import _PlaneMixin

logger = logging.getLogger(__name__)


class FeatureExtractor(_DimensionMixin, _PlaneMixin):
    """加工特征提取器。

    基于DXF解析结果，通过规则匹配算法识别：
    1. 孔特征——关联圆与尺寸标注
    2. 平面特征——检测矩形轮廓

    使用方式:
        extractor = FeatureExtractor()
        features = extractor.extract(parse_result)
        for hole in features.holes:
            logger.info("孔: %s, 直径=%.2fmm", hole.hole_id, hole.diameter)
    """

    DEFAULT_DEPTH_RATIO = 3.0
    MIN_DEPTH = 5.0
    MAX_DEPTH = 200.0
    DEFAULT_PLATE_THICKNESS = 10.0

    def __init__(self) -> None:
        logger.info("FeatureExtractor初始化完成")

    def extract(self, parse_result) -> FeatureExtractionResult:
        """从DXF解析结果中提取加工特征。

        Args:
            parse_result: DxfParseResult 或者 DXF 文件路径（str / Path）
                          ——传字符串时会自动 parse，方便调用方单步骤使用

        Returns:
            FeatureExtractionResult: 包含孔和平面特征列表

        Raises:
            DxfFeatureError: 输入数据无效
        """
        if parse_result is None:
            raise DxfFeatureError("DXF解析结果为空，无法提取特征。请先调用DxfParser.parse()获取解析结果。")

        # 兼容字符串/路径输入：自动 parse
        if isinstance(parse_result, (str, Path)):
            from app.dxf.dxf_parser import DxfParser

            parse_result = DxfParser().parse(parse_result)

        result = FeatureExtractionResult()

        if parse_result.total_entities == 0:
            result.errors.append("DXF文件中无几何实体，无法提取加工特征")
            return result

        try:
            self._extract_overall_dimensions(parse_result, result)
            self._extract_plane_features(parse_result, result)
            self._extract_hole_features(parse_result, result)
        except (AttributeError, TypeError, ValueError, IndexError, KeyError) as e:
            # 防御性兜底：特征提取涉及几何运算/属性访问，异常类型多源
            # 任何阶段失败都通过 errors 字段暴露给上层，特征提取整体标记为失败
            result.errors.append(f"特征提取过程中发生异常: {e}")
            logger.error("特征提取异常: %s", e, exc_info=True)

        if not result.holes and not result.planes:
            result.warnings.append(
                "未识别到任何孔特征或平面特征。可能原因："
                "1) DXF中无可识别的圆或矩形轮廓；"
                "2) 几何实体过于分散或尺寸标注缺失"
            )

        logger.info(
            "特征提取完成: 孔=%d, 平面=%d, 外形=%.1fx%.1fx%.1f",
            result.hole_count,
            result.plane_count,
            result.overall_length,
            result.overall_width,
            result.overall_height,
        )
        return result
