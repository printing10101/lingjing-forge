"""NASA Milling 结构审计脚本单测（合成 mat 夹具）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_audit_nasa_milling import REQUIRED_FIELDS, SIGNAL_FIELDS, audit  # noqa: E402


def _write_synthetic_mat(path: Path) -> None:
    from scipy.io import savemat

    dtype = np.dtype([(name, "O") for name in REQUIRED_FIELDS])
    mill = np.empty((1, 4), dtype=dtype)
    specs = [  # case, run, DOC, feed, material, VB（None 表示未测）
        (1, 1, 1.5, 0.5, 1, 0.10),
        (1, 2, 1.5, 0.5, 1, None),
        (2, 1, 0.75, 0.25, 2, 0.20),
        (2, 2, 0.75, 0.25, 2, 0.35),
    ]
    for index, (case, run, doc, feed, material, vb) in enumerate(specs):
        mill[0, index]["case"] = np.array([[float(case)]])
        mill[0, index]["run"] = np.array([[float(run)]])
        mill[0, index]["VB"] = np.array([[float(vb)]]) if vb is not None else np.empty((0, 0))
        mill[0, index]["time"] = np.array([[float(36 + index)]])
        mill[0, index]["DOC"] = np.array([[float(doc)]])
        mill[0, index]["feed"] = np.array([[float(feed)]])
        mill[0, index]["material"] = np.array([[float(material)]])
        for name in SIGNAL_FIELDS:
            mill[0, index][name] = (np.arange(6, dtype=float) + index).reshape(1, 6)
    savemat(path, {"mill": mill})


def test_audit_reports_cases_combos_and_vb_coverage(tmp_path: Path):
    mat = tmp_path / "mill.mat"
    _write_synthetic_mat(mat)
    report = audit(mat)
    assert report["structure"]["cases"] == 2
    assert report["structure"]["runs"] == 4
    assert report["structure"]["signal_lengths_unique"] == [6]
    assert report["labels"]["vb_measured_runs"] == 3
    assert report["labels"]["vb_missing_runs"] == 1
    assert report["labels"]["vb_min"] == pytest.approx(0.10)
    assert report["labels"]["vb_max"] == pytest.approx(0.35)
    assert report["actions"]["spindle_speed_field_present"] is False
    assert len(report["actions"]["cases_per_combo"]) == 2
    assert report["verdict"]["adapter_feasible"] is True


def test_audit_rejects_missing_field(tmp_path: Path):
    from scipy.io import savemat

    dtype = np.dtype([(name, "O") for name in REQUIRED_FIELDS if name != "VB"])
    mill = np.empty((1, 1), dtype=dtype)
    for name in dtype.names:
        mill[0, 0][name] = np.array([[1.0]])
    path = tmp_path / "broken.mat"
    savemat(path, {"mill": mill})
    with pytest.raises(ValueError):
        audit(path)


def test_audit_flags_non_finite_signal(tmp_path: Path):
    mat = tmp_path / "nan.mat"
    _write_synthetic_mat(mat)
    from scipy.io import loadmat, savemat

    loaded = loadmat(mat, struct_as_record=True)
    loaded["mill"][0, 0]["smcAC"][0, 0] = np.nan
    savemat(mat, {"mill": loaded["mill"]})
    report = audit(mat)
    assert report["structure"]["all_signals_finite"] is False
    assert report["verdict"]["adapter_feasible"] is False
