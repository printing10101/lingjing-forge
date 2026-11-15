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
    """三家国产控制器：重新生成必须与冻结文件逐字节一致（日期归一化后）。"""
    ok, report = goldens.verify(goldens.DEFAULT_DXF, goldens.DEFAULT_OUT)
    assert ok, report
    assert set(report["programs"]) == {"gsk_980_25i", "hnc_848_22", "knd_1000_2000_3000"}
    for controller, info in report["programs"].items():
        assert info["status"] == "identical", (controller, info)


def test_xmachine_rejects_out_of_travel():
    """XM-100 行程 ±50mm：100×100 件必须被硬闸拒绝，而不是静默生成。"""
    from app.process_planning.gcode_generator import GCodeGenerator

    plan, height, _origin = goldens.build_plan(goldens.DEFAULT_DXF)
    with pytest.raises(ValueError, match="XM-100"):
        GCodeGenerator().generate(plan, controller_type="xmachine_xm100",
                                  material_name="45#钢", safe_z=goldens.SAFE_Z, stock_top_z=height)
