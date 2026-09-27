"""Intermediates Mixin 模块（P0-3-b 重构产物）。

将 ``LNNPredictor.predict_with_intermediates`` 方法拆分为多个私有辅助方法，
通过 Mixin 模式组合回 ``LNNPredictor``。Mixin 类不定义 ``__init__``，
所有状态通过 ``self.`` 在运行时绑定到 ``LNNPredictor`` 实例。

设计要点
--------
- ``self._mc_lock`` (RLock) 临界区保护：hook 注册/移除与模型状态读取
  需与 ``predict_mc_dropout`` 共享锁，避免并发干扰。
- 各辅助方法职责单一：初始化 intermediates、注册 hook、收集隐状态、
  收集门控值、执行前向、构造失败结果。
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


class _IntermediatesMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _compute_confidence: Callable[..., Any]
    _maybe_inverse_transform: Callable[..., Any]
    _postprocess: Callable[..., Any]
    _preprocess: Callable[..., Any]
    _to_tensor: Callable[..., Any]
    model: Callable[..., Any]
    _mc_lock: Any
    device: Any
    model_name: Any

    """``predict_with_intermediates`` 的 Mixin，提供非侵入式中间状态捕获。

    本 Mixin 不定义 ``__init__``，所有实例状态由 ``LNNPredictor.__init__``
    通过 MRO 链继承初始化。
    """

    # 私有辅助方法
    def _init_intermediates_dict(self) -> dict[str, Any]:
        """返回初始化为空字典结构的 intermediates 容器。

        Returns:
            包含 5 个键的 intermediates 字典：
            ``hidden_states``、``gate_values``、``time_constants``、
            ``hidden_shape``、``capture_mode``。
        """
        return {
            "hidden_states": [],
            "gate_values": [],
            "time_constants": [],
            "hidden_shape": [],
            "capture_mode": "disabled",
        }

    def _register_hidden_hooks(self, capture_hidden: bool) -> tuple[list[Any], list[np.ndarray], str]:
        """在模型 ``ltc_cells`` 上注册 forward hook 以捕获隐状态序列。

        Args:
            capture_hidden: 是否启用隐状态捕获。

        Returns:
            ``(hook_handles, captured_hidden, capture_mode)``：
              - ``hook_handles``：已注册的 hook 句柄列表（finally 中需移除）；
              - ``captured_hidden``：将被 hook 在前向过程中原地填充的列表；
              - ``capture_mode``：``"hook"`` 或 ``"disabled"``。
        """
        if not (capture_hidden and HAS_TORCH):
            return ([], [], "disabled")

        hook_handles: list[Any] = []
        captured_hidden: list[np.ndarray] = []

        # 尝试 forward hook 模式：注册到 ltc_cells
        ltc_cells = getattr(self.model, "ltc_cells", None)
        if ltc_cells is None or not isinstance(ltc_cells, (list, tuple)):
            return ([], [], "disabled")

        for cell in ltc_cells:

            def _hook(module, inputs, output, _cell=cell):
                try:
                    if isinstance(output, torch.Tensor):
                        captured_hidden.append(output.detach().cpu().numpy())
                except (RuntimeError, ValueError, TypeError) as hook_exc:
                    # 隐状态捕获失败不应中断推理，但需可追踪（debug 级，不刷屏）
                    logger.debug("LNN 隐状态 hook 捕获失败: %s", hook_exc)

            handle = cell.register_forward_hook(_hook)
            hook_handles.append(handle)

        return (hook_handles, captured_hidden, "hook")

    def _collect_hidden_states(self, captured_hidden: list[np.ndarray], intermediates: dict[str, Any]) -> None:
        """将捕获到的隐状态填充到 intermediates 字典中（原地修改）。

        优先使用 hook 模式捕获的多帧序列；若 hook 未捕获到任何数据，
        降级到属性读取模式（直接读取模型 ``hidden_state``）。

        Args:
            captured_hidden: hook 模式下捕获的隐状态列表。
            intermediates: 待原地填充的 intermediates 字典。
        """
        if captured_hidden:
            # hook 模式：逐层隐状态
            # 取最后一层的输出作为帧序列（[seq, batch, hidden] [seq, hidden]）
            last_layer = captured_hidden[-1]
            if last_layer.ndim == 3:
                # [seq, batch, hidden] [seq, hidden]（batch=1）
                hidden_seq = last_layer[:, 0, :]
            elif last_layer.ndim == 2:
                hidden_seq = last_layer
            else:
                hidden_seq = last_layer.reshape(1, -1)
            intermediates["hidden_states"] = hidden_seq.tolist()
            intermediates["hidden_shape"] = list(hidden_seq.shape)
        else:
            # 降级：属性读取模式
            last_hs = getattr(self.model, "hidden_state", None)
            if last_hs is not None:
                if HAS_TORCH and isinstance(last_hs, torch.Tensor):
                    last_hs = last_hs.detach().cpu().numpy()
                # [num_layers, batch, hidden] [num_layers, hidden]（batch=1）
                if isinstance(last_hs, np.ndarray):
                    if last_hs.ndim == 3:
                        hs_seq = last_hs[:, 0, :]
                    elif last_hs.ndim == 2:
                        hs_seq = last_hs
                    else:
                        hs_seq = last_hs.reshape(1, -1)
                    intermediates["hidden_states"] = hs_seq.tolist()
                    intermediates["hidden_shape"] = list(hs_seq.shape)
                    intermediates["capture_mode"] = "attribute"

    def _collect_gate_dynamics(self, intermediates: dict[str, Any]) -> None:
        """从模型配置中读取时间常数并广播为门控值/时间常数序列。

        若模型暴露 ``config.time_constant``，则计算 ``τ = 1/dt`` 并按
        hidden_dim 与帧数广播。若此前 ``capture_mode`` 为 ``disabled``，
        则升级为 ``attribute`` 表示至少通过属性读取到了部分中间状态。

        Args:
            intermediates: 待原地填充的 intermediates 字典。
        """
        config = getattr(self.model, "config", None)
        dt = getattr(config, "time_constant", None) if config else None
        if dt is None:
            return

        # 广播 dt 到 hidden_dim 维
        hidden_dim = len(intermediates["hidden_states"][0]) if intermediates["hidden_states"] else 1
        gate_values = [float(dt)] * hidden_dim
        time_constants = [1.0 / float(dt) if float(dt) > 0 else 0.0] * hidden_dim
        # 广播到帧数
        n_frames = len(intermediates["hidden_states"]) or 1
        intermediates["gate_values"] = [gate_values] * n_frames
        intermediates["time_constants"] = [time_constants] * n_frames
        if intermediates["capture_mode"] == "disabled":
            intermediates["capture_mode"] = "attribute"

    def _execute_forward_with_capture(self, features: Any, hidden_meta: Any) -> tuple[Any, Any]:
        """执行标准前向传播并完成逆变换/后处理。

        Args:
            features: 预处理后的输入特征。
            hidden_meta: ``_preprocess`` 返回的 hidden 元数据。

        Returns:
            ``(output, processed_output)``：
              - ``output``：模型原始输出；
              - ``processed_output``：经 ``_postprocess`` 与可选的
                ``_maybe_inverse_transform`` 处理后的输出。
        """
        if _HAS_TORCH_MODELS and isinstance(self.model, BaseLNNModel):
            output = self.model.predict(features)
        else:
            features_tensor = self._to_tensor(features)
            if HAS_TORCH:
                with torch.no_grad():
                    output = self.model(features_tensor)
            else:
                output = self.model(features_tensor)

        processed_output = self._postprocess(output, hidden_meta)
        if isinstance(processed_output, np.ndarray):
            processed_output = self._maybe_inverse_transform(processed_output)

        return output, processed_output

    def _build_intermediate_failure_result(
        self,
        intermediates: dict[str, Any],
        error_msg: str,
        start_time: float,
    ) -> "PredictionResult":
        """构造中间状态捕获失败时的降级预测结果。

        Args:
            intermediates: 已初始化（部分填充）的 intermediates 字典。
            error_msg: 异常错误信息。
            start_time: 方法起始 ``time.perf_counter()`` 时间戳。

        Returns:
            ``PredictionResult``，``value=None``、``confidence=0.0``，
            ``model_info`` 中附加 ``intermediates`` 与 ``intermediate_capture_error``。
        """
        from app.ai.lnn.inference.predictor import PredictionResult

        inference_time = (time.perf_counter() - start_time) * 1000
        return PredictionResult(
            value=None,
            confidence=0.0,
            inference_time=inference_time,
            model_info={
                "name": self.model_name,
                "device": str(self.device),
                "intermediates": intermediates,
                "intermediate_capture_error": error_msg,
            },
        )

    # 公开方法（保持原签名）
    def predict_with_intermediates(
        self,
        input_data: Any,
        *,
        capture_hidden: bool = True,
        capture_gates: bool = True,
    ) -> PredictionResult:
        """非侵入式推理并捕获中间状态（隐状态 / 门控值 / 时间常数）.

        对应 ADR-016（可解释性可视化）。本方法不修改主推理路径，
        仅在标准前向后附加读取模型内部状态，供可解释性服务消费：
        - 隐状态序列 → ``HiddenStateExplanation``（降维投影可视化）
        - 门控值 / 时间常数 → ``GateDynamicsExplanation``（门控动力学曲线）

        捕获策略
        --------
        1. **forward hook 模式**（首选）：若模型为 torch LTC 模型且暴露
           ``ltc_cells`` 属性，注册 forward hook 捕获每个 cell 的输出，
           得到逐层逐帧的隐状态序列。同时从 ``config.time_constant``
           读取 ``dt``，计算 ``τ = 1/dt`` 作为时间常数。
        2. **属性读取模式**（降级）：若模型暴露 ``hidden_state`` 属性但
           无 ``ltc_cells``，直接读取前向后的 ``hidden_state``（单帧快照）。
        3. **禁用模式**：torch 不可用或模型不暴露任何中间状态时，
           ``intermediates`` 返回空字典，仅保证主推理结果正确。

        线程安全
        --------
        使用 ``_mc_lock`` 保护（与 ``predict_mc_dropout`` 共享），避免
        并发请求的 hook 注册/移除相互干扰。hook 句柄在 finally 块中
        确保移除，防止泄漏。

        Parameters
        ----------
        input_data : Any
            输入数据（与 ``predict`` 接口一致）。
        capture_hidden : bool
            是否捕获隐状态序列（默认 True）。
        capture_gates : bool
            是否捕获门控值与时间常数（默认 True）。

        Returns
        -------
        PredictionResult
            标准预测结果，``model_info`` 中附加 ``intermediates`` 字段：
            - ``hidden_states``: list[list[float]] 隐状态 [N, hidden_dim]
            - ``gate_values``: list[list[float]] 门控值 [N, hidden_dim]
            - ``time_constants``: list[list[float]] 时间常数 τ [N, hidden_dim]
            - ``hidden_shape``: list[int] 原始隐状态形状
            - ``capture_mode``: str 捕获模式（``hook`` / ``attribute`` / ``disabled``）

        Notes
        -----
        - 本方法 **不更新** ``_stats`` 统计，避免与 ``predict`` 双重计数。
        - 捕获失败时记录 warning 并返回空 intermediates，不抛异常。
        """
        from app.ai.lnn.inference.predictor import PredictionResult

        start_time = time.perf_counter()

        # _mc_lock 保护 hook 注册/移除与模型状态读取，避免并发干扰
        with self._mc_lock:
            intermediates = self._init_intermediates_dict()

            try:
                features, hidden_meta = self._preprocess(input_data)
            except (ValueError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "predict_with_intermediates: 预处理失败，返回空 intermediates: %s",
                    exc,
                    exc_info=True,
                )
                return self._build_intermediate_failure_result(intermediates, str(exc), start_time)

            # 捕获中间状态
            hook_handles, captured_hidden, capture_mode = self._register_hidden_hooks(capture_hidden)
            intermediates["capture_mode"] = capture_mode

            try:
                output, processed_output = self._execute_forward_with_capture(features, hidden_meta)

                if capture_hidden:
                    self._collect_hidden_states(captured_hidden, intermediates)
                if capture_gates:
                    self._collect_gate_dynamics(intermediates)
            except (ValueError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "predict_with_intermediates: 中间状态捕获失败: %s",
                    exc,
                    exc_info=True,
                )
                # 主推理已失败，返回错误结果
                return self._build_intermediate_failure_result(intermediates, str(exc), start_time)
            finally:
                # 确保 hook 移除，防止泄漏
                for handle in hook_handles:
                    try:
                        handle.remove()
                    except (RuntimeError, ValueError, AttributeError) as rm_exc:
                        # hook 重复移除无害，但记录 debug 便于排查句柄泄漏
                        logger.debug("LNN hook 移除失败: %s", rm_exc)

            inference_time = (time.perf_counter() - start_time) * 1000
            confidence = self._compute_confidence(output) if output is not None else 0.0

            return PredictionResult(
                value=processed_output,
                confidence=confidence,
                inference_time=inference_time,
                model_info={
                    "name": self.model_name,
                    "device": str(self.device),
                    "intermediates": intermediates,
                },
            )
