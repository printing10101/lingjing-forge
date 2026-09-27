"""add_manufacturing_and_tool_tables

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f6
Create Date: 2026-07-07 10:00:00.000000

补齐 ``app/database/models/manufacturing.py`` 与 ``app/database/models/tool.py``
中定义但未生成 Alembic 迁移的 12 张核心业务表：

    materials / equipment / equipment_alarms / maintenance_plans
    quality_records / quality_anomalies
    production_records / work_orders
    process_routes / process_steps
    documents / tools

设计原则：
    - 字段定义严格对齐 ORM 模型（类型 / 约束 / 索引 / 外键级联）。
    - 时间戳列使用 ``server_default=CURRENT_TIMESTAMP`` 以保证跨库兼容。
    - 复合索引名称与 ORM ``__table_args__`` 中显式命名的索引保持一致。
    - 升级使用 ``op.create_table`` + ``op.create_index``；降级反向删除。
    - 不依赖 PostgreSQL 专属语法，确保 SQLite 单元测试环境可执行。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: 创建 12 张制造域业务表。"""

    # ------------------------------------------------------------------ materials
    op.create_table(
        "materials",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="物料编码"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="名称"),
        sa.Column("spec", sa.String(length=256), nullable=True, comment="规格"),
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            server_default="原材料",
            comment="分类: 原材料/半成品/成品",
        ),
        sa.Column(
            "quantity",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="库存数量",
        ),
        sa.Column(
            "safe_quantity",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="安全库存",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="正常",
            comment="状态: 正常/低库存/缺货",
        ),
        sa.Column("location", sa.String(length=64), nullable=True, comment="库位"),
        sa.Column("unit", sa.String(length=16), nullable=True, comment="单位"),
        sa.Column("supplier", sa.String(length=128), nullable=True, comment="供应商"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_materials_code", "materials", ["code"], unique=False)
    op.create_index("ix_materials_category", "materials", ["category"], unique=False)
    op.create_index("ix_materials_status", "materials", ["status"], unique=False)
    op.create_index(
        "idx_materials_category_status", "materials", ["category", "status"], unique=False
    )

    # ------------------------------------------------------------------ equipment
    op.create_table(
        "equipment",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, comment="设备名称"),
        sa.Column("model", sa.String(length=128), nullable=False, comment="型号"),
        sa.Column("location", sa.String(length=64), nullable=False, comment="位置"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="待机",
            comment="状态: 运行中/待机/维护中/故障",
        ),
        sa.Column("temperature", sa.Float(), nullable=True, comment="温度"),
        sa.Column("vibration", sa.Float(), nullable=True, comment="振动值"),
        sa.Column("rpm", sa.Float(), nullable=True, comment="转速"),
        sa.Column("power", sa.Float(), nullable=True, comment="功率"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("idx_equipment_status", "equipment", ["status"], unique=False)

    # ------------------------------------------------------------------ equipment_alarms
    op.create_table(
        "equipment_alarms",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(length=64),
            sa.ForeignKey("equipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "alarm_type",
            sa.String(length=32),
            nullable=False,
            comment="告警类型: 温度异常/振动异常/功率异常/设备故障/维护提醒",
        ),
        sa.Column(
            "severity",
            sa.String(length=16),
            nullable=False,
            comment="严重程度: 紧急/警告/提示",
        ),
        sa.Column("message", sa.String(length=512), nullable=False, comment="告警信息"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="未处理",
            comment="状态: 未处理/已确认/已解决",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_equipment_alarms_equipment_id",
        "equipment_alarms",
        ["equipment_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_alarms_status", "equipment_alarms", ["status"], unique=False
    )
    op.create_index(
        "idx_alarm_equipment_status",
        "equipment_alarms",
        ["equipment_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_alarm_severity", "equipment_alarms", ["severity"], unique=False
    )

    # ------------------------------------------------------------------ maintenance_plans
    op.create_table(
        "maintenance_plans",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(length=64),
            sa.ForeignKey("equipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=128), nullable=False, comment="维护项目"),
        sa.Column(
            "type",
            sa.String(length=32),
            nullable=False,
            comment="类型: 定期保养/故障维修/预防性维护",
        ),
        sa.Column(
            "frequency",
            sa.String(length=16),
            nullable=False,
            comment="频次: 每日/每周/每月/每季度",
        ),
        sa.Column("last_date", sa.DateTime(timezone=True), nullable=True, comment="上次维护日期"),
        sa.Column("next_date", sa.DateTime(timezone=True), nullable=True, comment="下次维护日期"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="待执行",
            comment="状态: 待执行/进行中/已完成/已逾期",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_maintenance_plans_equipment_id",
        "maintenance_plans",
        ["equipment_id"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_plans_status", "maintenance_plans", ["status"], unique=False
    )
    op.create_index(
        "idx_maintenance_equipment", "maintenance_plans", ["equipment_id"], unique=False
    )
    op.create_index(
        "idx_maintenance_status", "maintenance_plans", ["status"], unique=False
    )

    # ------------------------------------------------------------------ quality_records
    op.create_table(
        "quality_records",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "inspection_no",
            sa.String(length=64),
            nullable=False,
            unique=True,
            comment="检验编号",
        ),
        sa.Column("batch_no", sa.String(length=64), nullable=False, comment="批次号"),
        sa.Column(
            "inspection_type",
            sa.String(length=32),
            nullable=False,
            comment="检验类型: 进料检验/过程检验/成品检验",
        ),
        sa.Column(
            "result",
            sa.String(length=16),
            nullable=False,
            comment="结果: 合格/不合格/待判定",
        ),
        sa.Column("inspector", sa.String(length=64), nullable=False, comment="检验员"),
        sa.Column("notes", sa.String(length=512), nullable=True, comment="备注"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_quality_records_inspection_no",
        "quality_records",
        ["inspection_no"],
        unique=True,
    )
    op.create_index(
        "ix_quality_records_batch_no", "quality_records", ["batch_no"], unique=False
    )
    op.create_index(
        "ix_quality_records_inspection_type",
        "quality_records",
        ["inspection_type"],
        unique=False,
    )
    op.create_index(
        "ix_quality_records_result", "quality_records", ["result"], unique=False
    )
    op.create_index(
        "idx_qr_type_result",
        "quality_records",
        ["inspection_type", "result"],
        unique=False,
    )

    # ------------------------------------------------------------------ quality_anomalies
    op.create_table(
        "quality_anomalies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "record_id",
            sa.String(length=64),
            sa.ForeignKey("quality_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "anomaly_type",
            sa.String(length=32),
            nullable=False,
            comment="异常类型: 尺寸偏差/表面缺陷/材料问题/其他",
        ),
        sa.Column("description", sa.String(length=512), nullable=True, comment="描述"),
        sa.Column("severity", sa.String(length=16), nullable=False, comment="严重程度"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="待处理",
            comment="状态: 待处理/处理中/已解决",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_quality_anomalies_record_id",
        "quality_anomalies",
        ["record_id"],
        unique=False,
    )
    op.create_index(
        "ix_quality_anomalies_anomaly_type",
        "quality_anomalies",
        ["anomaly_type"],
        unique=False,
    )
    op.create_index(
        "ix_quality_anomalies_status", "quality_anomalies", ["status"], unique=False
    )
    op.create_index(
        "idx_qa_type_status",
        "quality_anomalies",
        ["anomaly_type", "status"],
        unique=False,
    )

    # ------------------------------------------------------------------ production_records
    op.create_table(
        "production_records",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("date", sa.String(length=16), nullable=False, comment="日期 YYYY-MM-DD"),
        sa.Column("line_name", sa.String(length=32), nullable=False, comment="产线名称"),
        sa.Column("planned_qty", sa.Integer(), nullable=False, comment="计划产量"),
        sa.Column("actual_qty", sa.Integer(), nullable=False, comment="实际产量"),
        sa.Column("qualified_qty", sa.Integer(), nullable=False, comment="良品数"),
        sa.Column(
            "defect_qty", sa.Integer(), nullable=False, server_default="0", comment="不良数"
        ),
        sa.Column(
            "equipment_utilization", sa.Float(), nullable=False, comment="设备利用率%"
        ),
        sa.Column("energy_consumption", sa.Float(), nullable=False, comment="能耗 kWh"),
        sa.Column(
            "shift", sa.String(length=16), nullable=False, comment="班次: 早班/中班/晚班"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_production_records_date", "production_records", ["date"], unique=False
    )
    op.create_index(
        "ix_production_records_line_name",
        "production_records",
        ["line_name"],
        unique=False,
    )
    op.create_index(
        "idx_pr_date_line", "production_records", ["date", "line_name"], unique=False
    )

    # ------------------------------------------------------------------ work_orders
    op.create_table(
        "work_orders",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "order_no",
            sa.String(length=64),
            nullable=False,
            unique=True,
            comment="工单号",
        ),
        sa.Column("product_name", sa.String(length=128), nullable=False, comment="产品名称"),
        sa.Column("planned_qty", sa.Integer(), nullable=False, comment="计划数量"),
        sa.Column(
            "completed_qty",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="已完成数量",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="待开始",
            comment="状态: 进行中/已完成/待开始/已延期",
        ),
        sa.Column(
            "priority",
            sa.String(length=16),
            nullable=False,
            server_default="中",
            comment="优先级: 紧急/高/中/低",
        ),
        sa.Column("start_date", sa.String(length=16), nullable=True, comment="开始日期"),
        sa.Column("due_date", sa.String(length=16), nullable=True, comment="截止日期"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_work_orders_order_no", "work_orders", ["order_no"], unique=True
    )
    op.create_index("ix_work_orders_status", "work_orders", ["status"], unique=False)
    op.create_index(
        "idx_wo_status_priority", "work_orders", ["status", "priority"], unique=False
    )

    # ------------------------------------------------------------------ process_routes
    op.create_table(
        "process_routes",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, comment="工艺名称"),
        sa.Column("part_type", sa.String(length=64), nullable=False, comment="零件类型"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="草稿",
            comment="状态: 已发布/草稿/已归档",
        ),
        sa.Column(
            "steps_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="工序数",
        ),
        sa.Column("description", sa.String(length=512), nullable=True, comment="描述"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_process_routes_name", "process_routes", ["name"], unique=False
    )
    op.create_index(
        "ix_process_routes_part_type", "process_routes", ["part_type"], unique=False
    )
    op.create_index(
        "ix_process_routes_status", "process_routes", ["status"], unique=False
    )
    op.create_index("idx_prt_status", "process_routes", ["status"], unique=False)

    # ------------------------------------------------------------------ process_steps
    op.create_table(
        "process_steps",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "route_id",
            sa.String(length=64),
            sa.ForeignKey("process_routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False, comment="序号"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="工序名称"),
        sa.Column("work_center", sa.String(length=64), nullable=False, comment="工作中心"),
        sa.Column("hours", sa.Integer(), nullable=False, comment="工时(分钟)"),
        sa.Column("equipment", sa.String(length=128), nullable=True, comment="设备"),
        sa.Column("tooling", sa.String(length=128), nullable=True, comment="工装夹具"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_process_steps_route_id", "process_steps", ["route_id"], unique=False
    )
    op.create_index(
        "idx_pst_route_seq", "process_steps", ["route_id", "sequence"], unique=False
    )

    # ------------------------------------------------------------------ documents
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=256), nullable=False, comment="标题"),
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            comment="分类: 工艺规范/SOP标准/设备手册/质量标准/材料参数",
        ),
        sa.Column(
            "version",
            sa.String(length=16),
            nullable=False,
            server_default="v1.0",
            comment="版本",
        ),
        sa.Column("author", sa.String(length=64), nullable=False, comment="作者"),
        sa.Column("content", sa.String(length=4096), nullable=True, comment="内容/描述"),
        sa.Column("tags", sa.JSON(), nullable=True, comment="标签 JSON数组"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="待审核",
            comment="状态: 已发布/待审核",
        ),
        sa.Column(
            "view_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="浏览量",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_documents_title", "documents", ["title"], unique=False)
    op.create_index("ix_documents_category", "documents", ["category"], unique=False)
    op.create_index("ix_documents_status", "documents", ["status"], unique=False)
    op.create_index(
        "idx_doc_category_status", "documents", ["category", "status"], unique=False
    )

    # ------------------------------------------------------------------ tools
    op.create_table(
        "tools",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "code",
            sa.String(length=32),
            nullable=False,
            unique=True,
            comment="刀具编码 (T01, T02, ...)",
        ),
        sa.Column("name", sa.String(length=128), nullable=False, comment="刀具名称"),
        sa.Column(
            "type",
            sa.String(length=32),
            nullable=False,
            comment="刀具类型: end_mill/ball_mill/drill/reamer/tap/insert/grooving/threading",
        ),
        sa.Column("diameter", sa.Float(), nullable=False, comment="刀具直径 (mm)"),
        sa.Column("length", sa.Float(), nullable=True, comment="刀具长度 (mm)"),
        sa.Column(
            "flute_count", sa.Integer(), nullable=True, server_default="2", comment="刃数"
        ),
        sa.Column(
            "material",
            sa.String(length=32),
            nullable=True,
            comment="刀具材料: carbide/hss/ceramic/cbn/diamond",
        ),
        sa.Column(
            "coating",
            sa.String(length=32),
            nullable=True,
            comment="涂层类型: TiN/TiAlN/AlCrN/DLC/None",
        ),
        sa.Column("max_rpm", sa.Float(), nullable=True, comment="最大允许转速 (RPM)"),
        sa.Column("max_feed", sa.Float(), nullable=True, comment="最大允许进给 (mm/min)"),
        sa.Column(
            "usage_time",
            sa.Float(),
            nullable=False,
            server_default="0.0",
            comment="累计使用时间 (分钟)",
        ),
        sa.Column(
            "wear_amount",
            sa.Float(),
            nullable=False,
            server_default="0.0",
            comment="磨损量 (mm)",
        ),
        sa.Column("last_sharpened", sa.DateTime(), nullable=True, comment="上次刃磨时间"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
            comment="刀具状态: active/worn/broken/maintenance",
        ),
        sa.Column("vendor", sa.String(length=128), nullable=True, comment="供应商"),
        sa.Column("cost", sa.Float(), nullable=True, comment="采购成本"),
        sa.Column("notes", sa.Text(), nullable=True, comment="备注"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="更新时间",
        ),
    )
    op.create_index("ix_tools_code", "tools", ["code"], unique=True)
    op.create_index("ix_tools_type", "tools", ["type"], unique=False)
    op.create_index("ix_tools_status", "tools", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema: 反向删除 12 张表。"""

    # tools
    op.drop_index("ix_tools_status", table_name="tools")
    op.drop_index("ix_tools_type", table_name="tools")
    op.drop_index("ix_tools_code", table_name="tools")
    op.drop_table("tools")

    # documents
    op.drop_index("idx_doc_category_status", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_category", table_name="documents")
    op.drop_index("ix_documents_title", table_name="documents")
    op.drop_table("documents")

    # process_steps
    op.drop_index("idx_pst_route_seq", table_name="process_steps")
    op.drop_index("ix_process_steps_route_id", table_name="process_steps")
    op.drop_table("process_steps")

    # process_routes
    op.drop_index("idx_prt_status", table_name="process_routes")
    op.drop_index("ix_process_routes_status", table_name="process_routes")
    op.drop_index("ix_process_routes_part_type", table_name="process_routes")
    op.drop_index("ix_process_routes_name", table_name="process_routes")
    op.drop_table("process_routes")

    # work_orders
    op.drop_index("idx_wo_status_priority", table_name="work_orders")
    op.drop_index("ix_work_orders_status", table_name="work_orders")
    op.drop_index("ix_work_orders_order_no", table_name="work_orders")
    op.drop_table("work_orders")

    # production_records
    op.drop_index("idx_pr_date_line", table_name="production_records")
    op.drop_index("ix_production_records_line_name", table_name="production_records")
    op.drop_index("ix_production_records_date", table_name="production_records")
    op.drop_table("production_records")

    # quality_anomalies
    op.drop_index("idx_qa_type_status", table_name="quality_anomalies")
    op.drop_index("ix_quality_anomalies_status", table_name="quality_anomalies")
    op.drop_index("ix_quality_anomalies_anomaly_type", table_name="quality_anomalies")
    op.drop_index("ix_quality_anomalies_record_id", table_name="quality_anomalies")
    op.drop_table("quality_anomalies")

    # quality_records
    op.drop_index("idx_qr_type_result", table_name="quality_records")
    op.drop_index("ix_quality_records_result", table_name="quality_records")
    op.drop_index("ix_quality_records_inspection_type", table_name="quality_records")
    op.drop_index("ix_quality_records_batch_no", table_name="quality_records")
    op.drop_index("ix_quality_records_inspection_no", table_name="quality_records")
    op.drop_table("quality_records")

    # maintenance_plans
    op.drop_index("idx_maintenance_status", table_name="maintenance_plans")
    op.drop_index("idx_maintenance_equipment", table_name="maintenance_plans")
    op.drop_index("ix_maintenance_plans_status", table_name="maintenance_plans")
    op.drop_index("ix_maintenance_plans_equipment_id", table_name="maintenance_plans")
    op.drop_table("maintenance_plans")

    # equipment_alarms
    op.drop_index("idx_alarm_severity", table_name="equipment_alarms")
    op.drop_index("idx_alarm_equipment_status", table_name="equipment_alarms")
    op.drop_index("ix_equipment_alarms_status", table_name="equipment_alarms")
    op.drop_index("ix_equipment_alarms_equipment_id", table_name="equipment_alarms")
    op.drop_table("equipment_alarms")

    # equipment
    op.drop_index("idx_equipment_status", table_name="equipment")
    op.drop_table("equipment")

    # materials
    op.drop_index("idx_materials_category_status", table_name="materials")
    op.drop_index("ix_materials_status", table_name="materials")
    op.drop_index("ix_materials_category", table_name="materials")
    op.drop_index("ix_materials_code", table_name="materials")
    op.drop_table("materials")
