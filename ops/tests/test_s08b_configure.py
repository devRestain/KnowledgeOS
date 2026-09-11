from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from vaultops.bridge_contract import (
    canonical_json_bytes,
    remote_identity_sha256,
    validate_root_sentinel,
)
from vaultops.configure import configure
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
VAULT_UUID = "411602c1-5278-4a8b-8b96-9183fb6ef8c2"
REMOTE = "git@github.com:devRestain/KnowledgeHub.git"


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


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
    _run_git(root, "init", "--initial-branch", "main")
    vault = root / "KnowledgeHub"
    vault.mkdir()
    (vault / ".gitignore").write_text(".DS_Store\n", encoding="utf-8")
    _run_git(vault, "init", "--initial-branch", "main")
    _run_git(vault, "config", "user.email", "test@example.invalid")
    _run_git(vault, "config", "user.name", "KnowledgeOS Test")
    (vault / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run_git(vault, "add", "--", "seed.txt")
    _run_git(vault, "commit", "-m", "test seed")
    _run_git(vault, "remote", "add", "origin", "https://github.com/devRestain/KnowledgeHub.git")
    head = _run_git(vault, "rev-parse", "HEAD")
    _run_git(vault, "update-ref", "refs/remotes/origin/main", head)
    return root


def _configure(root: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "remote": REMOTE,
        "branch": "main",
        "vault_uuid": VAULT_UUID,
        "canonical_vault_name": "KnowledgeHub",
        "sensitive_data_confirmed": True,
    }
    values.update(overrides)
    return configure(root, **values)


def test_configure_creates_and_validates_the_production_sentinel(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)

    report = _configure(root)

    assert report["status"] == "PASS"
    assert report["sentinel_write"] == "CREATED"
    assert report["remote_identity_sha256"] == "a8c9310a232c9d41110f113aedb0bcdd6483571db43235109d092bdaa4ba3146"
    sentinel_path = root / "KnowledgeHub/.knowledgeos-root.json"
    document = json.loads(sentinel_path.read_text(encoding="utf-8"))
    blueprint = load_yaml_file(root / "blueprint/blueprint.yaml")
    assert validate_root_sentinel(
        document,
        blueprint,
        expected={
            "vault_uuid": VAULT_UUID,
            "canonical_vault_name": "KnowledgeHub",
            "remote_identity_sha256": remote_identity_sha256(REMOTE),
            "expected_branch": "main",
        },
    ).passed
    assert sentinel_path.read_bytes() == canonical_json_bytes(document)


def test_configure_is_idempotent_but_refuses_different_sentinel_bytes(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    first = _configure(root)
    second = _configure(root)
    assert first["sentinel_write"] == "CREATED"
    assert second["sentinel_write"] == "EXISTING"

    sentinel_path = root / "KnowledgeHub/.knowledgeos-root.json"
    original = sentinel_path.read_bytes()
    sentinel_path.write_bytes(original.replace(b"KnowledgeHub", b"WrongVault"))
    conflict = _configure(root)
    assert conflict["status"] == "FAIL"
    assert conflict["errors"][0]["code"] == "CONFIGURE_SENTINEL_CONFLICT"


def test_configure_requires_confirmed_boundary_and_matching_origin(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    unconfirmed = _configure(root, sensitive_data_confirmed=False)
    assert unconfirmed["status"] == "FAIL"
    assert unconfirmed["errors"][0]["code"] == "CONFIGURE_SENSITIVE_BOUNDARY_REQUIRED"
    assert not (root / "KnowledgeHub/.knowledgeos-root.json").exists()

    mismatch = _configure(root, remote="https://github.com/devRestain/KnowledgeOS.git")
    assert mismatch["status"] == "FAIL"
    assert mismatch["errors"][0]["code"] == "CONFIGURE_REMOTE_MISMATCH"
    assert not (root / "KnowledgeHub/.knowledgeos-root.json").exists()


def test_configure_rejects_wrong_branch_or_missing_tracking_ref(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    wrong_branch = _configure(root, branch="release")
    assert wrong_branch["status"] == "FAIL"
    assert wrong_branch["errors"][0]["code"] == "CONFIGURE_BRANCH_MISMATCH"

    vault = root / "KnowledgeHub"
    _run_git(vault, "update-ref", "-d", "refs/remotes/origin/main")
    missing_ref = _configure(root)
    assert missing_ref["status"] == "FAIL"
    assert missing_ref["errors"][0]["code"] == "CONFIGURE_REMOTE_BRANCH_MISSING"
