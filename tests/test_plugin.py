from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import plugin as citation_module
from agent.plugins.composable import ComposablePlugin
from agent.plugins.manager import PluginManager
from agent.plugins.snapshot import bind_runtime_snapshot, reset_runtime_snapshot
from agent.plugins.static_manifest import load_static_plugin_manifest
from bus.event_bus import EventBus
from plugins.content.api import Reference, TextSource
from plugins.content import plugin as content_module
from plugins.content.plugin import CONTENT
from session.log import MessageLog
from session.message import ContentPart, Output
from session.message_codec import json_value


def _copy_ignore():
    return shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__")


def _visible(parts: tuple[ContentPart, ...]) -> str:
    return "".join(str(part.value) for part in parts if part.kind == "text")


def test_static_manifest_matches_v3_module() -> None:
    manifest = load_static_plugin_manifest(
        Path(citation_module.__file__ or "").resolve().parent
    )
    assert manifest.name == citation_module.name == "citation"
    assert manifest.version == citation_module.version == "2.0.0"
    assert manifest.api_version == citation_module.api_version == 3
    assert manifest.entrypoint == "plugin.py"
    assert citation_module.inject == (CONTENT,)


@pytest.mark.asyncio
async def test_prompt_example_is_accepted_by_the_decoder() -> None:
    example = next(
        line
        for line in citation_module._CITATION_PROTOCOL.splitlines()
        if line.startswith("§cited:")
    )
    spans, metadata = await citation_module.decode_citations(
        TextSource(example, ()), ()
    )
    assert len(spans) == 1
    assert [record["ref"] for record in metadata["references"]] == ["id1", "id2", "id3"]


@pytest.mark.asyncio
async def test_decoder_tracks_declared_and_retrieved_evidence() -> None:
    source = TextSource(
        '答复 [§known]\n§cited:["known","unknown"]§ <meme:shy>',
        (),
    )
    spans, metadata = await citation_module.decode_citations(
        source,
        (Reference("known", "memory@2", "retrieval:1"),),
    )
    assert [(span.start, span.end, span.parts) for span in spans]
    assert metadata == {
        "version": 1,
        "references": [
            {
                "ref": "known",
                "declared": True,
                "retrieval_ref": "retrieval:1",
                "resolved_ref": "memory@2",
            },
            {"ref": "unknown", "declared": True},
        ],
    }


@pytest.mark.asyncio
async def test_decoder_fallback_requires_real_retrieval_evidence() -> None:
    spans, metadata = await citation_module.decode_citations(
        TextSource("普通答复", ()),
        (
            Reference("resolved-only", "memory@1"),
            Reference("retrieved", "memory@2", "retrieval:2"),
        ),
    )
    assert spans == []
    assert metadata == {
        "version": 1,
        "references": [
            {
                "ref": "retrieved",
                "declared": False,
                "retrieval_ref": "retrieval:2",
                "resolved_ref": "memory@2",
            }
        ],
    }


@pytest.mark.asyncio
async def test_real_manager_content_service_preserves_literals_and_other_protocols(
    tmp_path: Path,
) -> None:
    core_plugins = Path(content_module.__file__).parents[1]
    plugin_home = tmp_path / "plugins"
    shutil.copytree(core_plugins / "content", plugin_home / "content")
    shutil.copytree(
        Path(__file__).parents[1],
        plugin_home / "citation",
        ignore=_copy_ignore(),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    log = MessageLog(workspace / "sessions.db")
    manager = PluginManager(
        plugin_dirs=[plugin_home],
        event_bus=EventBus(),
        workspace=workspace,
        message_log=log,
    )
    await manager.load_all()
    snapshot = manager.current_snapshot
    assert snapshot is not None and snapshot.composition_root is not None
    generation = manager.generation("citation")
    assert generation is not None
    assert isinstance(generation.instance, ComposablePlugin)
    assert snapshot.composition_topology is not None
    assert snapshot.composition_topology.listeners == ()

    lease = manager._snapshot_store.lease()  # pyright: ignore[reportPrivateUsage]
    token = bind_runtime_snapshot(lease)
    try:
        content = snapshot.composition_root.context.require(CONTENT)
        async with content.bind() as view:
            assert len(view.prompts) == 1
            raw = (
                "答复 [§known]\n"
                '`§cited:["literal"]§`\n'
                '§cited:["known","unknown"]§ <meme:shy>'
            )
            parts, metadata = await view.decode(
                raw,
                (Reference("known", "memory@2", "retrieval:1"),),
            )
            assert _visible(parts) == ('答复\n`§cited:["literal"]§` <meme:shy>')
            assert json_value(metadata["citation"]) == {
                "version": 1,
                "references": [
                    {
                        "ref": "known",
                        "declared": True,
                        "retrieval_ref": "retrieval:1",
                        "resolved_ref": "memory@2",
                    },
                    {"ref": "unknown", "declared": True},
                ],
            }
            writer = log.writer(
                "session",
                author="model",
                source="conversation",
                body_types=(Output,),
                content=view.checks,
                check_metadata=view.check_metadata,
            )
            saved = writer.append(
                "reply",
                Output(parts, "complete"),
                metadata=metadata,
            )
            assert (
                json_value(saved.metadata)["citation"]["references"][0]["resolved_ref"]
                == "memory@2"
            )
    finally:
        reset_runtime_snapshot(token)
        await lease.release()
        root = snapshot.composition_root
        await manager.terminate_all()
        log.close()
        assert root.receipt().effects == ()
