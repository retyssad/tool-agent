"""共享列与枚举。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite 不保存时区，读回来是 naive datetime。

    写入时统一转 UTC，读取时补回 tzinfo，保证 API 输出带 Z/+00:00，
    前端 new Date() 才不会把它当本地时间（否则差 8 小时）。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# Run 状态机
RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"
RUN_INTERRUPTED = "interrupted"

# 证据条目状态
CLAIM_CONFIRMED = "confirmed"
CLAIM_PENDING = "pending"
CLAIM_EXPIRED = "expired"
CLAIM_REJECTED = "rejected"

# 岗位投递状态（仅用户手动设置）
JOB_STATUSES = ("saved", "applied", "interviewing", "rejected")

# 面试会话状态
INTERVIEW_ACTIVE = "active"
INTERVIEW_COMPLETED = "completed"
INTERVIEW_ABANDONED = "abandoned"

# 面试阶段
INTERVIEW_STAGES = ("project", "job_fit", "behavior", "closing")
