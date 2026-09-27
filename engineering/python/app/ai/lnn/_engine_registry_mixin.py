"""_EngineRegistryMixin (split from HybridInferenceEngine)."""

from __future__ import annotations

from __future__ import annotations
import logging
from typing import Any


logger = logging.getLogger(__name__)

# streaming 能力探测（与 engine.py 模块级保持一致）
StreamingPredictor: Any
try:
    from app.ai.lnn.inference.streaming import StreamingPredictor as StreamingPredictor

    _HAS_STREAMING = True
except ImportError:  # pragma: no cover
    StreamingPredictor = None
    _HAS_STREAMING = False

DEFAULT_PRIOR_CONFIDENCE = 0.6


class _EngineRegistryMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _custom_models: Any
    _lnn_predictors: Any
    _streaming_predictors: Any

    def register_custom_model(
        self,
        model_name: str,
        model_instance: Any,
        model_type: str | None = None,
    ) -> None:
        """注册一个自定义模型实例，供推理时按名称调用。"""
        if not model_name:
            raise ValueError("model_name must be a non-empty string")
        self._custom_models[model_name] = {
            "instance": model_instance,
            "model_type": model_type or type(model_instance).__name__,
        }
        logger.info(
            "HybridInferenceEngine: 注册自定义模型 %r (type=%s)",
            model_name,
            model_type,
        )

    def register_lnn_predictor(self, model_name: str, predictor: Any) -> None:
        """注册一个已实例化的 :class:`LNNPredictor`，供 LNN 引擎调用。"""
        if not model_name:
            raise ValueError("model_name must be a non-empty string")
        self._lnn_predictors[model_name] = predictor
        logger.info("HybridInferenceEngine: 注册 LNN 预测器 %r", model_name)

    def register_streaming_predictor(
        self,
        model_name: str,
        streaming_predictor: Any,
    ) -> None:
        """注册流式长时序推理器（借鉴 lingbot-map GCT 思想实现）.

        Parameters
        ----------
        model_name : str
            模型名称，后续 ``infer_stream`` / ``infer_windowed`` 据此调用。
        streaming_predictor : StreamingPredictor
            已实例化的 :class:`StreamingPredictor`。其内部封装的
            :class:`LNNPredictor` 会同时注册到普通 LNN 路径，使单次推理
            与流式推理共享同一份模型权重与预处理器。

        Raises
        ------
        ValueError
            当 ``model_name`` 为空，或当前环境缺少流式模块依赖时抛出。
        """
        if not model_name:
            raise ValueError("model_name must be a non-empty string")
        if not _HAS_STREAMING:
            raise ValueError(
                "StreamingPredictor 模块不可用，无法注册流式预测器。请确认 app.ai.lnn.inference.streaming 可正常导入。"
            )
        self._streaming_predictors[model_name] = streaming_predictor
        # 将流式预测器内部封装的 LNNPredictor 同步注册到普通路径，
        # 使 infer() 单次推理也能复用同一份模型权重。
        inner_predictor = getattr(streaming_predictor, "_predictor", None)
        if inner_predictor is not None and model_name not in self._lnn_predictors:
            self._lnn_predictors[model_name] = inner_predictor
        logger.info(
            "HybridInferenceEngine: 注册流式预测器 %r (streaming=%s)",
            model_name,
            type(streaming_predictor).__name__,
        )

    def build_streaming_predictor(
        self,
        model_name: str,
        config: Any | None = None,
    ) -> Any:
        """基于已注册的 LNN 预测器构造 :class:`StreamingPredictor` 并注册.

        Parameters
        ----------
        model_name : str
            已通过 :meth:`register_lnn_predictor` 注册的模型名。
        config : Optional[StreamingConfig]
            流式配置。None 时使用 ``StreamingConfig()`` 默认值。

        Returns
        -------
        StreamingPredictor
            新构造的流式预测器（已注册到 ``_streaming_predictors``）。

        Raises
        ------
        ValueError
            当 ``model_name`` 未注册或流式模块不可用时抛出。
        """
        if not _HAS_STREAMING:
            raise ValueError("StreamingPredictor 模块不可用，无法构造流式预测器。")
        base = self._lnn_predictors.get(model_name)
        if base is None:
            raise ValueError(f"LNN 预测器 {model_name!r} 未注册，请先调用 register_lnn_predictor。")
        streaming_predictor = StreamingPredictor(
            predictor=base,
            config=config,
        )
        self._streaming_predictors[model_name] = streaming_predictor
        logger.info(
            "HybridInferenceEngine: 基于已有 LNN 预测器构造流式预测器 %r",
            model_name,
        )
        return streaming_predictor
