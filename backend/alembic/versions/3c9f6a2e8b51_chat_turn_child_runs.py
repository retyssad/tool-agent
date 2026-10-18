"""chat turn child runs

Revision ID: 3c9f6a2e8b51
Revises: 2b8e5f1c3d47
Create Date: 2026-10-18 00:05:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3c9f6a2e8b51'
down_revision: Union[str, None] = '2b8e5f1c3d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('chat_turns', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('child_run_ids', sa.JSON(), nullable=False, server_default='[]')
        )


def downgrade() -> None:
    with op.batch_alter_table('chat_turns', schema=None) as batch_op:
        batch_op.drop_column('child_run_ids')
