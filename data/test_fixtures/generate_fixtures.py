"""合成 20 个真实风格的 DXF 测试样本。

覆盖：
- 简单 box（无孔）
- box + 多个圆孔
- LWPOLYLINE 外轮廓（不同形状）
- LWPOLYLINE 外轮廓 + 内孔
- 复杂面板（含多个孔+文字）
- 法兰盘（外圆 + 螺栓孔 + 中心孔）
- 支架（带切角）
- 齿轮毛坯（外圆 + 中心孔 + 键槽）
- 矩形块（多种尺寸）
- 工字形轮廓
- 多孔板（多孔阵列）
- 三角形支撑
- 异形件（圆角矩形）
- 圆形盘
- 椭圆轮廓
- 不规则外轮廓
- 含弧形轮廓
- 链轮
- 联轴器
- 异形键槽
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import ezdxf

logger = logging.getLogger(__name__)


def _add_simple_box(msp, x0: float, y0: float, w: float, h: float):
    """在 msp 上画一个简单的矩形（4 条 LINE）。"""
    pts = [
        (x0, y0),
        (x0 + w, y0),
        (x0 + w, y0 + h),
        (x0, y0 + h),
    ]
    for i in range(4):
        a = pts[i]
        b = pts[(i + 1) % 4]
        msp.add_line(a, b, dxfattribs={"layer": "0"})


def _add_hole(msp, cx: float, cy: float, r: float):
    """在 msp 上画一个圆孔（CIRCLE）。"""
    msp.add_circle((cx, cy), r, dxfattribs={"layer": "0"})


def _add_arc(msp, cx, cy, r, start_deg, end_deg):
    """画一段圆弧。"""
    msp.add_arc(
        (cx, cy), r,
        start_angle=math.radians(start_deg),
        end_angle=math.radians(end_deg),
        dxfattribs={"layer": "0"},
    )


def _add_polyline_outer(msp, x0: float, y0: float, w: float, h: float):
    """在 msp 上画一个 LWPOLYLINE 外轮廓。"""
    pts = [
        (x0, y0, 0.0),
        (x0 + w, y0, 0.0),
        (x0 + w, y0 + h, 0.0),
        (x0, y0 + h, 0.0),
    ]
    msp.add_lwpolyline(pts, dxfattribs={"layer": "0", "closed": True})


def _add_polyline_hole(msp, cx: float, cy: float, r: float, n: int = 16):
    """在 msp 上画一个 LWPOLYLINE 内孔。"""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a), 0.0))
    msp.add_lwpolyline(pts, dxfattribs={"layer": "0", "closed": True})


def _add_text(msp, x: float, y: float, text: str, height: float = 5.0):
    """在 msp 上加一个文字。"""
    msp.add_text(text, dxfattribs={"layer": "TEXT", "height": height}).set_placement(
        (x, y)
    )


def make_simple_box(path: Path) -> None:
    """case 1: 简单 box。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_simple_box(msp, 0, 0, 100, 60)
    _add_text(msp, 50, 30, "Simple Box 100x60", height=4.0)
    doc.saveas(str(path))


def make_box_with_4_holes(path: Path) -> None:
    """case 2: box + 4 个圆孔。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_simple_box(msp, 0, 0, 100, 100)
    _add_hole(msp, 25, 25, 5)
    _add_hole(msp, 75, 25, 5)
    _add_hole(msp, 25, 75, 5)
    _add_hole(msp, 75, 75, 5)
    _add_text(msp, 50, 50, "Box+4 Holes 100x100", height=4.0)
    doc.saveas(str(path))


def make_polyline_outer(path: Path) -> None:
    """case 3: LWPOLYLINE 外轮廓。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_polyline_outer(msp, 0, 0, 80, 50)
    _add_text(msp, 40, 25, "Polyline Outline 80x50", height=4.0)
    doc.saveas(str(path))


def make_polyline_outer_with_hole(path: Path) -> None:
    """case 4: LWPOLYLINE 外轮廓 + 内孔。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_polyline_outer(msp, 0, 0, 80, 80)
    _add_polyline_hole(msp, 40, 40, 15)
    _add_text(msp, 60, 10, "Outline+Hole 80x80", height=4.0)
    doc.saveas(str(path))


def make_complex_panel(path: Path) -> None:
    """case 5: 复杂面板（外轮廓 + 多个孔 + 多个孔 + 文字）。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_simple_box(msp, 0, 0, 200, 100)
    _add_hole(msp, 20, 20, 4)
    _add_hole(msp, 180, 20, 4)
    _add_hole(msp, 20, 80, 4)
    _add_hole(msp, 180, 80, 4)
    _add_hole(msp, 100, 50, 10)  # 中央大孔
    _add_text(msp, 100, 50, "Mounting Panel 200x100", height=5.0)
    _add_text(msp, 100, 30, "Material: 45 Steel", height=3.0)
    doc.saveas(str(path))


def make_flange(path: Path) -> None:
    """case 6: 法兰盘 - 大外圆 + 中心孔 + 6 个螺栓孔。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 100, dxfattribs={"layer": "0"})  # 外圆
    msp.add_circle((0, 0), 30, dxfattribs={"layer": "0"})   # 中心孔
    # 6 个螺栓孔，均布在 r=70 圆上
    for i in range(6):
        a = 2 * math.pi * i / 6
        x = 70 * math.cos(a)
        y = 70 * math.sin(a)
        msp.add_circle((x, y), 8, dxfattribs={"layer": "0"})
    _add_text(msp, 0, 110, "Flange D200 PCD140 6xD16", height=8.0)
    doc.saveas(str(path))


def make_bracket(path: Path) -> None:
    """case 7: L 型支架。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # 主体矩形
    _add_simple_box(msp, 0, 0, 120, 80)
    # 切角（左上 20x20 三角）
    msp.add_line((0, 0), (20, 0), dxfattribs={"layer": "0"})
    msp.add_line((20, 0), (0, 20), dxfattribs={"layer": "0"})
    # 右下切角
    msp.add_line((120, 80), (100, 80), dxfattribs={"layer": "0"})
    msp.add_line((100, 80), (120, 60), dxfattribs={"layer": "0"})
    # 安装孔
    _add_hole(msp, 30, 30, 6)
    _add_hole(msp, 90, 50, 6)
    _add_text(msp, 60, 40, "L-Bracket 120x80", height=5.0)
    doc.saveas(str(path))


def make_gear_blank(path: Path) -> None:
    """case 8: 齿轮毛坯 - 外圆 + 中心孔 + 键槽。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 80, dxfattribs={"layer": "0"})
    msp.add_circle((0, 0), 15, dxfattribs={"layer": "0"})
    # 键槽（圆孔 + 矩形外延）
    msp.add_line((15, 0), (40, 0), dxfattribs={"layer": "0"})
    msp.add_line((40, 0), (40, 5), dxfattribs={"layer": "0"})
    msp.add_line((40, 5), (15, 5), dxfattribs={"layer": "0"})
    msp.add_line((15, 5), (15, 0), dxfattribs={"layer": "0"})
    _add_text(msp, 0, -100, "Gear Blank D160", height=6.0)
    doc.saveas(str(path))


def make_rect_block_50x80(path: Path) -> None:
    """case 9: 矩形块 50x80 + 2 个安装孔。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_polyline_outer(msp, 0, 0, 50, 80)
    _add_polyline_hole(msp, 10, 10, 3)
    _add_polyline_hole(msp, 40, 70, 3)
    _add_text(msp, 25, 40, "Block 50x80", height=4.0)
    doc.saveas(str(path))


def make_i_shape(path: Path) -> None:
    """case 10: 工字形轮廓。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # 上翼
    msp.add_lwpolyline([
        (0, 0, 0), (100, 0, 0), (100, 15, 0),
        (60, 15, 0), (60, 35, 0), (100, 35, 0),
        (100, 50, 0), (0, 50, 0), (0, 35, 0),
        (40, 35, 0), (40, 15, 0), (0, 15, 0),
    ], dxfattribs={"layer": "0", "closed": True})
    _add_text(msp, 50, 25, "I-Section 100x50", height=4.0)
    doc.saveas(str(path))


def make_perforated_plate(path: Path) -> None:
    """case 11: 多孔板 - 5x5 圆孔阵列。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    _add_polyline_outer(msp, 0, 0, 100, 100)
    for i in range(5):
        for j in range(5):
            _add_hole(msp, 10 + i * 20, 10 + j * 20, 3)
    _add_text(msp, 50, 50, "5x5 Hole Plate", height=4.0)
    doc.saveas(str(path))


def make_triangle_support(path: Path) -> None:
    """case 12: 三角形支撑。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([
        (0, 0, 0), (120, 0, 0), (60, 100, 0),
    ], dxfattribs={"layer": "0", "closed": True})
    _add_hole(msp, 60, 20, 8)
    _add_text(msp, 60, 80, "Tri-Support 120x100", height=4.0)
    doc.saveas(str(path))


def make_rounded_rect(path: Path) -> None:
    """case 13: 圆角矩形 - 用 polyline 带 bulge 模拟圆角。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # 圆角矩形：4 个直边 + 4 个圆角（bulge 实现）
    msp.add_lwpolyline([
        (10, 0, 0),
        (90, 0, 0),
        (100, 10, 0.4142),   # bulge = tan(90°/4) ≈ 0.4142
        (100, 90, 0),
        (90, 100, 0.4142),
        (10, 100, 0),
        (0, 90, 0.4142),
        (0, 10, 0),
        (10, 0, 0.4142),
    ], dxfattribs={"layer": "0", "closed": True})
    _add_text(msp, 50, 50, "Rounded Rect 100x100 R10", height=4.0)
    doc.saveas(str(path))


def make_round_disk(path: Path) -> None:
    """case 14: 圆形盘。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 60, dxfattribs={"layer": "0"})
    _add_text(msp, 0, 70, "Round Disk D120", height=5.0)
    doc.saveas(str(path))


def make_ellipse_outline(path: Path) -> None:
    """case 15: 椭圆轮廓（用 polyline 模拟）。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    n = 36
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        x = 80 * math.cos(a)
        y = 50 * math.sin(a)
        pts.append((x, y, 0.0))
    msp.add_lwpolyline(pts, dxfattribs={"layer": "0", "closed": True})
    _add_text(msp, 0, -60, "Ellipse 160x100", height=4.0)
    doc.saveas(str(path))


def make_irregular(path: Path) -> None:
    """case 16: 不规则外轮廓。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([
        (0, 0, 0), (100, 0, 0), (110, 30, 0),
        (90, 60, 0), (60, 50, 0), (40, 80, 0),
        (0, 70, 0),
    ], dxfattribs={"layer": "0", "closed": True})
    _add_hole(msp, 50, 30, 5)
    _add_text(msp, 50, 40, "Irregular Part", height=4.0)
    doc.saveas(str(path))


def make_arc_profile(path: Path) -> None:
    """case 17: 圆弧外轮廓。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_arc((50, 50), 60, start_angle=0, end_angle=math.radians(180),
                dxfattribs={"layer": "0"})
    msp.add_line((50 - 60, 50), (50 + 60, 50), dxfattribs={"layer": "0"})
    _add_text(msp, 50, 60, "Arc Profile R60", height=4.0)
    doc.saveas(str(path))


def make_sprocket(path: Path) -> None:
    """case 18: 链轮 - 外圆 + 中心孔 + 8 个销孔。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 90, dxfattribs={"layer": "0"})
    msp.add_circle((0, 0), 20, dxfattribs={"layer": "0"})
    for i in range(8):
        a = 2 * math.pi * i / 8
        x = 60 * math.cos(a)
        y = 60 * math.sin(a)
        msp.add_circle((x, y), 6, dxfattribs={"layer": "0"})
    _add_text(msp, 0, 110, "Sprocket D180 8xD12", height=6.0)
    doc.saveas(str(path))


def make_coupling(path: Path) -> None:
    """case 19: 联轴器 - 双孔 + 销孔。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # 两个相对的 D 形孔（用半圆 + 直径）
    msp.add_circle((0, 0), 50, dxfattribs={"layer": "0"})
    msp.add_circle((0, 0), 15, dxfattribs={"layer": "0"})
    # 6 个螺栓孔
    for i in range(6):
        a = math.pi * i / 3
        x = 35 * math.cos(a)
        y = 35 * math.sin(a)
        msp.add_circle((x, y), 5, dxfattribs={"layer": "0"})
    _add_text(msp, 0, 60, "Coupling D100", height=4.0)
    doc.saveas(str(path))


def make_special_keyway(path: Path) -> None:
    """case 20: 异形键槽。"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 40, dxfattribs={"layer": "0"})
    msp.add_circle((0, 0), 10, dxfattribs={"layer": "0"})
    # 异形键槽：圆头矩形（用 polyline）
    msp.add_lwpolyline([
        (10, -3, 0), (25, -3, 0.5523), (30, 0, 0),
        (25, 3, 0.5523), (10, 3, 0),
    ], dxfattribs={"layer": "0", "closed": True})
    _add_text(msp, 0, 50, "Keyway Special", height=4.0)
    doc.saveas(str(path))


FIXTURES = {
    "case1_simple_box.dxf": make_simple_box,
    "case2_box_4holes.dxf": make_box_with_4_holes,
    "case3_polyline_outer.dxf": make_polyline_outer,
    "case4_polyline_outer_with_hole.dxf": make_polyline_outer_with_hole,
    "case5_complex_panel.dxf": make_complex_panel,
    "case6_flange.dxf": make_flange,
    "case7_bracket.dxf": make_bracket,
    "case8_gear_blank.dxf": make_gear_blank,
    "case9_rect_block.dxf": make_rect_block_50x80,
    "case10_i_shape.dxf": make_i_shape,
    "case11_perforated_plate.dxf": make_perforated_plate,
    "case12_triangle_support.dxf": make_triangle_support,
    "case13_rounded_rect.dxf": make_rounded_rect,
    "case14_round_disk.dxf": make_round_disk,
    "case15_ellipse_outline.dxf": make_ellipse_outline,
    "case16_irregular.dxf": make_irregular,
    "case17_arc_profile.dxf": make_arc_profile,
    "case18_sprocket.dxf": make_sprocket,
    "case19_coupling.dxf": make_coupling,
    "case20_special_keyway.dxf": make_special_keyway,
}


def generate_all(out_dir: str) -> list[str]:
    """生成所有 20 个 DXF 到指定目录。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, maker in FIXTURES.items():
        path = out / name
        maker(path)
        paths.append(str(path))
        logger.info("生成 DXF: %s", path)
    return paths


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "data/test_fixtures"
    paths = generate_all(target)
    for p in paths:
        print(p)
