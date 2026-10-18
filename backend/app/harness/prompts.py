"""提示词版本化：按内容 hash 去重写入 prompts 表。"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.harness import Prompt


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


async def ensure_prompt(session: AsyncSession, name: str, body: str, description: str = "") -> Prompt:
    """同名同内容幂等；内容变化则产生新版本并停用旧版本。"""
    h = content_hash(body)
    existing = await session.scalar(
        select(Prompt).where(Prompt.name == name, Prompt.hash == h)
    )
    if existing is not None:
        return existing

    previous = await session.scalars(
        select(Prompt).where(Prompt.name == name, Prompt.active.is_(True))
    )
    for row in previous:
        row.active = False

    prompt = Prompt(name=name, body=body, hash=h, description=description, active=True)
    session.add(prompt)
    await session.commit()
    await session.refresh(prompt)
    return prompt


async def get_active(session: AsyncSession, name: str) -> Prompt | None:
    return await session.scalar(
        select(Prompt).where(Prompt.name == name, Prompt.active.is_(True))
    )
