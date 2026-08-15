"""add visual_profile, visual_seed, visual_status, image_url, video_url to scenarios

Revision ID: 0006_visual_profile
Revises: 0005_turn_ratings
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision      = '0006_visual_profile'
down_revision = '0005_turn_ratings'
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column('scenarios', sa.Column('visual_profile', sa.JSON(), nullable=True))
    op.add_column('scenarios', sa.Column('visual_seed', sa.Integer(), nullable=True))
    op.add_column('scenarios', sa.Column('visual_status', sa.String(), nullable=False, server_default='pending'))
    op.add_column('scenarios', sa.Column('image_url', sa.String(), nullable=True))
    op.add_column('scenarios', sa.Column('video_url', sa.String(), nullable=True))


def downgrade():
    op.drop_column('scenarios', 'video_url')
    op.drop_column('scenarios', 'image_url')
    op.drop_column('scenarios', 'visual_status')
    op.drop_column('scenarios', 'visual_seed')
    op.drop_column('scenarios', 'visual_profile')
