"""JUST_CAS 槽铣 15 段：切削力系数标定（Kienzle 线性形式）.

数据：``M_7030_1..7``（7030 铝）、``M_45_1..8``（CK45 钢），槽铣（ae=刀具直径 6 mm），
ap=1 mm、n=5000 rpm、四刃；每齿进给 fz 由厂家设计表给出（铝 0.020–0.080 七档，
钢 0.020–0.055 八档）。每段 50 kHz × 1 s，10 通道。

模型（每材料）
--------------
``F_mean = kc · ap · fz + ke · ap``

- ``kc``：比切削力（N/mm²），``ke``：刃口力系数（N/mm）；
- 用最小二乘在 [ap·fz, ap] 两列上拟合；R² 对均值基线；
- 该形式是 Kienzle 线性近似（忽略剪切角/刃口非线性），仅作量级标定。

同时给出与侧铣 100 段的指数一致性对照：侧铣幂律 ``F ∝ ap^b_ap·ae^b_ae``
的 b_ap 应接近槽铣的线性假设（≈1），偏离说明模型形式差异。

用法（仓库根）::

    python engineering/python/scripts/wm_slot_force_calibration.py \
        --dir data/external/just_cas \
        --out research/experiments/results/just_cas_slot_calibration_<date>.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

FZ_ALUMINIUM = (0.020, 0.030, 0.040, 0.050, 0.060, 0.070, 0.080)
FZ_STEEL = (0.020, 0.025, 0.030, 0.035, 0.040, 0.045, 0.050, 0.055)
AP_MM = 1.0
TOOL_DIAMETER_MM = 6.0
SPEED_RPM = 5000.0
FLUTES = 4


def parse_slot_name(name: str) -> dict | None:
    """M_7030_{1..7} / M_45_{1..8} → 材料与 fz；其他返回 None。"""
    match = re.fullmatch(r"M_(7030|45)_(\d{1,2})\.xlsx", name)
    if not match:
        return None
    material, index = match.group(1), int(match.group(2))
    series = FZ_ALUMINIUM if material == "7030" else FZ_STEEL
    if not 1 <= index <= len(series):
        raise ValueError(f"槽铣编号越界: {name}")
    return {"file": name, "material": "aluminium_7030" if material == "7030" else "steel_CK45",
            "index": index, "fz_mm_per_tooth": series[index - 1], "ap": AP_MM,
            "ae": TOOL_DIAMETER_MM, "speed": SPEED_RPM,
            "feed_mm_per_min": series[index - 1] * FLUTES * SPEED_RPM}


def aggregate_slot(path: Path) -> dict:
    import pandas as pd

    frame = pd.read_excel(path, engine="openpyxl")
    expected = ["Fx", "Fy", "Fz", "Vx", "Vy", "Vz", "SN", "IU", "IV", "IW"]
    if list(frame.columns) != expected:
        raise ValueError(f"{path.name} 列名不符")
    values = frame.to_numpy(dtype=np.float64)
    if values.shape[0] < 1000 or not np.isfinite(values).all():
        raise ValueError(f"{path.name} 行数不足或含非有限值")
    force = values[:, 0:3]
    magnitude = np.sqrt((force ** 2).sum(axis=1))
    return {"rows": int(values.shape[0]),
            "force_mean_resultant": float(magnitude.mean()),
            "fx_mean": float(force[:, 0].mean()), "fy_mean": float(force[:, 1].mean()),
            "fz_mean": float(force[:, 2].mean())}


def fit_kienzle(fz: np.ndarray, force: np.ndarray, ap: float) -> dict:
    """F = kc·ap·fz + ke·ap 的最小二乘。"""
    if fz.ndim != 1 or force.shape != fz.shape or fz.size < 3:
        raise ValueError("样本数不足或形状不匹配")
    if not np.all(fz > 0) or not np.all(force > 0) or ap <= 0:
        raise ValueError("fz/force/ap 必须为正")
    design = np.column_stack([ap * fz, np.full_like(fz, ap)])
    coefficients, *_ = np.linalg.lstsq(design, force, rcond=None)
    prediction = design @ coefficients
    baseline = float(force.mean())
    residual = float(((force - prediction) ** 2).sum())
    total = float(((force - baseline) ** 2).sum())
    return {"kc_N_per_mm2": float(coefficients[0]), "ke_N_per_mm": float(coefficients[1]),
            "r2_vs_mean": 1.0 - residual / total if total > 0 else None,
            "residual_rmse": float(math.sqrt(residual / fz.size)),
            "samples": int(fz.size)}


def side_milling_exponents(samples: list[dict]) -> dict:
    """侧铣幂律指数（组内识别）作为形式一致性对照。"""
    residual_rows = []
    for row in samples:
        residual_rows.append((math.log(row["force_rms"]), math.log(row["ap"]), math.log(row["ae"]), row["group"]))
    groups = sorted({row[3] for row in residual_rows})
    design = []
    for log_f, log_ap, log_ae, group in residual_rows:
        design.append([log_ap, log_ae] + [1.0 if group == g else 0.0 for g in groups[1:]])
    design = np.array(design)
    target = np.array([row[0] for row in residual_rows])
    weights, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(design)), design]), target, rcond=None)
    return {"b_ap": float(weights[1]), "b_ae": float(weights[2])}


def run(directory: Path, out_path: Path, samples_path: Path | None) -> dict:
    if out_path.exists():
        raise FileExistsError("输出已存在，请使用新路径")
    rows = []
    for path in sorted(directory.glob("M_*.xlsx")):
        meta = parse_slot_name(path.name)
        if meta is None:
            continue
        rows.append({**meta, **aggregate_slot(path)})
    if len(rows) != 15:
        raise ValueError(f"槽铣样本应为 15，实际 {len(rows)}")
    report: dict = {"source": {"directory": str(directory.resolve()), "files": len(rows),
                               "design": "槽铣 ae=6mm(D6), ap=1mm, n=5000rpm, 四刃",
                               "licence": "CC BY 4.0"},
                    "caveats": ["Kienzle 线性近似，忽略刃口/剪切非线性与刀具磨损漂移",
                                "同一把刀贯穿全部试验",
                                "F_mean 为走刀内合力均值（N）"],
                    "by_material": {}, "samples": rows}
    for material in ("aluminium_7030", "steel_CK45"):
        subset = [row for row in rows if row["material"] == material]
        fz = np.array([row["fz_mm_per_tooth"] for row in subset])
        force = np.array([row["force_mean_resultant"] for row in subset])
        report["by_material"][material] = {**fit_kienzle(fz, force, AP_MM),
                                           "table": [{"fz": row["fz_mm_per_tooth"],
                                                      "F_mean": row["force_mean_resultant"]} for row in subset]}
    if samples_path is not None:
        payload = json.loads(samples_path.read_text(encoding="utf-8"))
        report["side_milling_consistency"] = side_milling_exponents(payload["samples"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=Path, default=None,
                        help="侧铣逐样本聚合表（用于指数一致性对照）")
    args = parser.parse_args()
    try:
        report = run(args.dir, args.out, args.samples)
    except (ValueError, OSError) as exc:
        print(f"[calibration failed] {exc}", file=sys.stderr)
        return 1
    for material, entry in report["by_material"].items():
        print(f"[{material}] kc={entry['kc_N_per_mm2']:.1f} N/mm² ke={entry['ke_N_per_mm']:.2f} N/mm "
              f"R2={entry['r2_vs_mean']:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
