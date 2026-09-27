"""RL Agent 服务层.

对应 ADR-017（世界模型与 RL 模块）第 2 / 8 节。封装 RL 策略版本管理、
决策推理与训练状态控制，为前端提供"直接决策（不走工作流）"与训练
控制能力。

职责
----
1. **版本管理**：列出 / 查询 / 注册 / 激活 RL 策略版本
2. **决策推理**：将前端结构化请求转换为 np.ndarray，调用
   ``PolicyNet`` + ``ValueNet`` + ``SafetyShield``，输出结构化
   ``RLActResponse``（含推荐动作 + 候选动作评估 + 策略元信息）
3. **训练控制**：查询 / 启动 / 停止训练——启动基于真实轨迹回放环境
   （``ReplayOfflineEnvironment`` + ``PPOTrainer``）在后台线程执行完整
   PPO 循环，数据缺失时诚实拒绝（学术诚信）

线程安全
--------
- 单例通过双重检查锁创建
- 策略/值网络缓存（LRU limit=4）使用锁保护
- 推理在 ``_infer_lock`` 保护下串行（与 RLAgentPlugin 对齐）
- DB 写操作通过 SQLAlchemy 事务保证原子性，显式 commit()

错误处理风格（与 WorldModelService / ExplainabilityService 对齐）：
- 策略未找到 → PolicyNotFoundError
- 训练数据缺失 → TrainingError（学术诚信：拒绝无数据/合成数据训练）
- 安全约束全违反 → SafetyViolationError
- 策略推理失败 → PolicyError
- 训练失败 → TrainingError
- 状态字典字段缺失 / 值越界 → ValueError → INVALID_REQUEST

工程现实约束
------------
- v1 仅离线 RL：策略权重来自离线训练，本服务层仅做前向推理 + 安全过滤
- SafetyShield 是硬约束层，不可被策略覆盖
- 输出动作仅供 CAM 验证层与决策日志参考，不直接接 CNC 控制器
- 物理执行需"持证操作员 + 导师签字 + 保险"，本服务层不涉及
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from app.utils.time import utcnow
from typing import Any

import numpy as np
from sqlalchemy import desc, select

from app.contracts.rl_agent import (
    ActionEvaluation,
    PolicyAlgorithm,
    PolicyError,
    PolicyInfo,
    PolicyNotFoundError,
    PolicyVersion,
    RLActError,
    RLActRequest,
    RLActResponse,
    RecommendedAction,
    SafetyConstraintsSpec,
    SafetyViolationError,
    TrainingError,
    TrainingStartRequest,
    TrainingStatus,
    TrainingStatusInfo,
)
from app.contracts.world_model import ActionField, StateField
from app.database.models.rl_agent import (
    RLAgentPolicyVersionORM,
    RLAgentTrainingRunORM,
)
from app.plugins.rl_agent.policy import PolicyConfig, PolicyNet
from app.plugins.rl_agent.training.environment import ReplayOfflineEnvironment
from app.plugins.rl_agent.training.trainer import PPOTrainer, TrainingConfig
from app.plugins.rl_agent.value import ValueConfig, ValueNet
from app.services._shared.service_base import BaseSingletonService

from app.services._agent_helpers import (
    _orm_to_dataclass,
    _training_run_to_status_info,
    _state_dict_to_array,
    _action_dict_to_array,
    _action_array_to_dict,
    _extract_state_field,
    _load_weights,
    _extract_action,
    _extract_value,
    _rank_candidates,
    _build_reasoning,
)

logger = logging.getLogger(__name__)


# 状态/动作字段索引（与 StateField.all() / ActionField.all() 顺序对齐）

_STATE_FIELD_ORDER: list[str] = StateField.all()
"""状态字段顺序（与 PolicyNet 输入维度对齐）."""

_ACTION_FIELD_ORDER: list[str] = ActionField.all()
"""动作字段顺序（与 PolicyNet 输出维度对齐）."""

_STATE_FIELD_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(_STATE_FIELD_ORDER)}

# 活跃训练线程注册表（run_id → PPOTrainer），供 stop_training 联动 request_stop
_active_trainers: dict[str, PPOTrainer] = {}
_active_trainers_lock = threading.Lock()

# 训练轨迹数据集默认路径（可用环境变量 RL_AGENT_TRAINING_DATA 覆盖）
_DEFAULT_TRAINING_DATA = Path(__file__).resolve().parents[4] / "data" / "rl_agent" / "trajectories.jsonl"


# 单例


def get_rl_agent_service() -> "RLAgentService":
    """获取 RLAgentService 单例（委托给 ``RLAgentService.get_instance``）."""
    return RLAgentService.get_instance()  # type: ignore[return-value]


def reset_rl_agent_service() -> None:
    """重置单例（仅供测试，委托给 ``RLAgentService.reset_instance``）."""
    RLAgentService.reset_instance()


# 服务实现


class RLAgentService(BaseSingletonService):
    """RL Agent 服务：版本管理 + 决策推理 + 训练控制.

    内部组合 ``PolicyNet`` + ``ValueNet`` + ``SafetyShield``（按 model_uri
    缓存网络，LRU limit=4），自身管理 ``rl_agent_policy_versions`` 与
    ``rl_agent_training_runs`` ORM 表。

    设计原则
    --------
    - 读操作（list/get）无锁
    - 写操作（register/set_active/start/stop）通过 DB 事务保证原子性
    - 网络 + SafetyShield 缓存复用，避免重复加载权重
    - 决策结果不入库（按需生成）
    - 训练状态记录入库（供前端轮询）
    """

    _NET_CACHE_LIMIT = 4
    """策略/值网络 LRU 缓存上限."""

    def __init__(self) -> None:
        self._policy_cache: dict[str, Any] = {}
        self._value_cache: dict[str, Any] = {}
        self._cache_lock = threading.Lock()
        self._shield_cache: dict[str, SafetyConstraintsSpec] = {}
        # 推理串行锁：NumPy 回退模式下避免随机状态污染（与 RLAgentPlugin 对齐）
        self._infer_lock = threading.Lock()
        # 记录最后一次合法动作（跨请求维持变化率约束的连续性）
        self._last_action: np.ndarray | None = None
        self._last_action_lock = threading.Lock()
        # 各 model_uri 最近一次权重加载结果（True=真实权重，False=随机初始化）
        self._weights_loaded: dict[str, bool] = {}

    # ── 版本管理 ──────────────────────────────────────────────────────

    async def list_versions(
        self,
        *,
        active_only: bool = False,
        algorithm: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PolicyVersion], int]:
        """列出策略版本记录（分页）.

        Args:
            active_only: 仅返回激活版本.
            algorithm: 按算法过滤（ppo/dqn/sac）.
            limit: 每页数量.
            offset: 偏移量.

        Returns
        -------
        tuple[list[PolicyVersion], int]
            (版本记录列表, 总数).
        """
        from sqlalchemy import func

        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(RLAgentPolicyVersionORM)
                count_stmt = select(func.count(RLAgentPolicyVersionORM.id))
                if active_only:
                    stmt = stmt.where(RLAgentPolicyVersionORM.is_active.is_(True))
                    count_stmt = count_stmt.where(RLAgentPolicyVersionORM.is_active.is_(True))
                if algorithm:
                    stmt = stmt.where(RLAgentPolicyVersionORM.algorithm == algorithm)
                    count_stmt = count_stmt.where(RLAgentPolicyVersionORM.algorithm == algorithm)
                stmt = stmt.order_by(desc(RLAgentPolicyVersionORM.created_at))
                stmt = stmt.limit(limit).offset(offset)
                result = await session.execute(stmt)
                orms = list(result.scalars().all())
                total = (await session.execute(count_stmt)).scalar_one()
        finally:
            await session.close()

        versions = [self._orm_to_dataclass(o) for o in orms]
        return versions, total

    async def get_version(self, version: str) -> PolicyVersion:
        """查询版本详情.

        Args:
            version: 版本号（semver）.

        Returns
        -------
        PolicyVersion
            版本记录.

        Raises
        ------
        PolicyNotFoundError
            版本不存在.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(RLAgentPolicyVersionORM).where(RLAgentPolicyVersionORM.version == version)
                result = await session.execute(stmt)
                orm = result.scalars().first()
        finally:
            await session.close()

        if orm is None:
            raise PolicyNotFoundError(f"RL 策略版本 '{version}' 不存在")
        return self._orm_to_dataclass(orm)

    async def register_version(
        self,
        *,
        version: str,
        model_uri: str,
        algorithm: str = PolicyAlgorithm.PPO,
        description: str = "",
        training_episodes: int = 0,
        training_steps: int = 0,
        mean_reward: float = 0.0,
        set_active: bool = True,
    ) -> PolicyVersion:
        """注册新策略版本（可选激活）.

        Args:
            version: 版本号（semver）.
            model_uri: 模型 URI（``model://rl_agent/<version>``）.
            algorithm: 策略算法（``PolicyAlgorithm`` 常量）.
            description: 版本描述.
            training_episodes: 训练 episode 数.
            training_steps: 训练步数.
            mean_reward: 训练时平均 episode 奖励.
            set_active: 是否设为激活版本（默认 True）.

        Returns
        -------
        PolicyVersion
            新注册的版本记录.
        """
        if not PolicyAlgorithm.is_valid(algorithm):
            raise ValueError(f"algorithm 不合法: {algorithm}")
        if not version:
            raise ValueError("version 不能为空")
        if not model_uri:
            raise ValueError("model_uri 不能为空")

        session = await self._get_session()
        try:
            async with session.begin():
                # 若 set_active，先清除其他激活版本
                if set_active:
                    active_stmt = select(RLAgentPolicyVersionORM).where(RLAgentPolicyVersionORM.is_active.is_(True))
                    active_result = await session.execute(active_stmt)
                    for active_orm in active_result.scalars().all():
                        active_orm.is_active = False

                orm = RLAgentPolicyVersionORM(
                    version=version,
                    model_uri=model_uri,
                    algorithm=algorithm,
                    description=description,
                    training_episodes=training_episodes,
                    training_steps=training_steps,
                    mean_reward=mean_reward,
                    is_active=set_active,
                    created_at=utcnow(),
                )
                session.add(orm)
            await session.commit()
            await session.refresh(orm)
        except Exception as e:
            await session.rollback()
            raise RLActError(f"注册策略版本失败: {e}") from e
        finally:
            await session.close()

        logger.info(
            "RL 策略版本已注册: version=%s algorithm=%s active=%s",
            version,
            algorithm,
            set_active,
        )
        return self._orm_to_dataclass(orm)

    async def set_active_version(self, version: str) -> PolicyVersion:
        """切换激活版本.

        Args:
            version: 版本号.

        Returns
        -------
        PolicyVersion
            激活后的版本记录.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(RLAgentPolicyVersionORM).where(RLAgentPolicyVersionORM.version == version)
                result = await session.execute(stmt)
                target = result.scalars().first()
                if target is None:
                    raise PolicyNotFoundError(f"RL 策略版本 '{version}' 不存在")
                # 清除其他激活版本
                active_stmt = select(RLAgentPolicyVersionORM).where(RLAgentPolicyVersionORM.is_active.is_(True))
                active_result = await session.execute(active_stmt)
                for active_orm in active_result.scalars().all():
                    active_orm.is_active = False
                target.is_active = True
            await session.commit()
            await session.refresh(target)
        except PolicyNotFoundError:
            await session.rollback()
            raise
        except Exception as e:
            await session.rollback()
            raise RLActError(f"切换激活版本失败: {e}") from e
        finally:
            await session.close()

        logger.info("RL 策略激活版本已切换: version=%s", version)
        return self._orm_to_dataclass(target)

    # ── 决策推理 ──────────────────────────────────────────────────────

    async def act(self, request: RLActRequest) -> RLActResponse:
        """执行 RL 决策（不走工作流，直接调用网络层）.

        流程：
            1. 将 ``current_state`` dict → ndarray（按 StateField 顺序）
            2. 将 ``candidate_actions`` list[dict] → list[ndarray]
            3. 获取或加载 ``PolicyNet`` + ``ValueNet`` + ``SafetyShield``
            4. 策略前向 → 原始推荐动作 → 安全过滤 → 推荐动作
            5. 对每个候选动作：安全过滤 + 价值评估 → ``ActionEvaluation``
            6. 按 ``optimization_target`` 排序候选动作，生成推荐理由
            7. 构建 ``PolicyInfo`` 并返回 ``RLActResponse``

        Args:
            request: 决策请求.

        Returns
        -------
        RLActResponse
            结构化决策响应.

        Raises
        ------
        PolicyNotFoundError
            ``model_uri`` 未注册.
        SafetyViolationError
            所有候选动作均被 SafetyShield 过滤.
        PolicyError
            策略推理失败.
        """
        start_time = time.perf_counter()
        try:
            # 1. 状态字典 ndarray
            state_arr = self._state_dict_to_array(request.current_state, field_name="current_state")

            # 2. 候选动作 list[dict] list[ndarray]
            candidate_action_arrs = [
                self._action_dict_to_array(act, field_name=f"candidate_actions[{idx}]")
                for idx, act in enumerate(request.candidate_actions)
            ]

            # 3. 获取或加载网络 + 安全盾
            policy_net = self._get_or_load_policy(request.model_uri)
            value_net = self._get_or_load_value(request.model_uri)
            shield = self._get_or_create_shield(request.safety_constraints)

            # 4. 策略前向 原始推荐动作
            with self._infer_lock:
                policy_out = policy_net(self._to_net_input(policy_net, state_arr))
                raw_action = self._extract_action(policy_out)
                value_out = value_net(self._to_net_input(value_net, state_arr))
                state_value = self._extract_value(value_out)

            # 安全过滤推荐动作
            ref_action = self._get_last_action()
            safe_action, safety_result = shield.filter(raw_action, prev_action=ref_action)
            self._set_last_action(safe_action)

            # 5. 评估候选动作
            action_evaluations: list[ActionEvaluation] = []
            for idx, cand_arr in enumerate(candidate_action_arrs):
                cand_safe, cand_result = shield.filter(cand_arr, prev_action=ref_action)
                # v1 简化：q_value ≈ V(s)（PPO 离线 RL 中 Q ≈ V(s)）
                # v2 可引入 Q 网络或 (state, action) 联合价值评估
                q_value = state_value
                expected_return = q_value * (0.0 if cand_result.violated else 1.0)

                # 从 current_state 提取预测颤振概率 / 刀具磨损
                chatter_prob = self._extract_state_field(state_arr, StateField.CHATTER_PROBABILITY, default=0.0)
                tool_wear = self._extract_state_field(state_arr, StateField.TOOL_WEAR, default=0.0)

                action_evaluations.append(
                    ActionEvaluation(
                        action=self._action_array_to_dict(cand_safe),
                        expected_return=expected_return,
                        predicted_chatter_prob=max(0.0, min(1.0, float(chatter_prob))),
                        predicted_tool_wear=max(0.0, float(tool_wear)),
                        safety_violation=cand_result.violated,
                        q_value=q_value,
                    )
                )

            # 6. 选择推荐动作：优先安全动作，其次按 optimization_target 排序
            recommended_action = self._select_recommended_action(
                safe_action=safe_action,
                safety_result=safety_result,
                action_evaluations=action_evaluations,
                optimization_target=request.optimization_target,
            )

            # 7. 构建策略元信息
            policy_info = await self._build_policy_info(
                model_uri=request.model_uri,
                exploration_rate=0.1,  # v1 固定探索率（离线推理无探索）
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "RL 决策完成: model_uri=%s candidates=%d violated=%s time=%.2fms",
                request.model_uri,
                len(action_evaluations),
                safety_result.violated,
                elapsed_ms,
            )

            return RLActResponse(
                recommended_action=recommended_action,
                action_evaluation=action_evaluations,
                policy_info=policy_info,
            )

        except PolicyNotFoundError:
            raise
        except (ValueError, KeyError) as exc:
            raise ValueError(f"状态/动作字段不合法: {exc}") from exc
        except SafetyViolationError:
            raise
        except RuntimeError as exc:
            raise PolicyError(f"策略推理失败: {exc}") from exc
        except RLActError:
            raise
        except Exception as exc:
            raise PolicyError(f"决策过程中发生未预期错误: {exc}") from exc

    # ── 训练控制 ──────────────────────────────────────────────────────

    async def get_training_status(self) -> TrainingStatusInfo:
        """查询训练状态（最新一条训练记录）.

        Returns
        -------
        TrainingStatusInfo
            训练状态信息. 无训练记录时返回 IDLE 状态.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                stmt = select(RLAgentTrainingRunORM).order_by(desc(RLAgentTrainingRunORM.created_at)).limit(1)
                result = await session.execute(stmt)
                orm = result.scalars().first()
        finally:
            await session.close()

        if orm is None:
            return TrainingStatusInfo(
                status=TrainingStatus.IDLE,
                current_step=0,
                max_steps=100000,
                current_episode=0,
            )
        return self._training_run_to_status_info(orm)

    async def start_training(self, request: TrainingStartRequest) -> TrainingStatusInfo:
        """启动训练：真实训练循环（轨迹回放环境 + PPO），后台线程执行.

        数据源与学术诚信约束
        --------------------
        训练需要真实历史轨迹数据集（JSONL，每行含 ``state`` / ``action`` /
        ``next_state`` 数组，见 ``ReplayOfflineEnvironment.from_jsonl``），
        通过环境变量 ``RL_AGENT_TRAINING_DATA`` 指定（缺省检查
        ``<仓库>/data/rl_agent/trajectories.jsonl``）。数据集缺失时抛出
        ``TrainingError`` 拒绝创建 RUNNING 记录——绝不制造"假训练"
        （学术诚信要求）。

        执行模型
        --------
        1. 构建环境与网络（同步，失败即拒绝）
        2. 创建 status=RUNNING 的训练记录
        3. 启动 daemon 线程执行 ``PPOTrainer.train()``；结束时回主循环
           落库（COMPLETED / FAILED + 指标 JSON）
        4. ``stop_training`` 置 STOPPING 并联动 ``trainer.request_stop()``

        Args:
            request: 训练启动请求（max_steps / seed / algorithm / optimization_target）.

        Raises
        ------
        TrainingError
            训练数据缺失、环境/网络构建失败或训练记录创建失败.
        """
        # 1. 构建环境（数据缺失 → 诚实拒绝，不触碰数据库）
        trainer = self._prepare_trainer(request)
        started_at = utcnow()

        # 2. 创建 RUNNING 记录
        session = await self._get_session()
        run_id: str | None = None
        try:
            async with session.begin():
                run = RLAgentTrainingRunORM(
                    status=TrainingStatus.RUNNING,
                    current_step=0,
                    current_episode=0,
                    total_steps_target=request.max_steps,
                    started_at=started_at,
                )
                session.add(run)
                await session.flush()
                run_id = run.id
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise TrainingError(f"创建训练记录失败: {e}") from e
        finally:
            await session.close()
        assert run_id is not None

        # 3. 注册并启动训练线程（daemon：进程退出不悬挂）
        main_loop = asyncio.get_running_loop()
        self._register_active_trainer(run_id, trainer)
        thread = threading.Thread(
            target=self._training_worker,
            args=(run_id, trainer, main_loop),
            daemon=True,
            name="rl-train-" + run_id,
        )
        thread.start()
        logger.info("RL 训练已启动: run_id=%s 目标步数=%d", run_id, request.max_steps)

        return TrainingStatusInfo(
            status=TrainingStatus.RUNNING,
            current_step=0,
            max_steps=request.max_steps,
            current_episode=0,
            metrics=None,
            started_at=started_at,
            finished_at=None,
            error_message=None,
        )

    def _prepare_trainer(self, request: TrainingStartRequest) -> PPOTrainer:
        """构建 PPOTrainer（网络 + 回放环境）.

        Raises:
            TrainingError: 训练数据缺失或维度不合规（诚实拒绝，不创建记录）.
        """
        seed = request.seed if request.seed is not None else 0
        state_dim = len(_STATE_FIELD_ORDER)
        action_dim = len(_ACTION_FIELD_ORDER)

        data_path = os.environ.get("RL_AGENT_TRAINING_DATA") or str(_DEFAULT_TRAINING_DATA)
        if not Path(data_path).is_file():
            raise TrainingError(
                "训练数据不可用：未找到真实轨迹数据集（RL_AGENT_TRAINING_DATA 未设置，"
                "或缺省路径不存在: " + str(_DEFAULT_TRAINING_DATA) + "）。"
                "拒绝在无数据或合成数据上训练（学术诚信要求）。"
                "建议操作：将历史轨迹导出为 JSONL（每行含 state/action/next_state 数组，"
                "可选 episode 分组键），设置 RL_AGENT_TRAINING_DATA 指向该文件后重试；"
                "在此之前可使用 /act 离线推理进行决策（响应中 weights_loaded=false 表示权重未经训练）。"
            )
        try:
            env = ReplayOfflineEnvironment.from_jsonl(
                data_path,
                state_dim=state_dim,
                action_dim=action_dim,
                seed=seed,
            )
        except (FileNotFoundError, ValueError) as e:
            raise TrainingError(f"训练环境构建失败: {e}") from e

        config = TrainingConfig(
            max_steps=request.max_steps,
            seed=seed,
            state_dim=state_dim,
            action_dim=action_dim,
        )
        policy_net = PolicyNet(
            PolicyConfig(state_dim=state_dim, action_dim=action_dim, hidden_dim=config.hidden_dim, seed=seed)
        )
        value_net = ValueNet(ValueConfig(state_dim=state_dim, hidden_dim=config.hidden_dim, seed=seed))
        trainer = PPOTrainer(config=config)
        trainer.setup(policy_net=policy_net, value_net=value_net, env=env)
        return trainer

    def _training_worker(self, run_id: str, trainer: PPOTrainer, main_loop: asyncio.AbstractEventLoop) -> None:
        """训练线程主体：执行训练循环，结束后回主循环落库终态."""
        try:
            trainer.train()
            metrics = trainer.metrics
            payload = {
                "step": metrics.step,
                "episode": metrics.episode,
                "policy_loss": metrics.policy_loss,
                "value_loss": metrics.value_loss,
                "entropy": metrics.entropy,
                "mean_reward": metrics.mean_reward,
                "mean_value": metrics.mean_value,
                "epsilon": metrics.epsilon,
                "elapsed_seconds": metrics.elapsed_seconds,
            }
            status = TrainingStatus.COMPLETED
            error_message: str | None = None
            if trainer.stop_requested:
                logger.info("训练因外部停止请求而结束: run_id=%s step=%d", run_id, metrics.step)
        except Exception as e:
            logger.exception("RL 训练线程异常: run_id=%s", run_id)
            status = TrainingStatus.FAILED
            payload = None
            error_message = str(e)
        finally:
            self._unregister_active_trainer(run_id)

        future = asyncio.run_coroutine_threadsafe(
            self._finalize_training(run_id, status=status, metrics_payload=payload, error_message=error_message),
            main_loop,
        )
        try:
            future.result(timeout=30)
        except Exception:
            logger.error("训练终态落库失败: run_id=%s", run_id, exc_info=True)

    async def _finalize_training(
        self,
        run_id: str,
        *,
        status: str,
        metrics_payload: dict[str, Any] | None,
        error_message: str | None,
    ) -> None:
        """将训练终态（COMPLETED/FAILED + 指标）写回训练记录."""
        session = await self._get_session()
        try:
            async with session.begin():
                target = await session.get(RLAgentTrainingRunORM, run_id)
                if target is None:
                    logger.warning("训练记录不存在，忽略终态落库: run_id=%s", run_id)
                    return
                target.status = status
                if metrics_payload is not None:
                    target.current_step = int(metrics_payload.get("step", 0))
                    target.current_episode = int(metrics_payload.get("episode", 0))
                    target.metrics_json = json.dumps(metrics_payload, ensure_ascii=False)
                if error_message:
                    target.error_message = error_message
                target.finished_at = utcnow()
            await session.commit()
            logger.info("训练终态已落库: run_id=%s status=%s", run_id, status)
        except Exception as e:
            await session.rollback()
            logger.error("训练终态落库异常: run_id=%s err=%s", run_id, e)
        finally:
            await session.close()

    @staticmethod
    def _register_active_trainer(run_id: str, trainer: PPOTrainer) -> None:
        with _active_trainers_lock:
            _active_trainers[run_id] = trainer

    @staticmethod
    def _unregister_active_trainer(run_id: str) -> None:
        with _active_trainers_lock:
            _active_trainers.pop(run_id, None)

    async def stop_training(self) -> TrainingStatusInfo:
        """请求停止训练（状态置为 STOPPING，由训练线程检测后终止）.

        Returns
        -------
        TrainingStatusInfo
            训练状态信息.

        Raises
        ------
        TrainingError
            无运行中训练或更新失败.
        """
        session = await self._get_session()
        try:
            async with session.begin():
                running_stmt = (
                    select(RLAgentTrainingRunORM).where(RLAgentTrainingRunORM.status == TrainingStatus.RUNNING).limit(1)
                )
                running_result = await session.execute(running_stmt)
                target = running_result.scalars().first()
                if target is None:
                    raise TrainingError("无运行中的训练")
                target.status = TrainingStatus.STOPPING
            await session.commit()
            await session.refresh(target)
        except TrainingError:
            await session.rollback()
            raise
        except Exception as e:
            await session.rollback()
            raise TrainingError(f"停止训练失败: {e}") from e
        finally:
            await session.close()

        # 联动训练线程：置 STOPPING 后立即请求训练循环停止（否则要等下一轮
        # 指标查询才会观察到状态变化）。线程结束后由 _finalize_training 收敛终态。
        active_trainer = self._pop_active_trainer(target.id)
        if active_trainer is not None:
            active_trainer.request_stop()

        logger.info("RL 训练停止请求已发送: run_id=%s", target.id)
        return self._training_run_to_status_info(target)

    @staticmethod
    def _pop_active_trainer(run_id: str) -> PPOTrainer | None:
        with _active_trainers_lock:
            return _active_trainers.pop(run_id, None)

    # ── 内部辅助方法：ORM dataclass ──────────────────────────────

    def _orm_to_dataclass(self, orm: RLAgentPolicyVersionORM) -> PolicyVersion:
        return _orm_to_dataclass(orm)

    def _training_run_to_status_info(self, orm: RLAgentTrainingRunORM) -> TrainingStatusInfo:
        return _training_run_to_status_info(orm)

    # ── 内部辅助方法：dict ndarray ────────────────────────────────

    def _state_dict_to_array(self, state_dict: dict[str, float], *, field_name: str) -> np.ndarray:
        return _state_dict_to_array(state_dict, field_name=field_name)

    def _action_dict_to_array(self, action_dict: dict[str, float], *, field_name: str) -> np.ndarray:
        return _action_dict_to_array(action_dict, field_name=field_name)

    def _action_array_to_dict(self, action_arr: np.ndarray) -> dict[str, float]:
        return _action_array_to_dict(action_arr)

    def _extract_state_field(self, state_arr: np.ndarray, field_name: str, *, default: float = 0.0) -> float:
        return _extract_state_field(state_arr, field_name)

    # ── 内部辅助方法：网络加载与推理 ────────────────────────────────

    @staticmethod
    def _set_inference_mode(net: Any) -> None:
        """torch 网络切到 eval 模式（nn.Module 默认 training=True 会让策略
        走随机采样分支，推理动作非确定）；NumPy 回退实现无 eval，跳过。"""
        if hasattr(net, "eval") and hasattr(net, "encoder"):
            net.eval()

    @staticmethod
    def _to_net_input(net: Any, state_arr: "np.ndarray") -> Any:
        """状态数组 → 网络输入。

        torch 实现（nn.Module，带 encoder）需要 float32 Tensor——此前直接
        传 ndarray 导致 ``linear(): argument 'input' must be Tensor`` 崩溃
        （W 引擎验证发现）；NumPy 回退实现自带 ndarray 适配，原样传入。
        """
        if not hasattr(net, "encoder"):
            return state_arr
        try:
            import torch

            state = torch.as_tensor(np.asarray(state_arr, dtype=np.float32), dtype=torch.float32)
            if state.ndim == 1:
                state = state[None, :]
            return state
        except ImportError:
            return state_arr

    def _get_or_load_policy(self, model_uri: str):
        """获取或加载 PolicyNet（LRU 缓存，limit=4）."""
        # 快速路径
        net = self._policy_cache.get(model_uri)
        if net is not None:
            return net

        with self._cache_lock:
            net = self._policy_cache.get(model_uri)
            if net is not None:
                return net

            # 延迟导入，避免循环依赖
            from app.plugins.rl_agent.policy import PolicyConfig, PolicyNet

            net = PolicyNet(PolicyConfig())
            self._weights_loaded[model_uri] = self._load_weights(net, model_uri, kind="policy")
            self._set_inference_mode(net)

            # LRU 淘汰
            if len(self._policy_cache) >= self._NET_CACHE_LIMIT:
                oldest = next(iter(self._policy_cache))
                self._policy_cache.pop(oldest, None)
            self._policy_cache[model_uri] = net
            return net

    def _get_or_load_value(self, model_uri: str):
        """获取或加载 ValueNet（LRU 缓存，limit=4）."""
        net = self._value_cache.get(model_uri)
        if net is not None:
            return net

        with self._cache_lock:
            net = self._value_cache.get(model_uri)
            if net is not None:
                return net

            from app.plugins.rl_agent.policy import PolicyConfig
            from app.plugins.rl_agent.value import ValueConfig, ValueNet

            policy_config = PolicyConfig()
            value_config = ValueConfig(
                state_dim=policy_config.state_dim,
                hidden_dim=policy_config.hidden_dim,
                seed=policy_config.seed,
            )
            net = ValueNet(value_config)
            self._weights_loaded[model_uri] = self._load_weights(net, model_uri, kind="value")
            self._set_inference_mode(net)

            if len(self._value_cache) >= self._NET_CACHE_LIMIT:
                oldest = next(iter(self._value_cache))
                self._value_cache.pop(oldest, None)
            self._value_cache[model_uri] = net
            return net

    def _get_or_create_shield(self, constraints_spec: SafetyConstraintsSpec):
        """获取或创建 SafetyShield（按约束规格缓存）.

        SafetyShield 实例本身线程安全（内部有锁），可跨请求共享.
        """
        from app.plugins.rl_agent.safety_shield import (
            SafetyConstraints,
            SafetyShield,
        )

        # 将 SafetyConstraintsSpec 映射为 SafetyConstraints
        # v1 使用默认 SafetyConstraints（Spec 仅用于校验阈值，物理边界由
        # SafetyConstraints 默认值提供）
        cache_key = "default"
        shield: Any = self._shield_cache.get(cache_key)
        if shield is not None:
            return shield

        with self._cache_lock:
            shield = self._shield_cache.get(cache_key)
            if shield is not None:
                return shield
            shield = SafetyShield(
                constraints=SafetyConstraints(),
                strict=True,  # v1 默认 strict 模式
            )
            self._shield_cache[cache_key] = shield
            return shield

    def _load_weights(self, net: Any, model_uri: str, *, kind: str) -> bool:
        """加载权重并返回是否成功（False=随机初始化，输出不可信）."""
        return _load_weights(net, model_uri, kind=kind)

    def _extract_action(self, policy_out: Any) -> np.ndarray:
        return _extract_action(policy_out)

    def _extract_value(self, value_out: Any) -> float:
        return _extract_value(value_out)

    def _get_last_action(self) -> np.ndarray | None:
        """获取最后一次合法动作（跨请求维持变化率约束）."""
        with self._last_action_lock:
            return self._last_action.copy() if self._last_action is not None else None

    def _set_last_action(self, action: np.ndarray) -> None:
        """更新最后一次合法动作."""
        with self._last_action_lock:
            self._last_action = np.asarray(action, dtype=np.float32).copy()

    # ── 内部辅助方法：决策结果构建 ──────────────────────────────────

    def _select_recommended_action(
        self,
        *,
        safe_action: np.ndarray,
        safety_result: Any,
        action_evaluations: list[ActionEvaluation],
        optimization_target: str,
    ) -> RecommendedAction:
        """选择推荐动作并生成理由.

        策略：
            - 推荐动作 = 策略输出经安全过滤后的动作
            - 若策略动作违反安全约束，回退到候选动作中最优的安全动作
            - 若所有候选动作均违反，抛 SafetyViolationError

        Args:
            safe_action: 安全过滤后的策略动作.
            safety_result: SafetyShield 过滤结果.
            action_evaluations: 候选动作评估列表.
            optimization_target: 优化目标.

        Returns
        -------
        RecommendedAction
            推荐动作 + 推荐理由.
        """
        action_dict = self._action_array_to_dict(safe_action)

        # 若策略动作未违反约束，直接推荐
        if not safety_result.violated:
            reasoning = self._build_reasoning(
                action_dict=action_dict,
                optimization_target=optimization_target,
                source="policy",
                safety_violated=False,
            )
            return RecommendedAction(action=action_dict, reasoning=reasoning)

        # 策略动作违反约束：尝试从候选动作中找最优安全动作
        safe_candidates = [e for e in action_evaluations if not e.safety_violation]
        if not safe_candidates:
            raise SafetyViolationError("所有候选动作均被 SafetyShield 过滤，无安全动作可选")

        # 按 optimization_target 排序安全候选动作
        best = self._rank_candidates(safe_candidates, optimization_target)[0]
        reasoning = self._build_reasoning(
            action_dict=best.action,
            optimization_target=optimization_target,
            source="candidate_fallback",
            safety_violated=False,
        )
        return RecommendedAction(action=best.action, reasoning=reasoning)

    def _rank_candidates(
        self,
        candidates: list[ActionEvaluation],
        optimization_target: str,
    ) -> list[ActionEvaluation]:
        return _rank_candidates(candidates, optimization_target)

    def _build_reasoning(
        self,
        *,
        action_dict: dict[str, float],
        optimization_target: str,
        source: str,
        safety_violated: bool,
    ) -> str:
        return _build_reasoning(
            action_dict=action_dict,
            optimization_target=optimization_target,
            source=source,
            safety_violated=safety_violated,
        )

    async def _build_policy_info(self, *, model_uri: str, exploration_rate: float) -> PolicyInfo:
        """构建策略元信息.

        从数据库查询 model_uri 对应的版本记录，提取算法 / 版本 / 训练
        episode 数。若未注册，使用默认值.
        """
        version_str = model_uri.rsplit("/", 1)[-1] if "/" in model_uri else "1.0.0"
        algorithm = PolicyAlgorithm.PPO
        training_episodes = 0

        try:
            session = await self._get_session()
            try:
                async with session.begin():
                    stmt = select(RLAgentPolicyVersionORM).where(RLAgentPolicyVersionORM.model_uri == model_uri)
                    result = await session.execute(stmt)
                    orm = result.scalars().first()
                if orm is not None:
                    version_str = orm.version
                    algorithm = orm.algorithm
                    training_episodes = orm.training_episodes
            finally:
                await session.close()
        except Exception as exc:
            logger.debug(
                "查询策略版本失败，使用默认值: uri=%s err=%s",
                model_uri,
                exc,
            )

        return PolicyInfo(
            algorithm=algorithm,
            policy_version=version_str,
            training_episodes=training_episodes,
            exploration_rate=max(0.0, min(1.0, exploration_rate)),
            weights_loaded=self._weights_loaded.get(model_uri, False),
        )


__all__ = [
    "RLAgentService",
    "get_rl_agent_service",
    "reset_rl_agent_service",
]
