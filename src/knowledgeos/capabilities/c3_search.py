"""Capability C3 Search: the EmbeddingOfficer (static) keeps the local vector index current.

``index.embed`` (user, or actor ``schedule/embed`` for the C7 ``embed`` job) admits the eligible
notes that have no current vectors, snapshotted with their exact bytes, plus the embedded
notes that are gone or no longer eligible. The step chunks the snapshots; the static checks
confirm eligibility and chunking; the effect sends each chunk to the local embedding service
and commits the vectors (``knowledgeos.semantic``). A run with nothing to do settles at
admission with no Work. Each run stops starting notes before the Activity window closes and
reports what is still pending; the next run continues.
"""

from __future__ import annotations

import time
from typing import Any

from ..capabilities import Done, Registry, WorkDraft
from ..engine import EffectContext, StepContext
from ..owner import Admission, Owner
from ..policy import effective
from ..semantic import Pending, SemanticStore, chunk, ineligible_reason, texts_for
from ..vault import VaultPathError

CAPABILITY = "C3"
OFFICER = "EmbeddingOfficer"
ACTIVITY_SECONDS = 120
SLACK_SECONDS = 10


def register(reg: Registry) -> None:
    reg.command("index.embed", admit)
    reg.officer(OFFICER, step=officer_step, finish=finish)
    reg.check("idx.eligible_only", check_eligible)
    reg.check("idx.chunks_cover_body", check_chunks)


def _store(owner: Owner) -> SemanticStore | None:
    backend = owner.index.semantic_backend
    return backend if isinstance(backend, SemanticStore) else None


def admit(adm: Admission) -> WorkDraft | Done:
    store = _store(adm.owner)
    if store is None:
        adm.refuse("semantic search is not configured (settings C3.semantic needs ollama_url and model)")
    plan = store.plan(adm.owner.index.refresh())
    targets = plan.embed[: store.max_notes]
    if not targets and not plan.remove:
        return Done(
            outcome={"embedded": 0, "unchanged": plan.current, "removed": 0, "failed": 0, "pending": 0,
                     "model": store.model},
            notice="Semantic index is current",
        )
    return WorkDraft(
        mode="embed",
        targets=targets,
        params={"remove": plan.remove, "unchanged": plan.current, "deferred": len(plan.embed) - len(targets)},
    )


def officer_step(ctx: StepContext) -> dict[str, Any]:
    """Chunk every admitted snapshot; embedding text and vectors come only from these bytes."""
    notes = [
        {"path": item["path"], "sha": item["sha"], "chunks": [list(span) for span in chunk(ctx.snapshot(item["path"]))]}
        for item in ctx.inputs()
    ]
    return {"notes": notes, "remove": ctx.params["remove"], "unchanged": ctx.params["unchanged"],
            "deferred": ctx.params["deferred"]}


def _judgment(target: str | None, ok: bool, reason: str) -> dict[str, Any]:
    return {"target": target, "verdict": "satisfied" if ok else "unmet", "reason": reason}


def check_eligible(ctx: StepContext, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = {item["path"]: item for item in ctx.inputs()}
    judgments = []
    for entry in artifact["notes"]:
        path = entry["path"]
        item = inputs.get(path)
        if item is None or item["sha"] != entry["sha"]:
            judgments.append(_judgment(path, False, "planned note is not an admitted snapshot"))
            continue
        note = ctx.snapshot(path)
        reason = ineligible_reason(path, item["policy"], note.type, note.conflict)
        if reason is None and path in artifact["remove"]:
            reason = "also listed for removal"
        judgments.append(_judgment(path, reason is None, f"not eligible: {reason}" if reason else "eligible for embedding"))
    return judgments


def check_chunks(ctx: StepContext, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = {item["path"] for item in ctx.inputs()}
    judgments = []
    for entry in artifact["notes"]:
        path = entry["path"]
        ok = path in inputs and entry["chunks"] == [list(span) for span in chunk(ctx.snapshot(path))]
        judgments.append(_judgment(path, ok, "chunks match the snapshot's body" if ok else "chunks differ from the snapshot's body"))
    return judgments


def _eligible_now(owner: Owner, path: str) -> bool:
    """Policy of the note's current content, checked immediately before anything is sent."""
    try:
        note = owner.vault.read(path)
    except VaultPathError:
        return False
    return note is not None and ineligible_reason(
        path, effective(note, owner.config.default_policy), note.type, note.conflict
    ) is None


def _budget_seconds(owner: Owner, store: SemanticStore) -> float:
    """Time to start new notes: the Activity window minus one request timeout and slack."""
    window = int(owner.config.settings.get("runtime", {}).get("activity_timeout_seconds", ACTIVITY_SECONDS))
    return max(1.0, window - store.timeout - SLACK_SECONDS)


def finish(ctx: EffectContext, artifact: dict[str, Any]) -> Done:
    owner = ctx.owner
    store = _store(owner)
    if store is None:
        return Done(status="failed", notice="Semantic search is not configured; nothing embedded")
    pending = []
    for entry in artifact["notes"]:
        spans = [(start, end) for start, end in entry["chunks"]]
        pending.append(Pending(entry["path"], entry["sha"], spans, texts_for(ctx.snapshot(entry["path"]), spans)))
    counts = store.apply(
        pending,
        artifact["remove"],
        deadline=time.monotonic() + _budget_seconds(owner, store),
        allowed=lambda path: _eligible_now(owner, path),
        guard=ctx._guard,
    )
    error = counts.pop("error")
    counts["unchanged"] += artifact["unchanged"]
    counts["pending"] += artifact["deferred"]
    outcome = {**counts, "model": store.model}
    summary = f"{counts['embedded']} embedded, {counts['removed']} removed, {counts['pending']} pending"
    if error:
        return Done(
            status="failed",
            outcome={**outcome, "error": error},
            notice=f"Embedding stopped: {error}. {summary}; existing vectors kept; not retried before the next run",
        )
    return Done(outcome=outcome, notice=f"Semantic index updated: {summary}")
