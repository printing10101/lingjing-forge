"""XM-100 桌面级五轴CNC示例G代码生成器。

本脚本演示如何使用 XMachineXM100PostProcessor 生成符合 XM-100 物理约束的 G 代码，
覆盖以下典型场景：

1. 三轴铣削（平面加工、外形铣削）
2. 五轴 RTCP 联动加工（G43.4）
3. 五轴 TWP 刀轴控制（G43.5）
4. 钻孔固定循环
5. 圆弧插补

生成的 G 代码严格遵循 XM-100 物理约束：
- 工作空间：100×100×100mm（±50mm）
- 主轴：1000-20000 RPM
- 进给：10-3000 mm/min
- A轴：-30°~110° / C轴：0°~360°

使用方法：
    python python/scripts/generate_xm100_demo_gcode.py

输出：
    python/output/xm100_demo_3axis_milling.nc
    python/output/xm100_demo_5axis_rtcp.nc
    python/output/xm100_demo_5axis_twp.nc
    python/output/xm100_demo_drilling.nc
    python/output/xm100_demo_arc.nc
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 将 python 目录加入 sys.path，便于直接运行脚本
_THIS_DIR = Path(__file__).resolve().parent
_PYTHON_DIR = _THIS_DIR.parent
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from app.postprocessor.xmachine import XMachineXM100PostProcessor


OUTPUT_DIR = _PYTHON_DIR / "output" / "xm100_demo"


def make_processor() -> XMachineXM100PostProcessor:
    """创建 XM-100 后处理器实例（使用默认配置）。"""
    return XMachineXM100PostProcessor(
        decimal_places=3,
        safe_z_height=30.0,
        rapid_feed=2000.0,
    )


def gen_3axis_milling(p: XMachineXM100PostProcessor) -> str:
    """生成三轴铣削示例：50×50mm 方形外形铣削。

    场景：在铝块上铣削 50×50mm 方形凹槽，深度 5mm，分两层切削。
    刀具：φ6 平底立铣刀
    """
    lines: list[str] = []
    lines.append(p.format_header(program_number=1001))
    lines.append("(================================================)")
    lines.append("( XM-100 示例 1: 三轴外形铣削                    )")
    lines.append("( 工件: 50x50x10mm 铝块 (Al6061)                  )")
    lines.append("( 刀具: φ6 硬质合金平底立铣刀 (T01)               )")
    lines.append("( 加工: 50x50mm 方形外形, 深5mm, 分2层            )")
    lines.append("(================================================)")
    lines.append(p.format_tool_change(tool_id=1, length_comp=0.0))

    # 第一层切削：深度 2.5mm
    lines.append("")
    lines.append("(--- 第一层切削: Z-2.5 ---)")
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=10.0))
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-2.5, feed=300))
    lines.append(p.format_linear_move(x=50.0, y=0.0, z=-2.5, feed=500))
    lines.append(p.format_linear_move(x=50.0, y=50.0, z=-2.5, feed=500))
    lines.append(p.format_linear_move(x=0.0, y=50.0, z=-2.5, feed=500))
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-2.5, feed=500))

    # 第二层切削：深度 5.0mm
    lines.append("")
    lines.append("(--- 第二层切削: Z-5.0 ---)")
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=10.0))
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-5.0, feed=300))
    lines.append(p.format_linear_move(x=50.0, y=0.0, z=-5.0, feed=500))
    lines.append(p.format_linear_move(x=50.0, y=50.0, z=-5.0, feed=500))
    lines.append(p.format_linear_move(x=0.0, y=50.0, z=-5.0, feed=500))
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-5.0, feed=500))

    # 抬刀
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=30.0))
    lines.append(p.format_footer())
    return "\n".join(lines)


def gen_5axis_rtcp(p: XMachineXM100PostProcessor) -> str:
    """生成五轴 RTCP 联动示例：斜面雕刻。

    场景：在 30° 倾斜的平面上加工一个圆形图案，使用 G43.4 RTCP 模式。
    刀具：φ3 球头铣刀
    """
    lines: list[str] = []
    lines.append(p.format_header(program_number=1002))
    lines.append("(================================================)")
    lines.append("( XM-100 示例 2: 五轴 RTCP 斜面加工              )")
    lines.append("( 工件: 30° 倾斜平面                              )")
    lines.append("( 刀具: φ3 硬质合金球头铣刀 (T02)                 )")
    lines.append("( 模式: G43.4 RTCP (旋转刀具中心点)              )")
    lines.append("(================================================)")
    lines.append(p.format_tool_change(tool_id=2, length_comp=0.0))

    # 配置旋转轴
    lines.append("")
    lines.append("(--- 旋转轴配置 ---)")
    lines.append(p.format_rotary_axis_config(a_axis_zero=0.0, c_axis_zero=0.0))

    # 开启 RTCP 模式
    lines.append("")
    lines.append("(--- 开启 RTCP 模式 ---)")
    lines.append(p.format_rtcp_on(tool_length=50.0))

    # A 轴倾斜 30°，C 轴保持 0°
    lines.append("")
    lines.append("(--- A轴倾斜30°，加工斜面 ---)")
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=5.0, a=30.0, c=0.0))
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-1.0, a=30.0, c=0.0, feed=200))

    # 在斜面上走一个矩形轨迹
    lines.append(p.format_linear_move(x=30.0, y=0.0, z=-1.0, a=30.0, c=0.0, feed=400))
    lines.append(p.format_linear_move(x=30.0, y=20.0, z=-1.0, a=30.0, c=0.0, feed=400))
    lines.append(p.format_linear_move(x=0.0, y=20.0, z=-1.0, a=30.0, c=0.0, feed=400))
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-1.0, a=30.0, c=0.0, feed=400))

    # C 轴旋转 90°，加工另一侧
    lines.append("")
    lines.append("(--- C轴旋转90°，加工另一侧 ---)")
    lines.append(p.format_linear_move(x=0.0, y=0.0, z=-1.0, a=30.0, c=90.0, feed=300))
    lines.append(p.format_linear_move(x=30.0, y=0.0, z=-1.0, a=30.0, c=90.0, feed=400))
    lines.append(p.format_linear_move(x=30.0, y=20.0, z=-1.0, a=30.0, c=90.0, feed=400))
    lines.append(p.format_linear_move(x=0.0, y=20.0, z=-1.0, a=30.0, c=90.0, feed=400))

    # 抬刀并关闭 RTCP
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=30.0, a=30.0, c=90.0))
    lines.append("")
    lines.append("(--- 关闭 RTCP 模式 ---)")
    lines.append(p.format_rtcp_off())
    lines.append(p.format_footer())
    return "\n".join(lines)


def gen_5axis_twp(p: XMachineXM100PostProcessor) -> str:
    """生成五轴 TWP 刀轴控制示例：圆柱面加工。

    场景：在圆柱面上加工螺旋槽，使用 G43.5 TWP 模式指定刀轴矢量。
    刀具：φ4 球头铣刀
    """
    lines: list[str] = []
    lines.append(p.format_header(program_number=1003))
    lines.append("(================================================)")
    lines.append("( XM-100 示例 3: 五轴 TWP 圆柱面加工             )")
    lines.append("( 工件: φ30mm 圆柱面                              )")
    lines.append("( 刀具: φ4 硬质合金球头铣刀 (T03)                 )")
    lines.append("( 模式: G43.5 TWP (刀轴矢量控制)                 )")
    lines.append("(================================================)")
    lines.append(p.format_tool_change(tool_id=3, length_comp=0.0))

    # 开启 TWP 模式，刀轴垂直向下（默认）
    lines.append("")
    lines.append("(--- 开启 TWP 模式，刀轴垂直向下 ---)")
    lines.append(p.format_twp_on(tool_axis_i=0.0, tool_axis_j=0.0, tool_axis_k=1.0))

    # 起始位置
    lines.append("")
    lines.append("(--- 圆柱面螺旋槽加工 ---)")
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=5.0))

    # 螺旋走刀：每步 C 轴旋转 30°，Z 轴下降 1mm
    c_angle = 0.0
    z_depth = 0.0
    lines.append(p.format_linear_move(x=15.0, y=0.0, z=0.0, feed=200))

    for i in range(12):
        c_angle = (i + 1) * 30.0
        z_depth = -(i + 1) * 1.0
        if z_depth < -10.0:
            z_depth = -10.0
        # 刀轴矢量随 C 轴变化（简化示例）
        import math
        rad = math.radians(c_angle)
        i_vec = math.sin(rad) * 0.3
        j_vec = -math.cos(rad) * 0.3
        k_vec = math.sqrt(max(0.0, 1.0 - i_vec * i_vec - j_vec * j_vec))
        lines.append(
            f"(步 {i+1:02d}: C={c_angle:.1f}° Z={z_depth:.2f}mm "
            f"刀轴=({i_vec:.3f},{j_vec:.3f},{k_vec:.3f}))"
        )
        lines.append(
            p.format_linear_move(
                x=15.0, y=0.0, z=z_depth, feed=300, a=0.0, c=c_angle
            )
        )

    # 抬刀并关闭 TWP
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=30.0))
    lines.append("")
    lines.append("(--- 关闭 TWP 模式 ---)")
    lines.append(p.format_twp_off())
    lines.append(p.format_footer())
    return "\n".join(lines)


def gen_drilling(p: XMachineXM100PostProcessor) -> str:
    """生成钻孔示例：4×4 孔阵。

    场景：在 40×40mm 区域内钻 4×4 = 16 个 φ3mm 孔，深度 8mm。
    刀具：φ3 麻花钻
    """
    lines: list[str] = []
    lines.append(p.format_header(program_number=1004))
    lines.append("(================================================)")
    lines.append("( XM-100 示例 4: 钻孔固定循环                     )")
    lines.append("( 工件: 40x40mm 区域 4x4 孔阵                     )")
    lines.append("( 刀具: φ3 硬质合金麻花钻 (T04)                   )")
    lines.append("( 孔深: 8mm, 孔距: 10mm                           )")
    lines.append("(================================================)")
    lines.append(p.format_tool_change(tool_id=4, length_comp=0.0))

    lines.append("")
    lines.append("(--- 4x4 钻孔阵 ---)")
    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=10.0))

    # 4x4 孔阵，间距 10mm，起始 (5, 5)
    for row in range(4):
        for col in range(4):
            x = 5.0 + col * 10.0
            y = 5.0 + row * 10.0
            lines.append(f"(孔 ({row+1},{col+1}): X{x:.1f} Y{y:.1f})")
            lines.append(p.format_cycle_drill(x=x, y=y, z=10.0, depth=8.0))

    lines.append(p.format_rapid_move(x=0.0, y=0.0, z=30.0))
    lines.append(p.format_footer())
    return "\n".join(lines)


def gen_arc(p: XMachineXM100PostProcessor) -> str:
    """生成圆弧插补示例：圆形外形。

    场景：铣削 φ30mm 圆形外形，使用 G02/G03 圆弧插补。
    刀具：φ6 平底立铣刀
    """
    lines: list[str] = []
    lines.append(p.format_header(program_number=1005))
    lines.append("(================================================)")
    lines.append("( XM-100 示例 5: 圆弧插补                         )")
    lines.append("( 工件: φ30mm 圆形外形                            )")
    lines.append("( 刀具: φ6 硬质合金平底立铣刀 (T01)               )")
    lines.append("(================================================)")
    lines.append(p.format_tool_change(tool_id=1, length_comp=0.0))

    lines.append("")
    lines.append("(--- 圆形外形铣削 ---)")
    lines.append(p.format_rapid_move(x=0.0, y=-15.0, z=10.0))
    lines.append(p.format_linear_move(x=0.0, y=-15.0, z=-3.0, feed=300))

    # 4 段圆弧组成完整圆（避免单段圆弧跨度过大）
    import math
    cx, cy, r = 0.0, 0.0, 15.0
    for i in range(4):
        start_angle = -90.0 + i * 90.0
        end_angle = start_angle + 90.0
        sx = cx + r * math.cos(math.radians(start_angle))
        sy = cy + r * math.sin(math.radians(start_angle))
        ex = cx + r * math.cos(math.radians(end_angle))
        ey = cy + r * math.sin(math.radians(end_angle))
        lines.append(
            f"(弧段 {i+1}: 起点({sx:.2f},{sy:.2f}) 终点({ex:.2f},{ey:.2f}))"
        )
        lines.append(
            p.format_arc(
                start=(sx, sy, -3.0),
                end=(ex, ey, -3.0),
                center=(cx, cy, 0.0),
                clockwise=True,
            )
        )

    lines.append(p.format_rapid_move(x=0.0, y=-15.0, z=30.0))
    lines.append(p.format_footer())
    return "\n".join(lines)


def gen_machine_info_report(p: XMachineXM100PostProcessor) -> str:
    """生成机床信息报告。"""
    info = p.get_machine_info()
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("XM-100 机床信息报告")
    lines.append("=" * 60)
    lines.append(f"机床名称: {info['machine_name']}")
    lines.append(f"制造商: {info['manufacturer']}")
    lines.append(f"类型: {info['type']}")
    lines.append(f"控制器: {info['controller']}")
    lines.append("")
    lines.append("工作空间:")
    ws = info["workspace"]
    lines.append(f"  X行程: {ws['x_travel_mm']}mm")
    lines.append(f"  Y行程: {ws['y_travel_mm']}mm")
    lines.append(f"  Z行程: {ws['z_travel_mm']}mm")
    lines.append("")
    lines.append("旋转轴:")
    ra = info["rotary_axes"]
    lines.append(f"  A轴范围: {ra['a_axis_range'][0]}° ~ {ra['a_axis_range'][1]}°")
    lines.append(f"  C轴范围: {ra['c_axis_range'][0]}° ~ {ra['c_axis_range'][1]}°")
    lines.append("")
    lines.append("主轴:")
    sp = info["spindle"]
    lines.append(f"  最大转速: {sp['max_rpm']} RPM")
    lines.append(f"  功率: {sp['power_kw']} kW")
    lines.append(f"  类型: {sp['type']}")
    lines.append("")
    lines.append("进给:")
    lines.append(f"  最大进给: {info['feed']['max_rate_mm_min']} mm/min")
    lines.append("")
    lines.append("支持特性:")
    for feat in info["features"]:
        lines.append(f"  - {feat}")
    lines.append("=" * 60)
    return "\n".join(lines)


def validate_gcode(gcode: str, label: str) -> list[str]:
    """简单验证 G 代码是否符合 XM-100 约束。

    返回警告列表（空列表表示无警告）。
    """
    warnings: list[str] = []
    # 检查是否包含 XM-100 标识
    if "XM-100" not in gcode:
        warnings.append(f"[{label}] 缺少 XM-100 标识")
    # 检查是否包含程序头 %
    if not gcode.strip().startswith("%"):
        warnings.append(f"[{label}] 程序头缺少 % 标记")
    # 检查是否包含 M30 程序结束
    if "M30" not in gcode:
        warnings.append(f"[{label}] 缺少 M30 程序结束指令")
    # 检查是否有 G28 回参考点
    if "G28" not in gcode:
        warnings.append(f"[{label}] 缺少 G28 回参考点指令")
    # 检查进给是否超限（简单正则匹配 F数值）
    feeds = [float(m) for m in re.findall(r"F(\d+\.?\d*)", gcode)]
    for f in feeds:
        if f > 3000.0:
            warnings.append(f"[{label}] 进给 {f} 超过 XM-100 最大值 3000 mm/min")
        if f < 10.0 and f > 0:
            warnings.append(f"[{label}] 进给 {f} 低于 XM-100 最小值 10 mm/min")
    # 检查主轴转速
    rpms = [int(float(m)) for m in re.findall(r"S(\d+\.?\d*)", gcode)]
    for s in rpms:
        if s > 20000:
            warnings.append(f"[{label}] 主轴转速 {s} 超过 XM-100 最大值 20000 RPM")
        if s < 1000 and s > 0:
            warnings.append(f"[{label}] 主轴转速 {s} 低于 XM-100 最小值 1000 RPM")
    return warnings


def main() -> int:
    """主函数：生成所有示例 G 代码并验证。"""
    print("=" * 60)
    print("XM-100 示例 G 代码生成器")
    print("=" * 60)

    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"输出目录: {OUTPUT_DIR}")

    # 创建后处理器
    p = make_processor()

    # 打印机床信息
    print()
    print(gen_machine_info_report(p))

    # 生成所有示例
    examples: list[tuple[str, str, str]] = [
        ("xm100_demo_3axis_milling.nc", "三轴铣削", gen_3axis_milling(p)),
        ("xm100_demo_5axis_rtcp.nc", "五轴RTCP", gen_5axis_rtcp(p)),
        ("xm100_demo_5axis_twp.nc", "五轴TWP", gen_5axis_twp(p)),
        ("xm100_demo_drilling.nc", "钻孔循环", gen_drilling(p)),
        ("xm100_demo_arc.nc", "圆弧插补", gen_arc(p)),
    ]

    print()
    print("=" * 60)
    print("生成示例 G 代码")
    print("=" * 60)

    all_warnings: list[str] = []
    for filename, label, gcode in examples:
        filepath = OUTPUT_DIR / filename
        filepath.write_text(gcode, encoding="utf-8")
        line_count = gcode.count("\n") + 1
        print(f"  [OK] {label:12s} -> {filepath.name} ({line_count} 行)")

        # 验证
        warnings = validate_gcode(gcode, label)
        if warnings:
            all_warnings.extend(warnings)
            for w in warnings:
                print(f"       {w}")
        else:
            print("       验证通过：符合 XM-100 约束")

    # 汇总
    print()
    print("=" * 60)
    print("生成完成")
    print("=" * 60)
    print(f"共生成 {len(examples)} 个示例 G 代码文件")
    print(f"输出目录: {OUTPUT_DIR}")
    if all_warnings:
        print(f"警告数量: {len(all_warnings)}")
    else:
        print("所有示例验证通过，无警告")

    return 0 if not all_warnings else 1


if __name__ == "__main__":
    sys.exit(main())
