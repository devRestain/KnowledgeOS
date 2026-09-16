from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

import vaultops.recovery as recovery_module
import vaultops.transactions as transactions_module
from vaultops.local_commands import create_note
from vaultops.recovery import RecoveryJournal
from vaultops.transactions import archive_project, finalize_capture
from vaultops.workflows import create_project_bundle

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    (root / "KnowledgeHub").mkdir()
    for relative in (
        "00_Inbox/Captures",
        "20_Projects",
        "80_Assets/Inbox",
        "80_Assets/Images",
        "80_Assets/Documents",
        "80_Assets/Audio",
        "90_Archive/Captures",
        "90_Archive/Projects",
    ):
        (root / "KnowledgeHub" / relative).mkdir(parents=True)
    return root


def _capture(root: Path, tmp_path: Path) -> tuple[str, Path, str]:
    body_file = tmp_path / "capture.md"
    body_file.write_text("recovery body\n", encoding="utf-8")
    report, exit_code = create_note(
        root,
        note_type="capture",
        title="Recovery Capture",
        body_file=body_file,
        created_at="2026-09-14T10:00:00+09:00",
    )
    assert exit_code == 0
    capture_path = str(report["path"])
    source = root / "KnowledgeHub" / capture_path
    return capture_path, source, hashlib.sha256(source.read_bytes()).hexdigest()


def test_recovery_journal_is_hashed_append_only_and_private(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    journal = RecoveryJournal(
        root,
        job_id="550e8400-e29b-41d4-a716-446655440000",
        operation="capture_finalize",
    )
    fsync_calls: list[int] = []
    monkeypatch.setattr(recovery_module.os, "fsync", lambda descriptor: fsync_calls.append(descriptor))
    intent = {
        "schema_version": 1,
        "source": "00_Inbox/Captures/2026/09/example.md",
        "destination": "90_Archive/Captures/2026/example.md",
        "source_sha256": "a" * 64,
        "destination_sha256": "b" * 64,
    }

    journal.start(intent)
    journal.append("applying", {"source": intent["source"]})

    records = journal.records()
    assert [record["sequence"] for record in records] == [1, 2]
    assert records[1]["previous_record_sha256"] == records[0]["record_sha256"]
    assert len(fsync_calls) >= 4
    assert journal.path.stat().st_mode & 0o777 == 0o600
    assert journal.job_dir.stat().st_mode & 0o777 == 0o700
    assert journal.runtime.stat().st_mode & 0o777 == 0o700
    assert json.loads(journal.path.read_text(encoding="utf-8").splitlines()[-1])["record_sha256"]


def test_capture_recovery_resumes_after_destination_publish_and_replays_noop(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    capture_path, source, digest = _capture(root, tmp_path)
    job_id = str(uuid.uuid4())
    original_write = transactions_module.write_note_file

    def fail_after_publish(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash after destination publish")

    monkeypatch.setattr(transactions_module, "write_note_file", fail_after_publish)
    _first, first_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        modified_at="2026-09-14T11:00:00+09:00",
        job_id=job_id,
    )
    assert first_code == 10
    assert _first["status"] == "FAIL"
    destination = root / "KnowledgeHub/90_Archive/Captures/2026/20260914-100000 Recovery Capture.md"
    assert source.is_file()
    assert destination.is_file()

    monkeypatch.setattr(transactions_module, "write_note_file", original_write)
    resumed, resumed_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        modified_at="2026-09-14T11:00:00+09:00",
        job_id=job_id,
    )
    assert resumed_code == 0
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert not source.exists()
    assert resumed["completion_receipt"]["job_id"] == job_id
    assert (root / "runtime/receipts" / f"{job_id}-capture_finalize.json").is_file()

    replay, replay_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        modified_at="2026-09-14T11:00:00+09:00",
        job_id=job_id,
    )
    assert replay_code == 0
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True


def test_recovery_intent_mismatch_quarantines_without_touching_vault(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    capture_path, source, digest = _capture(root, tmp_path)
    job_id = str(uuid.uuid4())
    original_write = transactions_module.write_note_file

    def fail_after_publish(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash")

    monkeypatch.setattr(transactions_module, "write_note_file", fail_after_publish)
    _first, first_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        job_id=job_id,
    )
    assert first_code == 10
    destination = root / "KnowledgeHub/90_Archive/Captures/2026/20260914-100000 Recovery Capture.md"
    before_source = source.read_bytes()
    before_destination = destination.read_bytes()
    monkeypatch.setattr(transactions_module, "write_note_file", original_write)

    conflict, conflict_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256="0" * 64,
        outcome="triaged",
        job_id=job_id,
    )
    assert conflict_code == 30
    assert conflict["status"] == "CONFLICT"
    assert conflict["errors"][0]["code"] == "RECOVERY_INTENT_MISMATCH"
    assert conflict["quarantined"].startswith("runtime/quarantine/transactions/")
    assert source.read_bytes() == before_source
    assert destination.read_bytes() == before_destination
    assert not (root / "runtime/runs" / job_id).exists()


def test_malformed_recovery_journal_is_quarantined_without_vault_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    capture_path, source, digest = _capture(root, tmp_path)
    job_id = str(uuid.uuid4())
    journal = RecoveryJournal(root, job_id=job_id, operation="capture_finalize")
    journal.start(
        {
            "schema_version": 1,
            "request": {
                "capture_path": capture_path,
                "expected_sha256": digest,
                "outcome": "triaged",
                "related": None,
                "modified_at": "2026-09-14T10:00:00+09:00",
            },
            "source": capture_path,
            "destination": "90_Archive/Captures/2026/Recovery Capture.md",
            "source_sha256": digest,
            "destination_sha256": "b" * 64,
            "related_link": None,
        }
    )
    raw = journal.path.read_bytes()
    journal.path.write_bytes(raw.replace(b'"record_sha256":"', b'"record_sha256":"0', 1))
    before = source.read_bytes()

    report, exit_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        job_id=job_id,
    )

    assert exit_code == 30
    assert report["status"] == "CONFLICT"
    assert report["errors"][0]["code"] == "RECOVERY_JOURNAL_INVALID"
    assert source.read_bytes() == before
    assert report["quarantined"].startswith("runtime/quarantine/transactions/")


def test_project_archive_recovery_finishes_after_rename_fault(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    project = create_project_bundle(root, title="Recovery Archive", created_at="2026-09-14T09:00:00+09:00")
    assert project["status"] == "PASS"
    project_dir = root / "KnowledgeHub/20_Projects/Recovery Archive"
    before = {
        path.relative_to(root / "KnowledgeHub").as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project_dir.rglob("*")
        if path.is_file()
    }
    job_id = str(uuid.uuid4())
    original_rename = transactions_module.os.rename

    def fail_after_rename(source_path, destination_path):
        original_rename(source_path, destination_path)
        raise OSError("simulated crash after directory rename")

    monkeypatch.setattr(transactions_module.os, "rename", fail_after_rename)
    first, first_code = archive_project(
        root,
        project_path="20_Projects/Recovery Archive/Recovery Archive.md",
        expected_hashes=before,
        job_id=job_id,
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    destination_dir = root / "KnowledgeHub/90_Archive/Projects/2026/Recovery Archive"
    assert not project_dir.exists()
    assert destination_dir.is_dir()

    monkeypatch.setattr(transactions_module.os, "rename", original_rename)
    resumed, resumed_code = archive_project(
        root,
        project_path="20_Projects/Recovery Archive/Recovery Archive.md",
        expected_hashes=before,
        job_id=job_id,
    )
    assert resumed_code == 0
    assert resumed["status"] == "PASS"
    assert resumed["replayed"] is True
    assert resumed["identity_preserved"] is True
    assert resumed["completion_receipt"]["operation"] == "project_archive"
