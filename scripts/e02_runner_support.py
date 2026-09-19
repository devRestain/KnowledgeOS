"""Shared fail-closed helpers for the E02 one-shot launchers."""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
RUNS_ROOT = RUNTIME_ROOT / "runs"
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

_CLEARED_ENVIRONMENT = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "OLLAMA_API_KEY",
    "OLLAMA_ORIGINS",
    "SSH_AUTH_SOCK",
)


def job_directory(raw_path: str) -> Path:
    """Resolve exactly one project-owned ``runtime/runs/JOB_ID`` directory."""

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    candidate = Path(os.path.normpath(str(candidate)))
    expected_root = RUNS_ROOT
    if candidate.parent != expected_root or not JOB_ID_PATTERN.fullmatch(candidate.name):
        raise ValueError("job_dir must be exactly runtime/runs/<lowercase UUIDv4>")
    current = PROJECT_ROOT
    for component in ("runtime", "runs", candidate.name):
        current = current / component
        if current.is_symlink():
            raise ValueError("job_dir must not traverse a symlink")
    if not candidate.is_dir():
        raise ValueError("job_dir must be an existing private directory")
    return candidate


def ollama_environment() -> dict[str, str]:
    """Return a child environment restricted to the E02 loopback profile."""

    environment = dict(os.environ)
    for name in _CLEARED_ENVIRONMENT:
        environment.pop(name, None)
    environment["OLLAMA_HOST"] = "127.0.0.1:11434"
    environment["OLLAMA_NO_CLOUD"] = "1"
    return environment


def clear_proxy_environment() -> None:
    """Apply the same proxy and tunnel clearing to the current one-shot process."""

    for name in _CLEARED_ENVIRONMENT:
        os.environ.pop(name, None)
    os.environ["OLLAMA_HOST"] = "127.0.0.1:11434"
    os.environ["OLLAMA_NO_CLOUD"] = "1"


def private_relay_socket(job_dir: Path) -> Path:
    """Return the ephemeral relay socket path inside one selected job spool."""

    return job_dir / ".ollama-relay.sock"

