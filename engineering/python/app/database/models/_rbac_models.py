"""RBAC ORM 模型（Role / Permission / RolePermission，从 training_task 拆出）。"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import relationship

from app.database.models._base import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, comment="Role display name")
    code = Column(String(32), nullable=False, unique=True, index=True, comment="Role code identifier")
    description = Column(String(256), nullable=True, comment="Role description")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    permissions = relationship("Permission", secondary="role_permissions", back_populates="roles", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Role(id={self.id}, code={self.code})>"


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, comment="Permission display name")
    code = Column(String(64), nullable=False, unique=True, index=True, comment="Permission code identifier")
    description = Column(String(256), nullable=True, comment="Permission description")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    roles = relationship("Role", secondary="role_permissions", back_populates="permissions", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Permission(id={self.id}, code={self.code})>"


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        Index("idx_role_permissions_role", "role_id"),
        Index("idx_role_permissions_permission", "permission_id"),
    )

    def __repr__(self) -> str:
        return f"<RolePermission(role_id={self.role_id}, permission_id={self.permission_id})>"
