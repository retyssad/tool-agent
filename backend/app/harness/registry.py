"""工具注册表：schema、超时、读写分类、权限域、审计。

模型只能传 JSON 入参；路径白名单在工具层校验，不交给模型判断。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError


class ToolError(Exception):
    """可预期的工具失败，会作为工具结果返回给模型，而不是中断 run。"""


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    read_only: bool = True
    timeout_s: float = 30.0
    # 需要访问文件系统时，路径必须落在这些根目录内
    path_roots: list[Path] = field(default_factory=list)
    # 风险等级：read 直接执行 / write 直接执行并回显 /
    # destructive 需用户确认 / slow 立即返回 run_id
    risk: str = "read"
    # 前端渲染用的卡片类型，空表示不渲染
    card: str = ""
    # 哪些调用方可使用：agent（业务 Run）/ chat（对话助手）/ investigate（只读子 agent）
    audiences: tuple[str, ...] = ("agent",)

    @property
    def confirm_required(self) -> bool:
        return self.risk == "destructive"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"未知工具: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, allow: set[str] | None = None, audience: str | None = None) -> list[dict[str, Any]]:
        """转换为 OpenAI function calling 的 tools 数组。

        audience 过滤调用方可见的工具；allow 用于业务 agent 的显式白名单。
        """
        out = []
        for name, spec in sorted(self._tools.items()):
            if allow is not None and name not in allow:
                continue
            if audience is not None and audience not in spec.audiences:
                continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec.description,
                        "parameters": spec.args_model.model_json_schema(),
                    },
                }
            )
        return out

    async def call(self, name: str, args: dict[str, Any], *, run_id: int | None = None) -> Any:
        spec = self.get(name)
        try:
            parsed = spec.args_model.model_validate(args)
        except ValidationError as exc:
            raise ToolError(f"参数校验失败: {exc.errors()}") from exc
        self._check_roots(spec, parsed)
        result = await _with_timeout(spec, parsed, run_id)
        return result

    def _check_roots(self, spec: ToolSpec, parsed: BaseModel) -> None:
        if not spec.path_roots:
            return
        for field_name, value in parsed.model_dump().items():
            if not isinstance(value, str):
                continue
            # 仅校验看起来像路径的字段
            if not (value.startswith(("/", "\\")) or (len(value) > 2 and value[1] == ":")):
                continue
            resolved = Path(value).expanduser().resolve()
            if not any(resolved == root or root in resolved.parents for root in spec.path_roots):
                raise ToolError(f"路径超出白名单: {resolved}")


async def _with_timeout(spec: ToolSpec, parsed: BaseModel, run_id: int | None) -> Any:
    import asyncio

    kwargs = parsed.model_dump()
    if "run_id" in inspect.signature(spec.handler).parameters and run_id is not None:
        kwargs["run_id"] = run_id
    try:
        return await asyncio.wait_for(spec.handler(**kwargs), timeout=spec.timeout_s)
    except asyncio.TimeoutError as exc:
        raise ToolError(f"工具超时({spec.timeout_s}s): {spec.name}") from exc


registry = ToolRegistry()
