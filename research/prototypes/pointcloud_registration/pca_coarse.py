"""PCA coarse registration + point-to-point ICP on the flipped blade cases.

This is the standard baseline the SRT proposal calls "PCA/主方向粗配准".
It enumerates the four proper sign combinations of the principal axes, runs
point-to-point ICP from each, and selects the candidate with the lowest RMSE.

The question is not whether PCA can run; it is whether the RMSE-based selection
can tell the correct pressure/suction pose from its symmetry mate.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pca_core import pca_candidates
from registration_demo import icp_point_to_point, pose_errors
from symmetry_probe import (
    ARTIFACT_DIR,
    angle_vs_identity,
    build_case,
    prepare_flip_case,
    rmse_to_target,
)

PCA_ARTIFACT_DIR = ARTIFACT_DIR.parent / "blade_symmetry_pca_20261020"


def run_case(name: str, step_path: Path, axis: str) -> dict:
    prepared = prepare_flip_case(name, step_path, axis)
    target = prepared["target"]
    source = prepared["source"]
    rotation_gen = prepared["rotation_gen"]
    translation_gen = prepared["translation_gen"]

    candidates = []
    for candidate in pca_candidates(source, target):
        rotation0 = candidate["rotation"]
        translation0 = candidate["translation"]
        source_init = (rotation0 @ source.T).T + translation0
        result = icp_point_to_point(source_init, target, max_iter=80, tol=1e-8)
        rmse = rmse_to_target(result.aligned, target)

        rotation_total = result.rotation @ rotation0
        translation_total = result.rotation @ translation0 + result.translation
        rotation_error_flip, translation_error_flip = pose_errors(
            rotation_total,
            translation_total,
            rotation_gen,
            translation_gen,
        )
        candidates.append(
            {
                "signs": list(candidate["signs"]),
                "rmse_mm": rmse,
                "rotation_error_vs_generating_inverse_deg": rotation_error_flip,
                "translation_error_vs_generating_inverse_mm": translation_error_flip,
                "rotation_error_vs_identity_deg": angle_vs_identity(rotation_total),
            }
        )

    candidates.sort(key=lambda item: item["rmse_mm"])
    best = candidates[0]
    # A candidate is "correct" only if it lands on the generating inverse, not merely low-RMSE.
    correct = (
        best["rotation_error_vs_generating_inverse_deg"] < 1.0
        and best["translation_error_vs_generating_inverse_mm"] < 0.5
    )
    near_best = [item for item in candidates if item["rmse_mm"] <= best["rmse_mm"] + 0.05]
    return {
        "case": name,
        "axis": axis,
        "best_rmse_mm": best["rmse_mm"],
        "best_rotation_error_vs_generating_inverse_deg": best["rotation_error_vs_generating_inverse_deg"],
        "best_rotation_error_vs_identity_deg": best["rotation_error_vs_identity_deg"],
        "correct": bool(correct),
        "near_best_candidates": len(near_best),
        "candidates": candidates,
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
        f"{'case':30} {'best_rmse':>10} {'best_vs_flip':>13} {'best_vs_identity':>17} {'correct':>8} {'near_best':>10}"
    )
    print("-" * 100)
    for name, kwargs, axis in cases:
        step = build_case(name, **kwargs)
        row = run_case(name, step, axis)
        rows.append(row)
        print(
            f"{name:30} {row['best_rmse_mm']:>10.4f} "
            f"{row['best_rotation_error_vs_generating_inverse_deg']:>13.3f} "
            f"{row['best_rotation_error_vs_identity_deg']:>17.3f} "
            f"{str(row['correct']):>8} {row['near_best_candidates']:>10}"
        )

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "method": "PCA axes + four sign candidates + point-to-point ICP; select lowest RMSE",
        "cases": rows,
    }
    PCA_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PCA_ARTIFACT_DIR / "results.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult: {output_path}")

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
