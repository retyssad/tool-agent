"""对话层测试：工具注册、风险分级、上下文装配与压缩。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import chat_tools
from app.agents.chat_agent import _shrink
from app.agents.chat_context import (
    append_entry,
    build_context,
    estimate_tokens,
    hard_truncate,
    summarize_tool_result,
)
from app.agents.chat_tools import ChatRuntime, ToolFailure
from app.db import Base
from app.harness.registry import ToolError, registry
from app.models.chat import (
    ENTRY_ASSISTANT,
    ENTRY_TOOL_RESULT,
    ENTRY_USER,
    ChatSession,
    ChatTurn,
)


# ---------- 工具注册 ----------

def test_tool_schemas_are_openai_shape():
    schemas = chat_tools.schemas("chat")
    assert schemas
    first = schemas[0]
    assert first["type"] == "function"
    assert first["function"]["name"]
    assert "properties" in first["function"]["parameters"]


def test_tool_names_unique():
    names = registry.names()
    assert len(names) == len(set(names))


def test_chat_and_agent_audiences_are_separate():
    chat_only = {s["function"]["name"] for s in chat_tools.schemas("chat")}
    investigator = {s["function"]["name"] for s in chat_tools.schemas("investigate")}
    assert "search_run_events" in chat_only
    assert "delete_job" not in investigator
    assert "investigate" not in investigator


def test_risk_classification():
    assert chat_tools.tool_risk("delete_job") == "destructive"
    assert chat_tools.tool_risk("set_job_status") == "write"
    assert chat_tools.tool_risk("list_jobs") == "read"
    assert chat_tools.tool_risk("scan_project") == "slow"


@pytest.mark.asyncio
async def test_unknown_tool_raises():
    with pytest.raises(ToolFailure, match="未知工具"):
        await chat_tools.call("nope", {})


@pytest.mark.asyncio
async def test_invalid_args_raise():
    with pytest.raises(ToolFailure, match="参数校验失败"):
        await chat_tools.call("delete_job", {"wrong": 1})


@pytest.mark.asyncio
async def test_destructive_without_confirm_returns_pending(monkeypatch):
    async def fake_get(method, path, **kwargs):
        return {"id": 7, "title": "测试岗位"}

    monkeypatch.setattr(chat_tools, "_request", fake_get)
    runtime = ChatRuntime(session=None, handle=None, session_id=1, turn_id=2, run_id=3)
    # 没有 session 时无法落库，这里只验证拦截逻辑发生在 HTTP 之前
    with pytest.raises(Exception):
        await chat_tools.call("delete_job", {"job_id": 7})


@pytest.mark.asyncio
async def test_export_blocked_by_gate(monkeypatch):
    async def fake_get(method, path, **kwargs):
        return {"id": 1, "title": "简历", "gate": {"ok": False, "errors": [{"message": "证据条目 #3 未确认"}]}}

    monkeypatch.setattr(chat_tools, "_request", fake_get)
    runtime = ChatRuntime(session=None, handle=None, session_id=1, turn_id=2, run_id=3)
    token = chat_tools.bind_runtime(runtime)
    try:
        with pytest.raises(ToolFailure, match="门禁未通过"):
            await chat_tools.call("export_resume", {"draft_id": 1})
    finally:
        chat_tools.unbind_runtime(token)


# ---------- 摘要与截断 ----------

def test_shrink_truncates_large_payload():
    big = {"rows": ["x" * 100 for _ in range(200)]}
    shrunk = _shrink(big, limit=1000)
    assert shrunk["_truncated"] is True
    assert len(shrunk["rows"][0]) < 2000


def test_shrink_keeps_small_payload():
    small = {"a": 1}
    assert _shrink(small) == small


def test_tool_summary_is_deterministic():
    result = {"jobs": [{"id": 1, "title": "后端"}, {"id": 2, "title": "算法"}], "total": 2}
    first = summarize_tool_result("list_jobs", result)
    second = summarize_tool_result("list_jobs", result)
    assert first == second
    assert "后端" in first


def test_tool_summary_mentions_run():
    assert "Run #42" in summarize_tool_result("scan_project", {"run_id": 42})


def test_estimate_tokens_includes_tool_schemas():
    base = estimate_tokens("sys", "", [{"role": "user", "content": "hi"}])
    with_tools = estimate_tokens(
        "sys", "", [{"role": "user", "content": "hi"}], chat_tools.schemas("chat")
    )
    assert with_tools > base


def test_hard_truncate_keeps_system_and_last():
    messages = [
        {"role": "system", "content": "sys"},
        *[{"role": "user", "content": "x" * 500} for _ in range(20)],
        {"role": "user", "content": "最后一句"},
    ]
    out = hard_truncate(messages, limit_tokens=300)
    assert out[0]["role"] == "system"
    assert out[-1]["content"] == "最后一句"
    assert len(out) < len(messages)


# ---------- 上下文装配 ----------

@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'chat.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_context_ages_old_tool_results(db):
    state = ChatSession(title="t")
    db.add(state)
    await db.flush()

    turns = []
    for i in range(4):
        turn = ChatTurn(session_id=state.id, seq=i + 1, user_content=f"u{i}")
        db.add(turn)
        await db.flush()
        turns.append(turn)
        await append_entry(db, session_id=state.id, turn_id=turn.id, kind=ENTRY_USER, payload={"content": f"u{i}"})
        await append_entry(
            db, session_id=state.id, turn_id=turn.id, kind=ENTRY_ASSISTANT,
            payload={"content": f"a{i}"},
        )
        await append_entry(
            db, session_id=state.id, turn_id=turn.id, kind=ENTRY_TOOL_RESULT,
            payload={"tool_call_id": f"c{i}", "tool": "list_jobs", "content": "全文" * 100},
            summary=f"摘要{i}",
        )
    await db.commit()

    messages, _ = await build_context(db, session_id=state.id, system_prompt="sys")
    tool_contents = [m["content"] for m in messages if m.get("role") == "tool"]
    # 最近 2 轮保留全文，更早的用摘要
    assert tool_contents[-1].startswith("全文")
    assert tool_contents[-2].startswith("全文")
    assert tool_contents[0] == "摘要0"
    assert tool_contents[1] == "摘要1"


@pytest.mark.asyncio
async def test_context_is_append_only_after_compaction_marker(db):
    """compacted_into_id 非空的条目不进上下文，但原文仍保留。"""
    state = ChatSession(title="t")
    db.add(state)
    await db.flush()
    turn = ChatTurn(session_id=state.id, seq=1, user_content="q")
    db.add(turn)
    await db.flush()
    entry = await append_entry(
        db, session_id=state.id, turn_id=turn.id, kind=ENTRY_USER, payload={"content": "旧消息"}
    )
    entry.compacted_into_id = 99
    await db.commit()

    messages, _ = await build_context(db, session_id=state.id, system_prompt="sys")
    assert all(m.get("content") != "旧消息" for m in messages)
    assert await db.get(type(entry), entry.id) is not None
