"""LNN 模型评估器。

实现多指标评估（回归/分类）、性能基准测试、特征重要性分析与报告生成。
"""

import torch
import numpy as np
from torch.utils.data import DataLoader
from typing import Any, Dict, List, Optional, Tuple
import time
import json
from datetime import datetime
import os
import logging
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)


class LNNEvaluator:
    """LNN 评估器，支持多指标评估与性能测试。"""

    def __init__(self, model: torch.nn.Module, device: str = "cpu"):
        """
        初始化评估器

        Args:
            model: 要评估的LNN模型 (PyTorch nn.Module)
            device: 计算设备
        """
        self.model = model
        self.device = device
        self.model = self.model.to(self.device)
        self.evaluation_history: List[Dict[str, Any]] = []

    def evaluate(
        self,
        dataloader: DataLoader,
        metrics: Optional[List[str]] = None,
        task_type: str = "classification",
    ) -> Dict[str, float]:
        """
        评估模型

        Args:
            dataloader: 数据加载器
            metrics: 评估指标列表
            task_type: 任务类型 ('classification' 或 'regression')

        Returns:
            评估结果字典
        """
        if not hasattr(self.model, "is_trained") or not self.model.is_trained:
            raise RuntimeError(
                "模型评估失败：模型尚未完成训练。评估操作只能在模型训练完成后进行。可能原因：1) 训练流程尚未启动或未完成；2) 训练过程中出现中断。请先调用 POST /api/v1/lnn/models/train 完成模型训练，再进行评估。"  # noqa: E501
            )

        self.model.eval()

        all_preds = []
        all_labels = []
        inference_times = []

        # P2-AI-4: 使用 inference_mode 替代 no_grad，评估阶段无需 autograd 图，更高效
        with torch.inference_mode():
            for batch_X, batch_y in dataloader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)

                start_time = time.perf_counter()
                outputs = self.model(batch_X)
                # CFC/LTC models return (output, hidden_state) tuple
                if isinstance(outputs, tuple):
                    outputs = outputs[0]
                end_time = time.perf_counter()

                inference_times.append((end_time - start_time) * 1000)

                all_preds.append(outputs.cpu().numpy())
                all_labels.append(batch_y.cpu().numpy())

        all_preds = np.concatenate(all_preds, axis=0)
        all_labels = np.concatenate(all_labels, axis=0)

        results = {}

        if task_type == "classification":
            results.update(
                self.compute_classification_metrics(all_labels, all_preds, metrics)
            )
        elif task_type == "regression":
            results.update(
                self.compute_regression_metrics(all_labels, all_preds, metrics)
            )
        else:
            raise ValueError(
                f"评估指标计算失败：未知的任务类型 '{task_type}'。支持的任务类型包括：'classification'（分类任务）、'regression'（回归任务）。请检查评估配置中的 task_type 参数设置。"  # noqa: E501
            )

        results.update(
            self.compute_performance_metrics(inference_times, len(all_preds))
        )

        results["timestamp"] = datetime.now().isoformat()
        results["task_type"] = task_type
        self.evaluation_history.append(results)

        return results

    def compute_metrics(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        metrics: Optional[List[str]] = None,
        task_type: str = "classification",
    ) -> Dict[str, float]:
        """
        根据真实标签和预测结果计算指定评估指标

        Args:
            y_true: 真实标签
            y_pred: 预测结果
            metrics: 指标列表
            task_type: 任务类型

        Returns:
            指标结果字典
        """
        if task_type == "classification":
            return self.compute_classification_metrics(y_true, y_pred, metrics)
        elif task_type == "regression":
            return self.compute_regression_metrics(y_true, y_pred, metrics)
        else:
            raise ValueError(
                f"评估指标计算失败：未知的任务类型 '{task_type}'。支持的任务类型包括：'classification'（分类任务）、'regression'（回归任务）。请检查评估配置中的 task_type 参数设置。"  # noqa: E501
            )

    def compute_classification_metrics(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """
        计算分类任务指标

        Args:
            y_true: 真实标签
            y_pred: 预测结果（概率或logits）
            metrics: 指标列表，None表示计算所有指标

        Returns:
            指标结果字典
        """
        pred_classes, true_classes = self._get_classes(y_true, y_pred)

        results = {}

        if metrics is None or "accuracy" in metrics:
            results["accuracy"] = self.accuracy(true_classes, pred_classes)

        if metrics is None or "precision" in metrics:
            results["precision"] = self.precision(true_classes, pred_classes)

        if metrics is None or "recall" in metrics:
            results["recall"] = self.recall(true_classes, pred_classes)

        if metrics is None or "f1" in metrics:
            results["f1"] = self.f1_score(true_classes, pred_classes)

        if metrics is None or "confusion_matrix" in metrics:
            results["confusion_matrix"] = self.confusion_matrix(
                true_classes, pred_classes
            )

        return results

    def compute_regression_metrics(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """
        计算回归任务指标

        Args:
            y_true: 真实标签
            y_pred: 预测结果
            metrics: 指标列表，None表示计算所有指标

        Returns:
            指标结果字典
        """
        y_true = y_true.flatten()
        y_pred = y_pred.flatten()

        results = {}

        if metrics is None or "mae" in metrics:
            results["mae"] = self.mae(y_true, y_pred)

        if metrics is None or "rmse" in metrics:
            results["rmse"] = self.rmse(y_true, y_pred)

        if metrics is None or "mape" in metrics:
            results["mape"] = self.mape(y_true, y_pred)

        if metrics is None or "r2" in metrics:
            results["r2"] = self.r2_score(y_true, y_pred)

        return results

    @staticmethod
    def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """平均绝对误差"""
        return float(np.mean(np.abs(y_true - y_pred)))

    @staticmethod
    def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """均方根误差"""
        return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

    @staticmethod
    def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """平均绝对百分比误差"""
        mask = y_true != 0
        if np.sum(mask) == 0:
            return float("inf")
        return float(
            np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100
        )

    @staticmethod
    def r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """
        决定系数 (R²)

        公式: R² = 1 - SS_res / SS_tot
        其中 SS_res = sum((y_true - y_pred)²), SS_tot = sum((y_true - mean(y_true))²)
        """
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)

        if ss_tot == 0:
            return 0.0

        return float(1 - ss_res / ss_tot)

    @staticmethod
    def accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """准确率"""
        return float(np.mean(y_true == y_pred))

    @staticmethod
    def precision(
        y_true: np.ndarray, y_pred: np.ndarray, average: str = "binary"
    ) -> float:
        """
        精确率

        Args:
            y_true: 真实标签
            y_pred: 预测标签
            average: 平均方式 ('binary', 'macro', 'micro', 'weighted')
        """
        if average == "binary":
            tp = np.sum((y_pred == 1) & (y_true == 1))
            fp = np.sum((y_pred == 1) & (y_true == 0))
            return float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        elif average == "macro":
            classes = np.unique(np.concatenate([y_true, y_pred]))
            precisions = []
            for c in classes:
                tp = np.sum((y_pred == c) & (y_true == c))
                fp = np.sum((y_pred == c) & (y_true != c))
                precisions.append(tp / (tp + fp) if (tp + fp) > 0 else 0.0)
            return float(np.mean(precisions))
        elif average == "micro":
            tp = np.sum(y_pred == y_true)
            return float(tp / len(y_true))
        elif average == "weighted":
            classes, counts = np.unique(y_true, return_counts=True)
            precisions = []
            for c in classes:
                tp = np.sum((y_pred == c) & (y_true == c))
                fp = np.sum((y_pred == c) & (y_true != c))
                precisions.append(tp / (tp + fp) if (tp + fp) > 0 else 0.0)
            return float(np.average(precisions, weights=counts))
        else:
            raise ValueError(
                f"评估指标计算失败：未知的平均方式 '{average}'。支持的平均方式包括：'micro'（微平均）、'macro'（宏平均）、'weighted'（加权平均）、'samples'（样本平均）、'binary'（二分类）。请检查评估配置中的 average 参数。"  # noqa: E501
            )

    @staticmethod
    def recall(
        y_true: np.ndarray, y_pred: np.ndarray, average: str = "binary"
    ) -> float:
        """
        召回率

        Args:
            y_true: 真实标签
            y_pred: 预测标签
            average: 平均方式
        """
        if average == "binary":
            tp = np.sum((y_pred == 1) & (y_true == 1))
            fn = np.sum((y_pred == 0) & (y_true == 1))
            return float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        elif average == "macro":
            classes = np.unique(np.concatenate([y_true, y_pred]))
            recalls = []
            for c in classes:
                tp = np.sum((y_pred == c) & (y_true == c))
                fn = np.sum((y_pred != c) & (y_true == c))
                recalls.append(tp / (tp + fn) if (tp + fn) > 0 else 0.0)
            return float(np.mean(recalls))
        elif average == "micro":
            tp = np.sum(y_pred == y_true)
            return float(tp / len(y_true))
        elif average == "weighted":
            classes, counts = np.unique(y_true, return_counts=True)
            recalls = []
            for c in classes:
                tp = np.sum((y_pred == c) & (y_true == c))
                fn = np.sum((y_pred != c) & (y_true == c))
                recalls.append(tp / (tp + fn) if (tp + fn) > 0 else 0.0)
            return float(np.average(recalls, weights=counts))
        else:
            raise ValueError(
                f"评估指标计算失败：未知的平均方式 '{average}'。支持的平均方式包括：'micro'（微平均）、'macro'（宏平均）、'weighted'（加权平均）、'samples'（样本平均）、'binary'（二分类）。请检查评估配置中的 average 参数。"  # noqa: E501
            )

    @staticmethod
    def f1_score(
        y_true: np.ndarray, y_pred: np.ndarray, average: str = "binary"
    ) -> float:
        """
        F1分数

        Args:
            y_true: 真实标签
            y_pred: 预测标签
            average: 平均方式
        """
        precision = LNNEvaluator.precision(y_true, y_pred, average=average)
        recall = LNNEvaluator.recall(y_true, y_pred, average=average)

        if precision + recall == 0:
            return 0.0
        return float(2 * precision * recall / (precision + recall))

    def confusion_matrix(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        normalize: Optional[str] = None,
    ) -> np.ndarray:
        """
        生成混淆矩阵

        Args:
            y_true: 真实标签
            y_pred: 预测标签
            normalize: 归一化方式
                - None: 不归一化
                - 'true': 按真实标签归一化（每行和为1）
                - 'pred': 按预测标签归一化（每列和为1）
                - 'all': 全局归一化（所有元素和为1）

        Returns:
            混淆矩阵 (n_classes, n_classes)
        """
        classes = np.unique(np.concatenate([y_true, y_pred]))
        n_classes = len(classes)
        cm = np.zeros((n_classes, n_classes), dtype=np.int64)

        for t, p in zip(y_true, y_pred):
            t_idx = np.where(classes == t)[0][0]
            p_idx = np.where(classes == p)[0][0]
            cm[t_idx, p_idx] += 1

        if normalize == "true":
            row_sums = cm.sum(axis=1, keepdims=True)
            row_sums[row_sums == 0] = 1
            cm = cm / row_sums
        elif normalize == "pred":
            col_sums = cm.sum(axis=0, keepdims=True)
            col_sums[col_sums == 0] = 1
            cm = cm / col_sums
        elif normalize == "all":
            total = cm.sum()
            if total > 0:
                cm = cm / total

        return cm

    def feature_importance(
        self,
        X_test: np.ndarray,
        method: str = "permutation",
        n_permutations: int = 10,
        metric: str = "accuracy",
    ) -> Dict[str, Any]:
        """
        分析并返回模型特征重要性排序

        Args:
            X_test: 测试特征数据
            method: 分析方法 ('permutation', 'weight_based')
            n_permutations: 排列次数（用于permutation方法）
            metric: 评估指标

        Returns:
            特征重要性字典，包含排序和可视化数据
        """
        if method == "permutation":
            return self._permutation_importance(X_test, n_permutations, metric)
        elif method == "weight_based":
            return self._weight_based_importance()
        else:
            raise ValueError(
                f"特征重要性分析失败：未知的方法 '{method}'。支持的方法包括：'permutation'（排列重要性）、'weight_based'（基于权重的方法）。请检查配置中的 method 参数。"
            )

    def _permutation_importance(
        self,
        X_test: np.ndarray,
        n_permutations: int,
        metric: str,
    ) -> Dict[str, Any]:
        """
        基于排列的特征重要性分析

        通过随机打乱每个特征的值，观察模型性能下降程度来评估特征重要性
        """
        self.model.eval()

        # P2-AI-4: 使用 inference_mode 替代 no_grad，特征重要性评估无需 autograd 图
        with torch.inference_mode():
            # P3-AI-3: 使用 torch.tensor + 显式 dtype 替代 FloatTensor，避免受全局默认 dtype 影响
            X_tensor = torch.tensor(X_test, dtype=torch.float32).to(self.device)
            baseline_output = self.model(X_tensor).cpu().numpy()

        baseline_score = self._compute_metric_score(baseline_output, metric)

        n_features = X_test.shape[1]
        importance_scores = np.zeros(n_features)

        for feat_idx in range(n_features):
            scores = []
            for _ in range(n_permutations):
                X_permuted = X_test.copy()
                np.random.shuffle(X_permuted[:, feat_idx])

                # P2-AI-4: 使用 inference_mode 替代 no_grad，特征重要性评估无需 autograd 图
                with torch.inference_mode():
                    # P3-AI-3: 使用 torch.tensor + 显式 dtype 替代 FloatTensor
                    X_tensor = torch.tensor(X_permuted, dtype=torch.float32).to(self.device)
                    perm_output = self.model(X_tensor).cpu().numpy()

                perm_score = self._compute_metric_score(perm_output, metric)
                scores.append(perm_score)

            importance_scores[feat_idx] = baseline_score - np.mean(scores)

        feature_ranking = np.argsort(-importance_scores)

        return {
            "importance_scores": importance_scores,
            "feature_ranking": feature_ranking,
            "baseline_score": baseline_score,
            "method": "permutation",
        }

    def _weight_based_importance(self) -> Dict[str, Any]:
        """
        基于权重的特征重要性分析

        直接使用模型第一层权重的绝对值作为特征重要性
        """
        state_dict = self.model.state_dict()
        first_layer_weight = None

        for key, value in state_dict.items():
            if "weight" in key and value.ndim == 2:
                first_layer_weight = value.cpu().numpy()
                break

        if first_layer_weight is None:
            raise ValueError(
                "模型特征重要性分析失败：无法从模型中提取权重参数。可能原因：1) 模型尚未初始化或权重未加载；2) 模型架构不包含标准的权重层（如 Linear 层）；3) 模型使用自定义层且未暴露权重参数。请确认模型已完成训练并正确加载权重，或检查模型架构定义。"  # noqa: E501
            )

        importance_scores = np.mean(np.abs(first_layer_weight), axis=0)
        feature_ranking = np.argsort(-importance_scores)

        return {
            "importance_scores": importance_scores,
            "feature_ranking": feature_ranking,
            "method": "weight_based",
        }

    def _compute_metric_score(self, predictions: np.ndarray, metric: str) -> float:
        """计算指定指标分数"""
        if metric == "accuracy":
            return float(np.mean(predictions > 0.5))
        elif metric == "mse":
            return float(np.mean(predictions**2))
        else:
            return float(np.mean(predictions))

    def _get_classes(
        self, y_true: np.ndarray, y_pred: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """从预测结果中提取类别"""
        if y_pred.ndim > 1 and y_pred.shape[1] > 1:
            pred_classes = np.argmax(y_pred, axis=1)
        else:
            pred_classes = (y_pred.flatten() > 0.5).astype(int)

        if y_true.ndim > 1 and y_true.shape[1] > 1:
            true_classes = np.argmax(y_true, axis=1)
        else:
            true_classes = y_true.flatten().astype(int)

        return pred_classes, true_classes

    def compute_performance_metrics(
        self,
        inference_times: List[float],
        n_samples: int,
    ) -> Dict[str, float]:
        """计算性能指标"""
        times = np.array(inference_times)

        return {
            "avg_inference_time_ms": float(np.mean(times)),
            "throughput_samples_per_sec": float(n_samples / (np.sum(times) / 1000)),
            "p50_latency_ms": float(np.percentile(times, 50)),
            "p95_latency_ms": float(np.percentile(times, 95)),
            "p99_latency_ms": float(np.percentile(times, 99)),
        }

    def generate_report(self, results: Dict[str, float]) -> str:
        """生成评估报告"""
        task_type = results.get("task_type", "classification")

        report_parts = [
            "=== LNN Model Evaluation Report ===",
            f"Timestamp: {results.get('timestamp', 'N/A')}",
            f"Task Type: {task_type}",
            "",
        ]

        if task_type == "classification":
            report_parts.extend(
                [
                    "--- Classification Metrics ---",
                    f"  Accuracy:  {results.get('accuracy', 0):.4f}",
                    f"  Precision: {results.get('precision', 0):.4f}",
                    f"  Recall:    {results.get('recall', 0):.4f}",
                    f"  F1 Score:  {results.get('f1', 0):.4f}",
                    "",
                ]
            )
        elif task_type == "regression":
            report_parts.extend(
                [
                    "--- Regression Metrics ---",
                    f"  MAE:  {results.get('mae', 0):.4f}",
                    f"  RMSE: {results.get('rmse', 0):.4f}",
                    f"  MAPE: {results.get('mape', 0):.4f}%",
                    f"  R²:   {results.get('r2', 0):.4f}",
                    "",
                ]
            )

        report_parts.extend(
            [
                "--- Inference Performance ---",
                f"  Avg Latency:    {results.get('avg_inference_time_ms', 0):.2f} ms",
                f"  P50 Latency:    {results.get('p50_latency_ms', 0):.2f} ms",
                f"  P95 Latency:    {results.get('p95_latency_ms', 0):.2f} ms",
                f"  P99 Latency:    {results.get('p99_latency_ms', 0):.2f} ms",
                f"  Throughput:     {results.get('throughput_samples_per_sec', 0):.0f} samples/sec",
                "",
            ]
        )

        return "\n".join(report_parts)

    def save_report(self, results: Dict[str, float], path: str) -> None:
        """保存评估报告到文件"""
        os.makedirs(
            os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True
        )

        with open(path, "w", encoding="utf-8") as f:
            f.write(self.generate_report(results))

        results_json = path.replace(".txt", ".json")
        with open(results_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

    def get_evaluation_history(self) -> List[Dict[str, Any]]:
        """获取评估历史"""
        return self.evaluation_history

    def plot_results(
        self, output_dir: str, prefix: str = "evaluation"
    ) -> Dict[str, str]:
        """
        绘制评估结果图表

        Args:
            output_dir: 输出目录
            prefix: 文件名前缀

        Returns:
            生成的图表文件路径字典
        """
        os.makedirs(output_dir, exist_ok=True)
        plot_paths = {}

        if not self.evaluation_history:
            logger.info("No evaluation history to plot.")
            return plot_paths

        latest = self.evaluation_history[-1]
        task_type = latest.get("task_type", "classification")

        if task_type == "classification":
            fig, axes = plt.subplots(1, 2, figsize=(12, 5))

            cm = latest.get("confusion_matrix")
            if cm is not None:
                im = axes[0].imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
                axes[0].set_title("Confusion Matrix")
                plt.colorbar(im, ax=axes[0])
                axes[0].set_xlabel("Predicted")
                axes[0].set_ylabel("True")

            metrics = ["accuracy", "precision", "recall", "f1"]
            values = [latest.get(m, 0) for m in metrics]
            axes[1].bar(
                metrics, values, color=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
            )
            axes[1].set_ylim(0, 1)
            axes[1].set_title("Classification Metrics")
            axes[1].set_ylabel("Score")

            plt.tight_layout()
            path = os.path.join(output_dir, f"{prefix}_classification_results.png")
            plt.savefig(path, dpi=150)
            plt.close()
            plot_paths["classification"] = path

        elif task_type == "regression":
            fig, ax = plt.subplots(figsize=(8, 6))
            metrics = ["mae", "rmse", "mape", "r2"]
            values = [latest.get(m, 0) for m in metrics]
            ax.bar(metrics, values, color=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"])
            ax.set_title("Regression Metrics")
            ax.set_ylabel("Value")

            plt.tight_layout()
            path = os.path.join(output_dir, f"{prefix}_regression_results.png")
            plt.savefig(path, dpi=150)
            plt.close()
            plot_paths["regression"] = path

        perf_path = os.path.join(output_dir, f"{prefix}_performance.png")
        fig, ax = plt.subplots(figsize=(8, 5))
        perf_metrics = [
            "avg_inference_time_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "p99_latency_ms",
        ]
        perf_values = [latest.get(m, 0) for m in perf_metrics]
        ax.bar(perf_metrics, perf_values, color="#9467bd")
        ax.set_title("Inference Performance")
        ax.set_ylabel("Latency (ms)")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.savefig(perf_path, dpi=150)
        plt.close()
        plot_paths["performance"] = perf_path

        logger.info("Evaluation plots saved to %s", output_dir)
        return plot_paths
