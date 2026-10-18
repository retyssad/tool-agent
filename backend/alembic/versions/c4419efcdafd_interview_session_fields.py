"""interview session fields

Revision ID: c4419efcdafd
Revises: 967babb577a1
Create Date: 2026-10-17 18:11:27.753529

面试表此前为空，直接重建以获得干净的非空约束与外键。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4419efcdafd"
down_revision: Union[str, None] = "967babb577a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("interview_turns")
    op.drop_table("interview_sessions")

    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_kind", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("resume_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resume_version_id",
            sa.Integer(),
            sa.ForeignKey("resume_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "imported_resume_id",
            sa.Integer(),
            sa.ForeignKey("imported_resumes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("pact", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("report", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("round_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resume_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("job_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("plan_run_id", sa.Integer(), nullable=True),
        sa.Column("report_run_id", sa.Integer(), nullable=True),
        sa.Column("plan_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interview_sessions_job_id", "interview_sessions", ["job_id"])
    op.create_index("ix_interview_sessions_status", "interview_sessions", ["status"])

    op.create_table(
        "interview_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="primary"),
        sa.Column("stage", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("basis", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("scores", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("asked_by_run", sa.Integer(), nullable=True),
        sa.Column("score_run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interview_turns_session_id", "interview_turns", ["session_id"])


def downgrade() -> None:
    op.drop_table("interview_turns")
    op.drop_table("interview_sessions")
