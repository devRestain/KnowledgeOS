"""Metadata and payload admission; ACK never denotes domain execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..adapters.core import PINS, AdmissionError, canonical, digest
from ..adapters.owner_journal import stamp
from .knowledge import KnowledgeApplication


@dataclass(frozen=True)
class GatewayIdentity:
    operation_id: str
    principal_id: str
    allowed_actions: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeGateway:
    application: KnowledgeApplication

    def validate(self, envelope: dict[str, Any], payload: dict[str, Any], caller: GatewayIdentity, now: datetime) -> None:
        app = self.application
        catalog = app.core.verify()
        app.core.validate("InterOpsEnvelope", envelope)
        if envelope["recipient_operation_id"] != "knowledgeos" or envelope["sender_operation_id"] != caller.operation_id or envelope["sender_principal_id"] != caller.principal_id:
            raise AdmissionError("OWNER_DENIED", "authenticated sender or recipient does not match envelope")
        if envelope["core_digest"] != PINS["core_digest"] or envelope["semantic_digest"] != PINS["semantic_digest"]:
            raise AdmissionError("PIN_MISMATCH", "Gateway requires exact adopted release pins")
        if envelope["resource_owner_operation_id"] != "knowledgeos" or envelope["resource_kind"] not in {"operation", "vault", "artifact", "exchange"}:
            raise AdmissionError("OWNER_DENIED", "Gateway resource is not owned by this Operation")
        action = envelope["action"]
        if action not in caller.allowed_actions or action not in app.policy["gateway_actions"]:
            raise AdmissionError("ACTION_DENIED", "authenticated Gateway policy denies this action")
        issued = datetime.fromisoformat(envelope["issued_at"])
        if issued > now or now >= issued + timedelta(seconds=envelope["ttl_seconds"]):
            raise AdmissionError("MESSAGE_EXPIRED", "Gateway envelope is expired or issued in the future")
        policy = catalog["interop_policy"]
        kind = envelope["message_kind"]
        profile = policy["payload_profiles"][kind]
        if envelope["purpose"] not in profile["purposes"] or envelope["payload_type"] not in profile["payload_types"] or action not in profile["actions"]:
            raise AdmissionError("PAYLOAD_DENIED", "kind, action, purpose and payload profile disagree")
        purpose_types = policy["purpose_payload_types"][kind][envelope["purpose"]]
        if envelope["payload_type"] not in purpose_types:
            raise AdmissionError("PAYLOAD_DENIED", "purpose does not admit this payload type")
        if kind in policy["foundation_request_kinds"] and caller.operation_id not in policy["foundation_operation_ids"]:
            raise AdmissionError("REQUESTER_DENIED", "ordinary Operations cannot initiate cross-Operation requests")
        known_terms = {item["id"] for item in catalog["semantic_bundle"]["terms"]}
        if not set(envelope["semantic_term_ids"]).issubset(known_terms):
            raise AdmissionError("SEMANTIC_PROFILE_UNKNOWN", "Gateway semantic terms are not in the adopted bundle")
        encoded = canonical(payload)
        if len(encoded) != envelope["payload_size_bytes"] or digest(encoded) != envelope["payload_digest"]:
            raise AdmissionError("PAYLOAD_DIGEST_MISMATCH", "payload bytes differ from the envelope binding")
        if envelope["payload_type"] == "information.query":
            if set(payload) != {"query"} or not isinstance(payload["query"], str) or not 1 <= len(payload["query"]) <= 4096:
                raise AdmissionError("PAYLOAD_INVALID", "information query requires bounded query text only")
        elif envelope["payload_type"] == "status.snapshot":
            app.core.validate("StatusSnapshot", payload)
            if payload["operation_id"] != caller.operation_id:
                raise AdmissionError("OWNER_DENIED", "status source does not match authenticated sender")
        elif envelope["payload_type"] == "experience.candidate":
            app.core.validate("ExperienceCandidate", payload)
            if payload["owner_operation_id"] != caller.operation_id:
                raise AdmissionError("OWNER_DENIED", "experience source does not match authenticated sender")
        elif kind == "correlated_response" and envelope["payload_type"] == "information.result":
            correlation = app.journal.read()["correlations"].get(envelope.get("correlation_id"))
            if correlation is None or set(correlation) != {"envelope", "payload", "caller"}:
                raise AdmissionError("CORRELATION_UNKNOWN", "response requires a full trusted owner request record")
            prior = correlation["envelope"]
            owner = correlation["caller"]
            if not isinstance(owner, dict) or set(owner) != {"operation_id", "principal_id", "allowed_actions"} or owner["operation_id"] != "knowledgeos" or owner["principal_id"] != app.identity.actor_id:
                raise AdmissionError("CORRELATION_UNKNOWN", "prior request lacks the trusted owner identity")
            app.core.validate("InterOpsEnvelope", prior)
            if prior["sender_operation_id"] != "knowledgeos" or prior["recipient_operation_id"] != caller.operation_id or prior["resource_owner_operation_id"] != caller.operation_id or prior["request_id"] != envelope["request_id"] or prior["purpose"] != envelope["purpose"]:
                raise AdmissionError("CORRELATION_UNKNOWN", "response does not match the admitted request")
            if prior["sender_principal_id"] != owner["principal_id"] or prior["action"] not in owner["allowed_actions"] or prior["action"] not in app.policy["gateway_actions"] or prior["message_kind"] != "information_request" or prior["payload_type"] != "information.query":
                raise AdmissionError("CORRELATION_UNKNOWN", "prior request eligibility or policy differs")
            if prior["core_digest"] != PINS["core_digest"] or prior["semantic_digest"] != PINS["semantic_digest"] or not set(prior["semantic_term_ids"]).issubset(known_terms):
                raise AdmissionError("CORRELATION_UNKNOWN", "prior request pins or semantic profile differ")
            prior_issued = datetime.fromisoformat(prior["issued_at"])
            if prior_issued > now or now >= prior_issued + timedelta(seconds=prior["ttl_seconds"]):
                raise AdmissionError("CORRELATION_EXPIRED", "prior correlated request has expired")
            prior_payload = correlation["payload"]
            if not isinstance(prior_payload, dict) or set(prior_payload) != {"query"} or not isinstance(prior_payload["query"], str) or not 1 <= len(prior_payload["query"]) <= 4096 or len(canonical(prior_payload)) != prior["payload_size_bytes"] or digest(canonical(prior_payload)) != prior["payload_digest"]:
                raise AdmissionError("CORRELATION_UNKNOWN", "prior correlation payload differs")
            if set(payload) != {"resources"} or not isinstance(payload["resources"], list) or len(payload["resources"]) > 20:
                raise AdmissionError("PAYLOAD_INVALID", "information result requires a bounded ResourceReference array")
            for reference in payload["resources"]:
                app.core.validate("ResourceReference", reference)
                if reference["owner_operation_id"] != caller.operation_id:
                    raise AdmissionError("OWNER_DENIED", "result resource does not belong to the authenticated sender")
        else:
            raise AdmissionError("PAYLOAD_DENIED", "Gateway has no domain handler for this payload")

    def admit(self, envelope: dict[str, Any], payload: dict[str, Any], caller: GatewayIdentity) -> dict[str, Any]:
        app = self.application
        app.guard("inspect")
        now = app.clock()
        self.validate(envelope, payload, caller, now)
        fingerprint = digest(canonical({"envelope": envelope, "payload": payload}))
        key = caller.operation_id + ":" + envelope["idempotency_key"]
        with app.journal.writer() as session:
            self.validate(envelope, payload, caller, now)
            previous = session.value["gateway"].get(key)
            if previous is not None and (now - datetime.fromisoformat(previous["recorded_at"])).total_seconds() < 43200:
                if previous["content_digest"] != fingerprint:
                    raise AdmissionError("REQUEST_CONFLICT", "idempotency key has another exact request")
                return {**previous["receipt"], "replayed": True}
            receipt = {"receipt_id": "admission." + fingerprint[7:39], "owner_operation_id": "knowledgeos", "request_id": envelope["request_id"], "content_digest": fingerprint,
                       "admission_state": "admitted", "execution_outcome": "not_started", "acceptance_state": "not_evaluated", "human_approval": "unknown", "replayed": False}
            session.value["gateway"][key] = {"recorded_at": stamp(now), "content_digest": fingerprint, "receipt": receipt, "envelope": envelope, "payload_digest": envelope["payload_digest"], "detail_sequence": session.value["revision"] + 1}
            session.value["history"].append({"event": "gateway_admitted", "receipt_id": receipt["receipt_id"], "sequence": session.value["revision"] + 1})
            session.commit(now)
            return receipt
