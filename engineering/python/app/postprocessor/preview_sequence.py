"""方言预览样例序列（生产代码）。

对应 docs/development/postprocessor-方言声明化设计.md §5.1「预览器」：
给定样例刀路输入，渲染方言完整 NC 输出。

本模块提供与黄金测试一致的标准加工序列（header → 换刀 → 补偿 → 冷却 →
快移 → 直线 → 圆弧 → 钻孔 → 冷却关 → 程序尾），供方言预览 API 使用。
黄金测试（tests/regression/test_postprocessor_golden.py）从本模块导入同一
序列，保证「预览输出」与「黄金基线」行为一致。
"""

from __future__ import annotations

from app.postprocessor.base import BasePostProcessor


def build_standard_program(processor: BasePostProcessor, program_number: int = 1000) -> str:
    """用一套确定性加工序列生成完整 NC 程序文本。

    坐标统一控制在 ±50mm 内：XM100 桌面五轴机行程仅 ±50mm，需兼容所有控制器。
    ``program_number`` 可定制（默认 1000，与 golden 测试基线一致）。
    """
    lines = [
        processor.format_header(program_number=program_number),
        processor.format_tool_change(tool_id=1, length_comp=50.0, radius_comp=5.0),
        processor.format_tool_compensation(length_offset=1, radius_offset=1),
        processor.format_coolant("on"),
        processor.format_rapid_move(0.0, 0.0, 30.0),
        processor.format_linear_move(10.0, 20.0, 25.0, feed=500.0),
        processor.format_arc((10.0, 20.0, 25.0), (20.0, 30.0, 25.0), (15.0, 25.0, 25.0), clockwise=True),
        processor.format_cycle_drill(x=20.0, y=30.0, z=25.0, depth=8.0, dwell=0.5),
        processor.format_coolant("off"),
        processor.format_footer(),
    ]
    return "\n".join(lines) + "\n"


__all__ = ["build_standard_program"]
