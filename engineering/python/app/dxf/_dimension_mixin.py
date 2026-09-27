"""加工特征尺寸/孔提取 mixin（从 feature_extractor 拆出）。"""

from __future__ import annotations

import logging
import math
import re

from app.dxf.dxf_parser import DxfDimension, DxfParseResult
from app.dxf._dxf_feature_models import PROXIMITY_THRESHOLD, FeatureExtractionResult, HoleFeatureInfo
from app.dxf._helpers import extract_tolerance_from_text, is_counterbore_text

logger = logging.getLogger(__name__)


class _DimensionMixin:
    # 宿主契约：由主类提供
    DEFAULT_PLATE_THICKNESS: float
    DEFAULT_DEPTH_RATIO: float
    MIN_DEPTH: float
    MAX_DEPTH: float

    def _extract_overall_dimensions(
        self,
        parse_result: DxfParseResult,
        result: FeatureExtractionResult,
    ) -> None:
        """从图形范围推断零件整体尺寸。

        优先从尺寸标注中提取，标注缺失时使用几何边界。
        """
        extents = parse_result.extents
        # M6 bug 修复：原代码 length/width 语义反转。
        # extents["width"] 是 X 方向（图纸宽度），应为 length；
        # extents["height"] 是 Y 方向（图纸高度），应为 width。
        # 但实际几何语义中，DXF extents 的 width 对应 X 轴（长边），
        # height 对应 Y 轴（短边）。这里反转回来，使下游切削参数推荐
        # 基于正确的几何信息。
        result.overall_length = extents.get("width", 100.0)
        result.overall_width = extents.get("height", 80.0)
        # 注：经复查，原赋值实际是正确的（widthlength, heightwidth 符合
        # DXF 坐标系约定）。但下游消费者存在两种解读，因此显式标注映射关系，
        # 避免后续误改。如确实需要反转，请同步更新所有下游消费者。

        dim_texts = [d.text for d in parse_result.dimensions if d.text]
        numbers: list[float] = []
        for text in dim_texts:
            found = re.findall(r"[\d.]+", text)
            numbers.extend(float(n) for n in found)

        if numbers and len(numbers) >= 2:
            sorted_nums = sorted(numbers, reverse=True)
            max_dim = sorted_nums[0]
            second_dim = sorted_nums[1] if len(sorted_nums) > 1 else max_dim * 0.6
            if max_dim > result.overall_length * 0.8:
                result.overall_length = max_dim
            if second_dim > result.overall_width * 0.8:
                result.overall_width = second_dim

        result.overall_height = self.DEFAULT_PLATE_THICKNESS
        result.height_inferred = True

        for dim in parse_result.dimensions:
            if dim.dim_type in ("LINEAR_ROTATED", "ALIGNED"):
                if "厚" in dim.text or "深" in dim.text or "H" in dim.text.upper():
                    try:
                        nums = re.findall(r"[\d.]+", dim.text)
                        if nums:
                            result.overall_height = float(nums[0])
                            result.height_inferred = False
                            break
                    except (ValueError, IndexError) as parse_err:
                        # 解析厚/深尺寸文本失败时，保留默认值并继续
                        logger.debug(
                            "Failed to parse height dimension text %r: %s",
                            dim.text,
                            parse_err,
                            exc_info=True,
                        )

        if result.overall_length < 1.0 or result.overall_width < 1.0:
            result.warnings.append(
                f"推断的零件尺寸异常(长={result.overall_length:.1f}mm, "
                f"宽={result.overall_width:.1f}mm)，请检查DXF文件内容"
            )

    def _extract_hole_features(
        self,
        parse_result: DxfParseResult,
        result: FeatureExtractionResult,
    ) -> None:
        """识别孔特征：将圆关联到尺寸标注并提取参数。"""
        if not parse_result.circles:
            return

        dim_map = self._build_dimension_proximity_map(parse_result)
        hole_index = 0

        for circle in parse_result.circles:
            hole_index += 1
            hole_id = f"HOLE_{hole_index:03d}"

            diameter = circle.radius * 2.0
            depth = self.DEFAULT_DEPTH_RATIO * diameter
            depth = max(self.MIN_DEPTH, min(depth, self.MAX_DEPTH))
            tolerance_grade = "IT8"
            hole_type = "through_hole" if depth >= result.overall_height * 0.9 else "blind_hole"
            dim_text = ""
            dim_handle = ""
            depth_inferred = True

            matched_dim = dim_map.get(circle.handle)
            if matched_dim:
                dim_handle = matched_dim.handle
                dim_text = matched_dim.text

                if matched_dim.dim_type == "DIAMETER":
                    if matched_dim.measurement > 0:
                        diameter = matched_dim.measurement
                elif matched_dim.measurement > 0:
                    if abs(matched_dim.measurement - diameter) / diameter < 0.5:
                        diameter = matched_dim.measurement

                depth_nums = re.findall(r"[\d.]+", dim_text)
                if len(depth_nums) >= 2:
                    try:
                        potential_depth = float(depth_nums[-1])
                        if 3.0 < potential_depth < 500.0 and potential_depth != diameter:
                            depth = potential_depth
                            depth_inferred = False
                    except ValueError as depth_err:
                        # 深度候选解析失败时回退到推断深度，记录以便排查
                        logger.debug(
                            "Failed to parse hole depth candidate %r: %s",
                            dim_text,
                            depth_err,
                            exc_info=True,
                        )

                if "通孔" in dim_text or "THRU" in dim_text.upper():
                    hole_type = "through_hole"
                    depth = result.overall_height
                    depth_inferred = True
                elif "盲孔" in dim_text or "BLIND" in dim_text.upper():
                    hole_type = "blind_hole"
                elif is_counterbore_text(dim_text) or "沉头" in dim_text:
                    hole_type = "counterbore"

                _tolerance = extract_tolerance_from_text(dim_text)
                if _tolerance:
                    tolerance_grade = _tolerance

            hole = HoleFeatureInfo(
                hole_id=hole_id,
                center_x=circle.center[0],
                center_y=circle.center[1],
                diameter=round(diameter, 4),
                depth=round(depth, 4),
                depth_inferred=depth_inferred,
                tolerance_grade=tolerance_grade,
                hole_type=hole_type,
                surface="A",
                layer=circle.layer,
                associated_dim_handle=dim_handle,
                dimension_text=dim_text,
            )
            result.holes.append(hole)

        if not result.holes:
            result.warnings.append("未识别到任何孔特征")
        else:
            holes_with_dim = sum(1 for h in result.holes if h.associated_dim_handle)
            if holes_with_dim < len(result.holes):
                result.warnings.append(f"{len(result.holes) - holes_with_dim}个孔缺少尺寸标注，使用几何测量值作为孔径")
            inferred_depth_count = sum(1 for h in result.holes if h.depth_inferred)
            if inferred_depth_count > 0:
                result.warnings.append(f"{inferred_depth_count}个孔的深度为推断值，建议在DXF中添加深度标注")

    def _build_dimension_proximity_map(self, parse_result: DxfParseResult) -> dict[str, DxfDimension]:
        """建立圆实体与尺寸标注的空间关联映射。

        对每个圆，在其邻域范围内搜索最近的尺寸标注，
        使用KD树风格的空间分配策略避免多圆配对冲突。
        """
        if not parse_result.dimensions or not parse_result.circles:
            return {}

        circle_positions: dict[str, tuple[float, float]] = {}
        for c in parse_result.circles:
            circle_positions[c.handle] = (c.center[0], c.center[1])

        assignments: dict[str, DxfDimension] = {}
        used_dims: set[str] = set()

        sorted_circles = sorted(parse_result.circles, key=lambda c: c.radius, reverse=True)

        for circle in sorted_circles:
            cx, cy = circle.center[0], circle.center[1]
            best_dim = None
            best_dist = float("inf")

            for dim in parse_result.dimensions:
                if dim.handle in used_dims:
                    continue
                dx, dy = dim.position[0], dim.position[1]
                dist = math.sqrt((cx - dx) ** 2 + (cy - dy) ** 2)
                threshold = max(PROXIMITY_THRESHOLD, circle.radius * 3)
                if dist < threshold and dist < best_dist:
                    best_dist = dist
                    best_dim = dim

            if best_dim is not None:
                assignments[circle.handle] = best_dim
                used_dims.add(best_dim.handle)

        return assignments
