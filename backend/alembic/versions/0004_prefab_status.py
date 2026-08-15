"""add prefab_status to scenarios

Revision ID: 0004_prefab_status
Revises: 0003_scenario_published
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision      = '0004_prefab_status'
down_revision = '0003_scenario_published'
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column('scenarios', sa.Column('prefab_status', sa.String(), nullable=False, server_default='pending'))
    # Backfill: scenarios that already have a prefab are ready; published scenarios
    # without one genuinely failed (or never ran) under the old silent-failure behavior.
    op.execute("UPDATE scenarios SET prefab_status = 'ready' WHERE prefab_engine_state IS NOT NULL")
    op.execute("UPDATE scenarios SET prefab_status = 'failed' WHERE prefab_engine_state IS NULL AND is_published = true")


def downgrade():
    op.drop_column('scenarios', 'prefab_status')
