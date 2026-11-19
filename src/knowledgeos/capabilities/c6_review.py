"""Human review decisions, exact-preimage effects and C2 undo; never repeat unknown effects."""
from __future__ import annotations

import logging

from .. import proposals
from ..capabilities import Done
from ..engine import ActionEffects
from ..notes import FrontmatterEditor
from ..policy import effective
from ..standardize import LINK_KEYS
from ..util import digest, iso
from ..vault import PROPOSALS_DIR, RESERVED_REQUESTS, Vault, VaultPathError
from .c5_stewardship import target_path

CAPABILITY = "C6"
log = logging.getLogger("knowledgeos.review")
ALLOWED_KEYS = {"type", "status", "title", "tags", "aliases", *LINK_KEYS}


def register(reg):
    reg.command("review.apply", apply)
    reg.command("review.reject", reject)
    reg.command("review.undo", undo)


def _lock(adm):
    try:
        path = adm.owner.vault.normalize(adm.params["path"])
    except VaultPathError:
        adm.refuse("invalid note path")
    return adm.owner.store.lock("review/" + digest(path))


class _Effects(ActionEffects):
    """Vault proposals remain applicable even after their originating State is lost."""
    def __init__(self, adm, work_id, action_id):
        work = adm.owner.get_work(work_id)
        self.retained = work is not None
        if not work:
            work = {"work_id": work_id, "workspec": {"capability": CAPABILITY, "mode": "review", "params": {}, "inputs": []}}
        super().__init__(adm.owner, work, adm.request_id, adm.actor, action_id)

    def _note_effect(self, op, path, state, **extra):
        if self.retained:
            super()._note_effect(op, path, state, **extra)


def _note(adm):
    try:
        path = adm.owner.vault.normalize(adm.params["path"])
    except VaultPathError:
        adm.refuse("invalid note path")
    if Vault.ignored(path) or Vault.system(path) or path.startswith(RESERVED_REQUESTS):
        adm.refuse("this path cannot be changed by review")
    note = adm.owner.vault.read(path)
    if note is None:
        adm.refuse("note no longer exists; read the original request receipt for its outcome")
    if effective(note, adm.owner.config.default_policy) == "deny":
        adm.refuse("ai_policy deny")
    if not note.valid or note.conflict:
        adm.refuse("invalid or conflict-marked note")
    if note.sha != adm.params["digest"]:
        adm.refuse("note changed after the client read it; read it again before deciding")
    return note


def _proposal(adm):
    note = _note(adm)
    if not note.path.startswith(PROPOSALS_DIR + "/"):
        adm.refuse("review applies only to a proposal in the review queue")
    proposal = proposals.parse(note)
    if proposal is None:
        adm.refuse("invalid proposal or ambiguous checklist")
    return note, proposal


def _start(adm, work_id):
    # These commands settle in admission. Persist 'running' before effects so recovery cannot re-admit them.
    adm.owner.store.update("requests", adm.request_id, lambda r: r.update(status="running"))
    retained = work_id if adm.owner.get_work(work_id) else None
    return adm.owner.record_action(retained, adm.request_id, adm.command, "received")


def _finish_action(adm, work_id, action_id, done):
    retained = work_id if adm.owner.get_work(work_id) else None
    state = done.outcome["state"]
    adm.owner.record_action(retained, adm.request_id, adm.command, state, action_id)
    done.work_id = retained
    done.outcome["action_id"] = action_id
    return done


def _unknown(adm, work_id, action_id):
    unknown = adm.owner.add_unknown("action", {"request_id": adm.request_id, "action_id": action_id, "work_id": work_id})
    return _finish_action(adm, work_id, action_id, Done(status="unknown", outcome={"state": "unknown", "unknown_id": unknown},
                         notice="Review outcome unknown; inspect this receipt and reconcile before any new action"))


def _feedback_change(change):
    if change["op"] == "move":
        return {"op": "move", "to": change["to"]}
    values = change.get("values")
    if values is not None:
        return {"keys": list(values), "values": {key: value for key, value in values.items() if key in ("type", "status")}}
    key = change["key"]
    if key in LINK_KEYS:
        return {"key": key, "count": len(change.get("value", []))}
    if key in ("title", "aliases"):
        return {"key": key}
    return {"key": key, "value": change.get("value")}


def _learning(adm, proposal, action_id, decision):
    from .. import learning
    for change in proposal.changes:
        role = change.get("role", "stewardship")
        outcome = "rejected" if decision == "rejected" else "applied" if change["change_id"] in proposal.checked else "unchecked"
        learning.record_feedback(adm.owner, role=role, work_id=proposal.work_id, kind="proposal_change",
                                 outcome=outcome, change=_feedback_change(change))
    if decision == "rejected":
        learning.on_review(adm.owner, proposal.work_id, "rejected")
        learning.signal(adm.owner, category="proposal_rejected", source={"kind": "actions", "id": action_id}, outcome_state="denied")
    elif any(change["change_id"] not in proposal.checked for change in proposal.changes):
        learning.signal(adm.owner, category="proposal_partially_applied", source={"kind": "actions", "id": action_id}, outcome_state="completed")


def _learn_safely(fn, *args):
    try:
        fn(*args)
    except Exception:
        # The human effect is already committed; learning cannot change its recorded outcome.
        log.exception("review feedback could not be recorded")


def _mark_record(owner, proposal, **fields):
    record = owner.store.get("proposals", proposal.proposal_id)
    if record:
        owner.store.update("proposals", proposal.proposal_id, lambda r: r.update(**fields))


def _stale(adm, effects, note, proposal):
    editor = FrontmatterEditor(note)
    editor.set("status", "stale")
    editor.set("kos_stale_at", iso(adm.owner.clock.now()))
    raw = editor.render()
    marked = effects.replace(note.path, note.sha, raw)
    if marked:
        from ..util import sha256
        _mark_record(adm.owner, proposal, status="stale", stale_at=iso(adm.owner.clock.now()), sha=sha256(raw))
    return Done(status="unmet", outcome={"state": "not_applied", "accepted": True, "proposal": note.path, "stale": marked},
                notice="Accepted, not applied: target changed or disappeared. Request organize again to regenerate; reject a user-edited stale proposal first if needed.")


def _validate_changes(adm, proposal):
    for change in proposal.changes:
        if change["op"] == "move":
            folder = change["to"]
            if Vault.ignored(folder + "/note.md") or Vault.system(folder + "/") or Vault.review(folder + "/") or (folder + "/").startswith(RESERVED_REQUESTS):
                adm.refuse("proposal cannot move a note into a reserved or ignored folder")
        elif set(change.get("values", {change.get("key"): change.get("value")})) - ALLOWED_KEYS:
            adm.refuse("proposal changes properties outside review's admitted scope")


def apply(adm):
    with _lock(adm):
        note, proposal = _proposal(adm)
        _validate_changes(adm, proposal)
        identity = proposal.target_id or proposal.target_path
        with adm.owner.store.lock("proposal-target/" + digest(identity)):
            action_id = _start(adm, proposal.work_id)
            effects = _Effects(adm, proposal.work_id, action_id)
            try:
                done = _apply(adm, effects, note, proposal)
            except Exception:
                log.exception("apply outcome unknown")
                return _unknown(adm, proposal.work_id, action_id)
            done = _finish_action(adm, proposal.work_id, action_id, done)
            if done.outcome["state"] == "applied":
                _learn_safely(_learning, adm, proposal, action_id, "applied")
            return done


def _apply(adm, effects, note, proposal):
    path = target_path(adm.owner, proposal)
    target = adm.owner.vault.read(path) if path else None
    if target is None or target.sha != proposal.target_sha or effective(target, adm.owner.config.default_policy) == "deny":
        return _stale(adm, effects, note, proposal)
    if not target.valid or target.conflict or Vault.ignored(path) or Vault.system(path) or Vault.review(path) or path.startswith(RESERVED_REQUESTS):
        return Done(status="refused", outcome={"state": "not_applied"}, notice="Target is invalid, conflict-marked or reserved; nothing applied")
    raw, destination = proposals.apply(target, proposal.changes, proposal.checked)
    if destination:
        if adm.owner.vault.exists(destination):
            return Done(status="unmet", outcome={"state": "not_applied"}, notice="Move destination already exists; nothing applied")
        written = effects.move(path, proposal.target_sha, destination, raw) is not None
    else:
        # Even an unchecked/no-op decision must compare the preimage at the write boundary.
        written = effects.replace(path, proposal.target_sha, raw)
    if not written:
        return _stale(adm, effects, note, proposal)
    deleted = effects.delete_own(note.path, note.sha)
    _mark_record(adm.owner, proposal, status="applied", applied_at=iso(adm.owner.clock.now()))
    return Done(outcome={"state": "applied", "target": destination or path,
                         "applied": [c["change_id"] for c in proposal.changes if c["change_id"] in proposal.checked],
                         "unchecked": [c["change_id"] for c in proposal.changes if c["change_id"] not in proposal.checked],
                         "proposal_deleted": deleted},
                notice="Checked changes applied" + ("; proposal deleted" if deleted else "; proposal changed during apply and was not deleted; do not apply it again"))


def reject(adm):
    with _lock(adm):
        note, proposal = _proposal(adm)
        action_id = _start(adm, proposal.work_id)
        try:
            deleted = _Effects(adm, proposal.work_id, action_id).delete_own(note.path, note.sha)
            if deleted:
                _mark_record(adm.owner, proposal, status="rejected", rejected_at=iso(adm.owner.clock.now()))
        except Exception:
            log.exception("rejection effect outcome unknown")
            return _unknown(adm, proposal.work_id, action_id)
        done = _finish_action(adm, proposal.work_id, action_id,
                              Done(status="completed" if deleted else "unmet", outcome={"state": "applied" if deleted else "not_applied", "rejected": deleted},
                                   notice="Proposal rejected and deleted" if deleted else "Proposal changed; rejection recorded but deletion not applied"))
        if deleted:
            _learn_safely(_learning, adm, proposal, action_id, "rejected")
        return done


def undo(adm):
    with _lock(adm):
        note = _note(adm)
        if Vault.review(note.path) or note.type in ("home", "system", "proposal", "report"):
            adm.refuse("this note has no undoable standardization")
        matches = [row for row in adm.owner.store.list("changes") if row.get("kind") in ("manual", "batch", "autofix")
                   and (row["path"] == note.path or (note.note_id and row.get("note_id") == note.note_id))]
        if not matches:
            adm.refuse("No standardization change remains in State. Use Git history.")
        # Clock timestamps have second precision; effect-journal order breaks same-second ties.
        order = {(row.get("request_id"), row.get("result")): index
                 for index, row in enumerate(adm.owner.store.read_stream("effects"))
                 if row.get("kind") == "outcome" and row.get("state") == "applied"}
        change = max(matches, key=lambda row: (row["at"], order.get((row["request_id"], row["result_sha"]), -1)))
        work_id = change["work_id"]
        action_id = _start(adm, work_id)
        try:
            raw = adm.owner.store.get_blob(change["preimage_blob"])
            if change.get("undone_at") or note.sha != change["result_sha"] or raw is None:
                return _finish_action(adm, work_id, action_id, Done(status="refused", outcome={"state": "not_applied"},
                                      notice="Cannot undo: the note changed, the change was undone, or its preimage is unavailable. Use Git history."))
            if not _Effects(adm, work_id, action_id).replace(note.path, change["result_sha"], raw):
                return _finish_action(adm, work_id, action_id, Done(status="refused", outcome={"state": "not_applied"}, notice="Note changed; nothing undone. Use Git history."))
            adm.owner.store.update("changes", change["change_id"], lambda row: row.update(undone_at=iso(adm.owner.clock.now()), undo_request_id=adm.request_id))
            from .c2_standardize import _advance
            _advance(adm.owner, {note.path: change["preimage_sha"]})
        except Exception:
            log.exception("undo effect outcome unknown")
            return _unknown(adm, work_id, action_id)
        done = _finish_action(adm, work_id, action_id, Done(outcome={"state": "applied", "change_id": change["change_id"], "target": note.path}, notice="Last standardization undone"))
        def learn():
            from ..learning import signal
            signal(adm.owner, category="undo", source={"kind": "actions", "id": action_id}, outcome_state="completed")
        _learn_safely(learn)
        return done
