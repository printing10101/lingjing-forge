"""Fixed-source frequency sensitivity; never select parameters by label score."""

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from .benchmark import confusion, write_json
from .coupled_milling import CoupledModes, Milling, coupled_radius
from .speed_dynamics import frequency_at_speed, load_frequency_table, rescale_modes

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]


def variant_ratios(variant, observed_frequency, reference_frequency):
    if variant["closure"] == "stationary":
        return 1.0, 1.0
    ratio = (observed_frequency + variant.get("frequency_offset_hz", 0)) / reference_frequency
    if variant["closure"] == "fixed_mass":
        return ratio, 1 / ratio**2
    if variant["closure"] == "specified_static_compliance":
        return ratio, float(variant["static_compliance_ratio"])
    raise ValueError("[配置错误] 未知动力学闭合方式。建议操作：只使用声明的对照假设。")


def run(output, config_path=ROOT / "speed_config.json"):
    output = Path(output)
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    reference = REPO / config["reference_csv"]
    if hashlib.sha256(reference.read_bytes()).hexdigest() != config["reference_sha256"]:
        raise ValueError("[来源漂移] 标签子集哈希不符。建议操作：核对当前审阅版本。")
    frequency_path = REPO / config["frequency_csv"]
    speeds, frequencies = load_frequency_table(frequency_path, config["frequency_sha256"])
    with reference.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    if not source_rows:
        raise ValueError("[数据错误] 标签子集为空。建议操作：检查审阅导出。")
    for row in source_rows:
        if row["result"] not in ("stable", "chatter"):
            raise ValueError("[数据错误] 含未判定标签。建议操作：将不确定点留在审计表。")
        frequency_at_speed(speeds, frequencies, float(row["n_rpm"]))
    names = [variant["name"] for variant in config["variants"]]
    if len(set(names)) != len(names) or names.count("stationary") != 1:
        raise ValueError("[配置错误] 需唯一对照名称与stationary基线。建议操作：核对配置。")
    for variant in config["variants"]:
        variant_ratios(variant, frequencies[0], config["reference_frequency_hz"])
    output.mkdir(parents=True, exist_ok=False)
    snapshots = output / "source"
    snapshots.mkdir()
    for filename in ("speed_dynamics.py", "speed_diagnostic.py", "coupled_milling.py",
                     "mechanics.py", "benchmark.py"):
        shutil.copy2(ROOT / filename, snapshots / filename)
    shutil.copy2(config_path, snapshots / "speed_config.json")
    shutil.copy2(reference, snapshots / "reviewed_points.csv")
    shutil.copy2(frequency_path, snapshots / "fig8_frequency.csv")
    write_json(output / "manifest.json", {
        "python": sys.version,
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots.iterdir()},
        "parameters_fitted": False, "reference_status": config["reference_status"],
        "static_compliance_and_operating_damping_measured": False,
        "population_accuracy_supported": False,
    })
    mass = np.array(config["mass_kg"])
    stiffness = np.array(config["stiffness_n_m"])
    damping = np.array(config["damping_n_s_m"]) / (2 * np.sqrt(mass * stiffness))
    baseline = CoupledModes(np.sqrt(stiffness / mass), damping, np.diag(1 / np.sqrt(mass)))
    tool = Milling(**config["milling"])
    records = []
    start = time.monotonic()
    for variant in config["variants"]:
        for source in source_rows:
            rpm = float(source["n_rpm"])
            observed = frequency_at_speed(speeds, frequencies, rpm)
            frequency_ratio, compliance_ratio = variant_ratios(
                variant, observed, config["reference_frequency_hz"])
            modes = rescale_modes(baseline, frequency_ratio, compliance_ratio)
            record = {
                "original_row": int(source["original_row"]), "variant": variant["name"],
                "ae_label": source["ae_label"], "rpm": rpm, "depth_mm": float(source["ap_mm"]),
                "stable_true": source["result"] == "stable", "observed_frequency_hz": observed,
                "frequency_ratio": frequency_ratio, "static_compliance_ratio": compliance_ratio,
                "effective_modal_mass_ratio": 1 / (compliance_ratio * frequency_ratio**2),
                "effective_modal_stiffness_ratio": 1 / compliance_ratio,
            }
            milling = replace(tool, immersion=float(source["ae_mm"]) / 1000 / tool.diameter_m)
            for steps in config["steps"]:
                radius = coupled_radius(modes, rpm, record["depth_mm"] / 1000, steps,
                                        milling, config["delay_rule"])
                record[f"rho_{steps}"] = radius
                record[f"stable_{steps}"] = radius < 1
            records.append(record)
            if time.monotonic() - start > config["budget_seconds"]:
                write_json(output / "incomplete.json", {"records": records, "reason": "budget"})
                raise RuntimeError("[预算停止] 对照计算超时。建议操作：保留现有记录并在新目录缩减预算。")
        print(f"completed {variant['name']} elapsed={time.monotonic()-start:.1f}s", flush=True)
    finest = f"stable_{config['steps'][-1]}"
    groups = {name: [r for r in records if r["variant"] == name] for name in names}
    original = {r["original_row"]: r for r in groups["stationary"]}
    errors = [i for i, r in original.items() if r[finest] != r["stable_true"]]
    summary = {
        "reference_status": config["reference_status"],
        "by_variant": {name: confusion(group, finest) for name, group in groups.items()},
        "baseline_error_rows": errors,
        "remaining_baseline_errors": {name: [r["original_row"] for r in group
            if r["original_row"] in errors and r[finest] != r["stable_true"]]
            for name, group in groups.items()},
        "all_error_rows": {name: [r["original_row"] for r in group if r[finest] != r["stable_true"]]
                           for name, group in groups.items()},
        "step_classification_changes": {name: sum(r[f"stable_{config['steps'][0]}"] != r[finest]
                                                  for r in group) for name, group in groups.items()},
        "elapsed_seconds": time.monotonic() - start,
        "parameters_fitted": False, "population_accuracy_supported": False,
        "conclusions_are_exploratory": True,
    }
    write_json(output / "results.json", {**summary, "records": records})
    with (output / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "speed_config.json")
    args = parser.parse_args()
    run(args.output, args.config)


if __name__ == "__main__":
    main()
