#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
灵境制造 - 应用图标生成器
================================
设计理念：
- "灵境"：星河粒子（呼应启动动画的银河星河主题）
- "制造"：齿轮 + 立方体（3D 建模 / 工艺规划）
- 配色：深色背景 (#0e0d0c) + 蓝色渐变 (#007aff → #66abff)

输出文件（覆盖 engineering/src-tauri/icons/）：
- 128x128.png
- 128x128@2x.png  (256x256)
- 32x32.png
- icon.ico        (含 16/32/48/64/128/256 多尺寸)
- icon.icns       (macOS，可选，需 macOS 系统)
"""

import math
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

# 配置（唯一活跃桌面壳位于 engineering/src-tauri/，根级 src-tauri/ 已移除）
ICONS_DIR = Path(__file__).parent.parent / "engineering" / "src-tauri" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)

# 颜色板（与应用 splashscreen/index.html 保持一致）
BG_DEEP = (14, 13, 12)  # #0e0d0c
BG_DARK = (22, 20, 18)  # 略浅一点，做径向渐变
BLUE_BRIGHT = (0, 122, 255)  # #007aff
BLUE_LIGHT = (102, 171, 255)  # #66abff
WHITE = (255, 255, 255)
STAR_COLOR = (255, 255, 255)
GLOW_COLOR = (0, 122, 255)


# 工具函数
def lerp_color(c1, c2, t):
    """线性插值两个颜色"""
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def radial_gradient(size, center_color, edge_color, center=None, radius=None):
    """生成径向渐变背景"""
    if center is None:
        center = (size[0] / 2, size[1] / 2)
    if radius is None:
        radius = max(size) / 2
    img = Image.new("RGB", size, edge_color)
    pixels = img.load()
    cx, cy = center
    for y in range(size[1]):
        for x in range(size[0]):
            dx = x - cx
            dy = y - cy
            dist = math.sqrt(dx * dx + dy * dy)
            t = min(dist / radius, 1.0)
            pixels[x, y] = lerp_color(center_color, edge_color, t)
    return img


def draw_star(draw, pos, radius, color, brightness=1.0):
    """画一颗星（带光晕）"""
    x, y = pos
    r = max(1, radius)
    # 光晕
    halo_r = int(r * 3)
    halo_color = tuple(min(255, int(c * brightness * 0.3)) for c in color)
    draw.ellipse([x - halo_r, y - halo_r, x + halo_r, y + halo_r], fill=halo_color + (128,))
    # 核心
    core_color = tuple(min(255, int(c * brightness)) for c in color)
    draw.ellipse([x - r, y - r, x + r, y + r], fill=core_color + (255,))


# 主绘制函数
def draw_icon(size: int) -> Image.Image:
    """绘制指定尺寸的图标，返回 RGBA Image"""
    # 高分辨率重采样以获得平滑边缘（2x 超采样）
    SCALE = 2
    S = size * SCALE
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 1. 圆形深色背景（带径向渐变）
    bg = Image.new("RGB", (S, S), BG_DEEP)
    bg_draw = ImageDraw.Draw(bg)
    # 径向渐变：中心略亮 BG_DARK，边缘 BG_DEEP
    cx = cy = S / 2
    max_r = S / 2
    for r in range(int(max_r), 0, -1):
        t = r / max_r
        c = lerp_color(BG_DARK, BG_DEEP, t)
        bg_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    # 圆形遮罩
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S - 1, S - 1], fill=255)
    img.paste(bg, (0, 0), mask)

    # 2. 装饰星点（呼应启动动画的银河星河）
    random.seed(42)  # 固定随机种子，保证图标每次生成一致
    star_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    star_draw = ImageDraw.Draw(star_layer)
    n_stars = 24 if size >= 64 else 12
    for _ in range(n_stars):
        # 在圆环内随机分布，但避开中心齿轮区域
        angle = random.uniform(0, 2 * math.pi)
        r_dist = random.uniform(S * 0.05, S * 0.45)
        x = cx + math.cos(angle) * r_dist
        y = cy + math.sin(angle) * r_dist
        # 跳过中心区域（齿轮所在）
        if math.hypot(x - cx, y - cy) < S * 0.18:
            continue
        brightness = random.uniform(0.4, 1.0)
        star_r = random.uniform(0.5, 1.8) * SCALE
        draw_star(star_draw, (x, y), star_r, STAR_COLOR, brightness)
    # 星点轻微模糊
    star_layer = star_layer.filter(ImageFilter.GaussianBlur(radius=0.3 * SCALE))
    img = Image.alpha_composite(img, star_layer)

    # 3. 齿轮（蓝色渐变 + 光晕）
    gear_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gear_draw = ImageDraw.Draw(gear_layer)

    gear_outer_r = S * 0.36  # 齿轮齿尖半径
    gear_inner_r = S * 0.28  # 齿轮齿根半径
    gear_hub_r = S * 0.18  # 齿轮中心圆半径
    n_teeth = 8  # 齿数

    # 绘制齿轮齿（用多边形）
    def draw_gear(draw_obj, cx, cy, outer_r, inner_r, hub_r, n_teeth, color):
        points = []
        tooth_angle = 2 * math.pi / n_teeth
        half_tooth = tooth_angle / 4
        for i in range(n_teeth):
            # 齿尖
            a1 = i * tooth_angle - half_tooth * 0.6
            a2 = i * tooth_angle + half_tooth * 0.6
            # 齿根
            a3 = i * tooth_angle + half_tooth * 1.4
            a4 = i * tooth_angle + tooth_angle - half_tooth * 1.4
            points.append((cx + outer_r * math.cos(a1), cy + outer_r * math.sin(a1)))
            points.append((cx + outer_r * math.cos(a2), cy + outer_r * math.sin(a2)))
            points.append((cx + inner_r * math.cos(a3), cy + inner_r * math.sin(a3)))
            points.append((cx + inner_r * math.cos(a4), cy + inner_r * math.sin(a4)))
        draw_obj.polygon(points, fill=color)

    # 齿轮光晕（先画一个大的模糊蓝色圆）
    glow_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_r = gear_outer_r * 1.25
    glow_draw.ellipse([cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r], fill=BLUE_BRIGHT + (60,))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=S * 0.04))
    img = Image.alpha_composite(img, glow_layer)

    # 主齿轮（蓝色渐变模拟）
    # 简化：用两层齿轮叠加（深色下层 + 浅色上层）实现立体感
    draw_gear(gear_draw, cx, cy, gear_outer_r, gear_inner_r, gear_hub_r, n_teeth, BLUE_BRIGHT + (255,))
    # 浅色齿轮（略小，偏移一点点模拟高光）
    draw_gear(
        gear_draw,
        cx - 0.02 * S,
        cy - 0.02 * S,
        gear_outer_r * 0.92,
        gear_inner_r * 0.92,
        gear_hub_r,
        n_teeth,
        BLUE_LIGHT + (180,),
    )
    # 重新画主齿轮（覆盖回去，让浅色只作为边缘高光）
    draw_gear(gear_draw, cx, cy, gear_outer_r * 0.95, gear_inner_r * 0.95, gear_hub_r, n_teeth, BLUE_BRIGHT + (255,))

    # 齿轮中心圆孔（深色，准备放立方体）
    gear_draw.ellipse([cx - gear_hub_r, cy - gear_hub_r, cx + gear_hub_r, cy + gear_hub_r], fill=BG_DEEP + (255,))

    img = Image.alpha_composite(img, gear_layer)

    # 4. 中心立方体（等距投影，代表 3D 建模）
    cube_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cube_draw = ImageDraw.Draw(cube_layer)

    cube_size = S * 0.13  # 立方体边长
    # 等距投影：三个可见面
    # 顶面（亮）：菱形
    # 左面（中）：平行四边形
    # 右面（暗）：平行四边形

    # 立方体中心
    ccx, ccy = cx, cy

    # 等距投影的 7 个顶点（顶面 + 左面 + 右面）
    # 顶面 4 个顶点：上、右、下、左
    top_top = (ccx, ccy - cube_size)
    top_right = (ccx + cube_size * math.cos(math.pi / 6), ccy - cube_size * math.sin(math.pi / 6))
    top_bottom = (ccx, ccy)
    top_left = (ccx - cube_size * math.cos(math.pi / 6), ccy - cube_size * math.sin(math.pi / 6))

    # 立方体底部 4 个顶点（顶面 4 个顶点向下平移；bot_top 与 top_bottom 重合，无需单独计算）
    bot_right = (top_right[0], top_right[1] + cube_size * 1.0)
    bot_bottom = (top_bottom[0], top_bottom[1] + cube_size * 1.0)
    bot_left = (top_left[0], top_left[1] + cube_size * 1.0)

    # 颜色：顶面最亮，左面中，右面暗
    top_color = BLUE_LIGHT + (255,)
    left_color = BLUE_BRIGHT + (255,)
    right_color = (0, 90, 200) + (255,)

    # 绘制三个面（顺序：左 右 顶，让顶面在最上层）
    # 左面：top_left, top_bottom, bot_bottom, bot_left
    cube_draw.polygon([top_left, top_bottom, bot_bottom, bot_left], fill=left_color)
    # 右面：top_right, top_bottom, bot_bottom, bot_right
    cube_draw.polygon([top_right, top_bottom, bot_bottom, bot_right], fill=right_color)
    # 顶面：top_top, top_right, top_bottom, top_left
    cube_draw.polygon([top_top, top_right, top_bottom, top_left], fill=top_color)

    # 立方体边缘高光（细线）
    edge_color = (200, 220, 255, 180)
    cube_draw.line([top_top, top_right, top_bottom, top_left, top_top], fill=edge_color, width=max(1, int(0.5 * SCALE)))

    img = Image.alpha_composite(img, cube_layer)

    # 5. 外圈描边（增强对比度，让小尺寸也清晰）
    border_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_layer)
    border_w = max(1, int(S * 0.012))
    # 外圈深色描边
    border_draw.ellipse(
        [border_w, border_w, S - 1 - border_w, S - 1 - border_w], outline=(0, 0, 0, 200), width=border_w
    )
    # 内圈蓝色高光
    border_w2 = max(1, int(S * 0.006))
    border_draw.ellipse(
        [border_w2 * 2, border_w2 * 2, S - 1 - border_w2 * 2, S - 1 - border_w2 * 2],
        outline=BLUE_BRIGHT + (100,),
        width=border_w2,
    )
    img = Image.alpha_composite(img, border_layer)

    # 6. 缩放到目标尺寸（高质量重采样）
    if SCALE > 1:
        img = img.resize((size, size), Image.LANCZOS)

    return img


# 主入口
def main():
    print("=" * 60)
    print("灵境制造 - 应用图标生成器")
    print("=" * 60)

    # 生成各个尺寸
    sizes = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
    }
    for filename, size in sizes.items():
        print(f"生成 {filename} ({size}x{size}) ...")
        img = draw_icon(size)
        img.save(ICONS_DIR / filename, "PNG")
        print(f"  -> {ICONS_DIR / filename}")

    # 生成 ICO（多尺寸，Windows 任务栏/桌面/资源管理器会按需选择）
    print("生成 icon.ico (含 16/32/48/64/128/256 多尺寸) ...")
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_images = [draw_icon(s) for s in ico_sizes]
    # Pillow 的 ICO 保存：第一个图像作为主图，sizes 参数指定包含的所有尺寸
    ico_images[0].save(
        ICONS_DIR / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_images[1:],
    )
    print(f"  -> {ICONS_DIR / 'icon.ico'}")

    # 生成 ICNS（macOS）— Windows 上 Pillow 不能直接生成完整 ICNS，
    # 但可以生成一个占位文件，部署到 macOS 时再重新生成
    print("生成 icon.icns (macOS 占位) ...")
    try:
        icns_img = draw_icon(512)
        icns_img.save(ICONS_DIR / "icon.icns", "ICNS")
        print(f"  -> {ICONS_DIR / 'icon.icns'}")
    except Exception as e:
        print(f"  [警告] ICNS 生成失败（Windows 平台限制）: {e}")
        print("  部署到 macOS 时请重新运行此脚本或使用 iconutil 工具")

    print()
    print("=" * 60)
    print("✓ 所有图标已生成完毕")
    print("=" * 60)
    print()
    print("下一步：")
    print("  1. 重新打包应用：npx tauri build --bundles nsis")
    print("  2. 新安装包将使用新图标")


if __name__ == "__main__":
    main()
