"""C38 local generation identity and promotion evidence contract.

The C38 boundary is deliberately narrower than a provider runner.  It
describes the one supported local generation profile and records the identity
and digest bindings needed to review a future promotion.  It never probes
Ollama, pulls a model, enables a profile, writes a Vault, or authorizes a
provider call.

The C31 request, response, and receipt remain the private transport artifacts.
C38 adds one private identity envelope that binds those artifacts to the
canonical profile, frozen evidence, policy decision, and host boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .recovery import fsync_directory

CAPABILITY = "C38"
SCHEMA_VERSION = 1
CONTRACT_ID = "knowledgeos-c38-generation-identity-v1"
IDENTITY_FILENAME = "generation-identity.json"
IDENTITY_SCHEMA_PATH = "ops/schemas/c38-generation-identity.schema.json"

GENERATION_PROFILE_ID = "local:gemma4-12b"
GENERATION_ROUTE = "local:ollama"
GENERATION_MODEL_TAG = "gemma4:12b"
GENERATION_CONTEXT = 8192
GENERATION_SEED = 0
GENERATION_MAX_OUTPUT_TOKENS = 1024
GENERATION_TIMEOUT_SECONDS = 120

STATE_DIMENSIONS = (
    "declared",
    "configured",
    "reachable",
    "authorized",
    "verified",
    "enabled",
    "healthy",
)
DEFAULT_STATES: dict[str, str] = {
    "declared": "declared",
    "configured": "configured",
    "reachable": "not_run",
    "authorized": "not_authorized",
    "verified": "not_verified",
    "enabled": "disabled",
    "healthy": "not_ready",
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_JOB_PATH = re.compile(
    r"^runtime/runs/(?P<job>[0-9a-f-]{36})/"
    r"(?:context\.json|request\.json|response\.json|receipts/provider-receipt\.json)$"
)
_MODEL_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

_ALLOWED_OUTCOMES = (
    "not_run",
    "completed",
    "refused",
    "failed",
    "timeout",
    "unavailable",
    "overloaded",
    "conflict",
    "cancelled",
)
_ALLOWED_PROMOTION_STATES = ("not_run", "retained_disabled", "eligible", "promoted")
_ALLOWED_PROMOTION_DECISIONS = ("retain_disabled", "defer", "promote")


class GenerationIdentityError(ValueError):
    """Raised when a C38 profile or identity envelope fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GenerationIdentityError("C38_JSON_INVALID", "value is not finite canonical JSON") from error


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GenerationIdentityError("C38_MAPPING_INVALID", f"{label} must be an object")
    return dict(value)


def _text(value: object, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GenerationIdentityError("C38_TEXT_INVALID", f"{label} must be non-empty text")
    if len(value.encode("utf-8")) > maximum:
        raise GenerationIdentityError("C38_TEXT_TOO_LARGE", f"{label} exceeds the bounded byte limit")
    return value


def _hash(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise GenerationIdentityError("C38_DIGEST_INVALID", f"{label} must be a lowercase SHA-256 digest")
    return value


def _uuid(value: object, label: str = "job_id") -> str:
    if not isinstance(value, str) or _UUID4.fullmatch(value) is None:
        raise GenerationIdentityError("C38_UUID_INVALID", f"{label} must be a lowercase UUIDv4")
    return value


def _datetime(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise GenerationIdentityError("C38_TIME_INVALID", f"{label} must be RFC3339 text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise GenerationIdentityError("C38_TIME_INVALID", f"{label} must be RFC3339 text") from error
    if parsed.tzinfo is None:
        raise GenerationIdentityError("C38_TIME_INVALID", f"{label} must include a timezone")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generation_model_options(*, max_output_tokens: int = GENERATION_MAX_OUTPUT_TOKENS) -> dict[str, Any]:
    """Return the deterministic model options shared by C30, E02, and C38."""

    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= GENERATION_MAX_OUTPUT_TOKENS
    ):
        raise GenerationIdentityError(
            "C38_OUTPUT_LIMIT_INVALID",
            "max_output_tokens must remain within the canonical C38 bound",
        )
    return {
        "num_ctx": GENERATION_CONTEXT,
        "stream": False,
        "think": False,
        "tools": False,
        "temperature": 0,
        "seed": GENERATION_SEED,
        "max_output_tokens": max_output_tokens,
        "keep_alive": 0,
        "parallel_requests": 1,
    }


def generation_inference_options(
    *,
    max_output_tokens: int = GENERATION_MAX_OUTPUT_TOKENS,
    timeout_seconds: int = GENERATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return the complete bounded C31/C38 inference option set."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= GENERATION_TIMEOUT_SECONDS
    ):
        raise GenerationIdentityError(
            "C38_TIMEOUT_INVALID",
            "timeout_seconds must remain within the canonical C38 bound",
        )
    return {
        **generation_model_options(max_output_tokens=max_output_tokens),
        "timeout_seconds": timeout_seconds,
    }


def canonical_generation_profile() -> dict[str, Any]:
    """Return the immutable, disabled-by-default generation profile."""

    return {
        "profile_id": GENERATION_PROFILE_ID,
        "task": "generation",
        "route": GENERATION_ROUTE,
        "requested_model_tag": GENERATION_MODEL_TAG,
        "canonical": True,
        "enabled_by_default": False,
        "options": generation_model_options(),
        "safety": {
            "automatic_pull": False,
            "fallback": False,
            "tools": False,
            "reasoning_retention": False,
            "loopback_only": True,
            "cloud_disabled": True,
        },
    }


def _policy(value: Mapping[str, Any]) -> dict[str, Any]:
    policy = _mapping(value, "binding.policy_decision")
    expected = {"decision", "policy_sha256", "privacy_policy_sha256", "scope", "authorization_sha256"}
    if set(policy) != expected:
        raise GenerationIdentityError("C38_POLICY_INVALID", "policy decision fields do not match the C38 contract")
    if policy["decision"] not in {"allow_local", "allow_remote", "deny", "ask"}:
        raise GenerationIdentityError("C38_POLICY_INVALID", "policy decision is not allowlisted")
    _hash(policy["policy_sha256"], "policy_sha256")
    _hash(policy["privacy_policy_sha256"], "privacy_policy_sha256")
    _text(policy["scope"], "policy scope", maximum=200)
    _hash(policy["authorization_sha256"], "authorization_sha256", nullable=True)
    return policy


def _frozen_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _mapping(value, "binding.frozen_evidence")
    expected = {
        "context_sha256",
        "source_hashes_sha256",
        "candidate_set_sha256",
        "index_generation_id",
        "projection_generation_id",
        "frozen_evidence_sha256",
    }
    if set(evidence) != expected:
        raise GenerationIdentityError("C38_EVIDENCE_INVALID", "frozen evidence fields do not match the C38 contract")
    for field in ("context_sha256", "source_hashes_sha256", "candidate_set_sha256"):
        _hash(evidence[field], f"frozen_evidence.{field}")
    for field in ("index_generation_id", "projection_generation_id"):
        _text(evidence[field], f"frozen_evidence.{field}", maximum=128)
    expected_digest = _sha256(_canonical_json_bytes({key: evidence[key] for key in evidence if key != "frozen_evidence_sha256"}))
    if evidence["frozen_evidence_sha256"] != expected_digest:
        raise GenerationIdentityError("C38_EVIDENCE_DRIFT", "frozen evidence digest does not match its fields")
    return evidence


def _artifact(value: object, label: str, *, job_id: str) -> dict[str, Any] | None:
    if value is None:
        return None
    artifact = _mapping(value, label)
    expected = {"path", "sha256", "byte_length", "mode"}
    if set(artifact) != expected:
        raise GenerationIdentityError("C38_ARTIFACT_INVALID", f"{label} fields do not match the C38 contract")
    path = _text(artifact["path"], f"{label}.path", maximum=300)
    match = _JOB_PATH.fullmatch(path)
    if match is None or match.group("job") != job_id:
        raise GenerationIdentityError("C38_ARTIFACT_PATH_INVALID", f"{label}.path is outside the selected private job")
    _hash(artifact["sha256"], f"{label}.sha256")
    byte_length = artifact["byte_length"]
    if isinstance(byte_length, bool) or not isinstance(byte_length, int) or not 1 <= byte_length <= 1024 * 1024:
        raise GenerationIdentityError("C38_ARTIFACT_SIZE_INVALID", f"{label}.byte_length is outside the bound")
    if artifact["mode"] != "0600":
        raise GenerationIdentityError("C38_ARTIFACT_MODE_INVALID", f"{label}.mode must be 0600")
    return artifact


def _host_profile(value: Mapping[str, Any] | None) -> dict[str, Any]:
    host = {
        "host_platform": "not_observed",
        "host_architecture": "not_observed",
        "endpoint": "http://127.0.0.1:11434",
        "loopback_only": True,
        "cloud_disabled": True,
        "proxy_allowed": False,
        "tunnel_allowed": False,
        "auto_pull": False,
        "storage_scope": "internal_ssd",
        "storage_path": None,
        "backend": "not_observed",
        "device_placement": "not_observed",
        "processor_split": "not_observed",
    }
    if value is not None:
        supplied = _mapping(value, "host_profile")
        if set(supplied) != set(host):
            raise GenerationIdentityError("C38_HOST_PROFILE_INVALID", "host profile fields do not match the C38 contract")
        host = supplied
    for field in ("host_platform", "host_architecture", "backend", "device_placement", "processor_split"):
        _text(host[field], f"host_profile.{field}", maximum=200)
    if host["endpoint"] != "http://127.0.0.1:11434":
        raise GenerationIdentityError("C38_ENDPOINT_INVALID", "C38 requires the fixed loopback Ollama endpoint")
    for field in ("loopback_only", "cloud_disabled"):
        if host[field] is not True:
            raise GenerationIdentityError("C38_HOST_POLICY_INVALID", f"host_profile.{field} must be true")
    for field in ("proxy_allowed", "tunnel_allowed", "auto_pull"):
        if host[field] is not False:
            raise GenerationIdentityError("C38_HOST_POLICY_INVALID", f"host_profile.{field} must be false")
    if host["storage_scope"] != "internal_ssd":
        raise GenerationIdentityError("C38_STORAGE_SCOPE_INVALID", "C38 only records internal SSD evidence")
    if host["storage_path"] is not None:
        _text(host["storage_path"], "host_profile.storage_path", maximum=500)
    return host


def _states(value: Mapping[str, Any] | None) -> dict[str, str]:
    states = dict(DEFAULT_STATES if value is None else _mapping(value, "states"))
    if set(states) != set(STATE_DIMENSIONS):
        raise GenerationIdentityError("C38_STATE_INVALID", "capability state dimensions do not match C30/C38")
    allowed = {
        "declared": {"declared", "not_declared"},
        "configured": {"configured", "not_configured", "invalid"},
        "reachable": {"reachable", "not_run", "unavailable"},
        "authorized": {"authorized", "not_authorized", "expired"},
        "verified": {"verified", "not_verified", "failed"},
        "enabled": {"enabled", "disabled"},
        "healthy": {"healthy", "not_ready", "degraded"},
    }
    for field, values in allowed.items():
        if states[field] not in values:
            raise GenerationIdentityError("C38_STATE_INVALID", f"states.{field} is not allowlisted")
    if states["enabled"] != "disabled":
        raise GenerationIdentityError("C38_ENABLEMENT_FORBIDDEN", "C38 identity evidence cannot enable the local profile")
    return states


def _promotion(value: Mapping[str, Any] | None) -> dict[str, Any]:
    promotion = {
        "state": "not_run",
        "live_identity": False,
        "loopback_cloud_off": False,
        "resource": False,
        "quality": False,
        "privacy": False,
        "citation": False,
        "staleness": False,
        "human_decision": False,
        "decision": "retain_disabled",
    }
    if value is not None:
        promotion = _mapping(value, "promotion")
    required = set(promotion)
    if required != {
        "state",
        "live_identity",
        "loopback_cloud_off",
        "resource",
        "quality",
        "privacy",
        "citation",
        "staleness",
        "human_decision",
        "decision",
    }:
        raise GenerationIdentityError("C38_PROMOTION_INVALID", "promotion fields do not match the C38 contract")
    if promotion["state"] not in _ALLOWED_PROMOTION_STATES:
        raise GenerationIdentityError("C38_PROMOTION_INVALID", "promotion state is not allowlisted")
    if promotion["decision"] not in _ALLOWED_PROMOTION_DECISIONS:
        raise GenerationIdentityError("C38_PROMOTION_INVALID", "promotion decision is not allowlisted")
    for field in (
        "live_identity",
        "loopback_cloud_off",
        "resource",
        "quality",
        "privacy",
        "citation",
        "staleness",
        "human_decision",
    ):
        if not isinstance(promotion[field], bool):
            raise GenerationIdentityError("C38_PROMOTION_INVALID", f"promotion.{field} must be boolean")
    if promotion["state"] == "promoted":
        gates = [promotion[field] for field in ("live_identity", "loopback_cloud_off", "resource", "quality", "privacy", "citation", "staleness", "human_decision")]
        if not all(gates) or promotion["decision"] != "promote":
            raise GenerationIdentityError("C38_PROMOTION_GATE_OPEN", "promoted identity evidence requires every explicit gate")
    if promotion["decision"] == "promote" and promotion["state"] != "promoted":
        raise GenerationIdentityError("C38_PROMOTION_INVALID", "promote decision requires promoted state")
    return dict(promotion)


def _schema_error(document: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(generation_identity_schema(), format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        locator = "/" + "/".join(str(part) for part in error.path)
        raise GenerationIdentityError("C38_SCHEMA_INVALID", f"{locator}: {error.message}")


def generation_identity_schema() -> dict[str, Any]:
    """Return the strict generated C38 identity-envelope schema."""

    sha = {"type": "string", "pattern": _SHA256.pattern}
    nullable_sha = {"anyOf": [sha, {"type": "null"}]}
    text = {"type": "string", "minLength": 1, "maxLength": 512}
    model = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _MODEL_TAG.pattern}
    policy = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "policy_sha256", "privacy_policy_sha256", "scope", "authorization_sha256"],
        "properties": {
            "decision": {"enum": ["allow_local", "allow_remote", "deny", "ask"]},
            "policy_sha256": sha,
            "privacy_policy_sha256": sha,
            "scope": {"type": "string", "minLength": 1, "maxLength": 200},
            "authorization_sha256": nullable_sha,
        },
    }
    frozen = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "context_sha256",
            "source_hashes_sha256",
            "candidate_set_sha256",
            "index_generation_id",
            "projection_generation_id",
            "frozen_evidence_sha256",
        ],
        "properties": {
            "context_sha256": sha,
            "source_hashes_sha256": sha,
            "candidate_set_sha256": sha,
            "index_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "projection_generation_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "frozen_evidence_sha256": sha,
        },
    }
    artifact = {
        "type": ["object", "null"],
        "additionalProperties": False,
        "required": ["path", "sha256", "byte_length", "mode"],
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 300, "pattern": _JOB_PATH.pattern},
            "sha256": sha,
            "byte_length": {"type": "integer", "minimum": 1, "maximum": 1024 * 1024},
            "mode": {"const": "0600"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/c38-generation-identity.schema.json",
        "title": "KnowledgeOS C38 local generation identity envelope",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "contract_id",
            "capability",
            "envelope_id",
            "job_id",
            "created_at",
            "profile",
            "binding",
            "identity",
            "host_profile",
            "inference_options",
            "states",
            "outcome",
            "artifacts",
            "promotion",
            "privacy",
            "envelope_sha256",
        ],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "contract_id": {"const": CONTRACT_ID},
            "capability": {"const": CAPABILITY},
            "envelope_id": {"type": "string", "pattern": r"^gen-[0-9a-f]{32}$"},
            "job_id": {"type": "string", "pattern": _UUID4.pattern},
            "created_at": {"type": "string", "format": "date-time"},
            "profile": {
                "type": "object",
                "additionalProperties": False,
                "required": ["profile_id", "task", "route", "requested_model_tag", "canonical", "enabled_by_default", "options", "safety"],
                "properties": {
                    "profile_id": {"const": GENERATION_PROFILE_ID},
                    "task": {"const": "generation"},
                    "route": {"const": GENERATION_ROUTE},
                    "requested_model_tag": {"const": GENERATION_MODEL_TAG},
                    "canonical": {"const": True},
                    "enabled_by_default": {"const": False},
                    "options": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(generation_model_options()),
                        "properties": {
                            "num_ctx": {"const": GENERATION_CONTEXT},
                            "stream": {"const": False},
                            "think": {"const": False},
                            "tools": {"const": False},
                            "temperature": {"const": 0},
                            "seed": {"const": GENERATION_SEED},
                            "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": GENERATION_MAX_OUTPUT_TOKENS},
                            "keep_alive": {"const": 0},
                            "parallel_requests": {"const": 1},
                        },
                    },
                    "safety": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["automatic_pull", "fallback", "tools", "reasoning_retention", "loopback_only", "cloud_disabled"],
                        "properties": {
                            "automatic_pull": {"const": False},
                            "fallback": {"const": False},
                            "tools": {"const": False},
                            "reasoning_retention": {"const": False},
                            "loopback_only": {"const": True},
                            "cloud_disabled": {"const": True},
                        },
                    },
                },
            },
            "binding": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "prompt_sha256", "output_schema_sha256", "policy_decision", "frozen_evidence"],
                "properties": {
                    "action": {"type": "string", "pattern": _ACTION.pattern},
                    "prompt_sha256": sha,
                    "output_schema_sha256": sha,
                    "policy_decision": policy,
                    "frozen_evidence": frozen,
                },
            },
            "identity": {
                "type": "object",
                "additionalProperties": False,
                "required": ["provider_route", "requested_model_tag", "resolved_model_name", "model_digest", "ollama_version"],
                "properties": {
                    "provider_route": {"const": GENERATION_ROUTE},
                    "requested_model_tag": {"const": GENERATION_MODEL_TAG},
                    "resolved_model_name": {"anyOf": [model, {"type": "null"}]},
                    "model_digest": nullable_sha,
                    "ollama_version": {"anyOf": [{"type": "string", "pattern": _VERSION.pattern}, {"type": "null"}]},
                },
            },
            "host_profile": {
                "type": "object",
                "additionalProperties": False,
                "required": ["host_platform", "host_architecture", "endpoint", "loopback_only", "cloud_disabled", "proxy_allowed", "tunnel_allowed", "auto_pull", "storage_scope", "storage_path", "backend", "device_placement", "processor_split"],
                "properties": {
                    "host_platform": text,
                    "host_architecture": text,
                    "endpoint": {"const": "http://127.0.0.1:11434"},
                    "loopback_only": {"const": True},
                    "cloud_disabled": {"const": True},
                    "proxy_allowed": {"const": False},
                    "tunnel_allowed": {"const": False},
                    "auto_pull": {"const": False},
                    "storage_scope": {"const": "internal_ssd"},
                    "storage_path": {"anyOf": [{"type": "string", "maxLength": 500}, {"type": "null"}]},
                    "backend": text,
                    "device_placement": text,
                    "processor_split": text,
                },
            },
            "inference_options": {
                "type": "object",
                "additionalProperties": False,
                "required": list(generation_inference_options()),
                "properties": {
                    "num_ctx": {"const": GENERATION_CONTEXT},
                    "stream": {"const": False},
                    "think": {"const": False},
                    "tools": {"const": False},
                    "temperature": {"const": 0},
                    "seed": {"const": GENERATION_SEED},
                    "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": GENERATION_MAX_OUTPUT_TOKENS},
                    "keep_alive": {"const": 0},
                    "parallel_requests": {"const": 1},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": GENERATION_TIMEOUT_SECONDS},
                },
            },
            "states": {
                "type": "object",
                "additionalProperties": False,
                "required": list(STATE_DIMENSIONS),
                "properties": {
                    "declared": {"enum": ["declared", "not_declared"]},
                    "configured": {"enum": ["configured", "not_configured", "invalid"]},
                    "reachable": {"enum": ["reachable", "not_run", "unavailable"]},
                    "authorized": {"enum": ["authorized", "not_authorized", "expired"]},
                    "verified": {"enum": ["verified", "not_verified", "failed"]},
                    "enabled": {"const": "disabled"},
                    "healthy": {"enum": ["healthy", "not_ready", "degraded"]},
                },
            },
            "outcome": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "provider_called"],
                "properties": {
                    "status": {"enum": list(_ALLOWED_OUTCOMES)},
                    "provider_called": {"type": "boolean"},
                },
            },
            "artifacts": {
                "type": "object",
                "additionalProperties": False,
                "required": ["context", "request", "response", "receipt"],
                "properties": {
                    "context": artifact,
                    "request": artifact,
                    "response": artifact,
                    "receipt": artifact,
                },
            },
            "promotion": {
                "type": "object",
                "additionalProperties": False,
                "required": ["state", "live_identity", "loopback_cloud_off", "resource", "quality", "privacy", "citation", "staleness", "human_decision", "decision"],
                "properties": {
                    "state": {"enum": list(_ALLOWED_PROMOTION_STATES)},
                    "live_identity": {"type": "boolean"},
                    "loopback_cloud_off": {"type": "boolean"},
                    "resource": {"type": "boolean"},
                    "quality": {"type": "boolean"},
                    "privacy": {"type": "boolean"},
                    "citation": {"type": "boolean"},
                    "staleness": {"type": "boolean"},
                    "human_decision": {"type": "boolean"},
                    "decision": {"enum": list(_ALLOWED_PROMOTION_DECISIONS)},
                },
            },
            "privacy": {
                "type": "object",
                "additionalProperties": False,
                "required": ["raw_prompt_storage", "raw_response_storage", "receipt_content", "vault_mutation_performed", "canonical_apply_allowed", "model_output_untrusted"],
                "properties": {
                    "raw_prompt_storage": {"const": "private_runtime_only"},
                    "raw_response_storage": {"const": "private_runtime_only"},
                    "receipt_content": {"const": "hashes_only"},
                    "vault_mutation_performed": {"const": False},
                    "canonical_apply_allowed": {"const": False},
                    "model_output_untrusted": {"const": True},
                },
            },
            "envelope_sha256": sha,
        },
    }


def _without_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(field, None)
    return result


def validate_generation_identity(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one detached C38 identity envelope and return a copy."""

    value = deepcopy(_mapping(envelope, "generation identity envelope"))
    _schema_error(value)
    profile = value["profile"]
    if profile != canonical_generation_profile():
        raise GenerationIdentityError("C38_PROFILE_DRIFT", "identity profile differs from the canonical generation profile")
    binding = value["binding"]
    _text(binding["action"], "binding.action", maximum=64)
    _hash(binding["prompt_sha256"], "binding.prompt_sha256")
    _hash(binding["output_schema_sha256"], "binding.output_schema_sha256")
    _policy(binding["policy_decision"])
    _frozen_evidence(binding["frozen_evidence"])
    identity = value["identity"]
    if identity["resolved_model_name"] is not None and identity["resolved_model_name"] != GENERATION_MODEL_TAG:
        raise GenerationIdentityError("C38_IDENTITY_DRIFT", "resolved model name differs from gemma4:12b")
    if identity["model_digest"] is not None:
        _hash(identity["model_digest"], "identity.model_digest")
    if identity["ollama_version"] is not None:
        _text(identity["ollama_version"], "identity.ollama_version", maximum=64)
    _host_profile(value["host_profile"])
    options = value["inference_options"]
    expected_options = generation_inference_options(
        max_output_tokens=options["max_output_tokens"],
        timeout_seconds=options["timeout_seconds"],
    )
    if options != expected_options:
        raise GenerationIdentityError("C38_OPTIONS_DRIFT", "inference options differ from the canonical generation profile")
    states = _states(value["states"])
    if (
        states["verified"] == "verified"
        and (
            identity["resolved_model_name"] is None
            or identity["model_digest"] is None
            or identity["ollama_version"] is None
        )
    ):
        raise GenerationIdentityError("C38_VERIFICATION_INCOMPLETE", "verified state requires complete live identity fields")
    outcome = value["outcome"]
    if outcome["status"] == "not_run" and outcome["provider_called"]:
        raise GenerationIdentityError("C38_OUTCOME_INVALID", "not_run outcome cannot claim a provider call")
    if outcome["status"] == "completed" and not outcome["provider_called"]:
        raise GenerationIdentityError("C38_OUTCOME_INVALID", "completed outcome requires a provider call")
    artifacts = value["artifacts"]
    for name in ("context", "request", "response", "receipt"):
        _artifact(artifacts[name], f"artifacts.{name}", job_id=value["job_id"])
    promotion = _promotion(value["promotion"])
    if promotion["state"] == "promoted":
        if states["verified"] != "verified" or states["authorized"] != "authorized":
            raise GenerationIdentityError("C38_PROMOTION_GATE_OPEN", "promoted identity requires authorized and verified state")
        if identity["model_digest"] is None or identity["resolved_model_name"] is None or identity["ollama_version"] is None:
            raise GenerationIdentityError("C38_PROMOTION_GATE_OPEN", "promoted identity requires full provider identity")
    expected_envelope_digest = _sha256(_canonical_json_bytes(_without_digest(value, "envelope_sha256")))
    if value["envelope_sha256"] != expected_envelope_digest:
        raise GenerationIdentityError("C38_ENVELOPE_DRIFT", "envelope_sha256 does not match immutable envelope fields")
    return value


def _default_frozen_evidence(
    *,
    context_sha256: str,
    source_hashes_sha256: str,
    candidate_set_sha256: str,
    index_generation_id: str,
    projection_generation_id: str,
) -> dict[str, Any]:
    base = {
        "context_sha256": context_sha256,
        "source_hashes_sha256": source_hashes_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "index_generation_id": index_generation_id,
        "projection_generation_id": projection_generation_id,
    }
    base["frozen_evidence_sha256"] = _sha256(_canonical_json_bytes(base))
    return base


def build_generation_identity(
    *,
    job_id: str,
    action: str,
    prompt_sha256: str,
    output_schema_sha256: str,
    policy_decision: Mapping[str, Any],
    frozen_evidence: Mapping[str, Any],
    resolved_model_name: str | None = None,
    model_digest: str | None = None,
    ollama_version: str | None = None,
    host_profile: Mapping[str, Any] | None = None,
    inference_options: Mapping[str, Any] | None = None,
    states: Mapping[str, Any] | None = None,
    outcome_status: str = "not_run",
    provider_called: bool = False,
    artifacts: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic C38 envelope without contacting a provider."""

    job = _uuid(job_id)
    if not _ACTION.fullmatch(action):
        raise GenerationIdentityError("C38_ACTION_INVALID", "action is not allowlisted")
    _hash(prompt_sha256, "prompt_sha256")
    _hash(output_schema_sha256, "output_schema_sha256")
    policy = _policy(policy_decision)
    evidence = _frozen_evidence(frozen_evidence)
    resolved = None if resolved_model_name is None else _text(resolved_model_name, "resolved_model_name", maximum=128)
    digest = _hash(model_digest, "model_digest", nullable=True)
    version = None if ollama_version is None else _text(ollama_version, "ollama_version", maximum=64)
    options = generation_inference_options(
        max_output_tokens=(generation_inference_options()["max_output_tokens"] if inference_options is None else _mapping(inference_options, "inference_options")["max_output_tokens"]),
        timeout_seconds=(generation_inference_options()["timeout_seconds"] if inference_options is None else _mapping(inference_options, "inference_options")["timeout_seconds"]),
    ) if inference_options is None else dict(_mapping(inference_options, "inference_options"))
    # Validate the exact option set before it enters the immutable envelope.
    if options != generation_inference_options(
        max_output_tokens=options.get("max_output_tokens", GENERATION_MAX_OUTPUT_TOKENS),
        timeout_seconds=options.get("timeout_seconds", GENERATION_TIMEOUT_SECONDS),
    ):
        raise GenerationIdentityError("C38_OPTIONS_INVALID", "inference options are not the canonical bounded set")
    selected_states = _states(states)
    selected_artifacts = {name: None for name in ("context", "request", "response", "receipt")}
    if artifacts is not None:
        supplied_artifacts = _mapping(artifacts, "artifacts")
        if set(supplied_artifacts) != set(selected_artifacts):
            raise GenerationIdentityError("C38_ARTIFACT_INVALID", "artifacts must contain exactly the C31 artifact names")
        selected_artifacts = dict(supplied_artifacts)
    selected_outcome = {"status": outcome_status, "provider_called": bool(provider_called)}
    if outcome_status not in _ALLOWED_OUTCOMES:
        raise GenerationIdentityError("C38_OUTCOME_INVALID", "outcome status is not allowlisted")
    selected_promotion = _promotion(promotion)
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "capability": CAPABILITY,
        "envelope_id": "",
        "job_id": job,
        "created_at": _datetime(created_at or _now(), "created_at"),
        "profile": canonical_generation_profile(),
        "binding": {
            "action": action,
            "prompt_sha256": prompt_sha256,
            "output_schema_sha256": output_schema_sha256,
            "policy_decision": policy,
            "frozen_evidence": evidence,
        },
        "identity": {
            "provider_route": GENERATION_ROUTE,
            "requested_model_tag": GENERATION_MODEL_TAG,
            "resolved_model_name": resolved,
            "model_digest": digest,
            "ollama_version": version,
        },
        "host_profile": _host_profile(host_profile),
        "inference_options": options,
        "states": selected_states,
        "outcome": selected_outcome,
        "artifacts": selected_artifacts,
        "promotion": selected_promotion,
        "privacy": {
            "raw_prompt_storage": "private_runtime_only",
            "raw_response_storage": "private_runtime_only",
            "receipt_content": "hashes_only",
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
            "model_output_untrusted": True,
        },
    }
    seed = _canonical_json_bytes({key: value for key, value in envelope.items() if key != "envelope_id"})
    envelope["envelope_id"] = f"gen-{_sha256(seed)[:32]}"
    envelope["envelope_sha256"] = _sha256(_canonical_json_bytes(envelope))
    return validate_generation_identity(envelope)


def build_generation_identity_from_c31(
    request: Mapping[str, Any],
    *,
    resolved_model_name: str | None = None,
    model_digest: str | None = None,
    ollama_version: str | None = None,
    host_profile: Mapping[str, Any] | None = None,
    states: Mapping[str, Any] | None = None,
    outcome_status: str = "not_run",
    provider_called: bool = False,
    artifacts: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Bind an existing C31 request to the canonical C38 identity contract."""

    value = _mapping(request, "C31 request")
    required = {"job_id", "action", "prompt_sha256", "output_schema", "policy_decision", "index_generation_id", "candidate_set_sha256", "source_hashes_sha256", "context_sha256", "request_sha256", "provider", "inference_options"}
    if not required.issubset(value):
        raise GenerationIdentityError("C38_C31_REQUEST_INVALID", "C31 request is missing a required C38 binding")
    provider = _mapping(value["provider"], "C31 request.provider")
    if provider.get("route") != GENERATION_ROUTE or provider.get("model_tag") != GENERATION_MODEL_TAG:
        raise GenerationIdentityError("C38_IDENTITY_DRIFT", "C31 request provider is not the canonical local generation identity")
    selected_digest = provider.get("model_digest") if model_digest is None else model_digest
    selected_version = provider.get("ollama_version") if ollama_version is None else ollama_version
    selected_resolved = provider.get("model_tag") if resolved_model_name is None else resolved_model_name
    return build_generation_identity(
        job_id=value["job_id"],
        action=value["action"],
        prompt_sha256=value["prompt_sha256"],
        output_schema_sha256=_mapping(value["output_schema"], "C31 request.output_schema")["sha256"],
        policy_decision=value["policy_decision"],
        frozen_evidence=_default_frozen_evidence(
            context_sha256=value["context_sha256"],
            source_hashes_sha256=value["source_hashes_sha256"],
            candidate_set_sha256=value["candidate_set_sha256"],
            index_generation_id=value["index_generation_id"],
            projection_generation_id=value["index_generation_id"],
        ),
        resolved_model_name=selected_resolved,
        model_digest=selected_digest,
        ollama_version=selected_version,
        host_profile=host_profile,
        inference_options=value["inference_options"],
        states=states,
        outcome_status=outcome_status,
        provider_called=provider_called,
        artifacts=artifacts,
        promotion=promotion,
        created_at=created_at,
    )


def _workspace(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise GenerationIdentityError("C38_ROOT_INVALID", "control root must be an existing non-symlink directory")
    return path.resolve()


def _ensure_private_directory(path: Path, label: str) -> None:
    if path.is_symlink():
        raise GenerationIdentityError("C38_SYMLINK_FORBIDDEN", f"{label} is a symlink")
    if path.exists():
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise GenerationIdentityError("C38_PRIVATE_MODE_INVALID", f"{label} must be a mode 0700 directory")
        return
    parent = path.parent
    if parent == path:
        raise GenerationIdentityError("C38_PRIVATE_DIRECTORY_INVALID", f"cannot create {label}")
    _ensure_private_directory(parent, f"{label} parent")
    path.mkdir(mode=0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise GenerationIdentityError("C38_PRIVATE_MODE_INVALID", f"{label} must be a mode 0700 directory")


def _exclusive_private_write(path: Path, payload: bytes) -> str:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GenerationIdentityError("C38_PRIVATE_FILE_INVALID", "identity envelope path is not a regular file")
    if path.is_file():
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise GenerationIdentityError("C38_PRIVATE_MODE_INVALID", "identity envelope must be mode 0600")
        if path.read_bytes() == payload:
            return "NO_OP"
        raise GenerationIdentityError("C38_IMMUTABLE_CONFLICT", "identity envelope has different bytes")
    _ensure_private_directory(path.parent, "identity receipt directory")
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except FileExistsError:
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600 and path.read_bytes() == payload:
            return "NO_OP"
        raise GenerationIdentityError("C38_IMMUTABLE_CONFLICT", "identity envelope has different bytes")
    except BaseException:
        if descriptor != -1:
            os.close(descriptor)
        if path.exists() or path.is_symlink():
            path.unlink()
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)
    return "CREATED"


def _identity_path(workspace: Path, job_id: str, *, create: bool = True) -> Path:
    job = _uuid(job_id)
    runtime = workspace / "runtime"
    runs = runtime / "runs"
    job_dir = runs / job
    receipts = job_dir / "receipts"
    if create:
        if runtime.is_symlink():
            raise GenerationIdentityError("C38_SYMLINK_FORBIDDEN", "runtime is a symlink")
        if runtime.exists():
            _ensure_private_directory(runtime, "runtime")
        else:
            runtime.mkdir(mode=0o700)
            _ensure_private_directory(runtime, "runtime")
        _ensure_private_directory(runs, "runtime/runs")
        _ensure_private_directory(job_dir, "runtime job directory")
        _ensure_private_directory(receipts, "runtime receipt directory")
    else:
        for directory, label in (
            (runtime, "runtime"),
            (runs, "runtime/runs"),
            (job_dir, "runtime job directory"),
            (receipts, "runtime receipt directory"),
        ):
            if directory.is_symlink():
                raise GenerationIdentityError("C38_SYMLINK_FORBIDDEN", f"{label} is a symlink")
            if directory.exists() and (
                not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700
            ):
                raise GenerationIdentityError("C38_PRIVATE_MODE_INVALID", f"{label} must be a mode 0700 directory")
    return receipts / IDENTITY_FILENAME


def write_generation_identity(root: str | Path, envelope: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """Create one immutable mode-0600 C38 identity envelope."""

    try:
        workspace = _workspace(root)
        value = validate_generation_identity(envelope)
        path = _identity_path(workspace, value["job_id"])
        payload = _canonical_json_bytes(value)
        if len(payload) > 64 * 1024:
            raise GenerationIdentityError("C38_ENVELOPE_TOO_LARGE", "identity envelope exceeds 64 KiB")
        state = _exclusive_private_write(path, payload)
        return {
            "status": "PASS" if state in {"CREATED", "NO_OP"} else "FAIL",
            "operation": "c38 generation identity",
            "capability": CAPABILITY,
            "write": state,
            "path": str(path.relative_to(workspace)),
            "sha256": _sha256(payload),
            "byte_length": len(payload),
            "provider_called": False,
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
        }, 0
    except GenerationIdentityError as error:
        status = "CONFLICT" if error.code == "C38_IMMUTABLE_CONFLICT" else "FAIL"
        return {
            "status": status,
            "operation": "c38 generation identity",
            "capability": CAPABILITY,
            "errors": [{"code": error.code, "message": str(error)}],
            "provider_called": False,
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
        }, 30 if status == "CONFLICT" else 2
    except (OSError, TypeError, ValueError) as error:
        return {
            "status": "FAIL",
            "operation": "c38 generation identity",
            "capability": CAPABILITY,
            "errors": [{"code": "C38_WRITE_FAILED", "message": str(error)}],
            "provider_called": False,
            "vault_mutation_performed": False,
            "canonical_apply_allowed": False,
        }, 2


def read_generation_identity(root: str | Path, *, job_id: str) -> dict[str, Any]:
    """Read one private C38 envelope and fail closed on mode or byte drift."""

    workspace = _workspace(root)
    path = _identity_path(workspace, job_id, create=False)
    if path.is_symlink() or not path.is_file():
        raise GenerationIdentityError("C38_IDENTITY_MISSING", "identity envelope must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise GenerationIdentityError("C38_PRIVATE_MODE_INVALID", "identity envelope must be mode 0600")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise GenerationIdentityError("C38_JSON_INVALID", "identity envelope is not valid JSON") from error
    if not isinstance(value, Mapping) or _canonical_json_bytes(value) != raw:
        raise GenerationIdentityError("C38_SERIALIZATION_INVALID", "identity envelope is not canonical JSON")
    return validate_generation_identity(value)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GenerationIdentityError("C38_DUPLICATE_KEY", "identity envelope contains a duplicate key")
        result[key] = value
    return result


__all__ = [
    "CAPABILITY",
    "CONTRACT_ID",
    "DEFAULT_STATES",
    "GENERATION_CONTEXT",
    "GENERATION_MAX_OUTPUT_TOKENS",
    "GENERATION_MODEL_TAG",
    "GENERATION_PROFILE_ID",
    "GENERATION_ROUTE",
    "GENERATION_SEED",
    "GENERATION_TIMEOUT_SECONDS",
    "IDENTITY_FILENAME",
    "IDENTITY_SCHEMA_PATH",
    "SCHEMA_VERSION",
    "GenerationIdentityError",
    "build_generation_identity",
    "build_generation_identity_from_c31",
    "canonical_generation_profile",
    "generation_identity_schema",
    "generation_inference_options",
    "generation_model_options",
    "read_generation_identity",
    "validate_generation_identity",
    "write_generation_identity",
]
