"""Fail-closed E03 LaunchAgent installation and rollback.

The checked-in LaunchAgent is a C24 preview artifact.  E03 binds that
artifact to one already-installed host ``vaultctl`` executable and one
resolved KnowledgeOS control root, then asks the current user's LaunchAgent
domain to load the exact label.  The module never installs a provider, edits
the Vault, runs Git network commands, or overwrites an existing plist whose
bytes it cannot prove are the same contract.
"""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

from .background import (
    EXIT_CONFLICT,
    EXIT_INPUT_INVALID,
    EXIT_OK,
    LAUNCHD_ARTIFACT_PATH,
    LAUNCHD_LABEL,
    _validate_runtime,
    render_background_artifacts,
)
from .background import _workspace as _background_workspace
from .recovery import fsync_directory, sha256_bytes

CAPABILITY = "E03"
LAUNCHD_PATH = Path("/bin/launchctl")
LAUNCHD_DIRECTORY = "Library/LaunchAgents"
LAUNCHD_INSTALLATION = "E03"
ROLLBACK_CONTRACT = "bootout_the_exact_label_and_remove_the_installed_plist"


class LaunchdError(ValueError):
    """Raised when an E03 input or host precondition is invalid."""


class LaunchdConflict(LaunchdError):
    """Raised when an existing or observed state cannot be adopted safely."""


def _issue(code: str, locator: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "locator": locator, "message": message}
    if details:
        result["details"] = details
    return result


def _failure(
    operation: str,
    code: str,
    message: str,
    *,
    exit_code: int = EXIT_INPUT_INVALID,
    **details: Any,
) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = {
        "status": "CONFLICT" if exit_code == EXIT_CONFLICT else "FAIL",
        "operation": operation,
        "capability": CAPABILITY,
        "installed": False,
        "launchd_active": False,
        "mutation_performed": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "errors": [_issue(code, "/", message)],
    }
    report.update(details)
    return report, exit_code


def _failure_with_context(
    operation: str,
    code: str,
    message: str,
    context: Mapping[str, Any],
    *,
    exit_code: int,
) -> tuple[dict[str, Any], int]:
    report, result_code = _failure(operation, code, message, exit_code=exit_code)
    for key, value in context.items():
        if key not in {"operation", "status", "errors"}:
            report[key] = value
    return report, result_code


def _workspace(root: str | Path) -> Path:
    try:
        workspace, _vault = _background_workspace(root)
        _validate_runtime(workspace)
    except (OSError, TypeError, ValueError) as error:
        raise LaunchdError(str(error)) from error
    return workspace


def _c36_gate(workspace: Path) -> dict[str, bool]:
    """Require the recorded C36 implementation and canonical gates."""

    state_path = workspace / "PROJECT_STATE.md"
    if state_path.is_symlink() or not state_path.is_file():
        raise LaunchdConflict("PROJECT_STATE.md is required for the E03 C36 gate")
    try:
        state = state_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LaunchdConflict("PROJECT_STATE.md could not be read for the E03 C36 gate") from error
    checks = {
        "c36_complete": "/goal phase=history id=C36 state=complete" in state,
        "c36_gates_pass": "/evidence id=E_C36_GATES class=runtime result=pass" in state,
        "c36_background_inactive": "live providers inactive" in state
        or "background activation stays disabled" in state,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise LaunchdConflict("E03 C36 gate failed: " + ", ".join(failed))
    return checks


def _executable_path(executable: str | Path | None) -> Path:
    raw = Path(executable).expanduser() if executable is not None else Path(sys.argv[0]).expanduser()
    if not raw.is_absolute():
        raise LaunchdError("LaunchAgent executable must be an absolute path")
    if raw.is_symlink() or not raw.is_file():
        raise LaunchdError("LaunchAgent executable must be an existing non-symlink file")
    if not os.access(raw, os.X_OK):
        raise LaunchdError("LaunchAgent executable must be executable")
    if raw.name != "vaultctl":
        raise LaunchdError("LaunchAgent executable basename must be vaultctl")
    return raw.resolve()


def default_install_path() -> Path:
    """Return the current user's conventional LaunchAgent plist path."""

    return Path.home() / LAUNCHD_DIRECTORY / f"{LAUNCHD_LABEL}.plist"


def _install_path(value: str | Path | None) -> Path:
    path = default_install_path() if value is None else Path(value).expanduser()
    if not path.is_absolute():
        raise LaunchdError("LaunchAgent install path must be absolute")
    if path.name != f"{LAUNCHD_LABEL}.plist":
        raise LaunchdError("LaunchAgent install path must use the exact E03 label filename")
    parent = path.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise LaunchdError("LaunchAgent install directory must be a real directory")
    return path


def _domain() -> str:
    try:
        uid = os.getuid()
    except AttributeError as error:  # pragma: no cover - macOS always exposes getuid
        raise LaunchdError("current-user LaunchAgent domains are unavailable") from error
    if uid <= 0:
        raise LaunchdError("E03 requires a non-root current-user LaunchAgent domain")
    return f"gui/{uid}"


def _service_target(domain: str) -> str:
    return f"{domain}/{LAUNCHD_LABEL}"


def _run_launchctl(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(LAUNCHD_PATH), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LaunchdError(f"launchctl could not complete: {error}") from error


def _require_host_launchd() -> None:
    if sys.platform != "darwin":
        raise LaunchdError("E03 LaunchAgent activation requires macOS")
    if LAUNCHD_PATH.is_symlink() or not LAUNCHD_PATH.is_file() or not os.access(LAUNCHD_PATH, os.X_OK):
        raise LaunchdError("/bin/launchctl is not an executable host command")


def _service_active(domain: str) -> tuple[bool, str]:
    result = _run_launchctl(("print", _service_target(domain)))
    if result.returncode == 0:
        return True, (result.stdout or "").strip()
    message = (result.stderr or result.stdout or "").strip()
    lowered = message.lower()
    if any(marker in lowered for marker in ("could not find service", "no such process", "not found")):
        return False, message
    raise LaunchdConflict(f"launchctl could not inspect the exact E03 label: {message or result.returncode}")


def _template_and_bound_plist(
    workspace: Path,
    executable: Path,
) -> tuple[dict[str, Any], bytes, str]:
    artifact_report, artifact_code = render_background_artifacts(workspace)
    if artifact_code != EXIT_OK or artifact_report.get("status") != "PASS":
        raise LaunchdConflict("C24 background artifacts are not valid for E03 binding")
    template_path = workspace / LAUNCHD_ARTIFACT_PATH
    try:
        template_bytes = template_path.read_bytes()
        document = plistlib.loads(template_bytes)
    except (OSError, ExpatError, plistlib.InvalidFileException, TypeError, ValueError) as error:
        raise LaunchdError(f"C24 LaunchAgent template could not be parsed: {error}") from error
    if document.get("Label") != LAUNCHD_LABEL:
        raise LaunchdConflict("C24 LaunchAgent template label does not match E03")
    if document.get("RunAtLoad") is not False:
        raise LaunchdConflict("C24 LaunchAgent template must keep RunAtLoad false")
    if "__KNOWLEDGEOS_CONTROL_ROOT__" not in str(document):
        raise LaunchdConflict("C24 LaunchAgent template is already bound or missing its placeholder")
    template_sha256 = sha256_bytes(template_bytes)
    root_text = str(workspace)
    document["ProgramArguments"] = [
        str(executable),
        "ai",
        "worker",
        "--once",
        "--root",
        root_text,
    ]
    document["WorkingDirectory"] = root_text
    environment = dict(document.get("EnvironmentVariables", {}))
    environment["KNOWLEDGEOS_CONTROL_ROOT"] = root_text
    document["EnvironmentVariables"] = environment
    document["StandardOutPath"] = f"{root_text}/runtime/logs/worker.stdout.log"
    document["StandardErrorPath"] = f"{root_text}/runtime/logs/worker.stderr.log"
    metadata = dict(document.get("KnowledgeOS", {}))
    metadata.update(
        {
            "capability": "C24",
            "enabled_by_default": False,
            "installation": LAUNCHD_INSTALLATION,
            "launchd_active": True,
            "bound_control_root": root_text,
            "bound_executable": str(executable),
            "template_sha256": template_sha256,
            "rollback": ROLLBACK_CONTRACT,
            "provider_called": False,
            "vault_mutated": False,
            "git_network_called": False,
        }
    )
    document["KnowledgeOS"] = metadata
    bound_bytes = plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=False)
    return document, bound_bytes, template_sha256


def _inspect_existing(path: Path, expected: bytes | None = None) -> str:
    if path.is_symlink():
        raise LaunchdConflict("E03 install path must not be a symlink")
    if not path.exists():
        return "absent"
    if not path.is_file():
        raise LaunchdConflict("E03 install path is not a regular file")
    existing = path.read_bytes()
    if expected is not None and existing == expected:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise LaunchdConflict("owned E03 plist must have mode 0600")
        return "owned"
    return "conflict"


def _write_new_plist(path: Path, payload: bytes) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise LaunchdError("LaunchAgent install directory must already exist and be non-symlink")
    descriptor = -1
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(parent)
    except FileExistsError as error:
        raise LaunchdConflict("E03 install path appeared during create-only publication") from error
    except OSError as error:
        raise LaunchdError(f"E03 plist could not be published: {error}") from error
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _remove_exact_plist(path: Path, expected: bytes) -> None:
    if path.is_symlink() or not path.is_file():
        raise LaunchdConflict("E03 rollback target is not an owned regular file")
    if path.read_bytes() != expected:
        raise LaunchdConflict("E03 rollback target bytes changed after installation")
    try:
        path.unlink()
        fsync_directory(path.parent)
    except OSError as error:
        raise LaunchdError(f"E03 plist could not be removed: {error}") from error


def _bound_context(
    root: str | Path,
    *,
    executable: str | Path | None,
    install_path: str | Path | None,
    require_c36: bool = True,
) -> dict[str, Any]:
    workspace = _workspace(root)
    c36_checks = _c36_gate(workspace) if require_c36 else {}
    executable_path = _executable_path(executable)
    target = _install_path(install_path)
    document, payload, template_sha256 = _template_and_bound_plist(workspace, executable_path)
    existing = _inspect_existing(target, payload)
    return {
        "workspace": workspace,
        "executable": executable_path,
        "path": target,
        "document": document,
        "payload": payload,
        "plist_sha256": sha256_bytes(payload),
        "template_sha256": template_sha256,
        "existing": existing,
        "c36_checks": c36_checks,
    }


def _common_report(context: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(context["path"])
    workspace = Path(context["workspace"])
    return {
        "capability": CAPABILITY,
        "artifact": LAUNCHD_ARTIFACT_PATH,
        "install_path": str(path),
        "label": LAUNCHD_LABEL,
        "domain": _domain(),
        "bound_control_root": str(workspace),
        "bound_executable": str(context["executable"]),
        "plist_sha256": context["plist_sha256"],
        "template_sha256": context["template_sha256"],
        "existing_plist": context["existing"],
        "c36_gate": dict(context["c36_checks"]),
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
    }


def install(
    root: str | Path,
    *,
    dry_run: bool = False,
    activate: bool = False,
    executable: str | Path | None = None,
    install_path: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Preview or explicitly activate one exact current-user LaunchAgent."""

    try:
        context = _bound_context(
            root,
            executable=executable,
            install_path=install_path,
        )
    except LaunchdConflict as error:
        return _failure("launchd install", "E03_GATE_CONFLICT", str(error), exit_code=EXIT_CONFLICT)
    except (LaunchdError, OSError, TypeError, ValueError) as error:
        return _failure("launchd install", "E03_INPUT_INVALID", str(error))

    report = _common_report(context)
    report["operation"] = "launchd install"
    report["mode"] = "dry_run" if dry_run else ("activate" if activate else "authorization_required")
    report["mutation_performed"] = False
    report["installed"] = context["existing"] == "owned"
    report["launchd_active"] = False
    report["activation"] = "preview" if dry_run else "deferred"

    if context["existing"] == "conflict":
        return _failure_with_context(
            "launchd install",
            "LAUNCHD_PLIST_CONFLICT",
            "an existing plist at the exact E03 path has different bytes",
            report,
            exit_code=EXIT_CONFLICT,
        )
    if dry_run:
        report["status"] = "PASS"
        report["would_write_plist"] = context["existing"] == "absent"
        report["would_bootstrap"] = True
        return report, EXIT_OK
    if not activate:
        return _failure_with_context(
            "launchd install",
            "LAUNCHD_ACTIVATION_REQUIRED",
            "host LaunchAgent mutation requires the explicit --activate flag",
            report,
            exit_code=EXIT_CONFLICT,
        )

    try:
        _require_host_launchd()
        domain = _domain()
        active_before, _detail = _service_active(domain)
        if active_before:
            if context["existing"] != "owned":
                raise LaunchdConflict("the exact E03 label is active without the owned plist bytes")
            report.update(
                {
                    "status": "PASS",
                    "installed": True,
                    "launchd_active": True,
                    "activation": "already_active",
                    "mutation_performed": False,
                }
            )
            return report, EXIT_OK

        created = False
        if context["existing"] == "absent":
            _write_new_plist(Path(context["path"]), bytes(context["payload"]))
            created = True
        bootstrap = _run_launchctl(("bootstrap", domain, str(context["path"])))
        if bootstrap.returncode != 0:
            if created:
                _remove_exact_plist(Path(context["path"]), bytes(context["payload"]))
            message = (bootstrap.stderr or bootstrap.stdout or "").strip()
            raise LaunchdConflict(f"launchctl bootstrap failed: {message or bootstrap.returncode}")
        active_after, _detail = _service_active(domain)
        if not active_after:
            if created:
                _remove_exact_plist(Path(context["path"]), bytes(context["payload"]))
            raise LaunchdConflict("launchctl bootstrap returned success but the exact label is not active")
    except LaunchdConflict as error:
        return _failure_with_context(
            "launchd install",
            "LAUNCHD_ACTIVATION_FAILED",
            str(error),
            report,
            exit_code=EXIT_CONFLICT,
        )
    except LaunchdError as error:
        return _failure_with_context(
            "launchd install",
            "LAUNCHD_HOST_UNAVAILABLE",
            str(error),
            report,
            exit_code=EXIT_CONFLICT,
        )

    report.update(
        {
            "status": "PASS",
            "installed": True,
            "launchd_active": True,
            "activation": "bootstrapped",
            "mutation_performed": True,
            "plist_written": context["existing"] == "absent",
        }
    )
    return report, EXIT_OK


def _owned_document(path: Path, root: str | Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise LaunchdConflict("E03 rollback target is not an existing regular file")
    raw = path.read_bytes()
    try:
        document = plistlib.loads(raw)
    except (ExpatError, plistlib.InvalidFileException, TypeError, ValueError) as error:
        raise LaunchdConflict("E03 rollback target is not a valid plist") from error
    metadata = document.get("KnowledgeOS")
    workspace = _workspace(root)
    if (
        document.get("Label") != LAUNCHD_LABEL
        or not isinstance(metadata, Mapping)
        or metadata.get("installation") != LAUNCHD_INSTALLATION
        or metadata.get("bound_control_root") != str(workspace)
        or metadata.get("rollback") != ROLLBACK_CONTRACT
    ):
        raise LaunchdConflict("E03 rollback target ownership could not be proven")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise LaunchdConflict("owned E03 plist must have mode 0600")
    return document, raw


def rollback(
    root: str | Path,
    *,
    apply: bool = False,
    install_path: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Preview or apply rollback for the exact E03-owned service and plist."""

    try:
        workspace = _workspace(root)
        target = _install_path(install_path)
        if target.exists() or target.is_symlink():
            _document, payload = _owned_document(target, workspace)
            ownership = "owned"
        else:
            payload = b""
            ownership = "absent"
        domain = _domain()
    except LaunchdConflict as error:
        return _failure("launchd rollback", "E03_ROLLBACK_CONFLICT", str(error), exit_code=EXIT_CONFLICT)
    except (LaunchdError, OSError, TypeError, ValueError) as error:
        return _failure("launchd rollback", "E03_INPUT_INVALID", str(error))

    report: dict[str, Any] = {
        "status": "PASS",
        "operation": "launchd rollback",
        "capability": CAPABILITY,
        "install_path": str(target),
        "label": LAUNCHD_LABEL,
        "domain": domain,
        "ownership": ownership,
        "installed": ownership == "owned",
        "launchd_active": False,
        "mutation_performed": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "mode": "apply" if apply else "dry_run",
    }
    if not apply:
        report["would_bootout"] = ownership == "owned"
        report["would_remove_plist"] = ownership == "owned"
        return report, EXIT_OK

    try:
        _require_host_launchd()
        active, _detail = _service_active(domain)
        if active:
            bootout = _run_launchctl(("bootout", _service_target(domain)))
            if bootout.returncode != 0:
                message = (bootout.stderr or bootout.stdout or "").strip()
                raise LaunchdConflict(f"launchctl bootout failed: {message or bootout.returncode}")
            still_active, _detail = _service_active(domain)
            if still_active:
                raise LaunchdConflict("exact E03 label remained active after bootout")
        if ownership == "owned":
            _remove_exact_plist(target, payload)
    except LaunchdConflict as error:
        return _failure_with_context(
            "launchd rollback",
            "LAUNCHD_ROLLBACK_FAILED",
            str(error),
            report,
            exit_code=EXIT_CONFLICT,
        )
    except LaunchdError as error:
        return _failure_with_context(
            "launchd rollback",
            "LAUNCHD_HOST_UNAVAILABLE",
            str(error),
            report,
            exit_code=EXIT_CONFLICT,
        )
    report.update({"installed": False, "launchd_active": False, "mutation_performed": active or ownership == "owned"})
    return report, EXIT_OK


def status(
    root: str | Path,
    *,
    install_path: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Inspect the exact E03 plist and current-user service without mutation."""

    try:
        workspace = _workspace(root)
        target = _install_path(install_path)
        domain = _domain()
        if target.is_symlink():
            raise LaunchdConflict("E03 install path is a symlink")
        if target.exists() and not target.is_file():
            raise LaunchdConflict("E03 install path is not a regular file")
        installed = target.is_file()
        owned = False
        plist_sha256 = None
        bound_root = None
        if installed:
            raw = target.read_bytes()
            plist_sha256 = sha256_bytes(raw)
            try:
                document = plistlib.loads(raw)
            except (ExpatError, plistlib.InvalidFileException, TypeError, ValueError) as error:
                raise LaunchdConflict("E03 install path is not a valid plist") from error
            metadata = document.get("KnowledgeOS")
            owned = (
                document.get("Label") == LAUNCHD_LABEL
                and isinstance(metadata, Mapping)
                and metadata.get("installation") == LAUNCHD_INSTALLATION
            )
            bound_root = metadata.get("bound_control_root") if isinstance(metadata, Mapping) else None
        active_check = "not_run"
        active = False
        detail = ""
        if sys.platform == "darwin" and LAUNCHD_PATH.is_file() and os.access(LAUNCHD_PATH, os.X_OK):
            active, detail = _service_active(domain)
            active_check = "verified"
        if installed and not owned:
            raise LaunchdConflict("a plist exists at the E03 path but is not E03-owned")
        if owned and bound_root != str(workspace):
            raise LaunchdConflict("E03 plist is bound to a different control root")
    except LaunchdConflict as error:
        return _failure("launchd status", "E03_STATUS_CONFLICT", str(error), exit_code=EXIT_CONFLICT)
    except (LaunchdError, OSError, TypeError, ValueError) as error:
        return _failure("launchd status", "E03_STATUS_INVALID", str(error))
    return {
        "status": "PASS",
        "operation": "launchd status",
        "capability": CAPABILITY,
        "install_path": str(target),
        "label": LAUNCHD_LABEL,
        "domain": domain,
        "installed": installed,
        "owned": owned,
        "launchd_active": active,
        "active_check": active_check,
        "active_detail": detail,
        "plist_sha256": plist_sha256,
        "bound_control_root": bound_root,
        "mutation_performed": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
    }, EXIT_OK


launchd_install = install
launchd_rollback = rollback
launchd_status = status


__all__ = [
    "CAPABILITY",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "EXIT_OK",
    "LAUNCHD_INSTALLATION",
    "LAUNCHD_LABEL",
    "default_install_path",
    "install",
    "launchd_install",
    "launchd_rollback",
    "launchd_status",
    "rollback",
    "status",
]
