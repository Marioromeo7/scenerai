"""store image_prompt/narration_text/voice on turn_media for regenerate reuse

Revision ID: 0010_turn_media_context
Revises: 0009_user_is_admin
Create Date: 2026-08-15
"""
from alembic import op
import sqlalchemy as sa

revision      = '0010_turn_media_context'
down_revision = '0009_user_is_admin'
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column('turn_media', sa.Column('image_prompt', sa.Text(), nullable=True))
    op.add_column('turn_media', sa.Column('narration_text', sa.Text(), nullable=True))
    op.add_column('turn_media', sa.Column('voice_id', sa.String(), nullable=True))
    op.add_column('turn_media', sa.Column('voice_speed', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('turn_media', 'voice_speed')
    op.drop_column('turn_media', 'voice_id')
    op.drop_column('turn_media', 'narration_text')
    op.drop_column('turn_media', 'image_prompt')
