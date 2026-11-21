"""admin panel: tariflar, promokodlar, e'lonlar, sozlamalar, adminlar, audit, bloklash

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21 12:00:00
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = datetime.now(timezone.utc)


def upgrade() -> None:
    op.add_column('users', sa.Column('banned_at', TS, nullable=True))
    op.add_column('users', sa.Column('ban_reason', sa.String(length=200), nullable=True))

    op.create_table(
        'pricing_plans',
        sa.Column('key', sa.String(length=16), nullable=False),
        sa.Column('title', sa.String(length=64), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('price', sa.Integer(), nullable=False),
        sa.Column('original_price', sa.Integer(), nullable=True),
        sa.Column('tag', sa.String(length=64), nullable=True),
        sa.Column('emoji', sa.String(length=8), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('updated_at', TS, nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_pricing_plans')),
    )
    plans = sa.table(
        'pricing_plans',
        sa.column('key', sa.String), sa.column('title', sa.String), sa.column('days', sa.Integer),
        sa.column('price', sa.Integer), sa.column('tag', sa.String), sa.column('emoji', sa.String),
        sa.column('sort_order', sa.Integer), sa.column('is_active', sa.Boolean), sa.column('updated_at', TS),
    )
    op.bulk_insert(plans, [
        {'key': '1m', 'title': '1 oy', 'days': 30, 'price': 39900, 'tag': None, 'emoji': '✅', 'sort_order': 1, 'is_active': True, 'updated_at': NOW},
        {'key': '3m', 'title': '3 oy', 'days': 90, 'price': 79900, 'tag': '33% tejaysiz', 'emoji': '⭐', 'sort_order': 2, 'is_active': True, 'updated_at': NOW},
        {'key': '12m', 'title': '12 oy', 'days': 365, 'price': 179900, 'tag': None, 'emoji': '💎', 'sort_order': 3, 'is_active': True, 'updated_at': NOW},
    ])

    op.create_table(
        'promocodes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('discount_percent', sa.Integer(), nullable=False),
        sa.Column('plan_keys', sa.String(length=160), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=False),
        sa.Column('used_count', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('starts_at', TS, nullable=True),
        sa.Column('expires_at', TS, nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_promocodes')),
        sa.UniqueConstraint('code', name=op.f('uq_promocodes_code')),
    )

    op.add_column('payment_orders', sa.Column('promo_id', sa.Integer(), nullable=True))
    op.add_column('payment_orders', sa.Column('original_amount', sa.BigInteger(), nullable=True))
    op.add_column('payment_orders', sa.Column('bonus_days', sa.Integer(), server_default='0', nullable=False))
    op.create_foreign_key(op.f('fk_payment_orders_promo_id_promocodes'), 'payment_orders', 'promocodes', ['promo_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'promo_redemptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('promo_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('identity_ref', sa.String(length=80), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['payment_orders.id'], name=op.f('fk_promo_redemptions_order_id_payment_orders'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['promo_id'], ['promocodes.id'], name=op.f('fk_promo_redemptions_promo_id_promocodes'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_promo_redemptions_user_id_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_promo_redemptions')),
        sa.UniqueConstraint('promo_id', 'identity_ref', name='uq_promo_redemption_identity'),
    )
    op.create_index(op.f('ix_promo_redemptions_promo_id'), 'promo_redemptions', ['promo_id'], unique=False)
    op.create_index(op.f('ix_promo_redemptions_user_id'), 'promo_redemptions', ['user_id'], unique=False)

    op.create_table(
        'announcements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('button_label', sa.String(length=40), nullable=True),
        sa.Column('button_url', sa.String(length=500), nullable=True),
        sa.Column('audience', sa.String(length=12), nullable=False),
        sa.Column('target_user_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('starts_at', TS, nullable=True),
        sa.Column('ends_at', TS, nullable=True),
        sa.Column('views', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name=op.f('fk_announcements_target_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_announcements')),
    )
    op.create_index(op.f('ix_announcements_target_user_id'), 'announcements', ['target_user_id'], unique=False)

    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('updated_at', TS, nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_app_settings')),
    )

    op.create_table(
        'admin_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=40), nullable=False),
        sa.Column('full_name', sa.String(length=80), nullable=True),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=12), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('totp_secret', sa.Text(), nullable=True),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.Column('failed_logins', sa.Integer(), nullable=False),
        sa.Column('locked_until', TS, nullable=True),
        sa.Column('last_login_at', TS, nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_users')),
        sa.UniqueConstraint('username', name=op.f('uq_admin_users_username')),
    )
    op.create_table(
        'admin_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('csrf_token', sa.String(length=64), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=200), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('last_seen_at', TS, nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('revoked_at', TS, nullable=True),
        sa.ForeignKeyConstraint(['admin_id'], ['admin_users.id'], name=op.f('fk_admin_sessions_admin_id_admin_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_sessions')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_admin_sessions_token_hash')),
    )
    op.create_index(op.f('ix_admin_sessions_admin_id'), 'admin_sessions', ['admin_id'], unique=False)
    op.create_table(
        'admin_audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=True),
        sa.Column('admin_username', sa.String(length=40), nullable=True),
        sa.Column('action', sa.String(length=48), nullable=False),
        sa.Column('target_type', sa.String(length=24), nullable=True),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('summary', sa.String(length=500), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['admin_users.id'], name=op.f('fk_admin_audit_admin_id_admin_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_audit')),
    )
    op.create_index('ix_admin_audit_created', 'admin_audit', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_admin_audit_created', table_name='admin_audit')
    op.drop_table('admin_audit')
    op.drop_index(op.f('ix_admin_sessions_admin_id'), table_name='admin_sessions')
    op.drop_table('admin_sessions')
    op.drop_table('admin_users')
    op.drop_table('app_settings')
    op.drop_index(op.f('ix_announcements_target_user_id'), table_name='announcements')
    op.drop_table('announcements')
    op.drop_index(op.f('ix_promo_redemptions_user_id'), table_name='promo_redemptions')
    op.drop_index(op.f('ix_promo_redemptions_promo_id'), table_name='promo_redemptions')
    op.drop_table('promo_redemptions')
    op.drop_constraint(op.f('fk_payment_orders_promo_id_promocodes'), 'payment_orders', type_='foreignkey')
    op.drop_column('payment_orders', 'bonus_days')
    op.drop_column('payment_orders', 'original_amount')
    op.drop_column('payment_orders', 'promo_id')
    op.drop_table('promocodes')
    op.drop_table('pricing_plans')
    op.drop_column('users', 'ban_reason')
    op.drop_column('users', 'banned_at')
