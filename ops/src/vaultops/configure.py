"""S08B Git identity configuration and production root-sentinel writer."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .blueprint import validate_blueprint
from .bridge_contract import (
    BRANCH_PATTERN,
    canonical_json_bytes,
    canonicalize_github_remote,
    remote_identity_sha256,
    validate_root_sentinel,
)
from .yaml_safe import load_yaml_file

SENTINEL_RELATIVE_PATH = ".knowledgeos-root.json"


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _issue(code: str, message: str, locator: str = "/") -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message}


def _failure(mode: str, errors: list[dict[str, str]], **details: Any) -> dict[str, Any]:
    return {
        "operation": "configure",
        "mode": mode,
        **details,
        "status": "FAIL",
        "errors": errors,
    }


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if path.exists() or path.is_symlink():
            path.unlink()
        raise


def _prompt(value: str | None, label: str) -> str:
    if value is not None:
        return value
    return input(f"{label}: ").strip()


def configure(
    root: str | Path,
    *,
    remote: str | None = None,
    branch: str | None = None,
    vault_uuid: str | None = None,
    canonical_vault_name: str | None = None,
    sensitive_data_confirmed: bool = False,
    interactive: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Verify the notes topology and create the production root sentinel.

    The command never changes a Git remote, branch, history, index, or
    working-tree file other than the missing sentinel itself.  A configured
    ``origin`` and a local ``origin/<branch>`` tracking ref are required; a
    later network preflight can independently establish that the remote ref
    is current.
    """

    mode = "dry-run" if dry_run else "apply"
    workspace = Path(root).resolve()
    validation = validate_blueprint(workspace)
    if not validation.passed:
        return _failure(mode, validation.report.get("errors", []))

    try:
        blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
        if not isinstance(blueprint, dict):
            return _failure(mode, [_issue("CONFIGURE_BLUEPRINT_INVALID", "Blueprint root must be an object")])
        contract = blueprint["mobile_install_gate"]["root_sentinel_contract"]
        expected_name = str(contract["canonical_vault_name"])
        remote = _prompt(remote, "notes repository remote URL") if interactive else remote
        branch = _prompt(branch, "expected branch") if interactive else branch
        vault_uuid = _prompt(vault_uuid, "Vault UUID") if interactive else vault_uuid
        canonical_vault_name = (
            _prompt(canonical_vault_name, "canonical Vault name")
            if interactive
            else canonical_vault_name
        )
    except (OSError, TypeError, ValueError, KeyError, EOFError) as error:
        return _failure(mode, [_issue("CONFIGURE_INPUT_INVALID", str(error))])

    if not sensitive_data_confirmed:
        return _failure(
            mode,
            [
                _issue(
                    "CONFIGURE_SENSITIVE_BOUNDARY_REQUIRED",
                    "explicit sensitive-data boundary confirmation is required",
                    "/sensitive_data_boundary",
                )
            ],
        )
    missing = [
        ("remote", remote),
        ("expected_branch", branch),
        ("vault_uuid", vault_uuid),
    ]
    missing_fields = [field for field, value in missing if not isinstance(value, str) or not value]
    if missing_fields:
        return _failure(
            mode,
            [_issue("CONFIGURE_INPUT_REQUIRED", f"required input is missing: {', '.join(missing_fields)}")],
        )
    if canonical_vault_name is not None and canonical_vault_name != expected_name:
        return _failure(
            mode,
            [
                _issue(
                    "CONFIGURE_CANONICAL_NAME_MISMATCH",
                    f"canonical Vault name must be {expected_name!r}",
                    "/canonical_vault_name",
                )
            ],
        )
    if not re.fullmatch(BRANCH_PATTERN, branch):
        return _failure(
            mode,
            [_issue("CONFIGURE_BRANCH_INVALID", "expected branch does not satisfy the branch contract", "/expected_branch")],
        )

    vault_root = workspace / "KnowledgeHub"
    sentinel = vault_root / SENTINEL_RELATIVE_PATH
    if not vault_root.is_dir() or vault_root.is_symlink():
        return _failure(mode, [_issue("CONFIGURE_VAULT_ROOT_INVALID", "KnowledgeHub must be a real directory")])
    if sentinel.is_symlink():
        return _failure(mode, [_issue("CONFIGURE_SENTINEL_SYMLINK", "sentinel must not be a symlink")])

    control_code, control_git, control_error = _git(workspace, "rev-parse", "--show-toplevel")
    vault_code, vault_git, vault_error = _git(vault_root, "rev-parse", "--show-toplevel")
    if control_code != 0 or vault_code != 0:
        return _failure(
            mode,
            [
                _issue(
                    "CONFIGURE_GIT_ROOT_INVALID",
                    f"both independent Git roots are required: control={control_error or control_git}; vault={vault_error or vault_git}",
                )
            ],
        )
    if Path(control_git).resolve() != workspace or Path(vault_git).resolve() != vault_root:
        return _failure(
            mode,
            [_issue("CONFIGURE_GIT_ROOT_INVALID", "control and KnowledgeHub Git roots are not the expected independent roots")],
        )

    remote_code, configured_remote, remote_error = _git(vault_root, "config", "--get", "remote.origin.url")
    if remote_code != 0 or not configured_remote:
        return _failure(
            mode,
            [_issue("CONFIGURE_REMOTE_MISSING", f"KnowledgeHub origin is not configured: {remote_error or configured_remote}", "/remote")],
        )
    try:
        provided_canonical = canonicalize_github_remote(remote)
        configured_canonical = canonicalize_github_remote(configured_remote)
    except ValueError as error:
        return _failure(mode, [_issue("CONFIGURE_REMOTE_INVALID", str(error), "/remote")])
    if provided_canonical != configured_canonical:
        return _failure(
            mode,
            [
                _issue(
                    "CONFIGURE_REMOTE_MISMATCH",
                    "provided remote does not match KnowledgeHub origin after canonicalization",
                    "/remote",
                )
            ],
            provided_remote=provided_canonical.rstrip("\n"),
            configured_remote=configured_canonical.rstrip("\n"),
        )

    branch_code, current_branch, branch_error = _git(vault_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_code != 0 or current_branch != branch:
        return _failure(
            mode,
            [
                _issue(
                    "CONFIGURE_BRANCH_MISMATCH",
                    f"current branch is {current_branch or branch_error or 'detached'!r}, expected {branch!r}",
                    "/expected_branch",
                )
            ],
            current_branch=current_branch or None,
        )
    tracking_ref = f"refs/remotes/origin/{branch}"
    ref_code, _, ref_error = _git(vault_root, "show-ref", "--verify", "--quiet", "--", tracking_ref)
    if ref_code != 0:
        return _failure(
            mode,
            [_issue("CONFIGURE_REMOTE_BRANCH_MISSING", f"local tracking ref is missing: {tracking_ref}: {ref_error}", "/expected_branch")],
            tracking_ref=tracking_ref,
        )

    digest = remote_identity_sha256(remote)
    document = {
        "schema_version": contract["schema_version"],
        "contract_id": contract["contract_id"],
        "vault_uuid": vault_uuid,
        "canonical_vault_name": expected_name,
        "remote_identity_sha256": digest,
        "expected_branch": branch,
    }
    sentinel_report = validate_root_sentinel(
        document,
        blueprint,
        expected={
            "vault_uuid": vault_uuid,
            "canonical_vault_name": expected_name,
            "remote_identity_sha256": digest,
            "expected_branch": branch,
        },
    )
    if not sentinel_report.passed:
        return _failure(mode, [_issue(issue.code, issue.message, issue.locator) for issue in sentinel_report.issues])

    content = canonical_json_bytes(document)
    report: dict[str, Any] = {
        "status": "PASS",
        "operation": "configure",
        "mode": mode,
        "sentinel_path": SENTINEL_RELATIVE_PATH,
        "configured_remote": configured_canonical.rstrip("\n"),
        "remote_identity_sha256": digest,
        "current_branch": current_branch,
        "expected_branch": branch,
        "tracking_ref": tracking_ref,
        "vault_uuid": vault_uuid,
        "canonical_vault_name": expected_name,
        "sensitive_data_boundary": "confirmed",
        "remote_preflight": "local_origin_and_tracking_ref",
    }
    if sentinel.exists():
        if not sentinel.is_file():
            details = {key: value for key, value in report.items() if key not in {"status", "operation", "mode"}}
            return _failure(mode, [_issue("CONFIGURE_SENTINEL_CONFLICT", "sentinel path is not a regular file")], **details)
        if sentinel.read_bytes() != content:
            details = {key: value for key, value in report.items() if key not in {"status", "operation", "mode"}}
            return _failure(
                mode,
                [_issue("CONFIGURE_SENTINEL_CONFLICT", "existing sentinel bytes do not match; refusing overwrite")],
                **details,
            )
        report["sentinel_write"] = "EXISTING"
        return report
    if dry_run:
        report["sentinel_write"] = "WOULD_CREATE"
        return report
    try:
        _write_exclusive(sentinel, content)
    except FileExistsError:
        return _failure(mode, [_issue("CONFIGURE_SENTINEL_RACE", "sentinel appeared during create-only write")], **report)
    except (OSError, UnicodeError) as error:
        return _failure(mode, [_issue("CONFIGURE_SENTINEL_WRITE_FAILED", str(error))], **report)
    report["sentinel_write"] = "CREATED"
    return report


def configure_json(root: str | Path, **kwargs: Any) -> str:
    return json.dumps(configure(root, **kwargs), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
