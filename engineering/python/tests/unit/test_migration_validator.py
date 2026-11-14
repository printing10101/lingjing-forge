"""B3 工艺迁移验证器测试。"""

from __future__ import annotations

import pytest

from app.postprocessor.migration_validator import (
    DEFAULT_TOL_MM,
    compare_programs,
    render_checklist,
)

pytestmark = pytest.mark.unit

PROGRAM = """%
O1000
G21 G90 G17
G00 X10. Y10. Z80.
M03 S1800
G01 Z5. F200
G01 X40. F200
G00 Z80.
G00 X60. Y20.
G83 X60. Y20. Z-3. R5. Q3. F120
G80
G00 Z80.
M30
%
"""


def test_identical_programs_pass():
    report = compare_programs(PROGRAM, PROGRAM)
    assert report["passed"] is True
    assert report["tol_mm"] == DEFAULT_TOL_MM
    assert report["summary"]["failed"] == []


def test_feed_change_is_functional_failure():
    changed = PROGRAM.replace("F200", "F250")
    report = compare_programs(PROGRAM, changed)
    assert report["passed"] is False
    assert "feed_rates" in report["summary"]["failed"]


def test_endpoint_shift_is_functional_failure():
    changed = PROGRAM.replace("G01 X40. F200", "G01 X42. F200")
    report = compare_programs(PROGRAM, changed)
    assert report["passed"] is False
    assert "cutting_endpoint_deviation" in report["summary"]["failed"]


def test_missing_drill_cycle_fails():
    changed = PROGRAM.replace("G83 X60. Y20. Z-3. R5. Q3. F120\n", "")
    report = compare_programs(PROGRAM, changed)
    assert report["passed"] is False
    assert "drill_cycles" in report["summary"]["failed"]


def test_dialect_difference_only_warns():
    changed = PROGRAM.replace("M30", "G30\nM30")
    report = compare_programs(PROGRAM, changed)
    assert report["passed"] is True
    assert "dialect_codes" in report["summary"]["warned"]


def test_rapid_topology_difference_is_warning_not_failure():
    changed = PROGRAM.replace("G00 X10. Y10. Z80.", "G00 X0. Y0. Z80.\nG00 X10. Y10. Z80.")
    report = compare_programs(PROGRAM, changed)
    assert report["passed"] is True
    assert "total_segments" in report["summary"]["warned"]


def test_render_checklist_contains_verdict_and_caveat():
    report = compare_programs(PROGRAM, PROGRAM)
    markdown = render_checklist(report, source="fanuc_0i", target="gsk_980_25i")
    assert "# 工艺迁移验证清单：fanuc_0i → gsk_980_25i" in markdown
    assert "结论：PASS" in markdown
    assert "不替代空运行与首件试切" in markdown


def test_invalid_tolerance_rejected():
    with pytest.raises(ValueError):
        compare_programs(PROGRAM, PROGRAM, tol_mm=0)
