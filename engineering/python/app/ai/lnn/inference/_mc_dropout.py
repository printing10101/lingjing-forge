"""MC Dropout Mixin 模块（P0-3-b 重构产物）。

将 ``LNNPredictor.predict_mc_dropout`` 方法拆分为多个私有辅助方法，
通过 Mixin 模式组合回 ``LNNPredictor``。Mixin 类不定义 ``__init__``，
所有状态通过 ``self.`` 在运行时绑定到 ``LNNPredictor`` 实例。

设计要点
--------
- ``self._mc_lock`` (RLock) 临界区保护：``predict_mc_dropout`` 在切换
  ``model.train(True)/eval()`` 期间必须独占访问模型状态。
- 各辅助方法无副作用地访问/修改 ``self.model`` 状态，主流程负责编排。
- ``PredictionResult`` 通过方法内延迟导入引用，避免与 ``predictor.py``
  产生循环导入。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

import numpy as np

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from app.ai.lnn.models.base_lnn import BaseLNNModel

    _HAS_TORCH_MODELS = True
except ImportError:
    BaseLNNModel = None
    _HAS_TORCH_MODELS = False


if TYPE_CHECKING:
    from app.ai.lnn.inference.predictor import PredictionResult

logger = logging.getLogger(__name__)


class _MCDropoutMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_memory_usage_mb: Callable[..., Any]
    _maybe_inverse_transform: Callable[..., Any]
    _postprocess: Callable[..., Any]
    _preprocess: Callable[..., Any]
    _to_tensor: Callable[..., Any]
    _update_stats: Callable[..., Any]
    _write_trace: Callable[..., Any]
    model: Any
    predict: Callable[..., Any]
    _mc_lock: Any
    device: Any
    model_name: Any

    """``predict_mc_dropout`` 的 Mixin，提供 Monte Carlo Dropout 不确定性量化。

    本 Mixin 不定义 ``__init__``，所有实例状态由 ``LNNPredictor.__init__``
    通过 MRO 链继承初始化。
    """

    # 私有辅助方法
    def _mc_fallback_no_torch(self, input_data: Any) -> "PredictionResult":
        """非 torch 环境下的降级回退路径。

        调用标准 ``predict`` 并补全 MC Dropout 元信息（单样本、零方差）。

        Args:
            input_data: 原始输入数据。

        Returns:
            ``PredictionResult``，``model_info`` 中包含
            ``mc_n_samples=1``、``mc_std=0.0``。
        """
        from app.ai.lnn.inference.predictor import PredictionResult

        result = self.predict(input_data, return_confidence=True)
        if isinstance(result, PredictionResult):
            if result.model_info is not None:
                result.model_info.setdefault("mc_n_samples", 1)
                result.model_info.setdefault("mc_std", 0.0)
            return result
        return PredictionResult(
            value=result,
            confidence=0.0,
            inference_time=0.0,
            model_info={"mc_n_samples": 1, "mc_std": 0.0},
        )

    def _mc_setup_dropout_and_train_mode(self, dropout_override: float | None) -> tuple[Any, Any]:
        """配置临时 dropout 并切换模型到训练模式以激活 dropout 层。

        Args:
            dropout_override: 可选的临时 dropout 概率覆盖值。

        Returns:
            ``(original_dropout, was_training)``：
              - ``original_dropout``：原始 ``dropout_rate``（不存在则为 None），
                用于 ``finally`` 块恢复。
              - ``was_training``：进入方法前模型是否处于训练模式；
                ``None`` 表示模型不支持 ``train()`` 调用，恢复时跳过模式切换。
        """
        original_dropout = getattr(self.model, "dropout_rate", None)
        if dropout_override is not None and hasattr(self.model, "dropout_rate"):
            try:
                self.model.dropout_rate = float(dropout_override)
            except (AttributeError, TypeError, ValueError) as exc:
                logger.debug("predict_mc_dropout: 无法覆盖 dropout: %s", exc)

        was_training = getattr(self.model, "training", False)
        try:
            train_fn = getattr(self.model, "train", None)
            if callable(train_fn):
                train_fn(True)
            else:
                was_training = None
        except (RuntimeError, AttributeError) as exc:
            logger.debug("predict_mc_dropout: 切换 train 模式失败: %s", exc)
            was_training = None

        return original_dropout, was_training

    def _mc_run_forward_samples(self, features: Any, n_samples: int) -> list[np.ndarray]:
        """执行 ``n_samples`` 次前向传播并收集样本输出。

        若模型为 :class:`BaseLNNModel`，调用其 ``predict``；否则使用
        ``torch.no_grad()`` 包装直接调用 ``__call__``（不禁用 dropout，
        以保证 MC Dropout 生效）。

        Args:
            features: 预处理后的输入特征。
            n_samples: 前向传播次数。

        Returns:
            样本列表，每个元素为 ``np.ndarray``。
        """
        samples: list[np.ndarray] = []
        for _ in range(n_samples):
            if _HAS_TORCH_MODELS and isinstance(self.model, BaseLNNModel):
                output = self.model.predict(features)
            else:
                features_tensor = self._to_tensor(features)
                # 修复 P1: inference_mode 会禁用 dropout，导致 n_samples 次前向
                # 结果完全相同、std=0，MC Dropout 失效。改用 no_grad（不禁用 dropout），
                # 配合上方已设置的 model.train(True) 使 dropout 层保持激活。
                if HAS_TORCH:
                    with torch.no_grad():
                        output = self.model(features_tensor)
                    if isinstance(output, torch.Tensor):
                        output = output.detach().cpu().numpy()
                else:
                    output = self.model(features_tensor)
            samples.append(np.asarray(output, dtype=float))
        return samples

    def _mc_restore_model_state(self, original_dropout: Any, was_training: Any) -> None:
        """恢复模型原始训练/推理模式与 dropout 概率。

        Args:
            original_dropout: ``_mc_setup_dropout_and_train_mode`` 返回的原始
                dropout 值；``None`` 表示跳过恢复。
            was_training: ``_mc_setup_dropout_and_train_mode`` 返回的原始训练
                模式标记；``None`` 表示跳过模式恢复。
        """
        if was_training is not None:
            eval_fn = getattr(self.model, "eval", None)
            if callable(eval_fn):
                try:
                    if was_training:
                        self.model.train()
                    else:
                        self.model.eval()
                except (RuntimeError, AttributeError) as restore_err:
                    # 训练/推理模式恢复失败不阻塞预测结果返回（已得到 samples），
                    # 但记录便于排查：模型状态可能与预期不一致，影响后续推理
                    logger.debug(
                        "Failed to restore model train/eval mode: %s",
                        restore_err,
                        exc_info=True,
                    )
        if original_dropout is not None and hasattr(self.model, "dropout_rate"):
            try:
                self.model.dropout_rate = original_dropout
            except (AttributeError, TypeError, ValueError) as dropout_err:
                # dropout_rate 恢复失败同样不阻塞，但需记录：后续推理可能
                # 仍处于 MC dropout 模式，导致确定性预测出现非确定性
                logger.debug(
                    "Failed to restore original dropout_rate: %s",
                    dropout_err,
                    exc_info=True,
                )

    def _mc_compute_statistics(
        self,
        samples: list[np.ndarray],
        hidden: Any,
        features: Any,
        inference_time: float,
        n_samples: int,
    ) -> "PredictionResult":
        """根据 MC 样本计算均值、标准差、置信度并构造预测结果。

        Args:
            samples: ``_mc_run_forward_samples`` 返回的样本列表。
            hidden: ``_preprocess`` 返回的 hidden 元数据，供 ``_postprocess`` 使用。
            features: 预处理后的输入特征，用于 trace 记录。
            inference_time: 推理耗时（毫秒）。
            n_samples: 实际使用的样本数（用于填充 ``model_info``）。

        Returns:
            ``PredictionResult``，``value`` 为样本均值，``model_info`` 中
            包含 ``mc_n_samples``、``mc_std``、``mc_mean`` 等字段。
        """
        from app.ai.lnn.inference.predictor import PredictionResult

        try:
            stacked = np.stack(samples, axis=0)
            mean = np.mean(stacked, axis=0)
            std = np.std(stacked, axis=0)
        except (ValueError, TypeError) as exc:
            logger.warning("predict_mc_dropout: 样本堆叠失败，回退到首样本: %s", exc)
            mean = samples[0] if samples else np.array(0.0)
            std = np.zeros_like(mean)

        mean_value = self._maybe_inverse_transform(mean)
        processed = self._postprocess(mean_value, hidden)

        scalar_mean = float(np.mean(processed)) if isinstance(processed, np.ndarray) else float(processed)
        scalar_std = float(np.mean(std)) if std.size else 0.0

        mean_abs = abs(scalar_mean) if scalar_mean != 0 else 1.0
        confidence = max(0.0, min(1.0, 1.0 - scalar_std / mean_abs))

        mem_mb = self._get_memory_usage_mb()
        self._update_stats(inference_time, mem_mb)
        self._write_trace(
            inference_time,
            features.shape if hasattr(features, "shape") else (1,),
            success=True,
        )

        return PredictionResult(
            value=processed,
            confidence=confidence,
            inference_time=inference_time,
            model_info={
                "name": self.model_name,
                "device": str(self.device),
                "mc_n_samples": n_samples,
                "mc_std": scalar_std,
                "mc_mean": scalar_mean,
                "uncertainty_method": "mc_dropout",
            },
        )

    # 公开方法（保持原签名）
    def predict_mc_dropout(
        self,
        input_data: Any,
        n_samples: int = 30,
        dropout_override: float | None = None,
    ) -> "PredictionResult":
        """Monte Carlo Dropout 不确定性量化（Bayesian LNN 近似）。

        通过在推理阶段保持 dropout 激活并执行多次前向传播，得到预测分布的
        样本集合，进而计算认知不确定性（epistemic uncertainty）。

        Args:
            input_data: 输入数据，与 :meth:`predict` 相同。
            n_samples: 前向传播次数，建议 30~100。低于 1 视为 1。
            dropout_override: 可选，临时覆盖 dropout 概率。None 时使用模型
                当前配置。

        Returns:
            PredictionResult，其中：
                - ``value`` 为样本均值；
                - ``confidence`` 为 ``1 - std/|mean|``（裁剪到 [0,1]）；
                - ``model_info["mc_std"]``、``mc_samples``、``mc_n_samples``
                  记录真实标准差与样本数，供上层 API 透传。
        """
        # 临界区：整个 predict_mc_dropout 方法体在锁保护下执行，
        # 确保 model.train(True)/eval() 模式切换和恢复是原子操作。
        # 并发调用时，一个请求的 eval() 会关闭另一个请求的 dropout，
        # 导致 MC Dropout 失效。RLock 可重入，不影响正常推理性能。
        with self._mc_lock:
            if n_samples < 1:
                n_samples = 1

            features, hidden = self._preprocess(input_data)

            if not HAS_TORCH:
                return self._mc_fallback_no_torch(input_data)

            original_dropout, was_training = self._mc_setup_dropout_and_train_mode(dropout_override)

            start_ts = time.perf_counter()
            try:
                samples = self._mc_run_forward_samples(features, n_samples)
            finally:
                self._mc_restore_model_state(original_dropout, was_training)

            inference_time = (time.perf_counter() - start_ts) * 1000.0
            return self._mc_compute_statistics(samples, hidden, features, inference_time, n_samples)
