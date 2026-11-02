"""Owner-bound exchange disclosure and selected diagnostic plan contracts."""
from __future__ import annotations

import base64
import json

from ..adapters.core import AdmissionError, canonical, digest
from .read_tools import KnowledgeReadTools

EXCHANGE_PREFIX = "Adapt accepted Gateway receipt "


def require(condition, message):
    if not condition:
        raise AdmissionError("SCOPE_DENIED", message)


def exchange_receipt_id(objective):
    require(isinstance(objective, str) and objective.startswith(EXCHANGE_PREFIX),
            "Exchange objective must bind one accepted Gateway receipt")
    value = objective[len(EXCHANGE_PREFIX):]
    require(value.startswith("admission.") and len(value) == 42,
            "Exchange receipt identity is invalid")
    return value


def exchange_evidence(app, receipt_id):
    evidence = KnowledgeReadTools(app).gateway_evidence_read(receipt_id)
    envelope = evidence["envelope"]
    require(envelope["message_kind"] == "correlated_response"
            and envelope["payload_type"] == "information.result"
            and envelope["recipient_operation_id"] == "knowledgeos"
            and envelope.get("correlation_id") == envelope["request_id"]
            and isinstance(envelope.get("scope"), str) and bool(envelope["scope"].strip())
            and envelope["classification"] in {"public", "internal"},
            "Exchange requires one current correlated information result with explicit disclosure")
    require(bool(evidence["payload"]["resources"]), "Exchange requires disclosed resource evidence")
    allowed = {"public"} if envelope["classification"] == "public" else {"public", "internal"}
    require(all(reference["owner_operation_id"] == envelope["sender_operation_id"]
                and reference["classification"] in allowed
                for reference in evidence["payload"]["resources"]),
            "Exchange resources exceed authenticated sender disclosure")
    return evidence


def disclosure(evidence):
    envelope = evidence["envelope"]
    return {key: envelope[key] for key in (
        "scope", "classification", "purpose", "sender_operation_id", "recipient_operation_id")}


def validate_boundary_plan(app, action, source, payload, *, receipt_id=None):
    fields = ({"gateway_receipt_id", "disclosure_scope", "adaptation", "citations"}
              if action == "proposal_adaptation" else {"preimage_digest", "unresolved_effect_ids", "steps"})
    require(isinstance(payload, dict) and set(payload) == fields,
            "Boundary plan fields differ from the selected owner contract")
    if action == "proposal_adaptation":
        require(receipt_id is None or payload["gateway_receipt_id"] == receipt_id,
                "Adaptation changed the Director-selected exchange receipt")
        evidence = exchange_evidence(app, payload["gateway_receipt_id"])
        require(payload["disclosure_scope"] == disclosure(evidence)
                and payload["citations"] == evidence["payload"]["resources"]
                and isinstance(payload["adaptation"], str)
                and 0 < len(payload["adaptation"].strip()) <= 12000,
                "Adaptation changed exact Gateway disclosure, provenance or evidence")
        return evidence
    require(action == "proposal_recovery_plan", "Unsupported boundary plan")
    diagnostic = KnowledgeReadTools(app).vault_diagnose(source)
    require(payload["preimage_digest"] == source["content_digest"]
            and payload["unresolved_effect_ids"] == diagnostic["unresolved_effect_ids"],
            "Recovery plan changed source preimage or unresolved owner outcomes")
    steps = payload["steps"]
    require(isinstance(steps, list) and 1 <= len(steps) <= 20, "Recovery plan requires bounded human review steps")
    for step in steps:
        require(isinstance(step, dict) and set(step) == {
            "action", "diagnostic_digest", "diagnosis_complete", "unexamined", "instruction"}
            and step["action"] == "human_review"
            and step["diagnostic_digest"] == digest(canonical(diagnostic))
            and step["diagnosis_complete"] is False
            and step["unexamined"] == diagnostic["unexamined"]
            and isinstance(step["instruction"], str)
            and 0 < len(step["instruction"].strip()) <= 2000,
            "Recovery must retain selected diagnostic limits and operator-only review")
    return diagnostic


def validate_boundary_export(app, record, plan, raw):
    """Reconcile exact exported bytes with the independently assessed owner effect."""
    exchange = record["spec"]["method_contract_ref"]["method_id"] == "knowledgeos.knowledge_exchange_team"
    namespace, stage, action = (("exchange", "adaptation", "proposal_adaptation") if exchange else
                                ("maintenance", "recovery_plan", "proposal_recovery_plan"))
    execution = record[namespace + "_" + stage]
    require(execution["evidence_digest"] == digest(canonical({
        "request": execution["request"], "response": execution["response"]})),
        "Boundary export stage evidence changed")
    calls = execution["response"]["tool_calls"]
    require(len(calls) == 1 and calls[0]["name"] == action, "Boundary export effect changed")
    call = calls[0]
    arguments, data = call["arguments"], call["result"]["data"]
    require(json.loads(arguments["content"]) == plan
            and arguments["source_reference"] == record["method_source_reference"]
            and data["work_artifact_reference"] == record["run"]["result_reference"],
            "Boundary export differs from the assessed plan")
    snapshot = app.journal.read()
    effect = snapshot["intents"].get(data["effect_id"], {})
    receipt = snapshot["receipts"].get(data["effect_id"], {}).get("execution", {})
    expected = {"action": action, "source": record["method_source_reference"], "payload": plan,
                "idempotency_key": arguments.get("idempotency_key") or digest(canonical(plan)),
                "work_run_id": record["run"]["work_run_id"],
                "graph_run_id": execution["request"]["graph_run_id"],
                "executor_id": record["spec"]["allowed_executor_ids"][-1]}
    require(effect.get("state") == "completed" and effect.get("action") == action
            and effect.get("identity") == expected
            and effect.get("content_digest") == digest(raw)
            and base64.b64decode(effect.get("frozen_artifact", ""), validate=True) == raw
            and receipt.get("outcome") == "succeeded" and receipt.get("result_digest") == digest(raw)
            and all(receipt.get(key) == effect.get(key) for key in (
                "effect_id", "owner_operation_id", "content_digest", "generation", "fencing_token")),
            "Boundary export lacks its exact successful owner effect receipt")
    return validate_boundary_plan(
        app, action, record["method_source_reference"], plan,
        receipt_id=exchange_receipt_id(record["spec"]["objective"]) if exchange else None)
