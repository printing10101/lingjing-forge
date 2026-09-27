"""STEP 文件写入器：pythonOCC → FreeCAD API → 简易模板三级降级。

设计原则
========
灵境制造的参数化几何输出模块必须生成可被 CAM 软件（NX/PowerMill/PyCAM）识别的 STEP 文件。
STEP（ISO 10303-21）是工业标准 CAD 数据交换格式，但生成完整 STEP 文件需要 OpenCASCADE 支持。

三级降级策略：
1. pythonOCC（OpenCASCADE Python 绑定）：完整 B-rep 表达，工业可信度最高
   - 依赖：pip install pythonocc-core（Windows 上需要 conda 环境）
2. FreeCAD Python API：基于 OpenCASCADE，但接口层有抽象损失
   - 依赖：安装 FreeCAD 并设置 FREECAD_HOME 环境变量
3. 简易 STEP 模板：手工拼接 ISO 10303-21 字符串
   - 不依赖任何外部库，桌面部署最简单
   - 只能描述基础几何（平面/圆柱/孔），无法表达复杂拓扑
   - STEP 文件可能被 NX/PowerMill 部分拒绝

为什么三级降级？
- 桌面 sidecar 模式下用户可能未安装 pythonOCC / FreeCAD
- 模板模式保证模块在最小依赖下仍可生成 STEP 文件（用于测试与降级）
- step_disclaimer.py 会告知用户当前使用的引擎与表达精度等级

输入：BrepShape 列表（来自 feature_to_brep.py）
输出：STEP 文件路径 + engine_used 字符串
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.parametric_geometry.feature_to_brep import BrepShape
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)


# STEP 写入结果


@dataclass
class StepWriteResult:
    """STEP 写入结果。"""

    success: bool
    output_path: str | None = None
    engine_used: str = "unavailable"
    shape_count: int = 0
    error_message: str | None = None
    notes: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_path": self.output_path,
            "engine_used": self.engine_used,
            "shape_count": self.shape_count,
            "error_message": self.error_message,
            "notes": self.notes or [],
        }


# STEP 写入引擎抽象基类


class StepWriterEngine:
    """STEP 写入引擎抽象基类。"""

    engine_name: str = "abstract"

    def is_available(self) -> bool:
        """引擎是否可用（已安装依赖）。"""
        raise NotImplementedError

    def write_step(
        self,
        shapes: list[BrepShape],
        output_path: Path,
    ) -> StepWriteResult:
        """把 BrepShape 列表写入 STEP 文件。"""
        raise NotImplementedError


# pythonOCC 引擎（优先级 1）


class PythonOccStepWriter(StepWriterEngine):
    """pythonOCC（OpenCASCADE Python 绑定）STEP 写入引擎。

    依赖：pip install pythonocc-core
    Windows 上推荐用 conda 安装：conda install -c conda-forge pythonocc-core
    """

    engine_name = "pythonocc"

    def __init__(self) -> None:
        """导入 pythonOCC 核心模块，失败抛 ImportError。"""
        try:
            from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Pln, gp_Trsf
            from OCC.Core.BRepBuilderAPI import (
                BRepBuilderAPI_MakeFace,
                BRepBuilderAPI_Transform,
            )
            from OCC.Core.BRepPrimAPI import (
                BRepPrimAPI_MakeCylinder,
                BRepPrimAPI_MakeBox,
            )
            from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
            from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
            from OCC.Core.IFSelect import IFSelect_RetDone
            from OCC.Core.TopoDS import TopoDS_Compound
            from OCC.Core.BRep import BRep_Builder

            self._gp_Pnt = gp_Pnt
            self._gp_Dir = gp_Dir
            self._gp_Ax2 = gp_Ax2
            self._gp_Pln = gp_Pln
            self._gp_Trsf = gp_Trsf
            self._make_face = BRepBuilderAPI_MakeFace
            self._make_transform = BRepBuilderAPI_Transform
            self._make_cylinder = BRepPrimAPI_MakeCylinder
            self._make_box = BRepPrimAPI_MakeBox
            self._bool_cut = BRepAlgoAPI_Cut
            self._bool_fuse = BRepAlgoAPI_Fuse
            self._step_writer = STEPControl_Writer
            self._step_as_is = STEPControl_AsIs
            self._ret_done = IFSelect_RetDone
            self._compound = TopoDS_Compound
            self._builder = BRep_Builder
        except ImportError as e:
            raise ImportError(f"pythonOCC 不可用: {e}") from e

    def is_available(self) -> bool:
        return True  # 构造成功即说明可用

    def _shape_to_topods(self, shape: BrepShape) -> Any:
        """把 BrepShape 转换为 OpenCASCADE TopoDS_Shape。"""
        origin = shape.origin
        direction = shape.direction
        params = shape.params

        # 构造坐标系：origin + direction（z 轴方向）
        # 找一个与 direction 不平行的向量作为 x 轴
        if abs(direction[2]) < 0.9:
            ref_x = [1.0, 0.0, 0.0]
        else:
            ref_x = [0.0, 1.0, 0.0]

        # x 轴 = ref_x - (ref_x·direction) * direction（Gram-Schmidt）
        dot = sum(a * b for a, b in zip(ref_x, direction))
        x_axis = [a - dot * b for a, b in zip(ref_x, direction)]
        x_norm = math.sqrt(sum(a * a for a in x_axis))
        if x_norm > 1e-9:
            x_axis = [a / x_norm for a in x_axis]

        ax2 = self._gp_Ax2(
            self._gp_Pnt(origin[0], origin[1], origin[2]),
            self._gp_Dir(direction[0], direction[1], direction[2]),
            self._gp_Dir(x_axis[0], x_axis[1], x_axis[2]),
        )

        if shape.shape_type == "plane":
            width = max(float(params.get("width_mm", 10.0)), 0.1)
            height = max(float(params.get("height_mm", 10.0)), 0.1)
            # 平面用 box 的顶面近似（厚度 0.1mm）
            box = self._make_box(width, height, 0.1).Shape()
            # 应用坐标系变换
            trsf = self._gp_Trsf()
            trsf.SetTransformation(ax2)
            transformer = self._make_transform(box, trsf, True)
            return transformer.Shape()

        if shape.shape_type == "cylinder":
            radius = max(float(params.get("radius_mm", 1.0)), 0.1)
            height = max(float(params.get("height_mm", 5.0)), 0.1)
            cyl = self._make_cylinder(ax2, radius, height).Shape()
            return cyl

        raise ValueError(f"不支持的 shape_type: {shape.shape_type}")

    def write_step(
        self,
        shapes: list[BrepShape],
        output_path: Path,
    ) -> StepWriteResult:
        """用 pythonOCC 写入 STEP 文件。"""
        if not shapes:
            return StepWriteResult(
                success=False,
                engine_used=self.engine_name,
                error_message="无可用形状",
            )

        try:
            # 把所有 add 形状 fuse 在一起，再依次 subtract 减运算形状
            add_shapes = [s for s in shapes if s.operation == "add"]
            sub_shapes = [s for s in shapes if s.operation == "subtract"]

            if not add_shapes:
                return StepWriteResult(
                    success=False,
                    engine_used=self.engine_name,
                    error_message="无 add 操作的形状，无法构造基础零件",
                )

            # 构造基础形状（第一个 add 形状）
            base_shape = self._shape_to_topods(add_shapes[0])
            notes = [f"base shape: {add_shapes[0].shape_id}"]

            # fuse 其余 add 形状
            for shape in add_shapes[1:]:
                try:
                    other = self._shape_to_topods(shape)
                    fuser = self._bool_fuse(base_shape, other)
                    base_shape = fuser.Shape()
                    notes.append(f"fused: {shape.shape_id}")
                except Exception as e:
                    safe = safe_error_message(e, context="pythonocc.fuse")
                    notes.append(f"fuse failed: {shape.shape_id} (error_id={safe.get('error_id')})")

            # subtract 所有 subtract 形状
            for shape in sub_shapes:
                try:
                    tool = self._shape_to_topods(shape)
                    cutter = self._bool_cut(base_shape, tool)
                    base_shape = cutter.Shape()
                    notes.append(f"cut: {shape.shape_id}")
                except Exception as e:
                    safe = safe_error_message(e, context="pythonocc.cut")
                    notes.append(f"cut failed: {shape.shape_id} (error_id={safe.get('error_id')})")

            # 写入 STEP
            writer = self._step_writer()
            writer.Transfer(base_shape, self._step_as_is)
            status = writer.Write(str(output_path))

            if status != self._ret_done:
                return StepWriteResult(
                    success=False,
                    engine_used=self.engine_name,
                    error_message=f"STEPControl_Writer 返回非 Done 状态: {status}",
                )

            return StepWriteResult(
                success=True,
                output_path=str(output_path),
                engine_used=self.engine_name,
                shape_count=len(shapes),
                notes=notes,
            )
        except Exception as e:
            safe = safe_error_message(e, context="pythonocc.write_step")
            return StepWriteResult(
                success=False,
                engine_used=self.engine_name,
                error_message=safe.get("message"),
            )


# FreeCAD Python API 引擎（优先级 2）


class FreeCadStepWriter(StepWriterEngine):
    """FreeCAD Python API STEP 写入引擎。

    依赖：安装 FreeCAD 并设置 FREECAD_HOME 环境变量
    使用方式：import FreeCAD + import Part
    """

    engine_name = "freecad"

    def __init__(self) -> None:
        """导入 FreeCAD Python 模块，失败抛 ImportError。"""
        try:
            import FreeCAD
            import Part
            from FreeCAD import Base

            self._freecad = FreeCAD
            self._part = Part
            self._base = Base
        except ImportError as e:
            raise ImportError(f"FreeCAD Python API 不可用: {e}") from e

    def is_available(self) -> bool:
        return True

    def _shape_to_freecad(self, shape: BrepShape) -> Any:
        """把 BrepShape 转换为 FreeCAD Part.Shape。"""
        origin = shape.origin
        direction = shape.direction
        params = shape.params

        # FreeCAD Vector
        origin_vec = self._freecad.Vector(origin[0], origin[1], origin[2])
        dir_vec = self._freecad.Vector(direction[0], direction[1], direction[2])

        if shape.shape_type == "plane":
            width = max(float(params.get("width_mm", 10.0)), 0.1)
            height = max(float(params.get("height_mm", 10.0)), 0.1)
            # 平面用 box 近似
            box = self._part.makeBox(width, height, 0.1)
            # 旋转使 z 轴对齐 direction
            # 简化处理：构造 Placement
            placement = self._freecad.Placement(
                self._freecad.Vector(0, 0, 0),
                self._freecad.Rotation(
                    self._freecad.Vector(0, 0, 1),
                    dir_vec,
                ),
                self._freecad.Vector(0, 0, 0),
            )
            box.Placement = placement
            box.translate(origin_vec)
            return box

        if shape.shape_type == "cylinder":
            radius = max(float(params.get("radius_mm", 1.0)), 0.1)
            height = max(float(params.get("height_mm", 5.0)), 0.1)
            cyl = self._part.makeCylinder(radius, height)
            # 旋转 + 平移
            placement = self._freecad.Placement(
                self._freecad.Vector(0, 0, 0),
                self._freecad.Rotation(
                    self._freecad.Vector(0, 0, 1),
                    dir_vec,
                ),
                self._freecad.Vector(0, 0, 0),
            )
            cyl.Placement = placement
            cyl.translate(origin_vec)
            return cyl

        raise ValueError(f"不支持的 shape_type: {shape.shape_type}")

    def write_step(
        self,
        shapes: list[BrepShape],
        output_path: Path,
    ) -> StepWriteResult:
        """用 FreeCAD API 写入 STEP 文件。"""
        if not shapes:
            return StepWriteResult(
                success=False,
                engine_used=self.engine_name,
                error_message="无可用形状",
            )

        try:
            add_shapes = [s for s in shapes if s.operation == "add"]
            sub_shapes = [s for s in shapes if s.operation == "subtract"]

            if not add_shapes:
                return StepWriteResult(
                    success=False,
                    engine_used=self.engine_name,
                    error_message="无 add 操作的形状",
                )

            base = self._shape_to_freecad(add_shapes[0])
            notes = [f"base shape: {add_shapes[0].shape_id}"]

            for shape in add_shapes[1:]:
                try:
                    other = self._shape_to_freecad(shape)
                    base = base.fuse(other)
                    notes.append(f"fused: {shape.shape_id}")
                except Exception as e:
                    safe = safe_error_message(e, context="freecad.fuse")
                    notes.append(f"fuse failed: {shape.shape_id} (error_id={safe.get('error_id')})")

            for shape in sub_shapes:
                try:
                    tool = self._shape_to_freecad(shape)
                    base = base.cut(tool)
                    notes.append(f"cut: {shape.shape_id}")
                except Exception as e:
                    safe = safe_error_message(e, context="freecad.cut")
                    notes.append(f"cut failed: {shape.shape_id} (error_id={safe.get('error_id')})")

            base.exportStep(str(output_path))

            return StepWriteResult(
                success=True,
                output_path=str(output_path),
                engine_used=self.engine_name,
                shape_count=len(shapes),
                notes=notes,
            )
        except Exception as e:
            safe = safe_error_message(e, context="freecad.write_step")
            return StepWriteResult(
                success=False,
                engine_used=self.engine_name,
                error_message=safe.get("message"),
            )


# 简易 STEP 模板引擎（优先级 3，降级方案）


class TemplateStepWriter(StepWriterEngine):
    """简易 STEP 模板写入引擎（不依赖任何外部库）。

    生成 ISO 10303-21 STEP AP214 文件，仅包含基础几何实体：
    - CARTESIAN_POINT
    - DIRECTION
    - AXIS2_PLACEMENT_3D
    - PLANE / CYLINDRICAL_SURFACE

    限制：
    - 无法表达布尔运算（add/subtract 仅作为元数据记录在注释中）
    - 无法表达复杂拓扑（相切/同心/垂直等约束丢失）
    - STEP 文件可能被 NX/PowerMill 部分拒绝
    - 主要用于测试与降级场景

    即便如此，本引擎生成的 STEP 仍可被 FreeCAD / pythonOCC 重新加载验证几何正确性。
    """

    engine_name = "template"

    def is_available(self) -> bool:
        return True  # 始终可用（不依赖外部库）

    def write_step(
        self,
        shapes: list[BrepShape],
        output_path: Path,
    ) -> StepWriteResult:
        """用手工模板写入 STEP 文件。"""
        if not shapes:
            return StepWriteResult(
                success=False,
                engine_used=self.engine_name,
                error_message="无可用形状",
            )

        try:
            step_content = self._build_step_ap214(shapes)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(step_content, encoding="ascii")

            notes = [
                "降级模式：简易 STEP 模板（pythonOCC / FreeCAD 均不可用）",
                "限制：无法表达布尔运算，仅记录几何参数",
                "推荐：安装 pythonocc-core 或 FreeCAD 以获得完整 STEP 输出",
            ]

            return StepWriteResult(
                success=True,
                output_path=str(output_path),
                engine_used=self.engine_name,
                shape_count=len(shapes),
                notes=notes,
            )
        except Exception as e:
            safe = safe_error_message(e, context="template.write_step")
            return StepWriteResult(
                success=False,
                engine_used=self.engine_name,
                error_message=safe.get("message"),
            )

    def _build_step_ap214(self, shapes: list[BrepShape]) -> str:
        """构造 ISO 10303-21 STEP AP214 文件内容。"""
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        lines: list[str] = []
        lines.append("ISO-10303-21;")
        lines.append("HEADER;")
        lines.append(
            "FILE_DESCRIPTION(('Lingjing Manufacturing parametric geometry (template engine - degraded)'),'2;1');"
        )
        lines.append(
            f"FILE_NAME('{shapes[0].shape_id if shapes else 'output'}.step',"
            f"'{timestamp}',('Lingjing'),('Lingjing'),"
            "'Lingjing v2.5','Lingjing','None');"
        )
        lines.append("FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));")
        lines.append("ENDSEC;")
        lines.append("DATA;")

        # 实体 ID 分配（从 #10 开始）
        entity_id = 10
        entity_lines: list[str] = []

        # 应用上下文（AP214 必需）
        entity_lines.append(f"#{entity_id} = APPLICATION_CONTEXT('automotive design');")
        app_ctx_id = entity_id
        entity_id += 1

        entity_lines.append(
            f"#{entity_id} = APPLICATION_PROTOCOL_DEFINITION("
            "'international standard','automotive_design',1998,"
            f"#{app_ctx_id});"
        )
        entity_id += 1

        # 几何形状集合
        for shape in shapes:
            origin = shape.origin
            direction = shape.direction
            params = shape.params

            # CARTESIAN_POINT（origin）
            cp_id = entity_id
            entity_lines.append(
                f"#{cp_id} = CARTESIAN_POINT('{shape.shape_id}_origin',"
                f"({origin[0]:.6f},{origin[1]:.6f},{origin[2]:.6f}));"
            )
            entity_id += 1

            # DIRECTION（direction）
            dir_id = entity_id
            entity_lines.append(
                f"#{dir_id} = DIRECTION('{shape.shape_id}_dir',"
                f"({direction[0]:.6f},{direction[1]:.6f},{direction[2]:.6f}));"
            )
            entity_id += 1

            # AXIS2_PLACEMENT_3D
            # 需要第二个方向（ref_direction），用 Gram-Schmidt 构造
            if abs(direction[2]) < 0.9:
                ref_x = [1.0, 0.0, 0.0]
            else:
                ref_x = [0.0, 1.0, 0.0]
            dot = sum(a * b for a, b in zip(ref_x, direction))
            x_axis = [a - dot * b for a, b in zip(ref_x, direction)]
            x_norm = math.sqrt(sum(a * a for a in x_axis))
            if x_norm > 1e-9:
                x_axis = [a / x_norm for a in x_axis]
            else:
                x_axis = [1.0, 0.0, 0.0]

            ref_dir_id = entity_id
            entity_lines.append(
                f"#{ref_dir_id} = DIRECTION('{shape.shape_id}_refdir',"
                f"({x_axis[0]:.6f},{x_axis[1]:.6f},{x_axis[2]:.6f}));"
            )
            entity_id += 1

            axis_id = entity_id
            entity_lines.append(
                f"#{axis_id} = AXIS2_PLACEMENT_3D('{shape.shape_id}_axis',#{cp_id},#{dir_id},#{ref_dir_id});"
            )
            entity_id += 1

            # 根据 shape_type 添加几何实体
            if shape.shape_type == "plane":
                width = max(float(params.get("width_mm", 10.0)), 0.1)
                height = max(float(params.get("height_mm", 10.0)), 0.1)
                # PLANE
                plane_id = entity_id
                entity_lines.append(f"#{plane_id} = PLANE('{shape.shape_id}_plane',#{axis_id});")
                entity_id += 1
                # 附加注释（width / height 作为元数据）
                entity_lines.append(
                    f"/* {shape.shape_id}: plane "
                    f"width={width:.3f}mm height={height:.3f}mm "
                    f"operation={shape.operation} */"
                )
            elif shape.shape_type == "cylinder":
                radius = max(float(params.get("radius_mm", 1.0)), 0.1)
                height = max(float(params.get("height_mm", 5.0)), 0.1)
                # CYLINDRICAL_SURFACE
                cyl_id = entity_id
                entity_lines.append(f"#{cyl_id} = CYLINDRICAL_SURFACE('{shape.shape_id}_cyl',#{axis_id},{radius:.6f});")
                entity_id += 1
                entity_lines.append(
                    f"/* {shape.shape_id}: cylinder "
                    f"radius={radius:.3f}mm height={height:.3f}mm "
                    f"operation={shape.operation} */"
                )

        lines.extend(entity_lines)
        lines.append("ENDSEC;")
        lines.append("END-ISO-10303-21;")

        return "\n".join(lines) + "\n"


# 引擎选择器


def get_available_engine() -> StepWriterEngine | None:
    """按优先级返回可用的 STEP 写入引擎。

    优先级：pythonOCC > FreeCAD API > 简易模板

    Returns:
        StepWriterEngine 或 None（所有引擎均不可用，理论上不会发生因为模板始终可用）
    """
    # 优先级 1: pythonOCC
    try:
        return PythonOccStepWriter()
    except ImportError as e:
        logger.debug(f"pythonOCC 不可用，降级到 FreeCAD: {e}")

    # 优先级 2: FreeCAD Python API
    try:
        return FreeCadStepWriter()
    except ImportError as e:
        logger.debug(f"FreeCAD API 不可用，降级到模板: {e}")

    # 优先级 3: 简易模板（始终可用）
    return TemplateStepWriter()


def write_step_with_fallback(
    shapes: list[BrepShape],
    output_path: Path,
) -> StepWriteResult:
    """使用可用引擎写入 STEP 文件（自动降级）。

    Args:
        shapes: BrepShape 列表
        output_path: STEP 文件输出路径

    Returns:
        StepWriteResult
    """
    engine = get_available_engine()
    if engine is None:
        return StepWriteResult(
            success=False,
            engine_used="unavailable",
            error_message="无可用 STEP 写入引擎",
        )
    return engine.write_step(shapes, output_path)
