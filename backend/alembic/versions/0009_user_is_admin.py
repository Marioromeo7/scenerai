"""add is_admin to users, mark the operator account

Revision ID: 0009_user_is_admin
Revises: 0008_mock_billing
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa

revision      = '0009_user_is_admin'
down_revision = '0008_mock_billing'
branch_labels = None
depends_on    = None

_ADMIN_EMAIL = 'metrymarioromeo546@gmail.com'


def upgrade():
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(
        sa.text("UPDATE users SET is_admin = true WHERE email = :email").bindparams(email=_ADMIN_EMAIL)
    )


def downgrade():
    op.drop_column('users', 'is_admin')
