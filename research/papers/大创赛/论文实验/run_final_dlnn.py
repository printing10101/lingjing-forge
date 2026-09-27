"""
使用最佳激进配置运行DL-LNN最终实验
最佳配置: aggressive_large (hidden=256, embed=128, delay=10, layers=3, lr=0.0003, dt=0.1)
"""

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import json
import time
from typing import Dict, Tuple, List


# 1. Tlusty解析模型
class TlustyAnalyticalModel:
    def __init__(self, stiffness=1e6, modal_mass=100.0, damping_ratio=0.05, cutting_force_coeff=2000.0, num_teeth=4):
        self.stiffness = stiffness
        self.modal_mass = modal_mass
        self.damping_ratio = damping_ratio
        self.cutting_force_coeff = cutting_force_coeff
        self.num_teeth = num_teeth
        self.damping = 2 * damping_ratio * np.sqrt(stiffness * modal_mass)

    def frequency_response(self, omega):
        k = self.stiffness
        m = self.modal_mass
        c = self.damping
        return 1 / (k - m * omega**2 + 1j * c * omega)

    def compute_limiting_depth(self, spindle_speed, num_lobes=10):
        Ks = self.cutting_force_coeff * 1e6
        omega_n = np.sqrt(self.stiffness / self.modal_mass)
        f_n = omega_n / (2 * np.pi)
        omega_c = omega_n
        epsilon = 2 * np.pi * f_n * 60 / spindle_speed
        G = self.frequency_response(omega_c)
        real_G = np.real(G)
        real_G = np.where(np.abs(real_G) < 1e-10, 1e-10, real_G)
        a_lim_base = -1 / (2 * Ks * real_G)
        modulation = 1.0 / (1.0 + 0.1 * np.abs(np.sin(epsilon)))
        a_lim = a_lim_base * modulation
        a_lim = a_lim * 1000
        return np.clip(np.abs(a_lim), 0.01, 20.0)


# 2. 数据集类
class ChatterDataset(Dataset):
    def __init__(self, X, y, y_physics=None):
        self.X = torch.FloatTensor(X)
        self.y = torch.FloatTensor(y)
        self.y_physics = torch.FloatTensor(y_physics) if y_physics is not None else None

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        if self.y_physics is not None:
            return self.X[idx], self.y[idx], self.y_physics[idx]
        return self.X[idx], self.y[idx]


# 3. 高级DL-LNN模型
class AdvancedDelayEmbedding(nn.Module):
    """高级延迟嵌入层 - 使用注意力机制"""

    def __init__(self, input_dim, embedding_dim, delay_steps=10):
        super().__init__()
        self.input_dim = input_dim
        self.embedding_dim = embedding_dim
        self.delay_steps = delay_steps

        self.proj = nn.Linear(input_dim * delay_steps, embedding_dim)
        self.layer_norm = nn.LayerNorm(embedding_dim)

        self.attention = nn.MultiheadAttention(embed_dim=embedding_dim, num_heads=4, dropout=0.1, batch_first=True)

        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(0.2)

    def forward(self, x, history=None):
        if history is None:
            history = []

        history.append(x.detach())
        if len(history) > self.delay_steps:
            history.pop(0)

        while len(history) < self.delay_steps:
            history.insert(0, torch.zeros_like(x))

        concat = torch.cat(history, dim=-1)
        embedded = self.proj(concat)
        embedded = self.layer_norm(embedded)

        embedded_seq = embedded.unsqueeze(1)
        attn_out, _ = self.attention(embedded_seq, embedded_seq, embedded_seq)
        embedded = embedded + attn_out.squeeze(1)

        embedded = self.relu(embedded)
        embedded = self.dropout(embedded)

        return embedded, history


class AdvancedLTCCell(nn.Module):
    """高级LTC单元 - 带有自适应时间常数"""

    def __init__(self, input_size, hidden_size):
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size

        self.W = nn.Parameter(torch.randn(hidden_size, input_size))
        self.U = nn.Parameter(torch.randn(hidden_size, hidden_size))
        self.bias = nn.Parameter(torch.zeros(hidden_size))

        # 自适应时间常数网络
        self.tau_net = nn.Sequential(
            nn.Linear(input_size + hidden_size, 64), nn.ReLU(), nn.Linear(64, hidden_size), nn.Softplus()
        )

        # 输入门控
        self.input_gate = nn.Sequential(nn.Linear(input_size, input_size), nn.Sigmoid())

        # 遗忘门
        self.forget_gate = nn.Sequential(nn.Linear(input_size + hidden_size, hidden_size), nn.Sigmoid())

        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.U)

    def forward(self, x, h, dt=0.1):
        tau_input = torch.cat([x, h], dim=-1)
        tau = self.tau_net(tau_input)
        tau = torch.clamp(tau, min=0.1, max=10.0)

        gate = self.input_gate(x)

        forget_input = torch.cat([x, h], dim=-1)
        forget = self.forget_gate(forget_input)

        dh = torch.tanh(torch.mm(x * gate, self.W.t()) + torch.mm(h * forget, self.U.t()) + self.bias)

        h_new = h + dt * (dh - h) / tau

        return h_new


class AdvancedDLNNModel(nn.Module):
    """高级DL-LNN模型"""

    def __init__(
        self, input_dim=1, hidden_dim=256, embedding_dim=128, num_ltc_layers=3, delay_steps=10, output_dim=1, dt=0.1
    ):
        super().__init__()

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.embedding_dim = embedding_dim
        self.num_ltc_layers = num_ltc_layers
        self.delay_steps = delay_steps
        self.dt = dt

        self.delay_embedding = AdvancedDelayEmbedding(input_dim, embedding_dim, delay_steps)

        self.ltc_cells = nn.ModuleList(
            [AdvancedLTCCell(embedding_dim if i == 0 else hidden_dim, hidden_dim) for i in range(num_ltc_layers)]
        )

        self.layer_norms = nn.ModuleList([nn.LayerNorm(hidden_dim) for _ in range(num_ltc_layers)])

        self.output_proj = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim // 2, output_dim),
        )

        self.residual_calibrator = nn.Sequential(
            nn.Linear(hidden_dim + 1, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, output_dim),
        )

    def forward(self, x, physics_pred=None, history=None):
        embedded, history = self.delay_embedding(x, history)

        batch_size = x.size(0)
        h_states = [torch.zeros(batch_size, cell.hidden_size, device=x.device) for cell in self.ltc_cells]

        for i, ltc_cell in enumerate(self.ltc_cells):
            h_states[i] = ltc_cell(embedded if i == 0 else h_states[i - 1], h_states[i], self.dt)
            h_states[i] = self.layer_norms[i](h_states[i])

        h_final = h_states[-1]
        base_pred = self.output_proj(h_final)

        if physics_pred is not None:
            combined = torch.cat([h_final, physics_pred], dim=-1)
            residual = self.residual_calibrator(combined)
            output = base_pred + residual
        else:
            output = base_pred

        return output, history


# 4. 评价指标
def compute_metrics(predictions, actuals):
    predictions = predictions.flatten()
    actuals = actuals.flatten()

    mae = float(np.mean(np.abs(predictions - actuals)))
    rmse = float(np.sqrt(np.mean((predictions - actuals) ** 2)))
    ss_res = np.sum((actuals - predictions) ** 2)
    ss_tot = np.sum((actuals - np.mean(actuals)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    if len(predictions) > 1 and np.std(predictions) > 1e-8 and np.std(actuals) > 1e-8:
        corr_matrix = np.corrcoef(predictions, actuals)
        pcc = float(corr_matrix[0, 1])
    else:
        pcc = 0.0

    mape = float(np.mean(np.abs((actuals - predictions) / np.maximum(np.abs(actuals), 0.01))) * 100)

    return {"MAE": mae, "RMSE": rmse, "R2": r2, "MAPE": mape, "PCC": pcc}


# 5. 数据生成
def generate_datasets():
    datasets_config = [
        {"name": "PHM2010", "size": 315, "stiffness": 1.0e6, "damping": 0.05, "mass": 100.0},
        {"name": "NUAA", "size": 180, "stiffness": 1.2e6, "damping": 0.04, "mass": 80.0},
        {"name": "NIST", "size": 240, "stiffness": 0.9e6, "damping": 0.06, "mass": 120.0},
        {"name": "Benchmark1", "size": 200, "stiffness": 1.1e6, "damping": 0.045, "mass": 90.0},
        {"name": "6061T6", "size": 500, "stiffness": 1.3e6, "damping": 0.055, "mass": 85.0},
    ]

    datasets = {}
    for config in datasets_config:
        tlusty = TlustyAnalyticalModel(
            stiffness=config["stiffness"],
            modal_mass=config["mass"],
            damping_ratio=config["damping"],
            cutting_force_coeff=2000.0,
            num_teeth=4,
        )
        spindle_speeds = np.linspace(1000, 10000, config["size"])
        limit_depths = tlusty.compute_limiting_depth(spindle_speeds)
        noise_level = 0.05
        limit_depths_noisy = limit_depths * (1 + noise_level * np.random.randn(len(limit_depths)))

        datasets[config["name"]] = {
            "spindle_speeds": spindle_speeds,
            "limit_depths": limit_depths_noisy,
            "limit_depths_clean": limit_depths,
            "config": config,
        }

    return datasets


def prepare_dataloaders(datasets, batch_size=32):
    dataloaders = {}
    for name, data in datasets.items():
        X = data["spindle_speeds"].reshape(-1, 1).astype(np.float32)
        y = data["limit_depths"].reshape(-1, 1).astype(np.float32)

        X_mean, X_std = X.mean(), X.std()
        y_mean, y_std = y.mean(), y.std()

        X_norm = (X - X_mean) / (X_std + 1e-8)
        y_norm = (y - y_mean) / (y_std + 1e-8)

        tlusty = TlustyAnalyticalModel(
            stiffness=data["config"]["stiffness"],
            modal_mass=data["config"]["mass"],
            damping_ratio=data["config"]["damping"],
            cutting_force_coeff=2000.0,
            num_teeth=4,
        )
        y_physics_clean = tlusty.compute_limiting_depth(data["spindle_speeds"]).reshape(-1, 1).astype(np.float32)
        y_physics_norm = (y_physics_clean - y_mean) / (y_std + 1e-8)

        n_samples = len(X_norm)
        n_train = int(0.7 * n_samples)
        n_val = int(0.15 * n_samples)

        X_train, y_train = X_norm[:n_train], y_norm[:n_train]
        y_physics_train = y_physics_norm[:n_train]

        X_val, y_val = X_norm[n_train : n_train + n_val], y_norm[n_train : n_train + n_val]
        y_physics_val = y_physics_norm[n_train : n_train + n_val]

        X_test, y_test = X_norm[n_train + n_val :], y_norm[n_train + n_val :]
        y_physics_test = y_physics_norm[n_train + n_val :]

        train_dataset = ChatterDataset(X_train, y_train, y_physics_train)
        val_dataset = ChatterDataset(X_val, y_val, y_physics_val)
        test_dataset = ChatterDataset(X_test, y_test, y_physics_test)

        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
        test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

        dataloaders[name] = {
            "train": train_loader,
            "val": val_loader,
            "test": test_loader,
            "y_test_mean": y_mean,
            "y_test_std": y_std,
        }

    return dataloaders


# 6. 训练函数
def train_model(model, dataloaders, device, num_epochs=200, lr=0.0003):
    """训练模型 - 使用余弦退火"""
    model = model.to(device)
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4, betas=(0.9, 0.999))
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-6)
    criterion = nn.MSELoss()

    best_val_loss = float("inf")
    best_model_state = None
    patience = 40
    patience_counter = 0

    for epoch in range(num_epochs):
        model.train()
        train_loss = 0.0

        for batch_X, batch_y, batch_y_physics in dataloaders["train"]:
            batch_X = batch_X.to(device)
            batch_y = batch_y.to(device)

            optimizer.zero_grad()
            outputs, _ = model(batch_X, physics_pred=None)
            loss = criterion(outputs, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_loss += loss.item()

        scheduler.step()

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for batch_X, batch_y, batch_y_physics in dataloaders["val"]:
                batch_X = batch_X.to(device)
                batch_y = batch_y.to(device)
                outputs, _ = model(batch_X, physics_pred=None)
                loss = criterion(outputs, batch_y)
                val_loss += loss.item()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_model_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"    早停在 epoch {epoch}")
                break

    if best_model_state is not None:
        model.load_state_dict(best_model_state)

    return model


def evaluate_model(model, dataloaders, device):
    """评估模型"""
    model.eval()
    all_preds = []
    all_targets = []
    inference_times = []

    with torch.no_grad():
        for batch_X, batch_y, _ in dataloaders["test"]:
            batch_X = batch_X.to(device)
            start_time = time.time()
            outputs, _ = model(batch_X, physics_pred=None)
            inference_times.append((time.time() - start_time) * 1000 / batch_X.size(0))
            all_preds.append(outputs.cpu().numpy())
            all_targets.append(batch_y.numpy())

    all_preds = np.concatenate(all_preds, axis=0)
    all_targets = np.concatenate(all_targets, axis=0)

    y_mean = dataloaders["y_test_mean"]
    y_std = dataloaders["y_test_std"]

    all_preds = all_preds * y_std + y_mean
    all_targets = all_targets * y_std + y_mean

    metrics = compute_metrics(all_preds, all_targets)
    metrics["InferenceTime_ms"] = float(np.mean(inference_times))

    return metrics


# 7. 主函数
def run_final_experiment():
    """使用最佳激进配置运行最终实验"""
    print("=" * 80)
    print("DL-LNN最终实验（最佳激进配置）")
    print("=" * 80)

    best_config = {
        "name": "aggressive_large",
        "hidden_dim": 256,
        "embedding_dim": 128,
        "delay_steps": 10,
        "num_ltc_layers": 3,
        "lr": 0.0003,
        "dt": 0.1,
        "num_epochs": 200,
    }

    print(f"\n最佳配置: {best_config['name']}")
    print(f"  hidden_dim: {best_config['hidden_dim']}")
    print(f"  embedding_dim: {best_config['embedding_dim']}")
    print(f"  delay_steps: {best_config['delay_steps']}")
    print(f"  num_ltc_layers: {best_config['num_ltc_layers']}")
    print(f"  lr: {best_config['lr']}")
    print(f"  dt: {best_config['dt']}")
    print(f"  num_epochs: {best_config['num_epochs']}")

    np.random.seed(42)
    torch.manual_seed(42)

    print("\n生成数据集...")
    datasets = generate_datasets()

    print("准备数据加载器...")
    dataloaders = prepare_dataloaders(datasets, batch_size=32)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n使用设备: {device}")

    results = {}
    all_maes = []
    all_pccs = []
    all_rmses = []
    all_r2s = []
    all_inf_times = []
    all_train_times = []

    for dataset_name in datasets.keys():
        print(f"\n{'=' * 80}")
        print(f"处理数据集: {dataset_name}")
        print(f"{'=' * 80}")

        model = AdvancedDLNNModel(
            input_dim=1,
            hidden_dim=best_config["hidden_dim"],
            embedding_dim=best_config["embedding_dim"],
            num_ltc_layers=best_config["num_ltc_layers"],
            delay_steps=best_config["delay_steps"],
            output_dim=1,
            dt=best_config["dt"],
        )

        print(f"  训练模型...")
        start_time = time.time()
        model = train_model(
            model, dataloaders[dataset_name], device, num_epochs=best_config["num_epochs"], lr=best_config["lr"]
        )
        training_time = time.time() - start_time

        print(f"  评估模型...")
        metrics = evaluate_model(model, dataloaders[dataset_name], device)
        metrics["TrainingTime"] = training_time

        results[dataset_name] = metrics

        print(f"\n  数据集 {dataset_name} 结果:")
        print(f"    MAE: {metrics['MAE']:.4f} mm")
        print(f"    RMSE: {metrics['RMSE']:.4f} mm")
        print(f"    R2: {metrics['R2']:.4f}")
        print(f"    PCC: {metrics['PCC']:.4f}")
        print(f"    MAPE: {metrics['MAPE']:.4f}%")
        print(f"    训练时间: {training_time:.2f} s")
        print(f"    推理时间: {metrics['InferenceTime_ms']:.4f} ms")

        all_maes.append(metrics["MAE"])
        all_pccs.append(metrics["PCC"])
        all_rmses.append(metrics["RMSE"])
        all_r2s.append(metrics["R2"])
        all_inf_times.append(metrics["InferenceTime_ms"])
        all_train_times.append(training_time)

    avg_mae = float(np.mean(all_maes))
    std_mae = float(np.std(all_maes))
    avg_pcc = float(np.mean(all_pccs))
    avg_rmse = float(np.mean(all_rmses))
    avg_r2 = float(np.mean(all_r2s))
    avg_inf_time = float(np.mean(all_inf_times))
    avg_train_time = float(np.mean(all_train_times))

    print(f"\n{'=' * 80}")
    print(f"平均性能指标")
    print(f"{'=' * 80}")
    print(f"平均 MAE: {avg_mae:.4f} ± {std_mae:.4f} mm")
    print(f"平均 RMSE: {avg_rmse:.4f} mm")
    print(f"平均 R2: {avg_r2:.4f}")
    print(f"平均 PCC: {avg_pcc:.4f}")
    print(f"平均推理时间: {avg_inf_time:.4f} ms")
    print(f"平均训练时间: {avg_train_time:.2f} s")

    # 保存结果
    output_file = "final_dlnn_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    avg_metrics = {
        "avg_MAE": avg_mae,
        "std_MAE": std_mae,
        "avg_RMSE": avg_rmse,
        "avg_R2": avg_r2,
        "avg_PCC": avg_pcc,
        "avg_InferenceTime_ms": avg_inf_time,
        "avg_TrainingTime": avg_train_time,
    }

    with open("final_dlnn_avg_metrics.json", "w", encoding="utf-8") as f:
        json.dump(avg_metrics, f, indent=2, ensure_ascii=False)

    print(f"\n结果已保存到:")
    print(f"  - {output_file}")
    print(f"  - final_dlnn_avg_metrics.json")

    # 与基线模型对比
    print(f"\n{'=' * 80}")
    print(f"与基线模型对比")
    print(f"{'=' * 80}")
    print(f"SimpleNN: 1.1498 mm")
    print(f"LSTM:     0.8104 mm")
    print(f"GRU:      0.8126 mm")
    print(f"Transformer: 0.8210 mm")
    print(f"DL-LNN (最终): {avg_mae:.4f} mm")

    if avg_mae < 0.8104:
        improvement = (0.8104 - avg_mae) / 0.8104 * 100
        print(f"\n✓ DL-LNN优于LSTM基线 {improvement:.2f}%")
    else:
        gap = (avg_mae - 0.8104) / 0.8104 * 100
        print(f"\n✗ DL-LNN仍差于LSTM基线 {gap:.2f}%")

    return results, avg_metrics


if __name__ == "__main__":
    results, avg_metrics = run_final_experiment()
