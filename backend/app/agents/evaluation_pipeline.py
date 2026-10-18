"""评价任务的 run handler。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.agents.evaluator import aggregate, run_all_critics
from app.harness import events as ev
from app.harness.executor import RunHandle
from app.models.evaluation import Evaluation, EvaluationFinding
from app.models.job import Job
from app.models.resume import Claim, ResumeDraft
from app.tasks import register


@register("evaluate_resume")
async def evaluate_resume(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    evaluation_id = int(handle.target.get("evaluation_id") or 0)
    if not evaluation_id:
        raise ValueError("缺少 evaluation_id")

    async with SessionLocal() as session:
        evaluation = await session.get(Evaluation, evaluation_id)
        if evaluation is None:
            raise ValueError(f"评价记录不存在: {evaluation_id}")
        draft = await session.get(ResumeDraft, evaluation.draft_id)
        if draft is None:
            raise ValueError("简历草稿不存在")

        resume = draft.resume_json or {}
        if not resume:
            raise ValueError("草稿内容为空，无法评价")

        job = await session.get(Job, draft.target_job_id) if draft.target_job_id else None
        job_payload = {
            "title": job.title if job else "",
            "company": job.company if job else "",
            "location": job.location if job else "",
            "seniority": job.seniority if job else "",
            "hard_requirements": (job.parsed or {}).get("hard_requirements", []) if job else [],
            "responsibilities": (job.parsed or {}).get("responsibilities", []) if job else [],
            "skills": (job.parsed or {}).get("skills", []) if job else [],
            "nice_to_have": (job.parsed or {}).get("nice_to_have", []) if job else [],
        }

        claims = (
            await session.scalars(select(Claim).where(Claim.ledger_id == draft.ledger_id))
        ).all()
        claims_digest = "\n".join(f"[{c.id}] {c.statement}" for c in claims[:60]) or "（无）"

        evaluation.status = "running"
        evaluation.draft_revision = draft.revision
        evaluation.job_id = draft.target_job_id
        evaluation.resume_snapshot = resume
        evaluation.job_snapshot = job_payload
        await session.commit()

        await handle.emit(
            ev.AGENT_STARTED,
            {"agent": "evaluator", "critics": 4, "draft_id": draft.id},
        )
        try:
            results = await run_all_critics(
                handle, resume=resume, job=job_payload, claims_digest=claims_digest
            )
            report = aggregate(results)

            evaluation.overall_score = report["overall_score"]
            evaluation.dimension_scores = report["dimension_scores"]
            evaluation.summary = report["summary"]
            evaluation.status = "succeeded"
            evaluation.error = ""

            for item in report["results"]:
                for finding in item["findings"]:
                    session.add(
                        EvaluationFinding(
                            evaluation_id=evaluation.id,
                            critic=finding["critic"],
                            severity=finding["severity"],
                            title=finding["title"],
                            detail=finding["detail"],
                            where=finding["where"],
                            excerpt=finding["excerpt"],
                            suggestion=finding["suggestion"],
                            claim_ids=finding["claim_ids"],
                        )
                    )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            evaluation.status = "failed"
            evaluation.error = f"{type(exc).__name__}: {exc}"[:500]
            await session.commit()
            raise

        await handle.emit(
            ev.ARTIFACT_CREATED,
            {
                "kind": "evaluation",
                "evaluation_id": evaluation.id,
                "overall_score": evaluation.overall_score,
            },
        )
        return {
            "evaluation_id": evaluation.id,
            "overall_score": evaluation.overall_score,
            "dimension_scores": evaluation.dimension_scores,
            "failed_critics": report["summary"]["failed_critics"],
        }


def finding_dict(f: EvaluationFinding) -> dict[str, Any]:
    return {
        "id": f.id,
        "critic": f.critic,
        "severity": f.severity,
        "title": f.title,
        "detail": f.detail,
        "where": f.where,
        "excerpt": f.excerpt,
        "suggestion": f.suggestion,
        "claim_ids": f.claim_ids or [],
    }


def evaluation_dict(e: Evaluation, *, with_findings: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": e.id,
        "draft_id": e.draft_id,
        "draft_revision": e.draft_revision,
        "job_id": e.job_id,
        "run_id": e.run_id,
        "status": e.status,
        "overall_score": e.overall_score,
        "dimension_scores": e.dimension_scores or {},
        "summary": e.summary or {},
        "error": e.error,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }
    if with_findings:
        data["findings"] = [finding_dict(f) for f in e.findings]
    return data
