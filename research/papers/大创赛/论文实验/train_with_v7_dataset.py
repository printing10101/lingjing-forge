"""
使用v7高级非线性铣削仿真数据集训练DL-LNN和基线模型
v7数据集特点：
- 基于改进Tlusty理论，具有真实叶瓣结构
- 稳定/不稳定比例约40-60%
- 考虑过程阻尼、刀具跳动、非线性切削力
"""

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import json
import time
from typing import Dict, Tuple, List


# 1. 数据集类
class MillingStabilityDataset(Dataset):
    """铣削稳定性数据集"""

    def __init__(self, data_dict: Dict, dataset_name: str):
        self.dataset_name = dataset_name
        sld_data = data_dict["sld_data"]

        # 提取特征和标签
        self.X = []
        self.y = []
        self.a_crit = []

        for point in sld_data:
            # 特征：转速、切深、振动特征
            features = [
                point["n_spindle"],
                point["a_p"],
                point["x_rms"],
                point["x_peak"],
                point["dominant_freq"],
                point["harmonic_ratio"],
            ]
            self.X.append(features)

            # 标签：临界切深（回归任务）
            self.y.append(point["a_crit"])
            self.a_crit.append(point["a_crit"])

        self.X = np.array(self.X, dtype=np.float32)
        self.y = np.array(self.y, dtype=np.float32).reshape(-1, 1)
        self.a_crit = np.array(self.a_crit, dtype=np.float32).reshape(-1, 1)

        # 标准化
        self.X_mean = self.X.mean(axis=0)
        self.X_std = self.X.std(axis=0) + 1e-8
        self.y_mean = self.y.mean()
        self.y_std = self.y.std() + 1e-8

        self.X_norm = (self.X - self.X_mean) / self.X_std
        self.y_norm = (self.y - self.y_mean) / self.y_std

        print(
            f"✓ {dataset_name}: {len(self.X)} samples, "
            f"stable={sum(1 for p in sld_data if p['is_stable'])}, "
            f"unstable={sum(1 for p in sld_data if not p['is_stable'])}"
        )

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X_norm[idx], self.y_norm[idx]


# 2. 基线模型
class SimpleNN(nn.Module):
    """简单全连接网络"""

    def __init__(self, input_dim=6, hidden_dim=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(self, x):
        return self.net(x)


class LSTMModel(nn.Module):
    """LSTM基线模型"""

    def __init__(self, input_dim=6, hidden_dim=128, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden_dim // 2, 1)
        )

    def forward(self, x):
        # 将输入reshape为序列格式
        x_seq = x.unsqueeze(1)  # [batch, 1, features]
        _, (h_n, _) = self.lstm(x_seq)
        out = self.fc(h_n[-1])
        return out


class GRUModel(nn.Module):
    """GRU基线模型"""

    def __init__(self, input_dim=6, hidden_dim=128, num_layers=2):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden_dim // 2, 1)
        )

    def forward(self, x):
        x_seq = x.unsqueeze(1)
        _, h_n = self.gru(x_seq)
        out = self.fc(h_n[-1])
        return out


class TransformerModel(nn.Module):
    """Transformer基线模型"""

    def __init__(self, input_dim=6, d_model=128, nhead=4, num_layers=2):
        super().__init__()
        self.input_proj = nn.Linear(input_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model * 2, dropout=0.2, batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers)
        self.fc = nn.Sequential(
            nn.Linear(d_model, d_model // 2), nn.ReLU(), nn.Dropout(0.2), nn.Linear(d_model // 2, 1)
        )

    def forward(self, x):
        x_proj = self.input_proj(x).unsqueeze(1)
        out = self.transformer(x_proj)
        out = self.fc(out.squeeze(1))
        return out


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

        self.tau_net = nn.Sequential(
            nn.Linear(input_size + hidden_size, 64), nn.ReLU(), nn.Linear(64, hidden_size), nn.Softplus()
        )

        self.input_gate = nn.Sequential(nn.Linear(input_size, input_size), nn.Sigmoid())

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
        self, input_dim=6, hidden_dim=256, embedding_dim=128, num_ltc_layers=3, delay_steps=10, output_dim=1, dt=0.1
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

    def forward(self, x, history=None):
        embedded, history = self.delay_embedding(x, history)

        batch_size = x.size(0)
        h_states = [torch.zeros(batch_size, cell.hidden_size, device=x.device) for cell in self.ltc_cells]

        for i, ltc_cell in enumerate(self.ltc_cells):
            h_states[i] = ltc_cell(embedded if i == 0 else h_states[i - 1], h_states[i], self.dt)
            h_states[i] = self.layer_norms[i](h_states[i])

        h_final = h_states[-1]
        output = self.output_proj(h_final)

        return output, history


# 4. 训练函数
def train_model(model, train_loader, val_loader, device, num_epochs=150, lr=0.0005):
    """训练模型"""
    model = model.to(device)
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-6)
    criterion = nn.MSELoss()

    best_val_loss = float("inf")
    best_model_state = None
    patience = 30
    patience_counter = 0

    for epoch in range(num_epochs):
        model.train()
        train_loss = 0.0

        for batch_X, batch_y in train_loader:
            batch_X = batch_X.to(device)
            batch_y = batch_y.to(device)

            optimizer.zero_grad()

            if isinstance(model, AdvancedDLNNModel):
                outputs, _ = model(batch_X)
            else:
                outputs = model(batch_X)

            loss = criterion(outputs, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_loss += loss.item()

        scheduler.step()

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for batch_X, batch_y in val_loader:
                batch_X = batch_X.to(device)
                batch_y = batch_y.to(device)

                if isinstance(model, AdvancedDLNNModel):
                    outputs, _ = model(batch_X)
                else:
                    outputs = model(batch_X)

                loss = criterion(outputs, batch_y)
                val_loss += loss.item()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_model_state = model.state_dict().copy()
            patience_counter = 0
        else:
            patience_counter += 1

        if patience_counter >= patience:
            break

    model.load_state_dict(best_model_state)
    return model


# 5. 评价指标
def compute_metrics(predictions, actuals, y_mean, y_std):
    """计算评价指标（反标准化后）"""
    # 反标准化
    pred_orig = predictions * y_std + y_mean
    actual_orig = actuals * y_std + y_mean

    pred_orig = pred_orig.flatten()
    actual_orig = actual_orig.flatten()

    mae = float(np.mean(np.abs(pred_orig - actual_orig)))
    rmse = float(np.sqrt(np.mean((pred_orig - actual_orig) ** 2)))
    ss_res = np.sum((actual_orig - pred_orig) ** 2)
    ss_tot = np.sum((actual_orig - np.mean(actual_orig)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    if len(pred_orig) > 1 and np.std(pred_orig) > 1e-8 and np.std(actual_orig) > 1e-8:
        corr_matrix = np.corrcoef(pred_orig, actual_orig)
        pcc = float(corr_matrix[0, 1])
    else:
        pcc = 0.0

    return {"MAE": mae, "RMSE": rmse, "R2": r2, "PCC": pcc}


# 6. 主函数
def main():
    print("=" * 60)
    print("使用v7高级非线性铣削仿真数据集训练模型")
    print("=" * 60)

    # 加载v7数据集
    with open("all_advanced_datasets_v7.json", "r", encoding="utf-8") as f:
        all_data = json.load(f)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    results = {}

    for dataset_name, data_dict in all_data.items():
        print(f"\n{'=' * 60}")
        print(f"处理数据集: {dataset_name}")
        print(f"{'=' * 60}")

        # 创建数据集
        dataset = MillingStabilityDataset(data_dict, dataset_name)

        # 划分训练/验证/测试集
        n_samples = len(dataset)
        n_train = int(0.7 * n_samples)
        n_val = int(0.15 * n_samples)

        indices = np.random.permutation(n_samples)
        train_idx = indices[:n_train]
        val_idx = indices[n_train : n_train + n_val]
        test_idx = indices[n_train + n_val :]

        # 创建DataLoader
        train_dataset = torch.utils.data.Subset(dataset, train_idx)
        val_dataset = torch.utils.data.Subset(dataset, val_idx)
        test_dataset = torch.utils.data.Subset(dataset, test_idx)

        train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
        test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

        dataset_results = {}

        # 训练基线模型
        models = {
            "SimpleNN": SimpleNN(input_dim=6, hidden_dim=128),
            "LSTM": LSTMModel(input_dim=6, hidden_dim=128, num_layers=2),
            "GRU": GRUModel(input_dim=6, hidden_dim=128, num_layers=2),
            "Transformer": TransformerModel(input_dim=6, d_model=128, nhead=4, num_layers=2),
        }

        for model_name, model in models.items():
            print(f"\n训练 {model_name}...")
            start_time = time.time()

            model = train_model(model, train_loader, val_loader, device, num_epochs=150, lr=0.001)

            # 测试
            model.eval()
            all_preds = []
            all_targets = []

            with torch.no_grad():
                for batch_X, batch_y in test_loader:
                    batch_X = batch_X.to(device)
                    outputs = model(batch_X)
                    all_preds.append(outputs.cpu().numpy())
                    all_targets.append(batch_y.numpy())

            preds = np.vstack(all_preds)
            targets = np.vstack(all_targets)

            metrics = compute_metrics(preds, targets, dataset.y_mean, dataset.y_std)
            elapsed = time.time() - start_time

            dataset_results[model_name] = {
                "MAE": metrics["MAE"],
                "RMSE": metrics["RMSE"],
                "R2": metrics["R2"],
                "PCC": metrics["PCC"],
                "time": elapsed,
            }

            print(
                f"  MAE={metrics['MAE']:.6f}, RMSE={metrics['RMSE']:.6f}, "
                f"R2={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}, time={elapsed:.1f}s"
            )

        # 训练DL-LNN
        print(f"\n训练 DL-LNN...")
        start_time = time.time()

        dlnn_model = AdvancedDLNNModel(
            input_dim=6, hidden_dim=256, embedding_dim=128, num_ltc_layers=3, delay_steps=10, dt=0.1
        )

        dlnn_model = train_model(dlnn_model, train_loader, val_loader, device, num_epochs=150, lr=0.0005)

        # 测试
        dlnn_model.eval()
        all_preds = []
        all_targets = []

        with torch.no_grad():
            for batch_X, batch_y in test_loader:
                batch_X = batch_X.to(device)
                outputs, _ = dlnn_model(batch_X)
                all_preds.append(outputs.cpu().numpy())
                all_targets.append(batch_y.numpy())

        preds = np.vstack(all_preds)
        targets = np.vstack(all_targets)

        metrics = compute_metrics(preds, targets, dataset.y_mean, dataset.y_std)
        elapsed = time.time() - start_time

        dataset_results["DL-LNN"] = {
            "MAE": metrics["MAE"],
            "RMSE": metrics["RMSE"],
            "R2": metrics["R2"],
            "PCC": metrics["PCC"],
            "time": elapsed,
        }

        print(
            f"  MAE={metrics['MAE']:.6f}, RMSE={metrics['RMSE']:.6f}, "
            f"R2={metrics['R2']:.4f}, PCC={metrics['PCC']:.4f}, time={elapsed:.1f}s"
        )

        results[dataset_name] = dataset_results

    # 计算平均指标
    print(f"\n{'=' * 60}")
    print("汇总结果")
    print(f"{'=' * 60}")

    avg_metrics = {}
    for model_name in ["SimpleNN", "LSTM", "GRU", "Transformer", "DL-LNN"]:
        mae_list = [results[ds][model_name]["MAE"] for ds in results]
        rmse_list = [results[ds][model_name]["RMSE"] for ds in results]
        r2_list = [results[ds][model_name]["R2"] for ds in results]
        pcc_list = [results[ds][model_name]["PCC"] for ds in results]
        time_list = [results[ds][model_name]["time"] for ds in results]

        avg_metrics[model_name] = {
            "avg_MAE": np.mean(mae_list),
            "std_MAE": np.std(mae_list),
            "avg_RMSE": np.mean(rmse_list),
            "avg_R2": np.mean(r2_list),
            "avg_PCC": np.mean(pcc_list),
            "avg_time": np.mean(time_list),
        }

        print(f"\n{model_name}:")
        print(f"  平均MAE: {avg_metrics[model_name]['avg_MAE']:.6f} ± {avg_metrics[model_name]['std_MAE']:.6f}")
        print(f"  平均RMSE: {avg_metrics[model_name]['avg_RMSE']:.6f}")
        print(f"  平均R2: {avg_metrics[model_name]['avg_R2']:.4f}")
        print(f"  平均PCC: {avg_metrics[model_name]['avg_PCC']:.4f}")
        print(f"  平均时间: {avg_metrics[model_name]['avg_time']:.1f}s")

    # 保存结果
    with open("v7_training_results.json", "w", encoding="utf-8") as f:
        json.dump({"per_dataset": results, "average_metrics": avg_metrics}, f, indent=2, ensure_ascii=False)

    print(f"\n✓ 结果已保存到 v7_training_results.json")

    # 找出最佳模型
    best_model = min(avg_metrics.items(), key=lambda x: x[1]["avg_MAE"])
    print(f"\n🏆 最佳模型: {best_model[0]} (MAE={best_model[1]['avg_MAE']:.6f})")

    # 计算DL-LNN相对于基线的改进
    dlnn_mae = avg_metrics["DL-LNN"]["avg_MAE"]
    for model_name in ["SimpleNN", "LSTM", "GRU", "Transformer"]:
        baseline_mae = avg_metrics[model_name]["avg_MAE"]
        improvement = (baseline_mae - dlnn_mae) / baseline_mae * 100
        print(f"DL-LNN vs {model_name}: {improvement:+.2f}%")


if __name__ == "__main__":
    main()
