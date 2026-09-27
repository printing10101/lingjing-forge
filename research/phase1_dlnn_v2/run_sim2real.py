"""
Phase 1 进阶：Sim→Real 迁移实验

对比三条训练线:
    A: 合成数据训练 → 合成数据测试（baseline, 已跑）
    B: 合成预训练 → Piecuch 真实数据微调 → 真实数据测试（sim→real transfer）
    C: 纯 Piecuch 真实数据训练 → 真实数据测试（real-data baseline）

目标: 证明物理引导的 DL-LNN 在仅有 968 个真实样本时，通过合成预训练
      可获得优于纯真实训练的泛化能力。
"""

import sys, os, json, argparse
import torch
import numpy as np
from torch.utils.data import DataLoader, TensorDataset
from datetime import datetime

# Path setup
_PHASE1_DIR = os.path.dirname(os.path.abspath(__file__))
_RESEARCH_DIR = os.path.dirname(_PHASE1_DIR)  # research/
_PROJECT_ROOT = os.path.dirname(_RESEARCH_DIR)  # project root
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _PHASE1_DIR)

from config_v2 import Phase1Config
from models_v2 import DLLNNWithPhysicsV2
from trainer_v2 import Phase1Trainer, Phase1Metrics
from data_generator_v2 import LongHorizonChatterDataset


def load_piecuch_data():
    """加载预处理后的 Piecuch 数据集，转换为 PyTorch Tensors。"""
    import pandas as pd

    csv_path = os.path.join(_RESEARCH_DIR, "datasets", "piecuch_2025", "piecuch_dlnn_features.csv")
    df = pd.read_csv(csv_path)
    features = df[["n", "f", "ap", "ae", "H", "D", "z"]].values.astype(np.float32)
    labels = df["chatter_label"].values.astype(np.float32).reshape(-1, 1)
    X = torch.from_numpy(features)
    y = torch.from_numpy(labels)
    return X, y


def evaluate_model(model, X_test, y_test, device="cpu"):
    """评估模型并返回指标。"""
    model.eval()
    all_preds = []
    with torch.no_grad():
        for i in range(0, len(X_test), 32):
            x = X_test[i : i + 32].to(device)
            y_pred, _ = model(x, use_horizon=False)
            all_preds.append(y_pred.cpu().numpy())
    preds = np.concatenate(all_preds).flatten()
    targets = y_test.numpy().flatten()
    return {
        "MAE": Phase1Metrics.mae(preds, targets),
        "RMSE": Phase1Metrics.rmse(preds, targets),
        "R²": Phase1Metrics.r2(preds, targets),
    }


def main():
    parser = argparse.ArgumentParser(description="Sim→Real Transfer Experiment")
    parser.add_argument("--syn-samples", type=int, default=2000)
    parser.add_argument("--epochs-pretrain", type=int, default=20)
    parser.add_argument("--epochs-finetune", type=int, default=10)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--output", type=str, default="phase1_dlnn_v2/results_sim2real")
    args = parser.parse_args()

    config_base = Phase1Config(
        hidden_dim=args.hidden,
        num_layers=3,
        prediction_horizon=20,
        batch_size=32,
        device=args.device,
    )

    print("=" * 60)
    print(" Sim→Real 迁移实验")
    print(f" 启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 数据准备
    print("\n[1/5] 加载数据...")

    # 合成数据
    syn_ds = LongHorizonChatterDataset(
        num_samples=args.syn_samples,
        prediction_horizon=20,
        noise_level=0.02,
        seed=42,
    )
    n_syn = len(syn_ds)
    n_syn_train = int(n_syn * 0.8)
    syn_train, syn_test = torch.utils.data.random_split(
        syn_ds,
        [n_syn_train, n_syn - n_syn_train],
    )
    syn_train_loader = DataLoader(syn_train, batch_size=32, shuffle=True)
    syn_test_loader = DataLoader(syn_test, batch_size=32, shuffle=False)
    print(f"  合成数据: {n_syn_train} train / {n_syn - n_syn_train} test")

    # Piecuch 真实数据
    X_real, y_real = load_piecuch_data()
    n_real = len(X_real)
    idx = torch.randperm(n_real)
    n_real_train = int(n_real * 0.7)
    X_train_real, y_train_real = X_real[idx[:n_real_train]], y_real[idx[:n_real_train]]
    X_test_real, y_test_real = X_real[idx[n_real_train:]], y_real[idx[n_real_train:]]
    print(f"  Piecuch 真实数据: {n_real_train} train / {n_real - n_real_train} test")

    # Line A: 纯合成 (已完成)
    print("\n[2/5] Line A: 纯合成训练...")
    model_a = DLLNNWithPhysicsV2(
        input_dim=7,
        hidden_dim=args.hidden,
        num_layers=3,
        prediction_horizon=20,
        tau_init=0.1,
    )
    config_a = Phase1Config(
        num_epochs_stage1=args.epochs_pretrain,
        num_epochs_stage2=5,
        num_epochs_stage3=0,
        hidden_dim=args.hidden,
    )
    trainer_a = Phase1Trainer(config_a, model_a, device=args.device)
    trainer_a.train_stage1(syn_train_loader, syn_test_loader)
    # Eval on synthetic test
    _, metrics_a_syn = trainer_a._validate(syn_test_loader, stage=1)
    # Eval on real test (zero-shot)
    metrics_a_real_zs = evaluate_model(model_a, X_test_real, y_test_real, args.device)
    print(f"  Line A syn→syn: R²={metrics_a_syn['r2']:.4f}")
    print(f"  Line A syn→real (zero-shot): R²={metrics_a_real_zs['R²']:.4f}")

    # Line B: 合成预训练 真实微调 (SimReal Transfer)
    print("\n[3/5] Line B: 合成预训练 → 真实微调...")
    model_b = DLLNNWithPhysicsV2(
        input_dim=7,
        hidden_dim=args.hidden,
        num_layers=3,
        prediction_horizon=20,
        tau_init=0.1,
    )
    config_b_pretrain = Phase1Config(
        num_epochs_stage1=args.epochs_pretrain,
        num_epochs_stage2=0,
        num_epochs_stage3=0,
        hidden_dim=args.hidden,
    )
    trainer_b = Phase1Trainer(config_b_pretrain, model_b, device=args.device)
    trainer_b.train_stage1(syn_train_loader, syn_test_loader)

    # Fine-tune on real data
    real_train_ds = TensorDataset(X_train_real, y_train_real)
    real_train_loader = DataLoader(real_train_ds, batch_size=32, shuffle=True)
    real_val_loader = DataLoader(
        TensorDataset(X_test_real, y_test_real),
        batch_size=32,
        shuffle=False,
    )

    config_b_ft = Phase1Config(
        num_epochs_stage1=args.epochs_finetune,
        num_epochs_stage2=0,
        num_epochs_stage3=0,
        hidden_dim=args.hidden,
        lr_stage1=5e-4,
    )
    trainer_b_ft = Phase1Trainer(config_b_ft, model_b, device=args.device)
    trainer_b_ft.train_stage1(real_train_loader, real_val_loader)

    metrics_b_real = evaluate_model(model_b, X_test_real, y_test_real, args.device)
    print(f"  Line B sim→real transfer: R²={metrics_b_real['R²']:.4f}")

    # Line C: 纯真实数据训练
    print("\n[4/5] Line C: 纯真实数据训练...")
    model_c = DLLNNWithPhysicsV2(
        input_dim=7,
        hidden_dim=args.hidden,
        num_layers=3,
        prediction_horizon=20,
        tau_init=0.1,
    )
    config_c = Phase1Config(
        num_epochs_stage1=args.epochs_pretrain,
        num_epochs_stage2=0,
        num_epochs_stage3=0,
        hidden_dim=args.hidden,
    )
    trainer_c = Phase1Trainer(config_c, model_c, device=args.device)
    trainer_c.train_stage1(real_train_loader, real_val_loader)

    metrics_c_real = evaluate_model(model_c, X_test_real, y_test_real, args.device)
    print(f"  Line C real-only: R²={metrics_c_real['R²']:.4f}")

    # 汇总
    print(f"\n{'=' * 60}")
    print(f" Sim→Real 迁移实验结果")
    print(f"{'=' * 60}")
    print(f"  Line A (纯合成, syn test):     R² = {metrics_a_syn['r2']:.4f}")
    print(f"  Line A (纯合成, real test ZS):  R² = {metrics_a_real_zs['R²']:.4f}")
    print(f"  Line B (合成→真实迁移):          R² = {metrics_b_real['R²']:.4f}")
    print(f"  Line C (纯真实):                R² = {metrics_c_real['R²']:.4f}")
    print(f"  Δ(B-A, transfer gain):          ΔR² = {metrics_b_real['R²'] - metrics_a_real_zs['R²']:.4f}")
    print(f"  Δ(B-C, pretrain benefit):       ΔR² = {metrics_b_real['R²'] - metrics_c_real['R²']:.4f}")
    print(f"{'=' * 60}")

    # 保存
    results = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "syn_samples": args.syn_samples,
            "epochs_pretrain": args.epochs_pretrain,
            "epochs_finetune": args.epochs_finetune,
            "hidden_dim": args.hidden,
        },
        "line_a_syn_test": {"R²": metrics_a_syn["r2"], "MAE": metrics_a_syn["mae"]},
        "line_a_real_zs": metrics_a_real_zs,
        "line_b_transfer": metrics_b_real,
        "line_c_real_only": metrics_c_real,
    }
    os.makedirs(args.output, exist_ok=True)
    with open(os.path.join(args.output, "sim2real_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n结果已保存: {args.output}/sim2real_results.json")


if __name__ == "__main__":
    main()
