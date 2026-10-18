"""简历评价路由：发起、列表、详情、删除。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.evaluation_pipeline import evaluation_dict
from app.db import get_session
from app.models.common import RUN_QUEUED
from app.models.evaluation import Evaluation
from app.models.harness import Run
from app.models.resume import ResumeDraft
from app.queue import enqueue_run

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


class CreateEvaluation(BaseModel):
    draft_id: int


@router.post("")
async def create_evaluation(
    body: CreateEvaluation, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    draft = await session.get(ResumeDraft, body.draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    if not draft.resume_json:
        raise HTTPException(400, "草稿内容为空，无法评价")
    if draft.target_job_id is None:
        raise HTTPException(400, "评价必须绑定岗位，请先为该草稿设置目标岗位")

    evaluation = Evaluation(
        draft_id=draft.id,
        draft_revision=draft.revision,
        job_id=draft.target_job_id,
        status="pending",
        resume_snapshot=draft.resume_json,
    )
    session.add(evaluation)
    await session.commit()
    await session.refresh(evaluation)

    run = Run(
        kind="evaluate_resume",
        status=RUN_QUEUED,
        title=f"评价简历：{draft.title}",
        target={"evaluation_id": evaluation.id},
        budget_tokens=120_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    evaluation.run_id = run.id
    await session.commit()

    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        evaluation.status = "failed"
        evaluation.error = f"入队失败：{exc}"
        await session.commit()
        raise HTTPException(503, evaluation.error) from exc

    return {"evaluation_id": evaluation.id, "run_id": run.id}


@router.get("")
async def list_evaluations(
    draft_id: int | None = Query(default=None),
    job_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = select(Evaluation).order_by(Evaluation.id.desc()).limit(limit)
    if draft_id is not None:
        stmt = stmt.where(Evaluation.draft_id == draft_id)
    if job_id is not None:
        stmt = stmt.where(Evaluation.job_id == job_id)
    rows = (await session.scalars(stmt)).all()
    return [evaluation_dict(e, with_findings=False) for e in rows]


@router.get("/{evaluation_id}")
async def get_evaluation(
    evaluation_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    stmt = (
        select(Evaluation)
        .where(Evaluation.id == evaluation_id)
        .options(selectinload(Evaluation.findings))
    )
    evaluation = await session.scalar(stmt)
    if evaluation is None:
        raise HTTPException(404, "评价不存在")
    return evaluation_dict(evaluation)


@router.delete("/{evaluation_id}")
async def delete_evaluation(
    evaluation_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    evaluation = await session.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(404, "评价不存在")
    await session.delete(evaluation)
    await session.commit()
    return {"ok": True}
