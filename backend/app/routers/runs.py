from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_session
from app.harness import events as ev
from app.models.common import RUN_QUEUED
from app.models.harness import Run, RunEvent, RunStep
from app.queue import enqueue_run

router = APIRouter(prefix="/api/runs", tags=["runs"])
settings = get_settings()


class CreateRunRequest(BaseModel):
    kind: str = Field(default="fake")
    title: str = ""
    target: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    budget_tokens: int = 0


class RunView(BaseModel):
    id: int
    kind: str
    status: str
    title: str
    target: dict
    prompt_tokens: int
    completion_tokens: int
    error: str
    created_at: Any
    started_at: Any
    finished_at: Any
    last_seq: int = 0
    parent_run_id: int | None = None

    class Config:
        from_attributes = True


@router.post("", response_model=RunView)
async def create_run(body: CreateRunRequest, session: AsyncSession = Depends(get_session)) -> RunView:
    from app.tasks import get_handler

    if get_handler(body.kind) is None:
        raise HTTPException(400, f"未知任务类型: {body.kind}")

    run = Run(
        kind=body.kind,
        status=RUN_QUEUED,
        title=body.title or body.kind,
        target=body.target,
        input_json=body.input,
        budget_tokens=body.budget_tokens,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = f"入队失败（Redis 未启动？）: {exc}"
        await session.commit()
        raise HTTPException(503, run.error) from exc

    return _view(run)


@router.get("", response_model=list[RunView])
async def list_runs(
    limit: int = Query(30, ge=1, le=200),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    parent_run_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[RunView]:
    seq = (
        select(RunEvent.run_id, func.max(RunEvent.seq).label("last_seq"))
        .group_by(RunEvent.run_id)
        .subquery()
    )
    stmt = select(Run, func.coalesce(seq.c.last_seq, 0)).outerjoin(seq, seq.c.run_id == Run.id)
    if status:
        stmt = stmt.where(Run.status == status)
    if kind:
        stmt = stmt.where(Run.kind == kind)
    if parent_run_id is not None:
        stmt = stmt.where(Run.parent_run_id == parent_run_id)
    rows = await session.execute(stmt.order_by(Run.id.desc()).limit(limit))
    return [_view(run, last_seq) for run, last_seq in rows.all()]


@router.get("/events/search")
async def search_events(
    q: str = Query("", max_length=200),
    run_ids: str | None = Query(default=None, description="逗号分隔的 run id"),
    types: str | None = Query(default=None, description="逗号分隔的事件类型"),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO 时间，含"),
    until: str | None = Query(default=None, description="ISO 时间，不含"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """跨 Run 检索事件。

    默认排除 token.delta（流式碎片，检索价值为零），也排除已标记 transient 的类型。
    事件 payload 只返回摘要，完整结果让模型用 get_run / get_run_events 拿。
    """
    stmt = select(RunEvent, Run).join(Run, Run.id == RunEvent.run_id)

    if run_ids:
        try:
            ids = [int(x) for x in run_ids.split(",") if x.strip()]
        except ValueError as exc:
            raise HTTPException(400, "run_ids 必须是逗号分隔的整数") from exc
        if ids:
            stmt = stmt.where(RunEvent.run_id.in_(ids))

    excluded = {ev.TOKEN_DELTA}
    if types:
        wanted = {t.strip() for t in types.split(",") if t.strip()}
        stmt = stmt.where(RunEvent.type.in_(wanted))
    else:
        stmt = stmt.where(RunEvent.type.not_in(excluded))

    if status:
        stmt = stmt.where(Run.status == status)
    if kind:
        stmt = stmt.where(Run.kind == kind)
    if since:
        stmt = stmt.where(RunEvent.created_at >= _parse_time(since))
    if until:
        stmt = stmt.where(RunEvent.created_at < _parse_time(until))

    rows = (await session.execute(stmt.order_by(RunEvent.id.desc()).limit(limit))).all()

    out: list[dict] = []
    for event, run in rows:
        summary = _event_summary(event.type, event.payload or {})
        if q and q.lower() not in summary.lower():
            continue
        out.append(
            {
                "run_id": event.run_id,
                "seq": event.seq,
                "type": event.type,
                "kind": run.kind,
                "run_status": run.status,
                "summary": summary,
                "created_at": event.created_at,
            }
        )
    return out


def _parse_time(value: str):
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(400, f"时间格式不合法: {value}") from exc
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_summary(type_: str, payload: dict) -> str:
    """一行摘要，用于检索结果。不返回完整 payload。"""
    if type_ == ev.RUN_LOG:
        return str(payload.get("message", ""))[:300]
    if type_ == ev.TOOL_CALLED:
        return f"{payload.get('tool')} {json.dumps(payload.get('args', {}), ensure_ascii=False)[:200]}"
    if type_ == ev.TOOL_RESULT:
        ok = "成功" if payload.get("ok") else "失败"
        err = payload.get("error")
        return f"{payload.get('tool')} {ok}{'：' + str(err)[:200] if err else ''}"
    if type_ == ev.AGENT_STARTED:
        return f"agent 开始：{payload.get('agent', '')}"
    if type_ == ev.AGENT_FINISHED:
        return f"agent 结束：{payload.get('agent', '')}"
    if type_ == ev.RUN_FINISHED:
        return f"结束：{payload.get('status', '')} {str(payload.get('error') or '')[:200]}".strip()
    if type_ == ev.RUN_STARTED:
        return f"开始：{payload.get('kind', '')} {payload.get('title', '')}".strip()
    if type_ == ev.CHAT_MESSAGE:
        return str(payload.get("content", ""))[:300]
    return json.dumps(payload, ensure_ascii=False, default=str)[:300]


@router.get("/{run_id}", response_model=RunView)
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> RunView:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    last_seq = await session.scalar(
        select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id)
    )
    return _view(run, int(last_seq or 0))


@router.get("/{run_id}/steps")
async def get_steps(run_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await session.execute(
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)
    )
    return [
        {
            "id": s.id,
            "seq": s.seq,
            "agent": s.agent,
            "model": s.model,
            "prompt_name": s.prompt_name,
            "prompt_hash": s.prompt_hash,
            "request": s.request_json,
            "response": s.response_json,
            "tool_calls": s.tool_calls,
            "prompt_tokens": s.prompt_tokens,
            "completion_tokens": s.completion_tokens,
            "latency_ms": s.latency_ms,
            "error": s.error,
            "created_at": s.created_at,
        }
        for s in rows.scalars()
    ]


@router.get("/{run_id}/events")
async def list_events(
    run_id: int, after: int = 0, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = await session.execute(
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after)
        .order_by(RunEvent.seq)
    )
    return [{"seq": e.seq, "type": e.type, "payload": e.payload} for e in rows.scalars()]


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    if run.status not in ("queued", "running"):
        raise HTTPException(409, f"当前状态不可取消: {run.status}")
    run.cancel_requested = True
    await session.commit()
    return {"ok": True}


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: int,
    request: Request,
    last_event_id: str | None = Query(default=None, alias="last_event_id"),
) -> StreamingResponse:
    """SSE：先补历史（数据库），再订阅实时（Redis），seq 去重。"""
    return StreamingResponse(
        _event_stream(run_id, request, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(run_id: int, request: Request, last_event_id: str | None):
    cursor = 0
    if last_event_id:
        header = request.headers.get("last-event-id")
        raw = header or last_event_id
        try:
            cursor = int(raw)
        except (TypeError, ValueError):
            cursor = 0

    # 1) 补历史
    async with SessionLocal() as session:
        rows = await session.execute(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > cursor)
            .order_by(RunEvent.seq)
        )
        for event in rows.scalars():
            cursor = event.seq
            yield _sse(event.seq, event.type, event.payload)
        run = await session.get(Run, run_id)

    if run and run.status in ("succeeded", "failed", "cancelled", "interrupted"):
        yield "event: end\ndata: {}\n\n"
        return

    # 2) 实时订阅
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(ev.channel(run_id))
    try:
        while True:
            if await request.is_disconnected():
                return
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                # 兜底：Redis 丢消息时用数据库补齐
                async with SessionLocal() as session:
                    rows = await session.execute(
                        select(RunEvent)
                        .where(RunEvent.run_id == run_id, RunEvent.seq > cursor)
                        .order_by(RunEvent.seq)
                    )
                    new_events = list(rows.scalars())
                    if new_events:
                        for event in new_events:
                            cursor = event.seq
                            yield _sse(event.seq, event.type, event.payload)
                        continue
                    run = await session.get(Run, run_id)
                if run and run.status in ("succeeded", "failed", "cancelled", "interrupted"):
                    yield "event: end\ndata: {}\n\n"
                    return
                continue

            data = json.loads(message["data"])
            if data.get("transient"):
                # 流式分片没有 seq，直接转发；不推进 cursor，不参与去重
                payload = {**data.get("payload", {}), "transient": True}
                yield f"event: {data['type']}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                continue
            if data["seq"] <= cursor:
                continue
            cursor = data["seq"]
            yield _sse(data["seq"], data["type"], data["payload"])
            if data["type"] == ev.RUN_FINISHED:
                yield "event: end\ndata: {}\n\n"
                return
    finally:
        await pubsub.unsubscribe(ev.channel(run_id))
        await pubsub.aclose()
        await client.aclose()


def _sse(seq: int, type_: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {seq}\nevent: {type_}\ndata: {data}\n\n"


def _view(run: Run, last_seq: int = 0) -> RunView:
    return RunView(
        id=run.id,
        kind=run.kind,
        status=run.status,
        title=run.title,
        target=run.target or {},
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        error=run.error or "",
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_seq=last_seq,
        parent_run_id=run.parent_run_id,
    )
