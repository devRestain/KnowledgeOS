"""Explicit, compare-and-swap adoption of an exact preserved Core journal."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
from datetime import UTC, datetime

from .core import PINS, AdmissionError, canonical
from .owner_journal import OwnerJournal, fsync_directory, read_regular, replace_exact
from .paths import ResolvedPaths, resolve_beneath

_OLD_CORE = "sha256:b69f3e389de8b8362397dec8d55a92d10a4556cc2c5f4fe646b934944db5188e"
_PREVIOUS_CORE = "sha256:28697ac0cb49106afa32b143482cdca32e4e638d1a250173db381a2bf836e2be"
_CORE_0180 = "sha256:a1a051556d2e187c6012c562a46a9e5aed4bc842325e94e66522ccdbd9ce4c13"
_EMPTY_REQUIRED = ("gateway", "correlations", "experience", "outbox",
                   "human_requests", "decisions", "interops_outbound", "experience_events")


def _inspect(roots: ResolvedPaths, expected_sha256: str):
    path = resolve_beneath(roots.state, "owner-control.json")
    raw = read_regular(path)
    if raw is None or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise AdmissionError("STATE_PREIMAGE_CHANGED", "the exact owner State preimage differs")
    metadata = path.stat(follow_symlinks=False)
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
        raise AdmissionError("STATE_MODE_INVALID", "owner State mode or owner differs")
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise AdmissionError("STATE_INVALID", "preserved journal is not JSON") from error
    target = OwnerJournal(roots, {
        "fabric_id": "agentfabric", "instance_id": "knowledgeos.local",
    }).empty()
    if not isinstance(value, dict):
        raise AdmissionError("STATE_VERSION_UNSUPPORTED", "cutover source must be an exact owner journal")
    source_version = value.get("state_version")
    required = (set(target) - {"experience_events", "experience_cursor", "interops_outbound"}
                if source_version == 3 else set(target))
    if source_version not in {3, 4} or set(value) != required:
        raise AdmissionError("STATE_VERSION_UNSUPPORTED", "cutover accepts only an observed v3 or v4 shape")
    old_pins = copy.deepcopy(PINS)
    source_core = value.get("pins", {}).get("core_version") if isinstance(value.get("pins"), dict) else None
    supported_sources = {3: {"0.17.0": _OLD_CORE},
                         4: {"0.17.4": _PREVIOUS_CORE, "0.18.0": _CORE_0180}}
    source_digest = supported_sources[source_version].get(source_core)
    if source_digest is None:
        raise AdmissionError("STATE_PIN_MISMATCH", "cutover source Core release is unsupported")
    old_pins.update(core_version=source_core, core_digest=source_digest,
                    schema_version_pin=source_core)
    if value["pins"] != old_pins or value["owner_operation_id"] != "knowledgeos" or (
        value["fabric_id"] != "agentfabric" or value["instance_id"] != "knowledgeos.local"
    ):
        raise AdmissionError("STATE_PIN_MISMATCH", "cutover source identity differs")
    if any(not isinstance(value[key], dict) for key in (
        "intents", "receipts", "human_requests", "decisions", "gateway",
        "correlations", "interops_outbound"
    ) if key in value) or any(not isinstance(value[key], list) for key in (
        "history", "experience", "outbox", "experience_events"
    ) if key in value):
        raise AdmissionError("STATE_INVALID", "owner record collections are invalid")
    if any(value.get(key) for key in _EMPTY_REQUIRED):
        raise AdmissionError("STATE_ACTIVE", "legacy exchange or Experience requires separate review")
    if source_version == 4 and (type(value["experience_cursor"]) is not int
                                or value["experience_cursor"] != 0):
        raise AdmissionError("STATE_ACTIVE", "Experience cursor requires separate review")
    if any(not isinstance(item, dict) or item.get("state") not in {
        "completed", "failed", "cancelled", "expired", "rejected"
    } or "spec" in item or "run" in item
           for item in value["intents"].values()) or any(
               not isinstance(item, dict) or item.get("unresolved")
               for item in value["receipts"].values()):
        raise AdmissionError("STATE_ACTIVE", "unresolved owner work blocks Core cutover")
    if type(value["generation"]) is not int or not 1 <= value["generation"] < 2147483647 or (
        type(value["revision"]) is not int or value["revision"] < 0):
        raise AdmissionError("STATE_INVALID", "owner generation or revision is invalid")
    migrated = copy.deepcopy(value)
    migrated["state_version"] = 4
    migrated["pins"] = dict(PINS)
    migrated["generation"] += 1
    migrated["revision"] += 1
    migrated["observed_at"] = datetime.now(UTC).isoformat()
    if source_version == 3:
        migrated.update(experience_events=[], experience_cursor=0, interops_outbound={})
    return path, raw, migrated


def core_cutover(roots: ResolvedPaths, *, expected_sha256: str, apply: bool = False) -> dict:
    """Preview or apply one exact transition, preserving the byte preimage."""
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_sha256):
        raise AdmissionError("STATE_PREIMAGE_CHANGED", "expected SHA-256 must be exact lowercase hex")
    path, raw, migrated = _inspect(roots, expected_sha256)
    report = {"status": "PASS", "applied": False, "source_sha256": expected_sha256,
              "source_core_version": json.loads(raw)["pins"]["core_version"],
              "target_core_version": PINS["core_version"],
              "source_version": json.loads(raw)["state_version"], "target_version": 4,
              "source_generation": migrated["generation"] - 1,
              "target_generation": migrated["generation"],
              "preserved_intent_count": len(migrated["intents"]),
              "preserved_receipt_count": len(migrated["receipts"])}
    if not apply:
        return report
    lock_path = resolve_beneath(roots.state, ".owner-control.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise AdmissionError("STATE_MODE_INVALID", "owner writer lock differs")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AdmissionError("STATE_ACTIVE", "another owner writer holds the journal") from error
        _, current, migrated = _inspect(roots, expected_sha256)
        if current != raw:
            raise AdmissionError("STATE_PREIMAGE_CHANGED", "owner State changed before lock")
        archive_root = resolve_beneath(roots.state, "archives/core-cutover")
        archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive = archive_root / expected_sha256
        if archive.exists():
            if archive.is_symlink() or read_regular(archive) != raw:
                raise AdmissionError("STATE_PREIMAGE_CHANGED", "cutover archive conflicts")
        else:
            saved = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(saved, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                fsync_directory(archive_root)
        replace_exact(path, raw, canonical(migrated) + b"\n")
        report["applied"] = True
        report["archive_sha256"] = expected_sha256
        return report
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
