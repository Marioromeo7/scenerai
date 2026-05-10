"""add is_published to scenarios

Revision ID: 0003_scenario_published
Revises: 0002_scenario_prefab
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision      = '0003_scenario_published'
down_revision = '0002_scenario_prefab'
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column('scenarios', sa.Column('is_published', sa.Boolean(), nullable=False, server_default='false'))


def downgrade():
    op.drop_column('scenarios', 'is_published')
