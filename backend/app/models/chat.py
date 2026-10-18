"""对话层：会话、轮次、条目、待确认动作、压缩点。

条目（ChatEntry）是会话的唯一真相源，append-only。被压缩的条目只标记
compacted_into_id，不删除，便于追溯模型当时看到了什么。
"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import TimestampMixin

# 轮次状态
TURN_QUEUED = "queued"
TURN_RUNNING = "running"
TURN_SUCCEEDED = "succeeded"
TURN_FAILED = "failed"
TURN_CANCELLED = "cancelled"
TURN_AWAITING_CONFIRMATION = "awaiting_confirmation"

TURN_ACTIVE = (TURN_QUEUED, TURN_RUNNING)

TURN_STATUSES = (
    TURN_QUEUED,
    TURN_RUNNING,
    TURN_SUCCEEDED,
    TURN_FAILED,
    TURN_CANCELLED,
    TURN_AWAITING_CONFIRMATION,
)

# 条目类型
ENTRY_USER = "user"
ENTRY_ASSISTANT = "assistant"
ENTRY_TOOL_CALLS = "assistant_tool_calls"
ENTRY_TOOL_RESULT = "tool_result"
ENTRY_NOTE = "note"

ENTRY_KINDS = (ENTRY_USER, ENTRY_ASSISTANT, ENTRY_TOOL_CALLS, ENTRY_TOOL_RESULT, ENTRY_NOTE)


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    archived: Mapped[bool] = mapped_column(default=False)

    turns: Mapped[list["ChatTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatTurn.seq"
    )
    entries: Mapped[list["ChatEntry"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatEntry.seq"
    )


class ChatTurn(Base, TimestampMixin):
    """一条用户消息引发的一次执行，对应一个 kind=chat 的 Run。"""

    __tablename__ = "chat_turns"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_chat_turn_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    user_content: Mapped[str] = mapped_column(Text, default="")
    assistant_content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default=TURN_QUEUED, index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    # 本轮涉及的全部 Run（首个 Run + 确认续跑），按发生顺序
    run_ids: Mapped[list] = mapped_column(JSON, default=list)
    # 由本轮工具启动的慢任务子 Run，与 run_ids 区分开
    child_run_ids: Mapped[list] = mapped_column(JSON, default=list)
    current_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 计划块：多步任务的实时进度，由 set_plan 工具写入
    plan: Mapped[list] = mapped_column(JSON, default=list)
    # 本轮产生的卡片（追问选项、链接、慢任务），刷新后仍要显示
    cards: Mapped[list] = mapped_column(JSON, default=list)

    session: Mapped[ChatSession] = relationship(back_populates="turns")


class ChatEntry(Base, TimestampMixin):
    """append-only 的对话条目。seq 在会话内单调递增。"""

    __tablename__ = "chat_entries"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_chat_entry_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_turns.id", ondelete="CASCADE"), nullable=True, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # 工具结果老化用的确定性摘要；空表示直接用 payload
    summary: Mapped[str] = mapped_column(Text, default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    # 被哪个压缩点覆盖；None 表示仍原样进入上下文
    compacted_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    session: Mapped[ChatSession] = relationship(back_populates="entries")


class ChatAction(Base, TimestampMixin):
    """高影响操作的待确认记录。确认后由续跑 Run 执行。"""

    __tablename__ = "chat_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_turns.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tool_call_id: Mapped[str] = mapped_column(String(120), default="")
    # 占位条目：确认后原地改成真实结果，避免同一 tool_call_id 出现两条结果
    entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(80))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    preview: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(20), default="destructive")
    # pending / executed / rejected / failed
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class ChatCompaction(Base, TimestampMixin):
    """一次压缩：覆盖 [from_seq, to_seq]，摘要固定四段。"""

    __tablename__ = "chat_compactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_seq: Mapped[int] = mapped_column(Integer, default=0)
    to_seq: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens_before: Mapped[int] = mapped_column(Integer, default=0)
    tokens_after: Mapped[int] = mapped_column(Integer, default=0)
    # auto / manual
    trigger: Mapped[str] = mapped_column(String(20), default="auto")
