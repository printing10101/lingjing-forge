"""F06 STEP -> 20k surface samples -> point-to-point ICP.

This is a feasibility prototype for the blade point-cloud registration project.
It deliberately uses only numpy/scipy/trimesh for the ICP core; cadquery/OCP is
used only to read the STEP and tessellate it.

Typical run (system Python 3.14, cadquery + trimesh + scipy installed):

    python registration_demo.py --angles 5,30,60,90

If ``data/F06-d1/truth.step`` is missing, the script calls the existing
machinability benchmark generator to build it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation as Rot

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
ENGINEERING_PY = REPO_ROOT / "engineering" / "python"
DEFAULT_CASE_DIR = HERE / "data" / "F06-d1"
DEFAULT_ARTIFACT_DIR = HERE / "artifacts" / "f06_icp_20261020"


@dataclass(frozen=True)
class ICPResult:
    aligned: np.ndarray
    rotation: np.ndarray
    translation: np.ndarray
    rmse_mm: float
    max_nn_mm: float
    iterations: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_f06_case(case_dir: Path, force: bool = False) -> Path:
    """Generate F06-d1 with the existing benchmark generator if needed."""
    step = case_dir / "truth.step"
    if step.exists() and not force:
        return step
    sys.path.insert(0, str(ENGINEERING_PY))
    from app.benchmarks.machinability_pilot.case_families import FAMILY_REGISTRY
    from app.benchmarks.machinability_pilot.generate_cases import generate_case

    result = generate_case(FAMILY_REGISTRY["F06"], "d1", case_dir)
    if not result.ok:
        raise RuntimeError(f"F06-d1 generation failed: {result.error}")
    if not step.exists():
        raise RuntimeError(f"generator returned ok but STEP is missing: {step}")
    return step


def step_to_mesh(step_path: Path, stl_path: Path):
    """Import STEP through OCC and tessellate with a finer deflection than the benchmark STL."""
    import cadquery as cq
    import trimesh

    workplane = cq.importers.importStep(str(step_path))
    shape = workplane.val()
    if not shape.isValid():
        raise RuntimeError(f"invalid B-rep after STEP import: {step_path}")
    cq.exporters.export(
        workplane,
        str(stl_path),
        tolerance=0.005,
        angularTolerance=0.05,
    )
    mesh = trimesh.load(str(stl_path), force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return mesh, shape.Volume()


def sample_surface(mesh, count: int, seed: int) -> np.ndarray:
    import trimesh

    points, _ = trimesh.sample.sample_surface(mesh, count, seed=seed)
    return np.asarray(points, dtype=np.float64)


def icp_point_to_point(
    source: np.ndarray,
    target: np.ndarray,
    max_iter: int = 80,
    tol: float = 1e-7,
) -> ICPResult:
    """Classic point-to-point ICP (Besl & McKay) with SVD rigid update."""
    src = np.asarray(source, dtype=np.float64).copy()
    dst = np.asarray(target, dtype=np.float64)
    if src.ndim != 2 or dst.ndim != 2 or src.shape[1] != 3 or dst.shape[1] != 3:
        raise ValueError("source and target must be (N, 3) and (M, 3)")

    tree = cKDTree(dst)
    rotation = np.eye(3)
    translation = np.zeros(3)
    previous_rmse = float("inf")
    iterations = 0

    for iterations in range(1, max_iter + 1):
        _, indices = tree.query(src)
        matched = dst[indices]

        src_centroid = src.mean(axis=0)
        dst_centroid = matched.mean(axis=0)
        cross_cov = (src - src_centroid).T @ (matched - dst_centroid)

        u, _, vt = np.linalg.svd(cross_cov)
        delta_r = vt.T @ u.T
        if np.linalg.det(delta_r) < 0:
            vt[-1, :] *= -1
            delta_r = vt.T @ u.T
        delta_t = dst_centroid - delta_r @ src_centroid

        src = (delta_r @ src.T).T + delta_t
        rotation = delta_r @ rotation
        translation = delta_r @ translation + delta_t

        rmse = float(np.sqrt(np.mean(np.sum((src - matched) ** 2, axis=1))))
        if abs(previous_rmse - rmse) < tol:
            break
        previous_rmse = rmse

    final_distances, _ = tree.query(src)
    return ICPResult(
        aligned=src,
        rotation=rotation,
        translation=translation,
        rmse_mm=float(np.sqrt(np.mean(final_distances**2))),
        max_nn_mm=float(np.max(final_distances)),
        iterations=iterations,
    )


def pose_errors(
    rotation_est: np.ndarray,
    translation_est: np.ndarray,
    rotation_true: np.ndarray,
    translation_true: np.ndarray,
) -> tuple[float, float]:
    """Compare ICP's source->target transform with the inverse of the known source pose.

    The test source is built as ``source = R_true @ target + t_true``; therefore
    the ideal ICP result is ``R_true.T, -R_true.T @ t_true``.
    """
    residual = rotation_est @ rotation_true
    cos_angle = np.clip((np.trace(residual) - 1.0) / 2.0, -1.0, 1.0)
    rotation_error_deg = float(np.degrees(np.arccos(cos_angle)))
    translation_error_mm = float(np.linalg.norm(translation_est - (-rotation_true.T @ translation_true)))
    return rotation_error_deg, translation_error_mm


def run_one_angle(
    target: np.ndarray,
    source_base: np.ndarray,
    angle_deg: float,
    args: argparse.Namespace,
) -> dict:
    rotation_true = Rot.from_euler(
        "zyx",
        [angle_deg, args.tilt_deg[0], args.tilt_deg[1]],
        degrees=True,
    ).as_matrix()
    translation_true = np.asarray(args.translation_mm, dtype=np.float64)

    source = (rotation_true @ source_base.T).T + translation_true
    rng = np.random.default_rng(args.seed + int(angle_deg * 1000))
    if args.noise_mm > 0:
        source = source + rng.normal(0.0, args.noise_mm, source.shape)
    if args.dropout > 0:
        keep = rng.random(len(source)) > args.dropout
        source = source[keep]

    started = time.perf_counter()
    result = icp_point_to_point(
        source,
        target,
        max_iter=args.max_iter,
        tol=args.tolerance,
    )
    runtime_s = time.perf_counter() - started

    rotation_error_deg, translation_error_mm = pose_errors(
        result.rotation,
        result.translation,
        rotation_true,
        translation_true,
    )
    success = rotation_error_deg <= args.success_rot_deg and translation_error_mm <= args.success_trans_mm
    return {
        "init_angle_deg": float(angle_deg),
        "success": bool(success),
        "rotation_error_deg": rotation_error_deg,
        "translation_error_mm": translation_error_mm,
        "rmse_mm": result.rmse_mm,
        "max_nn_mm": result.max_nn_mm,
        "iterations": result.iterations,
        "runtime_s": runtime_s,
        "source_points": int(len(source)),
        "det_rotation": float(np.linalg.det(result.rotation)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="F06 STEP -> 20k points -> point-to-point ICP")
    parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)
    parser.add_argument("--regenerate", action="store_true", help="rebuild F06-d1 even if STEP exists")
    parser.add_argument("--points", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--angles", default="5,30,60,90", help="comma-separated initial pose angles (deg)")
    parser.add_argument("--tilt-deg", default="4,2", help="extra x/y tilt in degrees, e.g. 4,2")
    parser.add_argument("--translation-mm", default="12,-6,4", help="known translation, e.g. 12,-6,4")
    parser.add_argument("--noise-mm", type=float, default=0.03)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--max-iter", type=int, default=80)
    parser.add_argument("--tolerance", type=float, default=1e-7)
    parser.add_argument("--success-rot-deg", type=float, default=1.0)
    parser.add_argument("--success-trans-mm", type=float, default=0.5)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    args.tilt_deg = tuple(float(v) for v in args.tilt_deg.split(","))
    args.translation_mm = tuple(float(v) for v in args.translation_mm.split(","))
    angles = [float(v) for v in args.angles.split(",") if v.strip()]

    step_path = ensure_f06_case(args.case_dir, force=args.regenerate)
    fine_stl = args.case_dir / "truth_fine.stl"
    mesh, volume_mm3 = step_to_mesh(step_path, fine_stl)

    target = sample_surface(mesh, args.points, seed=args.seed)
    source_base = sample_surface(mesh, args.points, seed=args.seed + 1)

    print(f"STEP   : {step_path}")
    print(f"mesh   : {len(mesh.vertices)} vertices / {len(mesh.faces)} faces, volume={volume_mm3:.2f} mm^3")
    print(f"points : target={len(target)}, source_base={len(source_base)}, seed={args.seed}")
    print()

    header = f"{'init(deg)':>9} {'rot_err(deg)':>13} {'trans_err(mm)':>14} {'rmse(mm)':>9} {'max_nn(mm)':>10} {'iters':>6} {'time(s)':>8} {'ok':>4}"
    print(header)
    print("-" * len(header))
    cases = []
    for angle in angles:
        case = run_one_angle(target, source_base, angle, args)
        cases.append(case)
        print(
            f"{case['init_angle_deg']:>9.1f} {case['rotation_error_deg']:>13.4f} "
            f"{case['translation_error_mm']:>14.4f} {case['rmse_mm']:>9.4f} "
            f"{case['max_nn_mm']:>10.4f} {case['iterations']:>6d} "
            f"{case['runtime_s']:>8.2f} {'PASS' if case['success'] else 'FAIL':>4}"
        )

    summary = {
        "total": len(cases),
        "success": sum(1 for c in cases if c["success"]),
        "failed": sum(1 for c in cases if not c["success"]),
    }
    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "step": {
            "path": str(step_path),
            "sha256": sha256_file(step_path),
        },
        "mesh": {
            "vertices": int(len(mesh.vertices)),
            "faces": int(len(mesh.faces)),
            "volume_mm3": float(volume_mm3),
        },
        "config": {
            "points": args.points,
            "seed": args.seed,
            "angles_deg": angles,
            "tilt_deg": list(args.tilt_deg),
            "translation_mm": list(args.translation_mm),
            "noise_mm": args.noise_mm,
            "dropout": args.dropout,
            "max_iter": args.max_iter,
            "tolerance": args.tolerance,
            "success_rot_deg": args.success_rot_deg,
            "success_trans_mm": args.success_trans_mm,
            "sampling": "target and source are independent surface samples",
        },
        "cases": cases,
        "summary": summary,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.out_dir / "results.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(
        f"summary: {summary['success']}/{summary['total']} pass (rot<={args.success_rot_deg} deg, trans<={args.success_trans_mm} mm)"
    )
    print(f"result : {output_path}")

    sys.stdout.flush()
    sys.stderr.flush()
    # OCP on Windows + Python 3.14 can segfault during interpreter teardown.
    os._exit(0 if summary["failed"] == 0 else 2)


if __name__ == "__main__":
    main()
