"""Harness 核心：Run、事件、步骤、产物、提示词版本。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin, UTCDateTime


class Run(Base, TimestampMixin):
    """一次可追溯的 agent 执行。状态在库里，不在进程内存里。"""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 业务类型：fake / parse_job / extract_evidence / generate_resume / evaluate_resume /
    # scan_project / project_guide / interview
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # 业务对象引用，如 {"job_id": 3}
    target: Mapped[dict] = mapped_column(JSON, default=dict)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    # 预算与消耗
    budget_tokens: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # worker 归属，便于重启后识别孤儿 run
    worker_id: Mapped[str] = mapped_column(String(100), default="")
    # 由对话轮次里的工具启动的子 Run；用于把慢任务挂回那一轮
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    steps: Mapped[list["RunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunEvent(Base, TimestampMixin):
    """SSE 的真相源。seq 在单个 run 内单调递增，支持 Last-Event-ID 重放。"""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    # run.started / agent.started / tool.called / token.delta / artifact.created / run.finished
    type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    run: Mapped[Run] = relationship(back_populates="events")


class RunStep(Base, TimestampMixin):
    """一次模型调用的完整记录，用于回放与成本核算。"""

    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_name: Mapped[str] = mapped_column(String(100), default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    # 大内容落 artifact，表里存引用；小内容直接存
    request_json: Mapped[dict] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    request_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    response_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[Run] = relationship(back_populates="steps")


class Artifact(Base, TimestampMixin):
    """Run 产出的文件。"""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), default="file")  # file / json / log
    name: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(100), default="")
    path: Mapped[str] = mapped_column(String(500), default="")  # 相对 DATA_DIR
    size: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Prompt(Base, TimestampMixin):
    """版本化的提示词。run 记录 prompt@hash，保证历史可解释。"""

    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("name", "hash", name="uq_prompt_name_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    active: Mapped[bool] = mapped_column(default=True)
