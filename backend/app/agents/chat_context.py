"""对话上下文：条目追加、模型上下文装配、工具结果老化、压缩。

设计见 docs/adr/0005-chat-context-transcript-compaction.md：
- chat_entries 是 append-only 的唯一真相源
- 模型上下文每次调用前现场重建，不缓存、不滑动窗口
- 压缩只标记区间，不删原文
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.harness import events as ev
from app.harness.context import rough_tokens
from app.harness.executor import RunHandle
from app.models.chat import (
    ENTRY_ASSISTANT,
    ENTRY_NOTE,
    ENTRY_TOOL_CALLS,
    ENTRY_TOOL_RESULT,
    ENTRY_USER,
    ChatCompaction,
    ChatEntry,
)

settings = get_settings()

COMPACT_SYSTEM_PROMPT = """你是对话历史压缩器。把给定的对话记录压缩成 JSON，只输出 JSON 对象。

必须包含四个字段，全部是字符串数组：
- facts：已确认的事实与对象 id（岗位 #3、草稿 #7、账本 #2、Run #42 等），带 id
- done：已经完成的操作，一句话一条
- todo：还没完成的待办，包含用户要求的后续步骤
- preferences：用户明确说过的**长期**偏好或口径。只收用户直接说过的，不要推断。
  一次性指令（"先别做别的""只回一句""这次用xx"）不要写入；拿不准就不写。

规则：
1. 不要虚构任何 id 或结论。
2. 无关寒暄全部丢弃。
3. 每条不超过 40 字。
"""


# ---------- 条目写入 ----------

async def append_entry(
    session: AsyncSession,
    *,
    session_id: int,
    turn_id: int | None,
    kind: str,
    payload: dict[str, Any],
    summary: str = "",
    tokens: int | None = None,
) -> ChatEntry:
    """追加一条条目，seq 在会话内单调递增。"""
    max_seq = await session.scalar(
        select(func.coalesce(func.max(ChatEntry.seq), 0)).where(
            ChatEntry.session_id == session_id
        )
    )
    entry = ChatEntry(
        session_id=session_id,
        turn_id=turn_id,
        seq=int(max_seq or 0) + 1,
        kind=kind,
        payload=payload,
        summary=summary,
        tokens=tokens if tokens is not None else rough_tokens(_entry_text(kind, payload, summary)),
    )
    session.add(entry)
    await session.flush()
    return entry


def _entry_text(kind: str, payload: dict[str, Any], summary: str = "") -> str:
    if summary:
        return summary
    if kind in (ENTRY_USER, ENTRY_ASSISTANT):
        return str(payload.get("content", ""))
    return json.dumps(payload, ensure_ascii=False, default=str)


def summarize_tool_result(tool: str, result: Any) -> str:
    """工具结果的确定性摘要：不依赖模型，写入时就固定下来。

    只对已知的列表型返回做定制；其余统一截断。
    """
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, default=str)[:200]

    list_keys = ("jobs", "claims", "drafts", "projects", "evaluations", "interviews", "runs", "events")
    for key in list_keys:
        rows = result.get(key)
        if isinstance(rows, list) and rows:
            names = []
            for row in rows[:6]:
                if isinstance(row, dict):
                    label = row.get("title") or row.get("name") or row.get("statement") or row.get("id")
                    if label is not None:
                        names.append(str(label)[:40])
            more = f" 等 {len(rows)} 条" if len(rows) > 6 else ""
            head = f"{tool}：{', '.join(names)}" if names else f"{tool}：{len(rows)} 条"
            return f"{head}{more}"[:300]

    if "error" in result:
        return f"{tool} 失败：{str(result['error'])[:200]}"
    if "run_id" in result:
        return f"{tool} 已启动 Run #{result['run_id']}"

    # 任意 dict：只报结构（键 + 条数/标量），不灌整段 JSON
    parts: list[str] = []
    for key, value in list(result.items())[:8]:
        if isinstance(value, list):
            parts.append(f"{key} {len(value)} 项")
        elif isinstance(value, dict):
            parts.append(f"{key} {{{', '.join(list(value)[:5])}}}")
        else:
            text = str(value)
            if text:
                parts.append(f"{key}={text[:40]}")
    return f"{tool}：" + ("；".join(parts) if parts else "已完成")[:280]


# ---------- 上下文装配 ----------

def _entry_messages(entry: ChatEntry, *, aged: bool) -> list[dict[str, Any]]:
    payload = entry.payload or {}
    if entry.kind == ENTRY_USER:
        return [{"role": "user", "content": str(payload.get("content", ""))}]
    if entry.kind == ENTRY_ASSISTANT:
        return [{"role": "assistant", "content": str(payload.get("content", ""))}]
    if entry.kind == ENTRY_TOOL_CALLS:
        calls = payload.get("calls") or []
        return [
            {
                "role": "assistant",
                "content": str(payload.get("content", "")),
                "tool_calls": [
                    {
                        "id": c.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": c.get("name", ""),
                            "arguments": c.get("arguments", "{}"),
                        },
                    }
                    for c in calls
                ],
            }
        ]
    if entry.kind == ENTRY_TOOL_RESULT:
        content = payload.get("content", "")
        if aged and entry.summary:
            content = entry.summary
        return [
            {
                "role": "tool",
                "tool_call_id": str(payload.get("tool_call_id", "")),
                "content": content,
            }
        ]
    if entry.kind == ENTRY_NOTE:
        return [{"role": "user", "content": f"[工作台备注] {payload.get('content', '')}"}]
    return []


def _recent_turn_ids(entries: list[ChatEntry], keep: int) -> set[int]:
    seen: list[int] = []
    for entry in reversed(entries):
        if entry.turn_id is None:
            continue
        if entry.turn_id not in seen:
            seen.append(entry.turn_id)
            if len(seen) >= keep:
                break
    return set(seen)


def _summary_text(compaction: ChatCompaction | None) -> str:
    if compaction is None:
        return ""
    summary = compaction.summary or {}
    lines = ["## 会话摘要（已压缩的历史）"]
    labels = (
        ("facts", "已确认事实与 id"),
        ("done", "已完成操作"),
        ("todo", "未完成待办"),
        ("preferences", "用户口径"),
    )
    for key, label in labels:
        values = summary.get(key) or []
        if not values:
            continue
        lines.append(f"### {label}")
        lines.extend(f"- {v}" for v in values)
    return "\n".join(lines)


async def load_open_entries(
    session: AsyncSession, session_id: int, *, last_compaction: ChatCompaction | None
) -> list[ChatEntry]:
    stmt = select(ChatEntry).where(
        ChatEntry.session_id == session_id,
        ChatEntry.compacted_into_id.is_(None),
    )
    if last_compaction is not None:
        stmt = stmt.where(ChatEntry.seq > last_compaction.to_seq)
    return list((await session.scalars(stmt.order_by(ChatEntry.seq))).all())


async def latest_compaction(
    session: AsyncSession, session_id: int
) -> ChatCompaction | None:
    return await session.scalar(
        select(ChatCompaction)
        .where(ChatCompaction.session_id == session_id)
        .order_by(ChatCompaction.to_seq.desc(), ChatCompaction.id.desc())
        .limit(1)
    )


def estimate_tokens(
    system: str,
    summary: str,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None = None,
) -> int:
    """粗略估算一次请求的输入成本，含工具 schema。"""
    total = rough_tokens(system) + rough_tokens(summary)
    for msg in messages:
        total += rough_tokens(str(msg.get("content") or ""))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            total += rough_tokens(str(fn.get("name", ""))) + rough_tokens(str(fn.get("arguments", "")))
    if tool_schemas:
        total += rough_tokens(json.dumps(tool_schemas, ensure_ascii=False, default=str))
    return total


def should_compact(estimated: int) -> bool:
    threshold = settings.chat_context_window * settings.chat_compact_ratio
    return estimated > threshold


async def build_context(
    session: AsyncSession,
    *,
    session_id: int,
    system_prompt: str,
    tool_schemas: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """返回 (messages, 估算 token)。当前轮用户消息须已作为条目写入。

    最近 keep_recent_turns 轮的工具结果保留全文，更早的用摘要替换。
    """
    compaction = await latest_compaction(session, session_id)
    entries = await load_open_entries(session, session_id, last_compaction=compaction)
    recent = _recent_turn_ids(entries, settings.chat_keep_recent_turns)

    messages: list[dict[str, Any]] = []
    for entry in entries:
        aged = entry.kind == ENTRY_TOOL_RESULT and (
            entry.turn_id is None or entry.turn_id not in recent
        )
        messages.extend(_entry_messages(entry, aged=aged))

    summary = _summary_text(compaction)
    if summary:
        system_prompt = f"{system_prompt}\n\n{summary}"

    estimated = estimate_tokens(system_prompt, "", messages, tool_schemas)
    return [{"role": "system", "content": system_prompt}, *messages], estimated


def hard_truncate(
    messages: list[dict[str, Any]], limit_tokens: int
) -> list[dict[str, Any]]:
    """压缩失败时的降级：从最旧的 user/assistant 开始丢，保留 system 与最后一条。"""
    if not messages:
        return messages
    head = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if head else list(messages)
    tail = body[-1:]
    middle = body[:-1]

    used = rough_tokens(head["content"]) if head else 0
    kept: list[dict[str, Any]] = []
    for msg in reversed(middle):
        cost = rough_tokens(str(msg.get("content") or ""))
        if used + cost > limit_tokens:
            break
        used += cost
        kept.append(msg)
    kept.reverse()
    return _sanitize_pairs(([head] if head else []) + kept + tail)


def _sanitize_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保证 assistant(tool_calls) 与其 tool 结果成对，否则模型接口会 400。

    做法：先把 assistant(tool_calls) 及其结果暂存，配齐了才落盘；
    缺结果的调用整组丢弃（包括已经出现的部分结果）。
    """
    out: list[dict[str, Any]] = []
    pending_call: dict[str, Any] | None = None
    pending_ids: set[str] = set()
    pending_results: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending_call, pending_ids, pending_results
        if pending_call is not None and not pending_ids:
            out.append(pending_call)
            out.extend(pending_results)
        pending_call, pending_ids, pending_results = None, set(), []

    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            call_id = str(msg.get("tool_call_id", ""))
            if pending_call is not None and call_id in pending_ids:
                pending_ids.discard(call_id)
                pending_results.append(msg)
            continue
        # 非 tool 消息：未闭合的调用直接丢弃
        flush()
        if role == "assistant" and msg.get("tool_calls"):
            pending_call = msg
            pending_ids = {str(c.get("id", "")) for c in msg["tool_calls"]}
            continue
        out.append(msg)
    flush()
    return out


# ---------- 压缩 ----------

def _render_for_compaction(compaction: ChatCompaction | None, entries: list[ChatEntry]) -> str:
    parts: list[str] = []
    if compaction is not None:
        parts.append("【已有摘要】\n" + json.dumps(compaction.summary, ensure_ascii=False))
    for entry in entries:
        payload = entry.payload or {}
        if entry.kind == ENTRY_USER:
            parts.append(f"用户：{payload.get('content', '')}")
        elif entry.kind == ENTRY_ASSISTANT:
            parts.append(f"助手：{payload.get('content', '')}")
        elif entry.kind == ENTRY_TOOL_CALLS:
            names = [c.get("name", "") for c in payload.get("calls") or []]
            parts.append(f"助手调用工具：{', '.join(names)}")
        elif entry.kind == ENTRY_TOOL_RESULT:
            text = entry.summary or str(payload.get("content", ""))[:300]
            parts.append(f"工具结果：{text}")
        elif entry.kind == ENTRY_NOTE:
            parts.append(f"备注：{payload.get('content', '')}")
    return "\n".join(parts)[:24_000]


def _compaction_cutoff(entries: list[ChatEntry], keep_turns: int) -> int:
    """返回可压缩的最大 seq：最近 keep_turns 轮整体保留，不切成两半。"""
    turns: list[int] = []
    for entry in reversed(entries):
        if entry.turn_id is None or entry.turn_id in turns:
            continue
        turns.append(entry.turn_id)
        if len(turns) > keep_turns:
            # 这一轮属于要保留的部分：压到它第一条之前
            first = next(e.seq for e in entries if e.turn_id == entry.turn_id)
            return first - 1
    return 0


async def compact(
    handle: RunHandle,
    session: AsyncSession,
    *,
    session_id: int,
    trigger: str = "auto",
    force: bool = False,
) -> ChatCompaction | None:
    """执行一次压缩。失败时返回 None，由调用方降级为硬截断。"""
    previous = await latest_compaction(session, session_id)
    entries = await load_open_entries(session, session_id, last_compaction=previous)
    if not entries:
        return None

    cutoff = _compaction_cutoff(entries, settings.chat_keep_recent_turns)
    if force:
        # 手动压缩也要保留最近一轮，否则模型会丢掉刚发生的事
        cutoff = _compaction_cutoff(entries, 0)
    if cutoff <= 0:
        return None

    to_compact = [e for e in entries if e.seq <= cutoff]
    if not to_compact:
        return None

    transcript = _render_for_compaction(previous, to_compact)
    tokens_before = rough_tokens(transcript)

    await handle.emit(ev.RUN_LOG, {"message": f"压缩对话历史：{len(to_compact)} 条"})
    try:
        reply = await handle.call_model(
            [
                {"role": "system", "content": COMPACT_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            agent="compactor",
            prompt_name="chat.compactor",
            prompt_hash=await handle.use_prompt(
                "chat.compactor", COMPACT_SYSTEM_PROMPT, "对话历史压缩"
            ),
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        summary = json.loads(reply.content or "{}")
    except Exception as exc:  # noqa: BLE001 - 压缩失败不能阻断本轮
        await handle.emit(ev.RUN_LOG, {"message": f"压缩失败，降级为截断：{exc}", "level": "warn"})
        return None

    if not isinstance(summary, dict):
        return None
    normalized = {
        key: [str(v)[:200] for v in (summary.get(key) or [])][:30]
        if isinstance(summary.get(key), list)
        else []
        for key in ("facts", "done", "todo", "preferences")
    }
    tokens_after = rough_tokens(json.dumps(normalized, ensure_ascii=False))

    row = ChatCompaction(
        session_id=session_id,
        run_id=handle.run_id if handle.run_id > 0 else None,
        from_seq=previous.from_seq if previous else (to_compact[0].seq),
        to_seq=cutoff,
        summary=normalized,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        trigger=trigger,
    )
    session.add(row)
    await session.flush()

    for entry in to_compact:
        entry.compacted_into_id = row.id
    await session.commit()

    await handle.emit(
        ev.CHAT_COMPACTION,
        {
            "compaction_id": row.id,
            "from_seq": row.from_seq,
            "to_seq": row.to_seq,
            "entries": len(to_compact),
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "trigger": trigger,
            "summary": normalized,
        },
    )
    return row
