"""add composite indexes for scale

Revision ID: 0001_composite_indexes
Revises:
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision      = '0001_composite_indexes'
down_revision = '0000_initial_schema'
branch_labels = None
depends_on    = None


def upgrade():
    # Public scenario browse — WHERE is_public = TRUE ORDER BY created_at DESC
    op.create_index('ix_scenarios_public_created',  'scenarios',    ['is_public',  'created_at'])
    # Creator scenario list with cursor — WHERE creator_id = ? AND created_at < ?
    op.create_index('ix_scenarios_creator_created', 'scenarios',    ['creator_id', 'created_at'])
    # Session history cursor — WHERE user_id = ? AND started_at < ?
    op.create_index('ix_session_logs_user_started', 'session_logs', ['user_id',    'started_at'])
    # Last-session lookup on every session start — WHERE user_id = ? AND scenario_id = ?
    op.create_index('ix_session_logs_user_scenario_started', 'session_logs',
                    ['user_id', 'scenario_id', 'started_at'])


def downgrade():
    op.drop_index('ix_session_logs_user_scenario_started', table_name='session_logs')
    op.drop_index('ix_session_logs_user_started',          table_name='session_logs')
    op.drop_index('ix_scenarios_creator_created',          table_name='scenarios')
    op.drop_index('ix_scenarios_public_created',           table_name='scenarios')
