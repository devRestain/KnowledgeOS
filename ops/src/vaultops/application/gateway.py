"""Metadata and payload admission; ACK never denotes domain execution."""

from __future__ import annotations

from collections.abc import Callable
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
        if any(envelope[field] != PINS[pin] for field, pin in (
            ("core_version", "core_version"), ("core_digest", "core_digest"),
            ("schema_version", "schema_version_pin"),
            ("semantic_version", "semantic_version"),
            ("semantic_digest", "semantic_digest"),
            ("contract_version", "capsule_version"),
        )):
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
        if kind == "standard_submission" and (
            envelope["resource_kind"] != "exchange" or envelope["hop_count"] != 0 or
            envelope["purpose"] not in policy["standard_submission_purposes"]
        ):
            raise AdmissionError("PAYLOAD_DENIED", "standard submission has invalid route or purpose")
        if kind in policy["foundation_request_kinds"] and envelope["hop_count"] != 1:
            raise AdmissionError("HOP_LIMIT", "cross-Operation request requires one hop")
        if kind == "correlated_response" and not envelope.get("correlation_id"):
            raise AdmissionError("CORRELATION_UNKNOWN", "response requires an admitted prior request")
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
            if not isinstance(owner, dict) or set(owner) != {"operation_id", "principal_id", "allowed_actions"} or owner["operation_id"] != "knowledgeos":
                raise AdmissionError("CORRELATION_UNKNOWN", "prior request lacks the trusted owner identity")
            app.core.validate("InterOpsEnvelope", prior)
            if prior["sender_operation_id"] != "knowledgeos" or prior["recipient_operation_id"] != caller.operation_id or prior["resource_owner_operation_id"] != caller.operation_id or prior["request_id"] != envelope["correlation_id"] or prior["request_id"] != envelope["request_id"] or prior["purpose"] != envelope["purpose"]:
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

    def status(self) -> dict[str, Any]:
        """Read-only owner view; admission is never reported as execution."""
        self.application.guard("inspect")
        state = self.application.journal.read()
        counts = {name: 0 for name in ("pending", "in_flight", "admitted", "rejected",
                                       "expired", "quarantined")}
        for row in state["outbox"]:
            counts[row["entry"]["delivery_state"]] += 1
        return {"status": "PASS", "owner_operation_id": "knowledgeos",
                "core_version": PINS["core_version"],
                "source_event_count": len(state["experience_events"]),
                "candidate_count": len(state["experience"]),
                "delivery_counts": counts,
                "inbound_admission_count": len(state["gateway"]),
                "outbound_receipt_count": len(state["interops_outbound"]),
                "delivery_transport": "explicit_local_binding_required"}

    def dispatch_experience(self, transport: Any) -> dict[str, Any] | None:
        """Dispatch only the owner-committed Experience queue through this Gateway."""
        app = self.application
        app.guard("storage")
        if app.identity.ingress != "operator" or app.identity.owner_operation_id != "knowledgeos":
            raise AdmissionError("OWNER_DENIED", "Experience dispatch requires the owner operator")
        from .experience import KnowledgeExperience

        return KnowledgeExperience(app.journal, app.core, app.clock).dispatch_one(transport)

    def register_outbound_request(
        self, envelope: dict[str, Any], payload: dict[str, Any],
        caller: GatewayIdentity, *, recipient_available: Callable[[str], bool],
    ) -> dict[str, Any]:
        """Persist full owner correlation before a separately selected transport sends it."""
        app = self.application
        app.guard("inspect")
        if (caller.operation_id != "knowledgeos" or
            caller.principal_id != "knowledgeos.local" or
            envelope.get("sender_operation_id") != caller.operation_id or
            envelope.get("sender_principal_id") != caller.principal_id):
            raise AdmissionError("OWNER_DENIED", "outbound requester is not KnowledgeOS")
        app.core.validate("InterOpsEnvelope", envelope)
        catalog = app.core.verify()
        profile = catalog["interop_policy"]["payload_profiles"]["information_request"]
        known_terms = {item["id"] for item in catalog["semantic_bundle"]["terms"]}
        if (envelope["purpose"] not in profile["purposes"] or
            envelope["payload_type"] not in profile["payload_types"] or
            envelope["action"] not in profile["actions"] or
            envelope["payload_type"] not in catalog["interop_policy"]["purpose_payload_types"]["information_request"].get(envelope["purpose"], []) or
            not set(envelope["semantic_term_ids"]).issubset(known_terms)):
            raise AdmissionError("PAYLOAD_DENIED", "outbound request profile or semantics differ")
        if (envelope["message_kind"] != "information_request" or
            envelope["payload_type"] != "information.query" or
            envelope["action"] not in caller.allowed_actions or
            envelope["action"] not in app.policy["gateway_actions"] or
            envelope["hop_count"] != 1 or
            envelope["recipient_operation_id"] == "knowledgeos" or
            envelope["resource_owner_operation_id"] != envelope["recipient_operation_id"] or
            not recipient_available(envelope["recipient_operation_id"])):
            raise AdmissionError("UNAVAILABLE", "recipient route or request profile is unavailable")
        if any(envelope[field] != PINS[pin] for field, pin in (
            ("core_version", "core_version"), ("core_digest", "core_digest"),
            ("schema_version", "schema_version_pin"),
            ("semantic_version", "semantic_version"),
            ("semantic_digest", "semantic_digest"),
            ("contract_version", "capsule_version"),
        )):
            raise AdmissionError("PIN_MISMATCH", "outbound request pins differ")
        issued = datetime.fromisoformat(envelope["issued_at"])
        now = app.clock()
        if issued > now or now >= issued + timedelta(seconds=envelope["ttl_seconds"]):
            raise AdmissionError("MESSAGE_EXPIRED", "outbound request is expired")
        if (set(payload) != {"query"} or not isinstance(payload["query"], str) or
            not 1 <= len(payload["query"]) <= 4096 or
            envelope["payload_size_bytes"] != len(canonical(payload)) or
            envelope["payload_digest"] != digest(canonical(payload))):
            raise AdmissionError("PAYLOAD_INVALID", "outbound information query differs")
        record = {"envelope": envelope, "payload": payload,
                  "caller": {"operation_id": caller.operation_id,
                             "principal_id": caller.principal_id,
                             "allowed_actions": list(caller.allowed_actions)}}
        with app.journal.writer() as session:
            previous = session.value["correlations"].get(envelope["request_id"])
            if previous is not None:
                if previous != record:
                    raise AdmissionError("REQUEST_CONFLICT", "outbound request ID changed content")
                return {"status": "recorded", "replayed": True,
                        "request_id": envelope["request_id"]}
            session.value["correlations"][envelope["request_id"]] = record
            session.commit(now)
        return {"status": "recorded", "replayed": False,
                "request_id": envelope["request_id"], "dispatch_performed": False}
