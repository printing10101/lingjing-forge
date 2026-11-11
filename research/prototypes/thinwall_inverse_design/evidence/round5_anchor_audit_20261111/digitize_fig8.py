"""Fig.8 数字化：从 Micromachines 2022 (10.3390/mi13020160) 的稳定性叶瓣图中提取
稳定/不稳定标记点与曲线高度。

用途：I06 锚点验收的图证复核。只读图片，不做任何稳定性计算。

前置：图片不入库（仓库 .gitignore 忽略 *.png），需按下方 URL 重新获取。

    https://pub.mdpi-res.com/micromachines/micromachines-13-00160/article_deploy/html/images/micromachines-13-00160-g008.png

运行：
    cd research/prototypes/thinwall_inverse_design/evidence/round5_anchor_audit_20261111
    ../../../.venv/Scripts/python.exe digitize_fig8.py

输出：fig8_digitized.json（标记点数据坐标 + 曲线抽样）；打印对照表。

边界：
  坐标映射用轴框像素线性插值，未做透视校正（图为正视截图，轴向无旋转）。
  标记点是颜色连通域中心，误差约 ±1 像素 ≈ ±2 rpm / ±0.002 mm。
  曲线高度取列内最高细黑像素，标注文字会污染个别列，故用 5 列窗口取中值；
  该列被文字覆盖时结果为缺失，不做插值填补。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = Path(__file__).resolve().parent
IMG = HERE / "figures" / "g008.png"
OUT = HERE / "fig8_digitized.json"

# 轴框像素（由 dark-row / dark-col 长线探测得到，见 detect_axes()）
AX = {"x_left": 176, "x_right": 2121, "y_bottom": 1171, "y_top": 26}
RPM = (1000.0, 5000.0)
AP = (0.0, 2.0)


def detect_axes(a: np.ndarray) -> dict:
    """重新探测轴框，用于核对硬编码值是否仍成立。"""
    gray = a.mean(axis=2)
    dark = gray < 110
    rows = np.where(dark.sum(axis=1) > 0.6 * a.shape[1])[0]
    cols = np.where(dark.sum(axis=0) > 0.6 * a.shape[0])[0]
    return {
        "x_left": int(cols.min()), "x_right": int(cols.max()),
        "y_top": int(rows.min()), "y_bottom": int(rows.max()),
    }


def px_to_data(cx: float, cy: float) -> tuple[float, float]:
    rpm = RPM[0] + (cx - AX["x_left"]) / (AX["x_right"] - AX["x_left"]) * (RPM[1] - RPM[0])
    ap = (AX["y_bottom"] - cy) / (AX["y_bottom"] - AX["y_top"]) * (AP[1] - AP[0])
    return rpm, ap


def rpm_to_px(r: float) -> int:
    return int(AX["x_left"] + (r - RPM[0]) / (RPM[1] - RPM[0]) * (AX["x_right"] - AX["x_left"]))


def extract_markers(a: np.ndarray) -> dict:
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    masks = {
        "stable": (G > 90) & (G - R > 35) & (G - B > 35),
        "unstable": (B > 110) & (B - R > 45) & (B - G > 25),
    }
    out: dict[str, list[dict]] = {}
    for name, mask in masks.items():
        lab, _ = ndimage.label(mask)
        pts = []
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            size = int((lab[sl] == i).sum())
            if size < 40 or size > 4000:
                continue
            cy, cx = ndimage.center_of_mass(lab == i)
            if cy < 170 and cx > 1150:      # 图例区域
                continue
            rpm, ap = px_to_data(cx, cy)
            pts.append({"rpm": round(rpm), "ap_mm": round(ap, 3), "area_px": size})
        out[name] = sorted(pts, key=lambda p: (p["rpm"], p["ap_mm"]))
    return out


def curve_height(a: np.ndarray, rpm: float, win: int = 2) -> float | None:
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    dark = (R < 100) & (G < 100) & (B < 100)
    x = rpm_to_px(rpm)
    ys = []
    for xx in range(x - win, x + win + 1):
        col = np.where(dark[45:1160, xx])[0]
        if len(col):
            ys.append(col.min() + 45)
    if not ys:
        return None
    py = float(np.median(ys))
    return round((AX["y_bottom"] - py) / (AX["y_bottom"] - AX["y_top"]) * (AP[1] - AP[0]), 3)


def main() -> None:
    if not IMG.exists():
        raise SystemExit(
            f"缺少 {IMG}\n请先下载：\n"
            "https://pub.mdpi-res.com/micromachines/micromachines-13-00160/"
            "article_deploy/html/images/micromachines-13-00160-g008.png"
        )

    a = np.asarray(Image.open(IMG).convert("RGB")).astype(int)
    detected = detect_axes(a)
    drift = {k: detected[k] - AX[k] for k in AX}

    markers = extract_markers(a)
    sampled = [1100 + 200 * i for i in range(20)]
    curve = {str(r): curve_height(a, r) for r in sampled}

    # 正文 §6.3 声明的四个点
    paper = [
        {"name": "A", "rpm": 1500, "ap_mm": 0.2, "label": "stable"},
        {"name": "B", "rpm": 1500, "ap_mm": 0.6, "label": "chatter"},
        {"name": "C", "rpm": 2500, "ap_mm": 0.4, "label": "stable"},
        {"name": "D", "rpm": 3000, "ap_mm": 0.4, "label": "chatter"},
    ]
    matches = []
    for p in paper:
        want = "stable" if p["label"] == "stable" else "unstable"
        best = min(
            markers[want],
            key=lambda m: abs(m["rpm"] - p["rpm"]) / 100 + abs(m["ap_mm"] - p["ap_mm"]) / 0.1,
        )
        matches.append({
            "paper": p,
            "nearest_marker": best,
            "drift_rpm": best["rpm"] - p["rpm"],
            "drift_ap_mm": round(best["ap_mm"] - p["ap_mm"], 3),
        })

    payload = {
        "source_doi": "10.3390/mi13020160",
        "figure": "Fig.8 Stability lobe diagrams at the measured point 2",
        "image_px": {"width": int(a.shape[1]), "height": int(a.shape[0])},
        "axis_px_used": AX,
        "axis_px_detected": detected,
        "axis_drift_px": drift,
        "marker_count": {k: len(v) for k, v in markers.items()},
        "markers": markers,
        "paper_points_vs_figure": matches,
        "curve_sampled_ap_mm": curve,
        "notes": [
            "标记点总数见 marker_count；正文 §6.3 只描述 A-D 四点，图内含更多点与一组 4000 rpm 数据。",
            "curve_sampled_ap_mm 为叶瓣曲线高度抽样，被文字标注遮挡的列返回 null，不做插值。",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"轴框漂移(px): {drift}")
    print(f"标记点: {payload['marker_count']}")
    print("\n正文四点 vs 图内最近标记：")
    for m in matches:
        p, b = m["paper"], m["nearest_marker"]
        print(f"  {p['name']} 正文({p['rpm']} rpm, {p['ap_mm']} mm, {p['label']})"
              f" -> 图({b['rpm']} rpm, {b['ap_mm']} mm)"
              f"  偏差 {m['drift_rpm']:+d} rpm / {m['drift_ap_mm']:+.3f} mm")
    print(f"\n曲线抽样已写入 {OUT.name}")


if __name__ == "__main__":
    main()
