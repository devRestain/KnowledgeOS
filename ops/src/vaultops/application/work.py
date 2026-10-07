"""Static execution-contract validation; no Workflow, model, journal or transport loop."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ..adapters.core import AdmissionError as ContractViolation
from ..adapters.core import CoreContracts
from ..adapters.core import canonical as canonical_json
from .work_binding import _target_digest, _validate_target_binding


def graph_content_digest(graph_spec, nodes, edges):
    spec = copy.deepcopy(dict(graph_spec))
    spec.pop("graph_digest", None)
    return record_digest({"graph_spec": spec,
                          "nodes": sorted((dict(item) for item in nodes), key=lambda item: item["node_id"]),
                          "edges": sorted((dict(item) for item in edges), key=lambda item: item["edge_id"])})


PROFILE_KEYS = ("role_id", "role_version", "role_digest", "capability_id", "capability_version", "capability_digest")


def record_digest(value: Mapping[str, Any], omitted: Sequence[str] = ()) -> str:
    content = {key: item for key, item in value.items() if key not in omitted}
    return "sha256:" + hashlib.sha256(canonical_json(content)).hexdigest()


def work_spec_digest(spec: Mapping[str, Any]) -> str:
    return record_digest(spec, ("spec_digest",))


def segment_digest(segment: Mapping[str, Any]) -> str:
    return record_digest(segment, ("segment_digest",))


def gap_signature(remaining: Mapping[str, Any]) -> str:
    return record_digest({"criteria_revision": remaining["criteria_revision"],
                          "unmet": sorted(remaining["unmet_criterion_ids"]),
                          "not_run": sorted(remaining["not_run_criterion_ids"])})


def directive_digest(directive: Mapping[str, Any]) -> str:
    return record_digest(directive, ("directive_digest", "application_state", "applied_at",
                                     "applied_run_revision", "execution_receipt_id"))


def control_intent_digest(directive: Mapping[str, Any]) -> str:
    """Pin the proposed control before the responding actor/receipt is known."""
    keys = ("owner_operation_id", "work_id", "work_run_id", "work_spec_revision", "work_spec_digest",
            "directive_id", "target_control_revision", "generation", "fencing_token", "kind",
            "parameter_id", "directive_reference", "target_graph_run_id")
    return record_digest({key: directive[key] for key in keys if key in directive})


def work_control_binding(directive: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("owner_operation_id", "work_run_id", "work_spec_revision", "work_spec_digest",
            "target_control_revision", "generation", "fencing_token")
    return {**{key: directive[key] for key in keys}, "control_intent_digest": control_intent_digest(directive)}


def _fail(code: str, message: str) -> None:
    raise ContractViolation(code, message)


def _unique(values: Sequence[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    if any(not isinstance(item, Mapping) or key not in item for item in values):
        _fail("INVALID_PAYLOAD", f"Missing logical identity: {key}")
    result = {item[key]: item for item in values}
    if len(result) != len(values):
        _fail("INVALID_PAYLOAD", f"Duplicate logical identity: {key}")
    return result


class WorkContracts:
    """Validate owner-admitted data against caller-supplied authoritative snapshots.

    Callers must authenticate, authorize and commit through their existing owner
    ports. Schema/relationship validation does not issue admission or effects.
    """

    def __init__(self, core: CoreContracts):
        self.core = core
        self.owner = "knowledgeos"
        self.catalog = core.verify()

    def record(self, name: str, value: Mapping[str, Any]) -> None:
        self.core.validate(name, dict(value))
        if value.get("owner_operation_id") != self.owner:
            _fail("FORBIDDEN", f"{name} belongs to another owner")
        for field, pin in (("core_version", "core_version"), ("core_digest", "core_digest"),
                           ("schema_version", "schema_version_pin"), ("semantic_version", "semantic_version"),
                           ("semantic_digest", "semantic_digest")):
            if field in value and value[field] != self.core.binding[pin]:
                _fail("PIN_MISMATCH", f"{name} differs from the selected adoption pin")

    def context(self, value: Mapping[str, Any], spec: Mapping[str, Any], run: Mapping[str, Any] | None = None) -> None:
        if value.get("owner_operation_id") != self.owner or spec["owner_operation_id"] != self.owner:
            _fail("FORBIDDEN", "Work context cannot borrow another Operation owner")
        if "work_id" in value and value["work_id"] != spec["work_id"]:
            _fail("FORBIDDEN", "Work identity differs from the admitted target")
        if value.get("work_spec_revision") != spec["work_spec_revision"] or value.get("work_spec_digest") != spec["spec_digest"]:
            _fail("STALE_REVISION", "WorkSpec revision or digest is stale")
        if run is not None and value.get("work_run_id") != run["work_run_id"]:
            _fail("FORBIDDEN", "WorkRun identity differs from the admitted target")

    def spec(self, spec: Mapping[str, Any]) -> None:
        self.record("WorkSpec", spec)
        if work_spec_digest(spec) != spec["spec_digest"]:
            _fail("DIGEST_MISMATCH", "WorkSpec digest differs from its immutable content")
        method_ref = spec.get("method_contract_ref")
        if method_ref is not None and method_ref["owner_operation_id"] != "knowledgeos":
            _fail("FORBIDDEN", "Work method belongs to another Operation")
        criteria = _unique(spec["acceptance_criteria"], "criterion_id")
        if spec["criteria_revision"] > spec["work_spec_revision"]:
            _fail("STALE_REVISION", "Criteria revision exceeds its WorkSpec revision")
        if spec["eval_officer_executor_id"] in spec["allowed_executor_ids"]:
            _fail("FORBIDDEN", "EvalOfficer must remain independent from work execution")
        if spec["target_kind"] == "team":
            if spec["manager_executor_id"] in spec["allowed_executor_ids"] or spec["manager_executor_id"] == spec["eval_officer_executor_id"]:
                _fail("FORBIDDEN", "Team Manager must be distinct from teammates and EvalOfficer")
        else:
            if spec["allowed_executor_ids"] != [spec["officer_executor_id"]] or len(spec["allowed_profiles"]) != 1:
                _fail("SCOPE_DENIED", "Officer work binds exactly one executor and Profile")
        profiles = {tuple(item[key] for key in PROFILE_KEYS) for item in spec["allowed_profiles"]}
        for pin in _unique(spec["explicit_assignments"], "node_id").values():
            if pin["executor_id"] not in spec["allowed_executor_ids"] or tuple(pin[key] for key in PROFILE_KEYS) not in profiles:
                _fail("SCOPE_DENIED", "Explicit assignment is outside the admitted team/Profile envelope")
            if set(pin["criterion_ids"]) - criteria.keys():
                _fail("SCOPE_DENIED", "Explicit assignment names undeclared criteria")
        if spec["delegation_mode"] == "explicit_assignments" and not spec["explicit_assignments"]:
            _fail("SCOPE_DENIED", "An explicit-only WorkSpec requires explicit assignments")
        policy = spec["continuation_policy"]
        if policy["max_repeated_gap_assessments"] > policy["max_segments"]:
            _fail("INVALID_PAYLOAD", "Repeated-gap bound exceeds the admitted segment bound")

    def method_binding(self, spec: Mapping[str, Any], current_ref: Mapping[str, Any] | None) -> None:
        """Resolve exact method identity before admitting a Graph or a tool effect."""
        self.spec(spec)
        pinned = spec.get("method_contract_ref")
        if pinned is None:
            if current_ref is not None:
                _fail("SCOPE_DENIED", "Unadmitted method cannot be added to this Work")
            return
        if current_ref is None:
            _fail("UNAVAILABLE", "Pinned owner method is unavailable")
        if dict(current_ref) != dict(pinned):
            _fail("STALE_REVISION", "Owner method differs from the admitted WorkSpec pin")

    def artifact(self, reference: Mapping[str, Any], current: Mapping[str, Mapping[str, Any]]) -> None:
        self.record("ArtifactReference", reference)
        if reference["availability"] != "available_by_owner_export":
            _fail("UNAVAILABLE", "Unavailable evidence cannot establish acceptance")
        expected = current.get(reference["artifact_id"])
        if expected is None:
            _fail("UNAVAILABLE", "Artifact is absent from the current owner snapshot")
        self.record("ArtifactReference", expected)
        if dict(reference) != dict(expected):
            code = "STALE_REVISION" if reference["source_revision"] != expected["source_revision"] else "DIGEST_MISMATCH"
            _fail(code, "Artifact reference differs from the current immutable evidence target")

    def run(self, run: Mapping[str, Any], spec: Mapping[str, Any], *, previous=None, assessment=None,
            segments=(), evaluations=(), artifacts=None, checkpoints=None, candidate_assessor_id=None) -> None:
        self.spec(spec)
        self.record("WorkRun", run)
        self.context(run, spec)
        policy = spec["continuation_policy"]
        if run["segments_started"] != len(run["graph_run_ids"]):
            _fail("INVALID_PAYLOAD", "Segment counter differs from immutable lineage inventory")
        if run["segments_started"] > policy["max_segments"] or run["profile_invocations"] > policy["max_profile_invocations"]:
            _fail("SCOPE_DENIED", "WorkRun exceeded an admitted continuation budget")
        if run["repeated_gap_count"] > policy["max_repeated_gap_assessments"]:
            _fail("SCOPE_DENIED", "WorkRun exceeded its admitted repeated-gap bound")
        if run.get("active_graph_run_id") is not None and run["active_graph_run_id"] not in run["graph_run_ids"]:
            _fail("INVALID_PAYLOAD", "Active segment is absent from lineage")
        if previous is not None:
            self.record("WorkRun", previous)
            if any(run[key] != previous[key] for key in ("owner_operation_id", "work_id", "work_run_id")):
                _fail("FORBIDDEN", "Recovery cannot replace logical work identity")
            if run["revision"] != previous["revision"] + 1 or run["generation"] < previous["generation"]:
                _fail("STALE_REVISION", "Canonical commit is not the next live revision/generation")
            if run["state"] not in self.catalog["execution_policy"]["work_transitions"][previous["state"]]:
                _fail("SCOPE_DENIED", "Work lifecycle transition was not admitted")
            if run["control_revision"] not in {previous["control_revision"], previous["control_revision"] + 1}:
                _fail("STALE_REVISION", "Control revision is not the current or next target")
            control_keys = ("work_spec_digest", "generation", "fencing_token", "active_graph_run_id", "state")
            if any(run.get(key) != previous.get(key) for key in control_keys) and run["control_revision"] == previous["control_revision"]:
                _fail("STALE_REVISION", "Changed control targets require a new control revision")
            if run["generation"] == previous["generation"] and run["fencing_token"] != previous["fencing_token"]:
                _fail("STALE_REVISION", "Same generation cannot silently replace its fence")
            if run["generation"] > previous["generation"] and (run["fencing_token"] == previous["fencing_token"] or previous["unresolved_effect_ids"]):
                _fail("OUTCOME_UNKNOWN", "Replacement needs a new fence and reconciled prior effects")
            prefix = run["graph_run_ids"][:len(previous["graph_run_ids"])]
            if prefix != previous["graph_run_ids"] or run["profile_invocations"] < previous["profile_invocations"]:
                _fail("STALE_REVISION", "History rollover cannot discard lineage or consumed budget")
            if previous["work_spec_digest"] != run["work_spec_digest"] and (run["work_spec_revision"] <= previous["work_spec_revision"] or previous["state"] not in {"paused", "awaiting_human"} or previous["unresolved_effect_ids"]):
                _fail("STALE_REVISION", "Work amendment requires a safely waiting run and a newly admitted revision")
        if run["state"] in {"completed", "cancelled"}:
            if run["unresolved_effect_ids"]:
                _fail("OUTCOME_UNKNOWN", "Terminal work cannot pretend uncertain effects were rolled back")
            if run["pending_action_ids"] or run.get("active_graph_run_id") is not None:
                _fail("INVALID_PAYLOAD", "Terminal work still has active or pending control")
        if run["state"] == "completed":
            if assessment is None or candidate_assessor_id is None:
                _fail("UNAUTHENTICATED", "Completion needs an declared independent assessment")
            self.assessment(assessment, spec, run, segments=segments, evaluations=evaluations,
                            artifacts=artifacts or {}, checkpoints=checkpoints or {},
                            candidate_assessor_id=candidate_assessor_id)
            if assessment["completion_state"] != "complete" or run["latest_assessment_id"] != assessment["assessment_id"] or run["latest_assessment_digest"] != record_digest(assessment):
                _fail("INVALID_PAYLOAD", "WorkResult cannot be certified by a different or incomplete assessment")
            self.artifact(run["result_reference"], artifacts or {})

    def segment(self, segment: Mapping[str, Any], spec: Mapping[str, Any], run: Mapping[str, Any], *,
                graph_spec, nodes, edges, assignments, artifacts, predecessor=None, trigger=None, assessment=None,
                current_method_ref=None) -> None:
        self.spec(spec)
        self.method_binding(spec, current_method_ref)
        self.run(run, spec)
        self.record("GraphSegment", segment)
        self.context(segment, spec, run)
        self.record("GraphSpec", graph_spec)
        if segment_digest(segment) != segment["segment_digest"]:
            _fail("DIGEST_MISMATCH", "Segment content differs from its immutable digest")
        if graph_content_digest(graph_spec, nodes, edges) != graph_spec["graph_digest"]:
            _fail("DIGEST_MISMATCH", "Graph data differs from its pinned content digest")
        if any(segment[key] != graph_spec[key] for key in ("graph_id", "graph_revision", "graph_digest")):
            _fail("DIGEST_MISMATCH", "Lineage does not bind the admitted GraphSpec")
        if run["state"] not in {"admitted", "running"} or run["pending_action_ids"]:
            _fail("SCOPE_DENIED", "Waiting or controlled work cannot schedule a new segment")
        if run.get("active_graph_run_id") is not None:
            _fail("SCOPE_DENIED", "Close or safely cut over the previous segment before successor scheduling")
        if run["unresolved_effect_ids"]:
            _fail("OUTCOME_UNKNOWN", "Reconcile prior effects before successor scheduling")
        if segment["segment_index"] != run["segments_started"] + 1 or segment["segment_index"] > spec["continuation_policy"]["max_segments"]:
            _fail("SCOPE_DENIED", "Segment position exceeds current lineage or admitted budget")
        node_map = _unique(nodes, "node_id")
        edge_map = _unique(edges, "edge_id")
        if set(graph_spec["node_ids"]) != node_map.keys() or set(graph_spec["edge_ids"]) != edge_map.keys():
            _fail("INVALID_PAYLOAD", "Graph node/edge inventory is inconsistent")
        incoming = {key: [] for key in node_map}
        outgoing = {key: [] for key in node_map}
        for name, values in (("GraphNode", nodes), ("GraphEdge", edges)):
            for value in values:
                self.core.validate(name, dict(value))
                if value["graph_id"] != graph_spec["graph_id"] or value["graph_revision"] != graph_spec["graph_revision"]:
                    _fail("STALE_REVISION", "Graph component targets another revision")
        for edge in edges:
            if edge["source_node_id"] not in node_map or edge["target_node_id"] not in node_map or edge["condition"] != "always":
                _fail("UNSUPPORTED_CONTRACT", "The first Work profile requires a registered sequential path")
            outgoing[edge["source_node_id"]].append(edge["target_node_id"])
            incoming[edge["target_node_id"]].append(edge["source_node_id"])
        start = graph_spec["start_node_id"]
        if start not in node_map or node_map[start]["node_kind"] != "start":
            _fail("INVALID_PAYLOAD", "Sequential Graph has no declared start")
        visited = []
        cursor = start
        while cursor not in visited:
            visited.append(cursor)
            node = node_map[cursor]
            if node["node_kind"] not in {"start", "task", "terminal"} or len(incoming[cursor]) != (0 if cursor == start else 1):
                _fail("UNSUPPORTED_CONTRACT", "General branching and dynamic topology are outside the first Work profile")
            if node["node_kind"] == "terminal":
                if outgoing[cursor]:
                    _fail("INVALID_PAYLOAD", "Terminal node has an outgoing edge")
                break
            if len(outgoing[cursor]) != 1:
                _fail("UNSUPPORTED_CONTRACT", "Sequential work needs one successor at each stage")
            cursor = outgoing[cursor][0]
        else:
            _fail("UNSUPPORTED_CONTRACT", "A Work segment cannot contain an execution cycle")
        if set(visited) != node_map.keys():
            _fail("INVALID_PAYLOAD", "Sequential work contains unreachable stages")
        tasks = {key for key, node in node_map.items() if node["node_kind"] == "task"}
        if not tasks or run["profile_invocations"] + len(tasks) > spec["continuation_policy"]["max_profile_invocations"]:
            _fail("SCOPE_DENIED", "Segment exceeds its remaining Profile invocation budget")
        if spec["target_kind"] == "officer" and len(tasks) != 1:
            _fail("SCOPE_DENIED", "Standalone Officer segment contains exactly one task Profile")
        selected = _unique(assignments, "node_id")
        if selected.keys() != tasks:
            _fail("INVALID_PAYLOAD", "Every specialist stage needs exactly one recorded assignment")
        criteria = {item["criterion_id"] for item in spec["acceptance_criteria"]}
        targets = set(segment["target_criterion_ids"])
        if targets - criteria:
            _fail("SCOPE_DENIED", "Manager expanded acceptance criteria")
        profiles = {tuple(item[key] for key in PROFILE_KEYS) for item in spec["allowed_profiles"]}
        pins = {item["node_id"]: item for item in spec["explicit_assignments"]}
        for pin in pins.values():
            if targets.intersection(pin["criterion_ids"]) and pin["node_id"] not in tasks:
                _fail("SCOPE_DENIED", "A renamed or omitted stage cannot bypass a criterion-bound user pin")
        for assignment in assignments:
            self.record("GraphAssignment", assignment)
            self.context(assignment, spec, run)
            if assignment["graph_id"] != graph_spec["graph_id"] or assignment["graph_revision"] != graph_spec["graph_revision"]:
                _fail("STALE_REVISION", "Assignment targets another Graph revision")
            if assignment["assignment_generation"] != run["generation"]:
                _fail("STALE_REVISION", "Assignment belongs to a replaced worker generation")
            if assignment["executor_id"] not in spec["allowed_executor_ids"] or tuple(assignment[key] for key in PROFILE_KEYS) not in profiles:
                _fail("SCOPE_DENIED", "Manager selected an unadmitted executor or Profile version")
            pin = pins.get(assignment["node_id"])
            if pin is not None:
                if any(assignment[key] != pin[key] for key in ("executor_id", *PROFILE_KEYS)) or assignment["selection_authority"] != "explicit_user":
                    _fail("SCOPE_DENIED", "Explicit user assignment cannot be replaced")
            elif spec["target_kind"] == "officer":
                if assignment["selection_authority"] != "owner_officer" or assignment["executor_id"] != spec["officer_executor_id"]:
                    _fail("SCOPE_DENIED", "Unpinned Officer assignment differs from the admitted target")
            elif spec["delegation_mode"] != "manager_bounded" or assignment["selection_authority"] != "bounded_manager":
                _fail("SCOPE_DENIED", "Unpinned assignment lacks bounded Manager delegation")
        for reference in segment["input_references"]:
            self.artifact(reference, artifacts)
        if segment["segment_index"] == 1:
            if predecessor is not None or trigger is not None or targets != criteria:
                _fail("INVALID_PAYLOAD", "Initial segment must address the admitted criteria without a predecessor")
        else:
            if predecessor is None or trigger is None:
                _fail("INVALID_PAYLOAD", "Successor needs immutable predecessor and admitted trigger")
            self.record("GraphSegment", predecessor)
            if predecessor["work_run_id"] != run["work_run_id"] or predecessor["graph_run_id"] != run["graph_run_ids"][-1] or predecessor["segment_index"] + 1 != segment["segment_index"]:
                _fail("STALE_REVISION", "Successor lineage differs from the current tail")
            if segment["predecessor_graph_run_id"] != predecessor["graph_run_id"] or segment["graph_run_id"] in run["graph_run_ids"]:
                _fail("IDEMPOTENCY_CONFLICT", "Segment identity must be a new successor")
            trigger_key = {"remaining_work": "remaining_work_id", "steering": "directive_id", "work_revision": "work_id"}[segment["trigger_kind"]]
            if segment["trigger_reference_id"] != trigger[trigger_key] or segment["trigger_digest"] != record_digest(trigger):
                _fail("DIGEST_MISMATCH", "Successor trigger is not the exact admitted record")
            if segment["trigger_kind"] == "remaining_work":
                self.record("RemainingWork", trigger)
                self.context(trigger, spec, run)
                if assessment is None or targets != set(trigger["unmet_criterion_ids"] + trigger["not_run_criterion_ids"]) or self.continuation(trigger, spec, run, assessment=assessment) != "allowed":
                    _fail("SCOPE_DENIED", "Continuation exceeds declared gaps or its admitted budget")
            elif segment["trigger_kind"] == "steering":
                self.record("SteeringDirective", trigger)
                self.context(trigger, spec, run)
                if trigger["kind"] != "cutover" or trigger["application_state"] != "applied" or trigger.get("target_graph_run_id") != predecessor["graph_run_id"]:
                    _fail("SCOPE_DENIED", "Structural continuation requires applied safe CUTOVER")
            else:
                self.spec(trigger)
                if trigger["spec_digest"] != spec["spec_digest"] or trigger["work_spec_revision"] <= predecessor["work_spec_revision"]:
                    _fail("STALE_REVISION", "Work revision trigger was not newly admitted")

    def immutable_segment(self, previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
        self.record("GraphSegment", previous)
        self.record("GraphSegment", candidate)
        if dict(previous) != dict(candidate):
            _fail("IDEMPOTENCY_CONFLICT", "Admitted Graph segments cannot be rewritten")

    def assessment(self, assessment: Mapping[str, Any], spec: Mapping[str, Any], run: Mapping[str, Any], *,
                   segments, evaluations, artifacts, checkpoints, candidate_assessor_id) -> None:
        self.spec(spec)
        self.record("CompletionAssessment", assessment)
        self.context(assessment, spec, run)
        if candidate_assessor_id != spec["eval_officer_executor_id"] or assessment["assessor_id"] != candidate_assessor_id:
            _fail("UNAUTHENTICATED", "Assessment did not come from the admitted independent EvalOfficer")
        if assessment["criteria_revision"] != spec["criteria_revision"]:
            _fail("STALE_REVISION", "Assessment uses superseded criteria")
        known_segments = _unique(segments, "graph_run_id")
        if set(assessment["graph_run_ids"]) - set(run["graph_run_ids"]) or set(assessment["graph_run_ids"]) - known_segments.keys():
            _fail("FORBIDDEN", "Assessment references an unadmitted GraphRun")
        for segment in known_segments.values():
            self.record("GraphSegment", segment)
            if segment["work_run_id"] != run["work_run_id"] or segment["work_id"] != spec["work_id"] or segment["work_spec_revision"] > spec["work_spec_revision"] or segment_digest(segment) != segment["segment_digest"]:
                _fail("DIGEST_MISMATCH", "Assessment lineage was changed or belongs to another work")
        known_evaluations = _unique(evaluations, "evaluation_id")
        for evaluation in known_evaluations.values():
            self.record("RunEvaluation", evaluation)
            self.context(evaluation, spec, run)
            if evaluation["evaluator_id"] != candidate_assessor_id or evaluation["criteria_revision"] != spec["criteria_revision"]:
                _fail("STALE_REVISION", "Segment evaluation identity or criteria is stale")
            segment = known_segments.get(evaluation["run_id"])
            if segment is None or evaluation["run_id"] not in assessment["graph_run_ids"] or evaluation["checkpoint_digest"] != checkpoints.get(evaluation["run_id"]):
                _fail("STALE_REVISION", "Evaluation does not bind the current admitted checkpoint")
            partition = [set(evaluation[key]) for key in ("passed_criterion_ids", "failed_criterion_ids", "not_run_criterion_ids")]
            if any(partition[i] & partition[j] for i in range(3) for j in range(i)) or set.union(*partition) != set(evaluation["criterion_ids"]) or set(evaluation["criterion_ids"]) != set(segment["target_criterion_ids"]):
                _fail("INVALID_PAYLOAD", "RunEvaluation criterion outcomes do not partition its target")
            for reference in evaluation["evidence_references"]:
                self.artifact(reference, artifacts)
        outcomes = _unique(assessment["criterion_outcomes"], "criterion_id")
        criteria = {item["criterion_id"] for item in spec["acceptance_criteria"]}
        if outcomes.keys() != criteria:
            _fail("SCOPE_DENIED", "Assessment must cover exactly the admitted criteria")
        outcome_field = {"satisfied": "passed_criterion_ids", "unmet": "failed_criterion_ids", "not_run": "not_run_criterion_ids"}
        for criterion, outcome in outcomes.items():
            evidence = {record_digest(reference) for reference in outcome["evidence_references"]}
            recorded_evidence = set()
            addressed = [item for item in known_evaluations.values() if criterion in item["criterion_ids"]]
            latest = max((run["graph_run_ids"].index(item["run_id"]) for item in addressed), default=-1)
            for evaluation_id in outcome["evaluation_ids"]:
                evaluation = known_evaluations.get(evaluation_id)
                if evaluation is None or criterion not in evaluation[outcome_field[outcome["outcome"]]]:
                    _fail("INVALID_PAYLOAD", "Criterion outcome conflicts with its cited RunEvaluation")
                if run["graph_run_ids"].index(evaluation["run_id"]) != latest:
                    _fail("STALE_REVISION", "Assessment ignored a later evaluation of the same criterion")
                recorded_evidence.update(record_digest(reference) for reference in evaluation["evidence_references"])
            for reference in outcome["evidence_references"]:
                self.artifact(reference, artifacts)
            if evidence - recorded_evidence:
                _fail("DIGEST_MISMATCH", "Assessment evidence is absent from its segment evaluation")
        complete = all(item["outcome"] == "satisfied" for item in outcomes.values())
        if (assessment["completion_state"] == "complete") != complete:
            _fail("INVALID_PAYLOAD", "Narrative completion differs from criterion evidence")

    def remaining(self, remaining: Mapping[str, Any], assessment: Mapping[str, Any], spec: Mapping[str, Any], run: Mapping[str, Any]) -> None:
        self.spec(spec)
        self.record("CompletionAssessment", assessment)
        self.context(assessment, spec, run)
        self.record("RemainingWork", remaining)
        self.context(remaining, spec, run)
        if remaining["assessment_id"] != assessment["assessment_id"] or remaining["assessment_digest"] != record_digest(assessment) or remaining["criteria_revision"] != spec["criteria_revision"]:
            _fail("STALE_REVISION", "RemainingWork targets another assessment or criteria revision")
        if run.get("latest_assessment_id") != assessment["assessment_id"] or run.get("latest_assessment_digest") != record_digest(assessment):
            _fail("STALE_REVISION", "RemainingWork did not use the owner-admitted latest assessment")
        unmet = {item["criterion_id"] for item in assessment["criterion_outcomes"] if item["outcome"] == "unmet"}
        not_run = {item["criterion_id"] for item in assessment["criterion_outcomes"] if item["outcome"] == "not_run"}
        if set(remaining["unmet_criterion_ids"]) != unmet or set(remaining["not_run_criterion_ids"]) != not_run or not (unmet | not_run):
            _fail("SCOPE_DENIED", "RemainingWork must describe precisely the unfulfilled declared criteria")
        if _unique(remaining["gaps"], "criterion_id").keys() != unmet | not_run or remaining["gap_signature"] != gap_signature(remaining):
            _fail("DIGEST_MISMATCH", "Gap summary or repeated-gap signature changed its target")

    def continuation(self, remaining: Mapping[str, Any], spec: Mapping[str, Any], run: Mapping[str, Any], *, assessment) -> str:
        self.spec(spec)
        self.run(run, spec)
        self.record("RemainingWork", remaining)
        self.context(remaining, spec, run)
        self.remaining(remaining, assessment, spec, run)
        if remaining["assessment_id"] != run.get("latest_assessment_id") or remaining["gap_signature"] != gap_signature(remaining) or remaining["gap_signature"] != run.get("last_gap_signature"):
            _fail("STALE_REVISION", "Continuation did not use the latest admitted gap assessment")
        policy = spec["continuation_policy"]
        if run["segments_started"] >= policy["max_segments"] or run["repeated_gap_count"] >= policy["max_repeated_gap_assessments"] or run["profile_invocations"] >= policy["max_profile_invocations"]:
            return "human_required"
        if run["pending_action_ids"] or run["unresolved_effect_ids"] or run["state"] not in {"running", "admitted"}:
            return "human_required"
        return "allowed"

    def effect(self, intent: Mapping[str, Any], run: Mapping[str, Any], *, receipt=None, dispatch=False) -> str:
        self.record("WorkRun", run)
        self.record("ExecutionIntent", intent)
        if intent.get("work_run_id") != run["work_run_id"] or not run.get("active_graph_run_id") or intent.get("graph_run_id") != run.get("active_graph_run_id"):
            _fail("STALE_REVISION", "Effect belongs to an inactive or foreign GraphRun")
        if intent["generation"] != run["generation"] or intent["fencing_token"] != run["fencing_token"]:
            _fail("STALE_REVISION", "Stale worker cannot commit a canonical effect")
        if dispatch and (run["state"] != "running" or run["pending_action_ids"]):
            _fail("SCOPE_DENIED", "Safe control prevents new effect dispatch")
        if receipt is None:
            return "admitted_intent_only"
        self.record("ExecutionReceipt", receipt)
        if any(receipt.get(key) != intent.get(key) for key in ("effect_id", "content_digest", "generation", "fencing_token", "work_run_id", "graph_run_id")):
            _fail("IDEMPOTENCY_CONFLICT", "Effect acknowledgement belongs to another logical invocation")
        return receipt["outcome"]

    def steering(self, directive: Mapping[str, Any], spec: Mapping[str, Any], run: Mapping[str, Any], *,
                 request, response, decision, target_binding, intent, candidate_actor_id, now: datetime, artifacts,
                 receipt=None, receipt_reference_id=None, applied_run=None, successor_spec=None) -> None:
        self.spec(spec)
        self.record("WorkRun", run)
        self.context(run, spec)
        self.record("SteeringDirective", directive)
        self.context(directive, spec, run)
        for name, value in (("HumanActionRequest", request), ("HumanActionResponse", response), ("ExecutionIntent", intent)):
            self.record(name, value)
        if directive_digest(directive) != directive["directive_digest"]:
            _fail("DIGEST_MISMATCH", "Control target differs from its stable intent digest")
        target = _validate_target_binding(dict(target_binding), self.owner)
        if target.get("work_binding") != work_control_binding(directive) or target["action_id"] != directive["directive_id"]:
            _fail("DIGEST_MISMATCH", "Accepted human target did not pin this proposed control")
        decision_target = _target_digest(target, request["semantic_version"], request["semantic_digest"])
        if decision_target != directive["decision_target_digest"]:
            _fail("DIGEST_MISMATCH", "Control replaced its accepted owner target binding")
        if directive["actor_id"] != candidate_actor_id or response["actor_id"] != candidate_actor_id:
            _fail("UNAUTHENTICATED", "User command actor differs from declared ingress")
        if directive["target_control_revision"] != run["control_revision"] or directive["generation"] != run["generation"] or directive["fencing_token"] != run["fencing_token"]:
            _fail("STALE_REVISION", "Control targets a stale run revision or worker generation")
        for value in (request, response):
            self.context(value, spec, run)
            if value["target_control_revision"] != directive["target_control_revision"] or value["target_digest"] != directive["decision_target_digest"]:
                _fail("STALE_REVISION", "Human action target differs from its control intent")
        action = "steer" if directive["kind"] == "parameter" else directive["kind"]
        if directive["human_request_id"] != request["request_id"] or directive["human_response_id"] != response["response_id"] or response["request_id"] != request["request_id"] or response["request_revision"] != request["request_revision"]:
            _fail("STALE_REVISION", "Control receipt does not correlate to the accepted human request")
        if response["chosen_action"] != action or action not in request["allowed_actions"] or decision.get("decision_state") != "accepted" or decision.get("chosen_action") != action:
            _fail("FORBIDDEN", "Control lacks a matching accepted owner human decision")
        if any(decision.get(key) != value for key, value in {"receipt_id": directive["decision_receipt_id"], "owner_operation_id": self.owner,
                                                          "request_id": request["request_id"], "request_revision": request["request_revision"]}.items()):
            _fail("FORBIDDEN", "Decision receipt belongs to another action target")
        if now.tzinfo is None:
            _fail("INVALID_PAYLOAD", "Control validation requires a timezone-aware owner clock")
        received = datetime.fromisoformat(directive["received_at"])
        expires = datetime.fromisoformat(directive["expires_at"])
        applied = datetime.fromisoformat(directive.get("applied_at", directive["received_at"]))
        if directive["expires_at"] != request["expires_at"] or received > now or expires <= received:
            _fail("EXPIRED", "Command receipt is outside its admitted lifetime")
        if (directive["application_state"] == "received" and now >= expires) or (directive["application_state"] == "applied" and not received <= applied < expires):
            _fail("EXPIRED", "Command application missed its admitted lifetime")
        if intent.get("work_run_id") != run["work_run_id"] or intent.get("graph_run_id") != run.get("active_graph_run_id"):
            _fail("STALE_REVISION", "Control dispatch targets another WorkRun or segment")
        if intent["effect_id"] != directive["effect_id"] or intent["content_digest"] != directive["directive_digest"] or intent["generation"] != directive["generation"] or intent["fencing_token"] != directive["fencing_token"]:
            _fail("IDEMPOTENCY_CONFLICT", "Control dispatch is not the same logical effect intent")
        if directive.get("directive_reference") is not None:
            self.artifact(directive["directive_reference"], artifacts)
        if directive["kind"] == "parameter" and directive["parameter_id"] not in spec["steerable_parameter_ids"]:
            _fail("SCOPE_DENIED", "Parameter steering is outside the admitted context")
        if directive["kind"] == "cutover" and directive["target_graph_run_id"] != run.get("active_graph_run_id"):
            _fail("STALE_REVISION", "CUTOVER targets another active segment")
        if directive["application_state"] == "applied":
            if receipt is None or applied_run is None or directive["execution_receipt_id"] != receipt_reference_id:
                _fail("INVALID_PAYLOAD", "Received control cannot claim application without a correlated result")
            self.record("ExecutionReceipt", receipt)
            if any(receipt.get(key) != intent.get(key) for key in ("effect_id", "content_digest", "generation", "fencing_token", "work_run_id", "graph_run_id")) or receipt["outcome"] != "succeeded":
                _fail("OUTCOME_UNKNOWN", "Uncertain control dispatch is not a safely applied command")
            applied_spec = spec
            if directive["kind"] == "amend_work":
                if successor_spec is None:
                    _fail("SCOPE_DENIED", "Major amendment needs a new Director-admitted WorkSpec")
                self.spec(successor_spec)
                if successor_spec["work_id"] != spec["work_id"] or successor_spec["work_spec_revision"] <= spec["work_spec_revision"] or successor_spec["admission_receipt_id"] == spec["admission_receipt_id"]:
                    _fail("STALE_REVISION", "Major amendment did not obtain new owner admission")
                if successor_spec["acceptance_criteria"] != spec["acceptance_criteria"] and successor_spec["criteria_revision"] <= spec["criteria_revision"]:
                    _fail("STALE_REVISION", "Changed criteria need a new interpretation revision")
                applied_spec = successor_spec
            self.run(applied_run, applied_spec, previous=run)
            if applied_run["control_revision"] != directive["target_control_revision"] + 1:
                _fail("STALE_REVISION", "An applied command must close its exact control revision")
            if applied_run["revision"] != directive["applied_run_revision"] or directive["directive_id"] in applied_run["pending_action_ids"]:
                _fail("STALE_REVISION", "Command result was not applied to the next canonical revision")
            if directive["kind"] in {"pause", "cutover", "cancel", "amend_work"} and (applied_run["unresolved_effect_ids"] or run["unresolved_effect_ids"]):
                _fail("OUTCOME_UNKNOWN", "Safe control must reconcile in-flight effects before application")
            target_state = {"pause": "paused", "resume": "running", "cutover": "paused", "cancel": "cancelled"}.get(directive["kind"])
            if target_state and applied_run["state"] != target_state:
                _fail("INVALID_PAYLOAD", "Applied control result has the wrong lifecycle state")
            if directive["kind"] == "cutover" and applied_run.get("active_graph_run_id") is not None:
                _fail("SCOPE_DENIED", "Applied CUTOVER must close the prior active segment")


def validate_bundle_file(core, control, relative):
    """Check candidate snapshots; no authentication, admission or effects occur."""
    import inspect
    import json

    from ..adapters.paths import resolve_beneath

    result = {"status": "FAIL", "mode": "read_only", "authority": "none",
              "admission_performed": False, "execution_started": False,
              "native_execution": "not_run", "checks": [], "errors": []}
    try:
        path = resolve_beneath(control, relative)
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            _fail("INVALID_PAYLOAD", "bundle must be a bounded regular control file")
        bundle = json.loads(path.read_bytes())
        if not isinstance(bundle, dict) or set(bundle) != {"schema_version", "checks"} or type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1:
            _fail("INVALID_PAYLOAD", "bundle requires schema_version 1 and checks")
        checks = bundle["checks"]
        if not isinstance(checks, list) or not 1 <= len(checks) <= 64:
            _fail("INVALID_PAYLOAD", "bundle requires one to 64 candidate checks")
        contracts = WorkContracts(core)
        allowed = {"spec", "run", "segment", "immutable_segment", "assessment", "remaining", "continuation", "effect", "steering"}
        for index, check in enumerate(checks):
            if not isinstance(check, dict) or set(check) != {"kind", "arguments"} or check["kind"] not in allowed or not isinstance(check["arguments"], dict):
                _fail("INVALID_PAYLOAD", "unsupported check or argument shape")
            method = getattr(contracts, check["kind"])
            arguments = dict(check["arguments"])
            if "now" in arguments:
                arguments["now"] = datetime.fromisoformat(arguments["now"])
                if arguments["now"].tzinfo is None:
                    _fail("INVALID_PAYLOAD", "candidate time must have a timezone")
            inspect.signature(method).bind(**arguments)
            outcome = method(**arguments)
            result["checks"].append({"index": index, "kind": check["kind"], "status": "PASS", "candidate_outcome": outcome})
        result["status"] = "PASS"
        return result, 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError, IndexError) as error:
        result["errors"].append({"code": getattr(error, "code", "INVALID_PAYLOAD"), "message": str(error)})
        return result, 10
