"""STEP 三维实体 → 工艺规划 → NC 代码 → 体素校验端到端桥接。

从单实体 STEP 文件提取：
1. 三维包络尺寸（length / width / height / min_x / min_y / z_min / z_max）；
2. Z 向内圆柱孔特征（自动合并半圆分裂面，区分内孔与外圆柱凸台，计算孔径与孔深）；
3. 端到端串联 ``CuttingParamRecommender`` → ``ProcessPlanningPipeline`` → ``VoxelValidator``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StepFeatureExtractionResult:
    """从 STEP 单实体提取出的几何与孔特征摘要。"""

    step_path: str
    solid_count: int
    face_count: int
    overall_dimensions: dict[str, float]
    holes: list[dict[str, Any]] = field(default_factory=list)
    part_description: dict[str, Any] = field(default_factory=dict)


def _is_point_outside_solid(solid_shape: Any, x: float, y: float, z: float) -> bool:
    """用 OCCT BRepClass3d_SolidClassifier 判断点是否在实体外部（区分内孔与外凸台）。"""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_OUT

    classifier = BRepClass3d_SolidClassifier(solid_shape, gp_Pnt(x, y, z), 1e-4)
    return classifier.State() == TopAbs_OUT


def extract_step_features(
    step_path: str | Path,
    *,
    material: str = "steel_45",
    face_allowance_mm: float = 0.5,
) -> StepFeatureExtractionResult:
    """从单实体 STEP 文件提取三维包络与 Z 向孔特征，构造标准 ``part_description``。

    Raises:
        FileNotFoundError: STEP 文件不存在。
        ValueError: STEP 不含实体或为多实体装配体（需先拆分为单实体）。
    """
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    path = Path(step_path)
    if not path.is_file():
        raise FileNotFoundError(f"STEP 文件不存在: {path}")

    shape = cq.importers.importStep(str(path))
    solids = shape.solids().vals()
    if not solids:
        raise ValueError(f"STEP 文件中未找到任何三维实体: {path.name}")
    if len(solids) > 1:
        raise ValueError(
            f"STEP 文件包含 {len(solids)} 个实体（装配体），请先拆分为单实体零件后再生成工艺: {path.name}"
        )

    solid = solids[0]
    bb = solid.BoundingBox()
    length = float(bb.xmax - bb.xmin)
    width = float(bb.ymax - bb.ymin)
    height = float(bb.zmax - bb.zmin)
    overall_dimensions = {
        "length": round(length, 4),
        "width": round(width, 4),
        "height": round(height, 4),
        "min_x": round(float(bb.xmin), 4),
        "min_y": round(float(bb.ymin), 4),
        "min_z": round(float(bb.zmin), 4),
        "max_z": round(float(bb.zmax), 4),
    }

    faces = solid.Faces()
    # 按 (round(cx, 2), round(cy, 2), round(diameter, 2)) 合并分裂的半圆柱面
    hole_buckets: dict[tuple[float, float, float], dict[str, Any]] = {}
    for face in faces:
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() != GeomAbs_Cylinder:
            continue
        cyl = adaptor.Cylinder()
        axis_dir = cyl.Axis().Direction()
        if abs(axis_dir.Z()) < 0.99:
            continue  # 仅处理 Z 向立式加工孔特征
        loc = cyl.Location()
        cx, cy = float(loc.X()), float(loc.Y())
        radius = float(cyl.Radius())
        diameter = radius * 2.0
        if diameter <= 0:
            continue

        fbb = face.BoundingBox()
        z_low, z_high = float(fbb.zmin), float(fbb.zmax)
        z_mid = (z_low + z_high) / 2.0
        # 圆心在实体外部才是内孔；在实体内部则是外圆柱凸台/圆角柱
        if not _is_point_outside_solid(solid.wrapped, cx, cy, z_mid):
            continue

        key = (round(cx, 2), round(cy, 2), round(diameter, 2))
        bucket = hole_buckets.get(key)
        if bucket is None:
            hole_buckets[key] = {
                "x": cx,
                "y": cy,
                "diameter": diameter,
                "z_low": z_low,
                "z_high": z_high,
            }
        else:
            bucket["z_low"] = min(bucket["z_low"], z_low)
            bucket["z_high"] = max(bucket["z_high"], z_high)

    holes: list[dict[str, Any]] = []
    # 平移使模型底面/左下角对齐工件坐标系（若原 STEP 原点不在 (0,0,0)）
    x_shift = -float(bb.xmin)
    y_shift = -float(bb.ymin)
    for idx, (_, b) in enumerate(sorted(hole_buckets.items(), key=lambda item: (item[0][0], item[0][1])), start=1):
        z_low = b["z_low"]
        is_through = z_low <= float(bb.zmin) + 0.1
        depth = (height + float(face_allowance_mm or 0.0)) if is_through else max(float(bb.zmax) - z_low + float(face_allowance_mm or 0.0), 0.1)
        holes.append(
            {
                "id": f"HOLE_{idx:03d}",
                "type": "through_hole" if is_through else "blind_hole",
                "position": [round(b["x"] + x_shift, 4), round(b["y"] + y_shift, 4)],
                "diameter": round(b["diameter"], 4),
                "depth": round(depth, 4),
            }
        )

    part_description: dict[str, Any] = {
        "material": material,
        "part_type": "plate" if holes else "general",
        "holes": holes,
        "face_allowance_mm": float(face_allowance_mm or 0.0),
        "overall_dimensions": {
            "length": round(length, 4),
            "width": round(width, 4),
            "height": round(height, 4),
            "min_x": 0.0,
            "min_y": 0.0,
        },
    }

    return StepFeatureExtractionResult(
        step_path=str(path),
        solid_count=len(solids),
        face_count=len(faces),
        overall_dimensions=overall_dimensions,
        holes=holes,
        part_description=part_description,
    )


def run_step_to_nc_pipeline(
    step_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    material: str = "steel_45",
    controller_type: str = "fanuc_0i",
    machine_type: str = "vmc_850",
    face_allowance_mm: float = 0.5,
    safe_z: float = 80.0,
    breakthrough_tolerance_mm: float = 3.0,
) -> dict[str, Any]:
    """一键运行「STEP 单实体 → 特征提取 → 切参推荐 → 工艺/G代码 → 体素+欠切校验」。"""
    from app.cam_validation.voxel_validator import VoxelValidator
    from app.config import CamValidationConfig
    from app.cutting_parameters.recommender import CuttingParamRecommender
    from app.process_planning.operation_sequencer import (
        DEFAULT_MILL_DIAMETER_MM,
        MAX_DRILL_DIAMETER_MM,
    )
    from app.process_planning.pipeline import ProcessPlanningPipeline

    extracted = extract_step_features(
        step_path,
        material=material,
        face_allowance_mm=face_allowance_mm,
    )
    dims = extracted.overall_dimensions
    stock_top = float(dims["height"]) + float(face_allowance_mm or 0.0)
    if safe_z <= stock_top:
        safe_z = stock_top + 20.0

    # 1. 为每个识别出的孔推荐经过机床限幅的切削参数
    recommender = CuttingParamRecommender()
    for hole in extracted.holes:
        dia = float(hole["diameter"])
        tool_dia = dia if dia <= MAX_DRILL_DIAMETER_MM else DEFAULT_MILL_DIAMETER_MM
        rec = recommender.recommend(
            feature_id=hole["id"],
            feature_type="hole",
            material_id=material,
            precision_tier="standard",
            tool_diameter_mm=tool_dia,
            num_flutes=4,
            machine_type=machine_type,
        )
        hole["spindle_speed"] = rec.spindle_speed_rpm
        hole["feed_rate"] = rec.feed_rate_mm_per_min
        hole["params_source"] = "切削参数推荐器"

    # 2. 工艺规划与后处理
    pipeline_result = ProcessPlanningPipeline().run(
        part_description=extracted.part_description,
        controller_type=controller_type,
        safe_z=safe_z,
        stock_top_z=stock_top,
    )
    if not pipeline_result.success or pipeline_result.gcode_result is None or not pipeline_result.gcode_result.is_valid:
        raise RuntimeError(f"STEP 工艺规划或 G 代码生成失败: {pipeline_result.summary}")

    nc_text = pipeline_result.gcode_result.program_text

    # 3. 体素碰撞 + D4 欠切/切到位校验
    expected_features = [
        {
            "id": h["id"],
            "type": "hole",
            "x": h["position"][0],
            "y": h["position"][1],
            "diameter": h["diameter"],
            "depth": h["depth"],
        }
        for h in extracted.holes
    ]
    report = VoxelValidator(CamValidationConfig()).validate(
        gcode_text=nc_text,
        controller_type=controller_type.split("_")[0],
        safe_z=safe_z,
        stock_top_z=stock_top,
        stock_length=float(dims["length"]),
        stock_width=float(dims["width"]),
        stock_height=stock_top,
        tool_diameter_mm=DEFAULT_MILL_DIAMETER_MM,
        breakthrough_tolerance_mm=breakthrough_tolerance_mm,
        expected_features=expected_features or None,
    )

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{Path(step_path).stem}.{controller_type}.nc").write_text(nc_text, encoding="utf-8")

    return {
        "step_path": extracted.step_path,
        "overall_dimensions": dims,
        "hole_count": len(extracted.holes),
        "holes": extracted.holes,
        "gcode_lines": pipeline_result.gcode_result.total_lines,
        "voxel_passed": report.passed,
        "severity": report.severity,
        "collision_count": report.collision_count,
        "undercut_count": report.undercut_count,
        "removed_voxel_count": report.removed_voxel_count,
    }
