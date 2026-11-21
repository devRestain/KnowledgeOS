"""Stewardship: attention, lifecycle proposals (static rules plus System One status judgments), proposal expiry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .. import proposals, system_one
from ..capabilities import Done, Registry
from ..notes import FrontmatterEditor
from ..policy import approved, effective, strictest
from ..standardize import path_skip_reason
from ..system_one import SystemOneUnavailable
from ..util import digest, iso
from ..vault import PROPOSALS_DIR
from .c7_jobs import jobs_view

CAPABILITY = "C5"
OFFICER = "StewardshipOfficer"
STATIC_RULES = (("project", "done"), ("idea", "dropped"))
SEEN = "lifecycle_seen"  # State record kind: {path, sha, work, at} of the note content last asked of System One
log = logging.getLogger("knowledgeos.stewardship")


@dataclass(frozen=True)
class Judgment:
    """A status transition that needs reading the note: a System One predicate decides it."""

    question: str
    target: str
    claim: str
    instructions: str


JUDGMENTS: dict[tuple[str, str], Judgment] = {}
for _type, _statuses, _judgment in (
    ("question", ("open",), Judgment(
        "answered", "answered", "the note already answers its question",
        "The note is a question the author wants answered. Does the note itself already contain an "
        "answer to that question, stated in its body?")),
    ("project", ("planned", "active", "paused"), Judgment(
        "finished", "done", "the project is finished",
        "The note describes a project. Does the note state that the project is finished, completed "
        "or delivered?")),
    ("idea", ("seed", "developing", "mature"), Judgment(
        "abandoned", "dropped", "the author abandoned the idea",
        "The note describes an idea. Does the note state that its author abandoned the idea or decided "
        "not to pursue it?")),
):
    for _status in _statuses:
        JUDGMENTS[(_type, _status)] = _judgment


def register(reg: Registry) -> None:
    reg.officer(OFFICER, officer_step, finish)
    reg.view("attention", lambda owner, query: attention(owner))


def _eligible(doc) -> bool:
    return doc.policy != "deny" and doc.valid and not doc.conflict and not path_skip_reason(doc.path)


def _reserved(owner) -> tuple[set[str], set[str]]:
    """Target paths and IDs that an open or stale proposal already reserves."""
    paths: set[str] = set()
    ids: set[str] = set()
    for path in owner.vault.iter_paths(PROPOSALS_DIR):
        note = owner.vault.read(path)
        if note is not None and note.type == "proposal" and note.status in ("open", "stale"):
            paths.add(note.data.get("target_path"))
            ids.add(note.data.get("target_id"))
    return paths, ids


def _is_reserved(reserved: tuple[set[str], set[str]], path: str, note_id: str | None) -> bool:
    return path in reserved[0] or bool(note_id and note_id in reserved[1])


def _seen(owner, identity: str, sha: str) -> bool:
    return (owner.store.get(SEEN, digest(identity)) or {}).get("sha") == sha


def lifecycle_targets(owner) -> list[str]:
    """Notes the digest Work admits: static lifecycle targets and, with System One configured,
    notes whose status transition needs reading them (unreserved, sendable by a job, changed
    since last asked)."""
    docs = owner.index.refresh()
    duplicates = owner.index.duplicate_ids(docs)
    targets = {path for path, doc in docs.items() if _eligible(doc) and doc.note_id not in duplicates and
               (doc.type, doc.status) in STATIC_RULES}
    if owner.system_one is not None:
        reserved = _reserved(owner)
        targets.update(
            path for path, doc in docs.items()
            if (doc.type, doc.status) in JUDGMENTS and _eligible(doc) and doc.note_id not in duplicates
            and approved(doc.policy) and not _is_reserved(reserved, path, doc.note_id)
            and not _seen(owner, doc.note_id or path, doc.sha))
    return sorted(targets)


def target_path(owner, proposal, docs=None) -> str | None:
    docs = owner.index.refresh() if docs is None else docs
    if proposal.target_id:
        matches = [p for p, d in docs.items() if d.policy != "deny" and d.note_id == proposal.target_id]
        return matches[0] if len(matches) == 1 else None
    return proposal.target_path if proposal.target_path in docs and docs[proposal.target_path].policy != "deny" else None


def _item(kind: str, identity: str, title: str, *, paths=(), detail="", severity="attention", **extra):
    return {"item_id": f"{kind}-" + digest(identity)[:24], "kind": kind, "title": title,
            "paths": list(paths), "detail": detail, "severity": severity, **extra}


def attention(owner) -> list[dict[str, Any]]:
    docs = owner.index.refresh()
    visible = {p: d for p, d in docs.items() if d.policy != "deny"}
    result = []
    for note_id, paths in owner.index.duplicate_ids(docs).items():
        result.append(_item("duplicate_id", note_id, "Duplicate ID: " + ", ".join(visible[p].title for p in paths), paths=paths, detail=", ".join(paths)))
    for path, doc in sorted(visible.items()):
        if owner.vault.system(path):
            continue
        if doc.conflict:
            result.append(_item("conflict", path + (doc.sha or ""), doc.title, paths=[path], detail="Merge-conflict markers", severity="alert"))
        if not doc.valid:
            result.append(_item("invalid_yaml", path, doc.title, paths=[path], detail="Invalid YAML or text encoding"))
        note = owner.vault.read(path)
        if note is None or effective(note) == "deny":
            continue
        if path.startswith(PROPOSALS_DIR + "/") and note.type == "proposal":
            proposal = proposals.parse(note)
            if proposal:
                target = target_path(owner, proposal, docs)
                # A newly denied target suppresses its proposal, too.
                if proposal.target_path in docs and docs[proposal.target_path].policy == "deny":
                    continue
                stale = proposal.status == "stale" or target is None or docs[target].sha != proposal.target_sha
                if stale:
                    result.append(_item("stale_proposal", proposal.proposal_id, doc.title, paths=[path], detail="Target changed or disappeared"))
    cutoff = owner.clock.now() - timedelta(days=owner.setting("C5", "recent_autofix_days", 7))
    for change in owner.store.list("changes"):
        path = change["path"]
        if change.get("kind") == "autofix" and path in visible and datetime.fromisoformat(change["at"]) >= cutoff:
            result.append(_item("autofix", change["change_id"], visible[path].title, paths=[path], detail="Recent automatic standardization", change_id=change["change_id"]))
    for unknown in owner.unknowns():
        if unknown.get("resolution") is not None:
            continue
        ref = unknown.get("ref", {})
        path = ref.get("path")
        if path and (path not in visible):
            continue
        result.append(_item("unknown", unknown["unknown_id"], "Unknown effect outcome", paths=[path] if path else [],
                            detail=unknown.get("kind", "effect"), severity="alert", unknown_id=unknown["unknown_id"],
                            unknown_kind=unknown.get("kind"), unknown_ref=ref))
    for job in jobs_view(owner):
        status = job.get("last_outcome")
        if status in ("failed", "unknown", "unmet", "refused", "cancelled"):
            kind = "failed_job" if status in ("failed", "unknown") else "blocked_job"
            result.append(_item(kind, job.get("last_run_id", job["job"]), job["job"], detail=str(status), severity="alert" if kind == "failed_job" else "attention", job=job["job"]))
        if job["missed"]:
            result.append(_item("missed_job", job["job"], job["job"], detail="No run within two schedule intervals", job=job["job"]))
    candidates = sorted((d for d in visible.values() if _eligible(d) and
                         ((d.type == "idea" and d.status not in ("dropped", "archived")) or (d.type == "question" and d.status == "open")) and
                         owner.clock.now().timestamp() - d.mtime >= 30 * 86400), key=lambda d: d.path)
    if candidates:
        count = min(len(candidates), owner.setting("C5", "revisit_count", 3))
        offset = owner.clock.now().date().toordinal() % len(candidates)
        for index in range(count):
            doc = candidates[(offset + index) % len(candidates)]
            result.append(_item("revisit", doc.path, doc.title, paths=[doc.path], detail="Revisit", severity="info"))
    return result


def _judge(ctx, service, note, item, duplicates, reserved):
    """Ask System One whether ``note`` warrants its status transition.

    Returns ``(asked, entry)``: ``asked`` records the content that was sent (None when nothing
    was sent); ``entry`` is the lifecycle proposal, present only for a confident yes. Raises
    ``SystemOneUnavailable``."""
    owner = ctx.owner
    rule = JUDGMENTS[(note.type, note.status)]
    identity = note.note_id or note.path
    current = owner.vault.read(note.path)
    if (current is None or current.sha != note.sha or not current.valid or current.conflict
            or path_skip_reason(note.path) or note.note_id in duplicates
            or _is_reserved(reserved, note.path, note.note_id) or _seen(owner, identity, note.sha)):
        return None, None
    # The disclosure boundary: the note's current policy, at the strictest of admission and now.
    policy = strictest([item["policy"], effective(current)])
    if not approved(policy):
        return None, None
    answers = service.decide(system_one.note_input(note, service.max_input_chars),
                             [system_one.predicate(rule.question, rule.instructions)],
                             purpose="stewardship lifecycle", ref=ctx.work_id)
    answer = answers.get(rule.question)
    verdict = service.holds(answer)
    asked = {"path": note.path, "id": identity, "sha": note.sha, "holds": verdict}
    if not verdict:
        return asked, None
    probability = answer["probability"]
    return asked, {"path": note.path, "preimage": note.sha, "kind": "status", "source": "system_one",
                   "probability": probability, "changes": [
                       {"change_id": "status", "op": "set", "key": "status", "value": rule.target,
                        "description": f"Set status to {rule.target}: System One judged that {rule.claim} "
                                       f"(probability {probability:.2f})"}]}


def officer_step(ctx) -> dict[str, Any]:
    owner = ctx.owner
    service = owner.system_one
    duplicates = owner.index.duplicate_ids(owner.index.refresh()) if service else {}
    reserved = _reserved(owner) if service else (set(), set())
    lifecycle, asked = [], []
    for item in ctx.inputs():
        note = ctx.snapshot(item["path"])
        if (note.type, note.status) in STATIC_RULES:
            lifecycle.append({"path": note.path, "preimage": note.sha, "changes": [
                {"change_id": "archive-status", "op": "set", "key": "status", "value": "archived", "description": "Set status to archived"},
                {"change_id": "archive-move", "op": "move", "to": "90_Archive", "description": "Move to 90_Archive/"}]})
        elif service is not None and (note.type, note.status) in JUDGMENTS:
            try:
                record, entry = _judge(ctx, service, note, item, duplicates, reserved)
            except SystemOneUnavailable as error:
                # Static proposals continue; unasked notes are asked again in the next run.
                log.warning("stewardship status judgments stopped: %s", error)
                service = None
                continue
            if record:
                asked.append(record)
            if entry:
                lifecycle.append(entry)
    return {"lifecycle": lifecycle, "asked": asked}


def check(ctx, artifact):
    seen = set()
    result = []
    for entry in artifact["lifecycle"]:
        note = ctx.snapshot(entry["path"])
        ok = entry["path"] not in seen and note.sha == entry["preimage"]
        if entry.get("kind") == "status":
            rule = JUDGMENTS.get((note.type, note.status))
            ok = (ok and rule is not None and entry.get("source") == "system_one" and
                  [(c["op"], c.get("key"), c.get("value")) for c in entry["changes"]] == [("set", "status", rule.target)] and
                  approved(effective(note)))
            reason = "System One status judgment on an admitted candidate; Owner proposes, the user decides"
        else:
            ok = ok and (note.type, note.status) in STATIC_RULES
            reason = "Deterministic lifecycle rule for an admitted target"
        seen.add(entry["path"])
        result.append({"target": entry["path"], "verdict": "satisfied" if ok else "unmet", "reason": reason})
    return result


def maintain_proposals(ctx) -> dict[str, list[str]]:
    owner = ctx.owner
    result = {"stale": [], "expired": []}
    docs = owner.index.refresh()
    for path in owner.vault.iter_paths(PROPOSALS_DIR):
        note = owner.vault.read(path)
        if note is None or effective(note) == "deny":
            continue
        proposal = proposals.parse(note)
        if not proposal:
            continue
        target = target_path(owner, proposal, docs)
        if proposal.target_path in docs and docs[proposal.target_path].policy == "deny":
            continue
        record = owner.store.get("proposals", proposal.proposal_id) or {}
        if proposal.status == "stale":
            stat = owner.vault.stat(path)
            stale_at = record.get("stale_at") or note.data.get("kos_stale_at")
            since = datetime.fromisoformat(stale_at) if stale_at else datetime.fromtimestamp(stat.st_mtime, owner.clock.now().tzinfo)
            if owner.clock.now() - since > timedelta(days=7) and ctx.delete_own(path, note.sha):
                result["expired"].append(path)
                if record:
                    owner.store.update("proposals", proposal.proposal_id, lambda r: r.update(status="expired"))
            continue
        if target is None or docs[target].sha != proposal.target_sha:
            editor = FrontmatterEditor(note)
            editor.set("status", "stale")
            editor.set("kos_stale_at", iso(owner.clock.now()))
            raw = editor.render()
            if ctx.replace(path, note.sha, raw):
                result["stale"].append(path)
                # Do not bless a hand-edited proposal as safe to regenerate.
                if record:
                    owner.store.update("proposals", proposal.proposal_id, lambda r: r.update(status="stale", stale_at=iso(owner.clock.now())))
    return result


def finish(ctx, artifact) -> Done:
    owner = ctx.owner
    written = []
    reserved = None
    for item in artifact["lifecycle"]:
        note = ctx.snapshot(item["path"])
        if item.get("kind") != "status":
            written.append(proposals.write_or_regenerate(ctx, note, item["changes"], kind="archive", capability=CAPABILITY))
            continue
        # A System One status proposal never replaces or joins another proposal for its target.
        reserved = _reserved(owner) if reserved is None else reserved
        if _is_reserved(reserved, note.path, note.note_id):
            written.append({"status": "reserved", "path": None})
            continue
        written.append(proposals.write_or_regenerate(ctx, note, item["changes"], kind="status", capability=CAPABILITY))
    # Remember what was asked only after the effects, so a failed Work asks again.
    for asked in artifact.get("asked", []):
        owner.store.put(SEEN, digest(asked["id"]), {"path": asked["path"], "sha": asked["sha"],
                                                      "work": ctx.work_id, "at": iso(ctx.clock.now())})
    maintained = maintain_proposals(ctx)
    from .c8_notify import send_digest
    digest_result = send_digest(ctx.owner, ctx.request_id)
    return Done(outcome={"proposals": written, **maintained, **digest_result}, notice="Stewardship digest recorded")
