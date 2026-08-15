from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

import plugin as citation_module
from agent.core.response_parser import ResponseMetadata
from agent.lifecycle.composition import (
    AFTER_REASONING_CLEANUP_EVENT,
    AFTER_REASONING_PREPROCESS_EVENT,
    PROMPT_RENDER_EVENT,
)
from agent.lifecycle.types import AfterReasoningCtx, PromptRenderCtx
from agent.plugin_composition import CompositionRoot, PluginRuntime
from agent.plugins.composable import ComposablePlugin
from agent.plugins.manager import PluginManager
from bus.event_bus import EventBus
from plugin import (
    CITATION_PROTOCOL_SERVICE,
    CitationAfterReasoningModule,
    CitationPromptModule,
    ProtocolTagCleanupModule,
    apply,
    extract_cited_ids,
    extract_cited_ids_from_tool_chain,
    inject,
)


def _prompt_ctx() -> PromptRenderCtx:
    return PromptRenderCtx(
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


def _answer_ctx(reply: str) -> AfterReasoningCtx:
    return AfterReasoningCtx(
        session_key="telegram:1",
        channel="telegram",
        chat_id="1",
        tools_used=(),
        thinking=None,
        response_metadata=ResponseMetadata(raw_text=reply),
        streamed=False,
        tool_chain=(),
        context_retry={},
        reply=reply,
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
    ctx = _prompt_ctx()
    frame = SimpleNamespace(slots={"prompt:ctx": ctx})
    await CitationPromptModule().run(frame)
    assert ctx.system_sections_bottom[0].name == "citation_protocol"


@pytest.mark.asyncio
async def test_after_reasoning_modules_strip_and_persist() -> None:
    ctx = _answer_ctx("答复正文\n§cited:[mem_1]§ <meme:shy>")
    frame = SimpleNamespace(slots={"reasoning:ctx": ctx})
    await CitationAfterReasoningModule().run(frame)
    assert frame.slots["persist:assistant:cited_memory_ids"] == ["mem_1"]
    assert ctx.reply == "答复正文 <meme:shy>"
    await ProtocolTagCleanupModule().run(frame)
    assert ctx.reply == "答复正文"


@pytest.mark.asyncio
async def test_v3_named_exports_match_legacy_lifecycle_behavior(
    tmp_path: Path,
) -> None:
    ComposablePlugin.from_module(citation_module)
    root = CompositionRoot("citation-parity")

    async def mount(ctx) -> None:
        await apply(ctx, object())

    _ = await root.mount(
        mount,
        name="citation",
        inject=inject,
        runtime=PluginRuntime(
            plugin_id="citation",
            plugin_dir=Path(__file__).parents[1],
            data_dir=tmp_path / "data",
            workspace=tmp_path / "workspace",
            config=object(),
        ),
    )
    assert root.receipt().ready is True
    assert root.context.require(CITATION_PROTOCOL_SERVICE).version == 1

    legacy_prompt = _prompt_ctx()
    await CitationPromptModule().run(
        SimpleNamespace(slots={"prompt:ctx": legacy_prompt})
    )
    v3_prompt = _prompt_ctx()
    await root.context.serial(PROMPT_RENDER_EVENT, v3_prompt)
    assert v3_prompt.system_sections_bottom == legacy_prompt.system_sections_bottom

    reply = "答复正文\n§cited:[mem_1]§ <meme:shy>"
    legacy_answer = _answer_ctx(reply)
    legacy_frame = SimpleNamespace(slots={"reasoning:ctx": legacy_answer})
    await CitationAfterReasoningModule().run(legacy_frame)
    await ProtocolTagCleanupModule().run(legacy_frame)
    v3_answer = _answer_ctx(reply)
    await root.context.serial(AFTER_REASONING_PREPROCESS_EVENT, v3_answer)
    await root.context.serial(AFTER_REASONING_CLEANUP_EVENT, v3_answer)

    assert v3_answer.reply == legacy_answer.reply
    assert v3_answer.persist_assistant_metadata["cited_memory_ids"] == (
        legacy_frame.slots["persist:assistant:cited_memory_ids"]
    )
    await root.dispose()


@pytest.mark.asyncio
async def test_v3_plugin_loads_through_real_generation_manager(
    tmp_path: Path,
) -> None:
    plugin_home = tmp_path / "plugins"
    plugin_home.mkdir()
    shutil.copytree(
        Path(__file__).parents[1],
        plugin_home / "citation",
        ignore=shutil.ignore_patterns(
            ".akashic-core",
            ".git",
            ".plugin-contracts",
            ".pytest_cache",
            "__pycache__",
        ),
    )
    manager = PluginManager(
        plugin_dirs=[plugin_home],
        event_bus=EventBus(),
        tool_registry=None,
        workspace=tmp_path / "workspace",
        installed_cache_root=tmp_path / "plugin-home" / "cache",
    )

    await manager.load_all()

    generation = manager.generation("citation")
    snapshot = manager.current_snapshot
    assert generation is not None and snapshot is not None
    assert isinstance(generation.instance, ComposablePlugin)
    assert snapshot.composition_topology is not None
    assert "citation.protocol" in snapshot.composition_topology.services
    assert snapshot.composition_topology.listeners == (
        "serial:turn.prompt_render:citation",
        "serial:turn.after_reasoning.preprocess:citation",
        "serial:turn.after_reasoning.cleanup:citation",
    )
    await manager.terminate_all()
