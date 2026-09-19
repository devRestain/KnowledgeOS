"""E02 host-native Ollama verification contracts and bounded runner.

E02 is deliberately an external-service verification lane, not a new
canonical retrieval implementation.  This module owns the executable
boundary around that lane:

* a host runner receives only one ``runtime/runs/JOB_ID`` spool directory;
* the runner calls only an explicit loopback Ollama endpoint and never pulls;
* C31 response and receipt bytes remain private, immutable, and untrusted;
* inspection and benchmark reports carry identity and resource evidence but
  never raw prompts or model output; and
* no report can promote a model or mutate the frozen C34 contract.

Live service calls are opt-in at the API/CLI boundary.  The default path is
``DEFERRED`` and performs no network access, model discovery, or file write.
Fake-service tests exercise the live-shaped path without satisfying E02's
external-service promotion gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from .ollama import OllamaClient, OllamaError, OllamaProfile
from .provider_broker import EXIT_CONFLICT, EXIT_INPUT_INVALID, EXIT_OK
from .provider_contract import (
    LIMITS,
    ProviderContractError,
    build_identity_receipt,
    build_provider_response,
    canonical_json_bytes,
    provider_receipt_schema,
    provider_request_schema,
    provider_response_schema,
    validate_context_envelope,
)
from .recovery import fsync_directory

CAPABILITY = "E02"
REPORT_SCHEMA_VERSION = 1
REPORT_SCHEMA_PATH = "ops/schemas/e02-verification-report.schema.json"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_GENERATION_TAG = "gemma4:12b"
DEFAULT_GENERATION_CONTEXT = 8192
DEFAULT_GENERATION_SEED = 0
DEFAULT_MAX_QUEUE = 1
REPORT_FILENAME = "verification-report.json"
DEFAULT_EMBEDDING_QUERY_INSTRUCTION = (
    "Given a KnowledgeOS search query, retrieve note passages that answer or "
    "directly support the query"
)
DEFAULT_EMBEDDING_DOCUMENT_TEMPLATE = "title: {title | none} | text: {text}"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MODEL_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_BASE_URL = re.compile(r"^http://127\.0\.0\.1:[0-9]{1,5}$")
_SAFE_ERROR = re.compile(r"^[A-Z0-9_.:-]{1,80}$")
_FORBIDDEN_PATH_PARTS = frozenset({".git", ".obsidian", "KnowledgeHub"})
_JOB_FILENAMES = frozenset({"context.json", "request.json", "response.json", REPORT_FILENAME})
_RECEIPT_FILENAME = "provider-receipt.json"


class E02Error(ProviderContractError):
    """Raised when an E02 boundary or evidence artifact fails closed."""


class E02Client(Protocol):
    """The read/inference surface required from the bounded Ollama client."""

    def version(self) -> str: ...

    def tags(self) -> Mapping[str, Any]: ...

    def show(self, model: str) -> Mapping[str, Any]: ...

    def ps(self) -> Mapping[str, Any]: ...

    def verify_identity(
        self,
        model: str,
        expected_digest: str,
        *,
        expected_version: str | None = None,
    ) -> Mapping[str, Any]: ...

    def chat(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        options: Mapping[str, Any] | None = None,
        format: str | Mapping[str, Any] | None = "json",
        keep_alive: int = 0,
    ) -> Mapping[str, Any]: ...

    def embed(
        self,
        model: str,
        inputs: str | Sequence[str],
        *,
        options: Mapping[str, Any] | None = None,
        truncate: bool = False,
        keep_alive: int = 0,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class HostJobPaths:
    """The only files an E02 host runner may inspect or create."""

    job_dir: Path
    context: Path
    request: Path
    response: Path
    receipt: Path


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_json_without(value: Mapping[str, Any], field: str) -> str:
    copy = dict(value)
    copy.pop(field, None)
    return _sha256_bytes(canonical_json_bytes(copy))


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise E02Error("E02_MAPPING_INVALID", f"{label} must be an object")
    return value


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise E02Error("E02_TEXT_INVALID", f"{label} must be non-empty text")
    if len(value.encode("utf-8")) > maximum:
        raise E02Error("E02_TEXT_TOO_LARGE", f"{label} exceeds the bounded byte limit")
    return value


def _digest(value: object, label: str) -> str:
    if isinstance(value, str) and value.startswith("sha256:"):
        value = value.removeprefix("sha256:")
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise E02Error("E02_DIGEST_INVALID", f"{label} must be a lowercase SHA-256 digest")
    return value


def _model_tag(value: object, label: str = "model_tag") -> str:
    if not isinstance(value, str) or not _MODEL_TAG.fullmatch(value):
        raise E02Error("E02_MODEL_TAG_INVALID", f"{label} is not a bounded model tag")
    lowered = value.casefold()
    if lowered in {"latest", "gemma4", "embeddinggemma"} or lowered.endswith(":latest"):
        raise E02Error("E02_MODEL_TAG_UNPINNED", f"{label} must not use an alias")
    return value


def _uuid4(value: object, label: str = "job_id") -> str:
    if not isinstance(value, str) or not _UUID4.fullmatch(value):
        raise E02Error("E02_JOB_ID_INVALID", f"{label} must be a lowercase UUIDv4")
    return value


def _canonical_object(raw: bytes, label: str) -> dict[str, Any]:
    def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise E02Error("E02_DUPLICATE_JSON_KEY", f"{label} contains a duplicate key")
            result[key] = value
        return result

    if len(raw) > LIMITS["max_context_bytes"]:
        raise E02Error("E02_PRIVATE_FILE_TOO_LARGE", f"{label} exceeds the C31 context limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise E02Error("E02_JSON_INVALID", f"{label} is not valid JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise E02Error("E02_JSON_NON_CANONICAL", f"{label} is not canonical JSON")
    return value


def _no_symlink_path(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise E02Error("E02_PATH_NOT_ABSOLUTE", f"{label} must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            if current in {Path("/var"), Path("/tmp")} and current.resolve() in {
                Path("/private/var"),
                Path("/private/tmp"),
            }:
                continue
            raise E02Error("E02_SYMLINK_FORBIDDEN", f"{label} traverses a symlink")


def _private_file(path: Path, label: str, *, maximum: int = LIMITS["max_context_bytes"]) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise E02Error("E02_PRIVATE_FILE_INVALID", f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise E02Error("E02_PRIVATE_MODE_INVALID", f"{label} must be mode 0600")
    if path.stat().st_size > maximum:
        raise E02Error("E02_PRIVATE_FILE_TOO_LARGE", f"{label} exceeds the bounded size")
    return path.read_bytes()


def _private_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise E02Error("E02_PRIVATE_DIRECTORY_INVALID", f"{label} must be a directory")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise E02Error("E02_PRIVATE_MODE_INVALID", f"{label} must be mode 0700")


def _ensure_private_directory(path: Path, label: str) -> None:
    if path.is_symlink():
        raise E02Error("E02_SYMLINK_FORBIDDEN", f"{label} is a symlink")
    if path.exists():
        _private_directory(path, label)
        return
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise E02Error("E02_PRIVATE_DIRECTORY_INVALID", f"cannot create {label}")
        current = parent
        if current.is_symlink():
            raise E02Error("E02_SYMLINK_FORBIDDEN", f"{label} parent is a symlink")
    _private_directory(current, f"{label} parent")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        _private_directory(directory, label)


def _write_private_json(path: Path, value: Mapping[str, Any], label: str) -> tuple[str, str, int]:
    payload = canonical_json_bytes(value)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise E02Error("E02_PRIVATE_FILE_INVALID", f"{label} is not a regular file")
    if path.is_file():
        observed = _private_file(path, label)
        if observed == payload:
            return "NO_OP", _sha256_bytes(payload), len(payload)
        raise E02Error("E02_IMMUTABLE_CONFLICT", f"{label} has different bytes")
    _ensure_private_directory(path.parent, f"{label} parent")
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
        observed = _private_file(path, label)
        if observed == payload:
            return "NO_OP", _sha256_bytes(payload), len(payload)
        raise E02Error("E02_IMMUTABLE_CONFLICT", f"{label} has different bytes")
    except BaseException:
        if descriptor != -1:
            os.close(descriptor)
        if path.exists() or path.is_symlink():
            path.unlink()
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)
    return "CREATED", _sha256_bytes(payload), len(payload)


def _storage_scope(
    path: str | Path,
    *,
    storage_root: str | Path | None,
    label: str,
) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise E02Error("E02_STORAGE_PATH_INVALID", f"{label} must be an absolute path")
    _no_symlink_path(candidate, label)
    if storage_root is None:
        raise E02Error("E02_STORAGE_ROOT_REQUIRED", "an explicit internal SSD root is required")
    root = Path(storage_root).expanduser()
    if not root.is_absolute():
        raise E02Error("E02_STORAGE_ROOT_INVALID", "internal SSD root must be absolute")
    _no_symlink_path(root, "internal SSD root")
    if not root.is_dir():
        raise E02Error("E02_STORAGE_ROOT_INVALID", "internal SSD root must be an existing directory")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise E02Error(
            "E02_EXTERNAL_STORAGE_FORBIDDEN",
            f"{label} is outside the declared internal SSD root",
        ) from error
    if any(part in _FORBIDDEN_PATH_PARTS for part in candidate.parts):
        raise E02Error("E02_CANONICAL_PATH_FORBIDDEN", f"{label} enters a canonical or app path")
    return candidate


def _job_paths(job_dir: str | Path) -> HostJobPaths:
    candidate = Path(job_dir).expanduser()
    _no_symlink_path(candidate, "job_dir")
    if not candidate.is_dir():
        raise E02Error("E02_JOB_DIRECTORY_INVALID", "job_dir must be an existing directory")
    _private_directory(candidate, "job_dir")
    if not _UUID4.fullmatch(candidate.name):
        raise E02Error("E02_JOB_ID_INVALID", "job_dir name must be a lowercase UUIDv4")
    if candidate.parent.name != "runs" or candidate.parent.parent.name != "runtime":
        raise E02Error("E02_JOB_DIRECTORY_INVALID", "job_dir must be runtime/runs/JOB_ID")
    if any(part in _FORBIDDEN_PATH_PARTS for part in candidate.parts):
        raise E02Error("E02_CANONICAL_PATH_FORBIDDEN", "job_dir enters a forbidden path")
    receipts = candidate / "receipts"
    if receipts.exists():
        _private_directory(receipts, "receipt directory")
    for name in _JOB_FILENAMES:
        path = candidate / name
        if path.exists() and path.is_symlink():
            raise E02Error("E02_SYMLINK_FORBIDDEN", f"job artifact is a symlink: {name}")
    return HostJobPaths(
        job_dir=candidate,
        context=candidate / "context.json",
        request=candidate / "request.json",
        response=candidate / "response.json",
        receipt=receipts / _RECEIPT_FILENAME,
    )


def _validate_request_and_context(paths: HostJobPaths) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    request_raw = _private_file(paths.request, "C31 request", maximum=LIMITS["max_request_bytes"])
    context_raw = _private_file(paths.context, "C31 context")
    request = _canonical_object(request_raw, "C31 request")
    context = _canonical_object(context_raw, "C31 context")
    request_errors = sorted(
        Draft202012Validator(provider_request_schema(), format_checker=FormatChecker()).iter_errors(request),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if request_errors:
        raise E02Error("E02_REQUEST_INVALID", request_errors[0].message)
    try:
        context = validate_context_envelope(context)
    except ProviderContractError as error:
        raise E02Error("E02_CONTEXT_INVALID", str(error)) from error
    job_id = _uuid4(paths.job_dir.name)
    if request.get("job_id") != job_id or context.get("job_id") != job_id:
        raise E02Error("E02_JOB_ID_DRIFT", "request and context job_id must match job_dir")
    expected_context_path = f"runtime/runs/{job_id}/context.json"
    if request.get("context_path") != expected_context_path:
        raise E02Error("E02_CONTEXT_PATH_INVALID", "request context_path is not the selected job path")
    if request.get("context_sha256") != context.get("context_sha256"):
        raise E02Error("E02_CONTEXT_DIGEST_DRIFT", "request context_sha256 does not match context identity")
    if request.get("request_sha256") != _sha256_json_without(request, "request_sha256"):
        raise E02Error("E02_REQUEST_DIGEST_DRIFT", "request_sha256 does not match request bytes")
    if context.get("context_sha256") != _sha256_json_without(context, "context_sha256"):
        raise E02Error("E02_CONTEXT_DIGEST_DRIFT", "context_sha256 does not match context bytes")
    provider = _mapping(request.get("provider"), "request.provider")
    if provider.get("route") != "local:ollama":
        raise E02Error("E02_ROUTE_INVALID", "E02 host runner accepts only local:ollama")
    _model_tag(provider.get("model_tag"), "request.provider.model_tag")
    _digest(provider.get("model_digest"), "request.provider.model_digest")
    _text(provider.get("ollama_version"), "request.provider.ollama_version", maximum=64)
    return request, context, request_raw, context_raw


def _validate_generation_options(context: Mapping[str, Any]) -> dict[str, Any]:
    options = _mapping(context.get("inference_options"), "context.inference_options")
    expected = {
        "num_ctx": DEFAULT_GENERATION_CONTEXT,
        "stream": False,
        "think": False,
        "tools": False,
        "temperature": 0,
        "seed": DEFAULT_GENERATION_SEED,
        "keep_alive": 0,
        "parallel_requests": 1,
    }
    for key, wanted in expected.items():
        if options.get(key) != wanted:
            raise E02Error(
                "E02_GENERATION_PROFILE_INVALID",
                f"context.inference_options.{key} must be {wanted!r} for the E02 baseline",
            )
    max_output = options.get("max_output_tokens")
    if isinstance(max_output, bool) or not isinstance(max_output, int) or not 0 < max_output <= LIMITS["max_output_tokens"]:
        raise E02Error("E02_GENERATION_PROFILE_INVALID", "max_output_tokens is outside the C31 limit")
    timeout_seconds = options.get("timeout_seconds")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 0 < timeout_seconds <= LIMITS["max_timeout_seconds"]
    ):
        raise E02Error("E02_GENERATION_PROFILE_INVALID", "timeout_seconds is outside the C31 limit")
    return dict(options)


def _parse_json_output(content: object) -> Any:
    if not isinstance(content, str) or not content:
        raise E02Error("E02_OUTPUT_INVALID", "Ollama assistant content must be non-empty JSON text")

    def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise E02Error("E02_OUTPUT_DUPLICATE_KEY", "Ollama output contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(content, object_pairs_hook=strict_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise E02Error("E02_OUTPUT_INVALID", "Ollama output is not valid JSON") from error
    try:
        canonical_json_bytes(value)
    except ProviderContractError as error:
        raise E02Error("E02_OUTPUT_INVALID", "Ollama output is not finite canonical JSON") from error
    return value


def _response_status(error: BaseException) -> str:
    code = getattr(error, "code", "")
    if code in {"OLLAMA_TIMEOUT"}:
        return "timeout"
    if code in {"OLLAMA_MODEL_DIGEST_CONFLICT", "E02_IMMUTABLE_CONFLICT"}:
        return "conflict"
    if code in {"OLLAMA_HTTP_ERROR"} and getattr(error, "status_code", None) == 429:
        return "overloaded"
    if code in {"OLLAMA_CLOUD_NOT_DISABLED", "OLLAMA_ENDPOINT_NOT_LOOPBACK", "OLLAMA_AUTO_PULL_FORBIDDEN"}:
        return "refused"
    return "failed"


def _error_record(error: BaseException, fallback: str = "E02_INPUT_INVALID") -> dict[str, str]:
    code = getattr(error, "code", fallback)
    if not isinstance(code, str) or not _SAFE_ERROR.fullmatch(code):
        code = fallback
    return {"code": code, "message": str(error)[:1000]}


def _endpoint_report(base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    return {
        "base_url": base_url,
        "loopback_only": True,
        "cloud_disabled": True,
        "ollama_host": base_url.removeprefix("http://"),
        "ollama_no_cloud": "1",
        "wildcard_origins": False,
        "allow_redirects": False,
        "allow_proxy": False,
        "allow_tunnel": False,
        "auto_pull": False,
    }


def _validate_base_url(base_url: str) -> str:
    try:
        OllamaProfile(base_url=base_url).endpoint()
    except OllamaError as error:
        raise E02Error(error.code, str(error)) from error
    return base_url


def _validate_host_environment(base_url: str) -> None:
    """Require explicit local-only Ollama settings before an authorized live call."""

    expected_host = base_url.removeprefix("http://")
    configured_host = os.environ.get("OLLAMA_HOST")
    if configured_host != expected_host:
        raise E02Error(
            "E02_HOST_BINDING_INVALID",
            "OLLAMA_HOST must be explicitly set to the loopback endpoint",
        )
    cloud_setting = os.environ.get("OLLAMA_NO_CLOUD")
    if cloud_setting != "1":
        raise E02Error("E02_CLOUD_NOT_DISABLED", "OLLAMA_NO_CLOUD must be 1")
    origins = os.environ.get("OLLAMA_ORIGINS")
    if origins is not None and "*" in origins:
        raise E02Error("E02_WILDCARD_ORIGINS_FORBIDDEN", "wildcard OLLAMA_ORIGINS are forbidden")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        if os.environ.get(name):
            raise E02Error("E02_PROXY_FORBIDDEN", f"{name} must be unset for the host runner")


def generation_profile() -> dict[str, Any]:
    """Return the immutable E02 Gemma generation baseline."""

    return {
        "task": "generation",
        "requested_model_tag": DEFAULT_GENERATION_TAG,
        "options": {
            "num_ctx": DEFAULT_GENERATION_CONTEXT,
            "stream": False,
            "think": False,
            "tools": False,
            "temperature": 0,
            "seed": DEFAULT_GENERATION_SEED,
            "max_output_tokens": LIMITS["max_output_tokens"],
            "keep_alive": 0,
            "parallel_requests": 1,
            "max_queue": DEFAULT_MAX_QUEUE,
        },
        "context_comparison_profiles": [8192, 16384, 32768],
    }


def embedding_profiles() -> list[dict[str, Any]]:
    """Return E02 candidates without changing the frozen C34 profile."""

    return [
        {
            "requested_model_tag": "qwen3-embedding:8b-q4_K_M",
            "quality_tier": "quality_candidate",
            "expected_dimension": 4096,
            "recommended": True,
        },
        {
            "requested_model_tag": "qwen3-embedding:4b-q8_0",
            "quality_tier": "balanced_operational_baseline",
            "expected_dimension": 2560,
            "recommended": True,
        },
        {
            "requested_model_tag": "qwen3-embedding:0.6b-q8_0",
            "quality_tier": "low_resource_control",
            "expected_dimension": 1024,
            "recommended": False,
        },
        {
            "requested_model_tag": "embeddinggemma:300m-qat-q8_0",
            "quality_tier": "c34_compatibility_control",
            "expected_dimension": 768,
            "recommended": False,
        },
        {
            "requested_model_tag": "bge-m3:567m",
            "quality_tier": "optional_multilingual_control",
            "expected_dimension": 1024,
            "recommended": False,
        },
    ]


def e02_contract() -> dict[str, Any]:
    """Return the executable E02 profile and promotion boundary."""

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "capability": CAPABILITY,
        "enabled_by_default": False,
        "endpoint": _endpoint_report(),
        "generation": generation_profile(),
        "embeddings": embedding_profiles(),
        "embedding_query_instruction": DEFAULT_EMBEDDING_QUERY_INSTRUCTION,
        "embedding_document_template": DEFAULT_EMBEDDING_DOCUMENT_TEMPLATE,
        "storage_scope": "internal_ssd_only",
        "external_storage_allowed": False,
        "canonical_c34_mutation_allowed": False,
        "promotion_gate": [
            "live_external_service_identity",
            "privacy",
            "citation_faithfulness",
            "staleness_rejection",
            "korean_multilingual_quality",
            "latency",
            "memory_and_device",
            "digest_replay",
        ],
    }


def _base_report(
    *,
    operation: str,
    status: str,
    authorized: bool,
    base_url: str = DEFAULT_BASE_URL,
    storage_path: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "operation": operation,
        "capability": CAPABILITY,
        "status": status,
        "authorization": {
            "host_service_authorized": bool(authorized),
            "model_download_authorized": False,
            "model_download_performed": False,
        },
        "endpoint": _endpoint_report(base_url),
        "storage": {
            "scope": "internal_ssd",
            "external_storage_used": False,
            "storage_path": storage_path,
            "storage_path_evidence": "caller_declared" if storage_path else "unavailable",
        },
        "profiles": {
            "generation": generation_profile(),
            "embeddings": embedding_profiles(),
        },
        "job": {"job_id": job_id} if job_id else None,
        "service": None,
        "measurements": [],
        "evaluation": None,
        "promotion": {
            "gate_passed": False,
            "live_model_evidence": False,
            "decision": "retain_c34",
            "reasons": ["live E02 evidence is not available"],
        },
        "errors": [],
        "provider_called": False,
        "live_provider_called": False,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
        "source_verified": True,
    }
    return report


def e02_verification_report_schema() -> dict[str, Any]:
    """Return the strict schema for private E02 verification reports."""

    sha = {"type": "string", "pattern": _SHA256.pattern}
    nullable_text = {"oneOf": [{"type": "string"}, {"type": "null"}]}
    endpoint = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "base_url",
            "loopback_only",
            "cloud_disabled",
            "ollama_host",
            "ollama_no_cloud",
            "wildcard_origins",
            "allow_redirects",
            "allow_proxy",
            "allow_tunnel",
            "auto_pull",
        ],
        "properties": {
            "base_url": {"type": "string", "pattern": _BASE_URL.pattern},
            "loopback_only": {"const": True},
            "cloud_disabled": {"const": True},
            "ollama_host": {"type": "string", "pattern": r"^127\.0\.0\.1:[0-9]{1,5}$"},
            "ollama_no_cloud": {"const": "1"},
            "wildcard_origins": {"const": False},
            "allow_redirects": {"const": False},
            "allow_proxy": {"const": False},
            "allow_tunnel": {"const": False},
            "auto_pull": {"const": False},
        },
    }
    model_profile = {
        "type": "object",
        "additionalProperties": False,
        "required": ["requested_model_tag", "quality_tier", "expected_dimension", "recommended"],
        "properties": {
            "requested_model_tag": {"type": "string", "pattern": _MODEL_TAG.pattern},
            "quality_tier": {"type": "string", "minLength": 1},
            "expected_dimension": {"type": "integer", "minimum": 1, "maximum": 16384},
            "recommended": {"type": "boolean"},
        },
    }
    service = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "host_platform",
            "host_architecture",
            "ollama_version",
            "requested_model_tag",
            "resolved_model_name",
            "model_digest",
            "model_size_bytes",
            "quantization",
            "advertised_context",
            "effective_context",
            "backend",
            "device_placement",
            "processor_split",
            "load_state",
            "storage_path",
            "storage_scope",
            "observed_at",
        ],
        "properties": {
            "host_platform": {"type": "string", "minLength": 1},
            "host_architecture": {"type": "string", "minLength": 1},
            "ollama_version": {"type": "string", "minLength": 1},
            "requested_model_tag": {"type": "string", "pattern": _MODEL_TAG.pattern},
            "resolved_model_name": {"type": "string", "pattern": _MODEL_TAG.pattern},
            "model_digest": sha,
            "model_size_bytes": {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
            "quantization": nullable_text,
            "advertised_context": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "effective_context": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "backend": nullable_text,
            "device_placement": nullable_text,
            "processor_split": nullable_text,
            "load_state": {"enum": ["loaded", "not_loaded", "unknown"]},
            "storage_path": {"type": "string", "minLength": 1},
            "storage_scope": {"const": "internal_ssd"},
            "observed_at": {"type": "string", "format": "date-time"},
        },
    }
    measurement = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "profile",
            "phase",
            "model_tag",
            "request_count",
            "success_count",
            "failure_count",
            "total_latency_seconds",
            "first_token_latency_seconds",
            "throughput_per_second",
            "observed_dimension",
            "observed_context",
            "peak_memory_bytes",
            "peak_vram_bytes",
            "device_placement",
            "queue_depth",
            "timeout_count",
            "refusal_count",
            "recovery_count",
            "digest_drift",
            "resource_evidence",
        ],
        "properties": {
            "profile": {"enum": ["generation", "embedding"]},
            "phase": {"enum": ["cold", "warm", "unload", "single"]},
            "model_tag": {"type": "string", "pattern": _MODEL_TAG.pattern},
            "request_count": {"type": "integer", "minimum": 0},
            "success_count": {"type": "integer", "minimum": 0},
            "failure_count": {"type": "integer", "minimum": 0},
            "total_latency_seconds": {"oneOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
            "first_token_latency_seconds": {"oneOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
            "throughput_per_second": {"oneOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
            "observed_dimension": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "observed_context": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "peak_memory_bytes": {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
            "peak_vram_bytes": {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
            "device_placement": nullable_text,
            "queue_depth": {"type": "integer", "minimum": 0, "maximum": DEFAULT_MAX_QUEUE},
            "timeout_count": {"type": "integer", "minimum": 0},
            "refusal_count": {"type": "integer", "minimum": 0},
            "recovery_count": {"type": "integer", "minimum": 0},
            "digest_drift": {"type": "boolean"},
            "resource_evidence": {"enum": ["observed", "unobserved", "not_available"]},
        },
    }
    evaluation = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "candidate_id",
            "case_count",
            "metrics",
            "privacy_gate_passed",
            "citation_gate_passed",
            "staleness_gate_passed",
            "quality_gate_passed",
            "resource_gate_passed",
            "live_model_evidence",
            "canonical_index_mutated",
        ],
        "properties": {
            "candidate_id": {"type": "string", "pattern": _MODEL_TAG.pattern},
            "case_count": {"type": "integer", "minimum": 1},
            "metrics": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}},
            "privacy_gate_passed": {"type": "boolean"},
            "citation_gate_passed": {"type": "boolean"},
            "staleness_gate_passed": {"type": "boolean"},
            "quality_gate_passed": {"type": "boolean"},
            "resource_gate_passed": {"type": "boolean"},
            "live_model_evidence": {"type": "boolean"},
            "canonical_index_mutated": {"const": False},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/e02-verification-report.schema.json",
        "title": "KnowledgeOS E02 host-native Ollama verification report",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "operation",
            "capability",
            "status",
            "authorization",
            "endpoint",
            "storage",
            "profiles",
            "job",
            "service",
            "measurements",
            "evaluation",
            "promotion",
            "errors",
            "provider_called",
            "live_provider_called",
            "mutation_performed",
            "vault_mutation_performed",
            "canonical_apply_allowed",
            "source_verified",
        ],
        "properties": {
            "schema_version": {"const": REPORT_SCHEMA_VERSION},
            "operation": {"enum": ["e02 contract", "e02 inspect", "e02 run", "e02 benchmark", "e02 evaluate"]},
            "capability": {"const": CAPABILITY},
            "status": {"enum": ["PASS", "NO_OP", "DEFERRED", "FAIL", "CONFLICT"]},
            "authorization": {
                "type": "object",
                "additionalProperties": False,
                "required": ["host_service_authorized", "model_download_authorized", "model_download_performed"],
                "properties": {
                    "host_service_authorized": {"type": "boolean"},
                    "model_download_authorized": {"const": False},
                    "model_download_performed": {"const": False},
                },
            },
            "endpoint": endpoint,
            "storage": {
                "type": "object",
                "additionalProperties": False,
                "required": ["scope", "external_storage_used", "storage_path", "storage_path_evidence"],
                "properties": {
                    "scope": {"const": "internal_ssd"},
                    "external_storage_used": {"const": False},
                    "storage_path": nullable_text,
                    "storage_path_evidence": {"enum": ["caller_declared", "observed_internal_ssd", "unavailable"]},
                },
            },
            "profiles": {
                "type": "object",
                "additionalProperties": False,
                "required": ["generation", "embeddings"],
                "properties": {
                    "generation": {"type": "object"},
                    "embeddings": {"type": "array", "items": model_profile},
                },
            },
            "job": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["job_id"],
                        "properties": {
                            "job_id": {"type": "string", "format": "uuid"},
                            "response_path": {"type": "string", "minLength": 1},
                            "receipt_path": {"type": "string", "minLength": 1},
                            "request_sha256": sha,
                            "context_sha256": sha,
                            "response_sha256": sha,
                            "receipt_sha256": sha,
                            "response_byte_length": {"type": "integer", "minimum": 0},
                            "receipt_byte_length": {"type": "integer", "minimum": 0},
                        },
                    },
                ]
            },
            "service": {"oneOf": [{"type": "null"}, service]},
            "measurements": {"type": "array", "maxItems": 100, "items": measurement},
            "evaluation": {"oneOf": [{"type": "null"}, evaluation]},
            "promotion": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gate_passed", "live_model_evidence", "decision", "reasons"],
                "properties": {
                    "gate_passed": {"type": "boolean"},
                    "live_model_evidence": {"type": "boolean"},
                    "decision": {"enum": ["retain_c34", "promote_optional_candidate"]},
                    "reasons": {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 300}},
                },
            },
            "errors": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "message"],
                    "properties": {"code": {"type": "string", "pattern": _SAFE_ERROR.pattern}, "message": {"type": "string", "maxLength": 1000}},
                },
            },
            "provider_called": {"type": "boolean"},
            "live_provider_called": {"type": "boolean"},
            "mutation_performed": {"const": False},
            "vault_mutation_performed": {"const": False},
            "canonical_apply_allowed": {"const": False},
            "source_verified": {"const": True},
        },
    }


def validate_e02_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one report against the executable strict report contract."""

    value = dict(report)
    errors = sorted(
        Draft202012Validator(e02_verification_report_schema(), format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        locator = "/".join(str(part) for part in errors[0].path) or "/"
        raise E02Error("E02_REPORT_INVALID", f"report invalid at {locator}: {errors[0].message}")
    return value


def _deferred_report(
    *,
    operation: str,
    base_url: str = DEFAULT_BASE_URL,
    storage_path: str | None = None,
    job_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    report = _base_report(
        operation=operation,
        status="DEFERRED",
        authorized=False,
        base_url=base_url,
        storage_path=storage_path,
        job_id=job_id,
    )
    report["errors"] = [{
        "code": "E02_AUTHORIZATION_REQUIRED",
        "message": "live host service inspection and inference require explicit authorization",
    }]
    return validate_e02_report(report), EXIT_CONFLICT


def _extract_int(mapping: Mapping[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    for key, value in mapping.items():
        if (
            any(token in str(key).casefold() for token in keys)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        ):
            return value
    return None


def _extract_text(mapping: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    return None


def inspect_e02_service(
    client: E02Client,
    *,
    requested_model_tag: str = DEFAULT_GENERATION_TAG,
    storage_path: str | Path,
    storage_root: str | Path | None,
    authorized: bool = False,
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[dict[str, Any], int]:
    """Inspect Ollama read-only state without pulling or changing service state."""

    _model_tag(requested_model_tag, "requested_model_tag")
    _validate_base_url(base_url)
    if not authorized:
        return _deferred_report(
            operation="e02 inspect",
            base_url=base_url,
            storage_path=str(storage_path),
        )
    try:
        _validate_host_environment(base_url)
        store = _storage_scope(storage_path, storage_root=storage_root, label="model store")
        version = _text(client.version(), "Ollama version", maximum=64)
        tags = _mapping(client.tags(), "Ollama tags")
        models = tags.get("models")
        if not isinstance(models, list):
            raise E02Error("E02_TAGS_INVALID", "Ollama tags.models must be an array")
        selected: Mapping[str, Any] | None = None
        for item in models:
            record = _mapping(item, "Ollama tag record")
            if record.get("name") == requested_model_tag or record.get("model") == requested_model_tag:
                selected = record
                break
        if selected is None:
            raise E02Error("E02_MODEL_UNAVAILABLE", "requested model is not installed; auto-pull is disabled")
        resolved = selected.get("name", selected.get("model"))
        resolved_name = _model_tag(resolved, "resolved_model_name")
        model_digest = _digest(selected.get("digest"), "model digest")
        details = _mapping(client.show(resolved_name), "Ollama show")
        detail_fields = _mapping(details.get("details", {}), "Ollama show.details")
        model_info = _mapping(details.get("model_info", {}), "Ollama show.model_info")
        process = _mapping(client.ps(), "Ollama ps")
        running_models = process.get("models")
        if not isinstance(running_models, list):
            raise E02Error("E02_PS_INVALID", "Ollama ps.models must be an array")
        running: Mapping[str, Any] | None = None
        for item in running_models:
            record = _mapping(item, "Ollama process record")
            if record.get("name", record.get("model")) == resolved_name:
                running = record
                break
        record = running or {}
        report = _base_report(
            operation="e02 inspect",
            status="PASS",
            authorized=True,
            base_url=base_url,
            storage_path=str(store),
        )
        report["provider_called"] = True
        report["live_provider_called"] = True
        report["service"] = {
            "host_platform": platform.system() or "unknown",
            "host_architecture": platform.machine() or "unknown",
            "ollama_version": version,
            "requested_model_tag": requested_model_tag,
            "resolved_model_name": resolved_name,
            "model_digest": model_digest,
            "model_size_bytes": selected.get("size") if isinstance(selected.get("size"), int) else None,
            "quantization": _extract_text(detail_fields, ("quantization_level", "quantization")),
            "advertised_context": _extract_int(model_info, ("context_length", "context")),
            "effective_context": _extract_int(record, ("context_length", "context")),
            "backend": _extract_text(record, ("backend", "library")),
            "device_placement": _extract_text(record, ("device", "devices", "device_placement")),
            "processor_split": _extract_text(record, ("processor", "processor_split", "gpu_layers")),
            "load_state": "loaded" if running is not None else "not_loaded",
            "storage_path": str(store),
            "storage_scope": "internal_ssd",
            "observed_at": _now(),
        }
        report["promotion"] = {
            "gate_passed": False,
            "live_model_evidence": False,
            "decision": "retain_c34",
            "reasons": ["inspection identity alone cannot satisfy quality, privacy, citation, staleness, latency, and memory gates"],
        }
        return validate_e02_report(report), EXIT_OK
    except (E02Error, OllamaError, OSError, TypeError, ValueError) as error:
        report = _base_report(
            operation="e02 inspect",
            status="FAIL",
            authorized=True,
            base_url=base_url,
            storage_path=str(storage_path),
        )
        report["errors"] = [_error_record(error, "E02_INSPECTION_FAILED")]
        return validate_e02_report(report), EXIT_INPUT_INVALID


def run_e02_host_job(
    job_dir: str | Path,
    client: E02Client,
    *,
    storage_root: str | Path | None,
    authorized: bool = False,
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[dict[str, Any], int]:
    """Run one C31 generation job through a job-spool-only host boundary."""

    _validate_base_url(base_url)
    paths = _job_paths(job_dir)
    job_id = _uuid4(paths.job_dir.name)
    if not authorized:
        return _deferred_report(
            operation="e02 run",
            base_url=base_url,
            storage_path=str(paths.job_dir),
            job_id=job_id,
        )
    _validate_host_environment(base_url)
    _storage_scope(paths.job_dir, storage_root=storage_root, label="job_dir")
    try:
        request, context, request_raw, context_raw = _validate_request_and_context(paths)
        provider = _mapping(request["provider"], "request.provider")
        if provider.get("model_tag") != DEFAULT_GENERATION_TAG:
            raise E02Error("E02_GENERATION_MODEL_INVALID", "E02 generation jobs require requested tag gemma4:12b")
        _validate_generation_options(context)
        prompt = _mapping(context.get("prompt"), "context.prompt")
        prompt_text = _text(prompt.get("text"), "context.prompt.text", maximum=LIMITS["max_prompt_bytes"])
        if paths.response.exists():
            response_raw = _private_file(paths.response, "existing C31 response")
            response = _canonical_object(response_raw, "existing C31 response")
            receipt_raw = _private_file(paths.receipt, "existing C31 receipt")
            receipt = _canonical_object(receipt_raw, "existing C31 receipt")
            response_errors = sorted(
                Draft202012Validator(provider_response_schema()).iter_errors(response),
                key=lambda error: tuple(str(part) for part in error.path),
            )
            receipt_errors = sorted(
                Draft202012Validator(provider_receipt_schema()).iter_errors(receipt),
                key=lambda error: tuple(str(part) for part in error.path),
            )
            if response_errors or receipt_errors:
                raise E02Error("E02_REPLAY_INVALID", "existing response or receipt violates the C31 schema")
            if response.get("response_sha256") != _sha256_json_without(response, "response_sha256"):
                raise E02Error("E02_RESPONSE_DIGEST_DRIFT", "existing response digest is invalid")
            if receipt.get("receipt_sha256") != _sha256_json_without(receipt, "receipt_sha256"):
                raise E02Error("E02_RECEIPT_DIGEST_DRIFT", "existing receipt digest is invalid")
            if (
                response.get("job_id") != job_id
                or response.get("request_sha256") != request["request_sha256"]
                or receipt.get("response_sha256") != response["response_sha256"]
                or receipt.get("request_sha256") != request["request_sha256"]
            ):
                raise E02Error("E02_REPLAY_CONFLICT", "existing response and receipt are not bound to this request")
            report = _base_report(
                operation="e02 run",
                status="NO_OP",
                authorized=True,
                base_url=base_url,
                storage_path=str(paths.job_dir),
                job_id=job_id,
            )
            report["provider_called"] = False
            report["live_provider_called"] = False
            report["promotion"]["reasons"] = ["identical response and receipt already exist"]
            return validate_e02_report(report), EXIT_OK

        identity = client.verify_identity(
            str(provider["model_tag"]),
            str(provider["model_digest"]),
            expected_version=str(provider["ollama_version"]),
        )
        identity_record = _mapping(identity, "Ollama identity")
        if identity_record.get("model_digest") != provider["model_digest"]:
            raise E02Error("E02_IDENTITY_DIGEST_DRIFT", "verified Ollama identity digest differs from C31 request")
        if identity_record.get("model") != provider["model_tag"]:
            raise E02Error("E02_IDENTITY_MODEL_DRIFT", "verified Ollama model differs from C31 request")
        observed_version = identity_record.get("version")
        if observed_version is not None and observed_version != provider["ollama_version"]:
            raise E02Error("E02_IDENTITY_VERSION_DRIFT", "verified Ollama version differs from C31 request")
        options = _mapping(context["inference_options"], "context.inference_options")
        response = client.chat(
            str(provider["model_tag"]),
            [{"role": "user", "content": prompt_text}],
            options={
                "num_ctx": options["num_ctx"],
                "temperature": options["temperature"],
                "seed": options["seed"],
                "num_predict": options["max_output_tokens"],
            },
            format="json",
            keep_alive=0,
        )
        message = _mapping(response.get("message"), "Ollama response.message")
        output = _parse_json_output(message.get("content"))
        received_at = _now()
        c31_response = build_provider_response(
            request,
            status="completed",
            output=output,
            received_at=received_at,
            completed_at=received_at,
            response_id=f"e02:{request['request_sha256']}",
        )
        c31_receipt = build_identity_receipt(
            request,
            c31_response,
            provider_called=True,
            created_at=received_at,
            receipt_id=f"e02:{request['request_sha256']}",
        )
        response_state, response_digest, response_bytes = _write_private_json(
            paths.response,
            c31_response,
            "C31 response",
        )
        receipt_state, receipt_digest, receipt_bytes = _write_private_json(
            paths.receipt,
            c31_receipt,
            "C31 receipt",
        )
        report = _base_report(
            operation="e02 run",
            status="PASS",
            authorized=True,
            base_url=base_url,
            storage_path=str(paths.job_dir),
            job_id=job_id,
        )
        report["provider_called"] = True
        report["live_provider_called"] = True
        report["measurements"] = [{
            "profile": "generation",
            "phase": "single",
            "model_tag": str(provider["model_tag"]),
            "request_count": 1,
            "success_count": 1,
            "failure_count": 0,
            "total_latency_seconds": None,
            "first_token_latency_seconds": None,
            "throughput_per_second": None,
            "observed_dimension": None,
            "observed_context": None,
            "peak_memory_bytes": None,
            "peak_vram_bytes": None,
            "device_placement": None,
            "queue_depth": 0,
            "timeout_count": 0,
            "refusal_count": 0,
            "recovery_count": 0,
            "digest_drift": False,
            "resource_evidence": "unobserved",
        }]
        report["promotion"]["reasons"] = [
            "C31 response and receipt persisted; live quality and resource gates remain open"
        ]
        report["job"]["response_path"] = "response.json"
        report["job"]["receipt_path"] = f"receipts/{_RECEIPT_FILENAME}"
        report["job"]["request_sha256"] = request["request_sha256"]
        report["job"]["context_sha256"] = request["context_sha256"]
        report["job"]["response_sha256"] = response_digest
        report["job"]["response_byte_length"] = response_bytes
        report["job"]["receipt_sha256"] = receipt_digest
        report["job"]["receipt_byte_length"] = receipt_bytes
        del request_raw, context_raw, response_state, receipt_state
        return validate_e02_report(report), EXIT_OK
    except (E02Error, OllamaError, ProviderContractError, OSError, TypeError, ValueError) as error:
        report = _base_report(
            operation="e02 run",
            status="FAIL" if not isinstance(error, E02Error) or error.code != "E02_IMMUTABLE_CONFLICT" else "CONFLICT",
            authorized=True,
            base_url=base_url,
            storage_path=str(paths.job_dir),
            job_id=job_id,
        )
        report["errors"] = [_error_record(error, "E02_RUN_FAILED")]
        return validate_e02_report(report), EXIT_CONFLICT if report["status"] == "CONFLICT" else EXIT_INPUT_INVALID


def write_e02_report(
    job_dir: str | Path,
    report: Mapping[str, Any],
    *,
    storage_root: str | Path | None,
) -> dict[str, Any]:
    """Persist one validated E02 report as a private immutable job artifact."""

    paths = _job_paths(job_dir)
    _storage_scope(paths.job_dir, storage_root=storage_root, label="job_dir")
    validated = validate_e02_report(report)
    job = validated.get("job")
    if not isinstance(job, Mapping) or job.get("job_id") != paths.job_dir.name:
        raise E02Error("E02_REPORT_JOB_DRIFT", "report job_id must match the selected job directory")
    state, digest, byte_length = _write_private_json(
        paths.job_dir / REPORT_FILENAME,
        validated,
        "E02 verification report",
    )
    return {
        "state": state,
        "path": REPORT_FILENAME,
        "sha256": digest,
        "byte_length": byte_length,
    }


def _metric(ranking: Sequence[str], expected: set[str]) -> dict[str, float]:
    positions = {path: index + 1 for index, path in enumerate(ranking)}
    if not expected:
        raise E02Error("E02_EVALUATION_INVALID", "expected_paths must not be empty")
    result: dict[str, float] = {}
    for limit in (1, 3, 5):
        result[f"recall_at_{limit}"] = len(expected.intersection(ranking[:limit])) / len(expected)
    reciprocal = [1.0 / positions[path] for path in expected if path in positions]
    result["mrr"] = max(reciprocal, default=0.0)
    return result


def evaluate_e02_rankings(
    cases: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
    live_model_evidence: bool,
    privacy_gate_passed: bool,
    citation_gate_passed: bool,
    staleness_gate_passed: bool,
    resource_gate_passed: bool,
    external_storage_used: bool = False,
) -> tuple[dict[str, Any], int]:
    """Evaluate recorded frozen-fixture rankings without touching C34 artifacts."""

    candidate = _model_tag(candidate_id, "candidate_id")
    if not cases:
        raise E02Error("E02_EVALUATION_INVALID", "at least one frozen evaluation case is required")
    channel_totals: dict[str, dict[str, float]] = {}
    for raw_case in cases:
        case = _mapping(raw_case, "evaluation case")
        expected = case.get("expected_paths")
        rankings = _mapping(case.get("rankings"), "evaluation case rankings")
        if not isinstance(expected, list) or not expected or not all(isinstance(item, str) for item in expected):
            raise E02Error("E02_EVALUATION_INVALID", "expected_paths must be a non-empty string array")
        expected_set = set(expected)
        for channel, raw_ranking in rankings.items():
            if not isinstance(raw_ranking, list) or not all(isinstance(item, str) for item in raw_ranking):
                raise E02Error("E02_EVALUATION_INVALID", f"ranking channel {channel} is invalid")
            metrics = _metric(raw_ranking, expected_set)
            total = channel_totals.setdefault(channel, {key: 0.0 for key in metrics})
            for key, value in metrics.items():
                total[key] += value
    count = float(len(cases))
    averaged = {
        channel: {key: value / count for key, value in values.items()}
        for channel, values in sorted(channel_totals.items())
    }
    live_metrics = averaged.get(candidate, averaged.get("live", {}))
    c34_metrics = averaged.get("c34_learned", {})
    quality_gate = bool(
        live_metrics
        and c34_metrics
        and live_metrics.get("recall_at_3", 0.0) >= c34_metrics.get("recall_at_3", 0.0)
        and live_metrics.get("mrr", 0.0) >= c34_metrics.get("mrr", 0.0)
    )
    gate_passed = bool(
        live_model_evidence
        and quality_gate
        and privacy_gate_passed
        and citation_gate_passed
        and staleness_gate_passed
        and resource_gate_passed
        and not external_storage_used
    )
    evaluation = {
        "candidate_id": candidate,
        "case_count": len(cases),
        "metrics": {
            f"{channel}_{key}": round(value, 8)
            for channel, values in averaged.items()
            for key, value in values.items()
        },
        "privacy_gate_passed": bool(privacy_gate_passed),
        "citation_gate_passed": bool(citation_gate_passed),
        "staleness_gate_passed": bool(staleness_gate_passed),
        "quality_gate_passed": quality_gate,
        "resource_gate_passed": bool(resource_gate_passed),
        "live_model_evidence": bool(live_model_evidence),
        "canonical_index_mutated": False,
    }
    report = _base_report(operation="e02 evaluate", status="PASS", authorized=live_model_evidence)
    report["evaluation"] = evaluation
    report["promotion"] = {
        "gate_passed": gate_passed,
        "live_model_evidence": bool(live_model_evidence),
        "decision": "promote_optional_candidate" if gate_passed else "retain_c34",
        "reasons": (
            ["all E02 live quality, privacy, citation, staleness, resource, and storage gates passed"]
            if gate_passed
            else ["E02 evidence does not satisfy every promotion gate; keep C34 unchanged"]
        ),
    }
    if external_storage_used:
        report["errors"] = [{
            "code": "E02_EXTERNAL_STORAGE_FORBIDDEN",
            "message": "external storage evidence cannot promote an E02 candidate",
        }]
    return validate_e02_report(report), EXIT_OK


def benchmark_generation(
    client: E02Client,
    prompts: Sequence[str],
    *,
    model_tag: str = DEFAULT_GENERATION_TAG,
    authorized: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    storage_path: str | Path | None = None,
    storage_root: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Measure serial cold/warm/unload generation without retaining output text."""

    _model_tag(model_tag, "model_tag")
    if model_tag != DEFAULT_GENERATION_TAG:
        raise E02Error("E02_GENERATION_MODEL_INVALID", "E02 generation benchmarks require model tag gemma4:12b")
    _validate_base_url(base_url)
    if not authorized:
        return _deferred_report(operation="e02 benchmark", base_url=base_url, storage_path=str(storage_path) if storage_path else None)
    _validate_host_environment(base_url)
    if storage_path is None or storage_root is None:
        raise E02Error("E02_STORAGE_ROOT_REQUIRED", "authorized benchmarks require an internal SSD path and root")
    if not prompts or len(prompts) > DEFAULT_MAX_QUEUE:
        raise E02Error("E02_QUEUE_INVALID", "the safe benchmark accepts one prompt at a time")
    for prompt in prompts:
        _text(prompt, "benchmark prompt", maximum=LIMITS["max_prompt_bytes"])
    _storage_scope(storage_path, storage_root=storage_root, label="benchmark artifacts")
    report = _base_report(
        operation="e02 benchmark",
        status="PASS",
        authorized=True,
        base_url=base_url,
        storage_path=str(storage_path) if storage_path else None,
    )
    report["provider_called"] = True
    report["live_provider_called"] = True
    for phase, keep_alive in (("cold", 0), ("warm", 300), ("unload", 0)):
        successes = 0
        failures = 0
        timeout_count = 0
        refusal_count = 0
        digest_drift = False
        latencies: list[float] = []
        for prompt in prompts:
            started = time.perf_counter()
            try:
                client.chat(
                    model_tag,
                    [{"role": "user", "content": prompt}],
                    options={
                        "num_ctx": DEFAULT_GENERATION_CONTEXT,
                        "temperature": 0,
                        "seed": DEFAULT_GENERATION_SEED,
                        "num_predict": LIMITS["max_output_tokens"],
                    },
                    format="json",
                    keep_alive=keep_alive,
                )
                successes += 1
            except (OllamaError, OSError, TypeError, ValueError) as error:
                failures += 1
                outcome = _response_status(error)
                timeout_count += outcome == "timeout"
                refusal_count += outcome == "refused"
                digest_drift = digest_drift or outcome == "conflict"
            latencies.append(max(0.0, time.perf_counter() - started))
        total = sum(latencies) if latencies else None
        report["measurements"].append({
            "profile": "generation",
            "phase": phase,
            "model_tag": model_tag,
            "request_count": len(prompts),
            "success_count": successes,
            "failure_count": failures,
            "total_latency_seconds": total,
            "first_token_latency_seconds": None,
            "throughput_per_second": successes / total if total and successes else None,
            "observed_dimension": None,
            "observed_context": None,
            "peak_memory_bytes": None,
            "peak_vram_bytes": None,
            "device_placement": None,
            "queue_depth": 0,
            "timeout_count": timeout_count,
            "refusal_count": refusal_count,
            "recovery_count": 0,
            "digest_drift": digest_drift,
            "resource_evidence": "unobserved",
        })
    report["promotion"]["reasons"] = [
        "latency samples are recorded; host memory, device, citation, quality, and staleness gates remain open"
    ]
    return validate_e02_report(report), EXIT_OK


def benchmark_embeddings(
    client: E02Client,
    inputs: Sequence[str],
    *,
    model_tags: Sequence[str] | None = None,
    authorized: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    storage_path: str | Path | None = None,
    storage_root: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Measure selected embedding candidates using distinct Qwen query/document roles."""

    _validate_base_url(base_url)
    if not inputs or len(inputs) > 2:
        raise E02Error("E02_EMBED_INPUT_INVALID", "embedding benchmark accepts one or two bounded inputs")
    for item in inputs:
        _text(item, "embedding input", maximum=LIMITS["max_prompt_bytes"])
    selected = set(model_tags or [item["requested_model_tag"] for item in embedding_profiles()])
    profile_map = {item["requested_model_tag"]: item for item in embedding_profiles()}
    if not selected or not selected.issubset(profile_map):
        raise E02Error("E02_EMBED_MODEL_INVALID", "model_tags must select declared E02 embedding candidates")
    if not authorized:
        return _deferred_report(operation="e02 benchmark", base_url=base_url, storage_path=str(storage_path) if storage_path else None)
    _validate_host_environment(base_url)
    if storage_path is None or storage_root is None:
        raise E02Error("E02_STORAGE_ROOT_REQUIRED", "authorized benchmarks require an internal SSD path and root")
    _storage_scope(storage_path, storage_root=storage_root, label="benchmark artifacts")
    report = _base_report(
        operation="e02 benchmark",
        status="PASS",
        authorized=True,
        base_url=base_url,
        storage_path=str(storage_path) if storage_path else None,
    )
    report["provider_called"] = True
    report["live_provider_called"] = True
    query_input = f"{DEFAULT_EMBEDDING_QUERY_INSTRUCTION}: {inputs[0]}"
    document_input = DEFAULT_EMBEDDING_DOCUMENT_TEMPLATE.replace("{title | none}", "none").replace(
        "{text}",
        inputs[1] if len(inputs) == 2 else inputs[0],
    )
    request_inputs = [query_input, document_input]
    for tag in sorted(selected):
        started = time.perf_counter()
        failures = 0
        timeout_count = 0
        refusal_count = 0
        digest_drift = False
        observed_dimension: int | None = None
        try:
            response = client.embed(
                tag,
                request_inputs,
                truncate=False,
                keep_alive=0,
            )
            vectors = response.get("embeddings") if isinstance(response, Mapping) else None
            if not isinstance(vectors, list) or not vectors:
                raise E02Error("E02_EMBED_RESPONSE_INVALID", "embedding response has no vectors")
            observed_dimension = len(vectors[0]) if isinstance(vectors[0], list) else None
            if observed_dimension is None or any(not isinstance(vector, list) or len(vector) != observed_dimension for vector in vectors):
                raise E02Error("E02_EMBED_RESPONSE_INVALID", "embedding response dimensions are inconsistent")
            if response.get("truncated") is True:
                raise E02Error("E02_TRUNCATION_REPORTED", "embedding service reported truncation")
        except (E02Error, OllamaError, OSError, TypeError, ValueError) as error:
            failures = 1
            outcome = _response_status(error)
            timeout_count = int(outcome == "timeout")
            refusal_count = int(outcome == "refused")
            digest_drift = outcome == "conflict"
        elapsed = max(0.0, time.perf_counter() - started)
        report["measurements"].append({
            "profile": "embedding",
            "phase": "single",
            "model_tag": tag,
            "request_count": len(request_inputs),
            "success_count": 0 if failures else len(request_inputs),
            "failure_count": failures,
            "total_latency_seconds": elapsed,
            "first_token_latency_seconds": None,
            "throughput_per_second": len(inputs) / elapsed if elapsed and not failures else None,
            "observed_dimension": observed_dimension,
            "observed_context": None,
            "peak_memory_bytes": None,
            "peak_vram_bytes": None,
            "device_placement": None,
            "queue_depth": 0,
            "timeout_count": timeout_count,
            "refusal_count": refusal_count,
            "recovery_count": 0,
            "digest_drift": digest_drift,
            "resource_evidence": "unobserved",
        })
    report["promotion"]["reasons"] = [
        "embedding latency and dimension samples are recorded; quality and host resource gates remain open"
    ]
    return validate_e02_report(report), EXIT_OK


def build_client(
    base_url: str = DEFAULT_BASE_URL,
    *,
    relay_socket: str | Path | None = None,
) -> OllamaClient:
    """Build the explicit loopback client used by the live-gated CLI."""

    return OllamaClient(OllamaProfile(base_url=base_url, relay_socket=relay_socket))


__all__ = [
    "CAPABILITY",
    "DEFAULT_BASE_URL",
    "DEFAULT_GENERATION_TAG",
    "REPORT_FILENAME",
    "REPORT_SCHEMA_PATH",
    "E02Client",
    "E02Error",
    "HostJobPaths",
    "benchmark_embeddings",
    "benchmark_generation",
    "build_client",
    "e02_contract",
    "e02_verification_report_schema",
    "embedding_profiles",
    "evaluate_e02_rankings",
    "generation_profile",
    "inspect_e02_service",
    "run_e02_host_job",
    "validate_e02_report",
    "write_e02_report",
]
