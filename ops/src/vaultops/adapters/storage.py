"""Explicit, read-only storage resolution for the two different lifecycles."""

from pathlib import Path

from .paths import ResolvedPaths, resolve_beneath


def state_path(roots: ResolvedPaths, relative: str) -> Path:
    """Resolve durable owner evidence without creating even a parent directory."""
    return resolve_beneath(roots.state, relative)


def runtime_path(roots: ResolvedPaths, relative: str) -> Path:
    """Resolve reconstructable host artifacts without filename heuristics."""
    return resolve_beneath(roots.runtime, relative)
