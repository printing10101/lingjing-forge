"""工厂就绪性深度自检脚本。

验证多控制器/固定循环/边界场景下的碰撞检测。
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.simulation.toolpath_parser import ToolpathParser
from app.simulation.collision_detector import CollisionDetector
from app.simulation.stock_model import StockModel
from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor


def check_controller_collision(controller_name: str, gcode: str, stock: StockModel) -> bool:
    """测试指定控制器的G代码是否有致命碰撞。"""
    parser = ToolpathParser(controller_type=controller_name)
    segments = parser.parse_gcode(gcode)

    detector = CollisionDetector(stock=stock, safe_z_height=10.0)
    report = detector.check_segments(segments)

    high_severity = [c for c in report.collisions if c.severity == "high"]

    print(f"\n{'=' * 60}")
    print(f"控制器: {controller_name}")
    print(f"解析段数: {len(segments)}")
    print(f"碰撞事件: {len(report.collisions)} (高严重度: {len(high_severity)})")

    if high_severity:
        print(f"结果: FAIL")
        for c in high_severity:
            print(f"  - N{c.block_number}: {c.message}")
        return False
    else:
        print(f"结果: PASS")
        return True


def main():
    """运行工厂就绪性自检。"""
    print("=" * 60)
    print("工厂就绪性深度自检")
    print("=" * 60)

    # 创建标准毛坯: 200x150x50
    stock = StockModel(length=200, width=150, height=50)

    results = {}

    # 测试1: Fanuc 0i
    fanuc = FanucPostProcessor(safe_z_height=80.0)
    fanuc_gcode = fanuc.format_header(1000)
    results["Fanuc 0i"] = check_controller_collision("fanuc", fanuc_gcode, stock)

    # 测试2: Siemens 840D
    siemens = SiemensPostProcessor(safe_z_height=80.0)
    siemens_gcode = siemens.format_header(1000)
    results["Siemens 840D"] = check_controller_collision("siemens", siemens_gcode, stock)

    # 测试3: Heidenhain TNC
    heidenhain = HeidenhainPostProcessor(safe_z_height=80.0)
    heidenhain_gcode = heidenhain.format_header(1000)

    # 打印 Heidenhain G代码前15行用于调试
    print("\n=== Heidenhain G代码（前15行）===")
    for i, line in enumerate(heidenhain_gcode.splitlines()[:15], 1):
        print(f"{i}: {line}")

    # 解析并打印段
    parser = ToolpathParser(controller_type="heidenhain")
    segments = parser.parse_gcode(heidenhain_gcode)
    print(f"\n=== 解析结果（前10段）===")
    for seg in segments[:10]:
        print(f"N{seg.block_number}: {seg.type} {seg.start_point} -> {seg.end_point}")

    results["Heidenhain TNC"] = check_controller_collision("heidenhain", heidenhain_gcode, stock)

    # 测试4: 钻孔深度边界
    print(f"\n{'=' * 60}")
    print("钻孔深度边界测试")
    print("=" * 60)
    for depth in [5, 15, 25, 49]:
        fanuc = FanucPostProcessor(safe_z_height=80.0)
        drill_gcode = fanuc.format_header(1000)
        drill_gcode += "\n" + fanuc.format_cycle_drill(50, 50, -depth, depth)
        drill_gcode += "\n" + fanuc.format_footer()

        parser = ToolpathParser(controller_type="fanuc")
        segments = parser.parse_gcode(drill_gcode)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        report = detector.check_segments(segments)
        high_severity = [c for c in report.collisions if c.severity == "high"]

        status = "PASS" if not high_severity else "FAIL"
        print(f"钻孔深度 {depth}mm: {status} (碰撞: {len(high_severity)})")
        results[f"Drill {depth}mm"] = not high_severity

    # 测试5: 铣削切深边界
    print(f"\n{'=' * 60}")
    print("铣削切深边界测试")
    print("=" * 60)
    # 毛坯顶面 Z=50，切削深度基于顶面向下计算
    stock_top_z = 50.0
    for depth in [0.5, 1.5, 5, 10, 49]:
        fanuc = FanucPostProcessor(safe_z_height=80.0)
        mill_gcode = fanuc.format_header(1000)
        # 生成铣削路径：Z 坐标 = 毛坯顶面 - 切深
        mill_z = stock_top_z - depth
        mill_gcode += f"\nG00 Z80.000"
        mill_gcode += f"\nG00 X50.000 Y50.000"
        mill_gcode += f"\nG01 Z{mill_z:.3f} F500"
        mill_gcode += f"\nG01 X100.000 F1000"
        mill_gcode += f"\nG00 Z80.000"
        mill_gcode += "\n" + fanuc.format_footer()

        parser = ToolpathParser(controller_type="fanuc")
        segments = parser.parse_gcode(mill_gcode)
        detector = CollisionDetector(stock=stock, safe_z_height=10.0)
        report = detector.check_segments(segments)
        high_severity = [c for c in report.collisions if c.severity == "high"]

        status = "PASS" if not high_severity else "FAIL"
        print(f"铣削切深 {depth}mm: {status} (碰撞: {len(high_severity)})")
        results[f"Mill {depth}mm"] = not high_severity

    # 汇总结果
    print(f"\n{'=' * 60}")
    print("工厂就绪性自检汇总")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, status in results.items():
        print(f"  {name}: {'PASS' if status else 'FAIL'}")

    print(f"\n总计: {passed}/{total} 通过")

    if passed == total:
        print("\n✓ 所有测试通过！系统已具备工厂就绪性。")
        return 0
    else:
        print("\n✗ 存在失败项，需要修复后才能部署到工厂。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
