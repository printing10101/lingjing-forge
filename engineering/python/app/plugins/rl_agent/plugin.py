"""RL agent 插件：实现 ``rl_act`` 任务类型.

对应 ADR-017 第 1.2 节。``RLAgentPlugin`` 实现 ``TaskHandler`` 协议，
注册任务类型 ``rl_act``，由工作流编排器调度。

任务输入输出契约
----------------
- 输入：
    - ``current_state`` (Artifact, type=metrics): 当前加工状态（颤振概率/磨损/质量）
    - ``prev_action`` (Artifact, type=metrics, 可选): 上一次合法动作（用于变化率约束）

- 输出：
    - ``action`` (Artifact, type=metrics): 安全过滤后的切削参数调整量
    - ``safety_result`` (Artifact, type=metrics): 安全过滤结果（违反/回退/裁剪信息）
    - ``value_estimate`` (Artifact, type=metrics): 状态价值估计（来自 Critic）

- 指标：
    - ``decision_time_ms``: 决策耗时
    - ``safety_violated``: 是否发生安全违反（0/1）
    - ``fallback_used``: 是否使用回退动作（0/1）

工程现实约束
------------
- v1 仅离线 RL：策略权重来自离线训练，本插件仅做前向推理 + 安全过滤
- SafetyShield 是硬约束层，不可被策略覆盖；任何学习到的策略输出都先经 SafetyShield
- 不直接接 CNC 控制器，输出动作仅供 CAM 验证层与决策日志参考
- 物理执行需"持证操作员 + 导师签字 + 保险"，本插件不涉及

线程安全
--------
- 策略/值网络推理在 _infer_lock 保护下串行（避免 NumPy 回退模式下的状态污染）
- SafetyShield.filter 本身线程安全（内部有锁）
- 策略缓存 _policy_cache 使用独立锁保护
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

try:
    import torch
except ImportError:  # pragma: no cover - 无 torch 时仅影响推理路径
    torch = None

from app.contracts.task import Artifact, TaskContext, TaskResult, TaskStatus
from app.plugins.rl_agent.policy import PolicyConfig, PolicyNet
from app.plugins.rl_agent.safety_shield import (
    SafetyConstraints,
    SafetyShield,
)
from app.plugins.rl_agent.value import ValueConfig, ValueNet

logger = logging.getLogger(__name__)


class RLAgentPlugin:
    """RL agent 插件：实现 ``rl_act`` 任务处理器.

    实现 ``TaskHandler`` 协议（结构化子类型，无需继承）。
    由 ``PluginLifecycleManager`` 在插件 ``on_load`` 时注册到 ``ITaskRegistry``。

    生命周期
    --------
    1. 插件加载时，``RLAgentPlugin()`` 实例化
    2. ``register(registry)`` 注册 ``rl_act`` 任务类型
    3. 工作流编排器调度时调用 ``execute(ctx)``
    4. 插件卸载时，``unregister(registry)`` 注销任务类型

    组合关系
    --------
    - ``PolicyNet``：PPO Actor，输出动作均值 + 对数标准差 + 采样动作
    - ``ValueNet``：PPO Critic，输出状态价值（可选，用于可解释性）
    - ``SafetyShield``：硬约束过滤层，强制过滤违反物理/工艺约束的动作
    """

    TASK_TYPE = "rl_act"

    def __init__(
        self,
        policy_config: PolicyConfig | None = None,
        value_config: ValueConfig | None = None,
        safety_constraints: SafetyConstraints | None = None,
        safety_strict: bool = True,
    ) -> None:
        self._policy_config = policy_config or PolicyConfig()
        self._value_config = value_config or ValueConfig(
            state_dim=self._policy_config.state_dim,
            hidden_dim=self._policy_config.hidden_dim,
            seed=self._policy_config.seed,
        )
        self._safety_constraints = safety_constraints or SafetyConstraints()
        self._safety_strict = safety_strict

        # 网络实例（延迟加载，按 model_uri 缓存）
        self._policy_cache: dict[str, PolicyNet] = {}
        self._value_cache: dict[str, ValueNet] = {}
        self._cache_lock = threading.Lock()

        # SafetyShield 实例（约束配置固定，可共享）
        self._shield = SafetyShield(
            constraints=self._safety_constraints,
            strict=self._safety_strict,
        )

        # 推理串行锁：NumPy 回退模式下避免随机状态污染
        self._infer_lock = threading.Lock()

        # 记录最后一次合法动作（跨任务调用维持变化率约束的连续性）
        self._last_action: np.ndarray | None = None
        self._last_action_lock = threading.Lock()

    # TaskHandler 协议实现

    def name(self) -> str:
        """任务类型名称."""
        return self.TASK_TYPE

    def description(self) -> str:
        """任务类型描述."""
        return (
            "强化学习决策：基于当前加工状态输出切削参数调整动作，"
            "经 SafetyShield 硬约束过滤后返回安全动作。"
            "v1 仅支持离线 RL（策略权重来自离线训练）。"
        )

    def input_schema(self) -> dict[str, Any]:
        """输入 schema（JSON Schema 格式）."""
        return {
            "type": "object",
            "properties": {
                "current_state": {
                    "type": "object",
                    "description": "当前加工状态（含颤振概率/磨损/质量等特征）",
                    "properties": {
                        "uri": {"type": "string"},
                        "shape": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "状态向量形状 [state_dim]",
                        },
                    },
                    "required": ["uri"],
                },
                "prev_action": {
                    "type": "object",
                    "description": "上一次合法动作（可选，用于变化率约束）",
                    "properties": {
                        "uri": {"type": "string"},
                        "shape": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                },
                "model_uri": {
                    "type": "string",
                    "description": "策略模型 URI",
                    "default": "model://rl_agent/1.0.0",
                },
            },
            "required": ["current_state"],
        }

    def output_schema(self) -> dict[str, Any]:
        """输出 schema."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "object",
                    "description": "安全过滤后的切削参数调整量 [action_dim]",
                    "properties": {
                        "uri": {"type": "string"},
                        "shape": {"type": "array", "items": {"type": "integer"}},
                    },
                },
                "safety_result": {
                    "type": "object",
                    "description": "安全过滤结果（违反/回退/裁剪信息）",
                    "properties": {
                        "uri": {"type": "string"},
                    },
                },
                "value_estimate": {
                    "type": "object",
                    "description": "状态价值估计（标量）",
                    "properties": {
                        "uri": {"type": "string"},
                    },
                },
            },
            "required": ["action", "safety_result"],
        }

    async def execute(self, ctx: TaskContext) -> TaskResult:
        """执行 RL 决策.

        Args:
            ctx: 任务上下文，含输入 Artifact 与 config.

        Returns
        -------
        TaskResult
            任务执行结果。
        """
        start_time = time.perf_counter()
        try:
            # 1. 解析输入
            current_state = self._load_artifact_data(ctx.inputs.get("current_state"))
            prev_action = self._load_artifact_data(ctx.inputs.get("prev_action"))
            model_uri = ctx.config.get("model_uri", "model://rl_agent/1.0.0")

            if current_state is None:
                return TaskResult(
                    status=TaskStatus.FAILED,
                    error="缺少输入: current_state",
                    error_code="MISSING_INPUT",
                )

            # 2. 获取或加载策略/值网络（带缓存）
            policy_net = self._get_or_load_policy(model_uri)
            value_net = self._get_or_load_value(model_uri)

            # 3. 策略前向 + 安全过滤
            with self._infer_lock:
                # numpy Tensor 转换（PolicyNet/ValueNet 均为 torch 网络）
                if isinstance(current_state, np.ndarray):
                    state_tensor = torch.from_numpy(current_state).float()
                else:
                    state_tensor = current_state
                policy_out = policy_net(state_tensor)
                raw_action = self._extract_action(policy_out)
                value_out = value_net(state_tensor)
                value_scalar = self._extract_value(value_out)

            # 解析 prev_action：优先使用输入，否则用内部缓存
            ref_action = prev_action if prev_action is not None else self._last_action
            safe_action, safety_result = self._shield.filter(raw_action, prev_action=ref_action)

            # 更新内部缓存的最后一次合法动作
            with self._last_action_lock:
                self._last_action = safe_action.copy()

            decision_time_ms = (time.perf_counter() - start_time) * 1000

            # 4. 构造输出 Artifact
            action_artifact = Artifact(
                name="action",
                type="metrics",
                uri=f"metrics://{ctx.job_id}/action",
                metadata={
                    "values": safe_action.tolist(),
                    "shape": list(safe_action.shape),
                    "labels": [
                        "spindle_speed_delta",
                        "feed_rate_delta",
                        "depth_of_cut_delta",
                        "width_of_cut_delta",
                    ],
                    "model_uri": model_uri,
                },
            )
            safety_artifact = Artifact(
                name="safety_result",
                type="metrics",
                uri=f"metrics://{ctx.job_id}/safety",
                metadata={
                    "violated": safety_result.violated,
                    "violations": safety_result.violations,
                    "fallback_used": safety_result.fallback_used,
                    "original_action": safety_result.original_action.tolist(),
                    "strict_mode": self._safety_strict,
                },
            )
            value_artifact = Artifact(
                name="value_estimate",
                type="metrics",
                uri=f"metrics://{ctx.job_id}/value",
                metadata={
                    "value": float(value_scalar),
                    "model_uri": model_uri,
                },
            )

            logger.info(
                "RL 决策完成: job=%s violated=%s fallback=%s time=%.2fms",
                ctx.job_id,
                safety_result.violated,
                safety_result.fallback_used,
                decision_time_ms,
            )

            return TaskResult(
                status=TaskStatus.COMPLETED,
                outputs={
                    "action": action_artifact,
                    "safety_result": safety_artifact,
                    "value_estimate": value_artifact,
                },
                metrics={
                    "decision_time_ms": decision_time_ms,
                    "safety_violated": 1.0 if safety_result.violated else 0.0,
                    "fallback_used": 1.0 if safety_result.fallback_used else 0.0,
                },
            )

        except (ValueError, RuntimeError, OSError) as exc:
            logger.error(
                "RL 决策失败: job=%s error=%s",
                ctx.job_id,
                exc,
                exc_info=True,
            )
            return TaskResult(
                status=TaskStatus.FAILED,
                error=f"RL 决策失败: {exc}",
                error_code="DECISION_ERROR",
                metrics={
                    "decision_time_ms": (time.perf_counter() - start_time) * 1000,
                },
            )

    # 插件生命周期辅助方法

    def register(self, registry: Any) -> None:
        """注册到任务注册表.

        Args:
            registry: ``ITaskRegistry`` 实例.
        """
        registry.register(self, plugin_id="rl_agent")
        logger.info("RL agent 插件已注册: task_type=%s", self.TASK_TYPE)

    def unregister(self, registry: Any) -> None:
        """从任务注册表注销."""
        if hasattr(registry, "unregister"):
            registry.unregister(self.TASK_TYPE)
        logger.info("RL agent 插件已注销: task_type=%s", self.TASK_TYPE)

    # 内部辅助方法

    def _get_or_load_policy(self, model_uri: str) -> PolicyNet:
        """获取或加载策略网络（带缓存）.

        Args:
            model_uri: 模型 URI.

        Returns
        -------
        PolicyNet
            已加载权重的策略网络.
        """
        with self._cache_lock:
            if model_uri in self._policy_cache:
                return self._policy_cache[model_uri]

            net = PolicyNet(self._policy_config)
            # 权重加载：从 ModelRegistry 解析 storage_uri 并真实加载
            # （.pt/.pth via torch / .npz via numpy）；失败时 warning 并
            # 保持随机初始化，由 policy_info.weights_loaded 标记可信性
            self._load_weights(net, model_uri, kind="policy")

            # LRU：限制缓存大小为 4
            if len(self._policy_cache) >= 4:
                oldest = next(iter(self._policy_cache))
                self._policy_cache.pop(oldest, None)
            self._policy_cache[model_uri] = net
            return net

    def _get_or_load_value(self, model_uri: str) -> ValueNet:
        """获取或加载值网络（带缓存）."""
        with self._cache_lock:
            if model_uri in self._value_cache:
                return self._value_cache[model_uri]

            net = ValueNet(self._value_config)
            self._load_weights(net, model_uri, kind="value")

            if len(self._value_cache) >= 4:
                oldest = next(iter(self._value_cache))
                self._value_cache.pop(oldest, None)
            self._value_cache[model_uri] = net
            return net

    def _load_weights(self, net: Any, model_uri: str, *, kind: str) -> bool:
        """从 ModelRegistry 加载权重到网络.

        委托给 ``app.services._agent_helpers._load_weights`` 共享实现
        （真实加载 torch/npz 权重；失败时 warning + 返回 False，
        不再静默使用随机权重冒充训练结果）。

        Args:
            net: 策略或值网络实例.
            model_uri: 模型 URI.
            kind: "policy" 或 "value".

        Returns:
            True 表示成功加载真实权重；False 表示使用随机初始化（输出不可信）.
        """
        # 函数级导入：app.services 依赖 app.plugins，模块级导入会构成循环
        from app.services._agent_helpers import _load_weights as _shared_load_weights

        return _shared_load_weights(net, model_uri, kind=kind)

    def _extract_action(self, policy_out: dict[str, Any]) -> np.ndarray:
        """从策略输出提取动作向量.

        Args:
            policy_out: PolicyNet.__call__ 返回的字典.

        Returns
        -------
        np.ndarray
            动作向量 [action_dim]，float32.
        """
        action = policy_out["action"]
        if hasattr(action, "detach"):  # torch.Tensor
            action = action.detach().cpu().numpy()
        action = np.asarray(action, dtype=np.float32)
        # 去掉 batch 维度
        if action.ndim > 1:
            action = action.reshape(-1)
        return action

    def _extract_value(self, value_out: Any) -> float:
        """从值网络输出提取标量价值.

        Args:
            value_out: ValueNet.__call__ 返回的 tensor 或 ndarray.

        Returns
        -------
        float
            状态价值标量.
        """
        if hasattr(value_out, "detach"):  # torch.Tensor
            value_out = value_out.detach().cpu().numpy()
        value_arr = np.asarray(value_out, dtype=np.float32)
        return float(value_arr.reshape(-1)[0])

    def _load_artifact_data(self, artifact: Artifact | None) -> np.ndarray | None:
        """从 Artifact 加载数据.

        Args:
            artifact: 输入产物.

        Returns
        -------
        Optional[np.ndarray]
            数据数组. None 表示产物不存在.
        """
        if artifact is None:
            return None

        # 优先从 metadata 加载（小数据）
        if "data" in artifact.metadata:
            return np.asarray(artifact.metadata["data"], dtype=np.float32)

        # 从 URI 加载
        uri = artifact.uri
        if uri.startswith("file://"):
            path = uri[len("file://") :]
            try:
                return np.load(path, allow_pickle=False)
            except (OSError, ValueError) as exc:
                logger.warning("加载文件失败: %s err=%s", path, exc)
                return None

        logger.warning(
            "Artifact URI 协议未实现加载: %s（仅支持 file:// 和 metadata.data）",
            uri,
        )
        return None


__all__ = ["RLAgentPlugin"]
