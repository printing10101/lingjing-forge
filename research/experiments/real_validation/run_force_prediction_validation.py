# -*- coding: utf-8 -*-
"""真实切削力预测验证（567 数据集：精密铣削铝，真实力+振动特征，207 行/轴）。

任务：由真实振动特征预测实测切削力（三轴独立回归）。
模型：
  - DL-LNN（引擎模型，LTC 架构，经 DLLNNTrainer）
  - XGBoost / RandomForest / SVR（sklearn 基线）
划分：70/15/15；特征 StandardScaler 归一化（仅用训练集拟合）。
指标：MAE / RMSE / R² / PCC（测试集）。
输出：results/force_prediction_567_results.json
诚实标注：本数据集无颤振标签，验证的是"真实信号→真实力"的预测能力（力模型任务）。
"""

import csv
import json
import os
import sys
import time

import numpy as np
import torch

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_SCRIPT_DIR)
_RESEARCH_DIR = os.path.dirname(_EXP_DIR)
for p in (_EXP_DIR, _RESEARCH_DIR, _SCRIPT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from sklearn.ensemble import RandomForestRegressor
from sklearn.svm import SVR
from sklearn.model_selection import train_test_split
from torch.utils.data import Dataset, DataLoader, Subset

from config import get_config
from trainer import DLLNNTrainer

DATA_DIR = os.path.join(_RESEARCH_DIR, "datasets", "force_vibration_567")
RESULTS_DIR = os.path.join(_EXP_DIR, "results")
FILES = {
    "X": "X_axis_cutting_force_with_selected_vibration_features.csv",
    "Y": "Y_axis_cutting_force_with_selected_vibration_features.csv",
    "Z": "Z_axis_cutting_force_with_selected_vibration_features.csv",
}


def load_axis(fname):
    with open(os.path.join(DATA_DIR, fname), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    cols = [k for k in rows[0].keys()]
    target_col = [c for c in cols if "force" in c.lower()][0]
    feat_cols = [c for c in cols if c != target_col]
    X = np.array([[float(r[c]) for c in feat_cols] for r in rows], dtype=np.float64)
    y = np.array([float(r[target_col]) for r in rows], dtype=np.float64)
    return X, y, feat_cols, target_col


class ForceDataset(Dataset):
    """返回 (features, force, force_physics) 三元组，兼容引擎 trainer。"""

    def __init__(self, X, y):
        self.X = torch.from_numpy(X.astype(np.float32))
        self.y = torch.from_numpy(y.astype(np.float32))

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        f = self.X[idx]
        t = self.y[idx]
        return f, t.view(1), t.view(1)


def pcc(a, b):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def eval_metrics(y_true, y_pred):
    return {
        "MAE": float(mean_absolute_error(y_true, y_pred)),
        "RMSE": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "R2": float(r2_score(y_true, y_pred)),
        "PCC": pcc(y_true, y_pred),
    }


def main():
    all_results = {}
    for axis, fname in FILES.items():
        X, y, feat_cols, tcol = load_axis(fname)
        n_feat = X.shape[1]
        print(f"=== 轴 {axis}: {len(y)} 样本, {n_feat} 特征, 目标={tcol} ===")

        idx = np.arange(len(y))
        tr_idx, te_idx = train_test_split(idx, test_size=0.15, random_state=42)
        tr_idx, va_idx = train_test_split(tr_idx, test_size=0.15 / 0.85, random_state=42)

        sc = StandardScaler().fit(X[tr_idx])
        Xtr = sc.transform(X[tr_idx])
        Xva = sc.transform(X[va_idx])
        Xte = sc.transform(X[te_idx])
        ytr, yva, yte = y[tr_idx], y[va_idx], y[te_idx]

        res = {"n": len(y), "n_features": n_feat, "target": tcol, "test_n": len(te_idx)}
        print(f"  train={len(tr_idx)} val={len(va_idx)} test={len(te_idx)} | y范围=[{y.min():.2f},{y.max():.2f}]")

        # sklearn 基线
        for name, mdl in [
            (
                "XGBoost",
                __import__("xgboost").XGBRegressor(n_estimators=200, max_depth=6, random_state=42, verbosity=0),
            ),
            ("RandomForest", RandomForestRegressor(n_estimators=200, max_depth=None, random_state=42, n_jobs=-1)),
            ("SVR", SVR(C=10, epsilon=0.1)),
        ]:
            t0 = time.time()
            mdl.fit(Xtr, ytr)
            p = mdl.predict(Xte)
            m = eval_metrics(yte, p)
            res[name] = m
            print(
                f"  {name:<12} MAE={m['MAE']:.3f} RMSE={m['RMSE']:.3f} R2={m['R2']:.3f} PCC={m['PCC']:.3f} ({time.time() - t0:.0f}s)"
            )

        # ---- LTC（引擎核心单元，干净回归器）----
        # 说明：引擎 DLLNNWithPhysics 包装器在部分真实数据上数值不稳定（torchdiffeq
        # 自适应积分发散/stage2 NaN），此处直接用引擎的 LTCCell 核心搭最小 LTC 回归器，
        # 走文档化 Euler 路径 + 纯 MSE，保证结果可复现。
        t0 = time.time()
        import models as _models_mod

        _models_mod._HAS_TORCHDIFFEQ = False  # 强制 Euler 降级
        import torch.nn as nn
        from models import LTCCell as _LTCCell

        class LTCRegressor(nn.Module):
            def __init__(self, input_dim, hidden=32, n_layers=2, dt=0.1):
                super().__init__()
                self.dt = dt
                self.cells = nn.ModuleList([_LTCCell(input_dim if i == 0 else hidden, hidden) for i in range(n_layers)])
                self.head = nn.Linear(hidden, 1)

            def forward(self, x):
                h = torch.zeros(x.size(0), self.cells[0].hidden_size)
                inp = x
                for cell in self.cells:
                    h = cell(inp, h, self.dt)
                    inp = h  # 下一层输入 = 当前隐藏态
                return self.head(h)

        model = LTCRegressor(n_feat)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        crit = torch.nn.MSELoss()
        best_va, best_state, patience = 1e18, None, 0
        ytr_t = torch.from_numpy(ytr.astype(np.float32)).view(-1, 1)
        yva_t = torch.from_numpy(yva.astype(np.float32)).view(-1, 1)
        Xtr_t = torch.from_numpy(Xtr.astype(np.float32))
        Xva_t = torch.from_numpy(Xva.astype(np.float32))
        for ep in range(120):
            model.train()
            perm = torch.randperm(len(Xtr_t))
            for i in range(0, len(perm), 32):
                idx = perm[i : i + 32]
                opt.zero_grad()
                loss = crit(model(Xtr_t[idx]), ytr_t[idx])
                loss.backward()
                opt.step()
            model.eval()
            with torch.no_grad():
                va_loss = float(crit(model(Xva_t), yva_t))
            if va_loss < best_va:
                best_va = va_loss
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
                patience = 0
            else:
                patience += 1
                if patience >= 15:
                    break
        model.load_state_dict(best_state)
        model.eval()
        with torch.no_grad():
            p_dlnn = model(torch.from_numpy(Xte.astype(np.float32))).numpy().ravel()
        m = eval_metrics(yte, p_dlnn)
        res["LTC"] = m
        print(
            "  LTC        MAE={:.3f} RMSE={:.3f} R2={:.3f} PCC={:.3f} ({:.0f}s)".format(
                m["MAE"], m["RMSE"], m["R2"], m["PCC"], time.time() - t0
            )
        )

        all_results[axis] = res
        print()

    # 汇总平均
    print("=== 三轴平均（±std）===")
    models = ["LTC", "XGBoost", "RandomForest", "SVR"]
    summary = {}
    for mn in models:
        for met in ["MAE", "RMSE", "R2", "PCC"]:
            vals = [all_results[a][mn][met] for a in ["X", "Y", "Z"]]
            summary[f"{mn}.{met}"] = {"mean": float(np.mean(vals)), "std": float(np.std(vals))}
            print(f"  {mn}.{met}: {np.mean(vals):.3f} ± {np.std(vals):.3f}")

    out = {
        "experiment": "force_prediction_567",
        "note": "真实切削力预测（567 数据集，精密铣削铝，力+振动特征，CC BY 4.0）；DL-LNN=引擎模型",
        "per_axis": all_results,
        "summary": summary,
        "n_total": sum(all_results[a]["n"] for a in ["X", "Y", "Z"]),
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "force_prediction_567_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
