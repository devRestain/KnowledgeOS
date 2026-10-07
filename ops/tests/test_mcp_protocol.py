from __future__ import annotations

import asyncio
import json
import sys
import threading

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from support.control_factory import make_separate_portable_fixture_roots

from vaultops import mcp_server as mcp_adapter
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.note_engine import parse_frontmatter, render_frontmatter
from vaultops.projection import generate_projection

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"


def test_stdio_v3_tools_logical_refs_readonly_review_and_create_replay(tmp_path):
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    source = roots.vault / SOURCE
    doc = parse_frontmatter(source.read_text())
    source.write_text(render_frontmatter(dict(reversed(list(doc.properties.items()))), doc.body))
    before = source.read_bytes()
    report, code = generate_projection(roots)
    assert code == 0, report
    app = KnowledgeApplication.from_roots(roots)
    params = StdioServerParameters(command=sys.executable, args=["-m", "vaultops.mcp_server", "--control-root", str(roots.control)], env={"PYTHONPATH": "/workspace/control/ops/src"}, cwd=tmp_path)

    async def exercise():
        async with Client(params, mode="legacy") as client:
            listing = await client.list_tools()
            names = [item.name for item in listing.tools]
            assert len(names) == 24 and len(set(names)) == 24
            assert {"knowledge_read", "base_view_query", "link_context", "task_query",
                    "work_context_read", "assessment_submit", "proposal_draft_note"} <= set(names)
            with pytest.raises(MCPError):
                await client.call_tool("proposal_approve", {})
            denied = await client.call_tool("proposal_create", {"source_reference": app.reference(SOURCE), "actor_id": "owner"})
            assert denied.is_error
            retrieved = await client.call_tool("knowledge_retrieve", {"query": "공포"})
            data = retrieved.structured_content
            assert not retrieved.is_error, data
            assert data["data"]["candidates"]
            for item in data["data"]["candidates"]:
                assert "path" not in item and item["resource_reference"]["owner_operation_id"] == "knowledgeos"
            evidence = data["data"]["candidates"][0]
            read = await client.call_tool("knowledge_read", {
                "resource_reference": evidence["resource_reference"],
                "chunk_locator": evidence["chunk_locator"],
            })
            assert not read.is_error and read.structured_content["data"]["chunk_hash"] == evidence["chunk_hash"]
            denied_work = await client.call_tool("work_context_read", {})
            assert denied_work.is_error and denied_work.structured_content["error"]["code"] == "SCOPE_DENIED"
            first = await client.call_tool("proposal_create", {"source_reference": app.reference(SOURCE)})
            assert not first.is_error, first.structured_content
            created = first.structured_content
            assert created["effect"] == "proposal_created"
            assert created["canonical_target_changed"] is False
            repeated = await client.call_tool("proposal_create", {"source_reference": app.reference(SOURCE)})
            assert not repeated.is_error and repeated.structured_content["effect"] == "proposal_replayed"
            state_before = app.journal.path.read_bytes()
            artifact = created["data"]["proposal_reference"]
            inspected = await client.call_tool("proposal_inspect", {"proposal_reference": artifact})
            assert not inspected.is_error, inspected.structured_content
            assert app.journal.path.read_bytes() == state_before
            assert str(roots.vault) not in json.dumps(inspected.structured_content)
            foreign = await client.call_tool("proposal_inspect", {"proposal_reference": {**artifact, "owner_operation_id": "foreign"}})
            assert foreign.is_error
    asyncio.run(exercise())
    assert source.read_bytes() == before
    assert len(list((roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1


def test_cancellation_keeps_active_writer_until_thread_finishes():
    async def exercise():
        semaphore = asyncio.Semaphore(1)
        writer = asyncio.Lock()
        started, finish, completed = threading.Event(), threading.Event(), threading.Event()
        def operation():
            started.set()
            finish.wait(timeout=2)
            completed.set()
            return {}, 0
        task = asyncio.create_task(mcp_adapter._run_bounded(operation, semaphore=semaphore, writer=writer, timeout=10))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer.locked()
        finish.set()
        assert await asyncio.to_thread(completed.wait, 1)
        for _ in range(100):
            if not writer.locked():
                break
            await asyncio.sleep(0.01)
        assert not writer.locked()
    asyncio.run(exercise())


def test_proposal_timeout_marks_only_started_work_unknown_and_retains_writer() -> None:
    async def exercise() -> None:
        semaphore = asyncio.Semaphore(1)
        writer = asyncio.Lock()
        await writer.acquire()
        never_started = threading.Event()
        with pytest.raises(mcp_adapter._OperationTimeout) as queued_timeout:
            await mcp_adapter._run_bounded(
                lambda: (never_started.set() or ({}, 0)),
                semaphore=semaphore,
                writer=writer,
                timeout=0.03,
            )
        assert queued_timeout.value.effect_uncertain is False
        assert not never_started.is_set()
        writer.release()

        started = threading.Event()
        finish = threading.Event()
        completed = threading.Event()

        def blocked_operation() -> tuple[dict[str, object], int]:
            started.set()
            finish.wait(timeout=2)
            completed.set()
            return {}, 0

        with pytest.raises(mcp_adapter._OperationTimeout) as running_timeout:
            await mcp_adapter._run_bounded(
                blocked_operation,
                semaphore=semaphore,
                writer=writer,
                timeout=0.05,
            )
        assert await asyncio.to_thread(started.wait, 1)
        assert running_timeout.value.effect_uncertain is True
        assert writer.locked()
        finish.set()
        assert await asyncio.to_thread(completed.wait, 1)
        for _ in range(100):
            if not writer.locked():
                break
            await asyncio.sleep(0.01)
        assert not writer.locked()
        await semaphore.acquire()
        semaphore.release()

    asyncio.run(exercise())
