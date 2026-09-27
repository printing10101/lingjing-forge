"""SQLAlchemy ORM model for cutting experience records (P2-1/P2-2).

数据飞轮持久化底座。将 `app.contracts.cutting_experience.CuttingExperience`
契约对象映射为关系表，支持：

- **扁平核心列**：machine/tool/material/machining_type/result 等用于筛选与
  聚合索引的字段直接建列（沿用 ``MachiningRecord`` 的索引策略）。
- **JSON 嵌套**：``parameters`` / ``results`` / ``anomalies`` / ``tags``
  以 PostgreSQL JSONB（SQLite 测试环境回退 JSON）存储，避免建表膨胀，
  同时保持契约结构的完整性。

约定：
- 独立 ``declarative_base``，与 ``machining_record.Base`` 解耦，便于
  alembic 单独合并元数据。
- ``from_contract`` / ``to_contract_dict`` 双向转换保持契约 ↔ ORM 单一事实源。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    String,
    Float,
    Integer,
    DateTime,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _new_experience_id() -> str:
    """生成 CuttingExperienceRecord 主键 ID。"""
    return f"exp_{uuid.uuid4().hex}"


class CuttingExperienceRecord(Base):  # type: ignore[misc, valid-type]
    """切削实测记录 ORM 模型（数据飞轮核心表）。"""

    __tablename__ = "cutting_experiences"

    id = Column(
        String(64),
        primary_key=True,
        default=_new_experience_id,
        comment="记录主键 ID（exp_ 前缀 + UUID4 hex）",
    )
    job_id = Column(
        String(64),
        nullable=True,
        comment="关联工艺任务 ID（可能为空：手工录入/设备直采无任务上下文）",
    )
    machine_id = Column(
        String(64),
        nullable=False,
        comment="机床标识，关联 machines.json 中的 machine.id",
    )
    program_number = Column(
        String(32),
        nullable=False,
        default="",
        comment="NC 程序号",
    )
    tool_id = Column(
        String(64),
        nullable=False,
        comment="刀具标识，关联 tools.json 中的 tool.id",
    )
    material = Column(
        String(64),
        nullable=False,
        default="",
        comment="工件材料名称",
    )
    machining_type = Column(
        String(32),
        nullable=False,
        default="milling",
        comment="加工类型（milling/turning/drilling/tapping/boring/grooving/threading）",
    )
    result = Column(
        String(16),
        nullable=False,
        default="ok",
        comment="加工结果（ok/rework/scrap）",
    )
    cycle_time_s = Column(
        Float,
        nullable=True,
        comment="实际加工节拍（s）",
    )
    surface_roughness_ra = Column(
        Float,
        nullable=True,
        comment="表面粗糙度 Ra（μm）",
    )
    tool_wear_percent = Column(
        Float,
        nullable=True,
        comment="刀具磨损百分比 0-100",
    )
    dimensional_error_mm = Column(
        Float,
        nullable=True,
        comment="尺寸误差绝对值（mm）",
    )
    anomaly_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="异常快照条数（>0 表示本次加工存在异常）",
    )
    parameters = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        comment="切削工艺参数（depth_of_cut_mm/feed_mm_per_rev/spindle_rpm/coolant…）",
    )
    results_extra = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        comment="结果附加字段（未来扩展，避免频繁改表）",
    )
    anomalies = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
        comment="异常快照列表（chatter/overload/temperature…）",
    )
    tags = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        comment="自由扩展元数据",
    )
    operator = Column(
        String(64),
        nullable=True,
        comment="操作员",
    )
    source = Column(
        String(32),
        nullable=False,
        default="manual",
        comment="数据来源：manual/mtconnect/api",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="记录创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        comment="记录最后更新时间",
    )

    __table_args__ = (
        Index("ix_cutting_experiences_machine_id", "machine_id"),
        Index("ix_cutting_experiences_tool_id", "tool_id"),
        Index("ix_cutting_experiences_material", "material"),
        Index("ix_cutting_experiences_created_at", "created_at"),
        Index(
            "ix_cutting_experiences_machine_created",
            "machine_id",
            "created_at",
        ),
    )

    # 契约 ORM 转换（单一事实源在 app/contracts/cutting_experience.py）

    @classmethod
    def from_contract(cls, record: Any) -> "CuttingExperienceRecord":
        """从 CuttingExperience 契约对象构建 ORM 实例。"""
        from datetime import datetime

        params = record.parameters.model_dump() if hasattr(record.parameters, "model_dump") else dict(record.parameters)
        results = record.results.model_dump() if hasattr(record.results, "model_dump") else dict(record.results)

        # 序列化 anomalies 并处理 datetime 字段
        anomalies = []
        if record.anomalies:
            for a in record.anomalies:
                dump = a.model_dump() if hasattr(a, "model_dump") else dict(a)
                # 将 occurred_at 从 datetime 转为 ISO 字符串（SQLite JSON 兼容）
                if "occurred_at" in dump and isinstance(dump["occurred_at"], datetime):
                    dump["occurred_at"] = dump["occurred_at"].isoformat()
                anomalies.append(dump)
        # results_extra = results 中未扁平化的附加字段；当前所有结果字段均
        # 已扁平建列，因此仅保留原样以便未来扩展（不影响查询）。
        results_extra = {k: v for k, v in results.items() if k not in _FLAT_RESULT_KEYS}

        return cls(
            id=cls._id_or_new(record.id),
            job_id=str(record.job_id) if record.job_id else None,
            machine_id=record.machine_id,
            program_number=record.program_number,
            tool_id=record.tool_id,
            material=record.material,
            machining_type=(
                record.machining_type.value if hasattr(record.machining_type, "value") else str(record.machining_type)
            ),
            result=(
                record.results.result.value if hasattr(record.results.result, "value") else str(record.results.result)
            ),
            cycle_time_s=record.results.cycle_time_s,
            surface_roughness_ra=record.results.surface_roughness_ra,
            tool_wear_percent=record.results.tool_wear_percent,
            dimensional_error_mm=record.results.dimensional_error_mm,
            anomaly_count=len(anomalies),
            parameters=params,
            results_extra=results_extra,
            anomalies=anomalies,
            tags=dict(record.tags or {}),
            operator=record.operator,
            source=record.source,
        )

    @staticmethod
    def _id_or_new(record_id: Any) -> str:
        """主键统一为 str（UUID 转 hex 存储）。"""
        if record_id is None:
            return _new_experience_id()
        s = str(record_id)
        return f"exp_{s.replace('-', '')}" if not s.startswith("exp_") else s

    def to_contract_dict(self) -> dict:
        """序列化为契约结构的 dict（供 API 响应 / 飞轮消费）。"""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "machine_id": self.machine_id,
            "program_number": self.program_number,
            "tool_id": self.tool_id,
            "material": self.material,
            "machining_type": self.machining_type,
            "parameters": self.parameters or {},
            "results": {
                "cycle_time_s": self.cycle_time_s,
                "surface_roughness_ra": self.surface_roughness_ra,
                "tool_wear_percent": self.tool_wear_percent,
                "dimensional_error_mm": self.dimensional_error_mm,
                "result": self.result,
                **(self.results_extra or {}),
            },
            "anomalies": self.anomalies or [],
            "tags": self.tags or {},
            "operator": self.operator,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<CuttingExperienceRecord(id={self.id}, "
            f"machine_id={self.machine_id}, tool_id={self.tool_id}, "
            f"result={self.result})>"
        )


# 已扁平建列的结果字段名（from_contract 中剔除，避免重复存储）
_FLAT_RESULT_KEYS = frozenset(
    {
        "cycle_time_s",
        "surface_roughness_ra",
        "tool_wear_percent",
        "dimensional_error_mm",
        "result",
    }
)


__all__ = ["Base", "CuttingExperienceRecord", "_new_experience_id"]
