"""Run 任务注册表与 fake_run。

新增业务只需要写一个 handler 并注册，不动执行器。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from app.harness import events as ev
from app.harness.executor import RunHandle

Handler = Callable[[RunHandle], Awaitable[dict[str, Any] | None]]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def get_handler(kind: str) -> Handler | None:
    # 确保 agent 模块已被导入（注册发生在模块导入时）
    import app.agents  # noqa: F401

    return _HANDLERS.get(kind)


@register("fake")
async def fake_run(handle: RunHandle) -> dict[str, Any]:
    """M0 验收用：真实模型调用 + 五类事件 + 可取消 + 可中断。

    模型调用会产生 run_steps 与 token.delta，用于验证回放与 token 统计。
    """
    system_prompt = "你是工作台的自检助手，回答保持一句话。"
    prompt_hash = await handle.use_prompt("fake.selfcheck", system_prompt, "M0 自检提示词")

    await handle.log("fake run 开始：先做一次真实模型调用")

    reply = await handle.call_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "用一句话说明你正在执行一次自检。不要使用任何工具。"},
        ],
        agent="selfcheck",
        prompt_name="fake.selfcheck",
        prompt_hash=prompt_hash,
        stream=True,
        temperature=0.2,
    )
    await handle.log(f"模型回复：{reply.content[:80]}")

    for i in range(1, 4):
        await handle.check_cancelled()
        await handle.emit(ev.AGENT_STARTED, {"agent": f"worker-{i}", "step": i})
        await asyncio.sleep(1.0)
        await handle.emit(ev.TOOL_CALLED, {"tool": "fake_tool", "args": {"i": i}})
        await asyncio.sleep(0.6)
        await handle.emit(ev.TOOL_RESULT, {"tool": "fake_tool", "ok": True, "result": {"i": i}})
        await handle.emit(ev.AGENT_FINISHED, {"agent": f"worker-{i}"})

    await handle.log("fake run 完成")
    return {"steps": 3, "model_reply": reply.content}
