"""面试任务的 run handler：计划、逐答评分、终局报告。

出题不是异步 run：用户在对话页点「下一题」时同步调用 interviewer，
避免每次出题都要轮询任务状态。计划、评分、报告仍走 Run 以保证可追溯。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.agents.interviewer import final_report, make_plan, score_answer
from app.harness import events as ev
from app.harness.executor import RunHandle
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job
from app.models.project import ProjectEvidence
from app.models.resume import Claim, ResumeDraft
from app.tasks import register

MAX_FOLLOW_UPS = 1


@register("interview_plan")
async def plan_interview(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    session_id = int(handle.target.get("session_id") or 0)
    if not session_id:
        raise ValueError("缺少 session_id")

    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        if session is None:
            raise ValueError("面试会话不存在")

        async def mark_failed(reason: str) -> None:
            async with SessionLocal() as fresh:
                row = await fresh.get(InterviewSession, session_id)
                if row is not None:
                    row.plan_error = reason[:500]
                    await fresh.commit()

        handle.on_failure = mark_failed

        job = await db.get(Job, session.job_id)
        resume = session.resume_snapshot or {}
        claims = []
        evidence = []
        if session.resume_kind == "draft" and session.draft_id:
            draft = await db.get(ResumeDraft, session.draft_id)
            if draft is not None:
                claims = (
                    await db.scalars(
                        select(Claim).where(Claim.ledger_id == draft.ledger_id)
                    )
                ).all()
                claims = [c for c in claims if c.status == "confirmed"]
        evidence_rows = (
            await db.scalars(select(ProjectEvidence).order_by(ProjectEvidence.id.desc()).limit(40))
        ).all()

        await handle.log("生成面试契约")
        pact = await make_plan(
            handle,
            resume=resume,
            job=session.job_snapshot or {},
            claims=[{"id": c.id, "statement": c.statement} for c in claims],
            evidence=[{"id": e.id, "statement": e.statement} for e in evidence_rows],
        )

        session.pact = pact
        session.plan_total = len(pact["questions"])
        session.plan_run_id = handle.run_id
        session.plan_error = ""
        session.title = session.title or (
            f"{job.title if job else '面试'} · {session.resume_kind}"
        )
        await db.commit()

        await handle.emit(
            ev.ARTIFACT_CREATED,
            {"kind": "interview_pact", "session_id": session_id, "questions": session.plan_total},
        )
        return {"session_id": session_id, "questions": session.plan_total}


@register("interview_score")
async def score_turn(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    turn_id = int(handle.target.get("turn_id") or 0)
    if not turn_id:
        raise ValueError("缺少 turn_id")

    async with SessionLocal() as db:
        turn = await db.get(InterviewTurn, turn_id)
        if turn is None:
            raise ValueError("轮次不存在")
        session = await db.get(InterviewSession, turn.session_id)
        if session is None:
            raise ValueError("面试会话不存在")

        claims = []
        if session.resume_kind == "draft" and session.draft_id:
            draft = await db.get(ResumeDraft, session.draft_id)
            if draft is not None:
                rows = await db.scalars(select(Claim).where(Claim.ledger_id == draft.ledger_id))
                claims = [c for c in rows if c.status == "confirmed"]

        try:
            result = await score_answer(
                handle,
                question=turn.question,
                answer=turn.answer,
                resume=session.resume_snapshot or {},
                job=session.job_snapshot or {},
                claims=[{"id": c.id, "statement": c.statement} for c in claims],
            )
        except Exception as exc:  # noqa: BLE001
            turn.feedback = f"评分失败：{exc}"[:500]
            await db.commit()
            raise

        turn.scores = result["scores"]
        turn.feedback = result["one_line"]
        turn.score_run_id = handle.run_id
        await db.commit()

        await handle.emit(
            ev.RUN_LOG,
            {
                "message": f"第 {turn.idx} 题评分：{result['scores']}",
                "level": "info",
            },
        )
        return {"turn_id": turn_id, "scores": result["scores"]}


@register("interview_report")
async def build_report(handle: RunHandle) -> dict[str, Any]:
    from app.db import SessionLocal

    session_id = int(handle.target.get("session_id") or 0)
    if not session_id:
        raise ValueError("缺少 session_id")

    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        if session is None:
            raise ValueError("面试会话不存在")
        turns = (
            await db.scalars(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.idx)
            )
        ).all()

        payload = [
            {
                "idx": t.idx,
                "stage": t.stage,
                "kind": t.kind,
                "question": t.question,
                "answer": t.answer,
                "scores": t.scores or {},
                "feedback": t.feedback,
                "skipped": t.skipped,
            }
            for t in turns
        ]
        if not payload:
            raise ValueError("没有问答记录，无法生成报告")

        report = await final_report(
            handle,
            session_meta={
                "title": session.title,
                "job": session.job_snapshot or {},
                "focus": (session.pact or {}).get("focus", ""),
                "dimensions": (session.pact or {}).get("dimensions", []),
            },
            turns=payload,
        )
        session.report = report
        session.status = "completed"
        session.report_run_id = handle.run_id
        await db.commit()

        await handle.emit(
            ev.ARTIFACT_CREATED,
            {"kind": "interview_report", "session_id": session_id, "score": report["overall_score"]},
        )
        return {
            "session_id": session_id,
            "overall_score": report["overall_score"],
            "verdict": report["verdict"],
        }


def session_dict(session: InterviewSession, *, with_turns: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": session.id,
        "job_id": session.job_id,
        "resume_kind": session.resume_kind,
        "draft_id": session.draft_id,
        "imported_resume_id": session.imported_resume_id,
        "title": session.title,
        "status": session.status,
        "pact": session.pact or {},
        "plan_total": session.plan_total,
        "plan_run_id": session.plan_run_id,
        "report_run_id": session.report_run_id,
        "plan_error": session.plan_error,
        "report": session.report or {},
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }
    if with_turns:
        data["turns"] = [
            {
                "id": t.id,
                "idx": t.idx,
                "kind": t.kind,
                "stage": t.stage,
                "question": t.question,
                "answer": t.answer,
                "basis": t.basis or {},
                "scores": t.scores or {},
                "feedback": t.feedback,
                "skipped": t.skipped,
            }
            for t in session.turns
        ]
    return data
