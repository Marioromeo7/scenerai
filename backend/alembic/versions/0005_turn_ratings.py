"""add turn_ratings table

Revision ID: 0005_turn_ratings
Revises: 0004_prefab_status
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision      = '0005_turn_ratings'
down_revision = '0004_prefab_status'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'turn_ratings',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('scenario_id', sa.String(), sa.ForeignKey('scenarios.id', ondelete='SET NULL'), nullable=True),
        sa.Column('turn', sa.Integer(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'session_id', 'turn', name='uq_turn_ratings_user_session_turn'),
    )
    op.create_index('ix_turn_ratings_user_id', 'turn_ratings', ['user_id'])
    op.create_index('ix_turn_ratings_session_id', 'turn_ratings', ['session_id'])
    op.create_index('ix_turn_ratings_scenario_id', 'turn_ratings', ['scenario_id'])


def downgrade():
    op.drop_index('ix_turn_ratings_scenario_id', table_name='turn_ratings')
    op.drop_index('ix_turn_ratings_session_id', table_name='turn_ratings')
    op.drop_index('ix_turn_ratings_user_id', table_name='turn_ratings')
    op.drop_table('turn_ratings')
