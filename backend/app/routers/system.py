from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import SessionLocal
from app.models.evaluation import Evaluation
from app.models.interview import InterviewSession
from app.models.job import Job
from app.models.project import Project, ProjectEvidence
from app.models.resume import Claim, ImportedResume, ResumeDraft, ResumeVersion

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    db_ok = True
    db_error = ""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)

    redis_ok = True
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        await client.ping()
        await client.aclose()
    except Exception:  # noqa: BLE001
        redis_ok = False

    return {
        "ok": db_ok,
        "db": {"ok": db_ok, "error": db_error},
        "redis": {"ok": redis_ok},
        "model": settings.llm_model,
    }


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """总览页数据：各模块计数、待办、最近活动。"""
    async with SessionLocal() as session:
        async def count(stmt) -> int:
            return int(await session.scalar(stmt) or 0)

        jobs_total = await count(select(func.count(Job.id)))
        jobs_by_status = dict(
            (
                await session.execute(
                    select(Job.status, func.count(Job.id)).group_by(Job.status)
                )
            ).all()
        )
        pending_parse = await count(
            select(func.count(Job.id)).where(Job.parse_status.in_(("pending", "running")))
        )

        drafts = await count(select(func.count(ResumeDraft.id)))
        versions = await count(select(func.count(ResumeVersion.id)))
        imported = await count(select(func.count(ImportedResume.id)))

        claims_total = await count(select(func.count(Claim.id)))
        claims_pending = await count(
            select(func.count(Claim.id)).where(Claim.status == "pending")
        )
        claims_confirmed = await count(
            select(func.count(Claim.id)).where(Claim.status == "confirmed")
        )

        projects = await count(select(func.count(Project.id)))
        evidence = await count(select(func.count(ProjectEvidence.id)))
        scanning = await count(
            select(func.count(Project.id)).where(Project.scan_status.in_(("pending", "running")))
        )

        evaluations = await count(select(func.count(Evaluation.id)))
        avg_eval = await session.scalar(
            select(func.avg(Evaluation.overall_score)).where(Evaluation.status == "succeeded")
        )

        interviews = await count(select(func.count(InterviewSession.id)))
        interviews_active = await count(
            select(func.count(InterviewSession.id)).where(InterviewSession.status == "active")
        )
        interviews_done = await count(
            select(func.count(InterviewSession.id)).where(InterviewSession.status == "completed")
        )
        avg_interview = await session.scalar(
            select(func.avg(InterviewSession.report["overall_score"].as_integer())).where(
                InterviewSession.status == "completed"
            )
        )

        recent_jobs = (
            await session.scalars(select(Job).order_by(Job.id.desc()).limit(5))
        ).all()
        recent_interviews = (
            await session.scalars(
                select(InterviewSession).order_by(InterviewSession.id.desc()).limit(5)
            )
        ).all()

    return {
        "jobs": {
            "total": jobs_total,
            "by_status": jobs_by_status,
            "pending_parse": pending_parse,
        },
        "resumes": {
            "drafts": drafts,
            "versions": versions,
            "imported": imported,
        },
        "claims": {
            "total": claims_total,
            "pending": claims_pending,
            "confirmed": claims_confirmed,
        },
        "projects": {
            "total": projects,
            "evidence": evidence,
            "scanning": scanning,
        },
        "evaluations": {
            "total": evaluations,
            "avg_score": round(float(avg_eval), 1) if avg_eval is not None else None,
        },
        "interviews": {
            "total": interviews,
            "active": interviews_active,
            "completed": interviews_done,
            "avg_score": round(float(avg_interview), 1) if avg_interview is not None else None,
        },
        "recent_jobs": [
            {
                "id": j.id,
                "title": j.title,
                "status": j.status,
                "parse_status": j.parse_status,
            }
            for j in recent_jobs
        ],
        "recent_interviews": [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "score": (i.report or {}).get("overall_score"),
            }
            for i in recent_interviews
        ],
    }
