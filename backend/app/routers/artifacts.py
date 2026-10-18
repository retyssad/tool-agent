from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db import SessionLocal
from app.models.harness import Artifact

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
settings = get_settings()


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: int) -> FileResponse:
    async with SessionLocal() as session:
        art = await session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, "artifact 不存在")
        path = (settings.data_dir / art.path).resolve()
    if not path.is_file():
        raise HTTPException(404, "artifact 文件已丢失")
    # 防止路径逃逸
    if settings.data_dir.resolve() not in path.parents:
        raise HTTPException(403, "非法路径")
    return FileResponse(path, media_type=art.mime or "application/octet-stream", filename=art.name or path.name)
