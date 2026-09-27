"""V-JEPA复合损失函数模块。

实现：L_total = L_prediction + λ * Contrastive(正常嵌入, 异常嵌入)

其中：
- L_prediction: 时空嵌入预测损失（MSE）
- Contrastive: 三元组对比损失使正常/异常嵌入分离
- λ = 0.3

Key components:
    - PredictionLoss: 时空嵌入预测MSE损失
    - TripletContrastiveLoss: 三元组对比损失
    - VJEPALosses: 复合损失
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, Tuple


class PredictionLoss(nn.Module):
    """时空嵌入预测损失（MSE）。

    计算预测嵌入与目标（EMA编码器）嵌入之间的均方误差。
    """

    def __init__(self):
        super().__init__()
        self.mse = nn.MSELoss()

    def forward(
        self,
        predicted_embeddings: torch.Tensor,
        target_embeddings: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算预测损失。

        Args:
            predicted_embeddings: (B, N_target, D)
            target_embeddings: (B, N_target, D)

        Returns:
            loss: MSE损失
            loss_dict: 损失值
        """
        loss = self.mse(predicted_embeddings, target_embeddings)
        return loss, {"prediction_mse": loss.item()}


class TripletContrastiveLoss(nn.Module):
    """三元组对比损失。

    使正常嵌入聚集、异常嵌入与正常嵌入分离。
    loss = max(0, d(anchor, positive) - d(anchor, negative) + margin)

    Attributes:
        margin: 边距值
    """

    def __init__(self, margin: float = 0.5):
        super().__init__()
        self.margin = margin

    def forward(
        self,
        normal_embeddings: torch.Tensor,
        anomaly_embeddings: torch.Tensor,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算三元组对比损失。

        Args:
            normal_embeddings: (B_n, D) 正常样本嵌入
            anomaly_embeddings: (B_a, D) 异常样本嵌入

        Returns:
            loss: 对比损失
            loss_dict: 损失值
        """
        if normal_embeddings.shape[0] == 0 or anomaly_embeddings.shape[0] == 0:
            return torch.tensor(0.0), {"triplet_loss": 0.0}

        # 正常嵌入作为anchor和positive
        B_n = normal_embeddings.shape[0]
        if B_n >= 2:
            n1 = normal_embeddings[:B_n // 2]
            n2 = normal_embeddings[B_n // 2:2 * (B_n // 2)]
            d_pos = torch.norm(n1 - n2, dim=-1).mean()
        else:
            d_pos = torch.tensor(0.0)

        # 异常嵌入作为negative
        B_a = anomaly_embeddings.shape[0]
        n_match = min(B_n, B_a)
        if n_match > 0:
            anchors = normal_embeddings[:n_match]
            negatives = anomaly_embeddings[:n_match]
            d_neg = torch.norm(anchors - negatives, dim=-1).mean()
        else:
            return torch.tensor(0.0), {"triplet_loss": 0.0}

        loss = F.relu(d_pos - d_neg + self.margin)

        return loss, {
            "triplet_loss": loss.item(),
            "positive_distance": d_pos.item() if isinstance(d_pos, torch.Tensor) else d_pos,
            "negative_distance": d_neg.item() if isinstance(d_neg, torch.Tensor) else d_neg,
        }


class VJEPALosses(nn.Module):
    """V-JEPA加工异常检测复合损失函数。

    L_total = L_prediction + λ * Contrastive(正常嵌入, 异常嵌入)

    其中 λ = 0.3
    """

    def __init__(self, lambda_triplet: float = 0.30, triplet_margin: float = 0.5):
        super().__init__()
        self.lambda_triplet = lambda_triplet

        self.prediction_loss = PredictionLoss()
        self.triplet_loss = TripletContrastiveLoss(margin=triplet_margin)

    def forward(
        self,
        predicted_embeddings: torch.Tensor,
        target_embeddings: torch.Tensor,
        normal_embeddings: torch.Tensor = None,
        anomaly_embeddings: torch.Tensor = None,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算总损失。

        Args:
            predicted_embeddings: (B, N_target, D)
            target_embeddings: (B, N_target, D)
            normal_embeddings: (B_n, D) 正常样本嵌入
            anomaly_embeddings: (B_a, D) 异常样本嵌入

        Returns:
            total_loss: 总损失
            loss_dict: 详细损失字典
        """
        pred_loss, pred_dict = self.prediction_loss(predicted_embeddings, target_embeddings)

        total = pred_loss.clone()

        if normal_embeddings is not None and anomaly_embeddings is not None:
            trip_loss, trip_dict = self.triplet_loss(normal_embeddings, anomaly_embeddings)
            total = total + self.lambda_triplet * trip_loss
            loss_dict = {
                "total_loss": total.item(),
                **pred_dict,
                **trip_dict,
            }
        else:
            loss_dict = {
                "total_loss": total.item(),
                **pred_dict,
            }

        return total, loss_dict


class AnomalyClassificationLoss(nn.Module):
    """异常分类监督损失。

    训练异常检测头时使用的监督信号：
    - 二分类交叉熵（正常 vs 异常）
    - 多分类交叉熵（异常类型）
    - 严重程度交叉熵
    """

    def __init__(self):
        super().__init__()
        self.bce = nn.BCEWithLogitsLoss()
        self.ce = nn.CrossEntropyLoss()

    def forward(
        self,
        anomaly_logits: torch.Tensor,
        anomaly_labels: torch.Tensor,
        type_logits: torch.Tensor = None,
        type_labels: torch.Tensor = None,
        severity_logits: torch.Tensor = None,
        severity_labels: torch.Tensor = None,
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """计算异常分类损失。

        Args:
            anomaly_logits: (B,) 异常二分类logits
            anomaly_labels: (B,) 0=正常, 1=异常
            type_logits: (B, 4) 异常类型logits
            type_labels: (B,) 异常类型标签
            severity_logits: (B, 5) 严重程度logits
            severity_labels: (B,) 严重程度标签

        Returns:
            total_loss: 总分类损失
            loss_dict: 详细损失
        """
        binary_loss = self.bce(anomaly_logits, anomaly_labels.float())
        total = binary_loss
        loss_dict = {"binary_loss": binary_loss.item()}

        if type_logits is not None and type_labels is not None:
            type_loss = self.ce(type_logits, type_labels)
            total = total + type_loss
            loss_dict["type_loss"] = type_loss.item()

        if severity_logits is not None and severity_labels is not None:
            severity_loss = self.ce(severity_logits, severity_labels)
            total = total + severity_loss
            loss_dict["severity_loss"] = severity_loss.item()

        loss_dict["classification_total"] = total.item()
        return total, loss_dict
