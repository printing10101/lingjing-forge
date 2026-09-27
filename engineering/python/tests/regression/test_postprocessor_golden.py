"""后处理器黄金文件回归测试。

针对 9 种 CNC 控制器，用一套标准加工序列（程序头 → 换刀 → 刀具补偿 → 冷却开 →
快移 → 直线 → 圆弧 → 钻孔循环 → 冷却关 → 程序尾）生成 NC 输出，与
``tests/golden/postprocessor/`` 下的黄金文件逐字节比对，防止后处理器重构或配置
调整引入意外的输出漂移（这是「G 代码能否上机床」的回归护栏）。

另有一套**扩展序列**（``build_extended_program``）覆盖各方言的全部能力
（攻丝/镗孔/螺纹/切槽/子程序/高精度/五轴/RTCP/探针等，按 ``hasattr`` 能力探测），
黄金文件为 ``*_extended.nc``——这是方言声明化（docs/development/postprocessor-方言声明化设计.md
P0）的行为基线：任何方言从「代码类」迁移到「声明 + 模板」都必须逐字符保持输出一致。

黄金文件更新：审阅输出变更后执行 ``UPDATE_GOLDEN=1 pytest <本文件>`` 重新生成。

设计说明：
- 通过 monkeypatch 固定 ``BasePostProcessor._date_string``，消除 header 中的日期
  导致的天级不确定输出。
- ``_block_counter`` 为实例级状态，fresh 实例输出确定。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.postprocessor.base import BasePostProcessor
from app.postprocessor.fagor import FagorPostProcessor
from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.gsk import GSKPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.hnc import HNCPostProcessor
from app.postprocessor.knd import KNDPostProcessor
from app.postprocessor.mitsubishi import MitsubishiPostProcessor
from app.postprocessor.preview_sequence import build_standard_program
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.xmachine import XMachineXM100PostProcessor


CONTROLLERS: dict[str, type[BasePostProcessor]] = {
    "fanuc_0i": FanucPostProcessor,
    "siemens_840d": SiemensPostProcessor,
    "heidenhain_tnc": HeidenhainPostProcessor,
    "fagor_8055": FagorPostProcessor,
    "gsk_980_25i": GSKPostProcessor,
    "hnc_848_22": HNCPostProcessor,
    "knd_1000_2000_3000": KNDPostProcessor,
    "mitsubishi_m70_m80": MitsubishiPostProcessor,
    "xmachine_xm100": XMachineXM100PostProcessor,
}

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden" / "postprocessor"
FIXED_DATE = "2026-01-01"


@pytest.mark.regression
@pytest.mark.parametrize("controller_id", list(CONTROLLERS.keys()))
def test_golden_output_matches(controller_id: str, monkeypatch):
    monkeypatch.setattr(BasePostProcessor, "_date_string", staticmethod(lambda: FIXED_DATE))
    processor = CONTROLLERS[controller_id]()
    output = build_standard_program(processor)
    golden_path = GOLDEN_DIR / f"{controller_id}.nc"

    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(output, encoding="utf-8")
        pytest.skip("UPDATE_GOLDEN=1：已重新生成黄金文件")

    if not golden_path.exists():
        pytest.fail(
            f"黄金文件缺失: {golden_path}。建议操作：审阅当前输出后执行 "
            f"UPDATE_GOLDEN=1 pytest tests/regression/test_postprocessor_golden.py 生成。"
        )

    golden = golden_path.read_text(encoding="utf-8")
    assert output == golden, (
        f"{controller_id} 后处理器输出与黄金文件不一致。建议操作：若变更为预期，执行 "
        f"UPDATE_GOLDEN=1 更新黄金文件；否则回退后处理器改动。"
    )


# 扩展序列：覆盖各方言全部能力（方言声明化 P0 行为基线）


def build_extended_program(processor: BasePostProcessor) -> str:
    """用覆盖各方言全部能力的扩展序列生成完整 NC 程序文本。

    通过 ``hasattr`` 能力探测按方言实际拥有的方法调用，保证：
    - 所有 9 个方言都能跑同一构建器；
    - 能力差异（如仅 Heidenhain 有探针、仅 XMachine 有 RTCP）体现在黄金文件差异中。
    """
    lines: list[str] = []

    def emit(text: str) -> None:
        lines.append(text)

    emit(processor.format_header(program_number=2000))

    if hasattr(processor, "format_tool_change"):
        emit(processor.format_tool_change(tool_id=2, length_comp=30.0, radius_comp=5.0))
    if hasattr(processor, "format_tool_compensation"):
        emit(processor.format_tool_compensation(length_offset=2, radius_offset=2))
    emit(processor.format_coolant("on"))
    if hasattr(processor, "format_rapid_move"):
        emit(processor.format_rapid_move(5.0, 10.0, 40.0))
    if hasattr(processor, "format_linear_move"):
        emit(processor.format_linear_move(15.0, 25.0, 20.0, feed=600.0))

    # 圆弧（顺/逆时针各一）
    if hasattr(processor, "format_arc"):
        emit(processor.format_arc((15.0, 25.0, 20.0), (25.0, 35.0, 20.0), (20.0, 30.0, 20.0), clockwise=True))
        emit(processor.format_arc((25.0, 35.0, 20.0), (15.0, 25.0, 20.0), (20.0, 30.0, 20.0), clockwise=False))

    # 固定循环：钻孔 / 攻丝 / 镗孔 / 螺纹 / 切槽 / 螺纹车削
    if hasattr(processor, "format_cycle_drill"):
        emit(processor.format_cycle_drill(x=20.0, y=30.0, z=20.0, depth=10.0, dwell=0.3))
    if hasattr(processor, "format_cycle_tapping"):
        emit(processor.format_cycle_tapping(x=30.0, y=20.0, z=20.0, depth=12.0, pitch=1.5, spindle_rpm=800.0))
    if hasattr(processor, "format_cycle_boring"):
        emit(processor.format_cycle_boring(x=40.0, y=20.0, z=20.0, depth=8.0, cycle_type="G86", dwell=0.4))
    if hasattr(processor, "format_cycle_threading"):
        emit(
            processor.format_cycle_threading(
                x=10.0,
                y=10.0,
                depth=15.0,
                lead=1.5,
                passes=4,
                depth_cut_first=0.3,
                depth_cut_last=0.1,
                finishing_passes=1,
                tool_angle=55.0,
            )
        )
    if hasattr(processor, "format_cycle_groove"):
        emit(processor.format_cycle_groove(x=50.0, z=15.0, depth=2.0, width=4.0))
    if hasattr(processor, "format_cycle_thread_turning"):
        emit(
            processor.format_cycle_thread_turning(
                x=30.0,
                z=25.0,
                depth=2.0,
                pitch=2.0,
                passes=4,
                first_depth=0.4,
                last_depth=0.1,
            )
        )

    # 子程序
    if hasattr(processor, "format_subprogram_call"):
        emit(processor.format_subprogram_call(program_number=5000, repeat=2))
    if hasattr(processor, "format_subprogram_end"):
        emit(processor.format_subprogram_end())

    # 高级模式（高精度 / 五轴 / RTCP / 刀轴 / 探针 / 法向补偿）
    if hasattr(processor, "format_high_precision_mode"):
        emit(processor.format_high_precision_mode(enable=True))
    if hasattr(processor, "format_five_axis_mode"):
        emit(processor.format_five_axis_mode(enable=True))
    if hasattr(processor, "format_rtcp_on"):
        emit(processor.format_rtcp_on(tool_length=120.0))
    if hasattr(processor, "format_rtcp_off"):
        emit(processor.format_rtcp_off())
    if hasattr(processor, "format_twp_on"):
        emit(processor.format_twp_on(tool_axis_i=0.0, tool_axis_j=0.0, tool_axis_k=1.0))
    if hasattr(processor, "format_twp_off"):
        emit(processor.format_twp_off())
    if hasattr(processor, "format_rotary_axis_config"):
        emit(processor.format_rotary_axis_config(a_axis_zero=0.0, c_axis_zero=0.0))
    if hasattr(processor, "format_workspace_check"):
        emit(processor.format_workspace_check(x=20.0, y=20.0, z=20.0))
    if hasattr(processor, "format_probe_cycle"):
        emit(processor.format_probe_cycle(probe_number=1, x_pos=25.0, y_pos=25.0, z_depth=-5.0))
    if hasattr(processor, "format_surface_normal_compensation"):
        emit(processor.format_surface_normal_compensation(enable=True))

    # 冷却关 / 程序尾
    emit(processor.format_coolant("off"))
    emit(processor.format_footer())
    return "\n".join(lines) + "\n"


@pytest.mark.regression
@pytest.mark.parametrize("controller_id", list(CONTROLLERS.keys()))
def test_golden_extended_matches(controller_id: str, monkeypatch):
    """扩展序列黄金比对：覆盖各方言全部能力（方言声明化 P0 基线）。"""
    monkeypatch.setattr(BasePostProcessor, "_date_string", staticmethod(lambda: FIXED_DATE))
    processor = CONTROLLERS[controller_id]()
    output = build_extended_program(processor)
    golden_path = GOLDEN_DIR / f"{controller_id}_extended.nc"

    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(output, encoding="utf-8")
        pytest.skip("UPDATE_GOLDEN=1：已重新生成扩展黄金文件")

    if not golden_path.exists():
        pytest.fail(
            f"扩展黄金文件缺失: {golden_path}。建议操作：审阅当前输出后执行 "
            f"UPDATE_GOLDEN=1 pytest tests/regression/test_postprocessor_golden.py 生成。"
        )

    golden = golden_path.read_text(encoding="utf-8")
    assert output == golden, (
        f"{controller_id} 后处理器扩展序列输出与黄金文件不一致。建议操作：若变更为预期，"
        f"执行 UPDATE_GOLDEN=1 更新黄金文件；否则回退后处理器改动。"
    )


# 声明式方言（hooks 模式，非内置镜像）的黄金覆盖


# 仅覆盖「非内置镜像」的声明式方言：镜像型方言（gsk/hnc/knd/mitsubishi/fagor）
# 已由 TestGoldenConsistency（tests/unit/test_postprocessor_dialect.py）保证与内置一致，
# 不重复建黄金文件。这两个 hooks 模式方言曾因 declaration.py 不支持列表格式
# hooks 而静默加载失败（2026-09 修复），黄金文件是其注册与输出的回归基线。
DECLARED_ONLY_DIALECTS: list[str] = [
    "heidenhain_tnc640_declared",
    "siemens_840d_declared",
]

_DIALECT_PLUGIN_DIR = Path(__file__).resolve().parents[4] / "postprocessor-plugins"


def _get_declared_processor(dialect_id: str) -> BasePostProcessor:
    """通过方言插件编译注册后取处理器实例（每次 fresh registry，避免实例缓存串味）。"""
    from app.postprocessor.dialect.registry import DialectRegistry
    from app.postprocessor.registry import PostProcessorRegistry

    registry = PostProcessorRegistry()
    dialect_registry = DialectRegistry(plugin_root=_DIALECT_PLUGIN_DIR)
    dialect_registry.discover()
    dialect_registry.compile_all()
    dialect_registry.register_to(registry)
    return registry.get_processor(dialect_id)


@pytest.mark.regression
@pytest.mark.parametrize("dialect_id", DECLARED_ONLY_DIALECTS)
@pytest.mark.parametrize("sequence", ["standard", "extended"])
def test_golden_declared_dialect_matches(dialect_id: str, sequence: str, monkeypatch):
    """声明式方言（hooks 模式）黄金比对：标准 + 扩展序列。"""
    monkeypatch.setattr(BasePostProcessor, "_date_string", staticmethod(lambda: FIXED_DATE))
    processor = _get_declared_processor(dialect_id)
    output = build_standard_program(processor) if sequence == "standard" else build_extended_program(processor)
    suffix = "" if sequence == "standard" else "_extended"
    golden_path = GOLDEN_DIR / f"{dialect_id}{suffix}.nc"

    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(output, encoding="utf-8")
        pytest.skip(f"UPDATE_GOLDEN=1：已重新生成 {dialect_id}{suffix} 黄金文件")

    if not golden_path.exists():
        pytest.fail(
            f"声明式方言黄金文件缺失: {golden_path}。建议操作：审阅当前输出后执行 "
            f"UPDATE_GOLDEN=1 pytest tests/regression/test_postprocessor_golden.py 生成。"
        )

    golden = golden_path.read_text(encoding="utf-8")
    assert output == golden, (
        f"声明式方言 {dialect_id}（{sequence}）输出与黄金文件不一致。建议操作：若变更为预期，"
        f"执行 UPDATE_GOLDEN=1 更新黄金文件；否则回退方言/模板/hooks 改动。"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
