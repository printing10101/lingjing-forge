"""核心路径性能基线（E4）：测量 → 存档 → 回归比对.

覆盖链路：DXF 解析 / 特征提取 / STEP 建模 / 切削参数 / G-code 生成 / 体素校验。

**工具说明（诚实口径）**：`pytest-benchmark` 在本机无法安装（pip 无可用索引，
`No matching distribution found`），故用自建 harness 输出同口径统计
（min / p50 / p95 / mean，单位 ms），基线 JSON 存档于
``engineering/python/benchmarks/core_paths_baseline.json``。

**回归判据**：同机比对 p50 —— 超过基线 2× 记 warning，超过 3× 记 fail（退出码 1）。
跨机器/跨 Python 版本不可直接比，基线文件记录环境指纹，比对前先核对。

用法（仓库根）::

    python engineering/python/scripts/benchmark_core_paths.py --save     # 建立/覆盖基线
    python engineering/python/scripts/benchmark_core_paths.py --compare  # 对基线做回归检查
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

DEFAULT_DXF = _REPO_ROOT / "data" / "test_fixtures" / "case11_perforated_plate.dxf"
BASELINE_PATH = _PYTHON_ROOT / "benchmarks" / "core_paths_baseline.json"
WARN_RATIO = 2.0
FAIL_RATIO = 3.0


def summarize(samples_ms: list[float]) -> dict[str, float]:
    """min / p50 / p95 / mean（ms）。"""
    if not samples_ms:
        raise ValueError("samples 不能为空")
    ordered = sorted(samples_ms)
    p95_index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {"min": round(ordered[0], 3), "p50": round(statistics.median(ordered), 3),
            "p95": round(ordered[p95_index], 3),
            "mean": round(statistics.fmean(ordered), 3), "n": len(ordered)}


def compare_to_baseline(current: dict, baseline: dict, *,
                        warn_ratio: float = WARN_RATIO, fail_ratio: float = FAIL_RATIO) -> dict:
    """逐阶段比对 p50；返回 {status, rows}。"""
    if warn_ratio <= 1 or fail_ratio <= warn_ratio:
        raise ValueError("阈值需满足 1 < warn_ratio < fail_ratio")
    rows = []
    overall = "ok"
    for stage, stats in current.items():
        base = baseline.get(stage)
        if not base:
            rows.append({"stage": stage, "status": "no_baseline", "ratio": None})
            continue
        ratio = stats["p50"] / base["p50"] if base["p50"] else float("inf")
        status = "ok"
        if ratio > fail_ratio:
            status = "fail"
        elif ratio > warn_ratio:
            status = "warn"
        if status == "fail" or (status == "warn" and overall == "ok"):
            overall = status
        rows.append({"stage": stage, "status": status, "ratio": round(ratio, 3),
                     "current_p50": stats["p50"], "baseline_p50": base["p50"]})
    return {"status": overall, "rows": rows}


def _time_ms(func, iterations: int, warmup: int = 1) -> list[float]:
    for _ in range(warmup):
        func()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        func()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def run_benchmarks(iterations: int) -> dict[str, dict[str, float]]:
    from app.cam_validation.voxel_validator import VoxelValidator
    from app.config import CamValidationConfig
    from app.cutting_parameters.recommender import CuttingParamRecommender
    from app.dxf.dxf_parser import DxfParser
    from app.dxf.feature_extractor import FeatureExtractor
    from app.process_planning.pipeline import ProcessPlanningPipeline
    from scripts.golden_case_01 import build_step  # 复用 A2 的 STEP 构建（真实几何）

    dxf = str(DEFAULT_DXF)
    results: dict[str, dict[str, float]] = {}

    parse_result = DxfParser().parse(dxf)
    results["dxf_parse"] = summarize(_time_ms(lambda: DxfParser().parse(dxf), iterations))

    features = FeatureExtractor().extract(parse_result)
    results["feature_extract"] = summarize(_time_ms(lambda: FeatureExtractor().extract(parse_result), iterations))

    tmp_dir = Path(tempfile.mkdtemp(prefix="bench_step_"))
    step_path = tmp_dir / "bench.step"
    results["step_build"] = summarize(_time_ms(lambda: build_step(features, step_path), iterations))

    recommender = CuttingParamRecommender()
    holes = features.holes

    def _params():
        for hole in holes:
            recommender.recommend(feature_id=hole.hole_id, feature_type="hole", material_id="steel_45",
                                  precision_tier="standard", tool_diameter_mm=6.0, num_flutes=4,
                                  machine_type="vmc_850")

    results["cutting_params"] = summarize(_time_ms(_params, iterations))

    height = float(features.overall_height)
    part_desc = {"material": "45#钢", "part_type": "plate",
                 "holes": [{"id": hole.hole_id, "type": hole.hole_type,
                            "position": [hole.center_x, hole.center_y],
                            "diameter": hole.diameter, "depth": height} for hole in features.holes],
                 "overall_dimensions": {"length": float(features.overall_length),
                                        "width": float(features.overall_width),
                                        "min_x": float(features.overall_min_x),
                                        "min_y": float(features.overall_min_y)}}

    def _gcode():
        result = ProcessPlanningPipeline().run(part_desc, controller_type="fanuc_0i",
                                               safe_z=80.0, stock_top_z=height)
        if not result.success or result.gcode_result is None:
            raise RuntimeError("G-code 基准运行失败")
        return result

    results["gcode_generate"] = summarize(_time_ms(_gcode, iterations))
    gcode_text = _gcode().gcode_result.program_text

    validator = VoxelValidator(CamValidationConfig())
    results["voxel_validate"] = summarize(_time_ms(
        lambda: validator.validate(gcode_text=gcode_text, controller_type="fanuc_0i", safe_z=80.0,
                                   stock_top_z=height, stock_length=float(features.overall_length),
                                   stock_width=float(features.overall_width), stock_height=height,
                                   tool_diameter_mm=6.0, breakthrough_tolerance_mm=3.0),
        max(2, iterations // 2)))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--save", action="store_true", help="写入/覆盖基线文件")
    parser.add_argument("--compare", action="store_true", help="与基线比对")
    args = parser.parse_args()
    if args.iterations < 2:
        print("[bench] --iterations 至少为 2", file=sys.stderr)
        return 2
    try:
        current = run_benchmarks(args.iterations)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"[bench failed] {exc}", file=sys.stderr)
        return 1

    for stage, stats in current.items():
        print(f"[bench] {stage:<18} p50={stats['p50']:>8.3f} ms  p95={stats['p95']:>8.3f} ms")

    if args.save:
        baseline = {"environment": {"python": platform.python_version(), "platform": platform.platform()},
                    "iterations": args.iterations, "stages": current}
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bench] baseline saved: {BASELINE_PATH.relative_to(_REPO_ROOT)}")
        return 0

    if args.compare:
        if not BASELINE_PATH.exists():
            print("[bench] 基线不存在，先跑 --save", file=sys.stderr)
            return 1
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        report = compare_to_baseline(current, baseline["stages"])
        for row in report["rows"]:
            ratio = f"{row['ratio']:.2f}x" if row["ratio"] is not None else "n/a"
            print(f"[compare] {row['stage']:<18} {row['status']:<10} {ratio}")
        print(f"[compare] overall={report['status']}")
        return 0 if report["status"] != "fail" else 1

    print("[bench] 未指定 --save/--compare，仅输出本次测量")
    return 0


if __name__ == "__main__":
    sys.exit(main())
