"""模拟面试路由。

出题是同步的（点「下一题」即刻返回），评分与报告走异步 run。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.interview_pipeline import session_dict
from app.agents.interviewer import next_question
from app.config import get_settings
from app.db import SessionLocal, get_session
from app.harness.executor import RunHandle
from app.llm.client import LLMClient
from app.models.common import (
    INTERVIEW_ABANDONED,
    INTERVIEW_ACTIVE,
    INTERVIEW_COMPLETED,
    RUN_QUEUED,
)
from app.models.harness import Run
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job
from app.models.resume import ImportedResume, ResumeDraft
from app.queue import enqueue_run

router = APIRouter(prefix="/api/interviews", tags=["interviews"])
settings = get_settings()

MAX_FOLLOW_UPS = 1


class CreateSession(BaseModel):
    job_id: int
    resume_kind: str = "draft"
    draft_id: int | None = None
    imported_resume_id: int | None = None
    title: str = ""


class AnswerBody(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


class SkipBody(BaseModel):
    reason: str = ""


def _imported_text(record: ImportedResume) -> str:
    path = settings.data_dir / record.text_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


@router.post("")
async def create_session(
    body: CreateSession, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    job = await session.get(Job, body.job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")

    resume_snapshot: dict[str, Any] = {}
    if body.resume_kind == "draft":
        if body.draft_id is None:
            raise HTTPException(400, "resume_kind=draft 时必须提供 draft_id")
        draft = await session.get(ResumeDraft, body.draft_id)
        if draft is None:
            raise HTTPException(404, "简历草稿不存在")
        if not draft.resume_json:
            raise HTTPException(400, "简历草稿为空，无法面试")
        resume_snapshot = draft.resume_json
    elif body.resume_kind == "imported":
        if body.imported_resume_id is None:
            raise HTTPException(400, "resume_kind=imported 时必须提供 imported_resume_id")
        record = await session.get(ImportedResume, body.imported_resume_id)
        if record is None:
            raise HTTPException(404, "导入简历不存在")
        text = await asyncio.to_thread(_imported_text, record)
        resume_snapshot = {"text": text, "title": record.title}
    else:
        raise HTTPException(400, "resume_kind 只能是 draft 或 imported")

    job_snapshot = {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "seniority": job.seniority,
        "salary": job.salary,
        "hard_requirements": (job.parsed or {}).get("hard_requirements", []),
        "responsibilities": (job.parsed or {}).get("responsibilities", []),
        "skills": (job.parsed or {}).get("skills", []),
        "nice_to_have": (job.parsed or {}).get("nice_to_have", []),
    }

    interview = InterviewSession(
        job_id=job.id,
        resume_kind=body.resume_kind,
        draft_id=body.draft_id,
        imported_resume_id=body.imported_resume_id,
        title=body.title.strip()[:200] or f"{job.title} · 模拟面试",
        status=INTERVIEW_ACTIVE,
        resume_snapshot=resume_snapshot,
        job_snapshot=job_snapshot,
    )
    session.add(interview)
    await session.commit()
    await session.refresh(interview)

    run = Run(
        kind="interview_plan",
        status=RUN_QUEUED,
        title=f"生成面试契约：{interview.title}",
        target={"session_id": interview.id},
        budget_tokens=60_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    interview.plan_run_id = run.id
    await session.commit()

    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        interview.plan_error = f"入队失败：{exc}"
        await session.commit()
        raise HTTPException(503, interview.plan_error) from exc

    return {"session_id": interview.id, "run_id": run.id}


@router.get("")
async def list_sessions(
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(InterviewSession).order_by(InterviewSession.id.desc())
    if status:
        stmt = stmt.where(InterviewSession.status == status)
    rows = (await session.scalars(stmt)).all()
    return [session_dict(s, with_turns=False) for s in rows]


@router.get("/{session_id}")
async def get_session_detail(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await session.scalar(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.turns))
    )
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    data = session_dict(row)
    data["progress"] = _progress(row)
    return data


@router.post("/{session_id}/next")
async def next_turn(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """同步出题：点一次返回一题。"""
    row = await session.scalar(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.turns))
    )
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    if row.status != INTERVIEW_ACTIVE:
        raise HTTPException(409, f"会话状态为 {row.status}，不能继续")
    if not row.pact or not row.pact.get("questions"):
        raise HTTPException(409, "面试契约还没生成完成，请稍等")

    turns = list(row.turns)
    # 未回答的题不允许再出一题
    if turns and not turns[-1].answer and not turns[-1].skipped:
        raise HTTPException(409, "请先回答或跳过当前问题")

    streak = _follow_up_streak(turns)
    asked = sum(1 for t in turns if t.kind == "primary")
    plan_total = len(row.pact.get("questions") or [])
    if asked >= plan_total and streak >= MAX_FOLLOW_UPS:
        raise HTTPException(409, "主问题已全部问完，请结束面试并生成报告")

    async with SessionLocal() as work:
        llm = LLMClient()
        handle = RunHandle(run_id=0, kind="interview_ask", llm=llm, _session=work)
        # 出题不写 run_events（run_id=0 会污染），只借用 call_model 的模型调用能力
        try:
            decision = await next_question(
                handle,
                pact=row.pact,
                turns=[
                    {
                        "idx": t.idx,
                        "stage": t.stage,
                        "kind": t.kind,
                        "question": t.question,
                        "answer": t.answer,
                    }
                    for t in turns
                ],
                follow_up_streak=streak,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"出题失败：{exc}") from exc

        if decision["action"] == "finish" or not decision["question"]:
            row.round_index = len(turns)
            await session.commit()
            return {"action": "finish", "progress": _progress(row)}

        # follow_up 超过上限或主问题已问完时，强制转为主问题
        kind = "follow_up" if decision["action"] == "follow_up" and streak < MAX_FOLLOW_UPS else "primary"
        if kind == "primary" and asked >= plan_total:
            row.round_index = len(turns)
            await session.commit()
            return {"action": "finish", "progress": _progress(row)}

        turn = InterviewTurn(
            session_id=row.id,
            idx=len(turns) + 1,
            kind=kind,
            stage=decision["stage"],
            question=decision["question"],
            basis={
                "reason": decision["reason"],
                "follow_up_of": decision["follow_up_of"],
                "plan_index": asked + 1 if kind == "primary" else None,
            },
        )
        session.add(turn)
        row.round_index = len(turns) + 1
        await session.commit()
        await session.refresh(turn)

        await session.refresh(row, attribute_names=["turns"])
        return {
            "action": "ask",
            "turn": {
                "id": turn.id,
                "idx": turn.idx,
                "kind": turn.kind,
                "stage": turn.stage,
                "question": turn.question,
            },
            "progress": _progress(row),
        }


@router.post("/{session_id}/answer")
async def answer_turn(
    session_id: int, body: AnswerBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await session.scalar(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.turns))
    )
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    if row.status != INTERVIEW_ACTIVE:
        raise HTTPException(409, "会话已结束")
    if not row.turns:
        raise HTTPException(409, "还没有问题")
    turn = row.turns[-1]
    if turn.answer or turn.skipped:
        raise HTTPException(409, "当前问题已回答")

    turn.answer = body.answer.strip()
    await session.commit()

    # 异步评分：不阻塞对话
    run = Run(
        kind="interview_score",
        status=RUN_QUEUED,
        title=f"评分：会话 #{row.id} 第 {turn.idx} 题",
        target={"turn_id": turn.id},
        budget_tokens=30_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    turn.score_run_id = run.id
    await session.commit()

    try:
        enqueue_run(run.id)
    except Exception:  # noqa: BLE001 - 评分入队失败不阻塞对话
        pass

    await session.refresh(row, attribute_names=["turns"])
    return {"ok": True, "run_id": run.id, "progress": _progress(row)}


@router.post("/{session_id}/skip")
async def skip_turn(
    session_id: int, body: SkipBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await session.scalar(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.turns))
    )
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    if row.status != INTERVIEW_ACTIVE:
        raise HTTPException(409, "会话已结束")
    if not row.turns:
        raise HTTPException(409, "还没有问题")
    turn = row.turns[-1]
    if turn.answer or turn.skipped:
        raise HTTPException(409, "当前问题已回答或已跳过")

    turn.skipped = True
    turn.feedback = body.reason.strip()[:300] or "跳过"
    await session.commit()
    await session.refresh(row, attribute_names=["turns"])
    return {"ok": True, "progress": _progress(row)}


@router.post("/{session_id}/report")
async def generate_report(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await session.get(InterviewSession, session_id)
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    if row.status == INTERVIEW_COMPLETED:
        raise HTTPException(409, "报告已生成")

    answered = await session.scalar(
        select(InterviewTurn.id)
        .where(InterviewTurn.session_id == session_id, InterviewTurn.answer != "")
        .limit(1)
    )
    if answered is None:
        raise HTTPException(400, "还没有任何回答，无法生成报告")

    run = Run(
        kind="interview_report",
        status=RUN_QUEUED,
        title=f"面试报告：{row.title}",
        target={"session_id": session_id},
        budget_tokens=80_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    row.report_run_id = run.id
    await session.commit()

    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"入队失败：{exc}") from exc
    return {"run_id": run.id}


@router.post("/{session_id}/abandon")
async def abandon(session_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(InterviewSession, session_id)
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    if row.status == INTERVIEW_COMPLETED:
        raise HTTPException(409, "已完成的会话不能放弃")
    row.status = INTERVIEW_ABANDONED
    await session.commit()
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(
    session_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(InterviewSession, session_id)
    if row is None:
        raise HTTPException(404, "面试会话不存在")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


def _follow_up_streak(turns: list[InterviewTurn]) -> int:
    streak = 0
    for t in reversed(turns):
        if t.kind == "follow_up":
            streak += 1
        else:
            break
    return streak


def _progress(row: InterviewSession) -> dict[str, Any]:
    turns = list(row.turns)
    plan_total = len((row.pact or {}).get("questions") or [])
    answered = sum(1 for t in turns if t.answer)
    return {
        "asked": len(turns),
        "primary_asked": sum(1 for t in turns if t.kind == "primary"),
        "plan_total": plan_total,
        "answered": answered,
        "skipped": sum(1 for t in turns if t.skipped),
        "remaining": max(0, plan_total - sum(1 for t in turns if t.kind == "primary")),
    }
