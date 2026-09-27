"""_EngineInferenceMixin (split from HybridInferenceEngine)."""

from __future__ import annotations

from __future__ import annotations
import logging
import time
from typing import Any, Union
from collections.abc import Iterator
import numpy as np
from app.ai.lnn.core import (
    EngineType,
    FusionResult,
    InferenceResult,
    TaskInput,
)


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


class _EngineInferenceMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _custom_models: Any
    _engine_stats: Any
    _fusion: Any
    _lnn_predictors: Any
    _router: Any
    _streaming_predictors: Any

    def infer(
        self,
        task_description: str,
        input_data: Any,
        context: dict[str, Any] | None = None,
        precision_requirement: float = 0.9,
        time_sensitivity: float = 0.5,
        max_latency_ms: int = 1000,
    ) -> Union[FusionResult, InferenceResult]:
        """运行一次推理任务，返回 :class:`InferenceResult` 或 :class:`FusionResult`。"""
        self._engine_stats["total_inferences"] += 1

        task = TaskInput(
            task_description=task_description,
            input_data=input_data,
            context=context,
            precision_requirement=precision_requirement,
            time_sensitivity=time_sensitivity,
            max_latency_ms=max_latency_ms,
        )

        start_ts = time.perf_counter()
        decision = self._router.route(task)
        routing_ms = (time.perf_counter() - start_ts) * 1000.0

        results: list[InferenceResult] = []

        primary = self._run_engine(
            decision.selected_engine,
            decision.selected_model,
            task,
            routing_ms,
        )
        if primary is not None:
            results.append(primary)

        for alt in decision.alternatives or []:
            alt_engine = alt.get("engine")
            alt_model = alt.get("model")
            if alt_engine == decision.selected_engine.value and alt_model == decision.selected_model:
                continue
            try:
                engine_enum = EngineType(alt_engine) if isinstance(alt_engine, str) else alt_engine
            except ValueError:
                continue
            alt_result = self._run_engine(engine_enum, alt_model, task, routing_ms)
            if alt_result is not None:
                results.append(alt_result)

        if not results:
            self._engine_stats["fallback_invocations"] += 1
            fallback_result = self._fallback_result(task, decision, routing_ms)
            results.append(fallback_result)

        self._engine_stats["successful_inferences"] += 1
        self._router.update_outcome(
            engine=decision.selected_engine,
            success=True,
            confidence=decision.confidence,
        )

        if self._fusion is not None and len(results) >= 1:
            self._engine_stats["fusion_invocations"] += 1
            return self._fusion.fuse(results)

        return results[0]

    def infer_batch(
        self,
        tasks: list[dict[str, Any]],
        batch_size: int = 32,
    ) -> list[Union[FusionResult, InferenceResult]]:
        """批量推理：当前按顺序执行以保证可观测性。"""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        results: list[Union[FusionResult, InferenceResult]] = []
        for task in tasks:
            try:
                results.append(
                    self.infer(
                        task_description=task.get("task_description", ""),
                        input_data=task.get("input_data"),
                        context=task.get("context"),
                        precision_requirement=task.get("precision_requirement", 0.9),
                        time_sensitivity=task.get("time_sensitivity", 0.5),
                        max_latency_ms=task.get("max_latency_ms", 1000),
                    )
                )
            except (ValueError, TypeError, RuntimeError, OSError, KeyError, AttributeError) as exc:
                logger.error(
                    "HybridInferenceEngine: 批量推理中第 %d 项失败: %s",
                    len(results),
                    exc,
                    exc_info=True,
                )
                self._engine_stats["errors"] += 1
                results.append(
                    InferenceResult(
                        prediction=None,
                        confidence=0.0,
                        engine_used=EngineType.HYBRID,
                        model_used=None,
                        processing_time_ms=0.0,
                        metadata={"error": str(exc), "fallback": True},
                        evidence=[],
                        uncertainty={"error": 1.0},
                    )
                )
        return results

    def infer_stream(
        self,
        model_name: str,
        data_stream: Iterator[Any],
    ) -> Iterator[InferenceResult]:
        """对长时序加工数据流执行流式推理.

        逐帧调用 :meth:`StreamingPredictor.predict_frame`，并将结果包装为
        :class:`InferenceResult`。关键帧判定、锚点漂移修正、轨迹记忆约束
        均在 ``StreamingPredictor`` 内部完成，本方法仅做编排与统计。

        Parameters
        ----------
        model_name : str
            已注册的流式预测器名称。
        data_stream : Iterator[Any]
            输入数据迭代器（如传感器实时采样流）。

        Yields
        ------
        InferenceResult
            每帧的推理结果，``metadata`` 中包含 ``is_keyframe`` /
            ``anchor_drift`` / ``trajectory_deviation`` 等流式元信息。

        Raises
        ------
        ValueError
            当 ``model_name`` 未注册或流式模块不可用时抛出。
        """
        streaming_predictor = self._require_streaming_predictor(model_name)
        for frame in data_stream:
            try:
                pr = streaming_predictor.predict_frame(frame)
            except (ValueError, TypeError, RuntimeError, OSError, AttributeError, KeyError) as exc:
                logger.error(
                    "HybridInferenceEngine: 流式推理 frame 失败 (%s): %s",
                    model_name,
                    exc,
                    exc_info=True,
                )
                self._engine_stats["errors"] += 1
                yield InferenceResult(
                    prediction=None,
                    confidence=0.0,
                    engine_used=EngineType.LNN,
                    model_used=model_name,
                    processing_time_ms=0.0,
                    metadata={
                        "error": str(exc),
                        "streaming": True,
                        "fallback": True,
                    },
                    evidence=[],
                    uncertainty={"error": 1.0},
                )
                continue

            self._engine_stats["streaming_frames_processed"] += 1
            self._engine_stats["successful_inferences"] += 1
            yield self._streaming_result_to_inference(pr, model_name)

    def infer_windowed(
        self,
        model_name: str,
        data_list: list[Any],
        window_size: int | None = None,
        overlap_keyframes: int | None = None,
    ) -> list[InferenceResult]:
        """对超长加工序列执行窗口化推理.

        对应 lingbot-map 的 windowed mode：将序列切分为多个窗口，窗口间
        通过 ``overlap_keyframes`` 个关键帧传递隐状态，避免每次窗口都从零
        初始化。适用于多工序连续切削、跨工序颤振监控等场景。

        Parameters
        ----------
        model_name : str
            已注册的流式预测器名称。
        data_list : List[Any]
            完整序列数据。
        window_size : Optional[int]
            窗口大小。None 时使用 ``StreamingConfig.window_size``。
        overlap_keyframes : Optional[int]
            窗口间重叠关键帧数。None 时使用 ``StreamingConfig.overlap_keyframes``。

        Returns
        -------
        List[InferenceResult]
            完整序列的推理结果列表。
        """
        streaming_predictor = self._require_streaming_predictor(model_name)
        try:
            pr_list = streaming_predictor.predict_windowed(
                data_list=data_list,
                window_size=window_size,
                overlap_keyframes=overlap_keyframes,
            )
        except (ValueError, TypeError, RuntimeError, OSError, AttributeError, KeyError) as exc:
            logger.error(
                "HybridInferenceEngine: 窗口化推理失败 (%s): %s",
                model_name,
                exc,
                exc_info=True,
            )
            self._engine_stats["errors"] += 1
            return [
                InferenceResult(
                    prediction=None,
                    confidence=0.0,
                    engine_used=EngineType.LNN,
                    model_used=model_name,
                    processing_time_ms=0.0,
                    metadata={
                        "error": str(exc),
                        "streaming": True,
                        "windowed": True,
                        "fallback": True,
                    },
                    evidence=[],
                    uncertainty={"error": 1.0},
                )
            ]

        self._engine_stats["streaming_windows_processed"] += 1
        self._engine_stats["streaming_frames_processed"] += len(pr_list)
        self._engine_stats["successful_inferences"] += len(pr_list)
        return [self._streaming_result_to_inference(pr, model_name) for pr in pr_list]

    def reset_streaming(self, model_name: str) -> None:
        """重置指定流式预测器的状态（新工序/新工件开始时调用）.

        清空分页隐状态缓存、关键帧计数、锚点基准、轨迹记忆，使下一次
        推理从干净状态开始。对应 lingbot-map 中"序列边界重置"的语义。

        Parameters
        ----------
        model_name : str
            已注册的流式预测器名称。

        Raises
        ------
        ValueError
            当 ``model_name`` 未注册时抛出。
        """
        streaming_predictor = self._require_streaming_predictor(model_name)
        streaming_predictor.reset()
        logger.info(
            "HybridInferenceEngine: 已重置流式预测器 %r 的状态",
            model_name,
        )

    def _require_streaming_predictor(self, model_name: str) -> Any:
        """获取流式预测器实例，不存在时抛出 ValueError。"""
        if not _HAS_STREAMING:
            raise ValueError("StreamingPredictor 模块不可用，流式推理功能未启用。")
        sp = self._streaming_predictors.get(model_name)
        if sp is None:
            available = list(self._streaming_predictors.keys())
            raise ValueError(
                f"流式预测器 {model_name!r} 未注册。"
                f"已注册: {available or '无'}。"
                "请先调用 register_streaming_predictor 或 build_streaming_predictor。"
            )
        return sp

    def _streaming_result_to_inference(
        self,
        pr: Any,
        model_name: str,
    ) -> InferenceResult:
        """将 :class:`PredictionResult` 包装为 :class:`InferenceResult`。"""
        value = getattr(pr, "value", pr)
        confidence = float(getattr(pr, "confidence", 0.0) or 0.0)
        model_info = getattr(pr, "model_info", {}) or {}
        return InferenceResult(
            prediction=value,
            confidence=confidence,
            engine_used=EngineType.LNN,
            model_used=model_name,
            processing_time_ms=float(getattr(pr, "inference_time", 0.0) or 0.0),
            metadata={
                **model_info,
                "streaming": True,
            },
            evidence=[
                {
                    "engine": "LNN",
                    "model": model_name,
                    "is_keyframe": model_info.get("is_keyframe", False),
                    "keyframe_reason": model_info.get("keyframe_reason"),
                }
            ],
            uncertainty={
                "epistemic": max(0.0, 1.0 - confidence),
                "anchor_drift": model_info.get("anchor_drift", 0.0),
                "trajectory_deviation": model_info.get("trajectory_deviation", 0.0),
            },
        )

    def _run_engine(
        self,
        engine: EngineType,
        model_name: str | None,
        task: TaskInput,
        routing_ms: float,
    ) -> InferenceResult | None:
        """根据引擎类型分发到具体执行器，返回 None 表示不可用。"""
        start_ts = time.perf_counter()
        try:
            if engine == EngineType.LNN:
                prediction, confidence, uncertainty, meta = self._invoke_lnn(model_name, task)
            elif engine == EngineType.RULE:
                prediction, confidence, uncertainty, meta = self._invoke_rule(task)
            elif engine == EngineType.LLM:
                prediction, confidence, uncertainty, meta = self._invoke_llm(task)
            elif engine == EngineType.HYBRID:
                prediction, confidence, uncertainty, meta = self._invoke_hybrid(model_name, task)
            else:
                logger.warning("HybridInferenceEngine: 未知引擎类型 %s", engine)
                return None
        except (ValueError, TypeError, RuntimeError, OSError, KeyError, AttributeError) as exc:
            logger.error(
                "HybridInferenceEngine: 引擎 %s (model=%s) 推理失败: %s",
                engine.value,
                model_name,
                exc,
                exc_info=True,
            )
            self._engine_stats["errors"] += 1
            return None

        processing_ms = routing_ms + (time.perf_counter() - start_ts) * 1000.0
        return InferenceResult(
            prediction=prediction,
            confidence=float(confidence),
            engine_used=engine,
            model_used=model_name,
            processing_time_ms=processing_ms,
            metadata=meta,
            evidence=[
                {
                    "engine": engine.value,
                    "model": model_name,
                    "routing_confidence": task.precision_requirement,
                }
            ],
            uncertainty=uncertainty,
        )

    def _invoke_lnn(self, model_name: str | None, task: TaskInput) -> tuple:
        """调用 LNN 预测器；若未注册则回退到规则引擎。"""
        predictor = self._lnn_predictors.get(model_name or "")
        if predictor is None:
            available = list(self._lnn_predictors.keys())
            if available:
                predictor = self._lnn_predictors[available[0]]
                logger.debug(
                    "HybridInferenceEngine: LNN 模型 %s 未注册，回退到 %s",
                    model_name,
                    available[0],
                )
            else:
                return self._invoke_rule(task, source="lnn_fallback_to_rule")

        try:
            result = predictor.predict(
                input_data=task.input_data,
                return_confidence=True,
            )
        except (ValueError, TypeError, RuntimeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "HybridInferenceEngine: LNN 预测器调用失败，回退到规则: %s",
                exc,
                exc_info=True,
            )
            return self._invoke_rule(task, source="lnn_error_fallback")

        value = getattr(result, "value", result)
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        meta = {
            "model_name": getattr(predictor, "model_name", model_name),
            "device": str(getattr(predictor, "device", "cpu")),
            "inference_time": float(getattr(result, "inference_time", 0.0) or 0.0),
        }
        uncertainty = {
            "epistemic": max(0.0, 1.0 - confidence),
            "source": "lnn_predictor",
        }
        return value, confidence, uncertainty, meta

    def _invoke_rule(self, task: TaskInput, source: str = "rule_engine") -> tuple:
        """规则引擎：基于输入数据的统计特征给出确定性回退预测。

        当无任何模型可用时使用，确保引擎始终返回非 None 预测，
        并显式标注 ``fallback=True`` 以保证可追溯性。
        """
        data = task.input_data
        try:
            if isinstance(data, dict):
                numeric_vals = [float(v) for v in data.values() if isinstance(v, (int, float))]
                prediction = float(np.mean(numeric_vals)) if numeric_vals else 0.0
                std = float(np.std(numeric_vals)) if len(numeric_vals) > 1 else 0.0
            elif isinstance(data, (list, tuple)):
                arr = np.asarray(data, dtype=float)
                prediction = float(np.mean(arr)) if arr.size else 0.0
                std = float(np.std(arr)) if arr.size > 1 else 0.0
            else:
                prediction = 0.0
                std = 0.0
        except (ValueError, TypeError, RuntimeError) as exc:
            logger.warning("HybridInferenceEngine: 规则引擎特征提取失败: %s", exc)
            prediction = 0.0
            std = 1.0

        mean_abs = abs(prediction) if prediction != 0 else 1.0
        confidence = max(0.0, min(1.0, 1.0 - std / mean_abs))
        meta = {
            "fallback": True,
            "source": source,
            "rule": "mean_of_numeric_inputs",
        }
        uncertainty = {
            "epistemic": std,
            "aleatoric": 0.0,
            "source": source,
        }
        return prediction, confidence, uncertainty, meta

    def _invoke_llm(self, task: TaskInput) -> tuple:
        """LLM 引擎：通过自定义模型注册表调用具备 chat/forward 方法的实例。"""
        instance = self._lookup_custom_model(task.task_description, "llm")
        if instance is None:
            return self._invoke_rule(task, source="llm_fallback_to_rule")

        try:
            if hasattr(instance, "chat_completion"):
                output = instance.chat_completion(task.input_data)
            elif callable(instance):
                output = instance(task.input_data)
            else:
                return self._invoke_rule(task, source="llm_no_callable")
        except (ValueError, TypeError, RuntimeError, OSError, AttributeError, KeyError) as exc:
            logger.warning("HybridInferenceEngine: LLM 调用失败，回退到规则: %s", exc)
            return self._invoke_rule(task, source="llm_error_fallback")

        content = output.get("content") if isinstance(output, dict) else output
        confidence = DEFAULT_PRIOR_CONFIDENCE
        meta = {
            "model_type": "llm",
            "output_keys": list(output.keys()) if isinstance(output, dict) else None,
        }
        uncertainty = {"epistemic": 0.4, "source": "llm_default"}
        return content, confidence, uncertainty, meta

    def _invoke_hybrid(self, model_name: str | None, task: TaskInput) -> tuple:
        """混合引擎：先 LNN 再规则融合，给出更稳健的预测。"""
        lnn_value, lnn_conf, lnn_unc, lnn_meta = self._invoke_lnn(model_name, task)
        if lnn_meta.get("fallback"):
            return lnn_value, lnn_conf, lnn_unc, lnn_meta

        rule_value, rule_conf, rule_unc, rule_meta = self._invoke_rule(task, source="hybrid_rule_branch")

        try:
            lnn_f = float(lnn_value) if lnn_value is not None else 0.0
            rule_f = float(rule_value) if rule_value is not None else 0.0
            w_lnn = lnn_conf / (lnn_conf + rule_conf + 1e-6)
            w_rule = rule_conf / (lnn_conf + rule_conf + 1e-6)
            prediction = w_lnn * lnn_f + w_rule * rule_f
            uncertainty = {
                "epistemic": float(
                    np.sqrt(w_lnn * lnn_unc.get("epistemic", 0.0) ** 2 + w_rule * rule_unc.get("epistemic", 0.0) ** 2)
                ),
                "source": "hybrid_weighted",
            }
            confidence = float(w_lnn * lnn_conf + w_rule * rule_conf)
            meta = {
                "hybrid": True,
                "lnn_branch": lnn_meta,
                "rule_branch": rule_meta,
                "weights": {"lnn": w_lnn, "rule": w_rule},
            }
        except (ValueError, TypeError) as exc:
            logger.warning("HybridInferenceEngine: 混合融合失败，使用 LNN 单分支: %s", exc)
            prediction, confidence, uncertainty, meta = (lnn_value, lnn_conf, lnn_unc, lnn_meta)

        return prediction, confidence, uncertainty, meta

    def _lookup_custom_model(self, task_description: str, model_type: str | None) -> Any | None:
        """根据任务描述和模型类型查找自定义模型实例。"""
        if not self._custom_models:
            return None
        lowered = (task_description or "").lower()
        for name, entry in self._custom_models.items():
            if model_type and entry.get("model_type", "").lower() != model_type:
                continue
            if name.lower() in lowered or lowered in name.lower():
                return entry.get("instance")
        for entry in self._custom_models.values():
            if model_type and entry.get("model_type", "").lower() == model_type:
                return entry.get("instance")
        return None

    def _fallback_result(
        self,
        task: TaskInput,
        decision: Any,
        routing_ms: float,
    ) -> InferenceResult:
        """最终回退：当所有引擎都不可用时调用规则引擎保证非 None 输出。"""
        value, confidence, uncertainty, meta = self._invoke_rule(task, source="all_engines_unavailable")
        return InferenceResult(
            prediction=value,
            confidence=confidence,
            engine_used=decision.selected_engine,
            model_used=decision.selected_model,
            processing_time_ms=routing_ms,
            metadata={
                **meta,
                "reasoning": decision.reasoning,
                "input_keys": (list(task.input_data.keys()) if isinstance(task.input_data, dict) else None),
            },
            evidence=[
                {
                    "source": "router",
                    "engine": decision.selected_engine.value,
                    "fallback": True,
                }
            ],
            uncertainty=uncertainty,
        )
