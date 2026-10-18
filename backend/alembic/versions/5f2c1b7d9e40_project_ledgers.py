"""项目账本：ClaimLedger 增加 kind 与 project_id

Revision ID: 5f2c1b7d9e40
Revises: ccd6c5a00ba7
Create Date: 2026-10-17 21:20:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5f2c1b7d9e40'
down_revision: Union[str, None] = 'ccd6c5a00ba7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('claim_ledgers', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('kind', sa.String(length=20), nullable=False, server_default='personal')
        )
        batch_op.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_claim_ledgers_kind'), ['kind'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_claim_ledgers_project_id'), ['project_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_claim_ledgers_project_id',
            'projects',
            ['project_id'],
            ['id'],
            ondelete='SET NULL',
        )

    # 已有项目补建账本，避免老数据没有对应项目账本
    op.execute(
        """
        INSERT INTO claim_ledgers (name, kind, project_id, created_at, updated_at)
        SELECT p.name, 'project', p.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM projects p
        WHERE NOT EXISTS (
            SELECT 1 FROM claim_ledgers l WHERE l.project_id = p.id AND l.kind = 'project'
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('claim_ledgers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_claim_ledgers_project_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_claim_ledgers_project_id'))
        batch_op.drop_index(batch_op.f('ix_claim_ledgers_kind'))
        batch_op.drop_column('project_id')
        batch_op.drop_column('kind')
