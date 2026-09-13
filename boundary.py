"""Citation 与 Content 之间交换的中立 v3 边界值。"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from re import Match, Pattern
from typing import Protocol, TypedDict

from agent.plugin_composition import Context, ServiceKey
from agent.plugin_contracts import ContentPart


@dataclass(frozen=True, slots=True)
class Reference:
    """Content provider 已校验的引用证据的本地结构适配。"""

    ref: str
    resolved_ref: str | None = None
    retrieval_ref: str | None = None


@dataclass(frozen=True, slots=True)
class TextSource:
    """文本协议读取的原文与 Markdown 保护区间。"""

    text: str
    literals: tuple[tuple[int, int], ...]

    def allows(self, start: int, end: int) -> bool:
        return not any(left < end and start < right for left, right in self.literals)

    def matches(self, pattern: Pattern[str]) -> Iterator[Match[str]]:
        return (
            match
            for match in pattern.finditer(self.text)
            if self.allows(match.start(), match.end())
        )


class SpanData(TypedDict):
    start: int
    end: int
    parts: tuple[ContentPart, ...]


class ContentPort(Protocol):
    async def register(
        self,
        ctx: Context,
        definition: Mapping[str, object],
        *,
        prepare: Callable[[], Mapping[str, object]] | None = None,
    ) -> object: ...


CONTENT = ServiceKey[ContentPort]("content.v2")


__all__ = ["CONTENT", "ContentPort", "Reference", "SpanData", "TextSource"]
