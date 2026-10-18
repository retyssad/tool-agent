"""简历域：证据账本、生成简历版本、导入简历。"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin


class ClaimLedger(Base, TimestampMixin):
    """证据账本：一组证据条目。kind=personal 为个人账本，kind=project 绑定单个项目。"""

    __tablename__ = "claim_ledgers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    # personal / project
    kind: Mapped[str] = mapped_column(String(20), default="personal", index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="ledger", cascade="all, delete-orphan", order_by="Claim.id"
    )


class Claim(Base, TimestampMixin):
    """证据条目：可被生成简历引用的事实陈述。只有 confirmed 可进入导出。"""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(
        ForeignKey("claim_ledgers.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    # 来源：project / resume_import / manual
    source_type: Mapped[str] = mapped_column(String(30), default="manual")
    # 如 repo/path/file.py:12 或 imported_resume#3#p2
    source_ref: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    # 归入简历的哪一节：experience / project / skill / education / campus / award / other
    kind: Mapped[str] = mapped_column(String(30), default="other", index=True)
    organization: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    period: Mapped[str] = mapped_column(String(100), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    variants: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[str] = mapped_column(Text, default="")

    ledger: Mapped[ClaimLedger] = relationship(back_populates="claims")


class CandidateProfile(Base, TimestampMixin):
    """候选人基本信息。个人自用，只有一行，id 固定为 1。"""

    __tablename__ = "candidate_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    headline: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(150), default="")
    location: Mapped[str] = mapped_column(String(100), default="")
    wechat: Mapped[str] = mapped_column(String(100), default="")
    birth_year: Mapped[str] = mapped_column(String(20), default="")
    political_status: Mapped[str] = mapped_column(String(50), default="")
    english_level: Mapped[str] = mapped_column(String(50), default="")
    github: Mapped[str] = mapped_column(String(200), default="")
    photo_path: Mapped[str] = mapped_column(String(300), default="")


class ResumeVersion(Base, TimestampMixin):
    """生成简历的一个不可变版本；resume.json 是唯一事实源。"""

    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("claim_ledgers.id"), index=True)
    target_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    template: Mapped[str] = mapped_column(String(100), default="asu")
    locale: Mapped[str] = mapped_column(String(10), default="zh")
    # 相对 DATA_DIR：resumes/generated/{id}/...
    resume_json_path: Mapped[str] = mapped_column(String(300), default="")
    html_path: Mapped[str] = mapped_column(String(300), default="")
    pdf_path: Mapped[str] = mapped_column(String(300), default="")
    gate_status: Mapped[str] = mapped_column(String(20), default="unchecked")
    gate_errors: Mapped[list] = mapped_column(JSON, default=list)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")

    ledger: Mapped[ClaimLedger] = relationship()
    target_job: Mapped["Job"] = relationship(back_populates="resumes")  # noqa: F821


class ResumeDraft(Base, TimestampMixin):
    """编辑器里的工作副本；导出时冻结成 ResumeVersion。"""

    __tablename__ = "resume_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("claim_ledgers.id"), index=True)
    target_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    template: Mapped[str] = mapped_column(String(100), default="asu")
    resume_json: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ImportedResume(Base, TimestampMixin):
    """用户上传的既有 PDF 简历，不做结构化转换。"""

    __tablename__ = "imported_resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    original_name: Mapped[str] = mapped_column(String(300), default="")
    original_path: Mapped[str] = mapped_column(String(300))
    text_path: Mapped[str] = mapped_column(String(300), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    # 标题、标签，便于分类
    tags: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
