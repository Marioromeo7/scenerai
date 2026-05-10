"""add prefab_engine_state to scenarios

Revision ID: 0002_scenario_prefab
Revises: 0001_composite_indexes
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision      = '0002_scenario_prefab'
down_revision = '0001_composite_indexes'
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column('scenarios', sa.Column('prefab_engine_state', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('scenarios', 'prefab_engine_state')
