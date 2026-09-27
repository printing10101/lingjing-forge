"""
Postprocessing module for LNN system.

Provides structured result parsing, confidence computation,
uncertainty assessment, and visualization interfaces.
"""

import numpy as np
import json
from typing import Any
from datetime import datetime, timezone

from app.ai.lnn.core import InferenceResult, EngineType


class ResultPostprocessor:
    """
    输出后处理模块

    提供：
    - 结果结构化解析（JSON/XML格式输出）
    - 置信度计算（基于模型输出概率分布）
    - 不确定性评估
    - 结果可视化接口
    """

    def __init__(self, include_metadata: bool = True, include_uncertainty: bool = True):
        """
        初始化后处理器

        Args:
            include_metadata: 是否包含元数据
            include_uncertainty: 是否包含不确定性评估
        """
        self.include_metadata = include_metadata
        self.include_uncertainty = include_uncertainty

    def process_result(
        self,
        predictions: np.ndarray,
        engine: EngineType = EngineType.LNN,
        model_name: str | None = None,
        processing_time_ms: float = 0.0,
        input_data: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InferenceResult:
        """
        处理推理结果

        Args:
            predictions: 模型预测输出
            engine: 使用的推理引擎
            model_name: 使用的模型名称
            processing_time_ms: 处理时间（毫秒）
            input_data: 原始输入数据
            metadata: 附加元数据

        Returns:
            InferenceResult 标准化推理结果
        """
        confidences = self._calculate_confidence(predictions)
        uncertainty = self._calculate_uncertainty(predictions) if self.include_uncertainty else None

        # 构建证据列表
        evidence = self._build_evidence(predictions, confidences)

        # 合并元数据
        result_metadata = metadata or {}
        if self.include_metadata:
            result_metadata.update(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "input_shape": list(input_data.shape)
                    if input_data is not None and hasattr(input_data, "shape")
                    else None,
                    "prediction_shape": list(predictions.shape),
                }
            )

        return InferenceResult(
            prediction=predictions.tolist() if hasattr(predictions, "tolist") else predictions,
            confidence=float(np.mean(confidences)),
            engine_used=engine,
            model_used=model_name,
            processing_time_ms=processing_time_ms,
            metadata=result_metadata,
            evidence=evidence,
            uncertainty=uncertainty,
        )

    def _calculate_confidence(self, predictions: np.ndarray) -> np.ndarray:
        """
        基于模型输出概率分布计算置信度

        Args:
            predictions: 模型原始输出

        Returns:
            置信度数组
        """
        if not isinstance(predictions, np.ndarray):
            predictions = np.array(predictions, dtype=np.float32)  # M13 修复：指定 float32 dtype

        if predictions.ndim == 1:
            predictions = predictions.reshape(1, -1)

        # 空数组防御 [N-H2]：np.max 在空轴上会抛 ValueError
        if predictions.size == 0 or predictions.shape[0] == 0:
            return np.zeros(predictions.shape[0], dtype=np.float32)

        # Softmax归一化
        exp_preds = np.exp(predictions - np.max(predictions, axis=1, keepdims=True))
        softmax_preds = exp_preds / np.sum(exp_preds, axis=1, keepdims=True)

        # 最大概率作为置信度
        return np.max(softmax_preds, axis=1)

    def _calculate_uncertainty(self, predictions: np.ndarray) -> dict[str, float]:
        """
        计算不确定性指标

        Args:
            predictions: 模型预测

        Returns:
            不确定性字典
        """
        if not isinstance(predictions, np.ndarray):
            predictions = np.array(predictions, dtype=np.float32)  # M13 修复：指定 float32 dtype

        if predictions.ndim == 1:
            predictions = predictions.reshape(1, -1)

        # 空数组防御 [N-H2]：np.max / np.log 在空轴上会抛 ValueError / -inf
        if predictions.size == 0 or predictions.shape[1] == 0:
            return {
                "entropy": 0.0,
                "normalized_entropy": 0.0,
                "aleatoric_uncertainty": 0.0,
                "confidence_variance": 0.0,
            }

        # Softmax
        exp_preds = np.exp(predictions - np.max(predictions, axis=1, keepdims=True))
        softmax_preds = exp_preds / np.sum(exp_preds, axis=1, keepdims=True)

        # 熵计算
        entropy = -np.sum(softmax_preds * np.log(softmax_preds + 1e-10), axis=1)
        max_entropy = np.log(predictions.shape[1])
        normalized_entropy = entropy / (max_entropy + 1e-10)

        return {
            "entropy": float(np.mean(entropy)),
            "normalized_entropy": float(np.mean(normalized_entropy)),
            "aleatoric_uncertainty": float(np.mean(np.var(softmax_preds, axis=1))),
            "confidence_variance": float(np.var(np.max(softmax_preds, axis=1))),
        }

    def _build_evidence(self, predictions: np.ndarray, confidences: np.ndarray) -> list[dict[str, Any]]:
        """
        构建支持证据列表

        Args:
            predictions: 预测结果
            confidences: 置信度

        Returns:
            证据列表
        """
        evidence = []
        # [N-H6] 长度校验：zip 会静默截断不等长输入，导致证据条目数与样本数不一致
        if len(predictions) != len(confidences):
            raise ValueError(f"predictions 与 confidences 长度不一致: {len(predictions)} vs {len(confidences)}")
        for i, (pred, conf) in enumerate(zip(predictions, confidences)):
            if hasattr(pred, "__iter__") and len(pred) > 1:
                top_class = int(np.argmax(pred))
                top_score = float(np.max(pred))
                evidence.append(
                    {
                        "sample_index": i,
                        "predicted_class": top_class,
                        "confidence": float(conf),
                        "score": top_score,
                        "top_k_classes": self._get_top_k(pred, k=3),
                    }
                )
            else:
                evidence.append(
                    {
                        "sample_index": i,
                        "prediction": float(pred) if hasattr(pred, "__iter__") else pred,
                        "confidence": float(conf),
                    }
                )
        return evidence

    @staticmethod
    def _get_top_k(scores: np.ndarray, k: int = 3) -> list[dict[str, Any]]:
        """获取Top-K预测"""
        indices = np.argsort(scores)[::-1][:k]
        return [{"class": int(idx), "score": float(scores[idx])} for idx in indices]

    def to_json(self, result: InferenceResult, indent: int = 2) -> str:
        """
        转换为JSON格式

        Args:
            result: 推理结果
            indent: JSON缩进

        Returns:
            JSON字符串
        """
        return json.dumps(result.to_dict(), indent=indent, default=str)

    def to_xml(self, result: InferenceResult) -> str:
        """
        转换为XML格式

        Args:
            result: 推理结果

        Returns:
            XML字符串
        """
        xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_parts.append("<InferenceResult>")

        data = result.to_dict()
        self._dict_to_xml(data, "", xml_parts)

        xml_parts.append("</InferenceResult>")
        return "\n".join(xml_parts)

    def _dict_to_xml(self, data: Any, parent: str, parts: list[str]) -> None:
        """递归转换字典为XML"""
        if isinstance(data, dict):
            for key, value in data.items():
                tag = key.replace(" ", "_")
                if isinstance(value, dict):
                    parts.append(f"<{tag}>")
                    self._dict_to_xml(value, tag, parts)
                    parts.append(f"</{tag}>")
                elif isinstance(value, list):
                    parts.append(f"<{tag}>")
                    for item in value:
                        parts.append("<item>")
                        self._dict_to_xml(item, "item", parts)
                        parts.append("</item>")
                    parts.append(f"</{tag}>")
                else:
                    parts.append(f"<{tag}>{value}</{tag}>")

    def generate_visualization_data(self, result: InferenceResult) -> dict[str, Any]:
        """
        生成可视化数据

        Args:
            result: 推理结果

        Returns:
            可视化数据字典
        """
        return {
            "prediction_distribution": result.prediction,
            "confidence_score": result.confidence,
            "evidence_chart": result.evidence,
            "uncertainty_gauge": result.uncertainty,
            "processing_timeline": {
                "processing_time_ms": result.processing_time_ms,
                "engine": result.engine_used.value if result.engine_used else None,
                "model": result.model_used,
            },
        }
