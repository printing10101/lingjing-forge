"""Add cutting_experiences table (P2-1 数据飞轮)

Revision ID: 001
Revises: 
Create Date: 2026-08-22 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create cutting_experiences table for data flywheel."""
    op.create_table(
        'cutting_experiences',
        sa.Column('id', sa.String(64), primary_key=True, nullable=False, index=True),
        sa.Column('job_id', sa.String(64), nullable=True),
        sa.Column('machine_id', sa.String(64), nullable=False, index=True),
        sa.Column('program_number', sa.String(32), nullable=False),
        sa.Column('tool_id', sa.String(64), nullable=False, index=True),
        sa.Column('material', sa.String(64), nullable=False),
        sa.Column('machining_type', sa.String(32), nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('cycle_time_s', sa.Float(), nullable=True),
        sa.Column('surface_roughness_ra', sa.Float(), nullable=True),
        sa.Column('tool_wear_percent', sa.Float(), nullable=True),
        sa.Column('dimensional_error_mm', sa.Float(), nullable=True),
        sa.Column('anomaly_count', sa.Integer(), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('results_extra', sa.JSON(), nullable=False),
        sa.Column('anomalies', sa.JSON(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('operator', sa.String(64), nullable=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    
    # Performance indexes for query optimization
    op.create_index('ix_cutting_experiences_material', 'cutting_experiences', ['material'])
    op.create_index('ix_cutting_experiences_created_at', 'cutting_experiences', ['created_at'])
    op.create_index('ix_cutting_experiences_machine_created', 'cutting_experiences', ['machine_id', 'created_at'])


def downgrade() -> None:
    """Drop cutting_experiences table and indexes."""
    op.drop_index('ix_cutting_experiences_machine_created', table_name='cutting_experiences')
    op.drop_index('ix_cutting_experiences_material', table_name='cutting_experiences')
    op.drop_index('ix_cutting_experiences_created_at', table_name='cutting_experiences')
    op.drop_table('cutting_experiences')
