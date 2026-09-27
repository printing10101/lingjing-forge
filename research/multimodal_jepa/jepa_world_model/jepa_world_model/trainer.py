"""JEPA World Model 训练器模块。

实现World Model的训练策略：
1. 数据来源：使用历史工艺数据进行监督训练
2. 损失函数：最小化预测状态变化与实际状态变化的嵌入向量差异
3. 奖励函数设计：综合考虑质量达标率(40%)、生产效率(35%)和工艺风险(25%)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

from app.ai.jepa_world_model.config import JEPAWorldModelConfig
from app.ai.jepa_world_model.predictor import JEPAPredictor
from app.ai.jepa_world_model.state import ManufacturingState
from app.ai.jepa_world_model.action import ManufacturingAction

logger = logging.getLogger(__name__)


class WorldModelTrainer:
    """JEPA World Model 训练器。

    负责World Model的监督训练，包括数据准备、损失计算、
    训练循环和模型评估。

    训练策略：
    - 监督学习：使用历史工艺数据 (state, action, next_state, reward)
    - 损失函数：余弦相似度损失 + 奖励预测MSE损失
    - 奖励权重：质量40% + 效率35% + 风险25%
    """

    def __init__(self, config: JEPAWorldModelConfig, model: JEPAPredictor):
        """初始化训练器。

        Args:
            config: 配置
            model: JEPA预测器模型
        """
        self.config = config
        self.model = model
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

        self.optimizer = optim.AdamW(
            self.model.parameters(),
            lr=config.initial_lr,
            weight_decay=config.weight_decay,
        )
        self.scheduler = optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer,
            T_max=config.epochs,
        )

        self.best_loss = float("inf")
        self.patience_counter = 0
        self.train_history: List[Dict] = []

    def _compute_total_loss(
        self,
        pred_output: Dict[str, torch.Tensor],
        target_state: torch.Tensor,
        target_rewards: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算总损失。

        损失组成：
        1. 状态预测损失（余弦相似度）：最小化预测嵌入与目标嵌入的差异
        2. 奖励预测损失（MSE）：加权MSE损失
           - 质量权重 40%
           - 效率权重 35%
           - 风险权重 25%

        Args:
            pred_output: 预测输出
            target_state: 目标状态嵌入
            target_rewards: 目标奖励值

        Returns:
            (total_loss, loss_dict)
        """
        pred_state = pred_output["next_state_embedding"]
        pred_rewards = pred_output["reward_estimates"]

        # 状态预测损失：余弦相似度
        cosine_sim = torch.nn.functional.cosine_similarity(
            pred_state, target_state, dim=-1,
        )
        state_loss = (1.0 - cosine_sim).mean()

        # 奖励预测损失：加权MSE
        weights = torch.tensor(
            [
                self.config.reward_quality_weight,
                self.config.reward_efficiency_weight,
                self.config.reward_risk_weight,
                0.0,  # 综合奖励在第4维，由前三者计算
            ],
            device=self.device,
        )

        reward_loss = (
            weights[:3] * (pred_rewards[:, :3] - target_rewards[:, :3]) ** 2
        ).sum(dim=-1).mean()

        total_loss = state_loss + 0.5 * reward_loss

        loss_dict = {
            "state_loss": float(state_loss.item()),
            "reward_loss": float(reward_loss.item()),
            "total_loss": float(total_loss.item()),
        }

        return total_loss, loss_dict

    def prepare_training_data(
        self,
        states: List[ManufacturingState],
        actions: List[ManufacturingAction],
        next_states: List[ManufacturingState],
        rewards: List[np.ndarray],
    ) -> DataLoader:
        """准备训练数据。

        Args:
            states: 状态列表
            actions: 动作列表
            next_states: 下一状态列表
            rewards: 奖励列表

        Returns:
            DataLoader
        """
        state_embeddings = np.stack([s.state_embedding for s in states])
        action_embeddings = np.stack([a.action_embedding for a in actions])
        next_state_embeddings = np.stack([ns.state_embedding for ns in next_states])
        reward_array = np.stack(rewards)

        dataset = TensorDataset(
            torch.from_numpy(state_embeddings).float(),
            torch.from_numpy(action_embeddings).float(),
            torch.from_numpy(next_state_embeddings).float(),
            torch.from_numpy(reward_array).float(),
        )

        return DataLoader(
            dataset,
            batch_size=self.config.batch_size,
            shuffle=True,
        )

    def train_epoch(
        self,
        dataloader: DataLoader,
    ) -> Dict[str, float]:
        """训练一个epoch。

        Args:
            dataloader: 训练数据加载器

        Returns:
            平均损失字典
        """
        self.model.train()
        epoch_losses = {"state_loss": 0.0, "reward_loss": 0.0, "total_loss": 0.0}
        num_batches = 0

        for batch in dataloader:
            state_emb, action_emb, target_state, target_rewards = [
                x.to(self.device) for x in batch
            ]

            self.optimizer.zero_grad()

            pred_output = self.model(state_emb, action_emb)
            total_loss, loss_dict = self._compute_total_loss(
                pred_output, target_state, target_rewards,
            )

            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
            self.optimizer.step()

            for k in epoch_losses:
                epoch_losses[k] += loss_dict[k]
            num_batches += 1

        for k in epoch_losses:
            epoch_losses[k] /= max(num_batches, 1)

        return epoch_losses

    @torch.no_grad()
    def validate(
        self,
        dataloader: DataLoader,
    ) -> Dict[str, float]:
        """验证模型。

        Args:
            dataloader: 验证数据加载器

        Returns:
            平均损失字典
        """
        self.model.eval()
        val_losses = {"state_loss": 0.0, "reward_loss": 0.0, "total_loss": 0.0}
        num_batches = 0

        for batch in dataloader:
            state_emb, action_emb, target_state, target_rewards = [
                x.to(self.device) for x in batch
            ]

            pred_output = self.model(state_emb, action_emb)
            _, loss_dict = self._compute_total_loss(
                pred_output, target_state, target_rewards,
            )

            for k in val_losses:
                val_losses[k] += loss_dict[k]
            num_batches += 1

        for k in val_losses:
            val_losses[k] /= max(num_batches, 1)

        return val_losses

    def train(
        self,
        train_loader: DataLoader,
        val_loader: Optional[DataLoader] = None,
        verbose: bool = True,
    ) -> List[Dict]:
        """执行完整训练流程。

        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            verbose: 是否打印训练进度

        Returns:
            训练历史记录
        """
        self.train_history = []

        for epoch in range(self.config.epochs):
            train_losses = self.train_epoch(train_loader)

            if val_loader is not None:
                val_losses = self.validate(val_loader)
            else:
                val_losses = train_losses

            self.scheduler.step()

            history_entry = {
                "epoch": epoch + 1,
                "train_losses": train_losses,
                "val_losses": val_losses,
                "lr": self.optimizer.param_groups[0]["lr"],
            }
            self.train_history.append(history_entry)

            if verbose and (epoch + 1) % 10 == 0:
                logger.info(
                    f"Epoch {epoch + 1}/{self.config.epochs} | "
                    f"Train Loss: {train_losses['total_loss']:.4f} | "
                    f"Val Loss: {val_losses['total_loss']:.4f} | "
                    f"State Loss: {train_losses['state_loss']:.4f}"
                )

            # 早停检查
            current_val_loss = val_losses["total_loss"]
            if current_val_loss < self.best_loss - 1e-5:
                self.best_loss = current_val_loss
                self.patience_counter = 0
            else:
                self.patience_counter += 1
                if self.patience_counter >= self.config.early_stopping_patience:
                    if verbose:
                        logger.info(f"Early stopping at epoch {epoch + 1}")
                    break

        return self.train_history

    def generate_synthetic_training_data(
        self,
        num_samples: int = 1000,
    ) -> Tuple[List[ManufacturingState], List[ManufacturingAction],
               List[ManufacturingState], List[np.ndarray]]:
        """生成合成训练数据（用于初始训练或测试）。

        基于物理启发式规则生成合理的制造状态-动作-结果数据。

        Args:
            num_samples: 样本数量

        Returns:
            (states, actions, next_states, rewards)
        """
        materials = ["45#钢", "铝合金6061", "不锈钢304", "钛合金TC4", "铸铁HT200"]
        op_types = list(self.config.operation_types)
        tool_ids = [f"T{i:02d}" for i in range(1, 21)]

        states = []
        actions = []
        next_states = []
        rewards = []

        for _ in range(num_samples):
            # 随机生成初始状态
            material = np.random.choice(materials)
            geometry = np.random.randn(512).astype(np.float32)
            geometry = geometry / (np.linalg.norm(geometry) + 1e-10)

            state = ManufacturingState(
                geometry=geometry,
                material=material,
                precision=np.random.uniform(0.005, 0.1),
                tool_wear=np.random.uniform(0.0, 0.3),
                spindle_temp=np.random.uniform(20, 45),
                vibration=np.random.uniform(0.5, 3.0),
                current_operation=np.random.randint(0, 10),
                completed_operations=list(range(np.random.randint(0, 5))),
            )

            # 随机生成动作
            op_type = np.random.choice(op_types)
            tool_id = np.random.choice(tool_ids)
            action = ManufacturingAction(
                operation_type=op_type,
                tool_id=tool_id,
                parameters={
                    "spindle_speed": int(np.random.uniform(1000, 20000)),
                    "feed_rate": float(np.random.uniform(50, 1500)),
                    "depth_of_cut": float(np.random.uniform(0.5, 10.0)),
                    "coolant": np.random.choice([True, False]),
                },
            )

            # 模拟下一状态（基于物理启发式规则）
            next_geometry = geometry + np.random.normal(0, 0.02, 512).astype(np.float32)
            next_geometry = next_geometry / (np.linalg.norm(next_geometry) + 1e-10)

            # 工具磨损增量
            wear_increment = (
                0.002 * (action.parameters["spindle_speed"] / 8000)
                * (action.parameters["feed_rate"] / 500)
                * (action.parameters["depth_of_cut"] / 2.0)
            )
            if action.parameters["coolant"]:
                wear_increment *= 0.7

            next_state = ManufacturingState(
                geometry=next_geometry,
                material=material,
                precision=state.precision + np.random.normal(0, 0.001),
                tool_wear=np.clip(state.tool_wear + wear_increment, 0.0, 1.0),
                spindle_temp=state.spindle_temp
                + np.random.uniform(0.5, 5.0)
                * (action.parameters["spindle_speed"] / 10000),
                vibration=state.vibration + np.random.normal(0, 0.1),
                current_operation=state.current_operation + 1,
                completed_operations=state.completed_operations
                + [state.current_operation],
            )

            # 计算奖励
            quality = np.clip(1.0 - abs(next_state.precision) * 10, 0.0, 1.0)
            efficiency = np.clip(
                action.parameters["feed_rate"] / 1500.0
                * action.parameters["depth_of_cut"] / 10.0,
                0.0, 1.0,
            )
            risk = np.clip(
                wear_increment * 50
                + (next_state.spindle_temp - 20) / 100
                + next_state.vibration / 10,
                0.0, 1.0,
            )
            combined = (
                0.40 * quality + 0.35 * efficiency + 0.25 * (1.0 - risk)
            )

            states.append(state)
            actions.append(action)
            next_states.append(next_state)
            rewards.append(np.array([quality, efficiency, risk, combined], dtype=np.float32))

        return states, actions, next_states, rewards

    def train_on_synthetic_data(
        self,
        num_samples: int = 1000,
        epochs: Optional[int] = None,
        verbose: bool = True,
    ) -> List[Dict]:
        """在合成数据上训练模型。

        Args:
            num_samples: 合成样本数
            epochs: 训练轮次（默认使用配置值）
            verbose: 是否打印进度

        Returns:
            训练历史
        """
        if epochs is not None:
            self.config.epochs = epochs

        states, actions, next_states, rewards = self.generate_synthetic_training_data(
            num_samples,
        )

        # 划分训练/验证集
        split = int(0.8 * num_samples)
        train_states = states[:split]
        train_actions = actions[:split]
        train_next_states = next_states[:split]
        train_rewards = rewards[:split]

        val_states = states[split:]
        val_actions = actions[split:]
        val_next_states = next_states[split:]
        val_rewards = rewards[split:]

        train_loader = self.prepare_training_data(
            train_states, train_actions, train_next_states, train_rewards,
        )
        val_loader = self.prepare_training_data(
            val_states, val_actions, val_next_states, val_rewards,
        )

        return self.train(train_loader, val_loader, verbose=verbose)

    @torch.no_grad()
    def evaluate_prediction_accuracy(
        self,
        states: List[ManufacturingState],
        actions: List[ManufacturingAction],
        next_states: List[ManufacturingState],
    ) -> Dict[str, float]:
        """评估预测准确性。

        Args:
            states: 初始状态列表
            actions: 动作列表
            next_states: 实际下一状态列表

        Returns:
            评估指标字典
        """
        self.model.eval()
        cosine_similarities = []

        for state, action, next_state in zip(states, actions, next_states):
            result = self.model.predict_step(
                state.state_embedding, action.action_embedding,
            )
            pred = result["next_state_embedding"]
            target = next_state.state_embedding

            cos_sim = np.dot(pred, target) / (
                np.linalg.norm(pred) * np.linalg.norm(target) + 1e-10
            )
            cosine_similarities.append(float(cos_sim))

        cosine_similarities = np.array(cosine_similarities)

        return {
            "mean_cosine_similarity": float(np.mean(cosine_similarities)),
            "std_cosine_similarity": float(np.std(cosine_similarities)),
            "min_cosine_similarity": float(np.min(cosine_similarities)),
            "max_cosine_similarity": float(np.max(cosine_similarities)),
            "pass_rate_0.85": float(
                np.mean(cosine_similarities > 0.85)
            ),
        }
