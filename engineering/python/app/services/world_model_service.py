"""世界模型服务层.

对应 ADR-017（世界模型与 RL 模块）第 1 / 8 节。封装世界模型版本管理与
轨迹预测，为前端提供"直接预测（不走工作流）"能力。

职责
----
1. **版本管理**：列出 / 查询 / 注册 / 激活世界模型版本
2. **轨迹预测**：将前端结构化请求转换为 np.ndarray，调用
   ``TrajectoryPredictor.predict``，再将原始数组转换为结构化
   ``WorldModelPredictResponse``（含 ``TrajectoryStep`` 列表）

线程安全
--------
- 单例通过双重检查锁创建
- predictor 缓存（LRU limit=4）使用锁保护
- DB 写操作通过 SQLAlchemy 事务保证原子性，显式 commit()

错误处理风格（与 ExplainabilityService 对齐）：
- 模型未找到 → ModelNotFoundError
- 预测失败 → PredictionError
- 状态字典字段缺失 / 值越界 → InvalidStateError
"""

from __future__ import annotations

import logging
from datetime import datetime

import os
import threading
import time
from app.utils.time import utcnow
from typing import Any, cast

import numpy as np
from sqlalchemy import desc, select

from app.contracts.world_model import (
    DEFAULT_HORIZON,
    MAX_HORIZON,
    MIN_HORIZON,
    InvalidStateError,
    ModelNotFoundError,
    PredictionError,
    StateField,
    TrajectoryMetrics,
    TrajectoryStep,
    WorldModelError,
    WorldModelInfo,
    WorldModelPredictRequest,
    WorldModelPredictResponse,
    WorldModelVersion,
)
from app.database.models.world_model import WorldModelVersionORM
from app.services._shared.service_base import BaseSingletonService

logger = logging.getLogger(__name__)


# 状态/动作字段索引（与 StateField.all() / ActionField 顺序对齐）

_STATE_FIELD_ORDER: list[str] = StateField.all()
"""状态字段顺序（与 WorldModelNet 输入维度对齐）."""

_STATE_FIELD_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(_STATE_FIELD_ORDER)}


# 单例


def get_world_model_service() -> "WorldModelService":
    """获取 WorldModelService 单例（委托给 ``WorldModelService.get_instance``）."""
    return WorldModelService.get_instance()  # type: ignore[return-value]


def reset_world_model_service() -> None:
    """重置单例（仅供测试，委托给 ``WorldModelService.reset_instance``）."""
    WorldModelService.reset_instance()


# 服务实现


class WorldModelService(BaseSingletonService):
    """世界模型服务：版本管理 + 轨迹预测.

    内部组合 ``TrajectoryPredictor``（按 model_uri 缓存，LRU limit=4），
    自身管理 ``world_model_versions`` ORM 表。

    设计原则
    --------
    - 读操作（list/get）无锁
    - 写操作（register/set_active）通过 DB 事务保证原子性
    - predictor 缓存复用，避免重复加载权重
    - 预测结果不入库（按需生成，避免大数组膨胀数据库）
    """

    _PREDICTOR_CACHE_LIMIT = 4

    def __init__(self) -> None:
        self._predictor_cache: dict[str, Any] = {}
        self._predictor_lock = threading.Lock()

    # ── Session 管理 ──────────────────────────────────────────────────
    # ``_get_session`` 由 ``BaseSingletonService`` 提供。

    # ── 版本管理 ──────────────────────────────────────────────────────

    async def list_versions(
        self,
        *,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WorldModelVersion], int]:
        """列出版本记录（分页）.

        Args:
            active_only: 仅返回激活版本.
            limit: 每页数量.
            offset: 偏移量.

        Returns
        -------
        tuple[list[WorldModelVersion], int]
            (版本记录列表, 总数).
        """
        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(WorldModelVersionORM)
                count_stmt = select(WorldModelVersionORM)
                if active_only:
                    stmt = stmt.where(WorldModelVersionORM.is_active.is_(True))
                    count_stmt = count_stmt.where(WorldModelVersionORM.is_active.is_(True))
                stmt = stmt.order_by(desc(WorldModelVersionORM.created_at))
                stmt = stmt.limit(limit).offset(offset)
                result = await session.execute(stmt)
                orms = list(result.scalars().all())
                # 精确计数：无论是否 active_only 都用 func.count 查真实总数，
                # 避免 active_only=True 时 total=len(orms) 被分页截断导致前端分页错误
                from sqlalchemy import func

                total = (await session.execute(select(func.count()).select_from(count_stmt.subquery()))).scalar_one()
        finally:
            await session.close()

        versions = [self._orm_to_dataclass(o) for o in orms]
        return versions, total

    async def get_version(self, version: str) -> WorldModelVersion:
        """查询版本详情.

        Args:
            version: 版本号（semver）.

        Returns
        -------
        WorldModelVersion
            版本记录.

        Raises
        ------
        ModelNotFoundError
            版本不存在.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(WorldModelVersionORM).where(WorldModelVersionORM.version == version)
                result = await session.execute(stmt)
                orm = result.scalars().first()
        finally:
            await session.close()

        if orm is None:
            raise ModelNotFoundError(f"世界模型版本 '{version}' 不存在")
        return self._orm_to_dataclass(orm)

    async def register_version(
        self,
        *,
        version: str,
        model_uri: str,
        description: str = "",
        training_data_size: int = 0,
        prediction_horizon: int = DEFAULT_HORIZON,
        set_active: bool = True,
    ) -> WorldModelVersion:
        """注册新版本（可选激活）.

        Args:
            version: 版本号（semver）.
            model_uri: 模型 URI.
            description: 版本描述.
            training_data_size: 训练数据样本数.
            prediction_horizon: 训练时的预测步长.
            set_active: 是否设为激活版本（默认 True）.

        Returns
        -------
        WorldModelVersion
            新注册的版本记录.
        """
        if not MIN_HORIZON <= prediction_horizon <= MAX_HORIZON:
            raise WorldModelError(f"prediction_horizon 必须在 [{MIN_HORIZON}, {MAX_HORIZON}]: {prediction_horizon}")

        session = await self._get_session()
        try:
            async with session.begin():
                # 若 set_active，先清除其他激活版本
                if set_active:
                    active_stmt = select(WorldModelVersionORM).where(WorldModelVersionORM.is_active.is_(True))
                    active_result = await session.execute(active_stmt)
                    for active_orm in active_result.scalars().all():
                        active_orm.is_active = False

                orm = WorldModelVersionORM(
                    version=version,
                    model_uri=model_uri,
                    description=description,
                    training_data_size=training_data_size,
                    prediction_horizon=prediction_horizon,
                    is_active=set_active,
                    created_at=utcnow(),
                )
                session.add(orm)
                await session.flush()  # 获取 orm.id 但不提交事务
                await session.refresh(orm)
            # async with session.begin() 退出时自动 commit，无需手动 commit()
        except Exception as e:
            await session.rollback()
            raise WorldModelError(f"注册版本失败: {e}") from e
        finally:
            await session.close()

        logger.info(
            "世界模型版本已注册: version=%s model_uri=%s active=%s",
            version,
            model_uri,
            set_active,
        )
        return self._orm_to_dataclass(orm)

    async def set_active_version(self, version: str) -> WorldModelVersion:
        """切换激活版本.

        Args:
            version: 版本号.

        Returns
        -------
        WorldModelVersion
            激活后的版本记录.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(WorldModelVersionORM).where(WorldModelVersionORM.version == version)
                result = await session.execute(stmt)
                target = result.scalars().first()
                if target is None:
                    raise ModelNotFoundError(f"世界模型版本 '{version}' 不存在")
                # 清除其他激活版本
                active_stmt = select(WorldModelVersionORM).where(WorldModelVersionORM.is_active.is_(True))
                active_result = await session.execute(active_stmt)
                for active_orm in active_result.scalars().all():
                    active_orm.is_active = False
                target.is_active = True
                await session.flush()
                await session.refresh(target)
            # async with session.begin() 退出时自动 commit，无需手动 commit()
        except ModelNotFoundError:
            await session.rollback()
            raise
        except Exception as e:
            await session.rollback()
            raise WorldModelError(f"切换激活版本失败: {e}") from e
        finally:
            await session.close()

        logger.info("世界模型激活版本已切换: version=%s", version)
        return self._orm_to_dataclass(target)

    # ── 轨迹预测 ──────────────────────────────────────────────────────

    async def predict(self, request: WorldModelPredictRequest) -> WorldModelPredictResponse:
        """执行轨迹预测（不走工作流，直接调用插件层）.

        流程：
            1. 将 dict 状态/动作转换为 np.ndarray（按 StateField / ActionField 顺序）
            2. 获取或加载 ``TrajectoryPredictor``（LRU 缓存）
            3. 调用 ``predictor.predict()``
            4. 将原始数组转换为结构化 ``WorldModelPredictResponse``

        Args:
            request: 预测请求.

        Returns
        -------
        WorldModelPredictResponse
            结构化预测响应.
        """
        start_time = time.perf_counter()
        try:
            # 1. 动作字典 ndarray，扩展为 [horizon, action_dim]
            action_arr = self._action_dict_to_array(
                request.candidate_action,
                horizon=request.horizon,
                field_name="candidate_action",
            )

            # 2. 获取或加载 predictor
            predictor = self._get_or_load_predictor(request.model_uri)

            # 3. 路由判断：融合模式 vs 原始模式
            if request.unified_state is not None:
                # 融合模式：unified_state 路径（ADR-020 思路 1）
                # predictor.predict 内部会校验 config.use_fusion 是否已开启
                prediction = predictor.predict(
                    unified_state=request.unified_state,
                    candidate_action=action_arr,
                    horizon=request.horizon,
                )
            else:
                # 原始模式：current_state 字段拼接路径（向后兼容）
                current_state_arr = self._state_dict_to_array(request.current_state, field_name="current_state")
                prediction = predictor.predict(
                    current_state=current_state_arr,
                    candidate_action=action_arr,
                    horizon=request.horizon,
                )

            # 4. 原始数组 结构化响应
            response = self._build_response(
                prediction=prediction,
                model_uri=request.model_uri,
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            mode = "fusion" if request.unified_state is not None else "legacy"
            logger.info(
                "世界模型预测完成: model_uri=%s horizon=%d mode=%s time=%.2fms",
                request.model_uri,
                request.horizon,
                mode,
                elapsed_ms,
            )
            return response

        except ModelNotFoundError:
            raise
        except (ValueError, KeyError) as exc:
            raise InvalidStateError(f"状态/动作字段不合法: {exc}") from exc
        except RuntimeError as exc:
            raise PredictionError(f"预测失败: {exc}") from exc
        except Exception as exc:
            raise PredictionError(f"预测过程中发生未预期错误: {exc}") from exc

    # ── 内部辅助方法 ──────────────────────────────────────────────────

    def _orm_to_dataclass(self, orm: WorldModelVersionORM) -> WorldModelVersion:
        """ORM → 契约层 dataclass."""
        return WorldModelVersion(
            version=str(orm.version),
            model_uri=str(orm.model_uri),
            description=str(orm.description or ""),
            created_at=cast(datetime, orm.created_at) if orm.created_at else utcnow(),
            training_data_size=int(orm.training_data_size),
            prediction_horizon=int(orm.prediction_horizon),
            is_active=bool(orm.is_active),
        )

    def _state_dict_to_array(self, state_dict: dict[str, float], *, field_name: str) -> np.ndarray:
        """状态字典 → ndarray [state_dim].

        按-StateField.all() 顺序提取值，缺失字段报错.
        """
        if not state_dict:
            raise InvalidStateError(f"{field_name} 不能为空")
        values = []
        for field in _STATE_FIELD_ORDER:
            if field not in state_dict:
                raise InvalidStateError(f"{field_name} 缺少字段 '{field}'（必需字段: {_STATE_FIELD_ORDER}）")
            value = state_dict[field]
            try:
                values.append(float(value))
            except (TypeError, ValueError) as exc:
                raise InvalidStateError(f"{field_name}['{field}'] 不是合法数值: {value}") from exc
        return np.asarray(values, dtype=np.float32)

    def _action_dict_to_array(
        self,
        action_dict: dict[str, float],
        *,
        horizon: int,
        field_name: str,
    ) -> np.ndarray:
        """动作字典 → ndarray [horizon, action_dim].

        单步动作扩展为 horizon 步（同一动作重复 horizon 次）.
        """
        if not action_dict:
            raise InvalidStateError(f"{field_name} 不能为空")
        # 动作字段顺序与 ActionField.all() 对齐
        from app.contracts.world_model import ActionField

        action_order = ActionField.all()
        values = []
        for field in action_order:
            if field not in action_dict:
                raise InvalidStateError(f"{field_name} 缺少字段 '{field}'（必需字段: {action_order}）")
            value = action_dict[field]
            try:
                values.append(float(value))
            except (TypeError, ValueError) as exc:
                raise InvalidStateError(f"{field_name}['{field}'] 不是合法数值: {value}") from exc
        single_step = np.asarray(values, dtype=np.float32)
        # 扩展为 [horizon, action_dim]
        return np.tile(single_step, (horizon, 1))

    def _get_or_load_predictor(self, model_uri: str):
        """获取或加载 TrajectoryPredictor（LRU 缓存，limit=4）."""
        # 快速路径
        predictor = self._predictor_cache.get(model_uri)
        if predictor is not None:
            return predictor

        with self._predictor_lock:
            predictor = self._predictor_cache.get(model_uri)
            if predictor is not None:
                return predictor

            # 延迟导入，避免循环依赖
            from app.plugins.world_model.predictor import TrajectoryPredictor

            predictor = TrajectoryPredictor(
                config=self._build_world_model_config(),
                device="auto",
            )
            # 权重路径解析：从 model_uri 推导（v1 使用随机初始化，仅接口验证）
            weights_path = self._resolve_weights_path(model_uri)
            try:
                predictor.load_model(model_uri=model_uri, weights_path=weights_path)
            except (RuntimeError, OSError, KeyError) as exc:
                raise ModelNotFoundError(f"加载世界模型失败: model_uri={model_uri} error={exc}") from exc

            # LRU 淘汰
            if len(self._predictor_cache) >= self._PREDICTOR_CACHE_LIMIT:
                oldest = next(iter(self._predictor_cache))
                self._predictor_cache.pop(oldest, None)
            self._predictor_cache[model_uri] = predictor
            return predictor

    def _build_world_model_config(self):
        """从环境变量构建 WorldModelConfig.

        支持 ADR-020 思路 1 融合模式配置注入，与项目既有
        ``os.environ.get`` 模式（LNN_JWT_SECRET / LNN_TRAINING_DEVICE 等）对齐：

        - ``WORLD_MODEL_USE_FUSION``: 启用融合模式（"true"/"1"/"yes"/"on" 为真）
        - ``WORLD_MODEL_FEATURE_DIM``: 几何特征向量维度（默认 32）
        - ``WORLD_MODEL_D_MODEL``: 编码器输出维度（默认 64）
        - ``WORLD_MODEL_FUSED_DIM``: 融合 embedding 维度（默认 128）

        其他配置项（state_dim=8, action_dim=4, hidden_dim, num_lstm_layers
        等）使用 WorldModelConfig 默认值，保持与 ADR-017 输出契约一致。

        Returns
        -------
        WorldModelConfig
            网络配置实例.
        """
        from app.plugins.world_model.net import WorldModelConfig

        def _env_bool(key: str, default: bool = False) -> bool:
            val = os.environ.get(key)
            if val is None:
                return default
            return val.strip().lower() in ("true", "1", "yes", "on")

        def _env_int(key: str, default: int) -> int:
            val = os.environ.get(key)
            if val is None:
                return default
            try:
                return int(val)
            except ValueError:
                logger.warning(
                    "环境变量 %s=%r 不是合法整数，使用默认值 %d",
                    key,
                    val,
                    default,
                )
                return default

        config = WorldModelConfig(
            # W 引擎验证修复：默认关闭融合模式。原始 /predict 路径（StateField
            # 8 维 + action 4 维 = 12 维输入）与融合模式的 132 维输入层不匹配，
            # 默认 True 会让所有未提供 unified_state 的预测崩溃。融合调用方
            # （提供 unified_state）显式设置 WORLD_MODEL_USE_FUSION=true。
            use_fusion=_env_bool("WORLD_MODEL_USE_FUSION", False),
            feature_dim=_env_int("WORLD_MODEL_FEATURE_DIM", 32),
            d_model=_env_int("WORLD_MODEL_D_MODEL", 64),
            fused_dim=_env_int("WORLD_MODEL_FUSED_DIM", 128),
        )
        if config.use_fusion:
            logger.info(
                "世界模型融合模式已启用（ADR-020 思路 1）: feature_dim=%d d_model=%d fused_dim=%d",
                config.feature_dim,
                config.d_model,
                config.fused_dim,
            )
        return config

    def _resolve_weights_path(self, model_uri: str) -> str | None:
        """从模型 URI 解析权重文件路径.

        v1 实现：返回 None（使用随机初始化，仅用于接口验证）.
        实际部署中应调用 ModelRegistry.resolve(model_uri).
        """
        return None

    def _build_response(
        self,
        *,
        prediction: Any,
        model_uri: str,
    ) -> WorldModelPredictResponse:
        """将 TrajectoryPrediction（原始数组）→ 结构化 WorldModelPredictResponse.

        字段映射：
            - predicted_trajectory [horizon, state_dim] → list[TrajectoryStep]
              - predicted_state: 按字段名还原为 dict
              - chatter_probability: StateField.CHATTER_PROBABILITY 列
              - tool_wear_increment: 相邻步 StateField.TOOL_WEAR 差分（首步为 0）
              - surface_roughness: 从 StateField.VIBRATION_RMS 推导（×0.1）
              - confidence: 根据后端动态（torch=0.8, numpy 回退=0.2）
            - trajectory_metrics [3] → TrajectoryMetrics
              - [0] chatter_peak, [1] max_wear, [2] avg_quality
        """
        traj = prediction.predicted_trajectory
        if isinstance(traj, np.ndarray):
            traj_arr = traj
        else:
            traj_arr = np.asarray(traj, dtype=np.float32)

        # 确保 2D [horizon, state_dim]
        if traj_arr.ndim == 3:
            traj_arr = traj_arr[0]  # 去掉 batch 维

        horizon = traj_arr.shape[0]
        state_dim = traj_arr.shape[1] if traj_arr.ndim == 2 else 0

        # 根据后端类型动态决定 confidence：
        # - numpy 回退路径输出为随机噪声，confidence 应显著降低以警示下游消费方
        # - torch 路径保持 v1 固定置信度
        model_info_dict = prediction.model_info or {}
        backend = model_info_dict.get("backend", "torch")
        if backend == "numpy":
            step_confidence = 0.2
        else:
            step_confidence = 0.8

        # 字段索引
        chatter_idx = _STATE_FIELD_INDEX.get(StateField.CHATTER_PROBABILITY, state_dim - 1)
        wear_idx = _STATE_FIELD_INDEX.get(StateField.TOOL_WEAR, 0)
        vib_idx = _STATE_FIELD_INDEX.get(StateField.VIBRATION_RMS, 0)

        steps: list[TrajectoryStep] = []
        prev_wear = float(traj_arr[0, wear_idx]) if state_dim > 0 else 0.0
        for step_idx in range(horizon):
            row = traj_arr[step_idx] if state_dim > 0 else np.array([])
            # 还原状态字典
            state_dict: dict[str, float] = {}
            for field_idx, field_name in enumerate(_STATE_FIELD_ORDER):
                if field_idx < len(row):
                    state_dict[field_name] = float(row[field_idx])

            chatter_prob = (
                float(row[chatter_idx])
                if state_dim > 0 and 0.0 <= float(row[chatter_idx]) <= 1.0
                else max(0.0, min(1.0, float(row[chatter_idx]) if state_dim > 0 else 0.0))
            )
            cur_wear = float(row[wear_idx]) if state_dim > 0 else 0.0
            wear_inc = max(0.0, cur_wear - prev_wear) if step_idx > 0 else 0.0
            prev_wear = cur_wear

            vib_rms = float(row[vib_idx]) if state_dim > 0 else 0.0
            surface_roughness = max(0.0, vib_rms * 0.1)  # 简化映射

            steps.append(
                TrajectoryStep(
                    step=step_idx,
                    predicted_state=state_dict,
                    chatter_probability=chatter_prob,
                    tool_wear_increment=wear_inc,
                    surface_roughness=surface_roughness,
                    confidence=step_confidence,
                )
            )

        # 轨迹汇总指标
        chatter_values = [s.chatter_probability for s in steps]
        mean_chatter = sum(chatter_values) / len(chatter_values) if chatter_values else 0.0
        max_chatter = max(chatter_values) if chatter_values else 0.0
        cumulative_wear = sum(s.tool_wear_increment for s in steps)
        final_roughness = steps[-1].surface_roughness if steps else 0.0

        trajectory_metrics = TrajectoryMetrics(
            mean_chatter_probability=mean_chatter,
            max_chatter_probability=max_chatter,
            cumulative_tool_wear=cumulative_wear,
            final_surface_roughness=final_roughness,
        )

        # 模型信息
        model_info_dict = prediction.model_info or {}
        world_model_version = model_uri.rsplit("/", 1)[-1] if "/" in model_uri else "1.0.0"
        model_info = WorldModelInfo(
            world_model_version=world_model_version,
            training_data_size=int(model_info_dict.get("training_data_size", 0)),
            prediction_horizon=int(model_info_dict.get("prediction_horizon", prediction.horizon)),
            uncertainty_estimate=float(model_info_dict.get("uncertainty_estimate", 0.2)),
        )

        return WorldModelPredictResponse(
            predicted_trajectory=steps,
            trajectory_metrics=trajectory_metrics,
            model_info=model_info,
        )


__all__ = [
    "WorldModelService",
    "get_world_model_service",
    "reset_world_model_service",
]
