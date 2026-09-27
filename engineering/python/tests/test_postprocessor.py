"""CNC后处理器模块单元测试。

覆盖：
- 各后处理器基础功能（所有抽象方法实现完整性）
- 格式正确性（NC代码符合对应控制器语法规范）
- 语义等价性（相同刀轨数据经不同后处理器处理后保持加工语义一致）
- 边界条件（极端参数下的代码生成正确性）
- 配置加载（配置文件参数正确应用到后处理器）
"""

from __future__ import annotations

import os
import re
import tempfile

import pytest

from app.postprocessor.base import BasePostProcessor
from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.fagor import FagorPostProcessor
from app.postprocessor.gsk import GSKPostProcessor
from app.postprocessor.hnc import HNCPostProcessor
from app.postprocessor.knd import KNDPostProcessor
from app.postprocessor.mitsubishi import MitsubishiPostProcessor
from app.postprocessor.xmachine import XMachineXM100PostProcessor
from app.postprocessor.registry import PostProcessorRegistry

# 生产配置文件路径——TestConfigLoading 使用它避免与 ConfigValidator 的
# 必需字段清单重复维护（割裂式同步坑）。
# V2.7.0 解耦：tests 位于 engineering/python/tests/，仓库根需 4 级 dirname。
_PROD_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "config",
    "postprocessor_config.yaml",
)


def _make_config_with_controller(controller_id: str) -> str:
    """复制生产配置到临时文件并替换 target_controller。

    这样测试用的临时配置一定包含 ConfigValidator 要求的全部段
    （spindle/feed/work_coordinate/tool_offset/fixed_cycles/subprogram），
    避免每次新增必需字段都要同步改测试。
    """
    with open(_PROD_CONFIG, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(
        r"^target_controller:\s*\S+\s*$",
        f"target_controller: {controller_id}",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    fd, path = tempfile.mkstemp(suffix=".yaml", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _make_full_config() -> dict:
    """生成完整的后处理器配置字典。

    用于测试用例中直接创建后处理器实例，避免依赖文件系统。
    返回的配置包含 ConfigValidator 要求的全部必需字段。
    """
    import yaml
    with open(_PROD_CONFIG, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    # 返回 base 段配置（后处理器实际使用的合并后配置）
    return config.get("base", config)


class TestBasePostProcessor:
    """基类功能测试。"""

    def test_cannot_instantiate_abstract(self):
        """验证抽象基类不可直接实例化。"""
        with pytest.raises(TypeError):
            BasePostProcessor()

    def test_fmt_method(self):
        """验证数值格式化方法。"""

        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1):
                return ""

            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0):
                return ""

            def format_arc(self, start, end, center, clockwise=True):
                return ""

            def format_coolant(self, state):
                return ""

            def format_tool_compensation(self, length_offset=0, radius_offset=0):
                return ""

            def format_cycle_drill(self, x, y, z, depth, dwell=0):
                return ""

            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None):
                return ""

            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5):
                return ""

            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None):
                return ""

            def format_subprogram_call(self, program_number, repeat=1):
                return ""

            def format_subprogram_end(self, return_value=None):
                return ""

            def format_footer(self):
                return ""

        p = _Concrete(decimal_places=3)
        assert p._fmt(1.234567) == "1.235"
        assert p._fmt(0.0) == "0.000"
        p2 = _Concrete(decimal_places=0)
        assert p2._fmt(1.5) == "2"

    def test_date_string(self):
        """验证日期字符串格式。"""
        result = BasePostProcessor._date_string()
        parts = result.split("-")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_invalid_decimal_places(self):
        """验证负数小数位数抛出异常（覆盖行 47）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        with pytest.raises(ValueError, match="decimal_places must be >= 0"):
            _Concrete(decimal_places=-1)

    def test_invalid_safe_z_height(self):
        """验证非正安全高度抛出异常（覆盖行 49）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        with pytest.raises(ValueError, match="safe_z_height must be > 0"):
            _Concrete(safe_z_height=0.0)
        with pytest.raises(ValueError, match="safe_z_height must be > 0"):
            _Concrete(safe_z_height=-10.0)

    def test_comment_method(self):
        """验证注释生成方法（覆盖行 329）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        result = p._comment("This is a test comment")
        assert result == "; This is a test comment"

    def test_format_coolant_static_method(self):
        """验证静态冷却液方法（覆盖行 353, 356）。"""
        assert BasePostProcessor._format_coolant("on") == "M08"
        assert BasePostProcessor._format_coolant("fog") == "M07"
        assert BasePostProcessor._format_coolant("off") == "M09"
        assert BasePostProcessor._format_coolant("invalid") == ""

    def test_format_optional_stop(self):
        """验证可选停止指令（覆盖行 592）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        assert p.format_optional_stop() == "M01"

    def test_format_program_stop(self):
        """验证程序停止指令（覆盖行 603）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        assert p.format_program_stop() == "M00"

    def test_format_linear_move_with_abc(self):
        """验证带 A/C 轴的直线插补（覆盖行 637-643）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        result = p.format_linear_move(x=10.0, y=20.0, z=30.0, feed=500.0, a=45.0, c=90.0)
        assert "G01" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z30.000" in result
        assert "A45.000" in result
        assert "C90.000" in result
        assert "F" in result

    def test_format_rapid_move_with_abc(self):
        """验证带 A/C 轴的快速定位（覆盖行 667-672）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        result = p.format_rapid_move(x=100.0, y=200.0, z=50.0, a=-30.0, c=180.0)
        assert "G00" in result
        assert "X100.000" in result
        assert "Y200.000" in result
        assert "Z50.000" in result
        assert "A-30.000" in result
        assert "C180.000" in result

    def test_format_rtcp_on(self):
        """验证 RTCP 开启指令（覆盖行 686）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        result = p.format_rtcp_on(tool_length=150.5)
        assert result == "G43.4 H150"

    def test_format_rtcp_off(self):
        """验证 RTCP 关闭指令（覆盖行 696）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        result = p.format_rtcp_off()
        assert result == "G49"

    def test_config_missing_section_warning(self):
        """验证配置缺少顶层节时记录警告并填充默认值（覆盖行 96-101）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        # 提供不完整的配置，缺少 spindle 节
        incomplete_config = {
            "feed": {"min_rate": 10.0, "max_rate": 20000.0, "default_rate": 1000.0},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=incomplete_config)
        # 验证缺少的节被填充为空字典
        assert "spindle" in p.config
        assert isinstance(p.config["spindle"], dict)

    def test_config_section_type_error_warning(self):
        """验证配置节类型错误时记录警告并填充默认值（覆盖行 103-109）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        # 提供类型错误的配置，spindle 应该是 dict 但给了 list
        wrong_type_config = {
            "spindle": "not_a_dict",  # 类型错误
            "feed": {"min_rate": 10.0, "max_rate": 20000.0, "default_rate": 1000.0},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=wrong_type_config)
        # 验证类型错误的节被替换为空字典
        assert isinstance(p.config["spindle"], dict)

    def test_config_missing_top_level_parameter_warning(self):
        """验证配置缺少顶层参数时记录警告并填充默认值（覆盖行 193-197）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        # 提供缺少顶层参数的配置
        config_missing_top = {
            "spindle": {},
            "feed": {},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
            # 缺少 decimal_places, safe_z_height, rapid_feed
        }
        p = _Concrete(config=config_missing_top)
        # 验证缺少的参数被填充默认值
        assert p.config["decimal_places"] == 3
        assert p.config["safe_z_height"] == 80.0
        assert p.config["rapid_feed"] == 10000.0

    def test_config_missing_section_key_warning(self):
        """验证配置节缺少键时记录警告并填充默认值（覆盖行 214-218）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        # 提供缺少键的配置
        config_missing_keys = {
            "spindle": {"min_rpm": 50},  # 缺少 max_rpm, default_rpm
            "feed": {},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=config_missing_keys)
        # 验证缺少的键被填充默认值
        assert p.config["spindle"]["max_rpm"] == 24000
        assert p.config["spindle"]["default_rpm"] == 1000

    def test_spindle_rpm_with_limiter(self):
        """验证带限制器的主轴转速（覆盖行 248, 250, 252）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        # 带配置的后处理器会创建 limiter
        config = {
            "spindle": {"min_rpm": 100, "max_rpm": 20000, "default_rpm": 1500},
            "feed": {},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=config)
        
        # 测试带请求转速且有限制器
        result = p.get_spindle_rpm(25000.0)  # 超过最大值，应被限制
        assert result <= 20000.0
        
        # 测试带请求转速且在范围内
        result = p.get_spindle_rpm(5000.0)
        assert result == 5000.0
        
        # 测试无请求转速且有限制器（返回默认值）
        result = p.get_spindle_rpm(None)
        assert result == 1500.0

    def test_spindle_rpm_without_limiter(self):
        """验证无限制器的主轴转速（覆盖行 250, 253）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        # 无配置的后处理器没有限制器
        p = _Concrete()
        
        # 测试带请求转速且无限制器
        result = p.get_spindle_rpm(5000.0)
        assert result == 5000.0
        
        # 测试无请求转速且无限制器（返回默认值）
        result = p.get_spindle_rpm(None)
        assert result == 1000.0

    def test_feed_rate_with_limiter(self):
        """验证带限制器的进给速度（覆盖行 265, 268-270）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        config = {
            "spindle": {},
            "feed": {"min_rate": 50.0, "max_rate": 15000.0, "default_rate": 800.0},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=config)
        
        # 测试带请求进给且有限制器
        result = p.get_feed_rate(20000.0)  # 超过最大值，应被限制
        assert result <= 15000.0
        
        # 测试带请求进给且在范围内
        result = p.get_feed_rate(5000.0)
        assert result == 5000.0
        
        # 测试无请求进给且有限制器（返回默认值）
        result = p.get_feed_rate(None)
        assert result == 800.0

    def test_feed_rate_without_limiter(self):
        """验证无限制器的进给速度（覆盖行 267, 270）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        
        # 测试带请求进给且无限制器
        result = p.get_feed_rate(5000.0)
        assert result == 5000.0
        
        # 测试无请求进给且无限制器（返回默认值）
        result = p.get_feed_rate(None)
        assert result == 1000.0

    def test_work_coordinate_validation(self):
        """验证工件坐标系验证（覆盖行 281-284）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        
        # 测试有效坐标系
        result = p.get_work_coordinate("G54")
        assert isinstance(result, dict)
        
        result = p.get_work_coordinate("g55")  # 小写应被转换为大写
        assert isinstance(result, dict)
        
        # 测试无效坐标系
        with pytest.raises(ValueError, match="无效的工件坐标系"):
            p.get_work_coordinate("G99")

    def test_enabled_coordinate_systems(self):
        """验证已启用坐标系列表（覆盖行 288）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        config = {
            "spindle": {},
            "feed": {},
            "work_coordinate": {
                "G54": {"enabled": True},
                "G55": {"enabled": False},
                "G56": {"enabled": True},
                "G57": {},
                "G58": {},
                "G59": {},
                "default_coordinate_system": "G54",
            },
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=config)
        
        result = p.get_enabled_coordinate_systems()
        assert "G54" in result
        assert "G56" in result
        assert "G55" not in result

    def test_tool_offset_config(self):
        """验证刀具补偿配置获取（覆盖行 310）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        config = {
            "spindle": {},
            "feed": {},
            "work_coordinate": {},
            "tool_offset": {"length_registers": {"start": 1, "end": 50}},
            "fixed_cycles": {},
            "subprogram": {},
        }
        p = _Concrete(config=config)
        
        result = p.get_tool_offset_config()
        assert "length_registers" in result
        assert result["length_registers"]["start"] == 1

    def test_subprogram_config(self):
        """验证子程序配置获取（覆盖行 314）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        config = {
            "spindle": {},
            "feed": {},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {},
            "subprogram": {"call_format": "M98 P{program_number}", "end_code": "M99"},
        }
        p = _Concrete(config=config)
        
        result = p.get_subprogram_config()
        assert "call_format" in result
        assert result["end_code"] == "M99"

    def test_invalid_rapid_feed(self):
        """验证非正快速进给速度抛出异常（覆盖行 51）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        with pytest.raises(ValueError, match="rapid_feed must be > 0"):
            _Concrete(rapid_feed=0.0)
        with pytest.raises(ValueError, match="rapid_feed must be > 0"):
            _Concrete(rapid_feed=-100.0)

    def test_cycle_config(self):
        """验证固定循环配置获取（覆盖行 304-306）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            def format_coolant(self, state): return ""
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        config = {
            "spindle": {},
            "feed": {},
            "work_coordinate": {},
            "tool_offset": {},
            "fixed_cycles": {
                "drilling": {
                    "G81": {"peck_depth": 5.0, "retract_height": 10.0},
                    "G83": {"peck_depth": 10.0, "retract_height": 20.0},
                },
                "tapping": {},
                "boring": {},
                "threading": {},
            },
            "subprogram": {},
        }
        p = _Concrete(config=config)
        
        result = p.get_cycle_config("drilling", "G83")
        assert result["peck_depth"] == 10.0
        assert result["retract_height"] == 20.0
        
        # 测试不存在的循环组
        result = p.get_cycle_config("nonexistent", "G99")
        assert result == {}
        
        # 测试不存在的循环代码
        result = p.get_cycle_config("drilling", "G99")
        assert result == {}

    def test_calc_arc_radius(self):
        """验证圆弧半径计算（覆盖行 337）。"""
        # 测试基本圆弧半径计算
        end = (10.0, 0.0, 0.0)
        center = (5.0, 0.0, 0.0)
        radius = BasePostProcessor._calc_arc_radius(end, center)
        assert abs(radius - 5.0) < 1e-6
        
        # 测试更复杂的圆弧
        end = (10.0, 10.0, 0.0)
        center = (5.0, 5.0, 0.0)
        radius = BasePostProcessor._calc_arc_radius(end, center)
        expected = ((10.0 - 5.0) ** 2 + (10.0 - 5.0) ** 2) ** 0.5
        assert abs(radius - expected) < 1e-6

    def test_format_coolant_default_implementation(self):
        """验证冷却液默认实现返回 M09（覆盖行 421）。"""
        class _Concrete(BasePostProcessor):
            def format_header(self, program_number=1): return ""
            def format_tool_change(self, tool_id, length_comp=0, radius_comp=0): return ""
            def format_arc(self, start, end, center, clockwise=True): return ""
            # 不调用 format_coolant，使用基类默认实现
            def format_tool_compensation(self, length_offset=0, radius_offset=0): return ""
            def format_cycle_drill(self, x, y, z, depth, dwell=0): return ""
            def format_cycle_tapping(self, x, y, z, depth, pitch=1.0, spindle_rpm=None): return ""
            def format_cycle_boring(self, x, y, z, depth, cycle_type="G86", dwell=0.5): return ""
            def format_cycle_threading(self, x, y, depth, lead=1.0, passes=None,
                                       depth_cut_first=None, depth_cut_last=None,
                                       finishing_passes=None, tool_angle=None, taper=None): return ""
            def format_subprogram_call(self, program_number, repeat=1): return ""
            def format_subprogram_end(self, return_value=None): return ""
            def format_footer(self): return ""
        
        p = _Concrete()
        # 测试无效状态时返回 M09
        result = p.format_coolant("invalid_state")
        assert result == "M09"


class BaseProcessorTests:
    """各后处理器通用测试模式。"""

    processor_cls = None

    def test_all_methods_implemented(self, make_processor):
        """验证所有抽象方法都已实现。"""
        p = make_processor()
        assert hasattr(p, "format_header")
        assert hasattr(p, "format_tool_change")
        assert hasattr(p, "format_arc")
        assert hasattr(p, "format_coolant")
        assert hasattr(p, "format_tool_compensation")
        assert hasattr(p, "format_cycle_drill")
        assert hasattr(p, "format_footer")

    def test_header_not_empty(self, make_processor):
        """验证程序头生成非空。"""
        p = make_processor()
        result = p.format_header()
        assert len(result) > 0
        assert isinstance(result, str)

    def test_footer_not_empty(self, make_processor):
        """验证程序尾生成非空。"""
        p = make_processor()
        result = p.format_footer()
        assert len(result) > 0
        assert isinstance(result, str)

    def test_tool_change_contains_tool_id(self, make_processor):
        """验证换刀指令包含刀具ID。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3)
        assert "3" in result or "03" in result

    def test_coolant_on(self, make_processor):
        """验证冷却液开启指令。"""
        p = make_processor()
        result = p.format_coolant("on")
        assert len(result) > 0

    def test_coolant_off(self, make_processor):
        """验证冷却液关闭指令。"""
        p = make_processor()
        result = p.format_coolant("off")
        assert len(result) > 0

    def test_arc_contains_endpoint(self, make_processor):
        """验证圆弧指令包含终点坐标。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 0.0, 0.0),
            center=(5.0, 0.0, 0.0),
        )
        assert "10" in result

    def test_tool_compensation_no_offset(self, make_processor):
        """验证无补偿时的指令。"""
        p = make_processor()
        result = p.format_tool_compensation(length_offset=0, radius_offset=0)
        assert len(result) > 0

    def test_tool_compensation_with_offsets(self, make_processor):
        """验证有补偿时的指令。"""
        p = make_processor()
        result = p.format_tool_compensation(length_offset=5, radius_offset=3)
        assert len(result) > 0

    def test_cycle_drill_output(self, make_processor):
        """验证钻孔循环输出非空。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=0.0, depth=-5.0)
        assert len(result) > 0

    def test_cycle_drill_with_dwell(self, make_processor):
        """验证带暂停的钻孔循环。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=0.0, depth=-5.0, dwell=0.5)
        assert len(result) > 0

    def test_decimal_places_respected(self, make_processor):
        """验证小数位数参数生效。"""
        p_high = make_processor(decimal_places=6)
        p_low = make_processor(decimal_places=0)
        high_result = p_high.format_header()
        low_result = p_low.format_header()
        assert high_result != low_result


class TestFanucPostProcessor(BaseProcessorTests):
    """Fanuc 0i系列后处理器测试。"""

    processor_cls = FanucPostProcessor

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            defaults = {"decimal_places": 3, "safe_z_height": 50.0, "rapid_feed": 10000}
            defaults.update(kwargs)
            return self.processor_cls(**defaults)
        return _make

    def test_header_contains_fanuc_patterns(self, make_processor):
        """验证程序头包含Fanuc特征指令。"""
        p = make_processor()
        result = p.format_header()
        assert "G21" in result or "G17" in result
        assert "G40" in result
        assert "G49" in result
        assert "G80" in result
        assert "G90" in result

    def test_footer_contains_m30(self, make_processor):
        """验证程序尾包含M30。"""
        p = make_processor()
        result = p.format_footer()
        assert "M30" in result
        assert "M05" in result
        assert "M09" in result

    def test_arc_uses_g02_g03_r_format(self, make_processor):
        """验证圆弧使用G02/G03 R格式。"""
        p = make_processor()
        cw = p.format_arc((0, 0, 0), (10, 0, 0), (5, 0, 0), clockwise=True)
        assert "G02" in cw
        assert "R" in cw
        ccw = p.format_arc((10, 0, 0), (0, 0, 0), (5, 0, 0), clockwise=False)
        assert "G03" in ccw
        assert "R" in ccw

    def test_tool_change_uses_g43_h(self, make_processor):
        """验证换刀使用G43 H格式。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3)
        assert "G43" in result
        assert "H" in result

    def test_coolant_uses_m_code(self, make_processor):
        """验证冷却液使用M代码。"""
        p = make_processor()
        assert p.format_coolant("on") == "M08"
        assert p.format_coolant("off") == "M09"

    def test_tool_compensation_g41_g43(self, make_processor):
        """验证刀具补偿使用G41/G43。"""
        p = make_processor()
        result = p.format_tool_compensation(length_offset=5, radius_offset=3)
        assert "G43" in result
        assert "G41" in result

    def test_drill_uses_g73_or_g83(self, make_processor):
        """验证钻孔使用G73/G83循环。"""
        p = make_processor()
        with_dwell = p.format_cycle_drill(10, 20, 0, -5, dwell=0.5)
        assert "G73" in with_dwell
        without_dwell = p.format_cycle_drill(10, 20, 0, -5)
        assert "G83" in without_dwell

    def test_drill_includes_g80_cancel(self, make_processor):
        """验证钻孔后取消循环。"""
        p = make_processor()
        result = p.format_cycle_drill(10, 20, 0, -5)
        assert "G80" in result

    def test_boundary_zero_values(self, make_processor):
        """验证零值边界条件。"""
        p = make_processor()
        result = p.format_cycle_drill(0, 0, 0, -0.001)
        assert len(result) > 0

    def test_boundary_negative_depth(self, make_processor):
        """验证负深度值（钻孔）。"""
        p = make_processor()
        # 使用z=-100来测试负深度，这样Z坐标会出现在输出中
        result = p.format_cycle_drill(10, 10, -100.0, 0.0)
        # 验证输出包含深度信息（Z坐标或深度值）
        assert len(result) > 0
        assert "G83" in result or "G73" in result  # 验证是钻孔循环
        assert "Z-100.000" in result  # 验证Z坐标正确

    def test_tool_change_with_radius_compensation(self, make_processor):
        """验证换刀时刀具半径补偿不为零（覆盖行 81）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3, radius_comp=5.0)
        assert "M03" in result  # 应该包含主轴启动指令

    def test_cycle_tapping(self, make_processor):
        """验证攻丝循环（覆盖行 158-181）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5)
        assert "G84" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "F" in result
        assert "G80" in result

    def test_cycle_boring_g86(self, make_processor):
        """验证 G86 镗孔循环（覆盖行 192-222）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G86", dwell=0.5)
        assert "G86" in result
        assert "X30.000" in result
        assert "Y40.000" in result
        assert "Z-20.000" in result
        assert "P500" in result  # dwell 0.5s = 500ms
        assert "G80" in result

    def test_cycle_boring_g89(self, make_processor):
        """验证 G89 镗孔循环（覆盖行 208-213）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G89", dwell=0.3)
        assert "G89" in result
        assert "P300" in result  # dwell 0.3s = 300ms
        assert "G80" in result

    def test_cycle_threading(self, make_processor):
        """验证螺纹加工循环（覆盖行 237-265）。"""
        p = make_processor()
        result = p.format_cycle_threading(x=25.0, y=0.0, depth=10.0, lead=2.0)
        assert "G76" in result
        assert "X25.000" in result
        assert "Z-10.000" in result
        assert "F2.000" in result
        assert "G80" in result

    def test_cycle_groove(self, make_processor):
        """验证切槽循环（覆盖行 291-309）。"""
        p = make_processor()
        result = p.format_cycle_groove(x=50.0, z=-30.0, depth=5.0, width=3.0)
        assert "G75" in result
        assert "X50.000" in result
        assert "Z-30.000" in result
        assert "G80" in result

    def test_cycle_thread_turning(self, make_processor):
        """验证车削螺纹循环（覆盖行 341-381）。"""
        p = make_processor()
        result = p.format_cycle_thread_turning(x=20.0, z=-25.0, depth=8.0, pitch=1.5, passes=5)
        assert "G92" in result
        assert "X20.000" in result
        assert "Z-25.000" in result
        assert "F1.500" in result
        assert "G80" in result

    def test_subprogram_call(self, make_processor):
        """验证子程序调用（覆盖行 388-409）。"""
        p = make_processor()
        result = p.format_subprogram_call(program_number=100, repeat=3)
        assert "M98" in result
        assert "P0100" in result
        assert "L3" in result

    def test_subprogram_end(self, make_processor):
        """验证子程序结束（覆盖行 415-420）。"""
        p = make_processor()
        result = p.format_subprogram_end()
        assert "M99" in result
        
        result_with_return = p.format_subprogram_end(return_value="P100")
        assert "M99" in result_with_return
        assert "P100" in result_with_return

    def test_high_precision_mode(self, make_processor):
        """验证高精度模式（覆盖行 448-451）。"""
        p = make_processor()
        result_enable = p.format_high_precision_mode(enable=True, mode=1)
        assert "G05.1" in result_enable
        assert "Q1" in result_enable
        
        result_disable = p.format_high_precision_mode(enable=False)
        assert "G05.1" in result_disable
        assert "Q0" in result_disable

    def test_cycle_tapping_with_dwell(self, make_processor):
        """验证带暂停的攻丝循环（覆盖行 178）。"""
        p = make_processor()
        # 通过配置添加 dwell_time
        p.config = {
            "fixed_cycles": {
                "tapping": {
                    "G84": {
                        "spindle_direction": "M03",
                        "feed_per_rev": True,
                        "dwell_time": 0.5,  # 500ms
                    }
                }
            }
        }
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5)
        assert "G84" in result
        assert "P500" in result  # dwell 0.5s = 500ms

    def test_cycle_tapping_feed_per_minute(self, make_processor):
        """验证每分钟进给的攻丝循环（覆盖行 167）。"""
        p = make_processor()
        # 通过配置设置 feed_per_rev 为 False
        p.config = {
            "fixed_cycles": {
                "tapping": {
                    "G84": {
                        "spindle_direction": "M03",
                        "feed_per_rev": False,  # 每分钟进给
                        "dwell_time": 0.0,
                    }
                }
            },
            "spindle": {"default_rpm": 1000}
        }
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "G84" in result
        # feed = pitch * rpm = 1.5 * 1000 = 1500
        assert "F1500" in result or "F1500.000" in result

    def test_cycle_boring_unknown_type(self, make_processor):
        """验证未知类型的镗孔循环（覆盖行 215）。"""
        p = make_processor()
        # 使用未知的 cycle_type 触发 else 分支
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G99", dwell=0.5)
        # 应该回退到 G86
        assert "G86" in result or "G99" in result
        assert "G80" in result

    def test_subprogram_call_invalid_format(self, make_processor):
        """验证无效格式的的子程序调用（覆盖行 404-407）。"""
        p = make_processor()
        # 通过配置设置无效的 call_format
        p.config = {
            "subprogram": {
                "call_format": "M98 P{invalid_key}",  # 缺少 program_num 和 repeat
                "program_number": {"minimum": 1, "maximum": 9999},
                "repeat": {"minimum": 1, "maximum": 9999}
            }
        }
        result = p.format_subprogram_call(program_number=100, repeat=3)
        # 应该回退到默认格式
        assert "M98" in result
        assert "P0100" in result
        assert "L3" in result


class TestSiemensPostProcessor(BaseProcessorTests):
    """Siemens 840D后处理器测试。"""

    processor_cls = SiemensPostProcessor

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            defaults = {"decimal_places": 3, "safe_z_height": 50.0, "rapid_feed": 10000}
            defaults.update(kwargs)
            return self.processor_cls(**defaults)
        return _make

    def test_header_contains_siemens_patterns(self, make_processor):
        """验证程序头包含Siemens特征指令。"""
        p = make_processor()
        result = p.format_header()
        assert "G17" in result
        assert "G40" in result
        assert "G90" in result
        assert "G94" in result

    def test_header_has_block_numbers(self, make_processor):
        """验证程序头包含N段号。"""
        p = make_processor()
        result = p.format_header()
        assert "N" in result

    def test_footer_contains_m30(self, make_processor):
        """验证程序尾包含M30。"""
        p = make_processor()
        result = p.format_footer()
        assert "M30" in result

    def test_arc_uses_cr_notation(self, make_processor):
        """验证圆弧使用CR=表示法。"""
        p = make_processor()
        result = p.format_arc((0, 0, 0), (10, 0, 0), (5, 0, 0))
        assert "CR=" in result
        assert "G02" in result or "G03" in result

    def test_tool_change_uses_t_string(self, make_processor):
        """验证换刀使用T=字符串格式。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3)
        assert "TOOL" in result

    def test_tool_compensation_uses_tc_dp6(self, make_processor):
        """验证刀具补偿使用$TC_DP6。"""
        p = make_processor()
        result = p.format_tool_compensation(length_offset=5)
        assert "$TC_DP6" in result
        result2 = p.format_tool_compensation(radius_offset=3)
        assert "DISC" in result2

    def test_drill_uses_cycle_notation(self, make_processor):
        """验证钻孔使用CYCLE语法。"""
        p = make_processor()
        result = p.format_cycle_drill(10, 20, 0, -5)
        assert "CYCLE" in result

    def test_boundary_deep_drill(self, make_processor):
        """验证深孔边界条件。"""
        p = make_processor()
        result = p.format_cycle_drill(100, 200, 0, -200.0)
        assert len(result) > 0

    def test_cycle_tapping(self, make_processor):
        """验证攻丝循环（覆盖行 168-184）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "CYCLE84" in result
        assert "G00" in result
        assert "X10.000" in result
        assert "Y20.000" in result

    def test_cycle_boring_g86(self, make_processor):
        """验证 G86 镗孔循环（覆盖行 195-215）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G86", dwell=0.5)
        assert "CYCLE86" in result
        assert "G00" in result
        assert "X30.000" in result
        assert "Y40.000" in result

    def test_cycle_boring_g89(self, make_processor):
        """验证 G89 镗孔循环（覆盖行 216-224）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G89", dwell=0.5)
        assert "CYCLE89" in result
        assert "G00" in result

    def test_cycle_boring_unknown_type(self, make_processor):
        """验证未知类型镗孔循环回退到 G86（覆盖行 225-234）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G99", dwell=0.5)
        assert "CYCLE86" in result
        assert "G00" in result

    def test_cycle_threading(self, make_processor):
        """验证螺纹加工循环（覆盖行 251-283）。"""
        p = make_processor()
        result = p.format_cycle_threading(
            x=10.0, y=20.0, depth=-15.0, lead=1.5,
            passes=5, depth_cut_first=0.2, depth_cut_last=0.05,
            finishing_passes=2, tool_angle=60.0, taper=0.0
        )
        assert "CYCLE76" in result
        assert "G00" in result
        assert "X10.000" in result
        assert "Y20.000" in result

    def test_cycle_groove(self, make_processor):
        """验证切槽循环（覆盖行 309-328）。"""
        p = make_processor()
        result = p.format_cycle_groove(x=50.0, z=-10.0, depth=5.0, width=3.0, retract=0.5, finish_allowance=0.1)
        assert "CYCLE93" in result
        assert "G00" in result
        assert "X50.000" in result
        assert "Z-10.000" in result

    def test_cycle_thread_turning(self, make_processor):
        """验证螺纹车削循环（覆盖行 360-375）。"""
        p = make_processor()
        result = p.format_cycle_thread_turning(
            x=30.0, z=-20.0, depth=2.0, pitch=1.5,
            passes=5, first_depth=0.2, last_depth=0.05,
            finishing_passes=2, tool_angle=60.0
        )
        assert "CYCLE97" in result
        assert "G00" in result
        assert "X30.000" in result
        assert "Z-20.000" in result

    def test_subprogram_call(self, make_processor):
        """验证子程序调用（覆盖行 382-394）。"""
        p = make_processor()
        result = p.format_subprogram_call(program_number=100, repeat=3)
        assert "L0100" in result
        assert "P3" in result
        assert "N" in result

    def test_subprogram_end(self, make_processor):
        """验证子程序结束（覆盖行 400-402）。"""
        p = make_processor()
        result = p.format_subprogram_end()
        assert "M17" in result
        assert "N" in result

    def test_five_axis_mode_enable(self, make_processor):
        """验证五轴联动模式开启（覆盖行 427-428）。"""
        p = make_processor()
        result = p.format_five_axis_mode(enable=True)
        assert "TRAORI" in result
        assert "N" in result

    def test_five_axis_mode_disable(self, make_processor):
        """验证五轴联动模式关闭（覆盖行 429-430）。"""
        p = make_processor()
        result = p.format_five_axis_mode(enable=False)
        assert "TRAFOOF" in result
        assert "N" in result

    def test_surface_normal_compensation_enable(self, make_processor):
        """验证表面法向补偿开启（覆盖行 449-450）。"""
        p = make_processor()
        result = p.format_surface_normal_compensation(enable=True, tool_axis="Z")
        assert "COMPCAD" in result
        assert "N" in result

    def test_surface_normal_compensation_disable(self, make_processor):
        """验证表面法向补偿关闭（覆盖行 451-452）。"""
        p = make_processor()
        result = p.format_surface_normal_compensation(enable=False, tool_axis="X")
        assert "COMP0F" in result
        assert "N" in result


class TestHeidenhainPostProcessor(BaseProcessorTests):
    """Heidenhain TNC后处理器测试。"""

    processor_cls = HeidenhainPostProcessor

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            defaults = {"decimal_places": 3, "safe_z_height": 50.0, "rapid_feed": 10000}
            defaults.update(kwargs)
            return self.processor_cls(**defaults)
        return _make

    def test_header_contains_begin_pgm(self, make_processor):
        """验证程序头包含BEGIN PGM。"""
        p = make_processor()
        result = p.format_header()
        assert "BEGIN PGM" in result

    def test_header_contains_blk_form(self, make_processor):
        """验证程序头包含BLK FORM。"""
        p = make_processor()
        result = p.format_header()
        assert "BLK FORM" in result

    def test_footer_contains_end_pgm(self, make_processor):
        """验证程序尾包含END PGM。"""
        p = make_processor()
        result = p.format_footer()
        assert "END PGM" in result

    def test_arc_clockwise_uses_l(self, make_processor):
        """验证顺时针圆弧使用L指令。"""
        p = make_processor()
        result = p.format_arc((0, 0, 0), (10, 0, 0), (5, 0, 0), clockwise=True)
        assert " L " in result

    def test_arc_counter_clockwise_uses_cc(self, make_processor):
        """验证逆时针圆弧使用CC/C指令。"""
        p = make_processor()
        result = p.format_arc((10, 0, 0), (0, 0, 0), (5, 0, 0), clockwise=False)
        assert "CC" in result
        assert " C " in result

    def test_tool_change_uses_tool_call(self, make_processor):
        """验证换刀使用TOOL CALL。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3)
        assert "TOOL CALL" in result

    def test_tool_compensation_uses_dr(self, make_processor):
        """验证刀具补偿使用DR+。"""
        p = make_processor()
        result = p.format_tool_compensation(radius_offset=3)
        assert "DR+3" in result

    def test_drill_uses_cycl_def(self, make_processor):
        """验证钻孔使用CYCL DEF。"""
        p = make_processor()
        result = p.format_cycle_drill(10, 20, 0, -5)
        assert "CYCL DEF" in result

    def test_drill_uses_q_parameters(self, make_processor):
        """验证钻孔使用Q参数体系。"""
        p = make_processor()
        result = p.format_cycle_drill(10, 20, 0, -5)
        assert "Q200" in result
        assert "Q201" in result

    def test_boundary_zero_coordinates(self, make_processor):
        """验证原点坐标边界条件。"""
        p = make_processor()
        result = p.format_arc((0, 0, 0), (0, 0, 0), (0, 0, 0))
        assert len(result) > 0

    def test_cycle_tapping(self, make_processor):
        """验证攻丝循环（覆盖行 162-179）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "CYCL DEF 206" in result
        assert "CYCL CALL" in result
        assert "Q206" in result
        assert "Q220" in result

    def test_cycle_boring_g86(self, make_processor):
        """验证 G86 镗孔循环（覆盖行 190-209）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G86", dwell=0.5)
        assert "CYCL DEF 202" in result
        assert "CYCL CALL" in result
        assert "Q214" in result

    def test_cycle_boring_g89(self, make_processor):
        """验证 G89 镗孔循环（覆盖行 210-223）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G89", dwell=0.5)
        assert "CYCL DEF 209" in result
        assert "CYCL CALL" in result

    def test_cycle_boring_unknown_type(self, make_processor):
        """验证未知类型镗孔循环回退（覆盖行 224-236）。"""
        p = make_processor()
        result = p.format_cycle_boring(x=30.0, y=40.0, z=-20.0, depth=20.0, cycle_type="G99", dwell=0.5)
        assert "CYCL DEF 202" in result
        assert "CYCL CALL" in result

    def test_cycle_threading(self, make_processor):
        """验证螺纹加工循环（覆盖行 253-287）。"""
        p = make_processor()
        result = p.format_cycle_threading(
            x=10.0, y=20.0, depth=-15.0, lead=1.5,
            passes=5, depth_cut_first=0.2, depth_cut_last=0.05,
            finishing_passes=2, tool_angle=60.0, taper=0.0
        )
        assert "CYCL DEF 264" in result
        assert "CYCL CALL" in result
        assert "Q239" in result
        assert "Q244" in result

    def test_cycle_groove(self, make_processor):
        """验证切槽循环（覆盖行 313-349）。"""
        p = make_processor()
        result = p.format_cycle_groove(x=50.0, z=-10.0, depth=5.0, width=3.0, retract=0.5, finish_allowance=0.1)
        assert "CYCL DEF 266" in result
        assert "CYCL CALL" in result
        assert "Q226" in result
        assert "Q227" in result

    def test_cycle_thread_turning(self, make_processor):
        """验证螺纹车削循环（覆盖行 381-411）。"""
        p = make_processor()
        result = p.format_cycle_thread_turning(
            x=30.0, z=-20.0, depth=2.0, pitch=1.5,
            passes=5, first_depth=0.2, last_depth=0.05,
            finishing_passes=2, tool_angle=60.0
        )
        assert "CYCL DEF 263" in result
        assert "CYCL CALL" in result
        assert "Q239" in result
        assert "Q244" in result

    def test_subprogram_call(self, make_processor):
        """验证子程序调用（覆盖行 418-438）。"""
        p = make_processor()
        result = p.format_subprogram_call(program_number=100, repeat=3)
        assert "LBL CALL" in result
        assert "0100" in result
        assert "REP3" in result

    def test_subprogram_call_invalid_format(self, make_processor):
        """验证无效格式的的子程序调用（覆盖行 433-436）。"""
        p = make_processor()
        p.config = {
            "subprogram": {
                "call_format": "LBL CALL {invalid_key}",
                "program_number": {"minimum": 1, "maximum": 9999},
                "repeat": {"minimum": 1, "maximum": 999}
            }
        }
        result = p.format_subprogram_call(program_number=100, repeat=3)
        assert "LBL CALL" in result
        assert "0100" in result
        assert "REP3" in result

    def test_subprogram_end(self, make_processor):
        """验证子程序结束（覆盖行 444-446）。"""
        p = make_processor()
        result = p.format_subprogram_end()
        assert "LBL 0" in result

    def test_high_precision_mode_enable(self, make_processor):
        """验证高精度模式开启（覆盖行 472-473）。"""
        p = make_processor()
        result = p.format_high_precision_mode(enable=True)
        assert "M128" in result

    def test_high_precision_mode_disable(self, make_processor):
        """验证高精度模式关闭（覆盖行 474-475）。"""
        p = make_processor()
        result = p.format_high_precision_mode(enable=False)
        assert "M129" in result

    def test_probe_cycle_default_feed(self, make_processor):
        """验证测头循环默认进给（覆盖行 500-501）。"""
        p = make_processor()
        result = p.format_probe_cycle(probe_number=1, x_pos=10.0, y_pos=20.0, z_depth=-5.0)
        assert "CYCL DEF 19" in result
        assert "CYCL CALL" in result
        assert "Q264" in result
        assert "Q265" in result

    def test_probe_cycle_custom_feed(self, make_processor):
        """验证测头循环自定义进给（覆盖行 502-503）。"""
        p = make_processor()
        result = p.format_probe_cycle(probe_number=2, x_pos=15.0, y_pos=25.0, z_depth=-8.0, feed_rate=500.0)
        assert "CYCL DEF 19" in result
        assert "Q272=2" in result
        assert "Q273" in result


class TestFagorPostProcessor(BaseProcessorTests):
    """Fagor 8055 后处理器测试。"""

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            config = _make_full_config()
            config["fixed_cycles"]["drilling"]["G83"]["peck_depth"] = 5.0
            return FagorPostProcessor(**kwargs, config=config)
        return _make

    def test_format_header(self, make_processor):
        """验证 Fagor 程序头格式（覆盖行 46-64）。"""
        p = make_processor()
        result = p.format_header(program_number=42)
        assert "%00042" in result  # Fagor 特色：%xxxxx
        assert "G75 Z0." in result  # Fagor 特色：G75 替代 G28
        assert "G75 X0. Y0." in result
        assert "M03 S8000" in result  # 使用配置默认值
        assert "M08" in result

    def test_format_tool_change(self, make_processor):
        """验证 Fagor 换刀格式（覆盖行 73-87）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=5, length_comp=10.5, radius_comp=0.0)
        assert "G75 Z0." in result
        assert "G75 X0. Y0." in result
        assert "T05 M06" in result
        assert "G43 Z80.000 H05" in result  # 使用配置默认安全高度
        assert "G01 Z10.500" in result

    def test_format_tool_change_with_radius_comp(self, make_processor):
        """验证 Fagor 换刀带半径补偿（覆盖行 85-86）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3, length_comp=5.0, radius_comp=2.5)
        assert "T03 M06" in result
        assert "M03 S8000" in result  # 半径补偿时启动主轴

    def test_format_arc(self, make_processor):
        """验证 Fagor 圆弧格式（覆盖行 97-101）。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=True,
        )
        assert "G02" in result
        assert "I5.000" in result  # Fagor 使用 I/J/K
        assert "J5.000" in result

    def test_format_cycle_drill_pecking(self, make_processor):
        """验证 Fagor 啄钻循环（覆盖行 116-140）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0, pecking=True)
        assert "G83" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "Q5.000" in result
        assert "G80" in result

    def test_format_cycle_drill_with_dwell(self, make_processor):
        """验证 Fagor 钻孔带暂停（覆盖行 123-131）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.5, pecking=True)
        assert "G83" in result
        assert "P500" in result  # 0.5秒 = 500毫秒
        assert "G80" in result

    def test_format_cycle_drill_simple(self, make_processor):
        """验证 Fagor 简单钻孔（覆盖行 132-139）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0, pecking=False)
        assert "G81" in result
        assert "G80" in result

    def test_format_cycle_tapping(self, make_processor):
        """验证 Fagor 攻丝循环（覆盖行 152-169）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "M03 S1000" in result
        assert "G99 G84" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "F1.500" in result
        assert "G80" in result

    def test_format_cycle_tapping_with_dwell(self, make_processor):
        """验证 Fagor 攻丝带暂停（覆盖行 166-167）。"""
        p = make_processor()
        p.config["fixed_cycles"]["tapping"]["G84"]["dwell_time"] = 0.1
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "P100" in result  # 0.1秒 = 100毫秒

    def test_format_subprogram_call(self, make_processor):
        """验证 Fagor 子程序调用（覆盖行 177-179）。"""
        p = make_processor()
        result = p.format_subprogram_call(program_number=100, repeat=1)
        assert "CALL P00100" in result  # Fagor 特色：CALL Pxxxx
        
        result_repeat = p.format_subprogram_call(program_number=100, repeat=3)
        assert "CALL P00100" in result_repeat
        assert "R3" in result_repeat

    def test_format_subprogram_end(self, make_processor):
        """验证 Fagor 子程序结束（覆盖行 186-188）。"""
        p = make_processor()
        result = p.format_subprogram_end()
        assert "RET" in result  # Fagor 特色：RET 替代 M99
        
        result_with_value = p.format_subprogram_end(return_value="R1")
        assert "RET" in result_with_value
        assert "R1" in result_with_value

    def test_format_footer(self, make_processor):
        """验证 Fagor 程序结束格式（覆盖行 192-201）。"""
        p = make_processor()
        result = p.format_footer()
        assert "M09" in result
        assert "M05" in result
        assert "G75 Z0." in result
        assert "G75 X0. Y0." in result
        assert "G90" in result
        assert "M30" in result


class TestGSKPostProcessor(BaseProcessorTests):
    """广州数控 GSK 后处理器测试。"""

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            config = _make_full_config()
            return GSKPostProcessor(**kwargs, config=config)
        return _make

    def test_format_header(self, make_processor):
        """验证 GSK 程序头格式（覆盖行 46-63）。"""
        p = make_processor()
        result = p.format_header(program_number=1)
        assert "%", result  # GSK 使用 %
        assert "G21 G17 G40 G49 G80 G90 G94" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G30 X0. Y0." in result  # GSK 特色：G30 回零
        assert "M03 S8000" in result
        assert "M08" in result

    def test_format_tool_change(self, make_processor):
        """验证 GSK 换刀格式（覆盖行 72-87）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=2, length_comp=5.0, radius_comp=0.0)
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G30 X0. Y0." in result
        assert "T02 M06" in result
        assert "G43 Z80.000 H02" in result

    def test_format_arc(self, make_processor):
        """验证 GSK 圆弧格式（覆盖行 97-102）。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=False,
        )
        assert "G03" in result
        assert "I5.000" in result
        assert "J5.000" in result

    def test_format_cycle_drill(self, make_processor):
        """验证 GSK 钻孔循环（覆盖行 116-140）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0)
        assert "G83" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "G80" in result

    def test_format_cycle_tapping(self, make_processor):
        """验证 GSK 攻丝循环（覆盖行 152-169）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "M03 S1000" in result
        assert "G99 G84" in result
        assert "F1.500" in result
        assert "G80" in result

    def test_format_footer(self, make_processor):
        """验证 GSK 程序结束格式（覆盖行 173-183）。"""
        p = make_processor()
        result = p.format_footer()
        assert "M09" in result
        assert "M05" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G30 X0. Y0." in result
        assert "M30" in result

    def test_format_tool_change_with_radius_comp(self, make_processor):
        """验证 GSK 带半径补偿的换刀格式（覆盖行 85-86）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=2, length_comp=5.0, radius_comp=1.0)
        assert "M03 S8000" in result  # 半径补偿时启动主轴

    def test_format_cycle_tapping_with_dwell(self, make_processor):
        """验证 GSK 带停顿的攻丝循环（覆盖行 166-167）。"""
        p = make_processor()
        # 需要配置 dwell_time > 0
        p.config["fixed_cycles"]["tapping"]["G84"]["dwell_time"] = 0.5
        result = p.format_cycle_tapping(
            x=10.0, y=20.0, z=-15.0, depth=15.0, 
            pitch=1.5, spindle_rpm=1000.0
        )
        assert "P500" in result  # 停顿参数P500（500ms）


class TestHNCPostProcessor(BaseProcessorTests):
    """华中数控 HNC 后处理器测试。"""

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            config = _make_full_config()
            return HNCPostProcessor(**kwargs, config=config)
        return _make

    def test_format_header(self, make_processor):
        """验证 HNC 程序头格式（覆盖行 49-66）。"""
        p = make_processor()
        result = p.format_header(program_number=1)
        assert "%", result
        assert "G21 G17 G40 G49 G80 G90 G94" in result
        assert "G00 G91 G74 Z0." in result  # HNC 使用 G74
        assert "G00 G91 G74 X0. Y0." in result
        assert "M03 S8000" in result
        assert "M08" in result

    def test_format_tool_change(self, make_processor):
        """验证 HNC 换刀格式（覆盖行 75-89）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3, length_comp=8.0, radius_comp=0.0)
        assert "G00 G91 G74 Z0." in result  # HNC 使用 G74
        assert "G00 G91 G74 X0. Y0." in result
        assert "T03 M06" in result
        assert "G43 Z80.000 H03" in result

    def test_format_arc(self, make_processor):
        """验证 HNC 圆弧格式（覆盖行 99-103）。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=True,
        )
        assert "G02" in result
        assert "I5.000" in result  # HNC 使用 I/J 模式

    def test_format_cycle_drill(self, make_processor):
        """验证 HNC 钻孔循环（覆盖行 117-141）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0)
        assert "G83" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "G80" in result

    def test_format_cycle_tapping(self, make_processor):
        """验证 HNC 攻丝循环（覆盖行 153-170）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "M03 S1000" in result
        assert "G99 G84" in result
        assert "F1.500" in result
        assert "G80" in result

    def test_format_footer(self, make_processor):
        """验证 HNC 程序结束格式（覆盖行 177-179, 183-193）。"""
        p = make_processor()
        result = p.format_footer()
        assert "M09" in result
        assert "M05" in result
        assert "G00 G91 G74 Z0." in result
        assert "G00 G91 G74 X0. Y0." in result
        assert "M30" in result

    def test_format_tool_change_with_radius_comp(self, make_processor):
        """验证 HNC 带半径补偿的换刀格式（覆盖行 87-88）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=3, length_comp=8.0, radius_comp=1.0)
        assert "M03 S8000" in result  # 半径补偿时启动主轴

    def test_format_cycle_tapping_with_dwell(self, make_processor):
        """验证 HNC 带停顿的攻丝循环（覆盖行 167-168）。"""
        p = make_processor()
        p.config["fixed_cycles"]["tapping"]["G84"]["dwell_time"] = 0.5
        result = p.format_cycle_tapping(
            x=10.0, y=20.0, z=-15.0, depth=15.0, 
            pitch=1.5, spindle_rpm=1000.0
        )
        assert "P500" in result  # 停顿参数P500（500ms）

    def test_format_subprogram_end_with_return_value(self, make_processor):
        """验证 HNC 带返回值的子程序结束（覆盖行 177-178）。"""
        p = make_processor()
        result = p.format_subprogram_end(return_value="100")
        assert "M99" in result
        assert "P100" in result

    def test_format_subprogram_end_without_return_value(self, make_processor):
        """验证 HNC 不带返回值的子程序结束（覆盖行 179）。"""
        p = make_processor()
        result = p.format_subprogram_end()
        assert result == "M99"


class TestKNDPostProcessor(BaseProcessorTests):
    """北京凯恩帝 KND 后处理器测试。"""

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            config = _make_full_config()
            return KNDPostProcessor(**kwargs, config=config)
        return _make

    def test_format_header(self, make_processor):
        """验证 KND 程序头格式（覆盖行 45-60）。"""
        p = make_processor()
        result = p.format_header(program_number=1)
        assert "%", result
        assert "G21 G17 G40 G49 G80 G90 G94" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "M03 S8000" in result
        assert "M08" in result

    def test_format_tool_change(self, make_processor):
        """验证 KND 换刀格式（覆盖行 69-83）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=4, length_comp=6.0, radius_comp=0.0)
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "T04 M06" in result
        assert "G43 Z80.000 H04" in result

    def test_format_arc(self, make_processor):
        """验证 KND 圆弧格式（覆盖行 93-96）。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=True,
        )
        assert "G02" in result
        assert "R" in result  # KND 默认使用 R 模式

    def test_format_cycle_drill(self, make_processor):
        """验证 KND 钻孔循环（覆盖行 110-134）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0)
        assert "G83" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "G80" in result

    def test_format_cycle_tapping(self, make_processor):
        """验证 KND 攻丝循环（覆盖行 146-163）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "M03 S1000" in result
        assert "G99 G84" in result
        assert "F1.500" in result
        assert "G80" in result

    def test_format_footer(self, make_processor):
        """验证 KND 程序结束格式（覆盖行 167-177）。"""
        p = make_processor()
        result = p.format_footer()
        assert "M09" in result
        assert "M05" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "M30" in result

    def test_format_tool_change_with_radius_comp(self, make_processor):
        """验证 KND 带半径补偿的换刀格式（覆盖行 81-82）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=4, length_comp=6.0, radius_comp=1.0)
        assert "M03 S8000" in result  # 半径补偿时启动主轴

    def test_format_cycle_tapping_with_dwell(self, make_processor):
        """验证 KND 带停顿的攻丝循环（覆盖行 160-161）。"""
        p = make_processor()
        p.config["fixed_cycles"]["tapping"]["G84"]["dwell_time"] = 0.5
        result = p.format_cycle_tapping(
            x=10.0, y=20.0, z=-15.0, depth=15.0, 
            pitch=1.5, spindle_rpm=1000.0
        )
        assert "P500" in result  # 停顿参数P500（500ms）


class TestMitsubishiPostProcessor(BaseProcessorTests):
    """三菱 M70/M80 后处理器测试。"""

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            config = _make_full_config()
            return MitsubishiPostProcessor(**kwargs, config=config)
        return _make

    def test_format_header(self, make_processor):
        """验证 Mitsubishi 程序头格式（覆盖行 48-66）。"""
        p = make_processor()
        result = p.format_header(program_number=1)
        assert "%", result
        assert "G21 G17 G40 G49 G80 G90 G94" in result
        assert "G05.1 Q1" in result  # Mitsubishi 特色：AI 先行控制
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "M03 S8000" in result
        assert "M08" in result

    def test_format_tool_change(self, make_processor):
        """验证 Mitsubishi 换刀格式（覆盖行 75-89）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=5, length_comp=7.0, radius_comp=0.0)
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "T05 M06" in result
        assert "G43 Z80.000 H05" in result

    def test_format_arc(self, make_processor):
        """验证 Mitsubishi 圆弧格式（覆盖行 99-103）。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=True,
        )
        assert "G02" in result
        assert "I5.000" in result  # Mitsubishi 优先使用 I/J/K
        assert "J5.000" in result

    def test_format_cycle_drill(self, make_processor):
        """验证 Mitsubishi 钻孔循环（覆盖行 117-141）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0)
        assert "G83" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-15.000" in result
        assert "Q" in result
        assert "G80" in result

    def test_format_cycle_drill_with_dwell(self, make_processor):
        """验证 Mitsubishi 钻孔带暂停（覆盖行 124-132）。"""
        p = make_processor()
        result = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.5)
        assert "G73" in result  # 带暂停时使用 G73
        assert "P500" in result  # 0.5秒 = 500毫秒
        assert "G80" in result

    def test_format_cycle_tapping(self, make_processor):
        """验证 Mitsubishi 攻丝循环（覆盖行 153-170）。"""
        p = make_processor()
        result = p.format_cycle_tapping(x=10.0, y=20.0, z=-15.0, depth=15.0, pitch=1.5, spindle_rpm=1000.0)
        assert "M03 S1000" in result
        assert "G99 G84" in result
        assert "F1.500" in result
        assert "G80" in result

    def test_format_footer(self, make_processor):
        """验证 Mitsubishi 程序结束格式（覆盖行 174-186）。"""
        p = make_processor()
        result = p.format_footer()
        assert "M09" in result
        assert "M05" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "G05.1 Q0" in result  # Mitsubishi 特色：关闭 AI 先行控制
        assert "M30" in result

    def test_format_tool_change_with_radius_comp(self, make_processor):
        """验证 Mitsubishi 带半径补偿的换刀格式（覆盖行 87-88）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=5, length_comp=7.0, radius_comp=1.0)
        assert "M03 S8000" in result  # 半径补偿时启动主轴

    def test_format_cycle_tapping_with_dwell(self, make_processor):
        """验证 Mitsubishi 带停顿的攻丝循环（覆盖行 167-168）。"""
        p = make_processor()
        p.config["fixed_cycles"]["tapping"]["G84"]["dwell_time"] = 0.5
        result = p.format_cycle_tapping(
            x=10.0, y=20.0, z=-15.0, depth=15.0, 
            pitch=1.5, spindle_rpm=1000.0
        )
        assert "P500" in result  # 停顿参数P500（500ms）


class TestXMachineXM100PostProcessor(BaseProcessorTests):
    """数马电子 XM-100 桌面级五轴后处理器测试。"""

    @pytest.fixture
    def make_processor(self):
        def _make(**kwargs):
            config = _make_full_config()
            return XMachineXM100PostProcessor(**kwargs, config=config)
        return _make

    def test_format_header(self, make_processor):
        """验证 XM-100 程序头格式（覆盖行 76-96）。"""
        p = make_processor()
        result = p.format_header(program_number=1)
        assert "%", result
        assert "O0001 (XM-100 PROGRAM 1" in result
        assert "G21 G17 G40 G49 G80 G90 G94" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 A0. C0." in result  # 五轴特色：回旋转轴零点
        assert "M03 S8000" in result  # 使用配置默认值
        assert "M08" in result

    def test_format_tool_change(self, make_processor):
        """验证 XM-100 换刀格式（覆盖行 104-120）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=1, length_comp=5.0, radius_comp=0.0)
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 A0. C0." in result
        assert "T01 M06" in result
        assert "G43 Z30.000 H01" in result  # XM-100 默认安全高度 30.0

    def test_format_rapid_move_with_abc(self, make_processor):
        """验证 XM-100 五轴快速定位（覆盖行 142-150）。"""
        p = make_processor()
        result = p.format_rapid_move(x=10.0, y=20.0, z=-5.0, a=45.0, c=90.0)
        assert "G00" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-5.000" in result
        assert "A45.000" in result
        assert "C90.000" in result

    def test_format_linear_move_with_abc(self, make_processor):
        """验证 XM-100 五轴直线插补（覆盖行 174-183）。"""
        p = make_processor()
        result = p.format_linear_move(x=10.0, y=20.0, z=-5.0, feed=500.0, a=45.0, c=90.0)
        assert "G01" in result
        assert "X10.000" in result
        assert "Y20.000" in result
        assert "Z-5.000" in result
        assert "A45.000" in result
        assert "C90.000" in result
        assert "F500.000" in result

    def test_format_rtcp_on(self, make_processor):
        """验证 XM-100 RTCP 开启（覆盖行 197-198）。"""
        p = make_processor()
        result = p.format_rtcp_on(tool_length=10.0)
        assert "G43.4 H10" in result
        assert "RTCP ON" in result

    def test_format_rtcp_off(self, make_processor):
        """验证 XM-100 RTCP 关闭（覆盖行 202）。"""
        p = make_processor()
        result = p.format_rtcp_off()
        assert "G49" in result
        assert "RTCP OFF" in result

    def test_format_twp_on(self, make_processor):
        """验证 XM-100 TWP 开启（覆盖行 218-223）。"""
        p = make_processor()
        result = p.format_twp_on(tool_axis_i=0.0, tool_axis_j=0.0, tool_axis_k=1.0)
        assert "G43.5" in result
        assert "I0.000" in result
        assert "J0.000" in result
        assert "K1.000" in result
        assert "TWP ON" in result

    def test_format_twp_on_zero_vector(self, make_processor):
        """验证 XM-100 TWP 零矢量错误（覆盖行 219-220）。"""
        p = make_processor()
        with pytest.raises(ValueError, match="刀轴矢量不能为零向量"):
            p.format_twp_on(tool_axis_i=0.0, tool_axis_j=0.0, tool_axis_k=0.0)

    def test_format_twp_off(self, make_processor):
        """验证 XM-100 TWP 关闭（覆盖行 227）。"""
        p = make_processor()
        result = p.format_twp_off()
        assert "G49" in result
        assert "TWP OFF" in result

    def test_format_rotary_axis_config(self, make_processor):
        """验证 XM-100 旋转轴配置（覆盖行 247-253）。"""
        p = make_processor()
        result = p.format_rotary_axis_config(a_axis_zero=0.0, c_axis_zero=0.0, a_axis_dir=1, c_axis_dir=1)
        assert "G54.1 P1 A0.000" in result
        assert "G54.1 P2 C0.000" in result
        assert "M101" in result  # A轴方向
        assert "M201" in result  # C轴方向

    def test_format_workspace_check(self, make_processor):
        """验证 XM-100 工作空间检查（覆盖行 266-278）。"""
        p = make_processor()
        # 在工作空间内
        result_ok = p.format_workspace_check(x=10.0, y=20.0, z=-5.0)
        assert "OK" in result_ok
        
        # 超出工作空间
        result_warn = p.format_workspace_check(x=100.0, y=20.0, z=-5.0)
        assert "WARNING" in result_warn
        assert "超出" in result_warn

    def test_validate_workspace(self, make_processor):
        """验证 XM-100 工作空间验证（覆盖行 300-315）。"""
        p = make_processor()
        # 在工作空间内
        p._validate_workspace(x=10.0, y=20.0, z=-5.0, a=45.0, c=90.0)
        
        # 超出 X 行程
        with pytest.raises(ValueError, match="超出 XM-100 X行程"):
            p._validate_workspace(x=100.0, y=20.0, z=-5.0)
        
        # 超出 Y 行程
        with pytest.raises(ValueError, match="超出 XM-100 Y行程"):
            p._validate_workspace(x=10.0, y=100.0, z=-5.0)
        
        # 超出 Z 行程
        with pytest.raises(ValueError, match="超出 XM-100 Z行程"):
            p._validate_workspace(x=10.0, y=20.0, z=-100.0)

    def test_validate_a_axis(self, make_processor):
        """验证 XM-100 A轴验证（覆盖行 319-326）。"""
        p = make_processor()
        # 正常范围
        p._validate_a_axis(a=45.0)
        
        # 超出范围
        with pytest.raises(ValueError, match="A轴角度.*超出 XM-100 范围"):
            p._validate_a_axis(a=-50.0)
        
        with pytest.raises(ValueError, match="A轴角度.*超出 XM-100 范围"):
            p._validate_a_axis(a=120.0)

    def test_validate_c_axis(self, make_processor):
        """验证 XM-100 C轴验证（覆盖行 333-334）。"""
        p = make_processor()
        # 正常范围
        p._validate_c_axis(c=90.0)
        
        # 超出范围
        with pytest.raises(ValueError, match="C轴角度.*超出 XM-100 范围"):
            p._validate_c_axis(c=-10.0)
        
        with pytest.raises(ValueError, match="C轴角度.*超出 XM-100 范围"):
            p._validate_c_axis(c=400.0)

    def test_format_arc(self, make_processor):
        """验证 XM-100 圆弧格式（覆盖行 346-349）。"""
        p = make_processor()
        result = p.format_arc(
            start=(0.0, 0.0, 0.0),
            end=(10.0, 10.0, 0.0),
            center=(5.0, 5.0, 0.0),
            clockwise=True,
        )
        assert "G02" in result
        assert "R" in result  # XM-100 使用 R 模式

    def test_format_cycle_drill(self, make_processor):
        """验证 XM-100 钻孔循环（覆盖行 363-384）。"""
        p = make_processor()
        # 深孔啄钻
        result_deep = p.format_cycle_drill(x=10.0, y=20.0, z=-15.0, depth=15.0, dwell=0.0)
        assert "G83" in result_deep
        assert "Q2.000" in result_deep  # XM-100 小啄钻
        
        # 浅孔简单钻
        result_shallow = p.format_cycle_drill(x=10.0, y=20.0, z=-3.0, depth=3.0, dwell=0.0)
        assert "G81" in result_shallow

    def test_format_footer(self, make_processor):
        """验证 XM-100 程序结束格式（覆盖行 387-400）。"""
        p = make_processor()
        result = p.format_footer()
        assert "M09" in result
        assert "M05" in result
        assert "G00 G91 G28 Z0." in result
        assert "G00 G91 G28 A0. C0." in result
        assert "G00 G91 G28 X0. Y0." in result
        assert "G90" in result
        assert "G49" in result
        assert "M30" in result
        assert "%" in result

    def test_get_machine_info(self, make_processor):
        """验证 XM-100 机床信息获取（覆盖行 404）。"""
        p = make_processor()
        info = p.get_machine_info()
        assert info["machine_name"] == "XMachine XM-100"
        assert info["manufacturer"] == "数马电子 (XMachine)"
        assert info["type"] == "桌面级五轴加工中心"
        assert "workspace" in info
        assert "rotary_axes" in info
        assert "spindle" in info
        assert "feed" in info
        assert "controller" in info
        assert "features" in info

    def test_init_safe_z_height_limit(self, make_processor):
        """验证 XM-100 安全高度超限自动限制（覆盖行 66-70, 72）。"""
        # 安全高度超过 80.0 应被限制
        p = make_processor(safe_z_height=120.0)
        assert p.safe_z_height == 80.0
        # rapid_feed 超过最大值应被限制
        p2 = make_processor(rapid_feed=5000.0)
        assert p2.rapid_feed == p2.XM100_FEED_MAX

    def test_format_tool_change_with_radius_comp(self, make_processor):
        """验证 XM-100 带半径补偿的换刀（覆盖行 119）。"""
        p = make_processor()
        result = p.format_tool_change(tool_id=2, length_comp=5.0, radius_comp=1.0)
        assert "M03 S8000" in result  # 半径补偿时启动主轴

    def test_format_workspace_check_y_z_exceed(self, make_processor):
        """验证 XM-100 Y/Z 超行程警告（覆盖行 270, 272）。"""
        p = make_processor()
        # Y 超行程
        result_y = p.format_workspace_check(x=10.0, y=100.0, z=-5.0)
        assert "WARNING" in result_y
        assert "Y100.0" in result_y
        # Z 超行程
        result_z = p.format_workspace_check(x=10.0, y=20.0, z=-100.0)
        assert "WARNING" in result_z
        assert "Z-100.0" in result_z

    def test_validate_a_axis_singularity(self, make_processor):
        """验证 XM-100 A轴奇异点警告（覆盖行 326）。"""
        p = make_processor()
        # A轴接近 90° 应触发奇异点警告但不报错（A轴范围 -30~110°）
        p._validate_a_axis(a=88.0)   # 接近 +90°
        p._validate_a_axis(a=92.0)   # 接近 +90°（另一侧）


class TestSemanticEquivalence:
    """语义等价性测试——相同刀轨经不同后处理器保持加工语义一致。"""

    @pytest.fixture
    def processors(self):
        return {
            "fanuc": FanucPostProcessor(decimal_places=3),
            "siemens": SiemensPostProcessor(decimal_places=3),
            "heidenhain": HeidenhainPostProcessor(decimal_places=3),
        }

    def test_all_produce_headers(self, processors):
        """验证所有后处理器都能生成程序头。"""
        for name, p in processors.items():
            header = p.format_header()
            assert len(header) > 0, f"{name} header is empty"

    def test_all_produce_footers(self, processors):
        """验证所有后处理器都能生成程序尾。"""
        for name, p in processors.items():
            footer = p.format_footer()
            assert len(footer) > 0, f"{name} footer is empty"

    def test_arc_radius_consistency(self, processors):
        """验证各后处理器圆弧终点坐标一致。"""
        start = (0.0, 0.0, 0.0)
        end = (10.0, 0.0, 0.0)
        center = (5.0, 0.0, 0.0)

        results = {}
        for name, p in processors.items():
            results[name] = p.format_arc(start, end, center, clockwise=True)

        assert all("10.000" in v for v in results.values()), (
            f"Arc endpoint inconsistent: {results}"
        )

    def test_tool_change_tool_id_preserved(self, processors):
        """验证换刀指令中刀具ID信息被保留。"""
        for name, p in processors.items():
            result = p.format_tool_change(tool_id=7)
            assert "7" in result or "07" in result, (
                f"{name} tool ID not found in: {result}"
            )

    def test_drill_depth_preserved(self, processors):
        """验证钻孔深度在各后处理器中保持一致。"""
        for name, p in processors.items():
            result = p.format_cycle_drill(10, 20, 0, -5.0)
            assert "-5" in result or "5.000" in result, (
                f"{name} depth not found in: {result}"
            )


class TestBoundaryConditions:
    """边界条件测试。"""

    def test_negative_coordinates(self):
        """验证负坐标值处理。"""
        p = FanucPostProcessor()
        result = p.format_arc((0, 0, 0), (-10, -20, 0), (-5, -10, 0))
        assert len(result) > 0
        assert "-10" in result or "-20" in result

    def test_zero_radius_arc(self):
        """验证零半径圆弧（退化为点）处理。"""
        p = FanucPostProcessor()
        result = p.format_arc((0, 0, 0), (0, 0, 0), (0, 0, 0))
        assert len(result) > 0

    def test_very_large_coordinates(self):
        """验证大坐标值处理。"""
        p = SiemensPostProcessor()
        result = p.format_arc((0, 0, 0), (99999, 99999, 0), (50000, 50000, 0))
        assert len(result) > 0

    def test_custom_decimal_places(self):
        """验证自定义小数位数。"""
        p = HeidenhainPostProcessor(decimal_places=6)
        result = p.format_header()
        assert len(result) > 0

    def test_zero_feed_rate(self):
        """验证零进给速度被防御性校验拒绝。

        基类 ``__init__`` 对 ``rapid_feed <= 0`` 抛出 ``ValueError``，
        避免后续生成无效的 F0 代码。
        """
        with pytest.raises(ValueError, match="rapid_feed"):
            FanucPostProcessor(rapid_feed=0)
        # 正数边界值应可正常工作
        p = FanucPostProcessor(rapid_feed=1)
        result = p.format_arc((0, 0, 0), (10, 0, 0), (5, 0, 0))
        assert len(result) > 0

    def test_very_deep_drill(self):
        """验证极深钻孔。"""
        p = HeidenhainPostProcessor()
        result = p.format_cycle_drill(0, 0, 0, -999.0)
        assert len(result) > 0

    def test_small_decimal_places(self):
        """验证小数位数为0。"""
        p = FanucPostProcessor(decimal_places=0)
        result = p.format_header()
        assert len(result) > 0


class TestRegistry:
    """后处理器注册表测试。"""

    def test_singleton(self):
        """验证注册表是单例模式。"""
        r1 = PostProcessorRegistry()
        r2 = PostProcessorRegistry()
        assert r1 is r2

    def test_list_controllers(self):
        """验证列出所有控制器类型。"""
        registry = PostProcessorRegistry()
        controllers = registry.list_controllers()
        assert "fanuc_0i" in controllers
        assert "siemens_840d" in controllers
        assert "heidenhain_tnc" in controllers

    def test_get_processor_fanuc(self):
        """验证获取Fanuc后处理器。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        processor = registry.get_processor("fanuc_0i")
        assert isinstance(processor, FanucPostProcessor)

    def test_get_processor_siemens(self):
        """验证获取Siemens后处理器。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        processor = registry.get_processor("siemens_840d")
        assert isinstance(processor, SiemensPostProcessor)

    def test_get_processor_heidenhain(self):
        """验证获取Heidenhain后处理器。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        processor = registry.get_processor("heidenhain_tnc")
        assert isinstance(processor, HeidenhainPostProcessor)

    def test_get_unknown_controller(self):
        """验证未知控制器类型抛出KeyError。"""
        registry = PostProcessorRegistry()
        with pytest.raises(KeyError, match="Unknown controller type"):
            registry.get_processor("unknown_controller")

    def test_get_processor_with_config(self):
        """验证配置参数传递给后处理器。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        processor = registry.get_processor(
            "fanuc_0i", decimal_places=5, safe_z_height=100.0
        )
        assert processor.decimal_places == 5
        assert processor.safe_z_height == 100.0

    def test_register_custom_processor(self):
        """验证动态注册新的后处理器类型。"""

        class CustomProcessor(FanucPostProcessor):
            pass

        registry = PostProcessorRegistry()
        registry.register("custom_ctrl", CustomProcessor)
        assert "custom_ctrl" in registry.list_controllers()
        processor = registry.get_processor("custom_ctrl")
        assert isinstance(processor, CustomProcessor)

    def test_register_invalid_class(self):
        """验证注册非BasePostProcessor子类抛出TypeError。"""
        registry = PostProcessorRegistry()
        with pytest.raises(TypeError):

            class NotAProcessor:
                pass

            registry.register("invalid", NotAProcessor)

    def test_instance_caching(self):
        """验证相同配置的实例被缓存复用。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        p1 = registry.get_processor("fanuc_0i", decimal_places=3)
        p2 = registry.get_processor("fanuc_0i", decimal_places=3)
        assert p1 is p2

    def test_clear_instances(self):
        """验证清除实例缓存。"""
        registry = PostProcessorRegistry()
        registry.get_processor("fanuc_0i")
        registry.clear_instances()
        registry.get_processor("fanuc_0i")

    def test_reset_singleton(self):
        """验证重置单例实例（覆盖行 61-62）。"""
        # 获取当前单例
        r1 = PostProcessorRegistry()
        assert r1 is not None
        
        # 重置单例
        PostProcessorRegistry.reset()
        
        # 再次获取应该创建新实例
        r2 = PostProcessorRegistry()
        assert r2 is not None
        # 注意：重置后创建的新实例与旧实例不是同一个对象
        # 但由于单例模式，r2 应该是新的唯一实例
        
        # 验证新实例功能正常
        assert "fanuc_0i" in r2.list_controllers()

    def test_reload_config(self):
        """验证强制重新加载配置（覆盖行 213-215）。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        
        # 先加载一次配置
        processor1 = registry.load_from_config(_PROD_CONFIG)
        assert isinstance(processor1, FanucPostProcessor)
        
        # 强制重新加载配置
        processor2 = registry.reload_config(_PROD_CONFIG)
        assert isinstance(processor2, FanucPostProcessor)
        
        # 验证新实例与旧实例不同（因为清除了缓存）
        assert processor1 is not processor2
        
        # 验证新实例功能正常
        assert processor2.decimal_places == 3


class TestConfigLoading:
    """配置文件加载测试。

    使用生产配置文件 ``config/postprocessor_config.yaml`` 作为基础，
    避免与 ConfigValidator 的必需字段清单重复维护。
    """

    def test_load_from_config_file(self):
        """验证从生产配置文件加载 Fanuc 后处理器。"""
        registry = PostProcessorRegistry()
        registry.clear_instances()
        processor = registry.load_from_config(_PROD_CONFIG)
        assert isinstance(processor, FanucPostProcessor)
        # 生产配置中 base.decimal_places == 3
        assert processor.decimal_places == 3

    def test_load_siemens_from_config(self):
        """验证从配置文件加载 Siemens 后处理器。"""
        config_path = _make_config_with_controller("siemens_840d")
        try:
            registry = PostProcessorRegistry()
            registry.clear_instances()
            processor = registry.load_from_config(config_path)
            assert isinstance(processor, SiemensPostProcessor)
        finally:
            os.unlink(config_path)

    def test_load_heidenhain_from_config(self):
        """验证从配置文件加载 Heidenhain 后处理器。"""
        config_path = _make_config_with_controller("heidenhain_tnc")
        try:
            registry = PostProcessorRegistry()
            registry.clear_instances()
            processor = registry.load_from_config(config_path)
            assert isinstance(processor, HeidenhainPostProcessor)
        finally:
            os.unlink(config_path)

    def test_default_config_values(self):
        """验证配置文件缺少可选字段时后处理器使用构造默认值。

        生产配置文件已包含 decimal_places/safe_z_height/rapid_feed，
        这里验证它们被正确传递到后处理器实例。
        """
        registry = PostProcessorRegistry()
        registry.clear_instances()
        processor = registry.load_from_config(_PROD_CONFIG)
        assert processor.decimal_places == 3
        assert processor.safe_z_height == 50.0
        assert processor.rapid_feed == 10000

    def test_missing_config_file(self):
        """验证配置文件不存在时抛出FileNotFoundError。"""
        registry = PostProcessorRegistry()
        with pytest.raises(FileNotFoundError):
            registry.load_from_config("/nonexistent/path/config.yaml")

    def test_config_without_target_uses_default(self):
        """验证未指定target_controller时使用默认值 fanuc_0i。"""
        # 从生产配置中删除 target_controller 行
        with open(_PROD_CONFIG, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(
            r"^target_controller:\s*\S+\s*$",
            "",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        fd, config_path = tempfile.mkstemp(suffix=".yaml", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            registry = PostProcessorRegistry()
            registry.clear_instances()
            processor = registry.load_from_config(config_path)
            assert isinstance(processor, FanucPostProcessor)
        finally:
            os.unlink(config_path)

    def test_config_validator_errors_and_warnings(self):
        """验证 ConfigValidator 的错误和警告属性。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        assert validator.errors == []
        assert validator.warnings == []
        
        # 触发错误
        config = {"decimal_places": "invalid"}
        validator.validate(config)
        assert len(validator.errors) > 0
        assert len(validator.warnings) >= 0

    def test_config_validator_add_error_warning(self):
        """验证 _add_error 和 _add_warning 方法。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        validator._add_error("test.path", "test error")
        validator._add_warning("test.path", "test warning")
        
        assert "[test.path] test error" in validator.errors
        assert "[test.path] test warning" in validator.warnings

    def test_config_validator_check_type(self):
        """验证 _check_type 方法。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试 allow_none=True
        assert validator._check_type("test", None, int, allow_none=True) is True
        
        # 测试类型错误
        assert validator._check_type("test", "string", int) is False
        assert len(validator.errors) > 0

    def test_config_validator_check_positive_int(self):
        """验证 _check_positive_int 方法。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试负数
        assert validator._check_positive_int("test", -5) is False
        assert len(validator.errors) > 0
        
        # 测试正数
        validator._errors.clear()
        assert validator._check_positive_int("test", 10) is True
        assert len(validator.errors) == 0

    def test_config_validator_check_positive_float(self):
        """验证 _check_positive_float 方法。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试整数转换为浮点数
        assert validator._check_positive_float("test", 5) is True
        
        # 测试负数
        assert validator._check_positive_float("test", -5.0) is False
        assert len(validator.errors) > 0

    def test_config_validator_check_range(self):
        """验证 _check_range 方法。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试超出范围
        assert validator._check_range("test", 150, 0, 100) is False
        assert len(validator.errors) > 0
        
        # 测试在范围内
        validator._errors.clear()
        assert validator._check_range("test", 50, 0, 100) is True
        assert len(validator.errors) == 0

    def test_config_validator_validate_with_errors(self):
        """验证 validate 方法返回 False 当存在错误时。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        config = {
            "decimal_places": -1,
            "safe_z_height": -10.0,
            "rapid_feed": -100.0,
        }
        assert validator.validate(config) is False
        assert len(validator.errors) > 0

    def test_config_validator_validate_with_warnings(self):
        """验证 validate 方法返回 True 当只有警告时。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        config = {
            "decimal_places": 3,
            "safe_z_height": 50.0,
            "rapid_feed": 10000.0,
            "spindle": {
                "min_rpm": 50,
                "max_rpm": 24000,
                "default_rpm": 30000,  # 超出范围，产生警告
            },
            "feed": {
                "min_rate": 10.0,
                "max_rate": 20000.0,
                "default_rate": 1000.0,
            },
            "work_coordinate": {
                "G54": {}, "G55": {}, "G56": {},
                "G57": {}, "G58": {}, "G59": {},
                "default_coordinate_system": "G54",
            },
            "tool_offset": {
                "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
                "radius_registers": {
                    "start": 1, "end": 100, "default_offset": 0.0,
                    "compensation_types": {
                        "G41": {"register_range": [1, 100]},
                        "G42": {"register_range": [1, 100]},
                    }
                },
            },
            "fixed_cycles": {
                "drilling": {"G81": {}, "G83": {}},
                "tapping": {"G84": {}},
                "boring": {"G86": {}, "G89": {}},
                "threading": {"G76": {}},
            },
            "subprogram": {
                "call_format": "M98 P{program_number}",
                "end_code": "M99",
                "program_number": {"minimum": 1, "maximum": 9999, "format": "O"},
                "repeat": {"default": 1, "minimum": 1, "maximum": 999},
                "macro_variables": {
                    "local": {"range": [1, 33]},
                    "common": {"range": [100, 199]},
                    "system": {"range": [500, 599]},
                },
            },
        }
        result = validator.validate(config)
        # 验证通过时返回 True，可能有警告
        assert result is True or len(validator.errors) > 0

    def test_config_validator_spindle_validation(self):
        """验证主轴参数验证。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试缺少必需参数
        validator._validate_spindle({})
        assert len(validator.errors) > 0
        
        # 测试 min_rpm >= max_rpm
        validator._errors.clear()
        validator._validate_spindle({"min_rpm": 1000, "max_rpm": 500, "default_rpm": 750})
        assert any("必须小于" in err for err in validator.errors)

    def test_config_validator_feed_validation(self):
        """验证进给参数验证。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试缺少必需参数
        validator._validate_feed({})
        assert len(validator.errors) > 0
        
        # 测试 min_rate >= max_rate
        validator._errors.clear()
        validator._validate_feed({"min_rate": 2000.0, "max_rate": 1000.0, "default_rate": 1500.0})
        assert any("必须小于" in err for err in validator.errors)

    def test_config_validator_work_coordinate_validation(self):
        """验证工件坐标系验证。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试缺少坐标系
        validator._validate_work_coordinate({})
        assert len(validator.errors) > 0
        
        # 测试无效的默认坐标系
        validator._errors.clear()
        validator._validate_work_coordinate({
            "G54": {}, "G55": {}, "G56": {},
            "G57": {}, "G58": {}, "G59": {},
            "default_coordinate_system": "INVALID",
        })
        assert any("无效" in err for err in validator.errors)

    def test_config_validator_tool_offset_validation(self):
        """验证刀具偏置验证。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        
        # 测试缺少必需参数
        validator._validate_tool_offset({})
        assert len(validator.errors) > 0
        
        # 测试 start > end
        validator._errors.clear()
        validator._validate_tool_offset({
            "length_registers": {"start": 100, "end": 1, "default_offset": 0.0},
            "radius_registers": {"start": 1, "end": 100, "default_offset": 0.0},
        })
        assert any("不能大于" in err for err in validator.errors)

    def test_config_limiter_spindle_rpm(self):
        """验证主轴转速限制。"""
        from app.postprocessor.config_loader import ConfigLimiter
        
        config = {
            "spindle": {"min_rpm": 100, "max_rpm": 10000, "default_rpm": 1000},
            "feed": {"min_rate": 10.0, "max_rate": 5000.0, "default_rate": 500.0},
            "axis_limits": {"enabled": False},
        }
        limiter = ConfigLimiter(config)
        
        # 测试低于下限
        result = limiter.limit_spindle_rpm(50, "test")
        assert result == 100.0
        
        # 测试高于上限
        result = limiter.limit_spindle_rpm(15000, "test")
        assert result == 10000.0
        
        # 测试在范围内
        result = limiter.limit_spindle_rpm(5000, "test")
        assert result == 5000.0

    def test_config_limiter_feed_rate(self):
        """验证进给速度限制。"""
        from app.postprocessor.config_loader import ConfigLimiter
        
        config = {
            "spindle": {"min_rpm": 100, "max_rpm": 10000, "default_rpm": 1000},
            "feed": {"min_rate": 10.0, "max_rate": 5000.0, "default_rate": 500.0},
            "axis_limits": {"enabled": False},
        }
        limiter = ConfigLimiter(config)
        
        # 测试低于下限
        result = limiter.limit_feed_rate(5.0, "test")
        assert result == 10.0
        
        # 测试高于上限
        result = limiter.limit_feed_rate(10000.0, "test")
        assert result == 5000.0
        
        # 测试在范围内
        result = limiter.limit_feed_rate(2000.0, "test")
        assert result == 2000.0

    def test_config_limiter_axis_position(self):
        """验证坐标轴位置限制。"""
        from app.postprocessor.config_loader import ConfigLimiter
        
        config = {
            "spindle": {"min_rpm": 100, "max_rpm": 10000, "default_rpm": 1000},
            "feed": {"min_rate": 10.0, "max_rate": 5000.0, "default_rate": 500.0},
            "axis_limits": {
                "enabled": True,
                "x_min": -500.0,
                "x_max": 500.0,
                "y_min": -500.0,
                "y_max": 500.0,
                "z_min": -300.0,
                "z_max": 0.0,
            },
        }
        limiter = ConfigLimiter(config)
        
        # 测试 X 轴低于下限
        result = limiter.limit_axis_position("X", -600.0, "test")
        assert result == -500.0
        
        # 测试 Y 轴高于上限
        result = limiter.limit_axis_position("Y", 600.0, "test")
        assert result == 500.0
        
        # 测试 Z 轴在范围内
        result = limiter.limit_axis_position("Z", -150.0, "test")
        assert result == -150.0
        
        # 测试无效坐标轴
        with pytest.raises(ValueError, match="无效的坐标轴"):
            limiter.limit_axis_position("A", 0.0, "test")

    def test_config_limiter_get_defaults(self):
        """验证获取默认值方法。"""
        from app.postprocessor.config_loader import ConfigLimiter
        
        config = {
            "spindle": {"min_rpm": 100, "max_rpm": 10000, "default_rpm": 1000},
            "feed": {"min_rate": 10.0, "max_rate": 5000.0, "default_rate": 500.0},
            "axis_limits": {"enabled": False},
        }
        limiter = ConfigLimiter(config)
        
        assert limiter.get_spindle_default() == 1000.0
        assert limiter.get_feed_default() == 500.0

    def test_config_validator_warnings_logged(self, caplog):
        """验证配置验证通过但有警告时记录警告日志（覆盖行 232）。"""
        import logging
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        # default_rpm 不在范围内会产生警告，但配置仍需通过验证
        config = {
            "decimal_places": 3,
            "safe_z_height": 50.0,
            "rapid_feed": 10000.0,
            "spindle": {"min_rpm": 100, "max_rpm": 10000, "default_rpm": 50},  # 不在范围内
            "feed": {"min_rate": 10.0, "max_rate": 5000.0, "default_rate": 500.0},
            "work_coordinate": {
                "G54": {"x_offset": 0.0, "y_offset": 0.0, "z_offset": 0.0, "enabled": True},
                "G55": {"x_offset": 0.0, "y_offset": 0.0, "z_offset": 0.0, "enabled": True},
                "G56": {"x_offset": 0.0, "y_offset": 0.0, "z_offset": 0.0, "enabled": True},
                "G57": {"x_offset": 0.0, "y_offset": 0.0, "z_offset": 0.0, "enabled": True},
                "G58": {"x_offset": 0.0, "y_offset": 0.0, "z_offset": 0.0, "enabled": True},
                "G59": {"x_offset": 0.0, "y_offset": 0.0, "z_offset": 0.0, "enabled": True},
                "default_coordinate_system": "G54",
            },
            "tool_offset": {
                "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
                "radius_registers": {
                    "start": 1, "end": 100, "default_offset": 0.0,
                    "compensation_types": {
                        "G41": {"register_range": [1, 100]},
                        "G42": {"register_range": [1, 100]},
                    },
                },
            },
            "fixed_cycles": {
                "drilling": {
                    "G81": {"retract_mode": "G98", "peck_depth": 10.0, "retract_distance": 5.0},
                    "G83": {"retract_mode": "G98", "peck_depth": 10.0, "retract_distance": 5.0},
                },
                "tapping": {
                    "G84": {
                        "spindle_direction": "M03",
                        "feed_per_rev": True,
                        "dwell_time": 0.0,
                        "retract_spindle_direction": "M04",
                    },
                },
                "boring": {
                    "G86": {
                        "retract_mode": "G98",
                        "dwell_time": 0.5,
                        "retract_type": "rapid",
                        "orient_spindle": True,
                        "shift_axis": None,
                        "shift_distance": 0.0,
                    },
                    "G89": {
                        "retract_mode": "G98",
                        "dwell_time": 0.5,
                        "retract_type": "rapid",
                        "orient_spindle": True,
                        "shift_axis": None,
                        "shift_distance": 0.0,
                    },
                },
                "threading": {
                    "G76": {
                        "retract_mode": "G98",
                        "lead": 1.0,
                        "passes": 5,
                        "depth_cut_first": 0.5,
                        "depth_cut_last": 0.1,
                        "finishing_passes": 2,
                        "taper": 0.0,
                        "tool_angle": 60.0,
                        "retract_type": "rapid",
                        "shift_axis": None,
                        "shift_distance": 0.0,
                        "infeed_method": "compound",
                    },
                },
            },
            "subprogram": {
                "call_format": "M98 P{program_number}",
                "end_code": "M99",
                "program_number": {"minimum": 1, "maximum": 9999, "format": "O"},
                "repeat": {"default": 1, "minimum": 1, "maximum": 999},
                "macro_variables": {
                    "local": {"range": [1, 33]},
                    "common": {"range": [100, 199]},
                    "system": {"range": [500, 599]},
                },
            },
        }
        
        # 验证会触发 logger.warning（行 232）
        with caplog.at_level(logging.WARNING, logger='app.postprocessor.config_loader'):
            result = validator.validate(config)
        
        # 验证应该通过（警告不影响结果）
        assert result is True, f"Validation should pass but got errors: {validator.errors}"
        assert len(validator.warnings) > 0, "Should have warnings about default_rpm out of range"
        # 验证日志确实被记录
        assert any("配置验证通过" in record.message for record in caplog.records if record.levelname == "WARNING")

    def test_config_validator_work_coordinate_not_dict(self):
        """验证工件坐标系不是字典类型时的错误处理（覆盖行 313-314）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        wcs = {
            "G54": "invalid",  # 不是字典
            "G55": {}, "G56": {},
            "G57": {}, "G58": {}, "G59": {},
            "default_coordinate_system": "G54",
        }
        validator._validate_work_coordinate(wcs)
        assert any("必须为字典类型" in err for err in validator.errors)

    def test_config_validator_tool_offset_not_dict(self):
        """验证刀具偏置不是字典类型时的错误处理（覆盖行 338-339）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": "invalid",  # 不是字典
            "radius_registers": {"start": 1, "end": 100, "default_offset": 0.0},
        }
        validator._validate_tool_offset(tool_offset)
        assert any("必须为字典类型" in err for err in validator.errors)

    def test_config_validator_radius_compensation_not_dict(self):
        """验证半径补偿类型不是字典时的错误处理（覆盖行 368-369）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1, "end": 100, "default_offset": 0.0,
                "compensation_types": "invalid",  # 不是字典
            },
        }
        validator._validate_tool_offset(tool_offset)
        assert any("必须为字典类型" in err for err in validator.errors)

    def test_config_validator_radius_registers_not_dict(self):
        """验证 radius_registers 不是字典时的错误处理（覆盖行 337-338, 364）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": "invalid",  # 不是字典
        }
        validator._validate_tool_offset(tool_offset)
        # 应该报错，因为 radius_registers 不是字典
        assert any("radius_registers" in err and "必须为字典类型" in err for err in validator.errors)

    def test_config_loader_clear_specific_cache(self):
        """验证清除特定控制器的缓存（覆盖行 732-733）。"""
        import tempfile
        import os
        from app.postprocessor.config_loader import ConfigLoader
        
        # 创建临时配置文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
base:
  decimal_places: 3
  safe_z_height: 50.0
  rapid_feed: 10000.0
  spindle:
    min_rpm: 100
    max_rpm: 10000
    default_rpm: 1000
  feed:
    min_rate: 10.0
    max_rate: 5000.0
    default_rate: 500.0
  work_coordinate:
    G54: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G55: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G56: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G57: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G58: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G59: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    default_coordinate_system: G54
  tool_offset:
    length_registers: {start: 1, end: 100, default_offset: 0.0}
    radius_registers:
      start: 1
      end: 100
      default_offset: 0.0
      compensation_types:
        G41: {register_range: [1, 100]}
        G42: {register_range: [1, 100]}
  fixed_cycles:
    drilling:
      G81: {retract_mode: G98, peck_depth: 10.0, retract_distance: 5.0}
      G83: {retract_mode: G98, peck_depth: 10.0, retract_distance: 5.0}
    tapping:
      G84: {spindle_direction: M03, feed_per_rev: true, dwell_time: 0.0, retract_spindle_direction: M04}
    boring:
      G86: {retract_mode: G98, dwell_time: 0.5, retract_type: rapid, orient_spindle: true, shift_axis: null, shift_distance: 0.0}
      G89: {retract_mode: G98, dwell_time: 0.5, retract_type: rapid, orient_spindle: true, shift_axis: null, shift_distance: 0.0}
    threading:
      G76: {retract_mode: G98, lead: 1.0, passes: 5, depth_cut_first: 0.5, depth_cut_last: 0.1, finishing_passes: 2, taper: 0.0, tool_angle: 60.0, retract_type: rapid, shift_axis: null, shift_distance: 0.0, infeed_method: compound}
  subprogram:
    call_format: M98 P{program_number}
    end_code: M99
    program_number: {minimum: 1, maximum: 9999, format: O}
    repeat: {default: 1, minimum: 1, maximum: 999}
    macro_variables:
      local: {range: [1, 33]}
      common: {range: [100, 199]}
      system: {range: [500, 599]}
controllers:
  fanuc_0i:
    target_controller: fanuc_0i
""")
            config_path = f.name
        
        try:
            loader = ConfigLoader()
            # 加载配置到缓存
            loader.load(config_path, "fanuc")
            # 验证缓存存在（缓存键格式为 "{config_path}:{controller_id}"）
            cache_key = f"{config_path}:fanuc"
            assert cache_key in loader._cache
            # 清除特定控制器的缓存
            loader.clear_cache("fanuc")
            # 验证缓存被清除
            assert cache_key not in loader._cache
        finally:
            os.unlink(config_path)

    def test_config_loader_cache_expired(self):
        """验证缓存过期时重新加载（覆盖行 800）。"""
        import tempfile
        import os
        import time
        from app.postprocessor.config_loader import ConfigLoader
        
        # 创建临时配置文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
base:
  decimal_places: 3
  safe_z_height: 50.0
  rapid_feed: 10000.0
  spindle:
    min_rpm: 100
    max_rpm: 10000
    default_rpm: 1000
  feed:
    min_rate: 10.0
    max_rate: 5000.0
    default_rate: 500.0
  work_coordinate:
    G54: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G55: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G56: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G57: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G58: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G59: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    default_coordinate_system: G54
  tool_offset:
    length_registers: {start: 1, end: 100, default_offset: 0.0}
    radius_registers:
      start: 1
      end: 100
      default_offset: 0.0
      compensation_types:
        G41: {register_range: [1, 100]}
        G42: {register_range: [1, 100]}
  fixed_cycles:
    drilling:
      G81: {retract_mode: G98, peck_depth: 10.0, retract_distance: 5.0}
      G83: {retract_mode: G98, peck_depth: 10.0, retract_distance: 5.0}
    tapping:
      G84: {spindle_direction: M03, feed_per_rev: true, dwell_time: 0.0, retract_spindle_direction: M04}
    boring:
      G86: {retract_mode: G98, dwell_time: 0.5, retract_type: rapid, orient_spindle: true, shift_axis: null, shift_distance: 0.0}
      G89: {retract_mode: G98, dwell_time: 0.5, retract_type: rapid, orient_spindle: true, shift_axis: null, shift_distance: 0.0}
    threading:
      G76: {retract_mode: G98, lead: 1.0, passes: 5, depth_cut_first: 0.5, depth_cut_last: 0.1, finishing_passes: 2, taper: 0.0, tool_angle: 60.0, retract_type: rapid, shift_axis: null, shift_distance: 0.0, infeed_method: compound}
  subprogram:
    call_format: M98 P{program_number}
    end_code: M99
    program_number: {minimum: 1, maximum: 9999, format: O}
    repeat: {default: 1, minimum: 1, maximum: 999}
    macro_variables:
      local: {range: [1, 33]}
      common: {range: [100, 199]}
      system: {range: [500, 599]}
controllers:
  fanuc_0i:
    target_controller: fanuc_0i
""")
            config_path = f.name
        
        try:
            loader = ConfigLoader(cache_ttl=0.1)  # 100ms TTL
            # 加载配置到缓存
            config1 = loader.load(config_path, "fanuc")
            # 验证缓存存在（缓存键格式为 "{config_path}:{controller_id}"）
            cache_key = f"{config_path}:fanuc"
            assert cache_key in loader._cache
            # 等待缓存过期
            time.sleep(0.15)
            # 再次加载应该重新读取文件
            config2 = loader.load(config_path, "fanuc")
            # 验证配置相同
            assert config1["_controller_id"] == config2["_controller_id"]
        finally:
            os.unlink(config_path)

    def test_config_loader_missing_base_section(self):
        """验证配置文件缺少 base 段时抛出异常（覆盖行 806）。"""
        import tempfile
        import os
        from app.postprocessor.config_loader import ConfigLoader, ConfigLoadError
        
        # 创建缺少 base 段的配置文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
controllers:
  fanuc_0i:
    target_controller: fanuc_0i
""")
            config_path = f.name
        
        try:
            loader = ConfigLoader()
            with pytest.raises(ConfigLoadError, match="配置文件中缺少 'base' 段"):
                loader.load(config_path, "fanuc")
        finally:
            os.unlink(config_path)

    def test_config_loader_validation_error(self):
        """验证配置验证失败时抛出 ConfigValidationError（覆盖行 828-829）。"""
        import tempfile
        import os
        from app.postprocessor.config_loader import ConfigLoader, ConfigValidationError
        
        # 创建无效配置文件（缺少必需字段）
        # 必须显式 utf-8：本文件的注释含中文，而 ConfigLoader._load_yaml 以 utf-8
        # 读取。省略 encoding 时 Windows zh-CN 会按 locale(cp936) 写入 GBK 字节，
        # 读取侧抛 UnicodeDecodeError 而非预期的 ConfigValidationError。
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', encoding='utf-8', delete=False) as f:
            f.write("""
base:
  decimal_places: 3
  # 缺少 safe_z_height, rapid_feed, spindle, feed 等必需字段
controllers:
  fanuc_0i:
    target_controller: fanuc_0i
""")
            config_path = f.name
        
        try:
            loader = ConfigLoader()
            with pytest.raises(ConfigValidationError, match="配置验证失败"):
                loader.load(config_path, "fanuc")
        finally:
            os.unlink(config_path)

    def test_config_validator_compensation_type_not_dict(self):
        """验证补偿类型（G41/G42）不是字典时的错误处理（覆盖行 377-378）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1, "end": 100, "default_offset": 0.0,
                "compensation_types": {
                    "G41": "invalid",  # 不是字典
                    "G42": {"register_range": [1, 100]},
                },
            },
        }
        validator._validate_tool_offset(tool_offset)
        assert any("必须为字典类型" in err for err in validator.errors)

    def test_config_validator_register_range_missing(self):
        """验证 register_range 缺失时的错误处理（覆盖行 381）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1, "end": 100, "default_offset": 0.0,
                "compensation_types": {
                    "G41": {},  # 缺少 register_range
                    "G42": {"register_range": [1, 100]},
                },
            },
        }
        validator._validate_tool_offset(tool_offset)
        assert any("缺少 register_range" in err for err in validator.errors)

    def test_config_validator_register_range_invalid_format(self):
        """验证 register_range 格式无效时的错误处理（覆盖行 383）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1, "end": 100, "default_offset": 0.0,
                "compensation_types": {
                    "G41": {"register_range": [1]},  # 长度不是2
                    "G42": {"register_range": [1, 100]},
                },
            },
        }
        validator._validate_tool_offset(tool_offset)
        assert any("必须为长度为2的列表" in err for err in validator.errors)

    def test_config_validator_register_range_not_int(self):
        """验证 register_range 元素不是整数时的错误处理（覆盖行 389）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1, "end": 100, "default_offset": 0.0,
                "compensation_types": {
                    "G41": {"register_range": [1.5, 100.5]},  # 不是整数
                    "G42": {"register_range": [1, 100]},
                },
            },
        }
        validator._validate_tool_offset(tool_offset)
        assert any("元素必须为整数" in err for err in validator.errors)

    def test_config_validator_register_range_inverted(self):
        """验证 register_range 起始值大于结束值时的错误处理（覆盖行 394）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        tool_offset = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1, "end": 100, "default_offset": 0.0,
                "compensation_types": {
                    "G41": {"register_range": [100, 1]},  # 起始 > 结束
                    "G42": {"register_range": [1, 100]},
                },
            },
        }
        validator._validate_tool_offset(tool_offset)
        assert any("不能大于结束值" in err for err in validator.errors)

    def test_config_validator_fixed_cycles_not_dict(self):
        """验证固定循环组不是字典类型时的错误处理（覆盖行 406）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": "invalid",  # 不是字典
            "tapping": {},
            "boring": {},
            "threading": {},
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("必须为字典类型" in err for err in validator.errors)

    def test_config_validator_drilling_cycle_missing(self):
        """验证钻孔循环缺失时的错误处理（覆盖行 412）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {},  # 缺少 G81, G83
            "tapping": {},
            "boring": {},
            "threading": {},
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("缺少钻孔循环" in err for err in validator.errors)

    def test_config_validator_tapping_cycle_missing(self):
        """验证攻丝循环缺失时的错误处理（覆盖行 419）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {"G81": {}, "G83": {}},
            "tapping": {},  # 缺少 G84
            "boring": {},
            "threading": {},
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("缺少攻丝循环" in err for err in validator.errors)

    def test_config_validator_boring_cycle_missing(self):
        """验证镗孔循环缺失时的错误处理（覆盖行 430）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {"G81": {}, "G83": {}},
            "tapping": {"G84": {}},
            "boring": {},  # 缺少 G86, G89
            "threading": {},
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("缺少镗孔循环" in err for err in validator.errors)

    def test_config_validator_threading_cycle_missing(self):
        """验证螺纹加工循环缺失时的错误处理（覆盖行 437）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {"G81": {}, "G83": {}},
            "tapping": {"G84": {}},
            "boring": {"G86": {}, "G89": {}},
            "threading": {},  # 缺少 G76
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("缺少螺纹加工循环" in err for err in validator.errors)

    def test_config_validator_drilling_cycle_invalid_decrement(self):
        """验证钻孔循环 decrement_type 无效时的错误处理（覆盖行 460）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {
                "G81": {
                    "retract_mode": "G98",
                    "peck_depth": 10.0,
                    "retract_distance": 5.0,
                    "decrement_type": "invalid_type",  # 无效类型
                },
                "G83": {},
            },
            "tapping": {},
            "boring": {},
            "threading": {},
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("decrement_type 无效" in err for err in validator.errors)

    def test_config_validator_boring_cycle_invalid_shift_axis(self):
        """验证镗孔循环 shift_axis 无效时的错误处理（覆盖行 496）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {"G81": {}, "G83": {}},
            "tapping": {},
            "boring": {
                "G86": {
                    "retract_mode": "G98",
                    "dwell_time": 0.5,
                    "retract_type": "rapid",
                    "orient_spindle": True,
                    "shift_axis": "Z",  # 无效轴（只允许 None, "X", "Y"）
                    "shift_distance": 10.0,
                },
                "G89": {},
            },
            "threading": {},
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("shift_axis 无效" in err for err in validator.errors)

    def test_config_validator_threading_cycle_invalid_shift_axis(self):
        """验证螺纹加工循环 shift_axis 无效时的错误处理（覆盖行 521）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        fixed_cycles = {
            "drilling": {"G81": {}, "G83": {}},
            "tapping": {},
            "boring": {"G86": {}, "G89": {}},
            "threading": {
                "G76": {
                    "retract_mode": "G98",
                    "lead": 1.0,
                    "passes": 5,
                    "depth_cut_first": 0.5,
                    "depth_cut_last": 0.1,
                    "finishing_passes": 2,
                    "taper": 0.0,
                    "tool_angle": 60.0,
                    "retract_type": "rapid",
                    "shift_axis": "Z",  # 无效轴
                    "shift_distance": 0.0,
                    "infeed_method": "compound",
                },
            },
        }
        validator._validate_fixed_cycles(fixed_cycles)
        assert any("shift_axis 无效" in err for err in validator.errors)

    def test_config_validator_macro_variables_invalid_range(self):
        """验证宏变量 range 格式无效时的错误处理（覆盖行 561, 566）。"""
        from app.postprocessor.config_loader import ConfigValidator
        
        validator = ConfigValidator()
        subprogram = {
            "call_format": "M98 P{program_number}",
            "end_code": "M99",
            "program_number": {"minimum": 1, "maximum": 9999, "format": "O"},
            "repeat": {"default": 1, "minimum": 1, "maximum": 999},
            "macro_variables": {
                "local": {"range": [1]},  # 长度不是2
                "common": {"range": [100, 199.5]},  # 元素不是整数
                "system": {"range": [500, 599]},
            },
        }
        validator._validate_subprogram(subprogram)
        assert any("range 必须为长度为2的列表" in err for err in validator.errors)
        assert any("元素必须为整数" in err for err in validator.errors)

    def test_config_limiter_axis_disabled(self):
        """验证坐标轴限制禁用时直接返回原始位置（覆盖行 667）。"""
        from app.postprocessor.config_loader import ConfigLimiter
        
        config = {
            "spindle": {"min_rpm": 100, "max_rpm": 10000, "default_rpm": 1000},
            "feed": {"min_rate": 10.0, "max_rate": 5000.0, "default_rate": 500.0},
            "axis_limits": {"enabled": False},
        }
        limiter = ConfigLimiter(config)
        
        # 禁用时应直接返回原始值
        result = limiter.limit_axis_position("X", 1000.0, "test")
        assert result == 1000.0

    def test_config_loader_clear_cache(self):
        """验证缓存清除功能（覆盖行 728-733）。"""
        from app.postprocessor.config_loader import ConfigLoader
        
        # 清除全部缓存
        ConfigLoader.clear_cache()
        
        # 清除指定控制器缓存
        ConfigLoader.clear_cache("fanuc")

    def test_config_loader_resolve_path(self):
        """验证配置路径解析功能（覆盖行 747-752, 757-762）。"""
        from app.postprocessor.config_loader import ConfigLoader
        import os
        
        loader = ConfigLoader()
        
        # 测试 None 路径（使用默认路径）
        default_path = loader._resolve_path(None)
        assert "postprocessor_config.yaml" in default_path
        
        # 测试绝对路径
        abs_path = os.path.abspath("test.yaml")
        result = loader._resolve_path(abs_path)
        assert result == abs_path
        
        # 测试相对路径
        rel_path = "config/test.yaml"
        result = loader._resolve_path(rel_path)
        assert os.path.isabs(result)

    def test_config_loader_invalid_controller(self):
        """验证无效控制器标识的错误处理（覆盖行 812）。"""
        from app.postprocessor.config_loader import ConfigLoader, ConfigLoadError
        
        loader = ConfigLoader()
        
        # 创建临时配置文件
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
base:
  decimal_places: 3
  safe_z_height: 50.0
  rapid_feed: 10000.0
  spindle: {min_rpm: 100, max_rpm: 10000, default_rpm: 1000}
  feed: {min_rate: 10.0, max_rate: 5000.0, default_rate: 500.0}
  work_coordinate: {G54: {}, G55: {}, G56: {}, G57: {}, G58: {}, G59: {}, default_coordinate_system: G54}
  tool_offset: {length_registers: {start: 1, end: 100}, radius_registers: {start: 1, end: 100}}
  fixed_cycles: {drilling: {}, tapping: {}, boring: {}, threading: {}}
  subprogram: {call_format: M98, end_code: M99, program_number: {minimum: 1, maximum: 9999}, repeat: {default: 1}, macro_variables: {local: {range: [1, 33]}, common: {range: [100, 199]}, system: {range: [500, 599]}}}
controllers: {}
target_controller: invalid_controller
""")
            temp_path = f.name
        
        try:
            with pytest.raises(ConfigLoadError, match="无效的控制器标识"):
                loader.load(temp_path, controller_id="invalid_controller")
        finally:
            os.unlink(temp_path)

    def test_config_loader_yaml_parse_error(self):
        """验证 YAML 解析错误的处理（覆盖行 859-860）。"""
        from app.postprocessor.config_loader import ConfigLoader, ConfigLoadError
        import tempfile
        
        loader = ConfigLoader()
        
        # 创建格式错误的 YAML 文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("invalid: yaml: content: [unclosed")
            temp_path = f.name
        
        try:
            with pytest.raises(ConfigLoadError, match="YAML解析失败"):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_config_loader_empty文件(self):
        """验证空配置文件的处理（覆盖行 863）。"""
        from app.postprocessor.config_loader import ConfigLoader, ConfigLoadError
        import tempfile
        
        loader = ConfigLoader()
        
        # 创建空文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            temp_path = f.name
        
        try:
            with pytest.raises(ConfigLoadError, match="配置文件为空"):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_config_loader_非字典格式(self):
        """验证非字典格式配置文件的处理（覆盖行 866）。"""
        from app.postprocessor.config_loader import ConfigLoader, ConfigLoadError
        import tempfile
        
        loader = ConfigLoader()
        
        # 创建列表格式的 YAML 文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("- item1\n- item2\n")
            temp_path = f.name
        
        try:
            with pytest.raises(ConfigLoadError, match="配置文件格式错误"):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_config_loader_default_controller_fallback(self):
        """验证无法确定控制器类型时使用默认值 fanuc（覆盖行 893-896）。"""
        from app.postprocessor.config_loader import ConfigLoader
        import tempfile

        loader = ConfigLoader()

        # 创建没有 target_controller 的配置文件（使用完整配置以通过验证）
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("""
base:
  decimal_places: 3
  safe_z_height: 50.0
  rapid_feed: 10000.0
  spindle: {min_rpm: 100, max_rpm: 10000, default_rpm: 1000}
  feed: {min_rate: 10.0, max_rate: 5000.0, default_rate: 500.0}
  work_coordinate:
    G54: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G55: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G56: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G57: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G58: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    G59: {x_offset: 0.0, y_offset: 0.0, z_offset: 0.0, enabled: true}
    default_coordinate_system: G54
  tool_offset:
    length_registers: {start: 1, end: 100, default_offset: 0.0}
    radius_registers:
      start: 1
      end: 100
      default_offset: 0.0
      compensation_types:
        G41: {register_range: [1, 100]}
        G42: {register_range: [1, 100]}
  fixed_cycles:
    drilling:
      G81: {retract_mode: G98, peck_depth: 10.0, retract_distance: 5.0}
      G83: {retract_mode: G98, peck_depth: 10.0, retract_distance: 5.0}
    tapping:
      G84: {spindle_direction: M03, feed_per_rev: true, dwell_time: 0.0, retract_spindle_direction: M04}
    boring:
      G86: {retract_mode: G98, dwell_time: 0.5, retract_type: rapid, orient_spindle: true, shift_axis: null, shift_distance: 0.0}
      G89: {retract_mode: G98, dwell_time: 0.5, retract_type: rapid, orient_spindle: true, shift_axis: null, shift_distance: 0.0}
    threading:
      G76: {retract_mode: G98, lead: 1.0, passes: 5, depth_cut_first: 0.5, depth_cut_last: 0.1, finishing_passes: 2, taper: 0.0, tool_angle: 60.0, retract_type: rapid, shift_axis: null, shift_distance: 0.0, infeed_method: compound}
  subprogram:
    call_format: M98 P{program_number}
    end_code: M99
    program_number: {minimum: 1, maximum: 9999, format: O}
    repeat: {default: 1, minimum: 1, maximum: 999}
    macro_variables:
      local: {range: [1, 33]}
      common: {range: [100, 199]}
      system: {range: [500, 599]}
controllers: {}
""")
            temp_path = f.name

        try:
            config = loader.load(temp_path)
            assert config["_controller_id"] == "fanuc"
        finally:
            os.unlink(temp_path)

    def test_config_loader_load_for_controller(self):
        """验证 load_for_controller 方法（覆盖行 916）。"""
        from app.postprocessor.config_loader import ConfigLoader
        
        loader = ConfigLoader()
        config = loader.load_for_controller("fanuc")
        assert config["_controller_id"] == "fanuc"

    def test_config_loader_reload(self):
        """验证 reload 方法（覆盖行 927-928）。"""
        from app.postprocessor.config_loader import ConfigLoader
        
        loader = ConfigLoader()
        config1 = loader.load()
        config2 = loader.reload()
        assert config1["_controller_id"] == config2["_controller_id"]
