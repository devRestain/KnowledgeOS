"""Capability C2 Standardize: the StandardizationOfficer (static) and its static checks.

Paths (docs/CAPABILITIES.md C2):
- manual: ``standardize.preview`` -> Work waits as ``awaiting_confirmation`` with the exact
  planned bytes; ``standardize.confirm`` applies them with exact preimages;
  ``standardize.cancel`` writes nothing and leaves no change-log entry.
- batch: ``standardize.batch {path, apply}`` for the CLI ``--dry-run`` / ``--apply``.
- automatic: ``standardize.auto`` (actor ``schedule/standardize``), the C7 ``standardize``
  job's command: notes whose content changed since the last automatic run, honoring the
  quiet period; writes are autofix change-log entries.

Every write records preimage and result digests, the exact delta and the preimage blob in
``changes/<change_id>.json`` for undo (C6).
"""

from __future__ import annotations

import threading
from typing import Any

from ..capabilities import Done, Registry, WorkDraft
from ..engine import EffectContext, StepContext
from ..notes import parse
from ..owner import Admission, Owner
from ..standardize import diff, note_skip_reason, path_skip_reason, plan, verify
from ..util import iso, new_id, new_uuid, sha256

CAPABILITY = "C2"
OFFICER = "StandardizationOfficer"
PERMANENT_SKIPS = ("conflict", "not_utf8", "invalid_yaml", "duplicate_id", "navigation")


def register(reg: Registry) -> None:
    reg.command("standardize.preview", admit_preview)
    reg.command("standardize.confirm", admit_confirm)
    reg.command("standardize.cancel", admit_cancel)
    reg.command("standardize.batch", admit_batch)
    reg.command("standardize.auto", admit_auto)
    reg.officer(OFFICER, step=officer_step, finish=finish)
    reg.check("std.skips_respected", check_skips)
    reg.check("std.result_valid", lambda ctx, artifact: _verified(ctx, artifact, "result_valid"))
    reg.check("std.properties_preserved", lambda ctx, artifact: _verified(ctx, artifact, "properties_preserved"))
    reg.check("std.minimal_diff", lambda ctx, artifact: _verified(ctx, artifact, "minimal_diff"))
    reg.view("changes", list_changes, get_change)


# ---------------------------------------------------------------- admission


def admit_preview(adm: Admission) -> WorkDraft:
    targets, skipped = adm.resolve(adm.params["paths"], adm.params["digests"], strict=False, exclude=path_skip_reason)
    return WorkDraft(mode="manual", targets=targets, skipped=skipped, params={"paths": adm.params["paths"]})


def admit_batch(adm: Admission) -> WorkDraft:
    targets, skipped = adm.resolve([adm.params["path"]], strict=False, exclude=path_skip_reason)
    return WorkDraft(mode="batch", targets=targets, skipped=skipped, params=dict(adm.params))


def admit_auto(adm: Admission) -> WorkDraft:
    """Notes whose content differs from what the last automatic run saw, minus the quiet period."""
    owner = adm.owner
    seen = _cursor(owner)["seen"]
    quiet_seconds = 60 * adm.setting("quiet_period_minutes", 10)
    now = owner.clock.now().timestamp()
    docs = owner.index.refresh()
    duplicate_ids = set(owner.index.duplicate_ids(docs))
    targets: list[str] = []
    skipped: list[dict[str, str]] = []
    settled: dict[str, str] = {}
    for path, doc in sorted(docs.items()):
        if doc.policy == "deny" or path_skip_reason(path) or seen.get(path) == doc.sha:
            continue
        if now - doc.mtime < quiet_seconds:
            skipped.append({"path": path, "reason": "quiet_period"})  # reconsidered next run
        elif reason := Admission.problem(doc, duplicate_ids):
            skipped.append({"path": path, "reason": reason})
            settled[path] = doc.sha or ""
        else:
            targets.append(path)
    return WorkDraft(mode="automatic", targets=targets, skipped=skipped, params={"settled": settled})


def admit_confirm(adm: Admission) -> Done:
    work = _preview(adm)
    return adm.owner.engine.resume(work["work_id"], "confirm", _confirm, adm.request_id, adm.actor)


def admit_cancel(adm: Admission) -> Done:
    work = _preview(adm)
    return adm.owner.engine.resume(work["work_id"], "cancel", _cancel, adm.request_id, adm.actor)


def _preview(adm: Admission) -> dict[str, Any]:
    work = adm.work(adm.params["preview_id"])
    spec = work["workspec"]
    if spec["capability"] != CAPABILITY or spec["mode"] != "manual":
        adm.refuse(f"{adm.params['preview_id']} is not a standardization preview")
    return work


# ---------------------------------------------------------------- Officer


def officer_step(ctx: StepContext) -> dict[str, Any]:
    """Plan every snapshotted input. Result bytes are stored as State blobs."""
    duplicate_ids = set(ctx.owner.index.duplicate_ids())
    notes: list[dict[str, Any]] = []
    unchanged: list[str] = []
    skipped = list(ctx.workspec["skipped"])
    for item in ctx.inputs():
        path = item["path"]
        note = ctx.snapshot(path)
        planned = plan(
            path,
            note,
            policy=item["policy"],
            created=ctx.first_seen(path),
            new_id=new_uuid,
            default_policy=ctx.owner.config.default_policy,
            duplicate_ids=duplicate_ids,
        )
        if planned.skip:
            skipped.append({"path": path, "reason": planned.skip})
        elif not planned.changed:
            unchanged.append(path)
        else:
            assert planned.result is not None
            notes.append(
                {
                    "path": path,
                    "preimage": item["sha"],
                    "result": ctx.put_blob(planned.result),
                    "changes": [change.as_dict() for change in planned.changes],
                    "diff": diff(path, note.raw, planned.result),
                }
            )
    return {"mode": ctx.mode, "notes": notes, "unchanged": unchanged, "skipped": skipped}


def finish(ctx: EffectContext, artifact: dict[str, Any]) -> Done:
    planned = _planned_outcome(ctx.work_id, artifact)
    count = len(artifact["notes"])
    if ctx.mode == "manual":
        if not count:
            return Done(outcome=planned, notice=_summary("Nothing to standardize", artifact))
        return Done(
            outcome=planned,
            notice=_summary(f"{count} note(s) would change; confirm to apply", artifact),
            work_status="awaiting_confirmation",
        )
    if ctx.mode == "batch" and not ctx.params.get("apply"):
        return Done(
            outcome={**planned, "dry_run": True},
            notice=_summary(f"Dry run: {count} note(s) would change; nothing written", artifact),
        )
    applied, not_applied = _apply(ctx, artifact, "autofix" if ctx.mode == "automatic" else "batch")
    if ctx.mode == "automatic":
        settled = dict(ctx.params.get("settled", {}))
        for path in artifact["unchanged"]:
            settled[path] = _input_sha(ctx, path) or ""
        for item in artifact["skipped"]:
            if item["reason"] in PERMANENT_SKIPS and (sha := _input_sha(ctx, item["path"])):
                settled[item["path"]] = sha
        for entry in artifact["notes"]:
            if entry["path"] in applied:
                settled[entry["path"]] = entry["result"]  # blob key == result sha
        _advance(ctx.owner, settled)
    return _applied_done(applied, not_applied, planned)


def _confirm(ctx: EffectContext, artifact: dict[str, Any]) -> Done:
    applied, not_applied = _apply(ctx, artifact, "manual")
    return _applied_done(applied, not_applied, {"preview_id": ctx.work_id})


def _cancel(ctx: EffectContext, artifact: dict[str, Any]) -> Done:
    return Done(
        outcome={"preview_id": ctx.work_id, "cancelled": True},
        notice="Preview cancelled; nothing written",
        work_status="cancelled",
    )


def _apply(ctx: EffectContext, artifact: dict[str, Any], kind: str) -> tuple[list[str], list[dict[str, str]]]:
    """Write each planned result with its exact preimage; log each applied change."""
    applied: list[str] = []
    not_applied: list[dict[str, str]] = []
    for entry in artifact["notes"]:
        path = entry["path"]
        data = ctx.blob(entry["result"])
        if not ctx.replace(path, entry["preimage"], data):
            not_applied.append({"path": path, "reason": "changed since it was read; nothing written"})
            continue
        change_id = new_id("change")
        ctx.owner.store.put(
            "changes",
            change_id,
            {
                "change_id": change_id,
                "kind": kind,
                "work_id": ctx.work_id,
                "request_id": ctx.request_id,
                "path": path,
                "note_id": parse(path, data).note_id,
                "preimage_sha": entry["preimage"],
                "preimage_blob": entry["preimage"],
                "result_sha": sha256(data),
                "result_blob": entry["result"],
                "delta": {"changes": entry["changes"], "diff": entry["diff"]},
                "at": iso(ctx.clock.now()),
                "undone_at": None,
            },
        )
        applied.append(path)
    return applied, not_applied


def _applied_done(applied: list[str], not_applied: list[dict[str, str]], outcome: dict[str, Any]) -> Done:
    outcome = {**outcome, "applied": applied, "not_applied": not_applied}
    if not_applied:
        changed = ", ".join(item["path"] for item in not_applied)
        return Done(
            status="unmet",
            outcome=outcome,
            notice=f"Applied {len(applied)}; not written because the note changed: {changed}. Preview again.",
        )
    return Done(outcome=outcome, notice=f"Standardized {len(applied)} note(s)")


def _planned_outcome(work_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "preview_id": work_id,
        "notes": [
            {"path": entry["path"], "changes": entry["changes"], "diff": entry["diff"]} for entry in artifact["notes"]
        ],
        "skipped": artifact["skipped"],
        "unchanged": len(artifact["unchanged"]),
    }


def _summary(text: str, artifact: dict[str, Any]) -> str:
    skipped = len(artifact["skipped"])
    return f"{text}; {skipped} skipped" if skipped else text


def _input_sha(ctx: StepContext, path: str) -> str | None:
    return next((item["sha"] for item in ctx.inputs() if item["path"] == path), None)


# ---------------------------------------------------------------- automatic cursor


def _cursor(owner: Owner) -> dict[str, Any]:
    """Content digests the automatic path last settled per path (``standardize_seen.jsonl``)."""

    def load() -> dict[str, Any]:
        seen: dict[str, str] = {}
        for entry in owner.store.read_stream("standardize_seen"):
            seen[entry["path"]] = entry["sha"]
        return {"seen": seen, "lock": threading.Lock()}

    return owner.cache("C2.cursor", load)


def _advance(owner: Owner, settled: dict[str, str]) -> None:
    cursor = _cursor(owner)
    with cursor["lock"]:
        for path, sha in sorted(settled.items()):
            if cursor["seen"].get(path) != sha:
                cursor["seen"][path] = sha
                owner.store.append("standardize_seen", {"path": path, "sha": sha})


# ---------------------------------------------------------------- static EvalOfficer checks


def _judgment(target: str | None, ok: bool, reason: str) -> dict[str, Any]:
    return {"target": target, "verdict": "satisfied" if ok else "unmet", "reason": reason}


def check_skips(ctx: StepContext, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    duplicate_ids = set(ctx.owner.index.duplicate_ids())
    policies = {item["path"]: item for item in ctx.inputs()}
    judgments = []
    for entry in artifact["notes"]:
        path = entry["path"]
        item = policies.get(path)
        if item is None or item["sha"] != entry["preimage"]:
            judgments.append(_judgment(path, False, "planned write is not for an admitted snapshot"))
            continue
        reason = path_skip_reason(path) or note_skip_reason(ctx.snapshot(path), item["policy"], duplicate_ids)
        judgments.append(_judgment(path, reason is None, reason or "eligible"))
    return judgments


def _verified(ctx: StepContext, artifact: dict[str, Any], aspect: str) -> list[dict[str, Any]]:
    memo = ctx.memo.setdefault("c2.verify", {})
    judgments = []
    for entry in artifact["notes"]:
        path = entry["path"]
        if path not in memo:
            changed = [change["key"] for change in entry["changes"]]
            memo[path] = verify(path, ctx.snapshot(path), ctx.blob(entry["result"]), changed)
        problem = memo[path][aspect]
        judgments.append(_judgment(path, problem is None, problem or "ok"))
    return judgments


# ---------------------------------------------------------------- read view


def list_changes(owner: Owner, query: dict[str, str]) -> list[dict[str, Any]]:
    """Change log, newest first; ``?path=`` filters by note path. Diffs are in the detail view."""
    path = query.get("path")
    records = [record for record in owner.store.list("changes") if path is None or record["path"] == path]
    records.sort(key=lambda record: (record["at"], record["change_id"]), reverse=True)
    return [{key: value for key, value in record.items() if key != "delta"} for record in records]


def get_change(owner: Owner, change_id: str) -> dict[str, Any] | None:
    return owner.store.get("changes", change_id)
