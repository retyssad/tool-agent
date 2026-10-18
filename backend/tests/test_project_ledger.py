"""项目账本：证据导入、转入个人账本、账本删除保护。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.project import Project, ProjectEvidence
from app.models.resume import Claim, ClaimLedger, ResumeDraft
from app.routers import generated, projects


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
        session.add(ClaimLedger(id=1, name="个人账本", kind="personal"))
        session.add(Project(id=1, name="travel-bot", path=str(tmp_path / "repo")))
        await session.flush()
        session.add(ClaimLedger(id=2, name="travel-bot", kind="project", project_id=1))
        session.add(
            ProjectEvidence(
                id=1,
                project_id=1,
                statement="用 Redis 做限流，QPS 提升 3 倍",
                refs=[{"path": "app/limiter.py", "line": 42, "snippet": ""}],
                module="limiter",
                category="feature",
                capabilities=["Redis", "限流"],
            )
        )
        session.add(
            ProjectEvidence(
                id=2,
                project_id=1,
                statement="拆分网关模块，支持灰度路由",
                refs=[{"path": "app/gateway.py", "line": 8, "snippet": ""}],
                module="gateway",
                category="architecture",
                capabilities=["网关"],
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


async def test_import_evidence_into_project_ledger(session_factory):
    async with session_factory() as session:
        result = await generated.import_project_evidence(
            2, generated.ImportEvidenceBody(project_id=1), session=session
        )
        assert result == {"created": 2, "skipped": 0}

        claims = (await session.scalars(select(Claim).where(Claim.ledger_id == 2))).all()
        assert len(claims) == 2
        assert {c.status for c in claims} == {"pending"}
        assert claims[0].source_type == "project"
        assert claims[0].source_ref == "app/limiter.py:42"
        assert claims[0].organization == "travel-bot"


async def test_import_evidence_dedupes_by_source_ref(session_factory):
    async with session_factory() as session:
        await generated.import_project_evidence(
            2, generated.ImportEvidenceBody(project_id=1), session=session
        )
        again = await generated.import_project_evidence(
            2, generated.ImportEvidenceBody(project_id=1, evidence_ids=[1]), session=session
        )
        assert again == {"created": 0, "skipped": 1}
        assert len((await session.scalars(select(Claim).where(Claim.ledger_id == 2))).all()) == 2


async def test_copy_confirmed_claims_to_personal_ledger(session_factory):
    async with session_factory() as session:
        await generated.import_project_evidence(
            2, generated.ImportEvidenceBody(project_id=1), session=session
        )
        claim = await session.scalar(select(Claim).where(Claim.ledger_id == 2))
        claim.status = "confirmed"
        await session.commit()

        result = await generated.copy_claims(
            1, generated.CopyClaimsBody(claim_ids=[claim.id]), session=session
        )
        assert result == {"created": 1, "skipped": 0}

        copied = await session.scalar(select(Claim).where(Claim.ledger_id == 1))
        assert copied.status == "confirmed"
        assert copied.project_id == 1
        assert copied.source_ref == claim.source_ref

        # 再转一次应跳过重复
        again = await generated.copy_claims(
            1, generated.CopyClaimsBody(claim_ids=[claim.id]), session=session
        )
        assert again == {"created": 0, "skipped": 1}


async def test_delete_ledger_blocked_when_draft_uses_it(session_factory):
    async with session_factory() as session:
        session.add(ResumeDraft(id=1, ledger_id=1, title="简历", resume_json={}))
        await session.commit()

        with pytest.raises(generated.HTTPException) as exc:
            await generated.delete_ledger(1, session=session)
        assert exc.value.status_code == 409


async def test_delete_project_detaches_ledger_used_by_draft(session_factory):
    async with session_factory() as session:
        session.add(ResumeDraft(id=1, ledger_id=2, title="简历", resume_json={}))
        await session.commit()

        await projects.delete_project(1, session=session)

        ledger = await session.get(ClaimLedger, 2)
        assert ledger is not None
        assert ledger.kind == "personal"
        assert ledger.project_id is None
        assert ledger.name == "已归档：travel-bot"
        assert await session.get(Project, 1) is None
