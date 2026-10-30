"""Exact, separately authorized retirement of the inert predeployment archive.

Preview is read-only. Apply is intentionally not wired to a CLI or scheduler;
an operator must supply the exact preview checkpoint and fresh writer evidence.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .core import AdmissionError, canonical, digest, load_json
from .legacy_archive import inventory
from .owner_journal import fsync_directory, read_regular, replace_exact, stamp
from .paths import resolve_beneath


def _blocked(app: Any, target: str, inventory_digest: str) -> list[str]:
    state = app.journal.read()
    issues = []
    for key, record in state["intents"].items():
        if not isinstance(record, dict):
            continue
        if (record.get("state") in {"pending", "unknown", "started"}
                or record.get("dispatch_state") == "unknown"
                or record.get("application_state") == "started"
                or record.get("run", {}).get("unresolved_effect_ids")):
            issues.append("active_or_unknown:" + key)
    roots = (app.roots.state, app.roots.runtime)
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() and not path.is_symlink():
                continue
            if path.is_symlink():
                issues.append("unsafe_path:" + str(path.relative_to(root)))
                continue
            if root == app.roots.state and (
                    path == root / "storage-transition.json"
                    or path == root / "storage-retirement.json"
                    or root / target in path.parents):
                continue
            if path.stat().st_size > 4194304:
                issues.append("unscanned_large_file:" + str(path.relative_to(root)))
                continue
            raw = read_regular(path)
            if raw is not None and (target.encode() in raw or inventory_digest.encode() in raw):
                issues.append("live_reference:" + str(path.relative_to(root)))
    return issues


def preview_legacy_retirement(app: Any) -> dict[str, Any]:
    """Check the complete reference graph without reading archived authority as live data."""
    app.guard("storage")
    state_root = app.roots.state
    transition_path = resolve_beneath(state_root, "storage-transition.json")
    transition_raw = read_regular(transition_path)
    if transition_raw is None:
        raise AdmissionError("ARCHIVE_CONFLICT", "Completed storage transition is unavailable")
    transition = load_json(transition_path)
    target = transition.get("target")
    if (transition.get("phase") != "complete" or not isinstance(target, str)
            or not target.startswith("archives/legacy-runtime/")
            or len(Path(target).parts) != 3):
        raise AdmissionError("ARCHIVE_CONFLICT", "Retirement target is not the exact complete archive")
    snapshot = resolve_beneath(state_root, target)
    manifest_path = resolve_beneath(snapshot, "manifest.json")
    manifest_raw = read_regular(manifest_path)
    if manifest_raw is None:
        raise AdmissionError("ARCHIVE_CONFLICT", "Inert archive manifest is unavailable")
    manifest = load_json(manifest_path)
    if (manifest.get("disposition") != "inert" or manifest.get("authority_imported") is not False
            or manifest.get("inventory") != transition.get("inventory")
            or inventory(resolve_beneath(snapshot, "payload")) != transition["inventory"]):
        raise AdmissionError("ARCHIVE_CONFLICT", "Archived payload differs from its preserved inventory")
    inventory_digest = transition["inventory"]["inventory_digest"]
    issues = _blocked(app, target, inventory_digest)
    checkpoint = digest(canonical({"transition_digest": digest(transition_raw),
                                   "manifest_digest": digest(manifest_raw),
                                   "inventory_digest": inventory_digest,
                                   "target": target,
                                   "owner_revision": app.journal.read()["revision"]}))
    return {"status": "PASS" if not issues else "BLOCKED", "mode": "read_only",
            "target": target, "inventory_digest": inventory_digest,
            "checkpoint_digest": checkpoint, "issues": issues,
            "archive_file_count": sum(item["kind"] == "file"
                                      for item in transition["inventory"]["entries"]),
            "authority_imported": False}


def retire_legacy_archive(app: Any, *, expected_checkpoint: str,
                          writer_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Retire only the previewed archive after a fresh, separate operator gate."""
    app.guard("storage")
    now = app.clock()
    with app.journal.writer() as session:
        preview = preview_legacy_retirement(app)
        if preview["status"] != "PASS" or preview["checkpoint_digest"] != expected_checkpoint:
            raise AdmissionError("ARCHIVE_CONFLICT", "Exact retirement preview is stale or blocked")
        if (set(writer_evidence) != {"target", "inventory_digest", "observed_at",
                                    "writers_quiescent", "checks"}
                or writer_evidence["target"] != preview["target"]
                or writer_evidence["inventory_digest"] != preview["inventory_digest"]
                or writer_evidence["writers_quiescent"] is not True
                or not isinstance(writer_evidence["checks"], list)
                or not writer_evidence["checks"]):
            raise AdmissionError("WRITER_UNCONFIRMED", "Exact archive writer evidence is missing")
        observed = datetime.fromisoformat(writer_evidence["observed_at"])
        if (observed.tzinfo is None or not timedelta(0) <= now - observed <= timedelta(minutes=5)):
            raise AdmissionError("WRITER_UNCONFIRMED", "Archive writer inventory is stale")
        transition_path = resolve_beneath(app.roots.state, "storage-transition.json")
        transition_raw = read_regular(transition_path)
        retirement_path = resolve_beneath(app.roots.state, "storage-retirement.json")
        if read_regular(retirement_path) is not None:
            raise AdmissionError("OUTCOME_UNKNOWN", "Existing retirement checkpoint requires reconciliation")
        checkpoint = {"schema_version": 1, "phase": "prepared", "target": preview["target"],
                      "inventory_digest": preview["inventory_digest"],
                      "preview_checkpoint": expected_checkpoint,
                      "transition_digest": digest(transition_raw or b""),
                      "recorded_at": stamp(now), "authority_imported": False}
        session.fence()
        replace_exact(retirement_path, None, canonical(checkpoint) + b"\n")
        session.fence()
        snapshot = resolve_beneath(app.roots.state, preview["target"])
        shutil.rmtree(snapshot)
        fsync_directory(snapshot.parent)
        session.fence()
        if read_regular(transition_path) != transition_raw:
            raise AdmissionError("STALE_WRITER", "Transition changed during archive retirement")
        transition_path.unlink()
        fsync_directory(app.roots.state)
        checkpoint["phase"] = "complete"
        replace_exact(retirement_path, read_regular(retirement_path), canonical(checkpoint) + b"\n")
        session.value["history"].append({"event": "legacy_archive_retired",
                                         "sequence": session.value["revision"] + 1})
        session.commit(now)
        return {"status": "PASS", "retired_target": preview["target"],
                "checkpoint_digest": expected_checkpoint,
                "archive_removed": True, "authority_imported": False}
