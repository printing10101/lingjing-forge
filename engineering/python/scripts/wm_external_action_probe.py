"""NASA Milling 外部数据：参数信息检验（跨参数组合留出）.

问题：DOC / 进给 / 材料这组案例级参数，是否携带对响应（振动、磨损增量）
的可学信息？这是"动作响应"在公开数据上唯一可检验的形式——跨参数组合
泛化，而不是轨迹内动作阶跃。

两个任务
--------
- T_vib：预测走刀级振动 RMS（走刀内信号的 RMS，作为"过程响应"）；
- T_wear：预测相邻两次测得 VB 之间的磨损增量（磨损速率）。

特征集三档对照（同一折、同一 λ 选择流程）：
``actions``=DOC/进给/材料；``state``=上一步响应（振动 RMS/电流 RMS 或 VB）；
``state+actions``=两者合并。若 actions 档不优于"训练均值"，且 state+actions
不优于 state，则参数信息在本数据上不可学。

评估
----
留一参数组合（8 组，每组 2 个案例）：训练 7 组（14 案例）、测试留出组。
λ 由训练集内留一案例交叉验证选出（确定性）。每次只用训练折统计量。
样本量小（167 次走刀、磨损对更少），结论只作方向性判断。

用法（仓库根）::

    python engineering/python/scripts/wm_external_action_probe.py \
        --mat data/external/nasa_milling/extracted/inner/mill.mat \
        --out research/experiments/results/nasa_action_probe_<date>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

LAMBDA_GRID = (1e-3, 1e-2, 1e-1, 1.0, 10.0)
FEATURE_SETS = {"actions": ("DOC", "feed", "material"),
                "state": None,  # 由任务决定
                "state_actions": None}


def _scalar(entry, field: str):
    value = entry[field].ravel()
    return None if value.size == 0 else float(value[0])


def _signal(entry, field: str) -> np.ndarray:
    return np.asarray(entry[field]).ravel().astype(np.float64)


def extract_runs(mat_path: Path) -> list[dict]:
    """逐走刀提取元数据与信号 RMS。"""
    from scipy.io import loadmat

    loaded = loadmat(mat_path, struct_as_record=True, squeeze_me=False)
    mill = loaded["mill"].ravel()
    runs = []
    for entry in mill:
        vib = _signal(entry, "vib_spindle")
        current = _signal(entry, "smcAC")
        if vib.size == 0 or current.size == 0 or not (np.isfinite(vib).all() and np.isfinite(current).all()):
            continue
        # 截断到公共长度：多数走刀 9000 点，个别 15360 点
        common = min(vib.size, current.size, 9000)
        vb = _scalar(entry, "VB")
        runs.append({"case": int(_scalar(entry, "case")), "run": int(_scalar(entry, "run")),
                     "VB": vb if vb is not None and np.isfinite(vb) else None,
                     "DOC": _scalar(entry, "DOC"),
                     "feed": _scalar(entry, "feed"), "material": _scalar(entry, "material"),
                     "vib_rms": float(np.sqrt(np.mean(vib[:common] ** 2))),
                     "current_rms": float(np.sqrt(np.mean(current[:common] ** 2)))})
    runs.sort(key=lambda item: (item["case"], item["run"]))
    return runs


def build_task_vib(runs: list[dict]) -> dict:
    """走刀级振动 RMS：状态特征=上一条走刀的振动/电流 RMS。"""
    rows = []
    previous: dict[int, dict] = {}
    for run in runs:
        prior = previous.get(run["case"])
        previous[run["case"]] = run
        if prior is None:
            continue
        rows.append({"case": run["case"], "target": run["vib_rms"],
                     "actions": [run["DOC"], run["feed"], run["material"]],
                     "state": [prior["vib_rms"], prior["current_rms"]]})
    return {"name": "vib_rms", "rows": rows}


def build_task_wear(runs: list[dict]) -> dict:
    """磨损增量：仅相邻且两次都测得 VB 的走刀对。"""
    rows = []
    previous: dict[int, dict] = {}
    for run in runs:
        prior = previous.get(run["case"])
        previous[run["case"]] = run
        if prior is None or prior["VB"] is None or run["VB"] is None:
            continue
        rows.append({"case": run["case"], "target": run["VB"] - prior["VB"],
                     "actions": [run["DOC"], run["feed"], run["material"]],
                     "state": [prior["VB"]]})
    return {"name": "wear_delta", "rows": rows}


def _combo_key(row_actions: list[float]) -> str:
    return f"DOC={row_actions[0]},feed={row_actions[1]},material={row_actions[2]}"


def ridge_fit(features: np.ndarray, targets: np.ndarray, lam: float) -> dict:
    x_mean, x_scale = features.mean(axis=0), features.std(axis=0)
    x_scale = np.where(x_scale < 1e-8, 1.0, x_scale)
    x = (features - x_mean) / x_scale
    y_mean = float(targets.mean())
    weights = np.linalg.solve(x.T @ x + lam * np.eye(x.shape[1]), x.T @ (targets - y_mean))
    return {"x_mean": x_mean, "x_scale": x_scale, "y_mean": y_mean, "weights": weights}


def ridge_predict(model: dict, features: np.ndarray) -> np.ndarray:
    return ((features - model["x_mean"]) / model["x_scale"]) @ model["weights"] + model["y_mean"]


def _matrix(rows: list[dict], feature_set: str) -> np.ndarray:
    if feature_set == "actions":
        return np.array([row["actions"] for row in rows], dtype=np.float64)
    if feature_set == "state":
        return np.array([row["state"] for row in rows], dtype=np.float64)
    return np.array([row["state"] + row["actions"] for row in rows], dtype=np.float64)


def _select_lambda(train_rows: list[dict], feature_set: str) -> float:
    """训练集内留一案例交叉验证选 λ。"""
    cases = sorted({row["case"] for row in train_rows})
    if len(cases) < 3:
        return LAMBDA_GRID[0]
    best_lam, best_mse = LAMBDA_GRID[0], float("inf")
    for lam in LAMBDA_GRID:
        errors = []
        for case in cases:
            inner_train = [row for row in train_rows if row["case"] != case]
            inner_hold = [row for row in train_rows if row["case"] == case]
            if not inner_train or not inner_hold:
                continue
            model = ridge_fit(_matrix(inner_train, feature_set), np.array([row["target"] for row in inner_train]), lam)
            prediction = ridge_predict(model, _matrix(inner_hold, feature_set))
            errors.append(((prediction - np.array([row["target"] for row in inner_hold])) ** 2).mean())
        mse = float(np.mean(errors)) if errors else float("inf")
        if mse < best_mse - 1e-15:
            best_lam, best_mse = lam, mse
    return best_lam


def evaluate_task(task: dict) -> dict:
    rows = task["rows"]
    combos = {}
    for row in rows:
        combos.setdefault(_combo_key(row["actions"]), []).append(row)
    if len(combos) < 2:
        raise ValueError(f"任务 {task['name']} 的组合数不足")
    results = {name: {"r2": [], "r2_unclipped": [], "mae": [], "baseline_mae": []} for name in FEATURE_SETS}
    folds = []
    for combo, hold_rows in sorted(combos.items()):
        train_rows = [row for row in rows if _combo_key(row["actions"]) != combo]
        y_hold = np.array([row["target"] for row in hold_rows])
        baseline = float(np.mean([row["target"] for row in train_rows]))
        fold = {"combo": combo, "train_rows": len(train_rows), "hold_rows": len(hold_rows),
                "baseline_mae": float(np.abs(y_hold - baseline).mean())}
        for feature_set in FEATURE_SETS:
            lam = _select_lambda(train_rows, feature_set)
            train_y = np.array([row["target"] for row in train_rows])
            model = ridge_fit(_matrix(train_rows, feature_set), train_y, lam)
            raw_prediction = ridge_predict(model, _matrix(hold_rows, feature_set))
            # 小样本跨组合外推会爆炸：裁剪到训练折目标区间，未裁剪值单独记录
            prediction = np.clip(raw_prediction, float(train_y.min()), float(train_y.max()))
            mse = float(((prediction - y_hold) ** 2).mean())
            raw_mse = float(((raw_prediction - y_hold) ** 2).mean())
            baseline_mse = float(((y_hold - baseline) ** 2).mean())
            r2 = 1.0 - mse / baseline_mse if baseline_mse > 0 and np.isfinite(mse) else None
            r2_raw = 1.0 - raw_mse / baseline_mse if baseline_mse > 0 and np.isfinite(raw_mse) else None
            results[feature_set]["r2"].append(r2)
            results[feature_set]["r2_unclipped"].append(r2_raw)
            results[feature_set]["mae"].append(float(np.abs(prediction - y_hold).mean()))
            results[feature_set]["baseline_mae"].append(float(np.abs(y_hold - baseline).mean()))
            fold[feature_set] = {"lambda": lam, "mse": mse, "r2": r2, "r2_unclipped": r2_raw,
                                 "mae": results[feature_set]["mae"][-1]}
        folds.append(fold)
    summary = {}
    for name, values in results.items():
        r2 = [value for value in values["r2"] if value is not None]
        r2_raw = [value for value in values["r2_unclipped"] if value is not None and np.isfinite(value)]
        summary[name] = {"r2_mean": float(np.mean(r2)) if r2 else None,
                         "r2_min": float(np.min(r2)) if r2 else None,
                         "r2_max": float(np.max(r2)) if r2 else None,
                         "r2_unclipped_min": float(np.min(r2_raw)) if r2_raw else None,
                         "mae_mean": float(np.mean(values["mae"])),
                         "baseline_mae_mean": float(np.mean(values["baseline_mae"]))}
    return {"task": task["name"], "rows": len(rows), "combos": len(combos),
            "folds": folds, "summary": summary}


def run_probe(mat_path: Path, out_path: Path) -> dict:
    if out_path.exists():
        raise FileExistsError("输出已存在，请使用新路径")
    runs = extract_runs(mat_path)
    report = {"source": {"path": str(mat_path.resolve()), "sha256": hashlib.sha256(mat_path.read_bytes()).hexdigest()},
              "runs_used": len(runs),
              "caveats": ["每组合仅 2 个案例，留出折样本极少，结论仅方向性",
                          "动作仅案例级常量；无转速字段，速度效应不可检验",
                          "vib_rms 为走刀内信号 RMS，非计量振级"],
              "tasks": {}}
    for task in (build_task_vib(runs), build_task_wear(runs)):
        if len(task["rows"]) < 20:
            report["tasks"][task["name"]] = {"skipped": "样本不足20行", "rows": len(task["rows"])}
            continue
        report["tasks"][task["name"]] = evaluate_task(task)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mat", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_probe(args.mat, args.out)
    except (ValueError, OSError, KeyError) as exc:
        print(f"[probe failed] {exc}", file=sys.stderr)
        return 1
    for name, task in report["tasks"].items():
        if "summary" in task:
            parts = []
            for key in ("actions", "state", "state_actions"):
                value = task["summary"][key]["r2_mean"]
                parts.append(f"{key} R2={'None' if value is None else format(value, '+.3f')}")
            print(f"[probe] {name}: rows={task['rows']} " + " ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
