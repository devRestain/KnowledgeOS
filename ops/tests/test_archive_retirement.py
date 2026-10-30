"""Retirement checks run only against disposable State and an inert fake payload."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.archive_retirement import preview_legacy_retirement, retire_legacy_archive
from vaultops.adapters.core import AdmissionError, canonical
from vaultops.adapters.legacy_archive import inventory
from vaultops.application.knowledge import KnowledgeApplication


def _fixture(tmp_path):
    roots = make_separate_portable_fixture_roots(tmp_path)
    app = KnowledgeApplication.from_roots(roots)
    with app.journal.writer() as writer:
        writer.commit(datetime.now(UTC))
    target = "archives/legacy-runtime/fixture"
    snapshot = roots.state / target
    payload = snapshot / "payload"
    payload.mkdir(parents=True, mode=0o700)
    (payload / "inert.json").write_text('{"legacy":"inert"}\n', encoding="utf-8")
    preserved = inventory(payload)
    transition = {"phase": "complete", "target": target, "inventory": preserved}
    (roots.state / "storage-transition.json").write_bytes(canonical(transition) + b"\n")
    (snapshot / "manifest.json").write_bytes(canonical({
        "disposition": "inert", "authority_imported": False, "inventory": preserved}) + b"\n")
    return app, target, preserved


def test_archive_retirement_requires_exact_reference_graph_and_checkpoint(tmp_path):
    app, target, preserved = _fixture(tmp_path)
    preview = preview_legacy_retirement(app)
    assert preview["status"] == "PASS" and preview["target"] == target
    reference = app.roots.state / "other.json"
    reference.write_text('{"old":"' + target + '"}\n', encoding="utf-8")
    assert preview_legacy_retirement(app)["status"] == "BLOCKED"
    reference.unlink()
    evidence = {"target": target, "inventory_digest": preserved["inventory_digest"],
                "observed_at": datetime.now(UTC).isoformat(),
                "writers_quiescent": True, "checks": ["synthetic fixture has no writers"]}
    with pytest.raises(AdmissionError, match="stale or blocked"):
        retire_legacy_archive(app, expected_checkpoint="sha256:" + "0" * 64,
                              writer_evidence=evidence)
    assert (app.roots.state / target / "payload" / "inert.json").is_file()
    applied = retire_legacy_archive(app, expected_checkpoint=preview["checkpoint_digest"],
                                     writer_evidence=evidence)
    assert applied["archive_removed"] is True
    assert not (app.roots.state / target).exists()
    assert not (app.roots.state / "storage-transition.json").exists()
    assert (app.roots.state / "storage-retirement.json").is_file()
