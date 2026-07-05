from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent.core.response_parser import ResponseMetadata
from agent.lifecycle.types import AfterReasoningCtx, PromptRenderCtx
from plugin import (
    CitationAfterReasoningModule,
    CitationPromptModule,
    ProtocolTagCleanupModule,
    extract_cited_ids,
    extract_cited_ids_from_tool_chain,
)


def test_extract_cited_ids_keeps_trailing_meme_tag() -> None:
    clean, ids = extract_cited_ids("答复正文\n§cited:[mem_1]§ <meme:shy>")
    assert clean == "答复正文 <meme:shy>"
    assert ids == ["mem_1"]


def test_extract_cited_ids_from_recall_memory_tool_chain() -> None:
    cited = extract_cited_ids_from_tool_chain(
        [
            {
                "calls": [
                    {
                        "name": "recall_memory",
                        "result": '{"items":[{"id":"mem_1"},{"id":"mem_2"}]}',
                    }
                ]
            }
        ]
    )
    assert cited == ["mem_1", "mem_2"]


@pytest.mark.asyncio
async def test_prompt_module_injects_protocol() -> None:
    ctx = PromptRenderCtx(
        session_key="telegram:1",
        channel="telegram",
        chat_id="1",
        content="hi",
        media=None,
        timestamp=datetime.now(timezone.utc),
        history=[],
        skill_names=[],
        retrieved_memory_block="",
        disabled_sections=set(),
        turn_injection_prompt="",
    )
    frame = SimpleNamespace(slots={"prompt:ctx": ctx})
    await CitationPromptModule().run(frame)
    assert ctx.system_sections_bottom[0].name == "citation_protocol"


@pytest.mark.asyncio
async def test_after_reasoning_modules_strip_and_persist() -> None:
    ctx = AfterReasoningCtx(
        session_key="telegram:1",
        channel="telegram",
        chat_id="1",
        tools_used=(),
        thinking=None,
        response_metadata=ResponseMetadata(raw_text="答复正文"),
        streamed=False,
        tool_chain=(),
        context_retry={},
        reply="答复正文\n§cited:[mem_1]§ <meme:shy>",
    )
    frame = SimpleNamespace(slots={"reasoning:ctx": ctx})
    await CitationAfterReasoningModule().run(frame)
    assert frame.slots["persist:assistant:cited_memory_ids"] == ["mem_1"]
    assert ctx.reply == "答复正文 <meme:shy>"
    await ProtocolTagCleanupModule().run(frame)
    assert ctx.reply == "答复正文"
