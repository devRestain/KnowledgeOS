from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from support.control_factory import (
    fixture_path,
    make_control_root,
    make_separate_portable_fixture_roots,
)

import vaultops.transactions as transactions_module
from vaultops.local_commands import create_note
from vaultops.reconcile import (
    apply_repair_plan,
    reconcile_transactions,
    repair_plan,
    verify_receipts,
)
from vaultops.transactions import archive_project, finalize_capture
from vaultops.workflows import create_project_bundle

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = make_control_root(tmp_path, ("blueprint",))
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
        (fixture_path(root, "vault") / relative).mkdir(parents=True)
    (fixture_path(root, "state")).mkdir(mode=0o700, exist_ok=True)
    for relative in (
        "staging",
        "queue",
        "quarantine",
        "awaiting_remote_authorization",
        "running",
        "review",
        "approved",
        "applying",
        "done",
        "rejected",
        "expired",
        "failed",
        "conflict",
        "runs",
        "receipts",
        "locks",
        "index",
        "cache",
        "logs",
    ):
        (fixture_path(root, "state") / relative).mkdir(mode=0o700)
    return root


def _capture(root: Path, tmp_path: Path) -> tuple[str, Path, str]:
    body_file = tmp_path / "capture.md"
    body_file.write_text("reconcile body\n", encoding="utf-8")
    report, code = create_note(
        root,
        note_type="capture",
        title="Reconcile Capture",
        body_file=body_file,
        created_at="2026-09-14T10:00:00+09:00",
    )
    assert code == 0
    path = str(report["path"])
    source = fixture_path(root, "vault") / path
    return path, source, hashlib.sha256(source.read_bytes()).hexdigest()


def test_reconcile_builds_and_applies_a_digest_bound_capture_plan(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    capture_path, source, digest = _capture(root, tmp_path)
    job_id = str(uuid.uuid4())
    original_write = transactions_module.write_note_file

    def fail_after_publish(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash after publish")

    monkeypatch.setattr(transactions_module, "write_note_file", fail_after_publish)
    first, first_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        modified_at="2026-09-14T11:00:00+09:00",
        job_id=job_id,
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    monkeypatch.setattr(transactions_module, "write_note_file", original_write)

    reconciled, reconcile_code = reconcile_transactions(root)
    assert reconcile_code == 0
    assert reconciled["status"] == "REPAIR_REQUIRED"
    assert reconciled["summary"] == {"jobs": 1, "complete": 0, "repairable": 1, "conflict": 0}
    assert reconciled["jobs"][0]["actions"] == ["remove_source_and_complete"]

    plan_path = fixture_path(root, "state") / "repair-plan.json"
    planned, plan_code = repair_plan(root, output=plan_path)
    assert plan_code == 0
    assert planned["plan_path"] == "state/repair-plan.json"
    assert json.loads(plan_path.read_text(encoding="utf-8"))["plan_sha256"] == planned["plan_sha256"]

    applied, apply_code = apply_repair_plan(root, plan_path=plan_path)
    assert apply_code == 0
    assert applied["status"] == "PASS"
    assert applied["applied"] == 1
    assert not source.exists()
    assert verify_receipts(root, job_id=job_id)[0]["status"] == "PASS"


def test_reconcile_and_repair_plan_use_selected_runtime_and_vault_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path)
    (roots.vault / "90_Archive/Captures/2026").mkdir(parents=True, exist_ok=True)
    source_relative = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    source = roots.vault / source_relative
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    job_id = str(uuid.uuid4())
    original_write = transactions_module.write_note_file

    def fail_after_publish(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash before source removal")

    monkeypatch.setattr(transactions_module, "write_note_file", fail_after_publish)
    interrupted, interrupted_code = finalize_capture(
        roots,
        capture_path=source_relative,
        expected_sha256=digest,
        outcome="discarded",
        modified_at="2026-09-14T11:00:00+09:00",
        job_id=job_id,
    )
    assert interrupted_code == 10
    assert interrupted["status"] == "FAIL"
    monkeypatch.setattr(transactions_module, "write_note_file", original_write)

    reconciled, reconcile_code = reconcile_transactions(roots, job_id=job_id)
    assert reconcile_code == 0
    assert reconciled["status"] == "REPAIR_REQUIRED"
    plan_path = roots.state / "repair-plan.json"
    plan, plan_code = repair_plan(roots, job_id=job_id, output=plan_path)
    assert plan_code == 0, plan
    assert plan["plan_path"] == "state/repair-plan.json"
    applied, apply_code = apply_repair_plan(roots, plan_path=plan_path)

    assert apply_code == 0, applied
    assert applied["status"] == "PASS"
    assert not source.exists()
    assert (roots.state / "receipts" / f"{job_id}-capture_finalize.json").is_file()
    assert not (roots.control / "runtime").exists()
    assert not (roots.control / "KnowledgeHub").exists()


def test_stale_repair_plan_fails_closed_before_vault_mutation(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    capture_path, source, digest = _capture(root, tmp_path)
    job_id = str(uuid.uuid4())
    original_write = transactions_module.write_note_file

    def fail_after_publish(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("simulated crash before source removal")

    monkeypatch.setattr(transactions_module, "write_note_file", fail_after_publish)
    first, first_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="triaged",
        modified_at="2026-09-14T11:00:00+09:00",
        job_id=job_id,
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    monkeypatch.setattr(transactions_module, "write_note_file", original_write)
    plan_path = fixture_path(root, "state") / "repair-plan.json"
    plan, code = repair_plan(root, output=plan_path)
    assert code == 0
    before = source.read_bytes()
    source.write_bytes(before + b"changed")

    result, apply_code = apply_repair_plan(root, plan_path=plan_path)
    assert apply_code == 30
    assert result["status"] == "CONFLICT"
    assert result["errors"][0]["code"] == "REPAIR_PLAN_STALE"
    assert source.read_bytes() == before + b"changed"
    assert plan["summary"]["repairable"] == 1


def test_receipt_verification_is_historical_and_detects_receipt_tampering(
    tmp_path: Path,
) -> None:
    root = _fresh_control_copy(tmp_path)
    capture_path, _source, digest = _capture(root, tmp_path)
    result, code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=digest,
        outcome="discarded",
        modified_at="2026-09-14T11:00:00+09:00",
    )
    assert code == 0
    job_id = result["job_id"]
    receipt_path = fixture_path(root, "state") / "receipts" / f"{job_id}-capture_finalize.json"
    destination = fixture_path(root, "vault") / result["destination"]
    destination.write_bytes(destination.read_bytes() + b"later note update\n")
    verified, verify_code = verify_receipts(root, job_id=job_id)
    assert verify_code == 0
    assert verified["status"] == "PASS"
    assert verified["verified"] == 1

    receipt_path.write_bytes(receipt_path.read_bytes().replace(b'"status":"completed"', b'"status":"changed"'))
    tampered, tampered_code = verify_receipts(root, job_id=job_id)
    assert tampered_code == 30
    assert tampered["status"] == "CONFLICT"


def test_reconcile_repairs_archive_after_directory_publish_fault(tmp_path: Path, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    project = create_project_bundle(root, title="Reconcile Archive", created_at="2026-09-14T09:00:00+09:00")
    assert project["status"] == "PASS"
    project_dir = fixture_path(root, "vault") / "20_Projects" / "Reconcile Archive"
    expected_hashes = {
        path.relative_to(fixture_path(root, "vault")).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project_dir.rglob("*")
        if path.is_file()
    }
    job_id = str(uuid.uuid4())
    original_rename = transactions_module.os.rename

    def fail_after_rename(source_path, destination_path):
        original_rename(source_path, destination_path)
        raise OSError("simulated crash after archive publish")

    monkeypatch.setattr(transactions_module.os, "rename", fail_after_rename)
    first, first_code = archive_project(
        root,
        project_path="20_Projects/Reconcile Archive/Reconcile Archive.md",
        expected_hashes=expected_hashes,
        job_id=job_id,
    )
    assert first_code == 10
    assert first["status"] == "FAIL"
    monkeypatch.setattr(transactions_module.os, "rename", original_rename)

    plan, plan_code = repair_plan(root)
    assert plan_code == 0
    assert plan["summary"]["repairable"] == 1
    assert plan["jobs"][0]["actions"] == ["complete_journal_and_receipt"]
    plan_path = fixture_path(root, "state") / "archive-repair-plan.json"
    repair_plan(root, output=plan_path)
    applied, apply_code = apply_repair_plan(root, plan_path=plan_path)
    assert apply_code == 0
    assert applied["status"] == "PASS"
    assert not project_dir.exists()
    assert (fixture_path(root, "vault") / "90_Archive" / "Projects" / "2026" / "Reconcile Archive").is_dir()
    assert verify_receipts(root, job_id=job_id)[0]["status"] == "PASS"
