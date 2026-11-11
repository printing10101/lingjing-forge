"""Posthoc resolution check of the six cases selected before this run."""

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from prototypes.thinwall_inverse_design.coupled_milling import CoupledModes, Milling, coupled_radius
from prototypes.thinwall_inverse_design.speed_dynamics import rescale_modes


def main():
    folder = Path(__file__).resolve().parent
    root = folder.parents[1]
    if (folder / "refinement_640.json").exists():
        raise FileExistsError("Retain the completed refinement.")
    for name in ("coupled_milling.py", "mechanics.py", "speed_dynamics.py"):
        assert (root / name).read_bytes() == (folder / "source" / name).read_bytes(), "Source drift"
    config = json.loads((folder / "source/speed_config.json").read_text(encoding="utf-8"))
    plan = json.loads((folder / "refinement_plan.json").read_text(encoding="utf-8"))
    previous = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    rows = {(r["original_row"], r["variant"]): r for r in previous["records"]}
    mass = np.array(config["mass_kg"])
    stiffness = np.array(config["stiffness_n_m"])
    damping = np.array(config["damping_n_s_m"]) / (2 * np.sqrt(mass * stiffness))
    baseline = CoupledModes(np.sqrt(stiffness / mass), damping, np.diag(1 / np.sqrt(mass)))
    tool = Milling(**config["milling"])
    immersion = {"D": 1, "0.5D": .5, "0.1D": .1, "0.05D": .05}
    start = time.monotonic()
    records = []
    for selected in plan["flagged"]:
        row = rows[(selected["original_row"], selected["variant"])]
        modes = rescale_modes(baseline, row["frequency_ratio"], row["static_compliance_ratio"])
        rho = coupled_radius(modes, row["rpm"], row["depth_mm"] / 1000, plan["steps"],
                             replace(tool, immersion=immersion[row["ae_label"]]), config["delay_rule"])
        records.append({**selected, "rho_640": rho, "stable_640": rho < 1,
                        "stable_true": row["stable_true"],
                        "classification_changed_320_640": (rho < 1) != row["stable_320"]})
        if time.monotonic() - start > plan["budget_seconds"]:
            raise RuntimeError("Budget exceeded; retain partial console output, do not change parameters.")
    result = {"posthoc_numerical_refinement": True, "records": records,
              "classification_changes": sum(r["classification_changed_320_640"] for r in records),
              "elapsed_seconds": time.monotonic() - start,
              "plan_sha256": hashlib.sha256((folder / "refinement_plan.json").read_bytes()).hexdigest()}
    (folder / "refinement_640.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
