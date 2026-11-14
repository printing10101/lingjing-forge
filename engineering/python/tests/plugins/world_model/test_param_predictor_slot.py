"""分层参数模型与槽铣标定的单测（合成数据）。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_param_predictor import (  # noqa: E402
    evaluate_within_condition,
    fit_final_model,
    predict,
)
from wm_slot_force_calibration import fit_kienzle, parse_slot_name, side_milling_exponents  # noqa: E402

LEVELS = (2.0, 1.8, 1.6, 1.4, 1.2)
GROUPS = {"G1": (800.0, 5000.0, 0.0), "G2": (800.0, 10000.0, 0.0),
          "G3": (1600.0, 5000.0, 0.0), "G4": (800.0, 5000.0, 1.0)}
OFFSETS = {"G1": 0.0, "G2": 0.15, "G3": -0.25, "G4": 0.35}


def _synthetic_samples(response: str = "force_rms") -> list[dict]:
    rows = []
    for group, (feed, speed, material) in GROUPS.items():
        for index in range(25):
            ap = LEVELS[index // 5]
            ae = LEVELS[index % 5]
            value = 20.0 * (ap ** 0.9) * (ae ** 0.8) * math.exp(OFFSETS[group])
            rows.append({"file": f"{group}_{index + 1:02d}.xlsx", "group": group, "index": index + 1,
                         "ap": ap, "ae": ae, "feed": feed, "speed": speed, "material": material,
                         "force_rms": value, "current_rms": value * 0.1, "noise_rms": value * 0.01,
                         "vibration_rms": 1.0})
    return rows


def test_fit_recovers_exponents_and_group_offsets():
    samples = _synthetic_samples()
    model = fit_final_model(samples, "force_rms")
    assert model["b_ap"] == pytest.approx(0.9, abs=0.08)
    assert model["b_ae"] == pytest.approx(0.8, abs=0.08)
    offsets = model["intercepts"]
    assert offsets["G1"] < offsets["G2"] < offsets["G4"]
    assert offsets["G3"] < offsets["G1"]
    # 单点预测与合成真值一致（同工况）
    predicted = predict(model, ap=2.0, ae=2.0, group="G2")
    assert predicted == pytest.approx(20.0 * 2.0 ** 0.9 * 2.0 ** 0.8 * math.exp(0.15), rel=0.05)


def test_predict_unknown_group_falls_back_and_validates():
    model = fit_final_model(_synthetic_samples(), "force_rms")
    fallback = predict(model, ap=1.5, ae=1.5)
    assert fallback > 0
    with pytest.raises(ValueError):
        predict(model, ap=0.0, ae=1.0)
    with pytest.raises(ValueError):
        predict(model, ap=1.0, ae=1.0, group="G9")


def test_within_condition_is_accurate_and_deterministic():
    samples = _synthetic_samples()
    first = evaluate_within_condition(samples, "force_rms")
    second = evaluate_within_condition(samples, "force_rms")
    assert first["r2_pooled"] == pytest.approx(second["r2_pooled"])
    assert first["folds"] == 100  # 4 工况 × 25 组合
    assert first["r2_pooled"] > 0.95


def test_parse_slot_names_and_bounds():
    first = parse_slot_name("M_7030_1.xlsx")
    assert first["material"] == "aluminium_7030"
    assert first["fz_mm_per_tooth"] == pytest.approx(0.020)
    assert first["feed_mm_per_min"] == pytest.approx(0.020 * 4 * 5000)
    last_steel = parse_slot_name("M_45_8.xlsx")
    assert last_steel["fz_mm_per_tooth"] == pytest.approx(0.055)
    assert parse_slot_name("G1_01.xlsx") is None
    with pytest.raises(ValueError):
        parse_slot_name("M_7030_8.xlsx")


def test_fit_kienzle_recovers_coefficients():
    fz = np.array([0.02, 0.03, 0.04, 0.05, 0.06])
    force = 700.0 * 1.0 * fz + 15.0 * 1.0
    fitted = fit_kienzle(fz, force, ap=1.0)
    assert fitted["kc_N_per_mm2"] == pytest.approx(700.0, rel=1e-6)
    assert fitted["ke_N_per_mm"] == pytest.approx(15.0, rel=1e-6)
    assert fitted["r2_vs_mean"] == pytest.approx(1.0, abs=1e-9)
    with pytest.raises(ValueError):
        fit_kienzle(np.array([-0.02, 0.03, 0.04]), force[:3], ap=1.0)


def test_side_milling_exponents_recover_known_values():
    samples = _synthetic_samples()
    exponents = side_milling_exponents(samples)
    assert exponents["b_ap"] == pytest.approx(0.9, abs=0.03)
    assert exponents["b_ae"] == pytest.approx(0.8, abs=0.03)
