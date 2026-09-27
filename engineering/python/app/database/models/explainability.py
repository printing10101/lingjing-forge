"""可解释性可视化 ORM 模型：解释记录 + 解释对比记录持久化.

对应 ADR-016（可解释性可视化）。

新增 2 张表（与 training_task.Base 共享 metadata，与 project_package.py 同源）：
    - ``explanation_records``：解释任务记录（explanation_type / model_uri /
      payload_path / input_signature）
    - ``explanation_comparisons``：解释对比记录（base / compared / diff_payload_path）

设计要点
--------
    - payload（含大型数组：隐状态投影坐标 / 门控值序列 / 反事实扫描曲线 /
      MC dropout 直方图）以 JSON 文件存盘，数据库只存元数据 + payload_path，
      避免 JSON 数组膨胀数据库（与 project_package.py 风格一致）
    - input_signature 字段索引，支持相同输入 + 相同模型 + 相同解释类型的
      解释结果去重与缓存命中查询
    - explanation_type / comparison_type 为字符串（与契约层常量对齐），
      不使用枚举类型以保持 SQLite 兼容性
    - source_snapshot_id 为字符串字段（不建外键），因为快照 ID 来自
      ExperimentSnapshot，在解释生成时不一定存在对应记录
    - expires_at 可空，过期解释由清理任务删除 payload 文件 + 数据库记录
    - 与 project_package.py 风格对齐：uuid 前缀生成器 + to_dict() + __repr__
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.utils.time import utcnow

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.models.training_task import Base


def _gen_explanation_id() -> str:
    """生成解释记录 ID（exp_ 前缀 + uuid hex）."""
    return f"exp_{uuid.uuid4().hex}"


def _gen_comparison_id() -> str:
    """生成对比记录 ID（cmp_ 前缀 + uuid hex）."""
    return f"cmp_{uuid.uuid4().hex}"


class ExplanationRecord(Base):
    """解释记录 ORM.

    持久化每次解释生成任务的元数据与 payload 文件路径，支持前端查询历史解释
    与下载 payload 内容。

    状态机
    ------
    - 无显式状态机：解释生成是同步操作，生成成功即写入记录，失败则不写入
    - payload_path 指向 JSON 文件，文件内容为对应 ExplanationXxx.to_payload() 结果
    - expires_at 可空，过期后由清理任务删除 payload 文件 + 数据库记录

    注意
    ----
    - source_snapshot_id 为字符串字段（不建外键），因为快照 ID 来自
      ExperimentSnapshot，在解释生成时不一定存在对应记录
    - input_signature 索引，支持去重查询（相同输入 + 相同模型 + 相同解释类型
      可复用历史解释结果）
    """

    __tablename__ = "explanation_records"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_explanation_id,
        comment="解释 ID（exp_ 前缀 + uuid）",
    )
    explanation_type = Column(
        String(32),
        nullable=False,
        index=True,
        comment="解释类型：hidden_state/gate_dynamics/counterfactual/confidence",
    )
    model_uri = Column(
        String(256),
        nullable=False,
        index=True,
        comment="解释所用模型 URI（如 model://LTC-ChatterPredictor/1.0.0）",
    )
    source_snapshot_id = Column(
        String(64),
        nullable=True,
        index=True,
        comment="关联实验快照 ID（来自 ExperimentSnapshot，不建外键）",
    )
    input_signature = Column(
        String(64),
        nullable=False,
        index=True,
        comment="输入签名 sha256 前 16 字符（去重与缓存命中查询）",
    )
    payload_path = Column(
        String(512),
        nullable=False,
        comment="payload JSON 文件绝对路径（含大型数组）",
    )
    payload_size_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
        comment="payload 文件大小（字节）",
    )
    metadata_json = Column(
        Text,
        nullable=True,
        comment="附加元数据 JSON（降维方法/采样次数/异常帧数等）",
    )
    created_by = Column(
        String(128),
        nullable=True,
        index=True,
        comment="创建者 user_id 或 plugin_id",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
        comment="创建时间",
    )
    expires_at = Column(
        DateTime,
        nullable=True,
        index=True,
        comment="过期时间（过期后由清理任务删除 payload 文件 + 记录）",
    )

    # 关联：一个解释可被多次作为 base 或 compared 参与对比
    comparisons_as_base = relationship(
        "ExplanationComparison",
        foreign_keys="ExplanationComparison.base_explanation_id",
        lazy="selectin",
        back_populates="base_explanation",
    )
    comparisons_as_compared = relationship(
        "ExplanationComparison",
        foreign_keys="ExplanationComparison.compared_explanation_id",
        lazy="selectin",
        back_populates="compared_explanation",
    )

    __table_args__ = (
        Index(
            "ix_explanation_records_type_model",
            "explanation_type",
            "model_uri",
        ),
        Index(
            "ix_explanation_records_signature_type",
            "input_signature",
            "explanation_type",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        metadata: dict[str, Any] = {}
        if self.metadata_json:
            try:
                metadata = json.loads(str(self.metadata_json))
            except (ValueError, TypeError):
                metadata = {}
        return {
            "id": self.id,
            "explanation_type": self.explanation_type,
            "model_uri": self.model_uri,
            "source_snapshot_id": self.source_snapshot_id,
            "input_signature": self.input_signature,
            "payload_path": self.payload_path,
            "payload_size_bytes": self.payload_size_bytes,
            "metadata": metadata,
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ExplanationRecord(id={self.id}, type={self.explanation_type}, "
            f"model_uri={self.model_uri}, payload_size={self.payload_size_bytes})>"
        )


class ExplanationComparison(Base):
    """解释对比记录 ORM.

    持久化两次解释的差异 payload 路径，支持跨模型版本/跨输入的对比分析。

    对比类型
    --------
    - same_model_diff_input：同模型不同输入（诊断输入敏感性）
    - diff_model_same_input：不同模型同输入（模型版本对比）
    - diff_model_diff_input：不同模型不同输入（综合对比）

    注意
    ----
    - base_explanation_id / compared_explanation_id 外键关联
      explanation_records.id，ondelete=CASCADE（删除解释时连带删除对比记录）
    - diff_payload_path 指向 JSON 文件，文件内容为差异分析结果
    """

    __tablename__ = "explanation_comparisons"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_comparison_id,
        comment="对比 ID（cmp_ 前缀 + uuid）",
    )
    base_explanation_id = Column(
        String(64),
        ForeignKey("explanation_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="基准解释记录 ID",
    )
    compared_explanation_id = Column(
        String(64),
        ForeignKey("explanation_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="对比解释记录 ID",
    )
    comparison_type = Column(
        String(64),
        nullable=False,
        comment="对比类型：same_model_diff_input/diff_model_same_input/diff_model_diff_input",
    )
    diff_payload_path = Column(
        String(512),
        nullable=False,
        comment="差异 payload JSON 文件路径",
    )
    created_by = Column(
        String(128),
        nullable=True,
        index=True,
        comment="创建者 user_id 或 plugin_id",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
        comment="创建时间",
    )

    base_explanation = relationship(
        "ExplanationRecord",
        foreign_keys=[base_explanation_id],
        back_populates="comparisons_as_base",
    )
    compared_explanation = relationship(
        "ExplanationRecord",
        foreign_keys=[compared_explanation_id],
        back_populates="comparisons_as_compared",
    )

    __table_args__ = (
        Index(
            "ix_explanation_comparisons_base_compared",
            "base_explanation_id",
            "compared_explanation_id",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "id": self.id,
            "base_explanation_id": self.base_explanation_id,
            "compared_explanation_id": self.compared_explanation_id,
            "comparison_type": self.comparison_type,
            "diff_payload_path": self.diff_payload_path,
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ExplanationComparison(id={self.id}, base={self.base_explanation_id}, "
            f"compared={self.compared_explanation_id}, type={self.comparison_type})>"
        )


__all__ = [
    "ExplanationRecord",
    "ExplanationComparison",
    "_gen_explanation_id",
    "_gen_comparison_id",
]
