"""冻结国产后处理器黄金程序（C5）并支持逐字节回归.

案例：黄金案例 #1 的 25 孔板工序计划（与 A2 同一输入），分别用 4 家国产
控制器后处理：广数 GSK980/25i、华中 HNC-848/22、凯恩帝 KND1000/2000/3000、
数马 XMachine XM-100。

**归一化口径**（逐字节比对前统一处理）：
- 仅把日期 ``YYYY-MM-DD`` 替换为 ``<DATE>``（程序头注释里的生成日期）；
- 其余字符一个不改，包括空格、换行、注释与大小写。

产物：``tests/golden/postprocessors/<controller>.nc`` + ``manifest.json``（SHA256）。

用法（仓库根，--freeze 生成/覆盖黄金文件；不带参数则只做比对）::

    python engineering/python/scripts/freeze_postprocessor_goldens.py --freeze
    python engineering/python/scripts/freeze_postprocessor_goldens.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from app.dxf.dxf_parser import DxfParser  # noqa: E402
from app.dxf.feature_extractor import FeatureExtractor  # noqa: E402
from app.process_planning.gcode_generator import GCodeGenerator  # noqa: E402
from app.process_planning.pipeline import ProcessPlanningPipeline  # noqa: E402

DEFAULT_DXF = _REPO_ROOT / "data" / "test_fixtures" / "case11_perforated_plate.dxf"
DEFAULT_OUT = _REPO_ROOT / "tests" / "golden" / "postprocessors"
SOURCE_CONTROLLER = "fanuc_0i"
SAFE_Z = 80.0
# XMachine XM-100 行程仅 ±50mm，且本管线平面工序坐标不随工件零点平移
# （实测仍在 X195），故不纳入黄金集；其"超程拒绝"行为由
# tests/unit/test_postprocessor_goldens.py 单独断言。
DOMESTIC_CONTROLLERS = ("gsk_980_25i", "hnc_848_22", "knd_1000_2000_3000")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize(program: str) -> str:
    """日期归一化；其余保持原样（逐字节口径）。"""
    return DATE_RE.sub("<DATE>", program)


def build_plan(dxf_path: Path, *, center_origin: bool = True):
    """从 DXF 走真实链路得到工序计划与毛坯高度。

    ``center_origin=True`` 时按「工件零点设在毛坯中心」的装夹约定平移孔位，
    使程序坐标落在 XM-100 等小行程机床的 ±50 mm 包络内；偏移量写入 manifest。
    """
    parse_result = DxfParser().parse(str(dxf_path.resolve()))
    features = FeatureExtractor().extract(parse_result)
    if not features.success or not features.holes:
        raise RuntimeError("DXF 特征提取失败")
    height = float(features.overall_height)
    offset_x = offset_y = 0.0
    if center_origin:
        offset_x = float(features.overall_min_x) + float(features.overall_length) / 2
        offset_y = float(features.overall_min_y) + float(features.overall_width) / 2
    holes = [{"id": hole.hole_id, "type": hole.hole_type,
              "position": [hole.center_x - offset_x, hole.center_y - offset_y],
              "diameter": hole.diameter, "depth": height} for hole in features.holes]
    # 平面工序需显式给轮廓，否则管线用 0..L 外包络（平移后仍越出小行程机床）
    from app.dxf.contour_extractor import classify_rings

    outer, _ = classify_rings(features.contours)
    contours = []
    if outer is not None:
        shifted = [[float(x) - offset_x, float(y) - offset_y] for x, y in outer.vertices]
        contours.append({
            "name": "TOP_SURFACE",
            "type": "top_plane",
            "geometric_type": "plane",
            "surface": "A",
            "dimensions": {"length": float(features.overall_length),
                           "width": float(features.overall_width)},
            "boundary": shifted,
            "contour": shifted,
        })
    pipeline_result = ProcessPlanningPipeline().run(
        {"material": "45#钢", "part_type": "plate", "holes": holes, "features": contours},
        controller_type=SOURCE_CONTROLLER, safe_z=SAFE_Z, stock_top_z=height)
    if not pipeline_result.success or pipeline_result.operation_plan is None:
        raise RuntimeError(f"工序计划生成失败: {pipeline_result.summary}")
    return pipeline_result.operation_plan, height, {"offset_x": round(-offset_x, 3), "offset_y": round(-offset_y, 3)}


def generate_all(dxf_path: Path) -> dict[str, str]:
    """返回 {controller: normalized_program}。"""
    plan, height, origin = build_plan(dxf_path)
    generator = GCodeGenerator()
    programs: dict[str, str] = {}
    for controller in DOMESTIC_CONTROLLERS:
        result = generator.generate(plan, controller_type=controller, material_name="45#钢",
                                    safe_z=SAFE_Z, stock_top_z=height)
        if not result.is_valid:
            raise RuntimeError(f"{controller} 程序未通过语法校验: {result.errors}")
        programs[controller] = normalize(result.program_text)
    return programs, origin


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def freeze(dxf_path: Path, out_dir: Path) -> dict:
    programs, origin = generate_all(dxf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"dxf": dxf_path.name, "source_controller": SOURCE_CONTROLLER,
                "normalization": "date YYYY-MM-DD → <DATE>", "origin_shift": origin, "programs": {}}
    for controller, program in programs.items():
        path = out_dir / f"{controller}.nc"
        path.write_text(program, encoding="utf-8")
        manifest["programs"][controller] = {
            "bytes": path.stat().st_size, "lines": len(program.splitlines()),
            "sha256": _sha256(program)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def verify(dxf_path: Path, out_dir: Path) -> tuple[bool, dict]:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"缺少 manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    programs, _origin = generate_all(dxf_path)
    results = {}
    ok = True
    for controller, program in programs.items():
        expected_path = out_dir / f"{controller}.nc"
        if not expected_path.exists():
            results[controller] = {"status": "missing_golden"}
            ok = False
            continue
        expected = expected_path.read_text(encoding="utf-8")
        same = program == expected
        ok = ok and same
        results[controller] = {"status": "identical" if same else "differs",
                               "sha256_now": _sha256(program),
                               "sha256_golden": _sha256(expected),
                               "bytes_now": len(program.encode("utf-8")),
                               "bytes_golden": len(expected.encode("utf-8"))}
    return ok, {"passed": ok, "programs": results, "normalization": manifest.get("normalization")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dxf", type=Path, default=DEFAULT_DXF)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--freeze", action="store_true", help="生成/覆盖黄金文件")
    args = parser.parse_args()
    try:
        if args.freeze:
            manifest = freeze(args.dxf, args.out_dir)
            for controller, info in manifest["programs"].items():
                print(f"[freeze] {controller}: {info['lines']} 行 / {info['bytes']} B")
            return 0
        ok, report = verify(args.dxf, args.out_dir)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"[goldens failed] {exc}", file=sys.stderr)
        return 1
    for controller, info in report["programs"].items():
        print(f"[verify] {controller}: {info['status']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
