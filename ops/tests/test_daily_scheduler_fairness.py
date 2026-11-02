"""Durable consumption fairness using historical owner-export fixtures.

The candidate fixture matches the existing daily stewardship owner-boundary
regressions; it is not a simulated native Worker, model or assessment. Assertions
exercise the real exact-delta writer. Native Team evidence stays in its smoke.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from support.control_factory import make_separate_portable_fixture_roots
from support.metadata_fixture import SOURCE

from vaultops.adapters.core import AdmissionError, digest
from vaultops.adapters.owner_journal import stamp
from vaultops.application.daily_scheduler import DailyAdmissionScheduler
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.note_engine import parse_frontmatter, render_frontmatter

QUOTE = "확정되지 않은 정보가 플레이어의 해석을 움직인다."
CURSOR = "daily.scheduler.consumption"


def owner_export_fixture(tmp_path, effect_calls):
    roots = make_separate_portable_fixture_roots(tmp_path,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    app = KnowledgeApplication.from_roots(roots, fault_hook=effect_calls.append)
    document = parse_frontmatter((roots.vault / SOURCE).read_text())
    clone_path = "40_Knowledge/Notes/Fairness.md"
    properties = dict(document.properties)
    properties.update(id=str(uuid4()), title="Fairness")
    (roots.vault / clone_path).write_text(render_frontmatter(properties, document.body))
    steward = app.daily_stewardship()
    due = steward.due_check()
    paths = {app.reference(path)["logical_id"]: path for path in (SOURCE, clone_path)}
    selected = sorted(("daily.admission." + request["request_id"], request)
                      for request in due["work_requests"] if request["source_reference"]["logical_id"] in paths)
    assert len(selected) == 2
    unknown_key, _unknown_request = selected[0]
    complete_key, complete_request = selected[1]
    source = complete_request["source_reference"]
    candidate = {"source_reference": source, "registry_digest": steward._registry_digest(),
                 "fields": {"tags": ["narrative"]}, "evidence": {"tags": [QUOTE]}}
    relative = "01_AI_Review/Pending/daily-fairness.md"
    proposal_id = str(uuid4())
    proposal = {"schema_version": 1, "id": "proposal-" + proposal_id, "type": "proposal",
                "title": "daily-fairness", "status": "pending", "created": stamp(app.clock()),
                "modified": stamp(app.clock()), "aliases": [], "tags": ["ai/review"],
                "sensitivity": "personal", "ai_policy": "ask", "ai_status": "proposed",
                "proposal_id": proposal_id,
                "source_hashes": [paths[source["logical_id"]] + "|" + source["content_digest"]]}
    payload = {"artifact_kind": "daily_enrichment", "content": json.dumps(candidate, ensure_ascii=False)}
    artifact_path = roots.vault / relative
    artifact_path.write_text(render_frontmatter(proposal, "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"))
    reference = app.reference(relative, proposal=True)
    artifact = {"owner_operation_id": "knowledgeos", "artifact_id": "artifact.daily.fairness",
                "source_revision": reference["source_revision"], "content_digest": reference["content_digest"],
                "media_type": "text/markdown", "size_bytes": len(artifact_path.read_bytes()),
                "classification": "internal", "availability": "available_by_owner_export",
                "export_capability_id": "knowledgeos.mcp.artifact_read"}
    app.core.validate("ArtifactReference", artifact)
    with app.journal.writer() as writer:
        for key, request in selected:
            work_id = "work.daily.fairness." + ("unknown" if key == unknown_key else "complete")
            writer.value["intents"][key] = {
                "state": "admitted", "recorded_at": stamp(app.clock()), "day": due["day"],
                "path": paths[request["source_reference"]["logical_id"]], "request": request,
                "registry_digest": steward._registry_digest(), "policy_digest": steward._policy_digest(),
                "receipt": {"work_run_id": work_id},
            }
            writer.value["intents"]["exops.work." + work_id] = {
                "state": "unknown" if key == unknown_key else "completed", "recorded_at": stamp(app.clock()),
                "dispatch_state": "unknown" if key == unknown_key else "accepted",
                "application_state": "not_started" if key == unknown_key else "recorded",
                "spec": {"team_id": "corpus-stewardship", "method_contract_ref": request["method_contract_ref"]},
                "method_source_reference": request["source_reference"],
                "run": {"state": "admitted"} if key == unknown_key else {"state": "completed", "result_reference": artifact},
                "artifact_references": {} if key == unknown_key else {artifact["artifact_id"]: artifact},
                "submitted_artifacts": [] if key == unknown_key else [reference],
            }
        writer.commit(app.clock())
    return app, unknown_key, complete_key, roots.vault / paths[source["logical_id"]]


def schedule(app):
    def must_not_admit(_request):
        raise AssertionError("Completed-result consumption must not readmit or dispatch Work")
    return DailyAdmissionScheduler(app, admit_request=must_not_admit, authorize=lambda: True, limit=1)


@pytest.mark.parametrize("drift", [None, "source", "policy"])
def test_unknown_first_batch_rotates_durably_to_completed_result_and_applies_once(tmp_path, drift):
    effects = []
    app, unknown_key, complete_key, source = owner_export_fixture(tmp_path, effects)
    original = source.read_bytes()
    first = schedule(app).consume_completed()
    assert first["consumed"] == [] and first["blockers"][0]["code"] == "OUTCOME_UNKNOWN"
    assert app.journal.read()["intents"][CURSOR]["after_request_key"] == unknown_key
    assert source.read_bytes() == original and effects == []
    # Reconstruct the application/scheduler, not merely the Python object cursor.
    restored = KnowledgeApplication.from_roots(app.roots, fault_hook=effects.append)
    if drift is not None:
        target = source if drift == "source" else app.roots.control / "ops/policies/owner-control.json"
        raw = target.read_bytes()
        if drift == "source":
            target.write_bytes(raw + b"\nIntervening user edit\n")
            denied = schedule(restored).consume_completed()
            assert denied["consumed"] == [] and denied["blockers"][0]["code"] == "RESOURCE_STALE"
        else:
            policy = json.loads(raw)
            policy["operator_actions"].remove("apply")
            target.write_text(json.dumps(policy))
            with pytest.raises(AdmissionError):
                schedule(restored).consume_completed()
        observer = KnowledgeApplication.from_roots(app.roots)
        assert effects == [] and not observer.daily_stewardship().pending()
        expected_source = original + b"\nIntervening user edit\n" if drift == "source" else original
        assert source.read_bytes() == expected_source
        target.write_bytes(raw)
    results = []
    for _ in range(2):
        results.extend(schedule(restored).consume_completed()["consumed"])
    assert len(results) == 1 and results[0]["review_status"] == "pending"
    assert effects == ["daily_canonical_written"]
    provisional = source.read_bytes()
    assert digest(provisional) == results[0]["after_digest"]
    assert parse_frontmatter(provisional.decode()).properties["tags"] == ["narrative", "ai/pending"]
    again = KnowledgeApplication.from_roots(app.roots, fault_hook=effects.append)
    for _ in range(3):
        observed = schedule(again).consume_completed()
        assert observed["consumed"] == [] and observed["blockers"][0]["code"] == "OUTCOME_UNKNOWN"
    assert source.read_bytes() == provisional and effects == ["daily_canonical_written"]
    intents = again.journal.read()["intents"]
    assert intents[complete_key]["state"] == "consumed"
    assert intents[unknown_key]["state"] == "admitted"
    assert intents["exops.work.work.daily.fairness.unknown"]["dispatch_state"] == "unknown"
    assert len([key for key in intents if key.startswith("exops.work.")]) == 2
