from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from support.control_factory import (
    fixture_path,
    make_portable_fixture_root,
    make_separate_portable_fixture_roots,
)

from vaultops.embedding_index import CURRENT_POINTER as C34_CURRENT_POINTER
from vaultops.embedding_index import DEFAULT_MODEL_DIMENSION, build_embedding_index
from vaultops.projection import EXIT_OK, generate_projection
from vaultops.qwen_embedding_index import (
    QWEN_CURRENT_POINTER,
    QWEN_MODEL_DIMENSION,
    QWEN_MODEL_TAG,
    build_qwen_candidate_index,
    evaluate_qwen_promotion,
    load_qwen_candidate_index,
    qwen_embedding_index_manifest_schema,
    qwen_embedding_index_record_schema,
    qwen_learned_retrieve,
    qwen_learned_search,
)


class _DeterministicProvider:
    """Provider-shaped fake for the separate C39 contract tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], bool]] = []

    @staticmethod
    def _embed(text: str, dimension: int) -> list[float]:
        values = [0.0] * dimension
        for token in text.split():
            digest = hashlib.sha256(token.casefold().encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % dimension
            values[index] += 1.0 if digest[8] & 1 else -1.0
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
        dimension = QWEN_MODEL_DIMENSION if model == QWEN_MODEL_TAG else DEFAULT_MODEL_DIMENSION
        self.calls.append((model, selected, truncate))
        return {
            "embeddings": [self._embed(item, dimension) for item in selected],
            "truncated": False,
        }


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_portable_fixture_root(tmp_path)


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _build_projection(root: Path) -> None:
    report, code = generate_projection(root, generation_id="c39-qwen-fixture")
    assert code == EXIT_OK, report


def test_c39_qwen_candidate_is_separate_from_c34_and_immutable(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build_projection(root)
    provider = _DeterministicProvider()

    c34_report, c34_code = build_embedding_index(root, provider, model_digest="a" * 64)
    assert c34_code == EXIT_OK, c34_report
    c34_pointer = (fixture_path(root, "state") / "index/embeddings/current.json")
    c34_before = c34_pointer.read_bytes()

    qwen_report, qwen_code = build_qwen_candidate_index(root, provider, model_digest="b" * 64)

    assert qwen_code == EXIT_OK, qwen_report
    assert qwen_report["embedding_model_tag"] == QWEN_MODEL_TAG
    assert qwen_report["embedding_dimension"] == QWEN_MODEL_DIMENSION
    assert qwen_report["candidate_only"] is True
    assert qwen_report["canonical_c34_mutated"] is False
    assert qwen_report["canonical_apply_allowed"] is False
    assert c34_pointer.read_bytes() == c34_before

    qwen_pointer = fixture_path(root, "state") / QWEN_CURRENT_POINTER
    assert qwen_pointer.is_file()
    assert qwen_pointer.stat().st_mode & 0o777 == 0o600
    index = load_qwen_candidate_index(root, model_digest="b" * 64)
    assert index.dimension == QWEN_MODEL_DIMENSION
    assert index.model_tag == QWEN_MODEL_TAG
    assert all(model == QWEN_MODEL_TAG and truncate is False for model, _, truncate in provider.calls if model == QWEN_MODEL_TAG)


def test_c39_candidate_and_read_use_the_selected_separate_runtime(tmp_path: Path) -> None:
    roots = make_separate_portable_fixture_roots(tmp_path)
    _build_projection(roots)
    provider = _DeterministicProvider()
    vault_before = _file_snapshot(roots.vault)

    c34_report, c34_code = build_embedding_index(roots, provider, model_digest="a" * 64)
    assert c34_code == EXIT_OK, c34_report
    c34_pointer = roots.state / C34_CURRENT_POINTER
    c34_before = c34_pointer.read_bytes()

    qwen_report, qwen_code = build_qwen_candidate_index(roots, provider, model_digest="b" * 64)
    assert qwen_code == EXIT_OK, qwen_report
    qwen_pointer = roots.state / QWEN_CURRENT_POINTER
    assert qwen_pointer.is_file()
    assert qwen_report["canonical_c34_mutated"] is False
    assert c34_pointer.read_bytes() == c34_before

    runtime_before_read = _file_snapshot(roots.runtime)
    result, code = qwen_learned_retrieve(
        roots,
        "방명록 괴담 MVP의 플레이 루프를 고정한다",
        provider,
        model_digest="b" * 64,
        scope="project:guestbook-horror",
        limit=5,
        hops=1,
    )

    assert code == EXIT_OK, result
    assert result["candidate_only"] is True
    assert result["mutation_performed"] is False
    assert _file_snapshot(roots.runtime) == runtime_before_read
    assert _file_snapshot(roots.vault) == vault_before
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()


def test_c39_qwen_retrieval_revalidates_and_rejects_identity_drift(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build_projection(root)
    provider = _DeterministicProvider()
    build_report, build_code = build_qwen_candidate_index(root, provider, model_digest="b" * 64)
    assert build_code == EXIT_OK, build_report

    result, code = qwen_learned_retrieve(
        root,
        "방명록 괴담 MVP의 플레이 루프를 고정한다",
        provider,
        model_digest="b" * 64,
        scope="project:guestbook-horror",
        limit=5,
        hops=1,
    )
    assert code == EXIT_OK, result
    assert result["status"] == "PASS"
    assert result["candidate_only"] is True
    assert result["canonical_c34_mutated"] is False
    assert result["mutation_performed"] is False
    assert result["candidates"]
    assert all("embedding_provider_model_dimension_and_artifact_digest" in item for item in result["candidates"])

    before = len(provider.calls)
    drift, drift_code = qwen_learned_search(
        root,
        "방명록 괴담 MVP의 플레이 루프를 고정한다",
        provider,
        model_digest="c" * 64,
    )
    assert drift_code != EXIT_OK
    assert drift["errors"][0]["code"] == "C39_INDEX_CONFLICT"
    assert len(provider.calls) == before


def test_c39_qwen_promotion_decision_can_select_only_the_separate_candidate() -> None:
    report = evaluate_qwen_promotion(
        live_model_evidence=True,
        quality_gate_passed=True,
        privacy_gate_passed=True,
        citation_gate_passed=True,
        staleness_gate_passed=True,
        resource_gate_passed=True,
        human_decision="promote_separate_qwen",
    )

    assert report["promotion"]["gate_passed"] is True
    assert report["promotion"]["decision"] == "promote_separate_qwen_candidate"
    assert report["promotion"]["canonical_index_mutated"] is False
    assert report["canonical_apply_allowed"] is False


def test_c39_qwen_promotion_requires_explicit_canonical_decision_and_serial_policy() -> None:
    report = evaluate_qwen_promotion(
        live_model_evidence=True,
        quality_gate_passed=True,
        privacy_gate_passed=True,
        citation_gate_passed=True,
        staleness_gate_passed=True,
        resource_gate_passed=True,
        human_decision="promote_qwen_canonical",
    )

    assert report["promotion"]["gate_passed"] is True
    assert report["promotion"]["decision"] == "promote_qwen_canonical"
    assert report["canonical_apply_allowed"] is True
    assert report["canonical_c34_mutated"] is False
    assert report["gates"]["serial_only"] is True
    assert report["gates"]["one_model_loaded"] is True
    assert report["gates"]["unattended_activation"] is False


def test_c39_qwen_owned_schemas_keep_dimension_and_canonical_boundary_frozen() -> None:
    assert qwen_embedding_index_record_schema()["properties"]["embedding"]["minItems"] == QWEN_MODEL_DIMENSION
    manifest = qwen_embedding_index_manifest_schema()
    assert manifest["properties"]["embedding_model_tag"]["const"] == QWEN_MODEL_TAG
    assert manifest["properties"]["embedding_dimension"]["const"] == QWEN_MODEL_DIMENSION
    assert manifest["properties"]["canonical_c34_mutated"]["const"] is False
