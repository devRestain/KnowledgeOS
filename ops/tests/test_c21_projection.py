from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from support.control_factory import (
    fixture_path,
    make_portable_fixture_root,
    make_separate_portable_fixture_roots,
)

from vaultops.action_proposals import generate_draft_note_proposal, generate_normalize_proposal
from vaultops.interfaces.cli import main
from vaultops.projection import (
    CURRENT_POINTER,
    EXIT_CONFLICT,
    EXIT_OK,
    build_projection,
    canonical_json_bytes,
    generate_projection,
    read_current_projection,
)


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_portable_fixture_root(tmp_path)


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _jsonl_records(payload: bytes) -> list[dict[str, object]]:
    return [json.loads(line) for line in payload.decode("utf-8").splitlines()]


def test_c21_build_is_deterministic_and_does_not_write_runtime_or_vault(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    before = _file_snapshot(root)

    first = build_projection(root, generation_id="c21-fixture")
    second = build_projection(root, generation_id="c21-fixture")

    assert len(first.notes) == 10
    assert len(first.edges) > 0
    assert first.notes_bytes == second.notes_bytes
    assert first.edges_bytes == second.edges_bytes
    assert first.manifest_bytes == second.manifest_bytes
    assert first.source_snapshot_sha256 == second.source_snapshot_sha256
    assert not (fixture_path(root, "state") / "index/exports/current.json").exists()
    assert _file_snapshot(root) == before

    notes = _jsonl_records(first.notes_bytes)
    project = next(record for record in notes if record["id"] == "66666666-6666-4666-8666-666666666666")
    assert set(project["properties"]) == {
        "ai_policy",
        "ai_status",
        "aliases",
        "areas",
        "derived_from",
        "focus_rank",
        "implements",
        "next_action",
        "outcome",
        "people",
        "priority",
        "sensitivity",
        "tags",
        "target_date",
        "topics",
    }
    assert list(project["properties"]) == sorted(project["properties"])
    assert project["body"].startswith("# guestbook-horror\n")

    edges = _jsonl_records(first.edges_bytes)
    implements = next(edge for edge in edges if edge["predicate"] == "implements")
    assert implements["edge_kind"] == "semantic"
    assert implements["provenance"] == "canonical_property"
    assert implements["source_locator"] == "/implements/0"
    assert implements["object_locator"] is None
    derived = next(edge for edge in edges if edge["predicate"] == "derived_from")
    assert derived["object_locator"] == "#^daily-origin"



@pytest.mark.parametrize("action", ["normalize", "draft_note"])
def test_c21_generated_artifacts_are_excluded_while_user_review_is_live(
    tmp_path: Path, action: str,
) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    source = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    source_path = roots.vault / source
    original = source_path.read_text()
    # Valid but noncanonical ordering gives normalization an actual byte delta.
    source_path.write_text(original.replace(
        "schema_version: 1\nid: 11111111-1111-4111-8111-111111111111\n",
        "id: 11111111-1111-4111-8111-111111111111\nschema_version: 1\n"))
    source_bytes = source_path.read_bytes()
    before, code = generate_projection(roots)
    assert code == EXIT_OK, before
    arguments = {"source_path": source, "expected_sha256": hashlib.sha256(source_bytes).hexdigest()}
    if action == "normalize":
        generated, code = generate_normalize_proposal(roots, **arguments)
    else:
        generated, code = generate_draft_note_proposal(roots, title="Synthetic projection draft", **arguments)
    assert code == EXIT_OK and generated["status"] == "PASS", generated
    generated_path = roots.vault / generated["proposal_path"]
    assert generated_path.is_file() and source_path.read_bytes() == source_bytes
    verified, code = read_current_projection(roots, verify_sources=True)
    assert code == EXIT_OK and verified["source_verified"] is True, verified
    generation = verified["generation_id"]

    user_path = roots.vault / "01_AI_Review/Pending/User-authored Review.md"
    user_path.write_text(generated_path.read_text()
        .replace(generated["proposal"]["proposal_id"], "a0526fd1-88db-41a3-b45f-a013309831dd")
        .replace(generated_path.stem, user_path.stem))
    verified, code = read_current_projection(roots, verify_sources=True)
    assert code == EXIT_OK, verified
    assert verified["generation_id"] == generation
    paths = {note["path"] for note in verified["notes"]}
    assert user_path.relative_to(roots.vault).as_posix() in paths
    assert generated["proposal_path"] not in paths

    user_path.write_text(user_path.read_text() + "\nUser review changed.\n")
    current, code = read_current_projection(roots, verify_sources=True)
    assert code == EXIT_OK, current
    assert "User review changed." in next(note["body"] for note in current["notes"]
                                        if note["path"] == user_path.relative_to(roots.vault).as_posix())
    source_path.write_bytes(source_bytes + b"\nCanonical source changed.\n")
    current, code = read_current_projection(roots, verify_sources=True)
    assert code == EXIT_OK, current
    assert "Canonical source changed." in next(note["body"] for note in current["notes"] if note["path"] == source)


def test_c21_cli_uses_configured_separate_vault_and_runtime_roots(tmp_path: Path, capsys) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path)
    vault_before = _file_snapshot(roots.vault)

    built = build_projection(roots, generation_id="c21-separated")
    assert len(built.notes) == 10
    assert not (roots.state / CURRENT_POINTER).exists()

    assert main(
        ["index", "export", "--generation-id", "c21-separated-cli", "--root", str(roots.control)]
    ) == EXIT_OK
    exported = json.loads(capsys.readouterr().out)
    assert exported["provider_called"] is False
    assert (roots.state / CURRENT_POINTER).is_file()
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()

    assert main(["index", "verify", "--root", str(roots.control)]) == EXIT_OK
    verified = json.loads(capsys.readouterr().out)
    assert verified["generation_id"].startswith("local-")
    assert _file_snapshot(roots.vault) == vault_before


def test_c21_publish_replays_same_generation_and_reader_pins_one_pointer(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)

    first, first_code = generate_projection(root, generation_id="c21-fixture")
    assert first_code == EXIT_OK, first
    assert first["pointer_swapped"] is True
    pointer_path = (fixture_path(root, "state") / "index/exports/current.json")
    pointer_before = pointer_path.read_bytes()
    generation_root = fixture_path(root, "runtime") / first["generation_root"]
    assert generation_root.is_dir()
    assert (generation_root / "notes.jsonl").stat().st_mode & 0o777 == 0o600
    assert generation_root.stat().st_mode & 0o777 == 0o700

    second, second_code = generate_projection(root, generation_id="c21-fixture")
    assert second_code == EXIT_OK, second
    assert second["pointer_swapped"] is False
    assert pointer_path.read_bytes() == pointer_before

    verified, verify_code = read_current_projection(root, verify_sources=False)
    assert verify_code == EXIT_OK, verified
    assert verified["status"] == "PASS"
    assert verified["generation_id"] == "c21-fixture"
    assert len(verified["notes"]) == first["note_count"]
    assert len(verified["edges"]) == first["edge_count"]
    assert verified["source_verified"] is False


def test_c21_cli_exposes_export_build_and_verify_without_provider_or_vault_mutation(
    tmp_path: Path, capsys
) -> None:
    root = _fresh_control_copy(tmp_path)
    before = _file_snapshot(fixture_path(root, "vault"))

    assert main(["index", "export", "--generation-id", "c21-cli", "--root", str(root)]) == EXIT_OK
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "PASS"
    assert exported["operation"] == "index build"
    assert exported["provider_called"] is False

    assert main(["index", "build", "--generation-id", "c21-cli", "--root", str(root)]) == EXIT_OK
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["pointer_swapped"] is False

    assert main(["index", "verify", "--root", str(root)]) == EXIT_OK
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "PASS"
    assert verified["generation_id"].startswith("local-")
    assert verified["mutation_performed"] is False
    assert _file_snapshot(fixture_path(root, "vault")) == before


def test_c21_live_reader_tracks_source_and_frozen_reader_rejects_tampering(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    report, code = generate_projection(root, generation_id="c21-fixture")
    assert code == EXIT_OK, report

    source = (fixture_path(root, "vault") / "20_Projects/guestbook-horror/guestbook-horror.md")
    source.write_bytes(source.read_bytes() + b"\n")
    current, current_code = read_current_projection(root)
    assert current_code == EXIT_OK, current
    projected = next(note for note in current["notes"] if note["path"] == source.relative_to(fixture_path(root, "vault")).as_posix())
    assert projected["content_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()

    source.write_bytes(source.read_bytes().removesuffix(b"\n"))
    notes_path = fixture_path(root, "runtime") / report["notes_path"]
    notes_path.write_bytes(notes_path.read_bytes().replace(b"guestbook-horror", b"guestbook-horrors", 1))
    tampered, tampered_code = read_current_projection(root, verify_sources=False)
    assert tampered_code == EXIT_CONFLICT
    assert tampered["status"] == "FAIL"
    assert "digest" in tampered["errors"][0]["message"]


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        (
            "40_Knowledge/Notes/CRLF.md",
            b"---\r\nschema_version: 1\r\n---\r\n# CRLF\r\n",
        ),
        (
            "40_Knowledge/Notes/invalid-utf8.md",
            b"\xff\xfe",
        ),
    ],
)
def test_c21_rejects_noncanonical_source_bytes(tmp_path: Path, relative: str, payload: bytes) -> None:
    root = _fresh_control_copy(tmp_path)
    path = fixture_path(root, "vault") / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    report, code = generate_projection(root, generation_id="c21-invalid")

    assert code == EXIT_CONFLICT
    assert report["status"] == "FAIL"
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False


def test_c21_manifest_and_pointer_hashes_use_canonical_json_bytes(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    report, code = generate_projection(root, generation_id="c21-hash")
    assert code == EXIT_OK, report

    pointer_path = (fixture_path(root, "state") / "index/exports/current.json")
    pointer = json.loads(pointer_path.read_bytes())
    manifest_path = fixture_path(root, "runtime") / pointer["manifest_path"]
    manifest_bytes = manifest_path.read_bytes()
    assert pointer["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_bytes == canonical_json_bytes(json.loads(manifest_bytes))


def test_c21_first_use_needs_no_publication_and_reparses_only_changed_documents(tmp_path, monkeypatch):
    from vaultops import projection

    roots = make_separate_portable_fixture_roots(tmp_path)
    calls = []
    original = projection.source_properties

    def observed(path, raw, **kwargs):
        calls.append(path)
        return original(path, raw, **kwargs)

    monkeypatch.setattr(projection, "source_properties", observed)
    first = projection.load_current_projection(roots)
    assert first.notes and calls
    assert not (roots.state / CURRENT_POINTER).exists()
    calls.clear()
    assert projection.load_current_projection(roots).manifest == first.manifest
    assert calls == []

    path = "40_Knowledge/Notes/Plain first use.md"
    note = roots.vault / path
    note.write_text("# Current\nCreated directly in Obsidian.\n")
    current = projection.load_current_projection(roots)
    assert calls == [path]
    projected = next(item for item in current.notes if item["path"] == path)
    assert projected["body"] == note.read_text()
    assert projected["content_hash"] == hashlib.sha256(note.read_bytes()).hexdigest()
    assert projected["properties"]["projection_timestamp_source"] == "filesystem"
    defaults = original(path, note.read_bytes())
    assert "created" not in defaults and "modified" not in defaults
    assert current.manifest["generation_id"] == first.manifest["generation_id"]
    calls.clear()
    note.write_text("# Current\nEdited directly in Obsidian.\n")
    current = projection.load_current_projection(roots)
    assert calls == [path]
    assert next(item for item in current.notes if item["path"] == path)["content_hash"] != projected["content_hash"]
