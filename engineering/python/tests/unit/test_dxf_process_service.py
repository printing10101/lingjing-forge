"""DxfProcessService 端到端服务 单元测试。

回归背景（2026-09 全链条验证发现）：HTTP 层 ``/dxf/process`` 的 G 代码
阶段原本只写 header/footer 空程序作冒烟占位——服务声明"端到端"却产不出
可用 NC 代码。修复后 ``_run_gcode`` 用特征阶段下发的 holes_detail 组装
part_description，走真实 ProcessPlanningPipeline 生成完整程序文本。
"""

from __future__ import annotations

import ezdxf
import pytest

from app.dxf.process_service import DxfProcessService


@pytest.fixture
def dxf_with_4_holes(tmp_path):
    """生成 100x100 矩形 + 4 孔的 DXF（与 data/test_fixtures/case2 同构）。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_line((100, 0), (100, 100))
    msp.add_line((100, 100), (0, 100))
    msp.add_line((0, 100), (0, 0))
    for cx, cy in [(25, 25), (75, 25), (75, 75), (25, 75)]:
        msp.add_circle((cx, cy), radius=4)
    path = tmp_path / "plate_4holes.dxf"
    doc.saveas(path)
    return path


class TestProcessEndToEnd:
    def test_gcode_stage_produces_real_program(self, dxf_with_4_holes, tmp_path):
        """G 代码阶段必须产出真实程序：运动指令 + 程序尾，而非空壳。"""
        svc = DxfProcessService()
        out_dir = tmp_path / "out"
        result = svc.process(
            dxf_path=dxf_with_4_holes,
            output_dir=out_dir,
            postprocessor="fanuc_0i",
        )

        assert result.parse.success
        assert result.features.success
        assert result.gcode is not None, "提供 output_dir 时必须执行 G 代码阶段"
        assert result.gcode.success, f"G 代码阶段失败: {result.gcode.error}"

        nc_files = list(out_dir.glob("*.nc"))
        assert nc_files, "必须落盘 .nc 文件"
        text = nc_files[0].read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) > 20, f"程序行数过少，疑似空壳: {len(lines)} 行"
        assert any(l.lstrip().startswith(("G0", "G1", "G2", "G3")) for l in lines), (
            "缺少运动指令"
        )
        assert any(l.lstrip().startswith("M30") for l in lines), "缺少程序结束符 M30"

        gcode_summary = result.gcode.summary
        assert gcode_summary["controller"] == "fanuc_0i"
        assert gcode_summary["holes"] == 4
        assert gcode_summary["operations"] > 0

    def test_unknown_controller_falls_back_to_fanuc(self, dxf_with_4_holes, tmp_path):
        svc = DxfProcessService()
        result = svc.process(
            dxf_path=dxf_with_4_holes,
            output_dir=tmp_path / "out",
            postprocessor="not_a_dialect",
        )
        assert result.gcode is not None
        assert result.gcode.success
        assert result.gcode.summary["controller"] == "fanuc_0i"

    def test_no_output_dir_skips_gcode_stage(self, dxf_with_4_holes):
        """未提供 output_dir 时保持旧行为：跳过 G 代码阶段（可选步骤）。"""
        svc = DxfProcessService()
        result = svc.process(dxf_path=dxf_with_4_holes)
        assert result.success
        assert result.gcode is None
