"""How much camber is needed before PCA+ICP can tell the two flip poses apart?

Runs the symmetric-style 180-degree pressure/suction flip at several NACA camber
values.  The metric is the RMSE margin between the best and second-best PCA
candidate; if that margin is below the sampling floor, the choice is a coin flip.
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

from seed_sweep import SEEDS, run_seed
from symmetry_probe import ARTIFACT_DIR, build_case

CAMBER_ARTIFACT_DIR = ARTIFACT_DIR.parent / "blade_camber_sweep_20261020"
CAMBER_VALUES = [0.0, 0.005, 0.01, 0.02, 0.04]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    summaries = []
    all_rows = []
    print(f"{'camber':>8} {'correct':>9} {'margin_mean':>13} {'margin_min':>12} {'mean_rmse':>11}")
    print("-" * 60)
    for camber in CAMBER_VALUES:
        name = f"camber_{camber:.3f}"
        step = build_case(
            name,
            m=camber,
            p=0.4,
            t=0.12,
            twist_deg=0.0,
            taper=0.0,
        )
        rows = [run_seed(name, step, "x", seed) for seed in SEEDS]
        all_rows.extend(rows)
        margins = [row["rmse_margin_mm"] for row in rows]
        rmses = [row["best_rmse_mm"] for row in rows]
        summary = {
            "camber": camber,
            "correct": sum(1 for row in rows if row["correct"]),
            "total": len(rows),
            "margin_mean_mm": float(np.mean(margins)),
            "margin_min_mm": float(np.min(margins)),
            "rmse_mean_mm": float(np.mean(rmses)),
        }
        summaries.append(summary)
        print(
            f"{camber:>8.3f} {summary['correct']:>4}/{summary['total']:<4} "
            f"{summary['margin_mean_mm']:>13.4f} {summary['margin_min_mm']:>12.4f} "
            f"{summary['rmse_mean_mm']:>11.4f}"
        )

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seeds": SEEDS,
        "method": "PCA axes + four sign candidates + ICP; choose lowest RMSE",
        "summaries": summaries,
        "rows": all_rows,
    }
    CAMBER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CAMBER_ARTIFACT_DIR / "results.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult: {output_path}")

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
