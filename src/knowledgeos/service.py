"""Owner LaunchAgent (``knowledgeos install service``; Blueprint §3.3, capability C11).

The Owner runs on the host because it spawns ``hermes`` itself. This module installs it as an
on-demand macOS user service: the plist is loaded into ``gui/<uid>`` but neither runs at load
nor restarts (``RunAtLoad`` and ``KeepAlive`` false). The Obsidian Thin Client starts and stops
it with ``launchctl kickstart`` / ``kill``, so the Owner outlives Obsidian.

launchd gives the service only a minimal environment, so ``PATH`` (the Hermes binary directory
first), ``HERMES_HOME`` and ``PYTHONPATH`` (this source tree, as the Hermes adapter uses) are
written into the plist. Credentials are never written.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import OwnerConfig
from .hermes import host_from_config

LABEL = "com.agentfabric.knowledgeos.owner"
LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
SOURCE_PARENT = str(Path(__file__).resolve().parents[1])
LAUNCHCTL_TIMEOUT = 60.0
# ``launchctl bootout`` of a service that is not loaded: 3 (no such process) or 113 (service not found).
NOT_LOADED = (3, 113)


class ServiceError(Exception):
    pass


def default_plist(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _path_entries(binary: str | None) -> list[str]:
    entries: list[str] = []
    if binary:
        expanded = os.path.expanduser(binary)
        located = expanded if os.path.isabs(expanded) else shutil.which(expanded)
        if located:
            entries.append(os.path.dirname(os.path.abspath(located)))
    entries += os.environ.get("PATH", "").split(os.pathsep)
    entries += SYSTEM_PATH
    return list(dict.fromkeys(entry for entry in entries if os.path.isabs(entry)))


def build_plist(config: OwnerConfig, config_path: Path, label: str) -> dict[str, Any]:
    host = host_from_config(config)
    logs = config.runtime / "logs"
    environment = {
        "PATH": os.pathsep.join(_path_entries(host.binary if host else None)),
        "PYTHONPATH": SOURCE_PARENT,
    }
    if host is not None:
        environment["HERMES_HOME"] = str(host.home)
    elif os.environ.get("HERMES_HOME"):
        environment["HERMES_HOME"] = str(Path(os.environ["HERMES_HOME"]).expanduser().absolute())
    return {
        "Label": label,
        "ProgramArguments": [
            os.path.abspath(sys.executable), "-m", "knowledgeos.cli", "serve", "--config", str(config_path),
        ],
        "RunAtLoad": False,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "StandardOutPath": str(logs / "owner.out.log"),
        "StandardErrorPath": str(logs / "owner.err.log"),
        "EnvironmentVariables": environment,
    }


def _write_plist(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(plistlib.dumps(document))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _launchctl(argv: list[str]) -> dict[str, Any]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=LAUNCHCTL_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return {"argv": argv, "returncode": None, "output": f"{type(error).__name__}: {error}"}
    return {"argv": argv, "returncode": done.returncode, "output": (done.stdout + done.stderr).strip()}


def install(
    config: OwnerConfig,
    config_path: Path,
    *,
    label: str = LABEL,
    plist: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write the plist, then replace any loaded service of this label with the new definition.

    The service ends loaded and not running. ``dry_run`` reports the plist and ``launchctl``
    argv without writing or invoking anything.
    """
    if not LABEL_PATTERN.fullmatch(label):
        raise ServiceError(f"invalid launchd label {label!r}")
    document = build_plist(config, config_path, label)
    path = Path(plist).expanduser().resolve() if plist else default_plist(label)
    domain = f"gui/{os.getuid()}"
    steps = [["launchctl", "bootout", f"{domain}/{label}"], ["launchctl", "bootstrap", domain, str(path)]]
    report: dict[str, Any] = {
        "target": "service", "dry_run": dry_run, "ok": True, "label": label,
        "plist_path": str(path), "plist": document, "launchctl": [{"argv": argv} for argv in steps],
    }
    if dry_run:
        return report
    if shutil.which("launchctl") is None:
        raise ServiceError("installing the Owner service needs launchctl on PATH (macOS)")
    os.makedirs(config.runtime / "logs", mode=0o700, exist_ok=True)
    _write_plist(path, document)
    bootout = _launchctl(steps[0])
    report["launchctl"] = [bootout]
    report["was_loaded"] = bootout["returncode"] == 0
    if bootout["returncode"] != 0 and bootout["returncode"] not in NOT_LOADED:
        report["ok"] = False
        return report
    bootstrap = _launchctl(steps[1])
    report["launchctl"].append(bootstrap)
    report["loaded"] = report["ok"] = bootstrap["returncode"] == 0
    return report
