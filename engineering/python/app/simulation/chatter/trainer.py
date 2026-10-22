"""颤振稳定性 LTC 代理模型训练脚本。

设计依据（2026-10-22）
----------------------
1. **标签来源**：``app.simulation.chatter.stability.compute_stability_limit``。
   该函数于 2026-10-21 修正了负实部搜索（原实现只搜 ``Re[G] > 0``，极限被抬到
   25–31 mm），当前返回 1-DOF 绝对包络，裁剪到 [0.05, 20] mm。
2. **模型定位**：本模型是解析法的**快速代理（蒸馏）**，不是独立物理模型。
   推理时用 O(1) 前向替代解析法的 2000 点 FRF 扫描；不得对外表述为
   「神经网络优于解析法」或「AI 独立预测颤振」。
3. **口径一致性**：训练标签与解析回退路径同源，因此两条路径不会给出互相
   矛盾的稳定性结论（``tests/unit/test_chatter_trainer.py`` 有一致性断言）。
4. **输入信息量（诚实边界）**：当前 1-DOF 包络仅依赖 刚度 / 阻尼 / 切削力系数
   （模态质量固定 50 kg，见 ``MODAL_MASS_KG``）。转速、固有频率、刀具直径
   在解析口径下不参与计算，模型会学到"忽略"这些输入——这是该物理模型的
   固有局限，不是训练不足。叶瓣口径统一后需重新训练（见世界模型台账 P1）。

用法::

    cd engineering/python
    python -m app.simulation.chatter.trainer --samples 12000 --epochs 150
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from app.simulation.chatter.predictor import normalize_features
from app.simulation.chatter.stability import (
    ChatterParams,
    MachineParams,
    ToolParams,
    compute_stability_limit,
)

logger = logging.getLogger(__name__)

# 代理模型的假设切深（与 predict_stability 的回退口径一致：2 mm）
ASSUMED_DEPTH_MM = 2.0

# 模态质量在当前解析口径下固定（解析包络依赖 sqrt(k/m)，但 predictor 契约
# 不含模态质量，故训练时固定为默认值）
MODAL_MASS_KG = 50.0

# 参数采样区间（覆盖预设机床与常用刀具量级）
PARAM_RANGES: dict[str, tuple[float, float]] = {
    "stiffness": (5.0e6, 3.0e8),  # N/m，对数均匀
    "damping": (0.010, 0.100),  # 阻尼比
    "natural_freq": (80.0, 1200.0),  # Hz（仅作输入，包络实际用 sqrt(k/m)/2π）
    "diameter": (6.0, 25.0),  # mm（仅作输入，包络不含直径）
    "k_s": (1500.0, 3000.0),  # N/mm²
    "rpm": (1000.0, 20000.0),  # rpm（仅作输入，包络与转速无关）
}


def _sample_params(num_samples: int, seed: int) -> dict[str, np.ndarray]:
    """在参数区间内采样（刚度按对数均匀，覆盖三个数量级）。"""
    rng = np.random.RandomState(seed)
    lo, hi = PARAM_RANGES["stiffness"]
    stiffness = np.exp(rng.uniform(np.log(lo), np.log(hi), num_samples))
    return {
        "stiffness": stiffness,
        "damping": rng.uniform(*PARAM_RANGES["damping"], num_samples),
        "natural_freq": rng.uniform(*PARAM_RANGES["natural_freq"], num_samples),
        "diameter": rng.uniform(*PARAM_RANGES["diameter"], num_samples),
        "k_s": rng.uniform(*PARAM_RANGES["k_s"], num_samples),
        "rpm": rng.uniform(*PARAM_RANGES["rpm"], num_samples),
    }


class ChatterStabilityDataset(Dataset):
    """以 Tlusty 解析包络为标签的颤振稳定性数据集。

    Args:
        num_samples: 样本数
        seed: 采样种子
    """

    def __init__(self, num_samples: int = 12000, seed: int = 42) -> None:
        super().__init__()
        self.num_samples = num_samples
        p = _sample_params(num_samples, seed)

        inputs = np.zeros((num_samples, 6), dtype=np.float32)
        limit_depths = np.zeros(num_samples, dtype=np.float32)
        stable_labels = np.zeros(num_samples, dtype=np.float32)

        for i in range(num_samples):
            machine = MachineParams(
                machine_id="train_sample",
                stiffness_x=float(p["stiffness"][i]),
                stiffness_y=float(p["stiffness"][i]),
                stiffness_z=float(p["stiffness"][i]),
                damping_ratio=float(p["damping"][i]),
                natural_freq=float(p["natural_freq"][i]),
                modal_mass=MODAL_MASS_KG,
            )
            tool = ToolParams(
                tool_id="train_sample",
                diameter=float(p["diameter"][i]),
                num_flutes=4,
                helix_angle=30.0,
                cutting_force_coeff=float(p["k_s"][i]),
            )
            params = ChatterParams(
                spindle_rpm=float(p["rpm"][i]),
                machine=machine,
                tool=tool,
            )
            limit_depth = float(compute_stability_limit(params))

            inputs[i] = normalize_features(
                spindle_rpm=float(p["rpm"][i]),
                machine_stiffness=float(p["stiffness"][i]),
                machine_damping=float(p["damping"][i]),
                machine_freq=float(p["natural_freq"][i]),
                tool_diameter=float(p["diameter"][i]),
                tool_k_s=float(p["k_s"][i]),
            )
            limit_depths[i] = limit_depth
            stable_labels[i] = 1.0 if ASSUMED_DEPTH_MM < limit_depth else 0.0

        self.inputs = torch.from_numpy(inputs)
        self.limit_depths = torch.from_numpy(limit_depths)
        self.stable_labels = torch.from_numpy(stable_labels)

    def __len__(self) -> int:
        return self.num_samples

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.inputs[idx], self.stable_labels[idx], self.limit_depths[idx]


class ChatterTrainer:
    """LTC 颤振稳定性代理模型训练器。

    Args:
        epochs: 训练轮数
        batch_size: 批大小
        learning_rate: 学习率
        depth_weight: 极限切深 MSE 权重（与 BCE 平衡）
        device: 'cpu' 或 'cuda'
        save_dir: 检查点目录（None 时用 app/simulation/chatter/checkpoints）
        seed: 随机种子
    """

    def __init__(
        self,
        epochs: int = 150,
        batch_size: int = 256,
        learning_rate: float = 1e-3,
        depth_weight: float = 0.01,
        device: str = "cpu",
        save_dir: str | None = None,
        seed: int = 42,
    ) -> None:
        self.seed = seed
        torch.manual_seed(seed)
        np.random.seed(seed)

        from app.ai.lnn._research_bridge import (
            get_lnn_config_factory,
            get_ltc_model_factory,
        )

        LTCModel = get_ltc_model_factory()
        LNNConfig = get_lnn_config_factory()
        if LTCModel is None or LNNConfig is None:
            raise RuntimeError(
                "research LTC 模型不可用（需 torch + research/ 在路径上）。"
                "建议操作：在含 torch 的环境（如 lingjing-wm）执行本脚本。"
            )

        self.LTCModel = LTCModel
        self.config = LNNConfig(
            input_size=6,
            hidden_size=64,
            output_size=2,
            num_layers=2,
            dropout=0.0,
            time_constant=1.0,
        )
        self.device = torch.device(device)
        self.model = LTCModel(self.config).to(self.device)

        self.epochs = epochs
        self.batch_size = batch_size
        self.depth_weight = depth_weight
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)
        self.scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, mode="min", factor=0.5, patience=8)
        self.bce = torch.nn.BCEWithLogitsLoss()
        self.mse = torch.nn.MSELoss()

        if save_dir is None:
            save_dir = os.path.join(os.path.dirname(__file__), "checkpoints")
        self.save_dir = save_dir
        os.makedirs(self.save_dir, exist_ok=True)

        self.history: dict[str, list[float]] = {
            "train_loss": [],
            "val_loss": [],
            "depth_mae": [],
            "stability_acc": [],
        }
        self.best_metrics: dict[str, float] = {}

    def _forward(self, x: torch.Tensor) -> torch.Tensor:
        """无状态前向：每批显式给零初始隐藏态，避免跨批状态泄漏。"""
        hidden = self.model.init_hidden(x.shape[0])
        out, _ = self.model(x, hidden_state=hidden)
        return out

    def evaluate(self, loader: DataLoader) -> dict[str, float]:
        """在给定数据上评估损失、切深 MAE、相对误差与稳定性准确率。"""
        self.model.eval()
        total, n = 0.0, 0
        depth_abs_err: list[float] = []
        rel_err: list[float] = []
        correct, count = 0, 0
        us = []
        vs = []
        with torch.no_grad():
            for x, y_cls, y_depth in loader:
                x, y_cls, y_depth = (
                    x.to(self.device),
                    y_cls.to(self.device),
                    y_depth.to(self.device),
                )
                out = self._forward(x)
                loss = self.bce(out[:, 0], y_cls) + self.depth_weight * self.mse(out[:, 1], y_depth)
                total += float(loss) * x.shape[0]
                n += x.shape[0]

                pred_depth = out[:, 1].abs().cpu().numpy()
                true_depth = y_depth.cpu().numpy()
                depth_abs_err.extend(np.abs(pred_depth - true_depth).tolist())
                rel_err.extend((np.abs(pred_depth - true_depth) / np.maximum(true_depth, 1e-6)).tolist())
                pred_stable = (torch.sigmoid(out[:, 0]) > 0.5).cpu().numpy()
                correct += int((pred_stable == (y_cls.cpu().numpy() > 0.5)).sum())
                count += x.shape[0]
                us.extend(true_depth.tolist())
                vs.extend(pred_depth.tolist())

        us_arr, vs_arr = np.asarray(us), np.asarray(vs)
        ss_res = float(((us_arr - vs_arr) ** 2).sum())
        ss_tot = float(((us_arr - us_arr.mean()) ** 2).sum())
        return {
            "loss": total / max(n, 1),
            "depth_mae": float(np.mean(depth_abs_err)),
            "depth_rel_err_median": float(np.median(rel_err)),
            "depth_r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0,
            "stability_acc": correct / max(count, 1),
        }

    def train(
        self,
        train_dataset: Dataset | None = None,
        val_dataset: Dataset | None = None,
    ) -> dict[str, Any]:
        """执行训练并保存最佳检查点。"""
        if train_dataset is None:
            train_dataset = ChatterStabilityDataset(num_samples=12000)
        if val_dataset is None:
            val_dataset = ChatterStabilityDataset(num_samples=2000, seed=123)

        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=self.batch_size, shuffle=False)

        logger.info("训练样本数: %s", len(train_dataset))
        logger.info("验证样本数: %s", len(val_dataset))
        logger.info("训练轮数: %s，设备: %s", self.epochs, self.device)

        best_val = float("inf")
        for epoch in range(1, self.epochs + 1):
            self.model.train()
            losses: list[float] = []
            for x, y_cls, y_depth in train_loader:
                x, y_cls, y_depth = (
                    x.to(self.device),
                    y_cls.to(self.device),
                    y_depth.to(self.device),
                )
                self.optimizer.zero_grad()
                out = self._forward(x)
                loss = self.bce(out[:, 0], y_cls) + self.depth_weight * self.mse(out[:, 1], y_depth)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()
                losses.append(float(loss))

            metrics = self.evaluate(val_loader)
            self.history["train_loss"].append(float(np.mean(losses)))
            self.history["val_loss"].append(metrics["loss"])
            self.history["depth_mae"].append(metrics["depth_mae"])
            self.history["stability_acc"].append(metrics["stability_acc"])
            self.scheduler.step(metrics["loss"])

            if metrics["loss"] < best_val:
                best_val = metrics["loss"]
                self.best_metrics = dict(metrics, epoch=float(epoch))
                self._save_checkpoint("chatter_model.pt", metrics)

            if epoch % 10 == 0 or epoch == 1:
                logger.info(
                    "Epoch %3d/%d | train %.4f | val %.4f | depth MAE %.3f mm | rel-med %.2f%% | stab acc %.3f",
                    epoch,
                    self.epochs,
                    float(np.mean(losses)),
                    metrics["loss"],
                    metrics["depth_mae"],
                    metrics["depth_rel_err_median"] * 100,
                    metrics["stability_acc"],
                )

        logger.info("训练完成。最佳验证: %s", json.dumps(self.best_metrics, ensure_ascii=False))
        return {"history": self.history, "best": self.best_metrics}

    def _save_checkpoint(self, filename: str, metrics: dict[str, float]) -> None:
        """保存检查点（model_state_dict 供 predictor 直接加载）。"""
        path = os.path.join(self.save_dir, filename)
        torch.save(
            {
                "model_state_dict": self.model.state_dict(),
                "model_config": self.config.to_dict(),
                "normalization": {
                    "rpm": 10000.0,
                    "stiffness": 1e8,
                    "damping": 0.05,
                    "freq": 500.0,
                    "diameter": 20.0,
                    "k_s": 2000.0,
                },
                "label_provenance": {
                    "source": "app.simulation.chatter.stability.compute_stability_limit",
                    "note": "Tlusty 1-DOF 绝对包络，模态质量固定 50 kg；模型为解析法快速代理",
                    "assumed_depth_mm": ASSUMED_DEPTH_MM,
                    "param_ranges": PARAM_RANGES,
                },
                "metrics": metrics,
                "history": self.history,
            },
            path,
        )
        metrics_path = os.path.join(self.save_dir, "chatter_model_metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "best": self.best_metrics,
                    "history": self.history,
                    "label_provenance": "compute_stability_limit（解析包络，模态质量 50 kg）",
                },
                f,
                ensure_ascii=False,
                indent=2,
            )


def main() -> None:
    """命令行训练入口。"""
    parser = argparse.ArgumentParser(description="颤振稳定性 LTC 代理模型训练")
    parser.add_argument("--samples", type=int, default=12000, help="训练样本数")
    parser.add_argument("--val-samples", type=int, default=2000, help="验证样本数")
    parser.add_argument("--epochs", type=int, default=150, help="训练轮数")
    parser.add_argument("--batch-size", type=int, default=256, help="批大小")
    parser.add_argument("--lr", type=float, default=1e-3, help="学习率")
    parser.add_argument("--depth-weight", type=float, default=0.01, help="切深损失权重")
    parser.add_argument("--device", type=str, default="cpu", help="cpu 或 cuda")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--save-dir", type=str, default=None, help="检查点目录")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    start = time.time()
    logger.info("生成训练数据（标签调用解析包络，样本数 %d）…", args.samples)
    train_ds = ChatterStabilityDataset(num_samples=args.samples, seed=args.seed)
    val_ds = ChatterStabilityDataset(num_samples=args.val_samples, seed=args.seed + 1)
    logger.info("数据生成耗时: %.1fs", time.time() - start)

    trainer = ChatterTrainer(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        depth_weight=args.depth_weight,
        device=args.device,
        save_dir=args.save_dir,
        seed=args.seed,
    )
    result = trainer.train(train_ds, val_ds)
    logger.info("总耗时: %.1fs", time.time() - start)
    logger.info("最佳指标: %s", json.dumps(result["best"], ensure_ascii=False))


if __name__ == "__main__":
    main()
