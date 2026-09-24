"""F05's bounded, status-only adapter for the official Obsidian CLI.

The adapter intentionally probes only ``obsidian version``.  It does not
accept a command, arguments, a Vault path, or a plugin operation from a
caller.  A successful version probe is not promoted to app-connection or
device evidence; those dimensions remain explicitly ``not_run`` unless a
future, separately authorized status contract supplies them.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bridge_contract import validate_root_sentinel
from .yaml_safe import load_yaml_file

EXIT_OK = 0
EXIT_DEGRADED = 4
EXIT_INPUT_INVALID = 10
EXIT_CONFIG_INVALID = 11

OBSIDIAN_EXECUTABLE = "obsidian"
FIXED_ARGV = ("version",)
# The macOS CLI may need a few seconds to complete its IPC round-trip even
# when the desktop app is already running.  Keep the bound finite while
# leaving enough room for that startup path.
PROBE_TIMEOUT_SECONDS = 5.0
MAX_OUTPUT_BYTES = 4096
MAX_VERSION_LENGTH = 128

_VERSION_COMPONENT_PATTERN = r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][0-9A-Za-z.-]+)?"
_VERSION_PATTERN = re.compile(
    rf"^(?:Obsidian\s+)?v?(?P<version>{_VERSION_COMPONENT_PATTERN})"
    rf"(?:\s+\(installer\s+v?{_VERSION_COMPONENT_PATTERN}\))?$",
    re.IGNORECASE,
)
_VAULT_PATTERN = re.compile(r"^Vault:\s*(?P<name>[A-Za-z0-9][A-Za-z0-9 ._-]{0,127})$")
_CONNECTION_PATTERN = re.compile(r"^Connection:\s*(?P<state>connected|not_running)$")
_CAPABILITIES_PATTERN = re.compile(
    r"^Capabilities:\s*(?P<values>[a-z][a-z0-9]*(?:,[a-z][a-z0-9]*)*)$"
)
_APP_NOT_RUNNING_MARKERS = (
    "not running",
    "could not connect",
    "connection refused",
    "no such process",
)
_ALLOWED_CAPABILITIES = frozenset({"version"})
_FORBIDDEN_CAPABILITIES = (
    "read",
    "write",
    "create",
    "append",
    "search",
    "command",
    "eval",
    "plugin-control",
)


@dataclass(frozen=True)
class _ProbeResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limited: bool = False
    spawn_error: str | None = None


def _issue(code: str, message: str, locator: str = "/") -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message}


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _resolve_roots(root: str | Path) -> tuple[Path, Path]:
    control = Path(root).expanduser()
    if control.is_symlink() or not control.is_dir():
        raise ValueError("--root must be an existing, non-symlink control directory")
    control = control.resolve()
    vault = control / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise ValueError("KnowledgeHub must be an existing, non-symlink directory")
    return control, vault.resolve()


def _validate_vault_identity(control: Path, vault: Path) -> dict[str, Any]:
    sentinel = vault / ".knowledgeos-root.json"
    if sentinel.is_symlink() or not sentinel.is_file():
        return {
            "state": "invalid",
            "reason": "Vault root sentinel is missing or unsafe",
            "source": "KnowledgeHub/.knowledgeos-root.json",
        }
    try:
        document = json.loads(
            sentinel.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_pairs
        )
        blueprint = load_yaml_file(control / "blueprint/blueprint.yaml")
        validation = validate_root_sentinel(document, blueprint)
    except (OSError, UnicodeError, TypeError, ValueError, KeyError) as error:
        return {
            "state": "invalid",
            "reason": f"Vault root identity could not be validated: {error}",
            "source": "KnowledgeHub/.knowledgeos-root.json",
        }
    if not validation.passed:
        return {
            "state": "invalid",
            "reason": "Vault root sentinel failed the canonical identity contract",
            "source": "KnowledgeHub/.knowledgeos-root.json",
            "issues": [issue.as_dict() for issue in validation.issues],
        }
    canonical_name = document.get("canonical_vault_name")
    if canonical_name != vault.name:
        return {
            "state": "mismatch",
            "reason": "Vault root name does not match the validated canonical Vault identity",
            "expected": vault.name,
            "observed": canonical_name,
            "source": "KnowledgeHub/.knowledgeos-root.json",
        }
    return {
        "state": "validated",
        "canonical_vault_name": canonical_name,
        "vault_uuid": document.get("vault_uuid"),
        "source": "KnowledgeHub/.knowledgeos-root.json",
    }


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=0.2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _bounded_probe(executable: str, *, timeout: float, output_limit: int) -> _ProbeResult:
    """Run the fixed version probe with bounded pipes and no shell."""

    try:
        process = subprocess.Popen(
            [executable, *FIXED_ARGV],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as error:
        return _ProbeResult(None, b"", b"", spawn_error=str(error))

    selector = selectors.DefaultSelector()
    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    streams = (("stdout", process.stdout), ("stderr", process.stderr))
    try:
        for name, stream in streams:
            if stream is not None:
                selector.register(stream, selectors.EVENT_READ, name)
        deadline = time.monotonic() + timeout
        timed_out = False
        output_limited = False
        while selector.get_map():
            # Pipe EOF is not a reliable process-liveness signal: a GUI CLI
            # can exit after spawning a helper that inherits stdout/stderr.
            # Once the direct command is gone, drain bytes that are already
            # available and stop instead of waiting for that helper's EOF.
            process_exited = process.poll() is not None
            if process_exited:
                events = selector.select(0)
                if not events:
                    break
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                events = selector.select(min(remaining, 0.05))
            if not events:
                continue
            for key, _ in events:
                name = str(key.data)
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), min(4096, output_limit + 1 - len(buffers[name])))
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffers[name].extend(chunk)
                if len(buffers[name]) > output_limit:
                    output_limited = True
                    break
            if timed_out or output_limited:
                break
        if timed_out or output_limited:
            _terminate(process)
        else:
            process.wait(timeout=max(0.2, min(timeout, 1.0)))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(process)
    finally:
        selector.close()
        if process.poll() is None:
            _terminate(process)
        try:
            returncode = process.wait(timeout=0.2)
        except (OSError, subprocess.TimeoutExpired):
            returncode = None
        for _, stream in streams:
            if stream is not None:
                stream.close()
    return _ProbeResult(
        returncode,
        bytes(buffers["stdout"][:output_limit]),
        bytes(buffers["stderr"][:output_limit]),
        timed_out=timed_out,
        output_limited=output_limited,
    )


def _decode(value: bytes, label: str) -> tuple[str | None, str | None]:
    try:
        return value.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{label} is not valid UTF-8"


def _base_report(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operation": "obsidian status",
        "mode": "read-only",
        "adapter": {
            "executable_name": OBSIDIAN_EXECUTABLE,
            "fixed_argv": list(FIXED_ARGV),
            "shell": False,
            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
            "max_output_bytes": MAX_OUTPUT_BYTES,
            "vault_identity": dict(identity),
            "app_connection": {
                "state": "not_run",
                "evidence_class": "external_service",
                "reason": "the fixed version probe does not prove live app connection",
            },
            "capabilities": {
                "allowlisted": ["version"],
                "forbidden": list(_FORBIDDEN_CAPABILITIES),
                "raw_passthrough": False,
                "document_operations": False,
                "plugin_control": False,
                "evidence_class": "static",
            },
        },
        "errors": [],
    }


def _failure(report: dict[str, Any], code: str, message: str, state: str) -> tuple[dict[str, Any], int]:
    report["status"] = "DEGRADED"
    report["adapter"]["state"] = state
    report["errors"] = [_issue(code, message)]
    return report, EXIT_DEGRADED


def _parse_probe(report: dict[str, Any], probe: _ProbeResult, expected_vault: str) -> tuple[dict[str, Any], int]:
    if probe.spawn_error is not None:
        return _failure(report, "OBSIDIAN_CLI_SPAWN_FAILED", "fixed Obsidian CLI probe could not start", "spawn_failed")
    if probe.timed_out:
        return _failure(report, "OBSIDIAN_CLI_TIMEOUT", "fixed Obsidian CLI probe exceeded its bounded timeout", "timeout")
    if probe.output_limited:
        return _failure(report, "OBSIDIAN_CLI_OUTPUT_LIMIT", "fixed Obsidian CLI output exceeded the bounded byte limit", "output_limit")

    stdout, stdout_error = _decode(probe.stdout, "stdout")
    stderr, stderr_error = _decode(probe.stderr, "stderr")
    if stdout_error or stderr_error:
        return _failure(
            report,
            "OBSIDIAN_CLI_OUTPUT_INVALID",
            stdout_error or stderr_error or "Obsidian CLI output is invalid",
            "malformed_output",
        )
    assert stdout is not None and stderr is not None
    if probe.returncode != 0:
        combined = f"{stdout}\n{stderr}".lower()
        state = "app_not_running" if any(marker in combined for marker in _APP_NOT_RUNNING_MARKERS) else "probe_failed"
        code = "OBSIDIAN_APP_NOT_RUNNING" if state == "app_not_running" else "OBSIDIAN_CLI_FAILED"
        return _failure(report, code, "the fixed Obsidian CLI status probe did not establish a usable status", state)
    if stderr.strip():
        return _failure(report, "OBSIDIAN_CLI_UNEXPECTED_STDERR", "successful Obsidian CLI probe emitted unexpected stderr", "unexpected_output")

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines or len(lines[0]) > MAX_VERSION_LENGTH:
        return _failure(report, "OBSIDIAN_CLI_OUTPUT_INVALID", "Obsidian CLI version output is malformed", "malformed_output")
    version_match = _VERSION_PATTERN.fullmatch(lines[0])
    if version_match is None:
        return _failure(report, "OBSIDIAN_CLI_OUTPUT_INVALID", "Obsidian CLI version output is not allowlisted", "malformed_output")

    observed_vault: str | None = None
    connection_state = "not_run"
    observed_capabilities: list[str] | None = None
    for line in lines[1:]:
        if (match := _VAULT_PATTERN.fullmatch(line)) is not None:
            if observed_vault is not None:
                return _failure(report, "OBSIDIAN_CLI_OUTPUT_INVALID", "Obsidian CLI output repeated Vault identity", "malformed_output")
            observed_vault = match.group("name")
            continue
        if (match := _CONNECTION_PATTERN.fullmatch(line)) is not None:
            if connection_state != "not_run":
                return _failure(report, "OBSIDIAN_CLI_OUTPUT_INVALID", "Obsidian CLI output repeated connection state", "malformed_output")
            connection_state = match.group("state")
            continue
        if (match := _CAPABILITIES_PATTERN.fullmatch(line)) is not None:
            if observed_capabilities is not None:
                return _failure(report, "OBSIDIAN_CLI_OUTPUT_INVALID", "Obsidian CLI output repeated capability summary", "malformed_output")
            observed_capabilities = match.group("values").split(",")
            if not set(observed_capabilities) <= _ALLOWED_CAPABILITIES:
                return _failure(report, "OBSIDIAN_CLI_CAPABILITY_UNEXPECTED", "Obsidian CLI reported a capability outside the status-only allowlist", "unexpected_capability")
            continue
        return _failure(report, "OBSIDIAN_CLI_OUTPUT_INVALID", "Obsidian CLI output contains an unrecognized status field", "malformed_output")

    if observed_vault is not None and observed_vault != expected_vault:
        report["adapter"]["app_connection"]["state"] = "vault_mismatch"
        return _failure(report, "OBSIDIAN_VAULT_MISMATCH", "observed Obsidian Vault identity does not match the validated KnowledgeOS Vault", "vault_mismatch")
    if connection_state == "not_running":
        report["adapter"]["app_connection"]["state"] = "app_not_running"
        return _failure(report, "OBSIDIAN_APP_NOT_RUNNING", "Obsidian reported that the app is not running", "app_not_running")
    if connection_state == "connected":
        report["adapter"]["app_connection"] = {
            "state": "connected",
            "evidence_class": "external_service",
            "reason": "reported by the fixed status envelope; device UI behavior remains unverified",
        }
    report["adapter"].update(
        {
            "state": "version_verified",
            "version": version_match.group("version"),
            "observed_vault": observed_vault,
            "observed_capabilities": observed_capabilities or ["version"],
        }
    )
    report["status"] = "PASS"
    return report, EXIT_OK


def _collect_status(
    root: str | Path,
    *,
    executable_resolver: Callable[[str], str | None] = shutil.which,
    probe: Callable[[str], _ProbeResult] | None = None,
) -> tuple[dict[str, Any], int]:
    try:
        control, vault = _resolve_roots(root)
    except (OSError, TypeError, ValueError) as error:
        return {
            "operation": "obsidian status",
            "mode": "read-only",
            "status": "FAIL",
            "errors": [_issue("OBSIDIAN_ROOT_INVALID", str(error))],
        }, EXIT_INPUT_INVALID

    identity = _validate_vault_identity(control, vault)
    report = _base_report(identity)
    if identity.get("state") != "validated":
        report["status"] = "FAIL"
        report["adapter"]["state"] = "vault_identity_invalid"
        report["errors"] = [
            _issue("OBSIDIAN_VAULT_IDENTITY_INVALID", "validated KnowledgeOS Vault identity is required")
        ]
        return report, EXIT_CONFIG_INVALID

    executable = executable_resolver(OBSIDIAN_EXECUTABLE)
    report["adapter"]["executable"] = {
        "state": "found" if executable else "missing",
        "name": OBSIDIAN_EXECUTABLE,
    }
    if executable is None:
        return _failure(report, "OBSIDIAN_CLI_NOT_FOUND", "Obsidian CLI executable was not found on PATH", "missing_path")
    runner = probe or (lambda path: _bounded_probe(path, timeout=PROBE_TIMEOUT_SECONDS, output_limit=MAX_OUTPUT_BYTES))
    return _parse_probe(report, runner(executable), str(identity["canonical_vault_name"]))


def obsidian_status(root: str | Path) -> tuple[dict[str, Any], int]:
    """Run the only public Obsidian adapter command."""

    return _collect_status(root)
