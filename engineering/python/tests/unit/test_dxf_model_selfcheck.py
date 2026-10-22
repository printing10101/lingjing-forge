"""dxf_to_model 几何自检（体积自洽）测试。

覆盖两类「静默失败」：
1. 孔位偏移到实体外 → CadQuery 的 cut 不报错、体积不变，但 hole_count 仍为真；
2. 正常件必须通过自检（避免闸门误报）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

cq = pytest.importorskip("cadquery")

from app.dxf.dxf_parser import DxfParser  # noqa: E402
from app.dxf.feature_extractor import FeatureExtractor  # noqa: E402
from app.dxf.dxf_to_model import DxfToModelConverter  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE = _REPO_ROOT / "data" / "test_fixtures" / "case2_box_4holes.dxf"

pytestmark = pytest.mark.unit


def _extract():
    if not _FIXTURE.exists():
        pytest.skip(f"fixture 缺失: {_FIXTURE}")
    parser = DxfParser()
    return FeatureExtractor().extract(parser.parse(_FIXTURE))


class TestGeometrySelfCheck:
    def test_correct_model_passes_and_volumes_match(self):
        """4 孔板：移除体积必须等于 4×π·5²·10。"""
        fr = _extract()
        assert len(fr.holes) == 4

        mr = DxfToModelConverter().convert(fr)

        assert mr.success is True
        assert mr.geometry_selfcheck_passed is True
        assert mr.base_volume_mm3 == pytest.approx(100000.0, rel=1e-6)
        assert mr.removed_volume_mm3 == pytest.approx(mr.expected_hole_volume_mm3, rel=0.02)
        assert mr.removed_volume_mm3 == pytest.approx(4 * 3.141592653589793 * 25 * 10, rel=0.02)

    def test_holes_displaced_outside_part_fail_selfcheck(self):
        """把孔平移到实体外，模拟修复前的坐标系偏移 bug。

        cut 会静默无效（CadQuery 不报错），因此必须由体积自检拦下。
        """
        fr = _extract()
        for hole in fr.holes:
            hole.center_x += 500.0
            hole.center_y += 500.0

        mr = DxfToModelConverter().convert(fr)

        # 转换本身仍"成功"——这正是静默失败的危险之处
        assert mr.success is True
        assert mr.hole_count == 4
        # 但自检必须失败
        assert mr.geometry_selfcheck_passed is False
        assert mr.removed_volume_mm3 == pytest.approx(0.0, abs=1.0)
        assert any("体积自检失败" in w for w in mr.warnings)

    def test_no_hole_part_passes_with_zero_removal(self):
        """无孔件：移除量应接近 0 且自检通过。"""
        fixture = _REPO_ROOT / "data" / "test_fixtures" / "case10_i_shape.dxf"
        if not fixture.exists():
            pytest.skip(f"fixture 缺失: {fixture}")

        fr = FeatureExtractor().extract(DxfParser().parse(fixture))
        mr = DxfToModelConverter().convert(fr)

        assert mr.geometry_selfcheck_passed is True
        assert mr.removed_volume_mm3 == pytest.approx(0.0, abs=1.0)
        assert mr.final_volume_mm3 == pytest.approx(mr.base_volume_mm3, rel=1e-6)
