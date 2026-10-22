"""Seed sweep for the symmetry ambiguity under PCA + ICP.

A single run can pick the correct pressure/suction pose by luck.  This sweep
repeats the symmetric and cambered flip cases over several sampling seeds and
counts how often the lowest-RMSE PCA candidate is actually the correct pose.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from pca_core import pca_candidates
from registration_demo import icp_point_to_point, pose_errors
from symmetry_probe import ARTIFACT_DIR, build_case, prepare_flip_case, rmse_to_target

SWEEP_ARTIFACT_DIR = ARTIFACT_DIR.parent / "blade_symmetry_seed_sweep_20261020"
SEEDS = list(range(42, 52))


def run_seed(name: str, step_path: Path, axis: str, seed: int) -> dict:
    prepared = prepare_flip_case(name, step_path, axis, seed=seed)
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
        rotation_total = result.rotation @ rotation0
        translation_total = result.rotation @ translation0 + result.translation
        rotation_error, translation_error = pose_errors(
            rotation_total,
            translation_total,
            rotation_gen,
            translation_gen,
        )
        candidates.append(
            {
                "signs": list(candidate["signs"]),
                "rmse_mm": rmse_to_target(result.aligned, target),
                "rotation_error_deg": rotation_error,
                "translation_error_mm": translation_error,
            }
        )

    candidates.sort(key=lambda item: item["rmse_mm"])
    best = candidates[0]
    second = candidates[1]
    correct = best["rotation_error_deg"] < 1.0 and best["translation_error_mm"] < 0.5
    return {
        "case": name,
        "seed": seed,
        "correct": bool(correct),
        "best_signs": best["signs"],
        "best_rmse_mm": best["rmse_mm"],
        "best_rotation_error_deg": best["rotation_error_deg"],
        "best_translation_error_mm": best["translation_error_mm"],
        "second_signs": second["signs"],
        "second_rmse_mm": second["rmse_mm"],
        "rmse_margin_mm": second["rmse_mm"] - best["rmse_mm"],
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
    ]

    all_rows = []
    for name, kwargs, axis in cases:
        step = build_case(name, **kwargs)
        rows = [run_seed(name, step, axis, seed) for seed in SEEDS]
        all_rows.extend(rows)
        correct_count = sum(1 for row in rows if row["correct"])
        margins = [row["rmse_margin_mm"] for row in rows]
        print(
            f"{name:30} correct={correct_count}/{len(rows)} "
            f"margin mean={np.mean(margins):.4f} min={np.min(margins):.4f} mm"
        )
        for row in rows:
            print(
                f"  seed={row['seed']:>2} best={row['best_signs']} "
                f"rmse={row['best_rmse_mm']:.4f} rot_err={row['best_rotation_error_deg']:7.3f} "
                f"margin={row['rmse_margin_mm']:.4f} {'OK' if row['correct'] else 'WRONG'}"
            )

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seeds": SEEDS,
        "method": "PCA axes + four sign candidates + ICP; choose lowest RMSE",
        "summary": {
            case_name: {
                "correct": sum(1 for row in all_rows if row["case"] == case_name and row["correct"]),
                "total": sum(1 for row in all_rows if row["case"] == case_name),
            }
            for case_name, _, _ in cases
        },
        "rows": all_rows,
    }
    SWEEP_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SWEEP_ARTIFACT_DIR / "results.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult: {output_path}")

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
