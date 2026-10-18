"""chat turns as runs

Revision ID: 1a7c4d9e2f10
Revises: 5f2c1b7d9e40
Create Date: 2026-10-17 23:10:00.000000

旧的三张 chat 表（sessions/messages/actions）语义已废弃：对话轮次改为真实
Run 执行，条目 append-only。这里直接删表重建，不做数据搬迁。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1a7c4d9e2f10'
down_revision: Union[str, None] = '5f2c1b7d9e40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_run_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_runs_parent_run_id'), ['parent_run_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_runs_parent_run_id', 'runs', ['parent_run_id'], ['id'], ondelete='SET NULL'
        )

    op.drop_table('chat_actions')
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')

    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'chat_turns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('user_content', sa.Text(), nullable=False),
        sa.Column('assistant_content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('run_ids', sa.JSON(), nullable=False),
        sa.Column('current_run_id', sa.Integer(), nullable=True),
        sa.Column('plan', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'seq', name='uq_chat_turn_seq'),
    )
    with op.batch_alter_table('chat_turns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_turns_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_turns_status'), ['status'], unique=False)

    op.create_table(
        'chat_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=False),
        sa.Column('compacted_into_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['turn_id'], ['chat_turns.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'seq', name='uq_chat_entry_seq'),
    )
    with op.batch_alter_table('chat_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_entries_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_entries_turn_id'), ['turn_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_entries_seq'), ['seq'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_chat_entries_compacted_into_id'), ['compacted_into_id'], unique=False
        )

    op.create_table(
        'chat_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('tool_call_id', sa.String(length=120), nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=True),
        sa.Column('tool', sa.String(length=80), nullable=False),
        sa.Column('args', sa.JSON(), nullable=False),
        sa.Column('preview', sa.Text(), nullable=False),
        sa.Column('risk', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['turn_id'], ['chat_turns.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('chat_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_actions_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_actions_turn_id'), ['turn_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_actions_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_actions_entry_id'), ['entry_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_actions_status'), ['status'], unique=False)

    op.create_table(
        'chat_compactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('from_seq', sa.Integer(), nullable=False),
        sa.Column('to_seq', sa.Integer(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('tokens_before', sa.Integer(), nullable=False),
        sa.Column('tokens_after', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('chat_compactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_compactions_session_id'), ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_table('chat_compactions')
    op.drop_table('chat_actions')
    op.drop_table('chat_entries')
    op.drop_table('chat_turns')
    op.drop_table('chat_sessions')

    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_runs_parent_run_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_runs_parent_run_id'))
        batch_op.drop_column('parent_run_id')
