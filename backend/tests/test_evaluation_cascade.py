"""删除生成简历时，其评价报告与 findings 应一并删除（外键级联）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.evaluation import Evaluation, EvaluationFinding
from app.models.resume import ClaimLedger, ResumeDraft
from app.routers import generated


@pytest.fixture
async def session_factory(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(ClaimLedger(id=1, name="账本"))
        session.add(ResumeDraft(id=1, ledger_id=1, title="简历", resume_json={}))
        await session.flush()
        session.add(Evaluation(id=1, draft_id=1, status="succeeded"))
        session.add(Evaluation(id=2, draft_id=1, status="failed"))
        session.add(EvaluationFinding(id=1, evaluation_id=1, critic="jd_match", title="匹配"))
        session.add(EvaluationFinding(id=2, evaluation_id=2, critic="clarity", title="清晰度"))
        await session.commit()
    yield factory
    await engine.dispose()


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_delete_draft_cascades_evaluations_and_findings(session_factory):
    async with session_factory() as session:
        assert await generated.delete_draft(1, session=session) == {"ok": True}
        assert await _count(session, ResumeDraft) == 0
        assert await _count(session, Evaluation) == 0
        assert await _count(session, EvaluationFinding) == 0
