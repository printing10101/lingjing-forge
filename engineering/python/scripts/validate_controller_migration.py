"""工艺迁移验证（B3）：同一工序计划 → FANUC 与目标控制器 → 等价性清单.

案例：黄金案例 #1 的 25 孔板（``case11_perforated_plate.dxf``），源 FANUC 0i，
目标默认 GSK 980/25i；输出 JSON + Markdown 清单到指定目录。

用法（仓库根）::

    python engineering/python/scripts/validate_controller_migration.py \
        --dxf data/test_fixtures/case11_perforated_plate.dxf \
        --target gsk_980_25i --out-dir tests/golden/case_01
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from app.dxf.dxf_parser import DxfParser  # noqa: E402
from app.dxf.feature_extractor import FeatureExtractor  # noqa: E402
from app.postprocessor.migration_validator import compare_programs, render_checklist  # noqa: E402
from app.process_planning.gcode_generator import GCodeGenerator  # noqa: E402
from app.process_planning.pipeline import ProcessPlanningPipeline  # noqa: E402

SOURCE_CONTROLLER = "fanuc_0i"
SAFE_Z = 80.0


def run(dxf_path: Path, target_controller: str, out_dir: Path) -> dict:
    parse_result = DxfParser().parse(str(dxf_path.resolve()))
    features = FeatureExtractor().extract(parse_result)
    if not features.success or not features.holes:
        raise RuntimeError("DXF 特征提取失败")
    height = float(features.overall_height)
    holes = [{"id": hole.hole_id, "type": hole.hole_type,
              "position": [hole.center_x, hole.center_y],
              "diameter": hole.diameter, "depth": height} for hole in features.holes]

    pipeline_result = ProcessPlanningPipeline().run(
        {"material": "45#钢", "part_type": "plate", "holes": holes},
        controller_type=SOURCE_CONTROLLER, safe_z=SAFE_Z, stock_top_z=height)
    if not pipeline_result.success or pipeline_result.gcode_result is None:
        raise RuntimeError(f"源控制器编程失败: {pipeline_result.summary}")
    plan = pipeline_result.operation_plan
    if plan is None:
        raise RuntimeError("工序计划为空，无法迁移")
    source_text = pipeline_result.gcode_result.program_text

    target_result = GCodeGenerator().generate(
        plan, controller_type=target_controller, material_name="45#钢",
        safe_z=SAFE_Z, stock_top_z=height)
    if not target_result.is_valid:
        raise RuntimeError(f"目标控制器程序语法校验失败: {target_result.errors}")

    report = compare_programs(source_text, target_result.program_text,
                              source_controller="fanuc", target_controller="fanuc")
    report["case"] = {"dxf": dxf_path.name, "holes": len(holes), "stock_height_mm": height,
                      "source_controller": SOURCE_CONTROLLER, "target_controller": target_controller}
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"migration_{SOURCE_CONTROLLER}_to_{target_controller}"
    (out_dir / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(
        render_checklist(report, source=SOURCE_CONTROLLER, target=target_controller), encoding="utf-8")
    (out_dir / f"{stem}_target.nc").write_text(target_result.program_text, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dxf", type=Path,
                        default=_REPO_ROOT / "data/test_fixtures/case11_perforated_plate.dxf")
    parser.add_argument("--target", default="gsk_980_25i")
    parser.add_argument("--out-dir", type=Path, default=_REPO_ROOT / "tests/golden/case_01")
    args = parser.parse_args()
    try:
        report = run(args.dxf, args.target, args.out_dir)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"[migration failed] {exc}", file=sys.stderr)
        return 1
    print(f"[migration] {SOURCE_CONTROLLER} → {args.target}: "
          f"{'PASS' if report['passed'] else 'FAIL'} "
          f"checks={len(report['checks'])} warn={report['summary']['warned']}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
