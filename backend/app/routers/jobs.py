"""岗位库路由：原文 JD 入库、解析、状态标签。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.common import JOB_STATUSES, RUN_QUEUED
from app.models.harness import Run
from app.models.job import Job, RawJD
from app.queue import enqueue_run

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
settings = get_settings()


class CreateJobRequest(BaseModel):
    raw_text: str = Field(min_length=20)
    source_url: str = ""
    # 可选：用户手动填写的补充信息，解析前不回填任何字段
    title_hint: str = ""


class UpdateJobRequest(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    salary: str | None = None
    seniority: str | None = None
    status: str | None = None
    notes: str | None = None


class JobListItem(BaseModel):
    id: int
    title: str
    company: str
    location: str
    salary: str
    seniority: str
    status: str
    parse_status: str
    parse_error: str
    skills: list[str]
    created_at: Any
    updated_at: Any


class RawJDView(BaseModel):
    id: int
    content: str
    source_url: str
    char_count: int
    created_at: Any


class JobDetail(JobListItem):
    parsed: dict
    notes: str
    raw_jds: list[RawJDView]
    parse_run_id: int | None


@router.post("", response_model=JobDetail)
async def create_job(
    body: CreateJobRequest, session: AsyncSession = Depends(get_session)
) -> JobDetail:
    text = body.raw_text.strip()
    if len(text) < 20:
        raise HTTPException(400, "JD 内容太短")

    job = Job(title=body.title_hint.strip() or "未解析岗位", parse_status="pending")
    session.add(job)
    await session.flush()

    raw = RawJD(
        job_id=job.id,
        content=text,
        source_url=body.source_url.strip(),
        char_count=len(text),
    )
    session.add(raw)
    await session.flush()

    # 原文永久落盘，数据库之外再留一份
    rel = f"jobs/{job.id}/raw-{raw.id}.txt"
    path: Path = settings.data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    raw.file_path = rel

    await session.commit()
    await session.refresh(job, attribute_names=["raw_jds"])

    # 自动发起解析；入队失败不影响岗位创建，可手动重试
    try:
        run = await _enqueue_parse(session, job)
        job.parse_run_id = run.id
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        job.parse_status = "failed"
        job.parse_error = f"解析入队失败：{exc}"
        await session.commit()

    await session.refresh(job, attribute_names=["raw_jds"])
    return _detail(job)


@router.get("", response_model=list[JobListItem])
async def list_jobs(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="按标题/公司/技能搜索"),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[JobListItem]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    if status:
        if status not in JOB_STATUSES:
            raise HTTPException(400, f"未知状态: {status}")
        stmt = stmt.where(Job.status == status)
    rows = (await session.execute(stmt)).scalars().all()

    if q:
        needle = q.strip().lower()
        rows = [
            j
            for j in rows
            if needle in (j.title or "").lower()
            or needle in (j.company or "").lower()
            or any(needle in str(s).lower() for s in (j.parsed or {}).get("skills", []))
        ]
    return [_list_item(j) for j in rows]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)) -> JobDetail:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    await session.refresh(job, attribute_names=["raw_jds"])
    return _detail(job)


@router.patch("/{job_id}", response_model=JobDetail)
async def update_job(
    job_id: int, body: UpdateJobRequest, session: AsyncSession = Depends(get_session)
) -> JobDetail:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")

    if body.status is not None and body.status not in JOB_STATUSES:
        raise HTTPException(400, f"未知状态: {body.status}")

    for field in ("title", "company", "location", "salary", "seniority", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(job, field, value.strip())
    if body.status is not None:
        job.status = body.status

    await session.commit()
    await session.refresh(job, attribute_names=["raw_jds"])
    return _detail(job)


@router.post("/{job_id}/reparse", response_model=JobDetail)
async def reparse_job(job_id: int, session: AsyncSession = Depends(get_session)) -> JobDetail:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    if job.parse_status == "running":
        raise HTTPException(409, "该岗位正在解析")

    try:
        run = await _enqueue_parse(session, job)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"解析入队失败：{exc}") from exc

    job.parse_status = "pending"
    job.parse_error = ""
    job.parse_run_id = run.id
    await session.commit()
    await session.refresh(job, attribute_names=["raw_jds"])
    return _detail(job)


@router.delete("/{job_id}")
async def delete_job(job_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    import shutil

    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    await session.delete(job)
    await session.commit()

    # 原文目录随岗位一起清理，避免 data/jobs 下留下孤儿文件
    job_dir = settings.data_dir / "jobs" / str(job_id)
    if job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)
    return {"ok": True}


async def _enqueue_parse(session: AsyncSession, job: Job) -> Run:
    run = Run(
        kind="parse_job",
        status=RUN_QUEUED,
        title=f"解析岗位 #{job.id}",
        target={"job_id": job.id},
        budget_tokens=20_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    try:
        enqueue_run(run.id)
    except Exception:
        job.parse_status = "failed"
        job.parse_error = "Redis 未启动，无法投递解析任务"
        await session.commit()
        raise
    return run


def _skills(job: Job) -> list[str]:
    skills = (job.parsed or {}).get("skills") or []
    return [str(s) for s in skills]


def _list_item(job: Job) -> JobListItem:
    return JobListItem(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        salary=job.salary,
        seniority=job.seniority,
        status=job.status,
        parse_status=job.parse_status,
        parse_error=job.parse_error,
        skills=_skills(job),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _detail(job: Job) -> JobDetail:
    raws = [
        RawJDView(
            id=r.id,
            content=r.content,
            source_url=r.source_url,
            char_count=r.char_count,
            created_at=r.created_at,
        )
        for r in sorted(job.raw_jds, key=lambda r: r.id, reverse=True)
    ]
    return JobDetail(
        **_list_item(job).model_dump(),
        parsed=job.parsed or {},
        notes=job.notes,
        raw_jds=raws,
        parse_run_id=job.parse_run_id,
    )
