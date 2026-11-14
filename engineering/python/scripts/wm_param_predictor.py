"""分层参数响应模型：工况内插值 + 工况级偏移，并给出可调用预测器.

背景
----
JUST_CAS 100 段侧铣（4 工况 × ap×ae 5×5）的既有结果：
- 留参数组合：力 R²+0.961、电流 +0.998（工况族内插值强）；
- 留工况：力 +0.245、电流 +0.502（每因素仅 2 水平，外推不可辨识）。

本模块把两种情形合并为一个可用模型，并明确失败边界：

模型（log 空间，幂律形式，非中心化截距）
--------------------------------------
``log y = a_condition + b_ap·log ap + b_ae·log ae``

- 斜率 ``b_ap, b_ae`` 只由**工况内部**的参数变化估计（设计含工况 dummy，
  组间差异被 dummy 吸收），避免把工况差异错记为切深/切宽指数；
- 截距 ``a_condition`` 逐工况计算；工况未知时回退总体截距 ``a_pop``。

三种评估
--------
- ``within_condition``：每工况内留一 (ap,ae) 组合（25 折/组）；
- ``unseen_condition_hierarchical``：留一工况，截距回退 ``a_pop``；
- ``unseen_condition_pooled``：无 dummy 的池化对照（斜率吸收组间差异）。

产物：``fit_all`` 返回逐响应指标与最终系数；``predict`` 提供单点预测。
不接入生产服务；数值仅作探索性证据。

用法（仓库根）::

    python engineering/python/scripts/wm_param_predictor.py \
        --samples research/experiments/results/just_cas_param_response_20261114.json \
        --out research/experiments/results/just_cas_param_predictor_20261114.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_external_action_probe import ridge_fit, ridge_predict  # noqa: E402

RESPONSES = ("force_rms", "current_rms", "noise_rms")
LAMBDA_GRID = (1e-4, 1e-3, 1e-2, 1e-1)


def _log_features(row: dict, groups: list[str] | None = None) -> list[float]:
    values = [math.log(row["ap"]), math.log(row["ae"])]
    if groups:
        values += [1.0 if row["group"] == group else 0.0 for group in groups[1:]]
    return values


def _select_lambda(features: np.ndarray, targets: np.ndarray) -> float:
    n = features.shape[0]
    best_lam, best_mse = LAMBDA_GRID[0], float("inf")
    for lam in LAMBDA_GRID:
        errors = []
        for index in range(n):
            mask = np.arange(n) != index
            if mask.sum() < 3:
                continue
            model = ridge_fit(features[mask], targets[mask], lam)
            errors.append(float((ridge_predict(model, features[index:index + 1])[0] - targets[index]) ** 2))
        mse = float(np.mean(errors)) if errors else float("inf")
        if mse < best_mse - 1e-15:
            best_lam, best_mse = lam, mse
    return best_lam


def _fit_slopes(samples: list[dict], response: str, *, reference_groups: list[str] | None) -> dict:
    """拟合 log ap / log ae 斜率；有 reference_groups 时含 dummy 做组内识别。

    注意：ridge_fit 在标准化特征空间求解，权重需除以 x_scale 还原为
    「每单位 log 变化」的斜率。
    """
    x = np.array([_log_features(row, reference_groups) for row in samples])
    log_y = np.log(np.array([row[response] for row in samples]))
    lam = _select_lambda(x, log_y)
    model = ridge_fit(x, log_y, lam)
    weights = model["weights"] / model["x_scale"]
    return {"b_ap": float(weights[0]), "b_ae": float(weights[1]), "lambda": lam}


def _intercepts(samples: list[dict], response: str, slopes: dict) -> tuple[dict[str, float], float]:
    """a_g = mean(log y − b·log ap − b·log ae)，逐工况与总体。"""
    residuals: dict[str, list[float]] = {}
    for row in samples:
        value = (math.log(row[response]) - slopes["b_ap"] * math.log(row["ap"])
                 - slopes["b_ae"] * math.log(row["ae"]))
        residuals.setdefault(row["group"], []).append(value)
    per_group = {group: float(np.mean(values)) for group, values in residuals.items()}
    overall = float(np.mean([value for values in residuals.values() for value in values]))
    return per_group, overall


def evaluate_within_condition(samples: list[dict], response: str) -> dict:
    """每工况内留一 (ap,ae) 组合；只用同工况其余样本拟合。

    折内仅 1 个样本，逐折 R² 无意义，故用合并 SSE 计算 R²（基线=全体实际值均值）。
    """
    groups: dict[str, list[dict]] = {}
    for row in samples:
        groups.setdefault(row["group"], []).append(row)
    predictions, actuals, fold_mae = [], [], []
    for group, rows in sorted(groups.items()):
        combos: dict[tuple[float, float], list[dict]] = {}
        for row in rows:
            combos.setdefault((row["ap"], row["ae"]), []).append(row)
        for combo, hold_rows in sorted(combos.items()):
            train_rows = [row for row in rows if (row["ap"], row["ae"]) != combo]
            slopes = _fit_slopes(train_rows, response, reference_groups=None)
            per_group, _ = _intercepts(train_rows, response, slopes)
            for row in hold_rows:
                predicted = math.exp(per_group[group] + slopes["b_ap"] * math.log(row["ap"])
                                     + slopes["b_ae"] * math.log(row["ae"]))
                predictions.append(predicted)
                actuals.append(row[response])
                fold_mae.append(abs(predicted - row[response]))
    prediction_array, actual_array = np.array(predictions), np.array(actuals)
    baseline = float(actual_array.mean())
    sse = float(((prediction_array - actual_array) ** 2).sum())
    sst = float(((actual_array - baseline) ** 2).sum())
    return {"r2_pooled": 1.0 - sse / sst if sst > 0 else None,
            "mae_mean": float(np.mean(fold_mae)),
            "baseline_mae_mean": float(np.abs(actual_array - baseline).mean()),
            "folds": len(fold_mae)}


def evaluate_unseen_condition(samples: list[dict], response: str, *, with_dummies: bool) -> dict:
    """留一工况；新工况截距回退 a_pop（dummies 版斜率仅由组内变化识别）。"""
    groups = sorted({row["group"] for row in samples})
    r2_values, mae_values = [], []
    for group in groups:
        train_rows = [row for row in samples if row["group"] != group]
        hold_rows = [row for row in samples if row["group"] == group]
        reference = sorted({row["group"] for row in train_rows}) if with_dummies else None
        slopes = _fit_slopes(train_rows, response, reference_groups=reference)
        _, overall = _intercepts(train_rows, response, slopes)
        prediction = np.array([math.exp(overall + slopes["b_ap"] * math.log(row["ap"])
                                        + slopes["b_ae"] * math.log(row["ae"])) for row in hold_rows])
        y_hold = np.array([row[response] for row in hold_rows])
        baseline = float(np.mean([row[response] for row in train_rows]))
        r2_values.append(1.0 - float(((prediction - y_hold) ** 2).mean()) /
                         max(float(((y_hold - baseline) ** 2).mean()), 1e-12))
        mae_values.append(float(np.abs(prediction - y_hold).mean()))
    key = "unseen_condition_hierarchical" if with_dummies else "unseen_condition_pooled"
    return {key: {"r2_mean": float(np.mean(r2_values)), "mae_mean": float(np.mean(mae_values)), "folds": len(groups)}}[key]


def fit_final_model(samples: list[dict], response: str) -> dict:
    """全量拟合：组内识别斜率 + 逐工况截距 + 总体截距（新工况回退）。"""
    groups = sorted({row["group"] for row in samples})
    slopes = _fit_slopes(samples, response, reference_groups=groups)
    per_group, overall = _intercepts(samples, response, slopes)
    return {"response": response, "model": "log y = a_condition + b_ap·log ap + b_ae·log ae",
            "b_ap": slopes["b_ap"], "b_ae": slopes["b_ae"], "lambda": slopes["lambda"],
            "intercepts": per_group, "intercept_population": overall, "n_samples": len(samples)}


def predict(model: dict, *, ap: float, ae: float, group: str | None = None) -> float:
    """单点预测；group=None 表示新工况（截距回退总体均值）。"""
    if ap <= 0 or ae <= 0:
        raise ValueError("ap/ae 必须为正")
    if group is not None and group not in model["intercepts"]:
        raise ValueError(f"未知工况: {group}")
    intercept = model["intercepts"][group] if group is not None else model["intercept_population"]
    return float(math.exp(intercept + model["b_ap"] * math.log(ap) + model["b_ae"] * math.log(ae)))


def fit_all(samples: list[dict]) -> dict:
    report = {"responses": {}, "design": "log 幂律 + 工况截距（斜率组内识别）",
              "note": "工况因素各仅 2 水平，unseen_condition 仅代表截距回退上限"}
    for response in RESPONSES:
        report["responses"][response] = {
            "within_condition": evaluate_within_condition(samples, response),
            "unseen_condition_hierarchical": evaluate_unseen_condition(samples, response, with_dummies=True),
            "unseen_condition_pooled": evaluate_unseen_condition(samples, response, with_dummies=False),
            "final_model": fit_final_model(samples, response)}
    return report


def run(samples_path: Path, out_path: Path) -> dict:
    if out_path.exists():
        raise FileExistsError("输出已存在，请使用新路径")
    payload = json.loads(samples_path.read_text(encoding="utf-8"))
    samples = payload["samples"]
    if len(samples) != 100:
        raise ValueError(f"样本数应为 100，实际 {len(samples)}")
    report = {"source": {"samples_path": str(samples_path.resolve())}, **fit_all(samples)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.samples, args.out)
    except (ValueError, OSError, KeyError) as exc:
        print(f"[predictor failed] {exc}", file=sys.stderr)
        return 1
    for response, entry in report["responses"].items():
        within = entry["within_condition"]
        print(f"[{response}] within(pooled) R2={within['r2_pooled']:+.3f} MAE={within['mae_mean']:.3f} "
              f"({within['folds']}折) unseen_hier={entry['unseen_condition_hierarchical']['r2_mean']:+.3f} "
              f"pooled={entry['unseen_condition_pooled']['r2_mean']:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
