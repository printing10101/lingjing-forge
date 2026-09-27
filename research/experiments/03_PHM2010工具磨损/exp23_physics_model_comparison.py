"""
与物理解析模型的深度对比实验
对比DL-LNN与传统物理解析模型（Tlusty、Altintas等）的性能差异

实验设计：
1. 实现简化的Tlusty颤振稳定性模型
2. 实现Altintas经验模型
3. 在相同数据集上对比数据驱动模型与物理解析模型
4. 分析两种方法的互补性
"""

import sys
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ModelConfig
from models import DLLNNWithPhysics, BaselineLSTM, BaselineTransformer, BaselinePINN
from data_generator import Industrial6061T6Dataset, create_dataloaders
from metrics import ChatterMetrics


# 物理解析模型实现


class TlustyModel:
    """
    简化的Tlusty颤振稳定性模型

    基于再生颤振理论，考虑刀具-工件系统的动态特性
    a_lim = K * (1 + (2*pi*n*N/z)^2) / (Kc * b)
    其中：
    - K: 系统刚度
    - n: 主轴转速
    - N: 齿数
    - z: 模态数
    - Kc: 切削力系数
    - b: 切削宽度
    """

    def __init__(self, K=1e5, Kc=1000, N=4, m=10.0, c=100.0):
        """
        初始化Tlusty模型参数

        Args:
            K: 系统刚度 (N/m)
            Kc: 切削力系数 (N/mm^2)
            N: 刀具齿数
            m: 模态质量 (kg)
            c: 阻尼系数 (N·s/m)
        """
        self.K = K
        self.Kc = Kc
        self.N = N
        self.m = m
        self.c = c

    def predict(self, spindle_speed: np.ndarray, axial_depth: np.ndarray) -> np.ndarray:
        """
        预测极限切深

        Args:
            spindle_speed: 主轴转速 (rpm), 形状 (N,)
            axial_depth: 轴向切深 (mm), 形状 (N,)

        Returns:
            预测的极限切深 (mm), 形状 (N,)
        """
        # 简化的Tlusty模型
        # 考虑主轴转速对稳定性的影响
        omega = 2 * np.pi * spindle_speed / 60  # 角频率 (rad/s)

        # 系统固有频率
        omega_n = np.sqrt(self.K / self.m)

        # 频率比
        r = omega / omega_n

        # 动态放大因子
        H = 1 / np.sqrt((1 - r**2) ** 2 + (2 * 0.1 * r) ** 2)  # 假设阻尼比0.1

        # 极限切深预测
        # a_lim = K / (Kc * N * H)
        a_lim = self.K / (self.Kc * self.N * H)

        # 添加经验修正
        a_lim = a_lim * (1 + 0.001 * axial_depth)

        return a_lim


class AltintasModel:
    """
    Altintas经验模型

    基于大量实验数据拟合的经验公式
    a_lim = C * n^alpha * f^beta * d^gamma
    其中：
    - C: 经验系数
    - n: 主轴转速
    - f: 进给率
    - d: 切深
    - alpha, beta, gamma: 经验指数
    """

    def __init__(self, C=2.5, alpha=-0.2, beta=0.1, gamma=0.3):
        """
        初始化Altintas模型参数

        Args:
            C: 经验系数
            alpha: 转速指数
            beta: 进给率指数
            gamma: 切深指数
        """
        self.C = C
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma

    def predict(self, spindle_speed: np.ndarray, axial_depth: np.ndarray) -> np.ndarray:
        """
        预测极限切深

        Args:
            spindle_speed: 主轴转速 (rpm), 形状 (N,)
            axial_depth: 轴向切深 (mm), 形状 (N,)

        Returns:
            预测的极限切深 (mm), 形状 (N,)
        """
        # 假设进给率为常数
        feed = 0.1  # mm/tooth

        # Altintas经验公式
        a_lim = self.C * (spindle_speed / 1000) ** self.alpha * feed**self.beta * axial_depth**self.gamma

        return a_lim


class PhysicsBasedHybrid:
    """
    物理-数据混合模型

    结合物理解析模型和数据驱动模型的优势
    """

    def __init__(self, physics_model, data_model, alpha=0.5):
        """
        初始化混合模型

        Args:
            physics_model: 物理解析模型
            data_model: 数据驱动模型
            alpha: 物理模型权重 (0-1)
        """
        self.physics_model = physics_model
        self.data_model = data_model
        self.alpha = alpha

    def predict(self, features: torch.Tensor) -> torch.Tensor:
        """
        预测极限切深

        Args:
            features: 输入特征 (主轴转速, 切深)

        Returns:
            预测的极限切深
        """
        # 提取物理特征
        spindle_speed = features[:, 0].cpu().numpy() * 10000  # 反归一化
        axial_depth = features[:, 1].cpu().numpy() * 10  # 反归一化

        # 物理模型预测
        phys_pred = self.physics_model.predict(spindle_speed, axial_depth)
        phys_pred = torch.FloatTensor(phys_pred).to(features.device)

        # 数据模型预测
        with torch.no_grad():
            data_pred = self.data_model(features)
            if isinstance(data_pred, tuple):
                data_pred = data_pred[0]
            data_pred = data_pred.squeeze()

        # 加权融合
        hybrid_pred = self.alpha * phys_pred + (1 - self.alpha) * data_pred

        return hybrid_pred


def evaluate_physics_model(physics_model, test_loader: torch.utils.data.DataLoader) -> Dict[str, float]:
    """评估物理解析模型"""
    all_preds = []
    all_targets = []
    all_phys = []

    for batch in test_loader:
        x, y_true, y_physics = batch

        # 提取特征
        spindle_speed = x[:, 0].numpy() * 10000  # 反归一化
        axial_depth = x[:, 1].numpy() * 10

        # 物理模型预测
        pred = physics_model.predict(spindle_speed, axial_depth)

        all_preds.append(pred)
        all_targets.append(y_true.numpy())
        all_phys.append(y_physics.numpy())

    all_preds = np.concatenate(all_preds, axis=0).flatten()
    all_targets = np.concatenate(all_targets, axis=0).flatten()
    all_phys = np.concatenate(all_phys, axis=0).flatten()

    metrics_calc = ChatterMetrics()
    metrics = {
        "MAE": metrics_calc.mae(all_preds, all_targets),
        "RMSE": metrics_calc.rmse(all_preds, all_targets),
        "R2": metrics_calc.r2_score(all_preds, all_targets),
        "PCC": metrics_calc.physics_consistency_coefficient(all_preds, all_phys),
    }

    return metrics


def train_data_model(
    model: torch.nn.Module,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    config: ModelConfig,
    device: torch.device,
    num_epochs: int = 100,
) -> torch.nn.Module:
    """训练数据驱动模型"""
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs, eta_min=1e-5)

    best_val_loss = float("inf")
    best_state = None

    for epoch in range(num_epochs):
        model.train()
        train_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            x, y_true, _ = batch
            x = x.to(device)
            y_true = y_true.to(device)

            optimizer.zero_grad()
            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output

            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)

            loss = criterion(y_pred, y_true)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        train_loss /= max(n_batches, 1)

        model.eval()
        val_loss = 0.0
        n_val = 0

        with torch.no_grad():
            for batch in val_loader:
                x, y_true, _ = batch
                x = x.to(device)
                y_true = y_true.to(device)

                output = model(x)
                if isinstance(output, tuple):
                    y_pred = output[0]
                else:
                    y_pred = output

                if y_pred.shape != y_true.shape:
                    y_pred = y_pred.view_as(y_true)

                loss = criterion(y_pred, y_true)
                val_loss += loss.item()
                n_val += 1

        val_loss /= max(n_val, 1)
        scheduler.step()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    return model


def evaluate_data_model(
    model: torch.nn.Module, test_loader: torch.utils.data.DataLoader, device: torch.device
) -> Dict[str, float]:
    """评估数据驱动模型"""
    model.eval()
    all_preds = []
    all_targets = []
    all_phys = []

    with torch.no_grad():
        for batch in test_loader:
            x, y_true, y_physics = batch
            x = x.to(device)

            output = model(x)
            if isinstance(output, tuple):
                y_pred = output[0]
            else:
                y_pred = output

            if y_pred.shape != y_true.shape:
                y_pred = y_pred.view_as(y_true)

            all_preds.append(y_pred.cpu().numpy())
            all_targets.append(y_true.numpy())
            all_phys.append(y_physics.numpy())

    all_preds = np.concatenate(all_preds, axis=0).flatten()
    all_targets = np.concatenate(all_targets, axis=0).flatten()
    all_phys = np.concatenate(all_phys, axis=0).flatten()

    metrics_calc = ChatterMetrics()
    metrics = {
        "MAE": metrics_calc.mae(all_preds, all_targets),
        "RMSE": metrics_calc.rmse(all_preds, all_targets),
        "R2": metrics_calc.r2_score(all_preds, all_targets),
        "PCC": metrics_calc.physics_consistency_coefficient(all_preds, all_phys),
    }

    return metrics


def run_physics_comparison_experiment():
    """运行物理解析模型对比实验"""
    print("=" * 80)
    print("与物理解析模型的深度对比实验 (Physics Model Comparison)")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n使用设备: {device}")

    config = ModelConfig()

    # 加载数据
    print("\n[步骤 1/5] 加载工业 6061-T6 数据集...")
    train_loader, val_loader, test_loader = create_dataloaders(
        dataset_class=Industrial6061T6Dataset,
        dataset_params={"num_samples": 500, "noise_level": 0.08, "seed": 46},
        batch_size=config.batch_size,
        train_ratio=0.7,
        val_ratio=0.15,
    )
    print(f"  训练集样本数: {len(train_loader.dataset)}")
    print(f"  测试集样本数: {len(test_loader.dataset)}")

    results = {"timestamp": datetime.now().isoformat(), "physics_models": [], "data_models": [], "hybrid_models": []}

    # 物理解析模型评估
    print("\n[步骤 2/5] 评估物理解析模型...")

    # Tlusty模型
    print("  评估 Tlusty 模型...")
    tlusty_model = TlustyModel(K=1e5, Kc=1000, N=4)
    tlusty_metrics = evaluate_physics_model(tlusty_model, test_loader)

    results["physics_models"].append(
        {
            "model": "Tlusty",
            "description": "基于再生颤振理论的解析模型",
            "MAE": round(tlusty_metrics["MAE"], 6),
            "RMSE": round(tlusty_metrics["RMSE"], 6),
            "R2": round(tlusty_metrics["R2"], 6),
            "PCC": round(tlusty_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={tlusty_metrics['MAE']:.4f}, RMSE={tlusty_metrics['RMSE']:.4f}, "
        f"R²={tlusty_metrics['R2']:.4f}, PCC={tlusty_metrics['PCC']:.4f}"
    )

    # Altintas模型
    print("  评估 Altintas 模型...")
    altintas_model = AltintasModel(C=2.5, alpha=-0.2, beta=0.1, gamma=0.3)
    altintas_metrics = evaluate_physics_model(altintas_model, test_loader)

    results["physics_models"].append(
        {
            "model": "Altintas",
            "description": "基于实验数据的经验模型",
            "MAE": round(altintas_metrics["MAE"], 6),
            "RMSE": round(altintas_metrics["RMSE"], 6),
            "R2": round(altintas_metrics["R2"], 6),
            "PCC": round(altintas_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={altintas_metrics['MAE']:.4f}, RMSE={altintas_metrics['RMSE']:.4f}, "
        f"R²={altintas_metrics['R2']:.4f}, PCC={altintas_metrics['PCC']:.4f}"
    )

    # 数据驱动模型评估
    print("\n[步骤 3/5] 评估数据驱动模型...")

    # DL-LNN
    print("  训练 DL-LNN...")
    torch.manual_seed(42)
    np.random.seed(42)

    ct_ltc = DLLNNWithPhysics(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
        dt=config.ltc_dt,
        dropout=config.dropout,
    ).to(device)

    ct_ltc = train_data_model(ct_ltc, train_loader, val_loader, config, device, 100)
    ct_ltc_metrics = evaluate_data_model(ct_ltc, test_loader, device)

    results["data_models"].append(
        {
            "model": "DL-LNN",
            "description": "连续时间LTC网络（本方法）",
            "MAE": round(ct_ltc_metrics["MAE"], 6),
            "RMSE": round(ct_ltc_metrics["RMSE"], 6),
            "R2": round(ct_ltc_metrics["R2"], 6),
            "PCC": round(ct_ltc_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={ct_ltc_metrics['MAE']:.4f}, RMSE={ct_ltc_metrics['RMSE']:.4f}, "
        f"R²={ct_ltc_metrics['R2']:.4f}, PCC={ct_ltc_metrics['PCC']:.4f}"
    )

    # LSTM
    print("  训练 LSTM...")
    from models import BaselineLSTM

    torch.manual_seed(42)
    np.random.seed(42)

    lstm = BaselineLSTM(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
    ).to(device)

    lstm = train_data_model(lstm, train_loader, val_loader, config, device, 100)
    lstm_metrics = evaluate_data_model(lstm, test_loader, device)

    results["data_models"].append(
        {
            "model": "LSTM",
            "description": "长短期记忆网络",
            "MAE": round(lstm_metrics["MAE"], 6),
            "RMSE": round(lstm_metrics["RMSE"], 6),
            "R2": round(lstm_metrics["R2"], 6),
            "PCC": round(lstm_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={lstm_metrics['MAE']:.4f}, RMSE={lstm_metrics['RMSE']:.4f}, "
        f"R²={lstm_metrics['R2']:.4f}, PCC={lstm_metrics['PCC']:.4f}"
    )

    # PINN
    print("  训练 PINN...")
    from models import BaselinePINN

    torch.manual_seed(42)
    np.random.seed(42)

    pinn = BaselinePINN(
        input_dim=config.input_dim,
        hidden_dim=config.hidden_dim,
        num_layers=config.num_layers,
        output_dim=config.output_dim,
    ).to(device)

    pinn = train_data_model(pinn, train_loader, val_loader, config, device, 100)
    pinn_metrics = evaluate_data_model(pinn, test_loader, device)

    results["data_models"].append(
        {
            "model": "PINN",
            "description": "物理信息神经网络",
            "MAE": round(pinn_metrics["MAE"], 6),
            "RMSE": round(pinn_metrics["RMSE"], 6),
            "R2": round(pinn_metrics["R2"], 6),
            "PCC": round(pinn_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={pinn_metrics['MAE']:.4f}, RMSE={pinn_metrics['RMSE']:.4f}, "
        f"R²={pinn_metrics['R2']:.4f}, PCC={pinn_metrics['PCC']:.4f}"
    )

    # 混合模型评估
    print("\n[步骤 4/5] 评估物理-数据混合模型...")

    # DL-LNN + Tlusty 混合
    print("  评估 DL-LNN + Tlusty 混合模型...")
    hybrid_tlusty = PhysicsBasedHybrid(tlusty_model, ct_ltc, alpha=0.3)

    # 评估混合模型
    all_preds = []
    all_targets = []
    all_phys = []

    for batch in test_loader:
        x, y_true, y_physics = batch
        x = x.to(device)

        pred = hybrid_tlusty.predict(x)

        all_preds.append(pred.cpu().numpy())
        all_targets.append(y_true.numpy())
        all_phys.append(y_physics.numpy())

    all_preds = np.concatenate(all_preds, axis=0).flatten()
    all_targets = np.concatenate(all_targets, axis=0).flatten()
    all_phys = np.concatenate(all_phys, axis=0).flatten()

    metrics_calc = ChatterMetrics()
    hybrid_tlusty_metrics = {
        "MAE": metrics_calc.mae(all_preds, all_targets),
        "RMSE": metrics_calc.rmse(all_preds, all_targets),
        "R2": metrics_calc.r2_score(all_preds, all_targets),
        "PCC": metrics_calc.physics_consistency_coefficient(all_preds, all_phys),
    }

    results["hybrid_models"].append(
        {
            "model": "DL-LNN + Tlusty",
            "description": "DL-LNN与Tlusty模型加权融合（α=0.3）",
            "MAE": round(hybrid_tlusty_metrics["MAE"], 6),
            "RMSE": round(hybrid_tlusty_metrics["RMSE"], 6),
            "R2": round(hybrid_tlusty_metrics["R2"], 6),
            "PCC": round(hybrid_tlusty_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={hybrid_tlusty_metrics['MAE']:.4f}, RMSE={hybrid_tlusty_metrics['RMSE']:.4f}, "
        f"R²={hybrid_tlusty_metrics['R2']:.4f}, PCC={hybrid_tlusty_metrics['PCC']:.4f}"
    )

    # DL-LNN + Altintas 混合
    print("  评估 DL-LNN + Altintas 混合模型...")
    hybrid_altintas = PhysicsBasedHybrid(altintas_model, ct_ltc, alpha=0.2)

    all_preds = []
    all_targets = []
    all_phys = []

    for batch in test_loader:
        x, y_true, y_physics = batch
        x = x.to(device)

        pred = hybrid_altintas.predict(x)

        all_preds.append(pred.cpu().numpy())
        all_targets.append(y_true.numpy())
        all_phys.append(y_physics.numpy())

    all_preds = np.concatenate(all_preds, axis=0).flatten()
    all_targets = np.concatenate(all_targets, axis=0).flatten()
    all_phys = np.concatenate(all_phys, axis=0).flatten()

    hybrid_altintas_metrics = {
        "MAE": metrics_calc.mae(all_preds, all_targets),
        "RMSE": metrics_calc.rmse(all_preds, all_targets),
        "R2": metrics_calc.r2_score(all_preds, all_targets),
        "PCC": metrics_calc.physics_consistency_coefficient(all_preds, all_phys),
    }

    results["hybrid_models"].append(
        {
            "model": "DL-LNN + Altintas",
            "description": "DL-LNN与Altintas模型加权融合（α=0.2）",
            "MAE": round(hybrid_altintas_metrics["MAE"], 6),
            "RMSE": round(hybrid_altintas_metrics["RMSE"], 6),
            "R2": round(hybrid_altintas_metrics["R2"], 6),
            "PCC": round(hybrid_altintas_metrics["PCC"], 6),
        }
    )

    print(
        f"    MAE={hybrid_altintas_metrics['MAE']:.4f}, RMSE={hybrid_altintas_metrics['RMSE']:.4f}, "
        f"R²={hybrid_altintas_metrics['R2']:.4f}, PCC={hybrid_altintas_metrics['PCC']:.4f}"
    )

    # 保存结果
    print(f"\n[步骤 5/5] 保存实验结果...")

    output_dir = Path("results")
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "physics_model_comparison_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  结果已保存到: {output_file}")
    print("\n" + "=" * 80)
    print("与物理解析模型的深度对比实验完成!")
    print("=" * 80)

    return results


if __name__ == "__main__":
    run_physics_comparison_experiment()
