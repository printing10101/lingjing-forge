"""E4 性能基线工具测试（纯逻辑，不跑重活）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_core_paths.py"


def _module():
    spec = importlib.util.spec_from_file_location("benchmark_core_paths_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_summarize_statistics():
    bench = _module()
    stats = bench.summarize([10.0, 20.0, 30.0, 40.0, 100.0])
    assert stats["n"] == 5
    assert stats["min"] == 10.0
    assert stats["p50"] == 30.0
    assert stats["p95"] == 100.0
    assert stats["mean"] == 40.0


def test_summarize_rejects_empty():
    bench = _module()
    with pytest.raises(ValueError):
        bench.summarize([])


def test_compare_marks_regression_levels():
    bench = _module()
    baseline = {"a": {"p50": 100.0}, "b": {"p50": 100.0}, "c": {"p50": 100.0}}
    current = {"a": {"p50": 150.0}, "b": {"p50": 250.0}, "c": {"p50": 350.0}}
    report = bench.compare_to_baseline(current, baseline)
    statuses = {row["stage"]: row["status"] for row in report["rows"]}
    assert statuses == {"a": "ok", "b": "warn", "c": "fail"}
    assert report["status"] == "fail"


def test_compare_reports_missing_baseline():
    bench = _module()
    report = bench.compare_to_baseline({"new_stage": {"p50": 10.0}}, {})
    assert report["rows"][0]["status"] == "no_baseline"
    assert report["status"] == "ok"


def test_compare_validates_thresholds():
    bench = _module()
    with pytest.raises(ValueError):
        bench.compare_to_baseline({}, {}, warn_ratio=1.0)
    with pytest.raises(ValueError):
        bench.compare_to_baseline({}, {}, warn_ratio=3.0, fail_ratio=2.0)
