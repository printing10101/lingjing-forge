"""JUST_CAS 多传感器铣削数据：参数→响应的泛化检验.

数据（Mendeley 8jk3kyxmbn，CC BY 4.0；Zenodo 10613521 镜像）
------------------------------------------------------------
100 段侧铣 = 4 工况 × (ap×ae 5×5 全因子)；每段 50 kHz、1 s、10 通道
（Fx/Fy/Fz 力、Vx/Vy/Vz 振动、SN 噪声、IU/IV/IW 电流）。工况：
G1(800 mm/min, 5000 rpm, 7030 铝)、G2(800, 10000, 铝)、
G3(1600, 5000, 铝)、G4(800, 5000, CK45 钢)。另 15 段槽铣仅用于
切削力系数标定，本脚本不纳入主检验。

问题
----
参数（ap、ae、进给、转速、材料）是否携带可跨条件泛化的响应信息？
两种留出：
- 留一工况（4 折）：评估对新转速/进给/材料条件的泛化；
- 留一 (ap, ae) 组合（25 折）：评估参数平面内的插值泛化。

特征集对照（同一折、同一 λ 流程）：
- ``mean``：训练折响应均值（基线）；
- ``ap_ae``：仅切深/切宽；
- ``full``：ap/ae/进给/转速/材料；
- ``power_law``：对数线性幂律（log 力 = b0 + b·log ap + c·log ae + …），
  力的力学常用形式，预测在 log 空间完成后再反变换。

验收口径：R² 对训练折均值；MAE 原单位。结果仅作方向性判断——
同一切削刀具、同一机床、干切削，且为稳态单段响应，不含轨迹动态。

用法（仓库根，全新输出路径）::

    python engineering/python/scripts/wm_external_param_response.py \
        --dir data/external/just_cas \
        --out research/experiments/results/just_cas_param_response_<date>.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_external_action_probe import ridge_fit, ridge_predict  # noqa: E402

LEVELS = (2.0, 1.8, 1.6, 1.4, 1.2)
GROUPS = {"G1": {"feed": 800.0, "speed": 5000.0, "material": 0.0},
          "G2": {"feed": 800.0, "speed": 10000.0, "material": 0.0},
          "G3": {"feed": 1600.0, "speed": 5000.0, "material": 0.0},
          "G4": {"feed": 800.0, "speed": 5000.0, "material": 1.0}}
LAMBDA_GRID = (1e-3, 1e-2, 1e-1, 1.0, 10.0)
RESPONSES = ("force_rms", "vibration_rms", "noise_rms", "current_rms")
FEATURE_SETS = ("ap_ae", "full", "power_law")


def parse_name(name: str) -> dict | None:
    """G{1..4}_{01..25} → 参数；其余（槽铣）返回 None。"""
    match = re.fullmatch(r"(G[1-4])_(\d{2})\.xlsx", name)
    if not match:
        return None
    group, index = match.group(1), int(match.group(2))
    if not 1 <= index <= 25:
        raise ValueError(f"样本编号越界: {name}")
    level_ap = LEVELS[(index - 1) // 5]
    level_ae = LEVELS[(index - 1) % 5]
    condition = GROUPS[group]
    return {"file": name, "group": group, "index": index, "ap": level_ap, "ae": level_ae,
            "feed": condition["feed"], "speed": condition["speed"], "material": condition["material"]}


def aggregate_file(path: Path) -> dict:
    """单文件通道 RMS（含合力/合振动）。"""
    import pandas as pd

    frame = pd.read_excel(path, engine="openpyxl")
    expected = ["Fx", "Fy", "Fz", "Vx", "Vy", "Vz", "SN", "IU", "IV", "IW"]
    if list(frame.columns) != expected:
        raise ValueError(f"{path.name} 列名不符: {list(frame.columns)}")
    values = frame.to_numpy(dtype=np.float64)
    if values.shape[0] < 1000 or not np.isfinite(values).all():
        raise ValueError(f"{path.name} 行数不足或含非有限值")
    force = values[:, 0:3]
    vibration = values[:, 3:6]
    return {"rows": int(values.shape[0]),
            "force_rms": float(np.sqrt(np.mean((force ** 2).sum(axis=1)))),
            "vibration_rms": float(np.sqrt(np.mean((vibration ** 2).sum(axis=1)))),
            "noise_rms": float(np.sqrt(np.mean(values[:, 6] ** 2))),
            "current_rms": float(np.sqrt(np.mean((values[:, 7:10] ** 2).sum(axis=1)))),
            "force_mean_abs": float(np.abs(force).mean())}


def load_table(directory: Path) -> list[dict]:
    rows = []
    for path in sorted(directory.glob("G*_*.xlsx")):
        meta = parse_name(path.name)
        if meta is None:
            continue
        rows.append({**meta, **aggregate_file(path)})
    if len(rows) != 100:
        raise ValueError(f"侧铣样本数应为 100，实际 {len(rows)}（下载未完成？）")
    return rows


def _features(row: dict, feature_set: str) -> list[float]:
    if feature_set == "ap_ae":
        return [row["ap"], row["ae"]]
    if feature_set == "full":
        return [row["ap"], row["ae"], row["feed"], row["speed"], row["material"]]
    raise ValueError(f"未知特征集: {feature_set}")


def _log_features(row: dict) -> list[float]:
    return [math.log(row["ap"]), math.log(row["ae"]), math.log(row["feed"]), math.log(row["speed"]), 1.0]


def evaluate(rows: list[dict], response: str, fold_key: str) -> dict:
    """fold_key: group / combo。"""
    folds: dict[str, list[dict]] = {}
    for row in rows:
        key = row["group"] if fold_key == "group" else f"ap={row['ap']},ae={row['ae']}"
        folds.setdefault(key, []).append(row)
    if len(folds) < 2:
        raise ValueError("折数不足")
    results = {name: {"r2": [], "mae": []} for name in ("mean",) + FEATURE_SETS}
    fold_details = []
    for key, hold_rows in sorted(folds.items()):
        hold_ids = {id(row) for row in hold_rows}
        train_rows = [row for row in rows if id(row) not in hold_ids]
        y_train = np.array([row[response] for row in train_rows])
        y_hold = np.array([row[response] for row in hold_rows])
        baseline = float(y_train.mean())
        detail = {"fold": key, "train": len(train_rows), "hold": len(hold_rows)}
        for name in ("mean",) + FEATURE_SETS:
            if name == "mean":
                prediction = np.full_like(y_hold, baseline)
                detail[name] = {"r2": None, "mae": float(np.abs(y_hold - baseline).mean())}
                results[name]["mae"].append(detail[name]["mae"])
                results[name]["r2"].append(0.0)
                continue
            if name == "power_law":
                log_x = np.array([_log_features(row) for row in train_rows])
                log_y = np.log(y_train)
                lam = _select_lambda(log_x, log_y)
                model = ridge_fit(log_x, log_y, lam)
                prediction = np.exp(ridge_predict(model, np.array([_log_features(row) for row in hold_rows])))
            else:
                x = np.array([_features(row, name) for row in train_rows])
                lam = _select_lambda(x, y_train)
                model = ridge_fit(x, y_train, lam)
                prediction = ridge_predict(model, np.array([_features(row, name) for row in hold_rows]))
            mse = float(((prediction - y_hold) ** 2).mean())
            baseline_mse = float(((y_hold - baseline) ** 2).mean())
            r2 = 1.0 - mse / baseline_mse if baseline_mse > 0 else None
            detail[name] = {"r2": r2, "mae": float(np.abs(prediction - y_hold).mean()), "lambda": lam}
            results[name]["r2"].append(r2)
            results[name]["mae"].append(detail[name]["mae"])
        fold_details.append(detail)
    summary = {}
    for name, values in results.items():
        r2 = [value for value in values["r2"] if value is not None]
        summary[name] = {"r2_mean": float(np.mean(r2)) if r2 else None,
                         "r2_min": float(np.min(r2)) if r2 else None,
                         "mae_mean": float(np.mean(values["mae"]))}
    return {"response": response, "fold_by": fold_key, "folds": len(folds),
            "summary": summary, "fold_details": fold_details}


def _select_lambda(features: np.ndarray, targets: np.ndarray) -> float:
    """样本内留一交叉验证选 λ（n 小，直接闭式）。"""
    n = features.shape[0]
    best_lam, best_mse = LAMBDA_GRID[0], float("inf")
    for lam in LAMBDA_GRID:
        errors = []
        for index in range(n):
            mask = np.arange(n) != index
            if mask.sum() < 3:
                continue
            model = ridge_fit(features[mask], targets[mask], lam)
            prediction = ridge_predict(model, features[index:index + 1])[0]
            errors.append(float((prediction - targets[index]) ** 2))
        mse = float(np.mean(errors)) if errors else float("inf")
        if mse < best_mse - 1e-15:
            best_lam, best_mse = lam, mse
    return best_lam


def run(directory: Path, out_path: Path) -> dict:
    if out_path.exists():
        raise FileExistsError("输出已存在，请使用新路径")
    rows = load_table(directory)
    report = {"source": {"directory": str(directory.resolve()), "files": len(rows),
                         "design": "4 工况 × ap×ae 5×5 全因子，50 kHz×1 s",
                         "licence": "CC BY 4.0"},
              "caveats": ["同一把刀具贯穿全部试验，后期样本可能混入磨损影响",
                          "稳态单段 1 s 响应，不含轨迹动态",
                          "预测值未裁剪；幂律档在 log 空间拟合",
                          "工况因素(转速/进给/材料)各仅 2 水平且与工况组完全混淆，跨工况外推不可辨识"],
              "samples": [{key: row[key] for key in ("file", "group", "index", "ap", "ae", "feed", "speed",
                                                     "material", "force_rms", "vibration_rms", "noise_rms",
                                                     "current_rms")} for row in rows],
              "runs": []}
    for response in RESPONSES:
        for fold_key in ("group", "combo"):
            report["runs"].append(evaluate(rows, response, fold_key))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.dir, args.out)
    except (ValueError, OSError) as exc:
        print(f"[param-response failed] {exc}", file=sys.stderr)
        return 1
    for run_result in report["runs"]:
        if run_result["response"] != "force_rms":
            continue
        summary = run_result["summary"]
        print(f"[force/{run_result['fold_by']}] " + " ".join(
            f"{name}={summary[name]['r2_mean']:+.3f}" for name in ("mean", "ap_ae", "full", "power_law")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
