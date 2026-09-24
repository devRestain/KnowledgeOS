"""C42 privacy, citation, quality, and bounded-observability gates.

This module is deliberately independent from provider activation.  It
revalidates the privacy boundary immediately before a context is consumed and
again before a route result is presented.  It also provides deterministic
fixture evaluation and an ordinary-log surface that can contain codes,
digests, counters, and bounded timings but never source bodies, prompts,
responses, credentials, or synchronized personal content.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from numbers import Real
from typing import Any

from .provider_contract import canonical_json_bytes

CAPABILITY = "C42"
OPERATION = "ai safety gates"
EXIT_OK = 0
EXIT_INPUT_INVALID = 10
EXIT_CONFLICT = 30

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "system message",
    "developer message",
    "<tool_call",
    "<|tool|>",
    "chain of thought",
    "exfiltrate secrets",
)
_FORBIDDEN_KEYS = {
    "analysis",
    "chain_of_thought",
    "cot",
    "function_call",
    "function_calls",
    "reasoning",
    "thinking",
    "tool",
    "tool_call",
    "tool_calls",
    "tools",
}
_SENSITIVE_LOG_KEY_PARTS = (
    "prompt",
    "response",
    "body",
    "excerpt",
    "selection",
    "credential",
    "password",
    "secret",
    "token",
    "content",
    "message",
    "reason",
)
_REQUIRED_CASES = {
    "abstention",
    "refusal",
    "prompt_injection",
    "stale_citation",
    "wrong_scope",
    "contradiction",
    "citation_faithfulness",
    "quality_regression",
    "redaction",
}
_ELIGIBILITY_BY_PROFILE = {"local": "local_eligible", "remote": "remote_eligible"}


class SafetyGateError(ValueError):
    """Raised when C42 cannot prove a privacy or observability invariant."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise SafetyGateError("C42_HASH_INVALID", f"{label} must be a lowercase SHA-256")
    return value


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SafetyGateError("C42_INPUT_INVALID", f"{label} must be an object")
    return value


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SafetyGateError("C42_INPUT_INVALID", f"{label} must be an array")
    return value


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SafetyGateError("C42_PATH_INVALID", f"{label} is not a safe relative path")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise SafetyGateError("C42_PATH_INVALID", f"{label} is not a safe relative path")
    return value


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            result.extend(_all_strings(key))
            result.extend(_all_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_all_strings(item))
        return result
    return []


def _reject_untrusted_strings(value: Any) -> None:
    lowered = "\n".join(_all_strings(value)).lower()
    if any(marker in lowered for marker in _INJECTION_MARKERS):
        raise SafetyGateError("C42_PROMPT_INJECTION", "untrusted route output contains an injection marker")


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise SafetyGateError("C42_UNTRUSTED_PAYLOAD", f"route output contains forbidden key: {key}")
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)


def _candidate_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("note_id"),
        value.get("path"),
        value.get("content_hash"),
        value.get("chunk_id"),
        value.get("chunk_hash"),
        value.get("chunk_locator"),
        value.get("index_generation_id"),
    )


@dataclass(frozen=True)
class PrivacyGateResult:
    """Revalidated context and bounded privacy counters."""

    context: dict[str, Any]
    profile: str
    eligible_candidate_count: int
    rejected_candidate_count: int
    source_count: int
    context_sha256: str

    def as_report(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "operation": OPERATION,
            "capability": CAPABILITY,
            "profile": self.profile,
            "eligible_candidate_count": self.eligible_candidate_count,
            "rejected_candidate_count": self.rejected_candidate_count,
            "source_count": self.source_count,
            "context_sha256": self.context_sha256,
            "privacy_reapplied": True,
            "raw_source_bodies_retained": False,
        }


def reapply_privacy_gate(context: Mapping[str, Any], *, profile: str) -> PrivacyGateResult:
    """Reapply profile eligibility immediately before context materialization."""

    if profile not in _ELIGIBILITY_BY_PROFILE:
        raise SafetyGateError("C42_PROFILE_INVALID", "profile must be local or remote")
    value = deepcopy(dict(_as_mapping(context, "context")))
    policy = _as_mapping(value.get("policy_decision"), "context.policy_decision")
    expected_decision = "allow_local" if profile == "local" else "allow_remote"
    if policy.get("decision") != expected_decision:
        raise SafetyGateError("C42_POLICY_DENIED", "context policy decision does not match the selected profile")
    candidates = _as_list(value.get("frozen_candidates"), "context.frozen_candidates")
    source_hashes = _as_list(value.get("source_hashes"), "context.source_hashes")
    expected_eligibility = _ELIGIBILITY_BY_PROFILE[profile]
    scope_filter = value.get("scope")
    if scope_filter is not None:
        scope_filter = _as_mapping(scope_filter, "context.scope")
        unknown_scope_fields = set(scope_filter) - {"path_prefix", "include_types", "scope_id"}
        if unknown_scope_fields:
            raise SafetyGateError("C42_SCOPE_INVALID", "context scope contains unknown fields")
        path_prefix = scope_filter.get("path_prefix")
        if path_prefix is not None:
            path_prefix = _safe_path(path_prefix, "context.scope.path_prefix").rstrip("/") + "/"
        include_types = scope_filter.get("include_types", [])
        if not isinstance(include_types, list) or any(not isinstance(item, str) or not item for item in include_types):
            raise SafetyGateError("C42_SCOPE_INVALID", "context scope include_types is invalid")
        scope_id = scope_filter.get("scope_id")
        if scope_id is not None and (not isinstance(scope_id, str) or not scope_id):
            raise SafetyGateError("C42_SCOPE_INVALID", "context scope_id is invalid")
    else:
        path_prefix = None
        include_types = []
        scope_id = None
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for raw in candidates:
        candidate = _as_mapping(raw, "context candidate")
        path = _safe_path(candidate.get("path"), "candidate.path")
        eligibility = candidate.get("eligibility_class")
        sensitivity = candidate.get("sensitivity")
        ai_policy = candidate.get("ai_policy")
        forbidden = sensitivity in {"confidential", "secret"} or ai_policy == "deny"
        if profile == "remote" and ai_policy == "local_only":
            forbidden = True
        if path_prefix is not None and not path.startswith(path_prefix):
            forbidden = True
        if include_types and candidate.get("type") not in include_types:
            forbidden = True
        if scope_id is not None and candidate.get("scope_id") != scope_id:
            forbidden = True
        if eligibility != expected_eligibility or forbidden:
            rejected += 1
            continue
        for field in ("content_hash", "chunk_hash", "excerpt_sha256"):
            _validate_hash(candidate.get(field), f"candidate.{field}")
        excerpt = candidate.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt:
            raise SafetyGateError("C42_CANDIDATE_INVALID", "eligible candidate excerpt is missing")
        if candidate.get("excerpt_sha256") != _sha256(excerpt.encode("utf-8")):
            raise SafetyGateError("C42_CANDIDATE_DRIFT", "eligible candidate excerpt digest differs")
        accepted.append(dict(candidate))
        _ = path
    if not accepted:
        raise SafetyGateError("C42_NO_ELIGIBLE_CONTEXT", "privacy gate rejected every candidate")
    accepted_paths = {candidate["path"] for candidate in accepted}
    for raw_source in source_hashes:
        source = _as_mapping(raw_source, "context source hash")
        source_path = _safe_path(source.get("path"), "source_hash.path")
        _validate_hash(source.get("content_hash"), "source_hash.content_hash")
        if source_path not in accepted_paths:
            raise SafetyGateError("C42_PRIVACY_LEAK", "source hash is not bound to an eligible candidate")
    value["frozen_candidates"] = accepted
    value["source_hashes"] = [
        source for source in source_hashes if source.get("path") in accepted_paths
    ]
    if not value["source_hashes"]:
        raise SafetyGateError("C42_NO_ELIGIBLE_SOURCES", "privacy gate retained no source hashes")
    return PrivacyGateResult(
        context=value,
        profile=profile,
        eligible_candidate_count=len(accepted),
        rejected_candidate_count=rejected,
        source_count=len(value["source_hashes"]),
        context_sha256=_digest_json(value),
    )


def validate_route_safety(
    context: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    profile: str,
) -> dict[str, Any]:
    """Revalidate privacy and exact citations immediately before presentation."""

    gated = reapply_privacy_gate(context, profile=profile)
    value = _as_mapping(output, "route output")
    _reject_forbidden_keys(value)
    _reject_untrusted_strings(value)
    expected_eligibility = _ELIGIBILITY_BY_PROFILE[profile]
    candidates = {
        _candidate_key(candidate): candidate for candidate in gated.context["frozen_candidates"]
    }
    citations = value.get("citations", [])
    if citations is None:
        citations = []
    citations = _as_list(citations, "route citations")
    for raw_citation in citations:
        citation = _as_mapping(raw_citation, "route citation")
        key = _candidate_key(citation)
        candidate = candidates.get(key)
        if candidate is None:
            raise SafetyGateError("C42_CITATION_DRIFT", "route citation is not bound to the gated candidate set")
        if candidate.get("eligibility_class") != expected_eligibility:
            raise SafetyGateError("C42_CITATION_PRIVACY", "route citation eligibility differs from profile")
        if candidate.get("channel") is not None and citation.get("channel") != candidate.get("channel"):
            raise SafetyGateError("C42_CITATION_DRIFT", "route citation channel differs from provenance")
        excerpt = citation.get("excerpt")
        if not isinstance(excerpt, str) or citation.get("excerpt_sha256") != _sha256(excerpt.encode("utf-8")):
            raise SafetyGateError("C42_CITATION_DRIFT", "route citation excerpt digest is invalid")
        if citation.get("excerpt_byte_length") != len(excerpt.encode("utf-8")):
            raise SafetyGateError("C42_CITATION_DRIFT", "route citation excerpt length is invalid")
        if citation.get("sha256") != citation.get("chunk_hash"):
            raise SafetyGateError("C42_CITATION_DRIFT", "route citation hash is not the frozen chunk hash")
    if value.get("index_generation_id") is not None and value.get("index_generation_id") != gated.context.get("index_generation_id"):
        raise SafetyGateError("C42_GENERATION_DRIFT", "route generation differs from gated context")
    output_scope = value.get("scope")
    if output_scope is not None:
        scope = _as_mapping(output_scope, "route scope")
        if scope.get("path") not in {candidate.get("path") for candidate in gated.context["frozen_candidates"]}:
            raise SafetyGateError("C42_WRONG_SCOPE", "route output scope is outside the gated candidate set")
    return {
        "status": "PASS",
        "operation": OPERATION,
        "capability": CAPABILITY,
        "profile": profile,
        "context_sha256": gated.context_sha256,
        "output_sha256": _digest_json(value),
        "citation_count": len(citations),
        "privacy_reapplied": True,
        "citation_revalidated": True,
        "model_output_authoritative": False,
        "canonical_apply_allowed": False,
    }


def redact_log_record(value: Any) -> Any:
    """Redact raw content from an ordinary observability record."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered.endswith("_sha256") or lowered in {"job_id", "request_id", "status", "code", "event"}:
                result[key] = redact_log_record(item)
                continue
            if any(part in lowered for part in _SENSITIVE_LOG_KEY_PARTS):
                result[key] = "<redacted>"
            else:
                result[key] = redact_log_record(item)
        return result
    if isinstance(value, list):
        return [redact_log_record(item) for item in value[:20]]
    if isinstance(value, tuple):
        return [redact_log_record(item) for item in value[:20]]
    return value


def build_observability_record(
    *,
    event: str,
    status: str,
    code: str | None = None,
    job_id: str | None = None,
    request_sha256: str | None = None,
    context_sha256: str | None = None,
    duration_ms: int | None = None,
    queue_depth: int | None = None,
    failure_count: int = 0,
    replay: bool = False,
    no_op: bool = False,
    digest_conflict: bool = False,
    privacy_rejection: bool = False,
    citation_drift: bool = False,
    resource_pressure: bool = False,
) -> dict[str, Any]:
    """Build one bounded, redacted ordinary-log record."""

    if not isinstance(event, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", event):
        raise SafetyGateError("C42_OBSERVABILITY_INVALID", "event is not a bounded identifier")
    if not isinstance(status, str) or not status:
        raise SafetyGateError("C42_OBSERVABILITY_INVALID", "status is required")
    if duration_ms is not None and (not isinstance(duration_ms, int) or not 0 <= duration_ms <= 86_400_000):
        raise SafetyGateError("C42_OBSERVABILITY_INVALID", "duration_ms is out of bounds")
    if queue_depth is not None and (not isinstance(queue_depth, int) or not 0 <= queue_depth <= 100_000):
        raise SafetyGateError("C42_OBSERVABILITY_INVALID", "queue_depth is out of bounds")
    if not isinstance(failure_count, int) or not 0 <= failure_count <= 100_000:
        raise SafetyGateError("C42_OBSERVABILITY_INVALID", "failure_count is out of bounds")
    record: dict[str, Any] = {
        "event": event,
        "status": status,
        "code": code,
        "job_id": job_id,
        "request_sha256": request_sha256,
        "context_sha256": context_sha256,
        "duration_ms": duration_ms,
        "queue_depth": queue_depth,
        "failure_count": failure_count,
        "replay": bool(replay),
        "no_op": bool(no_op),
        "digest_conflict": bool(digest_conflict),
        "privacy_rejection": bool(privacy_rejection),
        "citation_drift": bool(citation_drift),
        "resource_pressure": bool(resource_pressure),
        "raw_prompt": None,
        "raw_response": None,
        "source_body": None,
    }
    redacted = redact_log_record(record)
    if any(value not in {None, "<redacted>"} and isinstance(value, str) and any(marker in value.lower() for marker in _INJECTION_MARKERS) for value in _all_strings(redacted)):
        raise SafetyGateError("C42_OBSERVABILITY_INVALID", "observability record contains untrusted content")
    return redacted


def aggregate_observability(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate only bounded counters from redacted records."""

    counters: Counter[str] = Counter()
    durations: list[int] = []
    max_queue = 0
    for raw in records:
        record = redact_log_record(raw)
        event = record.get("event")
        if isinstance(event, str):
            counters[event] += 1
        if isinstance(record.get("duration_ms"), int):
            durations.append(record["duration_ms"])
        if isinstance(record.get("queue_depth"), int):
            max_queue = max(max_queue, record["queue_depth"])
    return {
        "record_count": len(records),
        "event_counts": dict(sorted(counters.items())),
        "duration_ms_total": sum(durations),
        "duration_ms_max": max(durations, default=0),
        "queue_depth_max": max_queue,
        "raw_content_retained": False,
    }


def evaluate_quality_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the frozen Korean/multilingual safety-quality fixture."""

    value = _as_mapping(fixture, "quality fixture")
    if value.get("schema_version") != 1:
        raise SafetyGateError("C42_FIXTURE_INVALID", "quality fixture schema version is unsupported")
    cases = _as_list(value.get("cases"), "quality fixture cases")
    thresholds = _as_mapping(value.get("thresholds"), "quality fixture thresholds")
    seen: set[str] = set()
    seen_kinds: set[str] = set()
    failures: list[str] = []
    failed_kinds: set[str] = set()
    scores: list[float] = []
    per_kind: Counter[str] = Counter()
    for raw_case in cases:
        case = _as_mapping(raw_case, "quality fixture case")
        allowed = {"id", "kind", "expected", "observed", "score"}
        if set(case) != allowed:
            raise SafetyGateError("C42_FIXTURE_INVALID", "quality case contains raw or unknown fields")
        case_id = case.get("id")
        kind = case.get("kind")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise SafetyGateError("C42_FIXTURE_INVALID", "quality case ids must be unique")
        if kind not in _REQUIRED_CASES:
            raise SafetyGateError("C42_FIXTURE_INVALID", f"quality case kind is unsupported: {kind}")
        seen.add(case_id)
        seen_kinds.add(kind)
        passed = case.get("expected") == case.get("observed")
        per_kind[kind] += 1
        if not passed:
            failures.append(case_id)
            failed_kinds.add(kind)
        score = case.get("score")
        if not isinstance(score, Real) or isinstance(score, bool) or not 0 <= float(score) <= 1:
            raise SafetyGateError("C42_FIXTURE_INVALID", f"quality score is invalid: {case_id}")
        scores.append(float(score))
    missing = sorted(_REQUIRED_CASES - seen_kinds)
    if missing:
        raise SafetyGateError("C42_FIXTURE_INVALID", f"quality fixture is missing cases: {missing}")
    min_pass_rate = thresholds.get("min_pass_rate")
    min_quality_score = thresholds.get("min_quality_score")
    if not isinstance(min_pass_rate, Real) or not isinstance(min_quality_score, Real):
        raise SafetyGateError("C42_FIXTURE_INVALID", "quality thresholds must be numeric")
    pass_rate = (len(cases) - len(failures)) / len(cases) if cases else 0.0
    quality_score = sum(scores) / len(scores) if scores else 0.0
    privacy_gate = not failed_kinds.intersection({"prompt_injection", "wrong_scope", "redaction"})
    citation_gate = not failed_kinds.intersection({"stale_citation", "citation_faithfulness"})
    quality_gate = pass_rate >= float(min_pass_rate) and quality_score >= float(min_quality_score)
    return {
        "status": "PASS" if not failures and privacy_gate and citation_gate and quality_gate else "FAIL",
        "operation": OPERATION,
        "capability": CAPABILITY,
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(failures),
        "failed_case_ids": failures,
        "case_kinds": dict(sorted(per_kind.items())),
        "pass_rate": round(pass_rate, 6),
        "quality_score": round(quality_score, 6),
        "privacy_gate": privacy_gate,
        "citation_gate": citation_gate,
        "quality_gate": quality_gate,
        "observability_gate": "redaction" not in failed_kinds,
        "raw_fixture_content_retained": False,
    }


__all__ = [
    "CAPABILITY",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "OPERATION",
    "PrivacyGateResult",
    "SafetyGateError",
    "aggregate_observability",
    "build_observability_record",
    "evaluate_quality_fixture",
    "reapply_privacy_gate",
    "redact_log_record",
    "validate_route_safety",
]
