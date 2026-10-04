from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from support.control_factory import (
    fixture_path,
    make_portable_fixture_root,
    make_separate_portable_fixture_roots,
)

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
from vaultops.paths import ResolvedPaths
from vaultops.projection import generate_projection


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_portable_fixture_root(tmp_path)


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _write_eligibility_fixture(root: Path | ResolvedPaths) -> None:
    vault = root.vault if isinstance(root, ResolvedPaths) else fixture_path(root, "vault")
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
        path = vault / "40_Knowledge/Notes" / f"{title}.md"
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


def test_c29_cli_publishes_privacy_filtered_projection_to_separate_runtime(
    tmp_path: Path,
    capsys,
) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path)
    _write_eligibility_fixture(roots)
    vault_before = {
        path.relative_to(roots.vault).as_posix(): path.read_bytes()
        for path in roots.vault.rglob("*")
        if path.is_file()
    }

    assert main(
        [
            "ai",
            "projection",
            "build",
            "--profile",
            "remote",
            "--generation-id",
            "c29-remote-separated",
            "--root",
            str(roots.control),
        ]
    ) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["projection_profile"] == "remote"
    pointer = roots.state / "index/ai/remote/current.json"
    assert pointer.is_file()

    assert main(
        ["ai", "projection", "verify", "--profile", "remote", "--root", str(roots.control)]
    ) == EXIT_OK
    verified = json.loads(capsys.readouterr().out)
    assert verified["generation_id"] == "c29-remote-separated"
    assert all(record["properties"]["ai_policy"] == "remote_ok" for record in verified["notes"])
    assert _file_snapshot(roots.vault) == vault_before
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()

    pointer_before = pointer.read_bytes()
    source = roots.vault / "40_Knowledge/Notes/Denied C29.md"
    source.write_bytes(source.read_bytes() + b"\nsynthetic source drift\n")
    stale, stale_code = read_current_ai_projection(roots, profile="remote")
    assert stale_code == EXIT_CONFLICT
    assert "stale" in stale["errors"][0]["message"]
    assert pointer.read_bytes() == pointer_before


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

    local_pointer = (fixture_path(root, "state") / "index/ai/local/current.json")
    remote_pointer = (fixture_path(root, "state") / "index/ai/remote/current.json")
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

    denied = (fixture_path(root, "vault") / "40_Knowledge/Notes/Denied C29.md")
    denied.write_bytes(denied.read_bytes() + b"\nDRIFT")
    stale, stale_code = read_current_ai_projection(root, profile="remote")
    assert stale_code == EXIT_CONFLICT
    assert stale["status"] == "FAIL"
    assert "stale" in stale["errors"][0]["message"]

    clean_root = _fresh_control_copy(tmp_path / "clean")
    _write_eligibility_fixture(clean_root)
    full, full_code = generate_projection(clean_root, generation_id="c21-full")
    assert full_code == EXIT_OK, full
    pointer = fixture_path(clean_root, "state") / "index/exports/current.json"
    ai_pointer = fixture_path(clean_root, "state") / "index/ai/local/current.json"
    ai_pointer.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ai_pointer.parent.parent.chmod(0o700)
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
