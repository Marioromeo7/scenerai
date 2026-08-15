"""add turn_media table

Revision ID: 0007_turn_media
Revises: 0006_visual_profile
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa

revision      = '0007_turn_media'
down_revision = '0006_visual_profile'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'turn_media',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('scenario_id', sa.String(), sa.ForeignKey('scenarios.id', ondelete='SET NULL'), nullable=True),
        sa.Column('turn', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), server_default='pending'),
        sa.Column('image_url', sa.String(), nullable=True),
        sa.Column('image_seed', sa.Integer(), nullable=True),
        sa.Column('audio_url', sa.String(), nullable=True),
        sa.Column('video_segment_url', sa.String(), nullable=True),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('session_id', 'turn', name='uq_turn_media_session_turn'),
    )
    op.create_index('ix_turn_media_user_id', 'turn_media', ['user_id'])
    op.create_index('ix_turn_media_session_id', 'turn_media', ['session_id'])
    op.create_index('ix_turn_media_scenario_id', 'turn_media', ['scenario_id'])


def downgrade():
    op.drop_index('ix_turn_media_scenario_id', table_name='turn_media')
    op.drop_index('ix_turn_media_session_id', table_name='turn_media')
    op.drop_index('ix_turn_media_user_id', table_name='turn_media')
    op.drop_table('turn_media')
