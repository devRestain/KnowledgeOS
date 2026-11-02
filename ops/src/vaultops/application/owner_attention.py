"""Owner-journal attention outbox; delivery is never Work or human-gate truth.

Only fixed redacted summaries derived from committed owner records leave this
boundary. No caller can submit arbitrary text or claim an execution outcome.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import quote, urlsplit

from ..adapters.core import AdmissionError
from ..adapters.owner_journal import OwnerJournal, stamp, utc_now
from .work import record_digest

PREFIX = "exops.attention."
SUMMARIES = {
    "human_wait": "KnowledgeOS: human review is pending. Opening this link does not approve anything.",
    "blocker": "KnowledgeOS: work is blocked or paused; inspect the owner console.",
    "error_recovery": "KnowledgeOS: execution needs owner recovery; delivery is not an execution result.",
    "evaluated_outcome": "KnowledgeOS: an independently evaluated outcome is available. Domain approval remains separate.",
    "cancelled_outcome": "KnowledgeOS: the owner applied cancellation. This is not evaluated completion.",
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _require(value: bool, message: str) -> None:
    if not value:
        raise AdmissionError("ATTENTION_DENIED", message)


def _id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


@dataclass(frozen=True)
class AttentionEvent:
    owner_operation_id: str
    work_id: str
    work_run_id: str
    action_id: str
    event_type: str
    event_id: str
    evidence_digest: str
    work_generation: int
    fencing_digest: str
    work_spec_digest: str


@dataclass(frozen=True)
class AttentionRoute:
    """Current owner export grant, never a webhook URL or a credential.

    The policy resolving this grant must authorize the exact event, logical
    destination and audience. Increment audience_generation on audience changes.
    """
    logical_destination_id: str
    destination_generation: int
    audience_generation: int
    console_url: str

    def validate(self) -> None:
        url = urlsplit(self.console_url)
        _require(_id(self.logical_destination_id)
                 and type(self.destination_generation) is int and self.destination_generation > 0
                 and type(self.audience_generation) is int and self.audience_generation > 0
                 and len(self.console_url) <= 384 and url.scheme == "https"
                 and bool(url.hostname) and url.username is None and url.password is None
                 and not url.query and not url.fragment
                 and not any(char.isspace() or ord(char) < 32 for char in self.console_url)
                 and url.path in {"", "/"}, "Route must be an admitted bounded HTTPS console origin")


@dataclass(frozen=True)
class AttentionNotice:
    notice_id: str
    logical_destination_id: str
    destination_generation: int
    content: str
    expires_at: datetime


@dataclass(frozen=True)
class AttentionOutcome:
    state: str
    message_id: str | None = None
    retry_after_seconds: float | None = None


class AttentionTransport(Protocol):
    def send_once(self, notice: AttentionNotice,
                  authorize: Callable[[AttentionNotice], AttentionNotice]) -> AttentionOutcome: ...


def committed_events(snapshot: Mapping, now: datetime) -> tuple[AttentionEvent, ...]:
    """Project actionable facts, not received commands, admission or Activity status.

    Initial independent gaps may remain on completed Curation records: only the
    latest-assessment digest can establish a current evaluated outcome.
    """
    events = []
    for key, work in snapshot["intents"].items():
        if not key.startswith("exops.work.") or not isinstance(work, dict):
            continue
        spec, run = work.get("spec", {}), work.get("run", {})
        action = work.get("action_id")
        if (spec.get("owner_operation_id") != "knowledgeos"
                or run.get("owner_operation_id") != "knowledgeos"
                or not all(_id(item) for item in (spec.get("work_id"), run.get("work_run_id"), action))
                or key != "exops.work." + run["work_run_id"]
                or run.get("work_id") != spec["work_id"]
                or run.get("work_spec_digest") != spec.get("spec_digest")):
            continue

        def add(kind, identity, evidence, *, source_spec=spec, source_run=run, source_action=action):
            # Bind only this synchronous projection's source snapshot. Delivery
            # still recomputes committed_events from the live journal each time.
            events.append(AttentionEvent("knowledgeos", source_spec["work_id"], source_run["work_run_id"],
                                         source_action, kind, record_digest(identity), record_digest(evidence),
                                         source_run["generation"], record_digest({"fence": source_run["fencing_token"]}),
                                         source_spec["spec_digest"]))

        assessment = work.get("assessment")
        receipt = snapshot["receipts"].get("exops.work." + action, {})
        execution = receipt.get("receipt", {})
        recorded = (work.get("application_state") == "recorded"
                    and work.get("receipt", {}).get("application_state") == "recorded"
                    and receipt.get("unresolved") is False
                    and execution.get("work_run_id") == run["work_run_id"]
                    and execution.get("effect_id") == action
                    and execution.get("content_digest") == work.get("content_digest")
                    and execution.get("outcome") == "succeeded"
                    and execution.get("result_digest") == record_digest(run))
        if (recorded and run.get("state") == "completed" and isinstance(assessment, dict)
                and assessment.get("completion_state") == "complete"
                and assessment.get("work_run_id") == run["work_run_id"]
                and record_digest(assessment) == run.get("latest_assessment_digest")):
            add("evaluated_outcome", {"assessment": record_digest(assessment)},
                {"run": run, "assessment": assessment, "execution": execution})
        if recorded and run.get("state") == "cancelled":
            for command_key, command in snapshot["intents"].items():
                if not command_key.startswith("curation.control."):
                    continue
                command_id = command.get("body", {}).get("command_id")
                control = snapshot["receipts"].get(
                    "exops.control." + record_digest({"command_id": command_id})[7:39], {})
                if (command.get("state") == "applied"
                        and command.get("body", {}).get("kind") == "cancel"
                        and command.get("receipt", {}).get("application_state") == "applied"
                        and command.get("applied_run") == run
                        and command.get("work_execution_receipt") == execution
                        and control.get("unresolved") is False
                        and control.get("receipt") == command.get("execution_receipt")
                        and command.get("execution_receipt", {}).get("result_digest") == record_digest(run)):
                    add("cancelled_outcome", {"command": command_id},
                        {"run": run, "control": control, "execution": execution})
        if (work.get("application_state") == "unknown"
                or work.get("dispatch_state") in {"unknown", "rejected"}):
            evidence = {"run_revision": run.get("revision"),
                        "application_state": work.get("application_state"),
                        "dispatch_state": work.get("dispatch_state")}
            add("error_recovery", evidence, evidence)
        elif run.get("state") in {"paused", "blocked"} or work.get("continuation_state") in {
                "human_required", "budget_exhausted", "repeated_gap_limit"}:
            evidence = {"run_revision": run.get("revision"), "state": run.get("state"),
                        "continuation_state": work.get("continuation_state"),
                        "latest_assessment_digest": run.get("latest_assessment_digest")}
            add("blocker", evidence, evidence)
        if (run.get("state") == "awaiting_human" and not run.get("pending_action_ids")
                and isinstance(assessment, dict) and assessment.get("completion_state") != "complete"
                and record_digest(assessment) == run.get("latest_assessment_digest")):
            # The controlled initial independent gap is an actual owner wait,
            # unlike a received control command waiting for Worker application.
            evidence = {"run_revision": run.get("revision"),
                        "assessment_digest": run["latest_assessment_digest"],
                        "control_revision": run.get("control_revision")}
            add("human_wait", evidence, evidence)
        # Proposal approval gates are independent of evaluated Work completion.
        for effect in snapshot["intents"].values():
            if effect.get("work_binding", {}).get("work_run_id") != run["work_run_id"]:
                continue
            request_id = effect.get("human_request_id")
            request = snapshot["human_requests"].get(request_id, {})
            if (effect.get("state") == "completed" and request.get("lifecycle") == "pending"
                    and request_id not in snapshot["decisions"]
                    and isinstance(request.get("expires_at"), str)
                    and datetime.fromisoformat(request["expires_at"]) > now):
                add("human_wait", {"request_id": request_id, "revision": request.get("request_revision")},
                    {"request_id": request_id, "revision": request.get("request_revision"),
                     "expires_at": request["expires_at"], "target_digest": request.get("target_digest")})
    return tuple(events)


class OwnerAttention:
    """A serialized durable outbox with a fresh owner export decision per boundary."""

    def __init__(self, journal: OwnerJournal, current_route: Callable[[AttentionEvent], AttentionRoute | None],
                 transport: AttentionTransport, *, clock: Callable[[], datetime] = utc_now,
                 authorize_reconciliation: Callable[[str, Mapping], bool] | None = None):
        self.journal, self.current_route, self.transport = journal, current_route, transport
        self.clock = clock
        self.authorize_reconciliation = authorize_reconciliation

    def _grant(self, event: AttentionEvent) -> AttentionRoute:
        route = self.current_route(event)
        _require(isinstance(route, AttentionRoute), "Current export, routing or audience authorization denied")
        route.validate()
        return route

    def scan(self) -> list[str]:
        """Admit only facts currently present in the committed owner Work journal."""
        ids = []
        now = self.clock()
        with self.journal.writer() as session:
            for event in committed_events(session.value, now):
                try:
                    route = self._grant(event)
                except AdmissionError:
                    continue
                identity = {"event": asdict(event), "route": asdict(route)}
                key = PREFIX + record_digest(identity)[7:]
                if key in session.value["intents"]:
                    ids.append(key)
                    continue
                # Once an event was queued, route changes cannot move its old
                # content to another audience. A new owner event is required.
                if any(item.get("event") == asdict(event) for name, item in session.value["intents"].items()
                       if name.startswith(PREFIX)):
                    continue
                content = SUMMARIES[event.event_type] + "\n" + route.console_url.rstrip("/") + "/#work_run_id=" + quote(event.work_run_id, safe="")
                session.value["intents"][key] = {
                    "state": "pending", "recorded_at": stamp(now), "event": asdict(event),
                    "route": asdict(route), "content": content,
                    "expires_at": stamp(now + timedelta(hours=12)), "attempt_count": 0,
                    "message_id": None, "retry_at": None,
                }
                ids.append(key)
                session.commit(now)
        return ids

    def _authorize(self, record: Mapping, snapshot: Mapping, now: datetime) -> None:
        event = AttentionEvent(**record["event"])
        _require(event in committed_events(snapshot, now), "Source event is no longer current")
        _require(asdict(self._grant(event)) == record["route"], "Destination or audience generation changed")
        _require(datetime.fromisoformat(record["expires_at"]) > now, "Notice expired")

    def attempt(self, notice_id: str) -> dict:
        now = self.clock()
        with self.journal.writer() as session:
            record = session.value["intents"].get(notice_id)
            _require(notice_id.startswith(PREFIX) and isinstance(record, dict), "Exact notice is unavailable")
            self._authorize(record, session.value, now)
            if record["state"] not in {"pending", "retryable"}:
                return copy.deepcopy(record)
            if record["retry_at"] and datetime.fromisoformat(record["retry_at"]) > now:
                return copy.deepcopy(record)
            # Persist known host rate limits independently of process-local
            # connections, conservatively across all this owner's destinations.
            cooldown = session.value["receipts"].get(PREFIX + "rate-limit", {}).get("retry_at")
            if cooldown and datetime.fromisoformat(cooldown) > now:
                return copy.deepcopy(record)
            route = record["route"]
            notice = AttentionNotice(notice_id.removeprefix(PREFIX), route["logical_destination_id"],
                                     route["destination_generation"], record["content"],
                                     datetime.fromisoformat(record["expires_at"]))
            # Persist uncertainty *before* crossing the host boundary. A crash,
            # lost response or lost final journal write can never imply retry.
            record.update(state="unknown", attempt_count=record["attempt_count"] + 1,
                          attempt_id=record_digest({"notice_id": notice_id, "attempt": record["attempt_count"] + 1}),
                          retry_at=None)
            session.commit(now)

            def authorize(supplied):
                self._authorize(record, self.journal.read(), self.clock())
                _require(supplied == notice, "Host notice changed rendered owner export")
                return supplied

            try:
                authorize(notice)
                outcome = self.transport.send_once(notice, authorize)
            except (AdmissionError, OSError, ValueError):
                # Expected host authorization/validation and I/O failures never
                # journal exception strings (URLs, secrets or source text).
                outcome = AttentionOutcome("unknown")
            # Unexpected programming faults propagate unchanged to the caller's
            # public error boundary. The pretransport unknown commit survives.
            valid_message = (isinstance(outcome.message_id, str) and outcome.message_id.isdecimal()
                             and len(outcome.message_id) <= 32)
            delay = outcome.retry_after_seconds
            valid_delay = (type(delay) in {int, float} and math.isfinite(delay) and 0 < delay <= 86400)
            state = outcome.state if outcome.state in {"confirmed", "retryable", "rejected", "unknown"} else "unknown"
            if (state == "confirmed" and not valid_message) or (state == "retryable" and not valid_delay):
                state = "unknown"
            record.update(state=state, message_id=outcome.message_id if state == "confirmed" else None,
                          retry_at=stamp(self.clock() + timedelta(seconds=delay)) if state == "retryable" else None)
            if state == "retryable":
                session.value["receipts"][PREFIX + "rate-limit"] = {
                    "recorded_at": stamp(self.clock()), "unresolved": True,
                    "retry_at": record["retry_at"],
                }
            session.value["receipts"][notice_id] = {
                "recorded_at": stamp(self.clock()), "unresolved": state in {"unknown", "retryable"},
                "state": state, "message_id": record["message_id"],
                "attempt_id": record["attempt_id"], "route": copy.deepcopy(route),
            }
            session.commit(self.clock())
            return copy.deepcopy(record)

    def reconcile_unknown(self, notice_id: str, *, actor_id: str, attempt_id: str,
                          route: AttentionRoute, message_id: str, delivered: bool,
                          evidence_digest: str) -> dict:
        """Trusted host/owner evidence, never user reaction, resolves uncertainty.

        A proven non-delivery is terminal rejected, not automatic permission to
        resend. Evidence must identify the same host destination and message.
        """
        evidence = {"notice_id": notice_id, "attempt_id": attempt_id, "route": asdict(route),
                    "message_id": message_id, "delivered": delivered, "evidence_digest": evidence_digest}
        _require(_id(actor_id) and type(delivered) is bool and isinstance(message_id, str)
                 and message_id.isdecimal() and len(message_id) <= 32
                 and re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_digest) is not None,
                 "Exact destination/message reconciliation evidence is required")
        with self.journal.writer() as session:
            record = session.value["intents"].get(notice_id)
            _require(notice_id.startswith(PREFIX) and isinstance(record, dict), "Notice is unavailable")
            # Reconcile a historical attempt even if its source gate has since
            # resolved or its send TTL expired; never authorize another send.
            event = AttentionEvent(**record["event"])
            _require(asdict(self._grant(event)) == record["route"],
                     "Destination or audience generation changed")
            _require(self.authorize_reconciliation is not None
                     and self.authorize_reconciliation(actor_id, evidence), "Trusted reconciliation denied")
            _require(record.get("attempt_id") == attempt_id and record["route"] == asdict(route),
                     "Reconciliation target changed")
            previous = record.get("reconciliation")
            if previous is not None:
                _require(previous == {**evidence, "actor_id": actor_id}, "Conflicting reconciliation")
                return copy.deepcopy(record)
            _require(record["state"] == "unknown", "Only unknown delivery can be reconciled")
            record.update(state="confirmed" if delivered else "rejected",
                          message_id=message_id if delivered else None,
                          reconciliation={**evidence, "actor_id": actor_id})
            session.value["receipts"][notice_id] = {
                "recorded_at": stamp(self.clock()), "unresolved": False,
                "state": record["state"], "message_id": record["message_id"],
                "attempt_id": attempt_id, "route": asdict(route),
                "reconciliation": copy.deepcopy(record["reconciliation"]),
            }
            session.commit(self.clock())
            return copy.deepcopy(record)

    def reconcile_committed_work(self) -> dict:
        """Runnable supervisor/scanner cycle; Work is never mutated by delivery.

        Isolate denials per notice, so a revoked route cannot starve another
        authorized actionable outcome. Routine progress is intentionally absent.
        """
        admitted = self.scan()
        outcomes = {}
        for key in list(self.journal.read()["intents"]):
            if not key.startswith(PREFIX):
                continue
            try:
                outcomes[key] = self.attempt(key)["state"]
            except AdmissionError:
                outcomes[key] = "denied"
        return {"admitted": admitted, "outcomes": outcomes}
