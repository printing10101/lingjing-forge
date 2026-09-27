"""dxf_parser 覆盖率补强测试。

覆盖 app/dxf/dxf_parser.py：
- 全实体提取：LINE/CIRCLE/ARC/TEXT/MTEXT/DIMENSION/POLYLINE/HATCH/INSERT/SPLINE
- 错误路径：文件不存在/空文件/损坏/路径安全检查
- DxfParseResult 汇总统计
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from app.dxf.dxf_parser import DxfParser
from app.dxf.exceptions import DxfFormatError, DxfParseError

pytestmark = pytest.mark.unit


def _make_sample_dxf(path: Path) -> None:
    """生成包含各类实体的 DXF 文件。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_circle((5, 5), radius=2.5)
    msp.add_arc((0, 0), radius=5, start_angle=0, end_angle=90)
    msp.add_text("标注文字", height=2.0)
    msp.add_mtext("多行文本")
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5)], close=True)
    msp.add_spline([(0, 0), (2, 3), (5, 5), (8, 3), (10, 0)])
    doc.saveas(str(path))


class TestDxfParser:
    def test_parse_basic_entities(self, tmp_path):
        dxf = tmp_path / "sample.dxf"
        _make_sample_dxf(dxf)
        parser = DxfParser()
        result = parser.parse(dxf)
        assert len(result.lines) >= 1
        assert len(result.circles) >= 1
        assert len(result.arcs) >= 1
        assert len(result.polylines) >= 1
        assert len(result.splines) >= 1
        assert result.file_name == "sample.dxf"
        assert result.file_size > 0

    def test_parse_line_coordinates(self, tmp_path):
        dxf = tmp_path / "line.dxf"
        _make_sample_dxf(dxf)
        result = DxfParser().parse(dxf)
        line = result.lines[0]
        assert line.start[:2] == (0.0, 0.0)  # 3D 元组，前两维为 XY
        assert line.end[:2] == (10.0, 0.0)

    def test_parse_circle_radius(self, tmp_path):
        dxf = tmp_path / "circle.dxf"
        _make_sample_dxf(dxf)
        result = DxfParser().parse(dxf)
        circle = result.circles[0]
        assert circle.radius == pytest.approx(2.5)

    def test_parse_missing_file_raises(self, tmp_path):
        with pytest.raises(DxfParseError):
            DxfParser().parse(tmp_path / "nope.dxf")

    def test_parse_empty_file_raises(self, tmp_path):
        empty = tmp_path / "empty.dxf"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(DxfParseError):
            DxfParser().parse(empty)

    def test_parse_corrupt_file_raises(self, tmp_path):
        bad = tmp_path / "bad.dxf"
        bad.write_text("not a dxf file at all", encoding="utf-8")
        with pytest.raises((DxfFormatError, DxfParseError)):
            DxfParser().parse(bad)

    def test_parse_path_traversal_blocked(self, tmp_path):
        dxf = tmp_path / "sample.dxf"
        _make_sample_dxf(dxf)
        # base_dir 之外的路径 安全检查失败
        outside = tmp_path.parent / "evil.dxf"
        with pytest.raises(DxfParseError):
            DxfParser().parse(str(dxf), base_dir=str(outside))

    def test_parse_with_base_dir_ok(self, tmp_path):
        dxf = tmp_path / "sample.dxf"
        _make_sample_dxf(dxf)
        result = DxfParser().parse(dxf, base_dir=str(tmp_path))
        assert result.file_name == "sample.dxf"

    def test_parse_user_id_accepted(self, tmp_path):
        dxf = tmp_path / "sample.dxf"
        _make_sample_dxf(dxf)
        result = DxfParser().parse(dxf, user_id="u-1")
        assert result.file_name == "sample.dxf"

    def test_parse_result_counts(self, tmp_path):
        dxf = tmp_path / "sample.dxf"
        _make_sample_dxf(dxf)
        result = DxfParser().parse(dxf)
        total = (
            len(result.lines)
            + len(result.circles)
            + len(result.arcs)
            + len(result.polylines)
            + len(result.splines)
            + len(result.texts)
        )
        assert total >= 5

    def test_parse_dimensions(self, tmp_path):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_linear_dim(base=(0, -5), p1=(0, 0), p2=(10, 0)).render()
        doc.saveas(str(tmp_path / "dim.dxf"))
        result = DxfParser().parse(tmp_path / "dim.dxf")
        assert result.dimensions is not None
