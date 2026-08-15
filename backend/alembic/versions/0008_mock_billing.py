"""add subscription_tiers, user_subscriptions (mock billing scaffolding)

No payment processing lives in this schema at all -- is_mock defaults to
True on every row, stripe_subscription_id stays null until a real
processor is actually wired in. Seeds three default tiers as data so
pricing can be edited later without a code deploy.

Revision ID: 0008_mock_billing
Revises: 0007_turn_media
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa
import uuid

revision      = '0008_mock_billing'
down_revision = '0007_turn_media'
branch_labels = None
depends_on    = None

_TIERS_TABLE = sa.table(
    'subscription_tiers',
    sa.column('id', sa.String), sa.column('name', sa.String),
    sa.column('price_cents', sa.Integer), sa.column('currency', sa.String),
    sa.column('features', sa.JSON), sa.column('turns_per_day', sa.Integer),
    sa.column('sort_order', sa.Integer),
)


def upgrade():
    op.create_table(
        'subscription_tiers',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('price_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(), server_default='usd'),
        sa.Column('features', sa.JSON()),
        sa.Column('turns_per_day', sa.Integer(), nullable=True),
        sa.Column('sort_order', sa.Integer(), server_default='0'),
    )
    op.create_table(
        'user_subscriptions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('tier_id', sa.String(), sa.ForeignKey('subscription_tiers.id'), nullable=False),
        sa.Column('status', sa.String(), server_default='active'),
        sa.Column('is_mock', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('stripe_subscription_id', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_user_subscriptions_user_id', 'user_subscriptions', ['user_id'])

    op.bulk_insert(_TIERS_TABLE, [
        {'id': str(uuid.uuid4()), 'name': 'Free', 'price_cents': 0, 'currency': 'usd',
         'features': ['Standard response speed', 'Shared generation queue'],
         'turns_per_day': 50, 'sort_order': 0},
        {'id': str(uuid.uuid4()), 'name': 'Premium', 'price_cents': 999, 'currency': 'usd',
         'features': ['Priority response speed', 'Priority generation queue', 'Higher daily turn limit'],
         'turns_per_day': 500, 'sort_order': 1},
        {'id': str(uuid.uuid4()), 'name': 'Pro', 'price_cents': 1999, 'currency': 'usd',
         'features': ['Fastest response speed', 'Top-of-queue generation', 'Unlimited daily turns'],
         'turns_per_day': None, 'sort_order': 2},
    ])


def downgrade():
    op.drop_index('ix_user_subscriptions_user_id', table_name='user_subscriptions')
    op.drop_table('user_subscriptions')
    op.drop_table('subscription_tiers')
