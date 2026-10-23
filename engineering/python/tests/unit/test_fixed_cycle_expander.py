"""固定循环展开器单元测试。

覆盖：G81/G82/G83 展开、Q 啄钻步数、模态重复、G80 取消、
增量模式不展开、不支持循环原样保留、缺参保护、注释行保留。
"""

from __future__ import annotations

import pytest

from app.simulation.fixed_cycle_expander import expand_fixed_cycles


def _moves(text: str) -> list[str]:
    """取展开后的运动行（忽略注释与空行）。"""
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("(")]


class TestG81:
    def test_simple_drill_expands_to_position_approach_feed_retract(self):
        src = "G90\nG81 X10.000 Y20.000 Z-3.000 R2.000 F100.000\nG80"
        out, stats = expand_fixed_cycles(src)

        assert stats.expanded_cycles == 1
        assert stats.expanded_holes == 1
        assert stats.complete is True

        moves = _moves(out)
        assert "G00 X10.000 Y20.000" in moves
        assert "G00 Z2.000" in moves
        assert "G01 Z-3.000 F100.000" in moves
        assert "G80" in moves

    def test_original_cycle_line_is_not_left_in_motion_stream(self):
        src = "G81 X1 Y1 Z-1 R1 F50\nG80"
        out, _ = expand_fixed_cycles(src)
        assert not any(ln.strip().startswith("G81") for ln in _moves(out))

    def test_expansion_is_annotated_for_traceability(self):
        src = "G81 X1 Y1 Z-1 R1 F50\nG80"
        out, _ = expand_fixed_cycles(src)
        assert any("EXPANDED 81" in ln for ln in out.splitlines())


class TestG83:
    def test_peck_drill_steps_by_q(self):
        # R=10 -> Z=0，Q=4 => 10→6→10, 10→2→10, 10→0→10
        src = "G98 G83 X5.000 Y5.000 Z0.000 R10.000 Q4.000 F200.000\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 1

        moves = _moves(out)
        feeds = [m for m in moves if m.startswith("G01")]
        assert feeds == [
            "G01 Z6.000 F200.000",
            "G01 Z2.000 F200.000",
            "G01 Z0.000 F200.000",
        ]
        # 1 次初始接近 R 平面 + 每次啄钻后 3 次退回 R 平面
        assert moves.count("G00 Z10.000") == 4

    def test_single_peck_when_depth_within_q(self):
        src = "G83 X0 Y0 Z8.000 R10.000 Q5.000 F100\nG80"
        out, _ = expand_fixed_cycles(src)
        feeds = [m for m in _moves(out) if m.startswith("G01")]
        assert feeds == ["G01 Z8.000 F100.000"]

    def test_missing_q_falls_back_to_single_plunge(self):
        src = "G83 X0 Y0 Z0.000 R10.000 F100\nG80"
        out, _ = expand_fixed_cycles(src)
        feeds = [m for m in _moves(out) if m.startswith("G01")]
        assert feeds == ["G01 Z0.000 F100.000"]


class TestG82:
    def test_dwell_drill_treated_as_single_plunge(self):
        src = "G82 X3 Y4 Z-2 R1 P500 F80\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 1
        feeds = [m for m in _moves(out) if m.startswith("G01")]
        assert feeds == ["G01 Z-2.000 F80.000"]


class TestModalRepeat:
    def test_xy_only_line_repeats_cycle_with_previous_params(self):
        src = "G90\nG81 X10 Y10 Z-3 R2 F100\nX30 Y10\nX50 Y10\nG80\n"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_cycles == 1
        assert stats.expanded_holes == 3

        moves = _moves(out)
        assert "G00 X10.000 Y10.000" in moves
        assert "G00 X30.000 Y10.000" in moves
        assert "G00 X50.000 Y10.000" in moves
        assert moves.count("G01 Z-3.000 F100.000") == 3

    def test_g80_terminates_modal_repeat(self):
        src = "G81 X10 Y10 Z-3 R2 F100\nG80\nX40 Y40\n"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 1
        # G80 之后的纯 XY 行不再触发循环，原样保留
        assert any(ln.strip() == "X40 Y40" for ln in out.splitlines())


class TestSafetyGuards:
    def test_incremental_mode_is_not_expanded(self):
        src = "G91\nG81 X1 Y1 Z-3 R2 F100\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 0
        assert stats.skipped_incremental == 1
        assert stats.complete is False
        assert "G81 X1 Y1 Z-3 R2 F100" in out

    def test_unsupported_cycle_kept_verbatim_and_reported(self):
        # G76 精镗带让刀偏置、G87 反镗方向相反，两者仍不展开（2026-10-23 起
        # G84 已支持，故此处改用 G76 作未支持样本）。
        src = "G90\nG76 X10 Y10 Z-5 R2 F100\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.unsupported == {"76": 1}
        assert stats.complete is False
        assert "G76 X10 Y10 Z-5 R2 F100" in out

    def test_g87_back_boring_kept_verbatim(self):
        src = "G90\nG87 X10 Y10 Z-5 R2 F100\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.unsupported == {"87": 1}
        assert stats.complete is False
        assert "G87 X10 Y10 Z-5 R2 F100" in out

    def test_missing_r_or_z_kept_verbatim(self):
        src = "G90\nG81 X10 Y10 F100\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 0
        assert "G81 X10 Y10 F100" in out
        assert any("缺少 R 或 Z" in w for w in stats.warnings)

    def test_bottom_above_retract_plane_is_rejected(self):
        src = "G90\nG81 X10 Y10 Z5 R2 F100\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 0
        assert any("高于退回平面" in w for w in stats.warnings)


class TestPassThrough:
    def test_decimal_g_code_does_not_break_expansion(self):
        """G05.1 这类扩展码不能触发 int() 异常（实测 DXF 链路 G05.1 Q1）。"""
        src = "G05.1 Q1\nG90\nG81 X1 Y1 Z-1 R1 F50\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 1
        assert "G05.1 Q1" in out

    def test_non_cycle_lines_are_preserved(self):
        src = "%\nO1000 (PROGRAM 1000)\nG21 G17 G40 G49 G80 G90\nG00 Z80.000\nG81 X1 Y1 Z-1 R1 F50\nG80\nM30\n%\n"
        out, _ = expand_fixed_cycles(src)
        for expected in ("%", "O1000 (PROGRAM 1000)", "G21 G17 G40 G49 G80 G90", "G00 Z80.000", "M30"):
            assert expected in out

    def test_comment_lines_are_preserved(self):
        src = "; 材料: 45#钢\nG81 X1 Y1 Z-1 R1 F50\nG80"
        out, _ = expand_fixed_cycles(src)
        assert "; 材料: 45#钢" in out

    def test_empty_input(self):
        out, stats = expand_fixed_cycles("")
        assert out == ""
        assert stats.expanded_holes == 0
        assert stats.complete is True

    def test_stats_complete_true_when_only_supported_cycles(self):
        src = "G90\nG81 X1 Y1 Z-1 R1 F50\nG80"
        _, stats = expand_fixed_cycles(src)
        assert stats.complete is True


class TestTappingCycles:
    """G84/G74 攻丝：材料去除几何等价 G81，但 F 是螺距，必须告警。"""

    def test_g84_expands_to_feed_then_retract(self):
        src = "G90\nG84 X10 Y10 Z-5 R2 F1.25\nG80"
        out, stats = expand_fixed_cycles(src)

        assert stats.expanded_cycles == 1
        assert stats.expanded_holes == 1
        assert stats.complete is True

        moves = _moves(out)
        assert "G00 X10.000 Y10.000" in moves
        assert "G00 Z2.000" in moves
        assert "G01 Z-5.000 F1.250" in moves
        assert not any(m.startswith("G84") for m in moves)

    def test_g84_warns_that_f_is_pitch_not_feed_rate(self):
        src = "G90\nG84 X10 Y10 Z-5 R2 F1.25\nG80"
        _, stats = expand_fixed_cycles(src)
        assert any("螺距" in w for w in stats.warnings)

    def test_g74_left_hand_tap_also_expands(self):
        src = "G90\nG74 X1 Y1 Z-3 R1 F1.0\nG80"
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 1
        assert stats.complete is True
        assert "G01 Z-3.000 F1.000" in _moves(out)


class TestBoringCycles:
    """G85/G86/G88/G89 镗孔：进给到孔底后回退，几何同 G81。"""

    @pytest.mark.parametrize("code", ["85", "86", "88", "89"])
    def test_boring_cycle_expands_like_a_drill(self, code):
        src = f"G90\nG{code} X5 Y5 Z-8 R2 F80\nG80"
        out, stats = expand_fixed_cycles(src)

        assert stats.expanded_holes == 1
        assert stats.complete is True  # 覆盖度完整，不再计入 unsupported

        moves = _moves(out)
        assert "G01 Z-8.000 F80.000" in moves
        assert not any(m.startswith(f"G{code}") for m in moves)

    @pytest.mark.parametrize("code", ["85", "86", "88", "89"])
    def test_boring_cycle_does_not_peck_without_q(self, code):
        """镗孔不是啄钻循环：给了 Q 也不应产生多段下刀。"""
        src = f"G90\nG{code} X5 Y5 Z-8 R2 Q2 F80\nG80"
        out, _ = expand_fixed_cycles(src)
        plunge = [m for m in _moves(out) if m.startswith("G01 Z")]
        assert len(plunge) == 1


class TestMultiHoleRealShape:
    def test_four_hole_program_from_dxf_pipeline_shape(self):
        """复刻 DXF 链路实际产出的四孔程序形态（R 平面在毛坯上方）。"""
        src = (
            "G90\n"
            "G00 G43 Z80.000 H00\n"
            "G98 G83 X25.000 Y25.000 Z-3.000 R80.000 Q5.000 F3000.000\n"
            "G80\n"
            "G98 G83 X75.000 Y25.000 Z-3.000 R80.000 Q5.000 F3000.000\n"
            "G80\n"
        )
        out, stats = expand_fixed_cycles(src)
        assert stats.expanded_holes == 2
        assert stats.complete is True

        moves = _moves(out)
        feeds = [m for m in moves if m.startswith("G01")]
        # 每孔 (80-(-3))=83mm，Q=5 => 17 次进给
        assert len(feeds) == 34
        # 最终孔底必须是 -3，且不能更负（防止钻穿垫板）
        assert min(float(m.split("Z")[1].split()[0]) for m in feeds) == pytest.approx(-3.0)
