"""
Phase 1 一键启动脚本

用法：
    cd research/phase1_dlnn_v2
    python run_phase1.py                    # 默认配置
    python run_phase1.py --epochs 100 100 50 --horizon 50  # 自定义参数
    python run_phase1.py --no-physics-tau    # 关掉物理 τ 约束（消融）
    python run_phase1.py --device cpu        # 强制 CPU
"""

import sys
import os
import argparse
import json
import torch
import numpy as np
from datetime import datetime
from torch.utils.data import DataLoader

# 添加项目根目录到路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_v2 import Phase1Config
from models_v2 import DLLNNWithPhysicsV2, DLLNNModelV2
from trainer_v2 import Phase1Trainer, Phase1Metrics
from data_generator_v2 import (
    LongHorizonChatterDataset,
    create_long_horizon_dataloaders,
    MultiDatasetTrajectoryLoader,
)


def parse_args():
    parser = argparse.ArgumentParser(description="DL-LNN Phase 1: 可学习延迟 + 课程式训练")
    parser.add_argument(
        "--epochs", nargs=3, type=int, default=[100, 150, 50], help="三阶段 epoch 数（默认: 100 150 50）"
    )
    parser.add_argument("--horizon", type=int, default=50, help="预测时域帧数（默认: 50）")
    parser.add_argument("--hidden", type=int, default=128, help="隐藏层维度（默认: 128）")
    parser.add_argument("--layers", type=int, default=3, help="LTC 层数（默认: 3）")
    parser.add_argument("--samples", type=int, default=10000, help="训练样本数（默认: 10000）")
    parser.add_argument("--batch", type=int, default=32, help="批次大小（默认: 32）")
    parser.add_argument(
        "--lr", nargs=3, type=float, default=[1e-3, 5e-4, 1e-4], help="三阶段学习率（默认: 1e-3 5e-4 1e-4）"
    )
    parser.add_argument("--tau-init", type=float, default=0.1, help="延迟 τ 初始值 (s)（默认: 0.1）")
    parser.add_argument("--lambda-tau", type=float, default=0.01, help="τ 正则化系数（默认: 0.01）")
    parser.add_argument("--no-physics-tau", action="store_true", help="禁用 τ 物理正则化（消融）")
    parser.add_argument("--device", type=str, default="cuda", help="设备（默认: cuda）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认: 42）")
    parser.add_argument("--output", type=str, default="phase1_dlnn_v2/results", help="输出目录")
    parser.add_argument("--no-horizon", action="store_true", help="使用标量预测代替长时域预测（消融）")
    parser.add_argument("--multi-dataset", action="store_true", help="启用多数据集验证（Synthetic+NUAA+NIST+6061-T6）")
    return parser.parse_args()


def main():
    args = parse_args()

    # 配置
    config = Phase1Config(
        experiment_name=f"phase1_h{args.horizon}_h{args.hidden}_l{args.layers}",
        num_epochs_stage1=args.epochs[0],
        num_epochs_stage2=args.epochs[1],
        num_epochs_stage3=args.epochs[2],
        prediction_horizon=args.horizon,
        hidden_dim=args.hidden,
        num_layers=args.layers,
        num_samples=args.samples,
        batch_size=args.batch,
        lr_stage1=args.lr[0],
        lr_stage2=args.lr[1],
        lr_stage3=args.lr[2],
        tau_init=args.tau_init,
        lambda_tau_reg=0.0 if args.no_physics_tau else args.lambda_tau,
        tau_phys_enabled=not args.no_physics_tau,
        device=args.device,
        seed=args.seed,
        output_dir=args.output,
        checkpoint_dir=f"{args.output}/checkpoints",
    )

    print("=" * 65)
    print(f" DL-LNN Phase 1: 可学习延迟 + 课程式训练")
    print(f" 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 实验名称: {config.experiment_name}")
    print(f" 设备: {config.device}")
    print(f" 预测时域: {config.prediction_horizon} 帧")
    print(f" 延迟 τ 初始值: {config.tau_init}s")
    print(f" τ 物理正则化: {'启用' if config.tau_phys_enabled else '禁用'}")
    print(f" 三阶段: {config.num_epochs_stage1} → {config.num_epochs_stage2} → {config.num_epochs_stage3} epochs")
    print(f" 模型: {config.hidden_dim} 隐藏, {config.num_layers} 层 LTC")
    print("=" * 65)

    # 数据
    print("\n[1/4] 准备数据...")
    train_loader, val_loader, test_loader = create_long_horizon_dataloaders(
        num_samples=config.num_samples,
        prediction_horizon=config.prediction_horizon,
        batch_size=config.batch_size,
        noise_level=0.02,
        seed=config.seed,
    )
    print(f"  训练集: {len(train_loader.dataset)} 样本 ({len(train_loader)} 批)")
    print(f"  验证集: {len(val_loader.dataset)} 样本 ({len(val_loader)} 批)")
    print(f"  测试集: {len(test_loader.dataset)} 样本 ({len(test_loader)} 批)")

    # 模型
    print("\n[2/4] 构建模型...")
    model = DLLNNWithPhysicsV2(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        prediction_horizon=config.prediction_horizon,
        dt=config.ltc_dt,
        dropout=config.dropout,
        tau_init=config.tau_init,
    )
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  总参数量: {total_params:,}")
    print(f"  可训练参数: {trainable_params:,}")
    print(f"  初始 τ: {[f'{t.item():.4f}s' for t in model.all_taus]}")

    # 训练
    print("\n[3/4] 开始训练...")
    trainer = Phase1Trainer(config, model, device=config.device)
    history = trainer.train(train_loader, val_loader)

    # 评估
    print(f"\n[4/4] 评估...")
    trainer.model.eval()
    all_preds, all_targets = [], []

    with torch.no_grad():
        for batch in test_loader:
            x, y_true, y_physics = trainer._unpack_batch(batch)
            y_physics_sq = y_physics[:, 0, :] if y_physics is not None and y_physics.dim() > 2 else y_physics
            y_pred, _ = model(x, physics_pred=y_physics_sq, use_horizon=False)
            # 正确切片：3D [B,H,1] 2D [B,1]
            if y_true.dim() > 2:
                yt = y_true[:, 0, :]
            elif y_true.dim() > 1 and y_true.shape[1] > 1:
                yt = y_true[:, 0:1]
            else:
                yt = y_true
            all_preds.append(y_pred.cpu().numpy())
            all_targets.append(yt.cpu().numpy())

    all_preds_np = np.concatenate(all_preds, axis=0)
    all_targets_np = np.concatenate(all_targets, axis=0)

    # 反归一化
    if trainer._target_stats_computed:
        all_preds_np = trainer.denormalize(all_preds_np)
        all_targets_np = trainer.denormalize(all_targets_np)

    test_metrics = {
        "MAE": Phase1Metrics.mae(all_preds_np, all_targets_np),
        "RMSE": Phase1Metrics.rmse(all_preds_np, all_targets_np),
        "R²": Phase1Metrics.r2(all_preds_np, all_targets_np),
    }
    print(f"\n  测试集指标:")
    for k, v in test_metrics.items():
        print(f"    {k}: {v:.6f}")

    # 最终 τ 报告
    if hasattr(model, "all_taus"):
        final_taus = [t.item() for t in model.all_taus]
        print(f"\n  最终 τ: {[f'{t:.6f}s' for t in final_taus]}")
        print(f"  τ 物理参考值 (60/n, n~5000rpm): {60 / 5000:.6f}s")

    # 保存
    print(f"\n  保存结果到 {config.output_dir}...")
    os.makedirs(config.output_dir, exist_ok=True)

    # 指标 JSON
    results = {
        "config": {k: str(v) if isinstance(v, (torch.Tensor, np.ndarray)) else v for k, v in config.__dict__.items()},
        "test_metrics": test_metrics,
        "total_params": total_params,
        "trainable_params": trainable_params,
        "final_taus": [t.item() for t in model.all_taus] if hasattr(model, "all_taus") else None,
        "timestamp": datetime.now().isoformat(),
    }
    with open(os.path.join(config.output_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 训练历史
    trainer.save_history(os.path.join(config.output_dir, "training_history.json"))

    # 检查点
    trainer.save_checkpoint(os.path.join(config.checkpoint_dir, "phase1_final.pt"))

    print(f"\n{'=' * 65}")
    print(f" Phase 1 训练完成！")
    print(f" 测试 MAE = {test_metrics['MAE']:.6f} mm")
    print(f" 测试 R² = {test_metrics['R²']:.6f}")
    print(f" 结果保存在: {config.output_dir}/")
    print(f"{'=' * 65}")

    # 消融对照提示
    if args.no_physics_tau:
        print("\n  [消融] 已禁用 τ 物理正则化。")
        print("  请对比启用正则化的结果（默认行为），验证 τ→60/n 的物理收敛性。")
    if args.no_horizon:
        print("\n  [消融] 已使用标量预测。")
        print("  请对比长时间预测结果，验证预测时域对精度的边际增益。")

    return results


if __name__ == "__main__":
    main()
