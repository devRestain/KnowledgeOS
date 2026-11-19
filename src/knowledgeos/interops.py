"""References-only Experience transport to the actual HermesTrace Unix adapter.

Learning owns eligibility and durable outbox transitions. A dispatched request is never
replayed automatically: recovery queries its original effective request digest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import stat
import struct
from pathlib import Path
from typing import Any

from .core_records import pins
from .util import canonical, iso, parse_iso, sha256

MAX_FRAME = 32768
_ID = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})")
_RECEIPT_FIELDS = {"admission_level", "candidate_id", "disposition", "idempotency_key",
                   "owner_operation_id", "payload_digest", "receipt_id", "received_at",
                   "recipient_operation_id", "source_revision"}
_REFUSED = {"INVALID_PAYLOAD", "DIGEST_MISMATCH", "SCOPE_DENIED", "FORBIDDEN", "EXPIRED",
            "SEMANTIC_PROFILE_UNKNOWN", "IDEMPOTENCY_CONFLICT", "RATE_LIMITED", "MIGRATION_REQUIRED"}


def _binding(owner) -> tuple[Path, Path] | None:
    endpoint = owner.setting("C9", "hermestrace_socket")
    credential = owner.setting("C9", "hermestrace_credential_file")
    if not endpoint and not credential:
        return None
    if not isinstance(endpoint, str) or not isinstance(credential, str) \
            or not Path(endpoint).is_absolute() or not Path(credential).is_absolute():
        raise ValueError("C9 HermesTrace requires absolute socket and credential-file paths")
    return Path(endpoint), Path(credential)


def configured(owner) -> bool:
    return _binding(owner) is not None


def _secret(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("HermesTrace credential must be a private 0600 regular file")
    value = path.read_bytes()
    if len(value) != 32:
        raise ValueError("HermesTrace credential must contain exactly 32 bytes")
    return value


def status(owner) -> str:
    binding = _binding(owner)
    if binding is None:
        return "unconfigured"
    try:
        _secret(binding[1])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.5)
            connection.connect(str(binding[0]))
        return "reachable"
    except (OSError, ValueError):
        return "unreachable"


def _profile(owner) -> tuple[dict, dict, dict]:
    """Bind catalog/schema metadata to the Operation's exact selected Core release."""
    selected = pins(owner.config.root)
    root = Path(os.environ.get("KNOWLEDGEOS_CORE_ROOT") or owner.config.root.parents[1] / "Core")
    manifest = json.loads((root / "release/manifest.json").read_bytes())
    body = {name: manifest[name] for name in ("core_release_version", "files")}
    if manifest["release_digest"] != "sha256:" + sha256(canonical(body)) \
            or manifest["release_digest"] != selected["core_digest"]:
        raise ValueError("InterOps Core manifest differs from KnowledgeOS adoption")
    hashes = {row["path"]: row["sha256"] for row in manifest["files"]}

    def record(relative: str) -> dict:
        path = root / relative
        data = path.read_bytes()
        if path.is_symlink() or sha256(data) != hashes[relative]:
            raise ValueError("InterOps Core metadata bytes differ from the selected release")
        return json.loads(data)

    catalog = record("contracts/catalog.json")
    schema = record("schemas/ExperienceCandidate.schema.json")
    semantic = {key: value for key, value in catalog["semantic_bundle"].items() if key != "digest"}
    actual = {"core_version": catalog["core_release_version"], "core_digest": manifest["release_digest"],
              "schema_version": catalog["schema_bundle_version"], "semantic_version": semantic["version"],
              "semantic_digest": "sha256:" + sha256(canonical(semantic))}
    if actual != selected:
        raise ValueError("InterOps Core catalog pins differ from KnowledgeOS adoption")
    return selected, catalog, schema


def _exportable(candidate: dict, schema: dict) -> bool:
    """Reject undeclared text fields before crossing the transport boundary."""
    if not isinstance(candidate, dict) or not set(candidate).issubset(schema["properties"]) \
            or not set(schema["required"]).issubset(candidate):
        return False
    source = candidate["source_record"]
    source_schema = schema["properties"]["source_record"]
    if not isinstance(source, dict) or set(source) != set(source_schema["properties"]):
        return False
    evidence_schema = schema["properties"]["evidence_references"]["items"]
    evidence = candidate["evidence_references"]
    return candidate["classification"] in {"public", "internal"} and isinstance(evidence, list) and all(
        isinstance(ref, dict) and set(ref).issubset(evidence_schema["properties"])
        and set(evidence_schema["required"]).issubset(ref)
        and ref["classification"] in {"public", "internal"}
        and ref["availability"] == "available_by_owner_export"
        for ref in evidence)


def prepare(owner, entry: dict[str, Any]) -> dict[str, Any]:
    """Build once; caller commits this value as ``entry.interops`` before dispatch."""
    selected, catalog, schema = _profile(owner)
    candidate, outbox = entry["candidate"], entry["outbox"]
    if owner.config.operation_id != "knowledgeos" or not _exportable(candidate, schema):
        raise ValueError("Experience is not a references-only KnowledgeOS export")
    data = canonical(candidate)
    payload_digest = "sha256:" + sha256(data)
    expected = {"candidate_id": candidate["candidate_id"], "owner_operation_id": "knowledgeos",
                "recipient_operation_id": "hermestrace", "idempotency_key": candidate["idempotency_key"],
                "payload_digest": payload_digest}
    if any(outbox.get(key) != value for key, value in expected.items()) \
            or candidate["owner_operation_id"] != "knowledgeos" \
            or any(candidate.get(key) != selected[key] for key in
                   ("core_version", "core_digest", "semantic_version", "semantic_digest")):
        raise ValueError("Experience outbox identity, payload or Core pins differ")
    if len(data) > catalog["limits"]["max_experience_candidate_bytes"]:
        raise ValueError("Experience candidate exceeds the Core byte limit")
    key = sha256(candidate["idempotency_key"].encode("utf-8"))
    envelope = {
        **selected, "contract_version": catalog["capsule_contract_version"],
        "message_id": f"knowledgeos.message.{key}", "request_id": f"knowledgeos.request.{key}",
        "message_kind": "standard_submission", "idempotency_key": candidate["idempotency_key"],
        "sender_operation_id": "knowledgeos", "sender_principal_id": "knowledgeos.local",
        "recipient_operation_id": "hermestrace", "purpose": "experience_submission", "action": "submit",
        "scope": "experience", "resource_owner_operation_id": "hermestrace", "resource_kind": "exchange",
        "resource_id": "exchange.hermestrace.experience", "payload_type": "experience.candidate",
        "payload_reference_id": candidate["candidate_id"], "payload_digest": payload_digest,
        "payload_size_bytes": len(data), "classification": candidate["classification"],
        "semantic_term_ids": candidate["semantic_term_ids"], "issued_at": iso(owner.clock.now()),
        "ttl_seconds": catalog["limits"]["experience_ttl_seconds"], "hop_count": 0, "revision": 1,
    }
    fields = catalog["digest_policy"]["effective_request_fields"]
    return {"envelope": envelope, "request_digest": "sha256:" + sha256(canonical(
        {name: envelope[name] for name in fields}))}


def _read_exact(connection: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = connection.recv(count - len(chunks))
        if not chunk:
            raise ValueError("incomplete InterOps response")
        chunks.extend(chunk)
    return bytes(chunks)


def _receipt(value: Any, entry: dict) -> dict | None:
    if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS \
            or value["admission_level"] != "owner_durable" or value["disposition"] not in {"admitted", "rejected"}:
        return None
    envelope = entry["interops"]["envelope"]
    expected = {"candidate_id": envelope["payload_reference_id"], "owner_operation_id": "knowledgeos",
                "recipient_operation_id": "hermestrace", "idempotency_key": envelope["idempotency_key"],
                "payload_digest": envelope["payload_digest"]}
    if any(value[key] != expected_value for key, expected_value in expected.items()):
        return None
    for name in ("receipt_id", "source_revision"):
        if not isinstance(value[name], str) or len(value[name]) > 96 or not _ID.fullmatch(value[name]):
            return None
    timestamp = value["received_at"]
    if not isinstance(timestamp, str) or len(timestamp) > 40 or not _TIMESTAMP.fullmatch(timestamp):
        return None
    try:
        if parse_iso(timestamp).tzinfo is None:
            return None
    except (TypeError, ValueError):
        return None
    return value


def deliver(owner, entry: dict[str, Any]) -> dict[str, Any]:
    """Admit a pending entry, or query an unknown original request without resending."""
    result = {"delivery": "unknown", "receipt": None, "last_error_code": "OUTCOME_UNKNOWN"}
    query_only = entry["delivery"] in {"unknown", "delivering"}
    if entry["delivery"] not in {"pending", "unknown", "delivering"}:
        return result
    binding = _binding(owner)
    if binding is None:
        return {**result, "delivery": "unknown" if query_only else "pending", "last_error_code": "UNAVAILABLE"}
    candidate, envelope = entry["candidate"], entry["interops"]["envelope"]
    if (envelope["payload_digest"] != "sha256:" + sha256(canonical(candidate))
            or envelope["payload_size_bytes"] != len(canonical(candidate))
            or envelope["payload_reference_id"] != candidate["candidate_id"]
            or envelope["idempotency_key"] != candidate["idempotency_key"]):
        return {**result, "last_error_code": "DIGEST_MISMATCH"}
    request = ({"method": "query_admission", "sender_operation_id": "knowledgeos",
                "idempotency_key": envelope["idempotency_key"],
                "request_digest": entry["interops"]["request_digest"]} if query_only else
               {"method": "admit", "envelope": envelope, "candidate": candidate})
    body = canonical(request)
    if not 2 <= len(body) <= MAX_FRAME:
        return {**result, "delivery": "unknown" if query_only else "rejected", "last_error_code": "INVALID_PAYLOAD"}
    try:
        secret = _secret(binding[1])
    except (OSError, ValueError):
        return {**result, "delivery": "unknown" if query_only else "pending", "last_error_code": "SCOPE_DENIED"}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(float(owner.setting("C9", "hermestrace_timeout_seconds", 5)))
        try:
            connection.connect(str(binding[0]))
        except OSError:
            return {**result, "delivery": "unknown" if query_only else "pending", "last_error_code": "UNAVAILABLE"}
        try:
            signature = hmac.new(secret, body, hashlib.sha256).digest()
            connection.sendall(struct.pack("!I", len(body)) + signature + body)
            length = struct.unpack("!I", _read_exact(connection, 4))[0]
            if not 2 <= length <= MAX_FRAME:
                return result
            signature = _read_exact(connection, 32)
            data = _read_exact(connection, length)
            if not hmac.compare_digest(signature, hmac.new(secret, data, hashlib.sha256).digest()):
                return result
            response = json.loads(data)
            if not isinstance(response, dict) or set(response) != {"ok", "result"}:
                return result
            if response["ok"] is False:
                error = response["result"]
                code = error.get("code") if isinstance(error, dict) else None
                return {**result, "delivery": "rejected" if not query_only and code in _REFUSED else "unknown",
                        "last_error_code": code if isinstance(code, str) else "OUTCOME_UNKNOWN"}
            if response["ok"] is not True:
                return result
            receipt = _receipt(response["result"], entry)
            if receipt is not None:
                return {"delivery": "delivered" if receipt["disposition"] == "admitted" else "rejected",
                        "receipt": receipt, "last_error_code": None}
        except (OSError, ValueError, TypeError, KeyError):
            pass
    return result
