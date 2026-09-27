# -*- coding: utf-8 -*-
"""半自动图件数字化助手：从稳定性验证图检测/分类实验标记点。

用途：OA 论文（如 Ji 2024 SciRep）图件中的实测稳定/颤振标记点（实心圆=稳定，
叉=颤振）无法全自动提取（需要 OCR 读坐标轴刻度）。本工具自动完成最费时的
"找点 + 分类"（形状检测），输出 WebPlotDigitizer 可直接使用的候选清单；
坐标轴标定由你肉眼确认（1-2 分钟），保证数据准确可查证。

用法：
    ../.venv/Scripts/python.exe real_validation/digitize_fig_markers.py \
        --fig datasets/measured_stability/figures/Fig10.jpg \
        --out results/fig10_markers.csv

输出 CSV：pixel_x, pixel_y, class(0=颤振叉,1=稳定圆), size, fill
配合 WebPlotDigitizer：
    1) 打开 Fig10.jpg，设坐标轴（x=转速 rpm，y=轴向切深 mm，按图刻度）
    2) 对照本工具输出（或直接在图上目视）标记实心圆=1、叉=0
    3) 导出 (n, ap, stable) 后，用 ingest_literature_points.py --append 录入

诚实性：本工具只输出"候选位置+形状分类"，不产生数值数据；
最终数值必须来自 WebPlotDigitizer 标定后的导出，且逐点人工核对。
"""

import argparse
import csv
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage


def detect_markers(img_path: str, plot_only: bool = True):
    """检测图件中的标记点，按形状分类：实心圆(稳定)/叉(颤振)。

    返回 (markers, plot_box)：
        markers: list[dict] 含 pixel_x, pixel_y, size, bw, bh, fill, cls
        plot_box: (x0, y0, x1, y1) 估计的绘图区（用于过滤图例/轴标签）
    """
    img = Image.open(img_path).convert("L")
    arr = np.array(img)
    h, w = arr.shape
    dark = arr < 120
    labels, n = ndimage.label(dark)
    sizes = ndimage.sum(dark, labels, range(1, n + 1))

    markers = []
    # 轴框估计：找横/纵方向上最长的连续暗线
    col_dark = dark.sum(axis=0)
    row_dark = dark.sum(axis=1)
    x0 = int(np.argmax(col_dark > 30))
    x1 = int(w - np.argmax(col_dark[::-1] > 30))
    y0 = int(np.argmax(row_dark > 30))
    y1 = int(h - np.argmax(row_dark[::-1] > 30))

    for i in range(1, n + 1):
        s = int(sizes[i - 1])
        if not (4 <= s <= 120):
            continue
        ys, xs = np.where(labels == i)
        cx, cy = float(xs.mean()), float(ys.mean())
        bw = int(xs.max() - xs.min() + 1)
        bh = int(ys.max() - ys.min() + 1)
        fill = s / (bw * bh) if bw * bh > 0 else 0.0
        # 轴框/长线片段过滤
        if bw > 60 or bh > 60:
            continue
        # 形状分类：
        # 实心圆：紧凑高填充（fill>=0.65, 近方形）
        # 叉： 低填充 0.3-0.65, 10px 级
        if fill >= 0.65 and 5 <= bw <= 14 and 5 <= bh <= 14:
            cls = 1  # 稳定（实心圆）
        elif 0.28 <= fill < 0.65 and 6 <= bw <= 16 and 6 <= bh <= 16:
            cls = 0  # 颤振（叉）
        else:
            continue
        # 仅绘图区内（排除轴标签/图例，保留余量）
        if plot_only and not (x0 + 8 < cx < x1 - 8 and y0 + 8 < cy < y1 - 8):
            continue
        markers.append(
            {
                "pixel_x": round(cx, 1),
                "pixel_y": round(cy, 1),
                "size": s,
                "bw": bw,
                "bh": bh,
                "fill": round(float(fill), 2),
                "cls": cls,
            }
        )
    return markers, (x0, y0, x1, y1)


def main():
    ap = argparse.ArgumentParser(description="图件标记点半自动检测")
    ap.add_argument("--fig", required=True, help="图件路径")
    ap.add_argument("--out", required=True, help="输出 CSV 路径")
    ap.add_argument("--no-plot-filter", action="store_true", help="不过滤绘图区")
    args = ap.parse_args()

    markers, box = detect_markers(args.fig, plot_only=not args.no_plot_filter)
    print(f"绘图区估计: x[{box[0]},{box[2]}] y[{box[1]},{box[3]}]")
    n_stable = sum(1 for m in markers if m["cls"] == 1)
    n_chatter = sum(1 for m in markers if m["cls"] == 0)
    print(f"检测标记点: 稳定(圆)={n_stable} 颤振(叉)={n_chatter} 合计={len(markers)}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pixel_x", "pixel_y", "size", "bw", "bh", "fill", "cls"])
        w.writeheader()
        for m in sorted(markers, key=lambda x: (x["pixel_y"], x["pixel_x"])):
            w.writerow(m)
    print(f"已保存: {args.out}")
    print("\n下一步（WebPlotDigitizer 半自动标定）：")
    print("  1. 打开图件，设坐标轴（按图刻度：x=转速 rpm, y=切深 mm）")
    print("  2. 对照候选清单目视核对每个点（cls=1 稳定圆 / cls=0 颤振叉）")
    print("  3. 导出的 (n, ap, stable) 用 ingest_literature_points.py 录入")


if __name__ == "__main__":
    main()
