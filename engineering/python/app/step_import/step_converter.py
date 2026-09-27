"""STEP模型格式转换模块。

将解析后的STEP模型转换为目标格式：
- STL (三角网格): 支持ASCII和二进制两种格式
- BREP: OCCT原生边界表示格式

提供可配置的精度参数以平衡渲染性能与几何保真度。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq

from app.step_import.step_parser import StepParseResult, EntityInfo

logger = logging.getLogger(__name__)


@dataclass
class StlExportOptions:
    linear_deflection: float = 0.01
    angular_deflection: float = 0.5
    is_relative: bool = False
    binary: bool = True
    precision_level: str = "medium"

    @property
    def linear_tolerance(self) -> float:
        return self.linear_deflection / 1000.0

    @property
    def angular_tolerance(self) -> float:
        import math

        return math.radians(self.angular_deflection)


@dataclass
class ConvertResult:
    file_name: str
    stl_path: str
    stl_url: str
    format: str
    face_count: int
    vertex_count: int
    file_size: int
    entity_index: int
    entity_name: str
    precision_used: str
    conversion_time_ms: float


@dataclass
class BatchConvertResult:
    files: list[ConvertResult] = field(default_factory=list)
    total_time_ms: float = 0.0
    total_face_count: int = 0
    total_vertex_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


PRECISION_PRESETS = {
    "low": StlExportOptions(
        linear_deflection=0.1,
        angular_deflection=1.0,
        precision_level="low",
    ),
    "medium": StlExportOptions(
        linear_deflection=0.01,
        angular_deflection=0.5,
        precision_level="medium",
    ),
    "high": StlExportOptions(
        linear_deflection=0.001,
        angular_deflection=0.1,
        precision_level="high",
    ),
}


class StepConverter:
    """STEP模型格式转换器。

    负责将CadQuery Shape对象转换为各种目标格式。
    支持精度可配置的三角剖分和BREP导出。
    """

    def __init__(
        self,
        output_dir: str | Path | None = None,
        base_url: str = "/api/import/step/output",
    ) -> None:
        if output_dir is None:
            from app.utils.utils import get_output_dir

            output_dir = get_output_dir("step_import")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url

    def convert_to_stl(
        self,
        shape,
        file_name: str,
        options: StlExportOptions | None = None,
        entity_index: int = 0,
        entity_name: str = "Solid",
    ) -> ConvertResult:
        """将单个Shape转换为STL格式。

        Args:
            shape: CadQuery Shape对象
            file_name: 输出文件名(不含扩展名)
            options: STL导出选项
            entity_index: 实体索引
            entity_name: 实体名称

        Returns:
            ConvertResult: 转换结果
        """
        if options is None:
            options = PRECISION_PRESETS["medium"]

        name_base = Path(file_name).stem
        unique_id = uuid.uuid4().hex[:8]

        if entity_index > 0:
            stl_name = f"{name_base}_e{entity_index}_{unique_id}.stl"
        else:
            stl_name = f"{name_base}_{unique_id}.stl"

        stl_path = self.output_dir / stl_name

        start = time.perf_counter()

        try:
            cq.exporters.export(
                shape,
                str(stl_path),
                exportType="STL",
                tolerance=options.linear_deflection,
                angularTolerance=options.angular_deflection,
            )
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            # cadquery 导出器涉及 OCCT C++ 绑定，捕获核心错误类型
            logger.warning("默认精度导出失败，尝试降级重试: %s", e)
            try:
                cq.exporters.export(
                    shape,
                    str(stl_path),
                    exportType="STL",
                    tolerance=0.1,
                    angularTolerance=1.0,
                )
                options = PRECISION_PRESETS["low"]
            except (OSError, ValueError, RuntimeError, TypeError) as e2:
                raise RuntimeError(f"STL导出失败: {e2}") from e2

        elapsed = (time.perf_counter() - start) * 1000
        file_size = stl_path.stat().st_size

        face_count = 0
        vertex_count = 0
        try:
            if file_size >= 84:
                stl_path_obj = Path(stl_path)
                if stl_path_obj.exists():
                    raw = stl_path_obj.read_bytes()
                    if raw[:5] == b"solid":
                        tri_count = raw.count(b"facet normal")
                        face_count = tri_count
                        vertex_count = tri_count * 3
                    else:
                        tri_count = int.from_bytes(raw[80:84], "little")
                        face_count = tri_count
                        vertex_count = tri_count * 3
        except (OSError, ValueError, TypeError, AttributeError) as face_err:
            # STL header 解析失败不应阻塞主流程，仅记录并返回 0
            logger.debug("STL头解析失败，返回0: %s", face_err)
            face_count = 0
            vertex_count = 0

        return ConvertResult(
            file_name=stl_name,
            stl_path=str(stl_path),
            stl_url=f"{self.base_url}/{stl_name}",
            format="stl",
            face_count=face_count,
            vertex_count=vertex_count,
            file_size=file_size,
            entity_index=entity_index,
            entity_name=entity_name,
            precision_used=options.precision_level,
            conversion_time_ms=round(elapsed, 2),
        )

    def convert_to_brep(self, shape, file_name: str, entity_index: int = 0) -> ConvertResult:
        """将Shape转换为BREP格式(OCCT原生格式)。

        BREP格式保留精确的边界表示数据，不进行三角剖分。
        """
        name_base = Path(file_name).stem
        unique_id = uuid.uuid4().hex[:8]
        brep_name = (
            f"{name_base}_e{entity_index}_{unique_id}.brep" if entity_index > 0 else f"{name_base}_{unique_id}.brep"
        )
        brep_path = self.output_dir / brep_name

        start = time.perf_counter()

        try:
            cq.exporters.export(shape, str(brep_path), exportType="BREP")
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            raise RuntimeError(f"BREP导出失败: {e}") from e

        elapsed = (time.perf_counter() - start) * 1000
        file_size = brep_path.stat().st_size

        face_count = 0
        vertex_count = 0
        try:
            face_count = len(list(shape.Faces()))
            vertex_count = len(list(shape.Vertices()))
        except (AttributeError, RuntimeError, ValueError, TypeError) as e:
            # OpenCascade 形状枚举失败时，face/vertex 计数回退为 0（不影响转换主流程）
            logger.debug(
                f"Failed to enumerate faces/vertices for {brep_name}: {e}",
                exc_info=True,
            )

        return ConvertResult(
            file_name=brep_name,
            stl_path=str(brep_path),
            stl_url=f"{self.base_url}/{brep_name}",
            format="brep",
            face_count=face_count,
            vertex_count=vertex_count,
            file_size=file_size,
            entity_index=entity_index,
            entity_name=f"Solid_{entity_index + 1}",
            precision_used="exact",
            conversion_time_ms=round(elapsed, 2),
        )

    def convert_all_entities(
        self,
        shape,
        file_name: str,
        parse_result: StepParseResult,
        output_format: str = "stl",
        precision: str = "medium",
    ) -> BatchConvertResult:
        """批量转换STEP中的所有实体。

        Args:
            shape: CadQuery Shape对象
            file_name: 原始STEP文件名
            parse_result: 解析结果(含实体列表)
            output_format: 输出格式(stl/brep)
            precision: 精度级别(low/medium/high)

        Returns:
            BatchConvertResult: 批量转换结果
        """
        total_start = time.perf_counter()
        options = PRECISION_PRESETS.get(precision, PRECISION_PRESETS["medium"])

        results: list[ConvertResult] = []
        errors: list[str] = []
        total_faces = 0
        total_verts = 0

        entities = (
            parse_result.entities
            if parse_result.entities
            else [
                EntityInfo(
                    name="Solid",
                    entity_index=0,
                    volume=parse_result.model_info.volume,
                    surface_area=parse_result.model_info.surface_area,
                    bounding_box=parse_result.model_info.bounding_box,
                    center_of_mass=parse_result.model_info.center_of_mass,
                    face_count=parse_result.model_info.face_count,
                    vertex_count=parse_result.model_info.vertex_count,
                )
            ]
        )

        for entity in entities:
            try:
                if entity.entity_index == 0 and len(entities) == 1:
                    entity_shape = shape
                else:
                    solids = list(shape.Solids())
                    if entity.entity_index < len(solids):
                        entity_shape = solids[entity.entity_index]
                    else:
                        errors.append(f"实体索引越界: {entity.entity_index}")
                        continue

                if output_format == "brep":
                    result = self.convert_to_brep(entity_shape, file_name, entity.entity_index)
                else:
                    result = self.convert_to_stl(
                        entity_shape,
                        file_name,
                        options,
                        entity.entity_index,
                        entity.name,
                    )

                results.append(result)
                total_faces += result.face_count
                total_verts += result.vertex_count
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as e:
                # 单个实体转换失败不应中断整个批次，记录错误后继续处理
                errors.append(f"实体 {entity.name} 转换失败: {e}")
                logger.exception("实体转换异常: %s", entity.name)

        total_elapsed = (time.perf_counter() - total_start) * 1000

        return BatchConvertResult(
            files=results,
            total_time_ms=round(total_elapsed, 2),
            total_face_count=total_faces,
            total_vertex_count=total_verts,
            errors=errors,
        )

    def get_stl_path(self, file_name: str) -> Path:
        """根据文件名获取STL文件的完整路径。"""
        return self.output_dir / file_name

    def stl_exists(self, file_name: str) -> bool:
        """检查STL文件是否存在。"""
        return self.get_stl_path(file_name).exists()
