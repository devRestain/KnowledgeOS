from __future__ import annotations

import copy
import hashlib
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultops.answer import answer
from vaultops.note_engine import render_frontmatter
from vaultops.projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    answer_schema,
    generate_projection,
    load_current_projection,
)
from vaultops.retrieval import RetrievalConflict, _load_contract, revalidate_candidates
from vaultops.vector import vector_retrieve

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"


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
    shutil.copytree(FIXTURE_ROOT, root / "KnowledgeHub")
    return root


def _write_frontmatter_fixture(root: Path) -> str:
    relative = "40_Knowledge/Notes/C26 Frontmatter Marker.md"
    properties = {
        "schema_version": 1,
        "id": "c26f0000-0000-4000-8000-000000000026",
        "type": "knowledge",
        "title": "C26 Frontmatter Marker",
        "status": "developing",
        "created": "2026-09-17T10:00:00+09:00",
        "modified": "2026-09-17T10:00:00+09:00",
        "aliases": [],
        "tags": [],
        "sensitivity": "personal",
        "ai_policy": "ask",
        "ai_status": "idle",
        "areas": [],
        "projects": [],
        "topics": [],
        "sources": [],
        "related": [],
        "claim": "c26-frontmatter-marker",
        "confidence": "high",
        "last_reviewed": "2026-09-17",
    }
    body = "# Body-only fallback\n\nThis text must never be shown for a frontmatter citation.\n"
    path = root / "KnowledgeHub" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontmatter(properties, body), encoding="utf-8")
    return relative


def _build(root: Path, generation_id: str = "c26-fixture") -> None:
    report, code = generate_projection(root, generation_id=generation_id)
    assert code == EXIT_OK, report


def test_c26_citation_binds_frontmatter_chunk_and_displayed_excerpt(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    relative = _write_frontmatter_fixture(root)
    _build(root)

    report, code = answer(
        root,
        "c26 frontmatter marker",
        include_types=["knowledge"],
        limit=10,
        hops=0,
    )

    assert code == EXIT_OK, report
    citation = report["citations"][0]
    assert citation["path"] == relative
    assert citation["locator"] == "/frontmatter"
    assert citation["note_id"] == "c26f0000-0000-4000-8000-000000000026"
    assert citation["index_generation_id"] == "c26-fixture"
    assert "c26-frontmatter-marker" in citation["excerpt"]
    assert "Body-only fallback" not in citation["excerpt"]
    assert citation["excerpt_sha256"] == hashlib.sha256(citation["excerpt"].encode("utf-8")).hexdigest()
    assert citation["sha256"] == citation["chunk_hash"]
    assert not list(Draft202012Validator(answer_schema()).iter_errors(report["answer"]))

    projection = load_current_projection(root)
    note = next(note for note in projection.notes if note["path"] == relative)
    assert citation["content_hash"] == note["content_hash"]
    assert citation["chunk_id"].startswith(f"{note['id']}:")


def test_c26_rrf_keeps_channel_chunks_and_rejects_channel_or_generation_drift(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _write_frontmatter_fixture(root)
    _build(root)

    report, code = vector_retrieve(
        root,
        "c26 frontmatter marker",
        include_types=["knowledge"],
        limit=10,
        hops=0,
    )

    assert code == EXIT_OK, report
    candidate = report["candidates"][0]
    lexical = candidate["lexical_score_and_rank"]
    vector = candidate["vector_score_and_rank"]
    assert lexical["provenance"]["note_id"] == candidate["note_id"]
    assert vector["provenance"]["note_id"] == candidate["note_id"]
    assert lexical["provenance"]["index_generation_id"] == "c26-fixture"
    assert vector["provenance"]["index_generation_id"] == "c26-fixture"
    assert lexical["provenance"]["chunk_id"]
    assert vector["provenance"]["chunk_id"]

    projection = load_current_projection(root)
    _, policy, _ = _load_contract(root)
    revalidate_candidates(
        projection,
        policy,
        [candidate],
        include_types=["knowledge"],
        limit=10,
        hops=0,
    )

    channel_drift = copy.deepcopy(candidate)
    channel_drift["vector_score_and_rank"]["provenance"]["chunk_locator"] = "#not-the-selected-chunk"
    with pytest.raises(RetrievalConflict):
        revalidate_candidates(
            projection,
            policy,
            [channel_drift],
            include_types=["knowledge"],
            limit=10,
            hops=0,
        )

    generation_drift = copy.deepcopy(candidate)
    generation_drift["index_generation_id"] = "other-generation"
    with pytest.raises(RetrievalConflict):
        revalidate_candidates(
            projection,
            policy,
            [generation_drift],
            include_types=["knowledge"],
            limit=10,
            hops=0,
        )

    assert EXIT_CONFLICT == 30
