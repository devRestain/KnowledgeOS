"""Explicit, restartable inert preservation; never import legacy execution authority."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path

from ..runtime import RUNTIME_DIRECTORIES, STATE_DIRECTORIES
from .core import AdmissionError, canonical, digest, load_json
from .owner_journal import fsync_directory, private_directory, read_regular, replace_exact, stamp
from .paths import resolve_beneath


def inventory(root: Path) -> dict:
    """Include empty directories and stable metadata without displaying payloads."""
    if root.is_symlink() or not root.is_dir():
        raise AdmissionError("ARCHIVE_INPUT", "source must be a regular private directory")
    entries = []
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise AdmissionError("ARCHIVE_INPUT", "symlinks and special files require a separate preservation decision")
        item = {"path": path.relative_to(root).as_posix(), "kind": "directory" if path.is_dir() else "file",
                "mode": stat.S_IMODE(metadata.st_mode), "uid": metadata.st_uid, "gid": metadata.st_gid,
                "mtime_ns": metadata.st_mtime_ns}
        if path.is_file():
            item.update(size_bytes=metadata.st_size, sha256=digest(path.read_bytes()))
        entries.append(item)
    return {"entries": entries, "inventory_digest": digest(canonical(entries))}


def _save(path, value):
    replace_exact(path, read_regular(path), canonical(value) + b"\n")


def _writer_evidence(application, relative, expected):
    if not relative:
        raise AdmissionError("WRITER_UNCONFIRMED", "apply requires a recent operator writer inventory tied to this exact source and digest")
    value = load_json(resolve_beneath(application.roots.control, relative))
    if set(value) != {"source", "inventory_digest", "observed_at", "writers_quiescent", "checks"}:
        raise AdmissionError("WRITER_UNCONFIRMED", "writer evidence has an unsupported shape")
    observed = datetime.fromisoformat(value["observed_at"])
    if observed.tzinfo is None:
        raise AdmissionError("WRITER_UNCONFIRMED", "writer time must include a timezone")
    age = application.clock() - observed
    if not timedelta(0) <= age <= timedelta(minutes=5):
        raise AdmissionError("WRITER_UNCONFIRMED", "writer inventory is stale")
    if value["source"] != str(application.roots.runtime) or value["inventory_digest"] != expected or value["writers_quiescent"] is not True or not isinstance(value["checks"], list) or not value["checks"]:
        raise AdmissionError("WRITER_UNCONFIRMED", "writer inventory does not confirm the selected source")
    return value


def archive_runtime(application, *, apply=False, expected_digest=None, writer_evidence=None, fault_hook=lambda _phase: None):
    roots = application.roots
    application.guard("storage")
    transition_path = resolve_beneath(roots.state, "storage-transition.json")
    transition = load_json(transition_path) if transition_path.exists() else None
    if transition is not None:
        required = {"schema_version", "source", "target", "inventory", "pins", "phase", "writer_evidence"}
        if set(transition) != required or transition["schema_version"] != 1 or transition["source"] != str(roots.runtime) or transition["pins"] != application.journal.empty()["pins"]:
            raise AdmissionError("ARCHIVE_CONFLICT", "existing preservation plan does not match these bindings")
        snapshot = resolve_beneath(roots.state, transition["target"])
        observed = transition["inventory"]
    else:
        observed = inventory(roots.runtime)
        snapshot = resolve_beneath(roots.state, "archives/legacy-runtime/" + observed["inventory_digest"][7:31])
    payload = resolve_beneath(snapshot, "payload")
    report = {"status": "PASS", "mode": "apply" if apply else "dry_run", "source": str(roots.runtime),
              "target": str(snapshot), "inventory_digest": observed["inventory_digest"],
              "file_count": sum(e["kind"] == "file" for e in observed["entries"]),
              "directory_count": sum(e["kind"] == "directory" for e in observed["entries"]),
              "disposition": "inert", "legacy_authority_imported": False,
              "metadata": "file_and_directory_modes_uid_gid_mtime_preserved_by_same_filesystem_rename",
              "writer_confirmation": "required_for_apply"}
    if not apply:
        return report, 0
    if expected_digest != observed["inventory_digest"]:
        raise AdmissionError("ARCHIVE_CONFLICT", "apply requires the exact dry-run inventory digest")
    evidence = _writer_evidence(application, writer_evidence, expected_digest)
    if transition and transition["phase"] == "complete":
        if inventory(payload) != observed:
            raise AdmissionError("ARCHIVE_CONFLICT", "inert archive payload changed")
        report.update(replayed=True, archive_verified=True, phase="complete")
        return report, 0
    # Existing approvals, tokens and queues are never read as executable records.
    # A fresh journal is persisted before the archive makes State nonempty.
    with application.journal.writer() as session, ExitStack() as locks:
        if session.value["intents"] or session.value["decisions"] or session.value["human_requests"] or session.value["outbox"]:
            raise AdmissionError("ARCHIVE_CONFLICT", "fresh initialization cannot replace active owner State")
        session.fence()
        application.guard("storage")
        if transition is None:
            if snapshot.exists():
                raise AdmissionError("ARCHIVE_CONFLICT", "target exists without this exact preservation plan")
            if inventory(roots.runtime) != observed:
                raise AdmissionError("ARCHIVE_CONFLICT", "source changed after dry-run")
            for path in sorted(roots.runtime.rglob("*")):
                if path.is_file() and (path.suffix == ".lock" or path.parent.name == "locks"):
                    handle = locks.enter_context(path.open("rb"))
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise AdmissionError("WRITER_ACTIVE", "a source writer lock is held") from None
            session.commit(application.clock())
            private_directory(resolve_beneath(roots.state, "archives"))
            private_directory(resolve_beneath(roots.state, "archives/legacy-runtime"))
            private_directory(snapshot)
            transition = {"schema_version": 1, "source": str(roots.runtime),
                          "target": snapshot.relative_to(roots.state).as_posix(), "inventory": observed,
                          "pins": application.journal.empty()["pins"], "phase": "prepared", "writer_evidence": evidence}
            _save(transition_path, transition)
            _save(snapshot / "manifest.json", {"schema_version": 1, "disposition": "inert", "inventory": observed,
                                              "source": str(roots.runtime), "pins_at_archive": transition["pins"],
                                              "authority_imported": False, "preserved_at": stamp(application.clock())})
            fault_hook("prepared")
        if transition["phase"] == "prepared":
            if payload.exists():
                if roots.runtime.exists() or inventory(payload) != observed:
                    raise AdmissionError("ARCHIVE_CONFLICT", "interrupted rename has conflicting source or payload")
            else:
                if inventory(roots.runtime) != observed:
                    raise AdmissionError("ARCHIVE_CONFLICT", "source changed before archival rename")
                os.rename(roots.runtime, payload)
                fsync_directory(snapshot)
                fsync_directory(roots.runtime.parent)
            fault_hook("renamed")
            if inventory(payload) != observed:
                raise AdmissionError("ARCHIVE_CONFLICT", "archived bytes or metadata differ; leave preservation pending")
            transition["phase"] = "archived"
            _save(transition_path, transition)
        if transition["phase"] == "archived":
            if roots.runtime.exists() and any(roots.runtime.iterdir()):
                raise AdmissionError("ARCHIVE_CONFLICT", "new Runtime contains unconfirmed data")
            private_directory(roots.runtime)
            transition["phase"] = "initializing"
            _save(transition_path, transition)
        if transition["phase"] == "initializing":
            for name in RUNTIME_DIRECTORIES:
                private_directory(resolve_beneath(roots.runtime, name))
            for name in STATE_DIRECTORIES:
                private_directory(resolve_beneath(roots.state, name))
            if inventory(payload) != observed:
                raise AdmissionError("ARCHIVE_CONFLICT", "inert archive changed during initialization")
            fault_hook("initialized")
            transition["phase"] = "complete"
            _save(transition_path, transition)
        report.update(phase=transition["phase"], archive_verified=True, writer_confirmation="operator_inventory_and_local_lock_fence")
        return report, 0
