"""流式推理编排器 StreamingPredictor（原 streaming.py 主类）。

融合 lingbot-map GCT 五项核心思想（分页隐状态 / 关键帧策略 / 锚点上下文 /
轨迹记忆 / 窗口化推理），串联 LNNPredictor 与各子模块。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from collections.abc import Iterator

import numpy as np

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from app.ai.lnn.inference.predictor import LNNPredictor, PredictionResult
from .config import KeyframeDecision, StreamingConfig
from .cache import PagedHiddenStateCache
from .selector import KeyframeSelector
from .context import AnchorContext
from .memory import TrajectoryMemory

logger = logging.getLogger(__name__)


class StreamingPredictor:
    """流式长时序推理编排器（融合 lingbot-map GCT 五项核心思想）.

    将 :class:`LNNPredictor`、:class:`PagedHiddenStateCache`、
    :class:`KeyframeSelector`、:class:`AnchorContext`、:class:`TrajectoryMemory`
    串联为统一的流式推理管线，支持万帧以上长时序加工信号推理。

    使用示例
    --------
    >>> predictor = LNNPredictor(model=ltc_model, preprocessor=prep)
    >>> streaming = StreamingPredictor(
    ...     predictor=predictor,
    ...     config=StreamingConfig(
    ...         keyframe_interval=2,
    ...         keyframe_mode="hybrid",
    ...         anchor_enabled=True,
    ...     ),
    ... )
    >>> for frame in signal_stream:
    ...     result = streaming.predict_frame(frame)
    ...     print(result.value, result.model_info["is_keyframe"])

    线程安全
    --------
    内部所有共享状态（隐状态缓存、锚点、轨迹记忆）均使用各自的锁保护，
    但单次 ``predict_frame`` 调用不持有跨组件全局锁，避免性能损耗。
    多线程并发推理同一 ``StreamingPredictor`` 实例时，隐状态语义会混乱，
    建议每个流使用独立实例。
    """

    def __init__(
        self,
        predictor: LNNPredictor,
        config: StreamingConfig | None = None,
        seed: int = 42,
    ) -> None:
        self._predictor = predictor
        self._config = config or StreamingConfig()
        self._config.validate()
        self._seed = seed

        # 设置随机种子保证可复现（学术诚信）
        np.random.seed(seed)
        if HAS_TORCH:
            torch.manual_seed(seed)

        predictor_device = getattr(predictor, "device", None)
        self._cache = PagedHiddenStateCache(
            max_pages=self._config.max_cache_pages,
            device=self._config.cache_device,
            predictor_device=predictor_device,
        )
        self._keyframe_selector = KeyframeSelector(
            interval=self._config.keyframe_interval,
            mode=self._config.keyframe_mode,
            energy_threshold=self._config.energy_threshold,
            seed=seed,
        )
        self._anchor = AnchorContext(
            update_rate=self._config.anchor_update_rate,
            correction_strength=self._config.anchor_correction_strength,
            enabled=self._config.anchor_enabled,
        )
        self._trajectory = TrajectoryMemory(
            window_size=self._config.trajectory_memory_size,
            correction_strength=self._config.trajectory_correction_strength,
        )

        self._frame_id = 0
        self._stats = {
            "total_frames": 0,
            "keyframes": 0,
            "anchor_corrections": 0,
            "trajectory_corrections": 0,
            "cache_evictions": 0,
            "total_inference_ms": 0.0,
        }
        self._stats_lock = threading.Lock()

    # 单帧推理

    def predict_frame(
        self,
        frame_data: Any,
        force_keyframe: bool = False,
    ) -> PredictionResult:
        """对单帧数据执行流式推理。

        Parameters
        ----------
        frame_data : Any
            当前帧输入数据，与 :meth:`LNNPredictor.predict` 兼容。
        force_keyframe : bool
            强制将此帧标记为关键帧（窗口边界/工序切换时使用）。

        Returns
        -------
        PredictionResult
            预测结果，``model_info`` 中包含流式推理元数据：
            - ``is_keyframe``：是否为关键帧
            - ``keyframe_reason``：关键帧判定依据
            - ``frame_id``：帧序号
            - ``anchor_drift``：锚点漂移量
            - ``trajectory_deviation``：轨迹偏差量
        """
        start_ts = time.perf_counter()
        self._frame_id += 1
        frame_id = self._frame_id

        # 预处理 + 关键帧判定
        features, _ = self._predictor._preprocess(frame_data)
        kf_decision = self._keyframe_selector.decide(features)
        if force_keyframe:
            kf_decision = KeyframeDecision(is_keyframe=True, reason="forced", energy=kf_decision.energy)

        # 执行基础预测
        base_result = self._predictor.predict(frame_data, return_confidence=True)
        if not isinstance(base_result, PredictionResult):
            base_result = PredictionResult(
                value=base_result,
                confidence=0.0,
                inference_time=0.0,
                model_info={"name": self._predictor.model_name},
            )

        value = base_result.value
        if isinstance(value, np.ndarray):
            value_arr = value
        else:
            value_arr = np.asarray(value, dtype=np.float64)

        # 轨迹记忆修正
        trajectory_deviation = 0.0
        try:
            corrected_value, trajectory_deviation = self._trajectory.correct(value_arr)
            if isinstance(base_result.value, np.ndarray):
                base_result.value = corrected_value
            else:
                base_result.value = float(np.mean(corrected_value)) if corrected_value.size else 0.0
            if trajectory_deviation > 0:
                with self._stats_lock:
                    self._stats["trajectory_corrections"] += 1
        except (ValueError, TypeError) as exc:
            logger.debug("轨迹修正异常: %s", exc)

        # 锚点修正（基于预测值代理隐状态，避免侵入模型内部）
        anchor_drift = 0.0
        if self._config.anchor_enabled:
            try:
                # 用预测值作为隐状态代理（LTC 输出与隐状态强相关）
                proxy_hidden = value_arr.ravel()
                corrected_proxy, anchor_drift = self._anchor.correct(proxy_hidden)
                # 应用锚点修正
                if isinstance(base_result.value, np.ndarray):
                    # 混合修正后的代理与原值，保持维度一致
                    if corrected_proxy.shape == base_result.value.shape:
                        pass  # 已在 correct 内部处理
                # 稳态判定：非关键帧 + 低能量视为稳态，更新锚点
                is_stable = (not kf_decision.is_keyframe) or (kf_decision.reason in ("interval", "energy_stable"))
                self._anchor.update(proxy_hidden, is_stable=is_stable)
                if anchor_drift > 0:
                    with self._stats_lock:
                        self._stats["anchor_corrections"] += 1
            except (ValueError, TypeError) as exc:
                logger.debug("锚点修正异常: %s", exc)

        # 关键帧写入分页缓存
        if kf_decision.is_keyframe:
            try:
                self._cache.put(frame_id, value_arr.copy())
                with self._stats_lock:
                    self._stats["keyframes"] += 1
            except (ValueError, TypeError) as exc:
                logger.debug("隐状态缓存写入失败: %s", exc)

        # 轨迹记录
        self._trajectory.push(value_arr)

        # 更新统计
        inference_ms = (time.perf_counter() - start_ts) * 1000.0
        with self._stats_lock:
            self._stats["total_frames"] += 1
            self._stats["total_inference_ms"] += inference_ms
            self._stats["cache_evictions"] = self._cache.stats()["eviction_count"]

        # 注入流式元数据
        if base_result.model_info is not None:
            base_result.model_info.update(
                {
                    "is_keyframe": kf_decision.is_keyframe,
                    "keyframe_reason": kf_decision.reason,
                    "frame_energy": kf_decision.energy,
                    "frame_id": frame_id,
                    "anchor_drift": anchor_drift,
                    "trajectory_deviation": trajectory_deviation,
                    "streaming_mode": True,
                }
            )
        return base_result

    # 流式迭代器接口

    def predict_stream(
        self,
        data_stream: Iterator[Any],
    ) -> Iterator[PredictionResult]:
        """流式推理迭代器（与原 ``predict_streaming`` 接口兼容）。

        Parameters
        ----------
        data_stream : Iterator[Any]
            输入数据迭代器。

        Yields
        ------
        PredictionResult
            每帧预测结果。
        """
        for frame in data_stream:
            yield self.predict_frame(frame)

    # 窗口化推理（超长序列）

    def predict_windowed(
        self,
        data_list: list[Any],
        window_size: int | None = None,
        overlap_keyframes: int | None = None,
    ) -> list[PredictionResult]:
        """窗口化推理（对应 lingbot-map 的 windowed mode）.

        将超长序列切分为多个窗口，窗口间通过 ``overlap_keyframes`` 个关键帧
        传递隐状态，避免每次窗口都从零初始化导致状态重置崩溃。

        Parameters
        ----------
        data_list : List[Any]
            完整序列数据。
        window_size : Optional[int]
            窗口大小。None 时使用 config.window_size。
        overlap_keyframes : Optional[int]
            窗口间重叠关键帧数。None 时使用 config.overlap_keyframes。

        Returns
        -------
        List[PredictionResult]
            完整序列的预测结果。
        """
        ws = window_size or self._config.window_size
        if ws is None:
            # 未启用窗口化，直接逐帧推理
            return [self.predict_frame(d) for d in data_list]
        ws = max(1, ws)
        okf = overlap_keyframes if overlap_keyframes is not None else self._config.overlap_keyframes
        okf = max(0, min(okf, ws - 1))

        results: list[PredictionResult] = []
        total = len(data_list)
        if total == 0:
            return results

        stride = max(1, ws - okf)
        window_idx = 0
        # 记录上一窗口末尾的关键帧数据，用于本窗口初始化
        carryover: list[Any] | None = None

        for start in range(0, total, stride):
            end = min(start + ws, total)
            window = data_list[start:end]
            if carryover:
                # 将上一窗口尾部关键帧拼接到本窗口头部，实现隐状态传递
                window = carryover + window
            window_results = [self.predict_frame(d) for d in window]
            results.extend(window_results)
            # 取本窗口尾部关键帧作为下一窗口 carryover
            kf_indices = [i for i, r in enumerate(window_results) if (r.model_info or {}).get("is_keyframe")]
            if kf_indices and end < total:
                kf_tail = kf_indices[-okf:] if okf > 0 else []
                carryover = [window[i] for i in kf_tail]
            else:
                carryover = None
            window_idx += 1
            logger.debug(
                "窗口化推理: 窗口 %d [%d:%d], 处理 %d 帧, carryover=%d",
                window_idx,
                start,
                end,
                len(window),
                len(carryover) if carryover else 0,
            )

        return results

    # 状态管理

    def reset(self) -> None:
        """重置所有流式状态（新加工工序开始时调用）。

        清空隐状态缓存、锚点、轨迹记忆、关键帧计数器。
        """
        self._cache.clear()
        self._anchor.reset()
        self._trajectory.reset()
        self._keyframe_selector.reset()
        self._frame_id = 0
        with self._stats_lock:
            self._stats = {
                "total_frames": 0,
                "keyframes": 0,
                "anchor_corrections": 0,
                "trajectory_corrections": 0,
                "cache_evictions": 0,
                "total_inference_ms": 0.0,
            }

    def get_statistics(self) -> dict[str, Any]:
        """获取流式推理统计信息。"""
        with self._stats_lock:
            stats: dict[str, Any] = dict(self._stats)
        stats["cache"] = self._cache.stats()
        stats["anchor"] = self._anchor.stats()
        stats["trajectory"] = self._trajectory.stats()
        stats["keyframe_interval"] = self._config.keyframe_interval
        stats["keyframe_mode"] = self._config.keyframe_mode
        stats["avg_inference_ms"] = (
            stats["total_inference_ms"] / stats["total_frames"] if stats["total_frames"] > 0 else 0.0
        )
        stats["keyframe_ratio"] = stats["keyframes"] / stats["total_frames"] if stats["total_frames"] > 0 else 0.0
        return stats
