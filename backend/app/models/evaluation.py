"""简历评价：一次评价 = 多 critic 并行产出 findings，汇总为报告。"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin


class Evaluation(Base, TimestampMixin):
    """绑定 (简历草稿, 岗位) 的一次评价。只出报告，不自动改简历。"""

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("resume_drafts.id", ondelete="CASCADE"), index=True
    )
    # 版本快照：评价时草稿的 revision，便于说明"评的是哪一版"
    draft_revision: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # 总分与各维度分数
    overall_score: Mapped[int] = mapped_column(Integer, default=0)
    dimension_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    # 汇总：优点、优先改进项、一句话结论
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # 落盘快照，防止草稿被改后无法复现
    resume_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    job_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")

    findings: Mapped[list["EvaluationFinding"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="EvaluationFinding.id"
    )


class EvaluationFinding(Base, TimestampMixin):
    """一条评价结论。where 指向 resume.json 的路径，供前端定位。"""

    __tablename__ = "evaluation_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), index=True
    )
    # jd_match / quantification / technical_depth / clarity
    critic: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info")  # high / medium / low / good
    title: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    # 如 experience[0].bullets[1] / skills[2] / profile.headline
    where: Mapped[str] = mapped_column(String(200), default="")
    # 原文片段，便于前端高亮
    excerpt: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    # 关联的证据条目
    claim_ids: Mapped[list] = mapped_column(JSON, default=list)

    evaluation: Mapped[Evaluation] = relationship(back_populates="findings")
