"""STEP文件解析模块。

基于CadQuery/OCCT实现高性能STEP文件解析。
支持实体模型信息的完整提取，包括几何形状、拓扑结构、
包围盒、体积、表面积、重心等基础数据。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq

logger = logging.getLogger(__name__)


@dataclass
class BoundingBox:
    length: float
    width: float
    height: float
    min_point: tuple[float, float, float] = (0, 0, 0)
    max_point: tuple[float, float, float] = (0, 0, 0)


@dataclass
class ModelInfo:
    volume: float
    surface_area: float
    bounding_box: BoundingBox
    center_of_mass: tuple[float, float, float]
    entity_count: int
    face_count: int
    vertex_count: int
    edge_count: int = 0
    shell_count: int = 0
    solid_count: int = 0


@dataclass
class EntityInfo:
    name: str
    entity_index: int
    volume: float
    surface_area: float
    bounding_box: BoundingBox
    center_of_mass: tuple[float, float, float]
    face_count: int
    vertex_count: int
    is_solid: bool = True


@dataclass
class StepParseResult:
    file_name: str
    file_size: int
    parse_time_ms: float
    model_info: ModelInfo
    entities: list[EntityInfo] = field(default_factory=list)
    is_assembly: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class StepParseError(Exception):
    """STEP文件解析异常。"""


class StepParser:
    """STEP文件解析器。

    基于CadQuery(OCCT内核)解析STEP文件，提取模型几何与拓扑信息。
    支持AP203/AP214/AP242等STEP应用协议。
    """

    def __init__(self) -> None:
        self._initialized = True

    @staticmethod
    def _extract_shape(result) -> cq.Shape:
        """从importStep结果中提取Shape对象。

        CadQuery的importStep返回Workplane，需要调用.val()
        获取底层的Solid/Compound对象以访问Volume/BoundingBox等方法。
        """
        if isinstance(result, cq.Workplane):
            val = result.val()
            if val is None:
                raise StepParseError("STEP文件中未找到有效几何体")
            return val
        if hasattr(result, "Volume"):
            return result
        try:
            return result.val()
        except (AttributeError, RuntimeError, ValueError, TypeError) as extract_err:
            # CadQuery Workplane.val() 失败时，包装为可识别的 StepParseError
            raise StepParseError("无法从导入结果中提取几何体") from extract_err

    def parse(self, file_path: str | Path) -> StepParseResult:
        """解析STEP文件并提取模型信息。

        Args:
            file_path: STEP文件路径(.step或.stp)

        Returns:
            StepParseResult: 包含解析结果的完整数据结构

        Raises:
            StepParseError: 解析失败时抛出
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise StepParseError(f"文件不存在: {file_path}")

        if file_path.suffix.lower() not in (".step", ".stp"):
            raise StepParseError(f"不支持的文件格式: {file_path.suffix}，请使用 .step 或 .stp 文件")

        file_size = file_path.stat().st_size
        logger.info("开始解析STEP文件: %s (%d bytes)", file_path.name, file_size)

        start_time = time.perf_counter()

        try:
            raw_result = cq.importers.importStep(str(file_path))
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            # cadquery STEP 导入涉及 C++ OCCT 绑定，捕获核心错误类型
            logger.error("STEP文件解析失败: %s", e, exc_info=True)
            raise StepParseError(f"STEP文件解析失败: {e}") from e

        shape = self._extract_shape(raw_result)

        try:
            model_info = self._extract_model_info(shape)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            # 模型信息提取涉及 cadquery Shape 属性访问与几何遍历
            logger.error("模型信息提取失败: %s", e, exc_info=True)
            raise StepParseError(f"模型信息提取失败: {e}") from e

        entities = self._extract_entities(shape)
        is_assembly = len(entities) > 1
        warnings = self._check_geometry_issues(shape, model_info)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "STEP解析完成: %s, 耗时 %.1fms, 实体数=%d, 面数=%d",
            file_path.name,
            elapsed_ms,
            model_info.entity_count,
            model_info.face_count,
        )

        return StepParseResult(
            file_name=file_path.name,
            file_size=file_size,
            parse_time_ms=round(elapsed_ms, 2),
            model_info=model_info,
            entities=entities,
            is_assembly=is_assembly,
            warnings=warnings,
            errors=[],
        )

    def get_cadquery_shape(self, file_path: str | Path) -> cq.Shape:
        """直接获取CadQuery Shape对象用于后续处理。

        Args:
            file_path: STEP文件路径

        Returns:
            cq.Shape: 解析后的几何形状
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise StepParseError(f"文件不存在: {file_path}")
        try:
            raw_result = cq.importers.importStep(str(file_path))
            return self._extract_shape(raw_result)
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as import_err:
            # 导入流程任何环节失败都包装为业务异常，包含原始 cause
            raise StepParseError(f"STEP文件导入失败: {import_err}") from import_err

    def _extract_model_info(self, shape) -> ModelInfo:
        """从CadQuery Shape提取整体模型信息。"""
        try:
            bb = shape.BoundingBox()
            dx = bb.xmax - bb.xmin
            dy = bb.ymax - bb.ymin
            dz = bb.zmax - bb.zmin
            bbox = BoundingBox(
                length=round(dx, 4),
                width=round(dy, 4),
                height=round(dz, 4),
                min_point=(round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)),
                max_point=(round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4)),
            )
        except (AttributeError, RuntimeError, ValueError, TypeError) as geom_err:
            # CadQuery 包围盒计算失败时回退为零包围盒，记录以便排查
            logger.debug(
                "Failed to compute BoundingBox for shape: %s",
                geom_err,
                exc_info=True,
            )
            bbox = BoundingBox(0, 0, 0)

        try:
            volume = shape.Volume()
        except (AttributeError, RuntimeError, ValueError, TypeError) as vol_err:
            # CadQuery 体积计算失败时回退为 0.0，记录以便排查
            logger.debug(
                "Failed to compute shape Volume: %s",
                vol_err,
                exc_info=True,
            )
            volume = 0.0

        try:
            surface_area = shape.Area()
        except (AttributeError, RuntimeError, ValueError, TypeError) as area_err:
            # CadQuery 表面积计算失败时回退为 0.0，记录以便排查
            logger.debug(
                "Failed to compute shape Area: %s",
                area_err,
                exc_info=True,
            )
            surface_area = 0.0

        try:
            com = shape.Center()
            center = (round(com.x, 4), round(com.y, 4), round(com.z, 4))
        except (AttributeError, RuntimeError, ValueError, TypeError) as com_err:
            # CadQuery 重心计算失败时回退到原点，记录以便排查
            logger.debug(
                "Failed to compute shape Center: %s",
                com_err,
                exc_info=True,
            )
            center = (0.0, 0.0, 0.0)

        try:
            faces = shape.Faces()
            face_count = len(list(faces))
        except (AttributeError, RuntimeError, ValueError, TypeError) as face_err:
            # CadQuery 面枚举失败时回退为 0，记录以便排查
            logger.debug(
                "Failed to enumerate shape Faces: %s",
                face_err,
                exc_info=True,
            )
            face_count = 0

        try:
            vertices = shape.Vertices()
            vertex_count = len(list(vertices))
        except (AttributeError, RuntimeError, ValueError, TypeError) as vert_err:
            # CadQuery 顶点枚举失败时回退为 0，记录以便排查
            logger.debug(
                "Failed to enumerate shape Vertices: %s",
                vert_err,
                exc_info=True,
            )
            vertex_count = 0

        try:
            edges = shape.Edges()
            edge_count = len(list(edges))
        except (AttributeError, RuntimeError, ValueError, TypeError) as edge_err:
            # CadQuery 边枚举失败时回退为 0，记录以便排查
            logger.debug(
                "Failed to enumerate shape Edges: %s",
                edge_err,
                exc_info=True,
            )
            edge_count = 0

        try:
            shells = shape.Shells()
            shell_count = len(list(shells))
        except (AttributeError, RuntimeError, ValueError, TypeError) as shell_err:
            # CadQuery 壳枚举失败时回退为 0，记录以便排查
            logger.debug(
                "Failed to enumerate shape Shells: %s",
                shell_err,
                exc_info=True,
            )
            shell_count = 0

        try:
            solids = shape.Solids()
            solid_count = len(list(solids))
            entity_count = max(solid_count, 1)
        except (AttributeError, RuntimeError, ValueError, TypeError) as sol_err:
            # CadQuery 实体枚举失败时回退为单个实体，记录以便排查
            logger.debug(
                "Failed to enumerate shape Solids: %s",
                sol_err,
                exc_info=True,
            )
            solid_count = 0
            entity_count = 1

        return ModelInfo(
            volume=round(volume, 4),
            surface_area=round(surface_area, 4),
            bounding_box=bbox,
            center_of_mass=center,
            entity_count=entity_count,
            face_count=face_count,
            vertex_count=vertex_count,
            edge_count=edge_count,
            shell_count=shell_count,
            solid_count=solid_count,
        )

    def _extract_entities(self, shape) -> list[EntityInfo]:
        """提取多实体/装配体中的各个独立实体信息。"""
        entities: list[EntityInfo] = []

        try:
            solids = list(shape.Solids())
        except (AttributeError, RuntimeError, ValueError, TypeError) as sol_err:
            # 装配体实体枚举失败时按单实体处理，记录以便排查
            logger.debug(
                "Failed to enumerate assembly solids: %s",
                sol_err,
                exc_info=True,
            )
            solids = []

        if len(solids) <= 1:
            try:
                bb = shape.BoundingBox()
                bbox = BoundingBox(
                    length=round(bb.xmax - bb.xmin, 4),
                    width=round(bb.ymax - bb.ymin, 4),
                    height=round(bb.zmax - bb.zmin, 4),
                    min_point=(round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)),
                    max_point=(round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4)),
                )
            except (AttributeError, RuntimeError, ValueError, TypeError) as bb_err:
                # 单实体包围盒计算失败时回退为零包围盒，记录以便排查
                logger.debug(
                    "Failed to compute single-entity BoundingBox: %s",
                    bb_err,
                    exc_info=True,
                )
                bbox = BoundingBox(0, 0, 0)

            try:
                com = shape.Center()
                center = (round(com.x, 4), round(com.y, 4), round(com.z, 4))
            except (AttributeError, RuntimeError, ValueError, TypeError) as com_err:
                # 单实体重心计算失败时回退为原点，记录以便排查
                logger.debug(
                    "Failed to compute single-entity Center: %s",
                    com_err,
                    exc_info=True,
                )
                center = (0.0, 0.0, 0.0)

            try:
                face_count = len(list(shape.Faces()))
            except (AttributeError, RuntimeError, ValueError, TypeError) as face_err:
                # 单实体面枚举失败时回退为 0，记录以便排查
                logger.debug(
                    "Failed to enumerate single-entity faces: %s",
                    face_err,
                    exc_info=True,
                )
                face_count = 0

            try:
                vertex_count = len(list(shape.Vertices()))
            except (AttributeError, RuntimeError, ValueError, TypeError) as vert_err:
                # 单实体顶点枚举失败时回退为 0，记录以便排查
                logger.debug(
                    "Failed to enumerate single-entity vertices: %s",
                    vert_err,
                    exc_info=True,
                )
                vertex_count = 0

            entities.append(
                EntityInfo(
                    name="Solid",
                    entity_index=0,
                    volume=round(shape.Volume() if hasattr(shape, "Volume") else 0, 4),
                    surface_area=round(shape.Area() if hasattr(shape, "Area") else 0, 4),
                    bounding_box=bbox,
                    center_of_mass=center,
                    face_count=face_count,
                    vertex_count=vertex_count,
                    is_solid=True,
                )
            )
            return entities

        for i, solid in enumerate(solids):
            try:
                bb = solid.BoundingBox()
                entity_bbox = BoundingBox(
                    length=round(bb.xmax - bb.xmin, 4),
                    width=round(bb.ymax - bb.ymin, 4),
                    height=round(bb.zmax - bb.zmin, 4),
                    min_point=(round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)),
                    max_point=(round(bb.xmax, 4), round(bb.ymax, 4), round(bb.zmax, 4)),
                )
            except (AttributeError, RuntimeError, ValueError, TypeError) as bb_err:
                # 装配体包围盒计算失败时回退为零包围盒，记录以便排查
                logger.debug(
                    "Failed to compute entity BoundingBox: %s",
                    bb_err,
                    exc_info=True,
                )
                entity_bbox = BoundingBox(0, 0, 0)

            try:
                entity_com = solid.Center()
                e_center = (
                    round(entity_com.x, 4),
                    round(entity_com.y, 4),
                    round(entity_com.z, 4),
                )
            except (AttributeError, RuntimeError, ValueError, TypeError) as com_err:
                # 装配体重心计算失败时回退为原点，记录以便排查
                logger.debug(
                    "Failed to compute entity Center: %s",
                    com_err,
                    exc_info=True,
                )
                e_center = (0.0, 0.0, 0.0)

            try:
                e_faces = len(list(solid.Faces()))
            except (AttributeError, RuntimeError, ValueError, TypeError) as face_err:
                # 装配体面枚举失败时回退为 0，记录以便排查
                logger.debug(
                    "Failed to enumerate entity faces: %s",
                    face_err,
                    exc_info=True,
                )
                e_faces = 0

            try:
                e_verts = len(list(solid.Vertices()))
            except (AttributeError, RuntimeError, ValueError, TypeError) as vert_err:
                # 装配体顶点枚举失败时回退为 0，记录以便排查
                logger.debug(
                    "Failed to enumerate entity vertices: %s",
                    vert_err,
                    exc_info=True,
                )
                e_verts = 0

            try:
                e_vol = round(solid.Volume(), 4)
            except (AttributeError, RuntimeError, ValueError, TypeError) as vol_err:
                # 装配体体积计算失败时回退为 0.0，记录以便排查
                logger.debug(
                    "Failed to compute entity Volume: %s",
                    vol_err,
                    exc_info=True,
                )
                e_vol = 0.0

            try:
                e_area = round(solid.Area(), 4)
            except (AttributeError, RuntimeError, ValueError, TypeError) as area_err:
                # 装配体表面积计算失败时回退为 0.0，记录以便排查
                logger.debug(
                    "Failed to compute entity Area: %s",
                    area_err,
                    exc_info=True,
                )
                e_area = 0.0

            entities.append(
                EntityInfo(
                    name=f"Solid_{i + 1}",
                    entity_index=i,
                    volume=e_vol,
                    surface_area=e_area,
                    bounding_box=entity_bbox,
                    center_of_mass=e_center,
                    face_count=e_faces,
                    vertex_count=e_verts,
                    is_solid=True,
                )
            )

        return entities

    def _check_geometry_issues(self, shape, model_info: ModelInfo) -> list[str]:
        """检查几何质量问题。"""
        warnings: list[str] = []

        if model_info.volume <= 0:
            warnings.append("模型体积为零或无法计算，可能存在几何缺陷")

        if model_info.face_count == 0:
            warnings.append("模型未检测到有效的面，可能为非实体模型")

        if model_info.vertex_count == 0:
            warnings.append("模型未检测到顶点")

        bb = model_info.bounding_box
        if bb.length <= 0 or bb.width <= 0 or bb.height <= 0:
            warnings.append("包围盒尺寸异常，模型可能为空或退化")

        min_dim = min(bb.length, bb.width, bb.height)
        if min_dim > 0 and min_dim < 0.001:
            warnings.append(f"最小包围盒尺寸极小({min_dim:.6f}mm)，可能存在薄片几何")

        if model_info.face_count > 100000:
            warnings.append(f"面数较多({model_info.face_count})，渲染性能可能受影响")

        return warnings
