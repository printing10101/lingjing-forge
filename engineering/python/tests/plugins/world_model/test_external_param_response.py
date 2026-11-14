"""JUST_CAS 参数响应检验脚本单测（不依赖真实数据）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_external_param_response import (  # noqa: E402
    _features,
    _log_features,
    _select_lambda,
    evaluate,
    parse_name,
)


def test_parse_name_maps_factorial_levels():
    first = parse_name("G1_01.xlsx")
    assert first["ap"] == pytest.approx(2.0) and first["ae"] == pytest.approx(2.0)
    assert first["feed"] == pytest.approx(800.0) and first["speed"] == pytest.approx(5000.0)
    assert first["material"] == pytest.approx(0.0)
    sixth = parse_name("G1_06.xlsx")
    assert sixth["ap"] == pytest.approx(1.8) and sixth["ae"] == pytest.approx(2.0)
    last = parse_name("G1_25.xlsx")
    assert last["ap"] == pytest.approx(1.2) and last["ae"] == pytest.approx(1.2)
    steel = parse_name("G4_13.xlsx")
    assert steel["material"] == pytest.approx(1.0) and steel["speed"] == pytest.approx(5000.0)
    fast_speed = parse_name("G2_01.xlsx")
    assert fast_speed["speed"] == pytest.approx(10000.0)
    fast_feed = parse_name("G3_01.xlsx")
    assert fast_feed["feed"] == pytest.approx(1600.0)
    assert parse_name("M_7030_1.xlsx") is None
    with pytest.raises(ValueError):
        parse_name("G1_26.xlsx")


def test_feature_sets_and_log_features():
    row = {"ap": 2.0, "ae": 1.8, "feed": 800.0, "speed": 5000.0, "material": 0.0}
    assert _features(row, "ap_ae") == [2.0, 1.8]
    assert _features(row, "full") == [2.0, 1.8, 800.0, 5000.0, 0.0]
    with pytest.raises(ValueError):
        _features(row, "unknown")
    logs = _log_features(row)
    assert logs[0] == pytest.approx(np.log(2.0)) and logs[-1] == pytest.approx(1.0)


def test_evaluate_detects_parameter_signal_and_is_deterministic():
    rows = []
    for group in ("G1", "G2", "G3", "G4"):
        for index, (ap, ae) in enumerate([(2.0, 2.0), (1.5, 1.0), (1.0, 1.5), (0.5, 0.5)], start=1):
            rows.append({"file": f"{group}_{index:02d}.xlsx", "group": group, "index": index,
                         "ap": ap, "ae": ae, "feed": 800.0, "speed": 5000.0, "material": 0.0,
                         "force_rms": 50.0 * ap + 50.0 * ae,
                         "vibration_rms": 1.0 + 0.1 * ap,
                         "noise_rms": 2.0, "current_rms": 3.0})
    first = evaluate(rows, "force_rms", "group")
    second = evaluate(rows, "force_rms", "group")
    assert first["summary"]["ap_ae"]["r2_mean"] == pytest.approx(second["summary"]["ap_ae"]["r2_mean"])
    assert first["summary"]["ap_ae"]["r2_mean"] > 0.99
    assert first["summary"]["full"]["r2_mean"] > 0.99
    assert first["summary"]["mean"]["r2_mean"] == pytest.approx(0.0)


def test_evaluate_combo_folds_count_and_determinism():
    rows = []
    for group in ("G1", "G2"):
        for index, (ap, ae) in enumerate([(2.0, 2.0), (1.5, 1.0), (1.0, 0.5)], start=1):
            rows.append({"file": f"{group}_{index:02d}.xlsx", "group": group, "index": index,
                         "ap": ap, "ae": ae, "feed": 800.0, "speed": 5000.0, "material": 0.0,
                         "force_rms": 10.0 * ap + 5.0 * ae, "vibration_rms": 1.0,
                         "noise_rms": 1.0, "current_rms": 1.0})
    result = evaluate(rows, "force_rms", "combo")
    assert result["folds"] == 3
    assert result["summary"]["ap_ae"]["r2_mean"] > 0.9


def test_select_lambda_deterministic():
    features = np.random.default_rng(2).normal(size=(30, 3))
    targets = features @ np.array([1.0, -2.0, 0.5])
    assert _select_lambda(features, targets) == _select_lambda(features, targets)
