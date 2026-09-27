"""RL Agent 服务纯辅助函数（从 rl_agent_service 拆分，D5）。

模块级函数，无 self 依赖；原服务方法改为薄包装调用。
"""

from __future__ import annotations

import json
from datetime import datetime
import logging

import numpy as np
from typing import Any, cast

from app.contracts.rl_agent import (
    ActionEvaluation,
    OptimizationTarget,
    PolicyError,
    PolicyVersion,
    TrainingMetricsSnapshot,
    TrainingStatusInfo,
)
from app.contracts.world_model import ActionField, StateField
from app.database.models.rl_agent import (
    RLAgentPolicyVersionORM,
    RLAgentTrainingRunORM,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


# 状态/动作字段索引（与 StateField.all() / ActionField.all() 顺序对齐）
_STATE_FIELD_ORDER: list[str] = StateField.all()
_ACTION_FIELD_ORDER: list[str] = ActionField.all()
_STATE_FIELD_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(_STATE_FIELD_ORDER)}


def _orm_to_dataclass(orm: RLAgentPolicyVersionORM) -> PolicyVersion:
    """ORM → 契约层 dataclass."""
    return PolicyVersion(
        version=str(orm.version),
        model_uri=str(orm.model_uri),
        algorithm=str(orm.algorithm),
        description=str(orm.description or ""),
        created_at=cast(datetime, orm.created_at) if orm.created_at else utcnow(),
        training_episodes=int(orm.training_episodes),
        training_steps=int(orm.training_steps),
        mean_reward=float(orm.mean_reward),
        is_active=bool(orm.is_active),
    )


def _training_run_to_status_info(orm: RLAgentTrainingRunORM) -> TrainingStatusInfo:
    """训练运行 ORM → TrainingStatusInfo."""
    metrics: TrainingMetricsSnapshot | None = None
    if orm.metrics_json:
        try:
            metrics_dict = json.loads(str(orm.metrics_json))
            metrics = TrainingMetricsSnapshot(
                step=metrics_dict.get("step", orm.current_step),
                episode=metrics_dict.get("episode", orm.current_episode),
                policy_loss=metrics_dict.get("policy_loss", 0.0),
                value_loss=metrics_dict.get("value_loss", 0.0),
                entropy=metrics_dict.get("entropy", 0.0),
                approx_kl=metrics_dict.get("approx_kl", 0.0),
                clip_fraction=metrics_dict.get("clip_fraction", 0.0),
                mean_reward=metrics_dict.get("mean_reward", 0.0),
                mean_value=metrics_dict.get("mean_value", 0.0),
                epsilon=metrics_dict.get("epsilon", 1.0),
                elapsed_seconds=metrics_dict.get("elapsed_seconds", 0.0),
            )
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "解析训练指标 JSON 失败: run_id=%s err=%s",
                orm.id,
                exc,
            )
            metrics = None

    max_steps = orm.total_steps_target or 100000
    return TrainingStatusInfo(
        status=str(orm.status),
        current_step=int(orm.current_step),
        max_steps=int(max_steps),
        current_episode=int(orm.current_episode),
        metrics=metrics,
        started_at=cast(datetime, orm.started_at) if orm.started_at else None,
        finished_at=cast(datetime, orm.finished_at) if orm.finished_at else None,
        error_message=str(orm.error_message) if orm.error_message else None,
    )


def _state_dict_to_array(state_dict: dict[str, float], *, field_name: str) -> np.ndarray:
    """状态字典 → ndarray [state_dim].

    按 ``StateField.all()`` 顺序提取值，缺失字段报错.
    """
    if not state_dict:
        raise ValueError(f"{field_name} 不能为空")
    values = []
    for field in _STATE_FIELD_ORDER:
        if field not in state_dict:
            raise ValueError(f"{field_name} 缺少字段 '{field}'（必需字段: {_STATE_FIELD_ORDER}）")
        value = state_dict[field]
        try:
            values.append(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}['{field}'] 不是合法数值: {value}") from exc
    return np.asarray(values, dtype=np.float32)


def _action_dict_to_array(action_dict: dict[str, float], *, field_name: str) -> np.ndarray:
    """动作字典 → ndarray [action_dim].

    按 ``ActionField.all()`` 顺序提取值，缺失字段报错.
    """
    if not action_dict:
        raise ValueError(f"{field_name} 不能为空")
    values = []
    for field in _ACTION_FIELD_ORDER:
        if field not in action_dict:
            raise ValueError(f"{field_name} 缺少字段 '{field}'（必需字段: {_ACTION_FIELD_ORDER}）")
        value = action_dict[field]
        try:
            values.append(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}['{field}'] 不是合法数值: {value}") from exc
    return np.asarray(values, dtype=np.float32)


def _action_array_to_dict(action_arr: np.ndarray) -> dict[str, float]:
    """动作 ndarray → 字典（按 ActionField 顺序还原字段名）."""
    arr = np.asarray(action_arr, dtype=np.float32).reshape(-1)
    result: dict[str, float] = {}
    for idx, field_name in enumerate(_ACTION_FIELD_ORDER):
        if idx < len(arr):
            result[field_name] = float(arr[idx])
    return result


def _extract_state_field(state_arr: np.ndarray, field_name: str, *, default: float = 0.0) -> float:
    """从状态数组提取指定字段值."""
    idx = _STATE_FIELD_INDEX.get(field_name)
    if idx is None or idx >= len(state_arr):
        return default
    return float(state_arr[idx])


def _load_weights(net: Any, model_uri: str, *, kind: str) -> bool:
    """从 ModelRegistry 解析权重文件并加载到网络.

    v1 诚实化修复：此前此函数解析到 storage_uri 后只打 debug 日志、从不加载，
    推理永远使用随机初始化权重却对外呈现"决策完成"。现在真实加载：

    - torch 可用且权重为 ``.pt``/``.pth``：``torch.load(weights_only=True)`` +
      ``load_state_dict``（weights_only 受限反序列化，避免 pickle 任意代码执行；
      旧版 torch 不支持该参数时拒绝加载，绝不回退到不安全加载）；
    - torch 不可用且权重为 ``.npz``：按属性名加载 NumPy 数组（键与网络属性名
      一致，如 ``_w1``），形状不匹配视为加载失败；
    - 注册表无记录 / 路径不可达 / 格式不支持：拒绝静默降级为"看起来正常"的
      随机策略，改为 warning 日志并返回 False，由调用方在响应中标记
      ``weights_loaded=false``。

    Args:
        net: 策略或值网络实例（torch nn.Module 或 NumPy 回退实现）.
        model_uri: 模型 URI.
        kind: "policy" 或 "value".

    Returns:
        True 表示成功加载真实权重；False 表示使用随机初始化（输出不可信）.
    """
    try:
        from app.ai.lnn.inference.registry import LNNModelRegistry

        # 使用具体子类实例调用 get()，避免在抽象基类上直接调用抽象方法
        # （BaseModelRegistry.get() 是 @abstractmethod，需要 self 实例）
        registry = LNNModelRegistry()
        entry = registry.get(model_uri)
        storage_uri = getattr(entry, "storage_uri", None) or (entry.info.model_path if entry and entry.info else None)
        if not storage_uri:
            logger.warning(
                "RL 权重缺失: 注册表无 %s 记录，%s 网络使用随机初始化（输出不可信）",
                model_uri,
                kind,
            )
            return False

        local_path = _resolve_weight_path(str(storage_uri))
        if local_path is None:
            logger.warning(
                "RL 权重不可达: %s（仅支持本地路径/file:// URI），%s 网络使用随机初始化（输出不可信）",
                storage_uri,
                kind,
            )
            return False

        return _load_state_into_net(net, local_path, kind=kind)
    except (ImportError, AttributeError, KeyError, RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning(
            "RL 权重加载失败，%s 网络使用随机初始化（输出不可信）: uri=%s err=%s",
            kind,
            model_uri,
            exc,
        )
        return False


def _resolve_weight_path(storage_uri: str):
    """将 storage_uri 解析为存在的本地文件路径；不可达/不支持的协议返回 None."""
    from pathlib import Path
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    parsed = urlparse(storage_uri)
    scheme = parsed.scheme
    if scheme in ("", "file"):
        raw = parsed.path if scheme == "file" else storage_uri
        # Windows file:///C:/... 需经 url2pathname 还原盘符
        path = Path(url2pathname(raw)) if scheme == "file" else Path(raw)
        return path if path.is_file() else None
    if len(scheme) == 1:
        # Windows 盘符：urlparse 会把 "C:\a\b.npz" 解析成 scheme="c"，
        # 必须按本地路径处理而非"不支持的协议"
        path = Path(storage_uri)
        return path if path.is_file() else None
    return None


def _load_state_into_net(net: Any, path, *, kind: str) -> bool:
    """把本地权重文件加载进网络；成功返回 True，失败抛异常由上层统一处理."""
    suffix = path.suffix.lower()

    if suffix in (".pt", ".pth"):
        try:
            import torch
        except ImportError:
            logger.warning(
                "RL %s 权重为 torch 格式但运行环境无 torch，无法加载: %s（输出不可信）",
                kind,
                path,
            )
            return False
        # 安全：仅允许 weights_only=True 的受限反序列化。torch.load 底层是
        # pickle，加载被篡改的权重文件可导致任意代码执行；旧版 torch 不支持
        # 该参数时拒绝加载，绝不回退到不安全加载。
        try:
            state = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError as exc:
            logger.warning(
                "torch 版本过旧、不支持 weights_only 安全加载，拒绝加载 %s 权重: %s（输出不可信）",
                kind,
                exc,
            )
            return False
        net.load_state_dict(state)
        logger.info("RL %s 权重加载成功（torch）: %s", kind, path)
        return True

    if suffix == ".npz":
        # allow_pickle=False 阻止 npz 内嵌对象反序列化（安全默认，显式声明）
        with np.load(path, allow_pickle=False) as data:
            arrays = {k: np.asarray(data[k], dtype=np.float32) for k in data.files}
        applied = 0
        for key, arr in arrays.items():
            if not hasattr(net, key):
                continue
            current = getattr(net, key)
            if hasattr(current, "shape") and tuple(current.shape) != tuple(arr.shape):
                raise ValueError(f"权重形状不匹配: {key} 期望 {tuple(current.shape)} 实际 {tuple(arr.shape)}")
            setattr(net, key, arr)
            applied += 1
        if applied == 0:
            raise ValueError(f"npz 中无可识别的权重键（需与网络属性名一致）: {sorted(arrays)}")
        logger.info("RL %s 权重加载成功（numpy npz，%d 项）: %s", kind, applied, path)
        return True

    logger.warning("RL %s 权重格式不支持: %s（支持 .pt/.pth/.npz），使用随机初始化（输出不可信）", kind, path)
    return False


def _extract_action(policy_out: Any) -> np.ndarray:
    """从策略输出提取动作向量.

    处理 torch.Tensor 与 NumPy 回退两种模式（与 RLAgentPlugin 对齐）.
    """
    if isinstance(policy_out, dict):
        action = policy_out.get("action")
    else:
        action = policy_out
    if action is None:
        raise PolicyError("策略网络未返回动作")
    if hasattr(action, "detach"):  # torch.Tensor
        action = action.detach().cpu().numpy()
    action_arr = np.asarray(action, dtype=np.float32)
    # 去掉 batch 维度
    if action_arr.ndim > 1:
        action_arr = action_arr.reshape(-1)
    return action_arr


def _extract_value(value_out: Any) -> float:
    """从值网络输出提取标量价值."""
    if hasattr(value_out, "detach"):  # torch.Tensor
        value_out = value_out.detach().cpu().numpy()
    value_arr = np.asarray(value_out, dtype=np.float32)
    return float(value_arr.reshape(-1)[0])


def _rank_candidates(
    candidates: list[ActionEvaluation],
    optimization_target: str,
) -> list[ActionEvaluation]:
    """按优化目标对候选动作排序（降序，最优在前）.

    排序键：
        - MINIMIZE_CHATTER: 按 predicted_chatter_prob 升序
        - MAXIMIZE_MATERIAL_REMOVAL: 按 expected_return 降序
        - BALANCE: 按 q_value 降序
    """
    if optimization_target == OptimizationTarget.MINIMIZE_CHATTER:
        return sorted(candidates, key=lambda e: e.predicted_chatter_prob)
    if optimization_target == OptimizationTarget.MAXIMIZE_MATERIAL_REMOVAL:
        return sorted(candidates, key=lambda e: e.expected_return, reverse=True)
    # BALANCE
    return sorted(candidates, key=lambda e: e.q_value, reverse=True)


def _build_reasoning(
    *,
    action_dict: dict[str, float],
    optimization_target: str,
    source: str,
    safety_violated: bool,
) -> str:
    """生成推荐理由（自然语言，供工程师审查）."""
    source_label = {
        "policy": "策略网络输出",
        "candidate_fallback": "候选动作回退",
    }.get(source, source)

    target_label = {
        OptimizationTarget.MINIMIZE_CHATTER: "最小化颤振",
        OptimizationTarget.MAXIMIZE_MATERIAL_REMOVAL: "最大化材料去除率",
        OptimizationTarget.BALANCE: "平衡颤振/磨损/效率",
    }.get(optimization_target, optimization_target)

    # 动作摘要
    action_summary = ", ".join(f"{k}={v:+.3f}" for k, v in action_dict.items())

    return (
        f"推荐动作来源：{source_label}。"
        f"优化目标：{target_label}。"
        f"动作向量（{action_summary}）。"
        f"安全过滤：{'违反（已回退）' if safety_violated else '通过'}。"
        f"提示：本动作仅供 CAM 验证层参考，实际加工需经持证操作员审核。"
    )
