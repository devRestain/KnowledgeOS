"""C9 Learning and Experience (Blueprint §4.5; CAPABILITIES C9).

Role level: ``record_feedback`` appends rows to the State stream ``feedback``; each role's
``feedback`` tool shows the rows for that role (``Engine._feedback``). Rows never hold
note text, titles, paths, note IDs or digests.

Fabric level: ``signal`` commits a Core ``ExperienceSourceEvent`` that holds an exact
``ExperienceSourceReference`` to one owner State record (kind, id, revision, digest). It
then extracts eligible signals into an ``ExperienceCandidate`` and ``ExperienceOutboxEntry``
in ``experience/<id>.json``. Unverified TeamReview proposals stay local source events:
``routine_progress`` is not Candidate-eligible under Core, and no automatic promotion runs.
Candidates carry references, categories, digests and fixed summaries, never note text.
``deliver`` sends eligible candidates through the authenticated Unix InterOps helper.
Only a correlated durable receiver receipt proves delivery. Unavailable receivers leave
candidates pending; ambiguous sends only query the original receipt and are never re-sent.
Records expire under retention. Hooks log their failures without failing KnowledgeOS.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import timedelta
from typing import Any

from . import interops
from .util import canonical, digest, iso, parse_iso, sha256

log = logging.getLogger("knowledgeos.learning")

CAPABILITY = "C9"
KIND = "experience"
WATCH = "report_watch"
OUTBOX_LOCK = "C9.outbox"
EXTRACTOR_VERSION = "1.0.0"
REDACTION_POLICY = "knowledgeos.references_only.v1"
TERMS = ["agentfabric.core.experience_candidate", "agentfabric.core.run_outcome", "agentfabric.core.run_evaluation"]
OUTCOMES = ("completed", "failed", "timed_out", "aborted", "denied", "recovered", "unknown")
# KnowledgeOS signal -> (Core category, evaluation_state, fixed observation summary)
SIGNALS = {
    "work_unmet": ("failure", "fail", "A Work ended unmet: its result did not satisfy every completion criterion."),
    "work_failed": ("failure", "not_evaluated", "A Work failed visibly and was not retried or rerouted."),
    "work_unknown": ("failure", "unknown", "A Work ended with an unknown outcome and was not re-run."),
    "proposal_rejected": ("human_correction", "fail", "The user rejected a KnowledgeOS proposal."),
    "proposal_partially_applied": ("human_correction", "fail", "The user applied a proposal with some changes unchecked."),
    "undo": ("human_correction", "fail", "The user undid a recorded standardization change."),
    "job_blocked": ("policy_denial", "not_evaluated", "A scheduled job run was refused because the job is paused."),
    "privacy_refusal": ("policy_denial", "not_evaluated", "A request was refused by a note's ai_policy."),
    "teamreview_proposal": ("routine_progress", "not_evaluated",
                            "A Manager proposed an improvement; the proposal itself has not been evaluated."),
}
# Work status -> (signal, Core outcome_state). Unmet keeps completion distinct from acceptance.
WORK_SIGNALS = {"unmet": ("work_unmet", "completed"), "failed": ("work_failed", "failed"),
                "unknown": ("work_unknown", "unknown")}
# KnowledgeOS delivery state -> Core ExperienceOutboxEntry.delivery_state
CORE_STATE = {"pending": "pending", "delivering": "in_flight", "delivered": "admitted", "rejected": "rejected",
              "unknown": "quarantined"}
CORE_NAME = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
MAX_EVIDENCE = 8
TEXT_LIMIT = 200
LIST_LIMIT = 20


# ---------------------------------------------------------------- role feedback


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return value[:TEXT_LIMIT]
    if isinstance(value, list):
        return [_bounded(item) for item in value[:LIST_LIMIT]]
    if isinstance(value, dict):
        return {str(key)[:TEXT_LIMIT]: _bounded(item) for key, item in list(value.items())[:LIST_LIMIT]}
    return value


def record_feedback(
    owner, *, role: str, work_id: str, kind: str, outcome: str,
    change: dict | None = None, detail: dict | None = None,
) -> None:
    """Append one feedback row for ``role``'s own earlier output. Callers keep note text,
    titles, paths, note IDs and digests out of ``change`` and ``detail``; strings are cut."""
    owner.store.append("feedback", {
        "role": role,
        "work_id": work_id,
        "kind": kind,
        "outcome": outcome,
        "change": _bounded(change),
        "detail": _bounded(detail),
        "at": iso(owner.clock.now()),
    })


def feedback_view(owner, query: dict[str, str]) -> list[dict[str, Any]]:
    """``GET /api/feedback[?role=]``: each role's learning record, newest first."""
    role = query.get("role")
    rows = [row for row in owner.store.read_stream("feedback") if not role or row.get("role") == role]
    return list(reversed(rows[-200:]))


# ---------------------------------------------------------------- Experience signals


def _token(text: str) -> str:
    """One Core identifier segment: the text itself when it fits, else a digest of it."""
    return text if TOKEN.fullmatch(text) else "h" + sha256(text.encode("utf-8"))[:24]


def _owner_record(owner, item: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    kind, record_id = item.get("kind"), item.get("id")
    record = owner.store.get(kind, record_id) if isinstance(kind, str) and isinstance(record_id, str) else None
    if record is None:
        raise ValueError(f"no owner record {kind}/{record_id}")
    return kind, record_id, record


def _source_reference(owner, kind: str, record_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Core ``ExperienceSourceReference`` to the exact committed revision of an owner record."""
    content = digest(record)
    return {
        "content_digest": f"sha256:{content}",
        "owner_operation_id": owner.config.operation_id,
        "record_id": f"knowledgeos.{_token(kind)}.{_token(record_id)}",
        "record_kind": "owner_lifecycle_event",
        "source_revision": f"r{content[:16]}",
    }


def _artifact(owner, item: dict[str, Any]) -> dict[str, Any]:
    """Core ``ArtifactReference`` to a committed owner record; no byte-export port exists."""
    kind, record_id, record = _owner_record(owner, item)
    data = canonical(record)
    content = owner.store.put_blob(data)
    return {
        "artifact_id": f"knowledgeos.{_token(kind)}.{_token(record_id)}",
        "availability": "unavailable",
        "classification": "confidential",
        "content_digest": f"sha256:{content}",
        "media_type": "application/json",
        "owner_operation_id": owner.config.operation_id,
        "size_bytes": len(data),
        "source_revision": f"r{content[:16]}",
    }


def signal(
    owner, *, category: str, source: dict, outcome_state: str, evidence: list[dict] | None = None,
) -> str:
    """Commit a source event, returning the candidate ID when eligible or the event ID
    when local-only (unverified TeamReview proposals; no automatic later promotion).

    ``source`` is ``{"kind": <State record kind>, "id": <record id>, "part"?: <token>}`` and
    names an existing record. Its exact revision is retained and digested. ``part`` separates
    observations about one record; ``evidence`` items use ``{kind, id}``. Repeating the same
    signal about the same record revision returns the original ID. Invalid categories,
    outcome states or missing source records raise ``ValueError``.
    """
    if category not in SIGNALS:
        raise ValueError(f"unknown Experience signal {category!r}")
    if outcome_state not in OUTCOMES:
        raise ValueError(f"outcome_state must be one of {', '.join(OUTCOMES)}")
    kind, record_id, record = _owner_record(owner, source)
    reference = _source_reference(owner, kind, record_id, record)
    references: list[dict[str, Any]] = []
    for item in evidence or []:
        found = _artifact(owner, item)
        if found not in references:
            references.append(found)
            if len(references) == MAX_EVIDENCE:
                break
    part = source.get("part")
    key = digest({"signal": category, "source": reference, "part": part})
    candidate_id = f"knowledgeos.candidate.{key[:32]}"
    core_category, evaluation, summary = SIGNALS[category]
    eligible = core_category != "routine_progress"
    event_id = f"knowledgeos.event.{key[:32]}"
    experience_id = candidate_id if eligible else event_id
    if kind == "works":
        spec = record.get("workspec") or {}
        summary += f" Capability {spec.get('capability')}, command {spec.get('command')}."
    now = iso(owner.clock.now())
    event: dict[str, Any] = {
        "category": core_category,
        "classification": "internal",
        "evaluation_state": evaluation,
        "event_id": event_id,
        "evidence_references": references[:MAX_EVIDENCE],
        "occurred_at": now,
        "outcome_state": outcome_state,
        "owner_operation_id": owner.config.operation_id,
        "semantic_term_ids": TERMS,
        "source_record": reference,
    }
    if CORE_NAME.fullmatch(owner.config.instance_id) and len(owner.config.instance_id) <= 96:
        event["source_instance_id"] = owner.config.instance_id
    if kind == "works" and CORE_NAME.fullmatch(record_id) and len(record_id) <= 96:
        event["source_run_id"] = record_id
    entry = {
        "experience_id": experience_id,
        "event_id": event_id,
        "candidate_id": candidate_id if eligible else None,
        "signal": category,
        "source": {"kind": kind, "id": record_id, **({"part": part} if part is not None else {})},
        "key": key,
        "source_blob": owner.store.put_json_blob(record),
        "summary": summary,
        "created_at": now,
        "updated_at": now,
        "delivery": "pending" if eligible else "local_only",
        "source_event": event,
        "candidate": None,
        "outbox": None,
        "receipt": None,
        "interops": None,
        "unknown_id": None,
    }
    with owner.store.lock(OUTBOX_LOCK):  # the source event is committed before extraction
        if owner.store.get(KIND, experience_id) is not None:
            return experience_id
        if eligible and any(item.get("kind") == "experience_delivery"
                            and item.get("ref", {}).get("candidate_id") == candidate_id
                            for item in owner.store.list("unknowns")):
            return candidate_id  # unknown delivery tombstones survive bounded-outbox eviction/expiry
        _evict(owner)
        owner.store.create(KIND, experience_id, entry)
    if eligible:
        try:
            _extract(owner, candidate_id)
        except Exception:  # retried by the next delivery pass
            log.exception("Experience extraction for %s deferred", candidate_id)
    owner.cache("C9.wake", threading.Event).set()
    return experience_id


def _extract(owner, candidate_id: str) -> None:
    """Build the Core candidate and outbox entry from the committed source event."""
    from .core_records import pins

    pin = pins(owner.config.root)
    retention = timedelta(days=float(owner.setting(CAPABILITY, "retention_days", 30)))
    recipient = owner.setting(CAPABILITY, "recipient_operation_id", "hermestrace")

    def change(entry: dict[str, Any]) -> None:
        if entry["delivery"] == "local_only" or entry["candidate"] is not None:
            return
        event, key = entry["source_event"], entry["key"]
        event_digest = digest(event)
        event_reference = {
            "content_digest": "sha256:" + event_digest,
            "owner_operation_id": event["owner_operation_id"],
            "record_id": event["event_id"],
            "record_kind": "owner_lifecycle_event",
            "source_revision": "r" + event_digest[:16],
        }
        candidate: dict[str, Any] = {
            "candidate_id": candidate_id,
            "category": event["category"],
            "classification": "internal",
            "core_digest": pin["core_digest"],
            "core_version": pin["core_version"],
            "evaluation_state": event["evaluation_state"],
            "evidence_references": [],  # owner evidence has no export port; retain it only on the local event
            "extractor_version": EXTRACTOR_VERSION,
            "idempotency_key": f"knowledgeos:experience:{key}",
            "observation_summary": entry["summary"],
            "occurred_at": event["occurred_at"],
            "outcome_state": event["outcome_state"],
            "owner_operation_id": event["owner_operation_id"],
            "redaction_policy_version": REDACTION_POLICY,
            "semantic_digest": pin["semantic_digest"],
            "semantic_term_ids": TERMS,
            "semantic_version": pin["semantic_version"],
            "source_event_id": event["event_id"],
            "source_record": event_reference,
            "source_revision": event_reference["source_revision"],
            **{name: event[name] for name in ("source_instance_id", "source_run_id") if name in event},
        }
        candidate["candidate_fingerprint"] = "sha256:" + digest(
            {name: value for name, value in candidate.items() if name not in ("candidate_id", "idempotency_key")}
        )
        expires = iso(parse_iso(entry["created_at"]) + retention)
        entry["candidate"] = candidate
        entry["outbox"] = {
            "attempt_count": 0,
            "candidate_id": candidate_id,
            "created_at": entry["created_at"],
            "delivery_state": CORE_STATE[entry["delivery"]],
            "expires_at": expires,
            "idempotency_key": candidate["idempotency_key"],
            "next_attempt_at": entry["created_at"],
            "outbox_id": f"knowledgeos.outbox.{key[:32]}",
            "owner_operation_id": event["owner_operation_id"],
            "payload_digest": "sha256:" + sha256(canonical(candidate)),
            "priority": 3,
            "recipient_operation_id": recipient,
            "retention_until": expires,
        }

    owner.store.update(KIND, candidate_id, change)


def _remove(owner, kind: str, record_id: str) -> None:
    """Drop one State record (outbox eviction, retention expiry, finished report watch)."""
    with owner.store.lock(f"{kind}/{record_id}"):
        (owner.store.root / kind / f"{record_id}.json").unlink(missing_ok=True)


def _evict(owner) -> None:
    """Keep at most ``C9.outbox_limit`` records, counting the one about to be added: the
    oldest settled records go first, then the oldest pending ones. A send evicted while
    in flight keeps an unknown record and can never be sent again. Called under OUTBOX_LOCK."""
    limit = max(1, int(owner.setting(CAPABILITY, "outbox_limit", 4)))
    entries = owner.store.list(KIND)
    excess = len(entries) - limit + 1
    if excess <= 0:
        return
    rank = {"delivered": 0, "rejected": 0, "local_only": 0, "unknown": 1, "pending": 2, "delivering": 3}
    victims = sorted(entries,
                     key=lambda e: (rank[e["delivery"]], e["created_at"], e["experience_id"]))
    for entry in victims[:excess]:
        log.info("Experience capacity reached: evicting %s (%s)", entry["experience_id"], entry["delivery"])
        if entry["delivery"] == "delivering":
            _unknown_delivery(owner, entry, reason="outbox_capacity")
        _remove(owner, KIND, entry["experience_id"])


# ---------------------------------------------------------------- delivery to HermesTrace


def _set_delivery(owner, candidate_id: str, delivery: str, entry: dict | None = None, **outbox: Any) -> None:
    now = iso(owner.clock.now())

    def change(record: dict[str, Any]) -> None:
        record.update(delivery=delivery, updated_at=now, **(entry or {}))
        record["outbox"].pop("last_error_code", None)
        record["outbox"].update(delivery_state=CORE_STATE[delivery], **outbox)

    with owner.store.lock(f"{KIND}/{candidate_id}"):
        if owner.store.get(KIND, candidate_id) is not None:
            owner.store.update(KIND, candidate_id, change)


def _unknown_delivery(owner, entry: dict[str, Any], **detail: Any) -> str:
    if entry.get("unknown_id"):
        return entry["unknown_id"]
    with owner.store.lock("C9.unknowns"):
        for record in owner.store.list("unknowns"):
            if record.get("kind") == "experience_delivery" and record.get("ref", {}).get("candidate_id") == entry["candidate_id"]:
                return record["unknown_id"]
        return owner.add_unknown("experience_delivery", {
            "candidate_id": entry["candidate_id"],
            "request_digest": (entry.get("interops") or {}).get("request_digest"),
            "idempotency_key": entry["outbox"]["idempotency_key"],
            "payload_digest": entry["outbox"]["payload_digest"],
            **detail,
        })


def _send(owner, entry: dict[str, Any]) -> str:
    """Persist the exact envelope before dispatch; unknown sends only query that envelope's receipt."""
    candidate_id = entry["candidate_id"]
    retry_at = iso(owner.clock.now() + timedelta(seconds=float(owner.setting(CAPABILITY, "delivery_interval_seconds", 60))))
    with owner.store.lock(OUTBOX_LOCK):
        entry = owner.store.get(KIND, candidate_id)
        if entry is None:
            return "pending"
        if entry["interops"] is None:
            prepared = interops.prepare(owner, entry)
            owner.store.update(KIND, candidate_id, lambda record: record.update(interops=prepared))
            entry["interops"] = prepared
        if entry["delivery"] == "pending":
            _set_delivery(owner, candidate_id, "delivering")
    # The initial snapshot remains pending so the helper may admit once. Stored delivering
    # and recovered unknown snapshots are query-only; neither can replay admission.
    result = interops.deliver(owner, entry)
    state = result["delivery"]
    if entry["delivery"] != "pending" and state == "pending":
        state = "unknown"
    fields = {"delivery_error_code": result.get("last_error_code")}
    outbox = {"attempt_count": 0 if state == "pending" else 1}
    if state == "unknown":
        fields["unknown_id"] = _unknown_delivery(owner, entry)
        outbox["last_error_code"] = "OUTCOME_UNKNOWN"
    elif state == "pending":
        outbox.update(last_error_code="UNAVAILABLE", next_attempt_at=retry_at)
    elif result["receipt"] is not None:
        fields["receipt"] = result["receipt"]
        outbox["receipt_id"] = result["receipt"]["receipt_id"]
    _set_delivery(owner, candidate_id, state, fields, **outbox)
    return state


def deliver(owner) -> dict[str, int]:
    """Expire retained records, extract eligible events, send pending candidates or query
    uncertain admissions. An unset or unreachable receiver never blocks KnowledgeOS."""
    counts = dict.fromkeys(("delivered", "rejected", "pending", "unknown", "expired"), 0)
    with owner.cache("C9.pass", threading.Lock):
        now = owner.clock.now()
        for entry in sorted(owner.store.list(KIND), key=lambda e: (e["created_at"], e["experience_id"])):
            candidate_id = entry["candidate_id"]
            try:
                outbox = entry["outbox"]
                expires = (parse_iso(outbox["retention_until"]) if outbox else
                           parse_iso(entry["created_at"]) + timedelta(days=float(owner.setting(CAPABILITY, "retention_days", 30))))
                if expires <= now:
                    _remove(owner, KIND, entry["experience_id"])
                    counts["expired"] += 1
                    continue
                if entry["delivery"] == "local_only":
                    continue
                if entry["candidate"] is None:
                    _extract(owner, candidate_id)
                    entry = owner.store.get(KIND, candidate_id)
                outbox = entry["outbox"]
                if entry["delivery"] not in ("pending", "delivering", "unknown") \
                        or parse_iso(outbox["next_attempt_at"]) > now or not interops.configured(owner):
                    continue
                counts[_send(owner, entry)] += 1
            except Exception:  # an evicted record or a deferred extraction; the next pass retries
                log.exception("Experience delivery pass skipped %s", candidate_id)
    return counts


def experience_view(owner, query: dict[str, str]) -> list[dict[str, Any]]:
    """``GET /api/experience[?delivery=]``: candidates and local-only source events, newest first."""
    delivery = query.get("delivery")
    entries = [e for e in owner.store.list(KIND) if not delivery or e["delivery"] == delivery]
    return sorted(entries, key=lambda e: (e["created_at"], e["experience_id"]), reverse=True)


def experience_item(owner, experience_id: str) -> dict[str, Any] | None:
    return owner.store.get(KIND, experience_id)


# ---------------------------------------------------------------- synthesizer report feedback


def _watch_report(owner, work: dict[str, Any]) -> None:
    """Remember a created report so later edits, promotion or deletion become feedback."""
    path = (work.get("outcome") or {}).get("report")
    note = owner.vault.read(path) if isinstance(path, str) else None
    if note is None:
        return
    owner.store.create(WATCH, work["work_id"], {
        "work_id": work["work_id"], "path": path, "note_id": note.note_id, "sha": note.sha,
        "state": "created", "observed_at": iso(owner.clock.now()),
    })


def observe_reports(owner) -> None:
    """Report edits (content changed), promotion (``type`` no longer ``report``) and deletion
    become ``synthesizer`` feedback once each. Idle passes write nothing."""
    watches = owner.store.list(WATCH)
    if not watches:
        return
    docs = owner.index.refresh()
    for watch in watches:
        doc = docs.get(watch["path"])
        if doc is not None and doc.policy == "deny":
            continue
        if doc is None and watch["note_id"]:
            matches = [d for d in docs.values() if d.note_id == watch["note_id"]]
            if len(matches) > 1:
                continue
            doc = matches[0] if matches else None
        if doc is None:
            state = "deleted"
        elif doc.policy == "deny" or doc.sha is None:
            continue
        elif doc.type != "report":
            state = "promoted"
        elif doc.sha != watch["sha"]:
            state = "edited"
        else:
            continue
        if state == watch["state"]:
            continue
        record_feedback(owner, role="synthesizer", work_id=watch["work_id"], kind="report", outcome=state)
        if state == "edited":
            owner.store.update(WATCH, watch["work_id"], lambda r, state=state: r.update(state=state, observed_at=iso(owner.clock.now())))
        else:
            _remove(owner, WATCH, watch["work_id"])


# ---------------------------------------------------------------- hooks (never raise)


def on_work_status(owner, work: dict[str, Any]) -> None:
    """Engine hook after a Work status change: end-state signals and report watching."""
    try:
        status = work["status"]
        if status in WORK_SIGNALS:
            category, outcome = WORK_SIGNALS[status]
            signal(owner, category=category, source={"kind": "works", "id": work["work_id"]}, outcome_state=outcome)
        elif status == "completed":
            _watch_report(owner, work)
    except Exception:
        log.exception("learning hook failed for Work %s", work.get("work_id"))


def on_assessment(owner, work_id: str, manager: str, segment: int, steps: list[str], assessment: dict) -> None:
    """The Manager's Graph was assessed: feedback ``assessment`` for that Manager."""
    try:
        record_feedback(owner, role=manager, work_id=work_id, kind="assessment", outcome=assessment["status"],
                        change={"steps": list(steps)}, detail={"segment": segment, "unmet": assessment["unmet"]})
    except Exception:
        log.exception("assessment feedback failed for Work %s", work_id)


def on_team_review(owner, work_id: str, manager: str, review: dict) -> None:
    """Manager feedback and one local-only ExperienceSourceEvent per unverified proposal."""
    try:
        ratings = {name: value["rating"] for name, value in review.get("ratings", {}).items()}
        record_feedback(owner, role=manager, work_id=work_id, kind="team_review", outcome=review["status"],
                        detail={"ratings": ratings, "proposals": len(review.get("proposals", []))})
        for index, _ in enumerate(review.get("proposals", [])):
            signal(owner, category="teamreview_proposal", source={"kind": "works", "id": work_id, "part": f"p{index}"},
                   outcome_state="completed")
    except Exception:
        log.exception("TeamReview learning failed for Work %s", work_id)


def on_review(owner, work_id: str, decision: str) -> None:
    """C6 hook: a user ``rejected`` the result of ``work_id``. When the model EvalOfficer
    rated that result satisfied, it gets ``eval_overruled`` feedback. Otherwise nothing."""
    try:
        work = owner.get_work(work_id) if decision == "rejected" else None
        if work is None:
            return
        for segment in reversed(work["segments"]):
            if segment.get("assessment") and (segment.get("evaluation") or {}).get("judgments"):
                break
        else:
            return
        if segment["assessment"]["status"] != "satisfied":
            return
        rated = sorted({j["criterion"] for j in segment["evaluation"]["judgments"]
                        if j.get("runtime") == "model" and j["verdict"] == "satisfied"})
        if rated:
            role = (work["workspec"].get("evaluation") or {}).get("role", "eval_officer")
            record_feedback(owner, role=role, work_id=work_id, kind="eval_overruled", outcome="rejected",
                            detail={"criteria": rated})
    except Exception:
        log.exception("review learning failed for Work %s", work_id)


def on_refusal(owner, request_id: str, reason: str) -> None:
    """Owner hook for a refused request: a refusal by ``ai_policy`` is a privacy refusal."""
    try:
        if re.search(r"(?:^|: )ai_policy deny\b", reason or ""):
            signal(owner, category="privacy_refusal", source={"kind": "requests", "id": request_id},
                   outcome_state="denied")
    except Exception:
        log.exception("privacy refusal signal failed for %s", request_id)


def on_job_blocked(owner, job: str) -> None:
    """C7 hook: a run of a paused job was refused (one signal per paused-state revision)."""
    try:
        signal(owner, category="job_blocked", source={"kind": "jobs", "id": job}, outcome_state="denied")
    except Exception:
        log.exception("blocked job signal failed for %s", job)


# ---------------------------------------------------------------- worker


def start(owner) -> None:
    """Startup recovery and the periodic outbox/report observer (stopped by ``owner.close``)."""
    for entry in owner.store.list(KIND):
        if entry["delivery"] == "delivering":  # stopped during a send: unknown, never re-sent
            unknown_id = _unknown_delivery(owner, entry)
            _set_delivery(owner, entry["candidate_id"], "unknown", {"unknown_id": unknown_id},
                          last_error_code="OUTCOME_UNKNOWN")
    stop = threading.Event()
    wake = owner.cache("C9.wake", threading.Event)
    interval = float(owner.setting(CAPABILITY, "delivery_interval_seconds", 60))

    def loop() -> None:
        while not stop.is_set():
            try:
                deliver(owner)
                observe_reports(owner)
            except Exception:
                log.exception("Experience pass failed")
            wake.wait(interval)
            wake.clear()

    thread = threading.Thread(target=loop, name="kos-experience", daemon=True)
    thread.start()

    def close() -> None:
        stop.set()
        wake.set()
        thread.join()

    owner.on_close(close)
