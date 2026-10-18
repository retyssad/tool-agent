"""项目路由：CRUD、扫描、证据、导学、文件预览。"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.common import RUN_QUEUED
from app.models.harness import Run
from app.models.project import Project, ProjectEvidence, ProjectGuide
from app.models.resume import ClaimLedger, ResumeDraft, ResumeVersion
from app.queue import enqueue_run
from app.tools.scan import ScanError, assert_in_roots

router = APIRouter(prefix="/api/projects", tags=["projects"])
settings = get_settings()


class ProjectBody(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=3)
    role: str = ""
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None


class GuideRequest(BaseModel):
    kind: str = "interview"


def _summary(p: Project) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "path": p.path,
        "role": p.role,
        "description": p.description,
        "tech_stack": p.tech_stack or [],
        "scan_status": p.scan_status,
        "scan_error": p.scan_error,
        "scan_run_id": p.scan_run_id,
        "scan_summary": p.scan_summary or {},
        "last_scanned_at": p.last_scanned_at,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.scalars(select(Project).order_by(Project.id.desc()))).all()
    return [_summary(p) for p in rows]


@router.post("")
async def create_project(
    body: ProjectBody, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        resolved = assert_in_roots(Path(body.path), must_be_dir=True)
    except ScanError as exc:
        raise HTTPException(400, str(exc)) from exc

    existing = await session.scalar(select(Project).where(Project.path == str(resolved)))
    if existing is not None:
        raise HTTPException(409, f"该目录已添加过（项目 #{existing.id}）")

    project = Project(
        name=body.name.strip()[:200],
        path=str(resolved),
        role=body.role.strip()[:200],
        description=body.description.strip(),
    )
    session.add(project)
    await session.flush()
    # 每个项目一本独立账本，扫描出的证据可导入这里
    session.add(
        ClaimLedger(name=project.name, kind="project", project_id=project.id)
    )
    await session.commit()
    await session.refresh(project)
    return _summary(project)


@router.get("/{project_id}")
async def get_project(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")

    evidence = (
        await session.scalars(
            select(ProjectEvidence)
            .where(ProjectEvidence.project_id == project_id)
            .order_by(ProjectEvidence.id)
        )
    ).all()
    guides = (
        await session.scalars(
            select(ProjectGuide).where(ProjectGuide.project_id == project_id).order_by(ProjectGuide.id.desc())
        )
    ).all()

    analysis_path = settings.data_dir / "projects" / str(project_id) / "analysis.json"
    analysis: dict[str, Any] = {}
    if analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            analysis = {}

    return {
        **_summary(project),
        "evidence": [
            {
                "id": e.id,
                "statement": e.statement,
                "refs": e.refs or [],
                "module": e.module,
                "category": e.category,
                "capabilities": e.capabilities or [],
            }
            for e in evidence
        ],
        "guides": [
            {
                "id": g.id,
                "kind": g.kind,
                "title": g.title,
                "chars": len(g.content_md),
                "created_at": g.created_at,
            }
            for g in guides
        ],
        "interview_questions": analysis.get("interview_questions", []),
        "weak_spots": analysis.get("weak_spots", []),
        "skipped_dirs": (analysis.get("scout") or {}).get("skipped_dirs", []),
    }


@router.patch("/{project_id}")
async def patch_project(
    project_id: int, body: ProjectPatch, session: AsyncSession = Depends(get_session)
) -> dict:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    ledger = await session.scalar(
        select(ClaimLedger).where(
            ClaimLedger.project_id == project_id, ClaimLedger.kind == "project"
        )
    )
    if body.name is not None:
        new_name = body.name.strip()[:200] or project.name
        project.name = new_name
        if ledger is not None:
            ledger.name = new_name
    if body.role is not None:
        project.role = body.role.strip()[:200]
    if body.description is not None:
        project.description = body.description
    await session.commit()
    await session.refresh(project)
    return _summary(project)


@router.delete("/{project_id}")
async def delete_project(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")

    ledger = await session.scalar(
        select(ClaimLedger).where(
            ClaimLedger.project_id == project_id, ClaimLedger.kind == "project"
        )
    )
    if ledger is not None:
        used = await session.scalar(
            select(func.count(ResumeDraft.id)).where(ResumeDraft.ledger_id == ledger.id)
        )
        used = (used or 0) + (
            await session.scalar(
                select(func.count(ResumeVersion.id)).where(ResumeVersion.ledger_id == ledger.id)
            )
            or 0
        )
        if used:
            # 账本已被简历引用：保留事实，脱钩为普通账本，避免简历失去依据
            ledger.kind = "personal"
            ledger.project_id = None
            ledger.name = f"已归档：{ledger.name}"[:200]
        else:
            await session.delete(ledger)

    await session.delete(project)
    await session.commit()
    folder = settings.data_dir / "projects" / str(project_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


@router.post("/{project_id}/scan")
async def start_scan(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")

    # 只有确实存在活跃 run 时才拒绝；扫帚状态可能是进程崩溃遗留
    if project.scan_status in ("running", "pending"):
        active = await session.scalar(
            select(Run.id).where(
                Run.id == project.scan_run_id,
                Run.status.in_(("queued", "running")),
            )
        )
        if active is not None:
            raise HTTPException(409, "该项目正在扫描")
        project.scan_status = "failed"
        project.scan_error = "上次扫描未正常结束，可重新发起"
        await session.commit()

    # 入队前先校验路径，避免任务排队后才失败
    try:
        assert_in_roots(Path(project.path), must_be_dir=True)
    except ScanError as exc:
        project.scan_status = "failed"
        project.scan_error = str(exc)
        await session.commit()
        raise HTTPException(400, str(exc)) from exc

    run = Run(
        kind="scan_project",
        status=RUN_QUEUED,
        title=f"扫描项目：{project.name}",
        target={"project_id": project.id},
        budget_tokens=200_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    project.scan_status = "pending"
    project.scan_error = ""
    project.scan_run_id = run.id
    await session.commit()
    await session.refresh(project)

    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        project.scan_status = "failed"
        project.scan_error = f"入队失败：{exc}"
        await session.commit()
        raise HTTPException(503, project.scan_error) from exc

    return {"run_id": run.id, **_summary(project)}


@router.post("/{project_id}/guide")
async def create_guide(
    project_id: int, body: GuideRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    if body.kind not in {"interview", "course"}:
        raise HTTPException(400, "kind 只能是 interview 或 course")

    count = await session.scalar(
        select(ProjectEvidence.id).where(ProjectEvidence.project_id == project_id).limit(1)
    )
    if count is None:
        raise HTTPException(400, "请先扫描项目")

    run = Run(
        kind="project_guide",
        status=RUN_QUEUED,
        title=f"生成导学：{project.name}",
        target={"project_id": project_id, "kind": body.kind},
        budget_tokens=120_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"入队失败：{exc}") from exc
    return {"run_id": run.id}


@router.get("/{project_id}/guides/{guide_id}")
async def get_guide(
    project_id: int, guide_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    guide = await session.get(ProjectGuide, guide_id)
    if guide is None or guide.project_id != project_id:
        raise HTTPException(404, "导学不存在")
    return {
        "id": guide.id,
        "kind": guide.kind,
        "title": guide.title,
        "content_md": guide.content_md,
        "created_at": guide.created_at,
    }


@router.get("/{project_id}/evidence/{evidence_id}/source")
async def evidence_source(
    project_id: int, evidence_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """返回证据引用的代码片段，供前端校验 file:line 是否真实。"""
    project = await session.get(Project, project_id)
    evidence = await session.get(ProjectEvidence, evidence_id)
    if project is None or evidence is None or evidence.project_id != project_id:
        raise HTTPException(404, "证据不存在")

    root = Path(project.path)
    files = []
    for ref in (evidence.refs or [])[:4]:
        rel = str(ref.get("path") or "")
        line = int(ref.get("line") or 0)
        try:
            target = assert_in_roots(root / rel)
        except ScanError:
            files.append({"path": rel, "exists": False, "line": line, "content": ""})
            continue
        if not target.is_file():
            files.append({"path": rel, "exists": False, "line": line, "content": ""})
            continue
        try:
            raw_text = await asyncio.to_thread(
                target.read_text, encoding="utf-8", errors="replace"
            )
            lines = raw_text.splitlines()
        except OSError:
            files.append({"path": rel, "exists": False, "line": line, "content": ""})
            continue
        start = max(0, line - 4)
        end = min(len(lines), line + 4)
        files.append(
            {
                "path": rel,
                "exists": True,
                "line": line,
                "total_lines": len(lines),
                "excerpt": "\n".join(
                    f"{i + 1:>5} | {lines[i]}" for i in range(start, end)
                ),
            }
        )
    return {"evidence_id": evidence_id, "files": files}


@router.get("/{project_id}/file", response_class=PlainTextResponse)
async def read_file(
    project_id: int,
    path: str = Query(min_length=1),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    try:
        target = assert_in_roots(Path(project.path) / path)
    except ScanError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    if target.stat().st_size > 300_000:
        raise HTTPException(413, "文件过大")
    text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
    return PlainTextResponse(text)
