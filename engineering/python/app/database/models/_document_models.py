"""制造域 ORM 模型（从 manufacturing 拆出）。"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    JSON,
    Index,
    text,
)

from app.database.models.machining_record import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(256), nullable=False, index=True, comment="标题")
    category = Column(
        String(32), nullable=False, index=True, comment="分类: 工艺规范/SOP标准/设备手册/质量标准/材料参数"
    )
    version = Column(String(16), nullable=False, default="v1.0", comment="版本")
    author = Column(String(64), nullable=False, comment="作者")
    content = Column(String(4096), nullable=True, comment="内容/描述")
    tags = Column(JSON, nullable=True, comment="标签 JSON数组")
    status = Column(String(16), nullable=False, default="待审核", index=True, comment="状态: 已发布/待审核")
    view_count = Column(Integer, nullable=False, default=0, comment="浏览量")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (Index("idx_doc_category_status", "category", "status"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "version": self.version,
            "author": self.author,
            "content": self.content,
            "tags": self.tags or [],
            "status": self.status,
            "view_count": self.view_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
