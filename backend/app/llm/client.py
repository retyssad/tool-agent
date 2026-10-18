"""模型适配层：OpenAI-compatible Chat Completions。

处理三件 DashScope/Qwen 特有的事：
1. 流式 usage 默认返回 null，必须显式传 stream_options.include_usage
2. 响应带 reasoning_content，不能原样回传给下一轮
3. 工具调用参数可能是分片拼接的字符串
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.config import get_settings
from app.harness.registry import ToolError


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def parse_args(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ToolError(f"工具参数不是合法 JSON: {self.arguments[:200]}") from exc
        if not isinstance(value, dict):
            raise ToolError("工具参数必须是 JSON 对象")
        return value


@dataclass(slots=True)
class ModelReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""


class LLMClient:
    """所有模型调用的唯一出口，便于统一记录与预算控制。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_retries: int = 0,
    ):
        settings = get_settings()
        self.model = model or settings.llm_model
        self._client = AsyncOpenAI(
            api_key=api_key or settings.dashscope_api_key,
            base_url=base_url or settings.llm_base_url,
            # 长输出请求不该被自动重试拖成数倍耗时；失败由上层决定重跑
            max_retries=max_retries,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        timeout_s: float = 300.0,
    ) -> ModelReply:
        start = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _sanitize(messages),
            "temperature": temperature,
            "timeout": timeout_s,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_format:
            kwargs["response_format"] = response_format

        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        usage = resp.usage
        calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments or "{}",
            )
            for tc in (choice.tool_calls or [])
        ]
        return ModelReply(
            content=choice.content or "",
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=int((time.perf_counter() - start) * 1000),
            finish_reason=resp.choices[0].finish_reason or "",
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        timeout_s: float = 300.0,
    ) -> AsyncIterator[tuple[str, Any]]:
        """产出 ("delta", str) 与 ("done", ModelReply)。

        tool_calls 也支持流式：分片按 index 拼回完整调用。
        """
        start = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _sanitize(messages),
            "temperature": temperature,
            "timeout": timeout_s,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if response_format:
            kwargs["response_format"] = response_format
        stream = await self._client.chat.completions.create(**kwargs)
        content_parts: list[str] = []
        prompt_tokens = completion_tokens = 0
        finish_reason = ""
        # 工具调用在流式响应里是分片拼接的：按 index 聚合 id / name / arguments
        call_parts: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens or prompt_tokens
                completion_tokens = chunk.usage.completion_tokens or completion_tokens
            for choice in chunk.choices or []:
                delta = choice.delta
                if delta and delta.content:
                    content_parts.append(delta.content)
                    yield "delta", delta.content
                for tc in (getattr(delta, "tool_calls", None) or []):
                    slot = call_parts.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if fn.name:
                            slot["name"] = fn.name
                        if fn.arguments:
                            slot["arguments"] += fn.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for index, slot in sorted(call_parts.items())
            if slot["name"]
        ]
        yield "done", ModelReply(
            content="".join(content_parts),
            tool_calls=calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
            finish_reason=finish_reason,
        )


def _sanitize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """剥离 reasoning_content 等供应商私有字段，只保留标准角色字段。"""
    allowed = {"role", "content", "name", "tool_calls", "tool_call_id"}
    cleaned = []
    for msg in messages:
        cleaned.append({k: v for k, v in msg.items() if k in allowed})
    return cleaned
