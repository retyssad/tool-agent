"""RunExecutor：所有 agent 执行的唯一入口。

业务代码不允许直接调 LLM 或写库：
- 状态迁移只在这里发生
- 每个模型调用记一条 run_steps
- 事件双写（表 + Redis）
- 取消、超时、预算、异常统一处理
"""
from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.harness import events as ev
from app.harness.registry import ToolError, registry
from app.llm.client import LLMClient, ModelReply
from app.models.common import (
    RUN_CANCELLED,
    RUN_FAILED,
    RUN_INTERRUPTED,
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_SUCCEEDED,
)
from app.models.harness import Run, RunStep


class RunCancelled(Exception):
    pass


class BudgetExceeded(Exception):
    pass


@dataclass(slots=True)
class RunHandle:
    """传给任务函数的句柄。所有副作用都经它发生。"""

    run_id: int
    kind: str
    llm: LLMClient
    target: dict[str, Any] = field(default_factory=dict)
    budget_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    _step_seq: int = 0
    _cancelled: bool = False
    _session: AsyncSession | None = None
    # 并行子 agent 共用同一个 AsyncSession：网络调用并行，库操作必须串行
    _db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # run 失败时要回调的清理函数（如把业务对象状态从 running 改回 failed）
    on_failure: Callable[[str], Awaitable[None]] | None = None
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def db(self) -> AsyncSession:
        """本 Run 绑定的会话。业务写库复用它，避免 SQLite 多写者争锁。"""
        assert self._session is not None, "RunHandle 未绑定会话"
        return self._session

    async def log(self, message: str, *, level: str = "info") -> None:
        await self.emit(ev.RUN_LOG, {"message": message, "level": level})

    async def emit(self, type_: str, payload: dict[str, Any]) -> None:
        # run_id<=0 表示同步调用（如面试出题），只借模型能力，不写 run 事件
        if self.run_id <= 0:
            return
        assert self._session is not None, "RunHandle 未绑定会话"
        async with self._db_lock:
            event = await ev.append_event(self._session, self.run_id, type_, payload)
            await self._session.commit()
        await ev.publish(self.run_id, event)

    async def emit_transient(self, type_: str, payload: dict[str, Any]) -> None:
        """只推实时、不落库。用于流式 token，避免事件表膨胀。"""
        if self.run_id <= 0:
            return
        await ev.publish_transient(self.run_id, type_, payload)

    async def check_cancelled(self) -> None:
        if self.run_id <= 0:
            return
        assert self._session is not None
        async with self._db_lock:
            row = await self._session.execute(
                select(Run.cancel_requested).where(Run.id == self.run_id)
            )
            cancelled = bool(row.scalar_one_or_none())
        if cancelled:
            self._cancelled = True
            raise RunCancelled()

    async def use_prompt(self, name: str, body: str, description: str = "") -> str:
        """注册/取用提示词版本，返回 hash；run_step 会记录它。"""
        from app.harness.prompts import ensure_prompt

        assert self._session is not None
        async with self._db_lock:
            prompt = await ensure_prompt(self._session, name, body, description)
        return prompt.hash

    async def step(
        self,
        messages: list[dict[str, Any]],
        *,
        agent: str,
        prompt_name: str = "",
        prompt_hash: str = "",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> ModelReply:
        """单轮模型调用：记录 run_step，但**不执行工具**。

        给需要自行控制工具风险与落库的调用方（对话助手）使用。
        """
        await self.check_cancelled()
        self._guard_budget()
        step = await self._begin_step(agent, prompt_name, prompt_hash, messages)
        try:
            if stream:
                reply = await self._stream_and_emit(
                    messages, temperature, response_format, tools=tools
                )
            else:
                reply = await self.llm.complete(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    response_format=response_format,
                )
        except Exception as exc:  # noqa: BLE001
            await self._finish_step(step, None, error=str(exc))
            raise
        await self._finish_step(step, reply)
        self._add_usage(reply)
        return reply

    async def call_model(
        self,
        messages: list[dict[str, Any]],
        *,
        agent: str,
        prompt_name: str = "",
        prompt_hash: str = "",
        tools: set[str] | None = None,
        temperature: float = 0.3,
        max_tool_rounds: int = 6,
        response_format: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> ModelReply:
        """带工具循环的一次 agent 调用。所有轮次都记入 run_steps。"""
        await self.emit(ev.AGENT_STARTED, {"agent": agent, "model": self.llm.model})
        tool_schemas = registry.schemas(tools) if tools is not None else None
        convo = list(messages)
        final: ModelReply | None = None

        for _round in range(max_tool_rounds):
            await self.check_cancelled()
            self._guard_budget()
            step = await self._begin_step(agent, prompt_name, prompt_hash, convo)

            try:
                if stream:
                    reply = await self._stream_and_emit(convo, temperature, response_format)
                else:
                    reply = await self.llm.complete(
                        convo,
                        tools=tool_schemas,
                        temperature=temperature,
                        response_format=response_format,
                    )
            except Exception as exc:  # noqa: BLE001
                await self._finish_step(step, None, error=str(exc))
                raise

            await self._finish_step(step, reply)
            self._add_usage(reply)
            final = reply

            if not reply.tool_calls:
                break

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
                await self.emit(ev.TOOL_CALLED, {"tool": call.name, "args": _clip(call.arguments)})
                try:
                    result = await registry.call(call.name, call.parse_args(), run_id=self.run_id)
                    payload = {"tool": call.name, "ok": True, "result": _clip_json(result)}
                except ToolError as exc:
                    payload = {"tool": call.name, "ok": False, "error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    payload = {"tool": call.name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
                await self.emit(ev.TOOL_RESULT, payload)
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(payload.get("result", payload), ensure_ascii=False, default=str),
                    }
                )
        else:
            raise RuntimeError(f"工具循环超过 {max_tool_rounds} 轮")

        await self.emit(ev.AGENT_FINISHED, {"agent": agent})
        assert final is not None
        return final

    async def _stream_and_emit(
        self,
        convo: list[dict[str, Any]],
        temperature: float,
        response_format: dict[str, Any] | None,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        reply: ModelReply | None = None
        # 注意：流式下不转发 response_format。DashScope 对 stream + json_object 的
        # 支持不稳定，现有 agent 一直靠提示词约束 JSON 输出；这里保持既有行为。
        buffer: list[str] = []
        buffered = 0

        async def flush() -> None:
            nonlocal buffered
            if not buffer:
                return
            text = "".join(buffer)
            buffer.clear()
            buffered = 0
            await self.emit_transient(ev.TOKEN_DELTA, {"text": text})

        async for kind, value in self.llm.stream(
            convo, tools=tools, temperature=temperature
        ):
            if kind == "delta":
                # 流式分片只推实时，不落库：完整文本已记在 run_steps.response_json
                # 按字符数聚合，避免每个分片都开一次 Redis 连接
                buffer.append(value)
                buffered += len(value)
                if buffered >= 48:
                    await flush()
            else:
                reply = value
        await flush()
        if reply is None:
            raise RuntimeError("流式响应未返回完成事件")
        return reply

    async def _begin_step(
        self,
        agent: str,
        prompt_name: str,
        prompt_hash: str,
        messages: list[dict[str, Any]],
    ) -> RunStep | None:
        # 同步调用模式不记录步骤，避免伪造 run_id
        if self.run_id <= 0:
            return None
        assert self._session is not None
        async with self._db_lock:
            self._step_seq += 1
            step = RunStep(
                run_id=self.run_id,
                seq=self._step_seq,
                agent=agent,
                model=self.llm.model,
                prompt_name=prompt_name,
                prompt_hash=prompt_hash,
                request_json=_clip_messages(messages),
            )
            self._session.add(step)
            await self._session.commit()
        return step

    async def _finish_step(self, step: RunStep | None, reply: ModelReply | None, *, error: str = "") -> None:
        if step is None or self.run_id <= 0:
            return
        assert self._session is not None
        async with self._db_lock:
            if reply is not None:
                step.response_json = {
                    "content": reply.content,
                    "tool_calls": [
                        {"name": c.name, "arguments": c.arguments} for c in reply.tool_calls
                    ],
                    "finish_reason": reply.finish_reason,
                }
                step.tool_calls = [c.name for c in reply.tool_calls]
                step.prompt_tokens = reply.prompt_tokens
                step.completion_tokens = reply.completion_tokens
                step.latency_ms = reply.latency_ms
            step.error = error
            await self._session.commit()
        await self.emit(
            ev.STEP_RECORDED,
            {
                "step_id": step.id,
                "seq": step.seq,
                "agent": step.agent,
                "tokens": step.prompt_tokens + step.completion_tokens,
                "latency_ms": step.latency_ms,
                "error": error,
            },
        )

    def _add_usage(self, reply: ModelReply) -> None:
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens
        if self.budget_tokens and self.prompt_tokens + self.completion_tokens > self.budget_tokens:
            raise BudgetExceeded(
                f"token 预算超限: {self.prompt_tokens + self.completion_tokens} / {self.budget_tokens}"
            )

    def _guard_budget(self) -> None:
        if self.budget_tokens and self.prompt_tokens + self.completion_tokens > self.budget_tokens:
            raise BudgetExceeded("token 预算已超限")


Handler = Callable[[RunHandle], Awaitable[dict[str, Any] | None]]


async def execute_run(run_id: int, handler: Handler, *, worker_id: str = "") -> None:
    """Celery 任务的实际执行体。asyncio.run(execute_run(...))。"""
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return
        if run.status not in (RUN_QUEUED, RUN_INTERRUPTED):
            return

        run.status = RUN_RUNNING
        run.started_at = datetime.now(timezone.utc)
        run.worker_id = worker_id
        await session.commit()

        handle = RunHandle(
            run_id=run_id,
            kind=run.kind,
            llm=LLMClient(),
            target=run.target or {},
            budget_tokens=run.budget_tokens,
            _session=session,
        )
        await handle.emit(ev.RUN_STARTED, {"kind": run.kind, "title": run.title})

        try:
            result = await handler(handle) or {}
            await _finalize(session, run, handle, RUN_SUCCEEDED, result=result)
        except RunCancelled:
            await _finalize(session, run, handle, RUN_CANCELLED)
        except BudgetExceeded as exc:
            await _finalize(session, run, handle, RUN_FAILED, error=str(exc))
        except asyncio.CancelledError:
            await _finalize(session, run, handle, RUN_INTERRUPTED, error="worker 被中断")
            raise
        except Exception as exc:  # noqa: BLE001
            await _finalize(
                session,
                run,
                handle,
                RUN_FAILED,
                error=f"{type(exc).__name__}: {exc}",
                detail=traceback.format_exc(limit=8),
            )


async def _finalize(
    session: AsyncSession,
    run: Run,
    handle: RunHandle,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
    detail: str = "",
) -> None:
    run.status = status
    run.error = error
    run.finished_at = datetime.now(timezone.utc)
    run.prompt_tokens = handle.prompt_tokens
    run.completion_tokens = handle.completion_tokens
    if result:
        run.output_json = result
    await session.commit()

    # 让业务层有机会把自身的 running 状态收敛，避免一直卡在 running
    if status in (RUN_FAILED, RUN_CANCELLED, RUN_INTERRUPTED) and handle.on_failure is not None:
        try:
            await handle.on_failure(error or status)
        except Exception as exc:  # noqa: BLE001 - 清理失败不能掩盖原始错误
            error = f"{error}（清理失败：{exc}）"[:1000]

    payload: dict[str, Any] = {
        "status": status,
        "prompt_tokens": handle.prompt_tokens,
        "completion_tokens": handle.completion_tokens,
        "error": error,
    }
    if detail:
        payload["detail"] = detail
    if result:
        payload["output"] = _clip_json(result)
    await handle.emit(ev.RUN_FINISHED, payload)


async def mark_orphaned_runs(worker_id: str | None = None) -> int:
    """worker 启动时调用：把上次进程遗留的 running run 标为 interrupted。"""
    async with SessionLocal() as session:
        stmt = update(Run).where(Run.status == RUN_RUNNING)
        if worker_id:
            stmt = stmt.where(Run.worker_id == worker_id)
        stmt = stmt.values(status=RUN_INTERRUPTED, error="worker 重启，任务被中断")
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


def _clip(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + f"…(截断 {len(text) - limit} 字符)"


def _clip_json(value: Any, limit: int = 4000) -> Any:
    dumped = json.dumps(value, ensure_ascii=False, default=str)
    if len(dumped) <= limit:
        return value
    return dumped[:limit] + "…(截断)"


def _clip_messages(messages: list[dict[str, Any]], limit: int = 20_000) -> dict[str, Any]:
    """request_json 保持可读，同时限制单条大小；超长时由上层转 artifact。"""
    trimmed: list[dict[str, Any]] = []
    for msg in messages:
        item = dict(msg)
        if isinstance(item.get("content"), str):
            item["content"] = _clip(item["content"], 8000)
        trimmed.append(item)
    payload = {"messages": trimmed}
    if len(json.dumps(payload, ensure_ascii=False, default=str)) > limit:
        payload = {"messages": trimmed[:3], "note": "过长，仅保留前 3 条"}
    return payload
