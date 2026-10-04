"""Shared fail-closed helpers for the E02 one-shot launchers."""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT_ENV = "KNOWLEDGEOS_STATE_ROOT"
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


def selected_state_root(raw_root: str | Path | None = None) -> Path:
    """Resolve one existing State directory from an argument or environment."""

    selected = raw_root if raw_root is not None else os.environ.get(STATE_ROOT_ENV)
    if selected is None:
        raise ValueError("an explicit State root binding is required")
    candidate = Path(selected).expanduser()
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("State root must be absolute")
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents)) or not candidate.is_dir():
        raise ValueError("State root must be an existing non-symlink directory")
    return candidate.resolve()


def job_directory(raw_path: str, *, state_root: str | Path | None = None) -> Path:
    """Resolve exactly one explicitly bound private State/runs/JOB_ID directory."""
    selected_root = selected_state_root(state_root)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = selected_root / candidate
    if ".." in candidate.parts or candidate.parent != selected_root / "runs" or not JOB_ID_PATTERN.fullmatch(candidate.name):
        raise ValueError("job_dir must be exactly <state-root>/runs/<lowercase UUIDv4>")
    for path in (selected_root / "runs", candidate):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("job_dir must be an existing private directory without symlinks")
        if path.stat().st_mode & 0o777 != 0o700:
            raise ValueError("job_dir must be an existing private directory with mode 0700")
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
