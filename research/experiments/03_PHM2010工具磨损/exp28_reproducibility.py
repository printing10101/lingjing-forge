"""
实验二十八：多次随机种子可复现性验证实验
使用10个不同随机种子重复训练，报告均值±标准差，验证结果统计可靠性
"""

import torch
import numpy as np
import json
import os
import time
from typing import Dict, List
from torch.utils.data import DataLoader

from models import DLLNNModel
from data_generator import PHM2010Dataset, create_dataloaders
from metrics import ChatterMetrics


def train_and_eval(seed: int, device: str = "cpu") -> Dict[str, float]:
    """使用指定种子训练并评估"""
    torch.manual_seed(seed)
    np.random.seed(seed)

    train_ds = PHM2010Dataset(num_samples=2000, noise_level=0.05, seed=seed)
    val_ds = PHM2010Dataset(num_samples=500, noise_level=0.05, seed=seed + 100)
    test_ds = PHM2010Dataset(num_samples=500, noise_level=0.05, seed=seed + 200)

    train_loader = DataLoader(train_ds, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=32, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=32, shuffle=False)

    model = DLLNNModel(input_dim=7, hidden_dim=64, num_layers=3, output_dim=1, dt=0.1, dropout=0.2)
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    criterion = torch.nn.MSELoss()

    best_val = float('inf')
    best_state = None

    for epoch in range(60):
        model.train()
        for features, labels, _ in train_loader:
            features, labels = features.to(device), labels.to(device)
            optimizer.zero_grad()
            out = model(features)
            if isinstance(out, tuple):
                out = out[0]
            loss = criterion(out, labels)
            loss.backward()
            optimizer.step()

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for features, labels, _ in val_loader:
                features, labels = features.to(device), labels.to(device)
                out = model(features)
                if isinstance(out, tuple):
                    out = out[0]
                val_loss += criterion(out, labels).item()
        val_loss /= len(val_loader)

        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    model.eval()
    preds, labels_list = [], []
    with torch.no_grad():
        for features, labels, _ in test_loader:
            features = features.to(device)
            out = model(features)
            if isinstance(out, tuple):
                out = out[0]
            preds.append(out.cpu().numpy())
            labels_list.append(labels.numpy())

    preds = np.concatenate(preds)
    labels_arr = np.concatenate(labels_list)
    return ChatterMetrics().compute_all(preds, labels_arr)


def main():
    print("=" * 60)
    print("实验二十八：多次随机种子可复现性验证实验")
    print("=" * 60)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用设备: {device}")

    seeds = [42, 43, 44, 45, 46, 47, 48, 49, 50, 51]
    models_to_test = {
        "DL-LNN": lambda: None,  # placeholder
        "LSTM": lambda: None,
        "GRU": lambda: None,
    }

    all_results = {}

    for model_name in ["DL-LNN", "LSTM", "GRU"]:
        print(f"\n模型: {model_name}")
        print("-" * 40)
        run_results = []

        for i, seed in enumerate(seeds):
            torch.manual_seed(seed)
            np.random.seed(seed)

            if model_name == "DL-LNN":
                model = DLLNNModel(input_dim=7, hidden_dim=64, num_layers=3, output_dim=1, dt=0.1, dropout=0.2)
            elif model_name == "LSTM":
                model = torch.nn.LSTM(input_size=7, hidden_size=64, num_layers=2, batch_first=True)
            else:
                model = torch.nn.GRU(input_size=7, hidden_size=64, num_layers=2, batch_first=True)

            train_ds = PHM2010Dataset(num_samples=2000, noise_level=0.05, seed=seed)
            test_ds = PHM2010Dataset(num_samples=500, noise_level=0.05, seed=seed + 200)
            train_loader = DataLoader(train_ds, batch_size=32, shuffle=True)
            test_loader = DataLoader(test_ds, batch_size=32, shuffle=False)

            model = model.to(device)
            optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
            criterion = torch.nn.MSELoss()

            best_val, best_state = float('inf'), None
            val_ds = PHM2010Dataset(num_samples=500, noise_level=0.05, seed=seed + 100)
            val_loader = DataLoader(val_ds, batch_size=32, shuffle=False)

            for epoch in range(60):
                model.train()
                for features, labels, _ in train_loader:
                    features, labels = features.to(device), labels.to(device)
                    optimizer.zero_grad()
                    out = model(features)
                    if isinstance(out, tuple):
                        out = out[0]
                    loss = criterion(out, labels)
                    loss.backward()
                    optimizer.step()

                model.eval()
                vl = 0.0
                with torch.no_grad():
                    for f, l, _ in val_loader:
                        f, l = f.to(device), l.to(device)
                        o = model(f)
                        if isinstance(o, tuple):
                            o = o[0]
                        vl += criterion(o, l).item()
                vl /= len(val_loader)
                if vl < best_val:
                    best_val = vl
                    best_state = {k: v.clone() for k, v in model.state_dict().items()}

            model.load_state_dict(best_state)
            model.eval()
            preds, labs = [], []
            with torch.no_grad():
                for f, l, _ in test_loader:
                    f = f.to(device)
                    o = model(f)
                    if isinstance(o, tuple):
                        o = o[0]
                    if model_name in ['LSTM', 'GRU'] and o.dim() == 3:
                        o = o[:, -1, :]
                    if o.shape[-1] != 1:
                        o = o.mean(dim=-1, keepdim=True)
                    preds.append(o.cpu().numpy())
                    labs.append(l.numpy())

            p = np.concatenate(preds)
            la = np.concatenate(labs)
            res = ChatterMetrics().compute_all(p, la)
            run_results.append(res)
            print(f"  种子 {seed}: MAE={res['mae']:.4f}, RMSE={res['rmse']:.4f}, R2={res['r2']:.4f}")

        # 统计
        metrics_keys = ['mae', 'rmse', 'r2', 'mape']
        stats = {}
        for mk in metrics_keys:
            vals = [r[mk] for r in run_results]
            stats[mk] = {
                "mean": round(float(np.mean(vals)), 4),
                "std": round(float(np.std(vals)), 4),
                "min": round(float(np.min(vals)), 4),
                "max": round(float(np.max(vals)), 4),
                "cv_pct": round(float(np.std(vals) / (np.mean(vals) + 1e-8) * 100), 2)
            }

        all_results[model_name] = {
            "seeds": seeds,
            "per_seed": run_results,
            "statistics": stats
        }

        print(f"\n  汇总: MAE={stats['mae']['mean']:.4f}±{stats['mae']['std']:.4f}, "
              f"R2={stats['r2']['mean']:.4f}±{stats['r2']['std']:.4f}")

    output_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'reproducibility_results.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "多次随机种子可复现性验证实验",
            "num_seeds": len(seeds),
            "results": all_results
        }, f, indent=2, ensure_ascii=False)

    print(f"\n结果已保存至: {output_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
