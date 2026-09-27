"""制造域 ORM 模型（从 manufacturing 拆出）。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Date,
    ForeignKey,
    Index,
    text,
)

from app.database.models.machining_record import Base


class ProductionRecord(Base):
    __tablename__ = "production_records"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    date = Column(Date, nullable=False, index=True, comment="日期")
    line_name = Column(String(32), nullable=False, index=True, comment="产线名称")
    planned_qty = Column(Integer, nullable=False, comment="计划产量")
    actual_qty = Column(Integer, nullable=False, comment="实际产量")
    qualified_qty = Column(Integer, nullable=False, comment="良品数")
    defect_qty = Column(Integer, nullable=False, default=0, comment="不良数")
    equipment_utilization = Column(Float, nullable=False, comment="设备利用率%")
    energy_consumption = Column(Float, nullable=False, comment="能耗 kWh")
    shift = Column(String(16), nullable=False, comment="班次: 早班/中班/晚班")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (Index("idx_pr_date_line", "date", "line_name"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "line_name": self.line_name,
            "planned_qty": self.planned_qty,
            "actual_qty": self.actual_qty,
            "qualified_qty": self.qualified_qty,
            "defect_qty": self.defect_qty,
            "equipment_utilization": self.equipment_utilization,
            "energy_consumption": self.energy_consumption,
            "shift": self.shift,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_no = Column(String(64), nullable=False, unique=True, index=True, comment="工单号")
    product_name = Column(String(128), nullable=False, comment="产品名称")
    planned_qty = Column(Integer, nullable=False, comment="计划数量")
    completed_qty = Column(Integer, nullable=False, default=0, comment="已完成数量")
    status = Column(
        String(16), nullable=False, default="待开始", index=True, comment="状态: 进行中/已完成/待开始/已延期"
    )
    priority = Column(String(16), nullable=False, default="中", comment="优先级: 紧急/高/中/低")
    start_date = Column(Date, nullable=True, comment="开始日期")
    due_date = Column(Date, nullable=True, comment="截止日期")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (Index("idx_wo_status_priority", "status", "priority"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "order_no": self.order_no,
            "product_name": self.product_name,
            "planned_qty": self.planned_qty,
            "completed_qty": self.completed_qty,
            "status": self.status,
            "priority": self.priority,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Process Route Models (process_routes / process_steps)


class ProcessRoute(Base):
    __tablename__ = "process_routes"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(128), nullable=False, index=True, comment="工艺名称")
    part_type = Column(String(64), nullable=False, index=True, comment="零件类型")
    status = Column(String(16), nullable=False, default="草稿", index=True, comment="状态: 已发布/草稿/已归档")
    steps_count = Column(Integer, nullable=False, default=0, comment="工序数")
    description = Column(String(512), nullable=True, comment="描述")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (Index("idx_prt_status", "status"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "part_type": self.part_type,
            "status": self.status,
            "steps_count": self.steps_count,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProcessStep(Base):
    __tablename__ = "process_steps"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    route_id = Column(String(64), ForeignKey("process_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False, comment="序号")
    name = Column(String(128), nullable=False, comment="工序名称")
    work_center = Column(String(64), nullable=False, comment="工作中心")
    hours = Column(Integer, nullable=False, comment="工时(分钟)")
    equipment = Column(String(128), nullable=True, comment="设备")
    tooling = Column(String(128), nullable=True, comment="工装夹具")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (Index("idx_pst_route_seq", "route_id", "sequence"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "route_id": self.route_id,
            "sequence": self.sequence,
            "name": self.name,
            "work_center": self.work_center,
            "hours": self.hours,
            "equipment": self.equipment,
            "tooling": self.tooling,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Document Model (documents)
