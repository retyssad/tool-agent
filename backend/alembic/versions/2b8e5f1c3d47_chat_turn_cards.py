"""chat turns store cards

Revision ID: 2b8e5f1c3d47
Revises: 1a7c4d9e2f10
Create Date: 2026-10-17 23:50:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2b8e5f1c3d47'
down_revision: Union[str, None] = '1a7c4d9e2f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('chat_turns', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('cards', sa.JSON(), nullable=False, server_default='[]')
        )


def downgrade() -> None:
    with op.batch_alter_table('chat_turns', schema=None) as batch_op:
        batch_op.drop_column('cards')
