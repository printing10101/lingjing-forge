"""Consolidate adaptive numerical checks without overwriting frozen results."""

import copy
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np

from prototypes.thinwall_inverse_design.benchmark import confusion


def main():
    folder = Path(__file__).resolve().parent
    root = folder.parents[1]
    run = root / "artifacts/speed_diagnostic_20261111_01"
    result = json.loads((run / "results.json").read_text(encoding="utf-8"))
    records = copy.deepcopy(result["records"])
    by_key = {(r["original_row"], r["variant"]): r for r in records}
    for row in records:
        row.update(rho_final=row["rho_320"], stable_final=row["stable_320"],
                   final_steps=320, previous_radius=row["rho_160"])
    for steps, previous_steps in [(640, 320), (1280, 640)]:
        path = run / f"refinement_{steps}.json"
        if path.exists():
            for refined in json.loads(path.read_text(encoding="utf-8"))["records"]:
                by_key[(refined["original_row"], refined["variant"])].update(
                    rho_final=refined[f"rho_{steps}"], stable_final=refined[f"stable_{steps}"],
                    final_steps=steps, previous_radius=refined[f"rho_{previous_steps}"])
    groups = {name: [r for r in records if r["variant"] == name] for name in result["by_variant"]}
    original = {r["original_row"]: r for r in groups["stationary"]}
    baseline_errors = [i for i, r in original.items() if r["stable_final"] != r["stable_true"]]
    fixed = {r["original_row"]: r for r in groups["observed_frequency_fixed_static_compliance"]}
    stiffer = {r["original_row"]: r for r in groups["observed_frequency_static_compliance_0p75"]}
    uncertainty = [{"original_row": r["original_row"], "variant": r["variant"],
                    "final_steps": r["final_steps"], "rho_final": r["rho_final"],
                    "previous_radius": r["previous_radius"]} for r in records
                   if abs(r["rho_final"] - 1) <= 3 * abs(r["rho_final"] - r["previous_radius"])]
    analysis = {
        "by_variant_finest_available": {name: confusion(rows, "stable_final") for name, rows in groups.items()},
        "baseline_error_rows": baseline_errors,
        "all_error_rows_finest": {name: [r["original_row"] for r in rows
            if r["stable_final"] != r["stable_true"]] for name, rows in groups.items()},
        "same_frequency_compliance_changes_finest": [i for i in fixed
            if fixed[i]["stable_final"] != stiffer[i]["stable_final"]],
        "threshold_cases_not_resolved_by_heuristic": uncertainty,
        "heuristic_is_rigorous_error_bound": False,
        "population_accuracy_supported": False,
        "parameters_fitted": False,
    }
    (folder / "consolidated_analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    with (folder / "consolidated_predictions.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    with (folder / "fig8_frequency.csv").open(encoding="utf-8") as stream:
        table = list(csv.DictReader(stream))
    rpm = np.array([float(r["rpm"]) for r in table])
    hz = np.array([float(r["frequency_hz"]) for r in table])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1))
    axes[0].plot(rpm / 1000, hz, "o-", ms=3, label="Fig8 digitized frequency")
    axes[0].axhline(hz[0], ls="--", color="gray", label="Stationary reference")
    axes[0].set(xlabel="Spindle speed (krpm)", ylabel="Mode frequency (Hz)")
    axes[0].legend(fontsize=8)
    axes[1].plot(rpm / 1000, (hz[0] / hz)**2, label="Fixed mass: implied flexibility")
    axes[1].axhline(1, ls="--", label="Fixed static flexibility")
    axes[1].axhline(.75, ls=":", color="tab:green", label="Illustrative flexibility ratio 0.75")
    axes[1].set(xlabel="Spindle speed (krpm)", ylabel="Static modal flexibility / stationary")
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle("Observed frequency; unmeasured residue assumptions kept separate", fontsize=11)
    fig.tight_layout()
    fig.savefig(folder / "frequency_and_modal_closures.png", dpi=180)
    plt.close(fig)
    columns = ["stationary", "observed_frequency_fixed_mass",
               "observed_frequency_fixed_static_compliance", "observed_frequency_static_compliance_0p75"]
    ids = sorted(original)
    matrix = np.array([[original[i]["stable_true"], *[
        by_key[(i, name)]["stable_final"] for name in columns]] for i in ids], dtype=int)
    fig, ax = plt.subplots(figsize=(9, 8.3))
    ax.imshow(matrix, cmap=ListedColormap(["#dc7666", "#78ba9a"]), vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(5), ["Provisional\nsource label", "Stationary", "Observed f\nfixed mass",
                           "Observed f\nfixed flexibility", "Observed f\nflexibility 0.75"])
    ax.set_yticks(range(len(ids)), [str(i + 2) for i in ids])
    ax.set_ylabel("Original CSV line (including header)")
    uncertain_keys = {(r["original_row"], r["variant"]) for r in uncertainty}
    for y in range(len(ids)):
        for x in range(5):
            label = "S" if matrix[y, x] else "C"
            if x and (ids[y], columns[x - 1]) in uncertain_keys:
                label += "?"
            ax.text(x, y, label, ha="center", va="center", fontsize=8)
            if x and matrix[y, x] != matrix[y, 0]:
                ax.plot(x, y, marker="s", markerfacecolor="none", markeredgecolor="black", ms=17, mew=1)
    ax.set_title("All 25 provisional points; S=stable, C=chatter; outlined cells disagree\n"
                 "Finest available per point; ?=refinement heuristic unresolved\n"
                 "No fitting or population accuracy claim", fontsize=10)
    fig.tight_layout()
    fig.savefig(folder / "all25_classification_comparison.png", dpi=180)
    plt.close(fig)
    print(json.dumps(analysis))


if __name__ == "__main__":
    main()
