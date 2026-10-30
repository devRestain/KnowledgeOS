"""Disposable owner/Vault round trip for the daily provisional exception."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import AdmissionError
from vaultops.application.exops import OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.work_methods import WorkMethodCatalog
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.note_engine import parse_frontmatter, render_frontmatter
from vaultops.projection import generate_projection

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
QUOTE = "확정되지 않은 정보가 플레이어의 해석을 움직인다."
METHOD = "knowledgeos.daily_corpus_stewardship_team"


def _app(tmp_path):
    roots = make_separate_portable_fixture_roots(
        tmp_path, extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True)
    current = [datetime(2026, 10, 8, 1, tzinfo=UTC)]
    return KnowledgeApplication.from_roots(roots, clock=lambda: current[0]), current


def _work(app, candidate, number=1):
    """Record the completed source-bound artifact returned by a fixture Team run."""
    relative = f"01_AI_Review/Pending/daily-{number}.md"
    path = app.roots.vault / relative
    source = candidate["source_reference"]
    proposal_id = f"{number:08d}-0000-4000-8000-000000000000"
    properties = {"schema_version": 1, "id": "proposal-" + proposal_id,
                  "type": "proposal", "title": f"daily-{number}", "status": "pending",
                  "created": "2026-10-08T10:00:00+09:00",
                  "modified": "2026-10-08T10:00:00+09:00",
                  "aliases": [], "tags": ["ai/review"], "sensitivity": "personal",
                  "ai_policy": "ask", "ai_status": "proposed", "proposal_id": proposal_id,
                  "source_hashes": [SOURCE + "|" + source["content_digest"]]}
    path.write_text(render_frontmatter(properties,
        "```json\n" + json.dumps({"artifact_kind": "daily_enrichment",
                              "content": json.dumps(candidate, ensure_ascii=False)},
                             ensure_ascii=False) + "\n```\n"), encoding="utf-8")
    reference = app.reference(relative, proposal=True)
    formal = {
        "owner_operation_id": "knowledgeos", "artifact_id": f"artifact.daily.{number}",
        "source_revision": "revision.1", "content_digest": reference["content_digest"],
        "media_type": "text/markdown", "size_bytes": len(path.read_bytes()),
        "classification": "internal", "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.mcp.artifact_read",
    }
    app.core.validate("ArtifactReference", formal)
    work_id = f"work.daily.{number}"
    with app.journal.writer() as writer:
        writer.value["intents"]["exops.work." + work_id] = {
            "application_state": "recorded",
            "spec": {"team_id": "corpus-stewardship",
                     "method_contract_ref": WorkMethodCatalog.load(app.roots.control).reference(METHOD)},
            "method_source_reference": candidate["source_reference"],
            "run": {"state": "completed", "result_reference": formal},
            "artifact_references": {formal["artifact_id"]: formal},
            "submitted_artifacts": [reference],
        }
        writer.commit(app.clock())
    return work_id


def _candidate(app, fields):
    return {"source_reference": app.reference(SOURCE),
            "registry_digest": app.daily_stewardship()._registry_digest(),
            "fields": fields, "evidence": {field: [QUOTE] for field in fields}}


def test_due_resume_no_evidence_and_policy_hold(tmp_path):
    app, current = _app(tmp_path)
    steward = app.daily_stewardship()
    first = steward.due_check()
    same_day = steward.due_check()
    request = next(item for item in first["work_requests"] if item["source_reference"] == app.reference(SOURCE))
    assert request in same_day["work_requests"]
    assert request["method_contract_ref"] == WorkMethodCatalog.load(app.roots.control).reference(METHOD)
    empty = _candidate(app, {})
    work = _work(app, empty)
    before = (app.roots.vault / SOURCE).read_bytes()
    assert steward.record_no_evidence(work, empty)["status"] == "no_evidence"
    current[0] += timedelta(days=2)
    assert request not in steward.due_check()["work_requests"]
    assert (app.roots.vault / SOURCE).read_bytes() == before
    held = app.roots.vault / "40_Knowledge/Notes/Unclassified.md"
    held.write_text("---\ntype: knowledge\ntitle: Unclassified\n---\n# Unclassified\n", encoding="utf-8")
    assert "40_Knowledge/Notes/Unclassified.md" in steward.due_check()["held_paths"]
    assert (app.roots.vault / SOURCE).read_bytes() == before


@pytest.mark.parametrize("missing", ["artifact_references", "submitted_artifacts"])
def test_candidate_requires_admitted_artifact_export(tmp_path, missing):
    app, _ = _app(tmp_path)
    steward = app.daily_stewardship()
    steward.due_check()
    candidate = _candidate(app, {"tags": ["narrative"]})
    work_id = _work(app, candidate)
    with app.journal.writer() as writer:
        writer.value["intents"]["exops.work." + work_id].pop(missing)
        writer.commit(app.clock())
    before = (app.roots.vault / SOURCE).read_bytes()

    with pytest.raises(AdmissionError):
        steward.record_candidate(work_id, candidate)

    assert (app.roots.vault / SOURCE).read_bytes() == before
    assert steward.pending() == []


@pytest.mark.parametrize("field,value", [("ai_policy", "deny"),
                                         ("sensitivity", "confidential"),
                                         ("ai_policy", "local_only")])
def test_denied_confidential_and_unbound_local_notes_are_excluded(tmp_path, field, value):
    app, _ = _app(tmp_path)
    note_path = app.roots.vault / SOURCE
    note = parse_frontmatter(note_path.read_text())
    changed = dict(note.properties)
    changed[field] = value
    note_path.write_text(render_frontmatter(changed, note.body), encoding="utf-8")
    original = note_path.read_bytes()
    requests = app.daily_stewardship().due_check()["work_requests"]
    assert all(item["source_reference"] != app.reference(SOURCE) for item in requests)
    assert note_path.read_bytes() == original


def test_provisional_confirm_reject_and_conflict_preserve_user_values(tmp_path):
    app, _ = _app(tmp_path)
    steward = app.daily_stewardship()
    steward.due_check()
    candidate = _candidate(app, {"tags": ["narrative"],
                                  "related": ["[[플레이어가 텍스트 공포 게임에 다시 돌아올 이유는 무엇인가]]"]})
    effect = steward.record_candidate(_work(app, candidate), candidate)
    note_path = app.roots.vault / SOURCE
    pending = parse_frontmatter(note_path.read_text()).properties
    assert pending["tags"] == ["narrative", "ai/pending"]
    assert pending["projects"] == ["[[guestbook-horror]]"]
    assert steward.pending_relation_fields() == {(SOURCE, "related")}
    report, code = generate_projection(app.roots, generation_id="daily-pending")
    assert code == 0, report
    ontology, code = app.search("정보의 빈칸", mode="ontology")
    assert code == 0 and not any("related" in edge for row in ontology["candidates"]
                                 for edge in row["graph_path"])
    steward.review(effect["effect_id"], "confirm")
    assert steward.pending_relation_fields() == set()
    assert "ai/pending" not in parse_frontmatter(note_path.read_text()).properties["tags"]
    report, code = generate_projection(app.roots, generation_id="daily-confirmed")
    assert code == 0, report
    ontology, code = app.search("정보의 빈칸", mode="ontology")
    assert code == 0 and any("related" in edge for row in ontology["candidates"]
                             for edge in row["graph_path"])

    # A second isolated note checks exact-delta rollback and conflict behavior.
    other = app.roots.vault / "40_Knowledge/Notes/Second.md"
    source = parse_frontmatter((app.roots.vault / SOURCE).read_text())
    props = dict(source.properties)
    props.update(id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", title="Second", tags=[], related=[])
    other.write_text(render_frontmatter(props, source.body), encoding="utf-8")
    steward.due_check()
    second = {"source_reference": app.reference("40_Knowledge/Notes/Second.md"),
              "registry_digest": steward._registry_digest(),
              "fields": {"tags": ["narrative"],
                         "raises": ["[[플레이어가 텍스트 공포 게임에 다시 돌아올 이유는 무엇인가]]"]},
              "evidence": {"tags": [QUOTE], "raises": [QUOTE]}}
    rejected = steward.record_candidate(_work(app, second, 2), second)
    steward.review(rejected["effect_id"], "reject")
    rejected_properties = parse_frontmatter(other.read_text()).properties
    assert rejected_properties["tags"] == []
    assert "raises" not in rejected_properties
    other.write_text(other.read_text() + "\nNew source context.\n", encoding="utf-8")
    steward.due_check()
    third = {**second, "source_reference": app.reference("40_Knowledge/Notes/Second.md")}
    conflict = steward.record_candidate(_work(app, third, 3), third)
    edited = parse_frontmatter(other.read_text())
    changed = dict(edited.properties)
    changed["tags"] = ["narrative", "human-edited", "ai/pending"]
    other.write_text(render_frontmatter(changed, edited.body), encoding="utf-8")
    with pytest.raises(AdmissionError, match="changed a provisional value"):
        steward.review(conflict["effect_id"], "reject")
    assert steward.read_pending(conflict["effect_id"])["review_status"] == "conflict"
    assert "human-edited" in parse_frontmatter(other.read_text()).properties["tags"]


@pytest.mark.parametrize("original_tags", ["absent", None, "", []])
def test_reject_restores_original_empty_tags_shape(tmp_path, original_tags):
    app, _ = _app(tmp_path)
    note_path = app.roots.vault / SOURCE
    note = parse_frontmatter(note_path.read_text(encoding="utf-8"))
    properties = dict(note.properties)
    if original_tags == "absent":
        properties.pop("tags", None)
    else:
        properties["tags"] = original_tags
    note_path.write_text(render_frontmatter(properties, note.body), encoding="utf-8")
    steward = app.daily_stewardship()
    steward.due_check()
    candidate = _candidate(app, {"tags": ["narrative"]})
    effect = steward.record_candidate(_work(app, candidate), candidate)

    steward.review(effect["effect_id"], "reject")

    restored = parse_frontmatter(note_path.read_text(encoding="utf-8"))
    assert ("tags" in restored.properties) == ("tags" in properties)
    assert restored.properties.get("tags") == properties.get("tags")
    assert restored.body == note.body
    assert steward.pending() == []


def test_stale_source_and_unknown_effect_require_reconciliation(tmp_path):
    app, _ = _app(tmp_path)
    steward = app.daily_stewardship()
    steward.due_check()
    candidate = _candidate(app, {"tags": ["narrative"]})
    work = _work(app, candidate)
    note = app.roots.vault / SOURCE
    note.write_text(note.read_text() + "\nHuman edit.\n")
    with pytest.raises(AdmissionError):
        steward.record_candidate(work, candidate)
    assert "ai/pending" not in parse_frontmatter(note.read_text()).properties["tags"]


def test_interrupted_apply_reconciles_observed_bytes_without_replay(tmp_path):
    app, _ = _app(tmp_path)
    app.daily_stewardship().due_check()
    candidate = _candidate(app, {"tags": ["narrative"]})
    work = _work(app, candidate)
    interrupted = replace(app, fault_hook=lambda phase: (_ for _ in ()).throw(
        RuntimeError(phase)) if phase == "daily_canonical_written" else None)
    with pytest.raises(RuntimeError, match="daily_canonical_written"):
        interrupted.daily_stewardship().record_candidate(work, candidate)
    unknown = [key for key, item in app.journal.read()["intents"].items()
               if item.get("state") == "unknown" and item.get("path") == SOURCE]
    assert len(unknown) == 1
    before = (app.roots.vault / SOURCE).read_bytes()
    assert app.daily_stewardship().reconcile(unknown[0])["state"] == "completed"
    assert (app.roots.vault / SOURCE).read_bytes() == before
    with pytest.raises(AdmissionError, match="reconciliation"):
        app.daily_stewardship().record_candidate(work, candidate)


def test_authenticated_daily_review_route_exposes_exact_delta(tmp_path):
    app, _ = _app(tmp_path)
    steward = app.daily_stewardship()
    steward.due_check()
    candidate = _candidate(app, {"tags": ["narrative"]})
    effect = steward.record_candidate(_work(app, candidate), candidate)
    exops = SimpleNamespace(application=app, read_only=False,
                            authorize_actor=lambda actor, action: actor.actor_id == "owner.user"
                            and action in {"daily_list", "daily_read", "daily_decide"})
    backend = ExOpsWebBackend(exops, lambda login: OwnerActor("owner.user", "web")
                              if login == "owner.user" else None,
                              lambda actor, token: token == "valid")

    def call(path, payload, token="valid"):
        return backend(SimpleNamespace(owner_operation_id="knowledgeos", actor_login="owner.user",
                                       method="POST", resource_path=path,
                                       body=json.dumps(payload).encode(), csrf_token=token))

    with pytest.raises(AdmissionError, match="CSRF"):
        call("daily/list", {}, token="bad")
    listing = call("daily/list", {})
    assert any(item["effect_id"] == effect["effect_id"] for item in listing["pending"])
    detail = call("daily/read", {"effect_id": effect["effect_id"]})
    assert detail["added_fields"] == candidate["fields"]
    assert detail["evidence"] == candidate["evidence"]
    assert detail["original_fields"]["tags"] == {"present": True, "value": []}
    assert detail["before_digest"] == effect["before_digest"]
    assert call("daily/decide", {"effect_id": effect["effect_id"], "decision": "reject"})["decision"] == "reject"
    assert not call("daily/list", {})["pending"]
