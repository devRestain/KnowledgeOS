"""One owner-admitted curation Team, two ordered Profiles, independent evaluation.

No SDK or provider is imported here. The trusted host supplies the exact native
runner; its model answer is never authority for a proposal or assessment.
"""

from __future__ import annotations

import base64
import copy
import re
from collections.abc import Mapping
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..projection import source_properties
from ..retrieval import _chunks_for_note
from .exops import KnowledgeExOps, OwnerActor
from .read_tools import KnowledgeReadTools
from .team_catalog import TeamBinding, TeamCatalog
from .temporal_work import WorkResult
from .work import (
    WorkContracts,
    graph_content_digest,
    record_digest,
    segment_digest,
    work_spec_digest,
)
from .work_methods import WorkMethodCatalog
from .work_tools import TeamToolSession

ENGINE_ID = "agentfabric.operation.temporal_langgraph"
ENGINE_VERSION = "0.1.0"
METHOD_ID = "knowledgeos.capture_normalization_team"
CRITERION = {
    "criterion_id": "criterion.proposal",
    "description": "Exact triage evidence precedes one source-bound Pending normalization proposal with independently verified bytes, receipt and live human gate; no canonical apply.",
}
PACKAGES = {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
_ROUTE = {
    "runtime_kind": "hermes_native",
    "hermes_version": "0.22.5",
    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
    "provider": "openai-codex",
    "model": "gpt-6.1-terra",
}
_TOOLS = ("work_context_read", "knowledge_read", "proposal_create")
_ROLES = ("knowledgeos.triage", "knowledgeos.normalizer")


def _require(condition, message):
    if not condition:
        raise AdmissionError("SCOPE_DENIED", message)


def _runner_binding(value):
    _require(
        isinstance(value, Mapping)
        and set(value) == {*_ROUTE, "adapter_digest"}
        and all(value.get(k) == v for k, v in _ROUTE.items())
        and isinstance(value.get("adapter_digest"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["adapter_digest"]) is not None,
        "Curation requires the exact native Hermes runtime binding",
    )
    return dict(value)


def curation_native_digest(runner_binding):
    modules = {}
    for name in (
        "curation_work.py",
        "curation_temporal.py",
        "curation_workflow.py",
        "temporal_contract.py",
    ):
        raw = read_regular(Path(__file__).with_name(name))
        _require(raw is not None, "Curation execution module is unavailable")
        modules[name] = digest(raw)
    return record_digest(
        {
            "execution_modules": modules,
            "engine_id": ENGINE_ID,
            "engine_version": ENGINE_VERSION,
            "packages": PACKAGES,
            "runner_binding": _runner_binding(runner_binding),
        }
    )


def _pins(app):
    selected = app.core.binding
    return {
        **{
            k: selected[k]
            for k in ("core_version", "core_digest", "semantic_version", "semantic_digest")
        },
        "schema_version": selected["schema_version_pin"],
    }


def _binding(app, binding, runner_binding):
    _require(isinstance(binding, TeamBinding), "Curation requires a TeamBinding")
    catalog = TeamCatalog.load(app.roots.control)
    catalog.validate_binding(binding)
    _require(
        binding.team_id == "knowledge-curation"
        and [s.role_id for s in binding.specialists] == list(_ROLES)
        and binding.native_binding_digest == curation_native_digest(runner_binding),
        "Curation requires exact selected Team and native runtime binding",
    )
    WorkContracts(app.core).record("ArtifactReference", binding.binding_reference)


def curation_admission(
    application,
    binding,
    source_reference,
    *,
    work_id,
    work_run_id,
    admission_receipt_id,
    runner_binding,
):
    """Build exact records; Director source/spec admission is a separate authority."""
    _binding(application, binding, runner_binding)
    path = application.resolve_reference(dict(source_reference))
    _require(path.startswith("00_Inbox/Captures/"), "Curation requires one selected Capture")
    pins = _pins(application)
    spec = {
        "owner_operation_id": "knowledgeos",
        "work_id": work_id,
        "work_spec_revision": 1,
        "criteria_revision": 1,
        "objective": "Triage one Capture and prepare one Pending normalization proposal",
        "constraints": "No canonical apply or source mutation; native runner "
        + record_digest(_runner_binding(runner_binding))
        + "; selected source "
        + record_digest(source_reference),
        "acceptance_criteria": [dict(CRITERION)],
        "target_kind": "team",
        "team_id": binding.team_id,
        "manager_executor_id": binding.manager_executor_id,
        "eval_officer_executor_id": binding.eval_officer_executor_id,
        "allowed_executor_ids": [s.executor_id for s in binding.specialists],
        "allowed_profiles": [dict(s.profile) for s in binding.specialists],
        "delegation_mode": "explicit_assignments",
        "explicit_assignments": [
            {
                "node_id": node,
                "executor_id": specialist.executor_id,
                **dict(specialist.profile),
                "criterion_ids": [CRITERION["criterion_id"]],
            }
            for node, specialist in zip(
                ("node.capture_evidence", "node.normalization_proposal"), binding.specialists
            )
        ],
        "continuation_policy": {
            "max_segments": 1,
            "max_repeated_gap_assessments": 1,
            "max_profile_invocations": 2,
        },
        "execution_profile": "sequential_graph",
        "admission_receipt_id": admission_receipt_id,
        "steerable_parameter_ids": [],
        "method_contract_ref": WorkMethodCatalog.load(application.roots.control).reference(
            METHOD_ID
        ),
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
        "fencing_token": "curation." + record_digest({"work_run_id": work_run_id})[7:],
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
    WorkContracts(application.core).run(run, spec)
    return spec, run


def _current(exops, work_run_id, runner_binding, *, application_states=("started",)):
    exops._current_binding()
    record = exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
    _require(
        isinstance(record, dict) and record.get("application_state") in application_states,
        "Curation has no active owner attempt",
    )
    spec, run = record["spec"], record["run"]
    binding = exops.teams.get(spec.get("team_id"))
    source = record.get("method_source_reference")
    expected, _ = curation_admission(
        exops.application,
        binding,
        source,
        work_id=spec["work_id"],
        work_run_id=work_run_id,
        admission_receipt_id=spec["admission_receipt_id"],
        runner_binding=runner_binding,
    )
    _require(spec == expected, "Unsupported curation WorkSpec or criterion semantics")
    exops._team_binding(spec, run)
    _require(
        exops.director_admitted(spec)
        and exops.director_method_source_admitted is not None
        and exops.director_method_source_admitted(spec, source),
        "Exact curation source admission is not current",
    )
    exops.contracts.run(run, spec)
    _require(
        run["state"] == "running"
        and not run["pending_action_ids"]
        and not run["unresolved_effect_ids"],
        "Curation Work is not effect-safe",
    )
    return record


def _artifact(reference, raw):
    return {
        "owner_operation_id": "knowledgeos",
        "artifact_id": reference["logical_id"],
        "source_revision": reference["source_revision"],
        "content_digest": digest(raw),
        "media_type": "text/markdown",
        "size_bytes": len(raw),
        "classification": reference["classification"],
        "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.artifact_read",
    }


def _source(app, reference):
    path = app.resolve_reference(dict(reference))
    app._mcp_source_policy(path)
    raw = read_regular(resolve_beneath(app.roots.vault, path))
    _require(
        raw is not None and digest(raw) == reference["content_digest"],
        "Curation source bytes changed",
    )
    properties = source_properties(path, raw)
    _require(
        properties.get("ai_policy") not in {"local_only", "deny"}
        and properties.get("sensitivity") != "confidential",
        "Native curation cannot receive a local-only or private source",
    )
    return path, raw


def _bundle(app, spec, run, source):
    suffix = record_digest({"work_run_id": run["work_run_id"], "spec_digest": spec["spec_digest"]})[
        7:39
    ]
    base = {"graph_id": "graph.curation." + suffix, "graph_revision": 1}
    ids = ["node.start", "node.capture_evidence", "node.normalization_proposal", "node.finish"]
    nodes = [
        {
            **base,
            "node_id": name,
            "node_kind": kind,
            "max_attempts": 1,
            "input_semantic_term_ids": [],
            "output_semantic_term_ids": [],
        }
        for name, kind in zip(ids, ("start", "task", "task", "terminal"))
    ]
    edges = [
        {
            **base,
            "edge_id": "edge." + str(i),
            "source_node_id": a,
            "target_node_id": b,
            "condition": "always",
        }
        for i, (a, b) in enumerate(pairwise(ids))
    ]
    pins = _pins(app)
    graph = {
        **base,
        "owner_operation_id": "knowledgeos",
        "profile": "graph_enabled",
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "start_node_id": ids[0],
        "node_ids": ids,
        "edge_ids": [e["edge_id"] for e in edges],
        "max_steps": 4,
        "input_semantic_term_ids": [],
        "output_semantic_term_ids": [],
        **pins,
    }
    graph["graph_digest"] = graph_content_digest(graph, nodes, edges)
    _, raw = _source(app, source)
    artifact = _artifact(source, raw)
    segment = {
        **{
            k: run[k]
            for k in (
                "owner_operation_id",
                "work_id",
                "work_run_id",
                "work_spec_revision",
                "work_spec_digest",
            )
        },
        **base,
        **pins,
        "graph_run_id": "graphrun.curation." + suffix,
        "graph_digest": graph["graph_digest"],
        "segment_index": 1,
        "target_criterion_ids": [CRITERION["criterion_id"]],
        "trigger_kind": "initial",
        "input_references": [artifact],
    }
    segment["segment_digest"] = segment_digest(segment)
    assignments = [
        {
            **base,
            "owner_operation_id": "knowledgeos",
            "node_id": node,
            "assignment_generation": run["generation"],
            "executor_id": executor,
            **profile,
            "admission_receipt_id": spec["admission_receipt_id"],
            "work_run_id": run["work_run_id"],
            "work_spec_revision": spec["work_spec_revision"],
            "work_spec_digest": spec["spec_digest"],
            "selection_authority": "explicit_user",
            "semantic_version": pins["semantic_version"],
            "semantic_digest": pins["semantic_digest"],
        }
        for node, executor, profile in zip(
            ids[1:3], spec["allowed_executor_ids"], spec["allowed_profiles"]
        )
    ]
    return {
        "segment": segment,
        "graph_spec": graph,
        "nodes": nodes,
        "edges": edges,
        "assignments": assignments,
    }, {artifact["artifact_id"]: artifact}


class CurationTeamExecutor:
    def __init__(self, exops: KnowledgeExOps, *, runner, recovery_quiesced=None):
        self.exops, self.runner = exops, runner
        self.recovery_quiesced = recovery_quiesced
        self.runner_binding = _runner_binding(runner.binding)

    def _current(self, work_run_id):
        _require(
            _runner_binding(self.runner.binding) == self.runner_binding,
            "Curation runner binding changed",
        )
        return _current(self.exops, work_run_id, self.runner_binding)

    def mark_unknown(
        self, work_run_id, *, action_id, temporal_run_id, activity_id, failure_kind=None
    ):
        """Retain native Activity failure separately from its unknown effect outcome."""
        _require(failure_kind in (None, "failure", "timeout"), "Unknown native failure kind")
        _require(
            all(
                isinstance(value, str) and 0 < len(value) <= 256
                for value in (temporal_run_id, activity_id)
            ),
            "Failed Activity identity is unavailable",
        )
        record = _current(
            self.exops,
            work_run_id,
            self.runner_binding,
            application_states=("started", "unknown"),
        )
        _require(record["action_id"] == action_id, "Failed Activity action changed")
        failure = {"temporal_run_id": temporal_run_id, "activity_id": activity_id}
        updates = {}
        if "curation_failure" in record:
            _require(record["curation_failure"] == failure, "Failed Activity identity changed")
        else:
            updates["curation_failure"] = failure
        if failure_kind is not None:
            if "curation_native_failure" in record:
                marker = record["curation_native_failure"]
                _require(
                    marker["kind"] == failure_kind
                    and marker["target"]["temporal_run_id"] == temporal_run_id
                    and marker["target"]["activity_id"] == activity_id,
                    "Native failure sensor changed meaning",
                )
            else:
                run = record["run"]
                marker = {
                    "kind": failure_kind,
                    "occurred_at": stamp(self.exops.application.clock()),
                    "target": {
                        "owner_operation_id": "knowledgeos",
                        "work_run_id": work_run_id,
                        "action_id": action_id,
                        "content_digest": record["content_digest"],
                        "work_spec_digest": record["spec"]["spec_digest"],
                        "temporal_run_id": temporal_run_id,
                        "activity_id": activity_id,
                        "run_revision": run["revision"],
                        "generation": run["generation"],
                        "fencing_token": run["fencing_token"],
                    },
                    "run": copy.deepcopy(run),
                    "source_reference": copy.deepcopy(record["method_source_reference"]),
                }
                updates["curation_native_failure"] = marker
        if updates:
            app = self.exops.application
            _require(len(canonical(updates)) <= 256 * 1024, "Native failure evidence exceeds bound")
            with app.journal.writer() as writer:
                current = writer.value["intents"].get("exops.work." + work_run_id)
                _require(current == record, "Native failure attempt changed before commit")
                current.update(copy.deepcopy(updates))
                for key in updates:
                    writer.value["history"].append({
                        "event": key, "work_run_id": work_run_id,
                        "sequence": writer.value["revision"] + 1,
                    })
                writer.commit(app.clock())
        if record["application_state"] == "started":
            self.exops.worker_unknown(
                work_run_id,
                worker_identity=self.exops.worker_identity,
                action_id=action_id,
            )

    def _quiesced(self, target):
        _require(
            self.recovery_quiesced is not None
            and self.recovery_quiesced(copy.deepcopy(target)) is True,
            "Recovery requires confirmed native failure and stopped producer",
        )

    def _recovery_target(self, record, target):
        _require(isinstance(target, Mapping), "Recovery target is unavailable")
        run = record["run"]
        failure = record.get("curation_failure", {})
        expected = {
            "owner_operation_id": "knowledgeos",
            "work_run_id": run["work_run_id"],
            "action_id": record["action_id"],
            "content_digest": record["content_digest"],
            "work_spec_digest": record["spec"]["spec_digest"],
            "temporal_run_id": failure.get("temporal_run_id"),
            "activity_id": failure.get("activity_id"),
            "run_revision": run["revision"],
            "generation": run["generation"],
            "fencing_token": run["fencing_token"],
        }
        _require(
            dict(target) == expected
            and all(type(target[key]) is type(value) for key, value in expected.items())
            and all(
                isinstance(failure.get(k), str) and failure[k]
                for k in ("temporal_run_id", "activity_id")
            ),
            "Recovery target differs from exact failed owner attempt",
        )

    def _recovery_evidence(self, record):
        """Pin complete committed evidence, without creating an assessment or effect."""
        self._segment(record)
        self._evidence(record)
        evidence = record.get("curation_model_execution")
        _require(
            isinstance(evidence, dict)
            and set(evidence) == {"request", "response", "evidence_digest"}
            and evidence["evidence_digest"]
            == record_digest(
                {
                    "request": evidence["request"],
                    "response": evidence["response"],
                }
            )
            and record.get("curation_model_request") == evidence["request"],
            "Recovery has no complete recorded native response",
        )
        reference = self._response(record, evidence["request"], evidence["response"])
        _require(
            record.get("submitted_artifacts") == [reference]
            and record.get("canonical_proposal_reference") == reference,
            "Recovery has no exact committed proposal registration",
        )
        app = self.exops.application
        _, source_raw = _source(app, record["method_source_reference"])
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        _require(raw is not None, "Recovery Pending artifact is unavailable")
        artifact = _artifact(reference, raw)
        _require(
            record.get("artifact_references") == {artifact["artifact_id"]: artifact},
            "Recovery registered artifact bytes changed",
        )
        snapshot = app.journal.read()
        _require(
            snapshot["intents"].get("exops.work." + record["run"]["work_run_id"]) == record,
            "Recovery Work changed while pinning evidence",
        )
        effects = [
            e
            for e in snapshot["intents"].values()
            if e.get("work_binding", {}).get("work_run_id") == record["run"]["work_run_id"]
        ]
        _require(len(effects) == 1, "Recovery requires exactly one committed effect")
        effect = effects[0]
        receipt = snapshot["receipts"].get(effect.get("effect_id"))
        _require(
            effect.get("state") == "completed"
            and isinstance(receipt, dict)
            and receipt.get("execution", {}).get("outcome") == "succeeded"
            and receipt["execution"].get("result_digest") == digest(raw),
            "Recovery effect lacks its exact successful receipt",
        )
        return {
            "work_record_digest": record_digest(record),
            "source_digest": digest(source_raw),
            "proposal_digest": digest(raw),
            "effect": effect,
            "effect_receipt": receipt,
            "human_request": snapshot["human_requests"].get(effect.get("human_request_id")),
            "human_decision": snapshot["decisions"].get(effect.get("human_request_id")),
        }

    def _applied_recovery(self, record, intent):
        """Exact completion replay reads its durable receipt, never evaluates again."""
        self.exops._current_binding()
        app = self.exops.application
        _require(isinstance(record, dict), "Applied recovery Work is unavailable")
        spec = record["spec"]
        source = record["method_source_reference"]
        expected, _ = curation_admission(
            app,
            self.exops.teams.get(spec.get("team_id")),
            source,
            work_id=spec["work_id"],
            work_run_id=record["run"]["work_run_id"],
            admission_receipt_id=spec["admission_receipt_id"],
            runner_binding=self.runner_binding,
        )
        self.exops._team_binding(spec, record["run"])
        _require(
            spec == expected
            and self.exops.director_admitted(spec)
            and self.exops.director_method_source_admitted is not None
            and self.exops.director_method_source_admitted(spec, source),
            "Applied recovery admission changed",
        )
        execution = (
            app.journal.read()["receipts"]
            .get(
                "exops.work." + record["action_id"],
                {},
            )
            .get("receipt", {})
        )
        _require(
            _runner_binding(self.runner.binding) == self.runner_binding == intent["runner_binding"]
            and record.get("actor_id") == intent["actor_id"]
            and record.get("spec") == intent["spec"]
            and record.get("method_source_reference") == intent["source_reference"]
            and record.get("application_state") == "recorded"
            and record.get("receipt") == intent.get("applied_receipt")
            and record_digest(intent["evidence"]) == intent["evidence_digest"]
            and execution.get("outcome") == "succeeded"
            and execution.get("result_digest") == intent["completed_run_digest"]
            and record_digest(record["run"]) == intent.get("completed_run_digest"),
            "Applied recovery receipt or current binding changed",
        )
        _source(self.exops.application, intent["source_reference"])
        return copy.deepcopy(intent["applied_receipt"])

    def request_recovery(self, work_run_id, *, actor, recovery_id, target):
        self.exops._guard(actor, "recover_work")
        _require(
            isinstance(recovery_id, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", recovery_id),
            "Recovery identity is invalid",
        )
        _require(isinstance(target, Mapping), "Recovery target is unavailable")
        payload = {"recovery_id": recovery_id, "target": copy.deepcopy(dict(target))}
        snapshot = self.exops.application.journal.read()
        key = "curation.recovery." + recovery_id
        previous = snapshot["intents"].get(key)
        record = snapshot["intents"].get("exops.work." + work_run_id)
        _require(
            isinstance(record, dict) and record.get("actor_id") == actor.actor_id,
            "Recovery belongs to the original admitted actor",
        )
        if previous is not None:
            _require(
                previous.get("target") == payload["target"]
                and previous.get("actor_id") == actor.actor_id,
                "Recovery identity conflicts with an earlier request",
            )
            self._quiesced(payload["target"])
            if previous["state"] == "applied":
                self._applied_recovery(record, previous)
                return payload
        _require(
            _runner_binding(self.runner.binding) == self.runner_binding,
            "Curation runner binding changed",
        )
        record = _current(
            self.exops, work_run_id, self.runner_binding, application_states=("unknown",)
        )
        self._recovery_target(record, payload["target"])
        self._quiesced(payload["target"])
        pins = self._recovery_evidence(record)
        intent = {
            "state": "received",
            "actor_id": actor.actor_id,
            "actor_ingress": actor.ingress,
            "target": payload["target"],
            "runner_binding": self.runner_binding,
            "spec": record["spec"],
            "source_reference": record["method_source_reference"],
            "work_record_digest": pins["work_record_digest"],
            "evidence_digest": record_digest(pins),
            "evidence": pins,
        }
        app = self.exops.application
        with app.journal.writer() as writer:
            _require(
                writer.value["intents"].get("exops.work." + work_run_id) == record,
                "Recovery Work changed before intent admission",
            )
            existing = writer.value["intents"].get(key)
            if existing is not None:
                _require(
                    existing == {**intent, "state": existing["state"]}
                    and existing["state"] in {"received", "applying"},
                    "Recovery intent changed",
                )
            else:
                _require(
                    not any(
                        value.get("target", {}).get("work_run_id") == work_run_id
                        and name.startswith("curation.recovery.")
                        for name, value in writer.value["intents"].items()
                    ),
                    "A different recovery already owns this failed attempt",
                )
                writer.value["intents"][key] = copy.deepcopy(intent)
                writer.value["history"].append(
                    {
                        "event": "curation_recovery_received",
                        "work_run_id": work_run_id,
                        "recovery_id": recovery_id,
                        "sequence": writer.value["revision"] + 1,
                    }
                )
                writer.commit(app.clock())
        return payload

    async def reconcile(self, work_run_id, recovery_id, *, temporal_run_id, activity_id):
        app = self.exops.application
        key = "curation.recovery." + recovery_id
        snapshot = app.journal.read()
        intent = snapshot["intents"].get(key)
        _require(isinstance(intent, dict), "Recovery has no owner-admitted intent")
        self.exops._guard(OwnerActor(intent["actor_id"], intent["actor_ingress"]), "recover_work")
        target = intent["target"]
        _require(
            target["work_run_id"] == work_run_id
            and target["temporal_run_id"] == temporal_run_id
            and target["activity_id"] == activity_id,
            "Recovery Activity differs from admitted native failure",
        )
        self._quiesced(target)
        record = snapshot["intents"].get("exops.work." + work_run_id)
        if intent["state"] == "applied":
            return self._applied_recovery(record, intent)
        _require(intent["state"] in {"received", "applying"}, "Recovery is not applicable")
        _require(
            _runner_binding(self.runner.binding) == self.runner_binding == intent["runner_binding"],
            "Recovery runtime binding changed",
        )
        record = _current(
            self.exops, work_run_id, self.runner_binding, application_states=("unknown",)
        )
        self._recovery_target(record, target)
        pins = self._recovery_evidence(record)
        _require(
            record["actor_id"] == intent["actor_id"]
            and record["spec"] == intent["spec"]
            and pins == intent["evidence"]
            and record_digest(pins) == intent["evidence_digest"],
            "Recovery pinned evidence changed",
        )
        result = self._assess(record)
        self._quiesced(target)
        with app.journal.writer() as writer:
            _require(
                writer.value["intents"].get("exops.work." + work_run_id) == record
                and writer.value["intents"].get(key) == intent,
                "Recovery changed during independent assessment",
            )
            _require(
                self._recovery_evidence(record) == pins,
                "Recovery effect evidence changed before application",
            )
            writer.value["intents"][key]["state"] = "applying"
            writer.commit(app.clock())
        return self.exops.worker_finished(
            work_run_id,
            worker_identity=self.exops.worker_identity,
            action_id=record["action_id"],
            next_run=result.next_run,
            assessment=result.assessment,
            segments=result.segments,
            evaluations=result.evaluations,
            artifacts=result.artifacts,
            checkpoints=result.checkpoints,
            candidate_assessor_id=result.candidate_assessor_id,
            recovery_id=recovery_id,
        )

    def _save(self, work_run_id, record, key, value):
        _require(
            len(canonical(value)) <= 256 * 1024, "Curation evidence exceeds its bounded record"
        )
        with self.exops.application.journal.writer() as writer:
            current = writer.value["intents"].get("exops.work." + work_run_id)
            _require(
                current == record and key not in current,
                "Curation stage changed or already recorded",
            )
            current[key] = copy.deepcopy(value)
            writer.value["history"].append(
                {"event": key, "work_run_id": work_run_id, "sequence": writer.value["revision"] + 1}
            )
            writer.commit(self.exops.application.clock())

    def _segment(self, record):
        spec, run = record["spec"], record["run"]
        admitted = record.get("admitted_method_segments", {})
        _require(
            len(admitted) == 1
            and run["graph_run_ids"] == list(admitted)
            and run["segments_started"] == 1
            and run["profile_invocations"] == 2,
            "Curation requires one Manager Graph and two Profile invocations",
        )
        bundle, _ = _bundle(self.exops.application, spec, run, record["method_source_reference"])
        selected = admitted.get(run["active_graph_run_id"])
        _require(
            selected is not None
            and selected["segment"] == bundle["segment"]
            and selected["assignments"] == bundle["assignments"]
            and selected["source_reference"] == record["method_source_reference"]
            and selected["native_binding_digest"] == curation_native_digest(self.runner_binding),
            "Curation immutable segment or assignments changed",
        )
        return bundle["segment"]

    def _view(self, record, index):
        """Read-only evidence reconstruction, never a grant for Profile tools."""
        spec, run = record["spec"], record["run"]
        methods = WorkMethodCatalog.load(self.exops.application.roots.control)
        method = methods.admitted_method(spec, self.exops.contracts)
        role = _ROLES[index]
        allowed = methods.allowed_tools(spec, role, self.exops.contracts)
        selected = record["admitted_method_segments"][run["active_graph_run_id"]]
        assignment = [
            a
            for a in selected["assignments"]
            if a["executor_id"] == spec["allowed_executor_ids"][index]
        ]
        _require(
            len(assignment) == 1
            and assignment[0]["role_id"] == role
            and assignment[0]["assignment_generation"] == run["generation"],
            "Evidence has no exact Profile assignment",
        )
        source = record["method_source_reference"]
        reader = KnowledgeReadTools(self.exops.application)
        path = self.exops.application.resolve_reference(source)
        _, notes = reader._eligible(selected_path=path)
        chunks = [chunk.locator for chunk in _chunks_for_note(reader._note(source, notes))]
        return {
            "work_run_id": run["work_run_id"],
            "work_spec_digest": spec["spec_digest"],
            "work_spec_revision": spec["work_spec_revision"],
            "criteria_revision": spec["criteria_revision"],
            "acceptance_criteria": spec["acceptance_criteria"],
            "team_id": method["team_id"],
            "method_contract_ref": spec["method_contract_ref"],
            "source_reference": source,
            "source_chunks": chunks,
            "executor_id": spec["allowed_executor_ids"][index],
            "generation": run["generation"],
            "fencing_token": run["fencing_token"],
            "graph_run_id": run["active_graph_run_id"],
            "run_state": run["state"],
            "allowed_tools": list(allowed),
        }

    def _authorize_evidence(self, context, name, arguments):
        _require(name in context["allowed_tools"], "Evidence exceeds its Profile grant")
        if name == "work_context_read":
            _require(not arguments, "Evidence context has caller-selected arguments")
        for key in ("source_reference", "resource_reference"):
            if key in arguments:
                _require(arguments[key] == context["source_reference"], "Evidence source changed")
        _require(
            not ({"proposal_reference", "artifact_reference"} & set(arguments)),
            "Evidence has an unadmitted artifact grant",
        )

    async def manager(self, spec, run):
        record = self._current(run["work_run_id"])
        _require(
            record["spec"] == dict(spec) and record["run"] == dict(run),
            "Manager input differs from exact owner Work",
        )
        _require(
            not run["graph_run_ids"] and not record.get("admitted_method_segments"),
            "Manager cannot repeat an admitted Graph",
        )
        session = TeamToolSession(
            self.exops.application,
            run["work_run_id"],
            spec["manager_executor_id"],
            exops=self.exops,
        )
        _require(
            session.method_view()["method_contract_ref"] == spec["method_contract_ref"],
            "Manager method changed",
        )
        bundle, artifacts = _bundle(
            self.exops.application, spec, run, record["method_source_reference"]
        )
        session.propose_graph({"bundle": bundle, "artifacts": artifacts})

    def _triage(self, record):
        self._segment(record)
        context = self._view(record, 0)
        reader = KnowledgeReadTools(
            self.exops.application,
            allowed_path=self.exops.application.resolve_reference(context["source_reference"]),
        )
        reads = []
        for locator in context["source_chunks"]:
            offset = 0
            while True:
                arguments = {
                    "resource_reference": context["source_reference"],
                    "chunk_locator": locator,
                    "offset": offset,
                }
                self._authorize_evidence(context, "knowledge_read", arguments)
                data = reader.knowledge_read(
                    arguments["resource_reference"], locator, offset=offset
                )
                _require(bool(data["text"]), "Triage source chunk is empty")
                reads.append({"arguments": arguments, "data": data})
                offset += len(data["text"])
                if not data["truncated"]:
                    break
        _require(bool(reads), "Triage requires exact source chunks")
        evidence = {"context": context, "reads": reads}
        return {**evidence, "evidence_digest": record_digest(evidence)}

    def _evidence(self, record):
        _require(
            record.get("curation_capture_evidence") == self._triage(record),
            "Triage evidence is missing or changed",
        )

    async def capture_evidence(self, work_run_id):
        record = self._current(work_run_id)
        _require("curation_model_execution" not in record, "Triage cannot follow normalization")
        self._save(work_run_id, record, "curation_capture_evidence", self._triage(record))

    def _request(self, record):
        self._evidence(record)
        _source(self.exops.application, record["method_source_reference"])
        context = self._view(record, 1)
        request = {
            k: context[k]
            for k in (
                "work_run_id",
                "work_spec_digest",
                "graph_run_id",
                "executor_id",
                "generation",
                "fencing_token",
                "source_reference",
                "source_chunks",
            )
        }
        request.update(
            runner_binding=self.runner_binding,
            control_root=str(self.exops.application.roots.control),
        )
        return {"request_digest": record_digest(request), **request}

    def _response(self, record, request, response):
        _require(
            request == self._request(record),
            "Native request differs from current Team/source/runtime",
        )
        _require(
            isinstance(response, dict)
            and set(response)
            == {
                "request_digest",
                "runner_binding",
                "model",
                "provider",
                "session_id",
                "final_text",
                "tool_calls",
                "exposed_tools",
            }
            and response["request_digest"] == request["request_digest"]
            and response["runner_binding"] == self.runner_binding
            and response["model"] == self.runner_binding["model"]
            and response["provider"] == self.runner_binding["provider"]
            and isinstance(response["session_id"], str)
            and 0 < len(response["session_id"]) <= 256
            and isinstance(response["final_text"], str)
            and 0 < len(response["final_text"]) <= 16384
            and isinstance(response["exposed_tools"], list)
            and len(response["exposed_tools"]) == 3
            and set(response["exposed_tools"]) == set(_TOOLS),
            "Native response changed route, tools or binding",
        )
        calls = response["tool_calls"]
        _require(
            isinstance(calls, list) and 3 <= len(calls) <= 32,
            "Native trace needs actual context, reads and proposal",
        )
        context = self._view(record, 1)
        reader = KnowledgeReadTools(
            self.exops.application,
            allowed_path=self.exops.application.resolve_reference(request["source_reference"]),
        )
        seen, proposal = set(), None
        offsets = dict.fromkeys(request["source_chunks"], 0)
        for index, call in enumerate(calls):
            _require(
                isinstance(call, dict)
                and {"name", "arguments", "result"} <= set(call)
                and set(call) <= {"name", "arguments", "result", "raw_callback_name"}
                and (
                    "raw_callback_name" not in call
                    or isinstance(call["raw_callback_name"], str)
                    and len(call["raw_callback_name"]) <= 256
                )
                and call["name"] in _TOOLS
                and isinstance(call["arguments"], dict)
                and isinstance(call["result"], dict),
                "Native trace has an ungranted tool",
            )
            name, args, envelope = call["name"], call["arguments"], call["result"]
            self._authorize_evidence(context, name, args)
            _require(
                envelope.get("schema_version") == 3
                and envelope.get("operation") == name
                and envelope.get("error") is None
                and envelope.get("canonical_target_changed") is False
                and isinstance(envelope.get("data"), dict),
                "Native trace has a denied owner result",
            )
            data = envelope["data"]
            if name == "work_context_read":
                _require(
                    index == 0
                    and envelope.get("status") == "ok"
                    and envelope.get("effect") == "read_only"
                    and data == context,
                    "Native context changed",
                )
            elif name == "knowledge_read":
                _require(
                    index > 0
                    and proposal is None
                    and {"resource_reference", "chunk_locator"} <= set(args)
                    and set(args) <= {"resource_reference", "chunk_locator", "offset", "max_chars"}
                    and args["chunk_locator"] in request["source_chunks"]
                    and args["chunk_locator"] not in seen
                    and args.get("offset", 0) == offsets[args["chunk_locator"]]
                    and envelope.get("status") == "ok"
                    and envelope.get("effect") == "read_only",
                    "Native read is not a complete ordered source page",
                )
                actual = reader.knowledge_read(
                    args["resource_reference"],
                    args["chunk_locator"],
                    offset=args.get("offset", 0),
                    max_chars=args.get("max_chars", 4096),
                )
                _require(
                    data == actual and bool(actual["text"]),
                    "Native read differs from fresh source evidence",
                )
                offsets[args["chunk_locator"]] += len(actual["text"])
                if not actual["truncated"]:
                    seen.add(args["chunk_locator"])
            else:
                _require(
                    index == len(calls) - 1
                    and proposal is None
                    and seen == set(request["source_chunks"])
                    and set(args) == {"source_reference"}
                    and envelope.get("status") in {"created", "replayed"}
                    and envelope.get("effect") in {"proposal_created", "proposal_replayed"}
                    and envelope.get("execution_outcome") == "completed"
                    and data.get("source_reference") == request["source_reference"]
                    and isinstance(data.get("proposal_reference"), dict),
                    "Native proposal lacks actual source reads or effect",
                )
                proposal = data["proposal_reference"]
        _require(
            proposal is not None and calls[0]["name"] == "work_context_read",
            "Native final answer has no proposal effect",
        )
        return proposal

    async def normalization_proposal(self, work_run_id):
        record = self._current(work_run_id)
        request = self._request(record)
        self._save(work_run_id, record, "curation_model_request", request)
        response = await self.runner(copy.deepcopy(request))
        record = self._current(work_run_id)
        reference = self._response(record, request, response)
        evidence = {"request": request, "response": response}
        self._save(
            work_run_id,
            record,
            "curation_model_execution",
            {**evidence, "evidence_digest": record_digest(evidence)},
        )
        record = self._current(work_run_id)
        app = self.exops.application
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        _require(raw is not None, "Native Pending proposal is unavailable")
        artifact = _artifact(reference, raw)
        # Independent assessment checks the actual effect and receipt, not this registration.
        with app.journal.writer() as writer:
            current = writer.value["intents"].get("exops.work." + work_run_id)
            _require(
                current == record and not current.get("submitted_artifacts"),
                "Curation proposal registration changed",
            )
            current.update(
                submitted_artifacts=[dict(reference)],
                canonical_proposal_reference=dict(reference),
                artifact_references={artifact["artifact_id"]: artifact},
            )
            writer.commit(app.clock())

    async def assess(self, work_run_id):
        return self._assess(self._current(work_run_id))

    def _assess(self, record):
        work_run_id = record["run"]["work_run_id"]
        spec, run = record["spec"], record["run"]
        evaluator = spec["eval_officer_executor_id"]
        _require(
            evaluator not in [spec["manager_executor_id"], *spec["allowed_executor_ids"]],
            "Only independent EvalOfficer may certify",
        )
        segment = self._segment(record)
        self._evidence(record)
        evidence = record.get("curation_model_execution")
        _require(
            isinstance(evidence, dict)
            and set(evidence) == {"request", "response", "evidence_digest"}
            and evidence["evidence_digest"]
            == record_digest({"request": evidence["request"], "response": evidence["response"]})
            and record.get("curation_model_request") == evidence["request"],
            "Native execution evidence changed or missing",
        )
        reference = self._response(record, evidence["request"], evidence["response"])
        _require(
            record.get("submitted_artifacts") == [reference]
            and record.get("canonical_proposal_reference") == reference,
            "Registered proposal differs from native effect",
        )
        app = self.exops.application
        source = record["method_source_reference"]
        source_path, source_raw = _source(app, source)
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        _require(raw is not None, "Pending proposal unavailable")
        artifact = _artifact(reference, raw)
        _require(
            record.get("artifact_references") == {artifact["artifact_id"]: artifact},
            "Registered proposal bytes changed",
        )
        snapshot = app.journal.read()
        _require(
            snapshot["intents"].get("exops.work." + work_run_id) == record,
            "Work changed during assessment",
        )
        effects = [
            e
            for e in snapshot["intents"].values()
            if e.get("work_binding", {}).get("work_run_id") == work_run_id
        ]
        _require(len(effects) == 1, "Assessment requires one exact Work effect, no unknown effects")
        effect = effects[0]
        bound = {
            "work_run_id": work_run_id,
            "work_spec_digest": spec["spec_digest"],
            "graph_run_id": run["active_graph_run_id"],
            "executor_id": spec["allowed_executor_ids"][1],
            "source_reference": source,
        }
        receipt = snapshot["receipts"].get(effect["effect_id"], {}).get("execution", {})
        _require(
            effect.get("work_binding") == bound
            and effect.get("state") == "completed"
            and effect.get("artifact_path") == path
            and effect.get("source_path") == source_path
            and effect.get("source_digest") == digest(source_raw)
            and effect.get("content_digest") == digest(raw)
            and base64.b64decode(effect["frozen_artifact"], validate=True) == raw
            and all(
                receipt.get(k) == effect[k]
                for k in (
                    "effect_id",
                    "owner_operation_id",
                    "content_digest",
                    "generation",
                    "fencing_token",
                )
            )
            and receipt.get("outcome") == "succeeded"
            and receipt.get("result_digest") == digest(raw),
            "Proposal receipt or exact frozen bytes changed",
        )
        report, code = app.inspect_proposal(reference)
        _require(
            code == 0 and report.get("status") == "PASS" and report.get("count") == 1,
            "Independent Pending proposal inspection failed",
        )
        inspected = report["proposals"][0]
        _require(
            inspected["proposal_path"] == path
            and inspected["status"] == "pending"
            and inspected["action"] == "update_note"
            and inspected["target_path"] == source_path
            and inspected["source_baselines"]
            == [{"path": source_path, "sha256": digest(source_raw)[7:]}]
            and inspected["proposal_sha256"] == digest(raw)[7:]
            and inspected["target_before_sha256"] == digest(source_raw)[7:]
            and inspected["target_after_sha256"] != inspected["target_before_sha256"],
            "Pending proposal differs from curation criterion",
        )
        human = snapshot["human_requests"].get(effect["human_request_id"], {})
        _require(
            human.get("lifecycle") == "pending"
            and human.get("allowed_actions") == ["approve", "reject", "inspect"]
            and human.get("evidence_reference_ids") == [reference["logical_id"]]
            and effect["human_request_id"] not in snapshot["decisions"]
            and datetime.fromisoformat(human["expires_at"]) > app.clock()
            and human.get("target_digest") == app._target_digest(source_path, path),
            "Proposal lost its exact pending human gate",
        )
        _require(
            app.reference(source_path) == source
            and app.reference(path, proposal=True) == reference,
            "Source or proposal changed during independent inspection",
        )
        checkpoint = record_digest(
            {
                "graph_run_id": run["active_graph_run_id"],
                "source_digest": digest(source_raw),
                "proposal_digest": digest(raw),
                "triage_digest": record["curation_capture_evidence"]["evidence_digest"],
                "model_digest": evidence["evidence_digest"],
            }
        )
        graph_run_id = run["active_graph_run_id"]
        suffix = record_digest({"graph_run_id": graph_run_id, "checkpoint": checkpoint})[7:39]
        evaluation = {
            "owner_operation_id": "knowledgeos",
            "run_id": graph_run_id,
            "work_run_id": work_run_id,
            "work_spec_revision": spec["work_spec_revision"],
            "work_spec_digest": spec["spec_digest"],
            "checkpoint_digest": checkpoint,
            "criteria_revision": spec["criteria_revision"],
            "criterion_ids": [CRITERION["criterion_id"]],
            "passed_criterion_ids": [CRITERION["criterion_id"]],
            "failed_criterion_ids": [],
            "not_run_criterion_ids": [],
            "evaluation_id": "evaluation.curation." + suffix,
            "evaluator_id": evaluator,
            "evidence_references": [artifact],
        }
        assessment = {
            **{
                k: run[k]
                for k in (
                    "owner_operation_id",
                    "work_id",
                    "work_run_id",
                    "work_spec_revision",
                    "work_spec_digest",
                )
            },
            **_pins(app),
            "assessment_id": "assessment.curation." + suffix,
            "criteria_revision": spec["criteria_revision"],
            "assessor_id": evaluator,
            "assessed_at": stamp(app.clock()),
            "graph_run_ids": [graph_run_id],
            "completion_state": "complete",
            "criterion_outcomes": [
                {
                    "criterion_id": CRITERION["criterion_id"],
                    "outcome": "satisfied",
                    "evaluation_ids": [evaluation["evaluation_id"]],
                    "evidence_references": [artifact],
                }
            ],
        }
        completed = {
            **copy.deepcopy(run),
            "revision": run["revision"] + 1,
            "control_revision": run["control_revision"] + 1,
            "state": "completed",
            "latest_assessment_id": assessment["assessment_id"],
            "latest_assessment_digest": record_digest(assessment),
            "result_reference": artifact,
        }
        completed.pop("active_graph_run_id")
        return WorkResult(
            completed,
            assessment,
            (segment,),
            (evaluation,),
            {artifact["artifact_id"]: artifact},
            {graph_run_id: checkpoint},
            evaluator,
        )
