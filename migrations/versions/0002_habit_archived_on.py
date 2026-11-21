"""habits.archived_on — intizom balida arxivlangan odatning o'tgan kunlari

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('habits', sa.Column('archived_on', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('habits', 'archived_on')
