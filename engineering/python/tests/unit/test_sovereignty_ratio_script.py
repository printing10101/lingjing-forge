"""scripts/sovereignty_ratio.py 回归测试（P5-2：默认模式 KeyError 修复）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "sovereignty_ratio.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sovereignty_ratio_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_aggregate_empty_and_target_semantics():
    module = _load_module()
    empty = module.aggregate([])
    assert empty["files"] == 0
    assert empty["sovereignty_ratio"] == 0.0
    assert empty["target_met"] is False

    results = [{"path": "a", "total": 100, "sovereign": 80, "framework": 20, "ratio": 0.8}]
    assert module.aggregate(results, target=0.75)["target_met"] is True
    assert module.aggregate(results, target=0.85)["target_met"] is False
    assert module.aggregate(results, target=0.75)["target"] == 0.75


def test_main_default_mode_does_not_raise_keyerror(monkeypatch, capsys):
    """P5-2 复现：main() 曾访问 summary['ratio']（实际键为 sovereignty_ratio）必崩."""
    module = _load_module()
    monkeypatch.setattr(
        module,
        "analyze_tree",
        lambda: [{"path": "app/x.py", "total": 100, "sovereign": 60, "framework": 40, "ratio": 0.6}],
    )
    monkeypatch.setattr(sys, "argv", ["sovereignty_ratio.py"])

    assert module.main() == 0
    output = capsys.readouterr().out
    assert "自主占比: 60.00%" in output
    assert "目标达成: ✅" in output


def test_main_returns_nonzero_when_target_missed(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "analyze_tree",
        lambda: [{"path": "app/x.py", "total": 100, "sovereign": 40, "framework": 60, "ratio": 0.4}],
    )
    monkeypatch.setattr(sys, "argv", ["sovereignty_ratio.py", "--target", "0.9"])
    assert module.main() == 1
