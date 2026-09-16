from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from vaultops.cli import main
from vaultops.triage import deterministic_triage

CONTROL_ROOT = Path(__file__).resolve().parents[2]
SOURCE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
DAILY = "10_Journal/Daily/2026/2026-09-09.md"


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    fixture = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"
    shutil.copytree(fixture, root / "KnowledgeHub")
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_triage_is_deterministic_typed_and_read_only(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source = root / "KnowledgeHub" / SOURCE
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    before = _files(root)

    first, first_code = deterministic_triage(root, source_path=SOURCE, expected_sha256=digest)
    second, second_code = deterministic_triage(root, source_path=SOURCE, expected_sha256=digest)

    assert first_code == second_code == 0
    assert first == second
    assert first["status"] == "PROPOSED"
    assert first["provider_called"] is False
    assert first["mutation_performed"] is False
    assert first["proposal"]["requested_mutations"] == []
    assert 1 <= len(first["proposal"]["candidates"]) <= 5
    assert _files(root) == before


def test_triage_rejects_drift_denied_source_and_unbound_daily(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    source = root / "KnowledgeHub" / SOURCE

    drift, drift_code = deterministic_triage(root, source_path=SOURCE, expected_sha256="0" * 64)
    assert drift_code == 30
    assert drift["errors"][0]["code"] == "TRIAGE_SOURCE_DRIFT"

    daily = root / "KnowledgeHub" / DAILY
    daily_digest = hashlib.sha256(daily.read_bytes()).hexdigest()
    missing, missing_code = deterministic_triage(root, source_path=DAILY, expected_sha256=daily_digest)
    assert missing_code == 30
    assert missing["errors"][0]["code"] == "TRIAGE_DAILY_LOCATOR_REQUIRED"

    source.write_bytes(source.read_bytes().replace(b"ai_policy: ask", b"ai_policy: deny"))
    denied_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    denied, denied_code = deterministic_triage(root, source_path=SOURCE, expected_sha256=denied_digest)
    assert denied_code == 30
    assert denied["errors"][0]["code"] == "TRIAGE_PRIVACY_DENIED"


def test_triage_cli_is_proposal_only(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    source = root / "KnowledgeHub" / SOURCE
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    assert main(["ai", "triage", "--source", SOURCE, "--expected-sha256", digest, "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert '"provider_called": false' in output
    assert '"mutation_performed": false' in output
