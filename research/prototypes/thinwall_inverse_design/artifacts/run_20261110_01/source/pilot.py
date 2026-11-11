"""Bounded exploration with frozen configuration, raw results and provenance."""

import argparse
import csv
import hashlib
import itertools
import json
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy

if __package__:
    from .mechanics import (Cutting, Strip, averaged_boundary, extract_modes,
                           floquet_radius, history_growth, periodic_boundary)
else:
    from mechanics import (Cutting, Strip, averaged_boundary, extract_modes,
                           floquet_radius, history_growth, periodic_boundary)

ROOT = Path(__file__).resolve().parent


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profiles(config):
    p = config["profile"]
    values = np.arange(p["min_mm"], p["max_mm"] + p["grid_step_mm"]/2, p["grid_step_mm"])
    return [np.array(t)/1000 for t in itertools.product(values, repeat=3)
            if abs(sum(t)-3*p["mean_thickness_mm"]) < 1e-8]


def freeze_run(output):
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    for name in ("mechanics.py", "pilot.py", "pilot_config.json"):
        shutil.copy2(ROOT/name, source/name)
    write_json(output/"manifest.json", {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "numpy": np.__version__,
        "scipy": scipy.__version__, "matplotlib": matplotlib.__version__,
        "source_sha256": {p.name: digest(p) for p in source.iterdir()},
        "scope": "illustrative simulation only; not experimental or novel contribution evidence",
    })


def load_frozen(output):
    manifest = json.loads((output/"manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["source_sha256"].items():
        if digest(ROOT/name) != expected:
            raise ValueError(f"[版本漂移] {name} 已变更。建议操作：新建运行目录重新筛选。")
    return json.loads((output/"source"/"pilot_config.json").read_text(encoding="utf-8"))


def screen(output):
    freeze_run(output)
    config = load_frozen(output)
    settings = config["screening"]
    strip, cutting = Strip(**config["geometry"]), Cutting(**config["cutting"])
    rpms = np.linspace(settings["rpm_min"], settings["rpm_max"], settings["rpm_count"])
    designs = profiles(config)
    rows, scores = [], []
    start = time.monotonic()
    for index, thickness in enumerate(designs):
        boundaries, compliances = [], []
        for position in settings["contacts"]:
            modes, compliance, mass = extract_modes(thickness, position, settings["elements"],
                                                    settings["modes"], settings["damping_ratio"], strip)
            compliances.append(compliance)
            for rpm in rpms:
                boundary = averaged_boundary(modes, float(rpm), cutting, settings["frequency_resolution"])
                boundaries.append(boundary)
                rows.append({"design_id": index, "root_mm": thickness[0]*1000,
                             "middle_mm": thickness[1]*1000, "tip_mm": thickness[2]*1000,
                             "mass_kg": mass, "contact_fraction": position, "rpm": rpm,
                             "tip_or_local_compliance_m_n": compliance,
                             "averaged_boundary_m": boundary})
        scores.append({"design_id": index, "thickness_mm": (thickness*1000).tolist(),
                       "mass_kg": mass, "tip_compliance_m_n": compliances[-1],
                       "minimum_boundary_mm": min(boundaries)*1000,
                       "mean_boundary_mm": float(np.mean(boundaries))*1000})
        if (index+1) % 10 == 0 or index+1 == len(designs):
            print(f"screen {index+1}/{len(designs)} elapsed={time.monotonic()-start:.1f}s", flush=True)
    with (output/"screening.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    uniform = min(scores, key=lambda s: sum((t-2)**2 for t in s["thickness_mm"]))
    selected = {"uniform": uniform,
                "static_best_on_grid": min(scores, key=lambda s: s["tip_compliance_m_n"]),
                "worst_boundary_best_on_grid": max(scores, key=lambda s: s["minimum_boundary_mm"]),
                "mean_boundary_best_on_grid": max(scores, key=lambda s: s["mean_boundary_mm"])}
    write_json(output/"screening_summary.json", {
        "design_count": len(scores), "scenario_count": len(rows),
        "elapsed_seconds": time.monotonic()-start, "all_designs": scores,
        "selected": selected, "model": "constant-mean scalar regenerative DDE screening proxy",
        "not_independent_samples": True,
    })
    plot_screen(output, rows, selected)
    print(json.dumps(selected, ensure_ascii=False), flush=True)


def plot_screen(output, rows, selected):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), constrained_layout=True)
    for label, chosen in selected.items():
        curve = [r for r in rows if r["design_id"] == chosen["design_id"] and r["contact_fraction"] == 1]
        axes[0].plot([r["rpm"] for r in curve], [r["averaged_boundary_m"]*1000 for r in curve],
                     marker=".", label=label.replace("_on_grid", "").replace("_", " "))
        axes[1].step([0, 1/3, 2/3, 1], chosen["thickness_mm"] + chosen["thickness_mm"][-1:],
                     where="post", label=label.replace("_on_grid", "").replace("_", " "))
    axes[0].set(xlabel="Spindle speed (rpm)", ylabel="Averaged-model boundary (mm)",
                title="Tip contact: screening only")
    axes[1].set(xlabel="Height / length (root to tip)", ylabel="Thickness (mm)",
                title="Equal mass; 3-zone final-part profiles")
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.legend(fontsize=7)
    fig.suptitle("Exploratory thin-strip pilot - assumed parameters, no physical validation", fontsize=10)
    fig.savefig(output/"screening.png", dpi=170)
    plt.close(fig)


def verify(output):
    config = load_frozen(output)
    summary = json.loads((output/"screening_summary.json").read_text(encoding="utf-8"))
    strip, cutting = Strip(**config["geometry"]), Cutting(**config["cutting"])
    selected = summary["selected"]
    start = time.monotonic()
    numerical, periodic, sensitivity, time_domain = [], [], [], []
    # Frozen evaluation choices: not chosen after looking for a positive outcome.
    for label, chosen in selected.items():
        thickness = np.array(chosen["thickness_mm"])/1000
        for elements in config["verification"]["mesh_elements"]:
            modes, compliance, _ = extract_modes(thickness, 1, elements, 4, 0.01, strip)
            numerical.append({"design": label, "elements": elements, "modes": 4,
                              "tip_compliance_m_n": compliance,
                              "first_frequency_hz": modes.omega_rad_s[0]/(2*np.pi),
                              "averaged_6750_boundary_mm": averaged_boundary(modes, 6750, cutting)*1000})
        for count in config["verification"]["mode_counts"]:
            modes, _, _ = extract_modes(thickness, 1, 48, count, 0.01, strip)
            numerical.append({"design": label, "elements": 48, "modes": count,
                              "averaged_6750_boundary_mm": averaged_boundary(modes, 6750, cutting)*1000})
        for damping in config["verification"]["damping_ratios"]:
            for contact in (0.625, 0.875, 1.0):
                modes, _, _ = extract_modes(thickness, contact, 48, 4, damping, strip)
                for rpm in config["verification"]["off_grid_rpms"]:
                    boundary = averaged_boundary(modes, rpm, cutting, resolution=1200)
                    sensitivity.append({"design": label, "damping": damping, "contact": contact,
                                        "rpm": rpm, "averaged_boundary_mm": boundary*1000})
        # Full periodic model is a numerical/model-change check, not a second physical dataset.
        modes, _, _ = extract_modes(thickness, 1, 48, 4, 0.01, strip)
        for rpm in config["verification"]["off_grid_rpms"]:
            for steps in config["verification"]["floquet_steps"]:
                boundary = periodic_boundary(modes, rpm, steps, cutting)
                periodic.append({"design": label, "rpm": rpm, "steps": steps,
                                 "boundary_mm": None if boundary is None else boundary*1000})
                print(f"periodic {label} rpm={rpm} steps={steps} boundary_mm={periodic[-1]['boundary_mm']}", flush=True)
        # Time-domain cross-check: same first-mode-only DDE, not the full beam model.
        if label in ("uniform", "static_best_on_grid"):
            single, _, _ = extract_modes(thickness, 1, 48, 1, 0.01, strip)
            boundary = periodic_boundary(single, 6750, 160, cutting)
            if boundary is not None:
                for ratio in (0.75, 1.25):
                    depth = boundary*ratio
                    growth, amplitudes = history_growth(single, 6750, depth, 200, 100, cutting)
                    radius = floquet_radius(single, 6750, depth, 160, cutting)
                    time_domain.append({"design": label, "modes": 1, "rpm": 6750,
                                        "boundary_ratio": ratio, "depth_mm": depth*1000,
                                        "floquet_radius": radius, "rk4_log_growth_per_tooth": growth,
                                        "consistent_growth_sign": bool((radius > 1) == (growth > 0)),
                                        "amplitude_by_tooth_m": amplitudes.tolist()})
        write_json(output/"verification_progress.json", {"completed_design": label,
                   "elapsed_seconds": time.monotonic()-start, "periodic": periodic})
    write_json(output/"verification.json", {"elapsed_seconds": time.monotonic()-start,
               "numerical_convergence": numerical, "off_grid_sensitivity": sensitivity,
               "periodic_boundaries": periodic, "time_domain_checks": time_domain,
               "physical_external_validation": "NOT PERFORMED",
               "new_contribution_established": False})
    print(f"verification complete elapsed={time.monotonic()-start:.1f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["screen", "verify"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    {"screen": screen, "verify": verify}[args.stage](args.output.resolve())


if __name__ == "__main__":
    main()
