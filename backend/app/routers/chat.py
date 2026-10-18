"""对话路由：会话、轮次、条目、确认与拒绝。

轮次本身是一个 Run：创建后立刻返回 run_id，前端订阅该 Run 的 SSE。
确认动作会创建"续跑 Run"，不阻塞 worker。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat_context import compact
from app.agents import chat_tools
from app.db import SessionLocal, get_session
from app.harness.executor import RunHandle
from app.llm.client import LLMClient
from app.models.chat import (
    ENTRY_USER,
    TURN_ACTIVE,
    TURN_CANCELLED,
    TURN_QUEUED,
    ChatAction,
    ChatEntry,
    ChatSession,
    ChatTurn,
)
from app.models.common import RUN_QUEUED
from app.models.harness import Run
from app.queue import enqueue_run

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateSessionBody(BaseModel):
    title: str = "新对话"


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class TurnBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class ConfirmBody(BaseModel):
    action_id: int


# ---------- 会话 ----------

@router.get("/sessions")
async def list_sessions(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.scalars(
            select(ChatSession)
            .where(ChatSession.archived.is_(False))
            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        )
    ).all()
    counts = dict(
        (
            await session.execute(
                select(ChatTurn.session_id, func.count(ChatTurn.id)).group_by(ChatTurn.session_id)
            )
        ).all()
    )
    return [
        {
            "id": s.id,
            "title": s.title,
            "turns": counts.get(s.id, 0),
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in rows
    ]


@router.post("/sessions")
async def create_session(
    body: CreateSessionBody, session: AsyncSession = Depends(get_session)
) -> dict:
    row = ChatSession(title=body.title[:200])
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "title": row.title, "turns": 0, "created_at": row.created_at}


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: int, body: RenameBody, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(ChatSession, session_id)
    if row is None:
        raise HTTPException(404, "对话不存在")
    row.title = body.title.strip()[:200]
    await session.commit()
    return {"id": row.id, "title": row.title}


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.scalar(
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .options(selectinload(ChatSession.turns), selectinload(ChatSession.entries))
    )
    if row is None:
        raise HTTPException(404, "对话不存在")

    actions = (
        await session.scalars(
            select(ChatAction)
            .where(ChatAction.session_id == session_id)
            .order_by(ChatAction.id.desc())
            .limit(50)
        )
    ).all()
    actions_by_turn: dict[int, list[dict]] = {}
    for a in actions:
        key = a.turn_id or 0
        actions_by_turn.setdefault(key, []).append(
            {
                "id": a.id,
                "tool": a.tool,
                "preview": a.preview,
                "status": a.status,
                "error": a.error,
                "run_id": a.run_id,
            }
        )

    turns = [
        {
            "id": t.id,
            "seq": t.seq,
            "user_content": t.user_content,
            "assistant_content": t.assistant_content,
            "status": t.status,
            "error": t.error,
            "plan": t.plan or [],
            "cards": t.cards or [],
            "run_ids": t.run_ids or [],
            "child_run_ids": t.child_run_ids or [],
            "current_run_id": t.current_run_id,
            "actions": actions_by_turn.get(t.id, []),
            "created_at": t.created_at,
        }
        for t in row.turns
    ]
    # 最近一个仍在执行的轮次，前端据此重连 SSE
    active = next(
        (t for t in reversed(turns) if t["status"] in TURN_ACTIVE or t["status"] == "awaiting_confirmation"),
        None,
    )
    return {
        "id": row.id,
        "title": row.title,
        "turns": turns,
        "active_turn": active,
        "entries_count": len(row.entries),
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(ChatSession, session_id)
    if row is None:
        raise HTTPException(404, "对话不存在")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


# ---------- 轮次 ----------

@router.post("/sessions/{session_id}/turns")
async def create_turn(
    session_id: int, body: TurnBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    chat_session = await session.get(ChatSession, session_id)
    if chat_session is None:
        raise HTTPException(404, "对话不存在")

    # 同一会话同时只允许一个执行中的轮次，避免 transcript 交错
    active = await session.scalar(
        select(ChatTurn.id).where(
            ChatTurn.session_id == session_id, ChatTurn.status.in_(TURN_ACTIVE)
        )
    )
    if active is not None:
        raise HTTPException(409, f"上一轮仍在执行（turn #{active}），请等待结束或取消")

    seq = int(
        await session.scalar(
            select(func.coalesce(func.max(ChatTurn.seq), 0)).where(
                ChatTurn.session_id == session_id
            )
        )
        or 0
    ) + 1

    turn = ChatTurn(
        session_id=session_id,
        seq=seq,
        user_content=body.content.strip(),
        status=TURN_QUEUED,
    )
    session.add(turn)
    await session.flush()

    from app.agents.chat_context import append_entry

    await append_entry(
        session,
        session_id=session_id,
        turn_id=turn.id,
        kind=ENTRY_USER,
        payload={"content": turn.user_content},
    )

    run = Run(
        kind="chat",
        status=RUN_QUEUED,
        title=turn.user_content[:40] or "对话",
        target={"session_id": session_id, "turn_id": turn.id},
    )
    session.add(run)
    await session.flush()

    turn.run_ids = [run.id]
    turn.current_run_id = run.id
    if chat_session.title in ("", "新对话"):
        chat_session.title = turn.user_content[:40]
    await session.commit()
    await session.refresh(turn)
    await session.refresh(run)

    try:
        enqueue_run(run.id, queue="chat")
    except Exception as exc:  # noqa: BLE001
        turn.status = "failed"
        turn.error = f"入队失败（Redis 未启动？）：{exc}"
        await session.commit()
        raise HTTPException(503, turn.error) from exc

    return {
        "turn_id": turn.id,
        "seq": turn.seq,
        "status": turn.status,
        "run_id": run.id,
    }


@router.post("/sessions/{session_id}/compact")
async def compact_session(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """手动压缩。同步执行（不占 chat 队列），结束后立刻返回新摘要。"""
    chat_session = await session.get(ChatSession, session_id)
    if chat_session is None:
        raise HTTPException(404, "对话不存在")

    async with SessionLocal() as work:
        handle = RunHandle(run_id=0, kind="chat_compact", llm=LLMClient(), _session=work)
        try:
            row = await compact(
                handle, session, session_id=session_id, trigger="manual", force=True
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"压缩失败：{exc}") from exc
    if row is None:
        return {"ok": False, "message": "没有可压缩的内容"}
    return {
        "ok": True,
        "compaction_id": row.id,
        "from_seq": row.from_seq,
        "to_seq": row.to_seq,
        "tokens_before": row.tokens_before,
        "tokens_after": row.tokens_after,
        "summary": row.summary,
    }


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    turn = await session.get(ChatTurn, turn_id)
    if turn is None:
        raise HTTPException(404, "轮次不存在")
    if turn.status not in TURN_ACTIVE:
        raise HTTPException(409, f"当前状态不可取消: {turn.status}")

    run = await session.get(Run, turn.current_run_id) if turn.current_run_id else None
    if run is not None and run.status in ("queued", "running"):
        run.cancel_requested = True
    turn.status = TURN_CANCELLED
    await session.commit()
    return {"ok": True, "note": "只取消本轮，已启动的子 Run 不受影响"}


# ---------- 确认 ----------

@router.post("/actions/{action_id}/confirm")
async def confirm_action(
    action_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    action = await session.get(ChatAction, action_id)
    if action is None:
        raise HTTPException(404, "待确认动作不存在")
    if action.status != "pending":
        raise HTTPException(409, f"该动作状态为 {action.status}")

    turn = await session.get(ChatTurn, action.turn_id) if action.turn_id else None
    if turn is None:
        raise HTTPException(404, "轮次不存在")

    # 已有续跑在跑就不重复开
    active = await session.scalar(
        select(ChatTurn.id).where(
            ChatTurn.session_id == turn.session_id,
            ChatTurn.status.in_(TURN_ACTIVE),
            ChatTurn.id != turn.id,
        )
    )
    if active is not None:
        raise HTTPException(409, "另一个轮次正在执行")

    run = Run(
        kind="chat",
        status=RUN_QUEUED,
        title=f"确认执行：{action.tool}",
        target={
            "session_id": turn.session_id,
            "turn_id": turn.id,
            "confirmed_action_id": action.id,
        },
    )
    session.add(run)
    await session.flush()
    turn.status = TURN_QUEUED
    turn.current_run_id = run.id
    turn.run_ids = [*(turn.run_ids or []), run.id]
    await session.commit()

    try:
        enqueue_run(run.id, queue="chat")
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = f"入队失败：{exc}"
        await session.commit()
        raise HTTPException(503, run.error) from exc

    return {"ok": True, "run_id": run.id, "turn_id": turn.id, "action_id": action.id}


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    action = await session.get(ChatAction, action_id)
    if action is None:
        raise HTTPException(404, "待确认动作不存在")
    if action.status != "pending":
        raise HTTPException(409, f"该动作状态为 {action.status}")
    action.status = "rejected"

    # 占位条目改成"已取消"，模型后续不会以为动作执行了
    if action.entry_id:
        entry = await session.get(ChatEntry, action.entry_id)
        if entry is not None:
            entry.payload = {
                "tool_call_id": action.tool_call_id,
                "tool": action.tool,
                "content": '{"ok": false, "error": "用户取消"}',
            }
            entry.summary = f"{action.tool} 已被用户取消"

    if action.turn_id:
        remaining = await session.scalar(
            select(func.count(ChatAction.id)).where(
                ChatAction.turn_id == action.turn_id, ChatAction.status == "pending"
            )
        )
        turn = await session.get(ChatTurn, action.turn_id)
        if turn is not None and not remaining:
            # 没有待确认动作了，本轮就此收尾
            turn.status = "succeeded"
    await session.commit()
    return {"ok": True, "action_id": action.id}


# ---------- 工具清单（前端展示"能做什么"） ----------

@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    out = []
    for spec in (chat_tools.registry.get(name) for name in chat_tools.registry.names()):
        if "chat" not in spec.audiences:
            continue
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                "risk": spec.risk,
            }
        )
    return out
