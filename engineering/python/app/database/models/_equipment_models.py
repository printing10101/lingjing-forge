"""制造域 ORM 模型（从 manufacturing 拆出）。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    ForeignKey,
    Index,
    text,
)
from sqlalchemy.orm import relationship

from app.database.models.machining_record import Base


class Equipment(Base):
    """设备监控模型。"""

    __tablename__ = "equipment"

    id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name = Column(String(128), nullable=False, comment="设备名称")
    model = Column(String(128), nullable=False, comment="型号")
    location = Column(String(64), nullable=False, comment="位置")
    status = Column(
        String(32),
        nullable=False,
        default="待机",
        index=True,
        comment="状态: 运行中/待机/维护中/故障",
    )
    temperature = Column(Float, nullable=True, comment="温度")
    vibration = Column(Float, nullable=True, comment="振动值")
    rpm = Column(Float, nullable=True, comment="转速")
    power = Column(Float, nullable=True, comment="功率")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    alarms = relationship("EquipmentAlarm", back_populates="equipment", lazy="selectin")
    maintenance_plans = relationship("MaintenancePlan", back_populates="equipment", lazy="selectin")

    __table_args__ = (Index("idx_equipment_status", "status"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "location": self.location,
            "status": self.status,
            "temperature": self.temperature,
            "vibration": self.vibration,
            "rpm": self.rpm,
            "power": self.power,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Equipment(id={self.id}, name={self.name}, status={self.status})>"


class EquipmentAlarm(Base):
    """设备告警模型。"""

    __tablename__ = "equipment_alarms"

    id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    equipment_id = Column(
        String(64),
        ForeignKey("equipment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alarm_type = Column(
        String(32),
        nullable=False,
        comment="告警类型: 温度异常/振动异常/功率异常/设备故障/维护提醒",
    )
    severity = Column(
        String(16),
        nullable=False,
        comment="严重程度: 紧急/警告/提示",
    )
    message = Column(String(512), nullable=False, comment="告警信息")
    status = Column(
        String(16),
        nullable=False,
        default="未处理",
        index=True,
        comment="状态: 未处理/已确认/已解决",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    equipment = relationship("Equipment", back_populates="alarms")

    __table_args__ = (
        Index("idx_alarm_equipment_status", "equipment_id", "status"),
        Index("idx_alarm_severity", "severity"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "equipment_id": self.equipment_id,
            "alarm_type": self.alarm_type,
            "severity": self.severity,
            "message": self.message,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<EquipmentAlarm(id={self.id}, type={self.alarm_type}, severity={self.severity})>"


class MaintenancePlan(Base):
    """设备维护计划模型。"""

    __tablename__ = "maintenance_plans"

    id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    equipment_id = Column(
        String(64),
        ForeignKey("equipment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(128), nullable=False, comment="维护项目")
    type = Column(
        String(32),
        nullable=False,
        comment="类型: 定期保养/故障维修/预防性维护",
    )
    frequency = Column(
        String(16),
        nullable=False,
        comment="频次: 每日/每周/每月/每季度",
    )
    last_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="上次维护日期",
    )
    next_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="下次维护日期",
    )
    status = Column(
        String(16),
        nullable=False,
        default="待执行",
        index=True,
        comment="状态: 待执行/进行中/已完成/已逾期",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    equipment = relationship("Equipment", back_populates="maintenance_plans")

    __table_args__ = (
        Index("idx_maintenance_equipment", "equipment_id"),
        Index("idx_maintenance_status", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "equipment_id": self.equipment_id,
            "title": self.title,
            "type": self.type,
            "frequency": self.frequency,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "next_date": self.next_date.isoformat() if self.next_date else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<MaintenancePlan(id={self.id}, title={self.title}, status={self.status})>"


# Quality Inspection Models (quality_records / quality_anomalies)
