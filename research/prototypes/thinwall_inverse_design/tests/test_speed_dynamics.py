import csv
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from prototypes.thinwall_inverse_design import speed_diagnostic
from prototypes.thinwall_inverse_design.coupled_milling import CoupledModes
from prototypes.thinwall_inverse_design.speed_dynamics import (
    frequency_at_speed, load_frequency_table, rescale_modes, static_compliance,
)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_modal_rescaling_matches_physical_mass_stiffness_and_damping():
    mass = np.array([0.02, 0.03])
    stiffness = np.array([410000, 390000])
    damping = np.array([1.5, 1.8])
    baseline = CoupledModes(np.sqrt(stiffness / mass),
        damping / (2 * np.sqrt(mass * stiffness)), np.diag(1 / np.sqrt(mass)))
    r, alpha = 0.947, 0.75
    modes = rescale_modes(baseline, r, alpha)
    assert np.allclose(static_compliance(modes), np.diag(alpha / stiffness))
    s = 110 + 4300j
    actual = modes.participation @ np.diag(1 / (
        s**2 + 2 * modes.damping * modes.omega * s + modes.omega**2)) @ modes.participation.T
    expected = np.diag(1 / ((mass / (alpha * r**2)) * s**2
                           + (damping / (alpha * r)) * s + stiffness / alpha))
    assert np.allclose(actual, expected, rtol=1e-12, atol=0)
    fixed_mass = rescale_modes(baseline, r, 1 / r**2)
    assert np.allclose(fixed_mass.participation, baseline.participation)
    assert np.allclose(static_compliance(fixed_mass), static_compliance(baseline) / r**2)
    assert np.allclose(rescale_modes(baseline, 1, 1).participation, baseline.participation)


@pytest.mark.parametrize("r,alpha", [(0, 1), (1, 0), (-1, 1), (np.nan, 1), (1, np.inf)])
def test_invalid_modal_closure_rejected(r, alpha):
    modes = CoupledModes(np.array([100.]), np.array([0.01]), np.array([[1.], [1.]]))
    with pytest.raises(ValueError, match="模态变换错误"):
        rescale_modes(modes, r, alpha)


def test_frequency_table_and_no_extrapolation(tmp_path):
    path = tmp_path / "frequency.csv"
    path.write_text("rpm,frequency_hz\n0,722\n23000,684\n34000,687\n", encoding="utf-8")
    speeds, frequencies = load_frequency_table(path, file_hash(path))
    assert frequency_at_speed(speeds, frequencies, 11500) == 703
    assert frequency_at_speed(speeds, frequencies, 34000) == 687
    for bad in [-1, 34001, np.nan]:
        with pytest.raises(ValueError, match="范围错误"):
            frequency_at_speed(speeds, frequencies, bad)
    with pytest.raises(ValueError, match="来源漂移"):
        load_frequency_table(path, "wrong")
    for text in ["0,700\n0,690", "0,nan\n1,690", "0,0\n1,690", "-1,700\n1,690", "0,700"]:
        path.write_text("rpm,frequency_hz\n" + text + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="频率表错误"):
            load_frequency_table(path, file_hash(path))


def tiny_config(tmp_path):
    points = tmp_path / "reference.csv"
    points.write_text("original_row,ae_label,n_rpm,ap_mm,ae_mm,result\n"
                      "4,D,12000,0.1,8,stable\n8,0.1D,22000,0.2,0.8,chatter\n", encoding="utf-8")
    frequency = tmp_path / "frequency.csv"
    frequency.write_text("rpm,frequency_hz\n0,722\n34000,684\n", encoding="utf-8")
    config = {"reference_csv": str(points), "reference_sha256": file_hash(points),
        "frequency_csv": str(frequency), "frequency_sha256": file_hash(frequency),
        "reference_frequency_hz": 722, "reference_status": "test_fixture",
        "mass_kg": [0.0201, 0.0199], "stiffness_n_m": [414000, 409000],
        "damping_n_s_m": [1.56, 1.60], "milling": {}, "delay_rule": "midpoint",
        "steps": [8, 16], "budget_seconds": 60,
        "variants": [{"name": "stationary", "closure": "stationary"},
                     {"name": "fixed_mass", "closure": "fixed_mass"},
                     {"name": "fixed_compliance", "closure": "specified_static_compliance",
                      "static_compliance_ratio": 1.0}]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, config


def test_diagnostic_actual_solver_end_to_end_and_cli(tmp_path, monkeypatch):
    path, config = tiny_config(tmp_path)
    output = tmp_path / "run"
    summary = speed_diagnostic.run(output, path)
    assert summary["population_accuracy_supported"] is False
    assert summary["parameters_fitted"] is False
    assert set(summary["by_variant"]) == {"stationary", "fixed_mass", "fixed_compliance"}
    result = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert len(result["records"]) == 6
    assert all(v["n"] == 2 for v in summary["by_variant"].values())
    assert len(list(csv.DictReader((output / "predictions.csv").open(encoding="utf-8")))) == 6
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"]["fig8_frequency.csv"] == config["frequency_sha256"]
    with pytest.raises(FileExistsError):
        speed_diagnostic.run(output, path)
    monkeypatch.setattr("sys.argv", ["speed", "--config", str(path), "--output", str(tmp_path / "cli")])
    speed_diagnostic.main()
    assert (tmp_path / "cli/results.json").exists()


@pytest.mark.parametrize("change,match", [
    ("reference_hash", "来源漂移"), ("empty", "数据错误"), ("label", "数据错误"),
    ("duplicate", "配置错误"), ("missing_baseline", "配置错误"), ("closure", "配置错误"),
])
def test_diagnostic_bad_inputs_rejected_before_output(tmp_path, change, match):
    path, config = tiny_config(tmp_path)
    points = tmp_path / "reference.csv"
    if change == "reference_hash":
        config["reference_sha256"] = "wrong"
    if change == "empty":
        points.write_text("original_row,ae_label,n_rpm,ap_mm,ae_mm,result\n", encoding="utf-8")
    if change == "label":
        points.write_text(points.read_text(encoding="utf-8").replace("stable", "ambiguous"), encoding="utf-8")
    if change in ("empty", "label"):
        config["reference_sha256"] = file_hash(points)
    if change == "duplicate":
        config["variants"].append(config["variants"][0])
    if change == "missing_baseline":
        config["variants"] = config["variants"][1:]
    if change == "closure":
        config["variants"][1]["closure"] = "fitted_unknown"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        speed_diagnostic.run(tmp_path / "invalid", path)
    assert not (tmp_path / "invalid").exists()


def test_diagnostic_budget_retains_partial_records(tmp_path, monkeypatch):
    path, config = tiny_config(tmp_path)
    times = iter([0, 61])
    monkeypatch.setattr(speed_diagnostic, "time", SimpleNamespace(monotonic=lambda: next(times)))
    with pytest.raises(RuntimeError, match="预算停止"):
        speed_diagnostic.run(tmp_path / "budget", path)
    partial = json.loads((tmp_path / "budget/incomplete.json").read_text(encoding="utf-8"))
    assert len(partial["records"]) == 1


def test_frequency_offset_is_declared_intervention():
    variant = {"closure": "specified_static_compliance", "static_compliance_ratio": 0.75,
               "frequency_offset_hz": -0.5}
    assert speed_diagnostic.variant_ratios(variant, 684, 722) == (683.5 / 722, 0.75)
