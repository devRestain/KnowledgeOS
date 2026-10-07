"""KnowledgeOS-owned lifecycle extraction and durable InterOps delivery."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..adapters.core import PINS, AdmissionError, canonical, digest
from ..adapters.owner_journal import OwnerJournal, stamp, utc_now

_ELIGIBLE = frozenset({"failure", "timeout", "crash_recovery", "human_correction",
                       "policy_denial", "verified_unusual_success"})
_SAFE_TERMS = frozenset({"agentfabric.core.experience_candidate",
                         "agentfabric.core.run_outcome", "agentfabric.core.run_evaluation",
                         "agentfabric.core.evidence_availability"})
_PERMANENT_RECEIVER_DENIALS = frozenset({
    "DIGEST_MISMATCH", "EXPIRED", "FORBIDDEN", "IDEMPOTENCY_CONFLICT",
    "INVALID_PAYLOAD", "SCOPE_DENIED", "SEMANTIC_PROFILE_UNKNOWN",
})


def _event_digest(event: dict[str, Any]) -> str:
    base = {key: copy.deepcopy(value) for key, value in event.items() if key != "source_record"}
    base["source_revision"] = event["source_record"]["source_revision"]
    return digest(canonical(base))


@dataclass(frozen=True)
class KnowledgeExperience:
    journal: OwnerJournal
    core: Any
    clock: Any = utc_now

    def observe(
        self, *, event_id: str, category: str, outcome_state: str,
        evaluation_state: str = "not_evaluated", classification: str = "internal",
        evidence_references: tuple[dict[str, Any], ...] = (),
        semantic_term_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Commit a bounded owner event before any candidate extraction."""
        if category not in _ELIGIBLE and category != "routine_progress":
            raise AdmissionError("EXPERIENCE_INVALID", "unsupported lifecycle category")
        expected_outcomes = {"failure": "failed", "timeout": "timed_out",
                             "crash_recovery": "recovered", "policy_denial": "denied",
                             "verified_unusual_success": "completed",
                             "routine_progress": "completed"}
        if expected_outcomes.get(category, outcome_state) != outcome_state:
            raise AdmissionError("EXPERIENCE_INVALID", "lifecycle category and outcome differ")
        if not event_id.startswith("event.") or len(event_id) > 96:
            raise AdmissionError("EXPERIENCE_INVALID", "owner event ID is invalid")
        now = self.clock()
        with self.journal.writer() as session:
            prior = next((row for row in session.value["experience_events"]
                          if row["event_id"] == event_id), None)
            if prior is not None:
                if (prior["category"], prior["outcome_state"], prior["evaluation_state"],
                        prior["classification"], prior["evidence_references"],
                        prior["semantic_term_ids"]) != (category, outcome_state, evaluation_state,
                            classification, list(evidence_references), sorted(set(semantic_term_ids))):
                    raise AdmissionError("EXPERIENCE_CONFLICT", "event ID changed meaning")
                return copy.deepcopy(prior)
            if len(session.value["experience_events"]) >= 100:
                raise AdmissionError("EXPERIENCE_CAPACITY", "unprocessed source history is full")
            source_revision = f"revision.r{session.value['revision'] + 1}"
            signal = {
                "owner_operation_id": "knowledgeos", "event_id": event_id,
                "category": category, "classification": classification,
                "outcome_state": outcome_state, "evaluation_state": evaluation_state,
                "occurred_at": stamp(now),
                "evidence_references": list(copy.deepcopy(evidence_references)),
                "semantic_term_ids": sorted(set(semantic_term_ids)),
            }
            if len(signal["evidence_references"]) > 8 or len(signal["semantic_term_ids"]) > 16:
                raise AdmissionError("EXPERIENCE_CAPACITY", "source references exceed Core bounds")
            for reference in signal["evidence_references"]:
                self.core.validate("ArtifactReference", reference)
                if reference["owner_operation_id"] != "knowledgeos":
                    raise AdmissionError("OWNER_DENIED", "Experience evidence belongs to another Operation")
            signal["source_record"] = {
                "owner_operation_id": "knowledgeos", "record_kind": "owner_lifecycle_event",
                "record_id": event_id, "source_revision": source_revision,
                "content_digest": digest(canonical({"source_revision": source_revision, **signal})),
            }
            self.core.validate("ExperienceSourceEvent", signal)
            session.value["experience_events"].append(copy.deepcopy(signal))
            session.commit(now)
            return signal

    def scan(self) -> dict[str, int]:
        """Extract only selected, bounded observations from committed events."""
        produced = quarantined = 0
        with self.journal.writer() as session:
            events = session.value["experience_events"]
            cursor = session.value["experience_cursor"]
            if cursor > len(events):
                raise AdmissionError("STATE_INVALID", "Experience source cursor exceeds history")
            for event in events[cursor:]:
                self.core.validate("ExperienceSourceEvent", event)
                if event["source_record"]["content_digest"] != _event_digest(event):
                    raise AdmissionError("EXPERIENCE_CONFLICT", "committed source digest differs")
                if (event["category"] == "routine_progress" or event["classification"] not in
                        {"public", "internal"} or event["category"] == "verified_unusual_success"
                        and (event["outcome_state"] != "completed" or
                             event["evaluation_state"] != "pass")):
                    quarantined += 1
                    session.value["experience_cursor"] += 1
                    continue
                active = [row for row in session.value["outbox"]
                          if row["entry"]["delivery_state"] in {"pending", "in_flight"}]
                if len(active) >= 4:
                    break
                references = [copy.deepcopy(ref) for ref in event["evidence_references"]
                              if ref["classification"] in {"public", "internal"}
                              and ref["availability"] == "available_by_owner_export"]
                terms = sorted({"agentfabric.core.experience_candidate",
                                "agentfabric.core.run_outcome",
                                "agentfabric.core.run_evaluation"}
                               | (set(event["semantic_term_ids"]) & _SAFE_TERMS)
                               | ({"agentfabric.core.evidence_availability"} if references else set()))
                token = hashlib.sha256(event["event_id"].encode()).hexdigest()[:24]
                candidate = {
                    "candidate_id": f"exp.knowledgeos.{token}",
                    "owner_operation_id": "knowledgeos", "source_event_id": event["event_id"],
                    "source_revision": event["source_record"]["source_revision"],
                    "source_record": copy.deepcopy(event["source_record"]),
                    "source_instance_id": self.journal.identity["instance_id"],
                    "category": event["category"], "classification": event["classification"],
                    "outcome_state": event["outcome_state"],
                    "evaluation_state": event["evaluation_state"],
                    "occurred_at": event["occurred_at"], "evidence_references": references,
                    "semantic_term_ids": terms,
                    "observation_summary": (
                        f"KnowledgeOS {event['category']} at "
                        f"{event['source_record']['record_id']} "
                        f"({event['source_record']['source_revision']}); "
                        f"outcome={event['outcome_state']}; evaluation={event['evaluation_state']}."
                    ),
                    "candidate_fingerprint": digest(canonical({
                        "category": event["category"], "classification": event["classification"],
                        "outcome_state": event["outcome_state"],
                        "evaluation_state": event["evaluation_state"],
                        "evidence_references": references, "semantic_term_ids": terms,
                    })),
                    "idempotency_key": f"exp:knowledgeos:{token}",
                    "extractor_version": "0.2.0",
                    "redaction_policy_version": "knowledgeos.experience.v1",
                    "core_version": PINS["core_version"], "core_digest": PINS["core_digest"],
                    "semantic_version": PINS["semantic_version"],
                    "semantic_digest": PINS["semantic_digest"], "capability_pins": [],
                }
                self.core.validate("ExperienceCandidate", candidate)
                if len(canonical(candidate)) > 4096:
                    raise AdmissionError("EXPERIENCE_CAPACITY", "candidate exceeds the 4 KiB bound")
                created_at = event["occurred_at"]
                expiry = stamp(datetime.fromisoformat(created_at) + timedelta(hours=12))
                entry = {
                    "outbox_id": f"outbox.knowledgeos.{token}",
                    "candidate_id": candidate["candidate_id"],
                    "owner_operation_id": "knowledgeos",
                    "recipient_operation_id": "hermestrace",
                    "payload_digest": digest(canonical(candidate)),
                    "idempotency_key": candidate["idempotency_key"],
                    "delivery_state": "pending", "attempt_count": 0, "priority": 2,
                    "created_at": created_at, "expires_at": expiry,
                    "retention_until": expiry, "next_attempt_at": created_at,
                }
                self.core.validate("ExperienceOutboxEntry", entry)
                queued = {"candidate": candidate, "entry": entry, "transport_state": "unconfigured"}
                if sum(len(canonical(row)) for row in active) + len(canonical(queued)) > 16384:
                    break
                session.value["experience"].append(copy.deepcopy(candidate))
                session.value["outbox"].append(queued)
                session.value["experience_cursor"] += 1
                produced += 1
            if produced or quarantined:
                session.commit(self.clock())
        return {"produced": produced, "quarantined": quarantined}

    def envelope(self, candidate: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        token = candidate["candidate_id"].rsplit(".", 1)[-1]
        envelope = {
            "contract_version": PINS["capsule_version"],
            "core_version": PINS["core_version"], "core_digest": PINS["core_digest"],
            "schema_version": PINS["schema_version_pin"],
            "semantic_version": PINS["semantic_version"],
            "semantic_digest": PINS["semantic_digest"],
            "message_id": f"message.knowledgeos.{token}",
            "message_kind": "standard_submission",
            "request_id": f"request.knowledgeos.{token}",
            "idempotency_key": entry["idempotency_key"],
            "sender_operation_id": "knowledgeos",
            "sender_principal_id": "knowledgeos.local",
            "recipient_operation_id": "hermestrace", "purpose": "experience_submission",
            "action": "submit", "scope": "experience",
            "resource_owner_operation_id": "hermestrace",
            "resource_kind": "exchange", "resource_id": "exchange.hermestrace.experience",
            "payload_type": "experience.candidate",
            "payload_reference_id": candidate["candidate_id"],
            "payload_digest": entry["payload_digest"],
            "payload_size_bytes": len(canonical(candidate)),
            "classification": candidate["classification"],
            "semantic_term_ids": candidate["semantic_term_ids"],
            "issued_at": entry["created_at"], "ttl_seconds": 43200,
            "hop_count": 0, "revision": 1,
        }
        self.core.validate("InterOpsEnvelope", envelope)
        return envelope

    def dispatch_one(self, transport: Any) -> dict[str, Any] | None:
        """Lease one candidate; query an uncertain prior attempt before resend."""
        now = self.clock()
        with self.journal.writer() as session:
            due = next((row for row in session.value["outbox"] if (
                        row["entry"]["delivery_state"] == "pending" and
                        datetime.fromisoformat(row["entry"]["next_attempt_at"]) <= now) or (
                        row["entry"]["delivery_state"] == "in_flight" and
                        datetime.fromisoformat(row["entry"]["lease_expires_at"]) <= now)), None)
            if due is None:
                return None
            entry = due["entry"]
            if now >= datetime.fromisoformat(entry["expires_at"]):
                entry["delivery_state"] = "expired"
                session.commit(now)
                return {"outcome": "expired", "candidate_id": entry["candidate_id"]}
            entry["delivery_state"] = "in_flight"
            entry["attempt_count"] += 1
            entry["lease_expires_at"] = stamp(now + timedelta(seconds=60))
            self.core.validate("ExperienceOutboxEntry", entry)
            candidate = copy.deepcopy(due["candidate"])
            selected = copy.deepcopy(entry)
            session.commit(now)
        envelope = self.envelope(candidate, selected)
        content = {key: envelope[key] for key in
                   self.core.verify()["digest_policy"]["effective_request_fields"]}
        request_digest = digest(canonical(content))
        try:
            if selected["attempt_count"] > 1:
                receipt = transport.query_admission(
                    sender_operation_id="knowledgeos",
                    idempotency_key=selected["idempotency_key"],
                    request_digest=request_digest)
                if receipt is not None:
                    return self.record_receipt(receipt, candidate)
            receipt = transport.admit(envelope, candidate)
        except AdmissionError as error:
            if error.code in _PERMANENT_RECEIVER_DENIALS:
                self.quarantine(candidate, error.code)
            raise
        return self.record_receipt(receipt, candidate)

    def quarantine(self, candidate: dict[str, Any], reason_code: str) -> None:
        """Keep a permanent rejection visible without inventing a receiver ACK."""
        now = self.clock()
        with self.journal.writer() as session:
            row = next((item for item in session.value["outbox"] if
                        item["entry"]["candidate_id"] == candidate["candidate_id"]), None)
            if row is None or row["candidate"] != candidate or row["entry"]["delivery_state"] != "in_flight":
                raise AdmissionError("STALE_WRITER", "outbox changed before permanent rejection")
            row["entry"]["delivery_state"] = "quarantined"
            row["entry"].pop("lease_expires_at", None)
            row["transport_state"] = f"receiver_denied:{reason_code}"
            self.core.validate("ExperienceOutboxEntry", row["entry"])
            session.commit(now)

    def record_receipt(self, receipt: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        self.core.validate("ExperienceDeliveryReceipt", receipt)
        if (receipt["candidate_id"] != candidate["candidate_id"] or
                receipt["owner_operation_id"] != "knowledgeos" or
                receipt["recipient_operation_id"] != "hermestrace" or
                receipt["idempotency_key"] != candidate["idempotency_key"] or
                receipt["payload_digest"] != digest(canonical(candidate)) or
                receipt["admission_level"] != "owner_durable"):
            raise AdmissionError("RECEIPT_CONFLICT", "receiver receipt differs from owner intent")
        now = self.clock()
        with self.journal.writer() as session:
            row = next((item for item in session.value["outbox"] if
                        item["entry"]["candidate_id"] == candidate["candidate_id"]), None)
            if (row is None or row["candidate"] != candidate or
                    row["entry"]["delivery_state"] not in {"in_flight", "admitted"}):
                raise AdmissionError("RECEIPT_CONFLICT", "owner outbox changed before receipt")
            row["entry"]["delivery_state"] = receipt["disposition"]
            row["entry"]["receipt_id"] = receipt["receipt_id"]
            row["entry"].pop("lease_expires_at", None)
            row["transport_state"] = "owner_durable"
            self.core.validate("ExperienceOutboxEntry", row["entry"])
            session.value["interops_outbound"][candidate["candidate_id"]] = {
                "receipt": copy.deepcopy(receipt), "recorded_at": stamp(now),
            }
            session.commit(now)
        return copy.deepcopy(receipt)
