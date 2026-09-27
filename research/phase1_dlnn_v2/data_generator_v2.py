"""
长时域预测数据生成器 （Phase 1 数据管线）

核心改动 vs 原 data_generator.py:
    1. 生成时序轨迹标签（而非单帧 a_lim）
    2. 模拟参数摄动下的 a_lim 时变轨迹
    3. 返回 (features_7d, y_future_horizon, y_physics_future_horizon)

轨迹生成策略：
    每样本随机选取基准工况 → 在基准附近产生参数微摄动
    → 对每个摄动步计算 Tlusty a_lim → 形成 [horizon] 长度轨迹。
    这模拟了真实切削中由于微小参数波动导致的 a_lim 时变。
"""

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from typing import Tuple, Dict, List, Optional
import sys
import os

# 导入原始数据生成器
# 项目根目录路径: 灵境制造（上线版）/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from experiments.data_generator import (
    TlustyAnalyticalModel,
    build_physics_features_7d,
    SyntheticChatterDataset,
    create_dataloaders,
)


class LongHorizonChatterDataset(Dataset):
    """
    长时域颤振轨迹数据集。

    对每个样本：
        1. 随机生成基准切削条件 x_base ∈ [7]
        2. 在预测时域内模拟微小参数摄动 x[0..horizon-1]
        3. 对每个摄动步计算 Tlusty a_lim → y_future[horizon]
        4. 返回 (x_base, y_future, y_physics_future)

    这使得模型需要学习"给定当前工况，预测未来时域的 a_lim 变化"，
    而非简单的点对点回归——对 DDE 网络提出了利用历史状态的需要。

    Args:
        num_samples: 总样本数
        prediction_horizon: 预测未来步数
        noise_level: 标签噪声水平
        perturbation_std: 参数摄动标准差（对归一化输入）
        seed: 随机种子
    """

    def __init__(
        self,
        num_samples: int = 10000,
        prediction_horizon: int = 50,
        noise_level: float = 0.02,
        perturbation_std: float = 0.005,
        seed: int = 42,
        spindle_speed_range: Tuple[float, float] = (1000, 10000),
        axial_depth_range: Tuple[float, float] = (0.1, 10.0),
    ):
        super().__init__()
        self.num_samples = num_samples
        self.prediction_horizon = prediction_horizon
        self.noise_level = noise_level
        self.perturbation_std = perturbation_std
        self.spindle_speed_range = spindle_speed_range
        self.axial_depth_range = axial_depth_range

        np.random.seed(seed)

        self.model = TlustyAnalyticalModel()
        self.data = self._generate()

    def _generate(self) -> Dict[str, np.ndarray]:
        """生成带时域轨迹的合成数据。"""
        # 基准参数采样
        spindle_speed = np.random.uniform(
            self.spindle_speed_range[0], self.spindle_speed_range[1],
            self.num_samples
        )
        axial_depth = np.random.uniform(
            self.axial_depth_range[0], self.axial_depth_range[1],
            self.num_samples
        )
        feed_rate = np.random.uniform(0.05, 0.5, self.num_samples)
        radial_depth = np.random.uniform(0.5, 8.0, self.num_samples)
        hardness = 95.0 + np.random.randn(self.num_samples) * 3.0
        tool_diameter = 10.0 + np.random.randn(self.num_samples) * 0.05
        num_teeth_arr = np.full(self.num_samples, 4.0)

        # 构造基准 7 维特征
        features = build_physics_features_7d(
            spindle_speed=spindle_speed,
            feed_rate=feed_rate,
            axial_depth=axial_depth,
            radial_depth=radial_depth,
            hardness=hardness,
            tool_diameter=tool_diameter,
            num_teeth=num_teeth_arr,
        )  # [N, 7]

        # 生成时域轨迹
        # 对每个样本，在特征空间中产生微摄动序列
        a_lim_trajectory = np.zeros((self.num_samples, self.prediction_horizon), dtype=np.float32)
        a_lim_clean_trajectory = np.zeros((self.num_samples, self.prediction_horizon), dtype=np.float32)

        for i in range(self.num_samples):
            # 基准特征（归一化）
            feat_base = features[i].copy()  # [7]

            traj_clean = np.zeros(self.prediction_horizon, dtype=np.float32)

            for t in range(self.prediction_horizon):
                # 微摄动：在归一化空间加高斯噪声（模拟参数波动）
                delta = np.random.randn(7) * self.perturbation_std * (1.0 + t * 0.01)
                feat_perturbed = np.clip(feat_base + delta, 0.0, 1.0)

                # 反归一化
                n_rpm = feat_perturbed[0] * 10000.0
                f_rate = feat_perturbed[1] * 0.5
                ax_dep = feat_perturbed[2] * 10.0
                rd = feat_perturbed[3] * 8.0
                hb = feat_perturbed[4] * 200.0
                td = feat_perturbed[5] * 20.0
                nz = feat_perturbed[6] * 6.0

                a_lim = self.model.compute_limiting_depth(
                    np.array([n_rpm]),
                    hardness=np.array([hb]),
                    tool_diameter=np.array([td]),
                    num_teeth=np.array([nz]),
                    feed_rate=np.array([f_rate]),
                    radial_depth=np.array([rd]),
                )
                traj_clean[t] = float(a_lim[0])

            a_lim_clean_trajectory[i] = traj_clean
            # 加噪声
            noise = np.random.randn(self.prediction_horizon) * self.noise_level * traj_clean
            a_lim_trajectory[i] = np.maximum(traj_clean + noise, 0.01)

        return {
            "features": features.astype(np.float32),
            "a_lim_trajectory": a_lim_trajectory.astype(np.float32),
            "a_lim_clean_trajectory": a_lim_clean_trajectory.astype(np.float32),
            "spindle_speed": spindle_speed.astype(np.float32),
        }

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Returns:
            features: [7] 输入特征（基准工况）
            a_lim_traj: [prediction_horizon, 1] 含噪 a_lim 轨迹
            a_lim_clean_traj: [prediction_horizon, 1] 无噪 a_lim 轨迹
        """
        features = torch.from_numpy(self.data["features"][idx])
        a_lim_traj = torch.from_numpy(self.data["a_lim_trajectory"][idx]).unsqueeze(-1)
        a_lim_clean_traj = torch.from_numpy(self.data["a_lim_clean_trajectory"][idx]).unsqueeze(-1)
        return features, a_lim_traj, a_lim_clean_traj


class MultiDatasetTrajectoryLoader:
    """
    多数据集混合加载器（用于评估不同数据域上的长期预测性能）。

    同时加载 Synthetic / NUAA / NIST / Benchmark-1 / 6061-T6 数据集，
    每个数据集生成对应的 LongHorizon 版本。
    """

    DATASET_CONFIGS = {
        "Synthetic": {
            "num_samples": 10000,
            "spindle_range": (1000, 10000),
            "axial_range": (0.1, 10.0),
            "hardness_mean": 95.0,
            "diameter_mean": 10.0,
        },
        "NUAA": {
            "num_samples": 1800,
            "spindle_range": (2500, 8500),
            "axial_range": (0.3, 6.0),
            "hardness_mean": 150.0,
            "diameter_mean": 12.0,
        },
        "NIST": {
            "num_samples": 1500,
            "spindle_range": (2000, 7000),
            "axial_range": (0.2, 5.0),
            "hardness_mean": 180.0,
            "diameter_mean": 10.0,
        },
        "6061-T6": {
            "num_samples": 500,
            "spindle_range": (2000, 8000),
            "axial_range": (0.5, 5.0),
            "hardness_mean": 95.0,
            "diameter_mean": 10.0,
        },
    }

    @classmethod
    def get_loaders(
        cls,
        prediction_horizon: int = 50,
        batch_size: int = 32,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15,
        seed: int = 42,
    ) -> Dict[str, Tuple[DataLoader, DataLoader, DataLoader]]:
        """
        返回所有数据集的 (train_loader, val_loader, test_loader) 字典。
        """
        loaders = {}
        for name, cfg in cls.DATASET_CONFIGS.items():
            dataset = LongHorizonChatterDataset(
                num_samples=cfg["num_samples"],
                prediction_horizon=prediction_horizon,
                spindle_speed_range=cfg["spindle_range"],
                axial_depth_range=cfg["axial_range"],
                seed=seed + hash(name) % 1000,
            )
            # 手动划分
            n = len(dataset)
            n_train = int(n * train_ratio)
            n_val = int(n * val_ratio)
            n_test = n - n_train - n_val

            torch.manual_seed(seed)
            train_ds, val_ds, test_ds = torch.utils.data.random_split(
                dataset, [n_train, n_val, n_test]
            )
            train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
            val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
            test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)
            loaders[name] = (train_loader, val_loader, test_loader)

        return loaders


def create_long_horizon_dataloaders(
    num_samples: int = 10000,
    prediction_horizon: int = 50,
    batch_size: int = 32,
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    noise_level: float = 0.02,
    seed: int = 42,
) -> Tuple[DataLoader, DataLoader, DataLoader]:
    """
    创建长时域预测的训练/验证/测试数据加载器。

    便捷函数，等效于 Phase 1 训练的默认数据输入。
    """
    dataset = LongHorizonChatterDataset(
        num_samples=num_samples,
        prediction_horizon=prediction_horizon,
        noise_level=noise_level,
        seed=seed,
    )
    n = len(dataset)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    n_test = n - n_train - n_val

    torch.manual_seed(seed)
    train_ds, val_ds, test_ds = torch.utils.data.random_split(
        dataset, [n_train, n_val, n_test]
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)

    return train_loader, val_loader, test_loader


if __name__ == "__main__":
    print("测试 LongHorizonChatterDataset...")
    dataset = LongHorizonChatterDataset(
        num_samples=500, prediction_horizon=20, noise_level=0.02, seed=42
    )
    print(f"数据集大小: {len(dataset)}")

    features, a_lim_traj, a_lim_clean_traj = dataset[0]
    print(f"特征形状: {features.shape}")
    print(f"a_lim 轨迹形状: {a_lim_traj.shape} 范围: [{a_lim_traj.min().item():.3f}, {a_lim_traj.max().item():.3f}]")
    print(f"a_lim 无噪轨迹形状: {a_lim_clean_traj.shape}")

    # 检查轨迹变化
    traj = a_lim_traj.squeeze().numpy()
    print(f"轨迹标准差: {traj.std():.4f} 范围: [{traj.min():.3f}, {traj.max():.3f}]")

    # 测试 dataloader
    train_loader, val_loader, test_loader = create_long_horizon_dataloaders(
        num_samples=500, prediction_horizon=20, batch_size=32, seed=42
    )
    print(f"\n训练批次: {len(train_loader)}, 验证批次: {len(val_loader)}, 测试批次: {len(test_loader)}")
    x, y, y_phys = next(iter(train_loader))
    print(f"批次 x: {x.shape}, y: {y.shape}, y_phys: {y_phys.shape}")

    print("\n测试通过！")
