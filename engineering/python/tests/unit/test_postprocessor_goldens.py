"""C5：国产后处理黄金程序逐字节回归 + XM-100 行程硬闸。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import freeze_postprocessor_goldens as goldens  # noqa: E402

pytestmark = pytest.mark.unit


def test_normalize_only_touches_date():
    program = "O1000 (PROGRAM 1000 - 2026-11-14)\nG01 X1.0 F100\n(POST: Fanuc 0i-MF)\n"
    normalized = goldens.normalize(program)
    assert normalized == "O1000 (PROGRAM 1000 - <DATE>)\nG01 X1.0 F100\n(POST: Fanuc 0i-MF)\n"


def test_domestic_goldens_byte_identical():
    """四家国产控制器：重新生成必须与冻结文件逐字节一致（日期归一化后）。

    先重置后处理器注册表单例：方言声明化测试会往单例里注册 YAML 方言，
    残留状态会让同一控制器的输出与冻结口径不一致（测试间污染）。
    """
    from app.postprocessor.registry import PostProcessorRegistry

    PostProcessorRegistry.reset()
    ok, report = goldens.verify(goldens.DEFAULT_DXF, goldens.DEFAULT_OUT)
    assert ok, report
    assert set(report["programs"]) == {
        "gsk_980_25i", "hnc_848_22", "knd_1000_2000_3000", "xmachine_xm100"}
    for controller, info in report["programs"].items():
        assert info["status"] == "identical", (controller, info)


def test_xmachine_program_stays_within_travel():
    """XM-100 行程 ±50mm：黄金程序所有 X/Y/Z 坐标必须在包络内。"""
    import re

    program = (goldens.DEFAULT_OUT / "xmachine_xm100.nc").read_text(encoding="utf-8")
    for axis in ("X", "Y", "Z"):
        values = [float(value) for value in re.findall(rf"{axis}(-?\d+(?:\.\d+)?)", program)]
        assert values, f"{axis} 轴无坐标"
        assert max(abs(value) for value in values) <= 50.0 + 1e-6, f"{axis} 超程: {max(values)}"
