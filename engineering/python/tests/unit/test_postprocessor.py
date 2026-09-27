"""Unit tests for CNC postprocessor modules.

Covers:
- Fanuc 0i series G-code generation accuracy
- Heidenhain TNC program generation accuracy
- Siemens 840D G-code generation accuracy
- Instruction format, parameter ranges, and process specifications
"""

from __future__ import annotations

import re
import pytest

from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.base import BasePostProcessor
from app.postprocessor.registry import PostProcessorRegistry


@pytest.mark.unit
@pytest.mark.postprocessor
class TestBasePostProcessor:
    """Tests for the abstract base postprocessor."""

    def test_format_value(self):
        """Value formatting with default 3 decimal places."""
        pp = FanucPostProcessor()
        assert pp._fmt(10.0) == "10.000"
        assert pp._fmt(0.123456) == "0.123"
        assert pp._fmt(-5.6789) == "-5.679"

    def test_custom_decimal_places(self):
        """Custom decimal place formatting."""
        pp = FanucPostProcessor(decimal_places=2)
        assert pp._fmt(10.123) == "10.12"
        assert pp._fmt(0.005) == "0.01"

    def test_date_string(self):
        """Date string format."""
        date_str = BasePostProcessor._date_string()
        assert re.match(r"\d{4}-\d{2}-\d{2}", date_str)

    def test_safe_z_default(self):
        """Default safe Z height (代码默认值 80.0，生产配置文件为 50.0)。"""
        pp = FanucPostProcessor()
        assert pp.safe_z_height == 80.0

    def test_rapid_feed_default(self):
        """Default rapid feed rate."""
        pp = FanucPostProcessor()
        assert pp.rapid_feed == 10000

    def test_abstract_methods_exist(self):
        """All postprocessors must implement abstract methods."""
        for cls in [FanucPostProcessor, HeidenhainPostProcessor, SiemensPostProcessor]:
            pp = cls()
            assert callable(getattr(pp, "format_header"))
            assert callable(getattr(pp, "format_tool_change"))
            assert callable(getattr(pp, "format_arc"))
            assert callable(getattr(pp, "format_coolant"))
            assert callable(getattr(pp, "format_tool_compensation"))
            assert callable(getattr(pp, "format_cycle_drill"))
            assert callable(getattr(pp, "format_footer"))


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestFanucPostProcessor:
    """Fanuc 0i series G-code generation tests."""

    def setup_method(self):
        self.pp = FanucPostProcessor()

    def test_header_format(self):
        """Program header should contain Fanuc-specific syntax."""
        header = self.pp.format_header(program_number=1)
        assert "%" in header
        assert "O0001" in header
        assert "G21 G17 G40 G49 G80 G90 G94" in header
        assert "M03 S1000" in header  # 代码默认 default_rpm=1000
        assert "M08" in header
        assert "G43" in header
        assert "H00" in header

    def test_header_program_number_format(self):
        """Program number should be 4-digit zero-padded."""
        header = self.pp.format_header(program_number=42)
        assert "O0042" in header

    def test_tool_change_format(self):
        """Tool change should use Tnn M06 syntax."""
        tc = self.pp.format_tool_change(tool_id=1)
        assert "T01 M06" in tc
        assert "G43" in tc
        assert "H01" in tc

    def test_tool_change_with_compensation(self):
        """Tool change with radius compensation."""
        tc = self.pp.format_tool_change(tool_id=2, length_comp=-5.0, radius_comp=3.0)
        assert "T02 M06" in tc
        assert "M03 S1000" in tc  # 代码默认 default_rpm=1000

    def test_arc_clockwise(self):
        """Clockwise arc should use G02."""
        arc = self.pp.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(0.0, 10.0, 0.0),
            clockwise=True,
        )
        assert "G02" in arc
        assert "R10.000" in arc

    def test_arc_counterclockwise(self):
        """Counterclockwise arc should use G03."""
        arc = self.pp.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(0.0, 10.0, 0.0),
            clockwise=False,
        )
        assert "G03" in arc

    def test_coolant_on_off(self):
        """Coolant on/off commands."""
        assert self.pp.format_coolant("on") == "M08"
        assert self.pp.format_coolant("off") == "M09"
        assert self.pp.format_coolant("ON") == "M08"

    def test_tool_compensation(self):
        """Tool length and radius compensation."""
        comp = self.pp.format_tool_compensation(length_offset=1, radius_offset=2)
        assert "G43 H01" in comp
        assert "G41 D02" in comp

    def test_tool_compensation_cancel(self):
        """Cancel all compensation."""
        comp = self.pp.format_tool_compensation()
        assert "G49 G40" in comp

    def test_drill_cycle_with_dwell(self):
        """Drilling cycle with dwell (G73 peck drill)."""
        drill = self.pp.format_cycle_drill(
            x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=1.0
        )
        assert "G98 G73" in drill
        assert "P1000" in drill
        assert "G80" in drill

    def test_drill_cycle_without_dwell(self):
        """Standard peck drill (G83)."""
        drill = self.pp.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0)
        assert "G98 G83" in drill
        assert "P" not in drill
        assert "G80" in drill

    def test_footer_format(self):
        """Program footer should contain proper shutdown sequence."""
        footer = self.pp.format_footer()
        assert "M09" in footer
        assert "M05" in footer
        assert "M30" in footer
        assert "%" in footer
        m30_idx = footer.index("M30")
        pct_idx = footer.rindex("%")
        assert m30_idx < pct_idx


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestHeidenhainPostProcessor:
    """Heidenhain TNC program generation tests."""

    def setup_method(self):
        self.pp = HeidenhainPostProcessor()

    def test_header_format(self):
        """Program header with Heidenhain BEGIN PGM syntax."""
        header = self.pp.format_header(program_number=1)
        assert "BEGIN PGM 0001 MM" in header
        assert "BLK FORM" in header
        assert "TOOL CALL 1 Z S1000" in header  # 代码默认 default_rpm=1000

    def test_block_numbering(self):
        """Heidenhain uses sequential block numbers."""
        self.pp.format_header()
        first_block = self.pp._next_block()
        assert first_block > 0

    def test_tool_change(self):
        """TOOL CALL syntax."""
        self.pp.format_header()
        tc = self.pp.format_tool_change(tool_id=3)
        assert "TOOL CALL 3 Z S1000" in tc  # 代码默认 default_rpm=1000

    def test_arc_clockwise(self):
        """Heidenhain uses CC for circle center."""
        self.pp.format_header()
        arc = self.pp.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=True,
        )
        assert "L" in arc

    def test_arc_counterclockwise(self):
        """Counterclockwise uses CC (circle center) command."""
        self.pp.format_header()
        arc = self.pp.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=False,
        )
        assert "CC" in arc
        assert "C" in arc

    def test_coolant(self):
        """Coolant on/off."""
        assert self.pp.format_coolant("on") == "M08"
        assert self.pp.format_coolant("off") == "M09"

    def test_tool_compensation(self):
        """TOOL CALL with DR+ offset."""
        comp = self.pp.format_tool_compensation(length_offset=1, radius_offset=2)
        assert "TOOL CALL 1 Z" in comp
        assert "DR+2" in comp

    def test_drill_cycle_with_dwell(self):
        """CYCL DEF 200 with dwell parameters."""
        drill = self.pp.format_cycle_drill(
            x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=1.0
        )
        assert "CYCL DEF 200" in drill
        assert "Q210=" in drill
        assert "Q211=" in drill
        assert "CYCL CALL" in drill

    def test_drill_cycle_without_dwell(self):
        """CYCL DEF 203 universal drilling."""
        drill = self.pp.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0)
        assert "CYCL DEF 203" in drill
        assert "CYCL CALL" in drill

    def test_footer_format(self):
        """Footer with M30 and END PGM."""
        footer = self.pp.format_footer()
        assert "M30" in footer
        assert "END PGM" in footer
        assert "M09" in footer
        assert "M05" in footer


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestSiemensPostProcessor:
    """Siemens 840D G-code generation tests."""

    def setup_method(self):
        self.pp = SiemensPostProcessor()

    def test_header_format(self):
        """Siemens program header with N block numbers."""
        header = self.pp.format_header(program_number=1)
        assert re.search(r"N\d{5}", header)
        assert "G17 G40 G90 G94" in header
        assert "M03 S1000" in header  # 代码默认 default_rpm=1000

    def test_block_numbering_step_10(self):
        """Siemens block numbers increment by 10."""
        self.pp.format_header()
        n1 = self.pp._next_block()
        n2 = self.pp._next_block()
        assert n2 - n1 == 10

    def test_tool_change(self):
        """Siemens T= naming convention."""
        tc = self.pp.format_tool_change(tool_id=5)
        assert 'T="TOOL05"' in tc
        assert "M06" in tc
        assert "D1" in tc

    def test_arc_radius_format(self):
        """Siemens uses CR= radius format."""
        self.pp.format_header()
        arc = self.pp.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(0.0, 10.0, 0.0),
            clockwise=True,
        )
        assert "G02" in arc
        assert "CR=" in arc

    def test_arc_counterclockwise(self):
        """G03 for counterclockwise."""
        self.pp.format_header()
        arc = self.pp.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(0.0, 10.0, 0.0),
            clockwise=False,
        )
        assert "G03" in arc

    def test_coolant(self):
        """Coolant with block numbers."""
        self.pp.format_header()
        on = self.pp.format_coolant("on")
        assert "M08" in on
        assert re.search(r"N\d{5}", on)

    def test_tool_compensation(self):
        """Siemens $TC_DP6 tool table and DISC offset."""
        comp = self.pp.format_tool_compensation(length_offset=1, radius_offset=3)
        assert "$TC_DP6" in comp
        assert "G41 DISC3" in comp

    def test_drill_cycle_with_dwell(self):
        """CYCLE82 with dwell (Siemens钻孔循环: dwell>0 使用 CYCLE82 支持底部暂停)。"""
        self.pp.format_header()
        drill = self.pp.format_cycle_drill(
            x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=1.0
        )
        assert "CYCLE82" in drill
        assert "G00" in drill

    def test_drill_cycle_without_dwell(self):
        """CYCLE83 deep hole drilling."""
        self.pp.format_header()
        drill = self.pp.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0)
        assert "CYCLE83" in drill

    def test_footer(self):
        """Siemens footer with M30."""
        footer = self.pp.format_footer()
        assert "M30" in footer
        assert re.search(r"N\d{5} M30", footer)


@pytest.mark.unit
@pytest.mark.postprocessor
class TestPostProcessorRegistry:
    """Test postprocessor registry."""

    def test_register_and_get(self):
        """Register and retrieve postprocessors."""
        registry = PostProcessorRegistry()
        registry.register("test_fanuc", FanucPostProcessor)

        pp = registry.get_processor("test_fanuc")
        assert isinstance(pp, FanucPostProcessor)

    def test_unregistered_key(self):
        """Getting unregistered key raises KeyError."""
        registry = PostProcessorRegistry()
        with pytest.raises(KeyError):
            registry.get_processor("nonexistent_controller")

    def test_list_controllers(self):
        """List all registered controllers."""
        registry = PostProcessorRegistry()
        controllers = registry.list_controllers()
        assert "fanuc_0i" in controllers
        assert "siemens_840d" in controllers
        assert "heidenhain_tnc" in controllers

    def test_full_program_fanuc(self):
        """Generate complete Fanuc program and verify structure."""
        pp = FanucPostProcessor()
        program = "\n".join(
            [
                pp.format_header(1),
                pp.format_tool_change(1),
                pp.format_cycle_drill(10.0, 10.0, 0.0, 15.0),
                pp.format_coolant("off"),
                pp.format_footer(),
            ]
        )
        assert program.startswith("%")
        assert program.endswith("%\n") or program.endswith("%")
        assert "O0001" in program
        assert "M30" in program

    def test_full_program_heidenhain(self):
        """Generate complete Heidenhain program."""
        pp = HeidenhainPostProcessor()
        program = "\n".join(
            [
                pp.format_header(1),
                pp.format_tool_change(1),
                pp.format_coolant("off"),
                pp.format_footer(),
            ]
        )
        assert "BEGIN PGM" in program
        assert "END PGM" in program

    def test_full_program_siemens(self):
        """Generate complete Siemens program."""
        pp = SiemensPostProcessor()
        program = "\n".join(
            [
                pp.format_header(1),
                pp.format_tool_change(1),
                pp.format_coolant("off"),
                pp.format_footer(),
            ]
        )
        assert re.search(r"N\d{5}", program)
        assert "M30" in program
