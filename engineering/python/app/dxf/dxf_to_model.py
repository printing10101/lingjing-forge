"""DXF到3D模型转换模块。

基于CadQuery库将DXF提取的2D几何特征转换为3D实体模型。
通过创建基础外形并使用布尔运算添加孔特征，
生成可用于后续工艺规划的完整三维模型。

转换流程：
    平面特征(外形尺寸) ──▶ box() ──▶ 基础立方体
    孔特征列表 ────────▶ cylinder() + cut() ──▶ 带孔实体模型
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cadquery as cq

from app.dxf.feature_extractor import (
    FeatureExtractionResult,
    HoleFeatureInfo,
)
from app.dxf.polyline_outline import (
    PolylineOutlineProcessor,
)
from app.dxf.dxf_parser import DxfPolyline
from app.dxf.exceptions import DxfModelError

logger = logging.getLogger(__name__)


@dataclass
class ModelConversionResult:
    """模型转换结果。

    Attributes:
        workplane: CadQuery Workplane对象(包含完整3D模型)
        length: 模型长度(X方向)
        width: 模型宽度(Y方向)
        height: 模型高度(Z方向)
        hole_count: 孔数量
        warnings: 转换过程中的警告
        errors: 转换过程中的错误
    """

    workplane: Any = None
    length: float = 0.0
    width: float = 0.0
    height: float = 10.0
    hole_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and self.workplane is not None


class DxfToModelConverter:
    """DXF特征到3D模型的转换器。

    基于CadQuery实现完整的2D→3D转换流程：
    1. 根据外形尺寸创建基准立方体
    2. 对每个孔特征进行布尔减运算
    3. 返回完整的Workplane对象供后续使用

    使用方式:
        converter = DxfToModelConverter()
        result = converter.convert(feature_result)
        if result.success:
            # 导出STL
            cq.exporters.export(result.workplane, "model.stl")
    """

    def __init__(self) -> None:
        logger.info("DxfToModelConverter初始化完成")

    def convert(
        self,
        feature_result: FeatureExtractionResult,
        user_id: str | None = None,
        source_dxf: str | None = None,
    ) -> ModelConversionResult:
        """将特征提取结果转换为3D CadQuery模型。

        Args:
            feature_result: FeatureExtractor的输出

        Returns:
            ModelConversionResult: 包含Workplane对象的转换结果

        Raises:
            DxfModelError: 特征数据无效或CadQuery操作失败
        """
        if feature_result is None:
            raise DxfModelError("特征提取结果为空，无法生成3D模型。请先调用FeatureExtractor.extract()获取特征数据。")

        result = ModelConversionResult()
        length = feature_result.overall_length
        width = feature_result.overall_width
        height = feature_result.overall_height

        if length <= 0 or width <= 0:
            result.errors.append(f"零件外形尺寸无效(长={length}, 宽={width})，无法创建3D模型")
            return result

        if height <= 0:
            height = 10.0
            result.warnings.append(f"零件高度无效，使用默认值{height}mm")

        result.length = length
        result.width = width
        result.height = height

        try:
            base = self._create_base_solid(length, width, height)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError, RuntimeError, OSError) as e:
            # cadquery/OCCT 在构造基础实体时可能抛出参数错误、内部运行时错误等
            # 统一收口为 DxfModelError 向上抛出，保持业务异常类型一致
            raise DxfModelError(f"基础立方体创建失败(尺寸={length}x{width}x{height}mm): {e}") from e

        hole_count = 0
        for hole in feature_result.holes:
            try:
                base = self._add_hole(base, hole, height)
                hole_count += 1
            except (ValueError, TypeError, ZeroDivisionError, OverflowError, RuntimeError) as e:
                # 单个孔特征失败不应阻塞整个模型转换，记录后跳过
                # 异常族：ValueError/TypeError/cadquery.CQException
                result.warnings.append(
                    f"孔{hole.hole_id}创建失败(中心=({hole.center_x:.1f},{hole.center_y:.1f}), "
                    f"直径={hole.diameter:.2f}mm): {e}"
                )
                logger.warning("孔特征转换失败: %s", e, exc_info=True)

        result.workplane = base
        result.hole_count = hole_count

        logger.info(
            "3D模型转换完成: 外形=%.1fx%.1fx%.1fmm, 孔=%d/%d",
            length,
            width,
            height,
            hole_count,
            feature_result.hole_count,
        )

        # 桥接层：把建模结果脱敏后落盘
        try:
            from app.research_bridge import UsageDataCollector

            collector = UsageDataCollector.get_instance()
            collector.record_recognition(
                feature="dxf_to_model",
                dxf_path=source_dxf or "",
                success=len(result.errors) == 0,
                latency_ms=0,
                user_id=user_id,
                extra={
                    "length": length,
                    "width": width,
                    "height": height,
                    "hole_count": hole_count,
                },
            )
        except (OSError, RuntimeError, ImportError) as e:
            logger.debug("bridge 数据收集失败（不影响主流程）: %s", e)

        return result

    def _create_base_solid(
        self,
        length: float,
        width: float,
        height: float,
    ) -> cq.Workplane:
        """创建基准立方体。

        在XY平面上创建box，底部位于Z=0，高度沿Z轴正向。
        """
        base = cq.Workplane("XY").box(length, width, height, centered=(True, True, False))
        logger.debug("基础立方体创建成功: %.1fx%.1fx%.1fmm", length, width, height)
        return base

    def _add_hole(
        self,
        workplane: cq.Workplane,
        hole: HoleFeatureInfo,
        base_height: float,
    ) -> cq.Workplane:
        """在模型上创建孔特征（布尔减运算）。

        Args:
            workplane: 当前Workplane对象
            hole: 孔特征信息
            base_height: 基础模型高度

        Returns:
            添加孔后的Workplane对象
        """
        hole_radius = hole.diameter / 2.0
        if hole_radius <= 0:
            logger.warning("孔%s半径为0，跳过", hole.hole_id)
            return workplane

        hole_depth = hole.depth
        if hole.hole_type == "through_hole" or hole_depth <= 0:
            hole_depth = base_height * 1.2
        hole_depth = min(hole_depth, base_height * 1.5)

        center_offset = (
            hole.center_x,
            hole.center_y,
        )

        hole_solid = (
            cq.Workplane("XY")
            .workplane(offset=-hole_depth * 0.1)
            .center(center_offset[0], center_offset[1])
            .circle(hole_radius)
            .extrude(hole_depth + hole_depth * 0.2)
        )

        result = workplane.cut(hole_solid)
        logger.debug(
            "孔%s: 中心=(%.1f,%.1f), 直径=%.2f, 深度=%.1f",
            hole.hole_id,
            hole.center_x,
            hole.center_y,
            hole.diameter,
            hole_depth,
        )
        return result

    def export_stl(
        self,
        conversion_result: ModelConversionResult,
        output_path: str | Path,
        tolerance: float = 0.001,
    ) -> Path:
        """将转换结果导出为STL文件。

        Args:
            conversion_result: 模型转换结果
            output_path: 输出文件路径
            tolerance: STL导出精度(mm)

        Returns:
            导出的STL文件路径

        Raises:
            DxfModelError: 模型无效或导出失败
        """
        if not conversion_result.success:
            raise DxfModelError(f"模型转换未成功，无法导出STL。错误: {'; '.join(conversion_result.errors)}")

        path = Path(output_path)
        try:
            cq.exporters.export(
                conversion_result.workplane,
                str(path),
                exportType="STL",
                tolerance=tolerance,
            )
            logger.info("STL导出: %s (%.1f KB)", path.name, path.stat().st_size / 1024)
            return path
        except (OSError, RuntimeError, ValueError, TypeError, OverflowError) as e:
            # cadquery + OCCT 在 STL 导出阶段可能抛出文件 I/O 错误、网格化错误等
            raise DxfModelError(f"STL导出失败({path}): {e}") from e

    def export_step(
        self,
        conversion_result: ModelConversionResult,
        output_path: str | Path,
    ) -> Path:
        """将转换结果导出为STEP文件。

        Args:
            conversion_result: 模型转换结果
            output_path: 输出文件路径

        Returns:
            导出的STEP文件路径

        Raises:
            DxfModelError: 模型无效或导出失败
        """
        if not conversion_result.success:
            raise DxfModelError(f"模型转换未成功，无法导出STEP。错误: {'; '.join(conversion_result.errors)}")

        path = Path(output_path)
        try:
            cq.exporters.export(
                conversion_result.workplane,
                str(path),
                exportType="STEP",
            )
            logger.info("STEP导出: %s (%.1f KB)", path.name, path.stat().st_size / 1024)
            return path
        except (OSError, RuntimeError, ValueError, TypeError, OverflowError) as e:
            raise DxfModelError(f"STEP导出失败({path}): {e}") from e

    def convert_from_polylines(
        self,
        polylines: list[DxfPolyline],
        height: float = 10.0,
    ) -> ModelConversionResult:
        """从多段线轮廓生成 3D 模型。

        流程：
        1. 用 PolylineOutlineProcessor 提取外轮廓和内部孔
        2. 用 CadQuery 拉伸外轮廓
        3. 用 cut 挖掉内部孔
        4. 返回 ModelConversionResult

        Args:
            polylines: DXF 解析得到的 polylines
            height: 拉伸高度（Z 方向），默认 10mm

        Returns:
            ModelConversionResult
        """
        result = ModelConversionResult()
        result.height = height

        if not polylines:
            result.errors.append("无多段线数据，无法生成 3D 模型")
            return result

        processor = PolylineOutlineProcessor()
        outlines = processor.extract_outlines(polylines)

        if not outlines:
            result.errors.append("未找到闭合多段线轮廓")
            return result

        outer = outlines[0]
        holes = [o for o in outlines[1:] if o.is_hole]

        # 计算包围盒
        xs = [v[0] for v in outer.vertices]
        ys = [v[1] for v in outer.vertices]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        length = max_x - min_x
        width = max_y - min_y
        result.length = length
        result.width = width

        try:
            # 1. 画外轮廓
            wp = cq.Workplane("XY")
            # 移动到外轮廓的起点
            wp = wp.moveTo(outer.vertices[0][0], outer.vertices[0][1])
            for v in outer.vertices[1:]:
                wp = wp.lineTo(v[0], v[1])
            if outer.is_closed:
                wp = wp.close()
            wp = wp.extrude(height)
            base = wp

            # 2. 挖掉内部孔
            for hole in holes:
                try:
                    hwp = cq.Workplane("XY")
                    hwp = hwp.moveTo(hole.vertices[0][0], hole.vertices[0][1])
                    for v in hole.vertices[1:]:
                        hwp = hwp.lineTo(v[0], v[1])
                    if hole.is_closed:
                        hwp = hwp.close()
                    hwp = hwp.extrude(height * 1.2)
                    base = base.cut(hwp)
                    result.hole_count += 1
                except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
                    result.warnings.append(f"挖孔失败(handle={hole.source_handle}): {e}")
                    logger.warning("挖孔失败: %s", e, exc_info=True)

            # 平移让最小 Z=0
            base = base.translate((0, 0, 0))

            result.workplane = base
            logger.info(
                "polyline→3D 完成: %.1fx%.1fx%.1f, 孔=%d",
                length,
                width,
                height,
                result.hole_count,
            )
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError, OverflowError) as e:
            result.errors.append(f"多段线建模失败: {e}")
            logger.error("多段线建模失败: %s", e, exc_info=True)
        return result

    def create_model_from_dimensions(
        self,
        length: float,
        width: float,
        height: float,
        holes: list[dict[str, float]] | None = None,
    ) -> cq.Workplane:
        """直接根据尺寸参数创建3D模型。

        Args:
            length: 长度(X方向)
            width: 宽度(Y方向)
            height: 高度(Z方向)
            holes: 孔参数列表 [{center_x, center_y, diameter, depth}, ...]

        Returns:
            CadQuery Workplane对象
        """
        try:
            model = self._create_base_solid(length, width, height)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError, RuntimeError, OSError) as e:
            # 与 convert 入口一致，统一收口
            raise DxfModelError(f"直接创建模型失败(尺寸={length}x{width}x{height}mm): {e}") from e

        if holes:
            # M3 bug 修复：原代码用 `len(holes) + 1` 作为 ID，但 len(holes) 在
            # 循环中是常量，导致所有孔获得相同 ID。改用 enumerate 计数。
            for hole_idx, hole_params in enumerate(holes, start=1):
                cx = hole_params.get("center_x", 0)
                cy = hole_params.get("center_y", 0)
                dia = hole_params.get("diameter", 10)
                dep = hole_params.get("depth", height)

                hole_info = HoleFeatureInfo(
                    hole_id=f"HOLE_{hole_idx:03d}",
                    center_x=cx,
                    center_y=cy,
                    diameter=dia,
                    depth=dep,
                    depth_inferred=False,
                )
                try:
                    model = self._add_hole(model, hole_info, height)
                except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
                    # 单个孔失败不阻塞后续孔和整体模型
                    logger.warning("孔创建失败: %s", e, exc_info=True)

        return model
