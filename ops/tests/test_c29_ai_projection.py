from __future__ import annotations

import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from vaultops.ai_projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    ai_edge_record_schema,
    ai_note_record_schema,
    build_ai_projection,
    generate_ai_projection,
    read_current_ai_projection,
)
from vaultops.cli import main
from vaultops.note_engine import render_frontmatter
from vaultops.projection import generate_projection

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            "KnowledgeHub",
            "runtime",
        ),
    )
    shutil.copytree(FIXTURE_ROOT, root / "KnowledgeHub")
    return root


def _write_eligibility_fixture(root: Path) -> None:
    notes = (
        (
            "Remote C29",
            "c29f0000-0000-4000-8000-000000000029",
            "personal",
            "remote_ok",
            "REMOTE_C29_BODY",
            None,
        ),
        (
            "Local C29",
            "c29f0000-0000-4000-8000-000000000030",
            "personal",
            "local_only",
            "LOCAL_ONLY_C29_BODY",
            None,
        ),
        (
            "Denied C29",
            "c29f0000-0000-4000-8000-000000000031",
            "personal",
            "deny",
            "DENIED_C29_BODY",
            None,
        ),
        (
            "Confidential C29",
            "c29f0000-0000-4000-8000-000000000032",
            "confidential",
            "local_only",
            "CONFIDENTIAL_C29_BODY",
            None,
        ),
        (
            "Host C29",
            "c29f0000-0000-4000-8000-000000000033",
            "personal",
            "remote_ok",
            "HOST_C29_BODY",
            ["[[Remote C29]]", "[[Denied C29]]", "[[Confidential C29]]"],
        ),
    )
    for title, identifier, sensitivity, ai_policy, body_marker, related in notes:
        properties: dict[str, object] = {
            "schema_version": 1,
            "id": identifier,
            "type": "knowledge",
            "title": title,
            "status": "developing",
            "created": "2026-09-17T10:00:00+09:00",
            "modified": "2026-09-17T10:00:00+09:00",
            "aliases": [],
            "tags": [],
            "sensitivity": sensitivity,
            "ai_policy": ai_policy,
            "ai_status": "idle",
            "claim": f"claim-{identifier[-2:]}",
            "confidence": "high",
            "last_reviewed": "2026-09-17",
            "related": related or [],
        }
        path = root / "KnowledgeHub/40_Knowledge/Notes" / f"{title}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_frontmatter(properties, f"# {title}\n\n{body_marker}\n"),
            encoding="utf-8",
        )


def test_c29_profiles_filter_notes_properties_and_incident_edges_before_serialization(
    tmp_path: Path,
) -> None:
    root = _fresh_control_copy(tmp_path)
    _write_eligibility_fixture(root)

    local = build_ai_projection(root, profile="local", generation_id="c29-local")
    remote = build_ai_projection(root, profile="remote", generation_id="c29-remote")

    local_titles = {record["title"] for record in local.notes}
    remote_titles = {record["title"] for record in remote.notes}
    assert "Local C29" in local_titles
    assert "Remote C29" in local_titles
    assert "Host C29" in local_titles
    assert "Denied C29" not in local_titles
    assert "Confidential C29" not in local_titles
    assert "Remote C29" in remote_titles
    assert "Host C29" in remote_titles
    assert "Local C29" not in remote_titles
    assert "Denied C29" not in remote_titles
    assert "Confidential C29" not in remote_titles

    local_host = next(record for record in local.notes if record["title"] == "Host C29")
    remote_host = next(record for record in remote.notes if record["title"] == "Host C29")
    assert local_host["properties"]["related"] == ["[[Remote C29]]"]
    assert remote_host["properties"]["related"] == ["[[Remote C29]]"]
    assert all(
        edge["object_id"] in {record["id"] for record in local.notes}
        for edge in local.edges
    )
    assert all(
        edge["object_id"] in {record["id"] for record in remote.notes}
        for edge in remote.edges
    )
    for forbidden in (b"Denied C29", b"DENIED_C29_BODY", b"Confidential C29", b"CONFIDENTIAL_C29_BODY"):
        assert forbidden not in local.notes_bytes
        assert forbidden not in local.edges_bytes
        assert forbidden not in remote.notes_bytes
        assert forbidden not in remote.edges_bytes
    assert local.manifest["eligibility_class"] == "local_eligible"
    assert remote.manifest["eligibility_class"] == "remote_eligible"
    assert local.manifest["source_note_count"] > local.manifest["eligible_note_count"]
    assert remote.manifest["eligible_note_count"] < local.manifest["eligible_note_count"]


def test_c29_publishes_separate_atomic_profile_pointers_and_replays_immutably(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _write_eligibility_fixture(root)

    local_report, local_code = generate_ai_projection(root, profile="local", generation_id="c29-local")
    assert local_code == EXIT_OK, local_report
    remote_report, remote_code = generate_ai_projection(root, profile="remote", generation_id="c29-remote")
    assert remote_code == EXIT_OK, remote_report
    replay_report, replay_code = generate_ai_projection(root, profile="local", generation_id="c29-local")
    assert replay_code == EXIT_OK, replay_report
    assert replay_report["pointer_swapped"] is False

    local_pointer = root / "runtime/index/ai/local/current.json"
    remote_pointer = root / "runtime/index/ai/remote/current.json"
    assert json.loads(local_pointer.read_text(encoding="utf-8"))["projection_profile"] == "local"
    assert json.loads(remote_pointer.read_text(encoding="utf-8"))["projection_profile"] == "remote"
    assert local_pointer.stat().st_mode & 0o777 == 0o600
    assert remote_pointer.stat().st_mode & 0o777 == 0o600

    local_verified, local_verify_code = read_current_ai_projection(root, profile="local")
    remote_verified, remote_verify_code = read_current_ai_projection(root, profile="remote")
    assert local_verify_code == EXIT_OK, local_verified
    assert remote_verify_code == EXIT_OK, remote_verified
    assert local_verified["source_verified"] is True
    assert remote_verified["source_verified"] is True
    assert local_verified["generation_id"] != remote_verified["generation_id"]


def test_c29_rejects_source_drift_and_a_c21_full_content_pointer(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _write_eligibility_fixture(root)
    report, code = generate_ai_projection(root, profile="remote", generation_id="c29-remote")
    assert code == EXIT_OK, report

    denied = root / "KnowledgeHub/40_Knowledge/Notes/Denied C29.md"
    denied.write_bytes(denied.read_bytes() + b"\nDRIFT")
    stale, stale_code = read_current_ai_projection(root, profile="remote")
    assert stale_code == EXIT_CONFLICT
    assert stale["status"] == "FAIL"
    assert "stale" in stale["errors"][0]["message"]

    clean_root = _fresh_control_copy(tmp_path / "clean")
    _write_eligibility_fixture(clean_root)
    full, full_code = generate_projection(clean_root, generation_id="c21-full")
    assert full_code == EXIT_OK, full
    pointer = clean_root / "runtime/index/exports/current.json"
    ai_pointer = clean_root / "runtime/index/ai/local/current.json"
    ai_pointer.parent.mkdir(parents=True, exist_ok=True)
    ai_pointer.write_bytes(pointer.read_bytes())
    ai_pointer.chmod(0o600)
    rejected, rejected_code = read_current_ai_projection(clean_root, profile="local")
    assert rejected_code == EXIT_CONFLICT
    assert rejected["status"] == "FAIL"
    assert "privacy-minimized" in rejected["errors"][0]["message"]


def test_c29_cli_and_generated_record_schemas_are_profile_explicit(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    _write_eligibility_fixture(root)

    assert main(["ai", "projection", "build", "--profile", "remote", "--root", str(root)]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["capability"] == "C29"
    assert report["projection_profile"] == "remote"
    assert main(["ai", "projection", "verify", "--profile", "remote", "--root", str(root)]) == EXIT_OK
    verified = json.loads(capsys.readouterr().out)
    assert verified["eligibility_class"] == "remote_eligible"
    assert all(
        not list(schema_validator.iter_errors(record))
        for schema_validator, records in (
            (
                Draft202012Validator(ai_note_record_schema()),
                verified["notes"],
            ),
            (
                Draft202012Validator(ai_edge_record_schema()),
                verified["edges"],
            ),
        )
        for record in records
    )
