"""NASA 参数信息检验（外部动作探针）单测。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_audit_nasa_milling import REQUIRED_FIELDS, SIGNAL_FIELDS  # noqa: E402
from wm_external_action_probe import (  # noqa: E402
    build_task_vib,
    build_task_wear,
    evaluate_task,
    extract_runs,
)


def _write_mat(path: Path, specs: list[tuple]) -> None:
    """specs: (case, run, DOC, feed, material, VB, signal_value)。"""
    from scipy.io import savemat

    dtype = np.dtype([(name, "O") for name in REQUIRED_FIELDS])
    mill = np.empty((1, len(specs)), dtype=dtype)
    for index, (case, run, doc, feed, material, vb, value) in enumerate(specs):
        mill[0, index]["case"] = np.array([[float(case)]])
        mill[0, index]["run"] = np.array([[float(run)]])
        mill[0, index]["VB"] = np.array([[float(vb)]]) if vb is not None else np.empty((0, 0))
        mill[0, index]["time"] = np.array([[36.0]])
        mill[0, index]["DOC"] = np.array([[float(doc)]])
        mill[0, index]["feed"] = np.array([[float(feed)]])
        mill[0, index]["material"] = np.array([[float(material)]])
        for name in SIGNAL_FIELDS:
            mill[0, index][name] = np.full((1, 8), float(value))
    savemat(path, {"mill": mill})


def test_extract_runs_treats_missing_vb_as_none(tmp_path: Path):
    mat = tmp_path / "mill.mat"
    _write_mat(mat, [(1, 1, 1.5, 0.5, 1, 0.1, 2.0), (1, 2, 1.5, 0.5, 1, None, 2.0)])
    runs = extract_runs(mat)
    assert len(runs) == 2
    assert runs[0]["VB"] == pytest.approx(0.1)
    assert runs[1]["VB"] is None
    assert runs[0]["vib_rms"] == pytest.approx(2.0)


def test_tasks_build_consecutive_pairs_only(tmp_path: Path):
    mat = tmp_path / "mill.mat"
    _write_mat(mat, [(1, 1, 1.5, 0.5, 1, 0.1, 1.0), (1, 2, 1.5, 0.5, 1, 0.2, 2.0),
                     (1, 3, 1.5, 0.5, 1, None, 3.0), (2, 1, 0.75, 0.25, 2, 0.3, 4.0),
                     (2, 2, 0.75, 0.25, 2, 0.4, 5.0)])
    runs = extract_runs(mat)
    vib = build_task_vib(runs)
    wear = build_task_wear(runs)
    assert len(vib["rows"]) == 3  # 每个案例的首条走刀无前驱
    assert len(wear["rows"]) == 2  # (1:1→2) 与 (2:1→2)；(1:2→3) 因 VB 缺失排除
    assert wear["rows"][0]["target"] == pytest.approx(0.1)


def test_evaluate_task_detects_action_signal_and_is_deterministic():
    rows = []
    docs = (0.5, 0.75, 1.0, 1.25, 1.5)
    for index, doc in enumerate(docs):
        for case_offset in (0, 1):
            case = index * 2 + case_offset
            for run in range(1, 6):
                # 响应由 DOC 线性决定 + 案例内小扰动，状态特征无关
                rows.append({"case": case, "target": 2.0 * doc + 0.01 * run,
                             "actions": [doc, 0.5, 1.0], "state": [float(run)]})
    first = evaluate_task({"name": "toy", "rows": rows})
    second = evaluate_task({"name": "toy", "rows": rows})
    assert first["summary"]["actions"]["r2_mean"] == pytest.approx(second["summary"]["actions"]["r2_mean"])
    assert first["summary"]["actions"]["r2_mean"] > 0.5
    assert first["combos"] == len(docs)


def test_evaluate_task_clips_explosive_extrapolation():
    rows = []
    for case, doc in ((1, 1.5), (2, 1.5), (3, 0.75), (4, 0.75)):
        for run in range(1, 6):
            # 留出组合的状态特征远超训练范围，未裁剪会爆炸
            scale = 1.0 if doc == 1.5 else 1e6
            rows.append({"case": case, "target": 2.0 * doc,
                         "actions": [doc, 0.5, 1.0], "state": [scale * run]})
    result = evaluate_task({"name": "toy", "rows": rows})
    state = result["summary"]["state"]
    assert state["r2_mean"] is not None and state["r2_mean"] > -10.0
