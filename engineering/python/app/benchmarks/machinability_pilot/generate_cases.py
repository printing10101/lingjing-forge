"""生成 machinability pilot 真值案例集（F2 里程碑 M1）。

对 10 个设计族 × 5 个难度档逐一构建 CadQuery 实体，做 B-rep 有效性检查，
导出 STEP（工艺链用）与 STL（几何相似度/体素仿真用），并把英文几何描述
写入 prompt.txt（M2 阶段喂给 LLM 生成器）。

用法（系统 Python 3.14，需 cadquery）：
    cd engineering/python
    python -m app.benchmarks.machinability_pilot.generate_cases --out ../../pilot/cases
可选: --families F01,F02  --difficulties d1,d2,d3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cadquery as cq

from app.benchmarks.machinability_pilot.case_families import (
    DIFFICULTY_TIERS,
    FAMILY_REGISTRY,
    CaseFamily,
)

# case_families.py → machinability_pilot → benchmarks → app → python → engineering → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUT = REPO_ROOT / "pilot" / "cases"

# STL 网格精度（mm）：0.05/0.2 rad 对 r≈20mm 的曲面弦差 <0.05mm，满足 CD 评估口径
STL_TOLERANCE = 0.05
STL_ANGULAR_TOLERANCE = 0.2


@dataclass
class CaseResult:
    case_id: str
    family: str
    difficulty: str
    ok: bool
    volume_mm3: float | None = None
    brep_valid: bool = False
    error: str | None = None


def generate_case(family: CaseFamily, difficulty: str, out_dir: Path) -> CaseResult:
    case_id = f"{family.family_id}-{difficulty}"
    params = family.params_for(difficulty)
    case_dir = out_dir / case_id
    try:
        case_dir.mkdir(parents=True, exist_ok=True)
        wp = family.build(params)
        shape = wp.val()
        brep_valid = bool(shape.isValid())
        volume = float(shape.Volume()) if brep_valid else None
        if not brep_valid or volume is None or volume <= 0.0:
            return CaseResult(case_id, family.family_id, difficulty, ok=False, error=f"invalid B-rep (volume={volume})")

        spec = {
            "case_id": case_id,
            "family": family.family_id,
            "family_name": family.name,
            "difficulty": difficulty,
            "surface_features": list(family.surface_features),
            "params": params,
            "units": "mm",
            "description": family.describe(params),
        }
        (case_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        (case_dir / "prompt.txt").write_text(family.describe(params) + "\n", encoding="utf-8")
        cq.exporters.export(wp, str(case_dir / "truth.step"))
        cq.exporters.export(
            wp, str(case_dir / "truth.stl"), tolerance=STL_TOLERANCE, angularTolerance=STL_ANGULAR_TOLERANCE
        )
        return CaseResult(case_id, family.family_id, difficulty, ok=True, volume_mm3=round(volume, 2), brep_valid=True)
    except Exception as exc:  # 单案例失败不阻断批次，manifest 里如实记录
        return CaseResult(case_id, family.family_id, difficulty, ok=False, error=f"{type(exc).__name__}: {exc}")


def generate_all(out_dir: Path, family_ids: list[str], difficulties: list[str]) -> list[CaseResult]:
    results = []
    for fid in family_ids:
        family = FAMILY_REGISTRY[fid]
        for d in difficulties:
            result = generate_case(family, d, out_dir)
            status = "ok" if result.ok else f"FAIL {result.error}"
            print(f"[{result.case_id}] {status} (volume={result.volume_mm3})")
            results.append(result)
    return results


def write_manifest(out_dir: Path, results: list[CaseResult], args: argparse.Namespace) -> Path:
    ok_count = sum(1 for r in results if r.ok)
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cadquery_version": cq.__version__,
        "command": {
            "families": args.families,
            "difficulties": args.difficulties,
            "stl_tolerance": STL_TOLERANCE,
            "stl_angular_tolerance": STL_ANGULAR_TOLERANCE,
        },
        "summary": {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count},
        "results": [asdict(r) for r in results],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 machinability pilot 真值案例集")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出根目录（默认 <repo>/pilot/cases）")
    parser.add_argument("--families", default=",".join(FAMILY_REGISTRY), help="逗号分隔的族 ID（默认全部）")
    parser.add_argument("--difficulties", default=",".join(DIFFICULTY_TIERS), help="逗号分隔的难度档（默认全部）")
    args = parser.parse_args()

    family_ids = [f.strip() for f in args.families.split(",") if f.strip()]
    difficulties = [d.strip() for d in args.difficulties.split(",") if d.strip()]
    unknown = [f for f in family_ids if f not in FAMILY_REGISTRY]
    bad_tiers = [d for d in difficulties if d not in DIFFICULTY_TIERS]
    if unknown or bad_tiers:
        raise SystemExit(f"未知族或难度档: {unknown or bad_tiers}")

    results = generate_all(args.out, family_ids, difficulties)
    manifest_path = write_manifest(args.out, results, args)
    ok_count = sum(1 for r in results if r.ok)
    print(f"\n完成: {ok_count}/{len(results)} 通过, manifest → {manifest_path}")

    # Windows + Python 3.14 上 OCCT 在解释器收尾阶段段错误（构建/导出/flush 均已完成），
    # 跳过收尾让退出码反映真实结果，否则永远以 139 退出
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok_count == len(results) else 1)


if __name__ == "__main__":
    main()
