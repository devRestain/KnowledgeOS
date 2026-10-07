"""Owner-journal effects for MCP plans and noncanonical Work artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from ..adapters.artifacts import create_artifact
from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..note_engine import parse_frontmatter
from ..template_engine import render_note_template

_PLAN_FIELDS = {
    "proposal_alignment": {"registry_digest", "changes", "rationale"},
    "proposal_project_review": {"observations", "interpretation", "next_actions", "citations"},
    "proposal_adaptation": {"gateway_receipt_id", "disclosure_scope", "adaptation", "citations"},
    "proposal_recovery_plan": {"preimage_digest", "unresolved_effect_ids", "steps"},
}


def _payload(action: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if action == "proposal_link_suggestions":
        references = arguments["candidate_references"]
        if not isinstance(references, list) or not 1 <= len(references) <= 20:
            raise AdmissionError("INVALID_ARGUMENT", "Link candidates must be bounded")
        return {"candidate_references": references, "status": "candidate_only"}
    if action == "artifact_submit":
        return {"artifact_kind": arguments["artifact_kind"], "content": arguments["content"]}
    if action not in _PLAN_FIELDS:
        raise AdmissionError("ACTION_DENIED", "Unsupported owner plan effect")
    try:
        value = json.loads(arguments["content"])
    except (TypeError, ValueError) as error:
        raise AdmissionError("INVALID_ARGUMENT", "Plan content must be JSON") from error
    if not isinstance(value, dict) or set(value) != _PLAN_FIELDS[action]:
        raise AdmissionError("INVALID_ARGUMENT", "Plan content does not match its action schema")
    if action == "proposal_recovery_plan" and (
            not isinstance(value["unresolved_effect_ids"], list) or not isinstance(value["steps"], list)):
        raise AdmissionError("INVALID_ARGUMENT", "Recovery plan requires exact effects and steps")
    if action == "proposal_project_review" and not all(
            isinstance(value[key], list) for key in ("observations", "next_actions", "citations")):
        raise AdmissionError("INVALID_ARGUMENT", "Project review requires separate observations and actions")
    return value


def submit_pending(application: Any, action: str, arguments: Mapping[str, Any],
                   *, session: Any = None) -> tuple[dict[str, Any], int]:
    """Record an exact plan or artifact without granting canonical apply."""
    app = application
    app.guard("propose")
    source = arguments.get("source_reference")
    if source is None:
        if session is None or getattr(session, "graph_run_id", None) is None:
            raise AdmissionError("SCOPE_DENIED", "An artifact needs an admitted Work Graph source")
        source = session.admitted_source()
    source_path = app.resolve_reference(dict(source))
    app._mcp_source_policy(source_path)
    source_raw = read_regular(resolve_beneath(app.roots.vault, source_path))
    if source_raw is None:
        raise AdmissionError("RESOURCE_UNAVAILABLE", "Selected source is unavailable")
    note = parse_frontmatter(source_raw.decode("utf-8"))
    payload = _payload(action, arguments)
    if action == "proposal_link_suggestions":
        for candidate in payload["candidate_references"]:
            candidate_path = app.resolve_reference(dict(candidate))
            app._mcp_source_policy(candidate_path)
    if action == "proposal_adaptation":
        from .read_tools import KnowledgeReadTools

        KnowledgeReadTools(app).gateway_evidence_read(payload["gateway_receipt_id"])
    if action == "proposal_alignment":
        from .read_tools import KnowledgeReadTools

        registry = KnowledgeReadTools(app).semantic_registry_read(category="relations")
        if payload["registry_digest"] != digest(canonical(registry["definitions"])):
            raise AdmissionError("RESOURCE_STALE", "Ontology registry changed")
    if action == "proposal_recovery_plan" and payload["preimage_digest"] != source["content_digest"]:
        raise AdmissionError("RESOURCE_STALE", "Recovery preimage differs from selected source")
    key = arguments.get("idempotency_key") or digest(canonical(payload))
    if not isinstance(key, str) or not 1 <= len(key) <= 500:
        raise AdmissionError("INVALID_ARGUMENT", "Idempotency key is invalid")
    identity = {"action": action, "source": source, "payload": payload, "idempotency_key": key,
                "work_run_id": getattr(session, "work_run_id", None),
                "graph_run_id": getattr(session, "graph_run_id", None),
                "executor_id": getattr(session, "executor_id", None)}
    effect_key = {field: identity[field] for field in (
        "action", "idempotency_key", "work_run_id", "graph_run_id", "executor_id")}
    suffix = hashlib.sha256(canonical(effect_key)).hexdigest()[:24]
    effect_id = "mcp." + suffix
    proposal_path = f"01_AI_Review/Pending/MCP {action} {suffix}.md"
    created = str(note.properties["created"])
    body = ("# " + action + "\n\n"
            "This is a noncanonical owner proposal or Work artifact.\n\n"
            "## Exact source\n\n`" + source_path + "` — `" + source["content_digest"] + "`\n\n"
            "## Submitted content\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```\n")
    artifact = render_note_template("T01_AI_Proposal.md", {
        "title": f"MCP {action} {suffix}", "id": suffix, "proposal_id": suffix,
        "created": created, "modified": created,
        "sensitivity": note.properties.get("sensitivity", "personal"),
        "source_hashes": [f"{source_path}|{source['content_digest']}"], "body": body,
    }).markdown.encode("utf-8")
    if len(artifact) > 64 * 1024:
        raise AdmissionError("INVALID_ARGUMENT", "Pending artifact exceeds its byte bound")
    now = app.clock()
    with app.journal.writer() as writer:
        app.guard("propose")
        if session is not None:
            session.authorize(action, arguments)
        if app.reference(source_path) != source:
            raise AdmissionError("RESOURCE_STALE", "Source changed before owner intent")
        prior = writer.value["intents"].get(effect_id)
        if prior is not None:
            if prior.get("identity") != identity:
                raise AdmissionError("IDEMPOTENCY_CONFLICT", "Effect identity belongs to other content")
            if prior["state"] != "completed":
                raise AdmissionError("OUTCOME_UNKNOWN", "Owner must reconcile the existing effect")
            if read_regular(resolve_beneath(app.roots.vault, proposal_path)) != artifact:
                raise AdmissionError("ARTIFACT_CONFLICT", "Pending bytes differ from owner receipt")
            return {"status": "NO_OP", "data": {"effect_id": effect_id,
                    "artifact_reference": app.reference(proposal_path, proposal=True),
                    "apply_allowed": False, "replayed": True},
                    "execution_outcome": "completed", "acceptance_state": "not_evaluated"}, 0
        intent = {"effect_id": effect_id, "owner_operation_id": "knowledgeos",
                  "content_digest": digest(artifact), "generation": writer.generation,
                  "fencing_token": f"generation.g{writer.generation}",
                  "issued_at": stamp(now), "expires_at": stamp(now + timedelta(hours=12)),
                  "state": "pending", "recorded_at": stamp(now), "source_path": source_path,
                  "source_digest": source["content_digest"], "artifact_path": proposal_path,
                  "frozen_artifact": base64.b64encode(artifact).decode(),
                  "identity": identity, "action": action, "acceptance_state": "not_evaluated"}
        app.core.validate("ExecutionIntent", {key: intent[key] for key in (
            "effect_id", "owner_operation_id", "content_digest", "generation",
            "fencing_token", "issued_at", "expires_at")})
        writer.value["intents"][effect_id] = intent
        writer.commit(now)
        writer.fence()
        if app.reference(source_path) != source:
            raise AdmissionError("RESOURCE_STALE", "Source changed before artifact dispatch")
        intent["state"] = "unknown"
        writer.commit(now)
        writer.fence()
        created_artifact = create_artifact(app.roots.vault, proposal_path, artifact)
        writer.fence()
        if app.reference(source_path) != source:
            raise AdmissionError("RESOURCE_STALE", "Source changed after artifact dispatch")
        intent["state"] = "completed"
        writer.value["receipts"][effect_id] = {"recorded_at": stamp(now),
            "execution": app._receipt(intent, now, "succeeded", digest(artifact))}
        artifact_reference = app.reference(proposal_path, proposal=True)
        if action == "artifact_submit" and session is not None:
            work_record = writer.value["intents"].get("exops.work." + session.work_run_id)
            if not isinstance(work_record, dict):
                raise AdmissionError("STALE_REVISION", "Work disappeared before artifact receipt")
            work_record.setdefault("submitted_artifacts", []).append(artifact_reference)
            formal_reference = {
                "owner_operation_id": "knowledgeos", "artifact_id": "artifact.mcp." + suffix,
                "source_revision": "revision.1", "content_digest": digest(artifact),
                "media_type": "text/markdown", "size_bytes": len(artifact),
                "classification": "internal", "availability": "available_by_owner_export",
                "export_capability_id": "knowledgeos.mcp.artifact_read",
            }
            app.core.validate("ArtifactReference", formal_reference)
            work_record.setdefault("artifact_references", {})[formal_reference["artifact_id"]] = formal_reference
        writer.value["history"].append({"event": "mcp_pending_created",
                                       "effect_id": effect_id,
                                       "sequence": writer.value["revision"] + 1})
        writer.commit(now)
        return {"status": "PASS", "data": {"effect_id": effect_id,
                "artifact_reference": artifact_reference,
                "work_artifact_reference": formal_reference if action == "artifact_submit" and session is not None else None,
                "apply_allowed": False, "created": created_artifact},
                "execution_outcome": "completed", "acceptance_state": "not_evaluated"}, 0
