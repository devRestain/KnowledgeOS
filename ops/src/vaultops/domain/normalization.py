"""Reuse Blueprint note semantics and deterministic normalization calculation."""

from __future__ import annotations

from typing import Any

from .. import action_proposals as kernel
from ..adapters.paths import ResolvedPaths


def prepare_normalization(roots: ResolvedPaths, source: str, expected_sha256: str) -> dict[str, Any]:
    normalized, _, raw, text, typed, engine, vault = kernel._load_source(roots, source, expected_sha256)
    kernel._check_privacy(typed)
    result = kernel._make_normalize(
        roots, source_path=normalized, source_hash=kernel._sha256(raw),
        text=text, typed=typed, engine=engine, vault=vault, fragment=None,
    )
    if result is None:
        return {"status": "NO_CHANGE", "source_sha256": expected_sha256}
    payload, markdown, diff = result
    prepared, _ = kernel._write_pending_artifact(
        roots, action="normalize", source=typed, source_path=normalized,
        source_hash=kernel._sha256(raw), payload=payload, target_markdown=markdown,
        diff=diff, prepare_only=True,
    )
    return prepared
