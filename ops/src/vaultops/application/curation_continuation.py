"""Opt-in, criterion-linked two-segment curation; no SDK or provider imports.

The first Graph reads the Capture, but does not claim the Pending criterion.
Only independently admitted RemainingWork can authorize the normalizer Graph.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path

from ..adapters.core import digest
from ..adapters.owner_journal import read_regular, stamp
from .curation_work import (
    CRITERION,
    CurationTeamExecutor,
    _artifact,
    _pins,
    _require,
    _runner_binding,
    _source,
    curation_native_digest,
)
from .curation_work import (
    _bundle as curation_bundle,
)
from .exops import OwnerActor
from .team_catalog import TeamBinding, TeamCatalog
from .temporal_work import WorkResult
from .work import (
    WorkContracts,
    gap_signature,
    graph_content_digest,
    record_digest,
    segment_digest,
    work_spec_digest,
)
from .work_methods import WorkMethodCatalog
from .work_tools import TeamToolSession

METHOD_ID = "knowledgeos.capture_normalization_continuation_team"
DEFAULT_CONTINUATION_POLICY = {
    "max_segments": 2,
    "max_repeated_gap_assessments": 2,
    "max_profile_invocations": 2,
}


def curation_continuation_native_digest(runner_binding):
    modules = {}
    for name in (
        "curation_continuation.py",
        "curation_continuation_temporal.py",
        "curation_continuation_workflow.py",
        "curation_control.py",
        "curation_control_temporal.py",
        "curation_control_workflow.py",
        "exops.py",
        "work_tools.py",
        "knowledge.py",
    ):
        raw = read_regular(Path(__file__).with_name(name))
        _require(raw is not None, "Continuation execution module is unavailable")
        modules[name] = digest(raw)
    return record_digest({
        "curation_native_digest": curation_native_digest(runner_binding),
        "continuation_modules": modules,
    })


def _policy(value):
    selected = DEFAULT_CONTINUATION_POLICY if value is None else value
    _require(
        isinstance(selected, Mapping)
        and set(selected) == set(DEFAULT_CONTINUATION_POLICY)
        and all(
            type(selected[key]) is int and 1 <= selected[key] <= maximum
            for key, maximum in (
                ("max_segments", 256), ("max_repeated_gap_assessments", 256),
                ("max_profile_invocations", 4096),
            )
        ),
        "Continuation requires exactly three bounded positive integer limits",
    )
    return dict(selected)


def curation_continuation_admission(
    application, binding, source_reference, *, work_id, work_run_id,
    admission_receipt_id, runner_binding, continuation_policy=None,
):
    """Construct the exact opt-in Work; Director admission remains independent."""
    _runner_binding(runner_binding)
    _require(isinstance(binding, TeamBinding), "Continuation requires a TeamBinding")
    TeamCatalog.load(application.roots.control).validate_binding(binding)
    _require(
        binding.team_id == "knowledge-curation"
        and [item.role_id for item in binding.specialists]
        == ["knowledgeos.triage", "knowledgeos.normalizer"]
        and binding.native_binding_digest == curation_continuation_native_digest(runner_binding),
        "Continuation requires exact Team and native execution binding",
    )
    contracts = WorkContracts(application.core)
    contracts.record("ArtifactReference", binding.binding_reference)
    path = application.resolve_reference(dict(source_reference))
    _require(path.startswith("00_Inbox/Captures/"), "Continuation requires one selected Capture")
    _source(application, source_reference)
    pins = _pins(application)
    spec = {
        "owner_operation_id": "knowledgeos",
        "work_id": work_id,
        "work_spec_revision": 1,
        "criteria_revision": 1,
        "objective": "Read one Capture, independently assess its gap, then prepare one Pending normalization proposal",
        "constraints": "No canonical apply or source mutation; native runner "
        + record_digest(_runner_binding(runner_binding))
        + "; selected source " + record_digest(source_reference),
        "acceptance_criteria": [dict(CRITERION)],
        "target_kind": "team",
        "team_id": binding.team_id,
        "manager_executor_id": binding.manager_executor_id,
        "eval_officer_executor_id": binding.eval_officer_executor_id,
        "allowed_executor_ids": [item.executor_id for item in binding.specialists],
        "allowed_profiles": [dict(item.profile) for item in binding.specialists],
        "delegation_mode": "manager_bounded",
        "explicit_assignments": [],
        "continuation_policy": _policy(continuation_policy),
        "execution_profile": "sequential_graph",
        "admission_receipt_id": admission_receipt_id,
        "steerable_parameter_ids": [],
        "method_contract_ref": WorkMethodCatalog.load(application.roots.control).reference(METHOD_ID),
        **pins,
    }
    spec["spec_digest"] = work_spec_digest(spec)
    run = {
        "owner_operation_id": "knowledgeos",
        "work_id": work_id,
        "work_run_id": work_run_id,
        "work_spec_revision": 1,
        "work_spec_digest": spec["spec_digest"],
        "revision": 1,
        "control_revision": 1,
        "generation": 1,
        "fencing_token": "curation.continuation." + record_digest({"work_run_id": work_run_id})[7:],
        "state": "admitted",
        "graph_run_ids": [],
        "segments_started": 0,
        "profile_invocations": 0,
        "repeated_gap_count": 0,
        "pending_action_ids": [],
        "unresolved_effect_ids": [],
        "binding_reference": dict(binding.binding_reference),
        **pins,
    }
    contracts.run(run, spec)
    return spec, run


def _bundle(app, spec, run, source, *, index, predecessor=None, remaining=None):
    """Each Graph has a distinct immutable identity and exactly one Profile."""
    bundle, artifacts = curation_bundle(app, spec, run, source)
    base = {
        "graph_id": bundle["graph_spec"]["graph_id"] + ".segment" + str(index),
        "graph_revision": 1,
    }
    stage = "node.capture_evidence" if index == 1 else "node.normalization_proposal"
    ids = ["node.start", stage, "node.finish"]
    nodes = [{**node, **base} for node in bundle["nodes"] if node["node_id"] in ids]
    edges = [
        {**base, "edge_id": "edge." + str(i), "source_node_id": a,
         "target_node_id": b, "condition": "always"}
        for i, (a, b) in enumerate(pairwise(ids))
    ]
    graph = {
        **bundle["graph_spec"], **base, "node_ids": ids,
        "edge_ids": [edge["edge_id"] for edge in edges], "max_steps": 3,
    }
    graph["graph_digest"] = graph_content_digest(graph, nodes, edges)
    segment = {
        **bundle["segment"], **base,
        "graph_run_id": bundle["segment"]["graph_run_id"] + ".segment" + str(index),
        "graph_digest": graph["graph_digest"], "segment_index": index,
    }
    if index == 2:
        _require(predecessor is not None and remaining is not None, "Successor lacks an admitted gap")
        segment.update(
            predecessor_graph_run_id=predecessor["graph_run_id"],
            trigger_kind="remaining_work",
            trigger_reference_id=remaining["remaining_work_id"],
            trigger_digest=record_digest(remaining),
            target_criterion_ids=remaining["unmet_criterion_ids"] + remaining["not_run_criterion_ids"],
        )
    segment["segment_digest"] = segment_digest(segment)
    assignments = [
        {**assignment, **base, "selection_authority": "bounded_manager"}
        for assignment in bundle["assignments"] if assignment["node_id"] == stage
    ]
    return {
        "segment": segment, "graph_spec": graph, "nodes": nodes,
        "edges": edges, "assignments": assignments,
    }, artifacts


class CurationContinuationExecutor(CurationTeamExecutor):
    """Reuse exact native trace/effect checks, not ordinary one-Graph assumptions."""

    def _current(self, work_run_id):
        _require(_runner_binding(self.runner.binding) == self.runner_binding, "Continuation runner changed")
        self.exops._current_binding()
        record = self.exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
        _require(
            isinstance(record, dict) and record.get("application_state") == "started",
            "Continuation has no active owner attempt",
        )
        spec, run = record["spec"], record["run"]
        expected, _ = curation_continuation_admission(
            self.exops.application, self.exops.teams.get(spec.get("team_id")),
            record.get("method_source_reference"), work_id=spec["work_id"],
            work_run_id=work_run_id, admission_receipt_id=spec["admission_receipt_id"],
            runner_binding=self.runner_binding, continuation_policy=spec.get("continuation_policy"),
        )
        _require(spec == expected, "Continuation WorkSpec, criteria or Profile envelope changed")
        self.exops._team_binding(spec, run)
        _require(
            self.exops.director_admitted(spec)
            and self.exops.director_method_source_admitted is not None
            and self.exops.director_method_source_admitted(spec, record["method_source_reference"]),
            "Continuation Director/source admission is no longer current",
        )
        _require(record.get("actor_ingress") in {"cli", "web"}, "Original actor ingress is unavailable")
        self.exops._guard(OwnerActor(record["actor_id"], record["actor_ingress"]), "submit_work")
        self.exops.contracts.run(run, spec)
        _require(
            run["state"] == "running" and not run["pending_action_ids"]
            and not run["unresolved_effect_ids"], "Continuation Work is not effect-safe",
        )
        if record.get("control_checkpoint") is True and "curation_initial_assessment" in record:
            controller = getattr(self.exops, "curation_control", None)
            _require(controller is not None, "Controlled continuation requires its owner release authority")
            controller._authorize_successor(self.exops.application.journal.read(), record)
        return record

    def _segment(self, record):
        spec, run = record["spec"], record["run"]
        admitted = record.get("admitted_method_segments", {})
        count = len(admitted)
        _require(
            count in {1, 2} and run["graph_run_ids"] == list(admitted)
            and run["segments_started"] == count and run["profile_invocations"] == count,
            "Continuation requires exact immutable one-Profile segment inventory",
        )
        predecessor = None
        retained = record.get("curation_initial_assessment", {})
        for index, graph_run_id in enumerate(run["graph_run_ids"], 1):
            bundle, _ = _bundle(
                self.exops.application, spec, run, record["method_source_reference"], index=index,
                predecessor=predecessor, remaining=retained.get("remaining_work"),
            )
            selected = admitted[graph_run_id]
            _require(
                graph_run_id == bundle["segment"]["graph_run_id"]
                and selected["segment"] == bundle["segment"]
                and selected["assignments"] == bundle["assignments"]
                and selected["source_reference"] == record["method_source_reference"]
                and selected["method_contract_ref"] == spec["method_contract_ref"]
                and selected["team_catalog_digest"] == self.exops.catalog.digest
                and selected["native_binding_digest"] == curation_continuation_native_digest(self.runner_binding),
                "Continuation immutable predecessor, trigger, assignment or native binding changed",
            )
            predecessor = selected["segment"]
        active = run.get("active_graph_run_id")
        _require(active in admitted, "Continuation has no selected active segment")
        return admitted[active]["segment"]

    def _triage(self, record):
        # Reconstruct the initial Graph context, including when the current Graph
        # is the normalizer. The initial evidence must never be reinterpreted as
        # a source read made by a successor Profile.
        self._segment(record)
        initial = copy.deepcopy(record)
        initial["run"]["active_graph_run_id"] = record["run"]["graph_run_ids"][0]
        initial["run"]["state"] = "running"
        return super()._triage(initial)

    async def manager(self, spec, run):
        record = self._current(run["work_run_id"])
        _require(record["spec"] == dict(spec) and record["run"] == dict(run), "Manager input changed")
        _require(not run["graph_run_ids"] and not record.get("admitted_method_segments"), "Initial Graph already admitted")
        bundle, artifacts = _bundle(
            self.exops.application, spec, run, record["method_source_reference"], index=1,
        )
        TeamToolSession(
            self.exops.application, run["work_run_id"], spec["manager_executor_id"], exops=self.exops,
        ).propose_graph({"bundle": bundle, "artifacts": artifacts})

    async def capture_evidence(self, work_run_id):
        record = self._current(work_run_id)
        _require(self._segment(record)["segment_index"] == 1, "Capture belongs only to the initial segment")
        _require("curation_initial_assessment" not in record, "Initial assessment already committed")
        await super().capture_evidence(work_run_id)

    def _initial_result(self, record, *, assessed_at=None):
        self._evidence(record)
        spec, run = record["spec"], record["run"]
        evaluator = spec["eval_officer_executor_id"]
        _require(
            evaluator not in [spec["manager_executor_id"], *spec["allowed_executor_ids"]],
            "Only independent EvalOfficer may assess RemainingWork",
        )
        initial_id = run["graph_run_ids"][0]
        segment = record["admitted_method_segments"][initial_id]["segment"]
        _, raw = _source(self.exops.application, record["method_source_reference"])
        artifact = _artifact(record["method_source_reference"], raw)
        checkpoint = record_digest({
            "graph_run_id": initial_id, "source_digest": digest(raw),
            "triage_digest": record["curation_capture_evidence"]["evidence_digest"],
            "proposal_state": "not_run",
        })
        suffix = record_digest({"graph_run_id": initial_id, "checkpoint": checkpoint})[7:39]
        evaluation = {
            "owner_operation_id": "knowledgeos", "run_id": initial_id,
            "work_run_id": run["work_run_id"], "work_spec_revision": spec["work_spec_revision"],
            "work_spec_digest": spec["spec_digest"], "checkpoint_digest": checkpoint,
            "criteria_revision": spec["criteria_revision"], "criterion_ids": [CRITERION["criterion_id"]],
            "passed_criterion_ids": [], "failed_criterion_ids": [],
            "not_run_criterion_ids": [CRITERION["criterion_id"]],
            "evaluation_id": "evaluation.curation.initial." + suffix,
            "evaluator_id": evaluator, "evidence_references": [artifact],
        }
        context = {key: run[key] for key in (
            "owner_operation_id", "work_id", "work_run_id", "work_spec_revision", "work_spec_digest",
        )}
        assessment = {
            **context, **_pins(self.exops.application),
            "assessment_id": "assessment.curation.initial." + suffix,
            "criteria_revision": spec["criteria_revision"], "assessor_id": evaluator,
            "assessed_at": assessed_at or stamp(self.exops.application.clock()),
            "graph_run_ids": [initial_id], "completion_state": "incomplete",
            "criterion_outcomes": [{
                "criterion_id": CRITERION["criterion_id"], "outcome": "not_run",
                "evaluation_ids": [evaluation["evaluation_id"]], "evidence_references": [artifact],
            }],
        }
        remaining = {
            **context, **_pins(self.exops.application),
            "remaining_work_id": "remaining.curation." + suffix,
            "assessment_id": assessment["assessment_id"], "assessment_digest": record_digest(assessment),
            "criteria_revision": spec["criteria_revision"], "unmet_criterion_ids": [],
            "not_run_criterion_ids": [CRITERION["criterion_id"]],
            "gaps": [{"criterion_id": CRITERION["criterion_id"],
                      "summary": "Capture evidence read; source-bound Pending normalization proposal has not run."}],
        }
        remaining["gap_signature"] = gap_signature(remaining)
        return assessment, remaining, (segment,), (evaluation,), {artifact["artifact_id"]: artifact}, {initial_id: checkpoint}

    def _initial_admission(self, record):
        retained = record.get("curation_initial_assessment")
        _require(isinstance(retained, dict) and isinstance(retained.get("assessment"), dict), "Initial assessment is unavailable")
        initial = copy.deepcopy(record)
        if record.get("control_checkpoint") is True:
            closed_initial = record.get("curation_control_checkpoint", {}).get("initial_run", retained.get("run"))
            _require(closed_initial == retained.get("run"), "Original closed control evidence changed")
            initial["run"] = copy.deepcopy(closed_initial)
            initial["run"]["active_graph_run_id"] = closed_initial["graph_run_ids"][0]
            initial_id = closed_initial["graph_run_ids"][0]
            initial["admitted_method_segments"] = {
                initial_id: copy.deepcopy(record["admitted_method_segments"][initial_id]),
            }
        assessment, remaining, segments, evaluations, artifacts, checkpoints = self._initial_result(
            initial, assessed_at=retained["assessment"].get("assessed_at"),
        )
        run, spec = record["run"], record["spec"]
        if record.get("control_checkpoint") is True:
            closed = copy.deepcopy(retained["run"])
        else:
            closed = copy.deepcopy(run)
            closed.update(
                revision=run["revision"] - (run["segments_started"] - 1),
                control_revision=run["control_revision"] - (run["segments_started"] - 1),
                graph_run_ids=run["graph_run_ids"][:1], segments_started=1, profile_invocations=1,
            )
            closed.pop("active_graph_run_id", None)
        _require(retained == {
            "assessment": assessment, "remaining_work": remaining, "evaluations": list(evaluations),
            "checkpoints": checkpoints, "assessment_artifacts": artifacts,
            "segments": list(segments), "run": closed,
        }, "Initial independent assessment or RemainingWork evidence changed")
        _require(
            record.get("remaining_work") == remaining
            and record.get("assessment") == assessment
            and run.get("latest_assessment_id") == assessment["assessment_id"]
            and run.get("latest_assessment_digest") == record_digest(assessment)
            and run.get("last_gap_signature") == remaining["gap_signature"]
            and run["repeated_gap_count"] == 1,
            "Continuation trigger is not the exact current independent gap",
        )
        self.exops.contracts.assessment(
            assessment, spec, run, segments=segments, evaluations=evaluations, artifacts=artifacts,
            checkpoints=checkpoints, candidate_assessor_id=spec["eval_officer_executor_id"],
        )
        self.exops.contracts.remaining(remaining, assessment, spec, run)
        _require(
            self.exops.contracts.continuation(remaining, spec, closed, assessment=assessment) == "allowed",
            "Initial continuation authorization exhausted its admitted budget",
        )
        return retained

    async def assess_initial(self, work_run_id):
        record = self._current(work_run_id)
        _require(
            self._segment(record)["segment_index"] == 1
            and "curation_initial_assessment" not in record
            and not any(key in record for key in ("curation_model_request", "curation_model_execution", "submitted_artifacts")),
            "Initial assessment requires an unassessed read-only segment",
        )
        snapshot = self.exops.application.journal.read()
        _require(
            not any(item.get("work_binding", {}).get("work_run_id") == work_run_id
                    for item in snapshot["intents"].values()),
            "Initial assessment cannot overlook an actual Work effect",
        )
        assessment, remaining, segments, evaluations, artifacts, checkpoints = self._initial_result(record)
        run = record["run"]
        next_run = {
            **copy.deepcopy(run), "revision": run["revision"] + 1,
            "control_revision": run["control_revision"] + 1,
            "latest_assessment_id": assessment["assessment_id"],
            "latest_assessment_digest": record_digest(assessment),
            "last_gap_signature": remaining["gap_signature"], "repeated_gap_count": 1,
        }
        next_run.pop("active_graph_run_id")
        # The owner boundary, not this producer, computes the actual budget
        # decision and atomically closes the segment with its assessment/gap.
        self._current(work_run_id)
        return self.exops.record_remaining_work(
            work_run_id, worker_identity=self.exops.worker_identity, action_id=record["action_id"],
            next_run=next_run, assessment=assessment, remaining=remaining,
            segments=segments, evaluations=evaluations, artifacts=artifacts, checkpoints=checkpoints,
            candidate_assessor_id=record["spec"]["eval_officer_executor_id"],
        )

    async def manager_successor(self, work_run_id):
        record = self._current(work_run_id)
        spec, run = record["spec"], record["run"]
        _require(
            run["segments_started"] == 1 and "active_graph_run_id" not in run,
            "Successor requires a closed initial segment",
        )
        # _evidence reconstructs the closed initial context, not a fabricated
        # current assignment. Select it only in an in-memory read-only view.
        initial = copy.deepcopy(record)
        initial["run"]["active_graph_run_id"] = run["graph_run_ids"][0]
        retained = self._initial_admission(initial)
        _require(
            self.exops.contracts.continuation(retained["remaining_work"], spec, run, assessment=retained["assessment"]) == "allowed",
            "Continuation budget requires a human",
        )
        predecessor = record["admitted_method_segments"][run["graph_run_ids"][-1]]["segment"]
        bundle, artifacts = _bundle(
            self.exops.application, spec, run, record["method_source_reference"], index=2,
            predecessor=predecessor, remaining=retained["remaining_work"],
        )
        TeamToolSession(
            self.exops.application, work_run_id, spec["manager_executor_id"], exops=self.exops,
        ).propose_graph({"bundle": bundle, "artifacts": artifacts})

    def _request(self, record):
        _require(self._segment(record)["segment_index"] == 2, "Normalizer requires the admitted successor")
        self._initial_admission(record)
        return super()._request(record)

    async def assess(self, work_run_id):
        record = self._current(work_run_id)
        retained = self._initial_admission(record)
        result = super()._assess(record)
        assessment = copy.deepcopy(result.assessment)
        assessment["graph_run_ids"] = list(record["run"]["graph_run_ids"])
        completed = {**result.next_run, "latest_assessment_digest": record_digest(assessment)}
        initial_segment = record["admitted_method_segments"][assessment["graph_run_ids"][0]]["segment"]
        combined = WorkResult(
            completed, assessment, (initial_segment, *result.segments),
            (*retained["evaluations"], *result.evaluations),
            {**retained["assessment_artifacts"], **result.artifacts},
            {**retained["checkpoints"], **result.checkpoints}, result.candidate_assessor_id,
        )
        self.exops.contracts.run(
            combined.next_run, record["spec"], previous=record["run"], assessment=combined.assessment,
            segments=combined.segments, evaluations=combined.evaluations, artifacts=combined.artifacts,
            checkpoints=combined.checkpoints, candidate_assessor_id=combined.candidate_assessor_id,
        )
        self._current(work_run_id)
        return combined
