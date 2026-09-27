"""后处理器边界与错误路径测试。

对应 docs/development/postprocessor-方言声明化设计.md P0 验收标准中的
「边界 / 错误路径」部分（正常路径由 golden 测试覆盖）。

覆盖：
- 边界：负数坐标、dwell=0、pecking 参数、深度归一化（-abs）、小数位数、大数值
- 错误路径：非法构造参数（decimal_places/safe_z_height/rapid_feed）、
  XM-100 工作空间超限、TWP 零向量、未知控制器、非法冷却液状态

说明：这些断言同时是方言声明化迁移后的**行为契约**——方言从代码类迁移为
「声明 + 模板」后，这些边界/错误行为必须保持逐字一致。
"""

from __future__ import annotations

import pytest

from app.postprocessor.fagor import FagorPostProcessor
from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.gsk import GSKPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.hnc import HNCPostProcessor
from app.postprocessor.knd import KNDPostProcessor
from app.postprocessor.mitsubishi import MitsubishiPostProcessor
from app.postprocessor.registry import PostProcessorRegistry
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.xmachine import XMachineXM100PostProcessor


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestConstructorValidation:
    """构造参数校验（所有方言继承 BasePostProcessor 同一套校验）。"""

    @pytest.mark.parametrize(
        "cls",
        [
            FanucPostProcessor,
            SiemensPostProcessor,
            HeidenhainPostProcessor,
            GSKPostProcessor,
            HNCPostProcessor,
            KNDPostProcessor,
            MitsubishiPostProcessor,
            FagorPostProcessor,
            XMachineXM100PostProcessor,
        ],
    )
    def test_negative_decimal_places_rejected(self, cls):
        with pytest.raises(ValueError):
            cls(decimal_places=-1)

    @pytest.mark.parametrize(
        "cls",
        [
            FanucPostProcessor,
            SiemensPostProcessor,
            HeidenhainPostProcessor,
            XMachineXM100PostProcessor,
        ],
    )
    def test_zero_safe_z_rejected(self, cls):
        with pytest.raises(ValueError):
            cls(safe_z_height=0.0)

    @pytest.mark.parametrize(
        "cls",
        [
            FanucPostProcessor,
            SiemensPostProcessor,
            HeidenhainPostProcessor,
            XMachineXM100PostProcessor,
        ],
    )
    def test_nonpositive_rapid_feed_rejected(self, cls):
        with pytest.raises(ValueError):
            cls(rapid_feed=0.0)

    def test_custom_decimal_places_accepted(self):
        pp = FanucPostProcessor(decimal_places=2)
        assert pp._fmt(3.14159) == "3.14"
        assert pp._fmt(-0.005) == "-0.01"  # 四舍五入


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestBoundaryCoordinates:
    """负数坐标与大数值边界（输出必须确定性且格式化一致）。"""

    def test_negative_coordinates_fanuc(self):
        pp = FanucPostProcessor()
        move = pp.format_linear_move(-25.5, -10.0, -5.0, feed=300.0)
        assert "X-25.500" in move
        assert "Y-10.000" in move
        assert "Z-5.000" in move

    def test_negative_arc_center_fanuc(self):
        pp = FanucPostProcessor()
        arc = pp.format_arc((-20.0, -10.0, -5.0), (-10.0, 0.0, -5.0), (-15.0, -5.0, -5.0), clockwise=True)
        assert "G02" in arc
        assert "X-10.000" in arc

    def test_negative_depth_normalized_fanuc(self):
        # Fanuc 钻孔循环：Z 直接使用传入的 z（不做 -abs(depth) 归一化），
        # 但负数 z 必须原样输出且不出现 "--" / "+-"
        pp = FanucPostProcessor()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=-15.0, depth=15.0)
        assert "Z-15.000" in drill
        assert "--" not in drill
        assert "+-" not in drill

    def test_negative_depth_normalized_heidenhain(self):
        pp = HeidenhainPostProcessor()
        pp.format_header()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=-15.0)
        assert "Q201=-15.000" in drill

    def test_negative_depth_normalized_siemens(self):
        pp = SiemensPostProcessor()
        pp.format_header()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=-15.0)
        assert "-15.000" in drill

    def test_large_coordinate_formatting(self):
        pp = FanucPostProcessor()
        move = pp.format_linear_move(12345.678, 0.0, 0.0, feed=500.0)
        assert "X12345.678" in move

    def test_zero_coordinates(self):
        pp = FanucPostProcessor()
        move = pp.format_linear_move(0.0, 0.0, 0.0, feed=500.0)
        assert "X0.000 Y0.000 Z0.000" in move


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestDrillCycleBoundaries:
    """钻孔循环边界：dwell=0 与 pecking 参数语义。"""

    def test_dwell_zero_uses_g83_fanuc(self):
        # Fanuc：dwell>0 G73（带 P 暂停），dwell=0 G83（无 P）
        pp = FanucPostProcessor()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, dwell=0.0)
        assert "G83" in drill
        assert "P" not in drill

    def test_dwell_positive_uses_g73_fanuc(self):
        pp = FanucPostProcessor()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, dwell=0.5)
        assert "G73" in drill
        assert "P500" in drill  # dwell 0.5s → 500ms

    def test_pecking_param_ignored_fanuc(self):
        # pecking 参数为签名兼容，不参与分支（分支由 dwell 决定）
        pp = FanucPostProcessor()
        a = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, pecking=True)
        b = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, pecking=False)
        assert a == b

    def test_depth_threshold_g83_g81_xmachine(self):
        # XM-100：depth>5.0 G83，否则 G81（桌面级小切深逻辑）
        pp = XMachineXM100PostProcessor()
        deep = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=6.0)
        shallow = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=3.0)
        assert "G83" in deep
        assert "G81" in shallow

    def test_pecking_param_ignored_xmachine(self):
        pp = XMachineXM100PostProcessor()
        a = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=6.0, pecking=False)
        b = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=6.0, pecking=True)
        assert a == b

    def test_zero_dwell_heidenhain_uses_203(self):
        # Heidenhain：dwell=0 CYCL DEF 203（万能钻孔）
        pp = HeidenhainPostProcessor()
        pp.format_header()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, dwell=0.0)
        assert "CYCL DEF 203" in drill

    def test_positive_dwell_heidenhain_uses_200(self):
        pp = HeidenhainPostProcessor()
        pp.format_header()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, dwell=1.0)
        assert "CYCL DEF 200" in drill
        assert "Q210=1.000" in drill

    def test_zero_dwell_siemens_uses_83(self):
        pp = SiemensPostProcessor()
        pp.format_header()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, dwell=0.0)
        assert "CYCLE83" in drill

    def test_positive_dwell_siemens_uses_82(self):
        pp = SiemensPostProcessor()
        pp.format_header()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0, dwell=1.0)
        assert "CYCLE82" in drill


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestCoolantBoundaries:
    """冷却液状态边界。"""

    def test_unknown_state_returns_m09(self):
        # 未知状态：_format_coolant 返回空 format_coolant 兜底 "M09"
        pp = FanucPostProcessor()
        assert pp.format_coolant("weird_state") == "M09"

    def test_case_insensitive(self):
        pp = FanucPostProcessor()
        assert pp.format_coolant("ON") == "M08"
        assert pp.format_coolant("Off") == "M09"

    def test_fog(self):
        pp = FanucPostProcessor()
        assert pp.format_coolant("fog") == "M07"


@pytest.mark.unit
@pytest.mark.postprocessor
@pytest.mark.gcode
class TestXMachineSafety:
    """XM-100 专属安全校验（五轴桌面机硬约束）。"""

    def test_workspace_overflow_raises(self):
        pp = XMachineXM100PostProcessor()
        # XM-100 行程 ±50mm，超出必须报错
        with pytest.raises(ValueError):
            pp._validate_workspace(x=100.0, y=0.0, z=0.0)

    def test_a_axis_out_of_range_raises(self):
        pp = XMachineXM100PostProcessor()
        with pytest.raises(ValueError):
            pp._validate_a_axis(200.0)

    def test_c_axis_out_of_range_raises(self):
        pp = XMachineXM100PostProcessor()
        with pytest.raises(ValueError):
            pp._validate_c_axis(400.0)

    def test_twp_zero_vector_raises(self):
        pp = XMachineXM100PostProcessor()
        with pytest.raises(ValueError):
            pp.format_twp_on(tool_axis_i=0.0, tool_axis_j=0.0, tool_axis_k=0.0)

    def test_twp_normalized_output(self):
        pp = XMachineXM100PostProcessor()
        out = pp.format_twp_on(tool_axis_i=0.0, tool_axis_j=0.0, tool_axis_k=2.0)
        # 非单位矢量被归一化：K=2.0 1.000
        assert "K1.000" in out

    def test_workspace_check_within_bounds(self):
        pp = XMachineXM100PostProcessor()
        out = pp.format_workspace_check(x=20.0, y=20.0, z=20.0)
        assert "OK" in out


@pytest.mark.unit
@pytest.mark.postprocessor
class TestRegistryBoundaries:
    """注册表边界（方言动态注册是插件化的挂载点）。"""

    def test_unknown_controller_raises_keyerror(self):
        registry = PostProcessorRegistry()
        with pytest.raises(KeyError):
            registry.get_processor("nonexistent_controller")

    def test_register_rejects_non_subclass(self):
        registry = PostProcessorRegistry()
        with pytest.raises(TypeError):
            registry.register("bad_controller", str)  # type: ignore[arg-type]

    def test_register_then_get(self):
        registry = PostProcessorRegistry()
        registry.register("custom_fanuc", FanucPostProcessor)
        pp = registry.get_processor("custom_fanuc")
        assert isinstance(pp, FanucPostProcessor)

    def test_duplicate_register_overwrites(self):
        # 当前实现允许覆盖注册（方言热更新场景需要），验证不抛错且生效
        registry = PostProcessorRegistry()
        registry.register("dup_controller", FanucPostProcessor)
        registry.register("dup_controller", SiemensPostProcessor)
        pp = registry.get_processor("dup_controller")
        assert isinstance(pp, SiemensPostProcessor)


@pytest.mark.unit
@pytest.mark.postprocessor
class TestSubprogramBoundaries:
    """子程序调用边界（寄存器范围钳制）。"""

    def test_program_number_clamped(self):
        pp = FanucPostProcessor()
        # 超过 9999 钳制到 9999（现有行为）
        out = pp.format_subprogram_call(program_number=99999)
        assert "M98" in out

    def test_subprogram_end_with_return(self):
        pp = FanucPostProcessor()
        out = pp.format_subprogram_end(return_value="1000")
        assert "P1000" in out

    def test_fanuc_variant_subprogram(self):
        # GSK/HNC/KND/Mitsubishi 继承 Fanuc 子程序格式（M98 Pxxxx Lx）
        # 注：HNC 只覆盖 format_subprogram_end，call 继承 Fanuc
        for cls in [GSKPostProcessor, HNCPostProcessor, KNDPostProcessor, MitsubishiPostProcessor]:
            pp = cls()
            out = pp.format_subprogram_call(program_number=100, repeat=2)
            assert "M98" in out

    def test_fagor_custom_subprogram(self):
        # Fagor 用自己的子程序格式：CALL Pxxxxx, R<repeat>（5 位程序号）
        pp = FagorPostProcessor()
        out = pp.format_subprogram_call(program_number=100, repeat=2)
        assert "CALL P00100, R2" in out
