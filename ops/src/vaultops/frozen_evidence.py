"""Validation of frozen live-provider evidence used by the E05 C19 bridge.

The live provider runner deliberately has no Vault mount.  This module gives
the interactive review boundary a narrow, explicit way to consume one
completed triage run without pretending that its source bytes are canonical
Vault bytes.  The only accepted runtime sources are the immutable C31 context,
response, and provider-receipt files for one UUIDv4 job.  The response is
revalidated through C35 and the promotion manifest is checked against the
same frozen candidate before C19 can review or apply it.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .gemma_routes import GemmaRouteError, validate_gemma_output
from .provider_contract import (
    ProviderContractError,
    canonical_json_bytes,
    provider_schema,
    validate_context_envelope,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_RUNTIME_SOURCE = re.compile(
    r"^runtime/runs/(?P<job>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"(?P<name>context\.json|response\.json|receipts/provider-receipt\.json)$"
)
_SOURCE_BINDING = re.compile(r"^(.+)\|sha256:([0-9a-f]{64})$")
_REQUIRED_RUNTIME_NAMES = (
    "context.json",
    "response.json",
    "receipts/provider-receipt.json",
)
_ALLOWED_TARGET_TYPES = {"idea"}
_MAX_JSON_BYTES = 2 * 1024 * 1024


class FrozenEvidenceError(ValueError):
    """A fail-closed frozen evidence or promotion-manifest error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FrozenTriageEvidence:
    """Validated C31/C35 data for one selected triage candidate."""

    job_id: str
    context: dict[str, Any]
    request: dict[str, Any]
    response: dict[str, Any]
    receipt: dict[str, Any]
    output: dict[str, Any]
    proposal: dict[str, Any]
    selected_candidate: dict[str, Any]
    frozen_candidate: dict[str, Any]
    source: dict[str, Any]
    source_bindings: tuple[dict[str, str], ...]
    target_type: str
    target_title: str
    target_path: str


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise FrozenEvidenceError("E05_ROOT_INVALID", "control root must be an existing non-symlink directory")
    return candidate.resolve()


def _error(code: str, message: str) -> FrozenEvidenceError:
    return FrozenEvidenceError(code, message)


def _canonical_private_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise _error("E05_RUNTIME_FILE_INVALID", f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise _error("E05_RUNTIME_MODE_INVALID", f"{label} must use mode 0600")
    raw = path.read_bytes()
    if len(raw) > _MAX_JSON_BYTES:
        raise _error("E05_RUNTIME_FILE_TOO_LARGE", f"{label} exceeds the bounded runtime size")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise _error("E05_RUNTIME_JSON_INVALID", f"{label} is not valid JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise _error("E05_RUNTIME_SERIALIZATION_INVALID", f"{label} is not canonical JSON")
    return value, raw


def _validate_schema(value: Mapping[str, Any], kind: str) -> None:
    errors = sorted(
        Draft202012Validator(provider_schema(kind), format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.path),
    )
    if errors:
        error = errors[0]
        locator = "/".join(str(part) for part in error.path) or "/"
        raise _error("E05_RUNTIME_SCHEMA_INVALID", f"{kind} envelope invalid at {locator}: {error.message}")


def _verify_digest(value: Mapping[str, Any], field: str) -> None:
    expected = _sha256(canonical_json_bytes({key: item for key, item in value.items() if key != field}))
    if value.get(field) != expected:
        raise _error("E05_RUNTIME_DIGEST_INVALID", f"{field} does not match canonical envelope bytes")


def _binding_map(bindings: Sequence[str]) -> tuple[str, dict[str, str]]:
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)) or not bindings:
        raise _error("E05_RUNTIME_BINDING_INVALID", "frozen runtime source bindings must be a non-empty list")
    parsed: dict[str, str] = {}
    job_id: str | None = None
    for item in bindings:
        if not isinstance(item, str):
            raise _error("E05_RUNTIME_BINDING_INVALID", "frozen runtime source bindings must be text")
        match = _SOURCE_BINDING.fullmatch(item)
        if match is None:
            raise _error("E05_RUNTIME_BINDING_INVALID", "frozen runtime source binding is malformed")
        relative, expected = match.groups()
        runtime_match = _RUNTIME_SOURCE.fullmatch(relative)
        if runtime_match is None:
            raise _error("E05_RUNTIME_BINDING_INVALID", "only context, response, and provider-receipt runtime files may be bound")
        current_job = runtime_match.group("job")
        if job_id is None:
            job_id = current_job
        elif job_id != current_job:
            raise _error("E05_RUNTIME_BINDING_INVALID", "runtime source bindings must belong to one job")
        if relative in parsed and parsed[relative] != expected:
            raise _error("E05_RUNTIME_BINDING_INVALID", "runtime source binding is duplicated with a different digest")
        parsed[relative] = expected
    if job_id is None or {path.rsplit(f"{job_id}/", 1)[-1] for path in parsed} != set(_REQUIRED_RUNTIME_NAMES):
        raise _error("E05_RUNTIME_BINDING_INVALID", "context, response, and provider receipt bindings are all required")
    return job_id, parsed


def _load_runtime_evidence(
    workspace: Path,
    bindings: Sequence[str],
) -> tuple[str, dict[str, str], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    job_id, expected_hashes = _binding_map(bindings)
    job_root = workspace / "runtime" / "runs" / job_id
    if not _UUID4.fullmatch(job_id) or job_root.is_symlink() or not job_root.is_dir():
        raise _error("E05_RUNTIME_JOB_INVALID", "frozen runtime job directory is unavailable")
    receipt_root = job_root / "receipts"
    if receipt_root.is_symlink() or not receipt_root.is_dir():
        raise _error("E05_RUNTIME_JOB_INVALID", "frozen provider receipt directory is unavailable")
    paths = {
        "context.json": job_root / "context.json",
        "response.json": job_root / "response.json",
        "receipts/provider-receipt.json": receipt_root / "provider-receipt.json",
    }
    loaded: dict[str, dict[str, Any]] = {}
    raws: dict[str, bytes] = {}
    for name, path in paths.items():
        value, raw = _canonical_private_json(path, label=f"runtime/{name}")
        relative = f"runtime/runs/{job_id}/{name}"
        observed = _sha256(raw)
        if expected_hashes[relative] != observed:
            raise _error("E05_RUNTIME_SOURCE_DRIFT", f"runtime source digest differs: {relative}")
        loaded[name] = value
        raws[name] = raw

    context = loaded["context.json"]
    request_path = job_root / "request.json"
    request, _request_raw = _canonical_private_json(request_path, label="runtime/request.json")
    try:
        context = validate_context_envelope(context)
    except ProviderContractError as error:
        raise _error("E05_RUNTIME_CONTEXT_INVALID", str(error)) from error
    _validate_schema(request, "request")
    _verify_digest(request, "request_sha256")
    response = loaded["response.json"]
    receipt = loaded["receipts/provider-receipt.json"]
    _validate_schema(response, "response")
    _validate_schema(receipt, "receipt")
    _verify_digest(response, "response_sha256")
    _verify_digest(receipt, "receipt_sha256")

    if request["job_id"] != job_id or context["job_id"] != job_id:
        raise _error("E05_RUNTIME_JOB_DRIFT", "request and context job ids differ from the bound runtime job")
    if context["action"] != "triage" or request["action"] != "triage":
        raise _error("E05_RUNTIME_ACTION_INVALID", "only one completed triage run may be promoted")
    if request["context_sha256"] != context["context_sha256"]:
        raise _error("E05_RUNTIME_CONTEXT_DRIFT", "request is bound to a different context")
    if response["job_id"] != job_id or response["request_sha256"] != request["request_sha256"] or response["context_sha256"] != context["context_sha256"]:
        raise _error("E05_RUNTIME_RESPONSE_DRIFT", "response is not bound to the request and context")
    if response["provider"] != request["provider"] or response["output_schema"] != request["output_schema"]:
        raise _error("E05_RUNTIME_RESPONSE_DRIFT", "response provider or output schema differs from the request")
    if response["status"] != "completed" or not isinstance(response.get("output"), dict):
        raise _error("E05_RUNTIME_RESPONSE_INVALID", "only a completed response with one object output may be promoted")
    expected_output = _sha256(canonical_json_bytes(response["output"]))
    if response["output_sha256"] != expected_output:
        raise _error("E05_RUNTIME_OUTPUT_DRIFT", "response output digest does not match its output")
    if receipt["job_id"] != job_id or receipt["request_sha256"] != request["request_sha256"] or receipt["context_sha256"] != context["context_sha256"] or receipt["response_sha256"] != response["response_sha256"] or receipt["provider"] != request["provider"]:
        raise _error("E05_RUNTIME_RECEIPT_DRIFT", "provider receipt is not bound to the response")
    if not receipt["provider_called"] or receipt["vault_mutation_performed"] or receipt["canonical_apply_allowed"]:
        raise _error("E05_RUNTIME_BOUNDARY_INVALID", "provider receipt does not prove the proposal-only boundary")
    provider = context["provider"]
    if provider.get("route") != "local:gemma4" or provider.get("model_tag") != "gemma4:12b":
        raise _error("E05_RUNTIME_PROVIDER_INVALID", "frozen evidence is not the authorized local Gemma route")
    boundary = response["runner_boundary"]
    if boundary.get("vault_mount") or boundary.get("canonical_apply"):
        raise _error("E05_RUNTIME_BOUNDARY_INVALID", "provider response claims Vault or canonical-apply authority")

    output = response["output"]
    try:
        validate_gemma_output(workspace, context, output, route="triage")
    except GemmaRouteError as error:
        raise _error("E05_RUNTIME_C35_INVALID", str(error)) from error
    return job_id, expected_hashes, context, request, response, receipt, output


def _candidate_and_target(
    context: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    candidate_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    if not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 128:
        raise _error("E05_CANDIDATE_INVALID", "candidate id is required and bounded")
    proposal = output.get("proposal")
    if not isinstance(proposal, Mapping):
        raise _error("E05_PROPOSAL_INVALID", "triage output has no proposal object")
    provider_candidates = proposal.get("candidates")
    if not isinstance(provider_candidates, list):
        raise _error("E05_CANDIDATE_INVALID", "triage proposal candidates are missing")
    selected_values = [item for item in provider_candidates if isinstance(item, Mapping) and item.get("candidate_id") == candidate_id]
    if len(selected_values) != 1:
        raise _error("E05_CANDIDATE_INVALID", "candidate id does not select exactly one provider candidate")
    selected = dict(selected_values[0])
    frozen_values = context.get("frozen_candidates")
    if not isinstance(frozen_values, list):
        raise _error("E05_CANDIDATE_INVALID", "frozen candidate set is missing")
    frozen_matches = [item for item in frozen_values if isinstance(item, Mapping) and item.get("path") == proposal.get("source")]
    if len(frozen_matches) != 1:
        raise _error("E05_CANDIDATE_INVALID", "triage source does not select exactly one frozen candidate")
    frozen = dict(frozen_matches[0])
    if selected.get("source_sha256") != frozen.get("content_hash") or proposal.get("input_sha256") != frozen.get("content_hash"):
        raise _error("E05_CANDIDATE_DRIFT", "provider candidate is not bound to the frozen source hash")
    target_type = selected.get("type")
    title = selected.get("title")
    if target_type not in _ALLOWED_TARGET_TYPES or not isinstance(title, str):
        raise _error("E05_TARGET_TYPE_UNSUPPORTED", "frozen triage promotion currently allows only an idea candidate")
    title = unicodedata.normalize("NFC", title)
    if (
        not title
        or len(title.encode("utf-8")) > 200
        or title != title.strip()
        or title in {".", ".."}
        or any(character in title for character in ("/", "\\", "\x00", "\r", "\n"))
    ):
        raise _error("E05_TARGET_TITLE_INVALID", "candidate title cannot be a canonical Markdown filename stem")
    target_path = f"40_Knowledge/Ideas/{title}.md"
    return selected, frozen, str(target_type), title, target_path


def _validate_manifest(
    evidence: FrozenTriageEvidence,
    manifest: Mapping[str, Any],
) -> None:
    if manifest.get("action") != "create_note":
        raise _error("E05_MANIFEST_INVALID", "frozen triage promotion must create one note")
    if manifest.get("target_path") != evidence.target_path:
        raise _error("E05_TARGET_DRIFT", "promotion target does not match the selected frozen candidate")
    frozen = manifest.get("frozen_evidence")
    if not isinstance(frozen, Mapping):
        raise _error("E05_MANIFEST_INVALID", "promotion manifest must contain frozen_evidence")
    expected = {
        "job_id": evidence.job_id,
        "candidate_id": evidence.selected_candidate["candidate_id"],
        "candidate_sha256": evidence.frozen_candidate["candidate_sha256"],
        "source_path": evidence.source["path"],
        "source_sha256": evidence.source["content_hash"],
        "target_type": evidence.target_type,
        "title": evidence.target_title,
    }
    if dict(frozen) != expected:
        raise _error("E05_MANIFEST_INVALID", "promotion manifest frozen_evidence does not match the validated run")


def inspect_frozen_triage_proposal(
    root: str | Path,
    *,
    source_bindings: Sequence[str],
    candidate_id: str,
    manifest: Mapping[str, Any] | None = None,
) -> FrozenTriageEvidence:
    """Validate one runtime triage run and, optionally, its C19 manifest."""

    workspace = _workspace(root)
    job_id, hashes, context, request, response, receipt, output = _load_runtime_evidence(workspace, source_bindings)
    proposal = output.get("proposal")
    if not isinstance(proposal, dict):
        raise _error("E05_PROPOSAL_INVALID", "triage output proposal is missing")
    selected, frozen, target_type, target_title, target_path = _candidate_and_target(
        context, output, candidate_id=candidate_id
    )
    source = next(
        (dict(item) for item in context["source_hashes"] if isinstance(item, Mapping) and item.get("path") == proposal.get("source")),
        None,
    )
    if source is None:
        raise _error("E05_SOURCE_INVALID", "triage output source is not in the frozen source set")
    evidence = FrozenTriageEvidence(
        job_id=job_id,
        context=dict(context),
        request=dict(request),
        response=dict(response),
        receipt=dict(receipt),
        output=dict(output),
        proposal=dict(proposal),
        selected_candidate=selected,
        frozen_candidate=frozen,
        source=source,
        source_bindings=tuple(
            {"path": path, "sha256": digest}
            for path, digest in sorted(hashes.items())
        ),
        target_type=target_type,
        target_title=target_title,
        target_path=target_path,
    )
    if manifest is not None:
        _validate_manifest(evidence, manifest)
    return evidence


def runtime_source_bindings(values: Sequence[str]) -> bool:
    """Return whether a proposal source list is exclusively runtime evidence."""

    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        return False
    matches = [_SOURCE_BINDING.fullmatch(item) for item in values if isinstance(item, str)]
    return len(matches) == len(values) and all(match is not None and _RUNTIME_SOURCE.fullmatch(match.group(1)) for match in matches)


def validate_runtime_proposal_sources(
    root: str | Path,
    *,
    source_bindings: Sequence[str],
    candidate_id: str,
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, str]], FrozenTriageEvidence]:
    """Validate C19 runtime source bindings and return its stable baselines."""

    evidence = inspect_frozen_triage_proposal(
        root,
        source_bindings=source_bindings,
        candidate_id=candidate_id,
        manifest=manifest,
    )
    return list(evidence.source_bindings), evidence


__all__ = [
    "FrozenEvidenceError",
    "FrozenTriageEvidence",
    "inspect_frozen_triage_proposal",
    "runtime_source_bindings",
    "validate_runtime_proposal_sources",
]
