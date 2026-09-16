"""Runtime directory contract used by the C01 harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RUNTIME_DIRECTORIES = (
    "staging",
    "queue",
    "quarantine",
    "awaiting_remote_authorization",
    "running",
    "review",
    "approved",
    "applying",
    "done",
    "rejected",
    "expired",
    "failed",
    "conflict",
    "runs",
    "receipts",
    "locks",
    "index",
    "cache",
    "logs",
)


@dataclass(frozen=True)
class RuntimeLayout:
    root: Path

    @property
    def directories(self) -> tuple[Path, ...]:
        return tuple(self.root / name for name in RUNTIME_DIRECTORIES)

    def check(self) -> list[str]:
        """Return contract violations without creating or changing anything."""

        problems: list[str] = []
        if not self.root.is_dir():
            return [f"missing runtime root: {self.root}"]
        if self.root.stat().st_mode & 0o777 != 0o700:
            problems.append(f"runtime root mode must be 0700: {self.root}")
        for directory in self.directories:
            if not directory.is_dir():
                problems.append(f"missing runtime directory: {directory}")
                continue
            if directory.stat().st_mode & 0o777 != 0o700:
                problems.append(f"runtime directory mode must be 0700: {directory}")
        for payload in self.root.rglob("*"):
            if payload.is_file() and payload.stat().st_mode & 0o777 != 0o600:
                problems.append(f"runtime file mode must be 0600: {payload}")
            if payload.is_symlink():
                problems.append(f"runtime symlink is not allowed: {payload}")
        return problems
