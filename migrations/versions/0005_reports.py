"""shikoyatlar (reports) jadvali — App Store Guideline 1.2

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25 12:00:00
"""
import sqlalchemy as sa
from alembic import op


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reporter_user_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(length=8), nullable=False),
        sa.Column('target_user_id', sa.Integer(), nullable=True),
        sa.Column('target_group_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=16), nullable=False),
        sa.Column('details', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('resolved_at', TS, nullable=True),
        sa.Column('resolution_note', sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(['reporter_user_id'], ['users.id'], name=op.f('fk_reports_reporter_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name=op.f('fk_reports_target_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_group_id'], ['groups.id'], name=op.f('fk_reports_target_group_id_groups'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
    )
    op.create_index(op.f('ix_reports_reporter_user_id'), 'reports', ['reporter_user_id'], unique=False)
    op.create_index(op.f('ix_reports_target_user_id'), 'reports', ['target_user_id'], unique=False)
    op.create_index(op.f('ix_reports_target_group_id'), 'reports', ['target_group_id'], unique=False)
    op.create_index('ix_reports_status_created', 'reports', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reports_status_created', table_name='reports')
    op.drop_index(op.f('ix_reports_target_group_id'), table_name='reports')
    op.drop_index(op.f('ix_reports_target_user_id'), table_name='reports')
    op.drop_index(op.f('ix_reports_reporter_user_id'), table_name='reports')
    op.drop_table('reports')
