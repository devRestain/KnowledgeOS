from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from vaultops.local_commands import create_note
from vaultops.transactions import archive_project, finalize_capture, import_asset
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_asset_import_is_safe_hash_bound_and_create_only(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"portable asset bytes\x00\x01")
    digest = _sha256(source)

    report, exit_code = import_asset(
        root,
        source_path=source,
        target_directory="Documents",
        expected_sha256=digest,
    )

    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["asset"] == "[[80_Assets/Documents/source.pdf]]"
    target = root / "KnowledgeHub/80_Assets/Documents/source.pdf"
    assert target.read_bytes() == source.read_bytes()
    assert report["provenance"]["source_sha256"] == digest

    replay, replay_code = import_asset(
        root,
        source_path=source,
        target_directory="Documents",
        expected_sha256=digest,
    )
    assert replay_code == 30
    assert replay["status"] == "CONFLICT"
    assert target.read_bytes() == source.read_bytes()

    unsafe_source = tmp_path / "unsafe.pdf"
    unsafe_source.symlink_to(source)
    unsafe, unsafe_code = import_asset(root, source_path=unsafe_source, target_directory="Documents")
    assert unsafe_code == 10
    assert unsafe["errors"][0]["code"] == "ASSET_INPUT_INVALID"

    executable_source = tmp_path / "unsafe.sh"
    executable_source.write_text("#!/bin/sh\n", encoding="utf-8")
    executable, executable_code = import_asset(
        root,
        source_path=executable_source,
        target_directory="Documents",
    )
    assert executable_code == 10
    assert executable["errors"][0]["code"] == "ASSET_INPUT_INVALID"


def test_capture_finalize_preserves_identity_body_and_related_link(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    project = create_project_bundle(
        root,
        title="Finalize Project",
        created_at="2026-09-13T09:00:00+09:00",
    )
    assert project["status"] == "PASS"
    body_file = tmp_path / "capture.md"
    body_file.write_text("capture body\n", encoding="utf-8")
    capture, capture_code = create_note(
        root,
        note_type="capture",
        title="Finalize Capture",
        body_file=body_file,
        created_at="2026-09-13T10:00:00+09:00",
    )
    assert capture_code == 0
    capture_path = str(capture["path"])
    source = root / "KnowledgeHub" / capture_path
    before = source.read_bytes()
    document_id = capture["note"]["id"]

    report, exit_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256=hashlib.sha256(before).hexdigest(),
        outcome="triaged",
        related="20_Projects/Finalize Project/Finalize Project.md",
        modified_at="2026-09-13T11:00:00+09:00",
    )

    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["note_id"] == document_id
    destination = root / "KnowledgeHub" / str(report["destination"])
    assert not source.exists()
    assert destination.is_file()
    assert "Finalize Capture" in destination.read_text(encoding="utf-8")
    assert "status: triaged" in destination.read_text(encoding="utf-8")
    assert "'[[Finalize Project]]'" in destination.read_text(encoding="utf-8")


def test_capture_finalize_rejects_digest_drift_without_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    body_file = tmp_path / "capture.md"
    body_file.write_text("capture body\n", encoding="utf-8")
    capture, exit_code = create_note(
        root,
        note_type="capture",
        title="Guarded Capture",
        body_file=body_file,
        created_at="2026-09-13T10:00:00+09:00",
    )
    assert exit_code == 0
    capture_path = str(capture["path"])
    source = root / "KnowledgeHub" / capture_path
    before = source.read_bytes()

    report, finalize_code = finalize_capture(
        root,
        capture_path=capture_path,
        expected_sha256="0" * 64,
        outcome="discarded",
    )

    assert finalize_code == 30
    assert report["status"] == "CONFLICT"
    assert source.read_bytes() == before
    assert not list((root / "KnowledgeHub/90_Archive/Captures").rglob("*.md"))


def test_project_archive_requires_exact_hashes_and_preserves_bundle_bytes(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    project = create_project_bundle(
        root,
        title="Archive Project",
        created_at="2026-09-13T09:00:00+09:00",
    )
    assert project["status"] == "PASS"
    child, child_code = create_note(
        root,
        note_type="project_note",
        title="Archive Working Note",
        project="20_Projects/Archive Project/Archive Project.md",
        created_at="2026-09-13T09:10:00+09:00",
    )
    assert child_code == 0
    assert child["status"] == "PASS"
    project_dir = root / "KnowledgeHub/20_Projects/Archive Project"
    before = {
        path.relative_to(root / "KnowledgeHub").as_posix(): path.read_bytes()
        for path in project_dir.rglob("*")
        if path.is_file()
    }
    hashes = {relative: hashlib.sha256(content).hexdigest() for relative, content in before.items()}

    report, archive_code = archive_project(
        root,
        project_path="20_Projects/Archive Project/Archive Project.md",
        expected_hashes=hashes,
    )

    assert archive_code == 0
    assert report["status"] == "PASS"
    assert report["identity_preserved"] is True
    assert report["links_preserved"] is True
    assert not project_dir.exists()
    destination_dir = root / "KnowledgeHub/90_Archive/Projects/2026/Archive Project"
    for relative, content in before.items():
        suffix = Path(relative).relative_to("20_Projects/Archive Project")
        assert (destination_dir / suffix).read_bytes() == content


def test_project_archive_hash_mismatch_does_not_move_bundle(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    project = create_project_bundle(
        root,
        title="Blocked Archive",
        created_at="2026-09-13T09:00:00+09:00",
    )
    assert project["status"] == "PASS"
    project_dir = root / "KnowledgeHub/20_Projects/Blocked Archive"
    hashes = {
        path.relative_to(root / "KnowledgeHub").as_posix(): "f" * 64
        for path in project_dir.rglob("*")
        if path.is_file()
    }

    report, archive_code = archive_project(
        root,
        project_path="20_Projects/Blocked Archive/Blocked Archive.md",
        expected_hashes=hashes,
    )

    assert archive_code == 30
    assert report["status"] == "CONFLICT"
    assert project_dir.is_dir()
    assert not (root / "KnowledgeHub/90_Archive/Projects/2026/Blocked Archive").exists()
