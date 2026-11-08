"""Daily consumption retains the admitted Director grant and semantic assessment.

Synthetic role judgments drive real Owner admission, scoped tool effects and
WorkResult validation in disposable roots. No provider or Temporal call is made.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from uuid import uuid4

import pytest
from support.agent_fixture import model_binding
from support.daily_scheduler_smoke import fixture, scheduler
from support.metadata_fixture import SOURCE, MetadataToolRunner

from vaultops.adapters.core import AdmissionError
from vaultops.application.metadata_work import DAILY_METHOD_ID, validate_candidate
from vaultops.application.owner_runtime import (
    DispatchSupervisor,
    OwnerRuntimePolicy,
    WritableOwnerRuntime,
)
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.interfaces.loopback_owner import LoopbackIngress
from vaultops.note_engine import parse_frontmatter, render_frontmatter
from vaultops.projection import generate_projection

ROUTE = model_binding()


def completed_daily_fixture(tmp_path, kind, *, semantic_outcome="satisfied", manual=False):
    runner = MetadataToolRunner(binding=ROUTE, semantic_outcome=semantic_outcome)
    runtime, canary, policy_path = fixture(tmp_path, ROUTE, runner)
    app = runtime.application
    runner.application, runner.exops = app, runtime.exops
    if manual:
        source = app.reference(SOURCE)
        receipt = runtime.admit_selected_request(runtime.actor, {
            "method_id": DAILY_METHOD_ID, "request_id": str(uuid4()), "parameters": {},
            "scope": {"path": SOURCE, "content_sha256": source["source_revision"]}})
        assert not any(key.startswith("daily.scan.") for key in app.journal.read()["intents"])
    else:
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
    runner.candidate = copy.deepcopy(candidate)
    executor = runtime.executors[DAILY_METHOD_ID]
    original = (app.roots.vault / SOURCE).read_bytes()
    started = runtime.exops.worker_started(
        run_id, worker_identity=runtime.exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        work_spec_digest=selected["spec"]["spec_digest"], workflow_id="knowledgeos/" + run_id)

    async def produce():
        await executor.manager(selected["spec"], started)
        for stage in executor.policy.stages:
            await executor.execute_stage(run_id, stage)
        return await executor.assess(run_id)

    result = asyncio.run(produce())
    finished = runtime.exops.worker_finished(
        run_id, worker_identity=runtime.exops.worker_identity,
        action_id=receipt["action_id"], next_run=result.next_run,
        assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts,
        checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id)
    assert finished["application_state"] == "recorded"
    assert (app.roots.vault / SOURCE).read_bytes() == original
    assert app.daily_stewardship().pending() == []
    if semantic_outcome == "satisfied":
        app.daily_stewardship()._work_artifact(run_id, candidate, source)
    report, code = generate_projection(app.roots)
    assert code == 0, report
    request_key = next(key for key in app.journal.read()["intents"] if key.startswith("daily.admission."))
    return runtime, runner, policy_path, run_id, request_key, candidate


@pytest.mark.parametrize("kind", ["candidate", "no_evidence"])
@pytest.mark.parametrize("outcome", ["unmet", "not_run"])
def test_semantic_gap_cannot_be_consumed_as_completed_daily_work(tmp_path, kind, outcome):
    runtime, runner, _policy_path, run_id, _request_key, candidate = completed_daily_fixture(
        tmp_path, kind, semantic_outcome=outcome)
    app = runtime.application
    before = app.journal.read()
    record = before["intents"]["exops.work." + run_id]
    assert record["run"]["state"] == "awaiting_human"
    assert record["assessment"]["completion_state"] == "incomplete"
    assert {item["outcome"] for item in record["assessment"]["criterion_outcomes"]} == {outcome}
    assert record["remaining_work"] and record["continuation_state"] == "human_required"
    assert [item["agent_request"]["responsibility"] for item in runner.decisions] == [
        "director", "manager", "evaluator"]
    original = (app.roots.vault / SOURCE).read_bytes()
    coverage_key = "daily.enrichment." + hashlib.sha256(SOURCE.encode()).hexdigest()
    consume = (app.daily_stewardship().record_candidate if kind == "candidate"
               else app.daily_stewardship().record_no_evidence)
    with pytest.raises(AdmissionError) as denied:
        consume(run_id, candidate)
    assert denied.value.code == "SCOPE_DENIED"
    assert app.journal.read() == before
    assert_no_daily_effect(app, run_id, original, before["intents"][coverage_key])


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
    runtime, _canary, _policy_path = fixture(tmp_path, ROUTE, MetadataToolRunner(binding=ROUTE))
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
    runtime, _canary, _policy_path = fixture(tmp_path, ROUTE, MetadataToolRunner(binding=ROUTE))
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


def daily_call(runtime, route, payload):
    backend = ExOpsWebBackend(runtime.exops, runtime.actor)
    return backend(LoopbackIngress("knowledgeos", "POST", route, json.dumps(payload).encode()))


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_manual_daily_completion_reaches_review_and_retains_historical_result(tmp_path, decision):
    runtime, _runner, _path, run_id, request_key, candidate = completed_daily_fixture(
        tmp_path, "candidate", manual=True)
    app = runtime.application
    original = (app.roots.vault / SOURCE).read_bytes()
    before = daily_call(runtime, "work/result", {"work_run_id": run_id})
    assert before["report"] == candidate and before["state"] == "completed"
    consumed = asyncio.run(DispatchSupervisor(runtime, {}).tick())
    assert not any("daily_blocker" in item for item in consumed["outcomes"]), consumed
    assert app.journal.read()["intents"][request_key]["state"] == "consumed"
    pending = daily_call(runtime, "daily/list", {})["pending"]
    assert len(pending) == 1
    detail = daily_call(runtime, "daily/read", {"effect_id": pending[0]["effect_id"]})
    assert detail["added_fields"] == candidate["fields"] and detail["evidence"] == candidate["evidence"]
    after = daily_call(runtime, "work/result", {"work_run_id": run_id})
    assert after["text"] == before["text"] and after["report"] == candidate
    assert after["historical"] is True and after["current_evidence_valid"] is False
    assert after["apply_allowed"] is False
    provisional = (app.roots.vault / SOURCE).read_bytes()
    assert provisional != original
    assert asyncio.run(DispatchSupervisor(runtime, {}).tick()) == {"outcomes": []}
    assert (app.roots.vault / SOURCE).read_bytes() == provisional
    reviewed = daily_call(runtime, "daily/decide", {"effect_id": pending[0]["effect_id"], "decision": decision})
    assert reviewed["decision"] == decision and not daily_call(runtime, "daily/list", {})["pending"]
    properties = parse_frontmatter((app.roots.vault / SOURCE).read_text()).properties
    assert properties["tags"] == (candidate["fields"]["tags"] if decision == "confirm" else [])
    assert daily_call(runtime, "work/result", {"work_run_id": run_id})["text"] == before["text"]
    assert len([key for key in app.journal.read()["intents"] if key.startswith("exops.work.")]) == 1


def test_manual_empty_candidate_covers_only_selected_revision_without_review(tmp_path):
    runtime, _runner, _path, _run_id, request_key, _candidate = completed_daily_fixture(
        tmp_path, "no_evidence", manual=True)
    app = runtime.application
    original = (app.roots.vault / SOURCE).read_bytes()
    consumed = asyncio.run(DispatchSupervisor(runtime, {}).tick())
    assert [item["daily_consumption"]["status"] for item in consumed["outcomes"]] == ["no_evidence"]
    assert app.journal.read()["intents"][request_key]["state"] == "consumed"
    assert app.daily_stewardship().pending() == [] and (app.roots.vault / SOURCE).read_bytes() == original
    with pytest.raises(AdmissionError, match="already covered"):
        runtime.admit_selected_request(runtime.actor, {
            "method_id": DAILY_METHOD_ID, "request_id": str(uuid4()), "parameters": {},
            "scope": {"path": SOURCE, "content_sha256": app.reference(SOURCE)["source_revision"]}})


@pytest.mark.parametrize("boundary", ["disabled", "unknown_work", "interrupted", "populated"])
def test_manual_daily_consumption_holds_revoked_unknown_and_changed_sources(tmp_path, boundary):
    runtime, _runner, policy_path, run_id, request_key, _candidate = completed_daily_fixture(
        tmp_path, "candidate", manual=True)
    app = runtime.application
    coverage_key = "daily.enrichment." + hashlib.sha256(SOURCE.encode()).hexdigest()
    coverage = copy.deepcopy(app.journal.read()["intents"][coverage_key])
    if boundary == "disabled":
        document = json.loads(policy_path.read_text())
        document["daily_enabled"] = False
        policy_path.write_text(json.dumps(document))
    elif boundary == "populated":
        path = app.roots.vault / SOURCE
        document = parse_frontmatter(path.read_text())
        properties = {**document.properties, "tags": ["human-value"]}
        path.write_text(render_frontmatter(properties, document.body))
    else:
        with app.journal.writer() as writer:
            if boundary == "unknown_work":
                writer.value["intents"]["exops.work." + run_id]["dispatch_state"] = "unknown"
            else:
                writer.value["intents"][request_key]["state"] = "consuming"
            writer.commit(app.clock())
    if boundary == "interrupted":
        request = app.journal.read()["intents"][request_key]["manual_request"]
        receipt = runtime.admit_selected_request(runtime.actor, request)
        assert receipt["replayed"] is True
        assert app.journal.read()["intents"][request_key]["state"] == "consuming"
    original = (app.roots.vault / SOURCE).read_bytes()
    for _ in range(2):
        observed = asyncio.run(DispatchSupervisor(runtime, {}).tick())
        assert any("daily_blocker" in item for item in observed["outcomes"]), observed
        assert_no_daily_effect(app, run_id, original, coverage)


@pytest.mark.parametrize("outcome", ["unmet", "not_run"])
def test_manual_semantic_gap_never_consumes_or_exports_success(tmp_path, outcome):
    runtime, _runner, _path, run_id, request_key, _candidate = completed_daily_fixture(
        tmp_path, "candidate", semantic_outcome=outcome, manual=True)
    assert asyncio.run(DispatchSupervisor(runtime, {}).tick()) == {"outcomes": []}
    app = runtime.application
    assert app.journal.read()["intents"][request_key]["state"] == "admitted"
    assert app.daily_stewardship().pending() == []
    result = daily_call(runtime, "work/result", {"work_run_id": run_id})
    assert result["state"] == "awaiting_human" and result["text"] is None
    assert result["assessment"]["completion_state"] == "incomplete"


def test_manual_lost_admission_reply_recovers_only_exact_committed_work(tmp_path):
    runtime, runner, policy_path, _run_id, request_key, _candidate = completed_daily_fixture(
        tmp_path, "candidate", manual=True)
    app = runtime.application
    with app.journal.writer() as writer:
        record = writer.value["intents"][request_key]
        record["state"] = "prepared"
        record.pop("receipt")
        writer.commit(app.clock())
    restored = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(policy_path), runners={DAILY_METHOD_ID: runner})
    observed = asyncio.run(DispatchSupervisor(restored, {}).tick())
    assert len([item for item in observed["outcomes"] if "daily_consumption" in item]) == 1
    assert restored.application.journal.read()["intents"][request_key]["state"] == "consumed"
    assert len(restored.application.daily_stewardship().pending()) == 1


def test_manual_admission_does_not_scan_unrelated_notes(tmp_path, monkeypatch):
    runtime, _canary, _policy_path = fixture(tmp_path, ROUTE, MetadataToolRunner(binding=ROUTE))
    app = runtime.application
    source = app.reference(SOURCE)
    from vaultops.application.daily_stewardship import DailyStewardship
    def forbidden_scan(_self):
        raise AssertionError("Manual source preparation must not scan the Vault")
    monkeypatch.setattr(DailyStewardship, "_notes", forbidden_scan)
    monkeypatch.setattr(DailyStewardship, "_held_paths", forbidden_scan)
    receipt = runtime.admit_selected_request(runtime.actor, {
        "method_id": DAILY_METHOD_ID, "request_id": str(uuid4()), "parameters": {},
        "scope": {"path": SOURCE, "content_sha256": source["source_revision"]}})
    assert receipt["receipt_state"] == "accepted"
    coverage = [item for key, item in app.journal.read()["intents"].items()
                if key.startswith("daily.enrichment.")]
    assert len(coverage) == 1 and coverage[0]["path"] == SOURCE and coverage[0]["empty_fields"]
