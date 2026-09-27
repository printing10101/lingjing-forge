"""Hybrid Inference Engine —— LNN 混合推理编排器（生产实现）.

本模块实现 :class:`HybridInferenceEngine`，是 ARCHITECTURE.md §3.5 文档化的
公开编排 API。引擎将任务路由（TaskRouter）、模型执行（自定义模型 / LNN
预测器）和结果融合（Dempster-Shafer）串联为统一的推理管线。

设计要点：
1. 真实推理：当注册了自定义模型或 LNN 预测器时，调用其 ``predict`` /
   ``__call__`` 方法获取实际预测值；否则走规则回退路径并显式标注
   ``fallback=True``，绝不返回 ``prediction=None`` 的"假结果"。
2. 真实不确定性：当多个引擎/模型可用时，使用 Dempster-Shafer 融合
   得到的标准差作为认知不确定性；单模型时使用预测器自身的置信度。
3. 在线学习：路由器随推理结果 ``update_outcome`` 反馈，逐步提升路由
   质量，避免长期固定 0.75 的"假置信度"。
4. 统计可观测：所有调用计数、延迟、融合次数均落到 ``get_engine_stats``。
5. 流式扩展：通过 ``register_streaming_predictor`` 注册借鉴 lingbot-map
   GCT 思想实现的 :class:`StreamingPredictor`，调用 ``infer_stream`` /
   ``infer_windowed`` 处理长时序加工流（关键帧 + 锚点漂移修正 + 窗口化推理）。
"""

from __future__ import annotations

import logging
from typing import Any


from app.ai.lnn.fusion import DempsterShaferFusion
from app.ai.lnn.router.task_router import TaskRouter
from app.ai.lnn._engine_registry_mixin import _EngineRegistryMixin
from app.ai.lnn._engine_inference_mixin import _EngineInferenceMixin

# 流式推理扩展（借鉴 lingbot-map GCT 架构思想）。采用惰性导入避免在
# torch 不可用时拖垮整个引擎；流式能力作为可选增强存在。
StreamingConfig: Any
StreamingPredictor: Any
try:
    from app.ai.lnn.inference.streaming import (
        StreamingConfig as StreamingConfig,
        StreamingPredictor as StreamingPredictor,
    )

    _HAS_STREAMING = True
except ImportError:  # pragma: no cover - 仅当 streaming 模块自身依赖缺失时触发
    StreamingConfig = None
    StreamingPredictor = None
    _HAS_STREAMING = False

logger = logging.getLogger(__name__)

# LLM 调用成功但未返回置信度时使用的默认先验置信度
DEFAULT_PRIOR_CONFIDENCE = 0.6


class HybridInferenceEngine(_EngineRegistryMixin, _EngineInferenceMixin):
    """混合推理引擎 —— 真实多模型编排，无 stub。

    Parameters:
        rule_weight: 透传至 :class:`TaskRouter`，规则得分权重。
        ml_weight: 透传至 :class:`TaskRouter`，机器学习得分权重。
        enable_fusion: 是否在单模型可用时也包装为 FusionResult。
        enable_parallel_execution: 多模型时是否并行执行（实验性）。
        cache_size: 保留缓存槽位，预留给未来推理结果缓存。
        device: 推理设备标识（cpu/cuda/mps），透传至下游模型。
    """

    def __init__(
        self,
        rule_weight: float = 0.4,
        ml_weight: float = 0.6,
        enable_fusion: bool = True,
        enable_parallel_execution: bool = False,
        cache_size: int = 10,
        device: str = "cpu",
    ) -> None:
        self._router = TaskRouter(
            rule_weight=rule_weight,
            ml_weight=ml_weight,
        )
        self._fusion = DempsterShaferFusion() if enable_fusion else None
        self._enable_parallel_execution = enable_parallel_execution
        self._cache_size = cache_size
        self._device = device
        self._custom_models: dict[str, dict[str, Any]] = {}
        self._lnn_predictors: dict[str, Any] = {}
        # 流式预测器注册表：model_name -> StreamingPredictor
        # 借鉴 lingbot-map GCT 思想，用于长时序加工流推理。
        # 与 _lnn_predictors 分离以保持单次推理路径完全不受影响。
        self._streaming_predictors: dict[str, Any] = {}
        self._engine_stats: dict[str, Any] = {
            "total_inferences": 0,
            "successful_inferences": 0,
            "fallback_invocations": 0,
            "fusion_invocations": 0,
            "errors": 0,
            "streaming_frames_processed": 0,
            "streaming_windows_processed": 0,
        }

    # 模型注册

    def initialize_models(self) -> None:
        """惰性初始化：触发已注册自定义模型的 ``build`` / ``eval`` 钩子。

        对未实现这些方法的模型静默跳过，避免阻塞引擎启动。
        """
        for name, entry in self._custom_models.items():
            instance = entry.get("instance")
            if instance is None:
                continue
            try:
                build_fn = getattr(instance, "build", None)
                if callable(build_fn):
                    build_fn()
                eval_fn = getattr(instance, "eval", None)
                if callable(eval_fn):
                    eval_fn()
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as exc:
                logger.warning(
                    "HybridInferenceEngine: 模型 %s 初始化失败: %s",
                    name,
                    exc,
                    exc_info=True,
                )
        logger.info(
            "HybridInferenceEngine: initialize_models 完成，已注册 %d 个自定义模型",
            len(self._custom_models),
        )

    # 核心推理

    # 流式长时序推理（借鉴 lingbot-map GCT 架构思想）

    # 引擎分发

    # 工具方法

    # 统计与诊断

    def get_engine_stats(self) -> dict[str, Any]:
        """返回聚合统计，便于 /api/v1/lnn/.../stats 端点暴露。"""
        stats = dict(self._engine_stats)
        stats["router_stats"] = self._router.get_decision_stats()
        if self._fusion is not None:
            stats["fusion_stats"] = self._fusion.get_fusion_stats()
        stats["custom_model_count"] = len(self._custom_models)
        stats["lnn_predictor_count"] = len(self._lnn_predictors)
        stats["streaming_predictor_count"] = len(self._streaming_predictors)
        stats["streaming_available"] = _HAS_STREAMING
        stats["stub_implementation"] = False
        # 聚合每个流式预测器的内部统计（关键帧率、缓存命中率、漂移等）
        streaming_details: dict[str, Any] = {}
        for name, sp in self._streaming_predictors.items():
            try:
                streaming_details[name] = sp.get_statistics()
            except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
                streaming_details[name] = {"error": str(exc)}
        stats["streaming_details"] = streaming_details
        return stats


__all__ = ["HybridInferenceEngine"]
