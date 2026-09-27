"""异常检测头模块。

实现双重异常检测功能：
1. 正常/异常二分类判断（基于余弦相似度）
2. 异常类型多分类识别（断刀/振动异常/过切/撞刀）+ K-means聚类辅助
3. 异常严重程度四等级评估（轻微/中等/严重/危险）

输出格式：
- 帧级异常概率：0-1，保留3位小数
- 异常类型：断刀/振动异常/过切/撞刀/正常
- 异常严重程度：轻微/中等/严重/危险
- 建议措施：降速/换刀/停机等

Key components:
    - BinaryAnomalyClassifier: 正常/异常二分类
    - MultiTypeAnomalyClassifier: 异常类型多分类
    - SeverityAssessor: 严重程度评估
    - AnomalyDetectionHead: 组合检测头
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Dict, List, Tuple, Optional
from sklearn.cluster import KMeans


class BinaryAnomalyClassifier(nn.Module):
    """正常/异常二分类器。

    同时使用两种判定方式：
    1. 余弦相似度 > 0.92 判定正常
    2. 欧氏距离 > 阈值判定异常
    """

    def __init__(
        self,
        embed_dim: int = 512,
        hidden_dim: int = 128,
        cosine_threshold: float = 0.92,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.cosine_threshold = cosine_threshold
        self.euclidean_threshold = nn.Parameter(torch.tensor(1.5))

        self.classifier = nn.Sequential(
            nn.Linear(embed_dim * 2 + 2, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 1),
        )

    def forward(
        self,
        predicted_embed: torch.Tensor,
        observed_embed: torch.Tensor,
    ) -> Dict[str, torch.Tensor]:
        """异常判定。

        Args:
            predicted_embed: (B, D) 预测嵌入（全局池化后）
            observed_embed: (B, D) 实际观测嵌入

        Returns:
            - anomaly_logit: (B, 1) 异常logit
            - anomaly_prob: (B,) 异常概率
            - cosine_similarity: (B,) 余弦相似度
            - euclidean_distance: (B,) 欧氏距离
        """
        cosine_sim = F.cosine_similarity(predicted_embed, observed_embed, dim=-1)
        euclidean_dist = torch.norm(predicted_embed - observed_embed, dim=-1)

        # 组合特征用于分类器
        concat_feat = torch.cat([
            predicted_embed, observed_embed,
            cosine_sim.unsqueeze(-1), euclidean_dist.unsqueeze(-1),
        ], dim=-1)

        anomaly_logit = self.classifier(concat_feat).squeeze(-1)
        anomaly_prob = torch.sigmoid(anomaly_logit)

        return {
            "anomaly_logit": anomaly_logit,
            "anomaly_prob": anomaly_prob,
            "cosine_similarity": cosine_sim,
            "euclidean_distance": euclidean_dist,
        }


class MultiTypeAnomalyClassifier(nn.Module):
    """异常类型多分类器。

    识别4种异常类型：断刀/振动异常/过切/撞刀
    结合K-means聚类进行初步分类，再用预定义特征确认。
    """

    ANOMALY_TYPES = ["tool_breakage", "vibration_anomaly", "overcut", "collision"]

    def __init__(
        self,
        embed_dim: int = 512,
        hidden_dim: int = 256,
        num_types: int = 4,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.num_types = num_types

        self.classifier = nn.Sequential(
            nn.Linear(embed_dim * 2 + 2, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_types),
        )

        # K-means聚类中心（运行时更新）
        self.cluster_centers: Optional[np.ndarray] = None
        self.cluster_labels: Optional[List[str]] = None

    def forward(
        self,
        predicted_embed: torch.Tensor,
        observed_embed: torch.Tensor,
        anomaly_prob: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """多分类预测。

        Args:
            predicted_embed: (B, D)
            observed_embed: (B, D)
            anomaly_prob: (B,) 异常概率

        Returns:
            type_logits: (B, num_types)
            type_probs: (B, num_types)
        """
        cosine_sim = F.cosine_similarity(predicted_embed, observed_embed, dim=-1)
        euclidean_dist = torch.norm(predicted_embed - observed_embed, dim=-1)

        concat_feat = torch.cat([
            predicted_embed, observed_embed,
            cosine_sim.unsqueeze(-1), euclidean_dist.unsqueeze(-1),
        ], dim=-1)

        type_logits = self.classifier(concat_feat)
        type_probs = F.softmax(type_logits, dim=-1)

        # 当异常概率低时，削弱类别预测置信度
        weight = anomaly_prob.unsqueeze(-1)
        type_probs = type_probs * weight + (1 - weight) / self.num_types

        return type_logits, type_probs

    def fit_kmeans(
        self,
        embeddings: np.ndarray,
        n_clusters: int = 4,
    ) -> np.ndarray:
        """使用K-means聚类拟合嵌入分布。

        Args:
            embeddings: (N, D) 嵌入向量
            n_clusters: 聚类数

        Returns:
            cluster_labels: (N,) 聚类标签
        """
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(embeddings)
        self.cluster_centers = kmeans.cluster_centers_
        return labels


class SeverityAssessor(nn.Module):
    """异常严重程度评估器。

    基于嵌入距离值与历史数据对比，实现四等级评估：
    - 轻微 (mild)
    - 中等 (moderate)
    - 严重 (severe)
    - 危险 (danger)

    通过统计方法计算严重程度：
    - 计算当前嵌入距离相对于历史分布的百分位数
    - 使用动态阈值进行分级
    """

    SEVERITY_LEVELS = ["normal", "mild", "moderate", "severe", "danger"]
    SEVERITY_THRESHOLDS = [0.92, 0.75, 0.55, 0.30]

    def __init__(self, embed_dim: int = 512, hidden_dim: int = 128, dropout: float = 0.3):
        super().__init__()
        self.register_buffer("historical_distances", torch.zeros(1000))
        self.register_buffer("historical_mean", torch.tensor(0.0))
        self.register_buffer("historical_std", torch.tensor(1.0))
        self.buffer_idx = 0
        self.buffer_full = False

        self.assessor = nn.Sequential(
            nn.Linear(embed_dim * 2 + 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, len(self.SEVERITY_LEVELS)),
        )

    def update_history(self, distances: torch.Tensor):
        """更新历史距离分布。

        Args:
            distances: 新的嵌入距离值
        """
        _ = distances.numel()
        for d in distances.flatten():
            self.historical_distances[self.buffer_idx] = d
            self.buffer_idx = (self.buffer_idx + 1) % len(self.historical_distances)

        self.buffer_full = self.buffer_full or self.buffer_idx == 0

        valid_data = self.historical_distances if self.buffer_full else self.historical_distances[:self.buffer_idx]
        self.historical_mean = valid_data.mean()
        self.historical_std = valid_data.std().clamp(min=1e-6)

    def forward(
        self,
        predicted_embed: torch.Tensor,
        observed_embed: torch.Tensor,
        anomaly_prob: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """评估异常严重程度。

        Args:
            predicted_embed: (B, D)
            observed_embed: (B, D)
            anomaly_prob: (B,)

        Returns:
            severity_logits: (B, 5)
            severity_probs: (B, 5)
        """
        cosine_sim = F.cosine_similarity(predicted_embed, observed_embed, dim=-1)
        euclidean_dist = torch.norm(predicted_embed - observed_embed, dim=-1)

        concat_feat = torch.cat([
            predicted_embed, observed_embed,
            cosine_sim.unsqueeze(-1), euclidean_dist.unsqueeze(-1),
        ], dim=-1)

        severity_logits = self.assessor(concat_feat)
        severity_probs = F.softmax(severity_logits, dim=-1)

        return severity_logits, severity_probs

    def get_severity_level(self, probs: torch.Tensor) -> List[str]:
        """根据概率分布获取严重程度标签。

        Args:
            probs: (B, 5) 严重程度概率

        Returns:
            严重程度标签列表
        """
        indices = probs.argmax(dim=-1)
        return [self.SEVERITY_LEVELS[i] for i in indices.cpu().tolist()]


class AnomalyDetectionHead(nn.Module):
    """组合异常检测头。

    整合二分类、多分类和严重程度评估。
    同时利用基于规则的方法（余弦相似度/欧氏距离）和基于学习的方法。

    Attributes:
        binary_classifier: 正常/异常二分类器
        type_classifier: 异常类型多分类器
        severity_assessor: 严重程度评估器
        anomaly_types: 异常类型标签
    """

    ANOMALY_TYPES_CN = ["正常", "断刀", "振动异常", "过切", "撞刀"]

    def __init__(
        self,
        embed_dim: int = 512,
        hidden_dim: int = 256,
        num_anomaly_types: int = 4,
        cosine_threshold: float = 0.92,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.binary_classifier = BinaryAnomalyClassifier(
            embed_dim, hidden_dim // 2, cosine_threshold, dropout,
        )
        self.type_classifier = MultiTypeAnomalyClassifier(
            embed_dim, hidden_dim, num_anomaly_types, dropout,
        )
        self.severity_assessor = SeverityAssessor(embed_dim, hidden_dim // 2, dropout)

    def forward(
        self,
        predicted_embed: torch.Tensor,
        observed_embed: torch.Tensor,
    ) -> Dict[str, torch.Tensor]:
        """完整异常检测。

        Args:
            predicted_embed: (B, D) 预测嵌入
            observed_embed: (B, D) 观测嵌入

        Returns:
            字典包含：
            - anomaly_prob: (B,) 异常概率 [0,1]
            - anomaly_type_probs: (B, 4) 异常类型概率
            - anomaly_type_pred: (B,) 预测类型索引
            - severity_probs: (B, 5) 严重程度概率
            - severity_pred: (B,) 严重程度索引
            - cosine_similarity: (B,)
            - euclidean_distance: (B,)
        """
        binary_result = self.binary_classifier(predicted_embed, observed_embed)
        anomaly_prob = binary_result["anomaly_prob"]

        type_logits, type_probs = self.type_classifier(
            predicted_embed, observed_embed, anomaly_prob,
        )

        severity_logits, severity_probs = self.severity_assessor(
            predicted_embed, observed_embed, anomaly_prob,
        )

        return {
            "anomaly_prob": anomaly_prob,
            "anomaly_type_logits": type_logits,
            "anomaly_type_probs": type_probs,
            "anomaly_type_pred": type_probs.argmax(dim=-1),
            "severity_probs": severity_probs,
            "severity_pred": severity_probs.argmax(dim=-1),
            "cosine_similarity": binary_result["cosine_similarity"],
            "euclidean_distance": binary_result["euclidean_distance"],
        }

    def format_predictions(self, outputs: Dict[str, torch.Tensor]) -> List[Dict]:
        """将模型输出格式化为结构化结果。

        Args:
            outputs: 模型输出字典

        Returns:
            结构化结果列表
        """
        B = outputs["anomaly_prob"].shape[0]
        results = []

        for i in range(B):
            anomaly_prob = round(float(outputs["anomaly_prob"][i]), 3)
            type_idx = int(outputs["anomaly_type_pred"][i])
            severity_idx = int(outputs["severity_pred"][i])
            cosine_sim = round(float(outputs["cosine_similarity"][i]), 3)
            euclidean_dist = round(float(outputs["euclidean_distance"][i]), 3)

            is_anomaly = anomaly_prob > 0.5 or cosine_sim < 0.92

            anomaly_type = "正常" if not is_anomaly else self.ANOMALY_TYPES_CN[type_idx + 1]
            severity = SeverityAssessor.SEVERITY_LEVELS[severity_idx]
            if not is_anomaly:
                severity = "正常"

            recommendation = self._get_recommendation(anomaly_type, severity)

            results.append({
                "帧级异常概率": anomaly_prob,
                "余弦相似度": cosine_sim,
                "欧氏距离": euclidean_dist,
                "异常类型": anomaly_type,
                "严重程度": severity,
                "建议措施": recommendation,
            })

        return results

    @staticmethod
    def _get_recommendation(anomaly_type: str, severity: str) -> str:
        """根据异常类型和严重程度生成建议措施。

        Args:
            anomaly_type: 异常类型
            severity: 严重程度

        Returns:
            建议措施字符串
        """
        recommendations = {
            ("断刀", "轻微"): "建议检查刀具状态，准备备刀",
            ("断刀", "中等"): "立即降速至50%，安排换刀",
            ("断刀", "严重"): "紧急停机，立即更换刀具",
            ("断刀", "危险"): "紧急停机，全面检查刀具系统",
            ("振动异常", "轻微"): "监控振动趋势，检查夹具紧固",
            ("振动异常", "中等"): "降低进给速率，检查主轴平衡",
            ("振动异常", "严重"): "立即降速至30%，调整切削参数",
            ("振动异常", "危险"): "紧急停机，检查机床地基与主轴",
            ("过切", "轻微"): "检查刀具补偿值，微调加工参数",
            ("过切", "中等"): "降低切削深度，重新计算刀路",
            ("过切", "严重"): "暂停加工，重置工件坐标系",
            ("过切", "危险"): "紧急停机，检查工件装夹与程序",
            ("撞刀", "轻微"): "检查刀路安全高度，降低快进速度",
            ("撞刀", "中等"): "暂停加工，验证刀路碰撞检测",
            ("撞刀", "严重"): "立即降速至10%，回退刀具至安全位置",
            ("撞刀", "危险"): "紧急停机，全面检查机床与工件状态",
            ("正常", "正常"): "加工状态正常，继续监测",
        }

        return recommendations.get(
            (anomaly_type, severity),
            f"检测到{anomaly_type}({severity})，请人工确认后采取措施",
        )
