"""生成简历：账本、证据条目、草稿、版本、导出。"""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.common import CLAIM_CONFIRMED, CLAIM_PENDING, RUN_QUEUED
from app.models.harness import Run
from app.models.project import Project, ProjectEvidence
from app.models.resume import CandidateProfile, Claim, ClaimLedger, ResumeDraft, ResumeVersion
from app.queue import enqueue_run
from app.tools import gate, render
from app.tools.export import ExportError, export_pdf
from app.tools.templates import DEFAULT_TEMPLATE, get_template, list_templates

router = APIRouter(prefix="/api", tags=["generated-resumes"])
settings = get_settings()

CLAIM_STATUSES = {"pending", "confirmed", "expired", "rejected"}
CLAIM_KINDS = {
    "experience", "project", "education", "skill", "campus", "award", "other",
}


# ---------- 模板 ----------

@router.get("/templates")
async def get_templates() -> list[dict]:
    return list_templates()


# ---------- 基本信息 ----------

class ProfileBody(BaseModel):
    name: str = ""
    headline: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""
    wechat: str = ""
    birth_year: str = ""
    political_status: str = ""
    english_level: str = ""
    github: str = ""


def _profile_dict(p: CandidateProfile | None) -> dict[str, Any]:
    if p is None:
        return ProfileBody().model_dump()
    return {k: getattr(p, k) for k in ProfileBody.model_fields}


@router.get("/profile")
async def get_profile(session: AsyncSession = Depends(get_session)) -> dict:
    return _profile_dict(await session.get(CandidateProfile, 1))


@router.put("/profile")
async def put_profile(
    body: ProfileBody, session: AsyncSession = Depends(get_session)
) -> dict:
    profile = await session.get(CandidateProfile, 1)
    if profile is None:
        profile = CandidateProfile(id=1)
        session.add(profile)
    for key, value in body.model_dump().items():
        setattr(profile, key, value.strip())
    await session.commit()
    return _profile_dict(profile)


# ---------- 账本 ----------

class LedgerBody(BaseModel):
    name: str = "默认账本"
    kind: str = "personal"


class LedgerPatch(BaseModel):
    name: str

LEDGER_KINDS = {"personal", "project"}


class ClaimBody(BaseModel):
    statement: str = Field(min_length=1)
    kind: str = "other"
    organization: str = ""
    role: str = ""
    period: str = ""
    tags: list[str] = Field(default_factory=list)
    detail: str = ""
    status: str = CLAIM_PENDING


class ClaimPatch(BaseModel):
    statement: str | None = None
    kind: str | None = None
    organization: str | None = None
    role: str | None = None
    period: str | None = None
    tags: list[str] | None = None
    detail: str | None = None
    status: str | None = None
    variants: list[str] | None = None


class BulkStatus(BaseModel):
    claim_ids: list[int]
    status: str


def _claim_dict(c: Claim) -> dict[str, Any]:
    return {
        "id": c.id,
        "ledger_id": c.ledger_id,
        "statement": c.statement,
        "source_type": c.source_type,
        "source_ref": c.source_ref,
        "kind": c.kind,
        "organization": c.organization,
        "role": c.role,
        "period": c.period,
        "tags": c.tags or [],
        "variants": c.variants or [],
        "status": c.status,
        "detail": c.detail,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


@router.get("/ledgers")
async def list_ledgers(
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(ClaimLedger).order_by(ClaimLedger.id)
    if kind is not None:
        if kind not in LEDGER_KINDS:
            raise HTTPException(400, f"未知账本类型: {kind}")
        stmt = stmt.where(ClaimLedger.kind == kind)
    rows = (await session.scalars(stmt)).all()
    counts = dict(
        (
            await session.execute(
                select(Claim.ledger_id, func.count(Claim.id)).group_by(Claim.ledger_id)
            )
        ).all()
    )
    confirmed = dict(
        (
            await session.execute(
                select(Claim.ledger_id, func.count(Claim.id))
                .where(Claim.status == CLAIM_CONFIRMED)
                .group_by(Claim.ledger_id)
            )
        ).all()
    )
    return [
        {
            "id": l.id,
            "name": l.name,
            "kind": l.kind,
            "project_id": l.project_id,
            "claim_count": counts.get(l.id, 0),
            "confirmed_count": confirmed.get(l.id, 0),
            "created_at": l.created_at,
            "updated_at": l.updated_at,
        }
        for l in rows
    ]


@router.post("/ledgers")
async def create_ledger(
    body: LedgerBody, session: AsyncSession = Depends(get_session)
) -> dict:
    if body.kind not in LEDGER_KINDS:
        raise HTTPException(400, f"未知账本类型: {body.kind}")
    if body.kind == "project":
        raise HTTPException(400, "项目账本由项目自动创建")
    ledger = ClaimLedger(name=body.name.strip() or "默认账本", kind="personal")
    session.add(ledger)
    await session.commit()
    await session.refresh(ledger)
    return {
        "id": ledger.id,
        "name": ledger.name,
        "kind": ledger.kind,
        "project_id": None,
        "claim_count": 0,
        "confirmed_count": 0,
    }


@router.patch("/ledgers/{ledger_id}")
async def rename_ledger(
    ledger_id: int, body: LedgerPatch, session: AsyncSession = Depends(get_session)
) -> dict:
    ledger = await session.get(ClaimLedger, ledger_id)
    if ledger is None:
        raise HTTPException(404, "账本不存在")
    ledger.name = body.name.strip() or ledger.name
    await session.commit()
    await session.refresh(ledger)
    return {
        "id": ledger.id,
        "name": ledger.name,
        "kind": ledger.kind,
        "project_id": ledger.project_id,
    }


@router.delete("/ledgers/{ledger_id}")
async def delete_ledger(ledger_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    ledger = await session.get(ClaimLedger, ledger_id)
    if ledger is None:
        raise HTTPException(404, "账本不存在")
    if ledger.kind == "project":
        raise HTTPException(400, "项目账本请通过删除项目来清理")
    used = await session.scalar(
        select(func.count(ResumeDraft.id)).where(ResumeDraft.ledger_id == ledger_id)
    )
    used = (used or 0) + (
        await session.scalar(
            select(func.count(ResumeVersion.id)).where(ResumeVersion.ledger_id == ledger_id)
        )
        or 0
    )
    if used:
        raise HTTPException(409, "该账本仍被简历草稿或版本引用，不能删除")
    await session.delete(ledger)
    await session.commit()
    return {"ok": True}


@router.get("/ledgers/{ledger_id}/claims")
async def list_claims(
    ledger_id: int,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if await session.get(ClaimLedger, ledger_id) is None:
        raise HTTPException(404, "账本不存在")
    stmt = select(Claim).where(Claim.ledger_id == ledger_id).order_by(Claim.id)
    if status:
        stmt = stmt.where(Claim.status == status)
    if kind:
        stmt = stmt.where(Claim.kind == kind)
    return [_claim_dict(c) for c in (await session.scalars(stmt)).all()]


@router.post("/ledgers/{ledger_id}/claims")
async def create_claim(
    ledger_id: int, body: ClaimBody, session: AsyncSession = Depends(get_session)
) -> dict:
    if await session.get(ClaimLedger, ledger_id) is None:
        raise HTTPException(404, "账本不存在")
    if body.kind not in CLAIM_KINDS:
        raise HTTPException(400, f"未知类型: {body.kind}")
    if body.status not in CLAIM_STATUSES:
        raise HTTPException(400, f"未知状态: {body.status}")
    claim = Claim(
        ledger_id=ledger_id,
        statement=body.statement.strip(),
        source_type="manual",
        kind=body.kind,
        organization=body.organization.strip(),
        role=body.role.strip(),
        period=body.period.strip(),
        tags=[t.strip() for t in body.tags if t.strip()][:12],
        detail=body.detail,
        status=body.status,
    )
    session.add(claim)
    await session.commit()
    await session.refresh(claim)
    return _claim_dict(claim)


@router.patch("/claims/{claim_id}")
async def patch_claim(
    claim_id: int, body: ClaimPatch, session: AsyncSession = Depends(get_session)
) -> dict:
    claim = await session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(404, "证据条目不存在")
    if body.kind is not None:
        if body.kind not in CLAIM_KINDS:
            raise HTTPException(400, f"未知类型: {body.kind}")
        claim.kind = body.kind
    if body.status is not None:
        if body.status not in CLAIM_STATUSES:
            raise HTTPException(400, f"未知状态: {body.status}")
        claim.status = body.status
    for field in ("statement", "organization", "role", "period", "detail"):
        value = getattr(body, field)
        if value is not None:
            setattr(claim, field, value.strip() if field != "detail" else value)
    if body.tags is not None:
        claim.tags = [t.strip() for t in body.tags if t.strip()][:12]
    if body.variants is not None:
        claim.variants = [v.strip() for v in body.variants if v.strip()][:8]
    await session.commit()
    await session.refresh(claim)
    return _claim_dict(claim)


@router.post("/claims/bulk-status")
async def bulk_status(
    body: BulkStatus, session: AsyncSession = Depends(get_session)
) -> dict:
    if body.status not in CLAIM_STATUSES:
        raise HTTPException(400, f"未知状态: {body.status}")
    if not body.claim_ids:
        return {"updated": 0}
    claims = (
        await session.scalars(select(Claim).where(Claim.id.in_(body.claim_ids)))
    ).all()
    for c in claims:
        c.status = body.status
    await session.commit()
    return {"updated": len(claims)}


@router.delete("/claims/{claim_id}")
async def delete_claim(claim_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    claim = await session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(404, "证据条目不存在")
    await session.delete(claim)
    await session.commit()
    return {"ok": True}


# ---------- 项目证据 → 账本 ----------

class ImportEvidenceBody(BaseModel):
    project_id: int
    evidence_ids: list[int] = Field(default_factory=list)


class CopyClaimsBody(BaseModel):
    claim_ids: list[int] = Field(default_factory=list)


@router.post("/ledgers/{ledger_id}/import-project-evidence")
async def import_project_evidence(
    ledger_id: int, body: ImportEvidenceBody, session: AsyncSession = Depends(get_session)
) -> dict:
    """把项目扫描证据导入指定账本，默认待确认。"""
    ledger = await session.get(ClaimLedger, ledger_id)
    if ledger is None:
        raise HTTPException(404, "账本不存在")
    project = await session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")

    stmt = select(ProjectEvidence).where(ProjectEvidence.project_id == project.id)
    if body.evidence_ids:
        stmt = stmt.where(ProjectEvidence.id.in_(body.evidence_ids))
    evidence = (await session.scalars(stmt.order_by(ProjectEvidence.id))).all()

    existing = set(
        (
            await session.scalars(
                select(Claim.source_ref).where(
                    Claim.ledger_id == ledger_id,
                    Claim.project_id == project.id,
                )
            )
        ).all()
    )

    created = 0
    skipped = 0
    for item in evidence:
        refs = item.refs or []
        first = refs[0] if refs and isinstance(refs[0], dict) else {}
        source_ref = f"{first.get('path', '')}:{first.get('line', 0)}" if first else ""
        if source_ref and source_ref in existing:
            skipped += 1
            continue
        session.add(
            Claim(
                ledger_id=ledger_id,
                statement=item.statement,
                source_type="project",
                source_ref=source_ref,
                project_id=project.id,
                kind="project",
                organization=project.name,
                tags=(item.capabilities or [])[:12],
                status=CLAIM_PENDING,
            )
        )
        if source_ref:
            existing.add(source_ref)
        created += 1

    await session.commit()
    return {"created": created, "skipped": skipped}


@router.post("/ledgers/{ledger_id}/copy-claims")
async def copy_claims(
    ledger_id: int, body: CopyClaimsBody, session: AsyncSession = Depends(get_session)
) -> dict:
    """把其它账本的条目复制过来（项目账本 → 个人账本），保留状态。"""
    target = await session.get(ClaimLedger, ledger_id)
    if target is None:
        raise HTTPException(404, "账本不存在")
    if not body.claim_ids:
        return {"created": 0, "skipped": 0}

    sources = (
        await session.scalars(select(Claim).where(Claim.id.in_(body.claim_ids)))
    ).all()
    existing = set(
        (
            await session.scalars(
                select(Claim.statement).where(Claim.ledger_id == ledger_id)
            )
        ).all()
    )

    created = 0
    skipped = 0
    for src in sources:
        if src.ledger_id == ledger_id or src.statement in existing:
            skipped += 1
            continue
        session.add(
            Claim(
                ledger_id=ledger_id,
                statement=src.statement,
                source_type=src.source_type,
                source_ref=src.source_ref,
                project_id=src.project_id,
                kind=src.kind,
                organization=src.organization,
                role=src.role,
                period=src.period,
                tags=list(src.tags or []),
                variants=list(src.variants or []),
                detail=src.detail,
                status=src.status,
            )
        )
        existing.add(src.statement)
        created += 1

    await session.commit()
    return {"created": created, "skipped": skipped}


# ---------- 从导入简历提炼 ----------

class ExtractBody(BaseModel):
    imported_resume_id: int
    ledger_id: int | None = None


@router.post("/claims/extract-from-resume")
async def extract_from_resume(
    body: ExtractBody, session: AsyncSession = Depends(get_session)
) -> dict:
    ledger_id = body.ledger_id
    if ledger_id is None:
        ledger = await session.scalar(select(ClaimLedger).order_by(ClaimLedger.id))
        if ledger is None:
            ledger = ClaimLedger(name="默认账本")
            session.add(ledger)
            await session.commit()
            await session.refresh(ledger)
        ledger_id = ledger.id

    run = Run(
        kind="extract_claims",
        status=RUN_QUEUED,
        title=f"从简历 #{body.imported_resume_id} 提炼证据",
        target={"imported_resume_id": body.imported_resume_id, "ledger_id": ledger_id},
        budget_tokens=40_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"入队失败：{exc}") from exc
    return {"run_id": run.id, "ledger_id": ledger_id}


# ---------- 草稿 ----------

class DraftBody(BaseModel):
    ledger_id: int
    target_job_id: int | None = None
    title: str = "新建简历"
    template: str = DEFAULT_TEMPLATE


class DraftPatch(BaseModel):
    title: str | None = None
    template: str | None = None
    resume_json: dict | None = None


def _draft_dict(d: ResumeDraft, gate_result: dict | None = None) -> dict[str, Any]:
    return {
        "id": d.id,
        "ledger_id": d.ledger_id,
        "target_job_id": d.target_job_id,
        "title": d.title,
        "template": d.template,
        "resume_json": d.resume_json or {},
        "revision": d.revision,
        "run_id": d.run_id,
        "gate": gate_result,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


@router.get("/drafts")
async def list_drafts(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.scalars(select(ResumeDraft).order_by(ResumeDraft.id.desc()))).all()
    return [
        {
            "id": d.id,
            "title": d.title,
            "template": d.template,
            "target_job_id": d.target_job_id,
            "ledger_id": d.ledger_id,
            "revision": d.revision,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }
        for d in rows
    ]


@router.post("/drafts")
async def create_draft(
    body: DraftBody, session: AsyncSession = Depends(get_session)
) -> dict:
    if await session.get(ClaimLedger, body.ledger_id) is None:
        raise HTTPException(404, "账本不存在")
    get_template(body.template)  # 提前校验模板 key
    draft = ResumeDraft(
        ledger_id=body.ledger_id,
        target_job_id=body.target_job_id,
        title=body.title.strip() or "新建简历",
        template=body.template,
        resume_json={},
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return _draft_dict(draft)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    draft = await session.get(ResumeDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    # 空草稿还没有任何内容，不跑门禁，避免一进编辑器就报"缺少姓名"
    if not (draft.resume_json or {}):
        return _draft_dict(draft, None)
    claims = (
        await session.scalars(select(Claim).where(Claim.ledger_id == draft.ledger_id))
    ).all()
    result = gate.check(draft.resume_json or {}, list(claims))
    return _draft_dict(draft, result.as_dict())


@router.patch("/drafts/{draft_id}")
async def patch_draft(
    draft_id: int, body: DraftPatch, session: AsyncSession = Depends(get_session)
) -> dict:
    draft = await session.get(ResumeDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    if body.title is not None:
        draft.title = body.title.strip() or draft.title
    if body.template is not None:
        get_template(body.template)
        draft.template = body.template
    if body.resume_json is not None:
        draft.resume_json = body.resume_json
        draft.revision += 1
    await session.commit()
    await session.refresh(draft)
    claims = (
        await session.scalars(select(Claim).where(Claim.ledger_id == draft.ledger_id))
    ).all()
    return _draft_dict(draft, gate.check(draft.resume_json or {}, list(claims)).as_dict())


@router.post("/drafts/{draft_id}/generate")
async def generate_draft(draft_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    draft = await session.get(ResumeDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    run = Run(
        kind="generate_resume",
        status=RUN_QUEUED,
        title=f"生成简历：{draft.title}",
        target={"draft_id": draft.id},
        budget_tokens=40_000,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    draft.run_id = run.id
    await session.commit()
    try:
        enqueue_run(run.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"入队失败：{exc}") from exc
    return {"run_id": run.id}


@router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    draft = await session.get(ResumeDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    await session.delete(draft)
    await session.commit()
    return {"ok": True}


@router.get("/drafts/{draft_id}/preview", response_class=HTMLResponse)
async def preview_draft(
    draft_id: int,
    template: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    draft = await session.get(ResumeDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    html = render.render(draft.resume_json or {}, template or draft.template)
    return HTMLResponse(html)


# ---------- 导出 ----------

@router.post("/drafts/{draft_id}/export")
async def export_draft(
    draft_id: int,
    template: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    draft = await session.get(ResumeDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")

    resume = draft.resume_json or {}
    claims = (
        await session.scalars(select(Claim).where(Claim.ledger_id == draft.ledger_id))
    ).all()
    result = gate.check(resume, list(claims))
    if not result.ok:
        raise HTTPException(
            409,
            {
                "message": "证据门禁未通过，无法导出",
                "errors": result.errors,
            },
        )

    tpl = template or draft.template
    try:
        get_template(tpl)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    version_no = (
        await session.scalar(
            select(func.coalesce(func.max(ResumeVersion.version_no), 0)).where(
                ResumeVersion.ledger_id == draft.ledger_id,
                ResumeVersion.target_job_id == draft.target_job_id,
            )
        )
    ) or 0

    version = ResumeVersion(
        ledger_id=draft.ledger_id,
        target_job_id=draft.target_job_id,
        title=draft.title,
        template=tpl,
        gate_status="passed",
        gate_errors=[],
        version_no=int(version_no) + 1,
        run_id=draft.run_id,
    )
    session.add(version)
    await session.flush()  # 先拿到版本 id，用于独立目录

    # 每个版本一个独立目录，后续导出/重渲染不会覆盖历史版本
    folder = settings.data_dir / "resumes" / "generated" / f"version-{version.id}"
    rel = f"resumes/generated/version-{version.id}"
    try:
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        json_path = folder / "resume.json"
        html_path = folder / "index.html"
        await asyncio.to_thread(
            json_path.write_text, json.dumps(resume, ensure_ascii=False, indent=2), "utf-8"
        )
        html = render.render(resume, tpl)
        await asyncio.to_thread(html_path.write_text, html, encoding="utf-8")
        pdf_path = folder / "resume.pdf"
        await asyncio.to_thread(export_pdf, html_path, pdf_path)
    except ExportError as exc:
        await session.rollback()
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, str(exc)) from exc
    except Exception:
        await session.rollback()
        shutil.rmtree(folder, ignore_errors=True)
        raise

    version.resume_json_path = f"{rel}/resume.json"
    version.html_path = f"{rel}/index.html"
    version.pdf_path = f"{rel}/resume.pdf"
    await session.commit()
    await session.refresh(version)

    return {
        "version_id": version.id,
        "version_no": version.version_no,
        "pdf_url": f"/api/versions/{version.id}/pdf",
        "html_url": f"/api/versions/{version.id}/html",
        "gate": result.as_dict(),
    }


# ---------- 版本 ----------

@router.get("/versions")
async def list_versions(
    job_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(ResumeVersion).order_by(ResumeVersion.id.desc())
    if job_id is not None:
        stmt = stmt.where(ResumeVersion.target_job_id == job_id)
    rows = (await session.scalars(stmt)).all()
    return [
        {
            "id": v.id,
            "title": v.title,
            "template": v.template,
            "target_job_id": v.target_job_id,
            "ledger_id": v.ledger_id,
            "version_no": v.version_no,
            "gate_status": v.gate_status,
            "created_at": v.created_at,
            "pdf_url": f"/api/versions/{v.id}/pdf",
            "html_url": f"/api/versions/{v.id}/html",
        }
        for v in rows
    ]


@router.get("/versions/{version_id}")
async def get_version(version_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    v = await session.get(ResumeVersion, version_id)
    if v is None:
        raise HTTPException(404, "版本不存在")
    path = settings.data_dir / v.resume_json_path
    resume = {}
    if path.is_file():
        resume = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    return {
        "id": v.id,
        "title": v.title,
        "template": v.template,
        "target_job_id": v.target_job_id,
        "ledger_id": v.ledger_id,
        "version_no": v.version_no,
        "gate_status": v.gate_status,
        "gate_errors": v.gate_errors or [],
        "resume_json": resume,
        "created_at": v.created_at,
        "pdf_url": f"/api/versions/{v.id}/pdf",
        "html_url": f"/api/versions/{v.id}/html",
    }


@router.get("/versions/{version_id}/pdf")
async def version_pdf(
    version_id: int,
    download: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
):
    v = await session.get(ResumeVersion, version_id)
    if v is None:
        raise HTTPException(404, "版本不存在")
    path = (settings.data_dir / v.pdf_path).resolve()
    if not path.is_file():
        raise HTTPException(404, "PDF 已丢失，请重新导出")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{v.title}-v{v.version_no}.pdf",
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/versions/{version_id}/html", response_class=HTMLResponse)
async def version_html(version_id: int, session: AsyncSession = Depends(get_session)):
    v = await session.get(ResumeVersion, version_id)
    if v is None:
        raise HTTPException(404, "版本不存在")
    path = (settings.data_dir / v.html_path).resolve()
    if not path.is_file():
        raise HTTPException(404, "HTML 已丢失")
    return HTMLResponse(await asyncio.to_thread(path.read_text, encoding="utf-8"))


@router.post("/versions/{version_id}/re-render")
async def rerender_version(
    version_id: int,
    template: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """换模板重渲染同一份 resume.json，不产生新版本。"""
    v = await session.get(ResumeVersion, version_id)
    if v is None:
        raise HTTPException(404, "版本不存在")
    try:
        get_template(template)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    path = settings.data_dir / v.resume_json_path
    if not path.is_file():
        raise HTTPException(404, "resume.json 已丢失")
    resume = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))

    html_path = settings.data_dir / v.html_path
    html = render.render(resume, template)
    await asyncio.to_thread(html_path.write_text, html, encoding="utf-8")
    pdf_path = settings.data_dir / v.pdf_path
    try:
        await asyncio.to_thread(export_pdf, html_path, pdf_path)
    except ExportError as exc:
        raise HTTPException(500, str(exc)) from exc

    v.template = template
    await session.commit()
    return {"ok": True, "template": template}


@router.delete("/versions/{version_id}")
async def delete_version(version_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    v = await session.get(ResumeVersion, version_id)
    if v is None:
        raise HTTPException(404, "版本不存在")

    generated_root = (settings.data_dir / "resumes" / "generated").resolve()
    folder = (settings.data_dir / v.resume_json_path).resolve().parent
    shared = await session.scalar(
        select(func.count(ResumeVersion.id)).where(
            ResumeVersion.id != v.id,
            ResumeVersion.resume_json_path == v.resume_json_path,
        )
    )
    await session.delete(v)
    await session.commit()

    # 旧数据可能多个版本共用同一目录：只在该目录不再被任何版本引用时清理
    if not shared and folder.is_dir() and folder.is_relative_to(generated_root):
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}
