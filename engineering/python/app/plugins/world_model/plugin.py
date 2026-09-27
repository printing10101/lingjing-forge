"""世界模型插件：实现 ``wm_predict_state`` 任务类型.

对应 ADR-017 第 1.1 节。``WorldModelPlugin`` 实现 ``TaskHandler`` 协议，
注册任务类型 ``wm_predict_state``，由工作流编排器调度。

任务输入输出契约
----------------
- 输入：
    - ``current_state`` (Artifact, type=metrics): 当前加工状态（颤振概率/磨损/质量）
    - ``candidate_action`` (Artifact, type=metrics): 候选切削参数序列
    - ``horizon`` (config): 预测步长，默认 10
    - ``model_uri`` (config): 世界模型 URI

- 输出：
    - ``predicted_trajectory`` (Artifact, type=metrics): 预测状态轨迹
    - ``trajectory_metrics`` (Artifact, type=metrics): 轨迹汇总指标

- 指标：
    - ``prediction_time_ms``: 预测耗时
    - ``trajectory_length``: 轨迹长度

工程现实约束
------------
- v1 仅离线 RL，本插件预测的轨迹供 RL agent 离线训练使用
- 不直接接 CNC 控制器，预测结果仅供决策参考
- 物理执行需"持证操作员 + 导师签字 + 保险"，本插件不涉及
"""

from __future__ import annotations

import logging
import time
from typing import Any, Union, cast

import numpy as np

from app.contracts.task import Artifact, TaskContext, TaskResult, TaskStatus
from app.plugins.world_model.dynamics_state_bridge import BridgeResult
from app.plugins.world_model.geometry_features_deriver import DerivationResult
from app.plugins.world_model.net import WorldModelConfig
from app.plugins.world_model.predictor import TrajectoryPredictor
from app.plugins.world_model.unified_state import (
    DynamicsState,
    GeometryFeatures,
    UnifiedState,
)
from app.plugins.world_model.unified_state_assembler import (
    AssemblerResult,
    UnifiedStateAssembler,
)

logger = logging.getLogger(__name__)


class WorldModelPlugin:
    """世界模型插件：实现 ``wm_predict_state`` 任务处理器.

    实现 ``TaskHandler`` 协议（结构化子类型，无需继承）。
    由 ``PluginLifecycleManager`` 在插件 ``on_load`` 时注册到 ``ITaskRegistry``。

    生命周期
    --------
    1. 插件加载时，``WorldModelPlugin()`` 实例化
    2. ``register(registry)`` 注册 ``wm_predict_state`` 任务类型
    3. 工作流编排器调度时调用 ``execute(ctx)``
    4. 插件卸载时，``unregister(registry)`` 注销任务类型
    """

    TASK_TYPE = "wm_predict_state"

    def __init__(self, config: WorldModelConfig | None = None) -> None:
        self._config = config or WorldModelConfig()
        self._predictor: TrajectoryPredictor | None = None
        self._predictor_lock = __import__("threading").Lock()
        # 缓存已加载的模型 uri predictor 映射，避免重复加载
        self._predictor_cache: dict[str, TrajectoryPredictor] = {}

    # TaskHandler 协议实现

    def name(self) -> str:
        """任务类型名称."""
        return self.TASK_TYPE

    def description(self) -> str:
        """任务类型描述."""
        return (
            "世界模型状态预测：基于当前加工状态与候选切削参数序列，"
            "预测未来 N 步的状态轨迹（颤振概率/刀具磨损/表面质量）。"
            "供 RL agent 离线训练与决策参考。"
        )

    def input_schema(self) -> dict[str, Any]:
        """输入 schema（JSON Schema 格式）.

        支持两种 ``current_state`` 输入格式：

        1. **传统模式**（``config.use_fusion=False``）：
            ``metadata.data`` 为 ``[state_dim]`` 或 ``[T, state_dim]`` 数组
            或 ``uri`` 指向 .npy 文件
        2. **融合模式**（``config.use_fusion=True``，ADR-020 思路 1）：
            ``metadata.unified_state`` 为 UnifiedState 字典
            （geometry + dynamics），由 ``UnifiedState.from_dict`` 解析
        """
        return {
            "type": "object",
            "properties": {
                "current_state": {
                    "type": "object",
                    "description": (
                        "当前加工状态。传统模式：metadata.data 为状态数组；"
                        "融合模式：metadata.unified_state 为 UnifiedState dict。"
                    ),
                    "properties": {
                        "uri": {"type": "string"},
                        "shape": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "状态向量形状 [state_dim] 或 [T, state_dim]",
                        },
                    },
                    "required": ["uri"],
                },
                "candidate_action": {
                    "type": "object",
                    "description": "候选切削参数序列",
                    "properties": {
                        "uri": {"type": "string"},
                        "shape": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "动作序列形状 [horizon, action_dim]",
                        },
                    },
                    "required": ["uri"],
                },
                "horizon": {
                    "type": "integer",
                    "description": "预测步长",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                },
                "model_uri": {
                    "type": "string",
                    "description": "世界模型 URI",
                    "default": "model://world_model/1.0.0",
                },
            },
            "required": ["current_state", "candidate_action"],
        }

    def output_schema(self) -> dict[str, Any]:
        """输出 schema."""
        return {
            "type": "object",
            "properties": {
                "predicted_trajectory": {
                    "type": "object",
                    "description": "预测的状态轨迹 [horizon, state_dim]",
                    "properties": {
                        "uri": {"type": "string"},
                        "shape": {"type": "array", "items": {"type": "integer"}},
                    },
                },
                "trajectory_metrics": {
                    "type": "object",
                    "description": "轨迹汇总指标 [3]（颤振峰值/最大磨损/平均质量）",
                    "properties": {
                        "uri": {"type": "string"},
                    },
                },
            },
            "required": ["predicted_trajectory", "trajectory_metrics"],
        }

    async def execute(self, ctx: TaskContext) -> TaskResult:
        """执行世界模型预测.

        自动检测 ``current_state`` 输入格式：
        - 若 Artifact.metadata 含 ``unified_state`` 键且 config.use_fusion=True，
          走融合路径（ADR-020 思路 1）
        - 否则走传统 np.ndarray 路径（向后兼容）

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
            current_state_artifact = ctx.inputs.get("current_state")
            candidate_action = self._load_artifact_data(ctx.inputs.get("candidate_action"))
            horizon = int(ctx.config.get("horizon", 10))
            model_uri = ctx.config.get("model_uri", "model://world_model/1.0.0")

            if current_state_artifact is None:
                return TaskResult(
                    status=TaskStatus.FAILED,
                    error="缺少输入: current_state",
                    error_code="MISSING_INPUT",
                )
            if candidate_action is None:
                return TaskResult(
                    status=TaskStatus.FAILED,
                    error="缺少输入: candidate_action",
                    error_code="MISSING_INPUT",
                )

            # 2. 尝试解析 UnifiedState（融合模式），否则回退到 np.ndarray
            unified_state = self._try_load_unified_state(current_state_artifact)
            assembly_diagnostics: dict[str, Any] | None = None

            # P0-3 自动组装（ADR-020 思路 1）：metadata 无预组装 unified_state
            # 但含组装原料（geometry_features + dynamics_state）时，由
            # UnifiedStateAssembler 自动组装，让真实数据源产出真正流入融合路径
            if unified_state is None and self._config.use_fusion:
                assembled = self._try_assemble_unified_state(current_state_artifact)
                if assembled is not None:
                    unified_state = assembled.unified_state
                    assembly_diagnostics = assembled.to_dict()
                    if assembled.should_degrade:
                        logger.warning(
                            "WorldModelPlugin 自动组装降级: "
                            "geometry_degraded=%s dynamics_degraded=%s "
                            "completeness=%.2f. 融合 embedding 质量可能下降，"
                            "建议补充数据源.",
                            assembled.geometry_degraded,
                            assembled.dynamics_degraded,
                            assembled.completeness_ratio,
                        )
                else:
                    # 无预组装 unified_state 也无组装原料：零向量融合兜底。
                    # 走融合路径（维度 fused_dim+action_dim 与 torch 模型匹配），
                    # 预测结果无实际意义，但保证 use_fusion=True 下工作流不因
                    # 维度不匹配而中断（与 predictor 的零向量降级语义一致）。
                    logger.warning(
                        "WorldModelPlugin 无 unified_state 且无组装原料，使用零向量融合兜底（预测无实际意义）。"
                    )
                    unified_state = UnifiedState(
                        geometry=GeometryFeatures(
                            bbox_dimensions=(0.0, 0.0, 0.0),
                            feature_vector=[0.0] * self._config.feature_dim,
                            symmetry_score=0.0,
                            complexity_score=0.0,
                        ),
                        dynamics=DynamicsState(
                            spindle_speed=0.0,
                            feed_rate=0.0,
                            depth_of_cut=0.0,
                            tool_wear=0.0,
                            vibration_rms=0.0,
                            temperature=0.0,
                        ),
                    )

            if unified_state is not None:
                current_state: Union[np.ndarray, UnifiedState] | None = unified_state
                input_mode = "fusion_assembled" if assembly_diagnostics else "fusion"
            else:
                current_state = self._load_artifact_data(current_state_artifact)
                if current_state is None:
                    return TaskResult(
                        status=TaskStatus.FAILED,
                        error=("current_state 加载失败（既无 unified_state 也无组装原料也无可加载的数组数据）"),
                        error_code="INVALID_INPUT",
                    )
                input_mode = "legacy"

            # 3. 获取或加载预测器（带缓存）
            predictor = self._get_or_load_predictor(model_uri)

            # 4. 执行预测（含 ADR-020 P3 降级兜底）
            # 融合模式：current_state 是 UnifiedState，predictor 内部走融合路径
            # 传统模式：current_state 是 np.ndarray，predictor 走原 LSTM 输入路径
            # 降级兜底：融合路径抛 RuntimeError 时（torch 不可用 / 权重不可用 /
            # 数据不完整），自动降级到 legacy 路径重新预测，保证生产路径不崩溃。
            prediction = None
            degraded_to_legacy = False
            if input_mode in ("fusion", "fusion_assembled"):
                try:
                    # 融合分支保证 current_state 是 UnifiedState（unified_state 非 None 分支）
                    prediction = predictor.predict(
                        unified_state=cast(UnifiedState, current_state),
                        candidate_action=candidate_action,
                        horizon=horizon,
                    )
                except RuntimeError as fusion_exc:
                    logger.warning(
                        "融合路径预测失败，降级到传统路径: job=%s error=%s",
                        ctx.job_id,
                        fusion_exc,
                    )
                    legacy_state = self._load_artifact_data(current_state_artifact)
                    if not isinstance(legacy_state, np.ndarray):
                        # UnifiedState 输入无法降级为 np.ndarray，
                        # 构造零向量兜底（仅满足接口契约，预测无意义）
                        logger.warning("降级兜底：current_state 无法转为 np.ndarray，使用零向量（预测无意义）。")
                        legacy_state = np.zeros((1, self._config.state_dim), dtype=np.float32)
                    prediction = predictor.predict(
                        current_state=legacy_state,
                        candidate_action=candidate_action,
                        horizon=horizon,
                    )
                    degraded_to_legacy = True
                    input_mode = "legacy_degraded"
            else:
                prediction = predictor.predict(
                    current_state=current_state,
                    candidate_action=candidate_action,
                    horizon=horizon,
                )

            prediction_time_ms = (time.perf_counter() - start_time) * 1000

            # 5. 构造输出 Artifact
            # 实际部署中应将数组持久化到文件并返回 file:// URI；
            # 此处简化为 metrics:// URI + metadata 携带数据摘要
            trajectory_artifact = Artifact(
                name="predicted_trajectory",
                type="metrics",
                uri=f"metrics://{ctx.job_id}/trajectory",
                metadata={
                    "shape": list(prediction.predicted_trajectory.shape),
                    "horizon": prediction.horizon,
                    "model_uri": model_uri,
                    "input_mode": input_mode,
                    "preview": prediction.predicted_trajectory[:3].tolist()
                    if hasattr(prediction.predicted_trajectory, "tolist")
                    else prediction.predicted_trajectory[:3],
                },
            )
            metrics_artifact = Artifact(
                name="trajectory_metrics",
                type="metrics",
                uri=f"metrics://{ctx.job_id}/metrics",
                metadata={
                    "values": prediction.trajectory_metrics.tolist()
                    if hasattr(prediction.trajectory_metrics, "tolist")
                    else list(prediction.trajectory_metrics),
                    "labels": ["chatter_peak", "max_wear", "avg_quality"],
                },
            )

            logger.info(
                "世界模型预测完成: job=%s horizon=%d mode=%s time=%.2fms",
                ctx.job_id,
                horizon,
                input_mode,
                prediction_time_ms,
            )

            return TaskResult(
                status=TaskStatus.COMPLETED,
                outputs={
                    "predicted_trajectory": trajectory_artifact,
                    "trajectory_metrics": metrics_artifact,
                },
                metrics={
                    "prediction_time_ms": prediction_time_ms,
                    "trajectory_length": float(horizon),
                    "degraded_to_legacy": float(degraded_to_legacy),
                },
            )

        except (ValueError, RuntimeError, OSError) as exc:
            logger.error(
                "世界模型预测失败: job=%s error=%s",
                ctx.job_id,
                exc,
                exc_info=True,
            )
            return TaskResult(
                status=TaskStatus.FAILED,
                error=f"世界模型预测失败: {exc}",
                error_code="PREDICTION_ERROR",
                metrics={
                    "prediction_time_ms": (time.perf_counter() - start_time) * 1000,
                },
            )

    # 插件生命周期辅助方法

    def register(self, registry: Any) -> None:
        """注册到任务注册表.

        Args:
            registry: ``ITaskRegistry`` 实例.
        """
        registry.register(self, plugin_id="world_model")
        logger.info("世界模型插件已注册: task_type=%s", self.TASK_TYPE)

    def unregister(self, registry: Any) -> None:
        """从任务注册表注销."""
        # 注册表通常提供 unregister(task_type) 方法
        if hasattr(registry, "unregister"):
            registry.unregister(self.TASK_TYPE)
        logger.info("世界模型插件已注销: task_type=%s", self.TASK_TYPE)

    # 内部辅助方法

    def _get_or_load_predictor(self, model_uri: str) -> TrajectoryPredictor:
        """获取或加载预测器（带缓存）.

        Args:
            model_uri: 模型 URI.

        Returns
        -------
        TrajectoryPredictor
            已加载权重的预测器.
        """
        with self._predictor_lock:
            if model_uri in self._predictor_cache:
                return self._predictor_cache[model_uri]

            predictor = TrajectoryPredictor(
                config=self._config,
                device="auto",
            )
            # 权重路径解析：从 model_uri 推导文件路径
            # 实际部署中应调用 ModelRegistry.resolve(model_uri)
            weights_path = self._resolve_weights_path(model_uri)
            predictor.load_model(model_uri=model_uri, weights_path=weights_path)

            # 缓存（限制大小防止内存泄漏）
            if len(self._predictor_cache) >= 4:
                # LRU：丢弃最早的一个
                oldest = next(iter(self._predictor_cache))
                self._predictor_cache.pop(oldest, None)
            self._predictor_cache[model_uri] = predictor
            return predictor

    def _resolve_weights_path(self, model_uri: str) -> str | None:
        """从模型 URI 解析权重文件路径.

        解析顺序（ADR-020 思路 1 P1：解锁 L3 权重阻塞）：

        1. **ModelRegistry 解析**：优先查 ``LNNModelRegistry``，命中则返回
           ``storage_uri``。覆盖已注册的 LNN 颤振预测模型等场景。
        2. **约定式 URI → path 解析**：若 URI 形如
           ``model://world_model/<version>``，按
           ``<models_dir>/world_model/<version>.pt`` 约定查找文件。这一路
           让 ``FusionWorldModelTrainer.save_checkpoint`` 产出的融合权重
           能被 ``TrajectoryPredictor.load_model`` 加载，形成训练 → 推理
           闭环（无需手动注册到 ModelRegistry）。
        3. 都未命中则返回 None（使用随机初始化权重，用于接口验证或未训练
           模型）。

        Args:
            model_uri: 模型 URI（如 ``model://world_model/1.0.0``）。

        Returns
        -------
        Optional[str]
            权重文件路径。返回 None 表示使用随机初始化权重
           （用于接口验证或未训练模型）。
        """
        # 1. 尝试通过 ModelRegistry 解析
        try:
            from app.ai.lnn.inference.registry import LNNModelRegistry

            # 使用具体子类实例调用 get()，避免在抽象基类上直接调用抽象方法
            # （BaseModelRegistry.get() 是 @abstractmethod，需要 self 实例）
            registry = LNNModelRegistry()
            entry = registry.get(model_uri)
            storage_uri = getattr(entry, "storage_uri", None) or (
                entry.info.model_path if entry and entry.info else None
            )
            if storage_uri:
                return storage_uri
        except (ImportError, AttributeError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug(
                "ModelRegistry 解析失败，尝试约定式路径解析: uri=%s err=%s",
                model_uri,
                exc,
            )

        # 2. 约定式 URI path 解析（torch-free，让训练产出的 checkpoint
        # 能被加载，无需手动注册到 ModelRegistry）
        try:
            from app.plugins.world_model.training import (
                resolve_world_model_weights_path,
            )

            resolved = resolve_world_model_weights_path(model_uri)
            if resolved is not None:
                logger.debug(
                    "world_model 权重路径解析成功（约定式）: uri=%s path=%s",
                    model_uri,
                    resolved,
                )
                return resolved
        except (ImportError, RuntimeError) as exc:
            # ImportError: training 模块加载失败（不应发生，防御性捕获）
            # RuntimeError: WeightsResolutionError（URI 版本字符串非法），
            # 降级为随机初始化 + 警告，保持 _resolve_weights_path 既有
            # "None = random init" 契约，不引入新失败路径
            logger.warning(
                "world_model 约定式权重路径解析失败，使用随机初始化: uri=%s err=%s",
                model_uri,
                exc,
            )
        return None

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

        # 从 URI 加载（实际部署中应支持 file:// / metrics:// 等协议）
        uri = artifact.uri
        if uri.startswith("file://"):
            path = uri[len("file://") :]
            try:
                return np.load(path, allow_pickle=False)
            except (OSError, ValueError) as exc:
                logger.warning("加载文件失败: %s err=%s", path, exc)
                return None

        # 未实现完整加载逻辑时返回 None
        logger.warning(
            "Artifact URI 协议未实现加载: %s（仅支持 file:// 和 metadata.data）",
            uri,
        )
        return None

    def _try_load_unified_state(self, artifact: Artifact | None) -> UnifiedState | None:
        """尝试从 Artifact.metadata 解析 UnifiedState（融合模式检测）.

        检测规则：
        - 若 ``artifact.metadata`` 含 ``unified_state`` 键且值为 dict，
          调用 ``UnifiedState.from_dict`` 解析为 ``UnifiedState`` 实例
        - 同时要求 ``self._config.use_fusion=True``（否则即使 metadata
          含 unified_state 也不启用融合路径，避免配置与输入不一致）
        - 解析失败（KeyError/TypeError/ValueError）时记录 warning 并
          返回 None，由上层回退到传统 np.ndarray 路径

        Args:
            artifact: 输入 ``current_state`` 产物.

        Returns
        -------
        Optional[UnifiedState]
            - ``UnifiedState`` 实例：走融合路径
            - ``None``：走传统 np.ndarray 路径
        """
        if artifact is None:
            return None

        # 配置未启用融合模式时直接返回 None（保持向后兼容）
        if not self._config.use_fusion:
            return None

        metadata = getattr(artifact, "metadata", None) or {}
        us_dict = metadata.get("unified_state")
        if not isinstance(us_dict, dict):
            # metadata 中无 unified_state 或类型不对 传统模式
            return None

        try:
            unified = UnifiedState.from_dict(us_dict)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "UnifiedState 解析失败，回退到传统 np.ndarray 路径: %s",
                exc,
            )
            return None

        logger.debug(
            "已解析 UnifiedState（融合模式）: bbox=%s spindle=%s",
            unified.geometry.bbox_dimensions,
            unified.dynamics.spindle_speed,
        )
        return unified

    def _try_assemble_unified_state(self, artifact: Artifact | None) -> "AssemblerResult | None":
        """P0-3 自动组装：从 metadata 中的组装原料组装 UnifiedState.

        当 ``_try_load_unified_state`` 返回 None（metadata 无预组装
        ``unified_state``）但 ``config.use_fusion=True`` 时，本方法尝试
        从 metadata 的 ``geometry_features`` + ``dynamics_state`` 两个
        半成品字典自动组装 UnifiedState，填补 P0-1/P0-2 产出到融合路径
        之间的"组装"gap.

        支持的 metadata 格式（任一缺失即返回 None，回退到传统路径）::

            metadata = {
                "geometry_features": {
                    "bbox_dimensions": [L, W, H],
                    "feature_vector": [32 floats],
                    "symmetry_score": float,
                    "complexity_score": float,
                },
                "dynamics_state": {
                    "spindle_speed": float, "feed_rate": float,
                    "depth_of_cut": float, "tool_wear": float,
                    "vibration_rms": float, "temperature": float,
                },
            }

        设计权衡
        --------
        - 不在 plugin 层反序列化完整 ``ExtractedFeature`` 列表
          （``ExtractedFeature`` 无 ``from_dict``，且 plugin 不应承担
          ADR-007 特征重建职责）；完整端到端组装
          （features+vertices → UnifiedState）由 ``WorldModelService``
          调用 ``UnifiedStateAssembler.assemble_from_sources`` 完成
        - 本方法只接受已派生的半成品 dict，对应 service 层调用
          ``GeometryFeaturesDeriver`` / ``DynamicsStateBridge`` 后
          序列化入 metadata 的场景
        - 组装失败（字段缺失/类型错误）不抛异常，返回 None 回退传统路径

        Args:
            artifact: 输入 ``current_state`` 产物.

        Returns
        -------
        Optional[AssemblerResult]
            - ``AssemblerResult``：组装成功（可能含降级诊断）
            - ``None``：原料缺失或组装失败，回退传统路径
        """
        if artifact is None:
            return None

        metadata = getattr(artifact, "metadata", None) or {}
        geo_dict = metadata.get("geometry_features")
        dyn_dict = metadata.get("dynamics_state")

        # 任一半成品缺失则无法组装
        if not isinstance(geo_dict, dict) or not isinstance(dyn_dict, dict):
            return None

        # 严格类型校验：bbox_dimensions / feature_vector 必须是 list/tuple.
        # 注意 ``tuple("not_a_list")`` 会逐字符拆分字符串而不抛异常，
        # 因此必须显式拒绝 str 等可迭代但语义错误的类型，避免污染
        # UnifiedState.geometry.bbox_dimensions.
        bbox_raw = geo_dict.get("bbox_dimensions")
        feature_vec_raw = geo_dict.get("feature_vector")
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 3:
            logger.warning(
                "自动组装失败：bbox_dimensions 必须是长度为 3 的 list/tuple, 实际类型=%s, 值=%r",
                type(bbox_raw).__name__,
                bbox_raw,
            )
            return None
        if not isinstance(feature_vec_raw, (list, tuple)):
            logger.warning(
                "自动组装失败：feature_vector 必须是 list/tuple, 实际类型=%s",
                type(feature_vec_raw).__name__,
            )
            return None
        try:
            geometry = GeometryFeatures(
                bbox_dimensions=tuple(bbox_raw),
                feature_vector=list(feature_vec_raw),
                symmetry_score=float(geo_dict["symmetry_score"]),
                complexity_score=float(geo_dict["complexity_score"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "自动组装失败：geometry_features 字段缺失或类型错误: %s",
                exc,
            )
            return None

        # 复用 DynamicsStateBridge 的字段映射与默认填充逻辑
        # dyn_dict 的 key 可能是 StateField 名或 DynamicsState 字段名，
        # 统一交给 Bridge 处理；若 key 已是 DynamicsState 字段名，
        # Bridge 找不到 StateField key 会标记 defaulted，因此这里
        # 优先识别 DynamicsState 字段名直连
        try:
            dynamics = DynamicsState(
                spindle_speed=float(dyn_dict.get("spindle_speed", 0.0)),
                feed_rate=float(dyn_dict.get("feed_rate", 0.0)),
                depth_of_cut=float(dyn_dict.get("depth_of_cut", 0.0)),
                tool_wear=float(dyn_dict.get("tool_wear", 0.0)),
                vibration_rms=float(dyn_dict.get("vibration_rms", 0.0)),
                temperature=float(dyn_dict.get("temperature", 0.0)),
            )
        except (TypeError, ValueError) as exc:
            logger.warning("自动组装失败：dynamics_state 类型错误: %s", exc)
            return None

        # 标记 defaulted：用 0.0 填充的字段（与 Bridge 语义一致）
        dynamics_defaulted = [
            f
            for f in (
                "spindle_speed",
                "feed_rate",
                "depth_of_cut",
                "tool_wear",
                "vibration_rms",
                "temperature",
            )
            if f not in dyn_dict
        ]

        # 几何侧：半成品 dict 视为完整（已由 Deriver 派生过），无 defaulted
        geometry_result = DerivationResult(
            geometry=geometry,
            defaulted_fields=[],
            derivation_notes=["assembled_from_metadata_half_product"],
            source="adr007_ransac",
        )
        dynamics_result = BridgeResult(
            dynamics=dynamics,
            missing_fields=list(dynamics_defaulted),
            defaulted_fields=list(dynamics_defaulted),
            source="legacy_current_state",
        )

        assembled = UnifiedStateAssembler.assemble_from_results(geometry_result, dynamics_result)
        logger.debug(
            "自动组装 UnifiedState: bbox=%s spindle=%s degraded=%s",
            assembled.unified_state.geometry.bbox_dimensions,
            assembled.unified_state.dynamics.spindle_speed,
            assembled.should_degrade,
        )
        return assembled
