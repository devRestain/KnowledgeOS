"""Isolated vector, RRF and stale-generation behavior with a fake local port."""

from __future__ import annotations

from dataclasses import replace

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import AdmissionError
from vaultops.application.knowledge import DomainHandlers, KnowledgeApplication
from vaultops.projection import generate_projection
from vaultops.search_modes import ModelPin, build_vector_index, index_status


class FakeLocalPort:
    trusted_local = True

    def __init__(self, digest_char="a"):
        self.pin = ModelPin("fixture.multilingual", "sha256:" + digest_char * 64, 3,
                            "query: {text}", "document: {text}")
        self.documents = 0
        self.queries = 0

    @staticmethod
    def _features(text):
        lower = text.casefold()
        if "확정" in text:
            return [1.0, 0.0, 0.0]
        if "guestbook horror design note" in lower:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_document(self, text):
        self.documents += 1
        return self._features(text)

    def embed_query(self, text):
        self.queries += 1
        return self._features(text)


def _ready(tmp_path):
    roots = make_separate_portable_fixture_roots(tmp_path)
    report, code = generate_projection(roots, generation_id="multimode-fixture")
    assert code == 0, report
    port = FakeLocalPort()
    app = KnowledgeApplication.from_roots(roots, handlers=DomainHandlers(vector_port=port))
    return roots, app, port


def test_four_modes_provenance_reuse_and_bilingual_fixture_quality(tmp_path):
    roots, app, port = _ready(tmp_path)
    source_bytes = {path.relative_to(roots.vault): path.read_bytes()
                    for path in roots.vault.rglob("*.md")}
    assert index_status(roots, port)["vector_state"] == "unavailable"
    publication = app.daily_stewardship().publish_index()
    first = publication["vector"]
    assert publication["lexical"]["generation_id"] == first["projection_generation_id"]
    assert first["new_vectors"] > 0
    assert index_status(roots, port)["vector_state"] == "ready"
    second = build_vector_index(roots, port)
    assert second["generation_id"] == first["generation_id"]
    assert second["reused_vectors"] == second["record_count"]
    assert port.documents == first["new_vectors"]
    assert source_bytes == {path.relative_to(roots.vault): path.read_bytes()
                            for path in roots.vault.rglob("*.md")}
    queries = {
        "확정되지 않은 정보": "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md",
        "Guestbook Horror Design Note": "40_Knowledge/Sources/Guestbook Horror Design Note.md",
    }
    top_one_hits = {"lexical": 0, "vector": 0, "hybrid": 0}
    for query, expected in queries.items():
        lexical, lexical_code = app.search(query)
        assert lexical_code == 0 and lexical["provider_called"] is False
        top_one_hits["lexical"] += lexical["candidates"][0]["path"] == expected
        for mode in ("vector", "hybrid"):
            report, code = app.search(query, mode=mode)
            assert code == 0, report
            top_one_hits[mode] += report["candidates"][0]["path"] == expected
            assert report["vector_generation_id"] == first["generation_id"]
            assert all(row["resource_reference"]["content_digest"].startswith("sha256:")
                       and row["index_generation_id"] == publication["lexical"]["generation_id"]
                       and row["chunk_locator"] for row in report["candidates"])
            if mode == "hybrid":
                assert report["candidates"][0]["rrf_parameter_and_rank"]["k"] == 60
    assert top_one_hits == {"lexical": 2, "vector": 2, "hybrid": 2}
    # Equal synthetic top-one coverage does not justify changing the default.
    ontology, code = app.search("implements", mode="ontology")
    assert code == 0 and ontology["provider_called"] is False
    assert all(row["graph_path"] and row["chunk_hash"] for row in ontology["candidates"])


def test_vector_reuse_preserves_generation_for_non_unit_embeddings(tmp_path):
    roots, _, _ = _ready(tmp_path)

    class NonUnitPort(FakeLocalPort):
        @staticmethod
        def _features(text):
            return [1.0, 1.0, 7.0]

    port = NonUnitPort()
    first = build_vector_index(roots, port)
    pointer = roots.runtime / "index/vector/current.json"
    before = pointer.read_bytes()
    for _ in range(2):
        reused = build_vector_index(roots, port)
        assert reused["generation_id"] == first["generation_id"]
        assert reused["reused_vectors"] == first["record_count"]
        assert reused["new_vectors"] == 0
        assert port.documents == first["new_vectors"]
        assert pointer.read_bytes() == before


def test_delete_policy_and_model_drift_fail_closed(tmp_path):
    roots, app, port = _ready(tmp_path)
    build_vector_index(roots, port)
    changed = replace(app, handlers=DomainHandlers(vector_port=FakeLocalPort("b")))
    report, code = changed.search("확정되지 않은 정보", mode="vector")
    assert code != 0 and report["errors"][0]["code"] == "INDEX_STALE"
    assert index_status(roots, changed.handlers.vector_port)["vector_state"] == "rebuild_required"

    policy = roots.control / "ops/policies/retrieval.yaml"
    original_policy = policy.read_text()
    assert "default_hops: 1" in original_policy
    policy.write_text(original_policy.replace("default_hops: 1", "default_hops: 0"))
    report, code = app.search("확정되지 않은 정보", mode="hybrid")
    assert code != 0 and report["errors"][0]["code"] in {"INDEX_STALE", "INDEX_INVALID"}
    policy.write_text(original_policy)
    note = roots.vault / "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
    note.unlink()
    report, code = app.search("확정되지 않은 정보", mode="vector")
    assert code != 0 and report["errors"][0]["code"] in {"INDEX_STALE", "INDEX_INVALID"}


def test_failed_embedding_does_not_publish_a_new_lexical_pointer(tmp_path):
    roots, app, _ = _ready(tmp_path)
    pointer = roots.state / "index/exports/current.json"
    before = pointer.read_bytes()
    note = roots.vault / "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
    note.write_text(note.read_text() + "\nNew evidence.\n")

    class FailingPort(FakeLocalPort):
        def embed_document(self, text):
            raise RuntimeError("fixture service interruption")

    failing = replace(app, handlers=DomainHandlers(vector_port=FailingPort()))
    with pytest.raises(AdmissionError) as error:
        failing.daily_stewardship().publish_index()
    assert error.value.code == "EMBEDDING_UNAVAILABLE"
    assert pointer.read_bytes() == before
