"""刀具库模型。

定义刀具(Tool)的ORM模型，支持刀具参数管理、磨损跟踪和寿命预测集成。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, String, Integer, Float, DateTime, Text, text

from app.database.models.machining_record import Base


class Tool(Base):
    """刀具模型。

    存储刀具的基本参数、使用状态和磨损信息。

    Attributes:
        id: 刀具唯一标识 (UUID)
        code: 刀具编码 (用于CNC程序中的T代码)
        name: 刀具名称
        type: 刀具类型 (end_mill/ball_mill/drill/reamer/tap/insert/grooving/threading)
        diameter: 刀具直径 (mm)
        length: 刀具长度 (mm)
        flute_count: 刃数
        material: 刀具材料 (carbide/hss/ceramic/cbn/diamond)
        coating: 涂层类型 (TiN/TiAlN/AlCrN/DLC/None)
        max_rpm: 最大允许转速 (RPM)
        max_feed: 最大允许进给 (mm/min)
        usage_time: 累计使用时间 (分钟)
        wear_amount: 磨损量 (mm)
        last_sharpened: 上次刃磨时间
        status: 刀具状态 (active/worn/broken/maintenance)
        vendor: 供应商
        cost: 采购成本
        notes: 备注
        created_at: 创建时间
        updated_at: 更新时间
    """

    __tablename__ = "tools"

    id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    code = Column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        comment="刀具编码 (T01, T02, ...)",
    )
    name = Column(
        String(128),
        nullable=False,
        comment="刀具名称",
    )
    type = Column(
        String(32),
        nullable=False,
        index=True,
        comment="刀具类型: end_mill/ball_mill/drill/reamer/tap/insert/grooving/threading",
    )
    diameter = Column(
        Float,
        nullable=False,
        comment="刀具直径 (mm)",
    )
    length = Column(
        Float,
        nullable=True,
        comment="刀具长度 (mm)",
    )
    flute_count = Column(
        Integer,
        nullable=True,
        default=2,
        comment="刃数",
    )
    material = Column(
        String(32),
        nullable=True,
        comment="刀具材料: carbide/hss/ceramic/cbn/diamond",
    )
    coating = Column(
        String(32),
        nullable=True,
        comment="涂层类型: TiN/TiAlN/AlCrN/DLC/None",
    )
    max_rpm = Column(
        Float,
        nullable=True,
        comment="最大允许转速 (RPM)",
    )
    max_feed = Column(
        Float,
        nullable=True,
        comment="最大允许进给 (mm/min)",
    )

    # 磨损跟踪字段
    usage_time = Column(
        Float,
        nullable=False,
        default=0.0,
        comment="累计使用时间 (分钟)",
    )
    wear_amount = Column(
        Float,
        nullable=False,
        default=0.0,
        comment="磨损量 (mm)",
    )
    last_sharpened = Column(
        DateTime,
        nullable=True,
        comment="上次刃磨时间",
    )
    status = Column(
        String(16),
        nullable=False,
        default="active",
        index=True,
        comment="刀具状态: active/worn/broken/maintenance",
    )

    # 供应商和成本信息
    vendor = Column(
        String(128),
        nullable=True,
        comment="供应商",
    )
    cost = Column(
        Float,
        nullable=True,
        comment="采购成本",
    )
    notes = Column(
        Text,
        nullable=True,
        comment="备注",
    )

    # 时间戳
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

    def to_dict(self) -> dict:
        """转换为字典。

        Returns:
            刀具信息的字典表示
        """
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "diameter": self.diameter,
            "length": self.length,
            "flute_count": self.flute_count,
            "material": self.material,
            "coating": self.coating,
            "max_rpm": self.max_rpm,
            "max_feed": self.max_feed,
            "usage_time": self.usage_time,
            "wear_amount": self.wear_amount,
            "last_sharpened": self.last_sharpened.isoformat() if self.last_sharpened else None,
            "status": self.status,
            "vendor": self.vendor,
            "cost": self.cost,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def wear_percentage(self) -> float:
        """计算磨损百分比。

        假设最大允许磨损量为0.3mm（典型值）。

        Returns:
            磨损百分比 (0-100)
        """
        max_wear = 0.3  # mm
        return min(100.0, (float(self.wear_amount or 0.0) / max_wear) * 100.0)

    @property
    def is_worn(self) -> bool:
        """判断刀具是否已磨损。

        Returns:
            True if 磨损量超过阈值或状态为worn
        """
        return float(self.wear_percentage or 0.0) > 80.0 or str(self.status) == "worn"

    @property
    def tool_life_remaining(self) -> float:
        """估算剩余刀具寿命（基于使用时间）。

        假设典型刀具寿命为240分钟（4小时）。

        Returns:
            剩余寿命百分比 (0-100)
        """
        typical_tool_life = 240.0  # 分钟
        remaining = max(0.0, typical_tool_life - float(self.usage_time or 0.0))
        return (remaining / typical_tool_life) * 100.0
