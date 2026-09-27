"""制造域 ORM 模型（从 manufacturing 拆出）。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Index,
    text,
)

from app.database.models.machining_record import Base


class Material(Base):
    """制造物料模型。"""

    __tablename__ = "materials"

    id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    code = Column(
        String(64),
        nullable=False,
        index=True,
        comment="物料编码",
    )
    name = Column(
        String(128),
        nullable=False,
        comment="名称",
    )
    spec = Column(
        String(256),
        nullable=True,
        comment="规格",
    )
    category = Column(
        String(32),
        nullable=False,
        default="原材料",
        index=True,
        comment="分类: 原材料/半成品/成品",
    )
    quantity = Column(
        Integer,
        nullable=False,
        default=0,
        comment="库存数量",
    )
    safe_quantity = Column(
        Integer,
        nullable=False,
        default=0,
        comment="安全库存",
    )
    status = Column(
        String(16),
        nullable=False,
        default="正常",
        index=True,
        comment="状态: 正常/低库存/缺货",
    )
    location = Column(
        String(64),
        nullable=True,
        comment="库位",
    )
    unit = Column(
        String(16),
        nullable=True,
        comment="单位",
    )
    supplier = Column(
        String(128),
        nullable=True,
        comment="供应商",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )

    __table_args__ = (
        Index("idx_materials_category_status", "category", "status"),
        Index("idx_materials_code", "code"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "spec": self.spec,
            "category": self.category,
            "quantity": self.quantity,
            "safe_quantity": self.safe_quantity,
            "status": self.status,
            "location": self.location,
            "unit": self.unit,
            "supplier": self.supplier,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Material(id={self.id}, code={self.code}, name={self.name})>"


# Equipment Models (equipment / equipment_alarms / maintenance_plans)
