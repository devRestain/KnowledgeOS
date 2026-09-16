"""Deterministic, provider-free C18 triage proposals.

The C18 boundary reads one canonical note and returns a typed proposal in
memory.  It deliberately has no filesystem writer, provider client, Git
command, approval operation, or apply operation.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .note_engine import NoteContractError, NoteEngine, UnsafePathError, resolve_vault_relative_path

_CANDIDATE_TYPES = ("task", "idea", "question", "knowledge", "project", "source")
_SOURCE_TYPES = ("capture", "daily")
_SHA256 = r"^[0-9a-f]{64}$"


def triage_result_schema() -> dict[str, Any]:
    """Return the strict, portable result schema owned by C18."""

    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "type", "title", "reason", "source_sha256"],
        "properties": {
            "candidate_id": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,63}$"},
            "type": {"type": "string", "enum": list(_CANDIDATE_TYPES)},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "source_sha256": {"type": "string", "pattern": _SHA256},
        },
    }
    proposal = {
        "type": "object",
        "additionalProperties": False,
        "required": ["proposal_id", "action", "source", "input_sha256", "candidates", "requested_mutations"],
        "properties": {
            "proposal_id": {"type": "string", "format": "uuid"},
            "action": {"const": "triage"},
            "source": {"type": "string", "minLength": 1, "maxLength": 500},
            "input_sha256": {"type": "string", "pattern": _SHA256},
            "candidates": {"type": "array", "minItems": 1, "maxItems": 5, "items": candidate},
            "requested_mutations": {"type": "array", "maxItems": 0},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/triage-result.schema.json",
        "title": "KnowledgeOS deterministic triage result",
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "proposal", "warnings", "provider_called", "mutation_performed"],
        "properties": {
            "status": {"const": "PROPOSED"},
            "proposal": proposal,
            "warnings": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
            "provider_called": {"const": False},
            "mutation_performed": {"const": False},
        },
    }


def _problem(code: str, message: str) -> tuple[dict[str, Any], int]:
    return {"status": "FAIL", "errors": [{"code": code, "message": message}]}, 30


def _candidate_type(note_type: str, hint: object) -> str:
    if isinstance(hint, str) and hint in _CANDIDATE_TYPES:
        return hint
    if note_type == "daily":
        return "task"
    return "idea"


def deterministic_triage(
    root: str | Path,
    *,
    source_path: str,
    expected_sha256: str,
    locator: str | None = None,
    fragment_sha256: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Validate a bounded source and return one non-mutating proposal.

    ``locator`` and ``fragment_sha256`` are mandatory together for daily notes.
    The returned proposal id is UUIDv5 over the source path and input digest, so
    identical input always yields byte-identical JSON output.
    """

    if not isinstance(expected_sha256, str) or not re.fullmatch(_SHA256, expected_sha256):
        return _problem("TRIAGE_EXPECTED_HASH_INVALID", "expected_sha256 must be lowercase SHA-256")
    if (locator is None) != (fragment_sha256 is None):
        return _problem("TRIAGE_FRAGMENT_BINDING_INVALID", "locator and fragment_sha256 must be supplied together")
    if fragment_sha256 is not None and not re.fullmatch(_SHA256, fragment_sha256):
        return _problem("TRIAGE_FRAGMENT_HASH_INVALID", "fragment_sha256 must be lowercase SHA-256")
    try:
        vault = Path(root).resolve() / "KnowledgeHub"
        path = resolve_vault_relative_path(vault, source_path)
        if path.is_symlink() or not path.is_file():
            return _problem("TRIAGE_SOURCE_INVALID", "source must be a regular Vault note")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha256:
            return _problem("TRIAGE_SOURCE_DRIFT", "source digest does not match expected_sha256")
        text = content.decode("utf-8")
        typed = NoteEngine.from_root(root).typed_note(source_path, text)
    except (OSError, UnicodeError, UnsafePathError, NoteContractError, ValueError) as error:
        return _problem("TRIAGE_SOURCE_INVALID", str(error))

    if typed.note_type not in _SOURCE_TYPES:
        return _problem("TRIAGE_SOURCE_TYPE_INVALID", "only capture and daily notes are eligible")
    if typed.properties.get("sensitivity") == "confidential" or typed.properties.get("ai_policy") == "deny":
        return _problem("TRIAGE_PRIVACY_DENIED", "source privacy policy denies triage")
    if typed.note_type == "daily" and (locator is None or fragment_sha256 is None):
        return _problem("TRIAGE_DAILY_LOCATOR_REQUIRED", "daily sources require locator and fragment_sha256")

    title = str(typed.properties["title"])
    candidate_type = _candidate_type(typed.note_type, typed.properties.get("triage_hint"))
    candidate_id = hashlib.sha256(f"{source_path}\0{digest}".encode()).hexdigest()[:16]
    proposal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledgeos:triage:{source_path}:{digest}"))
    report: dict[str, Any] = {
        "status": "PROPOSED",
        "proposal": {
            "proposal_id": proposal_id,
            "action": "triage",
            "source": source_path,
            "input_sha256": digest,
            "candidates": [{
                "candidate_id": f"candidate-{candidate_id}",
                "type": candidate_type,
                "title": title,
                "reason": "deterministic source metadata classification; human selection creates a separate job",
                "source_sha256": digest,
            }],
            "requested_mutations": [],
        },
        "warnings": ["proposal_only", "no_provider_call", "no_vault_or_git_mutation"],
        "provider_called": False,
        "mutation_performed": False,
    }
    errors = sorted(Draft202012Validator(triage_result_schema()).iter_errors(report), key=lambda item: item.json_path)
    if errors:
        return _problem("TRIAGE_RESULT_INVALID", errors[0].message)
    return report, 0


def canonical_triage_json(report: Mapping[str, Any]) -> bytes:
    """Serialize a result for stable CLI output without persisting it."""

    return (json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
