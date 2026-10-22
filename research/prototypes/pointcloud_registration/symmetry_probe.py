"""Does a 180-degree pressure/suction flip fool point-to-point ICP?

For each blade variant:
  * build/load a STEP blade
  * sample independent 20k target/source clouds
  * flip the source 180 deg about X (pressure <-> suction) and translate it back
  * run the same point-to-point ICP from identity
  * report RMSE before/after, and pose error against the generating flip AND identity

If the flipped source already fits with low RMSE, the registration problem is
ambiguous: the solver can return the identity (or any symmetry-equivalent pose)
and still look good.  A cambered/twisted blade is the control: asymmetry should
make the flip fit worse and let ICP recover the intended pose.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation as Rot

from blade_model import export_blade
from registration_demo import icp_point_to_point, pose_errors, sample_surface, step_to_mesh

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data" / "blades"
ARTIFACT_DIR = HERE / "artifacts" / "blade_symmetry_20261020"
SPAN_MM = 60.0
CHORD_MM = 40.0
POINTS = 20_000
SEED = 42
NOISE_MM = 0.02
DROPOUT = 0.10


def build_case(name: str, **blade_kwargs) -> Path:
    case_dir = DATA_DIR / name
    step_path = case_dir / "blade.step"
    if not step_path.exists():
        export_blade(step_path, span=SPAN_MM, chord=CHORD_MM, **blade_kwargs)
    return step_path


def rmse_to_target(source: np.ndarray, target: np.ndarray) -> float:
    distances, _ = cKDTree(target).query(source)
    return float(np.sqrt(np.mean(distances**2)))


def angle_vs_identity(rotation: np.ndarray) -> float:
    cosine = np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.degrees(np.arccos(cosine)))


def prepare_flip_case(name: str, step_path: Path, axis: str, seed: int = SEED) -> dict:
    mesh, volume_mm3 = step_to_mesh(step_path, step_path.parent / "blade_fine.stl")
    target = sample_surface(mesh, POINTS, seed=seed)
    source_base = sample_surface(mesh, POINTS, seed=seed + 1)

    if axis == "x":
        rotation_gen = Rot.from_euler("x", 180.0, degrees=True).as_matrix()
        translation_gen = np.array([0.0, 0.0, SPAN_MM])
    elif axis == "z":
        rotation_gen = Rot.from_euler("z", 180.0, degrees=True).as_matrix()
        translation_gen = np.array([CHORD_MM, 0.0, 0.0])
    else:
        raise ValueError(axis)

    source = (rotation_gen @ source_base.T).T + translation_gen
    rng = np.random.default_rng(seed + 100)
    if NOISE_MM > 0:
        source = source + rng.normal(0.0, NOISE_MM, source.shape)
    if DROPOUT > 0:
        keep = rng.random(len(source)) > DROPOUT
        source = source[keep]

    return {
        "case": name,
        "axis": axis,
        "mesh": mesh,
        "volume_mm3": volume_mm3,
        "target": target,
        "source": source,
        "rotation_gen": rotation_gen,
        "translation_gen": translation_gen,
    }


def run_flip_case(name: str, step_path: Path, axis: str) -> dict:
    prepared = prepare_flip_case(name, step_path, axis)
    mesh = prepared["mesh"]
    volume_mm3 = prepared["volume_mm3"]
    target = prepared["target"]
    source = prepared["source"]
    rotation_gen = prepared["rotation_gen"]
    translation_gen = prepared["translation_gen"]

    rmse_before = rmse_to_target(source, target)
    ideal_rotation = rotation_gen.T
    ideal_translation = -rotation_gen.T @ translation_gen
    aligned_ideal = (ideal_rotation @ source.T).T + ideal_translation
    rmse_ideal = rmse_to_target(aligned_ideal, target)

    result = icp_point_to_point(source, target, max_iter=100, tol=1e-9)
    rmse_after = rmse_to_target(result.aligned, target)

    rot_err_flip, trans_err_flip = pose_errors(
        result.rotation,
        result.translation,
        rotation_gen,
        translation_gen,
    )
    rot_err_identity = angle_vs_identity(result.rotation)
    trans_err_identity = float(np.linalg.norm(result.translation))

    return {
        "case": name,
        "axis": axis,
        "mesh": {
            "vertices": int(len(mesh.vertices)),
            "faces": int(len(mesh.faces)),
            "volume_mm3": float(volume_mm3),
        },
        "rmse_before_mm": rmse_before,
        "rmse_at_ideal_inverse_mm": rmse_ideal,
        "rmse_after_mm": rmse_after,
        "rotation_error_vs_generating_inverse_deg": rot_err_flip,
        "translation_error_vs_generating_inverse_mm": trans_err_flip,
        "rotation_error_vs_identity_deg": rot_err_identity,
        "translation_error_vs_identity_mm": trans_err_identity,
        "source_points": int(len(source)),
        "iterations": result.iterations,
        "ambiguity": bool(rmse_before < 0.5 and rot_err_flip > 90.0),
        "local_minimum": bool(rot_err_flip > 90.0 and rmse_after > rmse_ideal * 1.5),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cases = [
        (
            "symmetric_naca0012",
            {"m": 0.0, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0},
            "x",
        ),
        (
            "cambered_naca2412",
            {"m": 0.02, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0},
            "x",
        ),
        (
            "twisted_tapered_naca2412",
            {"m": 0.02, "p": 0.4, "t": 0.12, "twist_deg": 20.0, "taper": 0.25},
            "x",
        ),
        (
            "cambered_naca2412_yaw_control",
            {"m": 0.02, "p": 0.4, "t": 0.12, "twist_deg": 0.0, "taper": 0.0},
            "z",
        ),
    ]

    rows = []
    print(
        f"{'case':30} {'axis':4} {'rmse_before':>11} {'rmse_ideal':>10} "
        f"{'rmse_after':>10} {'err_vs_flip':>11} {'err_vs_identity':>15} "
        f"{'ambiguous':>10} {'local_min':>9}"
    )
    print("-" * 120)
    for name, kwargs, axis in cases:
        step = build_case(name, **kwargs)
        row = run_flip_case(name, step, axis)
        rows.append(row)
        print(
            f"{name:30} {axis:>4} {row['rmse_before_mm']:>11.4f} "
            f"{row['rmse_at_ideal_inverse_mm']:>10.4f} "
            f"{row['rmse_after_mm']:>10.4f} "
            f"{row['rotation_error_vs_generating_inverse_deg']:>11.3f} "
            f"{row['rotation_error_vs_identity_deg']:>15.3f} "
            f"{str(row['ambiguity']):>10} {str(row['local_minimum']):>9}"
        )

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "points": POINTS,
            "seed": SEED,
            "noise_mm": NOISE_MM,
            "dropout": DROPOUT,
            "span_mm": SPAN_MM,
            "chord_mm": CHORD_MM,
            "flip": "source = 180deg rotation about axis + translation to overlap",
        },
        "cases": rows,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = ARTIFACT_DIR / "results.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult: {output_path}")

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
