"""Exercise the installed MCP entrypoint against disposable roots, outside pytest."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from support.control_factory import make_separate_portable_fixture_roots
from vaultops.projection import generate_projection


async def exercise(control: Path, directory: str) -> None:
    executable = shutil.which("vaultmcp")
    if executable is None:
        raise RuntimeError("Installed vaultmcp entrypoint is missing")
    # Isolated Python ignores source-mount PYTHONPATH and user-site packages.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-I", executable, "--control-root", str(control)],
        cwd=directory,
    )
    async with Client(params, mode="legacy") as client:
        listing = await client.list_tools()
        assert "knowledge_retrieve" in {tool.name for tool in listing.tools}
        status = await client.call_tool("index_status_read", {})
        assert not status.is_error, status.structured_content
        assert status.structured_content["data"]["lexical_state"] == "ready"
        result = await client.call_tool("knowledge_retrieve", {"query": "공포"})
        assert not result.is_error, result.structured_content
        candidates = result.structured_content["data"]["candidates"]
        evidence = next(item for item in candidates if item["resource_reference"])
        excerpt = await client.call_tool("knowledge_read", {
            "resource_reference": evidence["resource_reference"],
            "chunk_locator": evidence["chunk_locator"],
        })
        assert not excerpt.is_error, excerpt.structured_content
        assert excerpt.structured_content["data"]["chunk_hash"] == evidence["chunk_hash"]
        citation = await client.call_tool("citation_verify", {
            "citations": [{key: evidence[key] for key in (
                "resource_reference", "chunk_locator", "chunk_hash")}],
        })
        assert not citation.is_error, citation.structured_content
        assert citation.structured_content["data"]["all_valid"] is True
        assert citation.structured_content["data"]["truth_evaluated"] is False
        denied = await client.call_tool("work_context_read", {})
        assert denied.is_error and denied.structured_content["error"]["code"] == "SCOPE_DENIED"
        print("PASS: installed MCP index status, retrieval, citation verification and unbound Work denial")


def main() -> None:
    with TemporaryDirectory(prefix="knowledgeos-installed-mcp-") as directory:
        roots = make_separate_portable_fixture_roots(Path(directory), review_queues=True)
        report, code = generate_projection(roots)
        assert code == 0, report
        asyncio.run(exercise(roots.control, directory))


if __name__ == "__main__":
    main()
