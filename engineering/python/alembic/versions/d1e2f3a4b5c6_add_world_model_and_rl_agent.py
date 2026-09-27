"""add_world_model_and_rl_agent

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-07-14 23:00:00.000000

ADR-017 阶段 8 p8-5d-alembic：新增世界模型 + RL Agent 持久化表
（3 张表）。

设计要点：
    - world_model_versions: 世界模型版本记录表
      (version / model_uri / training_data_size / prediction_horizon / is_active)
    - rl_agent_policy_versions: RL 策略版本记录表
      (version / model_uri / algorithm / training_episodes / training_steps /
       mean_reward / is_active)
    - rl_agent_training_runs: RL 训练运行记录表
      (policy_version_id / status / current_step / current_episode /
       metrics_json / error_message / started_at / finished_at)
    - 版本号 version 为 semver 字符串，model_uri 全局唯一
    - is_active 同一时刻仅允许一个为 True（由服务层保证）
    - 训练运行状态机：IDLE/RUNNING/PAUSED/COMPLETED/FAILED/STOPPING
      （status 为字符串常量，不使用枚举以保持 SQLite 兼容性）
    - 训练指标 metrics_json 以 JSON 字符串存储，避免频繁 schema 变更
    - policy_version_id 外键关联 rl_agent_policy_versions.id，ondelete=SET NULL
      （删除策略版本时保留训练历史）
    - 与 c0d1e2f3a4b5 风格对齐：单列索引 + 复合索引组合
    - trajectory / 决策结果不入库（按需生成）

状态机（与 RLAgentTrainingRunORM / TrainingStatus 对齐）：
    IDLE → RUNNING（启动训练）
    RUNNING → PAUSED / STOPPING / COMPLETED / FAILED
    PAUSED → RUNNING（恢复） / STOPPING
    STOPPING → COMPLETED / FAILED（保存 checkpoint 后终止）
    COMPLETED / FAILED 为终态
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create world_model_versions / rl_agent_policy_versions / rl_agent_training_runs."""
    # -----------------------------------------------------------------------
    # world_model_versions 表：世界模型版本记录
    # -----------------------------------------------------------------------
    op.create_table(
        "world_model_versions",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="版本记录 ID（wmv_ 前缀 + uuid）",
        ),
        sa.Column(
            "version",
            sa.String(32),
            nullable=False,
            comment="版本号（semver，如 1.0.0）",
        ),
        sa.Column(
            "model_uri",
            sa.String(256),
            nullable=False,
            comment="模型 URI（model://world_model/<version>）",
        ),
        sa.Column(
            "description",
            sa.Text,
            nullable=True,
            comment="版本描述",
        ),
        sa.Column(
            "training_data_size",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="训练数据样本数",
        ),
        sa.Column(
            "prediction_horizon",
            sa.Integer,
            nullable=False,
            server_default="10",
            comment="训练时的预测步长",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("0"),
            comment="是否为当前激活版本",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        comment="ADR-017 阶段 8 p8：世界模型版本记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_world_model_versions_version",
        "world_model_versions",
        ["version"],
    )
    # model_uri 唯一索引（与 ORM unique=True 对齐）
    op.create_index(
        "ix_world_model_versions_model_uri",
        "world_model_versions",
        ["model_uri"],
        unique=True,
    )
    op.create_index(
        "ix_world_model_versions_is_active",
        "world_model_versions",
        ["is_active"],
    )
    op.create_index(
        "ix_world_model_versions_created_at",
        "world_model_versions",
        ["created_at"],
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_world_model_versions_version_active",
        "world_model_versions",
        ["version", "is_active"],
    )

    # -----------------------------------------------------------------------
    # rl_agent_policy_versions 表：RL 策略版本记录
    # -----------------------------------------------------------------------
    op.create_table(
        "rl_agent_policy_versions",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="策略版本记录 ID（rlpv_ 前缀 + uuid）",
        ),
        sa.Column(
            "version",
            sa.String(32),
            nullable=False,
            comment="版本号（semver，如 1.0.0）",
        ),
        sa.Column(
            "model_uri",
            sa.String(256),
            nullable=False,
            comment="策略模型 URI（model://rl_agent/<version>）",
        ),
        sa.Column(
            "algorithm",
            sa.String(16),
            nullable=False,
            server_default="ppo",
            comment="策略算法（ppo/dqn/sac，v1 仅 ppo）",
        ),
        sa.Column(
            "description",
            sa.Text,
            nullable=True,
            comment="版本描述",
        ),
        sa.Column(
            "training_episodes",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="训练 episode 数",
        ),
        sa.Column(
            "training_steps",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="训练步数",
        ),
        sa.Column(
            "mean_reward",
            sa.Float,
            nullable=False,
            server_default="0.0",
            comment="训练时平均 episode 奖励",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("0"),
            comment="是否为当前激活版本",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        comment="ADR-017 阶段 8 p8：RL 策略版本记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_rl_agent_policy_versions_version",
        "rl_agent_policy_versions",
        ["version"],
    )
    # model_uri 唯一索引（与 ORM unique=True 对齐）
    op.create_index(
        "ix_rl_agent_policy_versions_model_uri",
        "rl_agent_policy_versions",
        ["model_uri"],
        unique=True,
    )
    op.create_index(
        "ix_rl_agent_policy_versions_algorithm",
        "rl_agent_policy_versions",
        ["algorithm"],
    )
    op.create_index(
        "ix_rl_agent_policy_versions_is_active",
        "rl_agent_policy_versions",
        ["is_active"],
    )
    op.create_index(
        "ix_rl_agent_policy_versions_created_at",
        "rl_agent_policy_versions",
        ["created_at"],
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_rl_agent_policy_versions_version_active",
        "rl_agent_policy_versions",
        ["version", "is_active"],
    )
    op.create_index(
        "ix_rl_agent_policy_versions_algo_active",
        "rl_agent_policy_versions",
        ["algorithm", "is_active"],
    )

    # -----------------------------------------------------------------------
    # rl_agent_training_runs 表：RL 训练运行记录
    # -----------------------------------------------------------------------
    op.create_table(
        "rl_agent_training_runs",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="训练运行 ID（rltr_ 前缀 + uuid）",
        ),
        sa.Column(
            "policy_version_id",
            sa.String(64),
            sa.ForeignKey("rl_agent_policy_versions.id", ondelete="SET NULL"),
            nullable=True,
            comment="关联策略版本 ID（可空，训练新建策略时暂未注册版本）",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="idle",
            comment="训练状态（idle/running/paused/completed/failed/stopping）",
        ),
        sa.Column(
            "current_step",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="当前训练步数",
        ),
        sa.Column(
            "current_episode",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="当前 episode 数",
        ),
        sa.Column(
            "total_steps_target",
            sa.Integer,
            nullable=True,
            comment="目标训练步数（可空，表示无上限）",
        ),
        sa.Column(
            "total_episodes_target",
            sa.Integer,
            nullable=True,
            comment="目标 episode 数（可空，表示无上限）",
        ),
        sa.Column(
            "metrics_json",
            sa.Text,
            nullable=True,
            comment="训练指标快照 JSON（policy_loss/value_loss/entropy/mean_reward 等）",
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
            comment="训练失败时的错误信息（status=failed 时填写）",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="训练开始时间",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="训练结束时间（completed/failed）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="记录创建时间",
        ),
        comment="ADR-017 阶段 8 p8：RL 训练运行记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_rl_agent_training_runs_policy_version_id",
        "rl_agent_training_runs",
        ["policy_version_id"],
    )
    op.create_index(
        "ix_rl_agent_training_runs_status",
        "rl_agent_training_runs",
        ["status"],
    )
    op.create_index(
        "ix_rl_agent_training_runs_started_at",
        "rl_agent_training_runs",
        ["started_at"],
    )
    op.create_index(
        "ix_rl_agent_training_runs_created_at",
        "rl_agent_training_runs",
        ["created_at"],
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_rl_agent_training_runs_status_created",
        "rl_agent_training_runs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    """Drop rl_agent_training_runs / rl_agent_policy_versions / world_model_versions."""
    # rl_agent_training_runs（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_rl_agent_training_runs_status_created",
        table_name="rl_agent_training_runs",
    )
    op.drop_index(
        "ix_rl_agent_training_runs_created_at",
        table_name="rl_agent_training_runs",
    )
    op.drop_index(
        "ix_rl_agent_training_runs_started_at",
        table_name="rl_agent_training_runs",
    )
    op.drop_index(
        "ix_rl_agent_training_runs_status",
        table_name="rl_agent_training_runs",
    )
    op.drop_index(
        "ix_rl_agent_training_runs_policy_version_id",
        table_name="rl_agent_training_runs",
    )
    op.drop_table("rl_agent_training_runs")

    # rl_agent_policy_versions（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_rl_agent_policy_versions_algo_active",
        table_name="rl_agent_policy_versions",
    )
    op.drop_index(
        "ix_rl_agent_policy_versions_version_active",
        table_name="rl_agent_policy_versions",
    )
    op.drop_index(
        "ix_rl_agent_policy_versions_created_at",
        table_name="rl_agent_policy_versions",
    )
    op.drop_index(
        "ix_rl_agent_policy_versions_is_active",
        table_name="rl_agent_policy_versions",
    )
    op.drop_index(
        "ix_rl_agent_policy_versions_algorithm",
        table_name="rl_agent_policy_versions",
    )
    op.drop_index(
        "ix_rl_agent_policy_versions_model_uri",
        table_name="rl_agent_policy_versions",
    )
    op.drop_index(
        "ix_rl_agent_policy_versions_version",
        table_name="rl_agent_policy_versions",
    )
    op.drop_table("rl_agent_policy_versions")

    # world_model_versions（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_world_model_versions_version_active",
        table_name="world_model_versions",
    )
    op.drop_index(
        "ix_world_model_versions_created_at",
        table_name="world_model_versions",
    )
    op.drop_index(
        "ix_world_model_versions_is_active",
        table_name="world_model_versions",
    )
    op.drop_index(
        "ix_world_model_versions_model_uri",
        table_name="world_model_versions",
    )
    op.drop_index(
        "ix_world_model_versions_version",
        table_name="world_model_versions",
    )
    op.drop_table("world_model_versions")
