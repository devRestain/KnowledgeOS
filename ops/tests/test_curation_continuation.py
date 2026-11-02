"""Disposable continuation: real Owner effects, synthetic native runner only."""

from __future__ import annotations

import asyncio
import copy

import pytest
import test_curation_work as ordinary

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_continuation import (
    DEFAULT_CONTINUATION_POLICY,
    METHOD_ID,
    CurationContinuationExecutor,
    _bundle,
    curation_continuation_admission,
    curation_continuation_native_digest,
)
from vaultops.application.curation_work import CRITERION
from vaultops.application.work import record_digest, segment_digest, work_spec_digest

DENIED = AdmissionError


def started(tmp_path, monkeypatch, *, continuation_policy=None, alter=None, mutate_spec=None):
    # Reuse the existing isolated fixture and production scoped OwnerToolRunner;
    # only the explicitly selected method/native registration differs.
    def admission(*args, **kwargs):
        return curation_continuation_admission(
            *args, **kwargs, continuation_policy=continuation_policy,
        )

    monkeypatch.setattr(ordinary, "curation_admission", admission)
    monkeypatch.setattr(ordinary, "curation_native_digest", curation_continuation_native_digest)
    exops, previous, spec, run, path, receipt = ordinary.started(
        tmp_path, alter=alter, mutate_spec=mutate_spec,
    )
    return exops, CurationContinuationExecutor(exops, runner=previous.runner), spec, run, path, receipt


def record(exops, run):
    return exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]


def effects(exops, run):
    return [
        item for item in exops.application.journal.read()["intents"].values()
        if item.get("work_binding", {}).get("work_run_id") == run["work_run_id"]
    ]


async def initial(executor, spec, run):
    await executor.manager(spec, run)
    await executor.capture_evidence(run["work_run_id"])
    return await executor.assess_initial(run["work_run_id"])


async def complete(executor, spec, run):
    result = await initial(executor, spec, run)
    assert result["continuation_state"] == "allowed"
    await executor.manager_successor(run["work_run_id"])
    await executor.normalization_proposal(run["work_run_id"])
    return await executor.assess(run["work_run_id"])


def test_two_immutable_graphs_independent_gap_and_latest_success(tmp_path, monkeypatch):
    exops, executor, spec, run, path, receipt = started(tmp_path, monkeypatch)
    before = path.read_bytes()
    assert spec["method_contract_ref"]["method_id"] == METHOD_ID
    assert spec["delegation_mode"] == "manager_bounded"
    assert spec["explicit_assignments"] == []
    assert spec["continuation_policy"] == DEFAULT_CONTINUATION_POLICY
    assert len(spec["allowed_profiles"]) == 2
    assert asyncio.run(initial(executor, spec, run))["continuation_state"] == "allowed"
    first = record(exops, run)
    initial_snapshot = copy.deepcopy(first["curation_initial_assessment"])
    initial_id = first["run"]["graph_run_ids"][0]
    first_segment = copy.deepcopy(first["admitted_method_segments"][initial_id])
    assert first["assessment"]["completion_state"] == "incomplete"
    assert first["assessment"]["criterion_outcomes"][0]["outcome"] == "not_run"
    assert first["evaluations"][0]["not_run_criterion_ids"] == [CRITERION["criterion_id"]]
    assert first["remaining_work"]["not_run_criterion_ids"] == [CRITERION["criterion_id"]]
    assert first["remaining_work"]["assessment_digest"] == record_digest(first["assessment"])
    assert first["run"]["repeated_gap_count"] == 1
    assert first["run"]["segments_started"] == first["run"]["profile_invocations"] == 1
    assert "active_graph_run_id" not in first["run"]
    assert first["curation_capture_evidence"]["context"]["graph_run_id"] == initial_id
    assert all(item["data"]["text"] for item in first["curation_capture_evidence"]["reads"])
    assert executor.runner.requests == [] and effects(exops, run) == []
    asyncio.run(executor.manager_successor(run["work_run_id"]))
    second = record(exops, run)
    successor_id = second["run"]["graph_run_ids"][1]
    successor = second["admitted_method_segments"][successor_id]["segment"]
    assert second["admitted_method_segments"][initial_id] == first_segment
    assert successor["predecessor_graph_run_id"] == initial_id
    assert successor["trigger_kind"] == "remaining_work"
    assert successor["trigger_reference_id"] == first["remaining_work"]["remaining_work_id"]
    assert successor["trigger_digest"] == record_digest(first["remaining_work"])
    assert successor["target_criterion_ids"] == [CRITERION["criterion_id"]]
    assert [
        item["assignments"][0]["role_id"] for item in second["admitted_method_segments"].values()
    ] == ["knowledgeos.triage", "knowledgeos.normalizer"]
    assert all(
        len(item["assignments"]) == 1 and item["assignments"][0]["selection_authority"] == "bounded_manager"
        for item in second["admitted_method_segments"].values()
    )
    asyncio.run(executor.normalization_proposal(run["work_run_id"]))
    result = asyncio.run(executor.assess(run["work_run_id"]))
    assert len(executor.runner.requests) == 1
    assert executor.runner.requests[0]["graph_run_id"] == successor_id
    assert len(effects(exops, run)) == 1
    assert path.read_bytes() == before
    assert result.next_run["state"] == "completed"
    assert result.next_run["segments_started"] == result.next_run["profile_invocations"] == 2
    assert result.assessment["graph_run_ids"] == [initial_id, successor_id]
    assert [item["run_id"] for item in result.evaluations] == [initial_id, successor_id]
    assert result.evaluations[0]["not_run_criterion_ids"] == [CRITERION["criterion_id"]]
    latest = result.evaluations[1]
    outcome = result.assessment["criterion_outcomes"][0]
    assert latest["passed_criterion_ids"] == [CRITERION["criterion_id"]]
    assert outcome["outcome"] == "satisfied"
    assert outcome["evaluation_ids"] == [latest["evaluation_id"]]
    assert len(result.segments) == len(result.checkpoints) == 2
    assert set(initial_snapshot["assessment_artifacts"]) <= set(result.artifacts)
    with pytest.raises(DENIED):
        exops.worker_finished(
            run["work_run_id"], worker_identity=exops.worker_identity, action_id=receipt["action_id"],
            next_run=result.next_run, assessment=result.assessment, segments=result.segments,
            evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
            candidate_assessor_id=spec["manager_executor_id"],
        )
    finished = exops.worker_finished(
        run["work_run_id"], worker_identity=exops.worker_identity, action_id=receipt["action_id"],
        next_run=result.next_run, assessment=result.assessment, segments=result.segments,
        evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
        candidate_assessor_id=result.candidate_assessor_id,
    )
    assert finished["application_state"] == "recorded"
    final = record(exops, run)
    assert final["curation_initial_assessment"] == initial_snapshot
    assert final["assessment"] == result.assessment
    assert final["admitted_method_segments"][initial_id] == first_segment


@pytest.mark.parametrize("budget", list(DEFAULT_CONTINUATION_POLICY))
def test_budget_denial_is_durable_waiting_without_model_or_proposal(tmp_path, monkeypatch, budget):
    policy = {**DEFAULT_CONTINUATION_POLICY, budget: 1}
    policy["max_repeated_gap_assessments"] = min(
        policy["max_repeated_gap_assessments"], policy["max_segments"],
    )
    exops, executor, spec, run, path, _receipt = started(
        tmp_path, monkeypatch, continuation_policy=policy,
    )
    before = path.read_bytes()
    result = asyncio.run(initial(executor, spec, run))
    assert set(result) == {"continuation_state", "receipt"}
    assert result["continuation_state"] == "human_required"
    saved = record(exops, run)
    assert saved["run"]["state"] == "awaiting_human"
    assert saved["application_state"] == "recorded"
    assert saved["receipt"] == result["receipt"]
    assert "active_graph_run_id" not in saved["run"]
    assert saved["assessment"]["completion_state"] == "incomplete"
    assert saved["remaining_work"]["not_run_criterion_ids"] == [CRITERION["criterion_id"]]
    for action in (executor.manager_successor, executor.normalization_proposal):
        with pytest.raises(DENIED):
            asyncio.run(action(run["work_run_id"]))
    assert record(exops, run) == saved
    assert executor.runner.requests == [] and effects(exops, run) == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("changed", ["source", "evidence", "criteria", "trigger", "predecessor", "initial_evaluation", "checkpoint"])
def test_successor_rejects_changed_admitted_inputs_before_model(tmp_path, monkeypatch, changed):
    exops, executor, spec, run, path, _receipt = started(tmp_path, monkeypatch)
    asyncio.run(initial(executor, spec, run))
    app = exops.application
    if changed == "source":
        path.write_text(path.read_text() + "\nChanged after assessment.\n")
    else:
        with app.journal.writer() as writer:
            saved = writer.value["intents"]["exops.work." + run["work_run_id"]]
            if changed == "evidence":
                evidence = saved["curation_capture_evidence"]
                evidence["reads"][0]["data"]["text"] += " forged"
                evidence["evidence_digest"] = record_digest({key: value for key, value in evidence.items() if key != "evidence_digest"})
            elif changed == "criteria":
                saved["spec"]["acceptance_criteria"][0]["description"] = "Narrative completion is enough"
                saved["spec"]["spec_digest"] = work_spec_digest(saved["spec"])
                saved["run"]["work_spec_digest"] = saved["spec"]["spec_digest"]
            elif changed == "trigger":
                saved["remaining_work"]["gaps"][0]["summary"] = "A different admitted gap"
            elif changed == "predecessor":
                predecessor = next(iter(saved["admitted_method_segments"].values()))["segment"]
                predecessor["input_references"][0]["classification"] = "public"
                predecessor["segment_digest"] = segment_digest(predecessor)
            elif changed == "initial_evaluation":
                saved["curation_initial_assessment"]["evaluations"][0]["evaluator_id"] = spec["manager_executor_id"]
            else:
                key = saved["run"]["graph_run_ids"][0]
                saved["curation_initial_assessment"]["checkpoints"][key] = "sha256:" + "f" * 64
            writer.commit(app.clock())
    with pytest.raises(DENIED):
        asyncio.run(executor.manager_successor(run["work_run_id"]))
    assert executor.runner.requests == [] and effects(exops, run) == []


@pytest.mark.parametrize("changed", ["trigger", "predecessor", "triage_context", "profile_budget", "authority"])
def test_model_boundary_rechecks_initial_lineage_and_authority(tmp_path, monkeypatch, changed):
    exops, executor, spec, run, _path, _receipt = started(tmp_path, monkeypatch)
    asyncio.run(initial(executor, spec, run))
    asyncio.run(executor.manager_successor(run["work_run_id"]))
    if changed == "authority":
        exops.authorize_actor = lambda _actor, _action: False
    else:
        app = exops.application
        with app.journal.writer() as writer:
            saved = writer.value["intents"]["exops.work." + run["work_run_id"]]
            if changed == "trigger":
                successor = list(saved["admitted_method_segments"].values())[1]["segment"]
                successor["trigger_digest"] = "sha256:" + "a" * 64
                successor["segment_digest"] = segment_digest(successor)
            elif changed == "predecessor":
                predecessor = next(iter(saved["admitted_method_segments"].values()))["segment"]
                predecessor["graph_digest"] = "sha256:" + "a" * 64
                predecessor["segment_digest"] = segment_digest(predecessor)
            elif changed == "triage_context":
                saved["curation_capture_evidence"]["context"]["graph_run_id"] = saved["run"]["active_graph_run_id"]
            else:
                saved["run"]["profile_invocations"] = 1
            writer.commit(app.clock())
    with pytest.raises(DENIED):
        asyncio.run(executor.normalization_proposal(run["work_run_id"]))
    assert executor.runner.requests == [] and effects(exops, run) == []


@pytest.mark.parametrize("changed", ["profile", "executor", "user_pin"])
def test_manager_cannot_widen_profile_envelope_or_bypass_user_pin(tmp_path, monkeypatch, changed):
    exops, _executor, spec, run, _path, _receipt = started(tmp_path, monkeypatch)
    saved = record(exops, run)
    bundle, artifacts = _bundle(exops.application, spec, run, saved["method_source_reference"], index=1)
    candidate = copy.deepcopy(spec)
    if changed == "profile":
        bundle["assignments"][0]["role_version"] = "999.0.0"
    elif changed == "executor":
        bundle["assignments"][0]["executor_id"] = "knowledgeos.unadmitted"
    else:
        candidate["explicit_assignments"] = [{
            "node_id": "node.normalization_proposal", "executor_id": spec["allowed_executor_ids"][1],
            **spec["allowed_profiles"][1], "criterion_ids": [CRITERION["criterion_id"]],
        }]
        candidate["spec_digest"] = work_spec_digest(candidate)
        run = {**run, "work_spec_digest": candidate["spec_digest"]}
        bundle, artifacts = _bundle(exops.application, candidate, run, saved["method_source_reference"], index=1)
    with pytest.raises(DENIED):
        exops.contracts.segment(
            bundle["segment"], candidate, run, graph_spec=bundle["graph_spec"], nodes=bundle["nodes"],
            edges=bundle["edges"], assignments=bundle["assignments"], artifacts=artifacts,
            current_method_ref=spec["method_contract_ref"],
        )


@pytest.mark.parametrize("policy", [
    {}, {**DEFAULT_CONTINUATION_POLICY, "max_segments": 0},
    {**DEFAULT_CONTINUATION_POLICY, "max_segments": True},
    {**DEFAULT_CONTINUATION_POLICY, "max_segments": 2147483648},
    {**DEFAULT_CONTINUATION_POLICY, "max_segments": 2.0},
    {**DEFAULT_CONTINUATION_POLICY, "extra": 1},
])
def test_exact_positive_policy_schema_is_required(tmp_path, monkeypatch, policy):
    with pytest.raises(DENIED):
        started(tmp_path, monkeypatch, continuation_policy=policy)


@pytest.mark.parametrize("stage", ["capture", "initial_assessment", "successor", "normalizer", "final_assessment"])
def test_stages_cannot_skip_their_exact_predecessor(tmp_path, monkeypatch, stage):
    exops, executor, _spec, run, _path, _receipt = started(tmp_path, monkeypatch)
    actions = {
        "capture": executor.capture_evidence, "initial_assessment": executor.assess_initial,
        "successor": executor.manager_successor, "normalizer": executor.normalization_proposal,
        "final_assessment": executor.assess,
    }
    with pytest.raises(DENIED):
        asyncio.run(actions[stage](run["work_run_id"]))
    assert executor.runner.requests == [] and effects(exops, run) == []


@pytest.mark.parametrize("changed", ["model", "tools", "trace", "receipt", "proposal", "human_gate"])
def test_latest_assessment_keeps_exact_native_and_pending_checks(tmp_path, monkeypatch, changed):
    exops, executor, spec, run, _path, _receipt = started(tmp_path, monkeypatch)
    asyncio.run(initial(executor, spec, run))
    asyncio.run(executor.manager_successor(run["work_run_id"]))
    asyncio.run(executor.normalization_proposal(run["work_run_id"]))
    app = exops.application
    if changed == "proposal":
        saved = record(exops, run)
        path = app.roots.vault / app.resolve_reference(saved["canonical_proposal_reference"], proposal=True)
        path.write_text(path.read_text() + "\nChanged Pending bytes\n")
    else:
        with app.journal.writer() as writer:
            saved = writer.value["intents"]["exops.work." + run["work_run_id"]]
            effect = next(item for item in writer.value["intents"].values() if item.get("work_binding", {}).get("work_run_id") == run["work_run_id"])
            if changed in {"model", "tools", "trace"}:
                evidence = saved["curation_model_execution"]
                response = evidence["response"]
                if changed == "model":
                    response["model"] = "unadmitted-model"
                elif changed == "tools":
                    response["exposed_tools"] = ["work_context_read", "knowledge_read", "shell"]
                else:
                    response["tool_calls"] = [item for item in response["tool_calls"] if item["name"] != "knowledge_read"]
                evidence["evidence_digest"] = record_digest({"request": evidence["request"], "response": response})
            elif changed == "receipt":
                writer.value["receipts"][effect["effect_id"]]["execution"]["result_digest"] = "sha256:" + "a" * 64
            else:
                writer.value["human_requests"][effect["human_request_id"]]["lifecycle"] = "resolved"
            writer.commit(app.clock())
    with pytest.raises(DENIED):
        asyncio.run(executor.assess(run["work_run_id"]))


@pytest.mark.parametrize("changed", ["actor", "director", "source_admission", "segments", "profiles", "latest_evaluation"])
def test_final_owner_commit_rechecks_authority_lineage_and_latest_evaluation(tmp_path, monkeypatch, changed):
    exops, executor, spec, run, _path, receipt = started(tmp_path, monkeypatch)
    result = asyncio.run(complete(executor, spec, run))
    next_run = copy.deepcopy(result.next_run)
    assessment = copy.deepcopy(result.assessment)
    if changed == "actor":
        exops.authorize_actor = lambda _actor, _action: False
    elif changed == "director":
        exops.director_admitted = lambda _spec: False
    elif changed == "source_admission":
        exops.director_method_source_admitted = lambda _spec, _source: False
    elif changed == "segments":
        next_run["graph_run_ids"] = next_run["graph_run_ids"][1:]
        next_run["segments_started"] = 1
    elif changed == "profiles":
        next_run["profile_invocations"] = 0
    else:
        outcome = assessment["criterion_outcomes"][0]
        outcome["outcome"] = "not_run"
        outcome["evaluation_ids"] = [result.evaluations[0]["evaluation_id"]]
        outcome["evidence_references"] = result.evaluations[0]["evidence_references"]
        assessment["completion_state"] = "incomplete"
        next_run["latest_assessment_digest"] = record_digest(assessment)
    with pytest.raises(DENIED):
        exops.worker_finished(
            run["work_run_id"], worker_identity=exops.worker_identity, action_id=receipt["action_id"],
            next_run=next_run, assessment=assessment, segments=result.segments,
            evaluations=result.evaluations, artifacts=result.artifacts, checkpoints=result.checkpoints,
            candidate_assessor_id=result.candidate_assessor_id,
        )
    assert record(exops, run)["application_state"] == "started"


@pytest.mark.parametrize("stage", ["capture", "initial_assessment", "successor"])
def test_graph_and_assessment_stages_cannot_repeat(tmp_path, monkeypatch, stage):
    exops, executor, spec, run, _path, _receipt = started(tmp_path, monkeypatch)
    if stage == "capture":
        asyncio.run(executor.manager(spec, run))
        asyncio.run(executor.capture_evidence(run["work_run_id"]))
        action = executor.capture_evidence
    else:
        asyncio.run(initial(executor, spec, run))
        if stage == "successor":
            asyncio.run(executor.manager_successor(run["work_run_id"]))
            action = executor.manager_successor
        else:
            action = executor.assess_initial
    saved = record(exops, run)
    with pytest.raises(DENIED):
        asyncio.run(action(run["work_run_id"]))
    assert record(exops, run) == saved
    assert executor.runner.requests == [] and effects(exops, run) == []


@pytest.mark.parametrize("changed", ["runner", "binding", "source"])
def test_admission_requires_exact_source_runner_and_native_binding(tmp_path, monkeypatch, changed):
    from dataclasses import replace

    from test_normalization_work import MODEL_BINDING

    from vaultops.adapters.core import canonical, digest

    exops, _executor, spec, run, _path, _receipt = started(tmp_path, monkeypatch)
    app = exops.application
    binding = exops.teams[spec["team_id"]]
    source = record(exops, run)["method_source_reference"]
    runner_binding = dict(MODEL_BINDING)
    if changed == "runner":
        runner_binding["model"] = "unselected-model"
    elif changed == "source":
        source = {**source, "content_digest": "sha256:" + "a" * 64}
    else:
        binding = replace(binding, native_binding_digest="sha256:" + "a" * 64)
        raw = canonical(binding.content())
        binding = replace(binding, binding_reference={
            **binding.binding_reference, "content_digest": digest(raw),
            "source_revision": digest(raw)[7:], "size_bytes": len(raw),
        })
    with pytest.raises(DENIED):
        curation_continuation_admission(
            app, binding, source, work_id=spec["work_id"], work_run_id=run["work_run_id"],
            admission_receipt_id=spec["admission_receipt_id"], runner_binding=runner_binding,
        )


@pytest.mark.parametrize("changed", ["criteria", "pin", "profile"])
def test_director_admission_cannot_change_the_selected_continuation_semantics(tmp_path, monkeypatch, changed):
    def mutate(spec):
        if changed == "criteria":
            spec["acceptance_criteria"][0]["description"] = "Manager says complete"
        elif changed == "profile":
            spec["allowed_profiles"].reverse()
        else:
            spec["explicit_assignments"] = [{
                "node_id": "node.capture_evidence", "executor_id": spec["allowed_executor_ids"][0],
                **spec["allowed_profiles"][0], "criterion_ids": [CRITERION["criterion_id"]],
            }]
    exops, executor, spec, run, _path, _receipt = started(
        tmp_path, monkeypatch, mutate_spec=mutate,
    )
    with pytest.raises(DENIED):
        asyncio.run(executor.manager(spec, run))
    assert not record(exops, run).get("admitted_method_segments")
    assert executor.runner.requests == [] and effects(exops, run) == []
