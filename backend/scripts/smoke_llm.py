"""M0 自检：流式、tool calling、usage 三项。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402

WEATHER_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询城市天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]


async def main() -> int:
    settings = get_settings()
    if not settings.dashscope_api_key:
        print("FAIL: .env 未配置 DASHSCOPE_API_KEY")
        return 2

    client = LLMClient()
    print(f"模型: {client.model}")
    failures: list[str] = []

    # 1. 普通对话 + usage
    print("\n[1/3] 非流式对话与 usage")
    reply = await client.complete([{"role": "user", "content": "只回复两个字：收到"}])
    print(f"  content={reply.content[:40]!r}")
    print(f"  usage=prompt:{reply.prompt_tokens} completion:{reply.completion_tokens}")
    if not reply.content:
        failures.append("非流式返回空内容")
    if reply.prompt_tokens <= 0:
        failures.append("未返回 prompt_tokens")

    # 2. tool calling
    print("\n[2/3] tool calling")
    reply = await client.complete(
        [{"role": "user", "content": "用工具查一下杭州天气"}], tools=WEATHER_TOOL
    )
    names = [c.name for c in reply.tool_calls]
    print(f"  tool_calls={names}")
    if "get_weather" not in names:
        failures.append("未触发 get_weather 工具调用")
    else:
        args = reply.tool_calls[0].parse_args()
        print(f"  args={args}")
        if "city" not in args:
            failures.append("工具参数缺少 city")

    # 3. 流式 + include_usage
    print("\n[3/3] 流式输出与流式 usage")
    chunks = 0
    done = None
    async for kind, value in client.stream([{"role": "user", "content": "数到五，只输出数字"}]):
        if kind == "delta":
            chunks += 1
        else:
            done = value
    print(f"  chunks={chunks}")
    if done:
        print(f"  usage=prompt:{done.prompt_tokens} completion:{done.completion_tokens}")
    if chunks < 2:
        failures.append("流式返回的 chunk 少于 2 个")
    if not done or done.prompt_tokens <= 0:
        failures.append("流式模式未返回 usage（检查 stream_options.include_usage）")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: 三项自检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
