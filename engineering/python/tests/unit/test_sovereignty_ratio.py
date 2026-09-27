"""P5-2 自主占比统计脚本测试（纯 stdlib，不依赖框架）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 直接加载脚本（避免依赖 scripts/ 包结构）
_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import sovereignty_ratio as sr  # noqa: E402


class TestLineClassification:
    def test_comment_and_blank(self) -> None:
        assert sr.is_comment_or_blank("") is True
        assert sr.is_comment_or_blank("   ") is True
        assert sr.is_comment_or_blank("# comment") is True
        assert sr.is_comment_or_blank('"""docstring"""') is True
        assert sr.is_comment_or_blank("'''doc'''") is True
        assert sr.is_comment_or_blank("x = 1") is False

    def test_framework_import(self) -> None:
        assert sr.is_framework_import("import torch") is True
        assert sr.is_framework_import("from cadquery import Workplane") is True
        assert sr.is_framework_import("import numpy as np") is True
        assert sr.is_framework_import("import os") is False  # stdlib
        assert sr.is_framework_import("x = 1") is False

    def test_framework_api_call(self) -> None:
        assert sr.is_framework_api_call("shape = cq.Workplane('XY')") is True
        assert sr.is_framework_api_call("t = torch.tensor([1,2])") is True
        assert sr.is_framework_api_call("arr = np.array([1])") is True
        assert sr.is_framework_api_call("@router.get('/x')") is True
        assert sr.is_framework_api_call("result = sum([1,2,3])") is False  # stdlib
        assert sr.is_framework_api_call("status = 'ok'") is False


class TestAnalyzeFile:
    def test_pure_stdlib_file(self, tmp_path: Path) -> None:
        f = tmp_path / "pure.py"
        f.write_text(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "result = add(1, 2)\n",
            encoding="utf-8",
        )
        info = sr.analyze_file(f)
        assert info["total"] == 3
        assert info["framework"] == 0
        assert info["ratio"] == 1.0

    def test_framework_file(self, tmp_path: Path) -> None:
        f = tmp_path / "with_fw.py"
        f.write_text(
            "import torch\n"
            "import numpy as np\n"
            "\n"
            "x = torch.tensor([1])\n"
            "y = np.array([2])\n"
            "z = x + y\n",
            encoding="utf-8",
        )
        info = sr.analyze_file(f)
        # import 行 + API 调用行计为框架
        assert info["framework"] >= 4
        assert info["sovereign"] == 1  # z = x + y
        assert 0.0 < info["ratio"] < 0.5

    def test_whitebox_module_sovereign(self, tmp_path: Path) -> None:
        f = tmp_path / "_feature_classifier.py"
        f.write_text(
            "import math\n"  # stdlib import（白盒仍算自主）
            "\n"
            "def classify(v):\n"
            "    return v > 0\n",
            encoding="utf-8",
        )
        info = sr.analyze_file(f)
        assert info["framework"] == 0
        assert info["ratio"] == 1.0

    def test_missing_file(self, tmp_path: Path) -> None:
        info = sr.analyze_file(tmp_path / "missing.py")
        assert info["total"] == 0


class TestAggregate:
    def test_empty(self) -> None:
        summary = sr.aggregate([])
        assert summary["files"] == 0
        assert summary["sovereignty_ratio"] == 0.0
        assert summary["target_met"] is False

    def test_mixed(self) -> None:
        results = [
            {"total": 10, "sovereign": 10, "framework": 0, "ratio": 1.0},
            {"total": 10, "sovereign": 2, "framework": 8, "ratio": 0.2},
        ]
        summary = sr.aggregate(results)
        assert summary["total_lines"] == 20
        assert summary["sovereign_lines"] == 12
        assert summary["sovereignty_ratio"] == 0.6
        assert summary["target_met"] is True

    def test_target_not_met(self) -> None:
        results = [{"total": 10, "sovereign": 1, "framework": 9, "ratio": 0.1}]
        summary = sr.aggregate(results)
        assert summary["sovereignty_ratio"] == 0.1
        assert summary["target_met"] is False


class TestWhiteboxMarkers:
    def test_markers_exist(self) -> None:
        # 白盒标记应包含已落地的白盒模块名
        for marker in ("_feature_classifier", "_review_state_machine", "_pipeline_stages"):
            assert marker in sr.WHITEBOX_MODULE_MARKERS
