"""事件写入与实时推送。

真相源是 run_events 表；Redis pub/sub 只负责实时性。
SSE 断线重连时以 Last-Event-ID 从表中补齐，因此进程重启不会丢事件。
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.harness import RunEvent

_settings = get_settings()

# 事件类型常量，避免拼写漂移
RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
AGENT_STARTED = "agent.started"
AGENT_FINISHED = "agent.finished"
TOOL_CALLED = "tool.called"
TOOL_RESULT = "tool.result"
TOKEN_DELTA = "token.delta"
ARTIFACT_CREATED = "artifact.created"
STEP_RECORDED = "step.recorded"
RUN_LOG = "run.log"

# 对话轮次专用
CHAT_PLAN = "chat.plan"
CHAT_MESSAGE = "chat.message"
CHAT_COMPACTION = "chat.compaction"
CHAT_INVESTIGATION = "chat.investigation"
CHAT_CONFIRMATION = "chat.confirmation"

# 只推实时、不落库
TRANSIENT_TYPES = {TOKEN_DELTA}


def channel(run_id: int) -> str:
    return f"run:{run_id}"


async def append_event(session: AsyncSession, run_id: int, type_: str, payload: dict[str, Any]) -> RunEvent:
    """在调用方事务内追加事件；自带 seq 分配。"""
    max_seq = await session.scalar(
        select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id)
    )
    event = RunEvent(run_id=run_id, seq=int(max_seq or 0) + 1, type=type_, payload=payload)
    session.add(event)
    await session.flush()
    return event


async def publish(run_id: int, event: RunEvent) -> None:
    """best-effort 实时推送。Redis 不可用不能影响任务本身。"""
    try:
        client = aioredis.from_url(_settings.redis_url, decode_responses=True)
        try:
            await client.publish(
                channel(run_id),
                json.dumps(
                    {"seq": event.seq, "type": event.type, "payload": event.payload},
                    ensure_ascii=False,
                    default=str,
                ),
            )
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001 - 降级为纯轮询/重放
        pass


async def publish_transient(run_id: int, type_: str, payload: dict[str, Any]) -> None:
    """只推实时、不落库的事件（如 token.delta）。

    断线重连后不会补发；调用方应以最终文本为准。
    复用模块级连接池，避免每个分片都新建连接。
    """
    try:
        client = _shared_client()
        await client.publish(
            channel(run_id),
            json.dumps(
                {
                    # transient 事件没有 seq，前端不能按 seq 去重/补历史
                    "seq": 0,
                    "type": type_,
                    "payload": payload,
                    "transient": True,
                },
                ensure_ascii=False,
                default=str,
            ),
        )
    except Exception:  # noqa: BLE001
        pass


_shared: aioredis.Redis | None = None


def _shared_client() -> aioredis.Redis:
    global _shared
    if _shared is None:
        _shared = aioredis.from_url(_settings.redis_url, decode_responses=True)
    return _shared
