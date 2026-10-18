"""harness 不变量的单元测试：预算、白名单、提示词版本。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.harness.context import ContextBlock, ContextBudget, assemble, rough_tokens
from app.harness.prompts import content_hash, ensure_prompt
from app.harness.registry import ToolError, ToolRegistry, ToolSpec


class ReadArgs(BaseModel):
    path: str


async def _read(path: str) -> str:
    return f"read:{path}"


def test_rough_tokens_counts_cjk_per_char():
    assert rough_tokens("") == 0
    assert rough_tokens("中文") == 2
    assert rough_tokens("abcdefgh") == 2


def test_assemble_truncates_tail_and_records_drop():
    budget = ContextBudget(max_tokens=120, reserved_for_output=0)
    blocks = [
        ContextBlock(label="a", content="甲" * 50),
        ContextBlock(label="b", content="乙" * 200),
    ]
    ctx = assemble(budget, blocks)
    assert ctx.blocks[0].label == "a"
    assert ctx.blocks[1].truncated is True
    assert ctx.snapshot()["blocks"][1]["truncated"] is True


def test_registry_rejects_path_outside_whitelist(tmp_path: Path):
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="read_file",
            description="读文件",
            args_model=ReadArgs,
            handler=_read,
            path_roots=[tmp_path],
        )
    )
    import asyncio

    with pytest.raises(ToolError):
        asyncio.run(reg.call("read_file", {"path": "C:\\Windows\\win.ini"}))
    assert asyncio.run(reg.call("read_file", {"path": str(tmp_path / "ok.txt")})) == f"read:{tmp_path / 'ok.txt'}"


def test_registry_schema_is_openai_shape(tmp_path: Path):
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="read_file", description="读文件", args_model=ReadArgs,
            handler=_read, path_roots=[tmp_path],
        )
    )
    schema = reg.schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert "path" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_prompt_versioning_deactivates_previous(tmp_path: Path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        v1 = await ensure_prompt(session, "p", "第一版")
        v2 = await ensure_prompt(session, "p", "第二版")
        assert v1.hash == content_hash("第一版")
        assert v1.hash != v2.hash
        again = await ensure_prompt(session, "p", "第二版")
        assert again.id == v2.id
        await session.refresh(v1)
        assert v1.active is False
        assert v2.active is True
    await engine.dispose()
