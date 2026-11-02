"""Completed-result fixtures retain the real Director's immutable private grant.

These tests exercise actual daily admission and owner effect boundaries without
calling a model or Worker. Completed artifact/assessment records are explicit
fixtures for the consumption boundary, not native execution evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

import pytest
from support.daily_scheduler_smoke import fixture, scheduler
from support.metadata_fixture import SOURCE

from vaultops.adapters.core import AdmissionError
from vaultops.adapters.owner_journal import stamp
from vaultops.application.metadata_work import DAILY_METHOD_ID, validate_candidate
from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime
from vaultops.application.work import record_digest
from vaultops.note_engine import render_frontmatter
from vaultops.projection import generate_projection

ROUTE = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
         "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
         "provider": "openai-codex", "model": "gpt-6.1-terra", "adapter_digest": "sha256:" + "1" * 64}


class NoModel:
    binding = ROUTE

    async def __call__(self, _request):
        raise AssertionError("Retained-authority regression must not call a model")


def completed_daily_fixture(tmp_path, kind):
    runner = NoModel()
    runtime, canary, policy_path = fixture(tmp_path, ROUTE, runner)
    app = runtime.application
    admitted = scheduler(runtime).tick()
    assert admitted["admitted"] == 1 and not admitted["blockers"], admitted
    receipt = admitted["work_receipts"][0]
    run_id = receipt["work_run_id"]
    selected = app.journal.read()["intents"]["exops.work." + run_id]
    source = selected["method_source_reference"]
    candidate = {"source_reference": source, "registry_digest": app.daily_stewardship()._registry_digest(),
                 "fields": {"tags": [canary]} if kind == "candidate" else {},
                 "evidence": {"tags": {canary: ["tags: " + canary]}} if kind == "candidate" else {}}
    # The candidate itself passes the production deterministic method check;
    # the changed private grant is the only later refusal condition.
    assert validate_candidate(app, source, candidate, method_id=DAILY_METHOD_ID) == candidate
    proposal_id = str(uuid4())
    relative = "01_AI_Review/Pending/daily-authority-fixture.md"
    properties = {"schema_version": 1, "id": "proposal-" + proposal_id, "type": "proposal",
                  "title": "daily-authority-fixture", "status": "pending", "created": stamp(app.clock()),
                  "modified": stamp(app.clock()), "aliases": [], "tags": ["ai/review"],
                  "sensitivity": "personal", "ai_policy": "ask", "ai_status": "proposed",
                  "proposal_id": proposal_id, "source_hashes": [SOURCE + "|" + source["content_digest"]]}
    payload = {"artifact_kind": "daily_enrichment", "content": json.dumps(candidate, ensure_ascii=False)}
    path = app.roots.vault / relative
    path.write_text(render_frontmatter(properties, "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"))
    reference = app.reference(relative, proposal=True)
    artifact = {"owner_operation_id": "knowledgeos", "artifact_id": "artifact.daily.authority.fixture",
                "source_revision": reference["source_revision"], "content_digest": reference["content_digest"],
                "media_type": "text/markdown", "size_bytes": len(path.read_bytes()), "classification": "internal",
                "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.mcp.artifact_read"}
    app.core.validate("ArtifactReference", artifact)
    spec, run = selected["spec"], selected["run"]
    assessment = {"owner_operation_id": "knowledgeos", "work_id": run["work_id"], "work_run_id": run_id,
                  "work_spec_revision": spec["work_spec_revision"], "work_spec_digest": spec["spec_digest"],
                  "assessment_id": "assessment.daily.authority.fixture", "criteria_revision": spec["criteria_revision"],
                  "assessor_id": spec["eval_officer_executor_id"], "assessed_at": stamp(app.clock()),
                  "graph_run_ids": [], "completion_state": "complete", "criterion_outcomes": [
                      {"criterion_id": spec["acceptance_criteria"][0]["criterion_id"], "outcome": "satisfied",
                       "evaluation_ids": [], "evidence_references": [artifact]}]}
    with app.journal.writer() as writer:
        record = writer.value["intents"]["exops.work." + run_id]
        record.update(state="completed", dispatch_state="accepted", application_state="recorded",
                      assessment=assessment, artifact_references={artifact["artifact_id"]: artifact},
                      submitted_artifacts=[reference])
        record["run"].update(state="completed", result_reference=artifact,
                             latest_assessment_id=assessment["assessment_id"],
                             latest_assessment_digest=record_digest(assessment))
        writer.commit(app.clock())
    # No source edit or owner provisional/no-evidence effect has occurred.
    app.daily_stewardship()._work_artifact(run_id, candidate, source)
    report, code = generate_projection(app.roots)
    assert code == 0, report
    request_key = next(key for key in app.journal.read()["intents"] if key.startswith("daily.admission."))
    return runtime, runner, policy_path, run_id, request_key, candidate


def exclude_source(path):
    document = json.loads(path.read_text())
    document["methods"][DAILY_METHOD_ID]["admission_scope"]["source_paths"] = ["40_Knowledge/Notes/Other.md"]
    path.write_text(json.dumps(document))


def assert_no_daily_effect(app, run_id, source_before, coverage_before):
    intents = app.journal.read()["intents"]
    coverage_key = "daily.enrichment." + hashlib.sha256(SOURCE.encode()).hexdigest()
    assert (app.roots.vault / SOURCE).read_bytes() == source_before
    assert intents[coverage_key] == coverage_before
    assert not any(item.get("work_run_id") == run_id for key, item in intents.items()
                   if key.startswith("daily.enrichment."))
    assert app.daily_stewardship().pending() == []


@pytest.mark.parametrize("kind", ["candidate", "no_evidence"])
def test_reconstructed_source_exclusion_blocks_completed_daily_before_consumption_marker(tmp_path, kind):
    runtime, runner, policy_path, run_id, request_key, candidate = completed_daily_fixture(tmp_path, kind)
    app = runtime.application
    original_policy = policy_path.read_bytes()
    source_before = (app.roots.vault / SOURCE).read_bytes()
    coverage_key = "daily.enrichment." + hashlib.sha256(SOURCE.encode()).hexdigest()
    coverage_before = copy.deepcopy(app.journal.read()["intents"][coverage_key])
    exclude_source(policy_path)
    restored = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(policy_path), runners={DAILY_METHOD_ID: runner})
    # The reconstructed composition considers its NEW registration current;
    # consumption must still enforce the exact retained Work's original grant.
    schedule = scheduler(restored)
    assert schedule.authorize() is True
    denied = schedule.consume_completed()
    assert denied["consumed"] == [] and any(item["code"] == "ADMISSION_REQUIRED" for item in denied["blockers"]), denied
    assert restored.application.journal.read()["intents"][request_key]["state"] == "admitted"
    assert_no_daily_effect(restored.application, run_id, source_before, coverage_before)
    policy_path.write_bytes(original_policy)
    allowed = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(policy_path), runners={DAILY_METHOD_ID: runner})
    consumed = scheduler(allowed).consume_completed()
    assert len(consumed["consumed"]) == 1 and not consumed["blockers"], consumed
    assert allowed.application.journal.read()["intents"][request_key]["state"] == "consumed"
    if kind == "candidate":
        assert consumed["consumed"][0]["review_status"] == "pending"
    else:
        assert consumed["consumed"][0]["status"] == "no_evidence"
        assert (app.roots.vault / SOURCE).read_bytes() == source_before
    assert candidate["source_reference"] == allowed.application.journal.read()["intents"]["exops.work." + run_id]["method_source_reference"]


@pytest.mark.parametrize("kind", ["candidate", "no_evidence"])
def test_retained_work_source_scope_rechecked_under_final_daily_writer(tmp_path, kind):
    runtime, _runner, policy_path, run_id, _request_key, candidate = completed_daily_fixture(tmp_path, kind)
    app = runtime.application
    source_before = (app.roots.vault / SOURCE).read_bytes()
    coverage_key = "daily.enrichment." + hashlib.sha256(SOURCE.encode()).hexdigest()
    coverage_before = copy.deepcopy(app.journal.read()["intents"][coverage_key])
    calls = []
    def revoke_at_writer():
        calls.append(True)
        exclude_source(policy_path)
        return True  # A caller callback cannot replace retained Work authority.
    steward = app.daily_stewardship()
    with pytest.raises(AdmissionError) as denied:
        if kind == "candidate":
            steward.record_candidate(run_id, candidate, authorize=revoke_at_writer)
        else:
            steward.record_no_evidence(run_id, candidate, authorize=revoke_at_writer)
    assert denied.value.code == "ADMISSION_REQUIRED"
    assert calls == [True]
    assert_no_daily_effect(app, run_id, source_before, coverage_before)


def test_unexpected_authorization_bug_propagates_without_scheduler_writes(tmp_path):
    runtime, _canary, _policy_path = fixture(tmp_path, ROUTE, NoModel())
    schedule = scheduler(runtime)
    before = runtime.application.journal.path.read_bytes()
    def broken_authorization():
        raise TypeError("unexpected authorization programming error")
    schedule.authorize = broken_authorization
    with pytest.raises(TypeError, match="unexpected authorization programming error"):
        schedule.tick()
    assert runtime.application.journal.path.read_bytes() == before


def test_unexpected_consumption_bug_propagates_before_provisional_effect(tmp_path, monkeypatch):
    runtime, _runner, _policy_path, run_id, request_key, _candidate = completed_daily_fixture(tmp_path, "candidate")
    app = runtime.application
    before = (app.roots.vault / SOURCE).read_bytes()
    coverage_key = "daily.enrichment." + hashlib.sha256(SOURCE.encode()).hexdigest()
    coverage = copy.deepcopy(app.journal.read()["intents"][coverage_key])
    schedule = scheduler(runtime)
    def broken_candidate(*_args):
        raise TypeError("unexpected candidate programming error")
    monkeypatch.setattr(schedule, "_candidate", broken_candidate)
    with pytest.raises(TypeError, match="unexpected candidate programming error"):
        schedule.consume_completed()
    assert app.journal.read()["intents"][request_key]["state"] == "admitted"
    assert_no_daily_effect(app, run_id, before, coverage)


def test_unexpected_admission_bug_propagates_but_retains_unknown_no_retry_fence(tmp_path):
    runtime, _canary, _policy_path = fixture(tmp_path, ROUTE, NoModel())
    schedule = scheduler(runtime)
    calls = []
    def committed_then_bug(request):
        receipt = runtime.admit_daily_request(request)
        calls.append(receipt["work_run_id"])
        raise TypeError("unexpected programming error after committed admission")
    schedule.admit_request = committed_then_bug
    with pytest.raises(TypeError, match="unexpected programming error after committed admission"):
        schedule.tick()
    intents = runtime.application.journal.read()["intents"]
    requests = [item for key, item in intents.items() if key.startswith("daily.admission.")]
    assert len(requests) == 1 and requests[0]["state"] == "unknown"
    assert len(calls) == len([key for key in intents if key.startswith("exops.work.")]) == 1
    observed = scheduler(runtime).tick()
    assert observed["admitted"] == 0
    assert any(item["code"] == "OUTCOME_UNKNOWN" for item in observed["blockers"])
    assert len([key for key in runtime.application.journal.read()["intents"] if key.startswith("exops.work.")]) == 1
