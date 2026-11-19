"""C11 user surfaces admitted through the shared ExOps request path: free text (mapped by the
model Director, ``Owner.director.free_text``) and the console's administration actions."""
from __future__ import annotations

import json
import time

from .. import core_records
from ..store import NAME_RE, StoreError
from ..util import iso
from . import Done, Refused

CAPABILITY = "C11"


def register(reg):
    reg.command("free_text", lambda adm: adm.owner.director.free_text(adm))
    reg.command("work.cancel", cancel)
    reg.command("outcome.reconcile", reconcile)
    reg.command("teamreview.considered", considered)
    reg.route("GET", "/api/works/{work_id}/core", work_core)


def cancel(adm):
    owner = adm.owner
    work = adm.work(adm.params["work_id"])
    work_id = work["work_id"]
    if work["status"] in ("completed", "unmet", "failed", "cancelled", "unknown"):
        raise Refused(f"Work {work_id} is already {work['status']}")
    action_id = owner.record_action(work_id, adm.request_id, "work.cancel", "received")
    try:
        status = owner.cancel_work(work_id)
        deadline = time.monotonic() + max(0, min(float(adm.setting("cancel_observation_seconds", 5)), 30))
        while status == "cancelling":
            current = owner.get_work(work_id)
            observed = current["status"] if current else "unknown"
            if observed in ("completed", "unmet", "failed", "cancelled", "unknown"):
                status = observed
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    except Refused as refusal:
        owner.record_action(work_id, adm.request_id, "work.cancel", "not_applied", action_id)
        return Done(status="refused", notice=refusal.reason, work_id=work_id,
                    outcome={"state": "not_applied"})
    except (OSError, StoreError, json.JSONDecodeError, UnicodeDecodeError):
        status = "unknown"
    state = "applied" if status == "cancelled" else "unknown" if status in ("unknown", "cancelling") else "not_applied"
    owner.record_action(work_id, adm.request_id, "work.cancel", state, action_id)
    outcome = {"work_id": work_id, "status": status, "state": state, "action_id": action_id}
    if state == "unknown":
        outcome["unknown_id"] = owner.add_unknown("cancellation", {"work_id": work_id, "request_id": adm.request_id, "action_id": action_id})
    return Done(status="unknown" if state == "unknown" else "completed", outcome=outcome,
                work_id=work_id, notice="Work cancelled; nothing more will run" if status == "cancelled" else f"Cancellation {state}: Work {status}")


def reconcile(adm):
    owner = adm.owner
    unknown_id = adm.params["unknown_id"]
    if not NAME_RE.fullmatch(unknown_id) or ".." in unknown_id:
        raise Refused("invalid unknown outcome ID")
    resolution = adm.params["resolution"]
    with owner.store.lock(f"unknowns/{unknown_id}"):
        unknown = owner.store.get("unknowns", unknown_id)
        if unknown is None:
            raise Refused("no such unknown outcome")
        if unknown.get("resolution") is not None:
            raise Refused("outcome has already been reconciled")
        target_work = unknown.get("ref", {}).get("work_id")
        work_id = target_work if isinstance(target_work, str) and owner.get_work(target_work) else None
        action_id = owner.record_action(work_id, adm.request_id, "outcome.reconcile", "received")
        try:
            owner.store.update("unknowns", unknown_id, lambda record: record.update(
                resolution=resolution, resolved_at=iso(owner.clock.now()), resolved_by=adm.actor,
                request_id=adm.request_id, action_id=action_id))
        except (OSError, StoreError, json.JSONDecodeError, UnicodeDecodeError):
            owner.record_action(work_id, adm.request_id, "outcome.reconcile", "unknown", action_id)
            return Done(status="unknown", outcome={"unknown_id": unknown_id, "state": "unknown", "action_id": action_id},
                        notice="Reconciliation outcome could not be observed")
        owner.record_action(work_id, adm.request_id, "outcome.reconcile", "applied", action_id)
    # This records the human observation, never repeats the original effect or rewrites its history.
    return Done(outcome={"unknown_id": unknown_id, "resolution": resolution, "state": "applied", "action_id": action_id},
                notice="Unknown outcome reconciled")


def considered(adm):
    owner = adm.owner
    work = adm.work(adm.params["work_id"])
    work_id = work["work_id"]
    index = adm.params["proposal_index"]

    def change(current):
        current["team_review"]["proposals"][index].update(considered=True, considered_at=iso(owner.clock.now()))
        current["updated_at"] = iso(owner.clock.now())

    with owner.store.lock(f"works/{work_id}"):
        review = (owner.get_work(work_id) or {}).get("team_review") or {}
        proposals = review.get("proposals", [])
        if review.get("status") != "recorded" or index < 0 or index >= len(proposals) or not isinstance(proposals[index], dict):
            raise Refused("no recorded TeamReview proposal at that index")
        action_id = owner.record_action(work_id, adm.request_id, "teamreview.considered", "received")
        try:
            owner.update_work(work_id, change)
        except (OSError, StoreError, json.JSONDecodeError, UnicodeDecodeError):
            owner.record_action(work_id, adm.request_id, "teamreview.considered", "unknown", action_id)
            unknown_id = owner.add_unknown("action", {"work_id": work_id, "request_id": adm.request_id, "action_id": action_id})
            return Done(status="unknown", outcome={"unknown_id": unknown_id, "state": "unknown"}, work_id=work_id)
        owner.record_action(work_id, adm.request_id, "teamreview.considered", "applied", action_id)
    return Done(outcome={"work_id": work_id, "proposal_index": index, "considered": True,
                         "state": "applied", "action_id": action_id}, work_id=work_id,
                notice="TeamReview proposal marked considered; no role or catalog changed")


def work_core(owner, req):
    """Read-only Core records compiled from one Work record (Blueprint §7)."""
    work = owner.get_work(req.params["work_id"])
    if work is None:
        return 404, {"error": "not found"}
    return core_records.compile_work(owner, work)
