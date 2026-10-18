"""面试会话与轮次。"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin


class InterviewSession(Base, TimestampMixin):
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    # 简历来源：draft（生成简历草稿）/ imported（导入简历）
    resume_kind: Mapped[str] = mapped_column(String(20), default="draft")
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_drafts.id", ondelete="SET NULL"), nullable=True
    )
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True
    )
    imported_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("imported_resumes.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    # 面试契约：阶段、问题计划、评分维度、上下文快照
    pact: Mapped[dict] = mapped_column(JSON, default=dict)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    round_index: Mapped[int] = mapped_column(Integer, default=0)
    plan_total: Mapped[int] = mapped_column(Integer, default=0)
    # 面试上下文快照，避免简历/岗位后续修改导致口径漂移
    resume_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    job_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_error: Mapped[str] = mapped_column(Text, default="")

    turns: Mapped[list["InterviewTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="InterviewTurn.idx"
    )


class InterviewTurn(Base, TimestampMixin):
    __tablename__ = "interview_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    # primary：计划题；follow_up：追问
    kind: Mapped[str] = mapped_column(String(20), default="primary")
    stage: Mapped[str] = mapped_column(String(40), default="")
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    # 出题依据：claim id / JD 要求 / 项目证据 id
    basis: Mapped[dict] = mapped_column(JSON, default=dict)
    # 内部评分，会话结束后才汇总进报告
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    feedback: Mapped[str] = mapped_column(Text, default="")
    skipped: Mapped[bool] = mapped_column(default=False)
    asked_by_run: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session: Mapped[InterviewSession] = relationship(back_populates="turns")
