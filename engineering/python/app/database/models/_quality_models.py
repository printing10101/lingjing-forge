"""制造域 ORM 模型（从 manufacturing 拆出）。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Index,
    text,
)

from app.database.models.machining_record import Base


class QualityRecord(Base):
    __tablename__ = "quality_records"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    inspection_no = Column(String(64), nullable=False, unique=True, index=True, comment="检验编号")
    batch_no = Column(String(64), nullable=False, index=True, comment="批次号")
    inspection_type = Column(String(32), nullable=False, index=True, comment="检验类型: 进料检验/过程检验/成品检验")
    result = Column(String(16), nullable=False, index=True, comment="结果: 合格/不合格/待判定")
    inspector = Column(String(64), nullable=False, comment="检验员")
    notes = Column(String(512), nullable=True, comment="备注")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (Index("idx_qr_type_result", "inspection_type", "result"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "inspection_no": self.inspection_no,
            "batch_no": self.batch_no,
            "inspection_type": self.inspection_type,
            "result": self.result,
            "inspector": self.inspector,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class QualityAnomaly(Base):
    __tablename__ = "quality_anomalies"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    record_id = Column(String(64), ForeignKey("quality_records.id", ondelete="CASCADE"), nullable=False, index=True)
    anomaly_type = Column(String(32), nullable=False, index=True, comment="异常类型: 尺寸偏差/表面缺陷/材料问题/其他")
    description = Column(String(512), nullable=True, comment="描述")
    severity = Column(String(16), nullable=False, comment="严重程度")
    status = Column(String(16), nullable=False, default="待处理", index=True, comment="状态: 待处理/处理中/已解决")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (Index("idx_qa_type_status", "anomaly_type", "status"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "anomaly_type": self.anomaly_type,
            "description": self.description,
            "severity": self.severity,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Production Models (production_records / work_orders)
