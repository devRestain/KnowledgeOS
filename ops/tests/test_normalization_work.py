"""Real disposable Officer effects and independently checked owner outcomes."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.normalization_work import (
    CRITERION,
    NormalizationOfficerExecutor,
    evaluate_normalization,
    normalization_admission,
    normalization_native_digest,
)
from vaultops.application.team_catalog import OfficerBinding, TeamCatalog
from vaultops.application.work import record_digest, work_spec_digest
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.projection import generate_projection

SOURCE = "00_Inbox/Captures/2026/09/Deterministic Officer capture.md"
BASE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
ACTOR = OwnerActor("owner.user", "cli")


def _started(tmp_path, *, mutate_spec=None, runner_binding=None, source_policy=None,
             source_sensitivity=None):
    roots = make_separate_portable_fixture_roots(
        tmp_path, extra_inputs=("ops/config/team-catalog.json",), review_queues=True)
    app = KnowledgeApplication.from_roots(roots)
    source_path = roots.vault / SOURCE
    source_path.write_text((roots.vault / BASE).read_text()
        .replace("11111111-1111-4111-8111-111111111111", "14b927ae-76b0-4c25-989f-299e680c5cdf")
        .replace("20260909-090000-mac-deadbeef", "Deterministic Officer capture"))
    if source_policy is not None:
        source_path.write_text(source_path.read_text().replace("ai_policy: ask", "ai_policy: " + source_policy))
    if source_sensitivity is not None:
        source_path.write_text(source_path.read_text().replace(
            "sensitivity: personal", "sensitivity: " + source_sensitivity))
    projection, code = generate_projection(roots)
    assert code == 0, projection
    source = app.reference(SOURCE)
    catalog = TeamCatalog.load(roots.control)
    candidate = OfficerBinding(
        officer_executor_id="knowledgeos.normalization_officer.one",
        officer_profile=catalog.profile("knowledgeos.normalization_officer"),
        eval_officer_executor_id="knowledgeos.eval_officer.one",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        catalog_digest=catalog.digest, native_binding_digest=normalization_native_digest(runner_binding),
        binding_reference={})
    content = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.normalization.binding",
        "source_revision": digest(content)[7:], "content_digest": digest(content),
        "media_type": "application/json", "size_bytes": len(content),
        "classification": "internal", "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.binding_read"})
    spec, run = normalization_admission(app, binding, source, work_id="work.normalization.test",
        work_run_id="workrun.normalization.test", admission_receipt_id="admission.normalization.test",
        runner_binding=runner_binding)
    if mutate_spec:
        mutate_spec(spec)
        spec["spec_digest"] = work_spec_digest(spec)
        run["work_spec_digest"] = spec["spec_digest"]
    admitted_spec, admitted_source = copy.deepcopy(spec), dict(source)
    exops = KnowledgeExOps(app,
        authorize_actor=lambda actor, action: actor.actor_id == ACTOR.actor_id
            and action in {"submit_work", "read_work"},
        director_admitted=lambda selected: dict(selected) == admitted_spec,
        current_team_binding=lambda _team_id: None,
        worker_identity="knowledgeos.worker.one",
        current_officer_binding=lambda executor_id: binding if executor_id == binding.officer_executor_id else None,
        officers={binding.officer_executor_id: binding},
        director_officer_source_admitted=lambda selected, selected_source:
            dict(selected) == admitted_spec and dict(selected_source) == admitted_source)
    receipt = exops.submit_work(spec, run, actor=ACTOR, action_id="action.normalization.test",
        expires_at=datetime.now(UTC) + timedelta(hours=1), officer_source_reference=source)
    exops.record_dispatch(run["work_run_id"], action_id=receipt["action_id"], outcome="accepted",
        workflow_id="knowledgeos/" + run["work_run_id"])
    running = exops.worker_started(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        work_spec_digest=spec["spec_digest"], workflow_id="knowledgeos/" + run["work_run_id"])
    return exops, spec, running, source_path, receipt


def _execute(exops, spec, run, *, runner=None):
    return asyncio.run(NormalizationOfficerExecutor(exops, runner=runner)(spec, run))


def _finish(exops, run, receipt, result, **overrides):
    return exops.worker_finished(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], next_run=result.next_run, assessment=result.assessment,
        segments=result.segments, evaluations=result.evaluations, artifacts=result.artifacts,
        checkpoints=result.checkpoints,
        candidate_assessor_id=overrides.get("candidate_assessor_id", result.candidate_assessor_id))


def test_executor_creates_pending_and_exports_independently_checked_exact_owner_result(tmp_path):
    exops, spec, run, source_path, receipt = _started(tmp_path)
    source_bytes = source_path.read_bytes()
    result = _execute(exops, spec, run)
    assert result.next_run["state"] == "completed"
    assert result.candidate_assessor_id != spec["officer_executor_id"]
    assert result.evaluations[0]["passed_criterion_ids"] == [CRITERION["criterion_id"]]
    assert result.assessment["criterion_outcomes"][0]["outcome"] == "satisfied"
    graph_run_id = result.segments[0]["graph_run_id"]
    proposals = list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(proposals) == 1
    proposal_bytes = proposals[0].read_bytes()
    reference = result.next_run["result_reference"]
    assert reference["content_digest"] == digest(proposal_bytes)
    assert reference["size_bytes"] == len(proposal_bytes)
    assert result.checkpoints == {graph_run_id: record_digest({
        "graph_run_id": graph_run_id, "source_digest": digest(source_bytes),
        "proposal_digest": digest(proposal_bytes)})}
    journal = exops.application.journal.read()
    effects = [item for item in journal["intents"].values() if item.get("work_binding")]
    assert len(effects) == 1
    assert effects[0]["work_binding"] == {
        "work_run_id": run["work_run_id"], "work_spec_digest": spec["spec_digest"],
        "graph_run_id": graph_run_id, "executor_id": spec["officer_executor_id"],
        "source_reference": exops.application.reference(SOURCE)}
    assert journal["human_requests"][effects[0]["human_request_id"]]["lifecycle"] == "pending"
    assert not journal["decisions"]
    with pytest.raises(AdmissionError, match="independent EvalOfficer"):
        _finish(exops, run, receipt, result, candidate_assessor_id=spec["officer_executor_id"])
    assert _finish(exops, run, receipt, result)["application_state"] == "recorded"
    backend = ExOpsWebBackend(exops,
        lambda login: OwnerActor(ACTOR.actor_id, "web") if login == "owner" else None,
        lambda _actor, token: token == "csrf")
    exported = backend(SimpleNamespace(owner_operation_id="knowledgeos", actor_login="owner",
        method="POST", resource_path="work/result", csrf_token="csrf",
        body=json.dumps({"work_run_id": run["work_run_id"]}).encode()))
    assert exported["state"] == "completed" and exported["text"].encode() == proposal_bytes
    assert exported["assessment"] == result.assessment
    assert source_path.read_bytes() == source_bytes


@pytest.mark.parametrize("criteria", [
    [{"criterion_id": "criterion.other", "description": CRITERION["description"]}],
    [{"criterion_id": CRITERION["criterion_id"], "description": "Apply this proposal automatically"}],
])
def test_executor_rejects_different_admitted_criterion_semantics(tmp_path, criteria):
    exops, spec, run, _source, _receipt = _started(tmp_path,
        mutate_spec=lambda selected: selected.update(acceptance_criteria=criteria))
    with pytest.raises(AdmissionError, match="criterion semantics"):
        _execute(exops, spec, run)
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_executor_rejects_unadmitted_source_and_input_snapshot(tmp_path):
    exops, spec, run, source, _receipt = _started(tmp_path)
    altered = copy.deepcopy(spec)
    altered["objective"] = "Another task"
    with pytest.raises(AdmissionError, match="Executor input"):
        _execute(exops, altered, run)
    exops.director_officer_source_admitted = lambda _spec, _source: False
    with pytest.raises(AdmissionError, match="source admission"):
        _execute(exops, spec, run)
    exops.director_officer_source_admitted = lambda _spec, _source: True
    source.write_bytes(source.read_bytes() + b"\nChanged source.\n")
    with pytest.raises(AdmissionError, match="digest or revision changed"):
        _execute(exops, spec, run)
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_independent_evaluator_denies_self_certification_and_tampered_pending(tmp_path):
    exops, spec, run, _source, _receipt = _started(tmp_path)
    _execute(exops, spec, run)
    with pytest.raises(AdmissionError, match="independent EvalOfficer"):
        evaluate_normalization(exops, run["work_run_id"], evaluator_id=spec["officer_executor_id"])
    pending = next((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    pending.write_bytes(pending.read_bytes() + b"\nTampered proposal.\n")
    with pytest.raises(AdmissionError, match="digest or revision changed"):
        evaluate_normalization(exops, run["work_run_id"], evaluator_id=spec["eval_officer_executor_id"])


def test_independent_evaluator_ignores_producer_pass_and_reinspects(tmp_path):
    exops, spec, run, _source, _receipt = _started(tmp_path)
    _execute(exops, spec, run)
    app = exops.application
    # The proposal producer's successful receipt still exists. An actual second
    # inspector failure must prevent evaluation rather than copying that PASS.
    exops.application = replace(app, handlers=replace(app.handlers,
        inspect=lambda *_args, **_kwargs: ({"status": "FAIL", "count": 0, "proposals": []}, 1)))
    with pytest.raises(AdmissionError, match="Independent Pending proposal inspection failed"):
        evaluate_normalization(exops, run["work_run_id"], evaluator_id=spec["eval_officer_executor_id"])


def test_executor_does_not_complete_no_change(tmp_path):
    exops, spec, run, _source, _receipt = _started(tmp_path)
    app = exops.application
    exops.application = replace(app, handlers=replace(app.handlers,
        prepare=lambda *_args: {"status": "NO_CHANGE"}))
    with pytest.raises(AdmissionError, match="no-change is not completion"):
        _execute(exops, spec, run)
    current = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert current["run"]["state"] == "running" and "assessment" not in current
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


@pytest.mark.parametrize("changed", ["receipt", "human_gate", "source"])
def test_independent_evaluator_rechecks_current_receipt_gate_and_source(tmp_path, changed):
    exops, spec, run, source_path, _receipt = _started(tmp_path)
    _execute(exops, spec, run)
    app = exops.application
    if changed == "source":
        source_path.write_bytes(source_path.read_bytes() + b"\nChanged after producer PASS.\n")
    else:
        with app.journal.writer() as writer:
            effect = next(item for item in writer.value["intents"].values()
                          if item.get("work_binding"))
            if changed == "receipt":
                writer.value["receipts"][effect["effect_id"]]["execution"]["result_digest"] = "sha256:" + "0" * 64
            else:
                writer.value["human_requests"][effect["human_request_id"]]["lifecycle"] = "resolved"
            writer.commit(app.clock())
    with pytest.raises(AdmissionError):
        evaluate_normalization(exops, run["work_run_id"], evaluator_id=spec["eval_officer_executor_id"])


MODEL_BINDING = {
    "runtime_kind": "hermes_native", "hermes_version": "0.22.5",
    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
    "provider": "openai-codex", "model": "gpt-6.1-terra",
    "adapter_digest": digest(b"synthetic focused-test runner; not a real Hermes execution"),
}


class _OwnerToolRunner:
    """Synthetic producer using real owner tools/effects, never a model claim."""

    def __init__(self, exops, *, alter=None, bound_effect=True):
        self.binding = dict(MODEL_BINDING)
        self.exops = exops
        self.alter = alter
        self.bound_effect = bound_effect
        self.requests = []

    async def __call__(self, request):
        from vaultops.application.work_tools import OfficerToolSession
        from vaultops.interfaces.mcp_server import _convert_report, _operation

        app = self.exops.application
        record = app.journal.read()["intents"]["exops.work." + request["work_run_id"]]
        assert record["run"]["active_graph_run_id"] == request["graph_run_id"]
        assert record["admitted_officer_segments"]
        self.requests.append(copy.deepcopy(request))
        session = OfficerToolSession(app, request["work_run_id"],
                                     request["executor_id"], request["graph_run_id"])
        calls = []

        def call(name, arguments):
            selected = None if name == "proposal_create" and not self.bound_effect else session
            if selected is not None:
                selected.authorize(name, arguments)
            result, error = _convert_report(SimpleNamespace(services=app), name, arguments,
                _operation(app, name, arguments, session=selected)())
            assert not error, result
            calls.append({"name": name, "arguments": arguments, "result": result})

        call("work_context_read", {})
        for locator in request["source_chunks"]:
            call("knowledge_read", {"resource_reference": request["source_reference"],
                                     "chunk_locator": locator})
        call("proposal_create", {"source_reference": request["source_reference"]})
        response = {
            "request_digest": request["request_digest"], "runner_binding": dict(self.binding),
            "model": self.binding["model"], "provider": self.binding["provider"],
            "session_id": "synthetic-focused-test", "final_text": "Pending proposal prepared.",
            "tool_calls": calls, "exposed_tools": list(session.tools()),
        }
        if self.alter is not None:
            self.alter(request, response)
        return response


def test_model_executor_requires_real_bound_effect_and_fresh_independent_evidence(tmp_path):
    exops, spec, run, source_path, receipt = _started(tmp_path, runner_binding=MODEL_BINDING)
    before = source_path.read_bytes()
    runner = _OwnerToolRunner(exops)
    result = _execute(exops, spec, run, runner=runner)
    assert result.next_run["state"] == "completed"
    assert result.candidate_assessor_id == spec["eval_officer_executor_id"]
    assert "No provider" not in spec["constraints"]
    assert record_digest(MODEL_BINDING) in spec["constraints"]
    request = runner.requests[0]
    assert request["source_chunks"] and all(isinstance(item, str) for item in request["source_chunks"])
    assert set(request) == {"request_digest", "runner_binding", "work_run_id", "work_spec_digest",
        "graph_run_id", "executor_id", "generation", "fencing_token", "source_reference",
        "source_chunks", "control_root"}
    assert request["request_digest"] == record_digest(
        {key: value for key, value in request.items() if key != "request_digest"})
    current = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    evidence = current["normalization_model_execution"]
    assert evidence["request"] == request and evidence["response"]["tool_calls"][-1]["name"] == "proposal_create"
    reevaluated = evaluate_normalization(exops, run["work_run_id"],
        evaluator_id=spec["eval_officer_executor_id"])
    assert reevaluated.next_run["result_reference"] == result.next_run["result_reference"]
    assert reevaluated.evaluations == result.evaluations
    assert _finish(exops, run, receipt, result)["application_state"] == "recorded"
    assert source_path.read_bytes() == before


@pytest.mark.parametrize("changed", [
    "binding", "request", "route", "context", "source", "read", "trace", "denial", "fake_final",
])
def test_model_boundary_rejects_changed_context_source_trace_or_fake_final(tmp_path, changed):
    exops, spec, run, source, _receipt = _started(tmp_path, runner_binding=MODEL_BINDING)
    before = source.read_bytes()

    def alter(_request, response):
        if changed == "binding":
            response["runner_binding"]["adapter_digest"] = digest(b"changed adapter")
        elif changed == "request":
            response["request_digest"] = digest(b"another Work")
        elif changed == "route":
            response["provider"] = "fallback-provider"
        elif changed == "context":
            response["tool_calls"][0]["result"]["data"]["generation"] += 1
        elif changed == "source":
            response["tool_calls"][1]["arguments"]["resource_reference"] = {
                **response["tool_calls"][1]["arguments"]["resource_reference"],
                "content_digest": digest(b"wrong source")}
        elif changed == "read":
            response["tool_calls"][1]["result"]["data"]["text"] = "fabricated source"
        elif changed == "trace":
            response["tool_calls"][1]["name"] = "terminal"
        elif changed == "denial":
            response["tool_calls"][1]["result"]["error"] = {"code": "SCOPE_DENIED"}
        else:
            response["tool_calls"] = []
            response["final_text"] = "I completed and independently approved the task."

    with pytest.raises(AdmissionError):
        _execute(exops, spec, run, runner=_OwnerToolRunner(exops, alter=alter))
    current = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert "assessment" not in current and "submitted_artifacts" not in current
    assert current["run"]["state"] == "running"
    assert current["normalization_model_execution"]["response"] is not None
    assert source.read_bytes() == before


def test_model_trace_with_unbound_real_proposal_cannot_substitute_for_owner_work_effect(tmp_path):
    exops, spec, run, _source, _receipt = _started(tmp_path, runner_binding=MODEL_BINDING)
    with pytest.raises(AdmissionError, match="exact completed Work effect"):
        _execute(exops, spec, run, runner=_OwnerToolRunner(exops, bound_effect=False))
    current = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert "assessment" not in current and "submitted_artifacts" not in current


@pytest.mark.parametrize("mode", ["missing_runner", "wrong_binding", "provider_failure", "missing_response"])
def test_model_execution_never_falls_back_to_deterministic_proposal(tmp_path, mode):
    exops, spec, run, _source, _receipt = _started(tmp_path, runner_binding=MODEL_BINDING)

    class FailedRunner:
        def __init__(self):
            self.binding = {**MODEL_BINDING,
                            **({"adapter_digest": digest(b"other runtime")} if mode == "wrong_binding" else {})}

        async def __call__(self, _request):
            if mode == "provider_failure":
                raise RuntimeError("provider failed")

    with pytest.raises((AdmissionError, RuntimeError)):
        _execute(exops, spec, run, runner=None if mode == "missing_runner" else FailedRunner())
    app = exops.application
    assert not list((app.roots.vault / "01_AI_Review/Pending").glob("*.md"))
    current = app.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert "assessment" not in current


@pytest.mark.parametrize("changed", ["binding", "source", "module"])
def test_model_boundary_rechecks_current_runtime_and_source_after_runner(tmp_path, monkeypatch, changed):
    import vaultops.application.normalization_work as implementation

    exops, spec, run, source, _receipt = _started(tmp_path, runner_binding=MODEL_BINDING)
    runner = _OwnerToolRunner(exops)

    def alter(_request, _response):
        if changed == "binding":
            runner.binding["adapter_digest"] = digest(b"changed during await")
        elif changed == "source":
            source.write_bytes(source.read_bytes() + b"\nChanged while model ran.\n")
        else:
            replacement = tmp_path / "changed_normalization_module.py"
            replacement.write_bytes(b"changed implementation bytes")
            monkeypatch.setattr(implementation, "__file__", str(replacement))

    runner.alter = alter
    with pytest.raises(AdmissionError):
        _execute(exops, spec, run, runner=runner)
    current = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert "assessment" not in current and "submitted_artifacts" not in current


def test_model_evaluator_rereads_execution_trace_instead_of_trusting_producer(tmp_path):
    exops, spec, run, _source, _receipt = _started(tmp_path, runner_binding=MODEL_BINDING)
    _execute(exops, spec, run, runner=_OwnerToolRunner(exops))
    app = exops.application
    with app.journal.writer() as writer:
        current = writer.value["intents"]["exops.work." + run["work_run_id"]]
        evidence = current["normalization_model_execution"]
        evidence["response"]["tool_calls"][0]["result"]["data"]["executor_id"] = "another.executor"
        # Even a self-consistent producer evidence digest is not independent authority.
        evidence["evidence_digest"] = digest(canonical({
            "request": evidence["request"], "response": evidence["response"]}))
        writer.commit(app.clock())
    with pytest.raises(AdmissionError, match="trace context"):
        evaluate_normalization(exops, run["work_run_id"], evaluator_id=spec["eval_officer_executor_id"])


@pytest.mark.parametrize("privacy", ["local_only", "deny", "confidential"])
def test_external_model_denies_private_source_before_request_or_runner_invocation(tmp_path, privacy):
    exops, spec, run, _source, _receipt = _started(tmp_path, runner_binding=MODEL_BINDING,
        source_policy=privacy if privacy != "confidential" else None,
        source_sensitivity="confidential" if privacy == "confidential" else None)
    runner = _OwnerToolRunner(exops)
    with pytest.raises(AdmissionError):
        _execute(exops, spec, run, runner=runner)
    assert not runner.requests
    current = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert "normalization_model_execution" not in current and "assessment" not in current
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))


def test_deterministic_local_normalization_remains_supported_for_local_only_source(tmp_path):
    exops, spec, run, _source, _receipt = _started(tmp_path, source_policy="local_only")
    assert _execute(exops, spec, run).next_run["state"] == "completed"
