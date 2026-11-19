"""Static stewardship: attention, lifecycle proposals, and proposal expiry."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import proposals
from ..capabilities import Done, Registry
from ..notes import FrontmatterEditor
from ..policy import declared_looser_than_confidential, effective
from ..standardize import path_skip_reason
from ..util import digest, iso
from ..vault import PROPOSALS_DIR
from .c7_jobs import jobs_view

CAPABILITY = "C5"
OFFICER = "StewardshipOfficer"


def register(reg: Registry) -> None:
    reg.officer(OFFICER, officer_step, finish)
    reg.view("attention", lambda owner, query: attention(owner))


def _eligible(doc) -> bool:
    return doc.policy != "deny" and doc.valid and not doc.conflict and not path_skip_reason(doc.path)


def lifecycle_targets(owner) -> list[str]:
    docs = owner.index.refresh()
    duplicates = owner.index.duplicate_ids(docs)
    return sorted(path for path, doc in docs.items() if _eligible(doc) and doc.note_id not in duplicates and
                  ((doc.type == "project" and doc.status == "done") or (doc.type == "idea" and doc.status == "dropped")))


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
        if note is None or effective(note, owner.config.default_policy) == "deny":
            continue
        if declared_looser_than_confidential(note):
            result.append(_item("confidential_policy", path, doc.title, paths=[path], detail="Confidential note has a looser declared ai_policy"))
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
                            detail=unknown.get("kind", "effect"), severity="alert", unknown_id=unknown["unknown_id"], unknown_kind=unknown.get("kind")))
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


def officer_step(ctx) -> dict[str, Any]:
    lifecycle = []
    for item in ctx.inputs():
        note = ctx.snapshot(item["path"])
        if (note.type, note.status) in (("project", "done"), ("idea", "dropped")):
            lifecycle.append({"path": note.path, "preimage": note.sha, "changes": [
                {"change_id": "archive-status", "op": "set", "key": "status", "value": "archived", "description": "Set status to archived"},
                {"change_id": "archive-move", "op": "move", "to": "90_Archive", "description": "Move to 90_Archive/"}]})
    return {"lifecycle": lifecycle}


def check(ctx, artifact):
    seen = set()
    result = []
    for entry in artifact["lifecycle"]:
        note = ctx.snapshot(entry["path"])
        ok = entry["path"] not in seen and note.sha == entry["preimage"] and (note.type, note.status) in (("project", "done"), ("idea", "dropped"))
        seen.add(entry["path"])
        result.append({"target": entry["path"], "verdict": "satisfied" if ok else "unmet", "reason": "Deterministic lifecycle rule for an admitted target"})
    return result


def maintain_proposals(ctx) -> dict[str, list[str]]:
    owner = ctx.owner
    result = {"stale": [], "expired": []}
    docs = owner.index.refresh()
    for path in owner.vault.iter_paths(PROPOSALS_DIR):
        note = owner.vault.read(path)
        if note is None or effective(note, owner.config.default_policy) == "deny":
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
    written = []
    for item in artifact["lifecycle"]:
        written.append(proposals.write_or_regenerate(ctx, ctx.snapshot(item["path"]), item["changes"], kind="archive", capability=CAPABILITY))
    maintained = maintain_proposals(ctx)
    from .c8_notify import send_digest
    digest_result = send_digest(ctx.owner, ctx.request_id)
    return Done(outcome={"proposals": written, **maintained, **digest_result}, notice="Stewardship digest recorded")
