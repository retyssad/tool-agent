"""对话 agent：一次轮次 = 一个 kind=chat 的 Run。

与业务 agent 的区别只有两点：
1. 工具由自己调度，能在执行前拦截高风险操作（转确认卡）；
2. 每轮结束把 transcript 写进 chat_entries，供下一轮重建上下文。

工具实现见 chat_tools.py，上下文装配见 chat_context.py。
"""
from __future__ import annotations

import json
from typing import Any

from app.agents import chat_tools
from app.agents.chat_context import (
    append_entry,
    build_context,
    compact,
    hard_truncate,
    summarize_tool_result,
)
from app.agents.chat_tools import ChatRuntime, ToolFailure
from app.config import get_settings
from app.harness import events as ev
from app.harness.executor import RunCancelled, RunHandle
from app.models.chat import (
    ENTRY_ASSISTANT,
    ENTRY_TOOL_CALLS,
    ENTRY_TOOL_RESULT,
    TURN_AWAITING_CONFIRMATION,
    TURN_CANCELLED,
    TURN_FAILED,
    TURN_RUNNING,
    TURN_SUCCEEDED,
    ChatAction,
    ChatSession,
    ChatTurn,
)
from app.tasks import register

settings = get_settings()

SYSTEM_PROMPT = """你是「求职工作台」的对话助手。用户是唯一使用者，你在本机帮他操作整个工作台。

## 能力边界
你可以读写岗位、证据账本、生成简历、项目导学、简历评价、模拟面试；也能查询和执行 Run。

## 工作方式
1. **先查后写**。涉及具体对象（岗位、草稿、证据、Run）时，先用 list_* / get_* 确认 id，不要凭空假设。
2. **信息不足就提问**。缺少必要参数时用 ask_user，给 2-4 个可选项；不要猜 id。
3. **多步任务先给计划**。需要 2 步以上时，先调用 set_plan 列出步骤（pending），每完成一步重新提交全量计划
   （把该步标 done、下一步标 running）。一步能做完的小事不要用 set_plan。
4. **高风险操作会自动转确认**。create_job / delete_job / create_draft / export_resume / cancel_run
   会返回 pending_confirmation。你只需说"已发确认，点确认后执行"，不要重复调用，也不要声称已完成。
5. **慢任务会立即返回 run_id**。告诉用户任务已启动、大约多久，并可以用 watch_run 查看进度；不要连续重复发起。
6. **查日志**。用户问"为什么失败""之前做过什么""现在卡在哪"时，用 list_runs / get_run / search_run_events / watch_run。
   日志量大、需要逐条阅读几十条以上事件时才派 investigate 子 agent；列几条记录直接用 list_*。
7. **一次只做一件事**。用户一句话里有多步时按顺序调用工具；每步结果都要汇报。
8. **不要编造数据**。工具返回什么就说什么；报错就转述原因并给出下一步建议。
9. **不要重复查询**。同一轮里已经拿到的信息不要反复调用工具；同一工具同一参数最多调用一次。
   如果一次查询返回的结果已经够回答问题，立即给出最终回答，不要为了"更完整"继续查。

## 输出风格
- 中文，简洁，不客套。
- 用短句和列表，不要大段散文，不要 markdown 表格。
- **不要用 markdown 语法**（`**粗体**`、`##` 标题、反引号）。前端按纯文本渲染，这些符号会原样显示。
  需要强调就用顿号、冒号和短句；列表用「-」开头。
- 操作类回复写清对象和结果，例如"岗位 #3 已标记为面试中"。
- 涉及具体对象时带 id，方便用户核对。
- 卡片由前端渲染，你只需要一句话说明。
"""

INVESTIGATE_PROMPT = """你是只读调查员。你只能查看数据，不能修改任何东西。

你的任务是回答问题，并给出可核验的依据（run_id、seq、对象 id）。
- 只调用只读工具：list_* / get_* / search_run_events / watch_run。
- 不要编造。查不到就说查不到，并说明你检索了什么条件。
- 输出不超过 200 字：先给结论，再给 1-3 条依据。
"""


@register("chat")
async def chat_turn(handle: RunHandle) -> dict[str, Any]:
    session_id = int(handle.target.get("session_id") or 0)
    turn_id = int(handle.target.get("turn_id") or 0)
    if not session_id or not turn_id:
        raise ValueError("缺少 session_id / turn_id")

    # 用 Run 自己的会话：事件写入与 chat_* 表写入共用一把锁，避免 SQLite 写冲突
    db = handle.db
    turn = await db.get(ChatTurn, turn_id)
    if turn is None:
        raise ValueError(f"对话轮次不存在: {turn_id}")
    chat_session = await db.get(ChatSession, session_id)
    if chat_session is None:
        raise ValueError(f"对话会话不存在: {session_id}")

    turn.status = TURN_RUNNING
    turn.current_run_id = handle.run_id
    if handle.run_id not in (turn.run_ids or []):
        turn.run_ids = [*(turn.run_ids or []), handle.run_id]
    await db.commit()

    runtime = ChatRuntime(
        session=db,
        handle=handle,
        session_id=session_id,
        turn_id=turn_id,
        run_id=handle.run_id,
    )
    runtime.investigate = lambda question: investigate(
        handle, question, session_id=session_id, turn_id=turn_id
    )
    token = chat_tools.bind_runtime(runtime)
    try:
        confirmed = handle.target.get("confirmed_action_id")
        if confirmed:
            result = await _resume_after_confirm(handle, db, runtime, turn, int(confirmed))
        else:
            result = await _run_turn(handle, db, runtime, turn)
    except RunCancelled:
        turn.status = TURN_CANCELLED
        await db.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        turn.status = TURN_FAILED
        turn.error = f"{type(exc).__name__}: {exc}"[:1000]
        await db.commit()
        await handle.emit(ev.CHAT_MESSAGE, {"content": "", "error": turn.error})
        raise
    finally:
        chat_tools.unbind_runtime(token)

    return result


# ---------- 正常轮次 ----------

async def _run_turn(
    handle: RunHandle,
    db,
    runtime: ChatRuntime,
    turn: ChatTurn,
) -> dict[str, Any]:
    tools = chat_tools.schemas("chat")
    prompt_hash = await handle.use_prompt("chat.assistant", SYSTEM_PROMPT, "对话助手")

    messages, estimated = await build_context(
        db,
        session_id=turn.session_id,
        system_prompt=SYSTEM_PROMPT,
        tool_schemas=tools,
    )
    await handle.emit(
        ev.RUN_LOG,
        {
            "message": f"上下文：{estimated} tokens（窗口 {settings.chat_context_window}）",
            "level": "debug",
        },
    )

    if estimated > settings.chat_context_window * settings.chat_compact_ratio:
        compaction = await compact(handle, db, session_id=turn.session_id, trigger="auto")
        if compaction is not None:
            messages, estimated = await build_context(
                db,
                session_id=turn.session_id,
                system_prompt=SYSTEM_PROMPT,
                tool_schemas=tools,
            )
        if estimated > settings.chat_context_window * settings.chat_budget_ratio:
            budget = int(
                settings.chat_context_window * settings.chat_budget_ratio
                - settings.chat_output_reserve
            )
            messages = hard_truncate(messages, budget)
            await handle.emit(
                ev.RUN_LOG,
                {"message": f"上下文超预算，已硬截断至 {budget} tokens", "level": "warn"},
            )

    final_text, pending = await _loop(
        handle, db, runtime, turn, messages, tools, prompt_hash
    )
    return await _finalize_turn(handle, db, runtime, turn, final_text, pending)


# ---------- 确认后续跑 ----------

async def _resume_after_confirm(
    handle: RunHandle,
    db,
    runtime: ChatRuntime,
    turn: ChatTurn,
    action_id: int,
) -> dict[str, Any]:
    """已批准的动作确定性执行，再把结果喂回模型继续本轮。"""
    from app.models.chat import ChatEntry

    action = await db.get(ChatAction, action_id)
    if action is None:
        raise ValueError(f"待确认动作不存在: {action_id}")
    if action.status != "pending":
        raise ValueError(f"该动作状态为 {action.status}")

    runtime.confirmed = True
    runtime.tool_call_id = action.tool_call_id
    try:
        result = await chat_tools.call(action.tool, action.args or {}, run_id=handle.run_id)
    except ToolFailure as exc:
        action.status = "failed"
        action.error = str(exc)[:500]
        await db.commit()
        payload = {"ok": False, "error": action.error}
    else:
        action.status = "executed"
        action.result = result if isinstance(result, dict) else {"value": result}
        await db.commit()
        payload = {"ok": True, "result": result}

    content = json.dumps(payload, ensure_ascii=False, default=str)
    summary = (
        summarize_tool_result(action.tool, result)
        if payload["ok"] else f"{action.tool} 失败：{payload.get('error', '')}"[:300]
    )

    # 占位条目原地更新：同一 tool_call_id 只留一条结果
    entry = await db.get(ChatEntry, action.entry_id) if action.entry_id else None
    if entry is not None:
        entry.payload = {
            "tool_call_id": action.tool_call_id,
            "tool": action.tool,
            "content": content,
        }
        entry.summary = summary
        await db.commit()
    else:
        await append_entry(
            db,
            session_id=turn.session_id,
            turn_id=turn.id,
            kind=ENTRY_TOOL_RESULT,
            payload={
                "tool_call_id": action.tool_call_id,
                "tool": action.tool,
                "content": content,
            },
            summary=summary,
        )
        await db.commit()

    await handle.emit(
        ev.TOOL_RESULT,
        {
            "tool": action.tool,
            "ok": payload["ok"],
            "error": payload.get("error", ""),
            "result": _clip(result) if payload["ok"] else None,
            "preview": summary,
        },
    )

    if not payload["ok"]:
        # 执行失败不再继续，直接收尾并说明原因
        text = f"{action.tool} 执行失败：{payload.get('error', '')}"
        return await _finalize_turn(handle, db, runtime, turn, text, [])

    # 让模型看到"已批准并执行"的结果，继续本轮
    runtime.confirmed = False
    tools = chat_tools.schemas("chat")
    prompt_hash = await handle.use_prompt("chat.assistant", SYSTEM_PROMPT, "对话助手")
    messages, _ = await build_context(
        db,
        session_id=turn.session_id,
        system_prompt=SYSTEM_PROMPT,
        tool_schemas=tools,
    )
    final_text, pending = await _loop(
        handle, db, runtime, turn, messages, tools, prompt_hash
    )
    return await _finalize_turn(handle, db, runtime, turn, final_text, pending)


# ---------- 工具循环 ----------

async def _loop(
    handle: RunHandle,
    db,
    runtime: ChatRuntime,
    turn: ChatTurn | None,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
    prompt_hash: str,
    *,
    agent: str = "chat",
    prompt_name: str = "chat.assistant",
    max_rounds: int | None = None,
    allow_confirmation: bool = True,
    stream: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """模型 ↔ 工具循环。

    db/turn 为 None 时（只读调查）不写 transcript，只走事件。
    调查子 agent 用 stream=False：它的输出不该混进主回答的流式文本。
    """
    convo = list(messages)
    rounds = max_rounds or settings.chat_max_rounds
    runtime.cards = []
    runtime.pending = []

    for _ in range(rounds):
        await handle.emit(ev.AGENT_STARTED, {"agent": agent, "model": handle.llm.model})
        reply = await handle.step(
            convo,
            agent=agent,
            prompt_name=prompt_name,
            prompt_hash=prompt_hash,
            tools=tool_schemas,
            temperature=0.4,
            stream=stream,
        )

        # finished 带上本轮结论，步骤行据此显示"调用了什么/直接回答"
        await handle.emit(
            ev.AGENT_FINISHED,
            {
                "agent": agent,
                "tools": [c.name for c in reply.tool_calls],
                "answered": not reply.tool_calls,
            },
        )

        if not reply.tool_calls:
            return reply.content.strip(), runtime.pending

        if db is not None and turn is not None:
            await append_entry(
                db,
                session_id=turn.session_id,
                turn_id=turn.id,
                kind=ENTRY_TOOL_CALLS,
                payload={
                    "content": reply.content or "",
                    "calls": [
                        {"id": c.id, "name": c.name, "arguments": c.arguments}
                        for c in reply.tool_calls
                    ],
                },
            )
            await db.commit()

        convo.append(
            {
                "role": "assistant",
                "content": reply.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in reply.tool_calls
                ],
            }
        )

        for call in reply.tool_calls:
            runtime.tool_call_id = call.id
            try:
                args = call.parse_args()
            except Exception as exc:  # noqa: BLE001
                payload = {"ok": False, "error": f"参数解析失败：{exc}"}
                await _record_tool(
                    db, turn, call.id, call.name, payload,
                    f"{call.name} 参数错误",
                )
                convo.append(_tool_message(call.id, payload))
                continue

            risk = chat_tools.tool_risk(call.name)
            if risk == "destructive" and not runtime.confirmed:
                if not allow_confirmation:
                    payload = {"ok": False, "error": "该操作需要用户确认，当前上下文不可发起"}
                    await _record_tool(
                        db, turn, call.id, call.name, payload, f"{call.name} 已阻止",
                    )
                    convo.append(_tool_message(call.id, payload))
                    continue

            await handle.emit(
                ev.TOOL_CALLED,
                {"tool": call.name, "args": _clip(args), "risk": risk},
            )
            try:
                result = await chat_tools.call(call.name, args, run_id=handle.run_id)
                payload = {"ok": True, "result": _shrink(result)}
            except ToolFailure as exc:
                result = None
                payload = {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                result = None
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}

            summary = (
                summarize_tool_result(call.name, result)
                if payload["ok"] else f"{call.name} 失败：{payload.get('error', '')}"[:300]
            )
            pending_result = (
                isinstance(result, dict) and result.get("status") == "pending_confirmation"
            )
            if not pending_result:
                # 待确认动作的结果由 _finalize_turn 写占位条目，确认后原地更新
                await _record_tool(db, turn, call.id, call.name, payload, summary)
            await handle.emit(
                ev.TOOL_RESULT,
                {
                    "tool": call.name,
                    "ok": payload["ok"],
                    "error": payload.get("error", ""),
                    "preview": summary,
                },
            )
            convo.append(_tool_message(call.id, payload))

    return "这轮调用步骤过多，我先停下。可以再具体一点告诉我要做什么。", runtime.pending

async def _record_tool(
    db,
    turn: ChatTurn | None,
    tool_call_id: str,
    tool: str,
    payload: dict,
    summary: str,
) -> None:
    if db is None or turn is None:
        return
    await append_entry(
        db,
        session_id=turn.session_id,
        turn_id=turn.id,
        kind=ENTRY_TOOL_RESULT,
        payload={
            "tool_call_id": tool_call_id,
            "tool": tool,
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        },
        summary=summary,
    )
    await db.commit()


def _tool_message(tool_call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, ensure_ascii=False, default=str),
    }


# ---------- 收尾 ----------

async def _finalize_turn(
    handle: RunHandle,
    db,
    runtime: ChatRuntime,
    turn: ChatTurn,
    final_text: str,
    pending: list[dict[str, Any]],
) -> dict[str, Any]:
    await append_entry(
        db,
        session_id=turn.session_id,
        turn_id=turn.id,
        kind=ENTRY_ASSISTANT,
        payload={"content": final_text},
    )
    turn.assistant_content = final_text

    # 收尾时把计划里悬空的 running 收敛：本轮结束不该留"进行中"
    if turn.plan:
        plan = [dict(step) for step in turn.plan]
        for step in plan:
            if step.get("status") == "running":
                step["status"] = "done" if not turn.error else "failed"
        turn.plan = plan

    if pending:
        turn.status = TURN_AWAITING_CONFIRMATION
        # 确认卡对应的工具结果先占位，确认后原地更新
        for item in pending:
            action = await db.get(ChatAction, item["action_id"])
            if action is None:
                continue
            entry = await append_entry(
                db,
                session_id=turn.session_id,
                turn_id=turn.id,
                kind=ENTRY_TOOL_RESULT,
                payload={
                    "tool_call_id": action.tool_call_id,
                    "tool": action.tool,
                    "content": json.dumps(
                        {"status": "pending_confirmation", "action_id": action.id},
                        ensure_ascii=False,
                    ),
                },
                summary=f"{action.tool} 等待确认",
            )
            action.entry_id = entry.id
        await db.commit()
    else:
        turn.status = TURN_SUCCEEDED
        await db.commit()

    cards = runtime.cards
    for run_id in runtime.child_runs:
        cards.append({"type": "run", "run_id": run_id})
    turn.cards = cards
    if runtime.child_runs:
        turn.child_run_ids = sorted(set([*(turn.child_run_ids or []), *runtime.child_runs]))
    await db.commit()

    if turn.plan:
        await handle.emit(ev.CHAT_PLAN, {"turn_id": turn.id, "steps": turn.plan})

    await handle.emit(
        ev.CHAT_MESSAGE,
        {
            "turn_id": turn.id,
            "content": final_text,
            "cards": cards,
            "status": turn.status,
        },
    )
    return {
        "turn_id": turn.id,
        "content": final_text,
        "status": turn.status,
        "cards": cards,
        "child_runs": runtime.child_runs,
    }


# ---------- 只读调查子 agent ----------

async def investigate(
    handle: RunHandle,
    question: str,
    *,
    session_id: int,
    turn_id: int,
) -> dict[str, Any]:
    """同 Run 内嵌套的只读调查。独立上下文，只返回结论。"""
    tools = chat_tools.schemas("investigate")
    prompt_hash = await handle.use_prompt("chat.investigator", INVESTIGATE_PROMPT, "只读调查员")

    messages = [
        {"role": "system", "content": INVESTIGATE_PROMPT},
        {"role": "user", "content": f"调查问题：{question}"},
    ]
    nested = ChatRuntime(
        session=None,
        handle=handle,
        session_id=session_id,
        turn_id=turn_id,
        run_id=handle.run_id,
    )

    await handle.emit(
        ev.CHAT_INVESTIGATION,
        {"question": question, "status": "running", "turn_id": turn_id},
    )
    token = chat_tools.bind_runtime(nested)
    try:
        text, _ = await _loop(
            handle,
            None,
            nested,
            None,
            messages,
            tools,
            prompt_hash,
            agent="investigator",
            prompt_name="chat.investigator",
            max_rounds=settings.chat_investigate_rounds,
            allow_confirmation=False,
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001
        await handle.emit(
            ev.CHAT_INVESTIGATION,
            {"question": question, "status": "failed", "error": str(exc)[:300], "turn_id": turn_id},
        )
        raise ToolFailure(f"调查失败：{exc}") from exc
    finally:
        chat_tools.unbind_runtime(token)

    await handle.emit(
        ev.CHAT_INVESTIGATION,
        {"question": question, "status": "done", "conclusion": text[:1000], "turn_id": turn_id},
    )
    return {"conclusion": text}


# ---------- 工具结果处理 ----------

def _shrink(result: Any, limit: int | None = None) -> Any:
    """限制回灌模型的体积，但**保留结构**。

    旧实现把超长列表整体变成字符串，模型读不全就会反复重查。
    这里改为：列表截断条目数，条目内长字符串再截断。
    """
    limit = limit or settings.chat_tool_result_limit

    def trim(value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return str(value)[:200]
        if isinstance(value, str):
            return value if len(value) <= 800 else value[:800] + "…"
        if isinstance(value, list):
            head = [trim(item, depth + 1) for item in value[:12]]
            if len(value) > 12:
                head.append({"_more": len(value) - 12})
            return head
        if isinstance(value, dict):
            return {key: trim(val, depth + 1) for key, val in value.items()}
        return value

    dumped = json.dumps(result, ensure_ascii=False, default=str)
    if len(dumped) <= limit:
        return result
    trimmed = trim(result)
    if isinstance(trimmed, dict):
        trimmed["_truncated"] = True
    return trimmed


def _clip(value: Any, limit: int = 4000) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else text[:limit] + "…"
