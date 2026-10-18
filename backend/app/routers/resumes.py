"""简历库路由。

两类简历严格分开：
- 导入简历：只存原 PDF 与抽取文本，不做结构化转换
- 生成简历：M3 起提供，resume.json 为事实源
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.resume import ImportedResume
from app.tools.pdf import extract_pdf

router = APIRouter(prefix="/api/resumes", tags=["resumes"])
settings = get_settings()

MAX_PDF_BYTES = 20 * 1024 * 1024


class ImportedResumeItem(BaseModel):
    id: int
    title: str
    original_name: str
    page_count: int
    has_text_layer: bool
    file_size: int
    tags: list[str]
    notes: str
    created_at: Any
    updated_at: Any


class ImportedResumeDetail(ImportedResumeItem):
    text: str


class UpdateImportedResume(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


@router.post("/imported", response_model=ImportedResumeDetail)
async def upload_imported(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    tags: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
) -> ImportedResumeDetail:
    filename = file.filename or "resume.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只接受 PDF 文件")

    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_PDF_BYTES // 1024 // 1024}MB")

    try:
        extracted = await asyncio.to_thread(extract_pdf, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"PDF 解析失败：{exc}") from exc

    if not extracted.has_text_layer:
        # 扫描件明确拒绝，不做 OCR；原件也不落库，避免留下无法检索的死数据
        raise HTTPException(
            422,
            "该 PDF 没有可用文本层（可能是扫描件或图片导出）。"
            "请导出为带文字层的 PDF 后重新上传。",
        )

    record = ImportedResume(
        title=(title.strip() or Path(filename).stem)[:200],
        original_name=filename[:300],
        original_path="",
        text_path="",
        page_count=extracted.page_count,
        has_text_layer=True,
        file_size=len(data),
        tags=_split_tags(tags),
    )
    session.add(record)
    await session.flush()

    base = settings.data_dir / "resumes" / "imported" / str(record.id)
    base.mkdir(parents=True, exist_ok=True)
    pdf_path = base / "original.pdf"
    text_path = base / "extracted.txt"
    pdf_path.write_bytes(data)
    text_path.write_text(extracted.text, encoding="utf-8")

    record.original_path = f"resumes/imported/{record.id}/original.pdf"
    record.text_path = f"resumes/imported/{record.id}/extracted.txt"
    await session.commit()
    await session.refresh(record)

    return _detail(record, extracted.text)


@router.get("/imported", response_model=list[ImportedResumeItem])
async def list_imported(
    q: str | None = Query(default=None, description="在抽取文本中搜索"),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ImportedResumeItem]:
    rows = (
        await session.execute(
            select(ImportedResume).order_by(ImportedResume.id.desc()).limit(limit)
        )
    ).scalars().all()

    if q:
        needle = q.strip().lower()
        matched: list[ImportedResume] = []
        for record in rows:
            if needle in (record.title or "").lower() or needle in record.original_name.lower():
                matched.append(record)
                continue
            text = await asyncio.to_thread(_read_text, record)
            if needle in text.lower():
                matched.append(record)
        rows = matched

    return [_item(r) for r in rows]


@router.get("/imported/{resume_id}", response_model=ImportedResumeDetail)
async def get_imported(
    resume_id: int, session: AsyncSession = Depends(get_session)
) -> ImportedResumeDetail:
    record = await session.get(ImportedResume, resume_id)
    if record is None:
        raise HTTPException(404, "简历不存在")
    return _detail(record, _read_text(record))


@router.patch("/imported/{resume_id}", response_model=ImportedResumeItem)
async def update_imported(
    resume_id: int, body: UpdateImportedResume, session: AsyncSession = Depends(get_session)
) -> ImportedResumeItem:
    record = await session.get(ImportedResume, resume_id)
    if record is None:
        raise HTTPException(404, "简历不存在")
    if body.title is not None:
        record.title = body.title.strip()[:200] or record.title
    if body.tags is not None:
        record.tags = [t.strip() for t in body.tags if t.strip()][:20]
    if body.notes is not None:
        record.notes = body.notes
    await session.commit()
    await session.refresh(record)
    return _item(record)


@router.get("/imported/{resume_id}/file")
async def download_imported(resume_id: int, session: AsyncSession = Depends(get_session)):
    record = await session.get(ImportedResume, resume_id)
    if record is None:
        raise HTTPException(404, "简历不存在")
    path = (settings.data_dir / record.original_path).resolve()
    if not path.is_file():
        raise HTTPException(404, "原始文件已丢失")
    if settings.data_dir.resolve() not in path.parents:
        raise HTTPException(403, "非法路径")
    # inline 便于浏览器内嵌预览
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=record.original_name or path.name,
        content_disposition_type="inline",
    )


@router.delete("/imported/{resume_id}")
async def delete_imported(
    resume_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    record = await session.get(ImportedResume, resume_id)
    if record is None:
        raise HTTPException(404, "简历不存在")
    await session.delete(record)
    await session.commit()

    folder = settings.data_dir / "resumes" / "imported" / str(resume_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()][:20]


def _read_text(record: ImportedResume) -> str:
    if not record.text_path:
        return ""
    path = settings.data_dir / record.text_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _item(record: ImportedResume) -> ImportedResumeItem:
    return ImportedResumeItem(
        id=record.id,
        title=record.title,
        original_name=record.original_name,
        page_count=record.page_count,
        has_text_layer=record.has_text_layer,
        file_size=record.file_size,
        tags=[str(t) for t in (record.tags or [])],
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _detail(record: ImportedResume, text: str) -> ImportedResumeDetail:
    return ImportedResumeDetail(**_item(record).model_dump(), text=text)
