"""版本管理回归：列表带预览 URL，删除只清理未被引用的目录。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.resume import ClaimLedger, ResumeVersion
from app.routers import generated


@pytest.fixture
async def session_factory(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(generated.settings, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    from app.db import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(ClaimLedger(id=1, name="账本"))
        await session.commit()
    yield factory
    await engine.dispose()


def _version(vid: int, rel: str, version_no: int = 1) -> ResumeVersion:
    return ResumeVersion(
        id=vid,
        ledger_id=1,
        title="简历",
        template="asu",
        resume_json_path=f"{rel}/resume.json",
        html_path=f"{rel}/index.html",
        pdf_path=f"{rel}/resume.pdf",
        gate_status="passed",
        version_no=version_no,
    )


async def test_list_versions_includes_preview_urls(session_factory):
    async with session_factory() as session:
        session.add(_version(1, "resumes/generated/version-1"))
        await session.commit()

        rows = await generated.list_versions(job_id=None, session=session)

    assert rows[0]["pdf_url"] == "/api/versions/1/pdf"
    assert rows[0]["html_url"] == "/api/versions/1/html"


async def test_delete_version_keeps_folder_shared_by_other_versions(session_factory, tmp_path):
    shared = tmp_path / "resumes" / "generated" / "draft-1"
    shared.mkdir(parents=True)
    (shared / "resume.json").write_text("{}", encoding="utf-8")

    async with session_factory() as session:
        # 旧数据：两个版本共用同一目录
        session.add(_version(1, "resumes/generated/draft-1", 1))
        session.add(_version(2, "resumes/generated/draft-1", 2))
        await session.commit()

        assert await generated.delete_version(1, session=session) == {"ok": True}
        assert shared.is_dir()

        await generated.delete_version(2, session=session)
        assert not shared.exists()


async def test_delete_version_removes_own_folder(session_factory, tmp_path):
    folder = tmp_path / "resumes" / "generated" / "version-3"
    folder.mkdir(parents=True)
    (folder / "resume.pdf").write_bytes(b"%PDF-1.4")

    async with session_factory() as session:
        session.add(_version(3, "resumes/generated/version-3"))
        await session.commit()

        await generated.delete_version(3, session=session)

    assert not folder.exists()


async def test_delete_version_refuses_path_outside_generated_root(session_factory, tmp_path):
    outside = tmp_path / "jobs" / "9"
    outside.mkdir(parents=True)
    (outside / "resume.json").write_text("{}", encoding="utf-8")

    async with session_factory() as session:
        session.add(_version(4, "jobs/9"))
        await session.commit()

        await generated.delete_version(4, session=session)

    assert outside.is_dir()


async def test_export_writes_each_version_to_own_folder(session_factory, tmp_path, monkeypatch):
    from app.models.resume import Claim, ResumeDraft

    def fake_export_pdf(html_path: Path, pdf_path: Path, **kwargs) -> Path:
        pdf_path.write_bytes(b"%PDF-1.4")
        return pdf_path

    monkeypatch.setattr(generated, "export_pdf", fake_export_pdf)

    resume = {
        "profile": {"name": "张三"},
        "experience": [
            {
                "organization": "某公司",
                "role": "后端",
                "dates": "2021-至今",
                "bullets": [{"label": "核心", "text": "重构交易链路", "claim_ids": [1]}],
            }
        ],
        "projects": [],
        "skills": [],
        "summary": [],
        "education": [],
        "campus": [],
    }

    async with session_factory() as session:
        session.add(Claim(id=1, ledger_id=1, statement="重构交易链路", status="confirmed"))
        session.add(ResumeDraft(id=1, ledger_id=1, title="简历", template="asu", resume_json=resume))
        await session.commit()

        first = await generated.export_draft(1, template=None, session=session)
        second = await generated.export_draft(1, template=None, session=session)

    assert first["version_id"] != second["version_id"]
    for vid in (first["version_id"], second["version_id"]):
        folder = tmp_path / "resumes" / "generated" / f"version-{vid}"
        assert (folder / "resume.json").is_file()
        assert (folder / "index.html").is_file()
        assert (folder / "resume.pdf").is_file()

