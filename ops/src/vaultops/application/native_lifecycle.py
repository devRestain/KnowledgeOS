"""Restart-safe sensors over committed owner evidence, never effect reconciliation.

A failed native Activity and an unknown effect are different observations. Only
SDK-classified markers establish failure/timeout; an identity-only historical
``curation_failure`` does not. Applied owner recovery is a separate source.
Cancellation requires the exact safe owner-applied CANCEL receipts, never task
termination. Routine completion or assessment pass is not unusual success.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular
from ..adapters.paths import resolve_beneath
from ..projection import source_properties
from .curation_control import _control_receipt_key
from .experience import KnowledgeExperience
from .temporal_contract import curation_recovery_target
from .work import WorkContracts, record_digest, work_spec_digest


def _require(condition: bool) -> None:
    if not condition:
        raise AdmissionError("EXPERIENCE_SOURCE_INVALID", "Native lifecycle evidence differs")


def _source_classification(app: Any, reference: dict[str, Any]) -> str:
    _require(reference.get("owner_operation_id") == "knowledgeos")
    classification = reference.get("classification")
    _require(classification in {"public", "internal"})
    path = app.resolve_reference(reference)
    app._mcp_source_policy(path)
    raw = read_regular(resolve_beneath(app.roots.vault, path))
    _require(raw is not None)
    properties = source_properties(path, raw)
    # Learning leaves this owner through InterOps, even for a local operator.
    _require(properties.get("ai_policy") not in {"deny", "local_only"}
             and properties.get("sensitivity") != "confidential")
    return classification


def _work(app: Any, key: str, record: dict[str, Any]) -> tuple[dict, dict, str]:
    spec, run = record["spec"], record["run"]
    contracts = WorkContracts(app.core)
    contracts.record("WorkSpec", spec)
    contracts.record("WorkRun", run)
    _require(key == "exops.work." + run["work_run_id"]
             and spec["spec_digest"] == work_spec_digest(spec)
             and run["work_spec_digest"] == spec["spec_digest"]
             and run["work_id"] == spec["work_id"]
             and run["work_spec_revision"] == spec["work_spec_revision"])
    source = record["method_source_reference"]
    return spec, run, _source_classification(app, source)


def _target(record: dict, target: dict) -> None:
    run, spec = record["run"], record["spec"]
    curation_recovery_target({"recovery_id": "sensor.validation", "target": target})
    _require(target.get("owner_operation_id") == "knowledgeos"
             and target.get("work_run_id") == run["work_run_id"]
             and target.get("action_id") == record["action_id"]
             and target.get("content_digest") == record["content_digest"]
             and target.get("work_spec_digest") == spec["spec_digest"]
             and target.get("generation") == run["generation"]
             and target.get("fencing_token") == run["fencing_token"]
             and type(target.get("run_revision")) is int
             and record.get("curation_failure") == {
                 "temporal_run_id": target.get("temporal_run_id"),
                 "activity_id": target.get("activity_id"),
             }
             and all(isinstance(target.get(key), str) and target[key]
                     for key in ("temporal_run_id", "activity_id")))


def _identity(app: Any, category: str, target: dict) -> str:
    # Logical failed attempt identity survives owner/Worker reconstruction and
    # later completed-run revisions; a recovery request ID is not a new attempt.
    token = digest(canonical({"instance_id": app.journal.identity["instance_id"],
                              "category": category, "target": target}))[7:39]
    return "event.native." + token


def _reference(payload: dict, classification: str, revision: int) -> dict:
    raw = canonical(payload)
    token = digest(raw)[7:39]
    # The owner journal is not an artifact_read endpoint. Do not promise an
    # export or disclose raw native/model/actor evidence through a fake pointer.
    return {
        "artifact_id": "artifact.lifecycle." + token,
        "owner_operation_id": "knowledgeos",
        "source_revision": f"revision.r{revision}",
        "content_digest": digest(raw), "media_type": "application/json",
        "size_bytes": len(raw), "classification": classification,
        "availability": "unavailable",
    }


def _failure_source(app: Any, record: dict, classification: str) -> dict | None:
    marker = record.get("curation_native_failure")
    if marker is None:
        return None
    target = marker["target"]
    _target(record, target)
    frozen = marker["run"]
    WorkContracts(app.core).record("WorkRun", frozen)
    _require(marker["kind"] in {"failure", "timeout"}
             and marker["source_reference"] == record["method_source_reference"]
             and frozen["work_run_id"] == target["work_run_id"]
             and frozen["work_spec_digest"] == target["work_spec_digest"]
             and frozen["revision"] == target["run_revision"]
             and frozen["generation"] == target["generation"]
             and frozen["fencing_token"] == target["fencing_token"])
    return {
        "event_id": _identity(app, marker["kind"], target),
        "category": marker["kind"],
        "outcome_state": "timed_out" if marker["kind"] == "timeout" else "failed",
        "classification": classification,
        "evidence_references": (_reference(marker, classification, target["run_revision"]),),
    }


def _recovery_source(app: Any, record: dict, recovery: dict,
                     snapshot: dict, classification: str) -> dict | None:
    if recovery.get("state") != "applied":
        return None
    target, run = recovery["target"], record["run"]
    _target(record, target)
    execution = snapshot["receipts"].get("exops.work." + record["action_id"], {}).get("receipt", {})
    WorkContracts(app.core).record("ExecutionReceipt", execution)
    _require(recovery["spec"] == record["spec"]
             and recovery["source_reference"] == record["method_source_reference"]
             and recovery["actor_id"] == record["actor_id"]
             and record["application_state"] == "recorded"
             and run["state"] == "completed"
             and run["revision"] == target["run_revision"] + 1
             and recovery["applied_receipt"] == record["receipt"]
             and record_digest(recovery["evidence"]) == recovery["evidence_digest"]
             and recovery["completed_run_digest"] == record_digest(run)
             and execution.get("work_run_id") == run["work_run_id"]
             and execution.get("content_digest") == record["execution_intent"]["content_digest"]
             and execution.get("generation") == run["generation"]
             and execution.get("fencing_token") == run["fencing_token"]
             and execution.get("outcome") == "succeeded"
             and execution.get("result_digest") == recovery["completed_run_digest"])
    payload = {"target": target, "applied_at": recovery["applied_at"],
               "evidence_digest": recovery["evidence_digest"],
               "completed_run_digest": recovery["completed_run_digest"]}
    return {
        "event_id": _identity(app, "crash_recovery", target),
        "category": "crash_recovery", "outcome_state": "recovered",
        "classification": classification,
        "evidence_references": (_reference(payload, classification, run["revision"]),),
    }


def _cancellation_source(app: Any, record: dict, command: dict,
                         snapshot: dict, classification: str) -> dict:
    """Read exact applied owner CANCEL receipts, not native task cancellation."""
    directive = command["directive"]
    cancelled, previous = command["applied_run"], command["received_run"]
    request_id = command["request"]["request_id"]
    decision = snapshot["decisions"].get(request_id, {})
    canonical = snapshot["receipts"].get(_control_receipt_key(command["command_id"]), {})
    work_execution = snapshot["receipts"].get("exops.work." + record["action_id"], {})
    _require(
        command["state"] == directive["application_state"] == "applied"
        and directive["kind"] == command["body"]["kind"] == "cancel"
        and command["command_id"] == directive["directive_id"]
        and command["target"]["work_run_id"] == record["run"]["work_run_id"]
        and record["run"] == cancelled
        and record["application_state"] == record["receipt"]["application_state"] == "recorded"
        and command["receipt"]["application_state"] == "applied"
        and command["receipt"]["applied_run_revision"] == cancelled["revision"]
        and canonical.get("receipt") == command["execution_receipt"]
        and canonical.get("unresolved") is False
        and work_execution.get("receipt") == command["work_execution_receipt"]
        and work_execution.get("unresolved") is False
        and snapshot["human_requests"].get(request_id) == command["request"]
        and decision.get("response") == command["response"]
        and decision.get("receipt") == command["decision"]
        and decision.get("target_digest") == command["request"]["target_digest"]
        and decision.get("pending_apply") is False
    )
    contracts = WorkContracts(app.core)
    execution = command["work_execution_receipt"]
    contracts.record("ExecutionReceipt", execution)
    _require(
        execution["outcome"] == "succeeded"
        and execution["result_digest"] == record_digest(cancelled)
        and all(execution.get(key) == record["execution_intent"].get(key)
                for key in ("owner_operation_id", "work_run_id", "effect_id",
                            "content_digest", "generation", "fencing_token"))
    )
    reference = contracts.cancellation_reference(
        directive, record["spec"], previous, cancelled,
        receipt=command["execution_receipt"],
        classification=classification,
        receipt_reference_id=command["receipt"]["execution_receipt_id"],
        request=command["request"], response=command["response"],
        decision=command["decision"], target_binding=command["target_binding"],
        intent=command["execution_intent"], candidate_actor_id=command["actor_id"],
        now=datetime.fromisoformat(directive["applied_at"]), artifacts={},
    )
    identity = {"work_run_id": cancelled["work_run_id"],
                "directive_id": directive["directive_id"],
                "applied_run_revision": cancelled["revision"]}
    return {
        "event_id": _identity(app, "cancellation", identity),
        "category": "cancellation", "outcome_state": "aborted",
        "classification": classification, "source_run_id": cancelled["work_run_id"],
        "evidence_references": (reference,),
    }


def scan_native_lifecycle(app: Any) -> dict[str, int]:
    """Observe durable sources, then use the existing bounded Experience outbox.

    Called by operation recover; a future owner supervisor uses this same entry
    point. Re-enumeration plus committed event IDs is the restart cursor: a crash
    before observe retries the source, a crash after observe only resumes scan.
    Never starts a Workflow, replays an effect, assesses a result or sends data.
    """
    app.guard("recover")
    snapshot = app.journal.read()
    experience = KnowledgeExperience(app.journal, app.core, app.clock)
    known = {event["event_id"] for event in snapshot["experience_events"]}
    observed = skipped = deferred = 0
    sources: list[dict] = []
    recoveries: dict[str, list[dict]] = {}
    cancellations: dict[str, list[dict]] = {}
    for key, value in snapshot["intents"].items():
        if key.startswith("curation.recovery."):
            recoveries.setdefault(value.get("target", {}).get("work_run_id", ""), []).append(value)
        if (key.startswith("curation.control.") and value.get("state") == "applied"
                and value.get("directive", {}).get("kind") == "cancel"):
            cancellations.setdefault(value["target"]["work_run_id"], []).append(value)
    for key, record in sorted(snapshot["intents"].items()):
        if not key.startswith("exops.work.") or not (
            record.get("curation_native_failure")
            or recoveries.get(record.get("run", {}).get("work_run_id"))
            or cancellations.get(record.get("run", {}).get("work_run_id"))
        ):
            continue
        try:
            _, run, classification = _work(app, key, record)
            source = _failure_source(app, record, classification)
            if source is not None:
                sources.append(source)
            for recovery in recoveries.get(run["work_run_id"], []):
                source = _recovery_source(app, record, recovery, snapshot, classification)
                if source is not None:
                    sources.append(source)
            for command in cancellations.get(run["work_run_id"], []):
                sources.append(_cancellation_source(app, record, command, snapshot, classification))
        except (ValueError, OSError, KeyError, TypeError):
            # Missing/denied evidence stays unavailable, never a policy-denial
            # learning event. An invalid source cannot grant learning authority.
            skipped += 1
    for key, marker in sorted(snapshot["intents"].items()):
        if not key.startswith("experience.local_recovery."):
            continue
        try:
            effect = marker["effect_id"]
            _require(marker["state"] == "observed"
                     and snapshot["intents"][effect]["state"] == "completed"
                     and marker["execution"]["outcome"] == "succeeded")
            _source_classification(app, marker["source_reference"])
            sources.append({"event_id": "event." + hashlib.sha256(effect.encode()).hexdigest()[:24],
                            "category": "crash_recovery", "outcome_state": "recovered"})
        except (ValueError, OSError, KeyError, TypeError):
            skipped += 1
    for source in sources:
        already_observed = source["event_id"] in known
        try:
            experience.observe(**source)
        except AdmissionError as error:
            if error.code != "EXPERIENCE_CAPACITY":
                raise
            deferred += 1
            continue
        known.add(source["event_id"])
        observed += not already_observed
    return {"observed": observed, "skipped_unavailable": skipped,
            "deferred": deferred, **experience.scan()}
