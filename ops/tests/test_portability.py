from __future__ import annotations

from support.control_factory import make_separate_portable_fixture_roots

from vaultops.application.knowledge import KnowledgeApplication
from vaultops.projection import generate_projection


def test_independent_relocated_fixture_roots_preserve_domain_results(tmp_path, monkeypatch):
    first = make_separate_portable_fixture_roots(tmp_path / "first", review_queues=True)
    second = make_separate_portable_fixture_roots(tmp_path / "other", review_queues=True)
    monkeypatch.chdir(tmp_path)
    reports = []
    for roots in (first, second):
        for kind in ("core", "control", "vault", "state", "runtime"):
            path = getattr(roots, kind)
            assert all(path != getattr(roots, other) for other in ("core", "control", "vault", "state", "runtime") if kind != other)
        report, code = generate_projection(roots)
        assert code == 0, report
        app = KnowledgeApplication.from_roots(roots)
        retrieved, code = app.retrieve("공포")
        assert code == 0, retrieved
        reports.append([(item["resource_reference"], item["chunk_hash"]) for item in retrieved["candidates"]])
    assert reports[0] == reports[1]
    assert reports[0]
