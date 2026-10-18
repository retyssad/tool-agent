"""岗位与原始 JD。"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(100), default="")
    salary: Mapped[str] = mapped_column(String(100), default="")
    seniority: Mapped[str] = mapped_column(String(50), default="")
    # 用户手动维护，不由系统推断
    status: Mapped[str] = mapped_column(String(20), default="saved", index=True)
    # 解析产物：职责 / 硬性要求 / 技能栈 / 加分项 / 关键词 / 摘要 / 歧义
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    # pending / running / succeeded / failed
    parse_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    parse_error: Mapped[str] = mapped_column(Text, default="")
    parse_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    raw_jds: Mapped[list["RawJD"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    resumes: Mapped[list["ResumeVersion"]] = relationship(back_populates="target_job")  # noqa: F821


class RawJD(Base, TimestampMixin):
    """用户原样粘贴的 JD，唯一事实来源，永不修改。"""

    __tablename__ = "raw_jds"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(500), default="")
    # 相对 DATA_DIR，如 jobs/1/raw-1.txt
    file_path: Mapped[str] = mapped_column(String(300), default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped[Job] = relationship(back_populates="raw_jds")
