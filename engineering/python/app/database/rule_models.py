"""
SQLAlchemy ORM models for process rules database.

Defines RuleGroup and ProcessRule models for the rule_groups and rules tables.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class RuleGroup(Base):  # type: ignore[misc, valid-type]
    """规则分组模型"""

    __tablename__ = "rule_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=False, server_default=text("''"))
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

    rules = relationship("ProcessRule", back_populates="group", lazy="select")

    def __repr__(self) -> str:
        return f"<RuleGroup(id={self.id}, name={self.name})>"


class ProcessRule(Base):  # type: ignore[misc, valid-type]
    """工艺规则模型"""

    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False, server_default=text("''"))
    group_id = Column(Integer, ForeignKey("rule_groups.id", ondelete="SET NULL"), nullable=True)
    conditions_json = Column(Text, nullable=False)
    logic_operator = Column(String, nullable=False, server_default=text("'AND'"))
    result_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, server_default=text("'active'"))
    priority = Column(Integer, nullable=False, server_default=text("0"))
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

    group = relationship("RuleGroup", back_populates="rules", lazy="select")

    __table_args__ = (
        Index("idx_rules_group_id", "group_id"),
        Index("idx_rules_status", "status"),
        Index("idx_rules_name", "name"),
    )

    def __repr__(self) -> str:
        return f"<ProcessRule(id={self.id}, name={self.name}, status={self.status})>"
