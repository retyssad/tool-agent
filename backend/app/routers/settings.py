from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsView(BaseModel):
    model: str
    base_url: str
    api_key_masked: str
    redis_url: str
    data_dir: str
    project_roots: list[str]


def _mask(key: str) -> str:
    if not key:
        return "未配置"
    if len(key) <= 10:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


@router.get("")
async def read_settings() -> SettingsView:
    s = get_settings()
    return SettingsView(
        model=s.llm_model,
        base_url=s.llm_base_url,
        api_key_masked=_mask(s.dashscope_api_key),
        redis_url=s.redis_url,
        data_dir=str(s.data_dir),
        project_roots=[str(p) for p in s.project_root_list],
    )
