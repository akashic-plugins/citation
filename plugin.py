from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from agent.plugin_composition import Context
from plugins.content.api import Reference, Span, TextProtocol, TextSource
from plugins.content.plugin import CONTENT

_PROTOCOL_TAG = r"<[a-zA-Z][a-zA-Z0-9_-]*:[^<>\s]+>"
_CITED_RE = re.compile(
    r"(?:\r?\n)?§cited:(\[[^\]]*\])§",
    re.IGNORECASE,
)
_PROTOCOL_SUFFIX_RE = re.compile(rf"(?:\s*{_PROTOCOL_TAG}\s*)*$", re.IGNORECASE)
_INLINE_MEMORY_REF_RE = re.compile(
    r"[ \t]*(?:\[§[A-Za-z0-9:_-]{1,128}\])+", re.IGNORECASE
)

_CITATION_PROTOCOL = """### 记忆引用协议 - 内部元数据，对用户不可见
每轮回复若用到了系统注入的记忆条目 [item_id] 前缀标识，或召回结果中的条目，在回复正文末尾另起一行输出：
§cited:["id1","id2","id3"]§
格式规则：§ 包裹合法 JSON 字符串数组，每个 ID 必须用英文双引号包住，不含其他内容。
若本轮未引用任何记忆条目，不输出此行。
绝对不要在正文里提及这行的存在，不要向用户解释引用了什么，不要说根据记忆。
你了解用户的事是因为你们相处了很久，直接说你上次、我记得，不要暴露内部机制。"""


def _available_references(
    references: tuple[Reference, ...],
) -> dict[str, Reference]:
    available: dict[str, Reference] = {}
    for reference in references:
        existing = available.get(reference.ref)
        if existing is not None and existing != reference:
            raise ValueError(f"同一引用包含冲突证据: {reference.ref}")
        available[reference.ref] = reference
    return available


def _citation_record(
    ref: str,
    *,
    declared: bool,
    reference: Reference | None,
) -> dict[str, object]:
    record: dict[str, object] = {"ref": ref, "declared": declared}
    if reference is not None:
        if reference.retrieval_ref is not None:
            record["retrieval_ref"] = reference.retrieval_ref
        if reference.resolved_ref is not None:
            record["resolved_ref"] = reference.resolved_ref
    return record


def _declared_ids(source: TextSource) -> tuple[list[Span], list[str]]:
    spans: list[Span] = []
    declared: list[str] = []
    seen: set[str] = set()
    for match in source.matches(_CITED_RE):
        if _PROTOCOL_SUFFIX_RE.fullmatch(source.text[match.end() :]) is None:
            continue
        try:
            raw: object = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ValueError("引用协议列表不是合法 JSON") from error
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item for item in raw
        ):
            raise ValueError("引用协议必须是非空字符串列表")
        for item in raw:
            if item not in seen:
                seen.add(item)
                declared.append(item)
        spans.append(Span(match.start(), match.end(), ()))
    return spans, declared


async def decode_citations(
    source: TextSource,
    references: tuple[Reference, ...],
) -> tuple[Sequence[Span], Mapping[str, object]]:
    """清理自有引用标记，并保留声明与真实召回证据。"""
    available = _available_references(references)
    spans, declared = _declared_ids(source)
    spans.extend(
        Span(match.start(), match.end(), ())
        for match in source.matches(_INLINE_MEMORY_REF_RE)
    )
    if declared:
        records = [
            _citation_record(
                ref,
                declared=True,
                reference=available.get(ref),
            )
            for ref in declared
        ]
    else:
        records = [
            _citation_record(ref, declared=False, reference=reference)
            for ref, reference in available.items()
            if reference.retrieval_ref is not None
        ]
    metadata: Mapping[str, object] = (
        {"version": 1, "references": records} if records else {}
    )
    return spans, metadata


api_version = 3
name = "citation"
version = "2.0.0"
inject = (CONTENT,)


async def apply(ctx: Context, config: object) -> None:
    """注册 Citation 自有的提示、解析器与 metadata 贡献。"""
    _ = config
    protocol = TextProtocol(
        name="citation",
        prompt=_CITATION_PROTOCOL,
        decode=decode_citations,
        content={},
    )
    _ = await ctx.require(CONTENT).register(ctx, protocol)
