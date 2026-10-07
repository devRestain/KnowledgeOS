"""Synthetic execution-contract records; no Temporal or domain runtime."""

from __future__ import annotations

import copy

from vaultops.adapters.core import PINS
from vaultops.application.work import (
    gap_signature,
    graph_content_digest,
    record_digest,
    segment_digest,
    work_spec_digest,
)


def finalize_graph_spec(spec, nodes, edges):
    return {**spec, "graph_digest": graph_content_digest(spec, nodes, edges)}

AT = "2030-01-01T00:00:00Z"
UNTIL = "2030-01-01T01:00:00Z"


class WorkFixture:
    def __init__(self):
        self.pins = {key: PINS[key] for key in ("core_version", "core_digest", "semantic_version", "semantic_digest")}
        self.pins["schema_version"] = PINS["schema_version_pin"]
        self.profile = {"role_id": "knowledgeos.writer", "role_version": "1.0.0",
                        "role_digest": record_digest({"role": "writer"}),
                        "capability_id": "knowledgeos.synthetic.write", "capability_version": "1.0.0",
                        "capability_digest": record_digest({"capability": "synthetic"})}
        self.artifacts = {name: self.artifact(name) for name in ("binding", "a", "b", "result", "parameter")}
        self.current_artifacts = {value["artifact_id"]: value for value in self.artifacts.values()}
        self.spec = {"owner_operation_id": "knowledgeos", "work_id": "work.synthetic", "work_spec_revision": 1,
                     "criteria_revision": 1, "objective": "Produce two independently checked synthetic sections",
                     "constraints": "Synthetic records only; no provider, domain or external effects",
                     "acceptance_criteria": [{"criterion_id": "criterion.a", "description": "Section A has owner evidence"},
                                             {"criterion_id": "criterion.b", "description": "Section B has owner evidence"}],
                     "target_kind": "team", "team_id": "team.synthetic", "allowed_executor_ids": ["knowledgeos.writer"],
                     "manager_executor_id": "knowledgeos.manager", "eval_officer_executor_id": "knowledgeos.evaluator",
                     "delegation_mode": "manager_bounded", "allowed_profiles": [self.profile], "explicit_assignments": [],
                     "continuation_policy": {"max_segments": 3, "max_repeated_gap_assessments": 2, "max_profile_invocations": 4},
                     "execution_profile": "sequential_graph", "admission_receipt_id": "admission.synthetic.one",
                     "steerable_parameter_ids": ["parameter.tone"], **self.pins}
        self.spec["spec_digest"] = work_spec_digest(self.spec)
        self.context = {"owner_operation_id": "knowledgeos", "work_id": self.spec["work_id"],
                        "work_run_id": "workrun.synthetic", "work_spec_revision": 1,
                        "work_spec_digest": self.spec["spec_digest"]}
        self.run = {**self.context, "revision": 1, "control_revision": 1, "generation": 1,
                    "fencing_token": "fence.synthetic.one", "state": "admitted", "graph_run_ids": [],
                    "segments_started": 0, "repeated_gap_count": 0, "profile_invocations": 0,
                    "pending_action_ids": [], "unresolved_effect_ids": [],
                    "binding_reference": self.artifacts["binding"], **self.pins}

    @staticmethod
    def artifact(name):
        return {"owner_operation_id": "knowledgeos", "artifact_id": "artifact.synthetic." + name,
                "source_revision": "revision.1", "content_digest": record_digest({"synthetic": name}),
                "media_type": "application/json", "size_bytes": 64, "classification": "internal",
                "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.synthetic.export"}

    def bundle(self, number=1, *, targets=None, predecessor=None, remaining=None):
        targets = targets or ["criterion.a", "criterion.b"]
        graph_id = f"graph.synthetic.segment{number}"
        base = {"graph_id": graph_id, "graph_revision": 1}
        node_ids = ["node.start", "node.write", "node.finish"]
        nodes = [{**base, "node_id": name, "node_kind": kind, "max_attempts": 1,
                  "input_semantic_term_ids": [], "output_semantic_term_ids": []}
                 for name, kind in zip(node_ids, ("start", "task", "terminal"))]
        edges = [{**base, "edge_id": name, "source_node_id": source, "target_node_id": target, "condition": "always"}
                 for name, source, target in (("edge.start.write", "node.start", "node.write"),
                                             ("edge.write.finish", "node.write", "node.finish"))]
        graph = finalize_graph_spec({**base, "owner_operation_id": "knowledgeos", "profile": "graph_enabled",
                                     "engine_id": "agentfabric.operation.temporal_langgraph", "engine_version": "0.1.0",
                                     "start_node_id": "node.start", "node_ids": node_ids,
                                     "edge_ids": [edge["edge_id"] for edge in edges], "max_steps": 8,
                                     "input_semantic_term_ids": [], "output_semantic_term_ids": [], **self.pins}, nodes, edges)
        assignments = [{**base, "node_id": "node.write", "owner_operation_id": "knowledgeos",
                        "assignment_generation": 1, "executor_id": "knowledgeos.writer", **self.profile,
                        "admission_receipt_id": "admission.synthetic.one", "work_run_id": self.context["work_run_id"],
                        "work_spec_revision": 1, "work_spec_digest": self.spec["spec_digest"],
                        "selection_authority": "bounded_manager",
                        "semantic_version": self.pins["semantic_version"], "semantic_digest": self.pins["semantic_digest"]}]
        segment = {**self.context, "graph_run_id": f"graphrun.synthetic.segment{number}", "segment_index": number,
                   "target_criterion_ids": targets, **base, "graph_digest": graph["graph_digest"],
                   "trigger_kind": "initial" if number == 1 else "remaining_work", "input_references": [], **self.pins}
        if number > 1:
            segment.update(predecessor_graph_run_id=predecessor["graph_run_id"],
                           trigger_reference_id=remaining["remaining_work_id"], trigger_digest=record_digest(remaining))
        segment["segment_digest"] = segment_digest(segment)
        return {"segment": segment, "graph_spec": graph, "nodes": nodes, "edges": edges, "assignments": assignments}

    def evaluation(self, segment, *, passed, failed=(), not_run=()):
        evidence = [self.artifacts[item.removeprefix("criterion.")] for item in passed]
        return {"owner_operation_id": "knowledgeos", "run_id": segment["graph_run_id"],
                "checkpoint_digest": record_digest({"checkpoint": segment["graph_run_id"]}),
                "criteria_revision": 1, "criterion_ids": segment["target_criterion_ids"],
                "passed_criterion_ids": list(passed), "failed_criterion_ids": list(failed), "not_run_criterion_ids": list(not_run),
                "work_run_id": self.context["work_run_id"], "work_spec_revision": 1, "work_spec_digest": self.spec["spec_digest"],
                "evaluation_id": "evaluation." + segment["graph_run_id"], "evaluator_id": "knowledgeos.evaluator",
                "evidence_references": evidence}

    def assessment(self, evaluations):
        latest = {criterion: evaluation for evaluation in evaluations for criterion in evaluation["criterion_ids"]}
        outcomes = []
        for criterion in ("criterion.a", "criterion.b"):
            evaluation = latest[criterion]
            outcome = "satisfied" if criterion in evaluation["passed_criterion_ids"] else "unmet" if criterion in evaluation["failed_criterion_ids"] else "not_run"
            outcomes.append({"criterion_id": criterion, "outcome": outcome, "evaluation_ids": [evaluation["evaluation_id"]],
                             "evidence_references": [self.artifacts[criterion.removeprefix("criterion.")]] if outcome == "satisfied" else []})
        return {**self.context, "assessment_id": f"assessment.synthetic.{len(evaluations)}", "criteria_revision": 1,
                "assessor_id": "knowledgeos.evaluator", "assessed_at": AT,
                "graph_run_ids": [item["run_id"] for item in evaluations],
                "completion_state": "complete" if all(item["outcome"] == "satisfied" for item in outcomes) else "incomplete",
                "criterion_outcomes": outcomes, **self.pins}

    def remaining(self, assessment):
        gaps = [item for item in assessment["criterion_outcomes"] if item["outcome"] != "satisfied"]
        record = {**self.context, "remaining_work_id": "remaining.synthetic.one", "criteria_revision": 1,
                  "assessment_id": assessment["assessment_id"], "assessment_digest": record_digest(assessment),
                  "unmet_criterion_ids": [item["criterion_id"] for item in gaps if item["outcome"] == "unmet"],
                  "not_run_criterion_ids": [item["criterion_id"] for item in gaps if item["outcome"] == "not_run"],
                  "gaps": [{"criterion_id": item["criterion_id"], "summary": "Produce the missing current evidence"} for item in gaps], **self.pins}
        record["gap_signature"] = gap_signature(record)
        return record

    def flow(self):
        first = self.bundle()
        eval1 = self.evaluation(first["segment"], passed=["criterion.a"], not_run=["criterion.b"])
        assessment1 = self.assessment([eval1])
        remaining = self.remaining(assessment1)
        between = {**copy.deepcopy(self.run), "revision": 4, "control_revision": 3, "state": "running",
                   "graph_run_ids": [first["segment"]["graph_run_id"]], "segments_started": 1,
                   "profile_invocations": 1, "latest_assessment_id": assessment1["assessment_id"],
                   "latest_assessment_digest": record_digest(assessment1), "repeated_gap_count": 1,
                   "last_gap_signature": remaining["gap_signature"]}
        second = self.bundle(2, targets=["criterion.b"], predecessor=first["segment"], remaining=remaining)
        eval2 = self.evaluation(second["segment"], passed=["criterion.b"])
        assessment2 = self.assessment([eval1, eval2])
        complete = {**between, "revision": 8, "control_revision": 6, "state": "completed",
                    "graph_run_ids": [first["segment"]["graph_run_id"], second["segment"]["graph_run_id"]],
                    "segments_started": 2, "profile_invocations": 2, "repeated_gap_count": 0,
                    "latest_assessment_id": assessment2["assessment_id"], "latest_assessment_digest": record_digest(assessment2),
                    "result_reference": self.artifacts["result"]}
        return {"first": first, "eval1": eval1, "assessment1": assessment1, "remaining": remaining,
                "between": between, "second": second, "eval2": eval2, "assessment2": assessment2, "complete": complete}

    def effect(self, run, *, content_digest=None):
        intent = {"owner_operation_id": "knowledgeos", "effect_id": "effect.synthetic.one",
                  "content_digest": content_digest or record_digest({"tool": "synthetic"}),
                  "generation": run["generation"], "fencing_token": run["fencing_token"],
                  "issued_at": AT, "expires_at": UNTIL, "work_run_id": run["work_run_id"]}
        if run.get("active_graph_run_id"):
            intent["graph_run_id"] = run["active_graph_run_id"]
        receipt = {key: value for key, value in intent.items() if key not in {"issued_at", "expires_at"}}
        receipt.update(outcome="succeeded", observed_at=AT, result_digest=record_digest({"result": "synthetic"}))
        return intent, receipt
