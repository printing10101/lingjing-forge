"""黄金案例 #1：DXF → 特征 → STEP → 参数 → 颤振 → G-code → 体素校验（A2）.

一条命令跑通全链路，产物 + 日志 + 指纹入 ``tests/golden/case_01/``：

1. **DXF 解析**：``app.dxf.dxf_parser.DxfParser``（ezdxf，真实文件）；
2. **特征提取**：``app.dxf.feature_extractor.FeatureExtractor``（孔/轮廓）；
3. **STEP 建模**：外轮廓拉伸 − 孔特征布尔（CadQuery/OCP，真实几何）；
4. **切削参数**：``CuttingParamRecommender``（材料库 + 稳定性证据）；
5. **颤振**：取全部特征中最小稳定性裕度，写入 chatter_report；
6. **G-code**：``ProcessPlanningPipeline``（孔识别 → 工序规划 → 后处理 → 语法校验）；
7. **体素校验**：``VoxelValidator``（毛坯去除仿真 + 碰撞判定）。

口径说明（诚实标注）
--------------------
- 通孔钻削按工业常规留 3 mm 超程（代码内 ``_DRILL_BREAKTHROUGH_MM=3.0``），
  体素校验以 ``breakthrough_tolerance_mm=3.0`` 放行，**不是放宽碰撞判定**，
  而是把"设计允许的穿透量"显式声明出来；其余任何越界仍判 critical。
- 案例零件：``data/test_fixtures/case11_perforated_plate.dxf``（100×100×10，25×Ø6 通孔）。
- 不依赖网络与 LLM；同一输入两次运行的几何/参数/G-code 应逐字节一致
  （G-code 头部含生成日期，manifest 对程序体做哈希时剔除注释行）。

用法（仓库根）::

    python engineering/python/scripts/golden_case_01.py --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import sys
import time
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

DEFAULT_DXF = _REPO_ROOT / "data" / "test_fixtures" / "case11_perforated_plate.dxf"
DEFAULT_OUT = _REPO_ROOT / "tests" / "golden" / "case_01"
MATERIAL_ID = "steel_45"
CONTROLLER = "fanuc_0i"
TOOL_DIAMETER_MM = 6.0
NUM_FLUTES = 4
MACHINE_TYPE = "vmc_850"
SAFE_Z = 80.0
BREAKTHROUGH_TOLERANCE_MM = 3.0  # 与 _DRILL_BREAKTHROUGH_MM 对齐
# 毛坯顶面余量（mm）：真实板料总比成品厚一点，第一道工序把它光掉。
# 毛坯厚度 = 零件厚度 + 该余量；余量为 0 时管线会跳过光刀（避免把零件切薄）。
FACE_ALLOWANCE_MM = 0.5

logger = logging.getLogger("golden_case_01")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _program_body_digest(text: str) -> str:
    """剔除注释与日期行后的程序体哈希（跨日期可复现比对）。"""
    body = [line for line in text.splitlines() if not line.strip().startswith("(") and not line.strip().startswith(";")]
    return hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()


def _stage(records: list[dict], name: str, started: float, **payload) -> None:
    records.append({"stage": name, "duration_ms": round((time.perf_counter() - started) * 1000, 2), **payload})
    logger.info("[stage] %s %s", name, payload.get("status", "ok"))


def _polygon_area(points: list[tuple[float, float]]) -> float:
    """鞋带公式面积（mm²），用于非矩形轮廓的体积预期。"""
    total = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def build_step(features, step_path: Path) -> dict:
    """外轮廓拉伸 − 孔布尔 → STEP；返回几何摘要。"""
    import cadquery as cq
    from app.dxf.contour_extractor import classify_rings

    outer, _ = classify_rings(features.contours)
    if outer is None:
        raise RuntimeError("未找到外轮廓环，无法建模")
    height = float(features.overall_height)
    points = [(float(x), float(y)) for x, y in outer.vertices]
    solid = cq.Workplane("XY").polyline(points).close().extrude(height)
    for hole in features.holes:
        cylinder = (cq.Workplane("XY").center(float(hole.center_x), float(hole.center_y))
                    .circle(float(hole.diameter) / 2).extrude(height))
        solid = solid.cut(cylinder)
    volume = float(solid.val().Volume())
    cq.exporters.export(solid, str(step_path))
    # 预期体积按真实外轮廓多边形面积（非包围盒），孔按圆柱扣除
    expected = _polygon_area(points) * height
    expected -= sum(3.141592653589793 * (float(h.diameter) / 2) ** 2 * height for h in features.holes)
    error_pct = abs(volume - expected) / expected * 100
    if error_pct > 5.0:
        raise RuntimeError(f"STEP 体积与轮廓预期偏差 {error_pct:.2f}% > 5%")
    return {"volume_mm3": round(volume, 1), "expected_volume_mm3": round(expected, 1),
            "volume_error_pct": round(error_pct, 3), "height_mm": height,
            "outer_vertices": len(points), "outer_area_mm2": round(_polygon_area(points), 1),
            "holes_subtracted": len(features.holes)}


def run(dxf_path: Path, out_dir: Path, case_name: str | None = None) -> dict:
    from app.cam_validation.voxel_validator import VoxelValidator
    from app.config import CamValidationConfig
    from app.cutting_parameters.recommender import CuttingParamRecommender
    from app.dxf.dxf_parser import DxfParser
    from app.dxf.feature_extractor import FeatureExtractor
    from app.process_planning.pipeline import ProcessPlanningPipeline

    dxf_path = dxf_path.resolve()
    out_dir = out_dir.resolve()
    stages: list[dict] = []
    started_all = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. DXF 解析
    started = time.perf_counter()
    parse_result = DxfParser().parse(str(dxf_path))
    if not parse_result.success:
        raise RuntimeError(f"DXF 解析失败: {parse_result.errors}")
    (out_dir / "dxf_parse.json").write_text(
        json.dumps(parse_result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _stage(stages, "dxf_parse", started, status="ok", entities=parse_result.total_entities)

    # 2. 特征提取
    started = time.perf_counter()
    features = FeatureExtractor().extract(parse_result)
    if not features.success or not features.holes:
        raise RuntimeError(f"特征提取失败或无孔特征: {features.errors}")
    (out_dir / "features.json").write_text(
        json.dumps(features.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _stage(stages, "feature_extract", started, status="ok", holes=features.hole_count,
           contours=features.contour_count)

    # 3. STEP 建模
    started = time.perf_counter()
    step_path = out_dir / "model.step"
    geometry = build_step(features, step_path)
    if step_path.stat().st_size < 10_000:
        raise RuntimeError("STEP 文件异常偏小")
    _stage(stages, "step_build", started, status="ok", **geometry)

    # 4. 切削参数（含稳定性证据）
    # 每个孔按**实际会用到的刀具**取直径：能一次钻透的按孔径，超过钻削上限的
    # 改走螺旋铣（用立铣刀）。此前全部按 TOOL_DIAMETER_MM=6 计算，导致 Ø60 孔
    # 拿到的是 Ø6 刀的参数——线速度换算差一个量级，证据链第一环就是假的。
    from app.process_planning.operation_sequencer import (
        DEFAULT_MILL_DIAMETER_MM,
        MAX_DRILL_DIAMETER_MM,
    )

    started = time.perf_counter()
    recommender = CuttingParamRecommender()
    rows = []
    for hole in features.holes:
        cutting_tool_mm = (
            float(hole.diameter)
            if float(hole.diameter) <= MAX_DRILL_DIAMETER_MM
            else DEFAULT_MILL_DIAMETER_MM
        )
        params = recommender.recommend(
            feature_id=hole.hole_id, feature_type="hole", material_id=MATERIAL_ID,
            precision_tier="standard", tool_diameter_mm=cutting_tool_mm,
            num_flutes=NUM_FLUTES, machine_type=MACHINE_TYPE)
        evidence = params.stability_evidence
        if not evidence or evidence.get("status") == "unavailable":
            raise RuntimeError(f"{hole.hole_id} 缺少稳定性证据: {evidence}")
        rows.append({"feature_id": params.feature_id, "hole_diameter_mm": float(hole.diameter),
                     "tool_diameter_mm": cutting_tool_mm,
                     "spindle_speed_rpm": params.spindle_speed_rpm,
                     "feed_rate_mm_per_min": params.feed_rate_mm_per_min,
                     "axial_depth_mm": params.axial_depth_mm,
                     "stability_status": evidence["status"],
                     "stability_limit_mm": evidence.get("limit_depth_mm"),
                     "stability_margin": evidence.get("margin_ratio"),
                     "warnings": params.warnings})
    (out_dir / "cutting_params.json").write_text(
        json.dumps({"material": MATERIAL_ID, "features": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    _stage(stages, "cutting_params", started, status="ok", features=len(rows))

    # 5. 颤振：最小裕度特征
    started = time.perf_counter()
    worst = min(rows, key=lambda row: row["stability_margin"] or float("inf"))
    chatter_report = {
        "source_feature": worst["feature_id"],
        "spindle_speed_rpm": worst["spindle_speed_rpm"],
        "limit_depth_mm": worst["stability_limit_mm"],
        "margin_ratio": worst["stability_margin"],
        "status": worst["stability_status"],
        "method": "Tlusty 1-DOF 绝对包络（app.simulation.chatter.stability）",
        "caveats": ["单模态 1-DOF，未计径向浸入增益", "证据供工程师复核，不构成上机许可"],
    }
    (out_dir / "chatter_report.json").write_text(
        json.dumps(chatter_report, ensure_ascii=False, indent=2), encoding="utf-8")
    _stage(stages, "chatter", started, status="ok", worst_margin=worst["stability_margin"])

    # 6. G-code（孔识别 → 工序规划 → 后处理 → 语法校验）
    started = time.perf_counter()
    # 通孔深度取**毛坯厚度**（零件厚 + 余量）：光刀把顶面降下来之后，
    # 孔仍要切到零件底面，否则螺旋铣会在孔底留下一个等于余量的台。
    stock_top = float(features.overall_height) + FACE_ALLOWANCE_MM
    rec_by_id = {r["feature_id"]: r for r in rows}
    holes = []
    for hole in features.holes:
        rec = rec_by_id.get(hole.hole_id, {})
        holes.append({
            "id": hole.hole_id,
            "type": hole.hole_type,
            "position": [hole.center_x, hole.center_y],
            "diameter": hole.diameter,
            "depth": stock_top,
            "spindle_speed": rec.get("spindle_speed_rpm"),
            "feed_rate": rec.get("feed_rate_mm_per_min"),
            "params_source": "切削参数推荐器",
        })
    # 基准面刀轨由 overall_dimensions 推导；缺省会回退 200×100 固定包络，
    # 对 100×100 件会切到工件外（A2 首版实测 X 至 195），故显式传真实包络。
    pipeline_result = ProcessPlanningPipeline().run(
        {"material": MATERIAL_ID, "part_type": "plate", "holes": holes,
         "face_allowance_mm": FACE_ALLOWANCE_MM,
         "overall_dimensions": {"length": float(features.overall_length),
                                "width": float(features.overall_width),
                                "min_x": float(features.overall_min_x),
                                "min_y": float(features.overall_min_y)}},
        controller_type=CONTROLLER, safe_z=SAFE_Z, stock_top_z=stock_top)
    if not pipeline_result.success or pipeline_result.gcode_result is None:
        raise RuntimeError(f"工艺规划/编程失败: {pipeline_result.summary}")
    gcode_result = pipeline_result.gcode_result
    if not gcode_result.is_valid:
        raise RuntimeError(f"G-code 语法校验未通过: {gcode_result.errors}")
    program_text = gcode_result.program_text
    (out_dir / "program.nc").write_text(program_text, encoding="utf-8")
    (out_dir / "pipeline.json").write_text(
        json.dumps({"summary": pipeline_result.summary,
                    "stages": [{"name": s.name, "status": s.status, "errors": s.errors}
                               for s in pipeline_result.stages]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    _stage(stages, "gcode", started, status="ok", lines=gcode_result.total_lines,
           program_number=gcode_result.program_number)

    # 7. 体素校验（含 D4 期望孔特征欠切/切到位检查）
    started = time.perf_counter()
    expected_features = [
        {"id": hole.hole_id, "type": "hole", "x": hole.center_x, "y": hole.center_y,
         "diameter": hole.diameter, "depth": stock_top}
        for hole in features.holes
    ]
    report = VoxelValidator(CamValidationConfig()).validate(
        gcode_text=program_text, controller_type=CONTROLLER, safe_z=SAFE_Z,
        stock_top_z=stock_top,
        stock_length=float(features.overall_length), stock_width=float(features.overall_width),
        stock_height=stock_top, tool_diameter_mm=TOOL_DIAMETER_MM,
        breakthrough_tolerance_mm=BREAKTHROUGH_TOLERANCE_MM,
        expected_features=expected_features)
    voxel = report.to_dict()
    (out_dir / "voxel_report.json").write_text(
        json.dumps(voxel, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report.passed:
        raise RuntimeError(f"体素校验未通过: severity={voxel.get('severity')} "
                           f"collisions={voxel.get('collision_count')}")
    _stage(stages, "voxel_validation", started, status="ok",
           removed_voxels=voxel.get("removed_voxel_count"), segments=voxel.get("total_segments"))

    # 8. 指纹与 manifest
    started = time.perf_counter()
    artifacts = ["dxf_parse.json", "features.json", "model.step", "cutting_params.json",
                 "chatter_report.json", "program.nc", "pipeline.json", "voxel_report.json"]
    manifest = {
        "case": case_name or f"{dxf_path.stem}",
        "dxf": {"path": str(dxf_path.relative_to(_REPO_ROOT)) if dxf_path.is_relative_to(_REPO_ROOT) else str(dxf_path),
                "sha256": _sha256(dxf_path)},
        "artifacts": {name: {"bytes": (out_dir / name).stat().st_size,
                             "sha256": _sha256(out_dir / name)} for name in artifacts},
        "program_body_sha256": _program_body_digest(program_text),
        "stages": stages,
        "elapsed_seconds": round(time.perf_counter() - started_all, 2),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "parameters": {"material": MATERIAL_ID, "controller": CONTROLLER,
                       "tool_diameter_rule": "按孔径取刀（>Ø20 改螺旋铣，用 Ø10 立铣刀）",
                       "num_flutes": NUM_FLUTES,
                       "machine": MACHINE_TYPE, "safe_z": SAFE_Z,
                       "face_allowance_mm": FACE_ALLOWANCE_MM,
                       "breakthrough_tolerance_mm": BREAKTHROUGH_TOLERANCE_MM},
        "passed": True,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _stage(stages, "manifest", started, status="ok")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dxf", type=Path, default=DEFAULT_DXF)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--case-name", default=None, help="manifest 中的案例名（默认取 DXF 文件名）")
    parser.add_argument("--force", action="store_true", help="覆盖已有输出目录")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "run.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_path, mode="w", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    if args.force:
        for name in ("dxf_parse.json", "features.json", "model.step", "cutting_params.json",
                     "chatter_report.json", "program.nc", "pipeline.json", "voxel_report.json",
                     "manifest.json"):
            target = args.out_dir / name
            if target.exists():
                target.unlink()
    elif (args.out_dir / "manifest.json").exists():
        print(f"[golden] 输出已存在: {args.out_dir}（用 --force 覆盖）", file=sys.stderr)
        return 1

    try:
        manifest = run(args.dxf, args.out_dir, args.case_name)
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        logger.error("[golden] 失败: %s", exc)
        return 1
    print(f"[golden] PASSED  阶段数={len(manifest['stages'])}  "
          f"耗时={manifest['elapsed_seconds']}s  输出={args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
