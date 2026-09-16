from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from vaultops.cli import main
from vaultops.projection import (
    EXIT_CONFLICT,
    EXIT_OK,
    generate_projection,
    retrieval_candidate_schema,
)
from vaultops.retrieval import search
from vaultops.vector import (
    DEFAULT_RRF_K,
    VECTOR_ALGORITHM,
    VECTOR_CONFIG_SHA256,
    VECTOR_DIMENSION,
    cosine_similarity,
    embed_text,
    evaluate_vector_baseline,
    reciprocal_rank_fusion,
    vector_evaluation_schema,
    vector_retrieve,
    vector_search,
)

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"
BASELINE_PATH = "ops/tests/fixtures/e01_vectors/evaluation.yaml"


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


def _build(root: Path, generation_id: str = "e01-fixture") -> None:
    report, code = generate_projection(root, generation_id=generation_id)
    assert code == EXIT_OK, report


def _runtime_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "runtime").rglob("*")
        if path.is_file()
    }


def test_e01_embedding_and_rrf_are_deterministic() -> None:
    first = embed_text("확정되지 않은 정보")
    second = embed_text("확정되지 않은 정보")

    assert first == second
    assert len(first) == VECTOR_DIMENSION
    assert cosine_similarity(first, first) == 1.0
    fused = reciprocal_rank_fusion(
        {
            "lexical": [{"note_id": "a"}, {"note_id": "b"}],
            "vector": [{"note_id": "b"}, {"note_id": "a"}],
        },
        k=DEFAULT_RRF_K,
    )
    assert fused["a"]["rank"] == 1
    assert fused["a"]["components"]["lexical"]["rank"] == 1
    assert fused["a"]["components"]["vector"]["rank"] == 2


def test_e01_vector_search_is_local_deterministic_and_schema_valid(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    before = _runtime_snapshot(root)

    first, first_code = vector_search(root, "확정되지 않은 정보")
    second, second_code = vector_search(root, "확정되지 않은 정보")

    assert first_code == EXIT_OK, first
    assert second_code == EXIT_OK, second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["vector_enabled"] is True
    assert first["retrieval_mode"] == "vector_only"
    assert first["vector_config_sha256"] == VECTOR_CONFIG_SHA256
    assert first["candidates"][0]["vector_score_and_rank"]["algorithm"] == (
        "cosine_" + VECTOR_ALGORITHM
    )
    assert first["candidates"][0]["embedding_provider_model_dimension_and_artifact_digest"] == {
        "provider": "local",
        "model": VECTOR_ALGORITHM,
        "dimension": VECTOR_DIMENSION,
        "artifact_digest": VECTOR_CONFIG_SHA256,
    }
    for candidate in first["candidates"]:
        assert not list(Draft202012Validator(retrieval_candidate_schema()).iter_errors(candidate))
    assert first["provider_called"] is False
    assert first["mutation_performed"] is False
    assert _runtime_snapshot(root) == before


def test_e01_rrf_retrieve_preserves_c22_default_and_expands_links(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)

    lexical, lexical_code = search(root, "플레이 루프", scope="project:guestbook-horror")
    fused, fused_code = vector_retrieve(root, "플레이 루프", scope="project:guestbook-horror")

    assert lexical_code == EXIT_OK, lexical
    assert fused_code == EXIT_OK, fused
    assert lexical["candidates"][0]["vector_score_and_rank"] is None
    assert fused["retrieval_mode"] == "vector_rrf"
    assert fused["candidates"][0]["rrf_parameter_and_rank"]["algorithm"] == "rrf_v1"
    assert any("typed_link_expansion" in item["retrieval_reason"] for item in fused["candidates"])
    assert any("40_Knowledge/Ideas/" in item["path"] for item in fused["candidates"])


def test_e01_frozen_evaluation_reports_metrics_without_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root, generation_id="e01-pinned")
    before = _runtime_snapshot(root)

    report, code = evaluate_vector_baseline(
        root,
        baseline_path=BASELINE_PATH,
        expected_generation_id="e01-pinned",
    )

    assert code == EXIT_OK, report
    assert report["status"] == "PASS"
    assert report["metrics"]["all_cases_passed"] is True
    assert report["metrics"]["promotion_decision"] in {
        "promote_optional_rrf",
        "retain_lexical_baseline",
    }
    assert not list(Draft202012Validator(vector_evaluation_schema()).iter_errors(report))
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False
    assert _runtime_snapshot(root) == before


def test_e01_schema_artifact_matches_executable_contract() -> None:
    artifact = json.loads(
        (CONTROL_ROOT / "ops/schemas/e01-vector-evaluation.schema.json").read_text(encoding="utf-8")
    )
    assert artifact == vector_evaluation_schema()


def test_e01_stale_projection_fails_closed(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    source = root / "KnowledgeHub/20_Projects/guestbook-horror/guestbook-horror.md"
    source.write_bytes(source.read_bytes() + b"\n")
    before = _runtime_snapshot(root)

    report, code = vector_retrieve(root, "방명록")

    assert code == EXIT_CONFLICT
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "E01_INDEX_STALE"
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False
    assert _runtime_snapshot(root) == before


def test_e01_cli_exposes_explicit_vector_routes(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    monkeypatch.setattr("sys.stdin", io.StringIO("synthetic source\n"))

    assert main(["retrieve", "--vector", "--query-stdin", "--root", str(root)]) == EXIT_OK
    fused = json.loads(capsys.readouterr().out)
    assert fused["capability"] == "E01"
    assert fused["retrieval_mode"] == "vector_rrf"

    assert main(
        [
            "vector",
            "evaluate",
            "--evaluation-file",
            BASELINE_PATH,
            "--root",
            str(root),
        ]
    ) == EXIT_OK
    evaluated = json.loads(capsys.readouterr().out)
    assert evaluated["operation"] == "vector evaluate"
    assert evaluated["metrics"]["all_cases_passed"] is True
