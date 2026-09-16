from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from vaultops.cli import main
from vaultops.projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    build_projection,
    canonical_json_bytes,
    generate_projection,
    read_current_projection,
)

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
    assert not (root / "runtime/index/exports/current.json").exists()
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


def test_c21_publish_replays_same_generation_and_reader_pins_one_pointer(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)

    first, first_code = generate_projection(root, generation_id="c21-fixture")
    assert first_code == EXIT_OK, first
    assert first["pointer_swapped"] is True
    pointer_path = root / "runtime/index/exports/current.json"
    pointer_before = pointer_path.read_bytes()
    generation_root = root / "runtime" / first["generation_root"]
    assert generation_root.is_dir()
    assert (generation_root / "notes.jsonl").stat().st_mode & 0o777 == 0o600
    assert generation_root.stat().st_mode & 0o777 == 0o700

    second, second_code = generate_projection(root, generation_id="c21-fixture")
    assert second_code == EXIT_OK, second
    assert second["pointer_swapped"] is False
    assert pointer_path.read_bytes() == pointer_before

    verified, verify_code = read_current_projection(root)
    assert verify_code == EXIT_OK, verified
    assert verified["status"] == "PASS"
    assert verified["generation_id"] == "c21-fixture"
    assert len(verified["notes"]) == first["note_count"]
    assert len(verified["edges"]) == first["edge_count"]
    assert verified["source_verified"] is True


def test_c21_cli_exposes_export_build_and_verify_without_provider_or_vault_mutation(
    tmp_path: Path, capsys
) -> None:
    root = _fresh_control_copy(tmp_path)
    before = _file_snapshot(root / "KnowledgeHub")

    assert main(["export", "jsonl", "--generation-id", "c21-cli", "--root", str(root)]) == EXIT_OK
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
    assert verified["generation_id"] == "c21-cli"
    assert verified["mutation_performed"] is False
    assert _file_snapshot(root / "KnowledgeHub") == before


def test_c21_reader_rejects_stale_source_and_tampered_generation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    report, code = generate_projection(root, generation_id="c21-fixture")
    assert code == EXIT_OK, report

    source = root / "KnowledgeHub/20_Projects/guestbook-horror/guestbook-horror.md"
    source.write_bytes(source.read_bytes() + b"\n")
    stale, stale_code = read_current_projection(root)
    assert stale_code == EXIT_CONFLICT
    assert stale["status"] == "FAIL"
    assert "stale" in stale["errors"][0]["message"]

    source.write_bytes(source.read_bytes().removesuffix(b"\n"))
    notes_path = root / "runtime" / report["notes_path"]
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
    path = root / "KnowledgeHub" / relative
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

    pointer_path = root / "runtime/index/exports/current.json"
    pointer = json.loads(pointer_path.read_bytes())
    manifest_path = root / "runtime" / pointer["manifest_path"]
    manifest_bytes = manifest_path.read_bytes()
    assert pointer["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest_bytes == canonical_json_bytes(json.loads(manifest_bytes))
