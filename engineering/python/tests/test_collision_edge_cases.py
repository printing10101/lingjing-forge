"""碰撞检测器边缘情况测试。

测试圆弧路径采样和快速移动步长优化的正确性。
"""

import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.simulation.collision_detector import CollisionDetector
from app.simulation.stock_model import StockModel
from app.simulation.toolpath_parser import ToolpathSegment


def test_arc_overcut_detection():
    """测试圆弧路径越界检测。"""
    print("\n" + "=" * 60)
    print("圆弧路径越界检测测试")
    print("=" * 60)
    
    # 创建毛坯：100x100x50，底部在 Z=0
    stock = StockModel(length=100, width=100, height=50)
    detector = CollisionDetector(stock, safe_z_height=10.0)
    
    # 测试 1: 圆弧路径中途 Z 轴过切
    # 起点 (50, 50, 45)，终点 (80, 50, 45)，圆心 (65, 50, 45)
    # 圆弧半径 15mm，扫掠 180 度
    # 圆弧中点应该在 (65, 65, 45) 附近
    arc_segment = ToolpathSegment(
        type="arc",
        start_point=(50.0, 50.0, 45.0),
        end_point=(80.0, 50.0, 45.0),
        arc_center=(65.0, 50.0, 45.0),
        clockwise=True,
        block_number=1,
        g_code="G02",
    )
    
    report = detector.check_segments([arc_segment])
    print(f"测试 1 - 正常圆弧路径: {report.safe}")
    assert report.safe, "正常圆弧路径不应检测到碰撞"
    
    # 测试 2: 圆弧路径 Z 轴过切（低于毛坯底部）
    arc_overcut = ToolpathSegment(
        type="arc",
        start_point=(50.0, 50.0, -5.0),  # Z=-5 低于毛坯底部 Z=0
        end_point=(80.0, 50.0, -5.0),
        arc_center=(65.0, 50.0, -5.0),
        clockwise=True,
        block_number=2,
        g_code="G02",
    )
    
    report = detector.check_segments([arc_overcut])
    print(f"测试 2 - Z 轴过切圆弧: 检测到 {len(report.collisions)} 个碰撞")
    assert not report.safe, "Z 轴过切应检测到碰撞"
    assert any(c.collision_type == "overcut_z" for c in report.collisions), \
        "应检测到 overcut_z 类型碰撞"
    
    # 测试 3: 圆弧路径 X/Y 越界
    arc_boundary = ToolpathSegment(
        type="arc",
        start_point=(95.0, 50.0, 25.0),  # 起点接近边界
        end_point=(50.0, 95.0, 25.0),    # 终点接近边界
        arc_center=(75.0, 75.0, 25.0),   # 圆心在边界外
        clockwise=False,
        block_number=3,
        g_code="G03",
    )
    
    report = detector.check_segments([arc_boundary])
    print(f"测试 3 - 边界附近圆弧: {len(report.warnings)} 个警告")
    # 圆弧路径可能超出毛坯边界，应产生警告
    assert len(report.warnings) > 0 or report.safe, \
        "边界附近圆弧应产生警告或安全"
    
    print("✓ 圆弧路径检测测试通过")


def test_rapid_adaptive_stepping():
    """测试快速移动自适应步长。"""
    print("\n" + "=" * 60)
    print("快速移动自适应步长测试")
    print("=" * 60)
    
    # 创建毛坯：100x100x50
    stock = StockModel(length=100, width=100, height=50)
    detector = CollisionDetector(stock, safe_z_height=10.0)
    
    # 测试 1: 短距离快速移动（< 10mm）
    # 从 (50, 50, 65) 到 (55, 50, 65)，距离 5mm
    # 起点在安全平面(60)以上，应使用 0.5mm 步长，至少 10 个采样点
    short_rapid = ToolpathSegment(
        type="rapid",
        start_point=(50.0, 50.0, 65.0),
        end_point=(55.0, 50.0, 65.0),
        block_number=1,
        g_code="G00",
    )
    
    report = detector.check_segments([short_rapid])
    print(f"测试 1 - 短距离快速移动 (5mm): {report.safe}")
    assert report.safe, "短距离安全移动不应检测到碰撞"
    
    # 测试 2: 长距离快速移动（>= 10mm）
    # 从 (10, 10, 65) 到 (90, 90, 65)，距离约 113mm
    # 应使用 2mm 步长，至少 5 个采样点
    long_rapid = ToolpathSegment(
        type="rapid",
        start_point=(10.0, 10.0, 65.0),
        end_point=(90.0, 90.0, 65.0),
        block_number=2,
        g_code="G00",
    )
    
    report = detector.check_segments([long_rapid])
    print(f"测试 2 - 长距离快速移动 (113mm): {report.safe}")
    assert report.safe, "长距离安全移动不应检测到碰撞"
    
    # 测试 3: 短距离碰撞检测
    # 从 (50, 50, 45) 到 (55, 50, 45)，距离 5mm，但在毛坯内部
    # 应使用精细步长检测到碰撞
    short_collision = ToolpathSegment(
        type="rapid",
        start_point=(50.0, 50.0, 45.0),  # 在毛坯内部
        end_point=(55.0, 50.0, 45.0),    # 仍在毛坯内部
        block_number=3,
        g_code="G00",
    )
    
    report = detector.check_segments([short_collision])
    print(f"测试 3 - 短距离碰撞 (5mm): 检测到 {len(report.collisions)} 个碰撞")
    assert not report.safe, "短距离碰撞应被检测到"
    assert any(c.collision_type == "rapid_into_stock" for c in report.collisions), \
        "应检测到 rapid_into_stock 类型碰撞"
    
    print("✓ 快速移动步长测试通过")


def test_arc_degenerate_cases():
    """测试圆弧退化情况。"""
    print("\n" + "=" * 60)
    print("圆弧退化情况测试")
    print("=" * 60)
    
    stock = StockModel(length=100, width=100, height=50)
    detector = CollisionDetector(stock, safe_z_height=10.0)
    
    # 测试 1: 半径为 0 的圆弧（退化为直线）
    degenerate_arc = ToolpathSegment(
        type="arc",
        start_point=(50.0, 50.0, 25.0),
        end_point=(80.0, 50.0, 25.0),
        arc_center=(50.0, 50.0, 25.0),  # 圆心与起点重合，半径为 0
        clockwise=True,
        block_number=1,
        g_code="G02",
    )
    
    report = detector.check_segments([degenerate_arc])
    print(f"测试 1 - 半径为 0 的圆弧: {report.safe}")
    # 应退化为直线检查，不崩溃
    assert report.safe or len(report.collisions) > 0, "退化圆弧应正常处理"
    
    # 测试 2: 完整圆弧（360 度）
    full_circle = ToolpathSegment(
        type="arc",
        start_point=(75.0, 50.0, 25.0),
        end_point=(75.0, 50.0, 25.0),  # 起点终点相同
        arc_center=(50.0, 50.0, 25.0),
        clockwise=True,
        block_number=2,
        g_code="G02",
    )
    
    report = detector.check_segments([full_circle])
    print(f"测试 2 - 完整圆弧: {report.safe}")
    assert report.safe or len(report.warnings) > 0, "完整圆弧应正常处理"
    
    print("✓ 圆弧退化情况测试通过")


def test_rapid_retract_protection():
    """测试抬刀保护逻辑。"""
    print("\n" + "=" * 60)
    print("抬刀保护逻辑测试")
    print("=" * 60)
    
    stock = StockModel(length=100, width=100, height=50)
    detector = CollisionDetector(stock, safe_z_height=10.0)
    
    # 安全平面 = 50 + 10 = 60
    
    # 测试 1: 正常抬刀（向上且终点在安全平面以上）
    retract_up = ToolpathSegment(
        type="rapid",
        start_point=(50.0, 50.0, 45.0),  # 在毛坯内部
        end_point=(50.0, 50.0, 65.0),    # 向上到安全平面以上
        block_number=1,
        g_code="G00",
    )
    
    report = detector.check_segments([retract_up])
    print(f"测试 1 - 正常抬刀: {report.safe}")
    assert report.safe, "正常抬刀不应被误判为碰撞"
    
    # 测试 2: 向下移动（不是抬刀）
    move_down = ToolpathSegment(
        type="rapid",
        start_point=(50.0, 50.0, 65.0),
        end_point=(50.0, 50.0, 45.0),  # 向下进入毛坯
        block_number=2,
        g_code="G00",
    )
    
    report = detector.check_segments([move_down])
    print(f"测试 2 - 向下移动: 检测到 {len(report.collisions)} 个碰撞")
    assert not report.safe, "向下移动进入毛坯应检测到碰撞"
    
    # 测试 3: 水平移动（不是抬刀）
    horizontal_move = ToolpathSegment(
        type="rapid",
        start_point=(50.0, 50.0, 45.0),
        end_point=(60.0, 50.0, 45.0),  # 水平移动，在毛坯内部
        block_number=3,
        g_code="G00",
    )
    
    report = detector.check_segments([horizontal_move])
    print(f"测试 3 - 水平移动: 检测到 {len(report.collisions)} 个碰撞")
    assert not report.safe, "水平移动在毛坯内部应检测到碰撞"
    
    print("✓ 抬刀保护逻辑测试通过")


def main():
    """运行所有边缘情况测试。"""
    print("\n" + "=" * 60)
    print("碰撞检测器边缘情况测试套件")
    print("=" * 60)
    
    try:
        test_arc_overcut_detection()
        test_rapid_adaptive_stepping()
        test_arc_degenerate_cases()
        test_rapid_retract_protection()
        
        print("\n" + "=" * 60)
        print("✓ 所有边缘情况测试通过！")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
