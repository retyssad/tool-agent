"""项目：本地源码目录与扫描缓存。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin, UTCDateTime


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(500), unique=True)
    tech_stack: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    last_scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    scan_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 扫描产物：data/projects/{id}/scan.json
    scan_path: Mapped[str] = mapped_column(String(300), default="")
    scan_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    scan_error: Mapped[str] = mapped_column(Text, default="")
    scan_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 扫描摘要：文件数、模块数、总行数
    scan_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # 由证据条目反查用
    role: Mapped[str] = mapped_column(String(200), default="")

    evidence: Mapped[list["ProjectEvidence"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    guides: Mapped[list["ProjectGuide"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectEvidence(Base, TimestampMixin):
    """从源码提取、带 file:line 引用的事实。"""

    __tablename__ = "project_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    refs: Mapped[list] = mapped_column(JSON, default=list)  # [{path, line, snippet}]
    module: Mapped[str] = mapped_column(String(200), default="")
    # 证据分类：architecture / feature / quality / data / ops
    category: Mapped[str] = mapped_column(String(30), default="feature")
    # 对应的能力关键词，供简历与面试使用
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="evidence")


class ProjectGuide(Base, TimestampMixin):
    """项目导学内容（面经题、模块讲解、追问链）。"""

    __tablename__ = "project_guides"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="interview")  # interview / course
    title: Mapped[str] = mapped_column(String(300), default="")
    content_md: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="guides")
