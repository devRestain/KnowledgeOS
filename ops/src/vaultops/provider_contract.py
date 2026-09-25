"""C31 provider-neutral envelopes and frozen-context boundaries.

The C31 surface stops at the private runtime boundary. It does not contact a
provider, mount a Vault, apply a proposal, or interpret model output. The
networkless core may place the bounded prompt and privacy-filtered evidence in
one immutable mode-0600 context envelope. Requests, responses, failures,
authorizations, and identity receipts bind that envelope by digest while
keeping raw prompt and response bytes out of long-lived receipts and reports.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .generation_identity import generation_inference_options
from .recovery import fsync_directory

SCHEMA_VERSION = 1
CONTRACT_ID = "knowledgeos-provider-envelopes-v1"
GENERATOR_ID = "vaultops.provider_contract"

PROVIDER_REQUEST_SCHEMA_PATH = "ops/schemas/provider-request.schema.json"
PROVIDER_RESPONSE_SCHEMA_PATH = "ops/schemas/provider-response.schema.json"
PROVIDER_RECEIPT_SCHEMA_PATH = "ops/schemas/provider-receipt.schema.json"
PROVIDER_FAILURE_SCHEMA_PATH = "ops/schemas/provider-failure.schema.json"
REMOTE_AUTHORIZATION_SCHEMA_PATH = "ops/schemas/remote-authorization.schema.json"
FROZEN_CONTEXT_SCHEMA_PATH = "ops/schemas/frozen-context.schema.json"
PROVIDER_SCHEMA_PATHS = (
    PROVIDER_REQUEST_SCHEMA_PATH,
    PROVIDER_RESPONSE_SCHEMA_PATH,
    PROVIDER_RECEIPT_SCHEMA_PATH,
    PROVIDER_FAILURE_SCHEMA_PATH,
    REMOTE_AUTHORIZATION_SCHEMA_PATH,
    FROZEN_CONTEXT_SCHEMA_PATH,
)

RUNTIME_CONTEXT_FILENAME = "context.json"
RUNTIME_REQUEST_FILENAME = "request.json"

LIMITS: dict[str, int] = {
    "max_request_bytes": 64 * 1024,
    "max_context_bytes": 1024 * 1024,
    "max_prompt_bytes": 128 * 1024,
    "max_response_bytes": 256 * 1024,
    "max_failure_message_bytes": 1000,
    "max_candidates": 50,
    "max_source_hashes": 50,
    "max_warnings": 20,
    "max_json_depth": 8,
    "max_output_tokens": 1024,
    "max_timeout_seconds": 600,
    "max_job_age_seconds": 3600,
}

DEFAULT_INFERENCE_OPTIONS: dict[str, Any] = generation_inference_options(
    max_output_tokens=LIMITS["max_output_tokens"],
    timeout_seconds=LIMITS["max_timeout_seconds"],
)

ALLOWED_ACTIONS = (
    "triage",
    "draft_note",
    "link_suggestions",
    "normalize",
    "summarize",
    "answer",
    "organize",
    "relate",
    "extract",
    "inbox",
    "project-summary",
)
ALLOWED_RESPONSE_STATUSES = (
    "completed",
    "refused",
    "failed",
    "cancelled",
    "timeout",
    "overloaded",
    "conflict",
)
ALLOWED_FAILURE_PHASES = (
    "validation",
    "authorization",
    "context",
    "provider",
    "output",
    "transport",
    "lifecycle",
)
ALLOWED_FAILURE_CODES = (
    "INVALID_INPUT",
    "POLICY_DENIED",
    "AUTHORIZATION_REQUIRED",
    "AUTHORIZATION_EXPIRED",
    "CONTEXT_INVALID",
    "CONTEXT_TOO_LARGE",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_REFUSED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_OVERLOADED",
    "OUTPUT_INVALID",
    "OUTPUT_TOO_LARGE",
    "OUTPUT_SCHEMA_INVALID",
    "CANCELLED",
    "DIGEST_CONFLICT",
)
ALLOWED_POLICY_DECISIONS = ("allow_local", "allow_remote", "deny", "ask")
ALLOWED_ELIGIBILITY_CLASSES = ("local_eligible", "remote_eligible")
ALLOWED_CHANNELS = ("lexical", "vector", "rrf", "graph", "exact")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ACTION = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_PATH = re.compile(r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.{1,2}(?:/|$))[^\r\n\x00]+$")
_MODEL_TAG = re.compile(r"^(?!.*(?:^|:)latest$)[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ROUTE = re.compile(r"^(?:codex_chatgpt_login|openai_api_relay|local:[a-z0-9][a-z0-9._-]{0,63})$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")


class ProviderContractError(ValueError):
    """Base error for invalid, stale, or unsafe C31 envelope state."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProviderConflict(ProviderContractError):
    """Raised when an immutable runtime artifact has different bytes."""


class ProviderSchemaError(ProviderContractError):
    """Raised when an envelope does not satisfy its strict JSON Schema."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically without permitting NaN or infinity."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProviderContractError("C31_JSON_INVALID", "value is not canonical JSON") from error


def _hash_schema() -> dict[str, Any]:
    return {"type": "string", "pattern": _SHA256.pattern}


def _uuid_schema(prefix: str | None = None) -> dict[str, Any]:
    if prefix is None:
        return {
            "type": "string",
            "minLength": 36,
            "maxLength": 36,
            "pattern": _UUID4.pattern,
        }
    return {"type": "string", "pattern": rf"^{re.escape(prefix)}[0-9a-f]{{32}}$"}


def _datetime_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "format": "date-time",
        "minLength": 20,
        "maxLength": 40,
    }


def _path_schema(*, max_length: int = 500) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": max_length,
        "pattern": _PATH.pattern,
    }


def _bounded_text(max_length: int) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": max_length}


def _nullable_hash() -> dict[str, Any]:
    return {"anyOf": [_hash_schema(), {"type": "null"}]}


def _provider_identity_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["route", "model_tag", "model_digest", "ollama_version"],
        "properties": {
            "route": {"type": "string", "pattern": _ROUTE.pattern},
            "model_tag": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": _MODEL_TAG.pattern,
            },
            "model_digest": _hash_schema(),
            "ollama_version": {
                "anyOf": [
                    {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                        "pattern": _VERSION.pattern,
                    },
                    {"type": "null"},
                ]
            },
        },
    }


def _binding_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "sha256"],
        "properties": {"path": _path_schema(), "sha256": _hash_schema()},
    }


def _policy_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "decision",
            "policy_sha256",
            "privacy_policy_sha256",
            "scope",
            "authorization_sha256",
        ],
        "properties": {
            "decision": {"type": "string", "enum": list(ALLOWED_POLICY_DECISIONS)},
            "policy_sha256": _hash_schema(),
            "privacy_policy_sha256": _hash_schema(),
            "scope": {"type": "string", "minLength": 1, "maxLength": 200},
            "authorization_sha256": _nullable_hash(),
        },
    }


def _inference_options_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "num_ctx",
            "stream",
            "think",
            "tools",
            "temperature",
            "seed",
            "max_output_tokens",
            "keep_alive",
            "parallel_requests",
            "timeout_seconds",
        ],
        "properties": {
            "num_ctx": {"type": "integer", "minimum": 1, "maximum": 8192},
            "stream": {"const": False},
            "think": {"const": False},
            "tools": {"const": False},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
            "max_output_tokens": {
                "type": "integer",
                "minimum": 1,
                "maximum": LIMITS["max_output_tokens"],
            },
            "keep_alive": {"type": "integer", "minimum": 0, "maximum": 3600},
            "parallel_requests": {"const": 1},
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": LIMITS["max_timeout_seconds"],
            },
        },
    }


def _limits_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(LIMITS),
        "properties": {key: {"const": value} for key, value in LIMITS.items()},
    }


def _runner_boundary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "vault_mount",
            "canonical_apply",
            "network_access",
            "raw_prompt_storage",
            "raw_response_storage",
            "model_output_untrusted",
        ],
        "properties": {
            "vault_mount": {"const": False},
            "canonical_apply": {"const": False},
            "network_access": {"enum": ["none", "separately_authorized_loopback"]},
            "raw_prompt_storage": {"const": "private_runtime_only"},
            "raw_response_storage": {"const": "private_runtime_only"},
            "model_output_untrusted": {"const": True},
        },
    }


def _source_hash_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["note_id", "path", "content_hash", "chunk_hash", "locator"],
        "properties": {
            "note_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "path": _path_schema(),
            "content_hash": _hash_schema(),
            "chunk_hash": _nullable_hash(),
            "locator": {"anyOf": [_bounded_text(500), {"type": "null"}]},
        },
    }


def _candidate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "candidate_sha256",
            "note_id",
            "path",
            "content_hash",
            "chunk_id",
            "chunk_hash",
            "chunk_locator",
            "index_generation_id",
            "excerpt",
            "excerpt_sha256",
            "excerpt_byte_length",
            "eligibility_class",
            "channel",
            "rank",
            "retrieval_reason",
        ],
        "properties": {
            "candidate_sha256": _hash_schema(),
            "note_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "path": _path_schema(),
            "content_hash": _hash_schema(),
            "chunk_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "chunk_hash": _hash_schema(),
            "chunk_locator": _bounded_text(1000),
            "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "excerpt": {"type": "string", "minLength": 1, "maxLength": 4096},
            "excerpt_sha256": _hash_schema(),
            "excerpt_byte_length": {"type": "integer", "minimum": 1, "maximum": 16384},
            "eligibility_class": {
                "type": "string",
                "enum": list(ALLOWED_ELIGIBILITY_CLASSES),
            },
            "channel": {"type": "string", "enum": list(ALLOWED_CHANNELS)},
            "rank": {
                "type": "integer",
                "minimum": 1,
                "maximum": LIMITS["max_candidates"],
            },
            "retrieval_reason": _bounded_text(1000),
        },
    }


def _context_properties() -> dict[str, Any]:
    return {
        "schema_version": {"const": SCHEMA_VERSION},
        "contract_id": {"const": CONTRACT_ID},
        "job_id": _uuid_schema(),
        "context_id": _uuid_schema("ctx-"),
        "created_at": _datetime_schema(),
        "expires_at": _datetime_schema(),
        "action": {"type": "string", "pattern": _ACTION.pattern},
        "prompt": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "sha256", "byte_length"],
            "properties": {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": LIMITS["max_prompt_bytes"],
                },
                "sha256": _hash_schema(),
                "byte_length": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": LIMITS["max_prompt_bytes"],
                },
            },
        },
        "output_schema": _binding_schema(),
        "policy_decision": _policy_schema(),
        "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "source_hashes": {
            "type": "array",
            "minItems": 1,
            "maxItems": LIMITS["max_source_hashes"],
            "items": _source_hash_schema(),
        },
        "source_hashes_sha256": _hash_schema(),
        "frozen_candidates": {
            "type": "array",
            "minItems": 1,
            "maxItems": LIMITS["max_candidates"],
            "items": _candidate_schema(),
        },
        "candidate_set_sha256": _hash_schema(),
        "provider": _provider_identity_schema(),
        "inference_options": _inference_options_schema(),
        "limits": _limits_schema(),
        "runner_boundary": _runner_boundary_schema(),
        "context_sha256": _hash_schema(),
    }


def frozen_context_schema() -> dict[str, Any]:
    """Return the strict private runtime frozen-context schema."""

    properties = _context_properties()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/frozen-context.schema.json",
        "title": "KnowledgeOS C31 frozen provider context",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def provider_request_schema() -> dict[str, Any]:
    """Return the request envelope schema with no raw prompt field."""

    properties = {
        "schema_version": {"const": SCHEMA_VERSION},
        "contract_id": {"const": CONTRACT_ID},
        "job_id": _uuid_schema(),
        "request_id": _uuid_schema("req-"),
        "created_at": _datetime_schema(),
        "context_path": {
            "type": "string",
            "pattern": r"^runtime/runs/[0-9a-f-]{36}/context\.json$",
        },
        "context_sha256": _hash_schema(),
        "context_byte_length": {
            "type": "integer",
            "minimum": 1,
            "maximum": LIMITS["max_context_bytes"],
        },
        "prompt_sha256": _hash_schema(),
        "output_schema": _binding_schema(),
        "policy_decision": _policy_schema(),
        "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "candidate_set_sha256": _hash_schema(),
        "source_hashes_sha256": _hash_schema(),
        "action": {"type": "string", "pattern": _ACTION.pattern},
        "provider": _provider_identity_schema(),
        "inference_options": _inference_options_schema(),
        "limits": _limits_schema(),
        "runner_boundary": _runner_boundary_schema(),
        "request_sha256": _hash_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/provider-request.schema.json",
        "title": "KnowledgeOS C31 provider request envelope",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def provider_response_schema() -> dict[str, Any]:
    """Return the bounded untrusted provider response envelope schema."""

    properties = {
        "schema_version": {"const": SCHEMA_VERSION},
        "contract_id": {"const": CONTRACT_ID},
        "job_id": _uuid_schema(),
        "response_id": _uuid_schema("res-"),
        "request_sha256": _hash_schema(),
        "context_sha256": _hash_schema(),
        "received_at": _datetime_schema(),
        "completed_at": _datetime_schema(),
        "status": {"type": "string", "enum": list(ALLOWED_RESPONSE_STATUSES)},
        "provider": _provider_identity_schema(),
        "output_schema": _binding_schema(),
        "output": {},
        "output_sha256": _nullable_hash(),
        "output_byte_length": {
            "type": "integer",
            "minimum": 0,
            "maximum": LIMITS["max_response_bytes"],
        },
        "output_token_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": LIMITS["max_output_tokens"],
        },
        "warnings": {
            "type": "array",
            "maxItems": LIMITS["max_warnings"],
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        },
        "runner_boundary": _runner_boundary_schema(),
        "model_output_untrusted": {"const": True},
        "response_sha256": _hash_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/provider-response.schema.json",
        "title": "KnowledgeOS C31 provider response envelope",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def provider_receipt_schema() -> dict[str, Any]:
    """Return the identity receipt schema; it intentionally has no raw fields."""

    properties = {
        "schema_version": {"const": SCHEMA_VERSION},
        "contract_id": {"const": CONTRACT_ID},
        "receipt_id": _uuid_schema("rcpt-"),
        "job_id": _uuid_schema(),
        "status": {"type": "string", "enum": list(ALLOWED_RESPONSE_STATUSES)},
        "created_at": _datetime_schema(),
        "completed_at": {"anyOf": [_datetime_schema(), {"type": "null"}]},
        "request_sha256": _hash_schema(),
        "context_sha256": _hash_schema(),
        "response_sha256": _nullable_hash(),
        "prompt_sha256": _hash_schema(),
        "output_schema_sha256": _hash_schema(),
        "policy_decision_sha256": _hash_schema(),
        "candidate_set_sha256": _hash_schema(),
        "source_hashes_sha256": _hash_schema(),
        "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "provider": _provider_identity_schema(),
        "inference_options_sha256": _hash_schema(),
        "remote_authorization_sha256": _nullable_hash(),
        "provider_called": {"type": "boolean"},
        "vault_mutation_performed": {"const": False},
        "canonical_apply_allowed": {"const": False},
        "raw_prompt_storage": {"const": "private_runtime_only"},
        "raw_response_storage": {"const": "private_runtime_only"},
        "receipt_sha256": _hash_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/provider-receipt.schema.json",
        "title": "KnowledgeOS C31 provider identity receipt",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def provider_failure_schema() -> dict[str, Any]:
    """Return the bounded failure schema with digest-only bindings."""

    properties = {
        "schema_version": {"const": SCHEMA_VERSION},
        "contract_id": {"const": CONTRACT_ID},
        "failure_id": _uuid_schema("fail-"),
        "job_id": _uuid_schema(),
        "occurred_at": _datetime_schema(),
        "phase": {"type": "string", "enum": list(ALLOWED_FAILURE_PHASES)},
        "code": {"type": "string", "enum": list(ALLOWED_FAILURE_CODES)},
        "message": {
            "type": "string",
            "minLength": 1,
            "maxLength": LIMITS["max_failure_message_bytes"],
        },
        "retryable": {"type": "boolean"},
        "request_sha256": _nullable_hash(),
        "context_sha256": _nullable_hash(),
        "provider": {"anyOf": [_provider_identity_schema(), {"type": "null"}]},
        "provider_called": {"type": "boolean"},
        "vault_mutation_performed": {"const": False},
        "raw_payload_persisted": {"const": False},
        "failure_sha256": _hash_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/provider-failure.schema.json",
        "title": "KnowledgeOS C31 provider failure envelope",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def remote_authorization_schema() -> dict[str, Any]:
    """Return the explicit, digest-bound remote authorization schema."""

    properties = {
        "schema_version": {"const": SCHEMA_VERSION},
        "contract_id": {"const": CONTRACT_ID},
        "authorization_id": _uuid_schema("auth-"),
        "job_id": _uuid_schema(),
        "decision": {"enum": ["approved", "denied", "expired"]},
        "authorized_at": _datetime_schema(),
        "expires_at": _datetime_schema(),
        "actor": {"type": "string", "minLength": 1, "maxLength": 200},
        "tty_confirmed": {"const": True},
        "scope": {"const": "one_job"},
        "provider": _provider_identity_schema(),
        "binds": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "request_sha256",
                "context_sha256",
                "prompt_sha256",
                "output_schema_sha256",
                "policy_decision_sha256",
                "candidate_set_sha256",
                "source_hashes_sha256",
                "index_generation_id",
            ],
            "properties": {
                "request_sha256": _hash_schema(),
                "context_sha256": _hash_schema(),
                "prompt_sha256": _hash_schema(),
                "output_schema_sha256": _hash_schema(),
                "policy_decision_sha256": _hash_schema(),
                "candidate_set_sha256": _hash_schema(),
                "source_hashes_sha256": _hash_schema(),
                "index_generation_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
            },
        },
        "authorization_sha256": _hash_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/remote-authorization.schema.json",
        "title": "KnowledgeOS C31 remote authorization envelope",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def provider_schema(kind: str) -> dict[str, Any]:
    """Return one generated schema by stable artifact kind."""

    schemas = {
        "request": provider_request_schema,
        "response": provider_response_schema,
        "receipt": provider_receipt_schema,
        "failure": provider_failure_schema,
        "authorization": remote_authorization_schema,
        "context": frozen_context_schema,
    }
    try:
        return schemas[kind]()
    except KeyError as error:
        raise ProviderContractError(
            "C31_SCHEMA_KIND_INVALID", f"unknown provider schema kind: {kind}"
        ) from error


def schema_bytes(schema: Mapping[str, Any]) -> bytes:
    """Serialize a generated schema deterministically."""

    return (
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _provider_envelope_declaration(blueprint: Mapping[str, Any]) -> Mapping[str, Any]:
    llm = blueprint.get("llm")
    declaration = llm.get("provider_envelopes") if isinstance(llm, Mapping) else None
    if not isinstance(declaration, Mapping):
        raise ProviderContractError(
            "C31_BLUEPRINT_CONTRACT_MISSING",
            "/llm/provider_envelopes is missing",
        )
    return declaration


def validate_blueprint_provider_contract(blueprint: Mapping[str, Any]) -> None:
    """Fail closed when the C31 Blueprint limits or boundary drift."""

    declaration = _provider_envelope_declaration(blueprint)
    if declaration.get("schema_version") != SCHEMA_VERSION:
        raise ProviderContractError(
            "C31_BLUEPRINT_SCHEMA_VERSION",
            "provider envelope schema_version must be 1",
        )
    if declaration.get("contract_id") != CONTRACT_ID:
        raise ProviderContractError(
            "C31_BLUEPRINT_CONTRACT_ID",
            "provider envelope contract_id is invalid",
        )
    limits = declaration.get("limits")
    if not isinstance(limits, Mapping) or dict(limits) != LIMITS:
        raise ProviderContractError(
            "C31_BLUEPRINT_LIMITS_DRIFT",
            "provider envelope limits differ from C31",
        )
    schemas = declaration.get("schemas")
    expected_schemas = {
        "request": PROVIDER_REQUEST_SCHEMA_PATH,
        "response": PROVIDER_RESPONSE_SCHEMA_PATH,
        "receipt": PROVIDER_RECEIPT_SCHEMA_PATH,
        "failure": PROVIDER_FAILURE_SCHEMA_PATH,
        "authorization": REMOTE_AUTHORIZATION_SCHEMA_PATH,
        "context": FROZEN_CONTEXT_SCHEMA_PATH,
    }
    if schemas != expected_schemas:
        raise ProviderContractError(
            "C31_BLUEPRINT_SCHEMA_PATHS",
            "provider envelope schema paths differ from C31",
        )
    if declaration.get("runtime_root") != "runtime":
        raise ProviderContractError(
            "C31_BLUEPRINT_RUNTIME_ROOT",
            "provider envelopes must be private runtime artifacts",
        )
    privacy = declaration.get("privacy")
    expected_privacy = {
        "raw_prompt": "private_runtime_only",
        "raw_response": "private_runtime_only",
        "receipt_content": "hashes_only",
        "ordinary_logs": "codes_and_hashes_only",
    }
    if privacy != expected_privacy:
        raise ProviderContractError(
            "C31_BLUEPRINT_PRIVACY",
            "provider envelope privacy boundary drifted",
        )
    boundary = declaration.get("runner_boundary")
    expected_boundary = {
        "vault_mount": False,
        "canonical_apply": False,
        "networkless_core": True,
        "model_output_untrusted": True,
    }
    if boundary != expected_boundary:
        raise ProviderContractError(
            "C31_BLUEPRINT_BOUNDARY",
            "provider runner boundary drifted",
        )


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderContractError("C31_MAPPING_INVALID", f"{label} must be an object")
    return value


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderContractError("C31_LIST_INVALID", f"{label} must be an array")
    return value


def _validate_uuid(value: object, label: str) -> str:
    if not isinstance(value, str) or not _UUID4.fullmatch(value):
        raise ProviderContractError("C31_UUID_INVALID", f"{label} must be a lowercase UUIDv4")
    return value


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ProviderContractError("C31_HASH_INVALID", f"{label} must be a lowercase SHA-256")
    return value


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProviderContractError("C31_TIME_INVALID", f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ProviderContractError("C31_TIME_INVALID", f"{label} must be an ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderContractError("C31_TIME_INVALID", f"{label} must include a timezone")
    return parsed


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json_depth(value: Any, depth: int = 0) -> int:
    if isinstance(value, (Mapping, list)) and not value:
        return depth + 1
    if isinstance(value, Mapping):
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return depth
    return max(_json_depth(item, depth + 1) for item in children)


def _validate_json_depth(value: Any) -> None:
    if _json_depth(value) > LIMITS["max_json_depth"]:
        raise ProviderContractError(
            "C31_JSON_DEPTH_EXCEEDED",
            "envelope JSON depth exceeds the C31 limit",
        )


def _validate_schema(document: Mapping[str, Any], kind: str) -> None:
    errors = sorted(
        Draft202012Validator(
            provider_schema(kind),
            format_checker=FormatChecker(),
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        locator = "/".join(str(part) for part in error.path) or "/"
        raise ProviderSchemaError(
            "C31_SCHEMA_INVALID",
            f"{kind} envelope invalid at {locator}: {error.message}",
        )
    _validate_json_depth(document)


def _validate_byte_length(text: str, expected: int, label: str, maximum: int) -> None:
    actual = len(text.encode("utf-8"))
    if actual != expected:
        raise ProviderContractError(
            "C31_BYTE_LENGTH_MISMATCH",
            f"{label} byte_length does not match UTF-8 bytes",
        )
    if actual > maximum:
        raise ProviderContractError(
            "C31_BYTE_LIMIT_EXCEEDED",
            f"{label} exceeds the C31 byte limit",
        )


def _without_digest(document: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(document)
    result.pop(field, None)
    return result


def _verify_provider_identity(provider: Mapping[str, Any]) -> None:
    route = provider.get("route")
    model_tag = provider.get("model_tag")
    model_digest = provider.get("model_digest")
    if not isinstance(route, str) or not _ROUTE.fullmatch(route):
        raise ProviderContractError("C31_ROUTE_INVALID", "provider route is not allowlisted")
    if not isinstance(model_tag, str) or not _MODEL_TAG.fullmatch(model_tag):
        raise ProviderContractError(
            "C31_MODEL_TAG_INVALID",
            "model tag must not use latest or an unqualified alias",
        )
    _validate_hash(model_digest, "/provider/model_digest")
    version = provider.get("ollama_version")
    if version is not None and (
        not isinstance(version, str) or not _VERSION.fullmatch(version)
    ):
        raise ProviderContractError(
            "C31_OLLAMA_VERSION_INVALID",
            "Ollama version is invalid",
        )


def _normalize_provider(value: Mapping[str, Any]) -> dict[str, Any]:
    provider = dict(value)
    _verify_provider_identity(provider)
    return provider


def _normalize_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    policy = dict(value)
    if policy.get("decision") not in ALLOWED_POLICY_DECISIONS:
        raise ProviderContractError(
            "C31_POLICY_DECISION_INVALID",
            "policy decision is not recognized",
        )
    _validate_hash(policy.get("policy_sha256"), "/policy_decision/policy_sha256")
    _validate_hash(
        policy.get("privacy_policy_sha256"),
        "/policy_decision/privacy_policy_sha256",
    )
    if not isinstance(policy.get("scope"), str) or not policy["scope"]:
        raise ProviderContractError(
            "C31_POLICY_SCOPE_INVALID",
            "policy scope is required",
        )
    authorization = policy.get("authorization_sha256")
    if authorization is not None:
        _validate_hash(authorization, "/policy_decision/authorization_sha256")
    return policy


def _normalize_options(value: Mapping[str, Any] | None) -> dict[str, Any]:
    options = dict(DEFAULT_INFERENCE_OPTIONS if value is None else value)
    if options.get("max_output_tokens", 0) > LIMITS["max_output_tokens"]:
        raise ProviderContractError(
            "C31_OUTPUT_TOKEN_LIMIT",
            "max_output_tokens exceeds the C31 limit",
        )
    if options.get("timeout_seconds", 0) > LIMITS["max_timeout_seconds"]:
        raise ProviderContractError(
            "C31_TIMEOUT_LIMIT",
            "timeout_seconds exceeds the C31 limit",
        )
    return options


def _normalize_source_hashes(value: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not 1 <= len(value) <= LIMITS["max_source_hashes"]:
        raise ProviderContractError(
            "C31_SOURCE_HASH_COUNT",
            "source_hashes count is outside the C31 limit",
        )
    result = [dict(_as_mapping(item, "source_hashes item")) for item in value]
    seen: set[tuple[str, str | None]] = set()
    for item in result:
        _validate_hash(item.get("content_hash"), "source content hash")
        chunk_hash = item.get("chunk_hash")
        if chunk_hash is not None:
            _validate_hash(chunk_hash, "source chunk hash")
        key = (str(item.get("path")), item.get("locator"))
        if key in seen:
            raise ProviderContractError(
                "C31_SOURCE_DUPLICATE",
                "source hash locator is duplicated",
            )
        seen.add(key)
    return result


def _candidate_without_digest(candidate: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(candidate)
    result.pop("candidate_sha256", None)
    return result


def _normalize_candidates(
    value: Sequence[Mapping[str, Any]],
    *,
    provider: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not 1 <= len(value) <= LIMITS["max_candidates"]:
        raise ProviderContractError(
            "C31_CANDIDATE_COUNT",
            "frozen_candidates count is outside the C31 limit",
        )
    result: list[dict[str, Any]] = []
    route = str(provider["route"])
    for item in value:
        candidate = dict(_as_mapping(item, "frozen candidate"))
        excerpt = candidate.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt or "\x00" in excerpt:
            raise ProviderContractError(
                "C31_EXCERPT_INVALID",
                "candidate excerpt must be bounded UTF-8 text",
            )
        excerpt_bytes = excerpt.encode("utf-8")
        if len(excerpt_bytes) > 16_384:
            raise ProviderContractError(
                "C31_EXCERPT_TOO_LARGE",
                "candidate excerpt exceeds the byte limit",
            )
        observed_excerpt_hash = _sha256(excerpt_bytes)
        if candidate.get("excerpt_sha256") != observed_excerpt_hash:
            raise ProviderContractError(
                "C31_EXCERPT_DRIFT",
                "candidate excerpt digest does not match bytes",
            )
        if candidate.get("excerpt_byte_length") != len(excerpt_bytes):
            raise ProviderContractError(
                "C31_EXCERPT_LENGTH",
                "candidate excerpt byte length is invalid",
            )
        eligibility = candidate.get("eligibility_class")
        if eligibility not in ALLOWED_ELIGIBILITY_CLASSES:
            raise ProviderContractError(
                "C31_ELIGIBILITY_INVALID",
                "candidate eligibility class is invalid",
            )
        if not route.startswith("local:") and eligibility != "remote_eligible":
            raise ProviderContractError(
                "C31_PRIVACY_DENIED",
                "candidate is not eligible for the provider route",
            )
        candidate_digest = candidate.get("candidate_sha256")
        observed_candidate_digest = _sha256(
            canonical_json_bytes(_candidate_without_digest(candidate))
        )
        if candidate_digest != observed_candidate_digest:
            raise ProviderContractError(
                "C31_CANDIDATE_DRIFT",
                "candidate digest does not match frozen fields",
            )
        result.append(candidate)
    if [item.get("rank") for item in result] != sorted(item.get("rank") for item in result):
        raise ProviderContractError(
            "C31_CANDIDATE_ORDER",
            "frozen candidates must be rank ordered",
        )
    return result


def _hash_collection(value: Sequence[Mapping[str, Any]]) -> str:
    return _sha256(canonical_json_bytes(list(value)))


def _validate_context_semantics(context: Mapping[str, Any]) -> None:
    prompt = _as_mapping(context["prompt"], "prompt")
    prompt_text = prompt.get("text")
    if not isinstance(prompt_text, str) or "\x00" in prompt_text:
        raise ProviderContractError(
            "C31_PROMPT_INVALID",
            "prompt must be bounded text without NUL",
        )
    _validate_byte_length(
        prompt_text,
        int(prompt["byte_length"]),
        "prompt",
        LIMITS["max_prompt_bytes"],
    )
    if _sha256(prompt_text.encode("utf-8")) != prompt["sha256"]:
        raise ProviderContractError(
            "C31_PROMPT_DRIFT",
            "prompt digest does not match bytes",
        )
    source_hashes = _as_list(context["source_hashes"], "source_hashes")
    if context["source_hashes_sha256"] != _hash_collection(source_hashes):
        raise ProviderContractError(
            "C31_SOURCE_HASH_DRIFT",
            "source hash set does not match its digest",
        )
    candidates = _as_list(context["frozen_candidates"], "frozen_candidates")
    if context["candidate_set_sha256"] != _hash_collection(candidates):
        raise ProviderContractError(
            "C31_CANDIDATE_SET_DRIFT",
            "candidate set does not match its digest",
        )
    created = _parse_time(context["created_at"], "created_at")
    expires = _parse_time(context["expires_at"], "expires_at")
    if expires <= created or expires - created > timedelta(seconds=LIMITS["max_job_age_seconds"]):
        raise ProviderContractError(
            "C31_EXPIRY_INVALID",
            "context expiry exceeds the C31 job age limit",
        )
    if context["context_sha256"] != _sha256(
        canonical_json_bytes(_without_digest(context, "context_sha256"))
    ):
        raise ProviderContractError(
            "C31_CONTEXT_DRIFT",
            "context_sha256 does not match frozen envelope bytes",
        )


def validate_context_envelope(context: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached frozen context mapping."""

    value = deepcopy(dict(_as_mapping(context, "context envelope")))
    _validate_schema(value, "context")
    _validate_context_semantics(value)
    provider = _as_mapping(value["provider"], "provider")
    _verify_provider_identity(provider)
    return value


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ProviderContractError(
            "C31_ROOT_INVALID",
            "control root must be an existing non-symlink directory",
        )
    return candidate.resolve()


def _runtime_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise ProviderContractError(
                "C31_RUNTIME_PATH_INVALID",
                f"runtime path is not a directory: {path}",
            )
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise ProviderContractError(
                "C31_RUNTIME_MODE_INVALID",
                f"runtime directory must be mode 0700: {path}",
            )
        return
    path.mkdir(mode=0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ProviderContractError(
            "C31_RUNTIME_MODE_INVALID",
            f"runtime directory was not mode 0700: {path}",
        )


def _exclusive_private_write(path: Path, payload: bytes) -> str:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ProviderContractError(
            "C31_RUNTIME_FILE_INVALID",
            f"runtime artifact is not a regular file: {path}",
        )
    if path.is_file():
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ProviderContractError(
                "C31_RUNTIME_MODE_INVALID",
                f"runtime file must be mode 0600: {path}",
            )
        existing = path.read_bytes()
        if existing == payload:
            return "NO_OP"
        raise ProviderConflict(
            "C31_IMMUTABLE_CONFLICT",
            f"runtime artifact has different bytes: {path}",
        )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except BaseException:
        if path.exists() or path.is_symlink():
            path.unlink()
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)
    return "CREATED"


def _job_path(workspace: Path, job_id: str, filename: str) -> Path:
    _validate_uuid(job_id, "job_id")
    runtime = workspace / "runtime"
    runs = runtime / "runs"
    job = runs / job_id
    _runtime_directory(runtime)
    _runtime_directory(runs)
    _runtime_directory(job)
    return job / filename


def _write_report(
    workspace: Path,
    path: Path,
    status: str,
    *,
    digest: str,
    byte_length: int,
    created: bool,
) -> dict[str, Any]:
    return {
        "status": "PASS" if status in {"CREATED", "NO_OP"} else status,
        "operation": "provider context envelope",
        "capability": "C31",
        "path": path.relative_to(workspace).as_posix(),
        "write": status,
        "sha256": digest,
        "byte_length": byte_length,
        "created": created,
        "provider_called": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
    }


def build_frozen_context(
    *,
    job_id: str,
    action: str,
    prompt: str,
    output_schema: Mapping[str, Any],
    policy_decision: Mapping[str, Any],
    frozen_candidates: Sequence[Mapping[str, Any]],
    index_generation_id: str,
    source_hashes: Sequence[Mapping[str, Any]],
    provider: Mapping[str, Any],
    inference_options: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build one bounded context without writing it or contacting a provider."""

    job = _validate_uuid(job_id, "job_id")
    if not isinstance(action, str) or not _ACTION.fullmatch(action):
        raise ProviderContractError("C31_ACTION_INVALID", "action is invalid")
    if not isinstance(prompt, str) or not prompt or "\x00" in prompt:
        raise ProviderContractError(
            "C31_PROMPT_INVALID",
            "prompt must be non-empty text without NUL",
        )
    prompt_bytes = prompt.encode("utf-8")
    if len(prompt_bytes) > LIMITS["max_prompt_bytes"]:
        raise ProviderContractError(
            "C31_PROMPT_TOO_LARGE",
            "prompt exceeds the C31 byte limit",
        )
    output = dict(_as_mapping(output_schema, "output_schema"))
    _validate_hash(output.get("sha256"), "output_schema.sha256")
    policy = _normalize_policy(_as_mapping(policy_decision, "policy_decision"))
    provider_value = _normalize_provider(_as_mapping(provider, "provider"))
    options = _normalize_options(inference_options)
    candidates = _normalize_candidates(frozen_candidates, provider=provider_value)
    sources = _normalize_source_hashes(source_hashes)
    if (
        not isinstance(index_generation_id, str)
        or not index_generation_id
        or len(index_generation_id) > 128
    ):
        raise ProviderContractError(
            "C31_INDEX_GENERATION_INVALID",
            "index_generation_id is invalid",
        )
    created = created_at or _now()
    created_time = _parse_time(created, "created_at")
    expires = (
        created_time + timedelta(seconds=LIMITS["max_job_age_seconds"])
    ).isoformat(timespec="seconds")
    prompt_record = {
        "text": prompt,
        "sha256": _sha256(prompt_bytes),
        "byte_length": len(prompt_bytes),
    }
    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "job_id": job,
        "context_id": "",
        "created_at": created,
        "expires_at": expires,
        "action": action,
        "prompt": prompt_record,
        "output_schema": output,
        "policy_decision": policy,
        "index_generation_id": index_generation_id,
        "source_hashes": sources,
        "source_hashes_sha256": _hash_collection(sources),
        "frozen_candidates": candidates,
        "candidate_set_sha256": _hash_collection(candidates),
        "provider": provider_value,
        "inference_options": options,
        "limits": dict(LIMITS),
        "runner_boundary": {
            "vault_mount": False,
            "canonical_apply": False,
            "network_access": "none",
            "raw_prompt_storage": "private_runtime_only",
            "raw_response_storage": "private_runtime_only",
            "model_output_untrusted": True,
        },
    }
    seed = canonical_json_bytes(
        {key: context[key] for key in context if key != "context_id"}
    )
    context["context_id"] = f"ctx-{_sha256(seed)[:32]}"
    context["context_sha256"] = _sha256(
        canonical_json_bytes(_without_digest(context, "context_sha256"))
    )
    return validate_context_envelope(context)


def write_context_envelope(
    root: str | Path,
    context: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Create one immutable mode-0600 context file under runtime/runs."""

    try:
        workspace = _workspace(root)
        value = validate_context_envelope(context)
        path = _job_path(workspace, str(value["job_id"]), RUNTIME_CONTEXT_FILENAME)
        payload = canonical_json_bytes(value)
        if len(payload) > LIMITS["max_context_bytes"]:
            raise ProviderContractError(
                "C31_CONTEXT_TOO_LARGE",
                "context envelope exceeds the byte limit",
            )
        write_state = _exclusive_private_write(path, payload)
        report = _write_report(
            workspace,
            path,
            write_state,
            digest=_sha256(payload),
            byte_length=len(payload),
            created=write_state == "CREATED",
        )
        report["context_sha256"] = value["context_sha256"]
        return report, 0
    except ProviderConflict as error:
        return {
            "status": "CONFLICT",
            "operation": "provider context envelope",
            "capability": "C31",
            "errors": [{"code": error.code, "message": str(error)}],
            "provider_called": False,
            "vault_mutation_performed": False,
        }, 30
    except (OSError, ProviderContractError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ProviderContractError) else "C31_CONTEXT_INVALID"
        return {
            "status": "FAIL",
            "operation": "provider context envelope",
            "capability": "C31",
            "errors": [{"code": code, "message": str(error)}],
            "provider_called": False,
            "vault_mutation_performed": False,
        }, 2


def _context_bindings(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "prompt_sha256": context["prompt"]["sha256"],
        "output_schema": context["output_schema"],
        "policy_decision": context["policy_decision"],
        "index_generation_id": context["index_generation_id"],
        "candidate_set_sha256": context["candidate_set_sha256"],
        "source_hashes_sha256": context["source_hashes_sha256"],
    }


def build_provider_request(
    context: Mapping[str, Any],
    *,
    context_path: str | None = None,
    request_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a digest-bound request that contains no raw prompt or candidates."""

    frozen = validate_context_envelope(context)
    request_uuid = request_id or str(uuid.uuid4())
    if request_id is not None:
        _validate_uuid(request_uuid, "request_id")
    path = context_path or f"runtime/runs/{frozen['job_id']}/{RUNTIME_CONTEXT_FILENAME}"
    bindings = _context_bindings(frozen)
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "job_id": frozen["job_id"],
        "request_id": f"req-{_sha256(request_uuid.encode('utf-8'))[:32]}",
        "created_at": created_at or _now(),
        "context_path": path,
        "context_sha256": frozen["context_sha256"],
        "context_byte_length": len(canonical_json_bytes(frozen)),
        "prompt_sha256": bindings["prompt_sha256"],
        "output_schema": bindings["output_schema"],
        "policy_decision": bindings["policy_decision"],
        "index_generation_id": bindings["index_generation_id"],
        "candidate_set_sha256": bindings["candidate_set_sha256"],
        "source_hashes_sha256": bindings["source_hashes_sha256"],
        "action": frozen["action"],
        "provider": frozen["provider"],
        "inference_options": frozen["inference_options"],
        "limits": dict(LIMITS),
        "runner_boundary": frozen["runner_boundary"],
    }
    request["request_sha256"] = _sha256(canonical_json_bytes(request))
    _validate_schema(request, "request")
    if len(canonical_json_bytes(request)) > LIMITS["max_request_bytes"]:
        raise ProviderContractError(
            "C31_REQUEST_TOO_LARGE",
            "request envelope exceeds the byte limit",
        )
    return request


def write_provider_request(
    root: str | Path,
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Persist an immutable request reference beside its context envelope."""

    try:
        workspace = _workspace(root)
        value = deepcopy(dict(_as_mapping(request, "request")))
        _validate_schema(value, "request")
        path = _job_path(workspace, str(value["job_id"]), RUNTIME_REQUEST_FILENAME)
        payload = canonical_json_bytes(value)
        state = _exclusive_private_write(path, payload)
        report = _write_report(
            workspace,
            path,
            state,
            digest=_sha256(payload),
            byte_length=len(payload),
            created=state == "CREATED",
        )
        report["request_sha256"] = value["request_sha256"]
        return report, 0
    except ProviderConflict as error:
        return {
            "status": "CONFLICT",
            "operation": "provider request envelope",
            "capability": "C31",
            "errors": [{"code": error.code, "message": str(error)}],
            "provider_called": False,
            "vault_mutation_performed": False,
        }, 30
    except (OSError, ProviderContractError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ProviderContractError) else "C31_REQUEST_INVALID"
        return {
            "status": "FAIL",
            "operation": "provider request envelope",
            "capability": "C31",
            "errors": [{"code": code, "message": str(error)}],
            "provider_called": False,
            "vault_mutation_performed": False,
        }, 2


def build_provider_response(
    request: Mapping[str, Any],
    *,
    status: str,
    output: Any = None,
    output_token_count: int | None = None,
    warnings: Sequence[str] = (),
    received_at: str | None = None,
    completed_at: str | None = None,
    response_id: str | None = None,
) -> dict[str, Any]:
    """Build a bounded response; its output remains explicitly untrusted."""

    req = deepcopy(dict(_as_mapping(request, "request")))
    _validate_schema(req, "request")
    if status not in ALLOWED_RESPONSE_STATUSES:
        raise ProviderContractError(
            "C31_RESPONSE_STATUS_INVALID",
            "response status is invalid",
        )
    received = received_at or _now()
    completed = completed_at or received
    _parse_time(received, "received_at")
    _parse_time(completed, "completed_at")
    output_bytes = canonical_json_bytes(output) if output is not None else b""
    if len(output_bytes) > LIMITS["max_response_bytes"]:
        raise ProviderContractError(
            "C31_RESPONSE_TOO_LARGE",
            "provider response exceeds the byte limit",
        )
    token_count = 0 if output is None else output_token_count
    if token_count is None:
        token_count = len(output_bytes.decode("utf-8").split())
    if not isinstance(token_count, int) or token_count < 0 or token_count > LIMITS["max_output_tokens"]:
        raise ProviderContractError(
            "C31_OUTPUT_TOKEN_LIMIT",
            "output token count exceeds the C31 limit",
        )
    response: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "job_id": req["job_id"],
        "response_id": f"res-{_sha256((response_id or str(uuid.uuid4())).encode('utf-8'))[:32]}",
        "request_sha256": req["request_sha256"],
        "context_sha256": req["context_sha256"],
        "received_at": received,
        "completed_at": completed,
        "status": status,
        "provider": req["provider"],
        "output_schema": req["output_schema"],
        "output": output,
        "output_sha256": _sha256(output_bytes) if output is not None else None,
        "output_byte_length": len(output_bytes),
        "output_token_count": token_count,
        "warnings": list(warnings),
        "runner_boundary": req["runner_boundary"],
        "model_output_untrusted": True,
    }
    response["response_sha256"] = _sha256(canonical_json_bytes(response))
    _validate_schema(response, "response")
    return response


def _validate_request_response_binding(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> None:
    if response["job_id"] != request["job_id"]:
        raise ProviderContractError(
            "C31_JOB_ID_DRIFT",
            "response job_id does not match request",
        )
    if response["request_sha256"] != request["request_sha256"]:
        raise ProviderContractError(
            "C31_REQUEST_DRIFT",
            "response request digest does not match request",
        )
    if response["context_sha256"] != request["context_sha256"]:
        raise ProviderContractError(
            "C31_CONTEXT_DRIFT",
            "response context digest does not match request",
        )
    if response["provider"] != request["provider"]:
        raise ProviderContractError(
            "C31_PROVIDER_IDENTITY_DRIFT",
            "response provider identity differs from request",
        )
    if response["output_schema"] != request["output_schema"]:
        raise ProviderContractError(
            "C31_OUTPUT_SCHEMA_DRIFT",
            "response output schema differs from request",
        )
    output = response["output"]
    expected = _sha256(canonical_json_bytes(output)) if output is not None else None
    if response["output_sha256"] != expected:
        raise ProviderContractError(
            "C31_OUTPUT_DIGEST_DRIFT",
            "response output digest does not match output",
        )


def build_identity_receipt(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    provider_called: bool,
    remote_authorization_sha256: str | None = None,
    receipt_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a long-lived hash-only identity receipt."""

    req = deepcopy(dict(_as_mapping(request, "request")))
    res = deepcopy(dict(_as_mapping(response, "response")))
    _validate_schema(req, "request")
    _validate_schema(res, "response")
    _validate_request_response_binding(req, res)
    authorization = remote_authorization_sha256
    if authorization is not None:
        _validate_hash(authorization, "remote_authorization_sha256")
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "receipt_id": f"rcpt-{_sha256((receipt_id or str(uuid.uuid4())).encode('utf-8'))[:32]}",
        "job_id": req["job_id"],
        "status": res["status"],
        "created_at": created_at or _now(),
        "completed_at": res["completed_at"] if res["status"] != "cancelled" else None,
        "request_sha256": req["request_sha256"],
        "context_sha256": req["context_sha256"],
        "response_sha256": res["response_sha256"],
        "prompt_sha256": req["prompt_sha256"],
        "output_schema_sha256": req["output_schema"]["sha256"],
        "policy_decision_sha256": req["policy_decision"]["policy_sha256"],
        "candidate_set_sha256": req["candidate_set_sha256"],
        "source_hashes_sha256": req["source_hashes_sha256"],
        "index_generation_id": req["index_generation_id"],
        "provider": req["provider"],
        "inference_options_sha256": _sha256(
            canonical_json_bytes(req["inference_options"])
        ),
        "remote_authorization_sha256": authorization,
        "provider_called": bool(provider_called),
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
        "raw_prompt_storage": "private_runtime_only",
        "raw_response_storage": "private_runtime_only",
    }
    receipt["receipt_sha256"] = _sha256(canonical_json_bytes(receipt))
    _validate_schema(receipt, "receipt")
    return receipt


def build_provider_failure(
    *,
    job_id: str,
    phase: str,
    code: str,
    message: str,
    retryable: bool,
    request_sha256: str | None = None,
    context_sha256: str | None = None,
    provider: Mapping[str, Any] | None = None,
    provider_called: bool = False,
) -> dict[str, Any]:
    """Build a bounded failure record without carrying prompt or response bytes."""

    job = _validate_uuid(job_id, "job_id")
    if phase not in ALLOWED_FAILURE_PHASES or code not in ALLOWED_FAILURE_CODES:
        raise ProviderContractError(
            "C31_FAILURE_CODE_INVALID",
            "failure phase or code is not allowlisted",
        )
    if not isinstance(message, str) or not message or "\x00" in message:
        raise ProviderContractError(
            "C31_FAILURE_MESSAGE_INVALID",
            "failure message must be bounded text",
        )
    message_bytes = message.encode("utf-8")
    if len(message_bytes) > LIMITS["max_failure_message_bytes"]:
        raise ProviderContractError(
            "C31_FAILURE_MESSAGE_TOO_LARGE",
            "failure message exceeds the byte limit",
        )
    if request_sha256 is not None:
        _validate_hash(request_sha256, "request_sha256")
    if context_sha256 is not None:
        _validate_hash(context_sha256, "context_sha256")
    normalized_provider = (
        None if provider is None else _normalize_provider(_as_mapping(provider, "provider"))
    )
    failure: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "failure_id": f"fail-{_sha256(str(uuid.uuid4()).encode('utf-8'))[:32]}",
        "job_id": job,
        "occurred_at": _now(),
        "phase": phase,
        "code": code,
        "message": message,
        "retryable": bool(retryable),
        "request_sha256": request_sha256,
        "context_sha256": context_sha256,
        "provider": normalized_provider,
        "provider_called": bool(provider_called),
        "vault_mutation_performed": False,
        "raw_payload_persisted": False,
    }
    failure["failure_sha256"] = _sha256(canonical_json_bytes(failure))
    _validate_schema(failure, "failure")
    return failure


def build_remote_authorization(
    request: Mapping[str, Any],
    *,
    actor: str,
    expires_at: str,
    decision: str = "approved",
    authorized_at: str | None = None,
    authorization_id: str | None = None,
) -> dict[str, Any]:
    """Create a one-job, TTY-confirmed authorization without changing the request."""

    req = deepcopy(dict(_as_mapping(request, "request")))
    _validate_schema(req, "request")
    if req["provider"]["route"].startswith("local:"):
        raise ProviderContractError(
            "C31_AUTHORIZATION_NOT_REMOTE",
            "local routes do not need remote authorization",
        )
    if decision not in {"approved", "denied", "expired"}:
        raise ProviderContractError(
            "C31_AUTHORIZATION_DECISION_INVALID",
            "authorization decision is invalid",
        )
    if not isinstance(actor, str) or not actor:
        raise ProviderContractError(
            "C31_AUTHORIZATION_ACTOR_INVALID",
            "authorization actor is required",
        )
    authorized = authorized_at or _now()
    start = _parse_time(authorized, "authorized_at")
    expiry = _parse_time(expires_at, "expires_at")
    if expiry <= start or expiry - start > timedelta(seconds=LIMITS["max_job_age_seconds"]):
        raise ProviderContractError(
            "C31_AUTHORIZATION_EXPIRY_INVALID",
            "authorization expiry is outside the C31 limit",
        )
    binds = {
        "request_sha256": req["request_sha256"],
        "context_sha256": req["context_sha256"],
        "prompt_sha256": req["prompt_sha256"],
        "output_schema_sha256": req["output_schema"]["sha256"],
        "policy_decision_sha256": req["policy_decision"]["policy_sha256"],
        "candidate_set_sha256": req["candidate_set_sha256"],
        "source_hashes_sha256": req["source_hashes_sha256"],
        "index_generation_id": req["index_generation_id"],
    }
    authorization: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "authorization_id": f"auth-{_sha256((authorization_id or str(uuid.uuid4())).encode('utf-8'))[:32]}",
        "job_id": req["job_id"],
        "decision": decision,
        "authorized_at": authorized,
        "expires_at": expires_at,
        "actor": actor,
        "tty_confirmed": True,
        "scope": "one_job",
        "provider": req["provider"],
        "binds": binds,
    }
    authorization["authorization_sha256"] = _sha256(canonical_json_bytes(authorization))
    _validate_schema(authorization, "authorization")
    return authorization


def read_context_envelope(root: str | Path, *, job_id: str) -> dict[str, Any]:
    """Read one private context file and fail closed on mode, bytes, or digest drift."""

    workspace = _workspace(root)
    path = _job_path(workspace, job_id, RUNTIME_CONTEXT_FILENAME)
    if path.is_symlink() or not path.is_file():
        raise ProviderContractError(
            "C31_CONTEXT_MISSING",
            "context envelope must be a regular file",
        )
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ProviderContractError(
            "C31_RUNTIME_MODE_INVALID",
            "context envelope must be mode 0600",
        )
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProviderContractError(
            "C31_CONTEXT_JSON_INVALID",
            "context envelope is not valid JSON",
        ) from error
    if not isinstance(value, Mapping) or canonical_json_bytes(value) != raw:
        raise ProviderContractError(
            "C31_CONTEXT_SERIALIZATION_INVALID",
            "context envelope is not canonical JSON",
        )
    return validate_context_envelope(value)


__all__ = [
    "CONTRACT_ID",
    "DEFAULT_INFERENCE_OPTIONS",
    "FROZEN_CONTEXT_SCHEMA_PATH",
    "LIMITS",
    "PROVIDER_FAILURE_SCHEMA_PATH",
    "PROVIDER_RECEIPT_SCHEMA_PATH",
    "PROVIDER_REQUEST_SCHEMA_PATH",
    "PROVIDER_RESPONSE_SCHEMA_PATH",
    "PROVIDER_SCHEMA_PATHS",
    "REMOTE_AUTHORIZATION_SCHEMA_PATH",
    "ProviderConflict",
    "ProviderContractError",
    "ProviderSchemaError",
    "build_frozen_context",
    "build_identity_receipt",
    "build_provider_failure",
    "build_provider_request",
    "build_provider_response",
    "build_remote_authorization",
    "canonical_json_bytes",
    "frozen_context_schema",
    "provider_failure_schema",
    "provider_receipt_schema",
    "provider_request_schema",
    "provider_response_schema",
    "provider_schema",
    "read_context_envelope",
    "remote_authorization_schema",
    "schema_bytes",
    "validate_blueprint_provider_contract",
    "validate_context_envelope",
    "write_context_envelope",
    "write_provider_request",
]
