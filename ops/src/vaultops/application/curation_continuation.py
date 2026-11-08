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
from .agent_assessment import assess_result
from .agent_decisions import admitted_director_context, source_context, validate_role_evidence
from .curation_work import (
    CRITERION,
    CurationTeamExecutor,
    _artifact,
    _pins,
    _require,
    _source,
    curation_native_digest,
)
from .curation_work import (
    _bundle as curation_bundle,
)
from .exops import OwnerActor
from .native_team import agent_plan, validate_runner_binding
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
    validate_runner_binding(runner_binding)
    _require(isinstance(binding, TeamBinding), "Continuation requires a TeamBinding")
    TeamCatalog.load(application.roots.control).validate_binding(binding)
    _require(
        binding.team_id == "knowledge-curation"
        and {"knowledgeos.triage", "knowledgeos.normalizer"} <= {item.role_id for item in binding.specialists}
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
        + record_digest(validate_runner_binding(runner_binding))
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
    """Seed each immutable segment; the real Manager selects its final tasks."""
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
    method_id = METHOD_ID

    def _current(self, work_run_id):
        _require(validate_runner_binding(self.runner.binding) == self.runner_binding, "Continuation runner changed")
        self.exops._current_binding()
        record = self.exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
        _require(
            isinstance(record, dict) and record.get("application_state") == "started",
            "Continuation has no active owner attempt",
        )
        spec, run = record["spec"], record["run"]
        binding = self.exops.team_for_spec(spec)
        _require(binding.native_binding_digest == curation_continuation_native_digest(self.runner_binding),
                 "Continuation native binding changed")
        admitted_director_context(self.exops.application, record)
        method = WorkMethodCatalog.load(self.exops.application.roots.control).admitted_method(spec, self.exops.contracts)
        _require(method["method_id"] == METHOD_ID, "Continuation method changed")
        _source(self.exops.application, record["method_source_reference"])
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
        _require(len(admitted) == run["segments_started"] in {1, 2}
                 and run["graph_run_ids"] == list(admitted),
                 "Continuation segment inventory changed")
        invocations = 0
        predecessor = None
        for index, graph_run_id in enumerate(run["graph_run_ids"], 1):
            plan = agent_plan(record, graph_run_id)
            selected = admitted[graph_run_id]
            segment = selected["segment"]
            invocations += len(plan["steps"])
            _require(segment["segment_index"] == index
                     and selected["source_reference"] == record["method_source_reference"]
                     and selected["method_contract_ref"] == spec["method_contract_ref"]
                     and selected["team_catalog_digest"] == self.exops.catalog.digest
                     and selected["native_binding_digest"] == curation_continuation_native_digest(self.runner_binding),
                     "Continuation source, method or binding changed")
            if predecessor is not None:
                remaining = record["curation_initial_assessment"]["remaining_work"]
                _require(segment["predecessor_graph_run_id"] == predecessor["graph_run_id"]
                         and segment["trigger_kind"] == "remaining_work"
                         and segment["trigger_digest"] == record_digest(remaining)
                         and segment["trigger_reference_id"] == remaining["remaining_work_id"],
                         "Continuation lineage changed")
            predecessor = segment
        _require(run["profile_invocations"] == invocations, "Continuation invocation accounting changed")
        return admitted[run["active_graph_run_id"]]["segment"]

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
        await self._compose(record, bundle, artifacts, required_stage_ids=["capture_evidence"])

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
        semantic = record.get("curation_initial_semantic")
        _require(isinstance(retained, dict) and isinstance(semantic, dict),
                 "Initial independent semantic assessment is unavailable")
        _require(all(retained[key] == semantic[key] for key in (
            "assessment", "remaining_work", "evaluations", "checkpoints", "assessment_artifacts", "segments"
        )), "Initial assessment differs from its actual EvalOfficer decision")
        evidence = semantic["evidence"]
        _require(evidence["content_digest"] == record_digest(evidence, ("content_digest",)),
                 "Initial EvalOfficer evidence changed")
        validate_role_evidence(self.exops.application.roots.control, "knowledgeos.eval_officer",
                               evidence["response"], tools=[])
        spec, run = record["spec"], record["run"]
        assessment, remaining = retained["assessment"], retained["remaining_work"]
        initial_id = run["graph_run_ids"][0]
        _require(retained["segments"] == [record["admitted_method_segments"][initial_id]["segment"]]
                 and retained["run"].get("latest_assessment_id") == assessment["assessment_id"]
                 and retained["run"].get("latest_assessment_digest") == record_digest(assessment)
                 and retained["run"].get("last_gap_signature") == remaining["gap_signature"],
                 "Historical continuation trigger or original segment changed")
        final = record.get("curation_semantic_assessment")
        if final is None:
            _require(record.get("remaining_work") == remaining and record.get("assessment") == assessment
                     and run.get("latest_assessment_id") == assessment["assessment_id"]
                     and run.get("latest_assessment_digest") == record_digest(assessment)
                     and run.get("last_gap_signature") == remaining["gap_signature"],
                     "Continuation trigger is not the current independent gap")
        else:
            _require(record.get("remaining_work") == final["remaining_work"],
                     "Current RemainingWork differs from the final semantic assessment")
            if record["application_state"] == "recorded":
                _require(record.get("assessment") == final["assessment"]
                         and run.get("latest_assessment_digest") == record_digest(final["assessment"]),
                         "Final semantic assessment does not bind the current result")
        initial_view = copy.deepcopy(record)
        initial_view["run"]["active_graph_run_id"] = initial_id
        initial_view["run"]["state"] = "running"
        self._evidence(initial_view)
        self.exops.contracts.assessment(
            assessment, spec, retained["run"], segments=retained["segments"],
            evaluations=retained["evaluations"], artifacts=retained["assessment_artifacts"],
            checkpoints=retained["checkpoints"], candidate_assessor_id=spec["eval_officer_executor_id"],
        )
        self.exops.contracts.remaining(remaining, assessment, spec, retained["run"])
        return retained

    async def assess_initial(self, work_run_id):
        record = self._current(work_run_id)
        _require(
            "curation_initial_assessment" not in record
            and "curation_initial_semantic" not in record
            and self._segment(record)["segment_index"] == 1
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
        structural = WorkResult(
            {**copy.deepcopy(record["run"]), "revision": record["run"]["revision"] + 1,
             "control_revision": record["run"]["control_revision"] + 1},
            assessment, segments, evaluations, artifacts, checkpoints, record["spec"]["eval_officer_executor_id"],
        )
        assessed = await assess_result(self.exops, self.runner, record, structural, evidence={
            "sources": [source_context(self.exops.application, record["method_source_reference"])],
            "artifacts": [],
            "observations": {"triage": record["curation_capture_evidence"],
                             "normalization_proposal": "not_run"},
        })
        result, remaining = assessed["result"], assessed["remaining_work"]
        _require(remaining is not None, "Read-only initial segment cannot certify the required Pending effect")
        assessment, segments, evaluations = result.assessment, result.segments, result.evaluations
        artifacts, checkpoints = result.artifacts, result.checkpoints
        self._save(work_run_id, record, "curation_initial_semantic", {
            "assessment": assessment, "remaining_work": remaining, "segments": list(segments),
            "evaluations": list(evaluations), "assessment_artifacts": artifacts,
            "checkpoints": checkpoints, "evidence": assessed["evidence"],
        })
        record = self._current(work_run_id)
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
        await self._compose(record, bundle, artifacts, required_stage_ids=["normalization_proposal"])

    def _request(self, record):
        _require(self._segment(record)["segment_index"] == 2, "Normalizer requires the admitted successor")
        self._initial_admission(record)
        return super()._request(record)

    def _assess(self, record):
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
        return combined
