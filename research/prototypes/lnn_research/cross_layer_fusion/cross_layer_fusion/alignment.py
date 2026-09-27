"""
对齐训练模块 (Alignment Loss)

实现对比学习对齐损失函数，用于对齐不同层的嵌入空间。
支持:
    - 余弦相似度正样本计算
    - 批次内负样本采样策略
    - 可调节温度参数的对比损失
    - 训练过程中损失监控与收敛性检验
"""

from __future__ import annotations

from typing import List, Optional

import torch
import torch.nn.functional as F


def alignment_loss(
    embed1: torch.Tensor,
    embed2: torch.Tensor,
    temperature: float = 0.07,
    neg_sample_strategy: str = "in_batch",
    neg_samples: Optional[torch.Tensor] = None,
    reduction: str = "mean",
) -> torch.Tensor:
    """对比学习对齐损失函数。

    计算两个嵌入空间的对齐损失，使用InfoNCE-style对比学习框架。
    正样本为对应的(embed1[i], embed2[i])对，负样本根据策略选择。

    损失公式:
        L = -1/N * Σ_i log[ exp(cos(e1_i, e2_i) / τ) / Σ_j exp(cos(e1_i, e2_j) / τ) ]

    其中:
        - cos(e1_i, e2_i): 正样本余弦相似度
        - cos(e1_i, e2_j), j≠i: 负样本余弦相似度
        - τ: temperature温度参数

    Args:
        embed1: 第一个嵌入空间向量 (N, D1)，需已归一化。
        embed2: 第二个嵌入空间向量 (N, D2)，需已归一化。
        temperature: 温度参数τ，控制分布的锐度。越小越关注难负样本。默认0.07。
        neg_sample_strategy: 负样本选择策略:
            - "in_batch": 使用批次内其他样本作为负样本（默认）
            - "random": 使用neg_samples参数提供的随机负样本
            - "hard": 使用批次内相似度最高的样本作为难负样本
        neg_samples: 显式提供的负样本 (M, D2)，仅在strategy="random"时使用。
        reduction: 损失聚合方式，'mean'或'sum'。默认'mean'。

    Returns:
        对齐损失标量。

    Raises:
        ValueError: 当neg_sample_strategy无效或维度不匹配时抛出。
        RuntimeError: 当批次大小不足以进行对比学习时抛出。

    Example:
        >>> e1 = F.normalize(torch.randn(32, 256), dim=-1)
        >>> e2 = F.normalize(torch.randn(32, 256), dim=-1)
        >>> loss = alignment_loss(e1, e2, temperature=0.07)
        >>> loss.item()
        2.0...
    """
    # 输入验证
    if embed1.dim() != 2 or embed2.dim() != 2:
        raise ValueError(
            f"嵌入向量必须是2维张量 (batch_size, dim)，实际维度: embed1={embed1.dim()}, embed2={embed2.dim()}"
        )

    if embed1.size(0) != embed2.size(0):
        raise ValueError(f"两个嵌入向量的批次大小必须相同，实际: embed1={embed1.size(0)}, embed2={embed2.size(0)}")

    batch_size = embed1.size(0)
    if batch_size < 2 and neg_sample_strategy == "in_batch":
        raise RuntimeError(
            f"批次内负样本策略需要 batch_size >= 2，当前 batch_size={batch_size}。请增大批次大小或使用 'random' 策略。"
        )

    # 确保嵌入向量已归一化
    embed1_norm = F.normalize(embed1, p=2, dim=-1)
    embed2_norm = F.normalize(embed2, p=2, dim=-1)

    # 计算正样本余弦相似度: 对角元素
    pos_sim = F.cosine_similarity(embed1_norm, embed2_norm, dim=-1)  # (N,)
    pos_sim = pos_sim / temperature

    # 计算负样本相似度
    if neg_sample_strategy == "in_batch":
        # 批次内所有配对: sim[i][j] = cos(e1_i, e2_j)
        neg_sim = _compute_in_batch_negatives(embed1_norm, embed2_norm, temperature)
    elif neg_sample_strategy == "random":
        if neg_samples is None:
            raise ValueError("strategy='random' 时必须提供 neg_samples 参数。")
        neg_sim = _compute_random_negatives(
            embed1_norm,
            embed2_norm,
            neg_samples,
            temperature,
        )
    elif neg_sample_strategy == "hard":
        neg_sim = _compute_hard_negatives(embed1_norm, embed2_norm, temperature)
    else:
        raise ValueError(f"无效的负样本策略: '{neg_sample_strategy}'。可选: 'in_batch', 'random', 'hard'。")

    # 计算InfoNCE损失
    logits = neg_sim  # (N, N) for in_batch, (N, N+M) for random
    # 将对角位置替换为正样本相似度
    if neg_sample_strategy == "in_batch":
        logits = logits.clone()
        logits[torch.arange(batch_size), torch.arange(batch_size)] = pos_sim
        # 对于in_batch: 正样本在对角线上，标签为 [0, 1, ..., N-1]
        labels = torch.arange(batch_size, dtype=torch.long, device=embed1.device)
    elif neg_sample_strategy == "hard":
        # 难负样本策略: 拼接正负样本相似度，正样本在位置0
        pos_expanded = pos_sim.unsqueeze(-1)  # (N, 1)
        logits = torch.cat([pos_expanded, neg_sim], dim=-1)  # (N, 1 + n_hard)
        labels = torch.zeros(batch_size, dtype=torch.long, device=embed1.device)
    else:
        # random策略: 拼接正负样本
        pos_expanded = pos_sim.unsqueeze(-1)  # (N, 1)
        logits = torch.cat([pos_expanded, neg_sim], dim=-1)  # (N, 1 + M)
        labels = torch.zeros(batch_size, dtype=torch.long, device=embed1.device)

    loss = F.cross_entropy(logits, labels, reduction=reduction)

    return loss


def _compute_in_batch_negatives(
    embed1_norm: torch.Tensor,
    embed2_norm: torch.Tensor,
    temperature: float,
) -> torch.Tensor:
    """计算批次内负样本相似度矩阵。

    使用批次内所有其他样本作为负样本:
        neg_sim[i][j] = cos(e1_i, e2_j) / τ

    Args:
        embed1_norm: 归一化嵌入1 (N, D)。
        embed2_norm: 归一化嵌入2 (N, D)。
        temperature: 温度参数。

    Returns:
        负样本相似度矩阵 (N, N)。
    """
    # sim[i][j] = cos(e1_i, e2_j)
    sim = torch.matmul(embed1_norm, embed2_norm.T)  # (N, N)
    return sim / temperature


def _compute_random_negatives(
    embed1_norm: torch.Tensor,
    embed2_norm: torch.Tensor,
    neg_samples: torch.Tensor,
    temperature: float,
) -> torch.Tensor:
    """计算随机负样本相似度。

    Args:
        embed1_norm: 归一化嵌入1 (N, D)。
        embed2_norm: 归一化嵌入2 (N, D)。
        neg_samples: 随机负样本 (M, D)。
        temperature: 温度参数。

    Returns:
        负样本相似度矩阵 (N, M)。
    """
    neg_norm = F.normalize(neg_samples, p=2, dim=-1)
    neg_sim = torch.matmul(embed1_norm, neg_norm.T)  # (N, M)
    return neg_sim / temperature


def _compute_hard_negatives(
    embed1_norm: torch.Tensor,
    embed2_norm: torch.Tensor,
    temperature: float,
    n_hard: int = 5,
) -> torch.Tensor:
    """计算难负样本相似度。

    选择批次内相似度最高的前K个非对应样本作为难负样本。

    Args:
        embed1_norm: 归一化嵌入1 (N, D)。
        embed2_norm: 归一化嵌入2 (N, D)。
        temperature: 温度参数。
        n_hard: 选择的难负样本数量。默认5。

    Returns:
        难负样本相似度矩阵 (N, n_hard)。
    """
    batch_size = embed1_norm.size(0)
    sim = torch.matmul(embed1_norm, embed2_norm.T)  # (N, N)

    # 将正样本位置设为极小值，确保不被选为难负样本
    sim_masked = sim.clone()
    sim_masked[torch.arange(batch_size), torch.arange(batch_size)] = float("-inf")

    # 选择相似度最高的K个负样本
    n_select = min(n_hard, batch_size - 1)
    hard_sim, _ = torch.topk(sim_masked, k=n_select, dim=-1)  # (N, n_select)

    return hard_sim / temperature


# 对齐损失追踪器


class AlignmentLossTracker:
    """对齐损失追踪器。

    用于监控训练过程中的对齐损失变化，
    支持收敛性检验和稳定性评估。

    Attributes:
        history: 损失历史记录列表 (List[float])。
        convergence_threshold: 收敛阈值。
        stability_window: 稳定性检查窗口大小。
        stability_threshold: 稳定性阈值（波动范围）。
    """

    def __init__(
        self,
        convergence_threshold: float = 0.1,
        stability_window: int = 10,
        stability_threshold: float = 0.01,
    ):
        """初始化损失追踪器。

        Args:
            convergence_threshold: 收敛阈值，损失需低于此值才算收敛。默认0.1。
            stability_window: 稳定性检查窗口大小（epoch数）。默认10。
            stability_threshold: 稳定性阈值，连续窗口内波动需小于此值。默认0.01。
        """
        self.convergence_threshold = convergence_threshold
        self.stability_window = stability_window
        self.stability_threshold = stability_threshold
        self.history: List[float] = []
        self.best_loss: float = float("inf")
        self.best_epoch: int = -1

    def record(self, loss: float, epoch: Optional[int] = None) -> None:
        """记录一个epoch的损失值。

        Args:
            loss: 当前epoch的对齐损失值。
            epoch: 当前epoch编号，可选。
        """
        self.history.append(loss)

        if epoch is not None and loss < self.best_loss:
            self.best_loss = loss
            self.best_epoch = epoch

    def is_converged(self) -> bool:
        """检查损失是否已收敛。

        收敛条件:
        1. 损失值低于收敛阈值 (< 0.1)
        2. 连续stability_window个epoch的损失波动小于stability_threshold (< 0.01)

        Returns:
            是否已收敛。
        """
        if len(self.history) < self.stability_window:
            return False

        # 检查条件1: 当前损失低于阈值
        recent = self.history[-self.stability_window :]
        if max(recent) >= self.convergence_threshold:
            return False

        # 检查条件2: 连续窗口内波动足够小
        fluctuation = max(recent) - min(recent)
        return fluctuation < self.stability_threshold

    def get_stability_metric(self) -> float:
        """获取稳定性指标。

        Returns:
            最近stability_window个epoch的损失标准差。
            如果历史不够，返回-1。
        """
        if len(self.history) < 3:
            return -1.0

        window = min(self.stability_window, len(self.history))
        recent = self.history[-window:]
        return float(torch.tensor(recent).std().item())

    def get_trend(self) -> str:
        """判断损失趋势。

        Returns:
            "decreasing": 下降趋势
            "stable": 稳定
            "increasing": 上升趋势
            "insufficient_data": 数据不足
        """
        if len(self.history) < 5:
            return "insufficient_data"

        # 比较最近5个epoch和更早5个epoch的均值
        recent = self.history[-5:]
        earlier = self.history[-10:-5] if len(self.history) >= 10 else self.history[:-5]

        if not earlier:
            return "insufficient_data"

        recent_mean = sum(recent) / len(recent)
        earlier_mean = sum(earlier) / len(earlier)

        relative_change = (recent_mean - earlier_mean) / (abs(earlier_mean) + 1e-10)

        if relative_change < -0.01:
            return "decreasing"
        elif relative_change > 0.01:
            return "increasing"
        else:
            return "stable"

    def get_summary(self) -> dict:
        """获取训练摘要。

        Returns:
            包含损失历史关键指标的字典。
        """
        if not self.history:
            return {"status": "no_data", "total_epochs": 0}

        return {
            "total_epochs": len(self.history),
            "final_loss": self.history[-1],
            "best_loss": self.best_loss,
            "best_epoch": self.best_epoch,
            "converged": self.is_converged(),
            "stability": self.get_stability_metric(),
            "trend": self.get_trend(),
            "convergence_threshold": self.convergence_threshold,
            "stability_threshold": self.stability_threshold,
        }

    def reset(self) -> None:
        """重置追踪器状态。"""
        self.history.clear()
        self.best_loss = float("inf")
        self.best_epoch = -1

    def __len__(self) -> int:
        return len(self.history)

    def __repr__(self) -> str:
        if not self.history:
            return "AlignmentLossTracker(empty)"
        return (
            f"AlignmentLossTracker(epochs={len(self.history)}, "
            f"final_loss={self.history[-1]:.4f}, "
            f"converged={self.is_converged()})"
        )
