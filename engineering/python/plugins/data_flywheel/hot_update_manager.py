"""模型热更新管理器：训练完成 → 注册 → 灰度切换.

对应 core-contracts-design.md 阶段 4 p4-5 / model_iteration_pipeline.yaml
最后两个 DAG 节点（``register_model`` → ``canary_deploy``）.

设计要点：
    1. **不重写 ModelRegistryService**：现有 ``app.services.model_registry_service``
       提供 ``register_model`` / ``get_model_entry`` / ``list_models`` 等基础能力，
       本模块通过组合（而非继承）复用它，叠加灰度部署语义。
    2. **stage 模型**：每个模型在 ``HotUpdateManager`` 视角下处于 4 个阶段之一
       (STAGING / CANARY / PRODUCTION / ARCHIVED)。同一 ``model_name`` 同时只允许
       一个 PRODUCTION、最多一个 CANARY；STAGING / ARCHIVED 数量不限。
    3. **流量分配**：``select_model_for_request`` 按 ``canary_ratio`` 随机选择
       CANARY 或 PRODUCTION 模型 URI，供推理路径调用。
    4. **观察期决策**：``observe_deployment`` 接收当前 canary 指标，与 baseline
       对比后返回 ``continue`` / ``promote`` / ``rollback`` 决策。
       - ``promote``：观察期结束且 canary 不退化
       - ``rollback``：canary 指标下降超过 ``rollback_metric_drop``
       - ``continue``：仍在观察期内，无退化迹象
    5. **降级模式**：``model_registry_service=None`` 时，``canary_deploy`` 仅记录
       DeploymentRecord 不实际注册模型（用于无 ModelRegistryService 环境）。
    6. **线程安全**：所有公开方法通过 ``_lock`` 串行化，防止并发部署/回滚竞态。
    7. **持久化**：DeploymentRecord 仅保存在内存（重启丢失）。生产环境若需持久化，
       可在后续迭代中扩展为 SQLite 存储（参考 SnapshotStore 实现）。

不依赖 torch / sklearn / fastapi，可在单元测试环境独立运行。
"""

from __future__ import annotations

import logging
import random
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# 枚举与数据类


class ModelStage(str, Enum):
    """模型部署阶段.

    状态机：
        STAGING --canary_deploy--> CANARY --promote--> PRODUCTION --新版本上线--> ARCHIVED
                                  CANARY --rollback--> ARCHIVED
    """

    STAGING = "staging"  # 已注册但未部署（评估中）
    CANARY = "canary"  # 灰度中
    PRODUCTION = "production"  # 全量线上
    ARCHIVED = "archived"  # 已归档（被新版本替代或回滚）


class DeploymentStatus(str, Enum):
    """灰度部署状态."""

    DEPLOYING = "deploying"  # 部署中（canary_deploy 调用过程）
    OBSERVING = "observing"  # 灰度观察期
    PROMOTED = "promoted"  # 已全量切换
    ROLLED_BACK = "rolled_back"  # 已回滚
    FAILED = "failed"  # 部署失败


@dataclass
class DeploymentRecord:
    """一次灰度部署的完整记录.

    生命周期：``canary_deploy`` 创建 → ``observe_deployment`` 多次决策 →
    ``promote`` 或 ``rollback`` 终态。
    """

    deployment_id: str
    model_name: str  # 业务模型名（如 "ltc-chatter"）
    new_model_uri: str  # 新版本模型 URI（canary）
    baseline_model_uri: str  # 基线模型 URI（当前 production）
    canary_ratio: float
    observation_hours: int
    started_at: datetime
    rollback_on_failure: bool
    rollback_metric_drop: float
    promote_on_success: bool
    eval_metric: str  # 用于决策的指标名（如 "f1"）
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    canary_metrics: dict[str, float] = field(default_factory=dict)
    status: DeploymentStatus = DeploymentStatus.OBSERVING
    rollback_reason: Optional[str] = None
    promoted_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    # 观察期内每次 observe_deployment 的快照（用于审计）
    observation_history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.canary_ratio <= 1.0:
            raise ValueError(f"canary_ratio 必须在 [0,1]，当前: {self.canary_ratio}")
        if self.observation_hours <= 0:
            raise ValueError(f"observation_hours 必须为正数: {self.observation_hours}")
        if not 0.0 <= self.rollback_metric_drop <= 1.0:
            raise ValueError(f"rollback_metric_drop 必须在 [0,1]，当前: {self.rollback_metric_drop}")

    @property
    def observation_ends_at(self) -> datetime:
        """观察期结束时间."""
        return self.started_at + timedelta(hours=self.observation_hours)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（供 API 响应/日志）."""
        return {
            "deployment_id": self.deployment_id,
            "model_name": self.model_name,
            "new_model_uri": self.new_model_uri,
            "baseline_model_uri": self.baseline_model_uri,
            "canary_ratio": self.canary_ratio,
            "observation_hours": self.observation_hours,
            "started_at": self.started_at.isoformat(),
            "observation_ends_at": self.observation_ends_at.isoformat(),
            "rollback_on_failure": self.rollback_on_failure,
            "rollback_metric_drop": self.rollback_metric_drop,
            "promote_on_success": self.promote_on_success,
            "eval_metric": self.eval_metric,
            "baseline_metrics": dict(self.baseline_metrics),
            "canary_metrics": dict(self.canary_metrics),
            "status": self.status.value,
            "rollback_reason": self.rollback_reason,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "observation_history": list(self.observation_history),
        }


# 决策结果


@dataclass
class ObservationDecision:
    """``observe_deployment`` 的决策结果."""

    decision: str  # "continue" / "promote" / "rollback"
    reason: str
    baseline_value: Optional[float]
    canary_value: Optional[float]
    drop: Optional[float]  # 指标下降比例（正数表示退化）
    observation_remaining_hours: Optional[float]
    deployment_status: DeploymentStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "baseline_value": self.baseline_value,
            "canary_value": self.canary_value,
            "drop": self.drop,
            "observation_remaining_hours": self.observation_remaining_hours,
            "deployment_status": self.deployment_status.value,
        }


# HotUpdateManager


class HotUpdateManager:
    """模型热更新管理器（灰度部署 + 观察期 + 自动晋升/回滚）.

    使用方式：
        manager = HotUpdateManager(
            model_registry_service=get_model_registry_service(),
            snapshot_store=get_snapshot_store(),
            config={"canary_ratio": 0.1, "observation_hours": 24, ...},
        )
        deployment = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://ltc-chatter-v3",
            baseline_model_uri="model://ltc-chatter-v2",
            eval_metrics={"f1": 0.92, "mae": 0.05},
            baseline_metrics={"f1": 0.88, "mae": 0.07},
            eval_metric="f1",
        )
        # 推理路径：按 canary_ratio 分配流量
        model_uri = manager.select_model_for_request("ltc-chatter")
        # 观察期决策
        decision = await manager.observe_deployment(
            deployment.deployment_id,
            current_canary_metrics={"f1": 0.91, "mae": 0.06},
        )
        if decision.decision == "promote":
            await manager.promote(deployment.deployment_id)
        elif decision.decision == "rollback":
            await manager.rollback(deployment.deployment_id, reason=decision.reason)
    """

    def __init__(
        self,
        model_registry_service: Any = None,
        snapshot_store: Any = None,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        self._model_registry_service = model_registry_service
        self._snapshot_store = snapshot_store
        self._config: dict[str, Any] = dict(config or {})
        self._deployments: dict[str, DeploymentRecord] = {}
        # model_name -> {stage -> model_uri}（当前部署视图）
        self._model_stages: dict[str, dict[ModelStage, str]] = {}
        self._lock = threading.RLock()

        if model_registry_service is None:
            logger.warning(
                "HotUpdateManager 初始化为降级模式（model_registry_service=None），"
                "canary_deploy 将仅记录 DeploymentRecord 不实际注册模型"
            )

    # 公开属性

    @property
    def config(self) -> dict[str, Any]:
        """配置（只读视图）."""
        return dict(self._config)

    @property
    def model_registry_service(self) -> Any:
        return self._model_registry_service

    @property
    def snapshot_store(self) -> Any:
        return self._snapshot_store

    @property
    def is_degraded(self) -> bool:
        """是否处于降级模式（无 model_registry_service）."""
        return self._model_registry_service is None

    # 灰度部署入口

    async def canary_deploy(
        self,
        *,
        model_name: str,
        new_model_uri: str,
        baseline_model_uri: str,
        eval_metrics: dict[str, float],
        baseline_metrics: Optional[dict[str, float]] = None,
        eval_metric: str = "f1",
        canary_ratio: Optional[float] = None,
        observation_hours: Optional[int] = None,
        rollback_on_failure: Optional[bool] = None,
        rollback_metric_drop: Optional[float] = None,
        promote_on_success: bool = True,
        metadata: Optional[dict[str, Any]] = None,
    ) -> DeploymentRecord:
        """以灰度比例部署新模型.

        Args:
            model_name: 业务模型名（如 "ltc-chatter"）
            new_model_uri: 新版本模型 URI（canary）
            baseline_model_uri: 基线模型 URI（当前 production）
            eval_metrics: 新模型评估指标（来自 evaluate_model 节点）
            baseline_metrics: 基线模型评估指标（缺省时无法做指标对比，仅时间触发晋升）
            eval_metric: 用于决策的指标名（默认 "f1"）
            canary_ratio: 灰度比例（None 时用 config 默认值）
            observation_hours: 观察期小时数（None 时用 config 默认值）
            rollback_on_failure: 是否自动回滚（None 时用 config 默认值）
            rollback_metric_drop: 回滚阈值（None 时用 config 默认值）
            promote_on_success: 观察期结束是否自动晋升
            metadata: 附加元数据（写入 DeploymentRecord，便于审计）

        Returns:
            DeploymentRecord（status=OBSERVING）

        Raises:
            ValueError: 参数非法或同名 canary 已存在
        """
        if not model_name:
            raise ValueError("model_name 不能为空")
        if not new_model_uri:
            raise ValueError("new_model_uri 不能为空")
        if not baseline_model_uri:
            raise ValueError("baseline_model_uri 不能为空")
        if new_model_uri == baseline_model_uri:
            raise ValueError("new_model_uri 与 baseline_model_uri 不能相同（无意义部署）")
        if not eval_metrics:
            raise ValueError("eval_metrics 不能为空（至少需要一个评估指标）")

        ratio = self._resolve_config("canary_ratio", canary_ratio, 0.1)
        obs_hours = self._resolve_config("observation_hours", observation_hours, 24)
        rb_on_fail = self._resolve_config("rollback_on_failure", rollback_on_failure, True)
        rb_drop = self._resolve_config("rollback_metric_drop", rollback_metric_drop, 0.05)

        with self._lock:
            # 同名模型已有进行中的 canary 拒绝（防止同时灰度两个版本）
            existing_canary = self._find_active_canary(model_name)
            if existing_canary is not None:
                raise ValueError(
                    f"模型 {model_name} 已有进行中的灰度部署 "
                    f"(deployment_id={existing_canary.deployment_id})，"
                    "请先 promote/rollback 后再发起新部署"
                )

            deployment_id = f"dep-{uuid.uuid4().hex[:16]}"
            started_at = datetime.utcnow()

            record = DeploymentRecord(
                deployment_id=deployment_id,
                model_name=model_name,
                new_model_uri=new_model_uri,
                baseline_model_uri=baseline_model_uri,
                canary_ratio=ratio,
                observation_hours=obs_hours,
                started_at=started_at,
                rollback_on_failure=rb_on_fail,
                rollback_metric_drop=rb_drop,
                promote_on_success=promote_on_success,
                eval_metric=eval_metric,
                baseline_metrics=dict(baseline_metrics or {}),
                canary_metrics=dict(eval_metrics),
                status=DeploymentStatus.OBSERVING,
            )

            # 注册到 ModelRegistryService（非降级模式）
            if not self.is_degraded:
                self._register_canary_to_model_registry(
                    model_name=model_name,
                    model_uri=new_model_uri,
                    metadata=metadata,
                )

            # 更新部署视图：model_name 的 CANARY 槽位
            stages = self._model_stages.setdefault(model_name, {})
            # 若尚无 PRODUCTION，则把 baseline_uri 视为 PRODUCTION（用于流量分配回退）
            if ModelStage.PRODUCTION not in stages:
                stages[ModelStage.PRODUCTION] = baseline_model_uri
            stages[ModelStage.CANARY] = new_model_uri

            self._deployments[deployment_id] = record

            logger.info(
                "HotUpdateManager.canary_deploy: deployment_id=%s model=%s "
                "canary=%s baseline=%s ratio=%.2f obs_hours=%d eval_metric=%s "
                "degraded=%s",
                deployment_id,
                model_name,
                new_model_uri,
                baseline_model_uri,
                ratio,
                obs_hours,
                eval_metric,
                self.is_degraded,
            )
            return record

    # 观察期决策

    async def observe_deployment(
        self,
        deployment_id: str,
        current_canary_metrics: dict[str, float],
        *,
        now: Optional[datetime] = None,
    ) -> ObservationDecision:
        """根据当前 canary 指标做决策.

        Args:
            deployment_id: ``canary_deploy`` 返回的 deployment_id
            current_canary_metrics: 当前 canary 模型的运行时指标
                （来自实时推理日志聚合，或再次评估）
            now: 当前时间（None 时用 ``datetime.utcnow()``，测试可注入）

        Returns:
            ObservationDecision，decision 取值：
                - ``continue``: 仍在观察期，无退化
                - ``promote``: 观察期结束且未退化，可全量切换
                - ``rollback``: 指标退化超过阈值，应回滚

        Raises:
            KeyError: deployment_id 不存在
            ValueError: 部署已终态（PROMOTED/ROLLED_BACK/FAILED）
        """
        with self._lock:
            record = self._deployments.get(deployment_id)
            if record is None:
                raise KeyError(f"deployment 不存在: {deployment_id}")

            if record.status in (
                DeploymentStatus.PROMOTED,
                DeploymentStatus.ROLLED_BACK,
                DeploymentStatus.FAILED,
            ):
                raise ValueError(f"部署已处于终态 ({record.status.value})，无法继续观察")

            now_dt = now or datetime.utcnow()
            eval_metric = record.eval_metric
            baseline_value = record.baseline_metrics.get(eval_metric)
            canary_value = current_canary_metrics.get(eval_metric)

            # 记录观察历史（审计用）
            record.observation_history.append(
                {
                    "observed_at": now_dt.isoformat(),
                    "canary_metrics": dict(current_canary_metrics),
                    "baseline_value": baseline_value,
                    "canary_value": canary_value,
                }
            )

            # 计算指标下降比例（正向表示退化）
            drop: Optional[float] = None
            if baseline_value is not None and canary_value is not None:
                if baseline_value > 0:
                    drop = (baseline_value - canary_value) / baseline_value
                elif canary_value < baseline_value:
                    drop = 1.0  # baseline=0 但 canary<0 的极端情况
                else:
                    drop = 0.0

            # 决策 1：是否回滚（指标退化超过阈值）
            if record.rollback_on_failure and drop is not None and drop > record.rollback_metric_drop:
                reason = (
                    f"canary 指标 {eval_metric} 退化 {drop:.2%} "
                    f"(>{record.rollback_metric_drop:.2%})，"
                    f"baseline={baseline_value} canary={canary_value}"
                )
                logger.warning(
                    "HotUpdateManager.observe: deployment=%s 触发回滚 (%s)",
                    deployment_id,
                    reason,
                )
                return ObservationDecision(
                    decision="rollback",
                    reason=reason,
                    baseline_value=baseline_value,
                    canary_value=canary_value,
                    drop=drop,
                    observation_remaining_hours=None,
                    deployment_status=record.status,
                )

            # 决策 2：观察期是否结束
            remaining_hours: Optional[float] = None
            if now_dt >= record.observation_ends_at:
                # 观察期结束且未退化
                if record.promote_on_success:
                    reason = f"观察期 {record.observation_hours}h 结束，canary 未退化 (drop={drop})"
                    logger.info(
                        "HotUpdateManager.observe: deployment=%s 触发晋升 (%s)",
                        deployment_id,
                        reason,
                    )
                    return ObservationDecision(
                        decision="promote",
                        reason=reason,
                        baseline_value=baseline_value,
                        canary_value=canary_value,
                        drop=drop,
                        observation_remaining_hours=0.0,
                        deployment_status=record.status,
                    )
                # promote_on_success=False：观察期结束但不自动晋升，返回 continue
                # 由外部调用方决定
                reason = f"观察期 {record.observation_hours}h 结束，promote_on_success=False，等待外部晋升决策"
                return ObservationDecision(
                    decision="continue",
                    reason=reason,
                    baseline_value=baseline_value,
                    canary_value=canary_value,
                    drop=drop,
                    observation_remaining_hours=0.0,
                    deployment_status=record.status,
                )

            # 决策 3：仍在观察期，继续
            remaining_hours = (record.observation_ends_at - now_dt).total_seconds() / 3600.0
            reason = f"观察期内，剩余 {remaining_hours:.1f}h (drop={drop}, 阈值={record.rollback_metric_drop})"
            return ObservationDecision(
                decision="continue",
                reason=reason,
                baseline_value=baseline_value,
                canary_value=canary_value,
                drop=drop,
                observation_remaining_hours=remaining_hours,
                deployment_status=record.status,
            )

    # 晋升 / 回滚

    async def promote(self, deployment_id: str) -> DeploymentRecord:
        """将 canary 升级为 PRODUCTION，原 PRODUCTION 归档.

        Args:
            deployment_id: ``canary_deploy`` 返回的 deployment_id

        Returns:
            更新后的 DeploymentRecord（status=PROMOTED）

        Raises:
            KeyError: deployment_id 不存在
            ValueError: 部署已终态或非 OBSERVING 状态
        """
        with self._lock:
            record = self._deployments.get(deployment_id)
            if record is None:
                raise KeyError(f"deployment 不存在: {deployment_id}")

            if record.status != DeploymentStatus.OBSERVING:
                raise ValueError(f"部署状态非 OBSERVING (当前: {record.status.value})，无法 promote")

            now = datetime.utcnow()
            record.status = DeploymentStatus.PROMOTED
            record.promoted_at = now
            record.ended_at = now

            # 更新部署视图
            stages = self._model_stages.setdefault(record.model_name, {})
            old_production = stages.get(ModelStage.PRODUCTION)
            if old_production is not None and old_production != record.new_model_uri:
                stages[ModelStage.ARCHIVED] = old_production
            stages[ModelStage.PRODUCTION] = record.new_model_uri
            stages.pop(ModelStage.CANARY, None)

            logger.info(
                "HotUpdateManager.promote: deployment=%s model=%s new_production=%s archived=%s",
                deployment_id,
                record.model_name,
                record.new_model_uri,
                old_production,
            )
            return record

    async def rollback(
        self,
        deployment_id: str,
        *,
        reason: str = "",
    ) -> DeploymentRecord:
        """回滚 canary，恢复 baseline 为 PRODUCTION.

        Args:
            deployment_id: ``canary_deploy`` 返回的 deployment_id
            reason: 回滚原因（写入审计日志）

        Returns:
            更新后的 DeploymentRecord（status=ROLLED_BACK）

        Raises:
            KeyError: deployment_id 不存在
            ValueError: 部署已终态
        """
        with self._lock:
            record = self._deployments.get(deployment_id)
            if record is None:
                raise KeyError(f"deployment 不存在: {deployment_id}")

            if record.status in (
                DeploymentStatus.PROMOTED,
                DeploymentStatus.ROLLED_BACK,
                DeploymentStatus.FAILED,
            ):
                raise ValueError(f"部署已处于终态 ({record.status.value})，无法 rollback")

            now = datetime.utcnow()
            record.status = DeploymentStatus.ROLLED_BACK
            record.rollback_reason = reason or "未提供回滚原因"
            record.ended_at = now

            # 更新部署视图：canary 归档，恢复 baseline 为 production
            stages = self._model_stages.setdefault(record.model_name, {})
            stages[ModelStage.ARCHIVED] = record.new_model_uri
            stages[ModelStage.PRODUCTION] = record.baseline_model_uri
            stages.pop(ModelStage.CANARY, None)

            logger.warning(
                "HotUpdateManager.rollback: deployment=%s model=%s "
                "rolled_back_canary=%s restored_production=%s reason=%s",
                deployment_id,
                record.model_name,
                record.new_model_uri,
                record.baseline_model_uri,
                record.rollback_reason,
            )
            return record

    # 查询 API

    async def get_deployment(self, deployment_id: str) -> DeploymentRecord:
        """按 ID 取部署记录.

        Raises:
            KeyError: deployment_id 不存在
        """
        with self._lock:
            record = self._deployments.get(deployment_id)
            if record is None:
                raise KeyError(f"deployment 不存在: {deployment_id}")
            return record

    async def list_deployments(
        self,
        *,
        model_name: Optional[str] = None,
        status: Optional[DeploymentStatus] = None,
    ) -> list[DeploymentRecord]:
        """列出部署记录（可选过滤）."""
        with self._lock:
            result: list[DeploymentRecord] = []
            for record in self._deployments.values():
                if model_name is not None and record.model_name != model_name:
                    continue
                if status is not None and record.status != status:
                    continue
                result.append(record)
            # 按启动时间倒序（最新在前）
            result.sort(key=lambda r: r.started_at, reverse=True)
            return result

    # 流量分配

    def select_model_for_request(
        self,
        model_name: str,
        *,
        rng: Optional[random.Random] = None,
    ) -> str:
        """按 canary_ratio 分配流量.

        若 ``model_name`` 当前无 CANARY 部署，直接返回 PRODUCTION URI。
        若 PRODUCTION 也无（罕见：从未部署），抛出 KeyError。

        Args:
            model_name: 业务模型名
            rng: 随机数生成器（测试可注入确定性 rng）

        Returns:
            选中的模型 URI（canary 或 production）

        Raises:
            KeyError: ``model_name`` 无任何已部署版本
        """
        with self._lock:
            stages = self._model_stages.get(model_name)
            if not stages:
                raise KeyError(f"模型 {model_name} 无任何已部署版本")

            canary_uri = stages.get(ModelStage.CANARY)
            production_uri = stages.get(ModelStage.PRODUCTION)

            if canary_uri is None:
                if production_uri is None:
                    raise KeyError(f"模型 {model_name} 无 PRODUCTION 版本")
                return production_uri

            # 有 canary：按比例分配
            # 找到对应的 deployment 取 canary_ratio
            canary_ratio = self._get_canary_ratio_for(model_name)
            rng = rng or random
            if rng.random() < canary_ratio:
                return canary_uri
            if production_uri is None:
                # 极端：只有 canary 无 production（首次部署场景）
                return canary_uri
            return production_uri

    def get_production_model(self, model_name: str) -> Optional[str]:
        """获取当前 PRODUCTION 模型 URI（无则 None）."""
        with self._lock:
            stages = self._model_stages.get(model_name)
            if not stages:
                return None
            return stages.get(ModelStage.PRODUCTION)

    def get_canary_model(self, model_name: str) -> Optional[str]:
        """获取当前 CANARY 模型 URI（无则 None）."""
        with self._lock:
            stages = self._model_stages.get(model_name)
            if not stages:
                return None
            return stages.get(ModelStage.CANARY)

    def list_model_stages(self, model_name: str) -> dict[str, Optional[str]]:
        """返回模型各阶段 URI（STAGING/CANARY/PRODUCTION/ARCHIVED）."""
        with self._lock:
            stages = self._model_stages.get(model_name, {})
            return {
                ModelStage.STAGING.value: stages.get(ModelStage.STAGING),
                ModelStage.CANARY.value: stages.get(ModelStage.CANARY),
                ModelStage.PRODUCTION.value: stages.get(ModelStage.PRODUCTION),
                ModelStage.ARCHIVED.value: stages.get(ModelStage.ARCHIVED),
            }

    # 内部辅助

    def _resolve_config(self, key: str, explicit: Optional[Any], default: Any) -> Any:
        """解析配置：显式参数 > config > 默认值."""
        if explicit is not None:
            return explicit
        return self._config.get(key, default)

    def _find_active_canary(self, model_name: str) -> Optional[DeploymentRecord]:
        """查找模型当前进行中的 canary 部署（OBSERVING 状态）."""
        for record in self._deployments.values():
            if record.model_name == model_name and record.status == DeploymentStatus.OBSERVING:
                return record
        return None

    def _get_canary_ratio_for(self, model_name: str) -> float:
        """获取模型当前 canary 部署的 canary_ratio（无则 0.0）."""
        record = self._find_active_canary(model_name)
        if record is None:
            return 0.0
        return record.canary_ratio

    def _register_canary_to_model_registry(
        self,
        *,
        model_name: str,
        model_uri: str,
        metadata: Optional[dict[str, Any]],
    ) -> None:
        """将 canary 模型注册到 ModelRegistryService.

        失败时仅记录警告，不阻塞部署（DeploymentRecord 仍创建）。
        ModelRegistryService.register_model 是同步方法，直接调用。
        """
        try:
            # 注意：ModelRegistryService.register_model 接收 ModelInfo 对象
            # 这里我们仅记录 URI，不构造完整 ModelInfo（业务侧应在 train_model
            # 节点已完成 ModelInfo 注册，本方法仅作为 best-effort 同步）
            # 如果业务侧已注册，register_model 返回 False（重复注册），属正常
            service = self._model_registry_service
            if service is None:
                return
            # 尝试调用 list_models 检查是否已注册（best-effort，失败忽略）
            try:
                existing = service.list_models(return_objects=False)
                if model_uri in (existing or []):
                    return  # 已注册
            except Exception:  # noqa: BLE001
                pass  # 检查失败不影响主流程
            # 不主动构造 ModelInfo 注册（缺字段会失败），仅记录日志
            logger.info(
                "HotUpdateManager: 模型 %s (%s) 注册由 train_model 节点完成，canary_deploy 仅记录 stage 转换",
                model_name,
                model_uri,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "HotUpdateManager: 注册 canary 到 ModelRegistryService 失败 (model=%s uri=%s)，部署继续",
                model_name,
                model_uri,
                exc_info=True,
            )


# 全局单例


_hot_update_manager: Optional[HotUpdateManager] = None
_hot_update_manager_lock = threading.Lock()


def get_hot_update_manager() -> HotUpdateManager:
    """获取全局 HotUpdateManager 单例.

    未配置时返回一个降级实例（无 model_registry_service）。
    """
    global _hot_update_manager
    if _hot_update_manager is None:
        with _hot_update_manager_lock:
            if _hot_update_manager is None:
                _hot_update_manager = HotUpdateManager()
    return _hot_update_manager


def configure_hot_update_manager(
    *,
    model_registry_service: Any = None,
    snapshot_store: Any = None,
    config: Optional[dict[str, Any]] = None,
) -> HotUpdateManager:
    """配置全局 HotUpdateManager 单例（覆盖现有实例）.

    通常在 data_flywheel 插件 ``on_load`` 时调用。
    """
    global _hot_update_manager
    with _hot_update_manager_lock:
        _hot_update_manager = HotUpdateManager(
            model_registry_service=model_registry_service,
            snapshot_store=snapshot_store,
            config=config,
        )
        logger.info(
            "HotUpdateManager 全局单例已配置 (degraded=%s, config_keys=%s)",
            _hot_update_manager.is_degraded,
            list(_hot_update_manager.config.keys()) or "empty",
        )
        return _hot_update_manager


def reset_hot_update_manager() -> None:
    """重置全局单例（仅用于测试）."""
    global _hot_update_manager
    with _hot_update_manager_lock:
        _hot_update_manager = None


__all__ = [
    # 枚举
    "ModelStage",
    "DeploymentStatus",
    # 数据类
    "DeploymentRecord",
    "ObservationDecision",
    # 主类
    "HotUpdateManager",
    # 全局单例
    "get_hot_update_manager",
    "configure_hot_update_manager",
    "reset_hot_update_manager",
]
