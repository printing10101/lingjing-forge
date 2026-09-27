"""RL Agent ORM 模型：策略版本记录 + 训练运行记录持久化.

对应 ADR-017（世界模型与 RL 模块）第 2 / 4 节。

新增 2 张表（与 training_task.Base 共享 metadata，与 explainability.py 同源）：
    - ``rl_agent_policy_versions``：RL 策略版本记录（version / model_uri /
      algorithm / training_episodes / mean_reward / is_active）
    - ``rl_agent_training_runs``：训练运行记录（status / current_step /
      current_episode / metrics_json / error_message）

设计要点
--------
    - 策略版本 ``model_uri`` 唯一索引，``is_active`` 同一时刻仅允许一个为 True
    - 训练运行记录持久化训练状态机（IDLE/RUNNING/PAUSED/COMPLETED/FAILED/STOPPING），
      供前端轮询训练进度
    - 训练指标（policy_loss/value_loss/entropy/mean_reward 等）以 JSON 存储，
      避免频繁 schema 变更
    - 与 explainability.py 风格对齐：uuid 前缀生成器 + to_dict() + __repr__
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.utils.time import utcnow

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.models.training_task import Base


def _gen_policy_version_id() -> str:
    """生成策略版本记录 ID（rlpv_ 前缀 + uuid hex）."""
    return f"rlpv_{uuid.uuid4().hex}"


def _gen_training_run_id() -> str:
    """生成训练运行记录 ID（rltr_ 前缀 + uuid hex）."""
    return f"rltr_{uuid.uuid4().hex}"


class RLAgentPolicyVersionORM(Base):
    """RL 策略版本记录 ORM.

    持久化已注册的 RL 策略版本元信息，支持前端列出版本 / 查询版本详情 /
    切换激活版本。策略权重文件不入库（按 model_uri 加载）。

    注意
    ----
    - ``version`` 为 semver 字符串，``model_uri`` 全局唯一
    - ``is_active`` 同一时刻仅允许一个为 True（由服务层保证）
    - ``algorithm`` 为策略算法常量（ppo/dqn/sac，v1 仅 ppo）
    - 与 ``app.contracts.rl_agent.PolicyVersion`` dataclass 对齐
    """

    __tablename__ = "rl_agent_policy_versions"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_policy_version_id,
        comment="策略版本记录 ID（rlpv_ 前缀 + uuid）",
    )
    version = Column(
        String(32),
        nullable=False,
        index=True,
        comment="版本号（semver，如 1.0.0）",
    )
    model_uri = Column(
        String(256),
        nullable=False,
        unique=True,
        index=True,
        comment="策略模型 URI（model://rl_agent/<version>）",
    )
    algorithm = Column(
        String(16),
        nullable=False,
        default="ppo",
        index=True,
        comment="策略算法（ppo/dqn/sac，v1 仅 ppo）",
    )
    description = Column(
        Text,
        nullable=True,
        comment="版本描述",
    )
    training_episodes = Column(
        Integer,
        nullable=False,
        default=0,
        comment="训练 episode 数",
    )
    training_steps = Column(
        Integer,
        nullable=False,
        default=0,
        comment="训练步数",
    )
    mean_reward = Column(
        Float,
        nullable=False,
        default=0.0,
        comment="训练时平均 episode 奖励",
    )
    is_active = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="是否为当前激活版本",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
        comment="创建时间",
    )

    # 关联：一个策略版本可对应多次训练运行
    training_runs = relationship(
        "RLAgentTrainingRunORM",
        foreign_keys="RLAgentTrainingRunORM.policy_version_id",
        lazy="selectin",
        back_populates="policy_version",
    )

    __table_args__ = (
        Index(
            "ix_rl_agent_policy_versions_version_active",
            "version",
            "is_active",
        ),
        Index(
            "ix_rl_agent_policy_versions_algo_active",
            "algorithm",
            "is_active",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "id": self.id,
            "version": self.version,
            "model_uri": self.model_uri,
            "algorithm": self.algorithm,
            "description": self.description or "",
            "training_episodes": self.training_episodes,
            "training_steps": self.training_steps,
            "mean_reward": self.mean_reward,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<RLAgentPolicyVersionORM(id={self.id}, version={self.version}, "
            f"algorithm={self.algorithm}, is_active={self.is_active})>"
        )


class RLAgentTrainingRunORM(Base):
    """RL 训练运行记录 ORM.

    持久化每次训练运行的状态与指标，供前端轮询训练进度。

    状态机
    ------
    - IDLE → RUNNING（启动训练）
    - RUNNING → PAUSED / STOPPING / COMPLETED / FAILED
    - PAUSED → RUNNING（恢复） / STOPPING
    - STOPPING → COMPLETED / FAILED（保存 checkpoint 后终止）
    - COMPLETED / FAILED 为终态

    注意
    ----
    - ``status`` 为训练状态常量（与 ``TrainingStatus`` 对齐）
    - ``metrics_json`` 存储训练指标快照（policy_loss/value_loss/entropy 等）
    - ``policy_version_id`` 外键关联 ``rl_agent_policy_versions.id``，
      ondelete=SET NULL（删除策略版本时保留训练历史）
    """

    __tablename__ = "rl_agent_training_runs"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_training_run_id,
        comment="训练运行 ID（rltr_ 前缀 + uuid）",
    )
    policy_version_id = Column(
        String(64),
        ForeignKey("rl_agent_policy_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联策略版本 ID（可空，训练新建策略时暂未注册版本）",
    )
    status = Column(
        String(16),
        nullable=False,
        default="idle",
        index=True,
        comment="训练状态（idle/running/paused/completed/failed/stopping）",
    )
    current_step = Column(
        Integer,
        nullable=False,
        default=0,
        comment="当前训练步数",
    )
    current_episode = Column(
        Integer,
        nullable=False,
        default=0,
        comment="当前 episode 数",
    )
    total_steps_target = Column(
        Integer,
        nullable=True,
        comment="目标训练步数（可空，表示无上限）",
    )
    total_episodes_target = Column(
        Integer,
        nullable=True,
        comment="目标 episode 数（可空，表示无上限）",
    )
    metrics_json = Column(
        Text,
        nullable=True,
        comment="训练指标快照 JSON（policy_loss/value_loss/entropy/mean_reward 等）",
    )
    error_message = Column(
        Text,
        nullable=True,
        comment="训练失败时的错误信息（status=failed 时填写）",
    )
    started_at = Column(
        DateTime,
        nullable=True,
        index=True,
        comment="训练开始时间",
    )
    finished_at = Column(
        DateTime,
        nullable=True,
        comment="训练结束时间（completed/failed）",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
        comment="记录创建时间",
    )

    # 关联：反向关联策略版本
    policy_version = relationship(
        "RLAgentPolicyVersionORM",
        foreign_keys=[policy_version_id],
        back_populates="training_runs",
    )

    __table_args__ = (
        Index(
            "ix_rl_agent_training_runs_status_created",
            "status",
            "created_at",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        metrics: dict[str, Any] = {}
        if self.metrics_json:
            try:
                metrics = json.loads(str(self.metrics_json))
            except (ValueError, TypeError):
                metrics = {}
        return {
            "id": self.id,
            "policy_version_id": self.policy_version_id,
            "status": self.status,
            "current_step": self.current_step,
            "current_episode": self.current_episode,
            "total_steps_target": self.total_steps_target,
            "total_episodes_target": self.total_episodes_target,
            "metrics": metrics,
            "error_message": self.error_message or "",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<RLAgentTrainingRunORM(id={self.id}, status={self.status}, "
            f"step={self.current_step}, episode={self.current_episode})>"
        )


__all__ = [
    "RLAgentPolicyVersionORM",
    "RLAgentTrainingRunORM",
    "_gen_policy_version_id",
    "_gen_training_run_id",
]
