"""DXF文件解析模块。

基于ezdxf库实现DXF工程图文件的完整解析。
支持DXF R12、R14及AutoCAD 2000-2021版本（AC1009-AC1032）。

提取的实体类型：
- LINE: 直线段，含起点/终点坐标、图层、颜色
- CIRCLE: 圆，含圆心坐标、半径、图层、颜色
- ARC: 圆弧，含圆心坐标、半径、起止角度、图层、颜色
- TEXT/MTEXT: 文字标注，含内容、位置、高度、图层
- DIMENSION: 尺寸标注，含类型、测量值、关联实体、文本内容
- POLYLINE/LWPOLYLINE: 多段线，含顶点列表、闭合标志、图层

处理流程：
    DXF文件 ──▶ ezdxf.readfile() ──▶ 遍历modelspace ──▶ 结构化数据输出
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

import ezdxf

from app.dxf.exceptions import DxfParseError, DxfFormatError
from app.utils.utils import safe_file_path

logger = logging.getLogger(__name__)

DXF_VERSION_MAP: dict[str, str] = {
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "2000",
    "AC1018": "2004",
    "AC1021": "2007",
    "AC1024": "2010",
    "AC1027": "2013",
    "AC1032": "2018",
}

SUPPORTED_VERSIONS = frozenset(DXF_VERSION_MAP.keys())


# Dxf* 类型经本模块再导出（dxf/__init__.py、feature_extractor、dxf_to_model 依赖），
# 故此处显式保留全部导入（F401 为有意再导出）。
from ._entities import (  # noqa: F401
    DxfArc,
    DxfCircle,
    DxfDimension,
    DxfHatch,
    DxfInsert,
    DxfLine,
    DxfParseResult,
    DxfPolyline,
    DxfSpline,
    DxfText,
)

from app.dxf.entities import (
    arc_to_obj,
    circle_to_obj,
    dimension_to_obj,
    hatch_to_obj,
    insert_to_obj,
    line_to_obj,
    lwpolyline_to_obj,
    mtext_to_obj,
    polyline_to_obj,
    spline_to_obj,
    text_to_obj,
)
from app.dxf.extents import compute_extents


class DxfParser:
    """DXF文件解析器。

    基于ezdxf库解析DXF文件，提取几何实体和尺寸标注信息。
    支持AutoCAD R12至2021版本的DXF格式。

    使用方式:
        parser = DxfParser()
        result = parser.parse("path/to/part.dxf")
        for line in result.lines:
            logger.info("直线: %s -> %s", line.start, line.end)
    """

    def __init__(self) -> None:
        logger.info("DxfParser初始化完成")

    def parse(
        self,
        file_path: str | Path,
        *,
        user_id: str | None = None,
        base_dir: str | None = None,
    ) -> DxfParseResult:
        """解析DXF文件并返回结构化数据。

        Args:
            file_path: DXF文件路径
            user_id: 可选的用户标识，仅用于桥接层数据收集，不影响解析逻辑
            base_dir: 可选的基础目录，用于路径安全检查。如果提供，将验证 file_path 是否在 base_dir 内

        Returns:
            DxfParseResult: 包含所有提取的几何实体和元数据

        Raises:
            DxfParseError: 文件不存在或读取失败
            DxfFormatError: 格式无效或版本不支持
            ValueError: 路径安全检查失败（当提供 base_dir 时）
        """
        # 路径安全检查
        if base_dir is not None:
            try:
                path = safe_file_path(str(file_path), base_dir)
            except ValueError as e:
                raise DxfParseError(f"路径安全检查失败: {file_path}。错误: {e}") from e
        else:
            path = Path(file_path)

        start_time = time.time()
        result = DxfParseResult(file_name=path.name)

        if not path.exists():
            raise DxfParseError(f"DXF文件不存在: {file_path}。请检查文件路径是否正确，并确认文件未被移动或删除。")

        if not path.is_file():
            raise DxfParseError(f"路径不是有效的文件: {file_path}。请提供DXF文件的完整路径。")

        result.file_size = path.stat().st_size

        if result.file_size == 0:
            raise DxfParseError(f"DXF文件为空(0字节): {file_path}。请确认文件未被损坏。")

        if result.file_size > 100 * 1024 * 1024:
            result.warnings.append(f"DXF文件过大({result.file_size / 1024 / 1024:.1f}MB)，解析可能需要较长时间")

        try:
            doc = ezdxf.readfile(str(path))
        except ezdxf.DXFStructureError as e:
            raise DxfFormatError(f"DXF文件结构错误: {file_path}。文件可能已损坏或不完整。技术详情: {e}") from e
        except ezdxf.DXFVersionError as e:
            raise DxfFormatError(
                f"DXF版本不兼容: {file_path}。支持的版本包括R12、R14、AutoCAD 2000-2021。技术详情: {e}"
            ) from e
        except (OSError, IOError, PermissionError) as e:
            raise DxfParseError(f"DXF文件读取失败: {file_path}。错误: {e}") from e
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.error("DXF文件读取遇到未知异常: %s", file_path, exc_info=True)
            raise DxfParseError(f"DXF文件读取失败: {file_path}。错误: {e}") from e

        dxf_version = doc.dxfversion
        result.dxf_version = f"{dxf_version} ({DXF_VERSION_MAP.get(dxf_version, '未知')})"

        if dxf_version not in SUPPORTED_VERSIONS:
            result.warnings.append(f"DXF版本 {dxf_version} 不在明确支持的版本列表中，解析可能不完全准确")

        modelspace = doc.modelspace()
        self._extract_lines(modelspace, result)
        self._extract_circles(modelspace, result)
        self._extract_arcs(modelspace, result)
        self._extract_texts(modelspace, result)
        self._extract_dimensions(modelspace, result)
        self._extract_polylines(modelspace, result)
        # 高级实体（HATCH / BLOCK INSERT / SPLINE）
        self._extract_hatches(modelspace, result)
        self._extract_inserts(modelspace, result)
        self._extract_splines(modelspace, result)
        compute_extents(result)

        result.entity_counts = {
            "LINE": len(result.lines),
            "CIRCLE": len(result.circles),
            "ARC": len(result.arcs),
            "TEXT": len(result.texts),
            "DIMENSION": len(result.dimensions),
            "POLYLINE": len(result.polylines),
            "HATCH": len(result.hatches),
            "INSERT": len(result.inserts),
            "SPLINE": len(result.splines),
        }
        result.parse_time_ms = (time.time() - start_time) * 1000

        logger.info(
            "DXF解析完成: %s (版本=%s, 实体数=%d, 耗时=%.1fms)",
            path.name,
            dxf_version,
            result.total_entities,
            result.parse_time_ms,
        )

        if result.total_entities == 0:
            result.warnings.append("DXF文件中未发现任何可识别的几何实体")

        # 桥接层：把解析结果脱敏后落盘，供研究模块使用
        try:
            from app.research_bridge import UsageDataCollector

            collector = UsageDataCollector.get_instance()
            extra = {
                "polylines_count": len(result.polylines),
                "lines_count": len(result.lines),
                "circles_count": len(result.circles),
                "arcs_count": len(result.arcs),
                "texts_count": len(result.texts),
                "dimensions_count": len(result.dimensions),
            }
            collector.record_recognition(
                feature="dxf_parser",
                dxf_path=str(path),
                success=len(result.errors) == 0,
                latency_ms=int(result.parse_time_ms),
                user_id=user_id,
                extra=extra,
            )
            if result.errors:
                collector.record_batch_errors(
                    feature="dxf_parser",
                    error_type="parse_error",
                    error_messages=result.errors,
                    context={"file_path": str(path)},
                    user_id=user_id,
                )
        except (ImportError, AttributeError, KeyError, TypeError, ValueError) as e:
            logger.warning("bridge 数据收集失败（不影响主流程）: %s", e, exc_info=True)

        return result

    def _extract_entities(
        self,
        modelspace,
        entity_type: str,
        extractor: Callable[[Any], Any],
        target: list,
        result: DxfParseResult,
        *,
        warn_on_fail: bool = False,
        query_warn: bool = True,
    ) -> None:
        """通用实体提取模板。

        遍历 modelspace 中指定类型的实体，对每个实体调用 extractor
        进行转换；返回非 None 则追加到 target 列表。

        Args:
            modelspace: DXF modelspace 对象
            entity_type: 实体类型字符串（用于 query 与日志）
            extractor: 单个实体 → 对象 的转换函数；返回 None 表示跳过
            target: 提取结果追加到的列表
            result: 解析结果（用于添加 warnings）
            warn_on_fail: 单实体提取失败时是否往 result.warnings 添加消息
            query_warn: 查询本身异常时是否往 result.warnings 添加消息
                       （POLYLINE/LWPOLYLINE 原代码不添加，保留兼容）
        """
        try:
            for entity in modelspace.query(entity_type):
                try:
                    item = extractor(entity)
                    if item is not None:
                        target.append(item)
                except (AttributeError, KeyError, TypeError, ValueError) as e:
                    handle = getattr(entity.dxf, "handle", "<unknown>")
                    logger.warning(
                        "%s实体提取跳过(handle=%s): %s",
                        entity_type,
                        handle,
                        e,
                        exc_info=True,
                    )
                    if warn_on_fail:
                        result.warnings.append(f"{entity_type}实体提取失败(handle={handle}): {e}")
        except (AttributeError, TypeError) as e:
            logger.warning("%s实体查询异常: %s", entity_type, e, exc_info=True)
            if query_warn:
                result.warnings.append(f"{entity_type}实体查询异常: {e}")

    def _extract_lines(self, modelspace, result: DxfParseResult) -> None:
        """提取所有LINE实体。"""
        self._extract_entities(modelspace, "LINE", line_to_obj, result.lines, result)

    def _extract_circles(self, modelspace, result: DxfParseResult) -> None:
        """提取所有CIRCLE实体。"""
        self._extract_entities(
            modelspace,
            "CIRCLE",
            circle_to_obj,
            result.circles,
            result,
        )

    def _extract_arcs(self, modelspace, result: DxfParseResult) -> None:
        """提取所有ARC实体。"""
        self._extract_entities(modelspace, "ARC", arc_to_obj, result.arcs, result)

    def _extract_texts(self, modelspace, result: DxfParseResult) -> None:
        """提取所有TEXT和MTEXT实体。"""
        # TEXT/MTEXT 在单实体提取失败时需要往 warnings 写入诊断信息
        # （原有行为，与其它实体只 logger.warning 不同），通过 warn_on_fail=True 保留
        self._extract_entities(
            modelspace,
            "TEXT",
            text_to_obj,
            result.texts,
            result,
            warn_on_fail=True,
        )
        self._extract_entities(
            modelspace,
            "MTEXT",
            mtext_to_obj,
            result.texts,
            result,
            warn_on_fail=True,
        )

    def _extract_dimensions(self, modelspace, result: DxfParseResult) -> None:
        """提取所有DIMENSION实体。

        对每种标注类型（线性/对齐/角度/半径/直径）调用专门的提取方法。
        对于无法确定类型的标注，尝试从文本内容推断。
        """
        self._extract_entities(
            modelspace,
            "DIMENSION",
            dimension_to_obj,
            result.dimensions,
            result,
        )

    def _extract_polylines(self, modelspace, result: DxfParseResult) -> None:
        """提取所有 POLYLINE 和 LWPOLYLINE 实体。

        LWPOLYLINE 顶点包含 bulge（凸度）信息，用于表示圆弧段。
        POLYLINE 子实体是 VERTEX，需要递归读取。
        """
        # LWPOLYLINE/POLYLINE 原代码在 query 失败时不往 warnings 添加消息，
        # 通过 query_warn=False 保留该行为
        self._extract_entities(
            modelspace,
            "LWPOLYLINE",
            lwpolyline_to_obj,
            result.polylines,
            result,
            query_warn=False,
        )
        self._extract_entities(
            modelspace,
            "POLYLINE",
            polyline_to_obj,
            result.polylines,
            result,
            query_warn=False,
        )

    def _extract_hatches(self, modelspace, result: DxfParseResult) -> None:
        """提取所有 HATCH 实体（填充图案）。

        HATCH 在工程图中常表示：
        - 剖面线（ANSI31 斜线）
        - 区域填色（SOLID 填充）
        - 截面区域
        - 文字背景
        """
        self._extract_entities(
            modelspace,
            "HATCH",
            hatch_to_obj,
            result.hatches,
            result,
        )

    def _extract_inserts(self, modelspace, result: DxfParseResult) -> None:
        """提取所有 INSERT 实体（Block 引用）。

        INSERT 表示"插入一个块"，是 DXF 复用的关键机制。
        工业场景中：标准件库（螺栓、键、键槽、孔标准件）通过 INSERT 引用。
        """
        self._extract_entities(
            modelspace,
            "INSERT",
            insert_to_obj,
            result.inserts,
            result,
        )

    def _extract_splines(self, modelspace, result: DxfParseResult) -> None:
        """提取所有 SPLINE 实体（样条曲线）。

        SPLINE 在工业场景中：
        - 自由曲面（航空叶片、船体）
        - 模具型腔的复杂轮廓
        - 凸轮轮廓线
        """
        self._extract_entities(
            modelspace,
            "SPLINE",
            spline_to_obj,
            result.splines,
            result,
        )
