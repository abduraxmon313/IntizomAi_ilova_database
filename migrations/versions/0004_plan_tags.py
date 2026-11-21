"""tariflar: 3 oy ostida "33% tejaysiz", 12 oy ostida belgi yo'q (oylik narx ko'rinadi)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21 16:00:00
"""
from alembic import op


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE pricing_plans SET tag = '33% tejaysiz' WHERE key = '3m' AND (tag IS NULL OR tag = '')")
    op.execute("UPDATE pricing_plans SET tag = NULL WHERE key = '12m' AND tag LIKE '%tejaysiz%'")


def downgrade() -> None:
    pass
