from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from support.control_factory import (
    APPLICATION_CONTROL_INPUTS,
    fixture_path,
    make_control_root,
    make_separate_portable_fixture_roots,
    populate_vault_from_fixture,
)

from vaultops.embedding_index import (
    CURRENT_POINTER,
    DEFAULT_MODEL_DIMENSION,
    DEFAULT_MODEL_TAG,
    EMBEDDING_CONFIG_SHA256,
    UNKNOWN_QUERY_ABSTENTION_THRESHOLD,
    _apply_unknown_query_abstention,
    build_embedding_index,
    embedding_index_manifest_schema,
    evaluate_c34_baseline,
    learned_retrieval_evaluation_schema,
    learned_search,
    load_embedding_index,
)
from vaultops.projection import EXIT_CONFLICT, EXIT_OK, generate_projection

MODEL_DIGEST = "a" * 64


class _DeterministicEmbeddingProvider:
    """Provider-shaped fake for contract and ranking tests, never a promotion gate."""

    def __init__(self, *, truncated: bool = False) -> None:
        self.truncated = truncated
        self.calls: list[tuple[str, list[str], bool]] = []

    @staticmethod
    def _embed(text: str) -> list[float]:
        values = [0.0] * DEFAULT_MODEL_DIMENSION
        tokens = [token.casefold() for token in text.split() if token]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % DEFAULT_MODEL_DIMENSION
            sign = 1.0 if digest[8] & 1 else -1.0
            values[index] += sign
        magnitude = math.sqrt(sum(value * value for value in values))
        return [value / magnitude for value in values] if magnitude else values

    def embed(
        self,
        model: str,
        inputs: str | list[str],
        *,
        options: dict[str, Any] | None = None,
        truncate: bool = False,
        keep_alive: int = 0,
    ) -> dict[str, Any]:
        del options, keep_alive
        selected = [inputs] if isinstance(inputs, str) else list(inputs)
        self.calls.append((model, selected, truncate))
        return {
            "embeddings": [self._embed(item) for item in selected],
            "truncated": self.truncated,
        }


def _fresh_control_copy(tmp_path: Path) -> Path:
    inputs = tuple(item for item in APPLICATION_CONTROL_INPUTS if item != "ops/vaultops.toml")
    root = make_control_root(
        tmp_path,
        (*inputs, "ops/tests/fixtures/c34_embeddings"),
    )
    populate_vault_from_fixture(
        root,
        "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input",
    )
    return root


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _build(root: Path, generation_id: str = "c34-fixture") -> None:
    report, code = generate_projection(root, generation_id=generation_id)
    assert code == EXIT_OK, report


def test_c34_builds_private_digest_bound_immutable_index(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    provider = _DeterministicEmbeddingProvider()

    report, code = build_embedding_index(root, provider, model_digest=MODEL_DIGEST)

    assert code == EXIT_OK, report
    assert report["status"] == "PASS"
    assert report["embedding_config_sha256"] == EMBEDDING_CONFIG_SHA256
    assert report["embedding_model_tag"] == DEFAULT_MODEL_TAG
    assert report["embedding_dimension"] == DEFAULT_MODEL_DIMENSION
    assert report["provider_called"] is True
    assert report["mutation_performed"] is False
    assert report["vault_mutation_performed"] is False
    index = load_embedding_index(
        root,
        model_tag=DEFAULT_MODEL_TAG,
        model_digest=MODEL_DIGEST,
        dimension=DEFAULT_MODEL_DIMENSION,
    )
    assert index.index_id == report["embedding_index_id"]
    assert len(index.records) == report["vector_record_count"]
    generation = (fixture_path(root, "runtime") / "index/embeddings/generations") / index.index_id
    assert (generation / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert (generation / "vectors.jsonl").stat().st_mode & 0o777 == 0o600
    assert (fixture_path(root, "state") / "index/embeddings/current.json").stat().st_mode & 0o777 == 0o600
    assert all(model == DEFAULT_MODEL_TAG and truncate is False for model, _, truncate in provider.calls)
    assert any(prompt.startswith("title: ") for _, prompts, _ in provider.calls for prompt in prompts)


def test_c34_uses_separate_runtime_for_index_and_learned_read(tmp_path: Path) -> None:
    roots = make_separate_portable_fixture_roots(
        tmp_path,
        extra_inputs=("ops/tests/fixtures/c34_embeddings",),
    )
    _build(roots)
    vault_before = _file_snapshot(roots.vault)
    provider = _DeterministicEmbeddingProvider()

    report, code = build_embedding_index(roots, provider, model_digest=MODEL_DIGEST)

    assert code == EXIT_OK, report
    index = load_embedding_index(
        roots,
        model_tag=DEFAULT_MODEL_TAG,
        model_digest=MODEL_DIGEST,
        dimension=DEFAULT_MODEL_DIMENSION,
    )
    pointer = roots.state / CURRENT_POINTER
    assert pointer.is_file()
    assert index.index_id == report["embedding_index_id"]
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()
    runtime_before_read = _file_snapshot(roots.runtime)

    learned, learned_code = learned_search(
        roots,
        "확정되지 않은 정보",
        provider,
        model_digest=MODEL_DIGEST,
    )

    assert learned_code == EXIT_OK, learned
    assert learned["provider_called"] is True
    assert learned["mutation_performed"] is False
    assert _file_snapshot(roots.runtime) == runtime_before_read
    assert _file_snapshot(roots.vault) == vault_before


def test_c34_query_identity_drift_and_truncation_fail_closed(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    provider = _DeterministicEmbeddingProvider()
    build_report, build_code = build_embedding_index(root, provider, model_digest=MODEL_DIGEST)
    assert build_code == EXIT_OK, build_report
    before = len(provider.calls)

    drift, drift_code = learned_search(
        root,
        "확정되지 않은 정보",
        provider,
        model_digest="b" * 64,
    )
    assert drift_code == EXIT_CONFLICT
    assert drift["errors"][0]["code"] == "C34_INDEX_CONFLICT"
    assert len(provider.calls) == before

    truncating = _DeterministicEmbeddingProvider(truncated=True)
    truncated, truncated_code = learned_search(
        root,
        "확정되지 않은 정보",
        truncating,
        model_digest=MODEL_DIGEST,
    )
    assert truncated_code == EXIT_CONFLICT
    assert truncated["errors"][0]["code"] == "C34_INDEX_CONFLICT"


def test_c34_learned_evaluation_compares_all_channels_without_promotion(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root, generation_id="c34-evaluation")
    provider = _DeterministicEmbeddingProvider()

    report, code = evaluate_c34_baseline(root, provider)

    assert code == EXIT_OK, report
    assert report["status"] == "PASS"
    assert report["metrics"]["case_count"] == 4
    assert report["metrics"]["all_cases_passed"] is True
    assert report["metrics"]["quality_gate_passed"] is True
    assert report["metrics"]["promotion_gate_passed"] is False
    assert report["metrics"]["promotion_decision"] == "retain_lexical_baseline"
    assert report["learned_retrieval_opt_in"] is True
    assert report["live_model_evidence"] is False
    assert report["provider_called"] is True
    assert report["mutation_performed"] is False
    assert all(set(case) == {"id", "mode", "lexical", "e01_feature", "learned", "rrf", "passed"} for case in report["cases"])
    assert not list(Draft202012Validator(learned_retrieval_evaluation_schema()).iter_errors(report))


def test_c34_owned_schemas_match_executable_contracts() -> None:
    assert embedding_index_manifest_schema()["properties"]["embedding_dimension"]["const"] == DEFAULT_MODEL_DIMENSION
    assert embedding_index_manifest_schema()["properties"]["truncate"]["const"] is False
    assert learned_retrieval_evaluation_schema()["properties"]["live_model_evidence"]["const"] is False


def test_c34_unknown_query_abstention_is_frozen_and_fail_closed() -> None:
    known = [{"vector_score_and_rank": {"score": 0.583974046833}}]
    unknown = [{"vector_score_and_rank": {"score": 0.245790569561}}]

    retained, known_report = _apply_unknown_query_abstention(known)
    assert retained == known
    assert known_report["abstained"] is False
    assert known_report["threshold"] == UNKNOWN_QUERY_ABSTENTION_THRESHOLD

    retained, unknown_report = _apply_unknown_query_abstention(unknown)
    assert retained == []
    assert unknown_report["abstained"] is True
    assert unknown_report["reason"] == "top_vector_score_below_threshold"
