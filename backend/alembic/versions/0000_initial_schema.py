"""create initial schema

Revision ID: 0000_initial_schema
Revises:
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa

revision      = '0000_initial_schema'
down_revision = None
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id',              sa.String(),  primary_key=True),
        sa.Column('email',           sa.String(),  nullable=False),
        sa.Column('hashed_password', sa.String(),  nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.create_table(
        'personas',
        sa.Column('id',         sa.String(), primary_key=True),
        sa.Column('user_id',    sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name',       sa.String(), nullable=False),
        sa.Column('pronouns',   sa.String(), nullable=False, server_default='she/her'),
        sa.Column('brief',      sa.Text(),   server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_personas_user_id', 'personas', ['user_id'])

    op.create_table(
        'scenarios',
        sa.Column('id',               sa.String(),  primary_key=True),
        sa.Column('creator_id',       sa.String(),  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('char_name',        sa.String(),  nullable=False),
        sa.Column('char_pronouns',    sa.String(),  nullable=False, server_default='he/him'),
        sa.Column('char_title',       sa.String(),  nullable=False),
        sa.Column('char_personality', sa.Text(),    nullable=False),
        sa.Column('greeting',         sa.Text(),    nullable=False),
        sa.Column('title',            sa.String(),  server_default=''),
        sa.Column('brief',            sa.Text(),    server_default=''),
        sa.Column('tags',             sa.JSON(),    server_default='[]'),
        sa.Column('intensity',        sa.Integer(), server_default='3'),
        sa.Column('image_seed',       sa.String(),  server_default=''),
        sa.Column('saves_count',      sa.Integer(), server_default='0'),
        sa.Column('plays_count',      sa.Integer(), server_default='0'),
        sa.Column('is_public',        sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_scenarios_creator_id', 'scenarios', ['creator_id'])

    op.create_table(
        'scenario_saves',
        sa.Column('id',          sa.String(), primary_key=True),
        sa.Column('user_id',     sa.String(), sa.ForeignKey('users.id',     ondelete='CASCADE'), nullable=False),
        sa.Column('scenario_id', sa.String(), sa.ForeignKey('scenarios.id', ondelete='CASCADE'), nullable=False),
        sa.Column('saved_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'scenario_id'),
    )
    op.create_index('ix_scenario_saves_user_id',     'scenario_saves', ['user_id'])
    op.create_index('ix_scenario_saves_scenario_id', 'scenario_saves', ['scenario_id'])

    op.create_table(
        'session_logs',
        sa.Column('id',           sa.String(),  primary_key=True),
        sa.Column('session_id',   sa.String(),  nullable=False),
        sa.Column('user_id',      sa.String(),  sa.ForeignKey('users.id',     ondelete='CASCADE'), nullable=False),
        sa.Column('scenario_id',  sa.String(),  sa.ForeignKey('scenarios.id', ondelete='SET NULL'), nullable=True),
        sa.Column('persona_id',   sa.String(),  sa.ForeignKey('personas.id',  ondelete='SET NULL'), nullable=True),
        sa.Column('history',      sa.JSON(),    server_default='[]'),
        sa.Column('turns_count',  sa.Integer(), server_default='0'),
        sa.Column('started_at',   sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('ended_at',     sa.DateTime(timezone=True), nullable=True),
        sa.Column('engine_state', sa.JSON(),    nullable=True),
    )
    op.create_index('ix_session_logs_session_id',  'session_logs', ['session_id'], unique=True)
    op.create_index('ix_session_logs_user_id',     'session_logs', ['user_id'])
    op.create_index('ix_session_logs_scenario_id', 'session_logs', ['scenario_id'])


def downgrade():
    op.drop_table('session_logs')
    op.drop_table('scenario_saves')
    op.drop_table('scenarios')
    op.drop_table('personas')
    op.drop_table('users')
