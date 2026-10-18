"""证据账本：CRUD、批量确认、从导入简历提炼。"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import delete, select

from app.agents.claim_extractor import extract_claims_from_resume
from app.config import get_settings
from app.harness.executor import RunHandle
from app.models.resume import Claim, ClaimLedger, ImportedResume, ResumeVersion
from app.tasks import register

settings = get_settings()


@register("extract_claims")
async def extract_claims(handle: RunHandle) -> dict[str, Any]:
    """从导入简历抽取证据条目，全部落到 pending。"""
    from app.db import SessionLocal

    resume_id = int(handle.target.get("imported_resume_id") or 0)
    ledger_id = int(handle.target.get("ledger_id") or 0)
    if not resume_id or not ledger_id:
        raise ValueError("缺少 imported_resume_id 或 ledger_id")

    async with SessionLocal() as session:
        imported = await session.get(ImportedResume, resume_id)
        ledger = await session.get(ClaimLedger, ledger_id)
        if imported is None or ledger is None:
            raise ValueError("导入简历或账本不存在")

        text_path = settings.data_dir / imported.text_path
        if not text_path.is_file():
            raise ValueError("抽取文本已丢失")
        text = await asyncio.to_thread(text_path.read_text, encoding="utf-8")

        existing = (
            await session.scalars(
                select(Claim.statement).where(Claim.ledger_id == ledger_id)
            )
        ).all()
        await handle.log(f"开始从简历 #{resume_id} 提炼证据，已有 {len(existing)} 条")

        items = await extract_claims_from_resume(
            handle,
            text=text,
            existing_statements=list(existing),
            source_ref=f"imported_resume#{resume_id}",
        )

        created = []
        for item in items:
            claim = Claim(
                ledger_id=ledger_id,
                statement=item["statement"],
                source_type="resume_import",
                source_ref=f"imported_resume#{resume_id}",
                kind=item.get("kind", "other"),
                organization=item.get("organization", ""),
                role=item.get("role", ""),
                period=item.get("period", ""),
                tags=item.get("tags", []),
                status="pending",
            )
            session.add(claim)
            await session.flush()
            created.append(claim.id)
        await session.commit()

        await handle.log(f"提炼完成，新增 {len(created)} 条待确认")
        return {"created": len(created), "claim_ids": created, "ledger_id": ledger_id}


@register("generate_resume")
async def generate_resume(handle: RunHandle) -> dict[str, Any]:
    """基于已确认证据生成 resume.json，写入草稿。"""
    from app.agents.resume_writer import write_resume
    from app.db import SessionLocal
    from app.models.resume import CandidateProfile, ResumeDraft

    draft_id = int(handle.target.get("draft_id") or 0)
    if not draft_id:
        raise ValueError("缺少 draft_id")

    async with SessionLocal() as session:
        draft = await session.get(ResumeDraft, draft_id)
        if draft is None:
            raise ValueError("草稿不存在")
        job = None
        if draft.target_job_id:
            from app.models.job import Job

            job = await session.get(Job, draft.target_job_id)
        profile = await session.get(CandidateProfile, 1)
        claims = (
            await session.scalars(select(Claim).where(Claim.ledger_id == draft.ledger_id))
        ).all()
        confirmed = [c for c in claims if c.status == "confirmed"]
        if not confirmed:
            raise ValueError("账本里没有已确认的证据条目")

        resume = await write_resume(
            handle,
            profile=profile,
            claims=confirmed,
            job=job,
            template=draft.template,
        )
        draft.resume_json = resume
        draft.revision += 1
        draft.run_id = handle.run_id
        await session.commit()

        await handle.log(f"已生成草稿 #{draft_id}，revision {draft.revision}")
        return {"draft_id": draft_id, "revision": draft.revision}


def claim_summary(claim: Claim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "ledger_id": claim.ledger_id,
        "statement": claim.statement,
        "source_type": claim.source_type,
        "source_ref": claim.source_ref,
        "kind": claim.kind,
        "organization": claim.organization,
        "role": claim.role,
        "period": claim.period,
        "tags": claim.tags or [],
        "status": claim.status,
        "detail": claim.detail,
        "variants": claim.variants or [],
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
    }
