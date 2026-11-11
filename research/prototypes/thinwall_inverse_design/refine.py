"""Resolve only the failed time-step convergence check; keep the first run frozen."""

import argparse
import json
from pathlib import Path

import numpy as np

if __package__:
    from .mechanics import Cutting, Strip, extract_modes, periodic_boundary
    from .pilot import load_frozen, write_json
else:
    from mechanics import Cutting, Strip, extract_modes, periodic_boundary
    from pilot import load_frozen, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    config = load_frozen(output)
    previous = json.loads((output/"verification.json").read_text(encoding="utf-8"))
    summary = json.loads((output/"screening_summary.json").read_text(encoding="utf-8"))
    rows = previous["periodic_boundaries"]
    refinements = []
    done = {}
    for label, chosen in summary["selected"].items():
        for rpm in config["verification"]["off_grid_rpms"]:
            curve = [r for r in rows if r["design"] == label and r["rpm"] == rpm]
            coarse, fine = curve[-2]["boundary_mm"], curve[-1]["boundary_mm"]
            if coarse is None or fine is None or abs(coarse-fine)/fine <= 0.05:
                continue
            key = (chosen["design_id"], rpm)
            if key in done:
                refinements.append({**done[key], "design": label, "reused_identical_profile": True})
                continue
            modes, _, _ = extract_modes(np.array(chosen["thickness_mm"])/1000, 1, 48, 4, 0.01,
                                         Strip(**config["geometry"]))
            depth = periodic_boundary(modes, rpm, steps=320, cutting=Cutting(**config["cutting"]))
            row = {"design": label, "rpm": rpm, "steps": 320,
                   "boundary_mm": None if depth is None else depth*1000,
                   "previous_160_boundary_mm": fine,
                   "relative_change": None if depth is None else abs(depth*1000-fine)/(depth*1000),
                   "purpose": "adaptive numerical refinement after failed 80/160 check; not new confirmatory evidence"}
            done[key] = row
            refinements.append(row)
            print(json.dumps(row), flush=True)
    write_json(output/"step_refinement.json", {"criterion": "relative change <=5%, heuristic numerical tolerance not a rigorous error bound",
               "refinements": refinements, "all_refined_checks_below_5_percent": all(
                   r["relative_change"] is not None and r["relative_change"] <= 0.05 for r in refinements)})


if __name__ == "__main__":
    main()
