from __future__ import annotations

import json
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from vaultops.projection import retrieval_candidate_schema

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas/mcp"
MANIFEST_PATH = SCHEMA_ROOT / "capabilities.json"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validator_registry() -> Registry[Any]:
    registry: Registry[Any] = Registry()
    for path in SCHEMA_ROOT.glob("*.schema.json"):
        schema = _read(path)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


def test_mcp_schema_files_and_manifest_are_strict_and_well_formed() -> None:
    schemas = sorted(SCHEMA_ROOT.glob("*.schema.json"))
    assert len(schemas) == 10
    for path in schemas:
        schema = _read(path)
        Draft202012Validator.check_schema(schema)

    manifest = _read(MANIFEST_PATH)
    assert manifest["contract_id"] == "knowledgeos-vaultmcp-v2"
    assert manifest["application_schema_version"] == 2
    assert manifest["transport"] == {
        "kind": "stdio",
        "stdout": "protocol_frames_only",
        "stderr": "bounded_redacted_diagnostics_only",
        "http_listener": False,
        "host_shell": False,
    }
    assert manifest["server_scope"]["client_root_selectors"] is False
    assert manifest["server_scope"]["cross_vault_routing"] is False
    assert manifest["server_scope"]["state_selector"] is False
    assert manifest["server_scope"]["hermes_home_selector"] is False
    assert manifest["limits"] == {
        "request_utf8_bytes": 65536,
        "response_utf8_bytes": 102400,
        "query_characters": 4096,
        "path_characters": 500,
        "results": 20,
        "retrieve_hops": 1,
        "read_timeout_seconds": 30,
        "proposal_timeout_seconds": 30,
        "concurrent_calls": 4,
        "concurrent_proposal_writes": 1,
        "stderr_event_bytes": 4096,
        "stdout_diagnostic_bytes": 0,
    }
    assert [item["name"] for item in manifest["tools"]] == [
        "knowledge_search",
        "knowledge_retrieve",
        "proposal_inspect",
        "proposal_create",
    ]
    assert set(manifest["forbidden_tool_names"]).isdisjoint(
        item["name"] for item in manifest["tools"]
    )
    assert manifest["effects"]["proposal_create_action"] == "normalize"
    assert manifest["effects"]["proposal_target_change_allowed"] is False
    assert manifest["sdk"]["host_install_allowed"] is False


def test_every_tool_has_allowed_schema_denied_and_policy_denied_vectors() -> None:
    manifest = _read(MANIFEST_PATH)
    for tool in manifest["tools"]:
        input_schema = _read(SCHEMA_ROOT / tool["input_schema"])
        validator = Draft202012Validator(input_schema)
        assert tool["effect"]
        assert tool["service"].startswith("vaultops.application.knowledge.KnowledgeApplication.")
        assert tool["allowed_vectors"]
        assert tool["schema_denied_vectors"]
        assert tool["policy_denied_vectors"]

        for example in tool["allowed_vectors"]:
            assert validator.is_valid(example), (tool["name"], example)
        for example in tool["schema_denied_vectors"]:
            assert not validator.is_valid(example), (tool["name"], example)
        for example in tool["policy_denied_vectors"]:
            assert validator.is_valid(example), (tool["name"], example)


def test_result_envelopes_and_errors_are_versioned_and_bound() -> None:
    read_schema = _read(SCHEMA_ROOT / "read_result.data.schema.json")
    candidate_schema = read_schema["$defs"]["candidate"]
    assert (set(candidate_schema["properties"]) - {"resource_reference"}).issubset(
        retrieval_candidate_schema()["properties"]
    )
    read_data = {
        "query_sha256": "a" * 64,
        "policy_decision_sha256": "b" * 64,
        "index_generation_id": "stage3-fixture",
        "retrieval_config_sha256": "c" * 64,
        "candidate_count": 1,
        "candidates": [
            {
                "note_id": "fixture-note",
                "resource_reference": {"owner_operation_id": "knowledgeos", "resource_kind": "vault", "logical_id": "note.fixture", "content_digest": "sha256:" + "d" * 64, "source_revision": "d" * 64, "classification": "internal", "availability": "available_by_owner_export"},
                "chunk_id": "fixture-chunk",
                "chunk_hash": "e" * 64,
                "chunk_locator": "heading:Fixture",
            }
        ],
        "truncated": False,
    }
    envelope = {
        "schema_version": 2,
        "request_id": "fixture-request-1",
        "operation": "knowledge_search",
        "status": "ok",
        "effect": "read_only",
        "execution_outcome": "completed",
        "acceptance_state": "not_evaluated",
        "human_approval": "unknown",
        "canonical_target_changed": False,
        "data": read_data,
        "error": None,
    }
    schema = _read(SCHEMA_ROOT / "tool_result.schema.json")
    validator = Draft202012Validator(schema, registry=_validator_registry())
    assert validator.is_valid(envelope)
    assert validator.is_valid(
        {
            **envelope,
            "status": "stale_generation",
            "data": None,
            "error": {
                "code": "STALE_GENERATION",
                "message": "The current index generation changed.",
                "retryable": True,
            },
        }
    )
    assert not validator.is_valid({**envelope, "canonical_target_changed": True})
    assert not validator.is_valid({**envelope, "private_detail": "absolute host path"})


def test_candidate_file_and_privileged_controls_are_not_schema_inputs() -> None:
    for name in (
        "knowledge_search.input.schema.json",
        "knowledge_retrieve.input.schema.json",
        "proposal_inspect.input.schema.json",
        "proposal_create.input.schema.json",
    ):
        schema = _read(SCHEMA_ROOT / name)
        properties = set(schema["properties"])
        assert properties.isdisjoint(
            {
                "root",
                "control_root",
                "vault_root",
                "state_root",
                "hermes_home",
                "candidate_set_file",
                "provider",
                "operation",
                "action",
                "include_review",
                "use_vector",
            }
        )


def test_mcp_sdk_version_provenance_and_lock_graph_are_pinned() -> None:
    manifest = _read(MANIFEST_PATH)
    sdk = manifest["sdk"]

    assert sdk["distribution"] == "mcp"
    assert sdk["upstream"] == "modelcontextprotocol/python-sdk"
    assert sdk["exact_version"] == "2.2.0"
    assert sdk["package_index"] == "https://pypi.org/simple"
    assert sdk["lock_status"] == "sha256_pinned_in_ops_uv_lock"
    assert sdk["dependency_download_scope"] == "one_shot_knowledgeos_project_container_only"
    assert sdk["runtime_network_enabled"] is False
    assert sdk["host_install_allowed"] is False

    lock = tomllib.loads((SCHEMA_ROOT.parents[1] / "uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    mcp_package = packages["mcp"]
    assert mcp_package["version"] == sdk["exact_version"]
    assert mcp_package["source"]["registry"] == sdk["package_index"]
    assert sdk["wheel_sha256"] in {
        wheel["hash"].removeprefix("sha256:") for wheel in mcp_package["wheels"]
    }
    assert packages["mcp-types"]["version"] == sdk["exact_version"]
    assert version("mcp") == sdk["exact_version"]
