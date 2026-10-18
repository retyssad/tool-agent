"""上下文装配：显式 token 预算、截断与快照。

上下文是构造出来的，不是"把整个仓库塞进去"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def rough_tokens(text: str) -> int:
    """中文按字符近似，英文按 4 字符 ≈ 1 token。用于预算控制，不用于计费。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    others = len(text) - cjk
    return max(1, cjk + (others + 3) // 4)


@dataclass(slots=True)
class ContextBudget:
    max_tokens: int = 24_000
    reserved_for_output: int = 4_000
    used: int = 0

    @property
    def available(self) -> int:
        return max(0, self.max_tokens - self.reserved_for_output - self.used)

    def fits(self, text: str) -> bool:
        return rough_tokens(text) <= self.available

    def take(self, text: str) -> bool:
        cost = rough_tokens(text)
        if cost > self.available:
            return False
        self.used += cost
        return True


@dataclass(slots=True)
class ContextBlock:
    """一段可追溯的上下文：来自哪里、被截断没有。"""

    label: str
    content: str
    source: str = ""
    truncated: bool = False
    tokens: int = 0


@dataclass(slots=True)
class AssembledContext:
    blocks: list[ContextBlock] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = []
        for block in self.blocks:
            marker = "（已截断）" if block.truncated else ""
            parts.append(f"## {block.label}{marker}\n{block.content}")
        return "\n\n".join(parts)

    def snapshot(self) -> dict[str, Any]:
        return {
            "blocks": [
                {"label": b.label, "source": b.source, "tokens": b.tokens, "truncated": b.truncated}
                for b in self.blocks
            ],
            "dropped": self.dropped,
        }


def assemble(
    budget: ContextBudget,
    blocks: list[ContextBlock],
    *,
    truncate_tail: bool = True,
) -> AssembledContext:
    """按优先级顺序装入；装不下的要么截断要么丢弃，并记录原因。"""
    marker = "\n…（截断）"
    ctx = AssembledContext()
    for block in blocks:
        cost = rough_tokens(block.content)
        block.tokens = cost
        if budget.take(block.content):
            ctx.blocks.append(block)
            continue
        if truncate_tail and budget.available > 50:
            # 中文约 1 字 1 token，英文约 4 字符 1 token；按最保守的字符数截断
            keep_chars = max(0, budget.available - rough_tokens(marker) - 1)
            while keep_chars > 0:
                candidate = block.content[:keep_chars] + marker
                if budget.take(candidate):
                    block.content = candidate
                    block.truncated = True
                    block.tokens = rough_tokens(candidate)
                    ctx.blocks.append(block)
                    break
                keep_chars //= 2
            else:
                ctx.dropped.append(block.label)
            if block.truncated:
                continue
        if not block.truncated:
            ctx.dropped.append(block.label)
    return ctx
