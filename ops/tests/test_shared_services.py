from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support.control_factory import (
    fixture_path,
    make_portable_fixture_root,
    make_separate_portable_fixture_roots,
)

from vaultops.cli import main
from vaultops.projection import EXIT_OK, generate_projection
from vaultops.shared_services import KnowledgeServices

PROPOSAL_SOURCE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"


def _make_normalize_source(roots) -> tuple[Path, str]:
    source = roots.vault / PROPOSAL_SOURCE
    original = source.read_text(encoding="utf-8")
    _, remaining = original.split("---\n", 1)
    frontmatter, body = remaining.split("---\n", 1)
    lines = frontmatter.splitlines()
    schema_index = lines.index("schema_version: 1")
    id_index = lines.index("id: 11111111-1111-4111-8111-111111111111")
    lines[schema_index], lines[id_index] = lines[id_index], lines[schema_index]
    reordered = "\n".join(lines)
    source.write_text(f"---\n{reordered}\n---\n{body}", encoding="utf-8")
    raw = source.read_bytes()
    return source, hashlib.sha256(raw).hexdigest()


def _vault_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("operation", "hops"),
    (("search", 0), ("retrieve", 1)),
)
def test_cli_and_shared_read_service_return_identical_reports(
    tmp_path: Path,
    monkeypatch,
    capsys,
    operation: str,
    hops: int,
) -> None:
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        extra_inputs=("ops/tests/fixtures/c22_retrieval",),
        schema_version=3,
    )
    _projection, build_code = generate_projection(roots, generation_id="r11b-read-v1")
    assert build_code == EXIT_OK

    query = "플레이 루프"
    service = KnowledgeServices(roots)
    direct, direct_code = getattr(service, operation)(
        query,
        scope="project:guestbook-horror",
        limit=7,
        hops=hops,
        expected_generation_id="r11b-read-v1",
    )

    monkeypatch.setattr("sys.stdin", io.StringIO(query))
    cli_code = main(
        [
            operation,
            "--query-stdin",
            "--scope",
            "project:guestbook-horror",
            "--limit",
            "7",
            "--hops",
            str(hops),
            "--generation-id",
            "r11b-read-v1",
            "--root",
            str(roots.control),
        ]
    )
    cli_report = json.loads(capsys.readouterr().out)

    assert direct_code == EXIT_OK, direct
    assert cli_code == direct_code
    assert cli_report == direct
    assert direct["index_generation_id"] == "r11b-read-v1"
    assert direct["provider_called"] is False
    assert direct["mutation_performed"] is False
    assert direct["filters"]["scope"] == "project:guestbook-horror"


def test_cli_and_shared_read_service_preserve_index_unavailable_error(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        extra_inputs=("ops/tests/fixtures/c22_retrieval",),
        schema_version=3,
    )
    service = KnowledgeServices(roots)

    direct, direct_code = service.search("synthetic query")
    monkeypatch.setattr("sys.stdin", io.StringIO("synthetic query"))
    cli_code = main(["search", "--query-stdin", "--root", str(roots.control)])
    cli_report = json.loads(capsys.readouterr().out)

    assert direct_code != EXIT_OK
    assert cli_code == direct_code
    assert cli_report == direct
    assert direct["errors"][0]["code"] == "RETRIEVAL_INDEX_UNAVAILABLE"


def test_shared_service_import_does_not_import_cli() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from vaultops.shared_services import KnowledgeServices; "
                "assert 'vaultops.cli' not in sys.modules"
            ),
        ],
        cwd=source_root.parent,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_shared_services_requires_resolved_trusted_roots(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="resolved independent roots"):
        KnowledgeServices(tmp_path)  # type: ignore[arg-type]


def test_cli_and_shared_normalize_services_match_proposal_identity_and_digest(
    tmp_path: Path,
    capsys,
) -> None:
    direct_root = tmp_path / "direct"
    cli_root = tmp_path / "cli"
    direct_root.mkdir()
    cli_root.mkdir()
    direct_roots = make_separate_portable_fixture_roots(
        direct_root,
        review_queues=True,
        schema_version=3,
    )
    cli_roots = make_separate_portable_fixture_roots(
        cli_root,
        review_queues=True,
        schema_version=3,
    )
    direct_source, direct_hash = _make_normalize_source(direct_roots)
    cli_source, cli_hash = _make_normalize_source(cli_roots)
    assert direct_hash == cli_hash
    direct_target_before = direct_source.read_bytes()
    cli_target_before = cli_source.read_bytes()

    direct, direct_code = KnowledgeServices(direct_roots).create_normalize_proposal(
        source_path=PROPOSAL_SOURCE,
        expected_sha256=direct_hash,
    )
    cli_code = main(
        [
            "ai",
            "normalize",
            "--source",
            PROPOSAL_SOURCE,
            "--expected-sha256",
            cli_hash,
            "--root",
            str(cli_roots.control),
        ]
    )
    cli_report = json.loads(capsys.readouterr().out)

    assert direct_code == EXIT_OK, direct
    assert cli_code == direct_code
    assert direct["status"] == cli_report["status"] == "PASS"
    assert direct["proposal"]["proposal_id"] == cli_report["proposal"]["proposal_id"]
    assert direct["proposal_sha256"] == cli_report["proposal_sha256"]
    assert direct["proposal_path"] == cli_report["proposal_path"]
    assert direct["created"] is cli_report["created"] is True
    assert direct["mutation_performed"] is cli_report["mutation_performed"] is True
    assert direct["canonical_target_changed"] is cli_report["canonical_target_changed"] is False
    assert direct_source.read_bytes() == direct_target_before
    assert cli_source.read_bytes() == cli_target_before


def test_proposal_inspection_is_read_only_and_replay_or_stale_source_preserves_target(
    tmp_path: Path,
    capsys,
) -> None:
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        review_queues=True,
        schema_version=3,
    )
    source, source_hash = _make_normalize_source(roots)
    service = KnowledgeServices(roots)
    target_before = source.read_bytes()
    created, create_code = service.create_normalize_proposal(
        source_path=PROPOSAL_SOURCE,
        expected_sha256=source_hash,
    )
    assert create_code == EXIT_OK, created
    proposal_path = created["proposal_path"]
    vault_before_review = _vault_snapshot(roots.vault)

    direct_review, direct_review_code = service.inspect_proposal(proposal_path)
    cli_review_code = main(
        ["ai", "review", "--proposal", proposal_path, "--root", str(roots.control)]
    )
    cli_review = json.loads(capsys.readouterr().out)

    assert direct_review_code == cli_review_code == EXIT_OK
    assert cli_review == direct_review
    assert direct_review["provider_called"] is False
    assert direct_review["mutation_performed"] is False
    assert _vault_snapshot(roots.vault) == vault_before_review

    replay, replay_code = service.create_normalize_proposal(
        source_path=PROPOSAL_SOURCE,
        expected_sha256=source_hash,
    )
    assert replay_code == EXIT_OK
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True
    assert replay["proposal"]["proposal_id"] == created["proposal"]["proposal_id"]
    assert replay["proposal_sha256"] == created["proposal_sha256"]
    assert source.read_bytes() == target_before

    stale_target = target_before + b"\nsynthetic source drift\n"
    source.write_bytes(stale_target)
    stale, stale_code = service.create_normalize_proposal(
        source_path=PROPOSAL_SOURCE,
        expected_sha256=source_hash,
    )
    assert stale_code != EXIT_OK
    assert stale["errors"][0]["code"] == "C27_SOURCE_DRIFT"
    assert source.read_bytes() == stale_target


def test_cli_normalize_keeps_rejecting_draft_target_options(
    tmp_path: Path,
    capsys,
) -> None:
    root = make_portable_fixture_root(tmp_path, review_queues=True)
    source = fixture_path(root, "vault") / PROPOSAL_SOURCE
    original = source.read_text(encoding="utf-8")
    _, remaining = original.split("---\n", 1)
    frontmatter, body = remaining.split("---\n", 1)
    lines = frontmatter.splitlines()
    schema_index = lines.index("schema_version: 1")
    id_index = lines.index("id: 11111111-1111-4111-8111-111111111111")
    lines[schema_index], lines[id_index] = lines[id_index], lines[schema_index]
    reordered = "\n".join(lines)
    source.write_text(f"---\n{reordered}\n---\n{body}", encoding="utf-8")
    source_before = source.read_bytes()
    source_hash = hashlib.sha256(source_before).hexdigest()

    code = main(
        [
            "ai",
            "normalize",
            "--source",
            PROPOSAL_SOURCE,
            "--expected-sha256",
            source_hash,
            "--target-path",
            "40_Knowledge/Ideas/Unexpected Target.md",
            "--root",
            str(root),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert code != EXIT_OK
    assert report["errors"][0]["code"] == "ACTION_DENIED"
    assert source.read_bytes() == source_before
    assert not list(((fixture_path(root, "vault") / "01_AI_Review/Pending")).glob("*.md"))
