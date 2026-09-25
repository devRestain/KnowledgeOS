"""C35 schema-constrained Gemma route orchestration.

This module is deliberately a recorded-response harness.  It consumes an
already validated C31 request/context pair and one Ollama-shaped response
that was recorded by a test or review harness.  It never opens a socket,
mounts or edits the Vault, invokes C19 apply, or enables a live provider.

The C33 transport already proves that Ollama can be reached through a bounded
loopback adapter.  C35 owns the next boundary: route-specific output
validation and provenance checks after a model response has been decoded.
The recorded response is treated as untrusted data; only the independently
validated output is written to private runtime artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .generation_identity import GENERATION_MODEL_TAG
from .provider_contract import (
    LIMITS,
    ProviderConflict,
    ProviderContractError,
    build_identity_receipt,
    build_provider_response,
    canonical_json_bytes,
    provider_schema,
    read_context_envelope,
)
from .recovery import fsync_directory

EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_CONFLICT = 30

CAPABILITY = "C35"
OPERATION = "ai gemma"
GEMMA_MODEL_TAG = GENERATION_MODEL_TAG

RESPONSE_FILENAME = "c35-response.json"
RECEIPT_FILENAME = "c35-receipt.json"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "system message",
    "developer message",
    "<tool_call",
    "<|tool|>",
    "exfiltrate secrets",
)
_TOOL_KEYS = frozenset(
    {"tool", "tools", "tool_call", "tool_calls", "function_call", "function_calls"}
)
_REASONING_KEYS = frozenset(
    {"thinking", "reasoning", "analysis", "chain_of_thought", "cot"}
)
_OLLAMA_RESPONSE_KEYS = frozenset(
    {
        "model",
        "created_at",
        "message",
        "done",
        "done_reason",
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_cached_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    }
)
_OLLAMA_MESSAGE_KEYS = frozenset({"role", "content"})

_ACTION_FILES = {
    "answer": "ops/actions/answer.json",
    "triage": "ops/actions/triage.json",
    "draft_note": "ops/actions/draft-note.json",
    "link_suggestions": "ops/actions/link-suggestions.json",
    "normalize": "ops/actions/normalize.json",
}
_PROMPT_FILES = {
    "answer": "ops/prompts/answer.md",
    "triage": "ops/prompts/triage.md",
    "draft_note": "ops/prompts/draft-note.md",
    "link_suggestions": "ops/prompts/link-suggestions.md",
    "normalize": "ops/prompts/normalize.md",
}


@dataclass(frozen=True)
class GemmaRoute:
    """One C35 route and its authoritative existing output schema."""

    name: str
    action: str
    schema_path: str
    schema_fragment: str | None
    artifact_kind: str
    human_approval_required: bool


C35_ROUTES: dict[str, GemmaRoute] = {
    "answer": GemmaRoute(
        name="answer",
        action="answer",
        schema_path="ops/schemas/answer.schema.json",
        schema_fragment="answer",
        artifact_kind="answer",
        human_approval_required=False,
    ),
    "triage": GemmaRoute(
        name="triage",
        action="triage",
        schema_path="ops/schemas/triage-result.schema.json",
        schema_fragment=None,
        artifact_kind="proposal",
        human_approval_required=True,
    ),
    "draft_note": GemmaRoute(
        name="draft_note",
        action="draft_note",
        schema_path="ops/schemas/proposal.schema.json",
        schema_fragment=None,
        artifact_kind="proposal",
        human_approval_required=True,
    ),
    "link_suggestions": GemmaRoute(
        name="link_suggestions",
        action="link_suggestions",
        schema_path="ops/schemas/proposal.schema.json",
        schema_fragment=None,
        artifact_kind="proposal",
        human_approval_required=True,
    ),
    "normalize": GemmaRoute(
        name="normalize",
        action="normalize",
        schema_path="ops/schemas/proposal.schema.json",
        schema_fragment=None,
        artifact_kind="proposal",
        human_approval_required=True,
    ),
}

_ROUTE_ALIASES = {
    "answer": "answer",
    "cited-answer": "answer",
    "triage": "triage",
    "draft": "draft_note",
    "draft_note": "draft_note",
    "draft-note": "draft_note",
    "link": "link_suggestions",
    "link_suggestions": "link_suggestions",
    "link-suggestions": "link_suggestions",
    "normalize": "normalize",
}


class GemmaRouteError(ProviderContractError):
    """Raised when a C35 route or recorded output fails closed."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _without_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(field, None)
    return result


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GemmaRouteError("C35_JSON_DUPLICATE_KEY", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise GemmaRouteError("C35_JSON_INVALID", f"non-finite JSON value is not allowed: {value}")


def _strict_json(raw: bytes | str, *, label: str) -> Any:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except GemmaRouteError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GemmaRouteError("C35_JSON_INVALID", f"{label} is not valid JSON") from error
    return value


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GemmaRouteError("C35_INPUT_INVALID", f"{label} must be an object")
    return value


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise GemmaRouteError("C35_INPUT_INVALID", f"{label} must be an array")
    return value


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise GemmaRouteError("C35_HASH_INVALID", f"{label} must be lowercase SHA-256")
    return value


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise GemmaRouteError("C35_ROOT_INVALID", "control root must be an existing non-symlink directory")
    return candidate.resolve()


def _job_directory(workspace: Path, job_id: str) -> Path:
    if not isinstance(job_id, str) or not _UUID4.fullmatch(job_id):
        raise GemmaRouteError("C35_JOB_ID_INVALID", "job_id must be a lowercase UUIDv4")
    runtime = workspace / "runtime"
    runs = runtime / "runs"
    job = runs / job_id
    for path, label in ((runtime, "runtime"), (runs, "runtime/runs"), (job, "runtime job")):
        if path.is_symlink() or not path.is_dir():
            raise GemmaRouteError("C35_RUNTIME_PATH_INVALID", f"{label} must be an existing directory")
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise GemmaRouteError("C35_RUNTIME_MODE_INVALID", f"{label} must be mode 0700")
    return job


def _read_private_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise GemmaRouteError("C35_RUNTIME_FILE_INVALID", f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise GemmaRouteError("C35_RUNTIME_MODE_INVALID", f"{label} must be mode 0600")
    raw = path.read_bytes()
    value = _strict_json(raw, label=label)
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise GemmaRouteError("C35_SERIALIZATION_INVALID", f"{label} is not canonical JSON")
    return value, raw


def _write_private_json(path: Path, value: Mapping[str, Any]) -> tuple[str, str, int]:
    payload = canonical_json_bytes(value)
    if len(payload) > LIMITS["max_response_bytes"]:
        raise GemmaRouteError("C35_ARTIFACT_TOO_LARGE", "private C35 artifact exceeds the response limit")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GemmaRouteError("C35_RUNTIME_FILE_INVALID", f"private C35 artifact is not a file: {path.name}")
    if path.is_file():
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise GemmaRouteError("C35_RUNTIME_MODE_INVALID", f"private C35 artifact must be mode 0600: {path.name}")
        existing = path.read_bytes()
        if existing == payload:
            return "NO_OP", _sha256(payload), len(payload)
        raise ProviderConflict("C35_DIGEST_CONFLICT", f"private C35 artifact differs: {path.name}")
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
    return "CREATED", _sha256(payload), len(payload)


def _verify_digest(document: Mapping[str, Any], field: str, code: str) -> None:
    expected = _digest_json(_without_digest(document, field))
    if document.get(field) != expected:
        raise GemmaRouteError(code, f"{field} does not match canonical envelope bytes")


def _validate_envelope(document: Mapping[str, Any], kind: str) -> None:
    errors = sorted(
        Draft202012Validator(provider_schema(kind), format_checker=FormatChecker()).iter_errors(document),
        key=lambda item: tuple(str(part) for part in item.path),
    )
    if errors:
        error = errors[0]
        locator = "/".join(str(part) for part in error.path) or "/"
        raise GemmaRouteError("C35_ENVELOPE_INVALID", f"{kind} envelope invalid at {locator}: {error.message}")


def _read_request_and_context(workspace: Path, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    job = _job_directory(workspace, job_id)
    request, _ = _read_private_json(job / "request.json", label="provider request")
    _validate_envelope(request, "request")
    _verify_digest(request, "request_sha256", "C35_REQUEST_DIGEST_DRIFT")
    if request["job_id"] != job_id:
        raise GemmaRouteError("C35_JOB_ID_DRIFT", "request job_id does not match the selected job")
    expected_context_path = f"runtime/runs/{job_id}/context.json"
    if request["context_path"] != expected_context_path:
        raise GemmaRouteError("C35_CONTEXT_PATH_INVALID", "request context_path is outside the selected job")
    context = read_context_envelope(workspace, job_id=job_id)
    if request["context_sha256"] != context["context_sha256"]:
        raise GemmaRouteError("C35_CONTEXT_DRIFT", "request context digest does not match frozen context")
    bindings = {
        "prompt_sha256": context["prompt"]["sha256"],
        "output_schema": context["output_schema"],
        "policy_decision": context["policy_decision"],
        "index_generation_id": context["index_generation_id"],
        "candidate_set_sha256": context["candidate_set_sha256"],
        "source_hashes_sha256": context["source_hashes_sha256"],
        "action": context["action"],
        "provider": context["provider"],
        "inference_options": context["inference_options"],
        "limits": context["limits"],
        "runner_boundary": context["runner_boundary"],
    }
    for field, expected in bindings.items():
        if request[field] != expected:
            raise GemmaRouteError("C35_CONTEXT_BINDING_DRIFT", f"request field does not match context: {field}")
    return request, context


def _resolve_route(action: str, requested: str | None) -> GemmaRoute:
    if requested is not None:
        canonical = _ROUTE_ALIASES.get(requested)
        if canonical is None:
            raise GemmaRouteError("C35_ROUTE_INVALID", f"unsupported C35 route: {requested}")
        route = C35_ROUTES[canonical]
        if action != route.action:
            raise GemmaRouteError("C35_ROUTE_ACTION_MISMATCH", "requested route does not match the C31 action")
        return route
    canonical = _ROUTE_ALIASES.get(action)
    if canonical is None:
        raise GemmaRouteError("C35_ROUTE_UNSUPPORTED", f"C35 does not implement the action: {action}")
    return C35_ROUTES[canonical]


def _validate_gemma_provider(context: Mapping[str, Any]) -> None:
    provider = _as_mapping(context.get("provider"), "provider")
    route = provider.get("route")
    if not isinstance(route, str) or not route.startswith("local:"):
        raise GemmaRouteError("C35_PROVIDER_ROUTE_INVALID", "C35 recorded routes require a local provider route")
    if provider.get("model_tag") != GEMMA_MODEL_TAG:
        raise GemmaRouteError(
            "C35_MODEL_UNSUPPORTED",
            f"C35 is pinned to the generated Gemma profile: {GEMMA_MODEL_TAG}",
        )
    if context["policy_decision"].get("decision") != "allow_local":
        raise GemmaRouteError("C35_POLICY_DENIED", "C35 requires an allow_local policy decision")
    options = _as_mapping(context.get("inference_options"), "inference_options")
    if options.get("stream") is not False or options.get("think") is not False or options.get("tools") is not False:
        raise GemmaRouteError("C35_INFERENCE_OPTIONS_INVALID", "C35 requires non-streaming text-only inference without tools or reasoning")


def _find_anchor(document: Mapping[str, Any], anchor: str) -> Any:
    if document.get("$anchor") == anchor:
        return document
    for value in document.values():
        if isinstance(value, Mapping):
            found = _find_anchor(value, anchor)
            if found is not None:
                return found
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    found = _find_anchor(item, anchor)
                    if found is not None:
                        return found
    return None


def _schema_for_route(workspace: Path, context: Mapping[str, Any], route: GemmaRoute) -> tuple[dict[str, Any], dict[str, Any]]:
    binding = _as_mapping(context.get("output_schema"), "output_schema")
    raw_path = binding.get("path")
    if not isinstance(raw_path, str):
        raise GemmaRouteError("C35_OUTPUT_SCHEMA_INVALID", "output schema path is required")
    if "#" in raw_path:
        relative, fragment = raw_path.split("#", 1)
    else:
        relative, fragment = raw_path, ""
    path = workspace / relative
    if path.is_symlink() or not path.is_file() or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise GemmaRouteError("C35_OUTPUT_SCHEMA_INVALID", "output schema path is unsafe or unavailable")
    expected_path = route.schema_path
    expected_fragment = route.schema_fragment or ""
    if path.relative_to(workspace).as_posix() != expected_path or fragment != expected_fragment:
        raise GemmaRouteError("C35_OUTPUT_SCHEMA_BINDING", "C31 output schema is not the C35 route schema")
    raw = path.read_bytes()
    observed = _sha256(raw)
    _validate_hash(binding.get("sha256"), "output_schema.sha256")
    if observed != binding["sha256"]:
        raise GemmaRouteError("C35_OUTPUT_SCHEMA_DRIFT", "bound output schema bytes changed")
    document = _strict_json(raw, label="bound output schema")
    if not isinstance(document, Mapping):
        raise GemmaRouteError("C35_OUTPUT_SCHEMA_INVALID", "bound output schema must be an object")
    selected: Any = document
    if fragment:
        selected = _find_anchor(document, fragment)
        if not isinstance(selected, Mapping):
            raise GemmaRouteError("C35_OUTPUT_SCHEMA_INVALID", "bound output schema anchor is missing")
    return dict(selected), {
        "path": expected_path,
        "fragment": expected_fragment or None,
        "sha256": observed,
    }


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for child in value.values():
            result.extend(_all_strings(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_all_strings(child))
        return result
    return []


def _reject_untrusted_content(value: Any) -> None:
    for item in _all_strings(value):
        lowered = item.casefold()
        if any(marker in lowered for marker in _INJECTION_MARKERS):
            raise GemmaRouteError("C35_PROMPT_INJECTION", "untrusted output contains an active instruction marker")


def _scan_for_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered in _TOOL_KEYS:
                raise GemmaRouteError("C35_TOOL_CALL_REJECTED", "tool calls are not accepted on C35 routes")
            if lowered in _REASONING_KEYS:
                raise GemmaRouteError("C35_REASONING_REJECTED", "reasoning payloads are not accepted or retained")
            _scan_for_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _scan_for_forbidden_keys(child)


def _decode_recorded_response(
    recorded_response: Mapping[str, Any] | bytes | str,
) -> tuple[dict[str, Any], str | None]:
    """Decode one direct output or Ollama response and return its model tag."""

    if isinstance(recorded_response, (bytes, str)):
        value = _strict_json(recorded_response, label="recorded response")
    else:
        value = recorded_response
    envelope = _as_mapping(value, "recorded response")
    _scan_for_forbidden_keys(envelope)
    if "message" not in envelope:
        output = dict(envelope)
        _reject_untrusted_content(output)
        return output, None
    unexpected = set(envelope) - _OLLAMA_RESPONSE_KEYS
    if unexpected:
        raise GemmaRouteError(
            "C35_RESPONSE_UNEXPECTED_FIELD",
            f"recorded Ollama response contains unexpected fields: {sorted(unexpected)}",
        )
    message = _as_mapping(envelope.get("message"), "recorded response message")
    unexpected_message = set(message) - _OLLAMA_MESSAGE_KEYS
    if unexpected_message:
        raise GemmaRouteError(
            "C35_RESPONSE_UNEXPECTED_FIELD",
            f"recorded Ollama message contains unexpected fields: {sorted(unexpected_message)}",
        )
    if message.get("role") != "assistant":
        raise GemmaRouteError("C35_RESPONSE_ROLE_INVALID", "recorded Ollama message must be an assistant message")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise GemmaRouteError("C35_CONTENT_INVALID", "recorded Ollama assistant content must be non-empty text")
    if len(content.encode("utf-8")) > LIMITS["max_response_bytes"]:
        raise GemmaRouteError("C35_OUTPUT_TOO_LARGE", "recorded assistant content exceeds the response limit")
    output = _strict_json(content, label="recorded assistant content")
    if not isinstance(output, Mapping):
        raise GemmaRouteError("C35_OUTPUT_INVALID", "recorded assistant content must contain one JSON object")
    result = dict(output)
    _scan_for_forbidden_keys(result)
    _reject_untrusted_content(result)
    model = envelope.get("model")
    if model is not None and not isinstance(model, str):
        raise GemmaRouteError("C35_MODEL_INVALID", "recorded Ollama model identity must be text")
    return result, model


def parse_recorded_response(recorded_response: Mapping[str, Any] | bytes | str) -> dict[str, Any]:
    """Decode one direct output or Ollama chat response without retaining raw bytes."""

    output, _ = _decode_recorded_response(recorded_response)
    return output


def _source_records(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = _as_list(context.get("source_hashes"), "source_hashes")
    if not values:
        raise GemmaRouteError("C35_CONTEXT_INVALID", "source_hashes must contain one source")
    return [_as_mapping(value, "source hash") for value in values]


def _candidate_records(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = _as_list(context.get("frozen_candidates"), "frozen_candidates")
    if not values:
        raise GemmaRouteError("C35_CONTEXT_INVALID", "frozen_candidates must contain one candidate")
    return [_as_mapping(value, "frozen candidate") for value in values]


def _source_for_path(context: Mapping[str, Any], path: object) -> Mapping[str, Any]:
    for source in _source_records(context):
        if source.get("path") == path:
            return source
    raise GemmaRouteError("C35_SOURCE_DRIFT", "model output references a source outside the frozen source set")


def _verify_answer_semantics(context: Mapping[str, Any], output: Mapping[str, Any]) -> None:
    citations = _as_list(output.get("citations"), "answer citations")
    candidates = _candidate_records(context)
    for raw_citation in citations:
        citation = _as_mapping(raw_citation, "answer citation")
        matches = [
            candidate
            for candidate in candidates
            if candidate.get("note_id") == citation.get("note_id")
            and candidate.get("path") == citation.get("path")
            and candidate.get("content_hash") == citation.get("content_hash")
            and candidate.get("chunk_id") == citation.get("chunk_id")
            and candidate.get("chunk_hash") == citation.get("chunk_hash")
        ]
        if not matches:
            raise GemmaRouteError("C35_CITATION_DRIFT", "citation is not bound to a frozen candidate")
        candidate = matches[0]
        if citation.get("locator") != candidate.get("chunk_locator"):
            raise GemmaRouteError("C35_CITATION_DRIFT", "citation locator differs from frozen chunk locator")
        if citation.get("index_generation_id") != context.get("index_generation_id"):
            raise GemmaRouteError("C35_CITATION_DRIFT", "citation generation differs from frozen context")
        excerpt = citation.get("excerpt")
        if excerpt != candidate.get("excerpt"):
            raise GemmaRouteError("C35_CITATION_DRIFT", "displayed citation excerpt differs from frozen bytes")
        if citation.get("excerpt_sha256") != _sha256(str(excerpt).encode("utf-8")):
            raise GemmaRouteError("C35_CITATION_DRIFT", "displayed citation excerpt digest is invalid")
        if citation.get("sha256") != citation.get("chunk_hash"):
            raise GemmaRouteError("C35_CITATION_DRIFT", "citation sha256 must equal the displayed chunk hash")
    if output.get("answer_sha256") != _digest_json(_without_digest(output, "answer_sha256")):
        raise GemmaRouteError("C35_OUTPUT_DIGEST_INVALID", "answer_sha256 does not match the validated answer")


def _verify_triage_semantics(context: Mapping[str, Any], output: Mapping[str, Any]) -> None:
    proposal = _as_mapping(output.get("proposal"), "triage proposal")
    source = _source_for_path(context, proposal.get("source"))
    if proposal.get("input_sha256") != source.get("content_hash"):
        raise GemmaRouteError("C35_SOURCE_DRIFT", "triage input hash differs from the frozen source")
    for raw_candidate in _as_list(proposal.get("candidates"), "triage candidates"):
        candidate = _as_mapping(raw_candidate, "triage candidate")
        if candidate.get("source_sha256") != source.get("content_hash"):
            raise GemmaRouteError("C35_SOURCE_DRIFT", "triage candidate source hash differs from the frozen source")


def _file_binding(workspace: Path, relative: str) -> dict[str, str]:
    path = workspace / relative
    if path.is_symlink() or not path.is_file():
        raise GemmaRouteError("C35_BINDING_UNAVAILABLE", f"required binding is unavailable: {relative}")
    return {"path": relative, "sha256": _sha256(path.read_bytes())}


def _verify_proposal_semantics(workspace: Path, context: Mapping[str, Any], output: Mapping[str, Any], route: GemmaRoute) -> None:
    source = _source_for_path(context, _as_mapping(output.get("source"), "proposal source").get("path"))
    output_source = _as_mapping(output["source"], "proposal source")
    if output_source.get("sha256") != source.get("content_hash"):
        raise GemmaRouteError("C35_SOURCE_DRIFT", "proposal source hash differs from the frozen source")
    for field in ("note_id", "locator", "fragment_sha256", "fragment_byte_length"):
        if field in source and output_source.get(field) != source.get(field):
            raise GemmaRouteError("C35_SOURCE_DRIFT", f"proposal source {field} differs from frozen evidence")
    bindings = _as_mapping(output.get("bindings"), "proposal bindings")
    expected_bindings = {
        "action_config": _file_binding(workspace, _ACTION_FILES[route.action]),
        "prompt": _file_binding(workspace, _PROMPT_FILES[route.action]),
        "schema": _file_binding(workspace, route.schema_path),
    }
    for field, expected in expected_bindings.items():
        actual = _as_mapping(bindings.get(field), f"proposal binding {field}")
        if dict(actual) != expected:
            raise GemmaRouteError("C35_BINDING_DRIFT", f"proposal {field} binding differs from the current contract")
    policy_binding = _as_mapping(bindings.get("policy"), "proposal policy binding")
    expected_policy = _file_binding(workspace, "ops/policies/privacy.yaml")
    if policy_binding.get("path") != expected_policy["path"] or policy_binding.get("sha256") != expected_policy["sha256"]:
        raise GemmaRouteError("C35_BINDING_DRIFT", "proposal privacy policy binding differs from the current contract")
    if policy_binding.get("decision") != _as_mapping(context["policy_decision"], "policy decision").get("decision"):
        raise GemmaRouteError("C35_POLICY_DRIFT", "proposal policy decision differs from frozen context")
    if bindings.get("candidate_set_sha256") != context.get("candidate_set_sha256"):
        raise GemmaRouteError("C35_CANDIDATE_DRIFT", "proposal candidate set differs from frozen context")
    if bindings.get("index_generation_id") != context.get("index_generation_id"):
        raise GemmaRouteError("C35_CANDIDATE_DRIFT", "proposal index generation differs from frozen context")
    retrieval_binding = _file_binding(workspace, "ops/policies/retrieval.yaml")
    if bindings.get("retrieval_config_sha256") != retrieval_binding["sha256"]:
        raise GemmaRouteError("C35_BINDING_DRIFT", "proposal retrieval policy binding differs from the current contract")

    target = _as_mapping(output.get("target"), "proposal target")
    mutations = _as_list(output.get("requested_mutations"), "proposal requested mutations")
    if len(mutations) != 1 or _as_mapping(mutations[0], "proposal mutation").get("path") != target.get("path"):
        raise GemmaRouteError("C35_PROPOSAL_INVALID", "proposal mutation does not bind to its target")
    if route.action in {"link_suggestions", "normalize"}:
        if target.get("path") != source.get("path") or target.get("expected_sha256") != source.get("content_hash"):
            raise GemmaRouteError("C35_SOURCE_DRIFT", "proposal update target is not the frozen source")
    elif route.action == "draft_note":
        candidate = _as_mapping(output.get("candidate"), "draft candidate")
        if candidate.get("source_sha256") != source.get("content_hash"):
            raise GemmaRouteError("C35_SOURCE_DRIFT", "draft candidate source hash differs from the frozen source")
    if route.action == "link_suggestions":
        candidates = {(item.get("note_id"), item.get("path"), item.get("content_hash")) for item in _candidate_records(context)}
        for raw in _as_list(output.get("candidate_set"), "link candidate set"):
            item = _as_mapping(raw, "link candidate")
            if (item.get("note_id"), item.get("path"), item.get("content_hash")) not in candidates:
                raise GemmaRouteError("C35_CANDIDATE_DRIFT", "link proposal contains a candidate outside frozen evidence")
        for raw in _as_list(output.get("suggested_links"), "suggested links"):
            item = _as_mapping(raw, "suggested link")
            if (item.get("note_id"), item.get("path"), item.get("content_hash")) not in candidates:
                raise GemmaRouteError("C35_CANDIDATE_DRIFT", "link suggestion contains a candidate outside frozen evidence")
    diff = _as_mapping(output.get("diff"), "proposal diff")
    if diff.get("sha256") != _sha256(str(diff.get("patch", "")).encode("utf-8")):
        raise GemmaRouteError("C35_OUTPUT_DIGEST_INVALID", "proposal diff sha256 does not match its patch")


def validate_gemma_output(
    root: str | Path,
    context: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    route: str | None = None,
) -> dict[str, Any]:
    """Independently validate one recorded C35 output against frozen context."""

    workspace = _workspace(root)
    value = _as_mapping(output, "Gemma output")
    selected = _resolve_route(str(context.get("action")), route)
    _validate_gemma_provider(context)
    schema, schema_report = _schema_for_route(workspace, context, selected)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.path),
    )
    if errors:
        error = errors[0]
        locator = "/".join(str(part) for part in error.path) or "/"
        raise GemmaRouteError("C35_OUTPUT_SCHEMA_INVALID", f"output invalid at {locator}: {error.message}")
    _scan_for_forbidden_keys(value)
    _reject_untrusted_content(value)
    if selected.action == "answer":
        _verify_answer_semantics(context, value)
    elif selected.action == "triage":
        _verify_triage_semantics(context, value)
    else:
        _verify_proposal_semantics(workspace, context, value, selected)
    return {
        **schema_report,
        "output_sha256": _digest_json(value),
        "route": selected.name,
        "artifact_kind": selected.artifact_kind,
    }


def _safe_recorded_path(workspace: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(workspace)
        except (OSError, ValueError) as error:
            raise GemmaRouteError("C35_RESPONSE_PATH_INVALID", "recorded response must be inside the control root") from error
    if not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise GemmaRouteError("C35_RESPONSE_PATH_INVALID", "recorded response path is unsafe")
    path = workspace.joinpath(*candidate.parts)
    current = workspace
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise GemmaRouteError("C35_RESPONSE_PATH_INVALID", "recorded response path traverses a symlink")
    if not path.is_file():
        raise GemmaRouteError("C35_RESPONSE_PATH_INVALID", "recorded response must be a regular file")
    return path


def _load_recorded_response(
    workspace: Path,
    recorded_response: Mapping[str, Any] | bytes | str | None,
    recorded_response_path: str | Path | None,
) -> tuple[dict[str, Any], str | None]:
    if recorded_response is not None and recorded_response_path is not None:
        raise GemmaRouteError("C35_INPUT_INVALID", "supply recorded_response or recorded_response_path, not both")
    if recorded_response_path is not None:
        path = _safe_recorded_path(workspace, recorded_response_path)
        raw = path.read_bytes()
        if len(raw) > LIMITS["max_response_bytes"]:
            raise GemmaRouteError("C35_OUTPUT_TOO_LARGE", "recorded response exceeds the response limit")
        return _decode_recorded_response(raw)
    if recorded_response is None:
        raise GemmaRouteError("C35_RESPONSE_REQUIRED", "C35 requires one recorded response")
    return _decode_recorded_response(recorded_response)


def _verify_recorded_model(context: Mapping[str, Any], recorded_model: str | None) -> None:
    if recorded_model is None:
        return
    provider = _as_mapping(context.get("provider"), "provider")
    if recorded_model != provider.get("model_tag"):
        raise GemmaRouteError("C35_MODEL_DRIFT", "recorded response model differs from the frozen provider identity")


def _read_replay(workspace: Path, request: Mapping[str, Any], context: Mapping[str, Any], route: GemmaRoute) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    job = _job_directory(workspace, str(request["job_id"]))
    response, response_raw = _read_private_json(job / RESPONSE_FILENAME, label="C35 provider response")
    _validate_envelope(response, "response")
    _verify_digest(response, "response_sha256", "C35_RESPONSE_DIGEST_DRIFT")
    if response["job_id"] != request["job_id"] or response["request_sha256"] != request["request_sha256"] or response["context_sha256"] != request["context_sha256"] or response["provider"] != request["provider"] or response["output_schema"] != request["output_schema"]:
        raise GemmaRouteError("C35_DIGEST_CONFLICT", "replayed C35 response is bound to a different request")
    if response["output"] is None:
        raise GemmaRouteError("C35_OUTPUT_INVALID", "completed C35 replay must contain output")
    validate_gemma_output(workspace, context, _as_mapping(response["output"], "replayed C35 output"), route=route.name)
    receipt_path = job / RECEIPT_FILENAME
    receipt, receipt_raw = _read_private_json(receipt_path, label="C35 receipt")
    _validate_envelope(receipt, "receipt")
    _verify_digest(receipt, "receipt_sha256", "C35_RECEIPT_DIGEST_DRIFT")
    if receipt["job_id"] != request["job_id"] or receipt["request_sha256"] != request["request_sha256"] or receipt["response_sha256"] != response["response_sha256"]:
        raise GemmaRouteError("C35_DIGEST_CONFLICT", "replayed C35 receipt is bound to a different response")
    return response, response_raw, receipt, receipt_raw


def _report(
    *,
    status: str,
    request: Mapping[str, Any] | None = None,
    route: GemmaRoute | None = None,
    error: GemmaRouteError | None = None,
    **extra: Any,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": status,
        "operation": OPERATION,
        "capability": CAPABILITY,
        "route": route.name if route else None,
        "provider_called": False,
        "recorded_provider_called": False,
        "live_provider_called": False,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
    }
    if request is not None:
        report["job_id"] = request.get("job_id")
        report["request_sha256"] = request.get("request_sha256")
        report["context_sha256"] = request.get("context_sha256")
    if error is not None:
        report["errors"] = [{"code": error.code, "message": str(error)}]
    report.update(extra)
    return report


def run_gemma_job(
    root: str | Path,
    *,
    job_id: str,
    recorded_response: Mapping[str, Any] | bytes | str | None = None,
    recorded_response_path: str | Path | None = None,
    route: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Run one C35 route from a recorded response without live provider access."""

    workspace: Path | None = None
    request: dict[str, Any] | None = None
    selected: GemmaRoute | None = None
    try:
        workspace = _workspace(root)
        request, context = _read_request_and_context(workspace, job_id)
        selected = _resolve_route(str(request["action"]), route)
        _validate_gemma_provider(context)
        job = _job_directory(workspace, job_id)
        response_path = job / RESPONSE_FILENAME
        if response_path.exists() or response_path.is_symlink():
            existing, response_raw, receipt, receipt_raw = _read_replay(workspace, request, context, selected)
            if recorded_response is not None or recorded_response_path is not None:
                candidate_output, recorded_model = _load_recorded_response(
                    workspace,
                    recorded_response,
                    recorded_response_path,
                )
                _verify_recorded_model(context, recorded_model)
                candidate_report = validate_gemma_output(workspace, context, candidate_output, route=selected.name)
                if candidate_report["output_sha256"] != existing["output_sha256"]:
                    raise GemmaRouteError("C35_DIGEST_CONFLICT", "recorded replay output differs from the immutable C35 response")
            return _report(
                status="NO_OP",
                request=request,
                route=selected,
                replayed=True,
                response_path=response_path.relative_to(workspace).as_posix(),
                response_sha256=_sha256(response_raw),
                receipt_path=(job / RECEIPT_FILENAME).relative_to(workspace).as_posix(),
                receipt_sha256=_sha256(receipt_raw),
                output=existing["output"],
                output_sha256=existing["output_sha256"],
                output_outcome="answer" if selected.artifact_kind == "answer" else "proposed",
                artifact_kind=selected.artifact_kind,
                human_approval_required=selected.human_approval_required,
            ), EXIT_OK

        output, recorded_model = _load_recorded_response(workspace, recorded_response, recorded_response_path)
        _verify_recorded_model(context, recorded_model)
        schema_report = validate_gemma_output(workspace, context, output, route=selected.name)
        response = build_provider_response(
            request,
            status="completed",
            output=output,
            received_at=str(request["created_at"]),
            completed_at=str(request["created_at"]),
            response_id=f"c35:{request['request_sha256']}:{selected.name}:{schema_report['output_sha256']}",
        )
        _verify_digest(response, "response_sha256", "C35_RESPONSE_DIGEST_DRIFT")
        receipt = build_identity_receipt(
            request,
            response,
            provider_called=False,
            created_at=str(request["created_at"]),
            receipt_id=f"c35:{response['response_sha256']}",
        )
        _verify_digest(receipt, "receipt_sha256", "C35_RECEIPT_DIGEST_DRIFT")
        response_state, response_digest, response_bytes = _write_private_json(response_path, response)
        receipt_path = job / RECEIPT_FILENAME
        receipt_state, receipt_digest, receipt_bytes = _write_private_json(receipt_path, receipt)
        return _report(
            status="PASS",
            request=request,
            route=selected,
            recorded_response_used=True,
            response_path=response_path.relative_to(workspace).as_posix(),
            response_sha256=response_digest,
            response_byte_length=response_bytes,
            response_write=response_state,
            receipt_path=receipt_path.relative_to(workspace).as_posix(),
            receipt_sha256=receipt_digest,
            receipt_byte_length=receipt_bytes,
            receipt_write=receipt_state,
            output=output,
            output_sha256=schema_report["output_sha256"],
            output_schema=schema_report,
            output_outcome="answer" if selected.artifact_kind == "answer" else "proposed",
            artifact_kind=selected.artifact_kind,
            human_approval_required=selected.human_approval_required,
        ), EXIT_OK
    except ProviderConflict as error:
        wrapped = GemmaRouteError(error.code, str(error))
        return _report(status="CONFLICT", request=request, route=selected, error=wrapped), EXIT_CONFLICT
    except GemmaRouteError as error:
        conflict_codes = {
            "C35_DIGEST_CONFLICT",
            "C35_RESPONSE_DIGEST_DRIFT",
            "C35_RECEIPT_DIGEST_DRIFT",
            "C35_CONTEXT_DRIFT",
            "C35_OUTPUT_SCHEMA_DRIFT",
            "C35_SOURCE_DRIFT",
            "C35_MODEL_DRIFT",
            "C35_CITATION_DRIFT",
            "C35_CANDIDATE_DRIFT",
            "C35_BINDING_DRIFT",
            "C35_POLICY_DRIFT",
        }
        status = "CONFLICT" if error.code in conflict_codes else "FAIL"
        return _report(status=status, request=request, route=selected, error=error), EXIT_CONFLICT if status == "CONFLICT" else EXIT_INPUT_INVALID
    except (OSError, TypeError, ValueError) as error:
        wrapped = GemmaRouteError("C35_INPUT_INVALID", str(error))
        return _report(status="FAIL", request=request, route=selected, error=wrapped), EXIT_INPUT_INVALID


__all__ = [
    "C35_ROUTES",
    "CAPABILITY",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "GEMMA_MODEL_TAG",
    "GemmaRoute",
    "GemmaRouteError",
    "parse_recorded_response",
    "run_gemma_job",
    "validate_gemma_output",
]
