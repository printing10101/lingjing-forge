"""切削力 PINN 模型训练脚本。

使用合成数据训练 PINN 模型，结合 Kienzle 解析公式作为物理约束。

用法:
    cd python && python -m app.simulation.cutting_force.trainer --epochs 100
"""

from __future__ import annotations

import argparse
import logging
import os
import time


import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader

from app.simulation.cutting_force.kienzle import (
    compute_cutting_forces,
)
from app.simulation.cutting_force.pinn import (
    CuttingForcePINN,
    PINNLoss,
)

# P0#3 解耦: 通过 research_bridge 延迟导入，fallback 到 numpy random
try:
    from app.ai.lnn._research_bridge import get_set_global_seed

    _seed_func = get_set_global_seed()
    set_global_seed = (
        _seed_func
        if _seed_func is not None
        else (lambda seed=42: (__import__("random").seed(seed), __import__("numpy").random.seed(seed)))
    )
except ImportError:

    def set_global_seed(seed: int = 42) -> None:
        import random
        import numpy as np

        random.seed(seed)
        np.random.seed(seed)


def get_worker_init_fn(seed: int = 42):
    """Worker 初始化函数（纯 numpy，不依赖 torch）。"""

    def _init(worker_id: int) -> None:
        import numpy as np

        np.random.seed(seed + worker_id)

    return _init


logger = logging.getLogger(__name__)


class SyntheticCuttingForceDataset(Dataset):
    """合成切削力数据集。

    基于 Kienzle 公式生成训练数据，并添加噪声模拟实际工况。

    Args:
        num_samples: 样本数量
        material: 材料名称
        noise_ratio: 噪声比例 (相对于力值)
        seed: 随机种子
    """

    def __init__(
        self,
        num_samples: int = 5000,
        material: str = "45steel",
        noise_ratio: float = 0.05,
        seed: int = 42,
    ) -> None:
        super().__init__()
        self.num_samples = num_samples
        self.material = material
        self.noise_ratio = noise_ratio

        rng = np.random.RandomState(seed)
        ranges = CuttingForcePINN.PARAM_RANGES

        # 在原始参数空间均匀采样
        self.speeds = rng.uniform(ranges["speed"][0], ranges["speed"][1], num_samples)
        self.feeds = rng.uniform(ranges["feed"][0], ranges["feed"][1], num_samples)
        self.depths = rng.uniform(ranges["depth"][0], ranges["depth"][1], num_samples)

        # 归一化输入
        self.inputs_norm = np.stack(
            [
                (self.speeds - ranges["speed"][0]) / (ranges["speed"][1] - ranges["speed"][0]),
                (self.feeds - ranges["feed"][0]) / (ranges["feed"][1] - ranges["feed"][0]),
                (self.depths - ranges["depth"][0]) / (ranges["depth"][1] - ranges["depth"][0]),
            ],
            axis=1,
        ).astype(np.float32)

        # 使用 Kienzle 公式计算目标力（切屑厚度取 depth 的简化映射）
        # 注：系数在下方逐样本查询，避免整表引用
        forces = np.zeros((num_samples, 3), dtype=np.float32)
        for i in range(num_samples):
            # 简化映射: 切屑厚度 h 与切深 depth 正相关
            chip_thickness = self.depths[i] * 0.1  # h = depth * 0.1
            width = self.depths[i]  # 切削宽度近似等于切深
            result = compute_cutting_forces(
                material=material,
                width=max(width, 0.01),
                chip_thickness=max(chip_thickness, 0.001),
            )
            forces[i] = [result["Fx"], result["Fy"], result["Fz"]]

        # 添加噪声
        noise = rng.normal(0, noise_ratio, forces.shape).astype(np.float32)
        self.targets = forces * (1.0 + noise)
        self.targets = np.maximum(self.targets, 0.0)  # 确保非负

        # 同时保存无噪声的 Kienzle 解析解（用于物理损失）
        self.kienzle_forces = forces.copy()

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return (
            torch.tensor(self.inputs_norm[idx]),
            torch.tensor(self.targets[idx]),
            torch.tensor(self.kienzle_forces[idx]),
        )


class CuttingForceTrainer:
    """切削力 PINN 训练器。

    Args:
        model: PINN 模型实例
        learning_rate: 学习率
        physics_weight: 物理损失权重
        epochs: 训练轮数
        batch_size: 批大小
        device: 设备 ('cpu' 或 'cuda')
        save_dir: 模型保存目录
    """

    def __init__(
        self,
        model: CuttingForcePINN | None = None,
        learning_rate: float = 1e-3,
        physics_weight: float = 0.1,
        epochs: int = 100,
        batch_size: int = 64,
        device: str = "cpu",
        save_dir: str | None = None,
        seed: int = 42,
    ) -> None:
        # 必须在任何随机操作之前调用，确保可复现性
        self.seed = seed
        set_global_seed(seed)

        self.device = torch.device(device)
        self.epochs = epochs
        self.batch_size = batch_size

        if model is None:
            model = CuttingForcePINN()
        self.model = model.to(self.device)

        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, mode="min", factor=0.5, patience=10)
        self.criterion = PINNLoss(physics_weight=physics_weight)

        if save_dir is None:
            save_dir = os.path.join(os.path.dirname(__file__), "checkpoints")
        self.save_dir = save_dir
        os.makedirs(self.save_dir, exist_ok=True)

        self.history: dict[str, list[float]] = {
            "train_loss": [],
            "val_loss": [],
            "data_loss": [],
            "physics_loss": [],
        }

    def train(
        self,
        train_dataset: SyntheticCuttingForceDataset | None = None,
        val_dataset: SyntheticCuttingForceDataset | None = None,
    ) -> dict[str, list[float]]:
        """执行训练。

        Args:
            train_dataset: 训练数据集，None 时自动生成
            val_dataset: 验证数据集，None 时自动生成

        Returns:
            训练历史记录
        """
        if train_dataset is None:
            train_dataset = SyntheticCuttingForceDataset(num_samples=5000)
        if val_dataset is None:
            val_dataset = SyntheticCuttingForceDataset(num_samples=1000, seed=123)

        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            worker_init_fn=get_worker_init_fn(self.seed),
        )
        val_loader = DataLoader(
            val_dataset,
            batch_size=self.batch_size,
            shuffle=False,
            worker_init_fn=get_worker_init_fn(self.seed),
        )

        logger.info(f"模型参数量: {self.model.count_parameters():,}")
        logger.info("训练样本数: %s", len(train_dataset))
        logger.info("验证样本数: %s", len(val_dataset))
        logger.info("训练轮数: %s", self.epochs)
        logger.info("设备: %s", self.device)

        best_val_loss = float("inf")

        for epoch in range(1, self.epochs + 1):
            # 训练阶段
            self.model.train()
            train_losses: list[float] = []
            train_data_losses: list[float] = []
            train_phys_losses: list[float] = []

            for inputs, targets, kienzle in train_loader:
                inputs = inputs.to(self.device)
                targets = targets.to(self.device)
                kienzle = kienzle.to(self.device)

                self.optimizer.zero_grad()
                preds = self.model(inputs)
                losses = self.criterion(preds, targets, kienzle)
                losses["total_loss"].backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()

                train_losses.append(losses["total_loss"].item())
                train_data_losses.append(losses["data_loss"].item())
                train_phys_losses.append(losses["physics_loss"].item())

            # 验证阶段
            self.model.eval()
            val_losses: list[float] = []
            with torch.no_grad():
                for inputs, targets, kienzle in val_loader:
                    inputs = inputs.to(self.device)
                    targets = targets.to(self.device)
                    kienzle = kienzle.to(self.device)
                    preds = self.model(inputs)
                    losses = self.criterion(preds, targets, kienzle)
                    val_losses.append(losses["total_loss"].item())

            avg_train = float(np.mean(train_losses))
            avg_val = float(np.mean(val_losses))
            avg_data = float(np.mean(train_data_losses))
            avg_phys = float(np.mean(train_phys_losses))

            self.history["train_loss"].append(avg_train)
            self.history["val_loss"].append(avg_val)
            self.history["data_loss"].append(avg_data)
            self.history["physics_loss"].append(avg_phys)

            self.scheduler.step(avg_val)

            if avg_val < best_val_loss:
                best_val_loss = avg_val
                self._save_checkpoint("best_model.pt")

            if epoch % 10 == 0 or epoch == 1:
                logger.info(
                    f"Epoch {epoch:3d}/{self.epochs} | "
                    f"Train: {avg_train:.4f} | Val: {avg_val:.4f} | "
                    f"Data: {avg_data:.4f} | Phys: {avg_phys:.4f}"
                )

        self._save_checkpoint("last_model.pt")
        logger.info(f"训练完成。最佳验证损失: {best_val_loss:.4f}")
        return self.history

    def _save_checkpoint(self, filename: str) -> None:
        """保存模型检查点。"""
        path = os.path.join(self.save_dir, filename)
        torch.save(
            {
                "model_state_dict": self.model.state_dict(),
                "model_config": {
                    "input_dim": 3,
                    "hidden_dim": 64,
                    "num_blocks": 3,
                    "output_dim": 3,
                },
                "optimizer_state_dict": self.optimizer.state_dict(),
                "history": self.history,
            },
            path,
        )


def main() -> None:
    """命令行训练入口。"""
    parser = argparse.ArgumentParser(description="切削力 PINN 模型训练")
    parser.add_argument("--epochs", type=int, default=100, help="训练轮数")
    parser.add_argument("--lr", type=float, default=1e-3, help="学习率")
    parser.add_argument("--batch-size", type=int, default=64, help="批大小")
    parser.add_argument("--physics-weight", type=float, default=0.1, help="物理损失权重")
    parser.add_argument("--samples", type=int, default=5000, help="训练样本数")
    parser.add_argument("--material", type=str, default="45steel", help="材料名称")
    parser.add_argument("--device", type=str, default="cpu", help="设备")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 训练入口设置全局种子，确保实验可复现
    set_global_seed(args.seed)

    model = CuttingForcePINN()
    trainer = CuttingForceTrainer(
        model=model,
        learning_rate=args.lr,
        physics_weight=args.physics_weight,
        epochs=args.epochs,
        batch_size=args.batch_size,
        device=args.device,
        seed=args.seed,
    )

    train_ds = SyntheticCuttingForceDataset(num_samples=args.samples, material=args.material)
    val_ds = SyntheticCuttingForceDataset(num_samples=max(500, args.samples // 5), material=args.material, seed=123)

    start = time.time()
    history = trainer.train(train_ds, val_ds)
    elapsed = time.time() - start

    logger.info(f"\n训练耗时: {elapsed:.2f}s")
    logger.info(f"最终训练损失: {history['train_loss'][-1]:.4f}")
    logger.info(f"最终验证损失: {history['val_loss'][-1]:.4f}")
    logger.info(f"模型参数量: {model.count_parameters():,}")


if __name__ == "__main__":
    main()
